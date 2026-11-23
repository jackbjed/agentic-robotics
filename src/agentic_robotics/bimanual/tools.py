"""Direct control of a bimanual environment by a model: the robot's commands (api.Robot) as tools, their results as
text and images (docs/bimanual.md). Provider-neutral: Toolbox.specs / Toolbox.call, and converters to the OpenAI
Responses and Anthropic Messages formats. Ported from embodied-rl/bimanual (astra_bigrip_real.py, astra_bimanual.py;
11-17) and rewritten for this rig: its frame, reach, cameras and sensing.

    box = Toolbox(Robot(S), max_calls=80)
    box.instructions            the system text;  box.specs  [{name, description, parameters (JSON schema)}]
    items = box.call("look", {})                 [{"type": "text", "text": ...} | {"type": "image", "rgb": array, "name": ...}]
    box.ended                   None | "done: ..." | "time: ..." | "calls: ..."

Tools: look, locate, home, done, and
    grippers:  move_gripper, turn_gripper, set_gripper
    dexterous: move_hand, turn_hand, set_joints, fingertips
The model sees only what the robot measures: the three D415s' colour images, depth through locate(), the tool poses,
the grippers' openings / the hands' joints and fingertip touch, and each arm's external-force estimate.
"""
from __future__ import annotations

import base64
import json

import numpy as np

from .api import Robot, TimeUp

_HEAD = """You control a two-armed robot through tools; nothing else moves it. Each arm is a Franka Panda with {tool}.
Each tool call is one action of one arm; look at the result before the next one.

Frame and units: millimetres, in the table frame: z up, the table surface at z = 0, the table's centre at x = y = 0
(the table is 1.2 m square). The robot stands at the table's -x edge and faces +x (forward, away from itself); +y is
to the robot's left. The right arm's base is at (x, y) = (-800, -390), the left arm's at (-790, 40), both just below
table height. Near the table each {hand} reaches x from about -550 to -50 (the far half of the table is out of reach);
the right one y from about -650 to 250, the left one y from about -200 to 600; both reach the strip between y = -200
and y = 250. A move an arm cannot make is refused and nothing moves. The two arms are solid: a move planned through
the other arm is refused.
"""

_CAMERAS = """Cameras (look()), all RealSense D415 colour + depth cameras, 640x480:
1. the scene camera, fixed across the table at (860, -10, 570), facing the robot and looking down at the table: in its
   image +y points right and +x towards the camera; the robot's right arm is on the left of that image.
2. the right arm's wrist camera and 3. the left arm's wrist camera: {wrist}
locate(camera, points) turns pixels of the latest look into 3D points (mm) on the surface seen there, from that
camera's depth image. This is real stereo depth: noisy (1-2 mm at 0.4 m, 5-8 mm at 1 m), with holes (null) at object
edges, on parts of the table, in the left ~70-130 px of every image, and for everything nearer than 275 mm to the
camera. The cameras' calibration is off by a few mm and up to a degree, so points are wrong by up to about 25 mm from
the scene camera (it is 1.2 m from the work area; e.g. the table top can read 20 mm too high or too low) and by less
from a wrist camera close to something, in a direction that stays the same during the episode: heights relative to
the table as the same camera sees it are better than absolute ones, and contact (stop_on_contact, {feel}) tells
where things really are.
"""

_TAIL = """In all moves the arm's safety stops it if the net force on it exceeds about 40 N; it then holds where it stopped and
you can go on. Anything in the way of an arm is pushed or knocked over.

You have at most {max_calls} tool calls and {time_s} s of robot time (model time does not count). When the task is
done, or cannot be done, call done()."""

GRIPPERS = (_HEAD.format(tool="a parallel gripper (two fingers that open and close along one line)", hand="gripper") + """
The grippers. A gripper's pose is its TCP: the point midway between the two fingers' gripping pads, 16 mm in from the
fingertips' ends. Its orientation is given by two directions (table frame):
- approach_dir: where the fingers point (from the wrist towards the fingertips);
- closing_dir: the line along which the two fingers open and close (it is made perpendicular to approach_dir; its sign
  does not matter: the wrist takes the nearer of the two).
The fingers open to at most 80 mm between the pads and close to 0; they are 36 mm wide (across, perpendicular to both
directions), about 45 mm thick at the root, and stick out 130 mm from the gripper's body, which is about 200 mm wide
along closing_dir and 65 mm thick; the wrist and forearm behind it are about 12 cm thick. With the fingers pointing
straight down the fingertips touch the table when the TCP is at z = 16. set_gripper(arm, width_mm) sets the opening;
the fingers are a position servo that presses with up to about 70 N on whatever stops them, so width 0 grips firmly,
and the opening reported afterwards is the width of what is between them.

Force: every result reports, for each arm, the arm's estimate of the net external force on it (noisy by about 0.5 N
per axis; a held weight or a push shows there; a squeeze between the fingers cancels out in it). There are no touch
sensors in the fingers: what a gripper holds shows in its opening.

""" + _CAMERAS.format(
    wrist="""each sits on its gripper's body beside the fingers, 50 mm
   to one side and 133 mm back from the TCP, looking along approach_dir: it sees what is in front of the fingertips,
   and the two fingertips show at the bottom corners of its image. At the start (grippers pointing straight down) its
   image's up is +x and its right is -y; the image turns with the gripper. Surfaces nearer than about 140 mm beyond
   the TCP are too near for its depth.""", feel="the gripper's opening after closing") + """
Motion:
- move_gripper(arm, x, y, z, approach_dir, closing_dir, stop_on_contact) moves that gripper's TCP along a straight line
  to (x, y, z) while it turns to the new orientation. With stop_on_contact = true it moves at 4 cm/s and stops as soon
  as something pushes on that arm with more than about 4 N; the result says where it stopped. With stop_on_contact =
  false it follows the line whatever it touches (4 cm/s below z = 300, 15 cm/s above). A free move with a big change
  of orientation is made by turning the arm's joints directly (a curved path, not a line; the result says so); it is
  refused if the arm would hit something on the way.
- turn_gripper(arm, centre, axis, angle_deg) rotates the whole gripper rigidly about the line through the point
  `centre` along the direction `axis` (right-hand rule: axis (0, 0, 1) and a positive angle turn it counter-clockwise
  seen from above), at 30 degrees a second; what the fingers hold on that line is turned. The arm's last joint turns
  about approach_dir through about 330 degrees in all; a turn that would pass a joint's limit is refused whole
  (nothing moves): turn less, or open the gripper, turn back and grip again. Where in that range the wrist is after a
  move is not known in advance.
""" + _TAIL)

DEXTEROUS = (_HEAD.format(tool="a dexterous five-fingered Sharpa Wave hand (about the size of a large human hand): a RIGHT hand "
                               "on the right arm, a LEFT hand on the left arm", hand="hand") + """
The hands. Each is bolted straight onto its arm's wrist flange: the hand continues the arm's last axis, so the fingers
point along it. A hand's orientation is given by two directions (table frame):
- fingers_dir: where the straight fingers point (from the wrist towards the fingertips);
- palm_dir: the direction the palm faces (it is made perpendicular to fingers_dir).
The thumb side follows from them (cross products): for the RIGHT hand it is fingers_dir x palm_dir, for the LEFT hand
palm_dir x fingers_dir. Examples: right hand, fingers_dir (0, 0, -1), palm_dir (0, 1, 0): thumb on the +x side. Left
hand, fingers_dir (1, 0, 0), palm_dir (0, -1, 0): thumb up (+z).
The pose you command is the palm centre: a point on the palm's surface, 85 mm from the wrist flange along fingers_dir.
Sizes: the palm is 85 mm wide (thumb side to little-finger side) and 45 mm thick (the back of the hand is 45 mm behind
the palm surface). The four fingers' knuckles are 35 mm beyond the palm centre along fingers_dir, 20 mm apart across
the palm (index nearest the thumb); straight fingers are about 110 mm long and 18 mm wide, so the fingertips are about
145 mm beyond the palm centre. The thumb is mounted at the wrist end of the palm on the thumb side; with its joints at
0 it lies in the palm's plane pointing out sideways, its tip about 120 mm to the thumb side of the palm centre and
55 mm beyond it along fingers_dir. The fingertips have soft pads (the gripping surfaces) on their palm side.
The wrist and forearm behind the hand are about 12 cm thick: with the fingers horizontal, keep the palm centre at
least 100 mm above the table or the arm presses on the table. At the start both hands point straight down, open and
flat, palms facing +x, the fingertips about 300 mm above the table.

Hand joints (degrees; set_joints sets any of them for one hand, the others keep their targets; all start at 0 = open,
flat hand; the two hands are mirror images, the same names and signs apply to both):
- index_*, middle_*, ring_*, pinky_* (the four fingers, each the same):
  MCP_FE  -10..90   bends the finger at the knuckle towards the palm side (90: the finger at right angles to the palm)
  MCP_AA  -20..20   swings the finger sideways at the knuckle: + towards the little-finger side, - towards the thumb
  PIP       0..100  bends the middle joint (towards the palm side)
  DIP       0..80   bends the last joint (towards the palm side)
  pinky_CMC 0..15   cups the little-finger side of the palm slightly
- thumb_*:
  CMC_FE  -10..110  swings the whole thumb from beside the palm (0) across the front of the palm side (opposition: at
                    110 its tip is about 65 mm in front of the palm side and 110 mm nearer the little-finger side)
  CMC_AA  -20..20   + moves the thumb tip towards the index and the fingertips' end, - outwards and towards the wrist
  MCP_FE  -30..80   bends the thumb at its middle joint (towards the palm side and across it)
  MCP_AA  -20..20   tilts the thumb's outer part: + its tip away from the palm side, - towards it (about 12 mm at 20)
  IP        0..100  bends the thumb's last joint
The joints are springs (position servos): a target past where an object stops the finger presses on it; the further
past, the harder (roughly 1-2 N per mm of the fingertip's target beyond the surface). The thumb can meet the
index/middle fingertips (a pinch) about 10-85 mm in front of the palm surface and 10-85 mm beyond the palm centre
along fingers_dir; with fingers_dir straight down, that is the place under and in front of the palm.
set_joints and fingertips take an optional stop_force_n: with it, each finger stops where it is as soon as the contact
force on that finger exceeds that many newtons (the other fingers go on to their targets); the result names the
fingers that stopped. That closes a hand on something gently: without it a finger goes to its target whatever is in
the way, and a quick or strong finger knocks a standing object over. A slow duration_s (2-3 s) is gentler too.
fingertips(hand, targets) computes the joints that put chosen fingertips at given points (table frame, with the arm
where it is) and moves them there; it reports how far each target is from what the finger can reach. Fingertip points
are the centres of the fingertips' pads.

Touch: every result reports, for each hand, the force on each fingertip's tactile pad (N; nothing is felt on the palm
or on the fingers' other links) and the arm's estimate of the net external force on that arm + hand (noisy by about
0.5 N per axis; a held object's weight or a push shows there; a squeeze between fingers cancels out in it).

""" + _CAMERAS.format(
    wrist="""each sits behind the back of its hand (about 95 mm
   behind the palm surface), looking along fingers_dir and tilted 10 degrees towards the palm side: it sees what is
   beyond the fingertips and in front of the palm; straight fingers show at the top of its image, whose up is
   palm_dir. Surfaces nearer than about 150 mm beyond the palm centre are too near for its depth.""",
    feel="the fingertips' touch") + """
Motion:
- move_hand(hand, x, y, z, fingers_dir, palm_dir, stop_on_contact) moves that hand's palm centre along a straight line
  to (x, y, z) while the hand turns to the new orientation. With stop_on_contact = true it moves at 4 cm/s and stops as
  soon as something pushes on that arm or hand with more than about 4 N; the result says where it stopped. With
  stop_on_contact = false it follows the line whatever it touches (4 cm/s below z = 300, 15 cm/s above). A free move
  with a big change of orientation is made by turning the arm's joints directly (a curved path, not a line; the
  result says so); it is refused if the arm or hand would hit something on the way.
- turn_hand(hand, centre, axis, angle_deg) rotates the whole hand rigidly about the line through the point `centre`
  along the direction `axis` (right-hand rule: axis (0, 0, 1) and a positive angle turn it counter-clockwise seen from
  above), at 30 degrees a second, the palm centre travelling on an arc; fingers that hold something on that line turn
  it. An arm can usually turn a hand whose fingers point down by 60-150 degrees one way from a comfortable pose before
  a joint runs out; it then refuses the rest (nothing moves): turn less, or let go, turn back and take hold again.
  Note that changing palm_dir with move_hand turns the hand about its own palm centre, which is NOT where the
  fingertips are.
""" + _TAIL)

DEXTEROUS_HINTS = """

Grasps that are known to work with these hands (found with a scripted test; positions are yours to measure):
- Holding an upright bottle by its body (a side wrap) with the LEFT hand: the hand left of and behind the bottle, the
  fingers horizontal, the palm facing the bottle, thumb up: palm_dir (0.5, -0.87, 0), fingers_dir (0.87, 0.5, 0)
  (with the palm facing straight right the arm's wrist is at its limits). First take the thumb back out of the way
  (thumb_CMC_FE -10, thumb_CMC_AA -20, thumb_MCP_FE -30). The holding pose has the palm centre about 105 mm above the
  table, on the line through the bottle's axis along palm_dir, shifted 25 mm back along fingers_dir so that the bottle
  sits in front of the knuckles. Go to a point 160 mm back from it along palm_dir (a curved move), then move along
  palm_dir with stop_on_contact until the palm touches the bottle (the palm centre about 30 mm from its axis). Then
  bend the fingers around it: index, middle and ring to MCP_FE 55, PIP 75, DIP 45, the little finger less (30, 40,
  20), over 1-2 s. The bottle is then held between the fingers and the palm and cannot turn. Closing fingertips onto
  the bottle from above knocks it over.
- Turning a small cap from above with the RIGHT hand (a three-finger pinch): fingers_dir (0, 0, -1), palm_dir
  (0, 1, 0); the cap's centre about 45 mm in front of the palm surface (along palm_dir), 75 mm beyond the palm centre
  along fingers_dir (so the palm centre is 75 mm higher than the cap's middle) and about 12 mm to the thumb side.
  Before coming down, curl the ring and little fingers away (MCP_FE 90, PIP 100, DIP 60) and bring the thumb across
  (thumb_CMC_FE 95). Come down from at least 150 mm above. Then use fingertips() to put the thumb tip on the far side
  of the cap (cap centre + r along palm_dir) and the index and middle tips on the near side (40 degrees to either side
  of the opposite direction), at the cap's mid height: first at r = cap radius + 20 mm (open), then at r = cap radius
  - 7 mm (the pads squeeze it). Then turn_hand about the cap's axis: up to 150 degrees counter-clockwise, less (100,
  60) where the arm refuses. To take a new hold: open to r + 20 mm, lift the hand 40 mm, turn_hand back, come down,
  pinch again. When the cap is free, open, centre the hand on where the cap is now, and pinch it lightly (r = cap
  radius - 6 mm, the index and middle tips 7 mm higher than the thumb's) before lifting it slowly.
- Picking a small low object up from the table (e.g. a ring 50 mm across lying flat) with either hand: the same
  three-finger pinch with the hand tilted 40 degrees, the palm half down: palm_dir (0.77, 0, -0.64), fingers_dir
  (-0.64, 0, -0.77). (With the fingers straight down the thumb does not reach as low as the other fingertips, and the
  fingers' bent knuckles land on the table first.) The object's centre 45 mm in front of the palm surface along
  palm_dir, 85 mm beyond the palm centre along fingers_dir and 12 mm to the thumb side. From 120 mm above that pose:
  curl the ring and little fingers away (MCP_FE 80, PIP 90, DIP 50), thumb_CMC_FE 95, and open the pinch with
  fingertips(): the thumb tip on the object's +x side at r + 18 mm from its centre, the index and middle tips on the
  -x side at r + 18 mm, 20 degrees to either side, all at the height they will have around the object (its mid height
  + 4 mm) plus the 120 mm. Move straight down, close with fingertips() at r - 7 mm over 1 s, lift at 5 cm/s."""

_NO_ARGS = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
_VEC = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
_SIDE = {"type": "string", "enum": ["right", "left"]}
_FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def _spec(name, description, properties=None, required=None):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": list(properties or {}) if required is None else required,
                           "additionalProperties": False}}


_COMMON = [
    _spec("look", "The scene camera's and both wrist cameras' colour images, and both arms' state."),
    _spec("locate", "3D points (mm, table frame) of the surface seen at pixels of the LATEST look's image of that camera "
                    "(the median depth in a 7x7 pixel window; null where there is no depth). At most 20 points a call.",
          {"camera": {"type": "string", "enum": list(Robot.CAMERAS)},
           "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}, "description": "[[x, y], ...] pixels"}}),
]
_HOME = _spec("home", "Bring one arm back to its start pose by turning its joints (a curved path; the gripper's opening / the "
                      "hand's finger joints stay as they are). Refused if the arm would hit something on the way.",
              {"arm": _SIDE})
_DONE = _spec("done", "End the episode.", {"summary": {"type": "string"}})
SPECS = {
    "grippers": _COMMON + [
        _spec("move_gripper", "Move one gripper's TCP (mm, table frame) along a straight line to (x, y, z), turning to the "
                              "orientation given by approach_dir (where the fingers point) and closing_dir (the line they close "
                              "along); stop_on_contact: stop at the first push (4 cm/s) or follow the line regardless.",
              {"arm": _SIDE, "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}, "approach_dir": _VEC,
               "closing_dir": _VEC, "stop_on_contact": {"type": "boolean"}}),
        _spec("turn_gripper", "Rotate one whole gripper rigidly by angle_deg about the line through `centre` (mm) along `axis` "
                              "(right-hand rule; axis (0,0,1), positive = counter-clockwise seen from above).",
              {"arm": _SIDE, "centre": _VEC, "axis": _VEC, "angle_deg": {"type": "number"}}),
        _spec("set_gripper", "Set one gripper's opening between the finger pads, 0..80 mm (0 = close and grip).",
              {"arm": _SIDE, "width_mm": {"type": "number"}}),
        _HOME, _DONE],
    "dexterous": _COMMON + [
        _spec("move_hand", "Move one hand's palm centre (mm, table frame) along a straight line to (x, y, z), the hand turning "
                           "to the orientation given by fingers_dir and palm_dir; stop_on_contact: stop at the first push "
                           "(4 cm/s) or follow the line regardless.",
              {"hand": _SIDE, "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}, "fingers_dir": _VEC,
               "palm_dir": _VEC, "stop_on_contact": {"type": "boolean"}}),
        _spec("turn_hand", "Rotate one whole hand rigidly by angle_deg about the line through `centre` (mm) along `axis` "
                           "(right-hand rule; axis (0,0,1), positive = counter-clockwise seen from above). The fingers keep "
                           "their joint targets.",
              {"hand": _SIDE, "centre": _VEC, "axis": _VEC, "angle_deg": {"type": "number"}}),
        _spec("set_joints", "Set joint targets of one hand in degrees, e.g. {\"index_MCP_FE\": 45, \"thumb_CMC_FE\": 90}; joints "
                            "not given keep their targets. They move there over duration_s (default 0.8 s).",
              {"hand": _SIDE, "joints": {"type": "object", "additionalProperties": {"type": "number"}},
               "duration_s": {"type": "number"},
               "stop_force_n": {"type": "number", "description": "optional: each finger stops where it is once it feels more than this force (N)"}},
              required=["hand", "joints"]),
        _spec("fingertips", "Put the given fingertips of one hand at points (mm, table frame), the arm staying where it is: the "
                            "fingers' joints are computed (IK) and moved there over duration_s (default 0.8 s). Fingers not "
                            "given keep their targets. Reports the distance between each target and what that finger can reach.",
              {"hand": _SIDE, "targets": {"type": "object", "properties": {f: _VEC for f in _FINGERS}, "additionalProperties": False},
               "duration_s": {"type": "number"},
               "stop_force_n": {"type": "number", "description": "optional: each finger stops where it is once it feels more than this force (N)"}},
              required=["hand", "targets"]),
        _spec("home", _HOME["description"], {"hand": _SIDE}), _DONE],
}


def text(t):
    return {"type": "text", "text": t}


def _v(v, nd=0):
    return "(" + ", ".join(f"{float(c):.{nd}f}" for c in v) + ")"


class Toolbox:
    def __init__(self, robot: Robot, max_calls=80, hints=False):
        self.robot, self.kind = robot, robot.kind
        self.max_calls, self.n_calls, self.ended = int(max_calls), 0, None
        self.specs = SPECS[self.kind]
        t = GRIPPERS if self.kind == "grippers" else DEXTEROUS[:DEXTEROUS.index("You have at most")].rstrip() + (DEXTEROUS_HINTS if hints else "") \
            + "\n\n" + DEXTEROUS[DEXTEROUS.index("You have at most"):]
        self.instructions = t.replace("{max_calls}", str(self.max_calls)).replace(
            "{time_s}", "unlimited" if robot.limit is None else f"{robot.limit:.0f}")
        self.log = []                                               # dict(name, args, free, result: the result's text)

    # ---------------------------------------------------------------- state as text
    def arm_text(self, arm):
        s = self.robot.state(arm)
        force = f"external force estimate {_v(s['force_n'], 1)} N"
        if self.kind == "grippers":
            return (f"{arm.upper()} arm: TCP {_v(s['tcp_mm'])} mm; approach_dir {_v(s['approach_dir'], 2)}, closing_dir "
                    f"{_v(s['closing_dir'], 2)}; opening {s['opening_mm']:.0f} mm (commanded {s['commanded_mm']:.0f}); {force}.")
        joints = [f"{n} {q:.0f}" + (f" (target {s['targets_deg'][n]:.0f})" if abs(q - s['targets_deg'][n]) > 4 else "")
                  for n, q in s["joints_deg"].items() if abs(q) > 2 or abs(s["targets_deg"][n]) > 2]
        touch = ", ".join(f"{f} {v:.1f} N" for f, v in s["fingertip_force_n"].items() if v >= 0.3) or "nothing"
        return (f"{arm.upper()} hand: palm centre {_v(s['palm_mm'])} mm; fingers_dir {_v(s['fingers_dir'], 2)}, palm_dir "
                f"{_v(s['palm_dir'], 2)}, thumb side {_v(s['thumb_side'], 2)}. Fingertips (mm): "
                + "; ".join(f"{f} {_v(X)}" for f, X in s["fingertips_mm"].items())
                + ". Joints (deg; all others 0): " + (", ".join(joints) or "all 0")
                + f". Fingertip touch: {touch}; {force}.")

    def state_text(self):
        return "\n".join(self.arm_text(a) for a in ("right", "left") if a in self.robot.ARMS)

    # ---------------------------------------------------------------- the tools
    def call(self, name, args, free=False):
        """One tool call -> the result's items. Never raises for a bad call: the text says what was wrong.
        free: not counted (the first look a harness gives with the task)."""
        if self.ended:
            return [text(f"the episode has ended ({self.ended})")]
        self.n_calls += 0 if free else 1
        try:
            if args is not None and not isinstance(args, dict):
                raise ValueError("the arguments must be a JSON object")
            args = dict(args or {})
            items = self._call(name, args)
        except TimeUp as e:
            self.ended = f"time: {e}"; items = [text(f"{e}: the episode has ended")]
        except (KeyError, ValueError, TypeError, IndexError) as e:
            items = [text(f"bad call: {str(e) or type(e).__name__}")]
        if not self.ended and self.n_calls >= self.max_calls:
            self.ended = f"calls: all {self.max_calls} tool calls used"
            items.append(text("that was the last tool call: the episode has ended"))
        self.log.append(dict(name=name, args=args if isinstance(args, dict) else {}, free=bool(free),
                             result=" ".join(i["text"] for i in items if i["type"] == "text")))
        return items

    def _call(self, name, a):
        rb = self.robot
        if name not in {s["name"] for s in self.specs}:
            return [text(f"unknown tool {name}: this robot has {', '.join(s['name'] for s in self.specs)}")]
        if name == "look":
            frames = rb.look()
            items = [text(self.state_text())]
            for c, f in frames.items():
                items += [text(f"{c.replace('_', ' ')} camera ({f.width}x{f.height} px):"), {"type": "image", "rgb": f.rgb, "name": c}]
            return items
        if name == "locate":
            f = rb.frames.get(a.get("camera"))
            if a.get("camera") not in rb.CAMERAS:
                return [text(f"camera must be one of {list(rb.CAMERAS)}")]
            if f is None:
                return [text("no look yet: call look() first")]
            out = []
            for p in list(a.get("points") or [])[:20]:
                try:
                    X = f.locate(float(p[0]), float(p[1]))
                except (TypeError, ValueError, IndexError):
                    X = None
                out.append({"pixel": p, "xyz_mm": None if X is None else [int(round(float(c))) for c in X]})
            return [text(json.dumps(out))]
        if name == "done":
            self.ended = "done: " + str(a.get("summary", ""))[:600]
            return [text("episode ended")]
        arm = a.get("arm", a.get("hand"))
        if arm not in rb.ARMS:
            return [text(f'give {"arm" if self.kind == "grippers" else "hand"} = ' + " or ".join(f'"{x}"' for x in rb.ARMS))]
        if name in ("move_gripper", "move_hand"):
            d1, d2 = ("approach_dir", "closing_dir") if name == "move_gripper" else ("fingers_dir", "palm_dir")
            r = rb.move(arm, [float(a["x"]), float(a["y"]), float(a["z"])], a[d1], a[d2], bool(a.get("stop_on_contact", True)))
            return [text(self._motion(r) + "now: " + self.state_text())]
        if name == "home":
            return [text(self._motion(rb.home(arm)) + "now: " + self.state_text())]
        if name in ("turn_gripper", "turn_hand"):
            r = rb.turn(arm, a["centre"], a["axis"], float(a["angle_deg"]))
            return [text(self._motion(r) + "now: " + self.state_text())]
        if name == "set_gripper":
            rb.set_gripper(arm, float(a["width_mm"]))
            return [text("now: " + self.state_text())]
        if name == "set_joints":
            r = rb.set_joints(arm, a.get("joints") or {}, a.get("duration_s"), a.get("stop_force_n"))
            bad = [f"{k} (clipped to {v})" for k, v in r["clipped"].items()] + [f"{k} (no such joint)" for k in r["unknown"]]
            return [text((f"unknown or out-of-range: {', '.join(bad)}. " if bad else "") + self._stopped(r) + "now: " + self.state_text())]
        if name == "fingertips":
            r = rb.fingertips(arm, a.get("targets") or {}, a.get("duration_s"), a.get("stop_force_n"))
            rep = [f"{f}: target " + ("reachable" if e < 2 else f"{e:.0f} mm beyond reach (closest point taken)") for f, e in r["miss_mm"].items()]
            rep += [f"{f}: unknown finger" for f in r["unknown"]]
            return [text("; ".join(rep) + ". " + self._stopped(r) + "now: " + self.state_text())]
        raise KeyError(name)

    @staticmethod
    def _motion(r):
        if not r["moved"]:
            return f"not done: {r['why']}. "
        msg = "" if r["done"] else "stopped by a contact before the target. " if r["why"] == "contact" else f"{r['why']}; the arm holds where it stopped. "
        return msg + ("(made by turning the arm's joints: a curved path.) " if r["curved"] else "")

    @staticmethod
    def _stopped(r):
        return f"stopped on contact: {', '.join(r['stopped'])}. " if r["stopped"] else ""


# -------------------------------------------------------------------- provider formats
def png_b64(rgb):
    import cv2
    ok, b = cv2.imencode(".png", cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR))
    return base64.b64encode(b.tobytes()).decode()


def openai_tools(specs):
    """Function tools of the OpenAI Responses API (strict where every property is required)."""
    return [{"type": "function", "name": s["name"], "description": s["description"], "parameters": s["parameters"]} for s in specs]


def openai_items(items, images=None):
    """A tool result's items as Responses content (of a user message, or the `output` list of a function_call_output).
    With `images` (a list): each image is appended to it and named by its index, {"type": "input_image", "image": k},
    the form perception.vlm.VLM.respond takes; without: inline PNG data URLs."""
    out = []
    for i in items:
        if i["type"] == "text":
            out.append({"type": "input_text", "text": i["text"]})
        elif images is None:
            out.append({"type": "input_image", "image_url": "data:image/png;base64," + png_b64(i["rgb"]), "detail": "high"})
        else:
            images.append(i["rgb"]); out.append({"type": "input_image", "image": len(images) - 1, "detail": "high"})
    return out


def anthropic_tools(specs):
    return [{"name": s["name"], "description": s["description"], "input_schema": s["parameters"]} for s in specs]


def anthropic_content(items):
    """A tool result's items as the content of an Anthropic tool_result block."""
    return [{"type": "text", "text": i["text"]} if i["type"] == "text" else
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64(i["rgb"])}} for i in items]
