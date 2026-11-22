"""The bimanual environments (docs/bimanual.md): both load with the rig's cameras; the reference solutions solve the
tasks; a program, replayed tool calls, a (stubbed) model and the shell drive mode all go through the same robot API
and judge."""
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from agentic_robotics import bimanual
from agentic_robotics.bimanual import drive, run, scripted
from agentic_robotics.bimanual.tasks import TASKS, Episode
from agentic_robotics.bimanual.tools import Toolbox, anthropic_content, anthropic_tools, openai_items, openai_tools

ROOT = Path(__file__).resolve().parents[1]
GRIPPERS, DEXTEROUS = "sim_bimanual_grippers", "sim_bimanual_dexterous"
dexterous = pytest.mark.skipif(not (ROOT / "src/agentic_robotics/bimanual/models/assets/sharpa/right").exists(),
                               reason="the Sharpa hand assets are not linked (assets/manifest.yaml sharpa_*)")
ENVS = [GRIPPERS, pytest.param(DEXTEROUS, marks=dexterous)]


def lift_calls(env, seed):
    """The roll lift as tool calls, written from the TRUE pose (a test of the tool layer, not of perception)."""
    ep = Episode(env, "lift", seed=seed, sensors="ideal")
    c = scripted._roll_centre(ep.S) * 1000
    arm = "left" if c[1] > -175 else "right"
    P = arm[0].upper()
    r = lambda v: [round(float(x), 1) for x in v]
    if env == GRIPPERS:
        g = lambda z: ("move_gripper", dict(arm=arm, x=r(c)[0], y=r(c)[1], z=z, approach_dir=[0, 0, -1], closing_dir=[0, 1, 0],
                                             stop_on_contact=False))
        calls = [g(200), g(round(c[2] + 6, 1)), ("set_gripper", dict(arm=arm, width_mm=0)), g(140)]
    else:
        t = math.radians(scripted.ROLL_TILT)
        d = np.array([1.0, 0, 0])
        p = scripted._roll_hand(P, c / 1000, d)[0] * 1000
        h = lambda dz: ("move_hand", dict(hand=arm, x=r(p)[0], y=r(p)[1], z=round(p[2] + dz, 1), fingers_dir=[-math.sin(t), 0, -math.cos(t)],
                                          palm_dir=[math.cos(t), 0, -math.sin(t)], stop_on_contact=False))
        j = {"thumb_CMC_FE": 95, **{f"{f}_{k}": v for f in ("ring", "pinky") for k, v in (("MCP_FE", 80), ("PIP", 90), ("DIP", 50))}}
        tips = lambda cc, rr: {k: r(v * 1000) for k, v in scripted._roll_tips(P, cc / 1000, d, rr).items()}
        calls = [h(120), ("set_joints", dict(hand=arm, joints=j)),
                 ("fingertips", dict(hand=arm, targets=tips(c + [0, 0, 120], scripted.ROLL_R + 0.018))), h(0),
                 ("fingertips", dict(hand=arm, targets=tips(c, scripted.ROLL_R - 0.007), duration_s=1.0)), h(120)]
    return [dict(name=n, args=a) for n, a in calls + [("look", {}), ("done", dict(summary="lifted"))]]


@pytest.mark.parametrize("env", ENVS)
def test_environment_cameras_and_state(env):
    ep = Episode(env, "open_bottle", seed=0)
    S, rb = ep.S, ep.robot
    assert S.NAME == env and rb.kind == env.rsplit("_", 1)[1]
    frames = rb.look()
    assert list(frames) == ["scene", "right_wrist", "left_wrist"]
    for f in frames.values():
        assert f.rgb.shape == (480, 640, 3) and f.depth.shape == (480, 640)
    assert np.linalg.norm(frames["scene"].eye - [864, -8, 572]) < 15            # the rig's D415 (+ this episode's calibration error)
    P = frames["scene"].points()
    assert np.isfinite(P[..., 2]).mean() > 0.5
    for arm, y in (("right", -391), ("left", 41)):                              # the bases at the table's -x edge
        s = rb.state(arm)
        tool = s["tcp_mm"] if env == GRIPPERS else s["palm_mm"]
        assert tool[0] < -400 and abs(tool[1] - y) < 5 and tool[2] > 300
    st = S.get_state()
    rb.move("right", (-350, -300, 300), (0, 0, -1), (0, 1, 0) if env == GRIPPERS else (1, 0, 0))
    S.set_state(st)
    assert np.allclose(S.get_state()["qpos"], st["qpos"])
    assert set(bimanual.ENVS) == {GRIPPERS, DEXTEROUS} and set(TASKS) == {"open_bottle", "move_across", "lift", "insert_cylinder"}


@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("task", ["lift", "move_across", "open_bottle"])
def test_reference_solution(env, task):
    ep = Episode(env, task, seed=1, sensors="ideal")
    assert run.run_scripted(ep, log=lambda *a: None) == "returned"
    v = ep.finish()
    assert v["ok"], v
    assert v["robot_s"] < ep.task.time_limit_s


@pytest.mark.parametrize("offset_mm, ok", [((0.0, 0.0), True), ((0.6, 0.6), True), ((2.0, 0.0), False)])
def test_insert_cylinder_needs_a_millimetre(offset_mm, ok):
    """The hole is 2 mm wider than the stick: the reference (grippers; it reads the true poses) puts the cylinder on with
    its axis within 1 mm of the stick's, not 2 mm off. The cylinder stays where the layout put it until it is grasped."""
    from agentic_robotics.bimanual.scripted import insert_cylinder
    ep = Episode(GRIPPERS, "insert_cylinder", seed=2, sensors="ideal")
    S = ep.S
    x, y, _ = ep.layout["cylinder"]
    S.move("L", S.object_pose("cylinder")[0] + [0.1, 0, 0.17], S.fk("L", S.arms["L"].home)[1])
    assert np.allclose(S.object_pose("cylinder")[0][:2], (x, y), atol=0.001)
    insert_cylinder(S, log=lambda *a: None, offset=tuple(v / 1000 for v in offset_mm))
    v = ep.finish()
    assert v["ok"] == ok, v
    assert v["released"] and v["stand_upright"]
    assert (v["stick_through_mm"] > 35) == ok


@pytest.mark.parametrize("task", ["lift", "move_across", "open_bottle", "insert_cylinder"])
def test_untouched_scene_is_not_a_success(task):
    ep = Episode(GRIPPERS, task, seed=0, sensors="ideal")
    assert not ep.finish()["ok"]


def test_program_mode(tmp_path, capsys):
    rc = run.main(["--env", GRIPPERS, "--task", "lift", "--seed", "0", "--program", str(ROOT / "examples/bimanual_lift.py"),
                   "--out", str(tmp_path), "--quiet"])
    v = json.loads((tmp_path / "result.json").read_text())
    assert rc == 0 and v["ok"] and v["mode"] == "program" and v["sensors"] == "d415"
    bad = tmp_path / "bad.py"
    bad.write_text("def run(robot, task):\n    while True:\n        robot.wait(1)\n")
    ep = Episode(GRIPPERS, "lift", seed=0, time_limit_s=5)
    assert run.run_program(ep, bad).startswith("time:") and not ep.finish()["ok"]


@pytest.mark.parametrize("env", ENVS)
def test_tools_replay_and_drive_agree(env, tmp_path):
    calls = lift_calls(env, 1)
    ep = Episode(env, "lift", seed=1)
    box = Toolbox(ep.robot, max_calls=ep.task.max_calls)
    assert run.replay(box, calls, log=lambda *a: None) == "done: lifted"
    v = ep.finish()
    assert v["ok"] and box.n_calls == len(calls)

    d = tmp_path / "ep"
    first = drive.start(d, env, "lift", seed=1)
    assert "instructions.md" in first and len(list((d / "looks").glob("000_*.png"))) == 3
    for c in calls[:-1]:
        drive.call(d, c["name"], json.dumps(c["args"]))
    w = json.loads(drive.judge(d))
    assert w["ok"] and (w["height_mm"], w["robot_s"]) == (v["height_mm"], v["robot_s"])   # the same episode, across processes' worth of checkpoints
    assert "judged" in drive.call(d, "look", "")
    logged = run.read_calls(d / "calls.jsonl")
    assert logged[0]["free"] and [c["name"] for c in logged[1:]] == [c["name"] for c in calls[:-1]]


def test_toolbox_bad_calls_and_limits():
    ep = Episode(GRIPPERS, "lift", seed=0, time_limit_s=30)
    box = Toolbox(ep.robot, max_calls=4)
    t = lambda items: " ".join(i["text"] for i in items if i["type"] == "text")
    assert "unknown tool" in t(box.call("set_joints", {}))
    assert "bad call" in t(box.call("move_gripper", "x"))
    assert "not done" in t(box.call("move_gripper", dict(arm="left", x=900, y=0, z=200, approach_dir=[0, 0, -1], closing_dir=[0, 1, 0],
                                                         stop_on_contact=False)))
    assert "last tool call" in t(box.call("look", {})) and box.ended.startswith("calls")
    assert "ended" in t(box.call("look", {})) and box.n_calls == 4
    look = Toolbox(Episode(GRIPPERS, "lift", seed=0).robot).call("look", {}, free=True)
    images = []
    items = openai_items(look, images)
    assert len(images) == 3 and [i["image"] for i in items if i["type"] == "input_image"] == [0, 1, 2]
    assert openai_items(look)[2]["image_url"].startswith("data:image/png;base64,")
    assert [c["type"] for c in anthropic_content(look)].count("image") == 3
    assert {s["name"] for s in openai_tools(box.specs)} == {s["name"] for s in anthropic_tools(box.specs)} >= {"look", "home", "done"}


class StubModel:
    """perception.vlm.VLM's respond(), answering with a fixed list of tool calls (no network, no cost)."""

    def __init__(self, calls):
        self.calls, self.seen, self.k = list(calls), [], 0

    def respond(self, input, images, instructions=None, tools=None, previous=None, effort=None, max_output_tokens=None):
        self.seen.append(dict(input=input, n_images=len(images), instructions=instructions, previous=previous))
        self.k += 1
        if not self.calls:
            return dict(ok=True, id=f"r{self.k}", output=[{"type": "message", "content": [{"type": "output_text", "text": "hm"}]}])
        c = self.calls.pop(0)
        return dict(ok=True, id=f"r{self.k}", output=[
            {"type": "message", "content": [{"type": "output_text", "text": f"now {c['name']}"}]},
            {"type": "function_call", "call_id": f"c{self.k}", "name": c["name"], "arguments": json.dumps(c["args"])}])


def test_model_loop_with_a_stub_model():
    calls = lift_calls(GRIPPERS, 2)
    ep = Episode(GRIPPERS, "lift", seed=2)
    box = Toolbox(ep.robot, max_calls=ep.task.max_calls)
    stub, messages = StubModel(calls), []
    assert run.run_model(ep, box, stub, log=lambda *a: None, messages=messages) == "done: lifted"
    assert ep.finish()["ok"] and box.n_calls == len(calls) and len(messages) == len(calls)
    first = stub.seen[0]
    assert first["previous"] is None and first["n_images"] == 3 and first["input"][0]["role"] == "user"
    assert first["input"][0]["content"][0]["text"].startswith("Task: pick up the roll")
    assert all(s["instructions"] == box.instructions for s in stub.seen)             # re-sent every turn
    assert stub.seen[1]["previous"] == "r1" and stub.seen[1]["input"][0]["type"] == "function_call_output"
    assert stub.seen[-1]["n_images"] == 3                                            # the look before done()

    ep = Episode(GRIPPERS, "lift", seed=2)                                           # a model that stops calling tools
    assert run.run_model(ep, Toolbox(ep.robot), StubModel([]), log=lambda *a: None) == "model: stopped calling tools"


def test_write_a_program_with_a_stub_model():
    from agentic_robotics.bimanual import write as W

    class Writer:
        def respond(self, input, images, instructions=None, **k):
            self.n_images, self.instructions, self.text = len(images), instructions, input[0]["content"][0]["text"]
            return dict(ok=True, id="r1", status="completed", output=[{"type": "message", "content": [
                {"type": "output_text", "text": "a note\n```python\ndef run(robot, task):\n    robot.set_gripper('left', 40)\n```\n"}]}])
    m = Writer()
    w = W.write(GRIPPERS, "insert_cylinder", m, example_seed=3, segmenter=True)
    assert w["ok"] and w["program"].startswith("def run(robot, task):") and m.n_images == 3
    assert "robot.segment(" in m.instructions and "tool call" not in m.instructions and "within 1 mm" in m.text
    assert "robot.segment(" not in W.instructions("dexterous", 600.0) and "robot.fingertips(" in W.instructions("dexterous", 600.0)
    assert W.extract_program("no code")[0] is None and W.extract_program("```python\nx = 1\n```")[0] is None
    assert W.extract_program("```python\ndef run(robot, task:\n```")[0] is None


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
def test_development_session_with_a_stub_model(tmp_path):
    """improve.py: the tools of a session (versions, sandboxed practice episodes, their output, trace and images),
    the submitted result, and the budget's end."""
    from agentic_robotics.bimanual import improve, practice

    class Dev:
        def __init__(self, calls, usd=0.01):
            self.calls, self.seen, self.usd = list(calls), [], usd

        def respond(self, input, images, **k):
            self.seen.append(dict(input=input, n_images=len(images), **k))
            n = len(self.seen)
            out = [{"type": "message", "content": [{"type": "output_text", "text": f"step {n}"}]}]
            if self.calls:
                name, args = self.calls.pop(0)
                out.append({"type": "function_call", "call_id": f"c{n}", "name": name, "arguments": json.dumps(args)})
            usd = self.usd if self.usd is not None else k["max_output_tokens"] * 50e-6       # None: a reply of the full length
            return dict(ok=True, id=f"r{n}", status="completed", output=out, usd=usd, wait_s=0.0,
                        tokens=dict(input=1000 * n, cached=0, output=100, reasoning=0))

        def results(self):
            return ["\n".join(c["text"] for c in i.get("output", []) if c.get("type") == "input_text") for s in self.seen for i in s["input"]]

    c = [round(float(x), 1) for x in scripted._roll_centre(Episode(GRIPPERS, "lift", seed=100, sensors="ideal").S) * 1000]
    arm = "left" if c[1] > -175 else "right"
    lift = (f"import numpy as np\n\ndef run(robot, task):\n    f = robot.look(['scene'])['scene']\n    robot.show('roll', f.rgb)\n"
            f"    for z in (200, {c[2] + 6}):\n        robot.move('{arm}', ({c[0]}, {c[1]}, z), (0, 0, -1), (0, 1, 0))\n"
            f"    print('closed to', robot.set_gripper('{arm}', 0))\n    robot.move('{arm}', ({c[0]}, {c[1]}, 140), (0, 0, -1), (0, 1, 0))\n"
            "    print('lifting')\n")
    start = tmp_path / "v0.py"
    start.write_text("def run(robot, task):\n    print('nothing yet')\n")
    dev = Dev([("run_episodes", dict(seeds=[100])), ("run_episodes", dict(seeds=[5])),
               ("write_program", dict(code="import os\n" + lift, note="bad")), ("write_program", dict(code=lift, note="lift from a known pose")),
               ("run_episodes", dict(seeds=[100])), ("read_output", dict(seed=100, contains="set_gripper")),
               ("view_images", dict(seed=100, names=["final", "roll", "none"])),
               ("edit_program", dict(edits=[dict(old="print('lifting')", new="print('up')"), dict(old="nowhere", new="x")], note="-")),
               ("edit_program", dict(edits=[dict(old="print('lifting')", new="print('up')")], note="says up")),
               ("read_program", dict(version=2, start_line=9)), ("submit", dict(version=1, final=True))])
    s = improve.Session(tmp_path / "s", GRIPPERS, "lift", dev, budget_usd=5.0, start=start, log=lambda *a: None)
    assert s.run() == "submitted"
    res = dev.results()
    first = dev.seen[0]
    assert first["n_images"] == 3 and "nothing yet" in first["input"][0]["content"][0]["text"] and "robot.show(label, image)" in first["instructions"]
    assert "v0 seed 100: FAILED" in res[1] and "nothing yet" in res[1] and "[budget: $0.01 of $5 spent" in res[1]
    assert "practice episodes are the seeds 100 to 999" in res[2] and "NOT saved" in res[3] and "import of os" in res[3]
    assert "saved as v1" in res[4] and "v1 seed 100: SUCCESS" in res[5] and "closed to" in res[5] and "TRUE positions" in res[5]
    assert "set_gripper(" in res[6] and "tool (" in res[6] and dev.seen[7]["n_images"] == 2 and "no image 'none'" in res[7]
    assert "NOT saved" in res[8] and "occurs 0 times" in res[8] and "saved as v2" in res[9] and "print('up')" in res[10]
    out = json.loads((tmp_path / "s/result.json").read_text())
    assert out["ended"] == "submitted" and out["result_version"] == 1 and len(out["versions"]) == 3 and out["versions"][2]["base"] == 1
    assert (tmp_path / "s/program.py").read_text() == lift and "v1 seed 100: SUCCESS" in (tmp_path / "s/transcript.md").read_text()
    assert practice.check_program("def run(robot, task):\n    getattr(robot, 'S')\n    robot.look()['scene']._o\n") != []

    dev = Dev([("run_episodes", dict(seeds=[100]))] * 9, usd=None)                   # the budget ends the session: v0 is the result
    s = improve.Session(tmp_path / "b", GRIPPERS, "lift", dev, budget_usd=4.0, start=start, log=lambda *a: None)
    assert s.run() == "budget" and len(dev.seen) == 3 and 3.5 < s.spent() <= 4.0
    assert json.loads((tmp_path / "b/result.json").read_text())["result_version"] == 0
    assert 4000 <= dev.seen[2]["max_output_tokens"] < dev.seen[0]["max_output_tokens"] == 32000 and "Little is left" in dev.results()[-1]

    s = improve.Session(tmp_path / "s", GRIPPERS, "lift", Dev([]), budget_usd=5.0, log=lambda *a: None)       # resumed: its state
    assert s.state["ended"] == "submitted" and len(s.state["versions"]) == 3 and s.state["submitted"] == 1
