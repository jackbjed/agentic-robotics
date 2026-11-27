"""A cheap test of the experiments' machinery, WITHOUT any model: every condition is played by a scripted stand-in
"agent" that calls the session command as an agent would. It checks the permissions, the shared initialization, the
accounting, the persistent feedback and the submission handling (the list at the end of the specification).

    ../.venv/bin/python -I xsim/smoke_test.py        -> out/xsim_smoke/smoke_report.json (and PASS / FAIL lines)

Small numbers (a panel of 2, 2 submissions, 6 practice episodes, ...) so that it takes minutes. What it cannot check
without a model: that the Claude Code CLI itself keeps an agent inside its workspace and to the one command (that was
audited on the earlier sessions that use the same flags)."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from xsim import config, launch, target  # noqa: E402
from xsim.session import Session  # noqa: E402

OUT = HERE / "out" / "xsim_smoke"
TASK = "insert"
CFG = dict(shown_solvable=[], target_panel=2, target_submissions=2, practice_episode_cap=6, f_extra_episodes=2, f_pool=3, practice_jobs=4, target_jobs=4,
           splits=dict(test_id=2, ood_appearance=1, ood_dimensions=1, ood_heights=1, ood_physics=1), checkpoints_usd=[2, 5, 10])
REFERENCE = HERE.parent / "runs" / "bimanual" / "insert_own_cc_xhigh_1125" / "program.py"      # (a program known to solve the task: the initial skill here)
QUICK = "def run(robot, task):\n    f = robot.look()\n    print('cameras', sorted(f))\n    print(robot.state('left')['tcp_mm'])\n    robot.home('left')\n"
CRASH = "def run(robot, task):\n    robot.look()\n    raise ValueError('a deliberate failure')\n"
WORLD = '''import math


def scene(scene, rng):
    x, y = rng.uniform(-400, -250), rng.uniform(-100, 150)
    sx, sy = x + 120, y + rng.uniform(-40, 40)
    scene["mjcf"] = ('<mujoco><worldbody>'
                     '<body name="w_block" pos="%.4f %.4f 0.0153"><freejoint/><geom type="box" size="0.02 0.02 0.015" rgba="0.9 0.5 0.1 1" density="800"/></body>'
                     '<body name="w_post" pos="%.4f %.4f 0.0003"><freejoint/><geom type="cylinder" size="0.03 0.006" pos="0 0 0.006" rgba="0.6 0.5 0.4 1" density="700"/>'
                     '<geom type="cylinder" size="0.005 0.04" pos="0 0 0.052" rgba="0.5 0.3 0.2 1" density="700"/></body>'
                     '</worldbody></mujoco>' % (x / 1000, y / 1000, sx / 1000, sy / 1000))
    return scene


def success(state):
    a, b = state.pos("w_block"), state.pos("w_post")
    return {"ok": bool(math.hypot(a[0] - b[0], a[1] - b[1]) < 5.0), "gap_mm": round(math.hypot(a[0] - b[0], a[1] - b[1]), 1)}
'''
checks = []


def check(name, ok, detail=""):
    checks.append(dict(check=name, ok=bool(ok), detail=str(detail)[:300]))
    print(("PASS  " if ok else "FAIL  ") + name + (("   [" + str(detail)[:160] + "]") if detail and not ok else ""), flush=True)


class Agent:
    """A scripted stand-in for an agent: it can write files in its workspace and call the one command."""

    def __init__(self, sdir, ws, tool):
        self.sdir, self.ws, self.tool = Path(sdir), Path(ws), tool

    def __call__(self, *argv, cwd=None, session=None):
        env = dict(os.environ, XSIM_SESSION=str(session or self.sdir))
        r = subprocess.run([str(self.tool), *[str(a) for a in argv]], cwd=str(cwd or self.ws), env=env, capture_output=True, text=True)
        return r.stdout.strip()

    def write(self, name, text):
        (self.ws / name).write_text(text)


def play_B(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    check("B cannot run a practice simulator", "REFUSED" in a("practice", "--seeds", "1"))
    check("B cannot save a practice world", "REFUSED" in a("save_world", "program.py", "--aim", "mismatch"))
    check("B cannot practise directly in the target", "REFUSED" in a("target_practice", "--configs", "0"))
    r0 = a("submit_target", "--diagnosis", "first try of the starting program")
    check("B: a target submission runs the whole panel", "target submission 0" in r0 and "of 2 succeeded" in r0, r0)
    a.write("crash.py", CRASH)
    check("B: a program is saved as a new version", "saved as v1" in a("save_program", "crash.py", "--note", "a failing program, on purpose"))
    a.write("program.py", "garbage (")                              # (a later edit of the file does not change what was saved)
    r1 = a("submit_target", "--version", "1")
    s = Session(sdir)
    check("failed episodes consume the target allowance", s.st["target"]["episodes"] == 4 and "0 of 2 succeeded" in r1, r1)
    check("B: no submission beyond the allowance", "REFUSED" in a("submit_target"))
    check("a submission that was refused is not counted", Session(sdir).st["target"]["rounds"] == 2)
    a("note", "--round", "1", "--text", "the program raised on purpose", "--kind", "diagnosis")
    check("B: finish", "final result" in a("finish", "--version", "0"))
    check("a finished session takes no more commands", "REFUSED" in a("save_program", "crash.py"))


def play_C(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    a.write("world.py", WORLD + "\n# an attempted change\n")
    check("C cannot save a changed practice world", "REFUSED" in a("save_world", "world.py", "--aim", "mismatch"))
    a.write("quick.py", QUICK)
    a("save_program", "quick.py", "--note", "a quick program for practice")
    r = a("practice", "--seeds", "1", "2")
    check("C: practice runs in MuJoCo", "practice world w0" in r and "self-check" in r, r)
    rec = json.loads(next((Path(sdir) / "practice").glob("p0000_*/practice_record.json")).read_text())
    s = Session(sdir)
    check("C: practice used the snapshot's world although the workspace file was edited", rec["world_sha"] == s.st["worlds"][0]["sha"] == s.st["start"]["world"])
    check("C: there is no other world to practise in", "REFUSED" in a("practice", "--seeds", "3", "--world", "1"))
    check("practice episodes are capped", "REFUSED" in a("practice", "--seeds", "11", "12", "13", "14", "15") and Session(sdir).st["practice"]["episodes"] == 2)
    check("C: a target submission is allowed", "target submission 0" in a("submit_target", "--version", "0"))
    a("finish", "--version", "0")


def play_D(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    a.write("w_robot.py", WORLD + "\n\ndef robot(robot, rng, kit):\n    return robot\n")
    check("D cannot change the robot through a world", "REFUSED" in a("save_world", "w_robot.py", "--aim", "mismatch"))
    a.write("w_goal.py", WORLD + "\n\ndef goal(scene, rng):\n    return 'another task'\n")
    check("D cannot change the task's text through a world", "REFUSED" in a("save_world", "w_goal.py", "--aim", "mismatch"))
    a.write("w_file.py", "import os\n" + WORLD)
    check("a world is under the program rules (imports)", "REFUSED" in a("save_world", "w_file.py", "--aim", "mismatch"))
    a.write("world.py", WORLD.replace("0.02 0.02 0.015", "0.025 0.025 0.015").replace('return scene', 'scene["cameras"] = {"depth": {"noise_px": 9}}\n    scene["table"] = {"height_mm": 40.0}\n    return scene'))
    check("D: a world revision must say what it is for", "REFUSED" in a("save_world", "world.py"))
    r = a("save_world", "world.py", "--aim", "mismatch", "--note", "the block looked wider in the feedback")
    check("D: a revised practice world is saved", "saved as w1" in r, r)
    full = (Path(sdir) / "worlds" / "w1.py").read_text()
    check("the harness keeps the tabletop, the cameras and the task text fixed in every world", 't["height_mm"] = 0.0' in full and 'sc.pop("cameras"' in full and "def goal" in full)
    v = a("validate_world", "--world", "1", "--seeds", "1")
    check("D: a world can be checked (loads, renders, interface)", "loads, renders and answers the interface check" in v, v)
    a.write("quick.py", QUICK); a("save_program", "quick.py", "--note", "quick")
    p = a("practice", "--seeds", "1", "--world", "1")
    check("D: practice in a revised world", "practice world w1" in p, p)
    a("submit_target", "--version", "0", "--diagnosis", "baseline")
    a.write("world2.py", WORLD); a("save_world", "world2.py", "--aim", "coverage", "--note", "after round 0")
    revs = [json.loads(l) for l in (Path(sdir) / "revisions.jsonl").read_text().splitlines()]
    w2 = [r for r in revs if r["kind"] == "world" and r.get("world") == 2]
    check("revisions are linked to the feedback round before them, with their aim", bool(w2) and w2[0]["after_round"] == 0 and w2[0]["aim"] == "coverage")
    (Path(sdir) / "cost.json").write_text(json.dumps(dict(usd=6.0, turns=1, t0=time.time(), t=time.time())))     # (as if $6 had been spent)
    a("status")
    cp = Session(sdir).st["checkpoints"]
    check("checkpoints are kept when spending passes a threshold", [c["threshold_usd"] for c in cp] == [2, 5] and (Path(sdir) / "checkpoints" / "usd_5" / "program.py").exists(), cp)
    a("finish", "--version", "0")


def play_E(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    check("E cannot request a target evaluation", "REFUSED" in a("submit_target") and "REFUSED" in a("target_practice", "--configs", "0"))
    a.write("world.py", WORLD); check("E may revise the practice world", "saved as w1" in a("save_world", "world.py", "--aim", "coverage", "--note", "x"))
    a.write("quick.py", QUICK); a("save_program", "quick.py", "--note", "quick")
    check("E: practice", "practice world w1" in a("practice", "--seeds", "4"))
    a("finish", "--version", "0")


def play_F(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    check("F has no practice simulator", "REFUSED" in a("practice", "--seeds", "1") and "REFUSED" in a("save_world", "program.py", "--aim", "coverage"))
    check("F has no batch submissions", "REFUSED" in a("submit_target"))
    r = a("target_practice", "--configs", "0", "1")
    check("F: practice episodes in the target, with feedback", "in the target environment" in r and (ws / "feedback" / "practice_0000" / "ep_00" / "episode.json").exists(), r)
    check("F: the practice scenes are numbered within its pool", "REFUSED" in a("target_practice", "--configs", "7"))
    a.write("quick.py", QUICK); a("save_program", "quick.py", "--note", "quick")
    for _ in range(3):
        a("target_practice", "--configs", "0", "1", "--version", "1")
    s = Session(sdir)
    check("F: its allowance is the practice cap plus the extra", s.f_cap() == 8 and s.st["fpractice"]["episodes"] == 8 and "REFUSED" in a("target_practice", "--configs", "2"))
    a("finish", "--version", "0")


def leaks(ws):
    bad = []
    for f in Path(ws).rglob("*"):
        if f.is_file() and f.suffix in (".txt", ".json", ".md", ".py"):
            t = f.read_text(errors="replace")
            if "TRUE positions" in t and "feedback" in str(f):
                bad.append(f"truth in {f.name}")
            if any(k in t for k in ("insert_target", "private/", "config_seed", "axis_off_mm", "on_stick")) and "feedback" in str(f):
                bad.append(f"evaluator data in {f.relative_to(ws)}")
        if f.name.endswith(".json") and f.stem.isdigit():
            bad.append(f"a scene file {f.name}")
    return bad


def main():
    if OUT.exists():
        subprocess.run(["chmod", "-R", "u+w", str(OUT)]); shutil.rmtree(OUT)
    cfg = config.load(**CFG)
    t0 = time.time()
    R = launch.prepare(TASK, 0, cfg, OUT)
    check("target configurations: development, practice pool, test splits, the look", (R / "private" / "dev").exists() and (R / "shared" / "look" / "look_scene.png").exists()
          and (OUT / TASK / "test_private" / "splits.json").exists())
    val = json.loads((R / "private" / "dev" / "validation.json").read_text())
    check("every admitted configuration passed the frozen rules", all(b["admitted"] or True for b in val["book"]) and val["label"].startswith("feasibility"))
    fx = OUT / "fixtures"; fx.mkdir(exist_ok=True); (fx / "world.py").write_text(WORLD)
    snap = launch.init(TASK, 0, cfg, OUT, fake=dict(program=REFERENCE, world=fx / "world.py", notes="stand-in construction notes\n"))
    check("shared initialization gives one snapshot with hashes", set(snap["files"]) == {"program.py", "world_agent.py", "construction_notes.md"})
    try:
        (R / "init" / "snapshot" / "program.py").write_text("x"); wrote = True
    except PermissionError:
        wrote = False
    check("the snapshot is read-only", not wrote)
    mans, roots = {}, {}
    for cond, play in (("A", None), ("B", play_B), ("C", play_C), ("D", play_D), ("E", play_E), ("F", play_F)):
        print(f"--- condition {cond}", flush=True)
        mans[cond] = launch.run(TASK, 0, cond, cfg, OUT, agent=play)
        if cond != "A":
            roots[cond] = Path(mans[cond]["workspace"])
    S = {c: Session(R / c) for c in "BCDEF"}
    check("C, D and E start from the identical snapshot (hashes)", S["C"].st["start"] == S["D"].st["start"] == S["E"].st["start"] and S["C"].st["start"]["world"] is not None)
    check("every condition starts from the same initial program", len({S[c].st["versions"][0]["sha"] for c in "BCDEF"}) == 1
          and mans["A"]["final"]["program_sha"] == target.sha(R / "init" / "snapshot" / "program.py"))
    check("A has no session, no command and no target interaction", mans["A"]["target_interactions"] == 0 and not (R / "A" / "state.json").exists())
    check("conditions have separate workspaces, homes and configuration directories", len({str(r) for r in roots.values()}) == 5
          and all((r.parent / "workspace") == r and r.parent != roots["B"].parent or c == "B" for c, r in roots.items()))
    a = Agent(R / "B", roots["C"], launch.TOOL)
    check("a session's command does not work from another session's workspace", "only from the session's workspace" in a("status"))
    check("without a session the command does nothing", "only inside a session" in Agent(OUT / "nowhere", roots["C"], launch.TOOL)("status"))
    bad = sum((leaks(r) for r in roots.values()), [])
    check("no workspace holds true target state, evaluator data or scene files", not bad, bad)
    rd = R / "B" / "rounds" / "round_1"
    rec = json.loads((rd / "round.json").read_text())
    check("every round is recorded with its program's hash, times, budget and the exact payload", rec["program"]["sha"] == target.sha(rd / "program.py")
          and (rd / "payload" / "summary.json").exists() and rec["cumulative"]["target_episodes"] == 4 and "usd_at_end" in rec and "session_s_at_end" in rec)
    same = all((rd / "payload" / f.relative_to(roots["B"] / "feedback" / "round_1")).read_bytes() == f.read_bytes()
               for f in (roots["B"] / "feedback" / "round_1").rglob("*") if f.is_file())
    check("the payload on record is exactly what the agent got", same)
    try:
        (rd / "round.json").write_text("x"); over = True
    except PermissionError:
        over = False
    check("a round's record cannot be overwritten", not over)
    priv = R / "private" / "rounds" / "B" / "round_1" / "private_round.json"
    check("the evaluator's private record is kept apart from the session", priv.exists() and "configs" in json.loads(priv.read_text()) and not list((R / "B").rglob("private_round.json")))
    check("the submitted program is the saved version, not the workspace file", (rd / "program.py").read_text() == CRASH)
    check("self-reported practice results are recorded apart from target results", "self_reported_success" in json.loads(next((R / "C" / "practice").glob("p0000_*/practice_record.json")).read_text()))
    cmdline = " ".join(["--tools", "Read,Edit,Write,Glob,Grep,Bash", f"Bash({launch.TOOL} *)"])
    check("the agent's shell is limited to the one command (the launcher's flags)", str(launch.TOOL) in cmdline and launch.TOOL.exists() and os.access(launch.TOOL, os.X_OK))
    print("--- final evaluation", flush=True)
    fin = launch.final(TASK, 0, cfg, None, OUT)
    check("final evaluation: every condition's frozen program on the ID and OOD splits", set(fin) == set("ABCDEF") and set(fin["A"]["splits"]) == set(cfg["splits"]), {c: list(v["splits"]) for c, v in fin.items()})
    check("final results are not in any workspace", not any("final_eval" in str(f) for r in roots.values() for f in r.rglob("*")))
    rows, curves = launch.report(TASK, OUT)
    check("report: a manifest row per session and development curves", len(rows) == 6 and any(c["condition"] == "B" and c["cumulative_target_episodes"] == 4 for c in curves))
    m = mans["D"]
    check("manifest: condition, model, snapshot, budgets, usage, final artifacts", all(k in m for k in ("condition", "model", "snapshot", "budgets", "cost", "usage", "final", "checkpoints")))
    ok = sum(c["ok"] for c in checks)
    (OUT / "smoke_report.json").write_text(json.dumps(dict(passed=ok, of=len(checks), wall_s=round(time.time() - t0, 1), checks=checks,
                                                           final={c: {k: f"{v['successes']}/{v['episodes']}" for k, v in x["splits"].items()} for c, x in fin.items()}), indent=1))
    print(f"\n{ok} of {len(checks)} checks passed in {time.time() - t0:.0f} s; final: " + json.dumps({c: x["splits"]["test_id"]["successes"] for c, x in fin.items()}))
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
