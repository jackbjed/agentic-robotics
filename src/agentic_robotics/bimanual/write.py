"""Code as policy: ask a model ONCE for a program run(robot, task) that does a bimanual task (docs/bimanual.md), and
save it. The model gets the robot's description (the text of direct control, tools.py), the robot API (api.Robot),
the task, and the first look of one example episode (colour images and the arms' states); it cannot run or revise
the program. The program is then run on other episodes with `run --program`.

    python -m agentic_robotics.bimanual.write --env sim_bimanual_grippers --task insert_cylinder --model astra \\
           --example-seed 3 --segmenter sam3 --out runs/bimanual/x                                        (PAID)
    python -m agentic_robotics.bimanual.run --env sim_bimanual_grippers --task insert_cylinder --seed 0 \\
           --program runs/bimanual/x/program.py --segmenter sam3 --video --out runs/bimanual/x/s0

--out DIR gets program.py (the program, as the model wrote it), prompt.md (all the text the model got), look_*.png
(the example's images), reply.md (the whole reply) and write.json (how the call went, its cost).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

from .tasks import TASKS, Episode
from .tools import DEXTEROUS, GRIPPERS, Toolbox, openai_items

_HEAD = """You write a Python program that controls a two-armed robot: a file that defines run(robot, task). It is run once
per episode, from the episode's start (both arms at their start poses, open), with `robot` the robot's interface
described below and `task` the task's text. Nothing else moves the robot, and the program knows nothing of the world
but what `robot` gives it: the cameras and the arms' own measurements. Each arm is a Franka Panda with {tool}.

"""

_API = """
The program's interface: the commands named above are methods of `robot` (lengths in mm, table frame; angles in degrees;
arm is "right" or "left"; directions and points are any sequence of 3 numbers):

robot.look(cameras=None) -> {name: Frame} of "scene", "right_wrist", "left_wrist" (all three by default). A Frame f has
    f.rgb                    (480, 640, 3) uint8, RGB
    f.depth                  (480, 640) float: mm along the optical axis, nan where there is no depth
    f.fx, f.fy, f.cx, f.cy   pinhole intrinsics (px); f.width, f.height
    f.eye, f.R               the camera's position (mm) and rotation (3x3, camera axes -> table frame; camera axes: x
                             right, y down, z forward) as the robot believes them (the calibration error above)
    f.locate(u, v)           -> np.array([x, y, z]) or None: the 3D point of the surface at pixel column u, row v
                             (the median depth in a 7x7 window): this is locate()
    f.points()               -> (480, 640, 3) array: every pixel's 3D point (nan where there is no depth)
    f.project(xyz)           -> (pixels (N, 2) as (u, v), depths (N,) mm) of table-frame points (N, 3) in this frame
{state}
{commands}
robot.home(arm)              that arm back to its start pose by turning its joints (refused if it would hit something)
robot.wait(seconds)          let robot time pass (at most 10 s a call);  robot.time_s() -> robot time since the start
{segment}{ask}
The motion commands (move, turn, home) return a dict: done (the target was reached), moved (False: the command was
refused and nothing moved), why ("" | "contact": a stop_on_contact move stopped at a touch | the refusal's or the
safety stop's text), curved (the move was made by turning the joints). They do not raise for a refused move.

The program: Python 3.11 with numpy, scipy, cv2 (OpenCV) and the standard library. No files, no network, and nothing
of `robot` beyond what is listed here. What it prints goes to the episode's log. An episode has {time_s} of robot
time (computing does not count); after that the next command raises an exception and the episode ends. The task is
judged on the state of the world when run() returns or raises. The arm's safety stops a move if the net force on that
arm exceeds about 40 N; anything in the way of an arm is pushed or knocked over.
"""

_ONCE = """
The program is run exactly as you write it: you cannot test it, see its output or revise it. Write it so that it finds
things with the cameras in each episode, checks what it measured, and recovers where it can.
"""

_STATE = {
    "grippers": """robot.state(arm) -> dict: tcp_mm [x, y, z], approach_dir, closing_dir, opening_mm (measured between the pads),
                             commanded_mm, force_n [fx, fy, fz] (the external-force estimate)""",
    "dexterous": """robot.state(arm) -> dict: palm_mm [x, y, z], fingers_dir, palm_dir, thumb_side, fingertips_mm {finger: [x, y, z]},
                             joints_deg and targets_deg {joint: deg}, fingertip_force_n {finger: N}, force_n [fx, fy, fz]""",
}
_COMMANDS = {
    "grippers": """robot.move(arm, xyz, approach_dir, closing_dir, stop_on_contact=False)    move_gripper
robot.turn(arm, centre, axis, angle_deg)                                  turn_gripper
robot.set_gripper(arm, width_mm) -> the opening measured afterwards (mm): the width of what is between the fingers""",
    "dexterous": """robot.move(arm, xyz, fingers_dir, palm_dir, stop_on_contact=False)        move_hand
robot.turn(arm, centre, axis, angle_deg)                                  turn_hand
robot.set_joints(arm, joints, duration_s=0.8, stop_force_n=None) -> dict(stopped=[fingers], clipped, unknown)
robot.fingertips(arm, targets, duration_s=0.8, stop_force_n=None) -> dict(stopped=[fingers], miss_mm={finger: mm})""",
}
_SEGMENT = """robot.segment(frame, phrase) -> [(mask, score), ...]: an open-vocabulary segmentation model (SAM3) on the frame's colour
                             image, prompted by a short noun phrase: at most 6 masks ((480, 640) bool), best score
                             first; it can miss the thing, return part of it, or another thing. About 1 s a call.
"""

_ASKER = """robot.ask(question, images=(), effort="low") -> dict: a question to a vision-language model ({model}), with up to
                             {images} images: Frames, or arrays (H x W x 3 uint8 RGB, or H x W), such as a crop or a frame
                             with marks drawn on it (cv2); an image whose longer side is over {side} px is scaled
                             down to that. The dict: ok, text (the answer: ask for a form the program can parse, such
                             as JSON; pixels are those of the image as sent), wait_s, usd, calls_left; or ok False
                             with why. At most {calls} calls an episode (further ones: ok False). The model knows
                             nothing but the question and its images: not the task, the robot or earlier calls. The
                             robot stands still while the answer comes, and that wait is robot time (a few seconds
                             at effort "low"; "medium" and "high" let the model reason longer: slower, dearer). A
                             call costs model usage: about $0.01 with one or two images at "low", more with more
                             images, a long answer or more effort.
"""

# how much the layout changes from one episode to the next, as told to the model (tasks.py: the layouts)
VARIES = {
    "open_bottle": "the bottle stands anywhere within about 12 cm of where it is in the example",
    "move_across": "the roll lies anywhere within about 15 cm of where it is in the example",
    "lift": "the roll lies anywhere within about 25 cm of where it is in the example",
    "insert_cylinder": "the cylinder and the stand are each anywhere within about 12 cm of where they are in the example, and "
                       "the stand is turned about the vertical by up to 45 degrees either way",
    "insert_varied": "another cylinder and another stand (other sizes, colours and weights) stand elsewhere, the stand turned about the "
                     "vertical by up to 45 degrees either way, and there can be other objects on the table",
    "pick_clutter_medium": "another heap of other objects (other shapes, sizes, colours and numbers) lies elsewhere on the table, and "
                           "the task names another object of it; that object touches its neighbours but can be seen",
    "pick_clutter_hard": "another heap of other objects (other shapes, sizes, colours and numbers) lies elsewhere on the table, and "
                         "the task names another object of it, which lies under other objects, hidden or nearly",
    "pick_clutter_extra_hard": "another heap of other objects (other shapes, sizes, colours and numbers) lies in another open box "
                               "(other size, colour, place and turn), and the task names another object of it, which lies under "
                               "other objects, hidden or nearly",
    "biolab_open": "other racks (other numbers of holes, colours and heights) stand elsewhere, turned otherwise, the tube stands "
                   "in another hole, and the pipette and the cup (not used) are others, elsewhere",
    "biolab_drops": "other racks (other numbers of holes, colours and heights) stand elsewhere, turned otherwise, the tube and the "
                    "pipette stand in other holes, and another cup (other size and colour) stands elsewhere",
}

_ASK = """

Write the program for this task. It will be run on episodes other than the example below; in each episode the layout
is new: {varies}.

Reply with the whole program in one ```python code block (a short note before it is fine).

The first look of one example episode (the arms at their start poses; the three cameras' colour images):"""


_ONE_ARM_FRAME = """The robot works with one arm, its left one, whose base is at (x, y) = (-790, 40), just
below table height. Its other arm (the right one, base at (-800, -390)) stays parked at its start pose for the whole
episode, its gripper at about (-490, -390, 350): it is not in the interface, and it is solid. The arm keeps the
safety limits of the real robot it stands for; a move that would break one is refused, with the limit named (there in
m and rad), and nothing moves:
- the TCP stays within x -720..100, y -420..600, z 0..800;
- the fingertips' ends go at most 4 mm below z = 0, and every other part of the gripper, the wrist camera and the arm
  stays at least 50 mm above the table. So near the table the gripper points down or nearly so: it can lean from the
  vertical by about 30 degrees with the TCP at z = 20, 40 at z = 40, 50 at z = 70 and 65 at z = 110 (less in some
  directions and places), and lie horizontal only from about z = 160 up, and not in every direction;
- no part of the gripper or the arm enters the space around the parked arm: x < -420 together with y < -280 (up to
  z = 800);
- the joints' ranges are a little narrower than a Franka Panda's.
Pointing straight down with the TCP at z = 20, the gripper reaches x from about -480 to -90 for y between -210 and
120 (as far as y = -390 where x > -330), and x from about -600 to -150 for y between 270 and 600 (the far half of the
table is out of reach); nothing farther than about 800 mm from the arm's base. A move the arm cannot make is refused
and nothing moves."""

# the two-armed text -> the one-armed robot's (arm="left": the real single-arm rig's arm and limits, core.BiCore.only)
_ONE_ARM = (
    ("controls a two-armed robot:", "controls a robot arm:"),
    ("(both arms at their start poses, open)", "(the arm at its start pose, the gripper open)"),
    ("the cameras and the arms' own measurements. Each arm is a Franka Panda with",
     "the cameras and the arm's own measurements. The arm is a Franka Panda with"),
    ("The grippers. A gripper's pose is its TCP", "The gripper. Its pose is its TCP"),
    ("Cameras (look()), all RealSense D415 colour + depth cameras, 640x480:", "Cameras (look()), two RealSense D415 colour + depth cameras, 640x480:"),
    ("the robot's right arm is on the left of that image.", "the parked right arm is on the left of that image."),
    ("2. the right arm's wrist camera and 3. the left arm's wrist camera: each sits on its gripper's body",
     "2. the arm's wrist camera (\"left_wrist\"): it sits on the gripper's body"),
    ("At the start (grippers pointing straight down)", "At the start (the gripper pointing straight down)"),
    ('arm is "right" or "left";', 'arm is always "left";'),
    ('of "scene", "right_wrist", "left_wrist" (all three by default)', 'of "scene", "left_wrist" (both by default)'),
)


def one_arm(text):
    """The two-armed instructions -> the one-armed robot's (every piece replaced must be there exactly once)."""
    a, b = "The right arm's base is at", "the other arm is refused."
    if text.count(a) != 1 or text.count(b) != 1:
        raise ValueError("the frame paragraph of the two-armed text is not there")
    text = text[:text.index(a)] + _ONE_ARM_FRAME + text[text.index(b) + len(b):]
    for old, new in _ONE_ARM:
        if text.count(old) != 1:
            raise ValueError(f"the two-armed text has {text.count(old)} of {old!r}")
        text = text.replace(old, new)
    return text


def instructions(kind, time_s, segmenter=False, closing=_ONCE, asker=None, arm=None):
    """The system text for writing a program: the robot's description of direct control, with the program's interface
    in place of the tool calls, and how the program is used (closing: one attempt; improve.py: a session). time_s: the
    episode's robot time (a number, or text). asker: the name of the model behind robot.ask, if programs have it
    (practice.py: ASK, AskBridge)."""
    from .practice import ASK
    ask = _ASKER.format(model=asker, images=ASK["images"], side=ASK["side"], calls=ASK["calls"]) if asker else ""
    time_s = time_s if isinstance(time_s, str) else f"{time_s:.0f} s"
    src = GRIPPERS if kind == "grippers" else DEXTEROUS
    tool = src[src.index("Each arm is a Franka Panda with ") + len("Each arm is a Franka Panda with "):src.index(".\nEach tool call")]
    body = src[src.index("Frame and units:"):src.index("In all moves the arm's safety")]
    body = body.replace("every result reports, for each arm, the arm's estimate", "robot.state(arm) gives, as force_n, the arm's estimate")
    body = body.replace("the result says where it stopped", "robot.state(arm) tells where it stopped")
    out = (_HEAD.format(tool=tool) + body.rstrip() + "\n"
           + _API.replace("{state}", _STATE[kind]).replace("{commands}", _COMMANDS[kind])
           .replace("{segment}", _SEGMENT if segmenter else "").replace("{ask}", ask).replace("{time_s}", time_s))
    if arm is not None:
        if arm != "left" or kind != "grippers":
            raise ValueError('arm: "left" (the one-armed robot, with grippers) or none')
        out = one_arm(out)
    return out + closing


def extract_program(reply):
    """The last ```python block of a reply -> (code, why): code None if there is none, it does not parse, or it does
    not define run()."""
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", reply, flags=re.S)
    if not blocks:
        return None, "no code block in the reply"
    code = blocks[-1].rstrip() + "\n"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return None, f"the program does not parse: {e}"
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        return None, "the program does not define run(robot, task)"
    return code, ""


def write(env, task, vlm, example_seed, sensors="d415", segmenter=False, effort=None, max_output_tokens=None):
    """One model call -> dict(ok, why, program, reply, instructions, text, images, ...): vlm is perception.vlm.VLM (or
    anything with its respond())."""
    ep = Episode(env, task, seed=example_seed, sensors=sensors)
    items = Toolbox(ep.robot).call("look", {}, free=True)
    instr = instructions(ep.robot.kind, ep.task.time_limit_s, segmenter)
    text = ep.text + _ASK.format(varies=VARIES.get(ep.task.name, "the objects are elsewhere"))
    images = []
    inp = [{"role": "user", "content": [{"type": "input_text", "text": text}] + openai_items(items, images)}]
    out = dict(ok=False, program=None, reply="", instructions=instr, text=text, items=items)
    r = vlm.respond(inp, images, instructions=instr, effort=effort, max_output_tokens=max_output_tokens)
    if not r.get("ok"):
        return dict(out, why=f"model: {r.get('why')}")
    reply = "\n".join(c.get("text", "") for o in r["output"] if o.get("type") == "message"
                      for c in o.get("content") or [] if c.get("type") == "output_text")
    code, why = extract_program(reply)
    if code is None and r.get("status") not in (None, "completed"):
        why += f" (the reply's status: {r.get('status')})"
    return dict(out, ok=code is not None, why=why, program=code, reply=reply, status=r.get("status"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", required=True, choices=["sim_bimanual_grippers", "sim_bimanual_dexterous"])
    ap.add_argument("--task", required=True, choices=list(TASKS))
    ap.add_argument("--model", required=True, help="a model of configs/models.yaml")
    ap.add_argument("--example-seed", type=int, required=True, help="the episode whose first look the model gets (not one to run on)")
    ap.add_argument("--sensors", default="d415", choices=["d415", "ideal"])
    ap.add_argument("--segmenter", choices=["sam3"], help="tell the model of robot.segment() (run the program with it too)")
    ap.add_argument("--max-usd", type=float, default=3.0, help="this call's budget")
    ap.add_argument("--effort", help="reasoning effort (default: the model's in configs/models.yaml)")
    ap.add_argument("--max-output-tokens", type=int, default=40000, help="reasoning included")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    from ..perception.vlm import VLM
    vlm = VLM(dict(model=a.model, converse=True, max_calls_per_episode=1, max_usd_per_episode=a.max_usd, max_usd_per_call=a.max_usd),
              tag=f"bimanual_write_{a.task}:{a.env}:{a.example_seed}")
    w = write(a.env, a.task, vlm, a.example_seed, sensors=a.sensors, segmenter=bool(a.segmenter), effort=a.effort,
              max_output_tokens=a.max_output_tokens)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    import cv2
    shown = []
    for i in w["items"]:
        if i["type"] == "text":
            shown.append(i["text"])
        else:
            cv2.imwrite(str(out / f"look_{i['name']}.png"), i["rgb"][..., ::-1]); shown.append(f"[image: look_{i['name']}.png]")
    (out / "prompt.md").write_text("# Instructions\n\n" + w["instructions"] + "\n# Message\n\n" + w["text"] + "\n\n" + "\n".join(shown) + "\n")
    (out / "reply.md").write_text(w["reply"] + "\n")
    if w["program"]:
        (out / "program.py").write_text(w["program"])
    v = dict(ok=w["ok"], why=w["why"], task=a.task, env=a.env, example_seed=a.example_seed, sensors=a.sensors,
             segmenter=a.segmenter, status=w.get("status"), program_lines=len((w["program"] or "").splitlines()),
             **vlm.usage(), effort=a.effort or vlm.m.get("effort"))
    (out / "write.json").write_text(json.dumps(v, indent=1) + "\n")
    print(json.dumps(v))
    return 0 if w["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
