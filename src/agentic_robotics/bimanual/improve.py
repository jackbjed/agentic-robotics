"""A development session: a model improves a program run(robot, task) for a bimanual task (docs/bimanual.md) by
practising in the simulator, until it submits a version or its budget is spent. The submitted program is then
evaluated without the model (practice.py suite).

    python -m agentic_robotics.bimanual.improve --env sim_bimanual_grippers --task insert_cylinder --model astra \\
           --start runs/bimanual/x/program.py --segmenter sam3 --budget-usd 20 --out runs/bimanual/y           (PAID)
    python -m agentic_robotics.bimanual.improve ... --out runs/bimanual/y --resume          (a stopped session: go on)
    python -m agentic_robotics.bimanual.practice suite --env sim_bimanual_grippers --task insert_cylinder \\
           --program runs/bimanual/y/program.py --seeds 0-2,4-10 --segmenter sam3 --video --out runs/bimanual/y/eval

The session is one conversation with tool calls (TOOLS): the model saves versions of the program (write_program,
edit_program), runs a version on practice episodes (run_episodes: seeds 100 to 999, each run in the program sandbox,
practice.py), reads what happened (the judge's verdict, the program's output, a trace of its commands with the
objects' TRUE positions, images the program hands over with robot.show, the overview camera's last frame) and
submits a version (submit). The program itself gets nothing more than in evaluation.

The budget is hard: before each model call its worst case (the whole conversation as uncached input, a full reply)
is reserved against the session's cap in the spending ledger (perception/vlm.py), and the reply's length is cut to
what is left; when a useful reply no longer fits, the session ends and the last submitted version is the result.

--out DIR gets versions/vNN.py, runs/vNN/sSEED/ (each practice episode: practice.py's files), transcript.md (what
the model said, called and got), session.jsonl (the same, as data, with every turn's cost), state.json + pending.pkl
(to resume), result.json and program.py (the submitted version).
"""
from __future__ import annotations

import argparse
import json
import pickle
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import practice, scenes
from .tasks import TASKS, Episode
from .tools import Toolbox, openai_items, openai_tools, text
from .write import VARIES, instructions

SEEDS = (100, 999)                                              # the practice episodes
AT_ONCE = 6                                                     # episodes of one run_episodes call
PRICES = {"in": 12.5, "cached": 1.0, "out": 50.0}               # $ per million tokens, if the model client has none
MIN_REPLY = 4000                                                # tokens: the session ends when a reply this long no longer fits
IMAGE_TOKENS = 800                                              # an image's input tokens (an upper estimate)

_SESSION = """
You develop this program in a session with a simulator of the robot to practise in, through these tools:

write_program, edit_program   save a new version of the program (v1, v2, ...{v0}). Any version can be run, so a
                              version can also be an experiment (a program that only looks and reports).
run_episodes                  run a version on practice episodes: seeds {lo} to {hi}, each seed one layout. You get, for
                              each episode: the judge's verdict, how the program ended, the robot time, what the
                              program printed, and the objects' TRUE positions at the start and at the end (the
                              simulator's state, which the program itself never gets){points}.
read_output                   more of an episode's output: what the program printed and, on lines starting with "#",
                              a trace of every robot command: robot time, the command, its result, that arm's TRUE
                              tool position (and opening), and after NOW the TRUE position of each object that
                              moved. Compare what the program measured with the truth there.
view_images                   the overview camera's picture of the end of an episode, and the images the program
                              handed over with robot.show
read_program                  a version's text, with line numbers
submit                        name the version that is your result

In a program, robot.show(label, image) hands an image to you (H x W x 3 uint8 RGB, or H x W; at most {shown} an episode;
no robot time): for instance a camera frame with what the program detected drawn on it (cv2). An episode's
randomness (sensor noise, calibration error) is fixed by its seed: the same version on the same seed gives the
same result, and run_episodes keeps results. {rules}

Your result is the version you submitted last. After the session it is run, without you, on {n_eval} episodes with new
layouts of the same kind (not practice seeds), and judged by how many succeed and then by the robot time it takes. So make the
program reliable over many layouts first, then fast.

The session's budget is ${budget:.0f} of model usage: your replies (the thinking included) and the conversation so far, which
is read again at every turn, are what costs; simulation is free. Every tool result ends with what is spent. So run
several seeds at once, read outputs selectively, and prefer edit_program to writing the whole program again. Submit
a version as soon as it is better than the one submitted before: when the budget runs out the session ends without
warning and the last submitted version counts{fallback}. When you are satisfied, call submit with final = true.
"""

_FIRST = """
{start}
The first look of practice episode {seed} (the arms at their start poses; the three cameras' colour images):"""

_V0 = """
v0, the starting program, was written earlier for this task in one attempt, without any test:

```python
{code}```
"""
# ---- a session on a task with varied scenes (scenes.py): from nothing, the scenes set up by the model
ASK_VARIED = ("You should write a skill that solves this task (i.e., picking the cylinder and threading it onto the stick so that "
              "the stick passes through its hole).\n\nThe skill should be robust to many variations, such as positions and poses of "
              "the stick and cylinder, size and dimensions of the cylinder and stick, colors of the objects, their densities, and "
              "the presence of distractors.")

_SCENES = """
You develop this program (the skill) in a session with a simulator of the robot to practise in. There is no program
yet, and nothing is told about the objects: the program has the cameras. The practice is yours to set up: you
choose the scenes, and in practice the simulator's TRUE state is open to you, to test and to measure with. The tools:

write_program, edit_program   save a new version of the program (v0, v1, ...). Any version can be run, so a
                              version can also be an experiment (a program that only looks and reports).
define_scenes                 set up practice scenes (see below) -> their names (s1, s2, ...) and what is in each
list_scenes                   what is in scenes: the objects' sizes, colours, densities and places
run_episodes                  run a version on scenes, one episode a scene. You get, for each episode: the judge's
                              verdict (the simulator's success check, on the TRUE state at the end), how the program
                              ended, the robot time, what the program printed, and the objects' TRUE positions at the
                              start and at the end{points}.
{eval_tool}read_output                   more of an episode's output: what the program printed and, on lines starting with "#",
                              a trace of every robot command: robot time, the command, its result, that arm's TRUE
                              tool position (and opening), and after NOW the TRUE position of each object that
                              moved. Compare what the program measured with the truth there.
view_images                   the overview camera's picture of the end of an episode, and the images the program
                              handed over with robot.show
read_program                  a version's text, with line numbers
submit                        name the version that is your result

The scenes. A scene has one cylinder (standing on the table, its hole upwards), one stand with its stick, and up to
{dis_n} other objects (distractors). define_scenes takes scenes as JSON objects in which every field can be left out; what
is left out is drawn at random within its range, by the scene's "seed" (the same object gives the same scene):

  {{"seed": 3,
   "cylinder": {{"diameter_mm": {cyl_d}, "height_mm": {cyl_h}, "color": "red", "density": {density}, "xy_mm": [x, y]}},
   "stand": {{"stick_diameter_mm": {stick_d}, "stick_height_mm": {stick_h}, "board_mm": [side, side, thickness],
             "color": "wood", "stick_color": "wood", "density": {density}, "xy_mm": [x, y], "yaw_deg": {yaw}}},
   "hole_clearance_mm": {clearance},
   "distractors": 2}}        (a number, or a list of {{"shape": "box" | "cylinder", "size_mm": [side, side, height] |
                            [diameter, height], "color": ..., "density": ..., "xy_mm": [x, y], "yaw_deg": ...}})

Lengths in mm, densities in kg/m3 (the weight follows from the size). The hole's diameter is the stick's plus
hole_clearance_mm ({clear0:g} if left out, and {clear0:g} in the final evaluation); the cylinder's wall is at least {wall:g} mm thick.
stick_height_mm is the stick's height above the board; the board's sides are {board}, its thickness {board_t}. A
distractor's sides or diameter are {dis_size}, its height {dis_h}. Colours: {colours}. xy_mm is where the
object's centre stands on the table: the cylinder and the stand within x {x}, y {y}, with at least {gap} mm
of free table between them; a distractor within x {dis_x}, y {dis_y}, at least {dis_gap} mm from the cylinder and
from the stand and {dis_dis} mm from another distractor (between the circles around their outlines); nothing stands on the
ruler that is fixed to the table (y {ruler}). A scene outside these limits is refused, with the reason. The
seed also fixes the episode's sensor noise and calibration error: a version on a scene gives the same result every
time, and run_episodes keeps results.

In a program, robot.show(label, image) hands an image to you (H x W x 3 uint8 RGB, or H x W; at most {shown} an episode;
no robot time): for instance a camera frame with what the program detected drawn on it (cv2). In practice episodes a
program can also read the simulator: robot.truth() -> {{name: [x, y, z]}} (mm), the objects' TRUE positions now (the
names and points of run_episodes' results), and robot.judge() -> the judge's verdict on the state as it is now (a dict;
"ok": success). Both cost no robot time, and both return None in the final evaluation: they are for measuring and
checking (how far is a measurement from the truth? did a step do what it should?), not for doing the task. {rules}

Your result is the version you submitted last. After the session it is run, without you, on the final evaluation:
{n_eval} episodes with new scenes, in which the program has the cameras and nothing of the simulator's state, judged by how
many succeed and then by the robot time it takes. Half of those scenes are drawn at random within the limits above{like}.
The other half go beyond these limits, in ways that cannot be set up here: sizes outside the ranges above, other
colours, other kinds of distractors and more of them, lighter and heavier objects, and other placements (objects
closer together, or elsewhere on the table within the arms' reach). So make the program reliable over many different
scenes first, without leaning on what only happens to hold in the scenes here; then fast.

The session's budget is ${budget:.0f} of model usage: your replies (the thinking included) and the conversation so far, which
is read again at every turn, are what costs; simulation is free. Every tool result ends with what is spent. So run
several scenes at once, read outputs selectively, and prefer edit_program to writing the whole program again. Submit
a version as soon as it is better than the one submitted before: when the budget runs out the session ends without
warning and the last submitted version counts (nothing if none). When you are satisfied, call submit with final = true.
"""

_EVAL_TOOL = """run_eval_set                  run a version on this session's self-evaluation set: the scenes E0 to E{last}, drawn at
                              random within the limits below and fixed (their names also work in list_scenes,
                              run_episodes, read_output and view_images). One line per episode.
"""

_FIRST_SCENE = """

There is no program yet.

The first look of the practice scene "example" (drawn at random; the arms at their start poses; the three cameras'
colour images):"""

EVAL_SET = (1000, 30)                                           # a session's self-evaluation set: scenes.sample of these seeds
EXAMPLE = 900                                                   # the scene of the first look: scenes.sample of this seed
AT_ONCE_SCENES = 8                                              # new episodes of one run_episodes call (the set: all at once)

NUDGE = "Go on with the tools; call submit with final = true when you are satisfied with a version."
NOTES = ("The conversation has grown long and will now be cut to save budget. Reply with notes of everything you want to keep "
         "(what you found out about the task, the robot and the cameras; what worked and what did not; what you plan next), "
         "without a tool call. After it you keep these notes, the versions with their notes, and all episode results "
         "(read_output, view_images); nothing else of the conversation.")


def _spec(name, description, properties, required=()):
    return dict(name=name, description=description,
                parameters={"type": "object", "properties": properties, "required": list(required), "additionalProperties": False})


_VER = {"type": "integer", "description": "the version's number (default: the newest)"}
TOOLS = [
    _spec("write_program", "Save a new version: the whole program.",
          {"code": {"type": "string", "description": "the whole Python file; it defines run(robot, task)"},
           "note": {"type": "string", "description": "one or two lines: what this version is"}}, ["code", "note"]),
    _spec("edit_program", "Save a new version: a version with pieces of its text replaced.",
          {"edits": {"type": "array", "description": "applied in order",
                     "items": {"type": "object", "additionalProperties": False, "required": ["old", "new"],
                               "properties": {"old": {"type": "string", "description": "text that occurs exactly once in the program (whitespace included)"},
                                              "new": {"type": "string", "description": "what replaces it"}}}},
           "note": {"type": "string", "description": "one or two lines: what this version changes"},
           "base": {"type": "integer", "description": "the version to edit (default: the newest)"}}, ["edits", "note"]),
    _spec("run_episodes", f"Run a version on practice episodes (at most {AT_ONCE} new ones a call, run at the same time; a few minutes).",
          {"seeds": {"type": "array", "items": {"type": "integer"}, "description": f"{SEEDS[0]} to {SEEDS[1]}"}, "version": _VER}, ["seeds"]),
    _spec("read_output", "Lines of an episode's output (the program's prints, its traceback, and the '#' trace of its robot commands).",
          {"version": _VER, "seed": {"type": "integer"},
           "start_line": {"type": "integer", "description": "default 1"},
           "max_lines": {"type": "integer", "description": "default 60, at most 200"},
           "contains": {"type": "string", "description": "only the lines that contain this text"},
           "trace": {"type": "boolean", "description": "false: without the '#' lines (default true)"}}, ["seed"]),
    _spec("view_images", "Images of an episode: 'final' (the overview camera at the end) and the names robot.show gave (at most 6 a call).",
          {"version": _VER, "seed": {"type": "integer"}, "names": {"type": "array", "items": {"type": "string"}}}, ["seed", "names"]),
    _spec("read_program", "A version's text with line numbers ('  12| ' is not part of the text).",
          {"version": _VER, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}),
    _spec("submit", "Name the version that is your result (it replaces an earlier submission). final = true ends the session.",
          {"version": _VER, "final": {"type": "boolean"}, "note": {"type": "string"}}, ["final"]),
]


class Session:
    """vlm: perception.vlm.VLM (or anything with its respond()); spent: () -> the session's spending so far in $ (the
    ledger's; default: the sum of what the replies report)."""

    def __init__(self, out, env, task, vlm, budget_usd=20.0, start=None, sensors="d415", segmenter=False, effort="high",
                 max_output_tokens=32000, n_eval=10, example_seed=100, spent=None, max_hours=8.0, compact_tokens=150000, log=print):
        self.dir = Path(out).resolve()
        (self.dir / "versions").mkdir(parents=True, exist_ok=True)
        self.env, self.task, self.vlm, self.budget, self.sensors = env, TASKS[task], vlm, float(budget_usd), sensors
        self.segmenter, self.effort, self.max_out, self.log = bool(segmenter), effort, int(max_output_tokens), log
        self.max_hours, self.compact_tokens, self.example_seed = float(max_hours), int(compact_tokens), int(example_seed)
        self.P = (getattr(vlm, "m", None) or {}).get("price_per_mtok") or PRICES
        self._spent = spent
        self.bridge = None
        kind = "grippers" if env == "sim_bimanual_grippers" else "dexterous"
        self.n_eval = int(n_eval)
        self.specs = self._specs()
        self.tools = openai_tools(self.specs)
        sf = self.dir / "state.json"
        if sf.exists():
            self.state = json.loads(sf.read_text())
            self.inp, self.images = pickle.loads((self.dir / "pending.pkl").read_bytes())
        else:
            self.state = dict(prev=None, turn=0, nudges=0, usd=0.0, at_risk=0.0, submitted=None, ended=None, versions=[], last=None,
                              calls=[], turn_usd=0.0, cache_fields=True, compactions=0, notes="", wall_s=0.0, start=bool(start))
            if start:
                code = Path(start).read_text()
                bad = practice.check_program(code)
                if bad:
                    raise ValueError(f"the starting program is not allowed: {'; '.join(bad)}")
                self._save(code, "the starting program")
            self.inp, self.images = self._first()
            self._keep()
            self._md("## The first message\n\n" + self.inp[0]["content"][0]["text"] + f"\n\n[{len(self.images)} images, with the arms' states]")
        self.instr = instructions(kind, self.task.time_limit_s, self.segmenter, closing=self._closing())
        (self.dir / "instructions.md").write_text(self.instr)

    # ------------------------------------------------------------------ what a kind of session sets (SceneSession)
    def _specs(self):
        return TOOLS

    def _closing(self):
        return _SESSION.format(
            lo=SEEDS[0], hi=SEEDS[1], shown=practice.SHOWN, points=practice.POINTS.get(self.task.name, ""), rules="\n" + practice.RULES,
            n_eval=self.n_eval, budget=self.budget, v0="; v0 is the starting program" if self.state["start"] else "",
            fallback=" (v0 if none)" if self.state["start"] else " (nothing if none)")

    def _intro(self):
        """The task, as every conversation of the session starts."""
        return self.task.text + "\n\nIn each episode the layout is new: " + VARIES.get(self.task.name, "the objects are elsewhere") + "."

    _EP = "seed"                                                # what an episode is named by, in texts

    # ------------------------------------------------------------------ state
    def _keep(self):
        (self.dir / "pending.pkl").write_bytes(pickle.dumps((self.inp, self.images)))
        (self.dir / "state.json").write_text(json.dumps(self.state, indent=1) + "\n")

    def _md(self, t):
        with open(self.dir / "transcript.md", "a") as f:
            f.write(t.rstrip() + "\n\n")

    def _jsonl(self, **rec):
        with open(self.dir / "session.jsonl", "a") as f:
            f.write(json.dumps(dict(rec, t=round(time.time(), 1))) + "\n")

    def spent(self):
        return float(self._spent()) if self._spent else float(self.state["usd"])

    def left(self):
        return self.budget - self.spent() - float(self.state["at_risk"])

    def _path(self, v):
        return self.dir / "versions" / f"v{int(v):02d}.py"

    def _save(self, code, note, base=None):
        v = len(self.state["versions"])
        self._path(v).write_text(code if code.endswith("\n") else code + "\n")
        self.state["versions"].append(dict(note=str(note)[:400], lines=len(code.splitlines()), base=base, runs={}))
        return v

    def _version(self, a, key="version"):
        n = len(self.state["versions"])
        if not n:
            raise ValueError("there is no version yet: write_program first")
        v = a.get(key)
        v = n - 1 if v is None else int(v)
        if not 0 <= v < n:
            raise ValueError(f"there is no v{v}: the versions are v0 to v{n - 1}")
        return v

    def _first(self):
        ep = Episode(self.env, self.task, seed=self.example_seed, sensors=self.sensors)
        start = _V0.format(code=self._path(0).read_text()) if self.state["start"] else "\nThere is no program yet.\n"
        t = self._intro() + _FIRST.format(start=start, seed=self.example_seed)
        images = []
        return [{"role": "user", "content": [{"type": "input_text", "text": t}]
                 + openai_items(Toolbox(ep.robot).call("look", {}, free=True), images)}], images

    def results_line(self):
        out = []
        for v, m in enumerate(self.state["versions"]):
            if m["runs"]:
                ok = [r[1] for r in m["runs"].values() if r[0]]
                out.append(f"v{v} {len(ok)}/{len(m['runs'])}" + (f" ({sum(ok) / len(ok):.0f} s)" if ok else ""))
        sub = self.state["submitted"]
        return ("Practice so far (successes/episodes, mean robot time of the successes): " + ("; ".join(out) or "nothing run")
                + f". Submitted: {'v' + str(sub) if sub is not None else 'nothing'}.")

    # ------------------------------------------------------------------ the tools
    def tool(self, name, a):
        """One tool call -> the result's items. Never raises for a bad call."""
        try:
            if not isinstance(a, dict):
                raise ValueError("the arguments are not a JSON object (was the reply cut at its length limit? Write less in one call)")
            fn = {s["name"]: getattr(self, "_t_" + s["name"]) for s in self.specs}.get(name)
            if fn is None:
                raise ValueError(f"unknown tool {name}: there are {', '.join(s['name'] for s in self.specs)}")
            items = fn(a)
        except (KeyError, ValueError, TypeError, IndexError) as e:
            items = [text(f"bad call: {str(e) or type(e).__name__}")]
        except Exception as e:                                  # a fault of the harness must not lose the session
            self.log(f"TOOL FAILED: {name}: {type(e).__name__}: {e}")
            items = [text(f"the tool failed ({type(e).__name__}: {str(e)[:300]}): try again, or otherwise")]
        for i in items:
            if i["type"] == "text" and len(i["text"]) > 30000:
                i["text"] = i["text"][:30000] + "\n... (cut: ask for less at once)"
        return items

    def _new(self, code, note, base=None):
        bad = practice.check_program(code)
        if bad:
            return [text("NOT saved, the program is not allowed:\n  " + "\n  ".join(bad) + "\n" + practice.RULES)]
        v = self._save(code, note, base)
        return [text(f"saved as v{v} ({self.state['versions'][v]['lines']} lines)")]

    def _t_write_program(self, a):
        return self._new(str(a["code"]), a.get("note", ""))

    def _t_edit_program(self, a):
        base = self._version(a, "base")
        code, bad = self._path(base).read_text(), []
        for k, e in enumerate(a["edits"]):
            n = code.count(e["old"]) if e["old"] else 0
            if n != 1:
                bad.append(f"edit {k + 1}: its `old` text occurs {n} times in the program (after the edits before it), not once: {e['old'][:80]!r}")
            else:
                code = code.replace(e["old"], e["new"])
        if bad:
            return [text(f"NOT saved (v{base} is unchanged; no edit was applied):\n  " + "\n  ".join(bad))]
        return self._new(code, a.get("note", ""), base)

    def _run_dir(self, v, seed):
        return self.dir / "runs" / f"v{v:02d}" / f"s{seed}"

    def _results(self, v, keys, brief=False):
        """The tool result for the episodes `keys` of version v, as run (their directories)."""
        items, runs, failed = [], self.state["versions"][v]["runs"], []
        for s in keys:
            d = self._run_dir(v, s)
            if not (d / "result.json").exists():                # the process gave no result: not kept, it can be run again
                items.append(text(f"v{v} {self._EP} {s}: NO RESULT: the episode's process was stopped (15 min of computing: a loop without "
                                  "robot commands?) or crashed" + ("" if brief else _printed(d))))
                continue
            r = json.loads((d / "result.json").read_text())
            runs[str(s)] = [bool(r["ok"]), r["robot_s"]]
            failed += [] if r["ok"] else [(s, d)]
            items.append(text(_episode_line(v, s, r, d, self._EP) if brief else _episode_text(v, s, r, d, self._EP)))
        if brief:
            items = [text("\n".join(i["text"] for i in items))]
        for s, d in ([] if brief else failed[:3]):
            items += [text(f"v{v} {self._EP} {s}, the overview camera at the end:"), _image(d / "final.jpg")]
        n = [runs[str(s)][0] for s in keys if str(s) in runs]
        items.append(text(f"v{v}: {sum(n)} of these {len(n)} episodes succeeded. " + self.results_line()))
        return items

    def _t_run_episodes(self, a):
        v = self._version(a)
        seeds = list(dict.fromkeys(int(s) for s in a["seeds"]))
        if not seeds or any(not SEEDS[0] <= s <= SEEDS[1] for s in seeds):
            raise ValueError(f"seeds: practice episodes are the seeds {SEEDS[0]} to {SEEDS[1]}")
        new = [s for s in seeds if not (self._run_dir(v, s) / "result.json").exists()]
        if len(new) > AT_ONCE:
            raise ValueError(f"at most {AT_ONCE} new episodes a call")
        if new:
            if self.segmenter and self.bridge is None:
                self.bridge = practice.SegmenterBridge()
            with ThreadPoolExecutor(len(new)) as ex:
                list(ex.map(lambda s: practice.run_sandboxed(self.env, self.task.name, s, self._path(v), self._run_dir(v, s), sensors=self.sensors,
                                                              trace=True, bridge=self.bridge, wall_s=900.0), new))
        return self._results(v, seeds)

    def _episode(self, a):
        v, s = self._version(a), int(a["seed"])
        d = self._run_dir(v, s)
        if not (d / "output.txt").exists():
            raise ValueError(f"v{v} was not run on seed {s}")
        return v, s, d

    def _t_read_output(self, a):
        v, s, d = self._episode(a)
        lines = (d / "output.txt").read_text(errors="replace").splitlines()
        k0, n = max(1, int(a.get("start_line") or 1)), min(200, max(1, int(a.get("max_lines") or 60)))
        has, tr = a.get("contains"), a.get("trace", True)
        sel = [(k, l) for k, l in enumerate(lines, 1)
               if k >= k0 and (tr or not l.startswith("#")) and (not has or has.lower() in l.lower())]
        out = [f"{k:5d}| {l[:300]}" for k, l in sel[:n]]
        more = f"; the next one is line {sel[n][0]}" if len(sel) > n else ""
        return [text(f"v{v} {self._EP} {s}: output.txt has {len(lines)} lines; {len(sel)} from line {k0} on match; {len(out)} shown{more}\n" + "\n".join(out))]

    def _t_view_images(self, a):
        v, s, d = self._episode(a)
        shown = sorted(p.name for p in (d / "shown").glob("*.png")) if (d / "shown").exists() else []
        items = []
        for name in list(a["names"])[:6]:
            name = str(name)
            hit = [n for n in shown if name in (n, n[:-4], n[3:-4], n[:2])]
            if name == "final" and (d / "final.jpg").exists():
                items += [text(f"v{v} {self._EP} {s}, the overview camera at the end:"), _image(d / "final.jpg")]
            elif hit:
                items += [text(f"v{v} {self._EP} {s}, {hit[0]}:"), _image(d / "shown" / hit[0])]
            else:
                items.append(text(f"no image {name!r}: this episode has final" + "".join(", " + n for n in shown)))
        return items or [text("names: which images?")]

    def _t_read_program(self, a):
        v = self._version(a)
        lines = self._path(v).read_text().splitlines()
        k0 = max(1, int(a.get("start_line") or 1)); k1 = min(len(lines), int(a.get("end_line") or len(lines)))
        m = self.state["versions"][v]
        return [text(f"v{v} ({len(lines)} lines; {m['note']}), lines {k0} to {k1}:\n" + "\n".join(f"{k:4d}| {lines[k - 1]}" for k in range(k0, k1 + 1)))]

    def _t_submit(self, a):
        v = self._version(a)
        self.state["submitted"] = v
        runs = self.state["versions"][v]["runs"]
        t = f"v{v} is now your submitted result" + ("" if runs else " (it was not run on any practice episode)") + ". "
        if a.get("final"):
            self.state["ended"] = "submitted"
            return [text(t + "The session has ended.")]
        return [text(t + self.results_line())]

    # ------------------------------------------------------------------ the conversation
    def _plan(self, inp, images):
        """-> (the reply's token limit that the budget still allows, the call's worst-case cost)."""
        last = self.state["last"] or {}
        ctx = (last.get("input", 0) + last.get("output", 0)) if self.state["prev"] else 0
        new = (len(json.dumps(inp)) + len(self.instr) + len(json.dumps(self.tools))) / 2.5 + IMAGE_TOKENS * len(images)
        cin = ((ctx + new) * 1.2 + 2000) * self.P["in"] / 1e6
        out = int(min(self.max_out, (self.left() - cin) / self.P["out"] * 1e6))
        return out, cin + max(out, 0) * self.P["out"] / 1e6

    def _model(self, inp, images, max_out, worst):
        """One reply, with retries -> the reply, or dict(ok=False, why, stop=how the session ends or None)."""
        for wait in (60, 300, 900, None):
            if hasattr(self.vlm, "cfg"):
                self.vlm.cfg["max_usd_per_call"] = worst
            extra = dict(prompt_cache_key=self.dir.name[:60], prompt_cache_retention="24h") if self.state["cache_fields"] else None
            r = self.vlm.respond(inp, images, instructions=self.instr, tools=self.tools, previous=self.state["prev"], effort=self.effort,
                                 max_output_tokens=max_out, extra=extra, timeout_s=1800)
            if r.get("ok"):
                return r
            why = str(r.get("why"))
            self.log(f"model call failed: {why[:300]}")
            self._jsonl(kind="failed_call", why=why[:600])
            if "prompt_cache" in why and self.state["cache_fields"]:
                self.state["cache_fields"] = False              # this model does not take the fields: without them
                continue
            if "model budget" in why:
                return dict(r, stop="budget")
            if "context" in why.lower() and any(w in why.lower() for w in ("length", "window", "exceed")):
                return dict(r, stop=None, overflow=True)
            if any(c in why for c in ("failed: 400", "failed: 401", "failed: 403", "failed: 404")):
                return dict(r, stop=f"model: {why[:300]}")
            if "Timeout" in why or "timed out" in why:           # the provider may still bill a reply that never arrived
                self.state["at_risk"] += worst; self._keep()
            if wait is None or self.left() < worst:
                break
            time.sleep(wait)
        return dict(ok=False, why=why, stop=f"model: {why[:300]}")

    def _compact(self, notes, pending):
        """A new conversation: the task, the model's notes, the versions and the newest program, the pending results."""
        st = self.state
        st["notes"] = notes or st["notes"]
        st["prev"], st["last"] = None, None
        st["compactions"] += 1
        vs = "\n".join(f"v{v} ({m['lines']} lines{', from v' + str(m['base']) if m['base'] is not None else ''}): {m['note']} | practice: "
                       + (", ".join(f"{self._EP} {s} {'ok' if r[0] else 'FAIL'} {r[1]} s" for s, r in m["runs"].items()) or "not run")
                       for v, m in enumerate(st["versions"]))
        newest = len(st["versions"]) - 1
        t = (self._intro() + "\n\n"
             "This session started earlier; the conversation so far was cut to save budget. You keep your notes, the versions "
             "(read_program) and all episode results (read_output, view_images).\n\nThe versions:\n" + vs + "\n\n" + self.results_line()
             + "\n\nYour notes:\n" + (st["notes"] or "(none)")
             + (f"\n\nv{newest}, the newest version:\n\n```python\n{self._path(newest).read_text()}```\n" if newest >= 0 else "")
             + ("\n\nThe result of your last tool call:\n" + pending[:8000] if pending else ""))
        self.inp, self.images = [{"role": "user", "content": [{"type": "input_text", "text": t}]}], []
        self._md(f"### (the conversation was cut; it goes on from the model's notes)\n\n{st['notes']}")

    def run(self):
        """The session, to its end -> how it ended. (A session stopped by a failing model call, or killed, can be
        resumed: the state is kept after every step.)"""
        st, t0 = self.state, time.time()
        try:
            while not st["ended"]:
                if st["wall_s"] + time.time() - t0 > self.max_hours * 3600:
                    st["ended"] = f"wall time: {self.max_hours:g} h"; break
                if not st["calls"]:
                    stop = self._turn()
                    if stop:
                        return stop                             # not ended: --resume goes on from here
                    continue
                results, self.inp, self.images = [], [], []
                for c in st["calls"]:                           # the reply's tool calls (again, if the session was killed in them)
                    try:
                        args = json.loads(c.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = None
                    items = self.tool(c.get("name"), args)
                    res = _texts_of(items)
                    results.append(dict(name=c.get("name"), args=_short(args), result=res))
                    self.log(f"> {c.get('name')} {json.dumps(_short(args))[:300]}\n  {res[:600]}")
                    self._md(f"**{c.get('name')}** `{json.dumps(_short(args))[:1500]}`\n\n```\n{res[:6000]}\n```")
                    self.inp.append({"type": "function_call_output", "call_id": c.get("call_id"), "output": items})
                self._jsonl(kind="calls", turn=st["turn"], calls=results)
                if st["ended"]:
                    break
                self.inp[-1]["output"].append(text(self._budget_note()))
                for i in self.inp:                              # the items as the model client takes them
                    i["output"] = openai_items(i["output"], self.images)
                st["calls"] = []
                tk = st["last"] or {}
                cached = tk.get("cached", 0) >= 0.5 * max(tk.get("input", 0), 1)
                if tk.get("input", 0) + tk.get("output", 0) > self.compact_tokens * (1.0 if cached else 0.35):
                    self._ask_notes()
                self._keep()
        finally:
            st["wall_s"] = round(st["wall_s"] + time.time() - t0, 1)
            if self.bridge is not None:
                self.bridge.close(); self.bridge = None
            self._keep()
        return self._finish()

    def _budget_note(self):
        left, st = self.left(), self.state
        note = f"[budget: ${self.budget - left:.2f} of ${self.budget:.0f} spent; that turn cost ${st['turn_usd']:.2f}]"
        if left < 0.2 * self.budget:
            note += " Little is left: make sure your best version is submitted" + ("." if st["submitted"] is not None else " (nothing is submitted yet).")
        return note

    def _turn(self):
        """One reply of the model to the pending input -> None, or why the session stops here (unfinished). The reply's
        tool calls become the state's pending calls; without one, the pending input is a nudge."""
        st = self.state
        max_out, worst = self._plan(self.inp, self.images)
        if max_out < MIN_REPLY:
            st["ended"] = "budget"; return None
        r = self._model(self.inp, self.images, max_out, worst)
        if not r.get("ok"):
            if r.get("overflow"):
                self._compact("", _texts(self.inp)); self._keep(); return None
            if r.get("stop") == "budget":
                st["ended"] = "budget"; return None
            self._keep()
            return r.get("stop") or "model"
        st["turn"] += 1
        st["turn_usd"] = float(r.get("usd") or 0.0)
        st["usd"] = round(st["usd"] + st["turn_usd"], 5)
        st["prev"], st["last"] = r["id"], r.get("tokens") or {}
        tk = st["last"]
        said = _said(r)
        st["calls"] = [dict(call_id=o.get("call_id"), name=o.get("name"), arguments=o.get("arguments"))
                       for o in r["output"] if o.get("type") == "function_call"]
        head = (f"turn {st['turn']}: ${st['turn_usd']:.2f} (in {tk.get('input', 0)}, cached {tk.get('cached', 0)}, out {tk.get('output', 0)}, "
                f"reasoning {tk.get('reasoning', 0)}; {r.get('wait_s', 0):.0f} s); spent ${self.spent():.2f} of ${self.budget:.2f}")
        self.log(head + (f"\n  model: {said[:500]}" if said else ""))
        self._md(f"## {head}" + (f"\n\n{said}" if said else ""))
        self._jsonl(kind="turn", turn=st["turn"], usd=r.get("usd"), tokens=tk, wait_s=r.get("wait_s"), status=r.get("status"), said=said,
                    calls=[dict(c, arguments=str(c["arguments"])[:20000]) for c in st["calls"]], spent=round(self.spent(), 4))
        self.inp, self.images = [], []
        if not st["calls"]:
            st["nudges"] += 1
            if st["nudges"] > 3:
                st["ended"] = "the model stopped calling tools"; return None
            cut = (f"Your reply was cut at its length limit ({max_out} tokens, thinking included) before a tool call: think less at "
                   "once and make smaller steps. ") if r.get("status") == "incomplete" else ""
            self.inp = [{"role": "user", "content": [{"type": "input_text", "text": cut + NUDGE + " " + self._budget_note()}]}]
        else:
            st["nudges"] = 0
        self._keep()
        return None

    def _ask_notes(self):
        """The conversation is long: one more turn for the model's notes, then a new conversation from them."""
        pending = _texts(self.inp)
        inp = self.inp + [{"role": "user", "content": [{"type": "input_text", "text": NOTES}]}]
        max_out, worst = self._plan(inp, self.images)
        notes = ""
        if max_out >= MIN_REPLY:
            r = self._model(inp, self.images, min(max_out, 12000), worst)
            if r.get("ok"):
                self.state["usd"] = round(self.state["usd"] + float(r.get("usd") or 0.0), 5)
                notes = _said(r)
                self._jsonl(kind="notes", usd=r.get("usd"), tokens=r.get("tokens"), said=notes)
        self._compact(notes, pending)

    def _finish(self):
        st = self.state
        sub = st["submitted"] if st["submitted"] is not None else (0 if st["start"] else None)
        if sub is not None:
            shutil.copyfile(self._path(sub), self.dir / "program.py")
        res = dict(ended=st["ended"], submitted=st["submitted"], result_version=sub, turns=st["turn"], versions=st["versions"],
                   task=self.task.name, env=self.env, sensors=self.sensors, budget_usd=self.budget, spent_usd=round(self.spent(), 4),
                   compactions=st["compactions"], wall_s=st["wall_s"], effort=self.effort)
        (self.dir / "result.json").write_text(json.dumps(res, indent=1) + "\n")
        self._md(f"## The session ended: {st['ended']}. Result: {'v' + str(sub) if sub is not None else 'none'}; ${self.spent():.2f} spent.")
        return st["ended"]


_NAME = {"type": "string", "description": "a scene's name"}
_N, _XY = {"type": "number"}, {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
_COL = {"type": "string", "enum": list(scenes.COLOURS)}


def _obj(description=None, **props):
    return dict({"type": "object", "properties": props, "additionalProperties": False}, **({"description": description} if description else {}))


_SCENE_SPEC = _obj(
    "a scene (the instructions: The scenes); every field can be left out",
    seed={"type": "integer"},
    cylinder=_obj(diameter_mm=_N, height_mm=_N, color=_COL, density=_N, xy_mm=_XY),
    stand=_obj(stick_diameter_mm=_N, stick_height_mm=_N, board_mm={"type": "array", "items": _N, "minItems": 3, "maxItems": 3},
               color=_COL, stick_color=_COL, density=_N, xy_mm=_XY, yaw_deg=_N),
    hole_clearance_mm=_N,
    distractors={"anyOf": [{"type": "integer", "description": "how many"},
                           {"type": "array", "items": _obj(shape={"type": "string", "enum": list(scenes.SHAPES)},
                                                           size_mm={"type": "array", "items": _N}, color=_COL, density=_N, xy_mm=_XY, yaw_deg=_N)}]})


def _scene_tools(eval_set):
    keep = {s["name"]: s for s in TOOLS}
    out = [keep["write_program"], keep["edit_program"],
           _spec("define_scenes", "Set up practice scenes -> their names and what is in each.",
                 {"scenes": {"type": "array", "items": _SCENE_SPEC}}, ["scenes"]),
           _spec("list_scenes", "What is in scenes (sizes, colours, densities, places); without names: all of them.",
                 {"names": {"type": "array", "items": _NAME}}),
           _spec("run_episodes", f"Run a version on scenes, one episode each (at most {AT_ONCE_SCENES} new ones a call, run at the same time; a few minutes).",
                 {"scenes": {"type": "array", "items": _NAME}, "version": _VER}, ["scenes"])]
    if eval_set:
        out.append(_spec("run_eval_set", "Run a version on the whole self-evaluation set (E0, E1, ...): one line per episode.", {"version": _VER}))
    for name in ("read_output", "view_images"):
        p = keep[name]["parameters"]
        props = {("scene" if k == "seed" else k): (_NAME if k == "seed" else x) for k, x in p["properties"].items()}
        out.append(_spec(name, keep[name]["description"], props, ["scene" if k == "seed" else k for k in p["required"]]))
    return out + [keep["read_program"], keep["submit"]]


class SceneSession(Session):
    """A session on a task with varied scenes (scenes.py): the model starts from nothing, sets up its own practice
    scenes within the training limits (define_scenes), and its practice episodes run with the simulation's truth
    (practice.py: practice). eval_set: it also gets a fixed self-evaluation set (EVAL_SET) to run versions on.
    Episodes are named by their scene."""
    _EP = "scene"

    def __init__(self, *a, eval_set=False, **k):
        self.eval_set = bool(eval_set)
        super().__init__(*a, **k)
        if self.state.get("eval_set", self.eval_set) != self.eval_set:
            raise ValueError("this session was started " + ("with" if self.state["eval_set"] else "without") + " the self-evaluation set")

    def _specs(self):
        return _scene_tools(self.eval_set)

    def _eval_names(self):
        return [f"E{k}" for k in range(EVAL_SET[1])] if self.eval_set else []

    def _closing(self):
        T = scenes.TRAIN
        r = lambda key: f"{T[key][0]:g}..{T[key][1]:g}"
        return _SCENES.format(
            points=practice.POINTS.get(self.task.name, ""), shown=practice.SHOWN, rules="\n" + practice.RULES, n_eval=self.n_eval, budget=self.budget,
            eval_tool=_EVAL_TOOL.format(last=EVAL_SET[1] - 1) if self.eval_set else "",
            like=f" (as this session's self-evaluation set is; its {EVAL_SET[1]} scenes are not among them)" if self.eval_set else "",
            dis_n=T["dis_n"][1], clear0=scenes.CLEARANCE, wall=scenes.WALL, colours=", ".join(scenes.COLOURS), gap=T["gap"], dis_gap=T["dis_gap"],
            dis_dis=T["dis_dis"], ruler=f"{scenes.RULER[0]:g}..{scenes.RULER[1]:g}",
            **{key: r(key) for key in ("cyl_d", "cyl_h", "density", "stick_d", "stick_h", "yaw", "clearance", "board", "board_t", "dis_size",
                                       "dis_h", "x", "y", "dis_x", "dis_y")})

    def _intro(self):
        return self.task.text + " " + ASK_VARIED

    def _first(self):
        st = self.state
        st["eval_set"] = self.eval_set
        st["scenes"] = {"example": scenes.sample(EXAMPLE)}
        st["scenes"].update({n: scenes.sample(EVAL_SET[0] + k) for k, n in enumerate(self._eval_names())})
        st["n_scenes"] = 0
        sc = st["scenes"]["example"]
        ep = Episode(self.env, self.task, seed=sc["seed"], sensors=self.sensors, scene=sc)
        images = []
        return [{"role": "user", "content": [{"type": "input_text", "text": self._intro() + _FIRST_SCENE}]
                 + openai_items(Toolbox(ep.robot).call("look", {}, free=True), images)}], images

    def results_line(self):
        out, ev = [], set(self._eval_names())
        for v, m in enumerate(self.state["versions"]):
            parts = []
            for what, runs in (("", [r for s, r in m["runs"].items() if s not in ev]), ("self-evaluation set ", [r for s, r in m["runs"].items() if s in ev])):
                if runs:
                    ok = [r[1] for r in runs if r[0]]
                    parts.append(f"{what}{len(ok)}/{len(runs)}" + (f" ({sum(ok) / len(ok):.0f} s)" if ok else ""))
            if parts:
                out.append(f"v{v} " + ", ".join(parts))
        sub = self.state["submitted"]
        return ("Practice so far (successes/episodes, mean robot time of the successes): " + ("; ".join(out) or "nothing run")
                + f". Submitted: {'v' + str(sub) if sub is not None else 'nothing'}.")

    # ------------------------------------------------------------------ the tools
    def _t_define_scenes(self, a):
        st, out = self.state, []
        specs = a["scenes"]
        if not isinstance(specs, list) or not specs or len(specs) > 12:
            raise ValueError("scenes: a list of 1 to 12 scene objects")
        for spec in specs:
            n = st["n_scenes"] + 1
            try:
                if not isinstance(spec, dict):
                    raise ValueError("a scene is a JSON object")
                seed = spec.get("seed", n)
                if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 1 << 30:
                    raise ValueError("seed: a whole number from 0 on")
                sc = scenes.build(spec, seed=seed)
            except (ValueError, TypeError, KeyError) as e:
                out.append(f"REFUSED ({json.dumps(spec)[:200]}): {str(e) or type(e).__name__}")
                continue
            st["n_scenes"] = n
            st["scenes"][f"s{n}"] = sc
            out.append(f"s{n} (seed {sc['seed']}): {scenes.describe(sc)}")
        return [text("\n".join(out))]

    def _names(self, names):
        names = list(dict.fromkeys(str(n) for n in names))
        bad = [n for n in names if n not in self.state["scenes"]]
        if bad:
            raise ValueError(f"no scene {', '.join(bad)}: there are {self._scene_names()}")
        return names

    def _scene_names(self):
        st, ev = self.state, self._eval_names()
        return ("example" + (f", {ev[0]} to {ev[-1]}" if ev else "")
                + (f", s1 to s{st['n_scenes']}" if st["n_scenes"] > 1 else ", s1" if st["n_scenes"] else "") + " (define_scenes makes more)")

    def _t_list_scenes(self, a):
        names = self._names(a.get("names") or list(self.state["scenes"]))
        return [text("\n".join(f"{n} (seed {self.state['scenes'][n]['seed']}): {scenes.describe(self.state['scenes'][n])}" for n in names))]

    def _run_dir(self, v, name):
        return self.dir / "runs" / f"v{v:02d}" / str(name)

    def _run(self, v, names):
        new = [n for n in names if not (self._run_dir(v, n) / "result.json").exists()]
        if new:
            if self.segmenter and self.bridge is None:
                self.bridge = practice.SegmenterBridge()
            sc = self.state["scenes"]
            with ThreadPoolExecutor(len(new)) as ex:                # (practice.SLOTS bounds how many run at the same time)
                list(ex.map(lambda n: practice.run_sandboxed(self.env, self.task.name, sc[n]["seed"], self._path(v), self._run_dir(v, n),
                                                              sensors=self.sensors, trace=True, bridge=self.bridge, wall_s=900.0,
                                                              scene=sc[n], practice=True), new))

    def _t_run_episodes(self, a):
        v, names = self._version(a), self._names(a["scenes"])
        if not names:
            raise ValueError(f"scenes: which? There are {self._scene_names()}")
        if sum(not (self._run_dir(v, n) / "result.json").exists() for n in names) > AT_ONCE_SCENES:
            raise ValueError(f"at most {AT_ONCE_SCENES} new episodes a call" + (" (run_eval_set runs the whole self-evaluation set)" if self.eval_set else ""))
        self._run(v, names)
        return self._results(v, names)

    def _t_run_eval_set(self, a):
        v, names = self._version(a), self._eval_names()
        self._run(v, names)
        return self._results(v, names, brief=True)

    def _episode(self, a):
        v, n = self._version(a), str(a["scene"])
        d = self._run_dir(v, n)
        if n not in self.state["scenes"] or not (d / "output.txt").exists():
            raise ValueError(f"v{v} was not run on a scene {n}")
        return v, n, d


def _said(r):
    return "\n".join(c.get("text", "") for o in r["output"] if o.get("type") == "message"
                     for c in o.get("content") or [] if c.get("type") == "output_text").strip()


def _image(path):
    import cv2
    return {"type": "image", "rgb": cv2.imread(str(path))[..., ::-1].copy(), "name": Path(path).stem}


def _texts_of(items):
    return "\n".join(i["text"] if i["type"] == "text" else f"[image: {i.get('name', '')}]" for i in items)


def _texts(inp):
    """The text of pending input items (already in the model client's form)."""
    out = []
    for i in inp:
        for c in i.get("output") or i.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "input_text":
                out.append(c["text"])
    return "\n".join(out)


def _short(args):
    if not isinstance(args, dict):
        return args
    return {k: (f"<{len(v.splitlines())} lines>" if k == "code" and isinstance(v, str) else v) for k, v in args.items()}


def _printed(d, head=12, tail=25):
    f = Path(d) / "output.txt"
    if not f.exists():
        return ""
    pr = [l[:200] for l in f.read_text(errors="replace").splitlines() if not l.startswith("#")]
    if not pr:
        return "\n  it printed nothing"
    if len(pr) <= head + tail:
        return f"\n  printed ({len(pr)} lines):\n" + "\n".join("    " + l for l in pr)
    return (f"\n  printed (the first {head} and the last {tail} of {len(pr)} lines):\n" + "\n".join("    " + l for l in pr[:head])
            + "\n    ...\n" + "\n".join("    " + l for l in pr[-tail:]))


_BOOK = ("ok", "task", "env", "seed", "robot_s", "mode", "ended", "sensors", "commands", "shown", "true_start_mm", "true_end_mm", "wall_s",
         "scene")


def _episode_line(v, s, r, d, ep="seed"):
    """An episode's result in one line (a whole evaluation set at once)."""
    j = {k: x for k, x in r.items() if k in ("on_stick", "released", "stand_upright", "off_axis_mm", "stick_through_mm")}
    return (f"v{v} {ep} {s}: {'SUCCESS' if r['ok'] else 'FAILED'}, {r['robot_s']} s; the program "
            f"{'returned' if r['ended'] == 'returned' else 'ended by ' + str(r['ended'])[:160]}" + ("" if r["ok"] else f"; judge: {json.dumps(j)}"))


def _episode_text(v, s, r, d, ep="seed"):
    pos = lambda t: "; ".join(f"{n} {tuple(p) if isinstance(p, list) else p}" for n, p in t.items())
    shown = sorted(p.name for p in (Path(d) / "shown").glob("*.png")) if (Path(d) / "shown").exists() else []
    n_lines = len((Path(d) / "output.txt").read_text(errors="replace").splitlines())
    return (f"v{v} {ep} {s}: {'SUCCESS' if r['ok'] else 'FAILED'}, {r['robot_s']} s of robot time, {r['commands']} robot commands; the program "
            f"{'returned' if r['ended'] == 'returned' else 'ended by ' + str(r['ended'])}\n"
            f"  judge: {json.dumps({k: x for k, x in r.items() if k not in _BOOK})}\n"
            f"  TRUE positions (mm) at the start: {pos(r['true_start_mm'])}\n  TRUE positions at the end: {pos(r['true_end_mm'])}"
            + (f"\n  shown: {', '.join(shown)}" if shown else "") + f"\n  output.txt: {n_lines} lines with the trace" + _printed(d))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", required=True, choices=["sim_bimanual_grippers", "sim_bimanual_dexterous"])
    ap.add_argument("--task", required=True, choices=list(TASKS))
    ap.add_argument("--model", required=True, help="a model of configs/models.yaml")
    ap.add_argument("--start", help="the starting program (v0); none: the model starts from nothing")
    ap.add_argument("--eval-set", action="store_true",
                    help="a task with varied scenes (insert_varied): the model also gets a fixed self-evaluation set")
    ap.add_argument("--budget-usd", type=float, required=True, help="the session's hard cap on model spending")
    ap.add_argument("--sensors", default="d415", choices=["d415", "ideal"])
    ap.add_argument("--segmenter", choices=["sam3"], help="robot.segment() through the SAM3 service")
    ap.add_argument("--effort", default="high", help="reasoning effort")
    ap.add_argument("--max-output-tokens", type=int, default=32000, help="per reply, reasoning included")
    ap.add_argument("--max-hours", type=float, default=8.0)
    ap.add_argument("--eval-episodes", type=int, default=10, help="how many episodes the model is told its result is judged on")
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume", action="store_true", help="go on with the session in --out")
    a = ap.parse_args(argv)

    out = Path(a.out)
    if (out / "state.json").exists() != a.resume:
        ap.error("--out holds a session: --resume goes on with it" if not a.resume else "--resume: no session in --out")
    from ..perception import vlm as V
    tag = f"bimanual_improve_{out.resolve().name}"
    vlm = V.VLM(dict(model=a.model, converse=True, max_calls_per_episode=100000, max_usd_per_episode=a.budget_usd, run_usd=a.budget_usd,
                     max_usd_per_call=a.budget_usd), tag=f"{tag}:{a.env}:{a.task}")
    kw = dict(budget_usd=a.budget_usd, sensors=a.sensors, segmenter=bool(a.segmenter), effort=a.effort, max_output_tokens=a.max_output_tokens,
              n_eval=a.eval_episodes, max_hours=a.max_hours, spent=lambda: V.spent(tag), log=lambda *x: print(*x, flush=True))
    if TASKS[a.task].scene:
        if a.start:
            ap.error("--start: a session on a task with varied scenes starts from nothing")
        s = SceneSession(out, a.env, a.task, vlm, eval_set=a.eval_set, **kw)
    elif a.eval_set:
        ap.error("--eval-set: only for a task with varied scenes")
    else:
        s = Session(out, a.env, a.task, vlm, start=a.start, **kw)
    how = s.run()
    print(json.dumps(dict(ended=s.state["ended"], stopped=None if s.state["ended"] else how, submitted=s.state["submitted"],
                          turns=s.state["turn"], spent_usd=round(s.spent(), 4))))
    return 0 if s.state["ended"] else 1


if __name__ == "__main__":
    sys.exit(main())
