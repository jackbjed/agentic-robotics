"""Pack the white box (the 6522 objects, model/twin_6522.xml): put the red tape, the yellow tape, the black block with
holes, the black cable and the screwdriver into the white box (the red tape ON the block, the screwdriver last), close
the box, then push it away with the closed gripper. ONE Astra call for the plan + its points (as task_fast.py), then
skill programs on the depth images, no more Astra calls.

    MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 ~/miniforge3/envs/teleavatar_client/bin/python -I code/task_box.py \
        --out out/task_box --layouts 3 [--perception gt|astra] [--save-traces]
    (needs code/composite_server.py on :8133 with data/scene/objsplat_6522.pt for the photoreal images)

Layouts: the white box with its lid open (~110 deg, hinge on a side), the 5 task objects and distractors (glue
bottle, sticky pad, white tape, brown box) at random poses within the left arm's reach and in view of the D415; the
other objects stay where they were scanned. Skills (on top of task_fast's pick and put_on):
  put_in (box)  survey the EMPTY-handed view into the box: its rim (rectangle, height), its floor, what is already in
                it (a height map), its open lid. After the pick: the spot for the held object (its footprint, seen at
                the pick, turned with the hand): inside the walls, on the lowest surface, low enough for the lid to
                close, fingers clear of walls and objects (the 3-box fin-ray model) for a release just above the
                surface; else a drop from just over the rim. Objects a later step stacks on get a corner (space for
                the fingers around them), the others go along the walls, away from it.
  put_on        as task_fast, with the target where the program put that object (Astra marked it where it was)
  close         the open lid: hinge = the rim edge next to the lid's points, its angle from them; the closed fingers
                go behind its top edge and sweep the arc about the hinge down to closed
  push          closed gripper against the box's wall that faces the arm, 12 cm away from the arm
"""
from __future__ import annotations

import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import argparse
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_bench as B  # noqa: E402

import task_bench as T  # noqa: E402
import task_fast as F  # noqa: E402
import press_skill as PS  # noqa: E402

B.MODEL = B.ROOT / ("model/twin_6522_realbox.xml" if os.environ.get("BOX_REAL", "0") == "1" else "model/twin_6522.xml")   # (after
# task_bench, which sets its own model; BOX_REAL=1: the white box at the real one's size, build_scene_6522_realbox.py)
B.PIECE = {}                                                    # (the stacking bench's pieces are not in this model)
B.COMP_URL = "http://127.0.0.1:8133/composite"

TASK = "pack_box"
TEXT = ("Put the red tape, the yellow tape, the black block with holes, the black cable and the screwdriver into the white "
        "box, then close the box. The red tape must end up on top of the black block, inside the box. The screwdriver "
        "must be the last object put into the box. After closing the box, close the gripper and push the box away.")
# objects of this task (body obj_<k>): footprint radius for the layout (m), top height
OBJ = {
    "box_white":   dict(foot=0.135, h=0.061, desc="the white cardboard box"),
    "tape_red":    dict(foot=0.026, h=0.019, desc="the red tape roll"),
    "tape_yellow": dict(foot=0.028, h=0.019, desc="the yellow tape roll"),
    "hole_block":  dict(foot=0.052, h=0.022, desc="the black block with four holes"),
    "cable":       dict(foot=0.087, h=0.014, desc="the coiled black cable"),
    "screwdriver": dict(foot=0.096, h=0.024, desc="the red screwdriver"),
    "glue":        dict(foot=0.036, h=0.128, desc="the glue bottle"),
    "sticky_pad":  dict(foot=0.055, h=0.020, desc="the sticky-note pad"),
    "tape_white":  dict(foot=0.027, h=0.027, desc="the white tape roll"),
    "box_brown":   dict(foot=0.105, h=0.175, desc="the brown box"),
}
ITEMS = ["hole_block", "tape_red", "tape_yellow", "cable", "screwdriver"]
DISTRACT = ["glue", "sticky_pad", "tape_white", "box_brown"]
T.TASKS[TASK] = dict(text=TEXT, move=["box_white"] + ITEMS,
                     steps=[("pick", "hole_block"), ("in", "box_white"), ("pick", "tape_red"), ("on", "hole_block"),
                            ("pick", "tape_yellow"), ("in", "box_white"), ("pick", "cable"), ("in", "box_white"),
                            ("pick", "screwdriver"), ("in", "box_white"), ("close", "box_white"), ("push", "box_white")])
PACK = ITEMS                                                    # what must end up in the box (ITEMS: the layouts)
if os.environ.get("BOX_TASK", "full") == "loose":               # 10-29 (user): the cable, the yellow tape and the screwdriver
    TEXT = ("Put the black cable into the white box, then the yellow tape, then the screwdriver, then close the box. They "  # in
            "may overlap. Leave everything else where it is.")    # the box, overlapping allowed (BoxProgram.loose_spot);
    PACK = ["cable", "tape_yellow", "screwdriver"]                # the cable first (alone it fits along the 18 cm side),
    T.TASKS[TASK] = dict(text=TEXT, move=["box_white"] + ITEMS,   # the screwdriver (187 mm) last, along a diagonal; no lid
                         steps=[("pick", "cable"), ("in", "box_white"), ("pick", "tape_yellow"), ("in", "box_white"),
                                ("pick", "screwdriver"), ("in", "box_white"), ("close", "box_white")])   # (+ the lid: user)
LOOSE = os.environ.get("BOX_TASK", "full") == "loose"
if os.environ.get("BOX_TASK", "full") == "close":               # 10-29 (user): only close the box's lid, from where the arm is
    TEXT = "Close the white box. Leave everything else where it is."
    PACK = []
    T.TASKS[TASK] = dict(text=TEXT, move=["box_white"] + ITEMS, steps=[("close", "box_white")])
if os.environ.get("BOX_TASK", "full") == "tape":                # 10-29 (user): one tape first (the block's 5 mm a side in the
    TEXT = ("Put the yellow tape into the white box, then close the box. Leave everything else where it is.")   # open
    PACK = ["tape_yellow"]                                        # gripper: the real fingers landed on its top)
    T.TASKS[TASK] = dict(text=TEXT, move=["box_white"] + ITEMS,
                         steps=[("pick", "tape_yellow"), ("in", "box_white"), ("close", "box_white")])
if os.environ.get("BOX_TASK", "full") == "three":               # 10-29 (user): the block, the red tape on it and the yellow tape
    TEXT = ("Put the black block with holes into the white box, then put the red tape on top of the black block (inside "   # in
            "the box), then put the yellow tape into the box, then close the box. Leave the black cable and the "          # the
            "screwdriver where they are.")                        # real box (15.8 x 18.0 cm inside), then the lid; no cable,
    PACK = ["hole_block", "tape_red", "tape_yellow"]              # no screwdriver (187 mm: it does not fit), no push
    T.TASKS[TASK] = dict(text=TEXT, move=["box_white"] + ITEMS,   # (the layouts: as the full task's)
                         steps=[("pick", "hole_block"), ("in", "box_white"), ("pick", "tape_red"), ("on", "hole_block"),
                                ("pick", "tape_yellow"), ("in", "box_white"), ("close", "box_white")])
T.TASK_BOX = np.array([[x, y, z] for x in (-0.62, 0.0) for y in (-0.36, 0.48) for z in (0.0, 0.26)])   # the D415 crop
BOX_L, BOX_W, BOX_H, BOX_T = 0.191, 0.193, 0.058, 0.003       # white box: tray (local x, local y), walls; lid on -y edge
if os.environ.get("BOX_REAL", "0") == "1":                      # (10-29: the real one, by ruler: 15.8 x 18.0 cm inside, 5.5 high)
    BOX_L, BOX_W, BOX_H = 0.186, 0.164, 0.055
LID_OPEN = 110.0                                                # deg, the task starts with the box open
LINK6 = os.environ.get("BOX_LINK6", "0") == "1"                 # (ablation switches; off = the 6/6 configuration)
LINK7 = os.environ.get("BOX_LINK7", "0") == "1"
DROP_WALL = os.environ.get("BOX_DROP_WALL", "0") == "1"
HOME_SEED = os.environ.get("BOX_HOME_SEED", "0") == "1"
DROP = os.environ.get("BOX_DROP", "1") == "1"                    # relaxed placement: drop it in from over the rim
# clearances against the wrist D415's box survey (straight down: the rim to ~0.5 mm, the walls' outer faces to ~1-2 mm;
# the earlier, tilted wrist camera read the rim ~2 mm high and the box 2-5 mm big per side; 4 and 8 mm absorbed that):
LID_CLEAR = float(os.environ.get("BOX_LID_CLEAR", "0.002"))      # what goes in stays this far under the rim
WALL_MARGIN = float(os.environ.get("BOX_WALL_MARGIN", "0.008"))  # its footprint inside the walls' outer faces
                                                                # (3 mm cardboard + 5 mm; with 2 mm the hole block, dropped
                                                                # into a corner, landed on the wall in 4 of 24 layouts)


class BoxSim(T.TaskSim):
    """TaskSim on the 6522 model: no kettle latch; obj_<k> bodies of OBJ."""
    def __init__(self):
        B.Sim.__init__(self)
        m = self.m
        self.ob = {k: m.body("obj_" + k).id for k in OBJ}
        self.oq = {k: m.jnt_qposadr[m.body_jntadr[b]] for k, b in self.ob.items()}
        self.fingers = {m.body(n).id for n in ("L_left_finger", "L_right_finger")}
        self.subtree = {k: {c for c in range(m.nbody) if self.root_of(c) == b} for k, b in self.ob.items()}
        self.lid_q = m.joint("obj_box_white_lid_hinge").qposadr[0]
        if os.environ.get("BOX_STIFF_FLOOR", "1") == "1":             # the box's 3 mm floor stiffer (user OK 10-25):
            for n in ("obj_box_white_floor",):                         # with the default soft contact, things dropped
                m.geom_solref[m.geom(n).id] = [0.012, 1.0]             # in fast sink through it to the table; the
                m.geom_priority[m.geom(n).id] = 1                      # table's own (soft) contact for the floor on it:
            m.geom_priority[m.geom("table_top").id] = 2                # (stiff there, the box jumps when it settles)

    @property
    def latched(self):
        return False

    def move(self, target, R, grip, T_, phase=None):
        """TaskSim.move; a straight line the arm cannot follow because of a configuration change (a joint jump) is
        done in joint space instead (the end pose reached exactly, the path in between curved, short moves only)."""
        try:
            return super().move(target, R, grip, T_, phase)
        except T.Unreachable as e:
            if getattr(self, "straight", False) or "joint step" not in str(e) or float(np.linalg.norm(np.asarray(target) - self.p_cmd)) > 0.6:
                raise
            q1, err = self.ik(np.asarray(target, float), R, self.q_cmd)
            if err > 0.002:
                q1, err = self.ik(np.asarray(target, float), R, self.q_home)
            if err > 0.002:
                raise
            self.timeline.append((float(self.d.time), "phase", (phase or "move") + " (joint space)"))
            q0, g0 = self.q_cmd.copy(), self.g_cmd
            n = max(int(max(T_, 1.2) / B.DT), 1)
            for k in range(1, n + 1):
                s_ = k / n; s_ = s_ * s_ * (3 - 2 * s_)
                self.q_cmd = q0 + (q1 - q0) * s_; self.d.ctrl[self.act] = self.q_cmd
                self.d.ctrl[self.grip] = g0 + (grip - g0) * min(1.0, 2 * s_)
                self.physics()
            self.p_cmd, self.R_cmd, self.g_cmd = np.asarray(target, float).copy(), R, grip
            return 0.0

    def physics(self, n_frames=1):
        m, d, mj = self.m, self.d, self.mj
        for _ in range(n_frames):
            for _ in range(self.sub):
                if self.gravcomp:
                    d.qfrc_applied[self.arm_dofs] = d.qfrc_bias[self.arm_dofs]
                mj.mj_step(m, d)
            self.rec.append((float(d.time), d.qpos.copy()))

    def box_frame(self):
        """White box: centre (tray, floor level), rotation (local x = tray L, y = tray W; the hinge on local -y)."""
        p, R = self.pose("box_white")
        return p, R


# ---------------------------------------------------------------- layouts
def sample_layout(seed, idx):
    """The white box (lid open, hinge on its +y or -y side, seen from the arm), the task objects and the distractors at
    random poses: no overlaps (2.5 cm contact margin), within the left arm's reach (inverse kinematics above each at 4
    hand angles, no arm contacts; for the box also its inside, its lid's top and the push), each task object visible
    from the D415 (>= 3 of 5 rays to its top)."""
    rng = np.random.default_rng([seed, idx, 6522])
    S = BoxSim(); m, d, mj = S.m, S.d, S.mj
    mj.mj_resetDataKeyframe(m, d, m.key("twin").id)
    key = d.qpos.copy()
    mv = ["box_white", "cable", "screwdriver", "hole_block", "tape_red", "tape_yellow"] + DISTRACT   # big ones first
    geoms_of = {k: [g for g in range(m.ngeom) if S.root_of(m.geom_bodyid[g]) == S.ob[k] and m.geom_contype[g]] for k in mv}
    table = m.geom("table_top").id
    base = S.base[:2]
    _why = {}
    for tries in range(2500):
        d.qpos[:] = key
        place = {}
        hinge = rng.choice([-1, 1])                                     # lid hinge on the world +y or -y side of the box
        for k in mv:
            fk = OBJ[k]["foot"]
            for _ in range(300):
                if k == "box_white":
                    p = rng.uniform([-0.42, -0.12], [-0.22, 0.14])
                    r_lo, r_hi = 0.40, 0.58
                    yaw = (0.0 if hinge < 0 else math.pi) + rng.uniform(-0.25, 0.25)    # local -y -> world -y (yaw 0)
                elif k in ITEMS:                                        # (the parked right arm's hand is over y -0.27)
                    p = rng.uniform([-0.60, -0.20], [-0.05, 0.46])
                else:
                    p = rng.uniform([-0.60, -0.36], [-0.05, 0.46])
                    r_lo, r_hi = (0.34 + 0.3 * fk, 0.64) if k in ITEMS else (0.30, 0.75)
                    yaw = rng.uniform(0, 2 * math.pi)
                r = np.linalg.norm(p - base)
                if not r_lo < r < r_hi:
                    continue
                if any(np.linalg.norm(p - q[:2]) < fk + OBJ[j]["foot"] + 0.01 for j, q in place.items()):
                    continue
                place[k] = np.r_[p, yaw]
                break
            else:
                if k in DISTRACT:                                       # a distractor may stay where it was scanned
                    continue
        if not all(k in place for k in ["box_white"] + ITEMS):
            _why["place"] = _why.get("place", 0) + 1
            continue
        for k, (x, y, yaw) in place.items():
            if k == "screwdriver":                                      # its body origin is the handle/shaft junction:
                x, y = np.r_[x, y] + 0.0075 * np.r_[math.cos(yaw), math.sin(yaw)]   # (x, y) is its middle
            S.put(k, x, y, yaw)
        d.qpos[S.lid_q] = math.radians(LID_OPEN)
        margin0 = m.geom_margin.copy()
        for k in place:
            m.geom_margin[geoms_of[k]] = 0.025
        mj.mj_forward(m, d)
        bad = False
        for i in range(d.ncon):
            c = d.contact[i]
            b1, b2 = S.root_of(m.geom_bodyid[c.geom1]), S.root_of(m.geom_bodyid[c.geom2])
            if b1 == b2 or table in (c.geom1, c.geom2):
                continue
            if any(g in geoms_of[k] for k in place for g in (c.geom1, c.geom2)) and not (m.body(b1).name.startswith("L_") or m.body(b2).name.startswith("L_") or m.body(b1).name.startswith("R_") or m.body(b2).name.startswith("R_")):
                bad = True; break
        m.geom_margin[:] = margin0
        # the open lid stands ~25 cm high: the hand comes straight down over a pick, its open fingers ~9 cm wide each
        # way: no task object within 8 cm of the lid's footprint (seen from above)
        lg = m.geom("obj_box_white_lidp").id
        lc, lR, lh = d.geom_xpos[lg], d.geom_xmat[lg].reshape(3, 3), m.geom_size[lg]
        lid_xy = np.array([lc + lR @ (lh * np.array([sx, sy, 0.0])) for sx in np.linspace(-1, 1, 7) for sy in np.linspace(-1, 1, 7)])[:, :2]
        for k in ITEMS:                                               # (the objects it picks; not the distractors)
            if np.min(np.linalg.norm(lid_xy - S.pose(k)[0][:2], axis=1)) < OBJ[k]["foot"] + 0.08:
                bad = True
        if bad:
            _why["overlap"] = _why.get("overlap", 0) + 1
            continue
        mj.mj_forward(m, d)
        ok = True
        bp, bR = S.pose("box_white")
        lid_dir = -bR[:, 1]                                             # hinge edge side (horizontal)
        reach = []
        for k in ITEMS:
            p, _ = S.pose(k)
            reach += [np.r_[p[:2], z] for z in (T.FINGERTIP + 0.006, 0.08, 0.20)]
        reach += [np.r_[bp[:2] + bR[:2, :2] @ [sx * 0.05, sy * 0.05], z] for sx in (-1, 1) for sy in (-1, 1) for z in (0.07, 0.25)]
        lid_top = bp + bR @ [0, -BOX_W / 2, BOX_H] + 0.17 * (lid_dir * math.cos(math.radians(LID_OPEN)) + np.r_[0, 0, math.sin(math.radians(LID_OPEN))])
        reach += [lid_top + 0.03 * lid_dir + [0, 0, 0.03], bp + bR @ [0, BOX_W / 2 - 0.03, BOX_H + 0.03]]
        to = bp[:2] - base; to /= np.linalg.norm(to)
        reach += [np.r_[bp[:2] - to * (0.11 + 0.05), 0.045], np.r_[bp[:2] + to * 0.02, 0.045]]
        q_keep = d.qpos.copy()
        def arm_hits():
            """Contacts of the left arm with anything but the table (fingertips) and the objects: the other arm, the
            robot's frame (it must not brush them reaching here)."""
            out = set()
            for i in range(d.ncon):
                c = d.contact[i]
                n1, n2 = m.body(m.geom_bodyid[c.geom1]).name, m.body(m.geom_bodyid[c.geom2]).name
                if n1.startswith("L_") ^ n2.startswith("L_"):
                    other = n2 if n1.startswith("L_") else n1
                    if not other.startswith("obj_") and other != "world":
                        out.add(other)
                    elif other == "world" and not ("finger" in n1 + n2):
                        out.add("table/frame")
            return out
        rest = arm_hits()
        for X in reach:
            y0 = math.atan2(*(X[:2] - base)[::-1]) + math.pi / 2
            for dy in (0, 45, 90, 135):
                q, e = S.ik(np.asarray(X, float), B.down(y0 + math.radians(dy)), S.q_home)
                ok &= e < 0.002
                if dy == 0 and ok:
                    d.qpos[S.qadr] = q; mj.mj_forward(m, d)
                    hits_ = arm_hits() - rest
                    if hits_ and os.environ.get("DEBUG_LAYOUT"):
                        print("reach", np.round(X, 3), "arm touches", hits_, flush=True)
                    ok &= not hits_
        d.qpos[:] = q_keep; mj.mj_forward(m, d)
        if not ok:
            _why["reach"] = _why.get("reach", 0) + 1
            continue
        eye = d.cam_xpos[m.camera("d415").id].copy(); gid = np.zeros(1, np.int32)
        for k in ITEMS + ["box_white"]:
            p, _ = S.pose(k); top = OBJ[k]["h"] if k != "box_white" else 0.01
            rr = 0.5 * min(OBJ[k]["foot"], 0.05)
            hits = 0
            for q in [np.r_[p[:2], top - 0.003]] + [np.r_[p[:2] + rr * np.array([math.cos(a), math.sin(a)]), top - 0.003] for a in np.arange(4) * math.pi / 2]:
                v = q - eye
                mj.mj_ray(m, d, eye, v / np.linalg.norm(v), None, 1, -1, gid)
                hits += gid[0] >= 0 and S.root_of(m.geom_bodyid[gid[0]]) == S.ob[k]
            ok &= hits >= 3
        if not ok:
            _why["view"] = _why.get("view", 0) + 1
            continue
        return dict(task=TASK, seed=seed, layout=idx, tries=tries, hinge=int(hinge), why=dict(_why),
                    place={k: v.tolist() for k, v in place.items()})
    if os.environ.get("DEBUG_LAYOUT"):
        print("layout rejected:", _why, flush=True)
    return None


def apply_layout(S, L):
    for k, (x, y, yaw) in L["place"].items():
        if k == "screwdriver":
            x, y = np.r_[x, y] + 0.0075 * np.r_[math.cos(yaw), math.sin(yaw)]
        S.put(k, x, y, yaw)
    S.d.qpos[S.lid_q] = math.radians(LID_OPEN)
    S.mj.mj_forward(S.m, S.d)
    for c in B.CAM:
        S.set_camera_error(c, [0, 0, 0], [0, 0, 0])
    S.d.ctrl[S.act] = S.q_home; S.d.ctrl[S.grip] = B.OPEN_W
    S.hold(0.8)
    S.rec.clear()
    S.sync_cmd(); S.p_cmd = S.tcp_now(); S.q_cmd = S.q_home.copy(); S.g_cmd = B.OPEN_W


# ---------------------------------------------------------------- the one Astra call
PLAN_PROMPT = """{task}
This is a view of the table from a fixed camera, {W}x{H} pixels; pixel coordinates are x from the left edge, y from
the top edge. The robot is one arm with a parallel-jaw gripper that works from above. Its skills:
- pick: grasp an object. Give "grasp": the 2 points where the fingertips should touch it, on opposite sides across its
  middle, each on the top edge of the object (a block: the middles of two opposite sides; a roll of tape: two opposite
  points of its outer edge; a coiled cable: across the coil's short width; a tool: across its handle).
- put_in: put the held object into a container (an open box). Give "point": the centre of the container's inside (its
  floor, as seen in this image). The skill finds free space inside the container by itself.
- put_on: put the held object on top of another object. Give "point": the centre of that object's top face where it
  is in this image (if an earlier step moved it, the robot knows where it put it).
- close: close the open lid of a box. Give "point": a point on that lid.
- push: close the gripper and push an object away from the robot. Give "point": the centre of that object.
{press}
Plan the task as a sequence of these skills, in order (each object is picked, then put somewhere, before the next one
is picked), and give each step's points in this image. Other objects may look similar: mark only the ones the task is
about. Use the same words for the same object every time it appears.
Respond with this JSON object and nothing else:
{{"steps": [{{"skill": "<skill>", "object": "<pick: the object grasped; put_in / put_on: the container / the object it is put on;
close, push: the box; press: the button and what it is on>", "grasp": [[x, y], [x, y]] or "point": [x, y]}}, ...],
 "reasoning": "<1-2 sentences>"}}"""
PLAN_PROMPT = PLAN_PROMPT.replace("{press}", PS.PROMPT_LINE)      # (11-02: press, press_skill.py)
SKILLS = {"pick": "pick", "put_in": "in", "put_on": "on", "close": "close", "push": "push", "press": "press"}


class BoxPerception(F.FastPerception):
    """The plan (one Astra call, or the reference plan from the true state with --perception gt) and the depth looks.
    Wrist looks are depth only (no photoreal image: the skills do not use it)."""
    def __init__(self, S, rng, out, tag, gt=False):
        super().__init__(S, rng, out, tag, TASK)
        self.task_text = f"Task: {TEXT}"
        self.gt = gt
        self.depth_model = None
        if os.environ.get("D415_DEPTH", "ideal") == "real":           # a real D415's depth + calibration error
            import d415_model
            self.depth_model = d415_model.D415Real(S, np.random.default_rng(int(rng.integers(1 << 31))), B.MIN_Z,
                                                   **json.loads(os.environ.get("D415_PARAMS", "{}")))

    def observe(self, cam):
        if cam == "d415":
            return super().observe(cam)
        S, m, d, mj = self.S, self.S.m, self.S.d, self.S.mj
        rr = self.renderers[B.SIZE[cam]]; Wc, Hc = B.SIZE[cam]
        mj.mj_forward(m, d)
        cid = m.camera(cam).id
        f = Hc / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        K = (Wc, Hc, f, f, Wc / 2, Hc / 2)
        opt = mj.MjvOption(); opt.sitegroup[:] = 0
        rr.enable_depth_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); dep = rr.render().copy(); rr.disable_depth_rendering()
        depth = np.where(np.isfinite(dep) & (dep < 10), dep, np.inf).astype(np.float32)
        sig = 0.08 * depth ** 2 / (f * B.CAM[cam]["baseline"])
        noisy = depth + self.rng.normal(0, 1, depth.shape).astype(np.float32) * np.where(np.isfinite(sig), sig, 0)
        noisy = np.where(depth > B.MIN_Z, noisy, np.inf).astype(np.float32)   # (too near: no depth)
        if self.depth_model is not None:
            rr.enable_segmentation_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); seg = rr.render().copy()
            rr.disable_segmentation_rendering()
            gid = np.where((seg[..., 1] == int(mj.mjtObj.mjOBJ_GEOM)) & (seg[..., 0] >= 0), seg[..., 0], -1)
            noisy = self.depth_model.apply(depth, gid, cam)
        R_nom, eye_nom = S.nominal_camera(cam)
        return dict(cam=cam, rgb=None, depth=noisy, K=K, R=R_nom, eye=eye_nom, t=float(d.time))

    def plan(self, feedback=None):
        if self.gt:
            return self.plan_gt()
        obs, img, o, _ = self.frame("d415")
        msgs = [{"role": "user", "content": [{"type": "text", "text": PLAN_PROMPT.format(task=self.task_text, W=img.shape[1], H=img.shape[0])},
                                             self.img_block(img)]}]
        if feedback:
            msgs += [{"role": "assistant", "content": self.astra_log[-1]["rounds"][0]["reply"]},
                     {"role": "user", "content": [{"type": "text", "text": feedback}]}]
        js, txt, info = self.ask(msgs)
        self.steps, self.seeds, marks = [], [], []
        for st in (js or {}).get("steps") or []:
            sk = SKILLS.get(str(st.get("skill", "")).strip())
            if sk is None:
                continue
            seed = None
            try:
                if sk == "pick" and st.get("grasp") and len(st["grasp"]) == 2:
                    uv = [np.asarray(p, float) + o for p in st["grasp"]]
                    mid = (uv[0] + uv[1]) / 2
                    zt = self.top_z(obs, mid, 0.012)
                    zt = min(zt if zt is not None else 0.03, 0.06)
                    seed = dict(p=self.on_plane(obs, mid, zt), ray=self.ray_of(obs, mid))
                    marks += [(tuple(p), self.COLS["grasp"]) for p in st["grasp"]]
                elif st.get("point") is not None:
                    uv = np.asarray(st["point"], float) + o
                    ray = self.ray_of(obs, uv)
                    if sk == "press":                           # (the nearest depth near the pixel if it has none)
                        seed = PS.seed_from_pixel(obs, uv)
                    elif sk == "on":
                        z = self.top_z(obs, uv, 0.012)
                        seed = dict(p=self.on_plane(obs, uv, z), ray=ray) if z is not None else (dict(p=ray[2], ray=ray) if ray[2] is not None else None)
                    elif ray[2] is not None:                    # the surface the camera saw at the point
                        seed = dict(p=ray[2], ray=ray)
                    marks.append((tuple(st["point"]), self.COLS["place"]))
            except Exception:
                seed = None
            if seed is not None:
                seed["uv"] = None
            self.steps.append((sk, str(st.get("object", "")).strip().lower()))
            self.seeds.append(seed)
        if marks:
            cv2.imwrite(str(self.dir / f"{self.tag}_plan.jpg"), cv2.cvtColor(self.draw(img, [((float(a), float(b)), c) for (a, b), c in marks]), cv2.COLOR_RGB2BGR))
        self.astra_log.append(dict(t=float(self.S.d.time), keys=["plan"] if not feedback else ["plan (retry)"],
                                   rounds=[dict(reply=txt, **info)], final=js, img=f"{self.tag}_plan.jpg"))
        return self.steps

    def plan_gt(self):
        """The reference plan with points from the true state (the D415's ray to each point, the surface it meets)."""
        S, m, d = self.S, self.S.m, self.S.d
        eye = d.cam_xpos[m.camera("d415").id].copy()
        def seed_at(X):
            u = (X - eye) / np.linalg.norm(X - eye)
            return dict(p=np.asarray(X, float), ray=(eye, u, np.asarray(X, float)), uv=None)
        self.steps, self.seeds = [], []
        bp, bR = S.pose("box_white")
        for sk, k in T.TASKS[TASK]["steps"]:
            if sk == "pick" or sk == "on":
                p, _ = S.pose(k)
                X = np.r_[p[:2], OBJ[k]["h"] - 0.002] if k != "screwdriver" else np.r_[(p + d.xmat[S.ob[k]].reshape(3, 3) @ [-0.0075, 0, 0])[:2], 0.022]
            elif sk == "in":
                X = bp + [0, 0, 0.004]
            elif sk == "close":
                lid = S.m.body("obj_box_white_lid").id
                X = d.xpos[lid] + d.xmat[lid].reshape(3, 3) @ [0, 0.10, 0.0]
            else:
                X = bp + [0, 0, BOX_H]
            self.steps.append((sk, k)); self.seeds.append(seed_at(X))
        self.astra_log.append(dict(t=float(d.time), keys=["plan (true state)"], rounds=[dict(reply="", s=0.0)], final=None, img=None))
        return self.steps


# ---------------------------------------------------------------- geometry: the box, a spot in it
def refine_box(obs, seed, zmax=0.30):
    """The open/closed box near seed from a depth image: its rim (rectangle: centre, axes, half sizes; height), floor,
    the points inside it, and its open lid (hinge edge, inward normal, angle, length) if any. zmax: ignore points above
    (the robot's own fingers, which the wrist camera sees)."""
    P, ok = F.world(obs)
    ok = ok & (P[..., 2] < zmax)
    tz = F.table_height(P, ok, seed[:2], r=0.25)
    d = np.hypot(P[..., 0] - seed[0], P[..., 1] - seed[1])
    # the walls: a top-down grid (4 mm) of the points below 7 cm (an open lid overhangs its neighbours); connected
    # cells = one thing (in the image, whatever is seen just behind a wall would join it)
    sel = ok & (d < 0.30) & (P[..., 2] > tz + F.MARGIN) & (P[..., 2] < tz + 0.07)
    G = P[sel]
    res_ = 0.004; x0, y0 = seed[0] - 0.30, seed[1] - 0.30
    ij = np.floor((G[:, :2] - [x0, y0]) / res_).astype(int)
    grid = np.zeros((150, 150), np.uint8)
    okc = (ij >= 0).all(1) & (ij < 150).all(1)
    grid[ij[okc, 1], ij[okc, 0]] = 1
    n, labg = cv2.connectedComponents(grid, connectivity=8)
    best = None
    for j in range(1, n):
        cells = np.argwhere(labg == j)
        if len(cells) < 60:
            continue
        xy = cells[:, ::-1] * res_ + [x0 + res_ / 2, y0 + res_ / 2]
        (cx, cy), (w, l), _ = cv2.minAreaRect(xy.astype(np.float32))
        if not (0.12 < w < 0.30 and 0.12 < l < 0.30):
            continue
        dist = math.hypot(cx - seed[0], cy - seed[1])
        if best is None or dist < best[0]:
            best = (dist, j)
    if best is None:
        raise F.Unseen("no box at the point")
    inc = np.zeros(len(G), bool); inc[okc] = labg[ij[okc, 1], ij[okc, 0]] == best[1]
    Q = G[inc]
    def rect(pts):
        (cx, cy), (w, l), ang = cv2.minAreaRect(pts[:, :2].astype(np.float32))
        a = math.radians(ang); u = np.array([math.cos(a), math.sin(a)]); v = np.array([-u[1], u[0]])
        return np.array([cx, cy]), u, v, w / 2, l / 2
    c, u, v, hu, hv = rect(Q)
    rel = Q[:, :2] - c; su, sv = rel @ u, rel @ v
    bands = [Q[(np.abs(su - s * hu) < 0.012) & (np.abs(sv) < hv - 0.02), 2] for s in (-1, 1)] + \
            [Q[(np.abs(sv - s * hv) < 0.012) & (np.abs(su) < hu - 0.02), 2] for s in (-1, 1)]
    rim = float(np.median([np.percentile(b, 95) for b in bands if len(b) > 20]))
    c, u, v, hu, hv = rect(Q[Q[:, 2] < rim + 0.004])                 # without the open lid's foot
    sp = F.spread(obs, Q)
    hu, hv = hu - sp, hv - sp                                         # (the D415 spreads the walls ~4 px outward: the
                                                                      # box read 8-11 mm big, things went onto a wall)
    # inside: all points (any component) within the walls; along the walls from the depth with the D415's edge spread
    # taken back (the walls' tops read ~4 px too wide, a rim-high band inside them that crowded out the free space: "no
    # free spot" for the screwdriver in 6 of 24 layouts). Not elsewhere: it would wipe out thin things (the cable's
    # strands) that read less spread than that, and the next thing would go on them
    rel = P[..., :2] - c; su, sv = rel @ u, rel @ v
    P2, ok2 = F.world(dict(obs, depth=F.unspread(obs["depth"])))
    wall = ((np.abs(su) > hu - 0.016) | (np.abs(sv) > hv - 0.016))[..., None]
    Pi = np.where(wall, P2, P); oki = np.where(wall[..., 0], ok2, ok) & (Pi[..., 2] < zmax)
    rel2 = Pi[..., :2] - c; su2, sv2 = rel2 @ u, rel2 @ v
    ins = oki & (np.abs(su2) < hu - 0.004) & (np.abs(sv2) < hv - 0.004) & (Pi[..., 2] < rim + 0.10)
    I = Pi[ins]
    floor = float(np.percentile(I[:, 2], 10)) if len(I) > 100 else tz + 0.004
    box = dict(c=c, u=u, v=v, hu=hu, hv=hv, rim=rim, floor=floor, table=tz, inside=I, lid=None)
    # the lid: points above the rim around the box (not over it), within 8 cm of its walls, and joined to them in the
    # image (a tall neighbour is not)
    m2 = F.clean(ok & (d < 0.30) & (P[..., 2] > tz + F.MARGIN))
    n2, lab2 = cv2.connectedComponents(m2.astype(np.uint8), connectivity=8)
    wall_px = np.zeros(m2.shape, bool); wall_px[sel] = inc
    ids, cnt = np.unique(lab2[wall_px], return_counts=True)
    whole = lab2 == ids[np.argmax(cnt * (ids > 0))]
    over = (np.abs(su) < hu - 0.01) & (np.abs(sv) < hv - 0.01)
    ring = (np.abs(su) < hu + 0.08) & (np.abs(sv) < hv + 0.08)
    box["above"] = int((ok & over & (P[..., 2] > rim + 0.012)).sum())   # over the opening: a lid not shut, or something
    box["standing"] = int((whole & ring & (P[..., 2] > rim + 0.03)).sum())   # a lid standing (seen edge-on from above)
    L = P[whole & ring & ~over & (P[..., 2] > rim + 0.02)]             # sticking out
    if len(L) > 150:                                                  # the open lid: its base is at one rim edge
        rl = L[:, :2] - c; lu, lv = rl @ u, rl @ v
        edges = [(-u, hu), (u, hu), (-v, hv), (v, hv)]              # (outward normal, half size)
        k = int(np.argmax([np.median(rl @ nrm) - h for nrm, h in edges]))  # the edge the lid stands at
        nrm, h = edges[k]
        n_in = -nrm; along_e = np.array([-nrm[1], nrm[0]])
        half_e = hv if k < 2 else hu
        mid = np.r_[c + nrm * h, rim]
        s_in = (L[:, :2] - mid[:2]) @ n_in; hgt = L[:, 2] - rim
        sel = hgt > 0.05
        th = float(np.median(np.arctan2(hgt[sel], s_in[sel]))) if sel.sum() > 30 else math.radians(100)
        box["lid"] = dict(mid=mid, n_in=n_in, axis=along_e, half=half_e, th=th, len=2 * (hu if k < 2 else hv),
                          top=float(np.percentile(L[:, 2], 98)), n=len(L))
    return box


class Spot:
    """Candidate placements of the held object in the box, scored. The held object = a rectangle in the hand frame
    (from the points seen at the pick); fingers = task_fast's 3-box fin-ray model."""
    def __init__(self, box, obs, zmax=0.30):
        self.b = box
        P, ok = F.world(obs)
        ok = ok & (P[..., 2] < zmax)                                  # (not the robot's own fingers, seen in the look)
        rel = P[..., :2] - box["c"]
        near = ok & (np.abs(rel @ box["u"]) < box["hu"] + 0.12) & (np.abs(rel @ box["v"]) < box["hv"] + 0.12) & (P[..., 2] > box["table"] + 0.002)
        self.P = P[near][::2]                                         # the box region's points (walls, lid, contents)
        Ld = box.get("lid")
        if Ld is not None:                                            # + the open lid's plane (a look misses parts of it)
            L_ = min(max((Ld["top"] - box["rim"]) / max(math.sin(Ld["th"]), 0.3), 0.8 * Ld["len"]), Ld["len"])
            n3 = np.r_[Ld["n_in"], 0.0]; al = n3 * math.cos(Ld["th"]) + np.r_[0, 0, 1.0] * math.sin(Ld["th"])
            ax3 = np.r_[Ld["axis"], 0.0]
            syn = np.array([Ld["mid"] + r * al + w * ax3 for r in np.linspace(0, L_, 14) for w in np.linspace(-Ld["half"], Ld["half"], 14)])
            self.P = np.r_[self.P, np.repeat(syn, 3, axis=0)]

    def fingers_hit(self, c, a, z_tcp, q, z_top=0.32):
        """Points in the hand's way when it comes straight down from TCP height z_top to z_tcp at c, fingers' closing
        axis at angle a, opened to q: the fin-ray fingers (3 boxes each), the hand (+-106 mm along the fingers, +-32
        across, 110-208 mm over the TCP), link 7 (175-250 mm up, -60..95 mm along the closing axis) and link 6
        (210-380 mm up, within 14 cm of the axis: its side depends on the arm's pose)."""
        u = np.array([math.cos(a), math.sin(a)]); v = np.array([-u[1], u[0]])
        rel = self.P[:, :2] - c
        along, across, z = np.abs(rel @ u), np.abs(rel @ v), self.P[:, 2]
        sweep = max(z_top - z_tcp, 0.0)
        hit = np.zeros(len(self.P), bool)
        for lo, hi, half, dz0, dz1 in ((q + 0.0014, q + 0.0156, 0.0129, -0.015, 0.0082),
                                       (q + 0.0041, q + 0.0307, 0.0129, 0.0103, 0.0465),
                                       (q + 0.0, q + 0.044, 0.018, 0.060, 0.122)):
            hit |= (along > lo) & (along < hi) & (across < half + 0.002) & (z > z_tcp + dz0) & (z < z_tcp + sweep + dz1)
        hit |= (along < 0.108) & (across < 0.034) & (z > z_tcp + 0.108) & (z < z_tcp + sweep + 0.21)
        s_al = rel @ u                                                # link 7 (the flange): off to one side of the
        if LINK7:                                                     # axis
            hit |= (s_al > -0.06) & (s_al < 0.095) & (across < 0.05) & (z > z_tcp + 0.17) & (z < z_tcp + sweep + 0.25)
        if LINK6:
            hit |= (np.hypot(along, across) < 0.14) & (z > z_tcp + 0.21) & (z < z_tcp + sweep + 0.38)   # link 6 (any side)
        return int(hit.sum())

    def choose(self, held, reserve=None, target=False, flap_side=None, margin=WALL_MARGIN, around=None):
        """held: rect (hx, hy half sizes in the hand frame, centre offset cx, cy), h, w, grip (TCP above the held
        bottom), a0 (hand angle at the pick). reserve: (centre, radius) of a spot to keep free (a later stacking
        target); target: this object is one (-> a corner, room for fingers around it). -> dict or None."""
        b = self.b
        I = b["inside"][::3]
        relI = I[:, :2] - b["c"]
        occ = I[I[:, 2] > b["floor"] + 0.008]                          # what is in the box already
        self.occ = occ[::5, :2] if len(occ) > 30 else None
        cands = []
        a_box = math.atan2(b["u"][1], b["u"][0])                       # hand angles relative to the box's walls
        angles = a_box + np.radians(np.arange(0, 180, 15.0))
        if around is not None:                                        # (a planned spot: its angle +-15 deg)
            oc0, a0 = around
            angles = [a0 + da for da in np.radians([0, -15, 15])]
        for a in angles:
            ya = np.array([math.cos(a), math.sin(a)]); xa = np.array([-ya[1], ya[0]])
            if around is not None:
                tcp0 = oc0 - held["cx"] * xa - held["cy"] * ya
                grid = [(float((tcp0 - b["c"] + du * b["u"] + dv * b["v"]) @ b["u"]), float((tcp0 - b["c"] + du * b["u"] + dv * b["v"]) @ b["v"]))
                        for du in (-0.01, 0, 0.01) for dv in (-0.01, 0, 0.01)]
            else:
                grid = [(su, sv) for su in np.arange(-b["hu"] + 0.02, b["hu"] - 0.019, 0.01) for sv in np.arange(-b["hv"] + 0.02, b["hv"] - 0.019, 0.01)]
            for su, sv in grid:
                if True:
                    tcp = b["c"] + su * b["u"] + sv * b["v"]
                    oc = tcp + held["cx"] * xa + held["cy"] * ya        # the object's footprint centre
                    corners = np.array([oc + sx * held["hx"] * xa + sy * held["hy"] * ya for sx in (-1, 1) for sy in (-1, 1)])
                    cu, cv_ = (corners - b["c"]) @ b["u"], (corners - b["c"]) @ b["v"]
                    if np.abs(cu).max() > b["hu"] - margin or np.abs(cv_).max() > b["hv"] - margin:
                        continue                                        # footprint within the walls
                    r_ = relI - (oc - b["c"])
                    under = (np.abs(r_ @ xa) < held["hx"] + 0.004) & (np.abs(r_ @ ya) < held["hy"] + 0.004)
                    surf = max(float(np.percentile(I[under, 2], 98)) if under.sum() > 10 else b["floor"], b["floor"])
                    if surf + held["h"] > b["rim"] - LID_CLEAR:
                        continue                                        # the lid must close over it
                    if flap_side is not None and surf + held["h"] > b["rim"] - 0.03:
                        n_, h_ = flap_side                              # the lid's tuck flap comes down inside the wall
                        if ((corners - b["c"]) @ n_).max() > h_ - 0.010:   # opposite the hinge: keep tall things off it
                            continue
                    # release height: the lowest at which the fingers miss the walls and what is in the box (the
                    # fin-ray fingers are fat 1-5 cm over the TCP: a few cm up, that part is over the rim; a drop of
                    # 1-3 cm is harmless), else from over the rim, the fingers wide open
                    q = held["width"] / 2 + 0.006
                    mode = None
                    halo = (np.abs(r_ @ xa) < held["hx"] + 0.019) & (np.abs(r_ @ ya) < held["hy"] + 0.019)
                    halo_top = float(np.percentile(I[halo, 2], 98)) if halo.sum() > 10 else b["floor"]
                    wall_gap = min(b["hu"] - np.abs(cu).max(), b["hv"] - np.abs(cv_).max())
                    for lift in (0.004, 0.015, 0.028, 0.04, 0.055, b["rim"] + 0.01 - surf):
                        if lift >= 0.028 and halo_top > surf + 0.012:      # a drop shifts it a little: not next to
                            break                                          # anything taller (it would land on it),
                        if DROP_WALL and lift >= 0.028 and (wall_gap < 0.012 or target):   # nor within 12 mm of a wall (it lands
                            break                                          # on its edge, tilted); a stacking target: low
                        z_ = surf + lift + held["grip"]
                        if self.fingers_hit(tcp, a, z_, q) <= 3:      # (a 3 mm wall top is only a few points)
                            mode, z, drop_h = ("low" if lift < 0.01 else "mid" if lift < 0.03 else "drop"), z_, lift
                            break
                    if mode is None:
                        if os.environ.get("DEBUG_SPOT") and abs(abs(su) - 0.047) < 0.006 and abs(abs(sv) - 0.047) < 0.006:
                            zz = b["rim"] + 0.01 + held["grip"]
                            uu = np.array([math.cos(a), math.sin(a)]); vv = np.array([-uu[1], uu[0]])
                            rr = self.P[:, :2] - tcp
                            al_, ac_, z__ = np.abs(rr @ uu), np.abs(rr @ vv), self.P[:, 2]
                            hand = (al_ < 0.108) & (ac_ < 0.034) & (z__ > zz + 0.108)
                            wrist = (np.hypot(al_, ac_) < 0.06) & (z__ > zz + 0.20)
                            print(f"   corner su {su:+.3f} sv {sv:+.3f} a {math.degrees(a - a_box) % 180:.0f}: over-rim hits {self.fingers_hit(tcp, a, zz, q)}"
                                  f" (hand {int(hand.sum())}, wrist {int(wrist.sum())}; their z {np.round(np.percentile(z__[hand | wrist], [5, 50, 95]), 3) if (hand | wrist).any() else '-'})", flush=True)
                        continue
                    # score: on the floor, a low release, near the walls (a corner for a stacking target; others
                    # along the walls, away from a reserved spot)
                    wall_u = b["hu"] - np.abs(cu).max(); wall_v = b["hv"] - np.abs(cv_).max()
                    score = 30 * (surf - b["floor"]) + 1.0 * drop_h
                    waste = np.ptp(cu) * np.ptp(cv_) - 4 * held["hx"] * held["hy"]   # square to the walls (a block
                    score += 20.0 * waste                                    # set in at 45 deg wastes the box)
                    long_ = max(held["hx"], held["hy"]) > 0.06               # long things along a wall; small things in
                    if long_:                                                # a corner next to what is in already (the
                        score += min(wall_u, wall_v) + 0.1 * max(wall_u, wall_v)   # long strips stay free)
                    else:
                        score += wall_u + wall_v
                        if self.occ is not None and len(self.occ):
                            score += 0.5 * float(np.min(np.linalg.norm(self.occ - oc, axis=1)))
                    if reserve is not None:
                        dres = np.linalg.norm(oc - reserve[0]) - reserve[1] - max(held["hx"], held["hy"])
                        if dres < 0.0:
                            continue
                        score += 0.02 * max(0.0, 0.06 - dres)
                    cands.append(dict(score=score, tcp=tcp, a=a, z=z, mode=mode, surf=surf, oc=oc, corners=corners,
                                      terms=dict(wall_u=round(float(wall_u), 3), wall_v=round(float(wall_v), 3), waste=round(float(waste) * 1e4, 1),
                                                 drop=round(float(drop_h), 3), su=round(float(su), 3), sv=round(float(sv), 3), a=round(math.degrees(a - a_box)) % 180)))
        return sorted(cands, key=lambda c_: c_["score"])


def rect_dist(p, oc, xa, ya, hx, hy):
    """Distance from point p to the rectangle (centre oc, axes xa, ya, half sizes hx, hy); 0 inside."""
    d_ = p - oc
    return math.hypot(max(abs(d_ @ xa) - hx, 0.0), max(abs(d_ @ ya) - hy, 0.0))


def rects_overlap(A, B_):
    """Do two rectangles (4 corners each, any order) overlap? Separating axes: each rectangle's two edge directions."""
    def axes(C):
        c0 = C.mean(0); d_ = C - c0
        k = np.argsort(np.linalg.norm(C - C[0], axis=1))            # C[0]'s two neighbours: the nearer two corners
        e1, e2 = C[k[1]] - C[0], C[k[2]] - C[0]
        return [e1 / np.linalg.norm(e1), e2 / np.linalg.norm(e2)]
    for ax in axes(A) + axes(B_):
        pa, pb = A @ ax, B_ @ ax
        if pa.max() < pb.min() or pb.max() < pa.min():
            return False
    return True


def _drop(self, held, avoid=(), flap_side=None, margin=WALL_MARGIN, why=None, strip=None, hand=True, low_first=True, hinge_margin=None,
          h_final=None):
    """Relaxed placement: let the held object go with the fingers over the rim (or over what is under them), anywhere
    its footprint is inside the walls and the lid still closes over it (on the floor or on other things); not on or
    next to a stacking target / stack (avoid: [(xy, radius)]); the hand clear of the open lid. Lowest first, then
    along the walls. -> candidates as choose(); why counts the rejections."""
    b = self.b
    I = b["inside"][::3]
    relI = I[:, :2] - b["c"]
    why = {} if why is None else why
    bump = lambda k: why.__setitem__(k, why.get(k, 0) + 1)
    cands = []
    a_box = math.atan2(b["u"][1], b["u"][0])
    q = held["width"] / 2 + 0.006
    long_ = max(held["hx"], held["hy"]) > 0.06
    for a in a_box + np.radians(np.arange(0, 180, 15.0)):
        ya = np.array([math.cos(a), math.sin(a)]); xa = np.array([-ya[1], ya[0]])
        for su in np.arange(-b["hu"] + 0.02, b["hu"] - 0.019, 0.01):
            for sv in np.arange(-b["hv"] + 0.02, b["hv"] - 0.019, 0.01):
                tcp = b["c"] + su * b["u"] + sv * b["v"]
                oc = tcp + held["cx"] * xa + held["cy"] * ya
                corners = np.array([oc + sx * held["hx"] * xa + sy * held["hy"] * ya for sx in (-1, 1) for sy in (-1, 1)])
                cu, cv_ = (corners - b["c"]) @ b["u"], (corners - b["c"]) @ b["v"]
                if np.abs(cu).max() > b["hu"] - margin or np.abs(cv_).max() > b["hv"] - margin:
                    bump("walls"); continue
                if any(rect_dist(c0, oc, xa, ya, held["hx"], held["hy"]) < r for c0, r in avoid):
                    bump("stack"); continue
                if hinge_margin is not None and flap_side is not None and \
                        ((corners - b["c"]) @ -flap_side[0]).max() > flap_side[1] - hinge_margin:
                    bump("hinge wall"); continue                       # (the lid's hinge edge catches what falls by it)
                r_ = relI - (oc - b["c"])
                under = (np.abs(r_ @ xa) < held["hx"] + 0.004) & (np.abs(r_ @ ya) < held["hy"] + 0.004)
                surf = max(float(np.percentile(I[under, 2], 98)) if under.sum() > 10 else b["floor"], b["floor"])
                h_f = held["h"] if h_final is None else h_final       # (a stacking target: with what goes on it)
                if surf + h_f > b["rim"] - LID_CLEAR:
                    bump("lid would not close"); continue
                if strip is not None and surf + held["h"] > b["rim"] - LID_CLEAR - strip["h"] and rects_overlap(corners, strip["corners"]):
                    bump("kept for the last long thing"); continue       # (it must still lie over this one)
                if flap_side is not None and surf + h_f > b["rim"] - 0.03:
                    n_, h_ = flap_side                                 # (a stack to come: well clear, the measured
                    if ((corners - b["c"]) @ n_).max() > h_ - (0.010 if h_final is None else 0.025):   # walls are up
                        bump("flap"); continue                         # to 1 cm off)
                z0 = surf + 0.005 + held["grip"]                      # its bottom just over what is under it, the
                z = None if hand else z0                              # fingers clear of the walls / contents / lid
                for z_ in (z0, z0 + 0.015, z0 + 0.03, b["rim"] + 0.006 + T.FINGERTIP, b["rim"] + 0.02 + T.FINGERTIP):
                    if not hand or z_ < z0 - 1e-6:
                        continue
                    if self.fingers_hit(tcp, a, z_, q) <= 3:
                        z = z_; break
                if z is None:
                    bump("hand hits"); continue
                wall_u = b["hu"] - np.abs(cu).max(); wall_v = b["hv"] - np.abs(cv_).max()
                score = 30 * (surf - b["floor"]) + 0.3 * (z - z0)
                if low_first and z - z0 > 0.03:                        # let go low (<= 3 cm over what is under it)
                    score += 10.0                                      # wherever possible
                score += 20.0 * (np.ptp(cu) * np.ptp(cv_) - 4 * held["hx"] * held["hy"])
                score += (min(wall_u, wall_v) + 0.1 * max(wall_u, wall_v)) if long_ else (wall_u + wall_v)
                if h_final is not None and flap_side is not None:      # a stack: by the hinge (pressed shut, the lid
                    score += 2.0 * (flap_side[1] - float(((corners - b["c"]) @ -flap_side[0]).max()))   # dips least
                                                                       # there; the flap is far)
                cands.append(dict(score=score, tcp=tcp, a=a, z=z, mode="drop", surf=surf, oc=oc, corners=corners,
                                  terms=dict(surf=round(float(surf - b["floor"]), 3), lift=round(float(z - z0), 3),
                                             su=round(float(su), 3), sv=round(float(sv), 3), a=round(math.degrees(a - a_box)) % 180)))
    return sorted(cands, key=lambda c_: c_["score"])


Spot.drop = _drop


def _with_placed(self, corners, top, step=0.004):
    """A copy of this Spot with an object placed (its footprint, filled at height top, as inside points)."""
    e1, e2 = corners[2] - corners[0], corners[1] - corners[0]
    n1, n2 = max(2, int(np.linalg.norm(e1) / step) + 1), max(2, int(np.linalg.norm(e2) / step) + 1)
    g = np.array([corners[0] + a_ * e1 + b_ * e2 for a_ in np.linspace(0, 1, n1) for b_ in np.linspace(0, 1, n2)])
    sp = Spot.__new__(Spot)
    sp.b = dict(self.b, inside=np.r_[self.b["inside"], np.c_[g, np.full(len(g), top)]])
    sp.P = self.P
    return sp


Spot.with_placed = _with_placed


def footprint(Q, c=None):
    """Robust footprint of points Q (seen from above): the axes of the smallest box around the 2nd-98th percentiles
    (a convex hull grows with every noisy point; principal axes are arbitrary for a square: on its diagonals it comes
    out 12 % big), x along the longer side; half extents, centre offset from c. -> (axis angle, hx, hy, cx, cy)"""
    xy = Q[:, :2][::max(1, len(Q) // 3000)]
    c = xy.mean(0) if c is None else c
    a = np.radians(np.arange(0.0, 180.0, 1.0))
    pu = (xy - c) @ np.stack([np.cos(a), np.sin(a)])              # [n, angle]: along the axis at each angle
    lo, hi = np.percentile(pu, [2, 98], axis=0)
    ext = hi - lo; ext_p = np.roll(ext, -90)                       # the extent across (the axis 90 deg on)
    k = int(np.argmin(np.where(ext >= ext_p, ext * ext_p, np.inf)))   # smallest box, x the longer side
    kp = (k + 90) % 180
    return float(a[k]), ext[k] / 2, ext[kp] / 2, (hi[k] + lo[k]) / 2, (hi[kp] + lo[kp]) / 2


class Grid:
    """The box's inside as a 1 cm height grid (walls = 1 m), for the packing lookahead: where could a rectangle
    (half sizes hx, hy, height h) still go, square to the walls or turned, with the lid still closing over it?"""
    RES = 0.01

    def __init__(self, box, I):
        self.b = box
        self.nu, self.nv = int(round(2 * box["hu"] / self.RES)) + 2, int(round(2 * box["hv"] / self.RES)) + 2
        H = np.full((self.nu, self.nv), box["floor"], np.float32)
        rel = I[:, :2] - box["c"]
        iu = np.floor((rel @ box["u"] + box["hu"]) / self.RES).astype(int) + 1
        iv = np.floor((rel @ box["v"] + box["hv"]) / self.RES).astype(int) + 1
        okk = (iu >= 0) & (iu < self.nu) & (iv >= 0) & (iv < self.nv)
        np.maximum.at(H, (iu[okk], iv[okk]), I[okk, 2].astype(np.float32))
        H[0, :] = H[-1, :] = H[:, 0] = H[:, -1] = 1.0                    # walls (and 8 mm off them: half a cell more)
        self.H = H

    def add(self, corners, top):
        """Mark a placed footprint (4 corners, world xy) at height top."""
        m_ = self.mask(corners)
        self.H[m_] = np.maximum(self.H[m_], top)

    def mask(self, corners):
        b = self.b
        pts = np.c_[(corners - b["c"]) @ b["u"] + b["hu"], (corners - b["c"]) @ b["v"] + b["hv"]] / self.RES + 1
        img = np.zeros((self.nv, self.nu), np.uint8)
        hull = cv2.convexHull(pts.astype(np.float32)).reshape(-1, 2)
        cv2.fillConvexPoly(img, np.round(hull).astype(np.int32), 1)
        return img.T.astype(bool)

    def copy(self):
        G = Grid.__new__(Grid); G.b, G.nu, G.nv, G.H = self.b, self.nu, self.nv, self.H.copy()
        return G

    def candidates(self, hx, hy, h, rim, n=6, corner_first=True):
        """Up to n distinct spots (different angles / places), best first: the lowest surface, then the nearest a corner
        (a stacking target: corners only first). -> [(corners, surf, angle, centre)]"""
        out = []
        for a in np.radians(np.arange(0, 180, 15.0)):
            ca, sa = math.cos(a), math.sin(a)
            ext_u, ext_v = abs(hx * ca) + abs(hy * sa), abs(hx * sa) + abs(hy * ca)
            ku, kv = int(math.ceil(ext_u / self.RES + 0.3)), int(math.ceil(ext_v / self.RES + 0.3))
            uu, vv = np.meshgrid((np.arange(2 * ku + 1) - ku) * self.RES, (np.arange(2 * kv + 1) - kv) * self.RES, indexing="ij")
            K = ((np.abs(uu * ca + vv * sa) <= hx + 0.002) & (np.abs(-uu * sa + vv * ca) <= hy + 0.002)).astype(np.uint8)
            under = cv2.dilate(self.H, K, borderValue=1.0)
            okp = under + h <= rim - LID_CLEAR
            if not okp.any():
                continue
            ii, jj = np.nonzero(okp)
            corner = np.minimum(ii, self.nu - 1 - ii) + np.minimum(jj, self.nv - 1 - jj)
            waste = ext_u * ext_v - 4 * hx * hy
            order = np.lexsort((corner, np.round(under[ii, jj] - self.b["floor"], 2)))
            for k in order[:: max(1, len(order) // 8)][:8]:            # spread over the feasible places, not just corners
                out.append((round(float(under[ii[k], jj[k]] - self.b["floor"]), 2), int(corner[k]) + 200 * waste, ii[k], jj[k], a))
        out.sort(key=lambda c_: c_[:2])
        res, seen = [], []
        b = self.b
        for surf, _, i_, j_, a in out:
            if any(abs(i_ - i2) + abs(j_ - j2) < 3 and abs(a - a2) < 0.3 for i2, j2, a2 in seen):
                continue
            seen.append((i_, j_, a))
            cu, cv_ = (i_ - 1 + 0.5) * self.RES - b["hu"], (j_ - 1 + 0.5) * self.RES - b["hv"]
            oc = b["c"] + cu * b["u"] + cv_ * b["v"]
            ua = math.cos(a) * b["u"] + math.sin(a) * b["v"]; va = -math.sin(a) * b["u"] + math.cos(a) * b["v"]
            corners = np.array([oc + sx * hx * ua + sy * hy * va for sx in (-1, 1) for sy in (-1, 1)])
            res.append((corners, surf + b["floor"], math.atan2(ua[1], ua[0]), oc))
            if len(res) >= n:
                break
        return res

    def fits(self, hx, hy, h, rim):
        """A spot for the rectangle: the first (lowest, then nearest a corner) where everything under it is low enough
        for the lid; None if none. -> its 4 corners and its top."""
        best = None
        for a in np.radians(np.arange(0, 180, 15.0)):
            ca, sa = math.cos(a), math.sin(a)
            ext_u, ext_v = abs(hx * ca) + abs(hy * sa), abs(hx * sa) + abs(hy * ca)
            ku, kv = int(math.ceil(ext_u / self.RES + 0.3)), int(math.ceil(ext_v / self.RES + 0.3))
            K = np.zeros((2 * ku + 1, 2 * kv + 1), np.uint8)
            uu, vv = np.meshgrid((np.arange(2 * ku + 1) - ku) * self.RES, (np.arange(2 * kv + 1) - kv) * self.RES, indexing="ij")
            K[(np.abs(uu * ca + vv * sa) <= hx + 0.002) & (np.abs(-uu * sa + vv * ca) <= hy + 0.002)] = 1
            under = cv2.dilate(self.H, K.T if False else K, borderValue=1.0, iterations=1)   # max under the footprint
            okp = under + h <= rim - LID_CLEAR
            if not okp.any():
                continue
            ii, jj = np.nonzero(okp)
            corner = np.minimum(ii, self.nu - 1 - ii) + np.minimum(jj, self.nv - 1 - jj)
            k = int(np.lexsort((corner, np.round(under[ii, jj], 3)))[0])
            cand = (float(under[ii[k], jj[k]]), int(corner[k]), ii[k], jj[k], a)
            if best is None or cand[:2] < best[:2]:
                best = cand
        if best is None:
            return None
        surf, _, i_, j_, a = best
        b = self.b
        cu, cv_ = (i_ - 1 + 0.5) * self.RES - b["hu"], (j_ - 1 + 0.5) * self.RES - b["hv"]
        oc = b["c"] + cu * b["u"] + cv_ * b["v"]
        ua = math.cos(a) * b["u"] + math.sin(a) * b["v"]; va = -math.sin(a) * b["u"] + math.cos(a) * b["v"]
        corners = np.array([oc + sx * hx * ua + sy * hy * va for sx in (-1, 1) for sy in (-1, 1)])
        return corners, surf + h


# ---------------------------------------------------------------- the program
_LAST = {}
_refine_object = F.refine_object


def _refine_object_kept(*a, **kw):
    g = _refine_object(*a, **kw)
    _LAST["g"] = g
    return g


F.refine_object = _refine_object_kept                           # f_pick's look at the object: kept for its footprint


def same_object(a, b):
    a, b = a.replace("the ", ""), b.replace("the ", "")
    return a == b or a in b or b in a


class BoxProgram(PS.PressSkill, F.FastProgram):           # (11-02: + press, press_skill.py)
    def __init__(self, S, per):
        super().__init__(S, per, TASK)
        self.where = {}                                             # object name -> where the program put it (top)
        self.last_lid = None
        self.pack, self.sizes = None, {}
        self.inside_log = []                                        # (step, items inside the box): for the judge

    def ref(self, i):
        return None                                                 # (task_fast's error logs know only its own objects)

    def run(self):
        self.targets, self.avoid, self.box = {}, {}, None
        steps = self.per.plan()
        bad = self.check_plan(steps)
        if bad:
            self.event("plan check failed: asking again", why=bad)
            steps = self.per.plan(feedback=bad + " Mark the right objects (the task's objects are within the arm's reach) "
                                  "and respond with the whole plan again, same JSON format.")
        self.steps = steps
        self.event("plan", steps=[f"{s}:{o}" for s, o in steps])
        self.sizes = self.estimate_sizes()
        for i, (sk, obj) in enumerate(steps):
            if self.per.seeds[i] is None:
                return f"step {i} {sk} {obj}: no point from Astra"
            try:
                if sk == "pick" and i + 1 < len(steps) and steps[i + 1][0] in ("on", "in") and self.per.seeds[i + 1] is not None:
                    self.survey(i + 1)
                r = getattr(self, "f_" + sk)(i)
            except T.Unreachable as e:
                return f"step {i} {sk}: unreachable ({e})"
            except F.Unseen as e:
                return f"step {i} {sk}: {e}"
            if r != "ok":
                return f"step {i} {sk}: {r}"
            if sk in ("in", "on"):
                self.inside_log.append((i, sorted(k for k in ITEMS if inside_box(self.S, k))))
        return "done"

    # -- moving between places: up over everything (the open lid stands ~25 cm high), across, then down
    def safe_z(self):
        z = 0.34
        if self.box is not None and self.box.get("lid") is not None:
            z = max(z, self.box["lid"]["top"] + 0.08)
        if self.held is not None:
            z += self.held.get("grip", 0.03)
        return z

    def transit(self, xy, R=None):
        S = self.S
        R = S.R_cmd if R is None else R
        z = max(self.safe_z(), S.p_cmd[2])
        for z_ in np.arange(z, self.safe_z() - 0.09, -0.02):            # the highest the arm reaches there (far
            if S.ik(np.r_[xy, z_], R, S.q_cmd)[1] < 0.002:              # points: not as high)
                z = z_; break
        if np.linalg.norm(S.p_cmd[:2] - xy) < 0.01:
            return
        if S.p_cmd[2] < z - 0.005:
            S.move(np.r_[S.p_cmd[:2], z], S.R_cmd, S.g_cmd, 0.5, "up")
        # high above everything: in joint space, to the home-seeded configuration (each solution seeded from the last
        # one drifts, over a long task, into twisted ones: the elbow then hits the other arm); a straight line if the
        # joint path would dip or touch something
        if not self.joint_move(np.r_[xy, z], R, check=True):
            try:
                S.move(np.r_[xy, z], R, S.g_cmd, 1.5, "transit")
            except T.Unreachable:
                self.joint_move(np.r_[xy, z], R)

    def joint_move(self, X, R, T_=2.5, check=False, q1=None):
        S = self.S
        q1, e = (q1, 0.0) if q1 is not None else S.ik(np.asarray(X, float), R, S.q_home)
        if e > 0.002:
            if check:
                return False
            raise T.Unreachable(f"joint move to {np.round(X, 3).tolist()}")
        q0 = S.q_cmd.copy()
        if check:                                                     # the path: high, touching nothing
            k_ = S.k; k_.qpos[:] = S.d.qpos
            for f_ in np.linspace(0, 1, 11):
                k_.qpos[S.qadr] = q0 + (q1 - q0) * f_
                S.mj.mj_forward(S.m, k_)
                if k_.site_xpos[S.tcp][2] < X[2] - 0.08:
                    return False
                for c in k_.contact[:k_.ncon]:
                    n1, n2 = S.m.body(S.m.geom_bodyid[c.geom1]).name, S.m.body(S.m.geom_bodyid[c.geom2]).name
                    if not (n1.startswith("L_") ^ n2.startswith("L_")) or "world" in (n1, n2):
                        continue
                    if "finger" in n1 + n2 and ("obj_" in n1 + n2):          # (what the hand holds)
                        continue
                    return False
            T_ = max(1.5, 1.2 * float(np.abs(q1 - q0).max()))
        n = max(int(T_ / B.DT), 1)
        S.timeline.append((float(S.d.time), "phase", "transit (joint space)"))
        for k in range(1, n + 1):
            s_ = k / n; s_ = s_ * s_ * (3 - 2 * s_)
            S.q_cmd = q0 + (q1 - q0) * s_; S.d.ctrl[S.act] = S.q_cmd; S.physics()
        S.d.ctrl[S.act] = q1; S.hold(0.2)
        S.p_cmd, S.R_cmd = np.asarray(X, float).copy(), R
        return True

    def estimate_sizes(self):
        """Inventory for the packing lookahead: a look from above (wrist camera) at each object a later step puts in
        the box: its footprint (robust half sizes) and height. (The scene camera is too far and oblique: neighbours
        merge.) Not the first thing put somewhere: only the things after it are looked ahead for (rest_fits, the
        strip kept for a long one, the height of what is stacked on it), unless BOX_PACK_PLAN=1 (all at once). (10-30:
        the one-tape task looked at its tape for nothing.)"""
        out = {}
        S = self.S
        placed = [i for i, (sk, _) in enumerate(self.steps) if sk == "pick" and i + 1 < len(self.steps) and self.steps[i + 1][0] in ("in", "on")]
        skip = set(placed[:1]) if os.environ.get("BOX_PACK_PLAN", "0") != "1" else set()
        for i, (sk, name) in enumerate(self.steps):
            if sk != "pick" or self.per.seeds[i] is None or i in skip:
                continue
            s_ = self.per.seeds[i]["p"]
            try:
                R = B.down(self.best_yaw(np.r_[s_[:2], s_[2] + 0.22], self.look_yaws(s_[:2])))
                self.transit(self.look_tcp(np.r_[s_[:2], s_[2]], R, s_[2] + 0.22)[:2], R)
                S.move(self.look_tcp(np.r_[s_[:2], s_[2]], R, s_[2] + 0.22), R, S.g_cmd, 0.8, "inventory: look at " + name)
                S.hold(0.15)
                g = F.refine_object(self.per.wrist(), s_, zmax=s_[2] + 0.10, ray=self.per.seeds[i].get("ray"), whole=False)
            except (F.Unseen, T.Unreachable):
                continue
            _, hx, hy, _, _ = footprint(g["Q"])                       # (as read: the D415's edge spread, ~3 mm a
            out[name] = (hx + 0.003, hy + 0.003, float(g["top"] - g["table"]))   # side, is margin the packing needs)
        self.event("inventory", **{k: [round(v * 1000) for v in x] for k, x in out.items()})
        return out

    def plan_packing(self, box):
        """All the placements at once (the greedy one-by-one choice strands the last, long objects): in plan order,
        each object's footprint on a 1 cm height grid of the box (a stacking target: a corner; on it, what is stacked
        adds its height); a small depth-first search over a few spots each. -> {name: (centre, long-axis angle)}"""
        items = []
        for j, (sk, nm) in enumerate(self.steps):
            if sk not in ("in", "on") or j == 0 or self.steps[j - 1][0] != "pick":
                continue
            held = self.steps[j - 1][1]
            sz = next((v for k, v in self.sizes.items() if same_object(held, k)), None)
            if sz is None:
                self.event("packing plan", ok=False, why=f"no size for {held}")
                return {}
            if sk == "in":
                tgt = any(self.steps[q][0] == "on" and self.per.seeds[q] is not None and self.per.seeds[j - 1] is not None and
                          (same_object(self.steps[q][1], held) or np.linalg.norm(self.per.seeds[q]["p"][:2] - self.per.seeds[j - 1]["p"][:2]) < 0.04)
                          for q in range(j + 1, len(self.steps)))
                items.append(("in", held, sz, tgt))
            else:
                base = next((self.steps[q - 1][1] for q in range(j) if self.steps[q][0] == "in" and q > 0 and self.per.seeds[j] is not None and
                             self.per.seeds[q - 1] is not None and (same_object(self.steps[j][1], self.steps[q - 1][1]) or
                             np.linalg.norm(self.per.seeds[j]["p"][:2] - self.per.seeds[q - 1]["p"][:2]) < 0.04)), None)
                items.append(("on", held, sz, base))
        sol = {}
        G0 = Grid(box, box["inside"][::2])
        def rec(k, G):
            if k == len(items):
                return True
            kind, nm, (hx, hy, h), extra = items[k]
            if kind == "on":
                if extra not in sol:
                    return rec(k + 1, G)
                G2 = G.copy(); c_, top = sol[extra][2], sol[extra][3]
                G2.add(c_, top + h); sol[nm] = (sol[extra][0], sol[extra][1], c_, top + h)
                return rec(k + 1, G2)
            for corners, surf, ang, oc in G.candidates(hx + infl, hy + infl, h, box["rim"], n=12):
                if extra and min(abs((corners - box["c"]) @ box["u"]).max() - box["hu"], 0) < -0.03 and \
                        min(abs((corners - box["c"]) @ box["v"]).max() - box["hv"], 0) < -0.03:
                    continue                                          # a stacking target: in a corner
                G2 = G.copy(); G2.add(corners, surf + h)
                sol[nm] = (oc, ang, corners, surf + h)
                if rec(k + 1, G2):
                    return True
                sol.pop(nm, None)
            return False
        ok = False
        for infl in (0.004, 0.002):
            sol.clear()
            ok = rec(0, G0)
            if ok:
                break
        self.event("packing plan", ok=ok, **{k: [np.round(v[0], 3).tolist(), round(math.degrees(v[1]))] for k, v in sol.items()} if ok else {})
        return {k: (v[0], v[1]) for k, v in sol.items()} if ok else {}

    def rest_fits(self, box, i, cand, H):
        """Placing the held object at cand, could every object a later step puts into the box still go in?"""
        later = [self.steps[j][1] for j in range(i + 1, len(self.steps)) if self.steps[j][0] == "in"]
        later = [nm for nm in later if any(same_object(nm, k) for k in self.sizes)]
        if not later:
            return True
        G = Grid(box, box["inside"][::2])
        G.add(cand["corners"], cand["surf"] + H["h"])
        for nm in later:
            hx, hy, h = next(v for k, v in self.sizes.items() if same_object(nm, k))
            r = G.fits(hx, hy, h, box["rim"])
            if r is None:
                return False
            G.add(*r)
        return True

    # -- where things are
    def tracked(self, i):
        """The point of step i, or where the program put that object if an earlier step moved it: the same name, or
        Astra's point within 4 cm of where an earlier step picked something up (it marked it where it was)."""
        name = self.steps[i][1]; p = self.per.seeds[i]["p"]
        for nm, X in self.where.items():
            if same_object(nm, name):
                return X, True
        for j in range(i):
            if self.steps[j][0] == "pick" and self.per.seeds[j] is not None and np.linalg.norm(self.per.seeds[j]["p"][:2] - p[:2]) < 0.04:
                nm = self.steps[j][1]
                if nm in self.where:
                    return self.where[nm], True
        return p, False

    def look_box(self, seed, h=0.36):
        S = self.S
        y0 = self.default_yaw(seed[:2])
        X = None
        groups = ((y0, y0 + math.pi), (y0 + math.pi / 2, y0 - math.pi / 2))
        if self.WIDE_YAWS:                                            # (all 8 at once, the hand's present angle among
            groups = (tuple(self.look_yaws(seed[:2])),)               # them: 10-30, the looks before and after the lid
        for hh in (h, h - 0.04, h - 0.08, h + 0.04):                 # turned the wrist 60-75 deg each way)
            for pair in groups:                                       # a look pose the arm reaches: of y0 / y0 + 180 deg
                                                                      # (then +-90 deg) the one nearest
                best = None                                           # the arm's joints (10-29: the first reachable one
                for yy in pair:                                       # turned the real wrist half a turn from READY)
                    R_ = B.down(yy); X_ = self.look_tcp(np.r_[seed[:2], 0.03], R_, hh, band=False)   # (the box clears the band)
                    q_, e_ = S.ik(X_, R_, S.q_cmd)
                    if e_ < 0.002:
                        sc = np.abs(q_ - S.q_cmd).max() + 2 * (np.minimum(q_ - S.lo, S.hi - q_).min() < 0.15)
                        if best is None or sc < best[0]:
                            best = (sc, X_, R_)
                if best is not None:
                    _, X, R = best; break
            if X is not None:
                break
        if X is None:
            raise T.Unreachable("no reachable look into the box")
        self.transit(X[:2], R)
        S.move(X, R, S.g_cmd, 1.0, "look into the box")
        S.hold(0.2)
        obs = self.per.wrist()
        if os.environ.get("DEBUG_BOX"):
            print(f"look_box at t={S.d.time:.2f}: true lid {math.degrees(S.d.qpos[S.lid_q]):.1f} deg, box at {np.round(S.pose('box_white')[0], 3)}", flush=True)
        if os.environ.get("DEBUG_BOX"):
            np.savez(os.environ["DEBUG_BOX"], depth=obs["depth"], R=obs["R"], eye=obs["eye"], K=np.array(obs["K"]), tcp=S.tcp_now(), q=S.d.qpos[S.qadr])
        box = refine_box(obs, seed, zmax=S.tcp_now()[2] - T.FINGERTIP - 0.01)
        box["zmax"] = S.tcp_now()[2] - T.FINGERTIP - 0.01
        if box["lid"] is not None:
            self.last_lid = dict(box["lid"], box_c=box["c"].copy())
        self.box = box
        return box, obs

    def survey(self, i):
        sk = self.steps[i][0]
        if sk == "in":
            seed = self.box["c"] if self.box is not None else self.per.seeds[i]["p"]
            box, obs = self.look_box(np.r_[seed[:2], 0.0])
            if np.linalg.norm(box["c"] - seed[:2]) > 0.025:            # looked at off-centre (Astra's point): the box
                box, obs = self.look_box(np.r_[box["c"], 0.0])         # comes out up to 2 cm big; again, over its middle
            self.targets[i] = (box, obs)
            self.event("box surveyed", rim_mm=box["rim"] * 1000, size_mm=[box["hu"] * 2000, box["hv"] * 2000],
                       lid=None if box["lid"] is None else math.degrees(box["lid"]["th"]), inside_pts=len(box["inside"]))
        elif sk == "on":
            X, moved = self.tracked(i)
            S, per = self.S, self.per
            y0 = self.default_yaw(X[:2])
            R = B.down(self.best_yaw(np.r_[X[:2], X[2] + 0.13], (y0, y0 + math.pi)))
            Xl = self.look_tcp(X, R, X[2] + self.look_height(0.05))
            if moved and self.box is not None:                        # in the box: the wrist camera right over it, on
                best = None                                           # the box's middle side (looking from over a
                for yy in (y0, y0 + math.pi / 2, y0 + math.pi, y0 - math.pi / 2):   # wall, the wall hides its
                    R_ = B.down(yy); X_ = self.look_tcp(np.r_[X[:2], X[2]], R_, X[2] + 0.20)   # near edge: its
                    if S.ik(X_, R_, S.q_cmd)[1] > 0.002:              # centre comes out cm off)
                        continue
                    cam = X_ + R_ @ self.cam_pos
                    dist = float(np.linalg.norm(cam[:2] - self.box["c"]))
                    if best is None or dist < best[0]:
                        best = (dist, R_, X_)
                if best is not None:
                    R, Xl = best[1], best[2]
            self.transit(Xl[:2], R)
            S.move(Xl, R, B.OPEN_W, 1.5, "survey the target")
            S.hold(0.2)
            obs = per.wrist()
            if moved and self.box is not None:                        # in the box: its walls (taller, touching) would
                P_, ok_ = F.world(obs)                                # join it: only the inside's points
                rel = P_[..., :2] - self.box["c"]
                inside = (np.abs(rel @ self.box["u"]) < self.box["hu"] - 0.008) & (np.abs(rel @ self.box["v"]) < self.box["hv"] - 0.008)
                obs = dict(obs, depth=np.where(inside, obs["depth"], np.inf).astype(np.float32))   # (the measured box, its
            zmax = X[2] + 0.06                                        # walls spread 4 px: their inner
            if moved and self.box is not None and X[2] < self.box["rim"] - 0.02:   # faces would join a target in a
                zmax = min(zmax, self.box["rim"] - 0.01)              # corner; nor anything as high as the rim)
            g = F.refine_object(obs, X, zmax=zmax, ray=None if moved else per.seeds[i].get("ray"))
            self.targets[i] = (np.r_[g["c"], g["top"]], obs, S.tcp_now()[2] - T.FINGERTIP - 0.01)
            self.event("target surveyed", moved=moved, err_mm=float(np.linalg.norm(g["c"] - X[:2]) * 1000))

    # -- skills
    def f_pick(self, i):
        self.transit(self.per.seeds[i]["p"][:2])
        r = super().f_pick(i)
        if r != "ok":
            return r
        g = getattr(self, "last_g", None) or _LAST["g"]; H = self.held   # (the look f_pick grasped by)
        R = H["R"]; xa, ya = R[:2, 0], R[:2, 1]
        tcp = self.S.tcp_now()
        Qr = g["Q"][:, :2] - g["c"]                                    # footprint in the hand frame: percentile extents
        hx_ = np.percentile(Qr @ xa, [2, 98]); hy_ = np.percentile(Qr @ ya, [2, 98])
        z_grip = max(g["table"] + T.FINGERTIP + 0.005, g["top"] - 0.010)   # (as f_pick): the held bottom from the grasp
        if abs(H["bottom_off"] - (g["table"] - z_grip)) > 0.008 or np.linalg.norm(H["off"][:2]) > 0.015:
            self.event("in-hand look implausible: grasp geometry used", bottom_off_mm=H["bottom_off"] * 1000, off_mm=(H["off"][:2] * 1000).tolist())
            H["bottom_off"] = g["table"] - z_grip; H["off"] = np.zeros(3)
        H.update(hx=float(np.ptp(hx_) / 2), hy=float(np.ptp(hy_) / 2), cx=float((hx_.max() + hx_.min()) / 2),
                 cy=float((hy_.max() + hy_.min()) / 2), grip=float(-H["bottom_off"]), name=self.steps[i][1],
                 round=len(g["dirs"]) > 90, top_h=float(g["top"] - g["table"]))
        H["h"] = H["top_h"]
        return "ok"

    def find_strip(self, i, spot, avoid, flap, n=40):
        """A place (reachable) for the last long object a later step puts in the box, given spot's box. -> dict|None"""
        later = [self.steps[j - 1][1] for j in range(i + 1, len(self.steps)) if self.steps[j][0] == "in" and self.steps[j - 1][0] == "pick"]
        longs = [(nm, v) for nm in later for k, v in self.sizes.items() if same_object(nm, k) and max(v[0], v[1]) > 0.06]
        if not longs:
            return None
        nm, (hx, hy, h) = longs[-1]
        b = spot.b
        virt = dict(hx=hx, hy=hy, h=h, cx=0.0, cy=0.0, grip=0.02, width=2 * min(hx, hy), name=nm)
        for c_ in spot.drop(virt, avoid=avoid, flap_side=flap, hand=False)[:n]:
            c2 = dict(c_, z=b["rim"] + 0.006 + T.FINGERTIP)
            if self.arm_ok(c2, virt):
                ya = np.array([math.cos(c_["a"]), math.sin(c_["a"])]); xa = np.array([-ya[1], ya[0]])
                return dict(name=nm, corners=c_["corners"], h=h, oc=c_["oc"], xa=xa, ya=ya, a=c_["a"])
        return None

    def keep_strip(self, i, spot, avoid, flap):
        """The last long object a later step puts in the box (the screwdriver: it only fits across a diagonal): a place
        for it, kept from the first box survey on (what goes under it must leave it room under the lid), re-chosen if
        it no longer fits. -> dict(corners, h) or None."""
        later = [self.steps[j - 1][1] for j in range(i + 1, len(self.steps)) if self.steps[j][0] == "in" and self.steps[j - 1][0] == "pick"]
        longs = [(nm, v) for nm in later for k, v in self.sizes.items() if same_object(nm, k) and max(v[0], v[1]) > 0.06]
        if not longs:
            return None
        nm, (hx, hy, h) = longs[-1]
        b = spot.b
        st = getattr(self, "strip", None)
        if st is not None and st["name"] == nm:                       # still free enough?
            I = b["inside"]; rel = I[:, :2] - st["oc"]
            under = (np.abs(rel @ st["xa"]) < hx + 0.004) & (np.abs(rel @ st["ya"]) < hy + 0.004)
            surf = max(float(np.percentile(I[under, 2], 98)) if under.sum() > 10 else b["floor"], b["floor"])
            if surf + h <= b["rim"] - LID_CLEAR:
                return st
        virt = dict(hx=hx, hy=hy, h=h, cx=0.0, cy=0.0, grip=0.02, width=2 * min(hx, hy), name=nm)
        for c_ in spot.drop(virt, avoid=avoid, flap_side=flap, hand=False)[:40]:
            c2 = dict(c_, z=b["rim"] + 0.006 + T.FINGERTIP)
            if self.arm_ok(c2, virt):                                  # (the arm can put it there)
                ya = np.array([math.cos(c_["a"]), math.sin(c_["a"])]); xa = np.array([-ya[1], ya[0]])
                self.strip = dict(name=nm, corners=c_["corners"], h=h, oc=c_["oc"], xa=xa, ya=ya)
                self.event("kept a place for the " + nm, oc_box_mm=[round(float((c_["oc"] - b["c"]) @ b["u"]) * 1000), round(float((c_["oc"] - b["c"]) @ b["v"]) * 1000)],
                           a_deg=round(math.degrees(c_["a"] - math.atan2(b["u"][1], b["u"][0]))) % 180)
                return self.strip
        self.strip = None
        return None

    def arm_touches(self, q):
        """At joint angles q, does the left arm touch the right arm (a drop spot can need the elbow over there: the arm
        is then blocked and drags the hand off its line)? Contacts already there at the home pose do not count."""
        S = self.S; k_ = S.k
        def touches(qq):
            k_.qpos[:] = S.d.qpos; k_.qpos[S.qadr] = qq
            S.mj.mj_forward(S.m, k_)
            out = set()
            for c in k_.contact[:k_.ncon]:
                n1, n2 = S.m.body(S.m.geom_bodyid[c.geom1]).name, S.m.body(S.m.geom_bodyid[c.geom2]).name
                if n1.startswith("L_") ^ n2.startswith("L_"):
                    other, mine = (n2, n1) if n1.startswith("L_") else (n1, n2)
                    if other.startswith("R_"):
                        out.add((mine, other))
            return out
        if not hasattr(self, "_rest_touch"):
            self._rest_touch = touches(S.q_home)
        return bool(touches(q) - self._rest_touch)

    def arm_ok(self, c_, H, spot=None):
        """Inverse kinematics over the spot (carry height) and at the release, from the current arm: both reached,
        no big joint change on the way down (a configuration flip)."""
        S = self.S
        y_now = math.atan2(S.R_cmd[1, 1], S.R_cmd[0, 1])
        wrap = lambda x: (x + math.pi) % (2 * math.pi) - math.pi
        for aa in sorted((c_["a"], c_["a"] + math.pi), key=lambda x: abs(wrap(x - y_now))):   # the smaller turn first
            R = B.down(aa)
            tcp = c_["oc"] - H["cx"] * R[:2, 0] - H["cy"] * R[:2, 1]
            if spot is not None and abs(wrap(aa - c_["a"])) > 1.0 and \
                    spot.fingers_hit(tcp, aa, c_["z"], min(B.OPEN_W, H["width"] / 2 + 0.006)) > 3:
                continue                                              # turned round, the hand sits elsewhere: recheck
            q1, e1 = S.ik(np.r_[tcp, self.safe_z()], R, S.q_home if HOME_SEED else S.q_cmd)   # (transits go to the
            if e1 > 0.002:                                                # home-seeded configuration over the spot)
                q1, e1 = S.ik(np.r_[tcp, self.safe_z()], R, S.q_cmd)
            if e1 > 0.002 or self.arm_touches(q1):
                continue
            q, okp = q1, True
            for z_ in np.linspace(self.safe_z(), c_["z"], 7)[1:]:          # down in steps: no jump (a flip), not into
                qn, e_ = S.ik(np.r_[tcp, z_], R, q)                          # the other arm
                if e_ > 0.002 or np.abs(qn - q).max() > 0.35 or np.minimum(qn - S.lo, S.hi - qn).min() < 0.03 or self.arm_touches(qn):
                    okp = False; break
                q = qn
            if not okp:
                continue
            c_["a"] = aa; c_["q1"] = q1                               # (the carry goes to this configuration)
            return True
        return False

    def turn_to(self, a, grip):
        """Turn the hand (pointing down) in place to closing-axis angle a, about the vertical, <= 60 deg per move
        (a slerp near 180 deg has no defined axis: it may tilt the hand and fling what it holds)."""
        S = self.S
        y0 = math.atan2(S.R_cmd[1, 1], S.R_cmd[0, 1])
        dy = (a - y0 + math.pi) % (2 * math.pi) - math.pi
        n = max(1, int(math.ceil(abs(dy) / math.radians(60))))
        if os.environ.get("DEBUG_TURN") and self.held is not None:
            print(f"turn_to: y0 {math.degrees(y0):.1f} -> {math.degrees(a):.1f} (dy {math.degrees(dy):.1f}, {n} moves) q {np.round(S.d.qpos[S.qadr], 2)}", flush=True)
        for k in range(1, n + 1):
            try:
                S.move(S.p_cmd, B.down(y0 + dy * k / n), grip, max(0.5, 1.6 * abs(dy) / n), "turn the hand" if k == 1 else None)
            except T.Unreachable:                                     # (the wrist at a configuration change: joint space)
                self.joint_move(S.p_cmd.copy(), B.down(y0 + dy * k / n), 1.5)
            if os.environ.get("DEBUG_TURN") and self.held is not None:
                ob = S.ob.get(self.held.get("k") or "", None)
                print(f"   after move {k}: tcp {np.round(S.tcp_now(), 3)} fingers {np.round([S.d.qpos[q] for q in S.fing], 4)} q {np.round(S.d.qpos[S.qadr], 2)}", flush=True)

    def _turn_hand(self, a, exact=False):
        """Turn the held object with the hand to closing-axis angle a (or a + pi: the arm's choice)."""
        S, H = self.S, self.held
        X = S.p_cmd.copy()
        Rn = B.down(a if exact else self.best_yaw(X, (a, a + math.pi)))
        off_local = H["R"].T @ np.r_[H["off"][:2], 0.0]
        H.update(R=Rn, off=np.r_[(Rn @ off_local)[:2], 0.0])          # (the object turns with the hand)
        return Rn

    def f_in(self, i):
        S, H = self.S, self.held
        box, obs = self.targets[i]
        pick_i = max(j for j in range(i) if self.steps[j][0] == "pick")
        later_on = [j for j in range(i + 1, len(self.steps)) if self.steps[j][0] == "on" and (
            same_object(self.steps[j][1], H["name"]) or (self.per.seeds[j] is not None and self.per.seeds[pick_i] is not None and
                                                         np.linalg.norm(self.per.seeds[j]["p"][:2] - self.per.seeds[pick_i]["p"][:2]) < 0.04))]
        reserve = None
        for nm, X in self.where.items():
            if any(self.steps[j][0] == "on" and same_object(self.steps[j][1], nm) for j in range(i + 1, len(self.steps))):
                reserve = (X[:2], 0.05)
        flap = None
        if box["lid"] is not None:                                    # the wall opposite the hinge
            flap = (box["lid"]["n_in"], box["hu"] if abs(box["lid"]["n_in"] @ box["u"]) > 0.7 else box["hv"])
        spot = Spot(box, obs, box["zmax"])
        cands = []
        if self.pack is None:                                         # the packing plan, once (first box survey)
            self.pack = self.plan_packing(box) if self.sizes and os.environ.get("BOX_PACK_PLAN", "0") == "1" else {}
        pl = next((v for k, v in self.pack.items() if same_object(H["name"], k)), None)
        if pl is not None:                                            # its planned spot: the hand angle that lays the
            oc0, ang = pl                                             # object's long side along the planned one
            a_hand = ang + (math.pi / 2 if H["hx"] >= H["hy"] else 0.0)
            cands = spot.choose(H, flap_side=flap, around=(oc0, a_hand), margin=0.005)
            self.event("planned spot", found=bool(cands))
        why = {}
        if not cands and DROP and later_on and not self.where:         # the stacking target, first into the empty box:
            on_h = [v[2] for j in later_on for k, v in self.sizes.items() if same_object(self.steps[j - 1][1], k)]
            cands = spot.drop(H, flap_side=flap, why=why, low_first=False, hinge_margin=0.018,   # into a corner (the middle free for
                              h_final=H["h"] + (max(on_h) if on_h else 0.025))
            self.event("target dropped into a corner", n=len(cands))  # the long things)
        if not cands and DROP and not later_on:                       # relaxed: drop it in anywhere it fits under the
            avoid = []                                                # lid, clear of the stacking target / the stack
            for nm, X in self.where.items():
                target_ = any(s_ == "on" and same_object(n_, nm) for s_, n_ in self.steps)
                stacked = any(self.steps[j][0] == "on" and same_object(self.steps[j - 1][1], nm) for j in range(1, len(self.steps)))
                if target_ or stacked or (reserve is not None and np.allclose(X[:2], reserve[0])):
                    avoid.append((X[:2], 0.045))
            strip = self.keep_strip(i, spot, avoid, flap)
            cands = spot.drop(H, avoid=avoid, flap_side=flap, why=why, strip=strip)
            if not cands and strip is not None:                       # no room around the kept place: anywhere, but
                free = spot.drop(H, avoid=avoid, flap_side=flap, why=why)   # only where the last long thing still fits
                kept, n_try = [], 0                                   # afterwards (it put there, as points)
                for c_ in free:
                    if n_try >= 15 or len(kept) >= 4:
                        break
                    n_try += 1
                    if self.find_strip(i, spot.with_placed(c_["corners"], c_["surf"] + H["h"]), avoid, flap) is not None:
                        kept.append(c_)
                self.event("lookahead for the last long thing", tried=n_try, kept=len(kept))
                cands = kept or free
        if not cands:
            cands = spot.choose(H, reserve=reserve, target=bool(later_on), flap_side=flap)
        if not cands:
            cands = spot.choose(H, reserve=reserve, target=bool(later_on), flap_side=flap, margin=0.004)
        if not cands and DROP and later_on:                           # (a stacking target the careful way could not
            cands = spot.drop(H, flap_side=flap, why=why)             # place: dropped, on the floor if it can)
        if why:
            self.event("drop rejections", n_cands=len(cands), **why)
        if os.environ.get("DEBUG_SPOT"):
            print(f"spot for {H['name']}: {len(cands)} candidates; best:", [(round(c_['score'], 3), c_['mode'], c_['terms']) for c_ in cands[:6]], flush=True)
            for corner_test in ([(-1, -1)], [(1, 1)]):
                pass
        if self.WIDE_YAWS and H.get("round") and cands:                # a round thing: every hand angle has the same
            yn = math.atan2(S.R_cmd[1, 1], S.R_cmd[0, 1])             # footprint, so the scores tie: the angle
            turn = lambda c_: abs((c_["a"] - yn + math.pi / 2) % math.pi - math.pi / 2)   # nearest the hand's (mod
            cands = sorted(cands, key=lambda c_: c_["score"] + 0.05 * turn(c_))          # 180 deg) first (10-30:
        sp = None                                                     # a 120 deg turn for a tape)
        n_look = 0
        for c_ in cands[:80]:                                         # the best spot the arm can do (the hand angle
            if not self.arm_ok(c_, H, spot):                          # reachable over it and down to it, no flip)
                continue                                              # that leaves room for what comes later
            if n_look < 25 and not self.rest_fits(box, i, c_, H):
                n_look += 1
                continue
            sp = c_; break
        if sp is not None and n_look:
            self.event("lookahead skipped spots", n=n_look)
        if sp is None and LOOSE:
            sp = self.loose_spot(box, H)
            if sp is not None:
                self.event("no free spot: dropped loose over the middle", a_deg=round(math.degrees(sp["a"]), 1))
        if sp is None:
            return "no free spot in the box" if not cands else "no reachable free spot in the box"
        self.event("spot", mode=sp["mode"], surf_mm=(sp["surf"] - box["floor"]) * 1000, a_deg=math.degrees(sp["a"]),
                   target=bool(later_on), oc_box_mm=[round(float((sp["oc"] - box["c"]) @ box["u"]) * 1000), round(float((sp["oc"] - box["c"]) @ box["v"]) * 1000)])
        z = max(self.safe_z(), S.p_cmd[2])                           # up, turn slowly in place (a long held thing
        S.move(np.r_[S.p_cmd[:2], z], S.R_cmd, 0.0, 0.6, "up")        # swung round fast slips out), then across
        R = self._turn_hand(sp["a"], exact=True)                     # (the angle checked by arm_ok)
        self.turn_to(sp["a"], 0.0)
        tcp = sp["oc"] - H["cx"] * R[:2, 0] - H["cy"] * R[:2, 1]       # the object's centre on the spot (the arm may
        S.timeline.append((float(S.d.time), "phase", "carry over the box"))   # to the configuration arm_ok checked
        if sp.get("q1") is None or not self.joint_move(np.r_[tcp, self.safe_z()], R, check=True, q1=sp["q1"]):
            S.move(np.r_[tcp, z], R, 0.0, max(1.5, 6.0 * float(np.linalg.norm(tcp - S.p_cmd[:2]))), "carry over the box")
        S.move(np.r_[tcp, sp["z"]], R, 0.0, 1.2, "lower into the box")
        S.hold(0.1)
        g_rel = min(B.OPEN_W, H["width"] / 2 + 0.006)                   # just off it (wide open would hit things)
        if sp["z"] - T.FINGERTIP >= box["rim"]:                         # fingers over the rim: let go at once (opened
            g_rel = min(B.OPEN_W, H["width"] / 2 + 0.015)              # slowly, one finger lets go first: it swings
            S.move(S.p_cmd, R, g_rel, 0.15, "release")                 # into a wall; wide open: a finger hooks a wall)
        else:
            S.move(S.p_cmd, R, g_rel, 0.5, "release")
        S.hold(0.3)
        S.move(np.r_[tcp, max(box["rim"] + 0.03 + T.FINGERTIP, S.p_cmd[2])], R, g_rel, 0.6, "retreat")   # out of the box
        S.move(np.r_[tcp, box["rim"] + 0.12], R, B.OPEN_W, 0.5)                    # first, then open
        self.where[H["name"]] = np.r_[sp["oc"], sp["surf"] + H["h"]]
        self.held = None
        return "ok"

    def loose_spot(self, box, H):
        """(BOX_TASK=loose) No free spot: the held thing over the box's middle, its long side along a diagonal (the
        screwdriver, 187 mm, lies only across the 15.8 x 18.0 cm box), let go 12 mm over the rim onto what is there."""
        S = self.S
        dirs = [math.atan2(*(box["hu"] * box["u"] + su * box["hv"] * box["v"])[::-1]) for su in (1, -1)]
        dirs += [math.atan2(box["u"][1], box["u"][0]), math.atan2(box["v"][1], box["v"][0])]
        z = box["rim"] + 0.012 - H["bottom_off"]                      # (the TCP: its bottom 12 mm over the rim)
        best = None
        for ang in dirs:                                              # the hand angle laying its long side along ang
            a0 = ang + (math.pi / 2 if H["hx"] >= H["hy"] else 0.0)
            for a in (a0, a0 + math.pi):
                R = B.down(a)
                tcp = box["c"] - H["cx"] * R[:2, 0] - H["cy"] * R[:2, 1]
                q, e = S.ik(np.r_[tcp, z], R, S.q_cmd)
                if e > 0.002 or S.ik(np.r_[tcp, self.safe_z()], R, S.q_cmd)[1] > 0.002:
                    continue
                cost = float(np.abs(q - S.q_cmd).max())
                if best is None or cost < best[0]:
                    best = (cost, dict(oc=np.asarray(box["c"], float).copy(), a=a, z=z, surf=box["rim"], mode="loose", q1=None))
            if best is not None:                                      # (the first diagonal the arm reaches)
                break
        return None if best is None else best[1]

    def f_on(self, i):
        S, H = self.S, self.held
        X, obs, zmax = self.targets[i]
        if H.get("round"):                                            # a round object: turn the hand so that the
            sp = Spot(dict(c=X[:2], u=np.r_[1.0, 0], v=np.r_[0, 1.0], hu=0.2, hv=0.2, table=0.0), obs, zmax)   # fingers miss
            z_rel = X[2] + 0.001 + H["grip"]                          # the walls and the other things around
            q = H["width"] / 2 + 0.006
            best = min(np.radians(np.arange(0, 180, 15.0)), key=lambda a: sp.fingers_hit(X[:2] - 0.0 * np.r_[1, 0], a, z_rel, q))
            self.event("hand turned for the fingers", a_deg=math.degrees(best), hits=sp.fingers_hit(X[:2], best, z_rel, q))
            S.move(np.r_[S.p_cmd[:2], max(self.safe_z(), S.p_cmd[2])], S.R_cmd, 0.0, 0.5, "up")
            Rn = self._turn_hand(best)
            self.turn_to(math.atan2(Rn[1, 1], Rn[0, 1]), 0.0)
        self.transit(self._over(X, 0.06)[:2], H["R"])
        S.move(self._over(X, 0.06), H["R"], 0.0, 1.5, "carry over")
        S.move(self._over(X, 0.012), H["R"], 0.0, 0.8, "down to it")
        cmd = S.p_cmd.copy()
        for j in range(25):
            cmd[2] -= 0.001
            S.move(cmd, H["R"], 0.0, 0.08, "set down" if j == 0 else None); S.hold(0.04)
            if self.hand_force() > 1.5:
                self.event("contact", steps=j + 1); break
        rel = cmd + [0, 0, 0.001]
        S.move(rel, H["R"], 0.0, 0.15, "unload")
        g_rel = min(B.OPEN_W, H["width"] / 2 + 0.006)
        S.move(rel, H["R"], g_rel, 0.5, "release"); S.hold(0.3)
        S.move(rel + [0, 0, 0.07], H["R"], g_rel, 0.6, "retreat")              # out of the box first, then open
        S.move(rel + [0, 0, 0.10], H["R"], B.OPEN_W, 0.4); S.hold(0.3)
        self.where[H["name"]] = np.r_[X[:2], X[2] + H["h"]]
        self.held = None
        return "ok"

    def f_close(self, i):
        S = self.S
        seed = self.box["c"] if self.box is not None else self.per.seeds[i]["p"]
        S.move(S.p_cmd + [0, 0, 0.0], S.R_cmd, 0.0, 0.4, "close the gripper")
        box, _ = self.look_box(np.r_[seed[:2], 0.0], h=0.42)
        if np.linalg.norm(box["c"] - seed[:2]) > 0.025:               # looked at off its middle (close only, 10-29: the
            box, _ = self.look_box(np.r_[box["c"], 0.0], h=0.42)      # point was the open lid's): again, over the middle
        Ld = box["lid"]
        if Ld is None and self.last_lid is not None:                  # a look can miss it (edge-on): the last one seen,
            Ld = dict(self.last_lid)                                  # moved with the box
            Ld["mid"] = Ld["mid"] + np.r_[box["c"] - self.last_lid["box_c"], 0.0]
            self.event("lid not seen in this look: the last one seen used")
        if Ld is None:
            self.event("no open lid seen"); return "ok"
        self.event("lid", deg=math.degrees(Ld["th"]), len_mm=Ld["len"] * 1000, top_mm=Ld["top"] * 1000)
        mid, n_in, ez = Ld["mid"], Ld["n_in"], np.r_[0.0, 0, 1]
        n3 = np.r_[n_in, 0.0]
        th0 = Ld["th"]
        L_lid = (Ld["top"] - box["rim"]) / math.sin(th0) if th0 > math.radians(60) else Ld["len"]   # from its top edge
        L_lid = float(np.clip(L_lid, 0.85 * Ld["len"], 1.0 * Ld["len"]))  # (its top may be out of view: the box's depth)
        self.event("lid length", mm=L_lid * 1000)
        along = lambda th: n3 * math.cos(th) + ez * math.sin(th)
        outer = lambda th: -n3 * math.sin(th) + ez * math.cos(th)
        a_ax = math.atan2(Ld["axis"][1], Ld["axis"][0])
        R = B.down(self.best_yaw(mid + 0.17 * along(th0) + [0, 0, 0.1], (a_ax, a_ax + math.pi)))   # fingers along the hinge
        # A: the lid stands (past vertical): the finger's side on its back, 2 cm below its top edge, round to 75 deg
        rA = L_lid - 0.02
        side = lambda th, off=0.016: mid + rA * along(th) + off * outer(th) + [0, 0, T.FINGERTIP]
        self.transit(side(th0, 0.045)[:2], R)
        S.move(side(th0, 0.045) + [0, 0, 0.10], R, 0.0, 1.5, "behind the lid, above")
        S.move(side(th0, 0.045), R, 0.0, 0.8, "down behind the lid")
        S.move(side(th0), R, 0.0, 0.5, "touch the lid")
        thA = math.radians(75)
        for th in np.arange(th0, thA - 1e-6, -math.radians(4))[1:]:
            S.move(side(th), R, 0.0, 0.2, "tip the lid over" if abs(th - th0) < math.radians(5) else None)
        S.move(side(thA), R, 0.0, 0.2)
        S.move(side(thA, 0.05) + [0, 0, 0.05], R, 0.0, 0.6, "off the lid")
        # B: it leans over the box now: press down on its outer face (60 % of its length) along the arc to shut
        rB = 0.6 * L_lid
        press = lambda th: mid + rB * along(th) + [0, 0, 0.004 / max(math.cos(th), 0.3) + T.FINGERTIP]
        th1 = math.radians(max(math.degrees(thA) - 6, 60))
        S.move(press(th1) + [0, 0, 0.05], R, 0.0, 0.8, "above the lid")
        S.move(press(th1), R, 0.0, 0.5, "onto the lid")
        for th in np.arange(th1, math.radians(-3), -math.radians(4))[1:]:
            S.move(press(th), R, 0.0, 0.2, "press the lid down" if abs(th - th1) < math.radians(5) else None)
        S.move(press(math.radians(-3)), R, 0.0, 0.3, "press it shut")
        S.hold(0.2)
        S.move(S.p_cmd + [0, 0, 0.12], R, 0.0, 0.8, "up")
        try:
            box2, _ = self.look_box(np.r_[box["c"], 0.0], h=0.40)
        except F.Unseen:                                              # no open box there any more: the lid lies flat on
            self.event("after closing: no open box seen (the lid flat on it)")   # it (10-29, real: the closed box was
            return "ok"                                               # not found as a box: "no box at the point")
        self.box = box2
        self.event("after closing", lid=None if box2["lid"] is None else math.degrees(box2["lid"]["th"]), above_rim_pts=box2["above"],
                   standing_pts=box2["standing"])
        if box2["lid"] is not None or box2["above"] > 400 or box2["standing"] > 60:
            return "the lid is not shut"
        return "ok"

    def f_push(self, i):
        S = self.S
        seed = self.box["c"] if self.box is not None else self.per.seeds[i]["p"]
        box, _ = self.look_box(np.r_[seed[:2], 0.0], h=0.40)
        dvec = box["c"] - S.base[:2]; dvec /= np.linalg.norm(dvec)
        # push square to the box's side that faces the arm most (the box slides straight)
        dirs = [(box["u"], box["hu"]), (-box["u"], box["hu"]), (box["v"], box["hv"]), (-box["v"], box["hv"])]
        pdir, half = max(dirs, key=lambda x: float(x[0] @ dvec))
        dvec = pdir
        contact = box["c"] - pdir * half
        z = box["table"] + 0.025 + T.FINGERTIP                       # fingertips 2.5 cm up the wall
        start = contact - dvec * (0.013 + 0.045)
        a_p = math.atan2(dvec[1], dvec[0])
        PUSH = 0.15                                                   # (the box turns a little: it moves ~2/3 of it)
        def feasible(p0, p1):
            """Hand angles whose straight push p0 -> p1 the arm can follow (no configuration change on the way: the
            line would curve), the best first."""
            out = []
            for aa in (a_p + math.pi / 2, a_p - math.pi / 2, a_p, a_p + math.pi):
                Rt = B.down(aa)
                q, e = S.ik(np.r_[p0, z], Rt, S.q_cmd)
                okp = e < 0.002
                for f_ in np.linspace(0, 1, 13)[1:]:
                    if not okp:
                        break
                    qn, e = S.ik(np.r_[p0 + (p1 - p0) * f_, z], Rt, q)
                    okp = e < 0.002 and np.abs(qn - q).max() < 0.12
                    q = qn
                if okp:
                    out.append(Rt)
            return out
        Rs = feasible(start, contact + dvec * PUSH)
        if not Rs:
            return "no hand angle for a straight push"
        R = Rs[0]
        self.transit(start, R)
        S.move(np.r_[start, 0.16], R, 0.0, 1.5, "in front of the box")
        S.move(np.r_[start, z], R, 0.0, 0.8, "down")
        S.move(np.r_[contact - dvec * 0.012, z], R, 0.0, 0.6, "touch the box")
        S.straight = True                                             # (no joint-space fallback while pushing)
        end = contact + dvec * PUSH
        try:
            for attempt in range(3):
                try:
                    S.move(np.r_[end, z], R, 0.0, 3.5, "push it away" if attempt == 0 else "push on")
                    break
                except T.Unreachable:                                 # stopped half way (a configuration change):
                    here = S.tcp_now()[:2]                            # back off, the rest with another hand angle
                    S.straight = False
                    S.move(np.r_[here - dvec * 0.03, z + 0.06], S.R_cmd, 0.0, 0.6, "back off, turn the hand")
                    Rs = [R_ for R_ in feasible(here - dvec * 0.03, end) if not np.allclose(R_, R)]
                    if not Rs:
                        break
                    R = Rs[0]
                    S.move(np.r_[here - dvec * 0.03, z + 0.06], R, 0.0, 0.8)
                    S.move(np.r_[here - dvec * 0.03, z], R, 0.0, 0.5)
                    S.straight = True
        finally:
            S.straight = False
        S.move(S.p_cmd - np.r_[dvec * 0.05, 0], R, 0.0, 0.6, "back off")
        S.move(S.p_cmd + [0, 0, 0.15], R, 0.0, 0.8, "up")
        self.event("pushed", from_xy=box["c"].tolist(), dir=dvec.tolist())
        return "ok"


# ---------------------------------------------------------------- judging (true state)
def inside_box(S, k, slack=0.0):
    bp, bR = S.pose("box_white")
    p, _ = S.pose(k)
    if k == "screwdriver":
        p = p + S.d.xmat[S.ob[k]].reshape(3, 3) @ [-0.0075, 0, 0]
    rel = bR.T @ (p - bp)
    return bool(abs(rel[0]) < BOX_L / 2 - BOX_T + slack and abs(rel[1]) < BOX_W / 2 - BOX_T + slack and -0.01 < rel[2] < BOX_H)


def judge(S, prog, box0, pdir=None):
    res = {}
    res["inside"] = {k: inside_box(S, k) for k in PACK}
    pr, _ = S.pose("tape_red"); pb, _ = S.pose("hole_block")
    res["red_on_block"] = bool(np.linalg.norm(pr[:2] - pb[:2]) < 0.025 and 0.010 < pr[2] - pb[2] < 0.032)   # (it may sit tilted into a hole)
    res["lid_deg"] = round(math.degrees(S.d.qpos[S.lid_q]), 1)
    bp, bR = S.pose("box_white")
    dvec = np.asarray(pdir) if pdir is not None else box0[:2] - S.base[:2]; dvec = dvec / np.linalg.norm(dvec)
    res["pushed_mm"] = round(float((bp[:2] - box0[:2]) @ dvec) * 1000, 1)
    res["box_tilt_deg"] = round(math.degrees(math.acos(min(1.0, bR[2, 2]))), 1)
    first = {}
    for st, ins in prog.inside_log:
        for k in ins:
            first.setdefault(k, st)
    res["entered_at_step"] = first
    res["screwdriver_last"] = bool("screwdriver" in first and all(first["screwdriver"] >= v for v in first.values()))
    steps = [sk for sk, _ in T.TASKS[TASK]["steps"]]
    res["ok"] = bool(all(res["inside"].values()) and (res["red_on_block"] or "on" not in steps) and (res["lid_deg"] < 8 or "close" not in steps) and res["box_tilt_deg"] < 10
                     and (res["pushed_mm"] > 80 or "push" not in steps) and (res["screwdriver_last"] or "screwdriver" not in PACK))
    return res


# ---------------------------------------------------------------- runner
def run_job(job):
    seed, idx, out, save, gt = job
    L = sample_layout(seed, idx)
    if L is None:
        return dict(task=TASK, layout=idx, result="layout rejected")
    S = BoxSim(); apply_layout(S, L)
    off = [k for k in OBJ if S.pose(k)[0][2] < -0.01]                 # (settled: everything still on the table)
    if off:
        return dict(task=TASK, layout=idx, result="layout rejected", why=f"off the table: {off}")
    if os.environ.get("DEBUG_BOX"):
        print(f"after apply_layout t={S.d.time:.2f}: lid {math.degrees(S.d.qpos[S.lid_q]):.1f} deg", flush=True)
    tag = f"{TASK}_{idx:02d}_{'gt' if gt else 'astra'}"
    per = BoxPerception(S, np.random.default_rng([seed, idx, 5]), out, tag, gt=gt)
    prog = BoxProgram(S, per)
    box0 = S.pose("box_white")[0].copy()
    t0 = time.time()
    try:
        r = prog.run()
    except Exception as e:
        import traceback
        r = f"crash: {e!r}"[:200]; traceback.print_exc()
    wall = time.time() - t0
    ev = next((e for e in prog.events if e["what"] == "pushed"), None)
    res = judge(S, prog, np.r_[ev["from_xy"], 0] if ev else box0, ev["dir"] if ev else None)
    api = sum(x.get("s", 0) for a in per.astra_log for x in a["rounds"])
    row = dict(task=TASK, layout=idx, perception="gt" if gt else "astra", program=r, result="success" if res.pop("ok") else "fail",
               **res, sim_s=round(float(S.d.time), 1), wall_s=round(wall, 1), astra_s=round(api, 1), astra_calls=per.calls,
               astra_cost=round(per.cost, 3), events=prog.events, astra=per.astra_log, layout_spec=L)
    if save:
        np.savez_compressed(Path(out) / f"trace_{tag}.npz", t=np.array([x[0] for x in S.rec]), qpos=np.array([x[1] for x in S.rec]))
        json.dump(dict(row=row, layout=L, timeline=S.timeline), open(Path(out) / f"trace_{tag}.json", "w"),
                  default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    per.close()
    with open(Path(out) / "runs.jsonl", "a") as f:
        f.write(json.dumps(row, default=lambda o: o.tolist() if hasattr(o, "tolist") else (o if isinstance(o, (int, float, str)) else str(o))) + "\n")
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--layouts", type=int, default=3)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--perception", choices=["gt", "astra"], default="astra")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--save-traces", action="store_true")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    jobs = [(a.seed, i, str(a.out), a.save_traces, a.perception == "gt") for i in (a.only or range(a.layouts))]
    t0 = time.time(); rows = []
    with Pool(max(1, min(a.workers, len(jobs)))) as pool:
        for r in pool.imap_unordered(run_job, jobs):
            rows.append(r)
            print(f"layout {r['layout']} ({time.time() - t0:.0f}s): {r['result']} - {r.get('program')}\n   inside {r.get('inside')} "
                  f"red_on_block {r.get('red_on_block')} lid {r.get('lid_deg')} pushed {r.get('pushed_mm')} mm tilt {r.get('box_tilt_deg')} "
                  f"screwdriver_last {r.get('screwdriver_last')} | calls {r.get('astra_calls')} astra {r.get('astra_s')}s "
                  f"sim {r.get('sim_s')}s wall {r.get('wall_s')}s", flush=True)
    print(f"{sum(r['result'] == 'success' for r in rows)}/{len(rows)} success, Astra ${sum(r.get('astra_cost', 0) for r in rows):.2f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "layouts":
        out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
        for idx in range(int(sys.argv[3]) if len(sys.argv) > 3 else 3):
            t0 = time.time(); L = sample_layout(11, idx)
            print(idx, "rejected" if L is None else f"tries {L['tries']}, hinge {L['hinge']:+d}", f"{time.time() - t0:.0f}s", flush=True)
            if L is None:
                continue
            json.dump(L, open(out / f"layout_{idx}.json", "w"))
            S = BoxSim(); apply_layout(S, L)
            per = BoxPerception(S, np.random.default_rng(0), out, f"l{idx}")
            obs = per.observe("d415")
            cv2.imwrite(str(out / f"d415_{idx}.jpg"), cv2.cvtColor(obs["rgb"], cv2.COLOR_RGB2BGR))
    else:
        main()
