"""Astra with skills (2026-11-12, user: "call astra repeatedly in the episode ... and give astra skills like pick,
place, push ... where astra gives object name and location ... making astra handle the clutter (eg 'first push this out
of the way, then move that away, then pick the red tape') instead of leaving the clutter handling fully to the skill").

Astra decides; the skills act. Astra is called at the start and after every skill, each time with what the skill
reported, a fresh scene image (the arm parked out of the scene camera's way) and a wrist image over the area it last
pointed at. Its tools (each with a one-line reason, printed and logged):
    look()                              both cameras now
    pick(object, point)                 the clutter pick without its own clearing: SAM3 finds the object by its name
                                        near the point, the wrist camera plans a grasp, lifts it, checks what came up.
                                        No grasp the fingers fit into: nothing done, "blocked" and where it would hit
    place(to, to_point)                 "box": surveyed, dropped over its middle; "table": set down there (or at the
                                        nearest free spot)
    move(object, point, to, to_point)   pick + place in one call (the box surveyed first, hand empty)
    push(object, point, to_point)       slide it so that the point ends at to_point (side push, or pressing on its top
                                        and dragging when there is no room behind it); <= 8 cm
    done(summary)
Points are pixels of the latest "scene", "wrist" or "skill" (the last pick's close view) image. Clearing the way is
Astra's job (11-12 user: no auto_clear; the real run 17:10 handed it to the skill: 463 s). The skills are track_run.py's
(the clutter pick with the 11-12 fixes); tracking is off (Astra looks again after every skill). Only here (SkillProgram):
the check "something else hangs at the hand" does not count pixels of the held thing's own colour (11-12 real: the
translucent green cup's own body, 937 px, undid Astra's first move).

    robot:  terminal A: srv --allow-motion [--no-confirm]
            cd ~/tdieudonne/panda_twin; export OPENAI_API_KEY="$(cat ~/tdieudonne/embodied-agents/secrets/openai_key)"
            export CLUTTER_GRIPPER=wedge CLUTTER_TIGHT=1
            py code/real/real_box.py home
            py ~/tdieudonne/panda_twin_tracking/astra_skills_real.py --text "Put the red tape roll into the white box."
    twin:   TWIN_COMPLIANT=1 CLUTTER_GRIPPER=wedge CLUTTER_TIGHT=1 py .../astra_skills_real.py --backend twin --clutter pile --layout 9
Logs to out/real/skills_<backend>_<time>/: run.json (Astra's calls and reasons, every skill and its result, the
program's events, cost, the verdict), exchange.jsonl, astra/ (the images Astra saw, the skills' debug images), video/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PT = Path.home() / "tdieudonne" / "panda_twin" / "code"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(PT)); sys.path.insert(0, str(PT / "real")); sys.path.insert(0, str(HERE))

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import track_run as TR  # noqa: E402  (imports real_box: its import reads --clutter from sys.argv)

RB, F, T, CB = TR.RB, TR.F, TR.T, TR.CB
B = CB.B
TR.TRACK = False                                       # (Astra looks again after every skill)

MODEL, EFFORT = "gpt-6-astra", "high"                  # (11-12: "medium" spent 0-25 reasoning tokens a decision)
PRICE = {"in": 12.5, "cached": 1.0, "out": 50.0}       # $ / M tokens (as astra_e2e.py: an upper bound)
PARK_Z = 0.30                                          # m: the hand goes up to this before the joint move home
FOCUS0 = (-0.25, -0.05)                                # m: the wrist's first look (where the real piles stood, 11-12)
HUE_TOL, SAT_MIN = 12, 50                              # the held thing's own colour (OpenCV hue 0-180)
OWN_FRAC = 0.6                                         # a blob this much of its colour is the held thing itself
PRE_R = 0.04                                           # m: an object this near the middle of the wrist view taken
                                                       # before the skill (the one Astra saw): no look of its own
LOOK_TURN_MAX = math.radians(95)                       # a look "from another side": the hand turned at most this
TURN_SLACK = 0.5                                       # of the clear grasps scoring within this of the best: the one
                                                       # that turns the hand least
PRESS_TALLER = 0.025                                   # m: a drag's press held up by at most this over the top seen:
                                                       # on the thing's own top (dark things read low in the depth)
WALL_PINCH = os.environ.get("SKILLS_WALL_PINCH", "0") == "1"   # (11-13, off: SkillProgram.wall_grasp; the 26 mm finger
                                                       # in a 32 mm hole needs the ring within ~2.5 mm, the twin's
                                                       # fit was 7 mm off: both descents landed on the rim, pile 17)
WALL_TEXT = (" A roll lying flat with its hole open (a tape roll) can also be gripped by its wall with one finger in the "
             "hole, which needs room on one side only.") if WALL_PINCH else ""

INSTRUCTIONS = """You direct a robot arm (a Franka Panda with a two-finger gripper) through skills. Each skill runs on its
own: it looks with the camera on the hand, finds the object by its name near the point you give (a segmentation
model), plans and checks its motions, then reports what happened, with new images. After every skill you decide what
to do next. The scene changes (things move when others are pushed or picked, a grasp can fail), so read each result
and look at each new image before deciding. Give every call a short reason: what you see and why this step.

Points are pixels [x, y] (640 x 480) of one of the latest images, named by `camera`: "scene" = the scene camera across
the table, facing the robot (the whole table; a pile looks small); "wrist" = the hand's camera looking down at the area
you last pointed at (at the start: the middle of the work area), sharper; "skill" = the close view of the last pick
(the object outlined green, the finger positions it tried: orange = blocked), the sharpest view of a pile. In move,
point and to_point can come from different images (camera, to_camera). For an object, point at its visible middle.
Name objects with a few words a segmentation model can find ("red tape roll", "coiled black cable", "purple eggplant",
"black block with holes", "green plastic cup").

Skills:
- move(object, point, to, to_point): pick the object up and put it into the box (to = "box", to_point = the middle of
  the box's opening), or move it aside on the table (to = "table"): lifted a few cm, carried low and set down within
  15 cm of where it was, at the free spot nearest to to_point. Give to_point 5 to 15 cm from the object, in the
  direction you want it to go, on free table; a few cm out of the way is enough. The lower edge of the scene image
  (near the scene camera) is out of the arm's reach.
- pick(object, point) and place(to, to_point): the same in two calls (pick, look at the result, decide).
- push(object, point, to_point): slide the object on the table so that the point you gave on it ends at to_point
  (2 to 8 cm). The fingers push it from its side, or press on its top and drag it when there is no room behind it.
- look(): both camera images now (only needed if you want another view; every skill already returns them).
Between skills the arm hovers above the area you last pointed at; the scene camera still sees the table under it.
- done(summary): the task is finished, or it cannot be done.

Clearing the way is your job. The gripper opens to 80 mm; each finger is 26 mm wide and needs about 1 cm of free table
beside the object on two opposite sides.{wall} When things touch the object on the sides the fingers need, a pick does
nothing and reports "blocked", with where the fingers would hit things (pixels of the "skill" image). Then clear the
way yourself, and try again. Try the object itself first (move it to the box): when it is blocked, the report says
where the fingers would hit; move aside or push only those things, a few cm is enough. A grasp whose fingers come
down on something tries a second direction by itself before reporting. A thing that cannot be picked (flat, wider
than 80 mm) can be pushed. The arm on the left of the scene image is
parked: put nothing near it. Keep everything on the table. You have at most {max_calls} skill calls."""

PT_ = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "[x, y] pixel of the latest image of `camera`"}
CAM_ = {"type": "string", "enum": ["scene", "wrist", "skill"], "description": "which latest image the points are pixels of"}
WHY_ = {"type": "string", "description": "one sentence: what you see and why this step"}
TOOLS = [
    {"type": "function", "name": "look", "description": "Both cameras' images now.",
     "parameters": {"type": "object", "properties": {"reason": WHY_}, "required": ["reason"], "additionalProperties": False}},
    {"type": "function", "name": "pick", "description": "Pick the object up (it stays in the hand).",
     "parameters": {"type": "object", "properties": {"object": {"type": "string"}, "point": PT_, "camera": CAM_, "reason": WHY_},
                    "required": ["object", "point", "camera", "reason"], "additionalProperties": False}},
    {"type": "function", "name": "place", "description": "Put the held object into the box or down on the table.",
     "parameters": {"type": "object", "properties": {"to": {"type": "string", "enum": ["box", "table"]}, "to_point": PT_, "camera": CAM_, "reason": WHY_},
                    "required": ["to", "to_point", "camera", "reason"], "additionalProperties": False}},
    {"type": "function", "name": "move", "description": "Pick the object and put it into the box or down on the table.",
     "parameters": {"type": "object", "properties": {"object": {"type": "string"}, "point": PT_, "to": {"type": "string", "enum": ["box", "table"]},
                                                     "to_point": PT_, "camera": CAM_, "to_camera": CAM_, "reason": WHY_},
                    "required": ["object", "point", "to", "to_point", "camera", "to_camera", "reason"], "additionalProperties": False}},
    {"type": "function", "name": "push", "description": "Slide the object on the table so that its point ends at to_point (2-8 cm).",
     "parameters": {"type": "object", "properties": {"object": {"type": "string"}, "point": PT_, "to_point": PT_, "camera": CAM_, "reason": WHY_},
                    "required": ["object", "point", "to_point", "camera", "reason"], "additionalProperties": False}},
    {"type": "function", "name": "done", "description": "End the episode.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}},
]


def png_item(rgb):
    import base64
    b = cv2.imencode(".png", cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR))[1].tobytes()
    return {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(b).decode(), "detail": "high"}


def jpg_item(path):
    import base64
    return {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode(), "detail": "high"}


def text_item(t):
    return {"type": "input_text", "text": t}


def colour_of(rgb, mask):
    """The median colour of the mask's pixels: (hue 0-180 (circular), saturation 0-255), or None if colourless (a
    black, white or grey thing: its hue means nothing)."""
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)[mask]
    if len(hsv) < 50 or np.median(hsv[:, 1]) < 60:
        return None
    a = hsv[:, 0].astype(float) * (2 * math.pi / 180)
    h = (math.degrees(math.atan2(np.sin(a).mean(), np.cos(a).mean())) / 2) % 180
    return float(h), float(np.median(hsv[:, 1]))


class SkillProgram(TR.TrackProgram):
    """track_run's TrackProgram for this file only: "something else hangs at the hand" leaves out the pixels of the held
    thing's own colour (pick_direct sets `target_colour` from its look). 11-12 real, the translucent green cup: its
    body hanging under the fingers, wider than its top (the target's size comes from the top seen from above), read
    as 937 px "beyond the target", hue 71-77 like the cup's 70-89 in the wrist look; the move was undone."""

    target_colour = None

    def hanging(self, r_t, h_obj, o_pre=None):
        o = self.per.scene_depth(); P, ok = F.world(o)
        tcp = self.S.tcp_now()
        dxy = np.linalg.norm(P[..., :2] - tcp[:2], axis=-1)
        air = ok & ~CB.dilate(o["arm"], max(3, self.ARM_GROW)) & (P[..., 2] > 0.13) & (dxy < 0.15)
        if o_pre is not None:
            air &= ~(np.isfinite(o_pre["depth"]) & (np.abs(o["depth"] - o_pre["depth"]) < 0.01))
        side = air & (dxy > r_t + 0.020)
        below = air & (P[..., 2] < tcp[2] - T.FINGERTIP - max(h_obj, 0.03) - 0.025)
        k_ = max(3, self.RISE_OPEN)
        extra = cv2.morphologyEx((side | below).astype(np.uint8), cv2.MORPH_OPEN, np.ones((k_, k_), np.uint8)) > 0
        own = np.zeros_like(extra)
        if self.target_colour is not None and extra.any():
            hsv = cv2.cvtColor(np.ascontiguousarray(o["rgb"]), cv2.COLOR_RGB2HSV)
            dh = np.abs(hsv[..., 0].astype(int) - int(round(self.target_colour[0])))
            dh = np.minimum(dh, 180 - dh)
            own = extra & (dh <= HUE_TOL) & (hsv[..., 1] >= SAT_MIN)
            n_c, lab = cv2.connectedComponents(extra.astype(np.uint8))   # (a blob mostly of its colour is the thing,
            for k in range(1, n_c):                                     # its edge too, where the depth smears into
                comp = lab == k                                         # the background's colours: the cup, 1480 px,
                if (comp & own).sum() >= OWN_FRAC * comp.sum():         # 83 % its green, the rest its rim)
                    own |= comp
            extra = extra & ~own
        self.event("hanging at the hand", air_px=int(air.sum()), beyond_target_px=int(extra.sum()), side_px=int(side.sum()),
                   below_px=int(below.sum()), own_colour_px=int(own.sum()),
                   colour=None if self.target_colour is None else [round(c) for c in self.target_colour])
        return int(extra.sum())

    HOP_R = 0.40            # m: a transit this short goes straight, over what stands under its path

    def transit(self, xy, R=None):
        """ClutterProgram.transit, but a short one goes straight from where the hand is: up only as far as the tallest
        thing within 6 cm of the path (the scene camera, the robot left out) needs, 6 cm over it (+ what the hand
        holds), then a line planned from the present joints. (11-12 twin profile: transits go up to the safe height and
        solve their joints from the home pose: a 5 cm shift between two looks took 13.6 s, 8-14 s each, ~4 a skill.)
        The line not possible: the original."""
        S = self.S
        R = S.R_cmd if R is None else R
        xy = np.asarray(xy[:2], float)
        d = float(np.linalg.norm(S.p_cmd[:2] - xy))
        if d <= self.HOP_R:
            rot = float(np.linalg.norm(T.rotvec(S.R_cmd.T @ R)))
            if d < 0.01 and rot < math.radians(2):
                return
            try:
                hang = float(self.held.get("h", 0.03)) if self.held is not None else 0.0
                z_hop = max(S.p_cmd[2], self.top_under(S.p_cmd[:2], xy) + T.FINGERTIP + hang + 0.06, 0.12)
                if z_hop > S.p_cmd[2] + 0.005:
                    S.move(np.r_[S.p_cmd[:2], z_hop], S.R_cmd, S.g_cmd, max(0.4, (z_hop - S.p_cmd[2]) / 0.15), "up (over what is on the way)")
                S.move(np.r_[xy, z_hop], R, S.g_cmd, max(0.6, d / 0.15, rot * 0.8), "hop (straight)")
                return
            except T.Unreachable:
                S.resync()
        return super().transit(xy, R)

    def top_under(self, a, b, w=0.06):
        """The tallest thing (m over the table, 98th percentile) within w of the segment a-b, the robot left out."""
        o = self.per.scene_depth()
        P, ok = F.world(o)
        hz, _ = CB.table_heights(o)
        a, b = np.asarray(a[:2], float), np.asarray(b[:2], float)
        L = float(np.linalg.norm(b - a)); u = (b - a) / L if L > 1e-6 else np.array([1.0, 0.0])
        rel = P[..., :2] - a
        along, across = rel @ u, np.abs(rel @ np.array([-u[1], u[0]]))
        way = ok & ~CB.dilate(o["arm"], 6) & (hz > 0.005) & (hz < 0.40) & (along > -w) & (along < L + w) & (across < w)
        return float(np.percentile(hz[way], 98)) if way.sum() > 20 else 0.0

    ASIDE_R = 0.15          # m: a thing moved aside goes at most this far from where it was (11-12 user: "just a few cm")
    ASIDE_CLEAR = 0.05      # m: with nothing within this of the spot (ClutterProgram.free_spot: 7 cm)
    ASIDE_RISE = 0.03       # m: lifted this much (+ over what is on the way), carried low (take_away_near: 4 cm)

    def aside_spot(self, c, prefer=None, r_obj=0.03):
        """A free table spot within ASIDE_R of c (nothing above 1 cm within ASIDE_CLEAR in the scene camera's depth,
        the robot left out; reachable), the one nearest to Astra's point `prefer` (else to c). Nothing reserved: the
        scene camera sees what was set down. (11-12 real 17:47: Astra's spots were out of reach, at the table's near
        edge; free_spot's nearest to them were 30-40 cm from the pile.) -> xy or None"""
        S = self.S
        o = self.per.scene_depth()
        P, ok = F.world(o)
        hz, _ = CB.table_heights(o)
        occ = P[ok & ~CB.dilate(o["arm"], 4) & (hz > 0.01) & (hz < 0.30)][:, :2][::3]
        base = S.base[:2]
        c = np.asarray(c[:2], float)
        cands = []
        for dx in np.arange(-self.ASIDE_R, self.ASIDE_R + 1e-9, 0.01):
            for dy in np.arange(-self.ASIDE_R, self.ASIDE_R + 1e-9, 0.01):
                d_c = math.hypot(dx, dy)
                p = c + [dx, dy]
                if d_c > self.ASIDE_R or d_c < r_obj + self.ASIDE_CLEAR or not 0.38 < np.linalg.norm(p - base) < 0.60:
                    continue                                           # (out of its own old place: the held thing is
                                                                       # under the robot's mask, not "occupied": 11-12
                                                                       # twin, a cable set down 2 cm from where it was)
                if len(occ) and np.min(np.linalg.norm(occ - p, axis=1)) < self.ASIDE_CLEAR:
                    continue
                cands.append((float(np.linalg.norm(p - prefer)) if prefer is not None else d_c, p))
        cands.sort(key=lambda t: t[0])
        for _, p in cands[:40]:
            y1 = math.atan2(*(p - base)[::-1]) + math.pi / 2
            if S.ik(np.r_[p, 0.10], B.down(y1), S.q_home)[1] < 0.002:
                return p
        return None

    APPROACH_DZ = 0.07      # m: the hand stops this far over the thing's top, the arm catches up, then the descent

    def approach(self, g, geo):
        """Over the grasp, APPROACH_DZ over the thing's top (lower if the arm does not reach there: >= 6 cm), opened to
        fit, then the arm left to catch up: the descent after it is ~8 cm (~35 mm/s), not the 17-23 cm from the look's height.
        (11-13 real 15:56: from the look, 22 cm down at up to 129 mm/s, the arm lagging its goals by different times per
        joint drifted 41 mm sideways (78 mm behind in height) and came down on the green cup beside the red block, 21 N;
        the server's hold watchdog then refused all motion. All 6 real descents that day drifted 13-41 mm.)"""
        S = self.S
        R = B.down(g["a"]); c = g["c"]; q = g["q"]
        z = geo["top"] + self.APPROACH_DZ
        while z > geo["top"] + 0.06 and S.ik(np.r_[c, z], R, S.q_cmd)[1] > 0.002:
            z -= 0.02
        S.move(np.r_[c, max(S.p_cmd[2], z)], R, q, 1.0, "over the grasp (opened to fit)")
        if S.p_cmd[2] > z + 0.005:
            S.move(np.r_[c, z], R, q, 0.8, "down over it")
        S.flush(); S.hold(0.3); S.flush()

    def aside_move(self, g, Q, geo, support, prefer, name):
        """Move it aside, fast: ClutterProgram.take_away_near's motion (descend, close, lift ASIDE_RISE, carry low over
        what the scene camera sees on the way, set down) to aside_spot (Astra's direction); a squeezed soft thing
        (fingers closed flat) checked between the fingers (still_holding). No spot within ASIDE_R: the nearest free
        spot anywhere, carried over the top. -> (result, (spot, "near"|"far") or None)"""
        S = self.S
        R = B.down(g["a"]); c = g["c"]; z_g = g["z"]; q = g["q"]
        self.approach(g, geo)
        S.move(np.r_[c, z_g], R, q, 1.2, self.DESCEND)
        S.hold(0.15)
        if self.descent_blocked(z_g, R, q):
            self.event("move aside failed", result="blocked on the way down")
            return "blocked on the way down", None
        S.move(np.r_[c, z_g], R, 0.0, 0.6, "close")
        for _ in range(15):
            w0 = S.finger_width(); S.hold(1 / 15)
            if abs(S.finger_width() - w0) < 0.0003:
                break
        w = S.finger_width()
        self.event("grasp (to move it aside)", width_mm=round(w * 1000, 1), expected_mm=round(g["w"] * 1000, 1),
                   a_deg=round(math.degrees(g["close_a"])))
        if w > g["w"] + 0.006:
            self.let_go(c, z_g, R, q)
            self.event("move aside failed", result="something else in the grip")
            return "something else in the grip", None
        h_obj = max(geo["top"] - support, 0.01)
        rise = self.ASIDE_RISE
        S.move(np.r_[c, z_g + rise], R, 0.0, 0.6, "lift a little")
        soft = w < 0.0025
        self.held = dict(k=None, R=R, h=h_obj, off=np.zeros(3), bottom_off=geo["top"] + rise - h_obj - (z_g + rise),
                         width=max(w, min(max(0.03, g["w"]), 0.068)) if soft else w, name=name)
        if soft and not self.still_holding():
            self.held = None
            S.move(S.p_cmd, S.R_cmd, B.OPEN_W, 0.4, "open (nothing held)")
            self.event("move aside failed", result="missed (closed on nothing)")
            return "missed (closed on nothing)", None
        r_obj = float(np.percentile(np.linalg.norm(np.asarray(Q)[:, :2] - c, axis=1), 95)) if len(Q) else 0.03
        spot = self.aside_spot(c, prefer, r_obj)
        if spot is None:
            try:
                far = self.free_spot(c, clear=self.ASIDE_CLEAR)
                self.reserved = self.reserved[:-1]
            except F.Unseen:
                return "no free spot on the table", None
            self.event("no free spot within %d cm: the nearest free one" % round(self.ASIDE_R * 100), at_mm=np.round(far * 1000).tolist())
            S.move(np.r_[c, max(S.p_cmd[2], 0.20)], R, 0.0, 1.0, "lift (no room near)")
            n0 = len(self.events)
            self.put_down(np.r_[far, 0.0], "put aside (no room near)")
            return self.set_down_result(n0), (far, "far", np.asarray(c, float))
        obs = self.per.observe("d415")                               # (the way there, low: over what the scene camera
        P, ok = F.world(obs)                                           # sees, as take_away_near)
        hz, _ = CB.table_heights(obs)
        u = spot - c; L_ = max(float(np.linalg.norm(u)), 1e-6); u = u / L_
        rel = P[..., :2] - c
        along, across = rel @ u, np.abs(rel @ np.array([-u[1], u[0]]))
        way = ok & (along > 0.04) & (along < L_ + 0.04) & (across < 0.05) & (hz > 0.005) & (hz < 0.25)
        z_way = float(np.percentile(hz[way], 98)) if way.sum() > 20 else 0.0
        z_tcp = max(S.p_cmd[2], z_way + 0.02 - self.held["bottom_off"])
        if z_tcp > S.p_cmd[2] + 0.002:
            S.move(np.r_[c, z_tcp], R, 0.0, 0.6, "up over the way")
        S.move(np.r_[spot, z_tcp], R, 0.0, 0.8 + 6 * L_, "carry it aside (low)")
        self.event("moved aside", to_mm=np.round(spot * 1000).tolist(), cm=round(L_ * 100, 1), way_top_mm=round(z_way * 1000))
        n0 = len(self.events)
        self.put_down(np.r_[spot, 0.0], "over the spot", transit=False)
        return self.set_down_result(n0), (spot, "near", np.asarray(c, float))

    def set_down_result(self, n0):
        if any(e["what"].startswith("it fell out of the hand") for e in self.events[n0:]):
            return "it fell out of the hand on the way"
        return "ok" if self.held is None else "not set down"

    # ---------------------------------------------------------------- 11-13: fewer looks, less wrist turning
    # (11-13 real run, 787 s: 55 % of it the wrist camera moving to look (the centred looks moved the arm 0.00-0.02 rad
    # for 3-5 s each; the green cup's looks "from another side" turned the wrist 127 deg: 30 s, then 37 s to centre);
    # the grasps that turned the wrist 79-96 deg took 16-18 s to come down, 8 s with 18 deg (joint speed limit 0.3 rad/s))
    pre_view = None         # (SkillEpisode.snap: the wrist view Astra just saw; used while the arm has not moved since)

    def view_unmoved(self):
        pv = self.pre_view
        return pv is not None and float(np.abs(np.asarray(self.S.q_cmd) - pv["q"]).max()) < 1e-4

    def disc_in_image(self, obs, X, r, margin=6):
        W, Hh = int(obs["K"][0]), int(obs["K"][1])
        for a in np.linspace(0, 2 * math.pi, 16, endpoint=False):
            uv = F.project(obs, np.r_[X[:2] + r * np.array([math.cos(a), math.sin(a)]), X[2]])
            if not (margin <= uv[0] < W - margin and margin <= uv[1] < Hh - margin):
                return False
        return True

    def look_at(self, X, r, what):
        """ClutterProgram.look_at, with fewer moves: the view taken just before the skill when X is within PRE_R of its
        middle, the disc around X whole in it and not hidden (no move); "from another side" only by a turn of the hand
        <= LOOK_TURN_MAX, once."""
        S = self.S
        X = np.asarray(X, float)
        if self.view_unmoved():
            o = self.pre_view["obs"]
            if np.linalg.norm(X[:2] - o["look"][:2]) <= PRE_R and self.disc_in_image(o, X, r) and self.hidden(o, X, r) < 0.25:
                self.event("look: the view before the skill (no move)", during=what, off_mm=round(float(np.linalg.norm(X[:2] - o["look"][:2])) * 1000))
                return o
        hl = self.look_height(r)
        yaws = list(self.look_yaws(X[:2]))
        y = self.best_yaw(np.r_[X[:2], X[2] + hl], yaws)
        extra = self.tall_extra(X, r, hl) if CB.TALL_LOOK else 0.0
        while extra > 0.005:
            R = B.down(y); tcp = self.look_tcp(X, R, X[2] + hl + extra)
            if S.ik(tcp, R, S.q_cmd)[1] < 0.002 or S.ik(tcp, R, S.q_home)[1] < 0.002:
                break
            extra -= 0.02
        if extra > 0.005:
            hl += extra
            self.event("look higher: taller things around it", extra_mm=round(extra * 1000))
        best, tried = None, []
        for k in range(2):
            R = B.down(y)
            tcp = self.look_tcp(X, R, X[2] + hl)
            if k == 0 or S.ik(tcp, R, S.q_cmd)[1] < 0.002 or S.ik(tcp, R, S.q_home)[1] < 0.002:
                self.transit(tcp[:2], R)
                S.move(tcp, R, B.OPEN_W, 1.0, what if k == 0 else what + " (from another side)")
                S.hold(0.2)
                obs = self.unspread_obs(self.per.wrist_rgbd())
                occ = self.hidden(obs, X, r)
                if best is None or occ < best[0]:
                    best = (occ, obs)
                if occ < 0.25 or k == 1:
                    break
            tried.append(y)
            sides = [yy for yy in yaws if self.turn_of(B.down(yy)) <= LOOK_TURN_MAX
                     and min(abs((yy - t + math.pi) % (2 * math.pi) - math.pi) for t in tried) > math.radians(30)]
            if not sides:
                self.event("look: hidden by something tall; no other side within a small turn", hidden=round(best[0], 2))
                break
            self.event("look: hidden by something tall, from another side", hidden=round(best[0], 2))
            y = max(sides, key=lambda yy: min(abs((yy - t + math.pi) % (2 * math.pi) - math.pi) for t in tried))
        obs = best[1]
        obs["look"] = X.copy()
        return obs

    def whole_in_view(self, obs, mask, X):
        """The object found in this look needs no look centred on it: its outline >= 12 px from the image's border,
        its middle within PRE_R of the look's, the disc around it not hidden."""
        Hh, W = mask.shape
        ys, xs = np.nonzero(mask)
        edge = not len(xs) or xs.min() < 12 or ys.min() < 12 or xs.max() >= W - 12 or ys.max() >= Hh - 12
        off = float(np.linalg.norm(X[:2] - np.asarray(obs["look"], float)[:2]))
        hid = self.hidden(obs, X, 0.05)
        ok = not edge and off <= PRE_R and hid < 0.25
        if not ok:
            self.event("look: a centred look needed", at_edge=bool(edge), off_mm=round(off * 1000), hidden=round(hid, 2))
        return ok

    def locate(self, i, phrase, digs=3):
        """ClutterProgram.locate without its second look ("centred") when the first already has the object whole, near
        its middle and not hidden (whole_in_view)."""
        sd = self.per.seeds[i]; s = sd["p"].copy(); ray = sd.get("ray")
        if self.est is not None:
            s, ray = self.est.copy(), None
        obs = self.look_at(np.r_[s[:2], max(s[2], 0.0)], 0.05, "look at " + phrase)
        f = self.find(obs, phrase, s, ray)
        self.dbg(obs, "look", getattr(self, "_last_masks", ()), None if f is None else f[0])
        zone = (s[:2], 0.16)
        for dig in range(digs + 1):
            if f is not None:
                break
            obs = self.look_at(np.r_[s[:2], max(s[2] - 0.02, 0.0)], 0.13, "search for " + phrase)
            f = self.find(obs, phrase, s, ray, search=True, zone=zone)
            self.dbg(obs, "search", getattr(self, "_last_masks", ()), None if f is None else f[0])
            self.event("search", found=f is not None, digs=dig, **({} if f is None else f[2]))
            if f is None:
                if dig == digs or not self.dig(obs, zone):
                    raise F.Unseen(f"{phrase} not found")
        mask, Q, info = f
        X = np.r_[np.median(Q[:, :2], 0), float(np.percentile(Q[:, 2], 97))]
        if self.whole_in_view(obs, mask, X):
            self.event("look: whole in this view, no centred look", off_mm=round(float(np.linalg.norm(X[:2] - obs["look"][:2])) * 1000))
        else:
            obs = self.look_at(X, 0.05, "look at " + phrase + " (centred)")
            f2 = self.find(obs, phrase, X, None)
            if f2 is not None:
                mask, Q, info = f2
        self.est = np.r_[np.median(Q[:, :2], 0), float(np.percentile(Q[:, 2], 97))]
        self.event("found " + phrase, **info)
        return obs, mask, Q, info

    def best_grasp(self, obs, mask, Q, avoid=None, push=False):
        """ClutterProgram.best_grasp, but of the clear grasps scoring within TURN_SLACK of the best, the one that turns
        the hand least from where it is first."""
        S = self.S
        P, ok = F.world(obs)
        Pn = P[ok]; is_t = CB.dilate(mask, 4)[ok]
        support = self.support_under(obs, mask, Q)
        if (Q[:, 2] > support + 0.004).sum() >= 40:
            Q = Q[Q[:, 2] > support + 0.004]
        for mg in CB.MARGINS + ([CB.PUSH] if push or os.environ.get("CLUTTER_PUSH", "0") == "1" else []):
            cands, geo = CB.plan_grasps(Q, Pn, is_t, support, mg, avoid=avoid)
            oks = [c for c in cands if c["ok"]]
            if not oks:
                continue
            near = sorted([c for c in oks if c["score"] >= oks[0]["score"] - TURN_SLACK], key=lambda c: self.turn_of(B.down(c["a"])))
            for c in (near + [c for c in oks if c["score"] < oks[0]["score"] - TURN_SLACK])[:12]:
                R = B.down(c["a"])
                if not self.grasp_allowed(c):
                    continue
                if S.ik(np.r_[c["c"], c["z"]], R, S.q_cmd)[1] < 0.002 and S.ik(np.r_[c["c"], c["z"] + 0.15], R, S.q_cmd)[1] < 0.002:
                    self.event("grasp chosen", turn_deg=round(math.degrees(self.turn_of(R))), score=round(c["score"], 2),
                               best_score=round(oks[0]["score"], 2), best_turn_deg=round(math.degrees(self.turn_of(B.down(oks[0]["a"])))))
                    return c, cands, geo, support, Pn, is_t, Q
        return None, cands, geo, support, Pn, is_t, Q

    # ---------------------------------------------------------------- 11-13: a roll gripped by its wall
    WALL_MARGIN = 0.004     # m: each pad this far off the wall as the wall pinch comes down
    HOLE_OPEN = 0.5         # of the hole's pixels with depth, at least this share >= 10 mm under the top

    def ring_for_pinch(self, obs, mask, Q, geo):
        """ClutterProgram.ring_of with a looser hole test: HOLE_OPEN, not 0.8 (that is for the closed fingers going down
        into the hole; here clear_height checks the one finger on the points). (11-13 twin, the red tape of pile 11:
        0.69 open, the D415 spreading the hole's edge inward.) -> dict(c, r_in, r_out, top, floor) or None"""
        self._ring_why = "tilted / few points"
        if geo.get("tilt_deg", 0) > 15 or len(Q) < 60:
            return None
        xy = Q[:, :2]
        sol = np.linalg.lstsq(np.c_[2 * xy, np.ones(len(xy))], (xy ** 2).sum(1), rcond=None)[0]
        c = sol[:2]
        r = np.linalg.norm(xy - c, axis=1)
        r_in, r_out = float(np.percentile(r, 3)), float(np.percentile(r, 97))
        self._ring_why = f"not a ring: r_in {r_in * 1000:.0f} r_out {r_out * 1000:.0f} mm"
        if not (r_in >= 0.010 and 0.018 <= r_out <= 0.035 and r_out - r_in <= 0.016):
            return None
        top = float(np.percentile(Q[:, 2], 90))
        P, ok = F.world(obs)
        hole = ok & (np.linalg.norm(P[..., :2] - c, axis=-1) < r_in - 0.002)
        self._ring_why = f"no depth in the hole ({int(hole.sum())} px)"
        if hole.sum() < 30:
            return None
        hz = P[hole][:, 2]
        self._ring_why = f"hole covered: {(hz < top - 0.010).mean():.2f} open"
        if (hz < top - 0.010).mean() < self.HOLE_OPEN:
            return None
        self._ring_why = None
        return dict(c=c, r_in=r_in, r_out=r_out, top=top, floor=float(np.percentile(hz, 25)))

    def wall_grasp(self, obs, mask, Q, support, geo, Pn, avoid=None):
        """(11-13) A roll gripped by its wall: one finger down into its hole, the other outside it, closed on the wall
        between them: room beside the roll for one finger only, on the side it is gripped. (11-13 real: the red tape,
        boxed in by five things, was picked only after four were moved; 11-06 twin: Astra end to end solved 3 of 4 piles
        the skills had failed by pinching a tape's wall.) The ring: ClutterProgram.ring_of (a circle fit to its points,
        the hole open in the depth). The inside pad's corners meet the inner wall where its width fits (a chord); both
        pads WALL_MARGIN off the wall; the fingertips 10 mm under its top; every point counts (the pads must clear the
        roll's own wall too). -> a grasp (plan_grasps' keys, wall=True) the arm reaches, or None"""
        S = self.S
        ring = self.ring_for_pinch(obs, mask, Q, geo)
        if ring is None:
            self.event("wall pinch: not a roll with an open hole", why=getattr(self, "_ring_why", None))
            return None
        hw = CB.PAD_HW + 0.001
        if ring["r_in"] <= hw or math.sqrt(ring["r_in"] ** 2 - hw ** 2) - self.WALL_MARGIN < 0.002:
            self.event("wall pinch: its hole is too small for a finger", hole_mm=round(2000 * ring["r_in"]))
            return None
        x_in = math.sqrt(ring["r_in"] ** 2 - hw ** 2) - self.WALL_MARGIN
        x_out = ring["r_out"] + self.WALL_MARGIN
        q = (x_out - x_in) / 2
        top = ring["top"]
        tip = max(top - min(0.010, 0.6 * (top - support)), ring["floor"] + 0.003, support + 0.003)
        z_want = tip + T.FINGERTIP
        w_exp = ring["r_out"] - math.sqrt(max(ring["r_in"] ** 2 - CB.PAD_HW ** 2, 0.0))
        Pk = Pn[np.linalg.norm(Pn[:, :2] - ring["c"], axis=1) < 0.17]
        allp = np.ones(len(Pk), bool)
        cands, lims, over = [], {}, []
        for a in np.radians(np.arange(0.0, 360.0, 15.0)):
            if avoid is not None and abs((a - avoid + math.pi / 2) % math.pi - math.pi / 2) < math.radians(10):
                continue
            c = ring["c"] + np.array([math.cos(a), math.sin(a)]) * (x_in + x_out) / 2
            for cs in (1, -1):
                zf = CB.clear_height(Pk, allp, c, a, q, cs, 0.001)
                over.append(zf - z_want)
                if top - (zf - T.FINGERTIP) < 0.006:                   # (as plan_grasps: >= 6 mm of it between the pads)
                    lims[CB.clear_height.lim] = lims.get(CB.clear_height.lim, 0) + 1
                    continue
                z_g = max(z_want, zf)
                cands.append(dict(c=c, a=float(a if cs > 0 else a + math.pi), close_a=float(a), q=float(q), w=float(w_exp), z=float(z_g),
                                  z_free=float(zf), z_want=float(z_want), depth=float(top - (z_g - T.FINGERTIP)), want=0.010, s=0.0, top=top, ltop=top,
                                  lh=max(top - support, 0.006), between=0, fm=0.001, level=0.0, lim=None, ok=True, score=0.0, wall=True))
        cands.sort(key=lambda c: self.turn_of(B.down(c["a"])))
        for c in cands[:12]:
            R = B.down(c["a"])
            if not self.grasp_allowed(c):
                continue
            if S.ik(np.r_[c["c"], c["z"]], R, S.q_cmd)[1] < 0.002 and S.ik(np.r_[c["c"], c["z"] + 0.15], R, S.q_cmd)[1] < 0.002:
                self.event("wall pinch: one finger in its hole", hole_mm=round(2000 * ring["r_in"]), outer_mm=round(2000 * ring["r_out"]),
                           ring_mm=np.round(np.asarray(ring["c"]) * 1000, 1).tolist(), tcp_mm=np.round(np.asarray(c["c"]) * 1000, 1).tolist(),
                           a_deg=round(math.degrees(c["close_a"])), opened_mm=round(2000 * q), clear=len(cands))
                return c
        self.event("wall pinch: no clear way down for it", clear=len(cands), blocked_by=lims,
                   least_over_mm=round(min(over) * 1000, 1) if over else None, hole_mm=round(2000 * ring["r_in"]),
                   outer_mm=round(2000 * ring["r_out"]), top_mm=round(top * 1000), floor_mm=round(ring["floor"] * 1000))
        return None

    # ---------------------------------------------------------------- 11-13: a drag's press on a dark thing
    def press_blocked(self, cmd, R, top):
        """RealClutterProgram.press_blocked, but held up by <= PRESS_TALLER over the top seen: on the thing's own top,
        taller than its depth said; pressed again from there and dragged. (11-13 real: all 3 pushes, on the coiled black
        cable and the black block with holes, stopped 12-13 mm over the top seen and were given up.) The command is set
        (in place) where it presses now."""
        S = self.S
        short = float(S.tcp_now()[2] - (top + T.FINGERTIP))
        if short <= self.BLOCKED or short > PRESS_TALLER:
            return super().press_blocked(cmd, R, top)
        S.resync()
        dr = dict(self.DRAG, **S.rig.get("drag", {}))
        touch = float(S.p_cmd[2])
        new = S.p_cmd.copy(); new[2] = max(touch - self.press_depth(), T.FINGERTIP - 0.003)
        S.move(new, R, 0.0, 0.8, "press"); S.hold(dr["hold"])
        short2 = float(S.tcp_now()[2] - touch)
        self.event("press: on its own top, higher than its depth said", short_mm=round(short * 1000, 1), again_short_mm=round(short2 * 1000, 1))
        if short2 > self.BLOCKED:
            return super().press_blocked(new, R, touch - T.FINGERTIP)
        cmd[:] = new
        return False


class SkillEpisode:
    def __init__(self, a):
        self.a = a
        ra = argparse.Namespace(backend=a.backend, port=a.port, no_cameras=False, speed=a.speed, v_max=a.v_max, no_record=a.no_record,
                                record_depth=None, clutter=a.clutter, layout=a.layout, seed=a.seed, pick="seg", plan="astra")
        self.out = RB.out_dir(ra, "skills")
        self.S, self.arm, self.world, self.wper, self.L = RB.make_rig(ra, self.out, "skills")
        S = self.S
        if a.backend == "robot":
            st = S.sync()
            dh = float(np.abs(np.asarray(st["q"]) - S.q_home).max())
            if dh > 0.05:
                raise SystemExit(f"the arm is {dh:.2f} rad from the home pose: run `real_box.py home` first")
        self.per = per = TR.TrackPerception(S, np.random.default_rng([a.seed, a.layout, 5]), self.out, f"skills_{a.backend}")
        self.prog = prog = SkillProgram(S, per, confirm_plan=False)
        prog.targets, prog.avoid, prog.box, prog.sizes, prog.reserved = {}, {}, None, {}, []
        prog.steps = per.steps = []
        per.seeds, per.templates, per.track_roi, per.still_mask = [], [], None, None
        prog.est, prog.PUSH_MAX = None, 0.08
        self.task = a.text.strip() if a.text else CB.task_sentence(self.L)
        if self.world is not None and self.L is not None:
            self.k0 = {k: self.world.pose(k)[0].copy() for k in self.L["objs"]}
        self.box_i = None
        self.scene, self.views = None, {}                              # (the latest images shown: Astra's pixels)
        self.focus = np.asarray(FOCUS0, float)                         # (the wrist's look: Astra's last object point)
        self.dir = per.dir                                             # (out/astra: the program's debug images too)
        self.n_img = 0
        self.log, self.calls, self.cost, self.usage = [], 0, 0.0, dict(input=0, cached=0, output=0, reasoning=0)
        self.n_tools, self.ended = 0, None
        self.exchange = open(self.out / "exchange.jsonl", "w")

    # ---------------------------------------------------------------- the arm out of the way, the cameras
    def park(self):
        """The hand up to PARK_Z, then the joints to the home pose (holding whatever it holds): the scene camera sees
        the table."""
        S = self.S
        S.flush()
        tcp = S.tcp_now()
        if np.abs(np.asarray(S.q_cmd) - S.q_home).max() < 0.05:
            return
        if tcp[2] < PARK_Z - 0.01:
            S.move(np.r_[tcp[:2], PARK_Z], S.R_cmd, S.g_cmd, 1.0, "up (park)"); S.flush()
        dq = float(np.abs(np.asarray(S.q_home) - np.asarray(S.q_cmd)).max())
        S._set_phase("park")
        S._queue(RB.joint_path(S, np.asarray(S.q_home, float), max(3.0, dq / 0.25)), S.g_cmd, B.DT)
        S.flush()
        S.resync()

    def snap(self):
        """The wrist camera over the focus (Astra's last object point; the arm hovers there, above the scene camera's
        line of sight to the table), then the scene and the wrist image (kept: Astra's next points are their pixels).
        (11-12 user: no reset to the home pose after every skill: 117 s of 715.) No look pose there: parked first."""
        wrist, what_w = self.wrist_over()
        if wrist is None:
            try:
                self.park()
            except (T.Unreachable, RB.RobotStop) as e:
                self.event_text += f" (the arm could not park: {e}; it may hide part of the scene)"
                try:
                    self.S.resync()
                except Exception:
                    pass
            wrist, what_w = self.per.observe("L_wrist"), "the hand's camera from the parked pose"
        self.scene = self.per.observe("d415")
        self.views.update(scene=self.scene, wrist=wrist)
        items = []
        for name, what in (("scene", "the scene camera"), ("wrist", what_w)):
            o = self.views[name]
            n = self.n_img; self.n_img += 1
            cv2.imwrite(str(self.dir / f"seen_{n:03d}_{name}.png"), cv2.cvtColor(o["rgb"], cv2.COLOR_RGB2BGR))
            items += [text_item(f'camera "{name}" ({what}, {o["rgb"].shape[1]}x{o["rgb"].shape[0]} px):'), png_item(o["rgb"])]
        return items

    def wrist_over(self, r=0.10):
        """The wrist camera straight down over the focus (Astra's last object point), seeing a disc of radius r: the pose
        of ClutterProgram.look_at (as high over tall things), the gripper opened as there (only here, with the hand
        empty). Kept as prog.pre_view: the next skill's look when its object is near the middle (11-13). The hand holding
        something: the view from the parked pose instead (the held thing hangs under the camera). -> (obs, text)"""
        S, prog = self.S, self.prog
        prog.pre_view = None
        if prog.held is None:
            try:
                X = np.r_[self.focus, 0.0]
                hl = prog.look_height(r)
                y = prog.best_yaw(np.r_[X[:2], X[2] + hl], list(prog.look_yaws(X[:2])))
                R = B.down(y)
                extra = prog.tall_extra(X, r, hl) if CB.TALL_LOOK else 0.0   # (as a pick's look: the view can stand
                while extra > 0.005:                                          # for one, PRE_R; 11-13)
                    if S.ik(prog.look_tcp(X, R, X[2] + hl + extra), R, S.q_cmd)[1] < 0.002:
                        break
                    extra -= 0.02
                hl += max(extra, 0.0)
                tcp = prog.look_tcp(X, R, X[2] + hl)
                if S.ik(tcp, R, S.q_cmd)[1] < 0.002 or S.ik(tcp, R, S.q_home)[1] < 0.002:
                    prog.transit(tcp[:2], R)
                    S.move(tcp, R, B.OPEN_W, 1.0, "look over the work area"); S.hold(0.2)   # (empty: open, as a look)
                    o = prog.unspread_obs(self.per.wrist_rgbd())
                    o["look"] = X.copy()
                    S.flush()
                    prog.pre_view = dict(obs=o, q=np.asarray(S.q_cmd, float).copy())
                    return o, "the hand's camera looking down at the area you last pointed at"
            except (T.Unreachable, RB.RobotStop) as e:
                self.event_text += f" (no wrist look there: {e})"
                try:
                    S.resync()
                except Exception:
                    pass
        if prog.held is not None:                                      # (holding: where it is, the held thing below)
            return self.per.observe("L_wrist"), "the hand's camera where the hand is (it holds something)"
        return None, None

    def holding_text(self):
        H = self.prog.held
        w = self.S.finger_width() * 1000
        return f"the hand holds the {H.get('name', 'thing')} (opening {w:.0f} mm)" if H is not None else f"the hand is empty (opening {w:.0f} mm)"

    # ---------------------------------------------------------------- steps from Astra's points
    def seed_at(self, uv, table=False, camera="scene", focus=False):
        o, per = self.views.get(camera) or self.scene, self.per
        uv = np.asarray(uv, float)
        if table:
            return dict(p=np.asarray(per.on_plane(o, uv, 0.0), float), ray=None)
        zt = per.top_z(o, uv, 0.012)
        zt = min(zt if zt is not None else 0.03, 0.06)
        p = np.asarray(per.on_plane(o, uv, zt), float)
        if focus and -0.75 < p[0] < 0.0 and -0.35 < p[1] < 0.60:      # (an object's point: the wrist's next look)
            self.focus = p[:2].copy()
        return dict(p=p, ray=per.ray_of(o, uv))

    def add_step(self, kind, name, seed):
        self.prog.steps.append((kind, name)); self.per.seeds.append(seed)
        return len(self.prog.steps) - 1

    def scene_px(self, X):
        uv = F.project(self.scene, np.asarray(X, float))
        return [int(round(uv[0])), int(round(uv[1]))]

    def last_dbg(self, name):
        n = getattr(self.prog, "_dbg_n", 0)
        p = self.dir / f"{self.per.tag}_{n:02d}_{name}.jpg"
        return p if p.exists() else None

    # ---------------------------------------------------------------- the skills
    def pick_direct(self, i, aside=None):
        """The clutter pick without its own clearing: found by name near the point, a grasp the fingers fit into, or a
        report of what blocks it. -> (result, extra text, debug image or None)"""
        prog = self.prog
        phrase = prog.steps[i][1]
        prog._phrase = phrase
        obs, mask, Q, info = prog.locate(i, phrase, digs=0)
        cov = prog.covering(obs, getattr(prog, "_raw", mask), Q)
        if cov is not None:
            P, ok = F.world(obs)
            Xc = P[cov & ok]
            where = self.scene_px(np.r_[np.median(Xc[:, :2], 0), float(np.percentile(Xc[:, 2], 90))]) if len(Xc) else None
            prog.event("something lies on it", points=int(cov.sum()))
            return "blocked", f"something lies on top of it (at scene pixel {where}): move that away first", None
        g, cands, geo, support, Pn, is_t, Q = prog.best_grasp(obs, mask, Q, None)
        b = g or (cands[0] if cands else None)
        prog.event("grasp plan", candidates=len(cands), clear=sum(c["ok"] for c in cands), top_mm=round(geo["top"] * 1000), found=g is not None)
        prog.dbg(obs, "plan", getattr(prog, "_last_masks", ()), mask, b)
        if g is None and cands and WALL_PINCH:                         # (11-13) a roll: gripped by its wall
            g = prog.wall_grasp(obs, mask, Q, support, geo, Pn)
            if g is not None:
                prog.dbg(obs, "plan", getattr(prog, "_last_masks", ()), mask, g)
        img = self.last_dbg("plan")
        if img is not None:
            self.views["skill"] = obs                                  # (the debug image is drawn on this look)
        if g is None:
            seen, spots = [], []
            for c in cands:
                if len(seen) >= 4:
                    break
                if any(abs((c["close_a"] - a + math.pi / 2) % math.pi - math.pi / 2) < math.radians(25) for a in seen):
                    continue
                seen.append(c["close_a"])
                hit = CB.blocking(Pn, ~is_t, c["c"], c["close_a"], c["q"], c["z_want"], c["fm"])
                if hit.sum() < 10:
                    continue
                Xb = Pn[hit]
                uv_ = F.project(obs, np.r_[np.median(Xb[:, :2], 0), float(np.percentile(Xb[:, 2], 90))])
                px = [int(round(uv_[0])), int(round(uv_[1]))]
                if all(abs(px[0] - s[0]) + abs(px[1] - s[1]) > 20 for s in spots):
                    spots.append(px)
            n_ok = sum(c["ok"] for c in cands)
            if not cands:                                              # (11-12 twin: a flat sticky pad: "0 of 0 blocked")
                prog.event("pick: no grasp possible (reported to Astra)")
                return "blocked", ("no grasp possible for it at all: the fingers cannot close across it anywhere (too flat, "
                                   "too wide for the 80 mm opening, or not seen well); push it instead"), img
            prog.event("pick blocked (reported to Astra)", candidates=len(cands), blocked_at_px=spots)
            return "blocked", (f"no grasp fits: {len(cands) - n_ok} of {len(cands)} finger directions blocked; the fingers would hit "
                               f"things at pixels {spots} of the \"skill\" image (the object outlined green, finger positions in "
                               f"orange = blocked)"), img
        prog.target_colour = colour_of(obs["rgb"], mask)
        try:
            for attempt in range(2):                                   # (a finger landed on something the depth did not
                if aside is not None:                                  # show (11-12 real: a thin black cable): another
                    r, self.aside_where = prog.aside_move(g, Q, geo, support, aside, phrase)   # direction once, before
                else:                                                  # Astra hears of it)
                    prog.approach(g, geo)                              # (11-13: a short, slow descent)
                    r = prog.grasp(g, Q, geo, support)
                if r not in ("blocked on the way down", "missed (closed on nothing)") or attempt == 1:
                    break
                g2 = (prog.wall_grasp(obs, mask, Q, support, geo, Pn, avoid=g["close_a"]) if g.get("wall")
                      else prog.best_grasp(obs, mask, Q, g["close_a"])[0])
                if g2 is None:
                    break
                prog.event("another grasp direction", why=r, a_deg=round(math.degrees(g2["close_a"])))
                print(f"   ({r}: another grasp direction)", flush=True)
                g = g2
        finally:
            prog.target_colour = None
        if aside is not None:
            return r, "", img
        if r == "ok":
            gg, H = prog.last_g, prog.held                             # (ClutterProgram.f_pick: its footprint for the drop)
            R = H["R"]; xa, ya = R[:2, 0], R[:2, 1]
            Qr = gg["Q"][:, :2] - gg["c"]
            hx_ = np.percentile(Qr @ xa, [2, 98]); hy_ = np.percentile(Qr @ ya, [2, 98])
            H.update(hx=float(np.ptp(hx_) / 2), hy=float(np.ptp(hy_) / 2), cx=float((hx_.max() + hx_.min()) / 2),
                     cy=float((hy_.max() + hy_.min()) / 2), grip=float(-H["bottom_off"]), name=phrase,
                     round=len(gg["dirs"]) > 90, top_h=float(gg["top"] - gg["table"]))
            H["h"] = H["top_h"]
            return "ok", "", img
        if r == "wrong object":
            prog.put_aside_held("wrong object in the hand")
        return r, "", img

    def do_pick(self, name, uv, camera="scene"):
        prog = self.prog
        if prog.held is not None:
            return f"not done: the hand already holds the {prog.held.get('name', 'thing')}; place it first", None
        prog.est = None
        i = self.add_step("pick", name, self.seed_at(uv, camera=camera, focus=True))
        r, extra, img = self.pick_direct(i)
        if r == "ok":
            return f"picked: {self.holding_text()}", img
        if r == "blocked":
            return f"not picked, blocked: {extra}", img
        return f"not picked: {r}{(': ' + extra) if extra else ''} ({self.holding_text()})", img

    def do_place(self, to, uv, camera="scene"):
        prog = self.prog
        if prog.held is None:
            return "not done: the hand holds nothing"
        name = prog.held.get("name", "thing")
        if not prog.still_holding():
            prog.held = None
            self.S.move(self.S.p_cmd, self.S.R_cmd, B.OPEN_W, 0.4, "open (nothing held)")
            return f"not placed: the {name} fell out of the hand on the way"
        if to == "box":
            i = self.add_step("in", "white box", self.seed_at(uv, camera=camera))
            self.survey_box(i)
            r = prog.f_in(i)
            return f"put into the box: {r}" if r == "ok" else f"not put into the box: {r}"
        prefer = self.seed_at(uv, table=True, camera=camera)["p"][:2]
        c0 = getattr(prog, "last_g", {}).get("c")
        Qg = getattr(prog, "last_g", {}).get("Q")
        r0 = float(np.percentile(np.linalg.norm(np.asarray(Qg)[:, :2] - np.asarray(c0)[:2], axis=1), 95)) if c0 is not None and Qg is not None and len(Qg) else 0.03
        spot = prog.aside_spot(c0, prefer, r0) if c0 is not None else None
        if spot is not None:
            n0 = len(prog.events)
            prog.put_down(np.r_[spot, 0.0], "carry to the table")
            r = prog.set_down_result(n0)
            return (f"set down on the table at scene pixel {self.scene_px(np.r_[spot, 0.0])}, "
                    f"{np.linalg.norm(np.asarray(spot) - np.asarray(c0)[:2]) * 100:.0f} cm from where it was picked") if r == "ok" else f"not set down: {r}"
        i = self.add_step("down", "table", self.seed_at(uv, table=True, camera=camera))
        r = prog.f_down(i)
        at = getattr(prog, "down_at", None)
        moved = next((e for e in reversed(prog.events) if e["what"].startswith("put down: the planned spot")), None)
        return (f"set down on the table at scene pixel {self.scene_px(np.r_[at, 0.0]) if at is not None else '?'}"
                + (" (the spot you gave was taken or out of reach: the nearest free one)" if moved is not None and moved["t"] >= self.t_tool else "")
                if r == "ok" else f"not set down: {r}")

    def survey_box(self, i):
        """The box measured by the wrist camera; again only if the point is off the box measured (beyond its corners +
        3 cm). (11-13 real: within 5 cm of its middle never held: Astra's point on the opening, through the scene
        camera, lands 5+ cm off it; the box was surveyed 4 times, 2 looks each, 64 s.)"""
        prog = self.prog
        b = prog.box
        if self.box_i is not None and b is not None and \
                np.linalg.norm(np.asarray(self.per.seeds[i]["p"][:2]) - np.asarray(b["c"])) < math.hypot(b["hu"], b["hv"]) + 0.03:
            prog.targets[i] = prog.targets[self.box_i]
            prog.event("box: as surveyed before", off_mm=round(float(np.linalg.norm(np.asarray(self.per.seeds[i]["p"][:2]) - np.asarray(b["c"]))) * 1000))
            return
        if b is not None:
            prog.event("box: surveyed again (the point is off the box measured)",
                       off_mm=round(float(np.linalg.norm(np.asarray(self.per.seeds[i]["p"][:2]) - np.asarray(b["c"]))) * 1000))
        prog.box = None
        prog.survey(i)
        self.box_i = i
        prog.box = prog.targets[i][0]

    def do_move(self, name, uv, to, to_uv, camera="scene", to_camera="scene"):
        if self.prog.held is not None:
            return f"not done: the hand already holds the {self.prog.held.get('name', 'thing')}; place it first", None
        if to == "table":                                              # (aside: lifted a few cm, carried low, set down
            prefer = self.seed_at(to_uv, table=True, camera=to_camera)["p"][:2]   # within 15 cm, toward to_point)
            self.prog.est, self.aside_where = None, None
            seed = self.seed_at(uv, camera=camera, focus=True)
            i = self.add_step("pick", name, seed)
            r, extra, img = self.pick_direct(i, aside=prefer)
            if r == "blocked":
                return f"not moved, blocked: {extra}", img
            if r == "ok" and self.aside_where is not None:
                spot, how, c0 = self.aside_where                      # (from where it was grasped, not from the point
                d = float(np.linalg.norm(np.asarray(spot) - c0[:2]))   # given: the twin said 52 mm where that read 1 cm)
                return (f"moved: set down {d * 100:.0f} cm from where it was, at scene pixel {self.scene_px(np.r_[spot, 0.0])}"
                        + (f" (no free spot within {self.prog.ASIDE_R * 100:.0f} cm of it: the nearest free one)" if how == "far" else "")), img
            return f"not moved: {r} ({self.holding_text()})", img
        if to == "box":                                                # (measured with the hand empty, before the pick)
            i = self.add_step("in", "white box", self.seed_at(to_uv, camera=to_camera))
            self.survey_box(i)
        to_view = self.views.get(to_camera)                            # (the pick's look replaces "skill")
        txt, img = self.do_pick(name, uv, camera)
        if self.prog.held is None:
            return txt, img
        if to_view is not None:
            self.views["_to"] = to_view
        return txt + "; " + self.do_place(to, to_uv, "_to" if to_view is not None else "scene"), img

    def do_push(self, name, uv, to_uv, camera="scene"):
        prog = self.prog
        if prog.held is not None:
            return f"not done: the hand holds the {prog.held.get('name', 'thing')}; place it first", None
        seed = self.seed_at(uv, camera=camera, focus=True)
        to = np.asarray(self.per.on_plane(self.views.get(camera) or self.scene, np.asarray(to_uv, float), float(seed["p"][2])), float)
        seed["to"] = to
        L = float(np.linalg.norm((to - seed["p"])[:2]))
        i = self.add_step("push", name, seed)
        n0 = len(prog.events)
        prog.f_push(i)
        ev = [e["what"] for e in prog.events[n0:]]
        img = None                                                     # (its look is not kept: not pointable)
        done = next((e for e in prog.events[n0:] if e["what"] == "pushed"), None)
        if done is not None:
            return f"pushed {done.get('dist_mm', round(min(L, prog.PUSH_MAX) * 1000))} mm ({done.get('how', 'from the side')})" + \
                (f" (asked {L * 1000:.0f} mm; at most {prog.PUSH_MAX * 1000:.0f})" if L > prog.PUSH_MAX else ""), img
        why = [w for w in ev if w.startswith(("push:", "drag", "press:", "look: hidden")) or "no free way" in w or "not found" in w]
        return f"not pushed: {'; '.join(why[-3:]) if why else 'nothing happened'}", img   # (11-13: the reason, not only "skipped")

    def tool(self, name, kw):
        self.t_tool = float(self.S.d.time)
        img = None
        if name == "look":
            self.event_text = ""
            return self.snap(), "look"
        if name == "done":
            self.ended = "done: " + str(kw.get("summary", ""))[:500]
            return [text_item("episode ended")], self.ended
        try:
            if name == "pick":
                txt, img = self.do_pick(str(kw["object"]), kw["point"], kw.get("camera", "scene"))
            elif name == "place":
                txt = self.do_place(str(kw["to"]), kw["to_point"], kw.get("camera", "scene"))
            elif name == "move":
                txt, img = self.do_move(str(kw["object"]), kw["point"], str(kw["to"]), kw["to_point"],
                                        kw.get("camera", "scene"), kw.get("to_camera", "scene"))
            elif name == "push":
                txt, img = self.do_push(str(kw["object"]), kw["point"], kw["to_point"], kw.get("camera", "scene"))
            else:
                txt = f"unknown skill {name}"
        except T.Unreachable as e:
            txt = f"not done: the arm cannot reach it ({e})"
            self.S.resync()
        except F.Unseen as e:
            txt = f"not done: {e} (looked near the point you gave)"
        except RB.RobotStop as e:
            txt = f"SAFETY STOP: {e}; the arm holds where it stopped"
            self.S.resync()
        self.event_text = ""
        items = [text_item(txt)]
        if img is not None:
            items += [text_item('camera "skill" (the close view of this pick, 640x480 px):'), jpg_item(img)]
        items += self.snap()
        items.insert(1, text_item(f"now {self.holding_text()}.{self.event_text} The scene now:"))
        return items, f"{name} {json.dumps(kw)[:160]} -> {txt}"

    # ---------------------------------------------------------------- Astra
    def ask(self, inp, prev=None):
        import requests
        body = {"model": MODEL, "instructions": self.instructions, "tools": TOOLS, "input": inp, "parallel_tool_calls": False,
                "reasoning": {"effort": EFFORT}, "max_output_tokens": 20000}
        if prev:
            body["previous_response_id"] = prev
        logged = []
        for it in inp:
            it2 = dict(it)
            for key in ("content", "output"):
                if isinstance(it.get(key), list):
                    it2[key] = [{**c, "image_url": "<image>"} if c.get("type") == "input_image" else c for c in it[key]]
            logged.append(it2)
        self.exchange.write(json.dumps({"request": {"previous_response_id": prev, "input": logged}}) + "\n")
        for attempt in range(6):
            t = time.time()
            try:
                r = requests.post("https://api.openai.com/v1/responses", json=body, timeout=600,
                                  headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
                if r.status_code == 200:
                    break
                err = f"{r.status_code}: {r.text[:400]}"
                if r.status_code in (400, 401, 403, 404):
                    raise RuntimeError("Astra: " + err)
            except requests.RequestException as e:
                err = repr(e)[:300]
            if attempt == 5:
                raise RuntimeError("Astra: " + err)
            time.sleep(5 * 2 ** attempt)
        j = r.json()
        self.exchange.write(json.dumps({"response": j}) + "\n"); self.exchange.flush()
        u = j.get("usage") or {}
        pin, out = u.get("input_tokens", 0), u.get("output_tokens", 0)
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
        reas = (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0) or 0
        cost = ((pin - cached) * PRICE["in"] + cached * PRICE["cached"] + out * PRICE["out"]) / 1e6
        self.calls += 1; self.cost += cost
        for k, v in (("input", pin), ("cached", cached), ("output", out), ("reasoning", reas)):
            self.usage[k] += v
        self.log.append(dict(t=float(self.S.d.time), api=dict(s=round(time.time() - t, 1), tokens_in=pin, cached=cached, tokens_out=out,
                                                               reasoning=reas, cost=round(cost, 4), status=j.get("status"))))
        return j

    def run(self):
        a, S = self.a, self.S
        self.instructions = INSTRUCTIONS.format(max_calls=a.max_calls, wall=WALL_TEXT)
        (self.out / "instructions.txt").write_text(self.instructions)
        self.event_text = ""
        items = self.snap()
        first = [text_item(self.task + " Other objects may be moved but must stay on the table. This is the first look:")] + items
        print(f"task: {self.task}", flush=True)
        resp = self.ask([{"role": "user", "content": first}])
        nudges = 0
        while True:
            outs = [it for it in resp.get("output", []) if it.get("type") == "function_call"]
            texts = [c.get("text", "") for it in resp.get("output", []) if it.get("type") == "message" for c in it.get("content", [])]
            if texts:
                self.log.append(dict(t=float(S.d.time), astra_text=" ".join(texts)[:2000]))
                print(f"  astra: {' '.join(texts)[:300]}", flush=True)
            if not outs:
                if nudges >= 2 or resp.get("status") == "incomplete" and nudges >= 1:
                    self.ended = self.ended or "no more tool calls"; break
                nudges += 1
                resp = self.ask([{"role": "user", "content": [text_item("Continue with the skills; call done() when the task is "
                                                                           "finished or cannot be done.")]}], prev=resp["id"])
                continue
            results = []
            for c in outs:
                try:
                    kw = json.loads(c.get("arguments") or "{}")
                except Exception:
                    kw = {}
                t0 = time.time()
                if kw.get("reason"):
                    print(f"  astra -> {c['name']}: {kw['reason']}", flush=True)
                try:
                    items, summ = self.tool(c["name"], {k: v for k, v in kw.items() if k != "reason"})
                except Exception as e:
                    traceback.print_exc()
                    items, summ = [text_item(f"error: {e!r}"[:300])], f"{c['name']} error {e!r}"[:300]
                self.n_tools += 1
                self.log.append(dict(t=float(S.d.time), tool=c["name"], args=kw, result=summ, wall_s=round(time.time() - t0, 1)))
                print(f"  [{self.n_tools:2d}] {summ[:300]}  ({time.time() - t0:.0f} s, ${self.cost:.2f})", flush=True)
                results.append({"type": "function_call_output", "call_id": c["call_id"], "output": items})
            if self.ended:
                break
            if self.n_tools >= a.max_calls:
                self.ended = f"skill call limit ({a.max_calls})"; break
            if self.cost >= a.max_cost:
                self.ended = f"cost limit (${a.max_cost})"; break
            if self.n_tools >= a.max_calls - 3:
                results.append({"role": "user", "content": [text_item(f"{a.max_calls - self.n_tools} skill calls left.")]})
            resp = self.ask(results, prev=resp["id"])

    def judge(self):
        if self.world is not None and self.L is not None:               # (the twin: its true state)
            w, L = self.world, self.L
            tg = L["target"]
            inside = RB.TB.inside_box(w, tg)
            off = [k for k in L["objs"] if w.pose(k)[0][2] < -0.01]
            moved = {k: round(float(np.linalg.norm(w.pose(k)[0][:2] - self.k0[k][:2])) * 1000) for k in L["objs"] if k != tg}
            return dict(ok=bool(inside and not off), target=tg, target_in_box=inside, off_table=off, others_moved_mm=moved, by="twin")
        if not sys.stdin.isatty():
            return dict(ok=None, by="nobody (no terminal)")

        def yn(q):
            while True:
                s = input(f"   {q} [y/n] ").strip().lower()
                if s in ("y", "yes", "n", "no"):
                    return s.startswith("y")
        try:
            done = yn("was the task done?")
            off = yn("anything knocked off the table?")
            iv = yn("did you intervene (E-stop, by hand, refused a move)?")
            note = input("   a note (Enter for none): ").strip()
        except EOFError:
            return dict(ok=None, by="no answer")
        return dict(ok=bool(done and not off and not iv), task_done=done, knocked_off=off, intervened=iv, note=note, by="operator")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", default=None, help="the task (the twin: its layout's sentence if not given)")
    ap.add_argument("--backend", choices=["robot", "twin"], default="robot")
    ap.add_argument("--clutter", choices=["pile", "bin"], default=None, help="twin: a clutter_bench layout")
    ap.add_argument("--layout", type=int, default=0)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--max-calls", type=int, default=25, help="skill calls")
    ap.add_argument("--max-cost", type=float, default=1.0, help="$ (upper-bound estimate)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--v-max", type=float, default=None)
    ap.add_argument("--no-record", action="store_true")
    a = ap.parse_args()
    if a.backend == "robot" and not a.text:
        raise SystemExit("--text is needed on the robot")
    if a.backend == "twin" and not a.clutter:
        raise SystemExit("--clutter pile|bin is needed on the twin")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")
    try:
        ep = SkillEpisode(a)
    except SystemExit as e:
        print(f"stopped: {e}", flush=True)
        os._exit(1)
    t0 = time.time()
    try:
        ep.run()
    except KeyboardInterrupt:
        ep.ended = "interrupted (Ctrl-C)"
        print("\ninterrupted: no more skills; the arm holds where it is", flush=True)
    except RB.RobotStop as e:
        ep.ended = f"robot stop: {e}"[:300]
    except Exception as e:
        traceback.print_exc(); ep.ended = f"crash: {e!r}"[:300]
    print(f"\nended: {ep.ended} | {ep.n_tools} skill calls, {ep.calls} Astra calls, ${ep.cost:.2f}, {time.time() - t0:.0f} s", flush=True)
    res = ep.judge()
    print(f"result: {'SUCCESS' if res.get('ok') else 'fail' if res.get('ok') is False else 'no verdict'} {res}", flush=True)
    row = dict(task=ep.task, backend=a.backend, layout=ep.L, ended=ep.ended, judge=res, skill_calls=ep.n_tools, astra_calls=ep.calls,
               cost_usd=round(ep.cost, 3), usage=ep.usage, wall_s=round(time.time() - t0, 1), log=ep.log, events=ep.prog.events,
               trajectories=ep.S.rec, server_log=getattr(ep.S, "server_log", None), model=MODEL, effort=EFFORT)
    (ep.out / "run.json").write_text(RB.jdump(row))
    (ep.out / "timeline.json").write_text(RB.jdump(ep.S.timeline))
    ep.exchange.close()
    try:
        RB.finish_recording(ep.S, compose_episode=True)
    except Exception as e:
        print(f"(recording: {e!r})")
    print(f"log: {ep.out}", flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
