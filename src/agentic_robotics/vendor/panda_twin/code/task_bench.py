"""Task bench for the Panda twin: everyday tasks with the scanned objects, on randomized layouts, judged on the TRUE state.

    MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 ~/miniforge3/envs/teleavatar_client/bin/python -I code/task_bench.py \
        --out out/task_bench_gt --perception gt --layouts 3
    (astra perception needs code/composite_server.py on :8132 and $OPENAI_API_KEY)

Scene: model/twin_6508_tasks.xml (build_tasks_scene.py: the "all objects" scan + an articulated kettle). Per task and
layout the task's objects are moved to random places on the left arm's side (the other objects stay where they were
scanned: clutter), checked reachable (IK, no arm collision) and visible to the D415.
Tasks: see TASKS. The program is the same for every task, a list of steps:
  pick(obj)       grasp points (2 contacts) from the D415 -> above the object -> grasp points again from the wrist D415 ->
                  fingers closing across the contacts, TCP 10 mm under the object's top (fingertips >= 5 mm above the
                  table) -> close, wait for the gripper, retry 6 mm higher if it did not close on something -> lift
  on(target)      place point = the target's top centre (D415) -> hover -> wrist servo of the held object's top centre
                  over it, in 3D (gap = held bottom - target top), at 10 cm / 3 cm / 8 mm -> lower until contact -> open
  in(container)   place point = the opening's centre: the midpoint of its leftmost and rightmost rim points at the rim's
                  height (D415) -> carry with the held bottom 14 cm over the rim -> one wrist correction (same rim
                  points around the held object; ignored if > 8 cm) -> down to 15 mm above the rim (fingertips >= 15 mm
                  over it) -> open (drop in)
  peg(stand)      place point = the middle stick's tip -> hover -> wrist servo of the ring's centre over the tip at
                  3 cm / 8 mm -> lower until the ring is 2 cm past the tip (stop and re-servo if it hits the tip) -> open
  press(kettle)   press point = the lid button (D415, then wrist) -> closed fingers above it -> down 1 mm at a time until
                  the fingers feel the button (> 3 N) or 6 mm past its top -> up
Perception: gt = the true geometry; astra = Astra's marks (yellow crosses = grasp contacts / the held object's centre,
blue = place / press point) on the photoreal D415 and wrist images, lifted to 3D with the cameras' depth (stack_bench's
AstraPerception); calibrated cameras.
Output: runs.jsonl, summary.md, astra/*.jpg (marked images), trace_*.npz/json (--save-traces).
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

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_bench as B  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
B.MODEL = ROOT / "model/twin_6508_tasks.xml"
FINGERTIP = 0.0157                     # fingertips below the TCP
TASK_BOX = np.array([[x, y, z] for x in (-0.60, 0.02) for y in (-0.22, 0.48) for z in (0.0, 0.22)])   # D415 crop

# objects the tasks use: kind, sizes, and how Astra is told about them. z0 = body origin above the object's bottom.
OBJ = {
    "oct_blue":     dict(kind="ring", flats=0.040, hole=0.018, h=0.0295, z0=0.01475, desc="the small BLUE ring (a short octagonal block with a hole)"),
    "oct_orange":   dict(kind="ring", flats=0.060, hole=0.025, h=0.0295, z0=0.01475, desc="the ORANGE ring (a short octagonal block with a hole)"),
    "oct_red":      dict(kind="ring", flats=0.050, hole=0.020, h=0.0295, z0=0.01475, desc="the RED ring (a short octagonal block with a hole)"),
    "oct_green":    dict(kind="ring", flats=0.078, hole=0.022, h=0.0295, z0=0.01475, desc="the big GREEN ring (a short octagonal block with a hole)"),
    "cube_red":     dict(kind="cube", size=0.030, h=0.030, z0=0.015, desc="the small RED cube"),
    "cube_green":   dict(kind="cube", size=0.059, h=0.059, z0=0.0295, desc="the big GREEN cube"),
    "kcup":         dict(kind="kcup", r=0.025, h=0.045, z0=0.0, desc="the coffee pod (a small white cup with a pink lid)"),
    "eggplant":     dict(kind="eggplant", r=0.019, len=0.16, h=0.038, z0=0.0, desc="the purple toy eggplant"),
    "mug_white":    dict(kind="mug", r=0.0425, h=0.090, z0=0.0, desc="the WHITE mug"),
    "basket_small": dict(kind="basket", r=0.114, h=0.070, z0=0.0, desc="the SMALL woven basket"),
    "basket_big":   dict(kind="basket", r=0.133, h=0.095, z0=0.0, desc="the BIG woven basket"),
    "kettle":       dict(kind="kettle", r=0.070, h=0.177, z0=0.0, desc="the steel kettle"),
    "peg_stand":    dict(kind="peg_stand", h=0.173, z0=0.0, desc="the wooden stand with three vertical wooden sticks"),
}
FOOT = {"ring": lambda o: o["flats"] / 2 / math.cos(math.pi / 8), "cube": lambda o: o["size"] * 0.71, "kcup": lambda o: 0.028,
        "eggplant": lambda o: 0.085, "mug": lambda o: 0.075, "basket": lambda o: o["r"], "kettle": lambda o: 0.11,
        "peg_stand": lambda o: 0.15}
TASKS = {
    "kettle_open":        dict(text="Open the kettle's lid by pressing the lid-release button on top of its handle.",
                               move=["kettle"], steps=[("press", "kettle")]),
    "kcup_in_kettle":     dict(text="Open the kettle (press the lid-release button on top of its handle) and put the coffee pod inside the kettle.",
                               move=["kettle", "kcup"], steps=[("press", "kettle"), ("pick", "kcup"), ("in", "kettle")]),
    "blue_in_mug":        dict(text="Put the small blue ring inside the white mug.", move=["oct_blue", "mug_white"],
                               steps=[("pick", "oct_blue"), ("in", "mug_white")]),
    "stack_cubes":        dict(text="Stack the small red cube on top of the big green cube.", move=["cube_red", "cube_green"],
                               steps=[("pick", "cube_red"), ("on", "cube_green")]),
    "cube_in_basket":     dict(text="Put the big green cube into the small basket.", move=["cube_green", "basket_small"],
                               steps=[("pick", "cube_green"), ("in", "basket_small")]),
    "ring_on_peg":        dict(text="Put the orange ring onto the middle wooden stick of the stand.", move=["oct_orange", "peg_stand"],
                               steps=[("pick", "oct_orange"), ("peg", "peg_stand")]),
    "stack_rings":        dict(text="Stack the red ring centred on top of the green ring.", move=["oct_red", "oct_green"],
                               steps=[("pick", "oct_red"), ("on", "oct_green")]),
    "eggplant_in_basket": dict(text="Put the eggplant into the big basket.", move=["eggplant", "basket_big"],
                               steps=[("pick", "eggplant"), ("in", "basket_big")]),
}


class Unreachable(Exception):
    pass


def rotvec(R):
    """Rotation vector (axis * angle) of a rotation matrix."""
    c = np.clip((np.trace(R) - 1) / 2, -1, 1); a = math.acos(c)
    if a < 1e-9:
        return np.zeros(3)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if math.pi - a < 1e-4:                                    # 180 deg: axis from the symmetric part
        w, V = np.linalg.eigh((R + np.eye(3)) / 2); return V[:, -1] * a
    return v / (2 * math.sin(a)) * a


def yaw_of(R):
    return math.atan2(R[1, 0], R[0, 0])


# ---------------------------------------------------------------- simulation
class TaskSim(B.Sim):
    def __init__(self):
        super().__init__()
        m = self.m
        self.ob = {k: m.body("obj_" + k).id for k in OBJ}
        self.oq = {k: m.jnt_qposadr[m.body_jntadr[b]] for k, b in self.ob.items()}
        self.latch = m.eq("obj_kettle_latch").id
        self.btn_q = m.joint("obj_kettle_button_slide").qposadr[0]
        self.lid_q = m.joint("obj_kettle_lid_hinge").qposadr[0]
        self.fingers = {m.body(self.mj_name(n)).id for n in ("L_left_finger", "L_right_finger")}
        self.subtree = {k: {c for c in range(m.nbody) if self.root_of(c) == b} for k, b in self.ob.items()}

    def mj_name(self, n):
        return n

    def root_of(self, b):
        m = self.m
        while m.body_parentid[b] != 0:
            b = m.body_parentid[b]
        return b

    @property
    def latched(self):
        arr = self.d.eq_active if hasattr(self.d, "eq_active") else self.m.eq_active
        return bool(arr[self.latch])

    def set_latch(self, on):
        arr = self.d.eq_active if hasattr(self.d, "eq_active") else self.m.eq_active
        arr[self.latch] = int(on)

    def physics(self, n_frames=1):
        m, d, mj = self.m, self.d, self.mj
        for _ in range(n_frames):
            for _ in range(self.sub):
                if self.gravcomp:
                    d.qfrc_applied[self.arm_dofs] = d.qfrc_bias[self.arm_dofs]
                mj.mj_step(m, d)
                if self.latched and d.qpos[self.btn_q] < -0.0025:          # the button releases the lid
                    self.set_latch(False)
            self.rec.append((float(d.time), d.qpos.copy()))

    def move(self, target, R, grip, T, phase=None):
        """As Sim.move, but a move the arm cannot follow (IK off by > 5 mm, or a joint jumping > 0.2 rad in 1/30 s:
        the IK flipping configuration) is stopped where it is and reported, never executed."""
        if phase:
            self.timeline.append((float(self.d.time), "phase", phase))
        p0, g0, R0 = self.p_cmd.copy(), self.g_cmd, self.R_cmd.copy()
        target = np.asarray(target, float)
        rv = rotvec(R0.T @ R)                                  # orientation: slerp from the commanded one
        T = max(T, float(np.linalg.norm(rv)) * 1.0, float(np.linalg.norm(target - p0)) * 3.0)   # <= ~1.5 rad/s, 0.5 m/s peak
        n = max(int(round(T / B.DT)), 1)
        for i in range(1, n + 1):
            s = i / n; s = s * s * (3 - 2 * s)
            q, e = self.ik(p0 + (target - p0) * s, R0 @ B.small_rot(rv * s), self.q_cmd)
            if e > 0.005 or np.abs(q - self.q_cmd).max() > 0.15:
                self.p_cmd = self.d.site_xpos[self.tcp].copy(); self.hold(0.3)
                raise Unreachable(f"{phase or 'move'} to {np.round(target, 3).tolist()} (IK error {e * 1000:.0f} mm, joint step {np.abs(q - self.q_cmd).max():.2f} rad)")
            self.q_cmd = q
            self.d.ctrl[self.act] = q
            self.d.ctrl[self.grip] = g0 + (grip - g0) * min(1.0, 2 * s)
            self.physics()
        self.p_cmd, self.R_cmd, self.g_cmd = target.copy(), R, grip
        return 0.0

    def pose(self, k):
        b = self.ob[k]
        return self.d.xpos[b].copy(), self.d.xmat[b].reshape(3, 3).copy()

    def put(self, k, x, y, yaw):
        """Move an object (upright, resting) to x, y, yaw."""
        a = self.oq[k]
        self.d.qpos[a:a + 2] = [x, y]
        self.d.qpos[a + 3:a + 7] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
        dof = self.m.jnt_dofadr[self.m.body_jntadr[self.ob[k]]]
        self.d.qvel[dof:dof + 6] = 0

    def force(self, A, Bs):
        """Total normal force between two sets of bodies."""
        m, d, f6, tot = self.m, self.d, np.zeros(6), 0.0
        for i in range(d.ncon):
            c = d.contact[i]
            b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            if (b1 in A and b2 in Bs) or (b2 in A and b1 in Bs):
                self.mj.mj_contactForce(m, d, i, f6); tot += abs(f6[0])
        return tot


# ---------------------------------------------------------------- layouts
def sample_layout(task, seed, idx):
    rng = np.random.default_rng([seed, idx, sum(map(ord, task))])
    S = TaskSim(); m, d, mj = S.m, S.d, S.mj
    mj.mj_resetDataKeyframe(m, d, m.key("twin").id)
    key = d.qpos.copy()
    mv = TASKS[task]["move"]
    geoms_of = {k: [g for g in range(m.ngeom) if S.root_of(m.geom_bodyid[g]) == S.ob[k] and m.geom_contype[g]] for k in mv}
    table = m.geom("table_top").id
    for tries in range(600):
        d.qpos[:] = key
        place = {}
        for k in mv:
            fk = FOOT[OBJ[k]["kind"]](OBJ[k])
            for _ in range(200):
                p = rng.uniform([-0.58, -0.16], [-0.05, 0.44])
                r = np.linalg.norm(p - S.base[:2])
                if not 0.32 + fk * 0.3 < r < 0.64:
                    continue
                if any(np.linalg.norm(p - q[:2]) < fk + FOOT[OBJ[j]["kind"]](OBJ[j]) + 0.04 for j, q in place.items()):
                    continue
                place[k] = np.r_[p, rng.uniform(0, 2 * math.pi)]
                break
        if len(place) < len(mv):
            continue
        for k, (x, y, yaw) in place.items():
            S.put(k, x, y, yaw)
        margin0 = m.geom_margin.copy()
        for k in mv:
            m.geom_margin[geoms_of[k]] = 0.025
        mj.mj_forward(m, d)
        bad = False
        for i in range(d.ncon):
            c = d.contact[i]
            for k in mv:
                if c.geom1 in geoms_of[k] or c.geom2 in geoms_of[k]:
                    other = c.geom2 if c.geom1 in geoms_of[k] else c.geom1
                    if other != table and S.root_of(m.geom_bodyid[other]) != S.ob[k]:
                        bad = True
        m.geom_margin[:] = margin0
        if bad:
            continue
        mj.mj_forward(m, d)
        # reach: above each moved object (15 cm over its top) and near its top, no new arm contacts
        ok = True
        q_keep = d.qpos.copy()

        def arm_touches(k):
            """Contacts of the left arm (hand and open fingers too) with anything but itself, the table (fingertips)
            and the object it is reaching for."""
            out = set()
            for i in range(d.ncon):
                c = d.contact[i]
                b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
                n1, n2 = m.body(b1).name, m.body(b2).name
                if not (n1.startswith("L_") ^ n2.startswith("L_")):
                    continue
                other = b2 if n1.startswith("L_") else b1
                if other == 0 and "finger" in n1 + n2 or S.root_of(other) == S.ob[k]:
                    continue
                out.add((n1, n2))
            return out
        picked = [o for st, o in TASKS[task]["steps"] if st == "pick"]
        for k in mv:
            p, R = S.pose(k)
            top = p[2] - OBJ[k]["z0"] + OBJ[k]["h"]
            y0 = math.atan2(*(p[:2] - S.base[:2])[::-1]) + math.pi / 2
            rest = arm_touches(k)                                        # the base standing on the frame
            for z in ([top - 0.010, top + 0.03, top + 0.10, top + 0.18, 0.32] if k in picked else [top + dz for dz in (0.06, 0.12, 0.20, 0.26)]):
                for dy in (0, 45, -45, 90):                        # any grip angle (mod 180) the program may choose
                    q, e = S.ik(np.r_[p[:2], max(z, FINGERTIP + 0.005)], B.down(y0 + math.radians(dy)), S.q_home)
                    ok &= e < 0.002
                    if dy == 0:
                        d.qpos[S.qadr] = q; mj.mj_forward(m, d)
                        ok &= not (arm_touches(k) - rest)
        d.qpos[:] = q_keep; mj.mj_forward(m, d)
        if not ok:
            continue
        # visible to the D415: rays to 5 points on each moved object's top, >= 3 reach it first
        eye = d.cam_xpos[m.camera("d415").id].copy(); gid = np.zeros(1, np.int32)
        for k in mv:
            p, _ = S.pose(k); top = p[2] - OBJ[k]["z0"] + OBJ[k]["h"]; rr = 0.5 * FOOT[OBJ[k]["kind"]](OBJ[k])
            hits = 0
            for q in [np.r_[p[:2], top - 0.003]] + [np.r_[p[:2] + rr * np.array([math.cos(a), math.sin(a)]), top - 0.003] for a in np.arange(4) * math.pi / 2]:
                v = q - eye
                mj.mj_ray(m, d, eye, v / np.linalg.norm(v), None, 1, -1, gid)
                hits += gid[0] >= 0 and S.root_of(m.geom_bodyid[gid[0]]) == S.ob[k]
            ok &= hits >= 3
        if not ok:
            continue
        return dict(task=task, seed=seed, layout=idx, tries=tries, place={k: v.tolist() for k, v in place.items()})
    return None


def apply_layout(S, L):
    for k, (x, y, yaw) in L["place"].items():
        S.put(k, x, y, yaw)
    S.mj.mj_forward(S.m, S.d)
    for c in B.CAM:
        S.set_camera_error(c, [0, 0, 0], [0, 0, 0])
    S.d.ctrl[S.act] = S.q_home; S.d.ctrl[S.grip] = B.OPEN_W
    S.hold(0.6)
    S.rec.clear()
    S.sync_cmd(); S.p_cmd = S.tcp_now(); S.q_cmd = S.q_home.copy(); S.g_cmd = B.OPEN_W


# ---------------------------------------------------------------- perception: the true geometry
class GTPerception:
    """The points the program asks for, from the true state (the reference for the program and the physics)."""

    def __init__(self, S):
        self.S, self.log, self.calls, self.cost = S, [], 0, 0.0

    def grasp(self, k, cam):
        S = self.S; p, R = S.pose(k); o = OBJ[k]
        bottom = p[2] - o["z0"]; top = bottom + o["h"]
        y0 = math.atan2(*(p[:2] - S.base[:2])[::-1]) + math.pi / 2
        if o["kind"] in ("ring", "cube"):
            step = math.pi / 4 if o["kind"] == "ring" else math.pi / 2
            a = (o["flats"] if o["kind"] == "ring" else o["size"]) / 2
            y = yaw_of(R) + round((y0 - yaw_of(R)) / step) * step
            c = p[:2]
        elif o["kind"] == "kcup":
            a, y, c = o["r"] - 0.002, y0, p[:2]
        elif o["kind"] == "eggplant":                       # across the thick part, 3 cm from its middle
            a, y, c = o["r"], yaw_of(R) + math.pi / 2, p[:2] + R[:2, 0] * 0.03
        u = np.array([math.cos(y), math.sin(y)])
        return dict(c=[np.r_[c - a * u, top], np.r_[c + a * u, top]], top=top, table=bottom)

    def place(self, k, mode, cam):
        S = self.S; p, R = S.pose(k); o = OBJ[k]; bottom = p[2] - o["z0"]
        if mode == "on":
            return np.r_[p[:2], bottom + o["h"]]
        if mode == "in":
            rim = bottom + (o["h"] - 0.012 if o["kind"] == "kettle" else o["h"])
            return np.r_[p[:2], rim]
        if mode == "peg":
            g = S.m.geom("obj_peg_stand_peg1").id
            gp = S.d.geom_xpos[g]; return np.r_[gp[:2], gp[2] + S.m.geom_size[g][1]]
        if mode == "press":
            b = S.m.body("obj_kettle_button").id
            return S.d.xpos[b] + [0, 0, 0.004]

    def relative(self, held, k, mode, cam, held_h):
        """(held object's centre xy - the place point's xy, held bottom - place point z)."""
        hp, _ = self.S.pose(held); bottom = hp[2] - OBJ[held]["z0"]
        t = self.place(k, mode, cam)
        return np.r_[hp[:2] - t[:2], bottom - t[2]]


# ---------------------------------------------------------------- perception: Astra
GRASP_PROMPT = """{task}
This is {what}, {W}x{H} pixels; pixel coordinates are x from the left edge, y from the top edge.
The robot's gripper is a parallel-jaw gripper that grasps from above.
Mark the 2 points where the 2 fingertips should touch {desc} for a firm grasp: on OPPOSITE sides of it, facing each
other across its middle (for a block or ring: the middles of two opposite, parallel flat side faces, never corners;
for a cup: two opposite points of its rim; for a long object: across its thick middle part). Put each point on the top
edge of the object (the border of its top surface, as seen in the image). Other objects may look similar: only {desc}.
If it is not visible, give null.
Respond with this JSON object and nothing else:
{{"grasp": [[x, y], [x, y]], "reasoning": "<1-2 sentences>"}}"""
PLACE_PROMPT = """{task}
This is {what}, {W}x{H} pixels; pixel coordinates are x from the left edge, y from the top edge.
Mark {items}. If it is not visible, give null.
Respond with this JSON object and nothing else:
{{{keys} "reasoning": "<1-2 sentences>"}}"""
PLACE_ITEMS = {
    "on": ('the place point: the centre of the top face of {desc} - where the centre of the held object has to be put',
           '"place": [x, y],'),
    "in": ('two points on the top edge of the rim of {desc} (its opening, where the held object will be dropped in): '
           'rim = [the LEFTMOST point of the rim, the RIGHTMOST point of the rim] as seen in this image',
           '"rim": [[x, y], [x, y]],'),
    "peg": ('the place point: the top tip of the MIDDLE one of the three vertical wooden sticks of {desc}',
            '"place": [x, y],'),
    "press": ('the press point: the centre of the small round lid-release button on top of the handle of {desc}',
              '"place": [x, y],'),
}
SERVO_PROMPT = """{task}
This is the wrist camera on the gripper, looking straight down from beside the hand (the fingers are out of view),
{W}x{H} pixels; pixel coordinates are x from
the left edge, y from the top edge. The gripper holds {held} above {desc}.
Mark, as precisely as you can:
- held: the centre of the top face of the held object (if it is a ring, the centre of its hole).
- place: {place}. If the held object hides part of it, infer it from what is visible.
If something is not visible, give null for its point.
Respond with this JSON object and nothing else:
{{"held": [x, y], "place": [x, y], "reasoning": "<1-2 sentences>"}}"""
SERVO_IN = """{task}
This is the wrist camera on the gripper, looking straight down from beside the hand (the fingers are out of view),
{W}x{H} pixels; pixel coordinates are x from
the left edge, y from the top edge. The gripper holds {held} above {desc}.
Mark, as precisely as you can:
- held: the centre of the top face of the held object (if it is a ring, the centre of its hole).
- rim: [the LEFTMOST point, the RIGHTMOST point] of the top edge of the rim of {desc}, as seen in this image.
If something is not visible, give null for its point(s).
Respond with this JSON object and nothing else:
{{"held": [x, y], "rim": [[x, y], [x, y]], "reasoning": "<1-2 sentences>"}}"""
SERVO_PLACE = {"on": "the centre of the top face of {desc} - where the held object's centre has to go",
               "peg": "the top tip of the wooden stick right under the held ring (the middle stick of {desc})",
               "in": "the centre of the opening of {desc}"}


class AstraTaskPerception(B.AstraPerception):
    COLS = dict(B.AstraPerception.COLS, rim=(0, 200, 255))

    def __init__(self, S, rng, out, tag, task):
        super().__init__(S, "cam", rng, out, tag)
        self.task_text = f"Task: {TASKS[task]['text']}"
        self.place_z = {}

    def frame(self, cam):
        obs = self.observe(cam)
        box = self.roi(obs, TASK_BOX) if cam == "d415" else None
        img = self.full(obs, box)
        what = ("a view of the table from a fixed camera" if cam == "d415"
                else "the wrist camera on the gripper, looking straight down from beside the hand")
        return obs, img, np.array([0, 0] if box is None else box[:2], float), what

    def grasp(self, k, cam):
        obs, img, o, what = self.frame(cam)
        js = self.mark(img, GRASP_PROMPT.format(task=self.task_text, what=what, W=img.shape[1], H=img.shape[0], desc=OBJ[k]["desc"]),
                       ["grasp"], check=True)
        g = js.get("grasp"); out = None
        if g and len(g) == 2 and g[0] is not None and g[1] is not None:
            uv = [np.asarray(p, float) + o for p in g]
            zt = self.top_z(obs, (uv[0] + uv[1]) / 2, 0.012)
            if zt is not None:
                tab = self.table_z(obs, (uv[0] + uv[1]) / 2, below=zt - 0.008)
                C = [self.on_plane(obs, q, zt) for q in uv]
                out = dict(c=C, top=zt, table=0.0 if tab is None else min(tab, zt - 0.01))
        gt = GTPerception(self.S).grasp(k, cam)                # (for the eggplant: our reference spot on its thick part)
        self._log("grasp", k, obs, None if out is None else np.r_[(out["c"][0][:2] + out["c"][1][:2]) / 2, out["top"]],
                  np.r_[(gt["c"][0][:2] + gt["c"][1][:2]) / 2, gt["top"]])
        return out

    def place(self, k, mode, cam):
        obs, img, o, what = self.frame(cam)
        items, keys = PLACE_ITEMS[mode]
        js = self.mark(img, PLACE_PROMPT.format(task=self.task_text, what=what, W=img.shape[1], H=img.shape[0],
                                                items=items.format(desc=OBJ[k]["desc"]), keys=keys),
                       ["rim"] if mode == "in" else ["place"], check=True)
        p = js.get("place"); out = None
        if mode == "in":                                      # the opening's centre = the midpoint of 2 rim points
            out = self.rim_centre(obs, js.get("rim"), o)
            if out is not None:
                self.place_z[k] = out[2]
            p = None
        if p is not None:
            uv = np.asarray(p, float) + o
            z = self.top_z(obs, uv, {"on": 0.012, "peg": 0.004, "press": 0.006}[mode])
            if z is not None:
                out = self.on_plane(obs, uv, z)
                self.place_z[k] = z
        self._log("place:" + mode, k, obs, out, GTPerception(self.S).place(k, mode, cam))
        return out

    def rim_centre(self, obs, rim, o, z=None, below=None):
        """Centre of a round opening from its leftmost and rightmost rim points (both at the rim's height, on either
        side of the centre in any view): their rays meet the rim plane (height from the depth at the points)."""
        if not rim or len(rim) != 2 or rim[0] is None or rim[1] is None:
            return None
        uv = [np.asarray(q, float) + o for q in rim]
        if z is None:
            z = self.rim_height(obs, uv, below)
            if z is None:
                return None
        C = [self.on_plane(obs, q, z) for q in uv]
        return np.r_[(C[0][:2] + C[1][:2]) / 2, z]

    def rim_height(self, obs, uv, below=None):
        """The rim's height: the upper quartile of the heights along the rim's ellipse in the image (the two points span
        its horizontal axis; a circle seen at elevation phi is sin(phi) as tall as wide), a band 0.8-1.2x its radius.
        The depth AT the leftmost / rightmost points is the background behind the rim's edge."""
        c = (uv[0] + uv[1]) / 2; a = float(np.linalg.norm(uv[1] - uv[0])) / 2
        if a < 4:
            return None
        e1 = (uv[1] - uv[0]) / (2 * a); e2 = np.array([-e1[1], e1[0]])
        d = self.ray(obs, c); b = max(a * abs(d[2]) / np.linalg.norm(d), 3.0)
        W, Hh = int(obs["K"][0]), int(obs["K"][1])
        yy, xx = np.mgrid[0:Hh, 0:W]
        du, dv = xx - c[0], yy - c[1]
        rho = np.sqrt(((du * e1[0] + dv * e1[1]) / a) ** 2 + ((du * e2[0] + dv * e2[1]) / b) ** 2)
        m = (rho > 0.8) & (rho < 1.2)
        P = self.lift(obs, m, erode=False)
        if P is None:
            return None
        if below is not None:
            P = P[P[:, 2] < below]
        return None if len(P) < 20 else float(np.percentile(P[:, 2], 75))

    def relative(self, held, k, mode, cam, held_h):
        obs = self.observe(cam); img = obs["rgb"]
        if mode == "in":                                      # the opening from its rim points (its centre is hidden)
            js = self.mark(img, SERVO_IN.format(task=self.task_text, W=img.shape[1], H=img.shape[0], held=OBJ[held]["desc"],
                                                desc=OBJ[k]["desc"]), ["held", "rim"], check=False)
            est = None
            zh = None if js.get("held") is None else self.top_z(obs, np.asarray(js["held"], float), 0.010)
            if zh is not None:                                 # the rim from above, below the held object
                c = self.rim_centre(obs, js.get("rim"), np.zeros(2), below=zh - held_h - 0.005)
                if c is not None:
                    h = self.on_plane(obs, np.asarray(js["held"], float), zh)
                    est = np.r_[h[:2] - c[:2], zh - held_h - c[2]]
            self._log("relative:" + mode, f"{held}-{k}", obs, est, GTPerception(self.S).relative(held, k, mode, cam, held_h))
            return est
        js = self.mark(img, SERVO_PROMPT.format(task=self.task_text, W=img.shape[1], H=img.shape[0], held=OBJ[held]["desc"],
                                                desc=OBJ[k]["desc"], place=SERVO_PLACE[mode].format(desc=OBJ[k]["desc"])),
                       ["held", "place"], check=False)
        est = None
        if js.get("held") is not None and js.get("place") is not None:
            uh, ut = np.asarray(js["held"], float), np.asarray(js["place"], float)
            zh = self.top_z(obs, uh, 0.010)
            if zh is not None:
                bottom = zh - held_h
                zt = self.place_z.get(k) if mode == "in" else \
                    self.under_z(obs, ut, below=bottom - 0.004, r=0.006 if mode == "peg" else 0.015)
                if zt is not None:
                    h = self.on_plane(obs, uh, zh); t = self.on_plane(obs, ut, zt)
                    est = np.r_[h[:2] - t[:2], bottom - zt]
        self._log("relative:" + mode, f"{held}-{k}", obs, est, GTPerception(self.S).relative(held, k, mode, cam, held_h))
        return est

    def under_z(self, obs, uv, below, r):
        """Height of the surface at a mark, under the held object: high points in a disc around it, lower than `below`."""
        m, _ = self.disc(obs, uv, r)
        P = self.lift(obs, m, erode=False)
        if P is None:
            return None
        P = P[P[:, 2] < below]
        return None if len(P) < 8 else float(np.percentile(P[:, 2], 90))


# ---------------------------------------------------------------- the program
class TaskProgram:
    def __init__(self, S, per, task, servo_iters=8, tol=0.0008):
        self.S, self.per, self.task = S, per, task
        self.iters, self.tol = servo_iters, tol
        self.events, self.held = [], None

    def event(self, what, **kw):
        self.events.append(dict(t=float(self.S.d.time), what=what, **kw))
        self.S.timeline.append((float(self.S.d.time), "event", what))

    def default_yaw(self, xy):
        return math.atan2(*(np.asarray(xy) - self.S.base[:2])[::-1]) + math.pi / 2

    def run(self):
        for step, k in TASKS[self.task]["steps"]:
            try:
                r = getattr(self, step)(k)
            except Unreachable as e:
                self.event("unreachable: stopped", why=str(e))
                return f"{step} {k}: unreachable ({e})"
            if r != "ok":
                return f"{step} {k}: {r}"
        return "done"

    # -- skills
    def pick(self, k):
        S, per = self.S, self.per
        g = per.grasp(k, "d415")
        if g is None:
            return "not found (scene camera)"
        for look in ("d415", "L_wrist"):
            c = (g["c"][0][:2] + g["c"][1][:2]) / 2
            y0 = self.default_yaw(c); dv = g["c"][1][:2] - g["c"][0][:2]
            y = math.atan2(dv[1], dv[0])                   # y and y + pi grasp the same: take the smaller wrist turn
            yaw = min((y, y + math.pi), key=lambda v: float(np.linalg.norm(rotvec(S.R_cmd.T @ B.down(v)))))
            R = B.down(yaw)
            S.move(np.r_[c, g["top"] + 0.15], R, B.OPEN_W, 2.0 if look == "d415" else 0.6, "above " + k if look == "d415" else "re-aim over " + k)
            if look == "d415":
                g2 = per.grasp(k, "L_wrist")
                if g2 is None:
                    self.event("not found in the wrist camera: keeping the scene camera's points")
                    break
                g = g2
        h_obj = max(g["top"] - g["table"], 0.01)
        z_grip = max(g["table"] + FINGERTIP + 0.005, g["top"] - 0.010)
        S.move(np.r_[c, z_grip + 0.05], R, B.OPEN_W, 1.0, "pregrasp")
        S.move(np.r_[c, z_grip], R, B.OPEN_W, 0.6, "descend")
        S.move(np.r_[c, z_grip], R, 0.0, 0.8, "close")
        for attempt in range(2):
            for _ in range(15):
                w0 = S.finger_width(); S.hold(1 / 15)
                if abs(S.finger_width() - w0) < 0.0003:
                    break
            if S.finger_width() < 0.072 or attempt == 1:
                break
            self.event("gripper blocked: open, 6 mm higher, close again", width_mm=S.finger_width() * 1000)
            z_grip += 0.006
            S.move(np.r_[c, z_grip], R, B.OPEN_W, 0.6); S.move(np.r_[c, z_grip], R, 0.0, 0.8, "close again")
        w = S.finger_width()
        self.event("grasp", width_mm=w * 1000, h_obj_mm=h_obj * 1000)
        if w < 0.004:
            return "missed (closed on nothing)"
        S.move(np.r_[c, max(0.32, z_grip + 0.20)], R, 0.0, 1.2, "lift")
        if S.finger_width() < 0.004:
            return "dropped"
        self.held = dict(k=k, R=R, h=h_obj, bottom_off=g["table"] - z_grip)     # held bottom = TCP z + bottom_off
        return "ok"

    def _hover(self, t, clear):
        """TCP above place point t (3D) with the held object's bottom `clear` above it."""
        H = self.held
        return np.r_[t[:2], t[2] + clear - H["bottom_off"]]

    def on(self, k, mode="on"):
        S, per, H = self.S, self.per, self.held
        t = per.place(k, mode, "d415")
        if t is None:
            return "place point not found (scene camera)"
        corr = np.zeros(3)
        stages = (0.10, 0.03, 0.008) if mode == "on" else (0.04, 0.012)
        S.move(self._hover(t, stages[0]), H["R"], 0.0, 2.0, "carry over " + k)
        for dz in stages:
            S.move(self._hover(t, dz) + corr, H["R"], 0.0, 0.8, f"to {dz * 1000:.0f} mm above")
            for it in range(self.iters):
                S.hold(0.25)
                e = per.relative(H["k"], k, mode, "L_wrist", H["h"])
                if e is None:
                    self.event("relative offset not seen", dz=dz, it=it)
                    break
                ez = e[2] - dz
                self.event("servo", dz=dz, it=it, err_mm=float(np.linalg.norm(e[:2]) * 1000), gap_err_mm=float(ez * 1000))
                if np.linalg.norm(e[:2]) < self.tol and abs(ez) < 0.002:
                    break
                step = -0.8 * np.r_[e[:2], ez]
                corr = corr + step
                S.move(self._hover(t, dz) + corr, H["R"], 0.0, max(0.3, float(np.linalg.norm(step)) / 0.08))
        cmd = S.p_cmd.copy()
        tgt = S.subtree[k]; mine = S.subtree[H["k"]]
        if mode == "on":                                     # down until red feels the target, unload, open
            for i in range(25):
                cmd[2] -= 0.001
                S.move(cmd, H["R"], 0.0, 0.1, "set down" if i == 0 else None); S.hold(0.05)
                if S.force(mine, tgt) > 1.5:
                    self.event("contact", steps=i + 1); break
            rel = cmd + [0, 0, 0.001]
            S.move(rel, H["R"], 0.0, 0.2, "unload")
        else:                                                # peg: down until 2 cm past the tip, unless it hits
            for i in range(40):
                cmd[2] -= 0.001
                S.move(cmd, H["R"], 0.0, 0.1, "thread" if i == 0 else None); S.hold(0.03)
                if S.force(mine, tgt) > 2.0:
                    self.event("hit the stick", steps=i + 1); break
            rel = cmd.copy()
        self.event("before release", width_mm=S.finger_width() * 1000)
        S.move(rel, H["R"], B.OPEN_W, 0.6, "release")
        S.move(rel + [0, 0, 0.10], H["R"], B.OPEN_W, 1.0, "retreat"); S.hold(0.8)
        self.held = None
        return "ok"

    def peg(self, k):
        return self.on(k, mode="peg")

    def in_(self, k):
        S, per, H = self.S, self.per, self.held
        t = per.place(k, "in", "d415")
        if t is None:
            return "place point not found (scene camera)"
        # carry high (held bottom 14 cm over the rim: clears an open kettle lid), then straight down over the opening
        S.move(self._hover(t, 0.14), H["R"], 0.0, 2.0, "carry over " + k)
        S.hold(0.25)
        e = per.relative(H["k"], k, "in", "L_wrist", H["h"])
        corr = np.zeros(3)
        # a wrist estimate is implausible if it is too large or its rim height disagrees with the scene camera's
        bottom_now = S.tcp_now()[2] + H["bottom_off"]
        if e is not None and (np.linalg.norm(e[:2]) > 0.08 or abs((bottom_now - e[2]) - t[2]) > 0.04):
            self.event("wrist correction ignored (implausible)", err_mm=float(np.linalg.norm(e[:2]) * 1000),
                       rim_dz_mm=float(((bottom_now - e[2]) - t[2]) * 1000))
        elif e is not None:
            self.event("servo", err_mm=float(np.linalg.norm(e[:2]) * 1000))
            corr[:2] = -e[:2]
        else:
            self.event("relative offset not seen")
        S.move(self._hover(t, 0.14) + corr, H["R"], 0.0, 0.6, "correct over " + k)   # sideways at the safe height first
        low = self._hover(t, 0.015) + corr                   # then straight down: held bottom 15 mm over the rim,
        low[2] = max(low[2], t[2] + FINGERTIP + 0.015)       # fingertips >= 15 mm over it
        S.move(low, H["R"], 0.0, 1.0, "lower to the rim")
        S.move(S.p_cmd, H["R"], B.OPEN_W, 0.6, "release")
        S.move(S.p_cmd + [0, 0, 0.10], H["R"], B.OPEN_W, 1.0, "retreat"); S.hold(1.0)
        self.held = None
        return "ok"

    def press(self, k):
        S, per = self.S, self.per
        p = per.place(k, "press", "d415")
        if p is None:
            return "button not found (scene camera)"
        R = B.down(self.default_yaw(p[:2]))
        S.move(np.r_[p[:2], p[2] + FINGERTIP + 0.12], R, 0.0, 2.0, "above the button")
        p2 = per.place(k, "press", "L_wrist")
        if p2 is not None:
            p = p2
        else:
            self.event("button not found in the wrist camera: keeping the scene camera's point")
        S.move(np.r_[p[:2], p[2] + FINGERTIP + 0.02], R, 0.0, 1.0, "approach")
        cmd = S.p_cmd.copy(); btn = {S.m.body("obj_kettle_button").id}
        for i in range(30):
            cmd[2] -= 0.001
            S.move(cmd, R, 0.0, 0.1, "press" if i == 0 else None); S.hold(0.03)
            if S.force(S.fingers, btn) > 3.0 and S.d.qpos[S.btn_q] < -0.003 or cmd[2] < p[2] + FINGERTIP - 0.006:
                break
        S.hold(0.3)
        self.event("pressed", button_mm=float(S.d.qpos[S.btn_q] * 1000), latched=S.latched)
        S.move(cmd + [0, 0, 0.12], R, 0.0, 0.6, "back up"); S.hold(1.0)
        return "ok"


setattr(TaskProgram, "in", TaskProgram.in_)


# ---------------------------------------------------------------- judging (true state)
def judge(S, task):
    P = {k: S.pose(k) for k in OBJ}

    def inside(k, cont, rim_frac=1.0):
        """k inside the container, the container standing where it was put (on the table, upright)."""
        p, R = P[k]; c, Rc = P[cont]; o = OBJ[cont]
        bottom = p[2] - OBJ[k]["z0"]
        standing = c[2] < 0.005 and Rc[2, 2] > math.cos(math.radians(10))
        top = bottom + OBJ[k]["h"]                            # in, not resting on the rim
        return standing and (np.linalg.norm(p[:2] - c[:2]) < o["r"] * rim_frac) and (top < c[2] + o["h"] + 0.005), float(np.linalg.norm(p[:2] - c[:2]) * 1000)
    lid = float(math.degrees(S.d.qpos[S.lid_q]))
    if task == "kettle_open":
        return dict(ok=lid > 30, lid_deg=lid)
    if task == "kcup_in_kettle":
        ok, dmm = inside("kcup", "kettle", 0.93)                 # within the inner wall (r * 0.93)
        return dict(ok=ok and lid > 30, lid_deg=lid, kcup_from_axis_mm=dmm, kcup_z_mm=float(P["kcup"][0][2] * 1000))
    if task in ("blue_in_mug", "cube_in_basket", "eggplant_in_basket"):
        k, c = {"blue_in_mug": ("oct_blue", "mug_white"), "cube_in_basket": ("cube_green", "basket_small"),
                "eggplant_in_basket": ("eggplant", "basket_big")}[task]
        ok, dmm = inside(k, c, 0.90 if c.startswith("mug") else 0.95)   # within the inner wall
        return dict(ok=ok, from_axis_mm=dmm, z_mm=float(P[k][0][2] * 1000))
    if task in ("stack_cubes", "stack_rings"):
        a, b = ("cube_red", "cube_green") if task == "stack_cubes" else ("oct_red", "oct_green")
        (pa, Ra), (pb, Rb) = P[a], P[b]
        off = Rb.T @ (pa - pb)
        dz = (pa[2] - OBJ[a]["z0"]) - (pb[2] - OBJ[b]["z0"] + OBJ[b]["h"])
        tilt = math.degrees(math.acos(np.clip(Ra[2, 2], -1, 1)))
        lim = (OBJ[b]["size"] - OBJ[a]["size"]) / 2 + 0.003 if task == "stack_cubes" else 0.02
        ok = (max(abs(off[0]), abs(off[1])) < lim if task == "stack_cubes" else np.hypot(off[0], off[1]) < lim) and abs(dz) < 0.008 and tilt < 15
        return dict(ok=bool(ok), offset_mm=float(np.hypot(off[0], off[1]) * 1000), dz_mm=float(dz * 1000), tilt_deg=tilt)
    if task == "ring_on_peg":
        p, _ = P["oct_orange"]; best = 1e9
        for i in range(3):
            g = S.m.geom(f"obj_peg_stand_peg{i}").id
            gp = S.d.geom_xpos[g]; tip = gp[2] + S.m.geom_size[g][1]
            d_ = np.linalg.norm(p[:2] - gp[:2])
            if p[2] - OBJ["oct_orange"]["z0"] < tip - 0.01:
                best = min(best, d_)
        ok = best < (OBJ["oct_orange"]["hole"] - 0.018) / 2 + 0.003
        return dict(ok=bool(ok), ring_to_stick_mm=float(best * 1000) if best < 1e8 else None, ring_z_mm=float(p[2] * 1000))


# ---------------------------------------------------------------- runner
def run_job(job):
    task, seed, idx, kind, out, save = job
    L = sample_layout(task, seed, idx)
    if L is None:
        return dict(task=task, layout=idx, result="layout rejected")
    S = TaskSim(); apply_layout(S, L)
    tag = f"{task}_{idx:02d}_{kind}"
    if kind == "astra":
        per = AstraTaskPerception(S, np.random.default_rng([seed, idx, 5]), out, tag, task)
        prog = TaskProgram(S, per, task, servo_iters=4, tol=0.0015)
    else:
        per = GTPerception(S)
        prog = TaskProgram(S, per, task)
    t0 = time.time()
    try:
        r = prog.run()
    except Exception as e:
        import traceback
        r = f"crash: {e!r}"[:200]; traceback.print_exc()
    res = judge(S, task)
    row = dict(task=task, layout=idx, perception=kind, program=r, result="success" if res.pop("ok") else "fail", **res,
               sim_s=round(float(S.d.time), 1), wall_s=round(time.time() - t0, 1), events=prog.events[-25:],
               perception_log=getattr(per, "log", None), astra_calls=getattr(per, "calls", 0), astra_cost=round(getattr(per, "cost", 0.0), 3),
               astra=getattr(per, "astra_log", None), layout_spec=L)
    if save:
        np.savez_compressed(Path(out) / f"trace_{tag}.npz", t=np.array([x[0] for x in S.rec]), qpos=np.array([x[1] for x in S.rec]))
        json.dump(dict(row=row, layout=L, timeline=S.timeline), open(Path(out) / f"trace_{tag}.json", "w"),
                  default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    if hasattr(per, "close"):
        per.close()
    with open(Path(out) / "runs.jsonl", "a") as f:
        f.write(json.dumps(row, default=lambda o: o.tolist() if hasattr(o, "tolist") else (o if isinstance(o, (int, float, str)) else str(o))) + "\n")
    return row


def summarize(rows, out):
    lines = ["| task | success | results (layout: outcome) | Astra calls / $ |", "|---|---|---|---|"]
    for t in TASKS:
        R = sorted([r for r in rows if r.get("task") == t], key=lambda r: r["layout"])
        if not R:
            continue
        ok = sum(r["result"] == "success" for r in R)
        det = "; ".join(f'{r["layout"]}: {r["result"]}' + ("" if r["result"] == "success" else f' ({r.get("program")})') for r in R)
        lines.append(f"| {t} | {ok}/{len(R)} | {det} | {sum(r.get('astra_calls', 0) for r in R)} / {sum(r.get('astra_cost', 0) for r in R):.2f} |")
    n_ok = sum(r["result"] == "success" for r in rows)
    txt = "\n".join(lines) + f"\n\nTotal {n_ok}/{len(rows)}\n"
    (Path(out) / "summary.md").write_text(txt)
    return txt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--perception", choices=["gt", "astra"], default="gt")
    ap.add_argument("--tasks", nargs="+", default=list(TASKS))
    ap.add_argument("--layouts", type=int, default=3)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--save-traces", action="store_true")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in open(a.out / "runs.jsonl")] if (a.out / "runs.jsonl").exists() else []
    done = {(r["task"], r["layout"]) for r in rows}
    jobs = [(t, a.seed, i, a.perception, str(a.out), a.save_traces) for t in a.tasks for i in (a.only or range(a.layouts)) if (t, i) not in done]
    print(f"{len(done)} done, {len(jobs)} to run", flush=True)
    t0 = time.time()
    with Pool(max(1, min(a.workers, len(jobs)))) as pool:
        for r in pool.imap_unordered(run_job, jobs):
            rows.append(r)
            print(f"{r['task']} {r['layout']} ({time.time() - t0:.0f}s): {r['result']} - {r.get('program')} "
                  + " ".join(f"{k}={v:.1f}" if isinstance(v, float) else "" for k, v in r.items() if k.endswith(("_mm", "_deg"))), flush=True)
    print(summarize(rows, a.out))


if __name__ == "__main__":
    main()
