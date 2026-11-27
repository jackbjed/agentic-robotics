"""One development session of the experiments (a condition of a replicate of a task): its record, its allowances, and
the handlers of the ONE command its agent has (xsim/bin/xsim-tool SUB ...). What a condition may do is decided HERE,
by the session's condition in its record (which the agent cannot reach: the record lies outside its workspace), not by
the agent's instructions.

  condition   save_program  save_world  validate_world  practice (MuJoCo)  submit_target  target_practice
  build            -            yes          yes               -                 -               -
  B               yes            -            -                -                yes              -
  C               yes            -           yes (as is)      yes               yes              -
  D               yes           yes          yes              yes               yes              -
  E               yes           yes          yes              yes                -               -
  F               yes            -            -                -                 -              yes
  (A has no command at all: file tools only.)   every condition with a command also has: note, status, finish

Records (never overwritten; saved files are made read-only):
  versions/v<k>.py (+ .diff, the note)            every saved program            worlds/w<k>.py (+ _agent.py, .diff)   every saved world
  practice/p<n>/                                  every MuJoCo practice episode  validation/                           world checks
  rounds/round_<k>/{round.json, payload/}         every target submission: the record and the exact payload returned
  fpractice/e<n>/                                 (F) every target practice episode
  revisions.jsonl                                 every save and note, linked to the feedback round before it
  checkpoints/usd_<x>/                            the latest complete program (and world) when spending passed $x
  <replicate>/private/...                         the evaluator's own records: NOT in this directory"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from xsim import target  # noqa: E402
from xsim.tasks import TASKS  # noqa: E402

SRC = target.SRC
PERMS = {"build": {"save_world", "validate_world"}, "B": {"save_program", "submit_target"},
         "C": {"save_program", "validate_world", "practice", "submit_target"},
         "D": {"save_program", "save_world", "validate_world", "practice", "submit_target"},
         "E": {"save_program", "save_world", "validate_world", "practice"}, "F": {"save_program", "target_practice"}}
ALWAYS = {"note", "status", "finish"}
FILE_BYTES = 400_000
PER_CALL = 8
INTERFACE_CHECK = '''def run(robot, task):
    # the harness's own check that a world supports the documented interface (not a skill, not the task)
    frames = robot.look()
    for name, f in frames.items():
        robot.show(name, f.rgb)
        print("camera", name, f.rgb.shape, "depth seen on", int((f.depth == f.depth).mean() * 100), "% of the pixels")
    s = robot.state("left")
    print("state", s)
    p = s["tcp_mm"]
    print("move", robot.move("left", (p[0], p[1], p[2] - 30), (0, 0, -1), (0, 1, 0)))
    print("gripper", robot.set_gripper("left", 40), robot.set_gripper("left", 80))
    print("home", robot.home("left"))
'''


class Refused(Exception):
    """What the agent asked for is not allowed or not possible: said to it as plain text."""


def sha(text):
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()[:16]


def world_footer(task_text, has_success=True):
    """What the harness adds to an agent's world file: the tabletop stays at z = 0, the cameras stay as documented, the
    task's text stays the experiment's, and a world without a self-check gets one that says so."""
    out = ('\n\n# ---- added by the harness (not the agent\'s): the fixed parts of every practice world\n'
           '_agent_scene = scene\n\n\ndef scene(scene, rng):\n    sc = _agent_scene(scene, rng)\n    t = dict(sc.get("table") or {})\n'
           '    t["height_mm"] = 0.0\n    sc["table"] = t\n    sc.pop("cameras", None)\n    sc.pop("joints", None)\n    return sc\n\n\n'
           f'def goal(scene, rng):\n    return {task_text!r}\n')
    if not has_success:
        out += '\n\ndef success(state):\n    return {"ok": False, "note": "this world has no self-check"}\n'
    return out


def _has_success(code):
    try:
        return any(isinstance(n, ast.FunctionDef) and n.name == "success" for n in ast.parse(code).body)
    except SyntaxError:
        return True


def check_world_code(code):
    """-> the reasons an agent's world file is not taken ([]: none)."""
    sys.path.insert(0, str(SRC))
    from agentic_robotics.bimanual import practice
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"line {e.lineno}: syntax error: {e.msg}"]
    defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    bad = []
    if "scene" not in defs:
        bad.append("it does not define scene(scene, rng)")
    if "robot" in defs:
        bad.append("it defines robot(...): the robot, its commands and its sensing are fixed in these experiments")
    if "goal" in defs:
        bad.append("it defines goal(...): the task's text is fixed in these experiments")
    probe = code + world_footer("x", "success" in defs)
    bad += [b for b in practice.check_world(probe) if "_agent_scene" not in b]
    return bad[:12]


class Session:
    def __init__(self, d):
        self.dir = Path(d)
        self.st = json.loads((self.dir / "state.json").read_text())
        self.cond, self.cfg, self.task = self.st["cond"], self.st["cfg"], TASKS[self.st["task"]]
        self.ws = Path(self.st["root"]) / "workspace"
        self.rep = Path(self.st["rep_dir"])
        self.private = self.rep / "private"
        self.bi = None
        if self.task.get("rig") == "x5":                            # (the two-arm tasks: xsim/bi/)
            from xsim.bi import backend
            self.bi = backend

    # ------------------------------------------------------------------ the record
    @staticmethod
    def create(d, cond, task, rep_dir, root, cfg, snapshot=None):
        d = Path(d); d.mkdir(parents=True, exist_ok=False)
        for n in ("versions", "worlds", "practice", "rounds", "checkpoints", "validation", "claude", "fpractice"):
            (d / n).mkdir()
        st = dict(cond=cond, task=task, rep_dir=str(rep_dir), root=str(root), cfg=cfg, t0=time.time(), versions=[], worlds=[], rounds=[],
                  practice=dict(episodes=0, calls=0), validation_jobs=0, target=dict(rounds=0, episodes=0), fpractice=dict(episodes=0),
                  checkpoints=[], final=None, finished=False, snapshot=snapshot, evaluator_sha=target.evaluator_sha(TASKS[task]))
        (d / "state.json").write_text(json.dumps(st, indent=1))
        (d / "cost.json").write_text(json.dumps(dict(usd=0.0, turns=0, tokens={}, t0=time.time(), t=time.time())))
        return Session(d)

    def save(self):
        tmp = self.dir / "state.tmp"; tmp.write_text(json.dumps(self.st, indent=1)); os.replace(tmp, self.dir / "state.json")

    def usd(self):
        try:
            return float(json.loads((self.dir / "cost.json").read_text()).get("usd") or 0.0)
        except Exception:
            return 0.0

    def _usage(self):
        try:
            c = json.loads((self.dir / "cost.json").read_text())
            return {k: c.get(k) for k in ("usd", "turns", "tokens", "invocation", "estimated")}
        except Exception:
            return {}

    def keep_notes(self, dst):
        """The agent's own notes.md and summary.md, as they are now, copied into the record (nothing if it wrote none)."""
        got = []
        for n in ("notes.md", "summary.md"):
            f = self.ws / n
            try:
                if f.is_file() and f.stat().st_size and f.stat().st_size <= FILE_BYTES:
                    Path(dst).mkdir(parents=True, exist_ok=True); shutil.copy(f, Path(dst) / n); got.append(n)
            except OSError:
                pass
        return got

    def cap_usd(self):
        return float(self.cfg["budget_usd"]["build" if self.cond == "build" else self.cond])

    def practice_cap(self):
        """The session's simulator allowance: practice episodes and world-check scenes together (each started one counts)."""
        return int(self.cfg["build_validation_cap"] if self.cond == "build" else self.cfg["practice_episode_cap"])

    def sim_used(self):
        return int(self.st["practice"]["episodes"]) + int(self.st["validation_jobs"])

    def f_cap(self):
        return int(self.cfg["practice_episode_cap"]) + int(self.cfg["f_extra_episodes"])

    def log(self, kind, **kw):
        with open(self.dir / "revisions.jsonl", "a") as f:
            f.write(json.dumps(dict(kind=kind, t=round(time.time(), 1), session_s=round(time.time() - self.st["t0"], 1), usd=round(self.usd(), 4),
                                    after_round=self._last_feedback(), **kw)) + "\n")

    def _last_feedback(self):
        if self.cond == "F":
            return dict(target_practice_episodes=self.st["fpractice"]["episodes"])
        return self.st["target"]["rounds"] - 1 if self.st["target"]["rounds"] else None

    def note(self):
        s = self.st
        parts = [f"model usage ${self.usd():.2f} of ${self.cap_usd():.0f}"]
        if "validate_world" in PERMS.get(self.cond, ()):
            parts.append(f"simulator episodes {self.sim_used()} of {self.practice_cap()} (practice {s['practice']['episodes']}, world checks {s['validation_jobs']})")
        if "submit_target" in PERMS.get(self.cond, ()):
            parts.append(f"target submissions {s['target']['rounds']} of {self.cfg['target_submissions']}")
        if self.cond == "F":
            parts.append(f"target practice episodes {s['fpractice']['episodes']} of {self.f_cap()}")
        return "[" + "; ".join(parts) + "]"

    def checkpoints(self):
        """The latest complete program (and world) kept when the spending passes a threshold."""
        usd = self.usd()
        if not self.st["versions"]:
            return
        for thr in self.cfg["checkpoints_usd"]:
            if usd >= thr and thr not in [c["threshold_usd"] for c in self.st["checkpoints"]]:
                d = self.dir / "checkpoints" / f"usd_{thr:g}"; d.mkdir(exist_ok=True)
                self.keep_notes(d)
                v = self.st["versions"][-1]; shutil.copy(self.dir / "versions" / f"v{v['k']}.py", d / "program.py")
                w = self.st["worlds"][-1] if self.st["worlds"] else None
                if w:
                    shutil.copy(self.dir / "worlds" / f"w{w['k']}.py", d / "world.py")
                self.st["checkpoints"].append(dict(threshold_usd=thr, actual_usd=round(usd, 4), version=v["k"], program_sha=v["sha"],
                                                   world=w and w["k"], world_sha=w and w["sha"], t=round(time.time(), 1),
                                                   practice_episodes=self.st["practice"]["episodes"], target_episodes=self.st["target"]["episodes"]))
        self.save()

    # ------------------------------------------------------------------ the command
    def command(self, argv):
        import argparse

        class P(argparse.ArgumentParser):
            def error(self, message):
                raise Refused("usage: " + message)
        p = P(prog="xsim-tool", add_help=False); sub = p.add_subparsers(dest="sub")
        a = sub.add_parser("save_program", add_help=False); a.add_argument("file"); a.add_argument("--note", default="")
        a = sub.add_parser("save_world", add_help=False); a.add_argument("file"); a.add_argument("--note", default="")
        a.add_argument("--aim", choices=["mismatch", "coverage", "construction"], default=None)
        a = sub.add_parser("validate_world", add_help=False); a.add_argument("--world", type=int); a.add_argument("--seeds", type=int, nargs="+", default=[1])
        a = sub.add_parser("practice", add_help=False); a.add_argument("--seeds", type=int, nargs="+", required=True)
        a.add_argument("--version", type=int); a.add_argument("--world", type=int)
        a = sub.add_parser("submit_target", add_help=False); a.add_argument("--version", type=int); a.add_argument("--diagnosis", default="")
        a = sub.add_parser("target_practice", add_help=False); a.add_argument("--configs", type=int, nargs="+", required=True); a.add_argument("--version", type=int)
        a = sub.add_parser("note", add_help=False); a.add_argument("--text", required=True); a.add_argument("--round", type=int)
        a.add_argument("--kind", choices=["diagnosis", "rationale", "other"], default="diagnosis")
        sub.add_parser("status", add_help=False)
        a = sub.add_parser("finish", add_help=False); a.add_argument("--version", type=int)
        try:
            a = p.parse_args(argv)
            if not a.sub:
                raise Refused("give a sub-command: " + ", ".join(sorted(PERMS.get(self.cond, set()) | ALWAYS)))
            if a.sub not in PERMS.get(self.cond, set()) | ALWAYS:
                raise Refused(f"{a.sub} is not available in this session")
            if self.st["finished"] and a.sub != "status":
                raise Refused("this session has finished")
            out = getattr(self, "_t_" + a.sub)(a)
        except Refused as e:
            out = f"REFUSED: {e}"
            self.log("refused", sub=argv[0] if argv else "", why=str(e)[:300])
        self.checkpoints()
        return out + "\n" + self.note()

    def _file(self, name):
        f = (self.ws / name).resolve()
        if self.ws.resolve() not in f.parents or not f.is_file():
            raise Refused(f"{name}: a file in your workspace")
        if f.stat().st_size > FILE_BYTES:
            raise Refused(f"{name} is too large")
        return f.read_text(errors="replace")

    def _diff(self, old, new, a, b):
        return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), a, b))

    def _version(self, k=None):
        if not self.st["versions"]:
            raise Refused("no program has been saved yet")
        k = self.st["versions"][-1]["k"] if k is None else k
        if not any(v["k"] == k for v in self.st["versions"]):
            raise Refused(f"there is no version v{k}")
        return k, self.dir / "versions" / f"v{k}.py"

    def _world(self, k=None):
        if not self.st["worlds"]:
            raise Refused("there is no practice world")
        k = self.st["worlds"][-1]["k"] if k is None else k
        if self.cond == "C" and k != 0:
            raise Refused("this session's practice world is fixed: w0")
        w = next((w for w in self.st["worlds"] if w["k"] == k), None)
        if w is None:
            raise Refused(f"there is no world w{k}")
        f = self.dir / "worlds" / f"w{k}.py"
        if sha(f.read_text()) != w["sha"]:                          # (a kept world never changes)
            raise RuntimeError(f"world w{k} does not match its record")
        return k, f

    # ---- programs and worlds
    def add_program(self, code, note, source="agent"):
        if self.bi:
            from xsim.bi.practice_run import api
            bad = api.check_program(code)
        else:
            sys.path.insert(0, str(SRC))
            from agentic_robotics.bimanual import practice
            bad = practice.check_program(code)
        if bad:
            raise Refused("the program was not saved: " + "; ".join(bad))
        k = len(self.st["versions"])
        f = self.dir / "versions" / f"v{k}.py"; f.write_text(code); f.chmod(0o444)
        prev = (self.dir / "versions" / f"v{k - 1}.py").read_text() if k else ""
        (self.dir / "versions" / f"v{k}.diff").write_text(self._diff(prev, code, f"v{k - 1}", f"v{k}"))
        self.st["versions"].append(dict(k=k, sha=sha(code), note=note[:2000], t=round(time.time(), 1), usd=round(self.usd(), 4), source=source,
                                        after_round=self._last_feedback()))
        self.save()
        self.log("program", version=k, sha=sha(code), rationale=note[:2000], rationale_is="agent-proposed" if source == "agent" else source)
        return k

    def add_world(self, code, note, aim, source="agent"):
        bad = self.bi.check_world(code) if self.bi else check_world_code(code)
        if bad:
            raise Refused("the world was not saved: " + "; ".join(bad))
        k = len(self.st["worlds"])
        full = code if self.bi else code + world_footer(" ".join(self.task["text"].split()), _has_success(code))
        (self.dir / "worlds" / f"w{k}_agent.py").write_text(code)
        f = self.dir / "worlds" / f"w{k}.py"; f.write_text(full); f.chmod(0o444); (self.dir / "worlds" / f"w{k}_agent.py").chmod(0o444)
        prev = (self.dir / "worlds" / f"w{k - 1}_agent.py").read_text() if k else ""
        (self.dir / "worlds" / f"w{k}.diff").write_text(self._diff(prev, code, f"w{k - 1}", f"w{k}"))
        self.st["worlds"].append(dict(k=k, sha=sha(full), agent_sha=sha(code), note=note[:2000], aim=aim, t=round(time.time(), 1), usd=round(self.usd(), 4),
                                      source=source, after_round=self._last_feedback()))
        self.save()
        self.log("world", world=k, sha=sha(full), aim=aim, rationale=note[:2000], rationale_is="agent-proposed" if source == "agent" else source)
        return k

    def _t_save_program(self, a):
        k = self.add_program(self._file(a.file), a.note)
        return f"saved as v{k}"

    def _t_save_world(self, a):
        aim = a.aim or ("construction" if self.cond == "build" else None)
        if aim is None:
            raise Refused('say what this revision is for: --aim mismatch (to make the practice world agree better with what the target '
                          'showed) or --aim coverage (to practise a wider range of cases)')
        k = self.add_world(self._file(a.file), a.note, aim)
        return f"saved as w{k} (not yet checked: validate_world --world {k})"

    # ---- MuJoCo: world checks and practice
    def _mujoco(self, program, world, seeds, out_dirs, time_limit=None, validate=False):
        if self.bi:
            return self.bi.practice(program, world, seeds, out_dirs, self.task, self.cfg, time_limit=time_limit, validate=validate)
        sys.path.insert(0, str(SRC))
        from agentic_robotics.bimanual import practice, scenes
        bridge = practice.SegmenterBridge()

        def one(sd):
            s, d = sd
            try:
                return practice.run_sandboxed("sim_bimanual_grippers", "insert_varied", s, program, d, sensors="ideal", trace=True, bridge=bridge,
                                              time_limit_s=time_limit or self.task["episode_s"], wall_s=max(1800.0, 4 * self.task["episode_s"]),
                                              scene=scenes.empty(s), practice=False, world=world)
            except Exception as e:
                return dict(ok=False, ended=f"runner error: {type(e).__name__}: {str(e)[:200]}")
        try:
            with ThreadPoolExecutor(max_workers=int(self.cfg["practice_jobs"])) as ex:
                return list(ex.map(one, list(zip(seeds, out_dirs))))
        finally:
            bridge.close()

    def _give(self, src, dst, names=("output.txt",)):
        dst = self.ws / dst; dst.mkdir(parents=True, exist_ok=True)
        for n in names:
            if (Path(src) / n).exists():
                shutil.copy(Path(src) / n, dst / n)
        if (Path(src) / "shown").is_dir():
            shutil.copytree(Path(src) / "shown", dst / "shown", dirs_exist_ok=True)
        return dst.relative_to(self.ws)

    def _t_validate_world(self, a):
        k, wf = self._world(a.world)
        seeds = list(dict.fromkeys(a.seeds))
        if len(seeds) > 4:
            raise Refused("at most 4 scenes a call")
        left = self.practice_cap() - self.sim_used()
        if len(seeds) > left:
            raise Refused(f"only {left} simulator episodes are left (a checked scene counts as one)")
        chk = self.dir / "validation" / "interface_check.py"
        if not chk.exists():
            chk.write_text(self.bi.INTERFACE_CHECK if self.bi else INTERFACE_CHECK)
        n0 = self.st["validation_jobs"]
        dirs = [self.dir / "validation" / f"j{n0 + i:04d}_w{k}_s{s}" for i, s in enumerate(seeds)]
        self.st["validation_jobs"] += len(seeds); self.save()
        res = self._mujoco(chk, wf, seeds, dirs, time_limit=min(60.0, self.task["episode_s"]), validate=True)
        lines = []
        for s, d, r in zip(seeds, dirs, res):
            ok = str(r.get("ended")) == "returned"
            where = self._give(d, f"validation/w{k}_s{s}")
            names = ("output.txt",)
            if (Path(d) / "frames").is_dir():                       # (the overview's first and last frames)
                fr = sorted((Path(d) / "frames").glob("overview_rgb_*.jpg")); (self.ws / f"validation/w{k}_s{s}").mkdir(parents=True, exist_ok=True)
                for f in fr[:1] + fr[-1:]:
                    shutil.copy(f, self.ws / f"validation/w{k}_s{s}" / f.name)
            lines.append(f"scene {s}: {'loads, renders and answers the interface check' if ok else 'FAILED: ' + str(r.get('ended'))[:900]}"
                         + (f"; while it settled its bodies moved: {json.dumps(r.get('settle'))[:400]}" if r.get("settle") else "") +
                         f"; your self-check on the untouched scene: {json.dumps(r.get('world_check'))[:200]}; files: {where}/ (output.txt has the TRUE "
                         f"positions of your world's bodies at the start; shown/ the cameras' images)")
        self.log("validation", world=k, seeds=seeds, ok=[str(r.get("ended")) == "returned" for r in res])
        return f"world w{k}:\n" + "\n".join(lines)

    def _t_practice(self, a):
        seeds = list(dict.fromkeys(a.seeds))
        if len(seeds) > PER_CALL:
            raise Refused(f"at most {PER_CALL} practice episodes a call")
        left = self.practice_cap() - self.sim_used()
        if len(seeds) > left:
            raise Refused(f"only {left} simulator episodes are left")
        v, pf = self._version(a.version)
        k, wf = self._world(a.world)
        n0 = self.st["practice"]["episodes"]
        dirs = [self.dir / "practice" / f"p{n0 + i:04d}_v{v}_w{k}_s{s}" for i, s in enumerate(seeds)]
        self.st["practice"]["episodes"] += len(seeds); self.st["practice"]["calls"] += 1; self.save()    # (every started episode counts)
        t = time.time()
        res = self._mujoco(pf, wf, seeds, dirs)
        lines = []
        for s, d, r in zip(seeds, dirs, res):
            rec = dict(n=n0 + seeds.index(s), version=v, program_sha=sha(pf.read_text()), world=k, world_sha=sha(wf.read_text()), seed=s,
                       self_check=r.get("world_check"), self_reported_success=bool(r.get("ok")), ended=str(r.get("ended"))[:300], robot_s=r.get("robot_s"),
                       t=round(time.time(), 1), usd=round(self.usd(), 4), measured_by="the agent's own self-check in its own practice world (not the target's evaluator)")
            (d / "practice_record.json").write_text(json.dumps(rec, indent=1))
            where = self._give(d, f"practice/v{v}_w{k}_s{s}")
            for sub in ("frames", "videos"):
                if (Path(d) / sub).is_dir():
                    shutil.copytree(Path(d) / sub, self.ws / where / sub, dirs_exist_ok=True)
            lines.append(f"scene {s}: your self-check says {json.dumps(r.get('world_check'))[:300]}; ended: {str(r.get('ended'))[:200]}; robot time "
                         f"{r.get('robot_s')} s; log: {where}/output.txt")
        self.log("practice", version=v, world=k, seeds=seeds, self_reported=[bool(r.get("ok")) for r in res], wall_s=round(time.time() - t, 1))
        return f"v{v} in your practice world w{k} (MuJoCo):\n" + "\n".join(lines)

    # ---- the target
    def _dev_panel(self):
        """The replicate's development panel: the same frozen scenes, in the same order, for every condition and every
        round; each file is checked against the panel's manifest. -> (files, the panel's identity)."""
        from xsim import config
        return target.checked_panel(self.task, self.private / "dev", config.scenes(self.cfg, self.cond))

    def _evaluator(self):
        """The target's judge and physics must be the ones this session started with."""
        now = target.evaluator_sha(self.task)
        if self.st.get("evaluator_sha") and now != self.st["evaluator_sha"]:
            raise RuntimeError("the target's evaluator changed during the session")
        return now

    def _t_submit_target(self, a):
        k = self.st["target"]["rounds"]
        if k >= int(self.cfg["target_submissions"]):
            raise Refused("all target submissions have been used")
        v, pf = self._version(a.version)
        panel, panel_id = self._dev_panel()
        ev = self._evaluator()
        w = self.st["worlds"][-1] if self.st["worlds"] else None
        priv = self.private / "rounds" / self.cond / f"round_{k}"
        if priv.exists() and any(priv.iterdir()):                   # (before anything is counted: this root holds another run's records)
            raise RuntimeError(f"the private folder of submission {k} exists already ({priv.relative_to(self.rep)}): this experiment root holds the "
                               "records of another run; nothing is evaluated into them")
        rd = self.dir / "rounds" / f"round_{k}"; rd.mkdir()
        frozen = rd / "program.py"; shutil.copy(pf, frozen); frozen.chmod(0o444)
        if w:                                                       # (the practice world as it was when this was submitted)
            shutil.copy(self.dir / "worlds" / f"w{w['k']}.py", rd / "world.py")
        notes_kept = self.keep_notes(rd / "agent_notes")           # (the agent's notes as they stood when it submitted)
        t0, usd0 = time.time(), self.usd()
        self.st["target"]["rounds"] += 1; self.st["target"]["episodes"] += len(panel); self.save()     # (counted when started)
        pub, res = target.run_batch(frozen, panel, self.task, priv, rd / "payload", self.cfg)
        agg = dict(successes=sum(1 for p in pub if p["success"]), episodes=len(pub))
        summary = dict(round=k, program_version=v, episodes=[dict(episode=i, **p) for i, p in enumerate(pub)], aggregate=agg,
                       success_rate=round(agg["successes"] / max(agg["episodes"], 1), 3),
                       note="per episode: episode.json, output.txt (the program's prints and every command with its result and the arm's measured "
                            "tool position), videos/ and frames/ (the episode's videos and frames sampled from them), shown/ (the images the program showed)")
        (rd / "payload" / "summary.json").write_text(json.dumps(summary, indent=1))
        rec = dict(condition=self.cond, replicate=self.rep.name, task=self.st["task"], round=k, t_start=round(t0, 1), t_end=round(time.time(), 1),
                   session_s_at_end=round(time.time() - self.st["t0"], 1), usd_at_start=round(usd0, 4), usd_at_end=round(self.usd(), 4),
                   cumulative=dict(target_episodes=self.st["target"]["episodes"], target_rounds=self.st["target"]["rounds"],
                                   simulator_episodes=self.sim_used(), simulator_allowance=self.practice_cap() if "validate_world" in PERMS.get(self.cond, ()) else 0,
                                   practice_episodes=self.st["practice"]["episodes"], practice_calls=self.st["practice"]["calls"],
                                   validation_jobs=self.st["validation_jobs"], program_versions=len(self.st["versions"]), world_versions=len(self.st["worlds"]),
                                   model_usage=self._usage()),
                   program=dict(version=v, sha=sha(frozen.read_text())), world=w and dict(version=w["k"], sha=w["sha"]),
                   episode_ids=list(range(len(panel))), panel=dict(identity=panel_id, scenes=len(panel)), evaluator_sha=ev, agent_notes=notes_kept,
                   allowance=dict(submissions=int(self.cfg["target_submissions"]), episodes_each=len(panel)),
                   episodes=summary["episodes"], aggregate=agg, diagnosis_given_with_submission=a.diagnosis[:3000],
                   payload="payload/ (exactly what was returned to the agent)", private_record=str(priv.relative_to(self.rep)))
        (rd / "round.json").write_text(json.dumps(rec, indent=1))
        (priv / "private_round.json").write_text(json.dumps(dict(rec, configs=[str(f) for f in panel], results=res), indent=1, default=str))
        for f in rd.rglob("*"):
            if f.is_file():
                f.chmod(0o444)
        dst = self.ws / "feedback" / f"round_{k}"
        shutil.copytree(rd / "payload", dst)
        self.st["rounds"].append(dict(round=k, version=v, successes=agg["successes"], episodes=agg["episodes"], usd=round(self.usd(), 4), t=round(time.time(), 1),
                                      panel=panel_id, evaluator_sha=ev))
        self.save()
        self.log("target_round", round=k, version=v, successes=agg["successes"], episodes=agg["episodes"], diagnosis=a.diagnosis[:3000], diagnosis_is="agent-proposed")
        lines = [f"episode {i}: {'SUCCESS' if p['success'] else 'failure'}; ended: {str(p['ended'])[:120]}; robot time {p['robot_s']} s" for i, p in enumerate(pub)]
        return (f"target submission {k} (v{v}, {len(pub)} episodes): {agg['successes']} of {agg['episodes']} succeeded\n" + "\n".join(lines)
                + f"\nthe feedback: feedback/round_{k}/ (summary.json; ep_NN/ for each episode)")

    def _t_target_practice(self, a):
        pool, _ = target.checked_panel(self.task, self.private / "f_pool")
        self._evaluator()
        ids = list(dict.fromkeys(a.configs))
        if len(ids) > PER_CALL:
            raise Refused(f"at most {PER_CALL} practice episodes a call")
        if any(not 0 <= i < len(pool) for i in ids):
            raise Refused(f"the practice configurations are numbered 0 to {len(pool) - 1}")
        left = self.f_cap() - self.st["fpractice"]["episodes"]
        if len(ids) > left:
            raise Refused(f"only {left} practice episodes are left")
        v, pf = self._version(a.version)
        n0 = self.st["fpractice"]["episodes"]
        self.st["fpractice"]["episodes"] += len(ids); self.save()
        call = self.dir / "fpractice" / f"e{n0:04d}_v{v}"; call.mkdir()
        frozen = call / "program.py"; shutil.copy(pf, frozen); frozen.chmod(0o444)
        priv = self.private / "fpractice" / f"e{n0:04d}_v{v}"
        pub, res = target.run_batch(frozen, [pool[i] for i in ids], self.task, priv, call / "payload", self.cfg)
        rec = dict(condition="F", first_episode=n0, configs=ids, program=dict(version=v, sha=sha(frozen.read_text())), episodes=pub, t=round(time.time(), 1),
                   usd=round(self.usd(), 4), session_s=round(time.time() - self.st["t0"], 1))
        (call / "record.json").write_text(json.dumps(rec, indent=1))
        (priv / "private_record.json").write_text(json.dumps(dict(rec, config_files=[str(pool[i]) for i in ids], results=res), indent=1, default=str))
        dst = self.ws / "feedback" / f"practice_{n0:04d}"
        shutil.copytree(call / "payload", dst)
        for i, p in zip(ids, dst.glob("ep_*")):
            pass
        self.log("target_practice", first_episode=n0, configs=ids, version=v, successes=sum(1 for p in pub if p["success"]))
        lines = [f"configuration {i}: {'SUCCESS' if p['success'] else 'failure'}; ended: {str(p['ended'])[:120]}; robot time {p['robot_s']} s; "
                 f"feedback/practice_{n0:04d}/ep_{j:02d}/" for j, (i, p) in enumerate(zip(ids, pub))]
        return f"v{v} in the target environment:\n" + "\n".join(lines)

    # ---- notes, status, the end
    def _t_note(self, a):
        self.log("note", note_kind=a.kind, about_round=a.round, text=a.text[:6000], text_is="agent-proposed (not verified)")
        return "noted"

    def _t_status(self, a):
        s = self.st
        return (f"programs saved: {len(s['versions'])}; worlds: {len(s['worlds'])}; target submissions so far: "
                + (", ".join(f"round {r['round']}: {r['successes']}/{r['episodes']} (v{r['version']})" for r in s["rounds"]) or "none"))

    def _t_finish(self, a):
        d = self.dir / "final"; d.mkdir(exist_ok=True)
        out = []
        if self.cond != "build":
            v, pf = self._version(a.version)
            shutil.copy(pf, d / "program.py"); out.append(f"program v{v}")
            self.st["final"] = dict(version=v, program_sha=sha(pf.read_text()))
        else:
            self.st["final"] = {}
        if self.st["worlds"]:
            w = self.st["worlds"][-1]; shutil.copy(self.dir / "worlds" / f"w{w['k']}.py", d / "world.py")
            shutil.copy(self.dir / "worlds" / f"w{w['k']}_agent.py", d / "world_agent.py")
            self.st["final"].update(world=w["k"], world_sha=w["sha"]); out.append(f"world w{w['k']}")
        if self.cond == "build" and not self.st["worlds"]:
            raise Refused("no world has been saved yet")
        self.st["final"].update(t=round(time.time(), 1), usd=round(self.usd(), 4), session_s=round(time.time() - self.st["t0"], 1),
                                agent_notes=self.keep_notes(d))
        self.st["finished"] = True; self.save()
        self.log("finish", final=self.st["final"])
        return "the session's final result is " + " and ".join(out) + ". The session is over: end your turn."
