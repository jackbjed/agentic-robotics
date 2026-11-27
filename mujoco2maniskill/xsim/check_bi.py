"""A check, WITHOUT any model call, of the experiments on the two-arm task (sweep_blocks), at the experiments' real
settings for target feedback: stand-in "agents" (scripts that call the session's one command) play conditions A to F
from one shared snapshot. In particular it checks, by running them, that C and D get exactly the same target-feedback
allowance (the same number of submissions, each on the same frozen development scenes, judged by the same evaluator)
and that nothing D may do (revising its practice world) reaches those scenes or their judge.

    PYTHONNOUSERSITE=1 MUJOCO_GL=egl ../.venv/bin/python -I xsim/check_bi.py     -> out/xsim_check_bi/report.json
    ... xsim/check_bi.py --config FILE.json --out DIR [--src DIR]                the same under a run's own settings (--src: a root
                                                                                 that holds the prepared scene sets, if not out/xsim)

It copies the real replicate's prepared scenes (out/xsim/sweep_blocks/rep0: run `launch.py prepare` first) into its own
folder; the real experiment's records are not touched."""
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
from xsim.tasks import TASKS  # noqa: E402

TASK, REAL, OUT = "sweep_blocks", HERE / "out" / "xsim", HERE / "out" / "xsim_check_bi"
CFG = dict(target_jobs=10, practice_jobs=4)                         # (everything else: the experiments' defaults)
if "--small" in sys.argv:                                           # (a quick run of the same checks on a cut-down copy: SRC OUT after --small)
    REAL, OUT = Path(sys.argv[sys.argv.index("--small") + 1]), Path(sys.argv[sys.argv.index("--small") + 2])
    CFG.update(target_panel=2, target_submissions=2, f_pool=2, submission_scenes=dict(B=2, C=2, D=2, F=2))
if "--src" in sys.argv:                                             # (the prepared scene sets to copy, at the real settings: --src DIR --out DIR)
    REAL = Path(sys.argv[sys.argv.index("--src") + 1]).resolve()
if "--config" in sys.argv:                                          # (the same checks under a run's own settings: --config FILE.json --out DIR)
    CFG.update(json.loads(Path(sys.argv[sys.argv.index("--config") + 1]).read_text()))
if "--out" in sys.argv:
    OUT = Path(sys.argv[sys.argv.index("--out") + 1]).resolve()
_c = config.load(**CFG)
N, NB, M = config.scenes(_c, "C"), config.scenes(_c, "B"), _c["target_submissions"]    # (scenes a submission: C and D; B; submissions)
POOL = CFG.get("f_pool", 40)
QUICK = ("def run(robot, task):\n    f = robot.look()\n    print('cameras', sorted(f))\n    for a in ('left', 'right'):\n"
         "        print(a, robot.state(a)['tcp_mm'])\n    p = robot.state('right')['tcp_mm']\n"
         "    print(robot.move('right', (p[0], p[1], p[2] - 40), (0, 1, 0), (1, 0, 0)))\n    print(robot.home('right'))\n")
W0 = (HERE / "xsim" / "bi" / "fixtures" / "world.py").read_text()
W_OK = W0.replace('return {"ok": bool(ok), "inside": inside, "held_now": held}', 'return {"ok": True, "note": "a self-check that always says yes"}') \
         .replace('0.0097"><freejoint/><geom type="box" size="0.0096 0.0096 0.0096"', '0.0152"><freejoint/><geom type="box" size="0.015 0.015 0.015"')
checks = []


def check(name, ok, detail=""):
    checks.append(dict(check=name, ok=bool(ok), detail=str(detail)[:400]))
    print(("PASS  " if ok else "FAIL  ") + name + (("   [" + str(detail)[:260] + "]") if detail and not ok else ""), flush=True)


class Agent:
    def __init__(self, sdir, ws, tool):
        self.sdir, self.ws, self.tool = Path(sdir), Path(ws), tool

    def __call__(self, *argv):
        r = subprocess.run([str(self.tool), *[str(a) for a in argv]], cwd=str(self.ws), env=launch.tool_env(self.sdir), capture_output=True, text=True)   # (a real session's tool env: nothing inherited)
        return r.stdout.strip()

    def write(self, name, text):
        (self.ws / name).write_text(text)


def submit_all(a, tag):
    n = int(Session(a.sdir).cfg["target_submissions"])
    for k in range(n):
        r = a("submit_target", "--version", "0", "--diagnosis", f"round {k}")
        check(f"{tag}: submission {k} runs the whole panel", f"target submission {k}" in r and f"{N} episodes" in r, r[:300])
    check(f"{tag}: a submission beyond the allowance is refused", "REFUSED" in a("submit_target", "--version", "0"))
    check(f"{tag}: a submission cannot name its own scenes", "REFUSED" in a("submit_target", "--configs", "1", "2"))
    check(f"{tag}: no practice in a pool of target scenes", "REFUSED" in a("target_practice", "--configs", "0"))


def play_B(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    check("B: no practice simulator, no world", "REFUSED" in a("practice", "--seeds", "1") and "REFUSED" in a("save_world", "program.py", "--aim", "mismatch")
          and "REFUSED" in a("validate_world"))
    r = a("submit_target", "--diagnosis", "the starting program")
    check("B: a target submission runs its scenes", "target submission 0" in r and f"{NB} episodes" in r, r[:300])
    a("finish")


def play_C(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    a.write("w.py", W_OK)
    check("C: the practice world cannot be changed", "REFUSED" in a("save_world", "w.py", "--aim", "mismatch"))
    v = a("validate_world", "--seeds", "1")
    check("C: its fixed world loads, renders and answers the interface check (MuJoCo)", "loads, renders and answers the interface check" in v, v[:400])
    p = a("practice", "--seeds", "1", "2")
    check("C: practice in the fixed world w0", "practice world w0" in p and "self-check" in p, p[:300])
    check("C: there is no other world to practise in", "REFUSED" in a("practice", "--seeds", "3", "--world", "1"))
    a.write("notes.md", "round 0: v0 as given\n")
    submit_all(a, "C")
    a.write("summary.md", "tried: nothing but v0. observed: 0 successes. hypothesis: v0 does nothing.\n")
    a("finish")


def play_D(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    a.write("w_robot.py", W0 + "\n\ndef robot(robot, rng, kit):\n    return robot\n")
    check("D: a world cannot redefine the robot", "REFUSED" in a("save_world", "w_robot.py", "--aim", "mismatch"))
    a.write("w_goal.py", W0 + "\n\ndef goal(scene, rng):\n    return 'another task'\n")
    check("D: a world cannot redefine the task", "REFUSED" in a("save_world", "w_goal.py", "--aim", "mismatch"))
    a.write("w_file.py", "import os\n" + W0)
    check("D: a world is under the program rules (imports)", "REFUSED" in a("save_world", "w_file.py", "--aim", "mismatch"))
    a.write("w_cam.py", W0.replace("<mujoco><worldbody>", '<mujoco><option timestep="0.01"/><worldbody><camera name="w_c" pos="0 0 1"/>'))
    r = a("save_world", "w_cam.py", "--aim", "mismatch", "--note", "tries to change the simulation and add a camera")
    k = int(r.split("saved as w")[1].split()[0]) if "saved as w" in r else None
    v = a("validate_world", "--world", k, "--seeds", "1") if k is not None else r
    check("D: a world's XML cannot hold cameras or simulation settings", "REFUSED" in r or ("FAILED" in v and "not allowed" in v), (r + " | " + v)[:400])
    a.write("world.py", W_OK)
    r = a("save_world", "world.py", "--aim", "mismatch", "--note", "bigger cubes; a self-check that always says yes")
    check("D: a revised practice world is saved", "saved as w" in r, r)
    wk = r.split("saved as w")[1].split()[0]
    v = a("validate_world", "--world", wk, "--seeds", "1")
    check("D: the revised world loads, renders and answers the interface check", "loads, renders and answers the interface check" in v, v[:400])
    p = a("practice", "--seeds", "1", "2", "--world", wk)
    check("D: practice in the revised world; its own self-check says yes", f"practice world w{wk}" in p and '"ok": true' in p, p[:300])
    submit_all(a, "D")
    a("finish")


def play_E(sdir, ws, tool):
    a = Agent(sdir, ws, tool)
    check("E: no target evaluation of any kind", "REFUSED" in a("submit_target") and "REFUSED" in a("target_practice", "--configs", "0"))
    a.write("world.py", W_OK)
    check("E: may revise the practice world and practise", "saved as w1" in a("save_world", "world.py", "--aim", "coverage", "--note", "x")
          and "practice world w1" in a("practice", "--seeds", "4", "--world", "1"))
    a("finish")


F_PRACTICE = '''
def scene(scene, rng):
    scene["dustpan"]["xy_mm"] = [float(scene["dustpan"]["xy_mm"][0]) - 20.0 * float(rng.uniform(0, 1)), float(scene["dustpan"]["xy_mm"][1])]
    scene["table"] = {"rgb": [0.3, 0.6, 0.4]}
    return scene
def success(state):
    a = state.arm("right")
    return {"ok": a["from_start_mm"] > 20.0, "right_moved_mm": a["from_start_mm"], "dustpan": state.pos("dustpan")}
'''
F_INVALID = '''
def scene(scene, rng):
    scene["cubes"][0]["xy_mm"] = list(scene["dustpan"]["xy_mm"])
    return scene
'''
OFFICIAL_WORDS = ("cubes_in_dustpan", "dustpan_left_of_broom", "blocks_never_gripped", "arms_back", "grippers_open", "dustpan_not_lifted", "dustpan_level", "gripped_at_action")


def play_F(sdir, ws, tool):
    """Oracle-simulator practice with sparse external evaluation: practice in the target simulator with its own self-check,
    and the same judged submissions as B, C and D."""
    a = Agent(sdir, ws, tool); R = Path(sdir).parent
    check("F: no practice world to build, no practice in a pool", "REFUSED" in a("save_world", "practice.py", "--aim", "coverage") and "REFUSED" in a("validate_world")
          and "REFUSED" in a("target_practice", "--configs", "0"))
    st = Session(sdir).st
    check("F: starts from the shared program and the harness's practice file p0, with its document", (ws / "practice.py").exists() and (ws / "docs" / "practice.md").exists()
          and (ws / "docs" / "target.md").exists() and not (ws / "world.py").exists() and st["worlds"][0]["source"] == "shared initialization" and st["versions"][0]["source"] == "shared initialization")
    r = a("validate_scene", "--seeds", "1")
    check("F: a fresh scene is built in the target simulator and checked", "is valid, rests as asked" in r and (ws / "validation" / "p0_s1" / "scene.json").exists()
          and "TRUE positions" in (ws / "validation" / "p0_s1" / "output.txt").read_text(), r[:400])
    a.write("practice.py", F_PRACTICE)
    check("F: saves a version of its practice file (scene requests and self-check)", "saved as p1" in a("save_practice", "practice.py", "--note", "my own check; the dustpan moved; a green table"))
    a.write("program.py", QUICK); a("save_program", "program.py", "--note", "a short program")
    r = a("practice", "--seeds", "2", "3")
    recs = [json.loads(f.read_text()) for f in sorted((Path(sdir) / "practice").glob("p*/practice_record.json"))]
    check("F: practises in the target simulator, judged by its OWN self-check", "in the target simulator" in r and "YOUR success()" in r and len(recs) == 2
          and all(x["self_reported_success"] and x["self_check"]["right_moved_mm"] > 20 and x["simulator"] == "the target simulator" for x in recs), r[:500])
    check("F: the scene it asked for is what was built, and is on record", all(x["scene"]["table"] == {"rgb": [0.3, 0.6, 0.4]} for x in recs)
          and all((R / x["private_record"] / "layout.json").exists() for x in recs) and (ws / "practice" / "v1_p1_s2" / "scene.json").exists())
    seen = "\n".join(p.read_text(errors="replace") for d in (ws, Path(sdir) / "practice", Path(sdir) / "validation") for p in d.rglob("*") if p.is_file() and p.suffix in (".json", ".txt", ".md"))
    check("F: nothing of the official judge in what practice returns", not any(w in seen for w in OFFICIAL_WORDS) and "judge's test" not in seen, [w for w in OFFICIAL_WORDS if w in seen])
    off = [json.loads((R / x["private_record"] / "official.json").read_text()) for x in recs]
    check("F: what the judge would have said of a practice episode is kept privately only", all("at_the_end" in o and "success" in o["at_the_end"] for o in off)
          and ws.resolve() not in (R / recs[0]["private_record"]).resolve().parents)
    a.write("bad.py", F_INVALID); a("save_practice", "bad.py", "--note", "a cube in the dustpan")
    r = a("practice", "--seeds", "4", "--practice", "2")
    check("F: a scene that is not valid is not run, and counts", "not valid" in r and "overlap" in r, r[:400])
    s = Session(sdir)
    check("F: every practice episode and every checked scene counts toward one allowance", s.sim_used() == 4 and s.st["practice"]["episodes"] == 3 and s.st["validation_jobs"] == 1
          and s.practice_cap() == s.cfg["practice_episode_cap"], (s.sim_used(), s.st["practice"], s.st["validation_jobs"]))
    s.st["practice"]["episodes"] = s.practice_cap() - s.st["validation_jobs"] - 1; s.save()
    check("F: the practice allowance is enforced (with 1 left, 2 are refused)", "REFUSED" in a("practice", "--seeds", "8", "9") and "REFUSED" in a("validate_scene", "--seeds", "8", "9"))
    s = Session(sdir); s.st["practice"]["episodes"] = 3; s.save()
    submit_all(a, "F")
    a("finish")


def main():
    src = REAL / TASK
    if not (src / "rep0" / "prepared.json").exists():
        raise SystemExit("run `xsim/launch.py prepare --task sweep_blocks --rep 0` first")
    if OUT.exists():
        subprocess.run(["chmod", "-R", "u+w", str(OUT)]); shutil.rmtree(OUT)
    (OUT / TASK / "rep0").mkdir(parents=True)
    shutil.copytree(src / "test_private", OUT / TASK / "test_private")
    from xsim.fork_root import copy_scene_sets
    copy_scene_sets(src / "rep0", OUT / TASK / "rep0")              # (the scene sets only: never the real sessions' private records)
    shutil.copytree(src / "rep0" / "shared", OUT / TASK / "rep0" / "shared")
    shutil.copy(src / "rep0" / "prepared.json", OUT / TASK / "rep0" / "prepared.json")
    cfg = config.load(**CFG); task = TASKS[TASK]; R = OUT / TASK / "rep0"
    t0 = time.time()
    dev, pid = target.checked_panel(task, R / "private" / "dev", N)
    allp, pid_all = target.checked_panel(task, R / "private" / "dev")
    check(f"the development panel: {len(allp)} frozen scenes with a manifest; a submission of C and of D runs its first {N}", len(dev) == N and config.scenes(cfg, "D") == N
          and [f.name for f in dev] == [f.name for f in allp[:N]])
    ver = json.loads((R / "private" / "dev" / "reverification.json").read_text())
    check("every development scene is solved by the sweeping reference", len(ver) >= N and all(v["solved"] and v["how"] == "swept with the brush" for v in ver))
    try:
        open(dev[0], "a").close(); wrote = True
    except PermissionError:
        wrote = False
    check("the development scenes are read-only files", not wrote)
    fx = OUT / "fixtures"; fx.mkdir(); (fx / "program.py").write_text(QUICK); (fx / "world.py").write_text(W0)
    snap = launch.init(TASK, 0, cfg, OUT, fake=dict(program=fx / "program.py", world=fx / "world.py", notes="stand-in construction notes\n"))
    check("shared initialization: one snapshot with hashes", set(snap["files"]) == {"program.py", "world_agent.py", "construction_notes.md"})
    mans = {}
    for cond, play in (("A", None), ("B", play_B), ("C", play_C), ("D", play_D), ("E", play_E), ("F", play_F)):
        print(f"--- condition {cond}", flush=True)
        mans[cond] = launch.run(TASK, 0, cond, cfg, OUT, agent=play)
    C, D = Session(R / "C"), Session(R / "D")
    rc = [json.loads((R / "C" / "rounds" / f"round_{k}" / "round.json").read_text()) for k in range(M)]
    rd = [json.loads((R / "D" / "rounds" / f"round_{k}" / "round.json").read_text()) for k in range(M)]
    check(f"C and D: {M} submissions each", C.st["target"]["rounds"] == M and D.st["target"]["rounds"] == M)
    check(f"C and D: {N} target episodes in every submission ({N * M} each in all)", all(r["aggregate"]["episodes"] == N for r in rc + rd)
          and C.st["target"]["episodes"] == D.st["target"]["episodes"] == N * M)
    check("C and D: the same recorded allowance", all(r["allowance"] == dict(submissions=M, episodes_each=N) for r in rc + rd))
    check("C and D: every round was on the SAME frozen panel", {r["panel"]["identity"] for r in rc + rd} == {pid}, {r["panel"]["identity"] for r in rc + rd})
    ev = target.evaluator_sha(task)
    check("C and D: every round was judged by the SAME evaluator", {r["evaluator_sha"] for r in rc + rd} == {ev})
    same = all([(e["success"], e["ended"], e["robot_s"]) for e in a_["episodes"]] == [(e["success"], e["ended"], e["robot_s"]) for e in b_["episodes"]]
               for a_, b_ in zip(rc, rd))
    check("the same program gets the same result, episode by episode, in C and in D (D's revised world changes nothing in the target)", same)
    check("D's rounds record its revised world, C's the shared one (the record tells them apart; the target does not)",
          all(r["world"]["version"] == 0 for r in rc) and all(r["world"]["version"] > 0 for r in rd))
    pc = [json.loads(Path(R / "private" / "rounds" / c / "round_0" / "private_round.json").read_text())["configs"] for c in ("C", "D")]
    check("C's and D's private records name the same scene files in the same order", pc[0] == pc[1] and len(pc[0]) == N)
    _, pid2 = target.checked_panel(task, R / "private" / "dev", N)
    check("after everything, the panel is unchanged", pid2 == pid)
    pb = json.loads(Path(R / "private" / "rounds" / "B" / "round_0" / "private_round.json").read_text())["configs"]
    check(f"B's submission runs {NB} scenes of the same frozen panel, beginning with C's and D's {N}", len(pb) == NB and pb[:N] == pc[0])
    bad = []
    for cond in "BCDEF":
        ws = Path(mans[cond]["workspace"])
        for f in ws.rglob("*"):
            if f.is_file() and f.suffix in (".txt", ".json", ".md", ".py"):
                t = f.read_text(errors="replace")
                if "feedback" in str(f) and any(k in t for k in ("TRUE positions", "| NOW", "the judge", "cubes_in_dustpan", "broom_shovel", "private/", "Rigid")):
                    bad.append(str(f.relative_to(ws)))
            if f.name.startswith("ep_") and f.suffix == ".json":
                bad.append("a scene file: " + str(f.relative_to(ws)))
    check("no workspace holds a target scene, true positions or the judge's measurements", not bad, bad[:4])
    c, d = Session(R / "C"), Session(R / "D")
    check("C and D: the same simulator allowance; practice episodes and world-check scenes both count",
          c.practice_cap() == d.practice_cap() == cfg["practice_episode_cap"] and c.sim_used() == c.st["practice"]["episodes"] + c.st["validation_jobs"]
          and c.sim_used() == 3 and d.sim_used() == d.st["practice"]["episodes"] + d.st["validation_jobs"] and d.st["validation_jobs"] >= 2,
          (c.sim_used(), d.sim_used(), d.st["validation_jobs"]))
    import argparse as _ap
    e = Session(R / "E"); e.st["finished"] = False; e.st["practice"]["episodes"] = e.practice_cap() - e.st["validation_jobs"] - 1
    refused = []
    for call, a_ in ((e._t_practice, _ap.Namespace(seeds=[91, 92], version=None, world=None)), (e._t_validate_world, _ap.Namespace(seeds=[91, 92], world=None))):
        try:
            call(a_); refused.append(False)
        except Exception as x:
            refused.append(type(x).__name__ == "Refused")
    check("the simulator allowance is enforced for practice and for world checks alike (with 1 left, 2 are refused)", refused == [True, True], refused)
    texts = {c_: launch.prompts.system_text(c_, task, cfg, cmd="", pool=40) for c_ in ("A", "build", "B", "C", "D", "E", "F")}
    check("the notes-and-summary request is in every developing session: word for word the same in B, C, D and F; in E with 'practice' for 'feedback'; not in A or the builder",
          all(launch.prompts.NOTES in texts[c_] for c_ in "BCDF") and launch.prompts.NOTES not in texts["E"]
          and launch.prompts.NOTES.replace("feedback\nrounds", "practice\nresults").replace("what the feedback showed", "what practice showed") in texts["E"]
          and not any(launch.prompts.NOTES[:40] in texts[c_] for c_ in ("A", "build")))
    check("E's text: no target submission, no target feedback document; it may revise what D may; F's two clarifications are F's only",
          "submit_target" not in texts["E"] and "FEEDBACK from an episode in the target" not in texts["E"]
          and "its things' shapes and physical properties, its scenes, how they vary, and its self-check" in texts["E"] and "its things' shapes and physical properties, its scenes, how they vary, and its self-check" in texts["D"]
          and not any(w in texts["E"] for w in ("what the target showed", "of the same kinds as a target episode gives", "feedback\nrounds", "what the feedback showed",
                                                "across B/C/D and across submissions", "target development configurations are identical"))
          and all(w in texts["E"] for w in ("This session has no target development evaluations; final\nevaluation feedback is never returned.", "look you were given; coverage",
                                            "what practice showed", "the episode's videos (an overview in colour; the head camera in colour and depth) with frames"))
          and all(w in texts["D"] for w in ("of the same kinds as a target episode gives", "the target showed; coverage", "what the feedback showed", "across B/C/D and across submissions"))
          and "You are NOT given, and nothing here tells" not in texts["F"] and texts["F"].count(launch.prompts.F_NOT_GIVEN[1]) == 1
          and all(launch.prompts.F_NOT_GIVEN[0] in texts[c_] and launch.prompts.F_NOT_GIVEN[1] not in texts[c_] for c_ in "BCDE")
          and texts["F"].count(launch.prompts.F_PRIVILEGED) == 1 and not any(launch.prompts.F_PRIVILEGED in texts[c_] or launch.prompts.F_TARGET in texts[c_] for c_ in "BCDE"))
    check("the agent's notes are kept: at every submission and at the end (C wrote both files)",
          (R / "C" / "rounds" / "round_0" / "agent_notes" / "notes.md").is_file() and (R / "C" / "final" / "summary.md").is_file()
          and mans["C"]["agent_notes"]["summary_written"] and not mans["D"]["agent_notes"]["summary_written"], mans["C"].get("agent_notes"))
    s = Session(R / "E"); s.st["evaluator_sha"] = "0" * 16
    try:
        s._evaluator(); stopped = False
    except RuntimeError:
        stopped = True
    check("a session whose evaluator had changed would be stopped", stopped)
    check("manifests: practice and target counts per condition",
          mans["B"]["target_interactions"] == NB and mans["C"]["target_interactions"] == N * M and mans["D"]["target_interactions"] == N * M
          and mans["E"]["target_interactions"] == 0 and mans["C"]["practice_episodes"] == 2 and mans["D"]["practice_episodes"] == 2)
    NF = config.scenes(cfg, "F")
    check("F's manifest: the judged episodes, and every episode in the target simulator (submissions + practice + scene checks); named an oracle reference",
          mans["F"]["target_interactions"] == NF * M and mans["F"]["practice_episodes"] == 3 and mans["F"]["validation_jobs"] == 1
          and mans["F"]["target_simulator_interactions"] == NF * M + 4 and "oracle reference" in mans["F"].get("oracle_reference", "")
          and mans["C"]["target_simulator_interactions"] == N * M and mans["F"]["budgets"]["practice_simulator"].startswith("the target simulator"),
          {k: mans["F"].get(k) for k in ("target_interactions", "practice_episodes", "validation_jobs", "target_simulator_interactions")})
    fr = [json.loads((R / "F" / "rounds" / f"round_{k}" / "round.json").read_text()) for k in range(M)]
    check("F's submissions: the same development scenes, the same evaluator and the same allowance as C's and D's", [r["panel"] for r in fr] == [r["panel"] for r in rc][:M]
          and {r["evaluator_sha"] for r in fr} == {ev} and all(r["allowance"] == dict(submissions=M, episodes_each=NF) for r in fr) and NF == N)
    fv = sorted(p.name for p in (R / "F" / "worlds").glob("w*_agent.py"))
    check("F's records: every program version, every practice-file version, every practice episode's scene and payload", len(fv) == 3
          and len(list((R / "F" / "versions").glob("v*.py"))) >= 2 and len(list((R / "F" / "practice").glob("p*/practice_record.json"))) == 3
          and len(list((R / "F" / "practice").glob("p*/scene.json"))) >= 2 and len(list((R / "private" / "oracle_practice" / "F").glob("*/layout.json"))) >= 3, fv)
    rep = dict(passed=sum(c["ok"] for c in checks), of=len(checks), wall_s=round(time.time() - t0, 1), panel=pid, evaluator=ev, checks=checks)
    (OUT / "report.json").write_text(json.dumps(rep, indent=1))
    print(f"{rep['passed']} of {rep['of']} checks passed in {rep['wall_s']} s")


if __name__ == "__main__":
    main()
