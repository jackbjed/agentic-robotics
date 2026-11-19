"""Astra's instructions and tools in direct control, VERBATIM from panda_twin code/real/astra_e2e.py (11-06,
md5 7a8dfece5872fb6e88cdbd0485646621). pick.py adapts only what the task and the cameras change (marked there)."""

INSTRUCTIONS = """You control a robot arm (a Franka Panda with a parallel gripper) through tools; nothing else moves it. Each
tool call is one action; look at the result before the next one.

Frame and units: millimetres, in the table frame: z up, the table surface at z = 0. The arm's base stands at
(x, y) = ({bx:.0f}, {by:.0f}); +x points away from the base (forward), +y to the base's left. The arm reaches roughly x from
-720 to -50 and y from -300 to 550 near the table (a move it cannot do is refused and nothing moves). A second arm,
parked, stands at y < -280, x < -420: never go there.

Gripper: opening 0-80 mm, two soft fin-ray fingers. Each finger is 26 mm wide (across the closing direction). Seen
from the side a finger is a wedge: about 3 mm thick at its tip, 11 mm thick 16 mm above the tip, 20 mm thick 36 mm
above it, 37 mm thick 76 mm above it; the two inner faces are flat and parallel, the thickness grows outwards. Above
the fingers is the hand: 21 cm long along the closing direction and 7 cm across it, from 12 cm above the fingertips
up; the wrist camera sticks out 10 cm to one side of it, from 11 cm above the fingertips up. A closing grasp squeezes
with about 30 N.

The pose you command is the fingertip point: the midpoint between the two fingertips at their lowest point.
yaw_deg is the closing direction: the fingers close along the horizontal direction at yaw_deg from +x towards +y
(0 = along x, 90 = along y; yaw and yaw + 180 are the same). The fingers point straight down unless you tilt them:
tilt_deg swings the fingertips about the closing axis towards the horizontal direction yaw_deg + 90 (negative: the
other way), lean_deg lowers the finger on the yaw_deg side and raises the other; both default 0, at most 45.

Cameras (look()):
1. the scene camera, fixed across the table, facing the robot: in its image +x points towards the camera (down in
   the image) and +y to the right. The arm you control is the one in the middle of that image; the arm left of it is
   the parked one.
2. the wrist camera, on the hand, looking straight down past the fingers (the fingers are mostly out of its view;
   it sits beside the hand, so the point under the fingers is not the image centre). Drawn on its image: two cyan
   squares = where the two fingertips would touch the table if the hand went straight down (at the current opening
   and yaw), a cyan cross between them, and arrows showing the +x and +y directions in that image.
Both cameras measure depth: locate(camera, points) turns pixels of the latest look into 3D points (mm) on the
surface seen there. There is no depth for surfaces nearer than 28 cm to a camera (the wrist camera close to things)
and on some dark, thin or shiny spots: then that point is null. Depth is noisy by a few mm.

Motion: move(x, y, z, yaw_deg, stop_on_contact, tilt_deg, lean_deg) moves the fingertip point along a straight line to
the target (the orientation changes along the way; with stop_on_contact the hand first turns in place). With
stop_on_contact = true it moves at 4 cm/s and stops as soon as something holds the fingers up or pushes up on them;
the result says where it stopped. With stop_on_contact = false it follows the line whatever it touches (3 cm/s when
the fingertips are below 6 cm, faster above): that is how you push things. In both cases the arm's safety stops it if
it presses with more than about 35 N or is blocked; it then holds where it stopped and you can go on. Anything in the
way of the fingers or the hand is pushed or knocked over. The fingertips may not go below z = -3.

gripper(width_mm): 0 = close with force (a grasp); any other value opens or closes to that width. The result gives the
measured opening: a closing grasp that stops above about 2 mm holds something about that wide.

You have at most {max_calls} tool calls. When the task is done, or cannot be done, call done()."""

_NO_ARGS = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
TOOLS = [
    {"type": "function", "name": "look", "description": "Both cameras' current colour images and the arm's state.",
     "parameters": _NO_ARGS},
    {"type": "function", "name": "locate",
     "description": "3D points (mm, table frame) of the surface seen at pixels of the LATEST look's image of that camera "
                    "(the median depth in a 7x7 pixel window; null where there is no depth).",
     "parameters": {"type": "object", "properties": {
         "camera": {"type": "string", "enum": ["scene", "wrist"]},
         "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}, "description": "[[x, y], ...] pixels"}},
         "required": ["camera", "points"], "additionalProperties": False}},
    {"type": "function", "name": "move",
     "description": "Move the fingertip point (mm, table frame) along a straight line to (x, y, z) with the closing "
                    "direction yaw_deg; stop_on_contact: stop at the first contact (4 cm/s) or follow the line regardless "
                    "(pushing). tilt_deg / lean_deg optional (default 0).",
     "parameters": {"type": "object", "properties": {
         "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}, "yaw_deg": {"type": "number"},
         "stop_on_contact": {"type": "boolean"}, "tilt_deg": {"type": "number"}, "lean_deg": {"type": "number"}},
         "required": ["x", "y", "z", "yaw_deg", "stop_on_contact"], "additionalProperties": False}},
    {"type": "function", "name": "gripper", "description": "0 = close with force (grasp); else open/close to width_mm (max 80).",
     "parameters": {"type": "object", "properties": {"width_mm": {"type": "number"}}, "required": ["width_mm"],
                    "additionalProperties": False}},
    {"type": "function", "name": "done", "description": "End the episode.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"],
                    "additionalProperties": False}},
]
