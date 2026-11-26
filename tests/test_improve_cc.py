"""bimanual/improve_cc.py without a model call: a scripted stand-in for the claude CLI (tests/cc/fake_agent.py) drives
whole sessions through the launcher and the practice command. What is checked: the Astra path is untouched (its
instructions for the two 1125 asks are byte-identical to the saved ones), the brief differs from Astra's text only
where tools become a command and files, the practice command (versions, worlds, scenes, episodes in the sandbox,
pictures as files, the checks, the final submit with its summary), the caps (budget, nudges, wall time), resuming
after a usage limit, and the feedback session (continue-from with a made-up pack of real episodes)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from agentic_robotics import paths
from agentic_robotics.bimanual import improve, improve_cc
from agentic_robotics.bimanual.improve import SceneSession

FAKE = str(Path(__file__).parent / "cc" / "fake_agent.py")
ASK = "push the block.\n\nOn the table there is a small block. Push it at least 30 mm from where it starts and take the hand off it."
MAIN = Path(os.environ.get("AR_MAIN_RUNS", str(Path(__file__).resolve().parents[2] / "agentic-robotics" / "runs" / "bimanual")))   # (the main checkout beside this one)


def launch(tmp, name, scenario, *more, budget=5.0, hours=0.5, ask=ASK, expect=None):
    cmd = [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from agentic_robotics.bimanual.improve_cc import main; sys.exit(main(sys.argv[2:]))",
           str(paths.ROOT / "src"), "run", "--out", str(tmp / "out" / name), "--root", str(tmp / "root" / name), "--budget-usd", str(budget),
           "--episode-s", "120", "--max-hours", str(hours), "--agent-cmd", FAKE, *more]
    if "--resume" not in more and "--continue-from" not in more:
        cmd += ["--ask", ask]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1500, env=dict(os.environ, FAKE_SCENARIO=scenario, MUJOCO_GL="egl", PYTHONNOUSERSITE="1"))
    last = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    if expect is not None:
        assert last.get("ended") == expect, (r.stdout[-1500:], r.stderr[-1500:])
    return last, tmp / "out" / name, tmp / "root" / name / "workspace", r


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cc")
    return (tmp,) + launch(tmp, "push", "full", expect="submitted")


def test_astra_path_is_unchanged(tmp_path):
    """improve.py of this checkout writes the saved instructions of the two 1125 sessions, byte for byte."""
    class NoModel:
        m = None
    for name, episode_s in (("insert_own_1125", 600.0), ("hang_mugs_own_1125", 900.0)):
        ask = MAIN / f"{name}_ask.txt"
        if not ask.exists():
            pytest.skip("the 1125 ask files are not on this machine")
        SceneSession(tmp_path / name, "sim_bimanual_grippers", "insert_varied", NoModel(), budget_usd=50.0, segmenter=True, effort="high", max_hours=24.0,
                     ask=ask.read_text(), summary=True, worlds=True, own_task=True, episode_s=episode_s, log=lambda *x: None)
        assert (tmp_path / name / "instructions.md").read_bytes() == (MAIN / f"{name}_instructions.md").read_bytes()


def test_brief_differs_only_where_tools_become_a_command(full):
    tmp, last, out, ws, r = full
    s = improve_cc.CodeSession(out, None, 5.0, log=lambda *x: None)
    mine, theirs = s.instr, s.astra_instructions()
    assert (out / "instructions.md").read_text() == mine
    a, b = theirs.splitlines(), mine.splitlines()
    import difflib
    changed = [l for l in difflib.unified_diff(a, b, lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    gone = [l[1:] for l in changed if l[0] == "-"]
    assert len(gone) <= 40, len(gone)                              # the tools block, and seven sentences
    for word in ("edit_program", "edit_world", "final = true", "tool result"):
        assert word not in mine
    for word in ("write_program FILE", "run_episodes --scenes", "notes.md", "pictures/", str(s.cmd), "robot.show(label, image)", "def scene(scene, rng):"):
        assert word in mine, word
    # everything before the session part (the robot's interface) and the worlds' description are Astra's, untouched
    assert mine[:mine.index("and to measure with.")] == theirs[:theirs.index("and to measure with.")]
    w0, w1 = "A world's file defines:", "Your result is the version you submitted last"
    assert mine[mine.index(w0):mine.index(w1)] == theirs[theirs.index(w0):theirs.index(w1)]


def test_session_records(full):
    tmp, last, out, ws, r = full
    st, res = json.loads((out / "state.json").read_text()), json.loads((out / "result.json").read_text())
    assert last["submitted"] == 1 and res["ended"] == "submitted" and res["result_version"] == 1 and res["task_world"] == 0
    assert [p.name for p in sorted((out / "versions").iterdir())] == ["v00.py", "v01.py"] and (out / "worlds" / "w00.py").exists()
    assert (out / "program.py").read_text() == (out / "versions" / "v01.py").read_text()
    assert (out / "summary.md").read_text().startswith("SUMMARY.") and (out / "instructions.md").exists() and (out / "transcript.md").exists()
    runs = st["versions"][1]["runs"]
    assert runs["s1@w0"][0] is True and runs["s2@w0"][0] is True and st["versions"][0]["runs"]["s1@w0"][0] is False     # judged by the world's success()
    assert (out / "runs" / "v01" / "s1@w0" / "result.json").exists() and (out / "runs" / "v01" / "s1@w0" / "world.py").exists()
    a = res["agent"]
    assert a["kind"] == "claude_code" and a["model"] == "opus" and a["effort"] == improve_cc.EFFORT == "xhigh" and a["cost_usd"] > 0 and a["tokens"]["output"] > 0
    assert st["notes"].startswith("The block is pushed")            # notes.md, kept in the state for a session that continues this one
    tr = (out / "transcript.md").read_text()
    assert "**run_episodes**" in tr and "## turn 1:" in tr and "The session ended: submitted" in tr


def test_workspace_and_pictures(full):
    tmp, last, out, ws, r = full
    names = {p.name for p in ws.iterdir()}
    assert {"notes.md", "pictures", "program.py", "world.py", "summary.md", "copy_of_v1.py"} <= names
    assert (ws / "copy_of_v1.py").read_text() == (out / "versions" / "v01.py").read_text()      # read_program --file
    pics = sorted(str(p.relative_to(ws)) for p in (ws / "pictures").rglob("*.png"))
    assert "pictures/first_look/1_scene.png" in pics and "pictures/first_look/2_left_wrist.png" in pics
    assert any("view_images/1_final.png" in p for p in pics) and any("view_images/2_00_start.png" in p for p in pics)
    assert any("run_episodes/1_final.png" in p for p in pics)       # a failed episode's final picture comes with the result
    first = (out / "first_message.md").read_text()
    assert first.startswith("THE SKILL TO MAKE: push the block.") and "[picture: pictures/first_look/1_scene.png]" in first
    # nothing of the platform or the records is in the workspace, and it is outside every checkout
    assert not any(p.name in ("state.json", "result.json", "versions", "runs", "src") for p in ws.rglob("*"))
    assert paths.ROOT not in ws.resolve().parents


def test_final_submit_needs_world_and_summary_and_ends_the_session(full):
    tmp, last, out, ws, r = full
    log = (out / "practice.log").read_text()
    assert "The session has NOT ended: --final needs --summary FILE" in log
    assert "The session has ended." in log
    calls = [json.loads(l) for l in (out / "session.jsonl").read_text().splitlines() if '"kind": "calls"' in l]
    assert calls[-1]["calls"][0]["name"] == "submit"                # the command after the final submit ran nothing
    inv = json.loads((out / "claude" / "session_0.json").read_text())
    assert inv["stopped"] is None and inv["subtype"] == "success" and not inv["estimated"]      # the agent ended its turn: the CLI's own total is the cost
    assert abs(json.loads((out / "result.json").read_text())["spent_usd"] - inv["cli_total_usd"]) < 1e-4
    assert json.loads((out / "state.json").read_text())["versions"][1]["at"]["min"] >= 0


def test_checks_and_refusals(tmp_path):
    (tmp_path / "root").mkdir()
    last, out, ws, r = launch(tmp_path, "bad", "bad")
    (ws.parent / "outside.py").write_text("def run(robot, task):\n    pass\n")
    log = (out / "practice.log").read_text()
    assert "PRACTICE FAILED" not in log
    assert log.count("is not a file of your workspace") >= 2        # ../outside.py, /etc/passwd (each time the agent is nudged)
    assert "NOT saved, the program is not allowed" in log and "NOT saved, the world is not allowed" in log
    assert "there is no version yet" in log
    st = json.loads((out / "state.json").read_text())
    assert st["versions"] == [] and st["worlds"] == [] and st["submitted"] is None
    assert "which sub-command?" in (out / "claude" / "claude_0.jsonl").read_text()


def test_practice_command_only_inside_its_session(full, tmp_path):
    tmp, last, out, ws, r = full
    cmd = str(ws.parent / "practice")
    env = dict(PATH=os.environ["PATH"], AR_CC_SESSION=str(out))
    a = subprocess.run([cmd, "list_scenes"], capture_output=True, text=True, cwd=str(tmp_path), env=env)
    assert "only from the session's workspace" in a.stdout
    b = subprocess.run([cmd, "list_scenes"], capture_output=True, text=True, cwd=str(ws), env=dict(PATH=os.environ["PATH"]))
    assert "only inside a session" in b.stdout
    c = subprocess.run([cmd, "list_scenes"], capture_output=True, text=True, cwd=str(ws), env=env)
    assert "The session has ended" in c.stdout
    # a module planted in the workspace is not imported by the command (Python runs isolated)
    (ws / "json.py").write_text("raise SystemExit('planted')\n")
    d = subprocess.run([cmd, "list_scenes"], capture_output=True, text=True, cwd=str(ws), env=env)
    (ws / "json.py").unlink()
    assert "planted" not in d.stdout + d.stderr and "The session has ended" in d.stdout


def test_budget_cap_stops_the_agent(tmp_path):
    last, out, ws, r = launch(tmp_path, "over", "overspend", budget=3.0, expect="budget")
    c = json.loads((out / "cost.json").read_text())
    assert 3.0 <= c["usd"] < 4.6                                    # stopped within a few replies of the cap (0.5 $ each)
    assert json.loads((out / "result.json").read_text())["result_version"] is None      # nothing was submitted


def test_agent_that_stops_is_nudged_then_ended(tmp_path):
    last, out, ws, r = launch(tmp_path, "stall", "stall", expect="the model stopped calling tools")
    st = json.loads((out / "state.json").read_text())
    assert len(st["invocations"]) == 1 + improve_cc.NUDGES and all(i["resumed"] for i in st["invocations"][1:])
    assert (out / "claude" / "prompt_1.md").read_text().startswith("Go on with the tools; call submit with --final")
    # the resumed conversation's CLI total is cumulative: each invocation's own share is what is added up
    assert abs(st["usd"] - st["invocations"][-1]["cli_total_usd"]) < 1e-6


def test_wall_time_ends_the_session(tmp_path):
    last, out, ws, r = launch(tmp_path, "wall", "overspend", budget=1e6, hours=0.002, expect="wall time: 0.002 h")


def test_usage_limit_stops_and_resume_goes_on(tmp_path):
    last, out, ws, r = launch(tmp_path, "limit", "limit")
    assert last["ended"] is None and last["stopped"] == "unfinished" and r.returncode == 1
    st = json.loads((out / "state.json").read_text())
    assert len(st["worlds"]) == 1 and st["invocations"][0]["subtype"] == "error_during_execution"
    last, out, ws, r = launch(tmp_path, "limit", "limit", "--resume", expect="submitted")
    st = json.loads((out / "state.json").read_text())
    assert len(st["worlds"]) == 1 and len(st["versions"]) == 2 and st["submitted"] == 1 and len(st["invocations"]) == 2
    assert st["invocations"][1]["resumed"] == st["invocations"][0]["session_id"]
    assert (out / "claude" / "prompt_1.md").read_text().startswith("The session was interrupted and goes on now.")


def make_pack(d):
    """A made-up pack of two real episodes, in the format scripts/real_feedback_pack.py writes and improve.py reads."""
    import cv2
    d.mkdir(parents=True)
    (d / "pack.json").write_text(json.dumps(dict(episodes=["R1", "R2"], made_up=True)))
    (d / "rig.md").write_text("A made-up rig for the tests: one arm, two cameras; moves until contact are slow.\n")
    for k, n in enumerate(("R1", "R2")):
        e = d / n; (e / "images").mkdir(parents=True); (e / "depth").mkdir()
        look = dict(image="look_00_scene", robot_time_s=0.0, pixels_without_depth=0.25, K=[640, 480, 600.0, 600.0, 320.0, 240.0])
        ep = dict(version=1, when="2026-11-25 10:0%d" % k, verdict="fail" if k == 0 else "success", operator_note="made up: the block was not reached" if k == 0 else "",
                  operator_intervened=False, knocked_off=False, robot_time_s=42.0 + k, setup="a red block at about (-300, 160) mm", contact="confirmed",
                  program_ended="returned", output_lines=3, shown=[], video_frames={}, server_guarded_lines=[], asks=[], looks=[look],
                  moves_until_contact=[dict(t=3.0, goal_mm=[-300, 160, 20], result="contact", tool_after_mm=[-300, 160, 31.5])])
        (e / "episode.json").write_text(json.dumps(ep))
        (e / "output.txt").write_text("# task: push the block\n# t=   3.0 move(...) -> stopped: contact | left tool (-300, 160, 31.5)\nmove 40 True\n")
        cv2.imwrite(str(e / "images" / "look_00_scene.png"), np.full((48, 64, 3), 90 + 60 * k, np.uint8))
        cv2.imwrite(str(e / "depth" / "look_00_scene.png"), np.full((480, 640), 700, np.uint16))


def test_feedback_session_continues_an_ended_one_with_a_pack(full, tmp_path):
    tmp, last, src, ws0, r = full
    pack = tmp_path / "pack"; make_pack(pack)
    last, out, ws, r = launch(tmp_path, "push_fb", "feedback", "--continue-from", str(src), "--real-feedback", str(pack), expect="submitted")
    st = json.loads((out / "state.json").read_text())
    assert st["continued"]["submitted"] == 1 and st["real"] == str(pack.resolve()) and len(st["versions"]) == 4 and len(st["worlds"]) == 1
    first = (out / "first_message.md").read_text()
    assert "This session continues an earlier one, which ended with v1 as its result." in first and "Start with list_real_episodes." in first
    assert "The block is pushed by the fingertip" in first and "R1 (v1: fail), R2 (v1: success)" in first      # the notes, the pack's episodes
    assert (ws / "program.py").read_text() == (src / "versions" / "v01.py").read_text() and (ws / "worlds" / "w0.py").exists()
    assert (ws / "notes.md").read_text().startswith("The block is pushed")
    brief = (out / "instructions.md").read_text()
    assert "The real robot. Versions of the program have been run on the real robot" in brief and "list_real_episodes [NAME ...]" in brief
    assert "view_real_images --episode NAME --names NAME ..." in brief and "`episodes`" not in brief
    log = (out / "practice.log").read_text()
    assert "THE REAL ROBOT" in log and "A made-up rig for the tests" in log and "real episode R1: output.txt has 3 lines" in log
    assert any("view_real_images/1_look_00_scene.png" in str(p) for p in (ws / "pictures").rglob("*.png"))
    assert (src / "state.json").exists() and len(json.loads((src / "state.json").read_text())["versions"]) == 2      # the source is not changed
    # the SAME pack and source feed the Astra path: its session builds, with the same first message but for the workspace's line
    class NoModel:
        m = None
    a = SceneSession(tmp_path / "astra_fb", "sim_bimanual_grippers", "insert_varied", NoModel(), budget_usd=5.0, segmenter=True, effort="high",
                     real=str(pack), continue_from=str(src), ask=json.loads((src / "state.json").read_text())["ask"], summary=True, worlds=True,
                     own_task=True, episode_s=120.0, log=lambda *x: None)
    theirs = a.inp[0]["content"][0]["text"]
    assert first.startswith(theirs.rstrip("\n")) and first[len(theirs.rstrip("\n")):].strip().startswith("In your workspace: program.py is v1")
    assert "list_real_episodes" in [t["name"] for t in a.specs]


def test_program_sandbox_is_closed(tmp_path):
    """The sandbox the practice command runs programs and worlds in (practice.run_sandboxed's: execution/sandbox.py),
    asked directly, without the static check that would refuse this code: no home, no checkout but its read-only src,
    no records, no workspaces, no key in the environment, no network."""
    from agentic_robotics.bimanual import practice, scenes
    from agentic_robotics.execution import sandbox
    refused = practice.check_program("import os, socket\ndef run(robot, task):\n    pass\n")
    assert refused, "the static check lets os and socket through"
    r = practice.run_sandboxed("sim_bimanual_grippers", "insert_varied", 1, _file(tmp_path / "bad" / "program.py", "import os\ndef run(robot, task):\n    pass\n"),
                               tmp_path / "e", scene=scenes.empty(1), practice=True, wall_s=300.0)
    assert str(r.get("ended", "")).startswith("refused"), r.get("ended")       # the episode itself checks the program again
    probes = [str(Path.home() / ".claude"), str(Path.home() / ".ssh"), str(MAIN), str(MAIN.parent.parent / "configs" / "local.yaml"),
              str(MAIN.parent.parent / "secrets"), str(MAIN.parent.parent / "real" / "results"), str(paths.ROOT / "configs"), str(paths.ROOT / "tests"),
              str(improve_cc.WORKSPACES), str(paths.ROOT / "runs"), str(paths.ROOT / "bin")]
    code = ("import os, socket, json\nseen = [p for p in %r if os.path.exists(p)]\ntry:\n    socket.create_connection(('1.1.1.1', 53), timeout=2); net = True\n"
            "except OSError:\n    net = False\nprint(json.dumps(dict(seen=seen, net=net, home=os.environ.get('HOME'), "
            "keys=sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k), src=os.path.exists(%r), "
            "beside=sorted(os.listdir(%r)), inside=sorted(os.listdir(%r)))))\n" % (probes, str(paths.ROOT / "src"), str(paths.ROOT.parent), str(paths.ROOT)))
    prog, out, sock = tmp_path / "p", tmp_path / "o", tmp_path / "s"
    for d in (prog, out, sock):
        d.mkdir()
    cmd = sandbox.program_cmd([sys.executable, "-c", code], prog, out, sock, extra_env={"PYTHONPATH": str(paths.ROOT / "src")})
    got = json.loads(subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout)
    beside, inside = got.pop("beside"), got.pop("inside")           # (only what is bound: the environment's checkout and this one's src)
    assert got == dict(seen=[], net=False, home="/tmp", keys=[], src=True), got
    assert set(beside) <= {paths.ROOT.name, MAIN.parent.parent.name} and set(inside) <= {"src", "pyproject.toml", ".venv", ".uv"}, (beside, inside)


def _file(p, body):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(body)
    return p
