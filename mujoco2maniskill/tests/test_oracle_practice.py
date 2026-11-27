"""Practice in the target simulator (xsim/bi/oracle_run.py), with the REAL simulator (slow: about 5 minutes; no model,
no segmentation service).

    PYTHONNOUSERSITE=1 M2M_MANISKILL_PYTHON=... ../.venv/bin/python -I tests/test_oracle_practice.py

Checks: the developer's own self-check ends an episode and is what is reported; the official judge neither ends a
practice episode nor appears in anything the developer gets (shown with a scene in which the judge says yes from the
first action); a scene that is asked for is built as asked, with the target's own things; an invalid one is refused;
the privileged state reaches the self-check, not the skill."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from m2m.envpaths import maniskill_python  # noqa: E402
from xsim.bi import backend  # noqa: E402

OFFICIAL_WORDS = ("cubes_in_dustpan", "dustpan_left_of_broom", "blocks_never_gripped", "arms_back", "grippers_open", "dustpan_not_lifted", "dustpan_level",
                  "gripped_at_action", "the judge", "official")
T = Path(tempfile.mkdtemp(prefix="m2m-oracle-"))
START = (HERE / "xsim" / "bi" / "fixtures" / "practice_start.py").read_text()


def run(tag, setup, program, seed=1, extra=()):
    d = T / tag; d.mkdir()
    (d / "practice.py").write_text(setup); (d / "program.py").write_text(program)
    r = subprocess.run([maniskill_python(), "-I", str(HERE / "xsim" / "bi" / "oracle_run.py"), "--setup", str(d / "practice.py"), "--seed", str(seed), "--program", str(d / "program.py"),
                        "--out", str(d / "out"), "--private", str(d / "private"), "--no-segmenter", "--run-id", "test"] + list(extra), capture_output=True, text=True, timeout=900)
    res = json.loads((d / "out" / "result.json").read_text()) if (d / "out" / "result.json").exists() else None
    assert res is not None, r.stderr[-900:]
    return d, res, (d / "out" / "output.txt").read_text(errors="replace")


def visible_text(d):
    return "\n".join(p.read_text(errors="replace") for p in (d / "out").rglob("*") if p.is_file() and p.suffix in (".json", ".txt"))


def test_1_a_fresh_scene_and_the_interface_check():
    d, res, log = run("validate", START, backend.INTERFACE_CHECK, extra=["--validate"])
    assert res["ended"] == "returned" and res["simulator"].startswith("maniskill") and res["world_check"] == dict(ok=False, note="this practice file has no self-check")
    sc = res["scene"]
    assert set(sc) == {"dustpan", "brush", "cubes", "colours", "table"} and len(sc["cubes"]) == 3 and max(res["settle"].values()) <= 5.0, (sc, res["settle"])
    assert "# TRUE positions at the start" in log and "dustpan (" in log and "cube_2 (" in log and "broom" not in log        # (the developer's names; privileged state is theirs to see)
    assert (d / "out" / "scene.json").exists() and (d / "out" / "videos" / "head_depth.mp4").exists() and len(list((d / "out" / "shown").glob("*.png"))) == 3
    assert (d / "private" / "layout.json").exists() and "success" in json.loads((d / "private" / "official.json").read_text())["at_the_end"]
    txt = visible_text(d)
    assert not any(w in txt for w in OFFICIAL_WORDS) and '"success"' not in txt, [w for w in OFFICIAL_WORDS if w in txt]
    d2, res2, _ = run("validate_again", START, backend.INTERFACE_CHECK, extra=["--validate"])
    d3, res3, _ = run("validate_other", START, backend.INTERFACE_CHECK, seed=2, extra=["--validate"])
    assert res2["scene"] == sc and res3["scene"] != sc                                 # (a seed gives its fresh scene; another seed another)


SELF_CHECK = '''
def scene(scene, rng):
    return scene
def success(state):
    a = state.arm("left")
    return {"ok": a["from_start_mm"] > 25.0, "left_moved_mm": a["from_start_mm"], "names": state.names, "dustpan": state.pos("dustpan"), "start": state.start["cube_0"],
            "touching": state.touching("cube_0"), "speed": state.speed("brush"), "held": state.held("cube_1"), "rot": state.rot("brush")[0]}
'''
MOVE_THEN_WAIT = '''
def run(robot, task):
    print("the robot has", sorted(k for k in dir(robot) if not k.startswith("_")))
    s = robot.state("left"); p = s["tcp_mm"]
    print("move", robot.move("left", (p[0], p[1], p[2] - 60), s["approach_dir"], s["closing_dir"]))
    robot.wait(8.0)
    print("the wait ran to its end at", robot.time_s())
'''


def test_2_the_developers_self_check_ends_the_episode():
    d, res, log = run("selfcheck", SELF_CHECK, MOVE_THEN_WAIT)
    c = res["world_check"]
    assert res["ok"] is True and res["ended"] == "time: the episode is over" and res["robot_s"] < 3.0 and c["ok"] and c["left_moved_mm"] > 25 and "held_at_s" in c, res
    assert c["names"] == ["dustpan", "brush", "cube_0", "cube_1", "cube_2"] and c["touching"] == dict(robot=False, others=["table"]) and c["held"] is None and len(c["start"]) == 3
    has = log.split("the robot has ")[1].splitlines()[0]
    assert "the wait ran to its end" not in log and "'look'" in has and not any(w in has for w in ("truth", "state_of", "pos", "env", "judge")), has   # (the skill's robot: the documented calls only)
    assert "# your own success():" in log and not any(w in visible_text(d) for w in OFFICIAL_WORDS)


def judged_success_from_the_start():
    """A layout in which the official judge says yes at the first action: the cubes in the pan, the brush to its right."""
    L = json.loads(sorted((HERE / "assets" / "robodojo_sweep" / "panel20").glob("ep_*.json"))[0].read_text())
    pan = L["Rigid"]["broom_shovel"][0]["default_pos"]
    for i, c in enumerate(L["Rigid"]["small_cube"]):
        c["default_pos"] = [pan[0] - 0.01 + 0.03 * (i - 1) * 0, pan[1] + 0.035 * (i - 1), pan[2] + 0.012]
    L["Rigid"]["broom"][0]["default_pos"][0] = pan[0] + 0.30; L["Rigid"]["broom"][0]["default_pos"][1] = pan[1] - 0.25
    f = T / "judged_yes.json"; f.write_text(json.dumps(L))
    return f


WAIT = "def run(robot, task):\n    robot.wait(2.0)\n    print('after the wait', robot.time_s())\n"


def test_3_the_official_judge_does_not_end_or_show_in_practice():
    f = judged_success_from_the_start()
    # control: the evaluator's own runner on that layout ends at once, a success
    c = T / "control"; c.mkdir(); (c / "program.py").write_text(WAIT); (c / "task.txt").write_text("t")
    subprocess.run([maniskill_python(), "-I", str(HERE / "xsim" / "bi" / "target_run.py"), "episode", "--layout", str(f), "--program", str(c / "program.py"), "--out", str(c / "out"),
                    "--task-text-file", str(c / "task.txt"), "--no-segmenter"], capture_output=True, text=True, timeout=900)
    ctrl = json.loads((c / "out" / "result.json").read_text())
    assert ctrl["success"] is True and ctrl["robot_s"] < 0.2 and ctrl["ended"] == "time: the episode is over", ctrl
    # practice on the same layout: the program runs on; no verdict of the judge anywhere the developer can see
    d, res, log = run("practice_on_judged_yes", START, WAIT, extra=["--layout", str(f)])
    assert res["ended"] == "returned" and res["robot_s"] == 2.0 and res["ok"] is False and "after the wait 2.0" in log, res
    txt = visible_text(d)
    assert not any(w in txt for w in OFFICIAL_WORDS) and '"success"' not in txt
    off = json.loads((d / "private" / "official.json").read_text())
    assert off["first_success_action"] == 1 and off["at_the_end"]["success"] is True     # (kept privately: the judge did say yes, from the first action)


ASKED = '''
def scene(scene, rng):
    scene["dustpan"] = {"xy_mm": [-150.0, 200.0], "yaw_deg": 120.0}
    scene["brush"] = {"xy_mm": [300.0, 150.0], "yaw_deg": -45.0}
    scene["cubes"] = [{"xy_mm": [-350.0 + 70.0 * i, -150.0], "yaw_deg": 15.0 * i} for i in range(3)]
    scene["colours"] = {"dustpan": [1.0, 0.0, 1.0], "brush": [0.0, 1.0, 1.0], "cubes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
    scene["table"] = {"rgb": [0.9, 0.9, 0.2]}
    scene["dustpan"]["mass"] = 50.0                  # (not a thing a scene can say: ignored)
    return scene
def success(state):
    import math
    R = state.rot("dustpan")
    return {"ok": False, "start": state.start, "mouth_deg": math.degrees(math.atan2(-R[1][1], -R[0][1]))}
'''


def test_4_a_scene_that_is_asked_for():
    d, res, log = run("asked", ASKED, "def run(robot, task):\n    robot.wait(0.2)\n")
    c = res["world_check"]; s = c["start"]
    assert abs(s["dustpan"][0] + 150) < 5 and abs(s["dustpan"][1] - 200) < 5 and abs(c["mouth_deg"] - 120) < 3, c
    assert abs(s["brush"][0] - 300) < 5 and all(abs(s[f"cube_{i}"][0] - (-350 + 70 * i)) < 5 and abs(s[f"cube_{i}"][1] + 150) < 5 for i in range(3)), s
    L = json.loads((d / "private" / "layout.json").read_text()); B = json.loads((HERE / "assets" / "robodojo_sweep" / "layouts" / "sweep_blocks_4.json").read_text())
    assert L["Rigid"]["broom_shovel"][0]["physics"] == B["Rigid"]["broom_shovel"][0]["physics"] and L["Appearance"]["table"]["rgb"] == [0.9, 0.9, 0.2]
    bad = ASKED.replace('[-350.0 + 70.0 * i, -150.0]', '[-150.0, 200.0 + 30.0 * i]')          # the cubes asked INTO the dustpan
    d2, res2, _ = run("asked_invalid", bad, "def run(robot, task):\n    robot.wait(0.2)\n")
    assert res2["ended"].startswith("the scene asked for is not valid") and "overlap" in res2["ended"] and res2["ok"] is False, res2


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in tests:
        try:
            f(); print("PASS ", n, flush=True)
        except Exception as e:
            import traceback
            bad += 1; print("FAIL ", n, "::", type(e).__name__, str(e)[:900], traceback.format_exc()[-300:], flush=True)
    print(f"{len(tests) - bad} of {len(tests)} passed  (files: {T})")
    sys.exit(1 if bad else 0)
