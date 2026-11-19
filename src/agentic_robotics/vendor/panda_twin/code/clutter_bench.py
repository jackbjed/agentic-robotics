"""Clutter bench (11-01): picks among touching objects, and out of a box full of objects, in the Panda twin (the 6522
objects, model/twin_6522.xml; photoreal images from composite_server.py :8133 (splats aligned: align_objsplats.py);
SAM3 from dexteleop-vla/twin/sam3_server.py :8131 (text) and sam3_points_server.py :8134 (clicks)).

    MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 ~/miniforge3/envs/teleavatar_client/bin/python -I code/clutter_bench.py \
        --out out/clutter --scene pile bin --layouts 12 --pick depth seg [--seed-err 0.015] [--save-traces]

Plans: --plan gt (default): the reference plan, its points from the true state, as the one Astra call would mark them;
--plan astra (11-02): the one Astra call on the scene camera's image (CLUTTER_PROMPT): its steps, its object names (the
seg skill's SAM3 prompts) and its marks (the D415's ray through each, the surface it meets there).
Scenes:
  pile  5-7 objects pulled together into one touching group on the table (a horizontal force towards a point, then
        settled: they touch, lean on each other, the glue bottle may fall over); task: the target into the white box
        (empty, lid open) = pick + put_in.
  bin   the white box (lid open) with 4-6 objects dropped in one after the other (they pile up); task: the target out
        of the box onto a free spot of the table = pick + down. A target the scene camera does not see (the box's near
        wall hides most of its floor from the D415) gets the box's middle as its point: "it is in the box".
--seed-err M: each pick point moved M metres sideways (Astra's marks were 15-20 mm off on the box task): the scene
camera's ray through the moved point, and the surface it meets there (often a neighbour).
Pick skills:
  depth  task_box's pick (the baseline): the depth blob above the table on the scene camera's ray, its narrowest
         width, the fin-ray fingers' clearance (task_fast.finger_clear), the gripper fully open.
  seg    perception: the wrist camera's COLOUR image + SAM3 (text prompt = the step's object name, sam3_server :8131)
         -> the mask nearest the mark; not there: a wider look (search; a sure mask >= 0.5, or a weak one a "small
         object" mask agrees with); still not: dig (take the topmost thing there away, or slide it aside). Looks are
         occlusion-aware (an open lid in the way: from the other side of the hand). Things that are not the target
         are segmented with the "small object" prompt or a CLICK on their pixels (sam3_points_server.py :8134), and
         never if they overlap any of the target name's masks (the target is not taken away as "another thing").
         grasp: every closing direction (6 deg), along a long thing every 1 cm (its middle half only); the gripper
         opened to the width there + 9/6/4 mm; the TCP height where the fin-ray finger (pad, middle, upper, base), the
         hand and the wrist camera, swept straight down, touch NO depth point (anything but the target's own between
         the pads), fingertips 20 mm / 70 % under the local top at the contacts; a tilted thing only along its level
         direction; nothing of other things between the fingers. Before it: something over the target (inside its
         outline's hull, or a quarter of its outline bordering something higher) is taken away first. No grasp
         clears: drag it out (closed fingers pressed on it, slid 6 cm the freest way, the arm reaching the end), then
         move a blocker (grasp it away, else drag it away from the target, else any graspable neighbour, highest
         first). After the lift: the target's own surface gone from the spot (else missed / the wrong thing in hand:
         put it aside, again). Up to 6 things put aside and 3 drags; a thing that is not the target may be grasped
         with the PUSH level below (11-02; CLUTTER_PERSIST=0: 2 + 2, clear grasps only, as 11-01). CLUTTER_PUSH=1:
         also the target: a last level where only the pads' inner 9 mm must clear (their outer part may push a
         neighbour's edge aside).
11-02 fixes (CLUTTER_CHECKS=1, default): after a grasp closes, > 6 mm wider than planned = something else in the grip
(let go, fully open, rise, check); a 2.5 cm lift, then the scene camera (only what changed, the robot's own pixels
masked, sideways shifts not counted) and the hand's load (> 2 N: the box) for anything else that came up; at the full
lift, what hangs at the hand beyond the target (set it all back); the match's colour (yellow / white); drags: the
way also in the scene camera where the wrist's look has no say, after it back off and check nothing hangs on the
fingers (shake it off); the travel move's first rise only as high as the arm reaches there; put-down spots in reach.
CLUTTER_GRIPPER=thin: the gripper variant of make_gripper_variants.py (4 mm plates), its finger boxes, and the grip
from inside a tape's hole (closed plates fit; the fin-ray pads do not). CLUTTER_POUR=1 (unfinished, off): a box too
full to pick from is gripped by a wall and turned over onto the table (the arm reaches a 130 deg turn on one wall in
~half the layouts; the first version leaves the box tilted and the things in it).
Judged on the true state: the target held after the pick (lifted > 5 cm, between the fingers), nothing else lifted,
the target in the box (pile) / on the table near the point (bin), nothing knocked off the table, the box not tipped.
"""
from __future__ import annotations

import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import argparse
import io
import json
import re
import math
import sys
import time
import urllib.request
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_bench as B  # noqa: E402
import task_bench as T  # noqa: E402
import task_fast as F  # noqa: E402
import task_box as TB  # noqa: E402

NAMES = {"tape_red": "red tape roll", "tape_yellow": "yellow tape roll", "tape_white": "white tape roll",
         "hole_block": "black block with holes", "cable": "coiled black cable", "screwdriver": "red screwdriver",
         "sticky_pad": "sticky note pad", "glue": "glue bottle"}
TARGETS = {"pile": ["tape_red", "tape_yellow", "tape_white", "hole_block", "cable", "screwdriver"],
           "bin": ["tape_red", "tape_yellow", "tape_white", "hole_block", "cable"]}
POOL = {"pile": TARGETS["pile"] + ["sticky_pad", "glue"], "bin": TARGETS["bin"] + ["sticky_pad"]}
SMALL = ["tape_red", "tape_yellow", "tape_white", "hole_block", "cable", "screwdriver", "sticky_pad", "glue"]
POINTS_URL = "http://127.0.0.1:8134/points"
B.COMP_URL = os.environ.get("CLUTTER_COMP", "http://127.0.0.1:8133/composite")   # (11-02: :8133 serves the
# aligned splats (align_objsplats.py); before, they sat 11-20 mm low in their own frames: a fallen object's colour ~2 cm
# off its depth; 11-01's runs used a second server :8135 with the aligned copy)
# the left hand in the TCP frame (measured on the model 11-01; u = along the fingers' closing axis, v = across it,
# z = up from the TCP): (u from, u to (None: from the opening q), half width across, bottom above the TCP)
FINGER_BOXES = [(-0.001, 0.018, 0.013, -0.016),   # fingertip pad: q-1 .. q+18 mm, 16 mm below the TCP up
                (0.001, 0.034, 0.013, 0.009),     # middle section
                (-0.001, 0.041, 0.013, 0.048),    # upper section
                (-0.001, 0.045, 0.018, 0.060)]    # base
HAND_BOX = (0.107, 0.033, 0.108)                  # |u| < 107 mm, |v| < 33 mm, from 108 mm above the TCP
CAM_BOX = (0.045, 0.095, 0.058, 0.098)            # wrist D415: v 45..95 mm on the camera side, |u| < 58, from 98 mm up
FINGER_NAMES = ("pad", "middle", "upper", "base")
CLOSED_HALF = (0.0157, 0.0129)                    # the closed fingertips' half extent (along the closing axis, across)
GRIPPER = os.environ.get("CLUTTER_GRIPPER", "finray")   # finray (the real UMI fingers) | thin (11-02, fix 3:
if GRIPPER == "thin":                                   # make_gripper_variants.py: 4 mm plates, 20 mm wide)
    B.MODEL = B.ROOT / "model/twin_6522_thin.xml"
    FINGER_BOXES = [(-0.001, 0.0055, 0.010, -0.016),  # the plate: q-1 .. q+5.5 mm, 16 mm below the TCP up
                    (-0.001, 0.045, 0.018, 0.060)]    # its mount (the fin-ray's base)
    FINGER_NAMES = ("plate", "base")
    CLOSED_HALF = (0.0054, 0.010)
# (11-05, CLUTTER_GRIPPER=wedge) the same fin-ray fingers with their TRUE outline: the finger mesh is a wedge, 3-6 mm
# thick over its lowest 5-8 mm, 10.6 mm at the TCP's height, ~0.5 mm more per mm up (the pad box above: 19 mm from the
# tip up; with the opening margin and the boxes' footprint margin a grasp needed ~25 mm free beside the thing; user
# 11-05: "about 0.8 cm gap should be enough"). make_gripper_variants.WEDGE_STEPS (the twin: model/twin_6522_wedge.xml,
# the same boxes for its contacts): (TCP heights h0..h1 mm, thickness from the pad's face mm)
WEDGE_STEPS = [(-15.7, -12.5, 3.4), (-12.5, -10, 4.9), (-10, -7.5, 6.4), (-7.5, -5, 7.8), (-5, -2.5, 9.2), (-2.5, 0, 10.6),
               (0, 4, 12.7), (4, 8, 14.7), (8, 12, 16.7), (12, 16, 18.5), (16, 20, 20.3), (20, 26, 22.8), (26, 32, 25.3),
               (32, 40, 28.7), (40, 48, 32.1)]
if GRIPPER == "wedge":
    B.MODEL = B.ROOT / "model/twin_6522_wedge.xml"
    FINGER_BOXES = [(-0.001, u / 1000, 0.013, h0 / 1000) for h0, h1, u in WEDGE_STEPS] + FINGER_BOXES[2:]
    FINGER_NAMES = tuple("pad" if h1 <= 0 else "middle" for h0, h1, u in WEDGE_STEPS) + FINGER_NAMES[2:]
    CLOSED_HALF = (0.0064, 0.0129)                # (along the closing axis: 5-8 mm over the tips, where a push touches)
PAD_HW = FINGER_BOXES[0][2]                       # the fingertip's half width across the closing axis
_FB = np.array(FINGER_BOXES)                      # (clear_height's one pass over them)
_FB_PAD = np.array([nm == FINGER_NAMES[0] for nm in FINGER_NAMES])


def pose_of(S, k):
    return S.pose(k)


def obj_extent(S, k):
    """The object's collision geoms' corners (world): the middle of its extent in x, y, its top."""
    m, d = S.m, S.d
    pts = []
    for g in range(m.ngeom):
        if S.root_of(m.geom_bodyid[g]) != S.ob[k] or not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        c, Rg = d.geom_xpos[g], d.geom_xmat[g].reshape(3, 3)
        ctr, half = m.geom_aabb[g].reshape(2, 3)
        pts += [c + Rg @ (ctr + half * np.array([sx, sy, sz])) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    P = np.array(pts)
    return np.r_[(P[:, :2].min(0) + P[:, :2].max(0)) / 2, P[:, 2].max()]


def in_box_xy(S, p, slack=0.0):
    bp, bR = S.pose("box_white")
    rel = bR.T @ (np.asarray(p, float) - bp)
    return abs(rel[0]) < TB.BOX_L / 2 + slack and abs(rel[1]) < TB.BOX_W / 2 + slack


def tilt_deg(S, k):
    return math.degrees(math.acos(min(1.0, S.pose(k)[1][2, 2])))


# ---------------------------------------------------------------- layouts
def _step(S, T_, forces=None):
    m, d, mj = S.m, S.d, S.mj
    for _ in range(int(T_ / m.opt.timestep)):
        if forces:
            forces()
        mj.mj_step(m, d)


def sample_layout(scene, seed, idx):
    """-> dict(scene, target, objs, qpos (settled), steps [(skill, name)], points, down (bin), ...) or None."""
    rng = np.random.default_rng([seed, idx, 1101, scene == "bin"])
    S = TB.BoxSim(); m, d, mj = S.m, S.d, S.mj
    key = m.key("twin").id
    base = S.base[:2]
    for tries in range(60):
        mj.mj_resetDataKeyframe(m, d, key)
        d.qvel[:] = 0
        n = int(rng.integers(5, 8)) if scene == "pile" else int(rng.integers(4, 7))
        target = str(rng.choice(TARGETS[scene]))
        others = [k for k in POOL[scene] if k != target]
        objs = [target] + list(rng.choice(others, n - 1, replace=False))
        rng.shuffle(objs)
        if scene == "pile":
            for _ in range(200):                                     # the box (hinge on the far -y side), the group
                bxy = rng.uniform([-0.42, -0.13], [-0.24, -0.03])
                cxy = rng.uniform([-0.52, 0.12], [-0.22, 0.34])
                if 0.40 < np.linalg.norm(bxy - base) < 0.58 and 0.38 < np.linalg.norm(cxy - base) < 0.60 and cxy[1] - bxy[1] > 0.30:
                    break
            S.put("box_white", bxy[0], bxy[1], rng.uniform(-0.25, 0.25))
            d.qpos[S.lid_q] = math.radians(TB.LID_OPEN)
            angs = np.linspace(0, 2 * math.pi, n, endpoint=False) + rng.uniform(0, 2 * math.pi)
            for k, a in zip(objs, angs):                             # on a ring, long sides pointing at the middle
                r0 = 0.17 + rng.uniform(-0.01, 0.01)                 # (no overlaps to start with)
                S.put(k, cxy[0] + r0 * math.cos(a), cxy[1] + r0 * math.sin(a), a + rng.choice([0, math.pi / 2]) + rng.uniform(-0.3, 0.3))
            mj.mj_forward(m, d)
            f_pull = rng.uniform(1.4, 1.9)

            def pull():
                for k in objs:
                    b = S.ob[k]; p = d.xpos[b]; v = cxy - p[:2]; dist = float(np.linalg.norm(v))
                    dof = m.jnt_dofadr[m.body_jntadr[b]]; mass = m.body_subtreemass[b]
                    d.xfrc_applied[b, :2] = f_pull * mass * 9.81 * v / max(dist, 1e-3) * min(1.0, dist / 0.01) - 4.0 * mass * d.qvel[dof:dof + 2]
            _step(S, 2.5, pull)
            d.xfrc_applied[:] = 0
            _step(S, 1.0)
        else:
            for _ in range(200):
                bxy = rng.uniform([-0.42, -0.08], [-0.26, 0.14])
                if 0.40 < np.linalg.norm(bxy - base) < 0.58:
                    break
            hinge = rng.choice([-1, 1])
            S.put("box_white", bxy[0], bxy[1], (0.0 if hinge < 0 else math.pi) + rng.uniform(-0.25, 0.25))
            d.qpos[S.lid_q] = math.radians(TB.LID_OPEN)
            mj.mj_forward(m, d)
            _step(S, 0.3)
            bp, bR = S.pose("box_white")
            for k in objs:                                           # dropped in, one after the other
                a = S.oq[k]
                xy = bp[:2] + bR[:2, :2] @ rng.uniform(-0.055, 0.055, 2)
                d.qpos[a:a + 3] = [xy[0], xy[1], 0.11]
                yaw = rng.uniform(0, 2 * math.pi); tl = rng.uniform(-0.3, 0.3)
                qz = np.array([math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]); qx = np.array([math.cos(tl / 2), math.sin(tl / 2), 0, 0])
                qq = np.zeros(4); mj.mju_mulQuat(qq, qz, qx); d.qpos[a + 3:a + 7] = qq
                dof = m.jnt_dofadr[m.body_jntadr[S.ob[k]]]; d.qvel[dof:dof + 6] = 0
                _step(S, 0.6)
            _step(S, 1.0)
        mj.mj_forward(m, d)
        why = None
        if any(S.pose(k)[0][2] < -0.01 for k in objs + ["box_white"]):
            why = "off the table"
        elif tilt_deg(S, "box_white") > 3 or abs(math.degrees(d.qpos[S.lid_q]) - TB.LID_OPEN) > 8:
            why = "box tipped / lid moved"
        elif scene == "bin" and not all(in_box_xy(S, obj_extent(S, k)) for k in objs):
            why = "not all in the box"
        elif scene == "pile" and np.linalg.norm(S.pose("box_white")[0][:2] - bxy) > 0.005:
            why = "box pushed"
        if why is None:                                              # the target: touching something, reachable
            margin0 = m.geom_margin.copy()
            gt = [g for g in range(m.ngeom) if S.root_of(m.geom_bodyid[g]) == S.ob[target]]
            m.geom_margin[gt] = 0.004; mj.mj_forward(m, d)
            touch = set()
            for c in d.contact[:d.ncon]:
                b1, b2 = S.root_of(m.geom_bodyid[c.geom1]), S.root_of(m.geom_bodyid[c.geom2])
                if S.ob[target] in (b1, b2):
                    o = b2 if b1 == S.ob[target] else b1
                    nm = m.body(o).name
                    if nm.startswith("obj_") and o != S.ob[target]:
                        touch.add(nm[4:])
            m.geom_margin[:] = margin0; mj.mj_forward(m, d)
            X = obj_extent(S, target)
            y0 = math.atan2(*(X[:2] - base)[::-1]) + math.pi / 2
            reach = all(min(S.ik(np.r_[X[:2], z], B.down(y0 + math.radians(a)), S.q_home)[1] for a in (0, 45, 90, 135)) < 0.002
                        for z in (X[2] + 0.005, 0.12, 0.25))
            if scene == "pile" and not touch - {"ruler"}:
                why = "target touches nothing"
            elif not reach:
                why = "target out of reach"
        if why is not None:
            continue
        eye = d.cam_xpos[m.camera("d415").id].copy(); gid = np.zeros(1, np.int32)
        X = obj_extent(S, target); seen = 0
        for q in [X - [0, 0, 0.003]] + [X + [0.012 * math.cos(a), 0.012 * math.sin(a), -0.003] for a in np.arange(4) * math.pi / 2]:
            v = q - eye
            mj.mj_ray(m, d, eye, v / np.linalg.norm(v), None, 1, -1, gid)
            seen += int(gid[0] >= 0 and S.root_of(m.geom_bodyid[gid[0]]) == S.ob[target])
        if scene == "pile" and seen < 2:
            continue
        L = dict(scene=scene, seed=seed, layout=idx, tries=tries, target=target, objs=objs, touching=sorted(touch - {"ruler"}),
                 seen_rays=seen, qpos=d.qpos.tolist())
        if scene == "pile":
            L["steps"] = [["pick", NAMES[target]], ["in", "white box"]]
        else:
            L["steps"] = [["pick", NAMES[target]], ["down", "the table"]]
            bp, bR = S.pose("box_white")
            lid = m.body("obj_box_white_lid").id
            for _ in range(400):                                    # a free spot on the table for it
                p = rng.uniform([-0.55, -0.15], [-0.12, 0.40])
                if not 0.38 < np.linalg.norm(p - base) < 0.60:
                    continue
                if np.linalg.norm(p - bp[:2]) < 0.20 or np.linalg.norm(p - d.xpos[lid][:2]) < 0.18:
                    continue
                if any(np.linalg.norm(p - S.pose(k)[0][:2]) < 0.12 for k in TB.OBJ if k != "box_white"):
                    continue
                y1 = math.atan2(*(p - base)[::-1]) + math.pi / 2
                if min(S.ik(np.r_[p, 0.10], B.down(y1 + math.radians(a)), S.q_home)[1] for a in (0, 90)) > 0.002:
                    continue
                L["down"] = p.tolist(); break
            else:
                continue
        return L
    return None


def apply_layout(S, L):
    S.d.qpos[:] = L["qpos"]; S.d.qvel[:] = 0
    S.mj.mj_forward(S.m, S.d)
    for c in B.CAM:
        S.set_camera_error(c, [0, 0, 0], [0, 0, 0])
    S.d.ctrl[S.act] = S.q_home; S.d.ctrl[S.grip] = B.OPEN_W
    S.hold(0.5)
    S.rec.clear()
    S.sync_cmd(); S.p_cmd = S.tcp_now(); S.q_cmd = S.q_home.copy(); S.g_cmd = B.OPEN_W


# ---------------------------------------------------------------- perception: the plan (reference / Astra); colour looks
CLUTTER_PROMPT = """{task}
This is a view of the table from a fixed camera, {W}x{H} pixels; pixel coordinates are x from the left edge, y from
the top edge. The robot is one arm with a parallel-jaw gripper that works from above; close up, its wrist camera and a
segmentation model find the object again by the name you give it. Its skills:
- pick: grasp an object. Give "grasp": the 2 points where the fingertips should touch it, on opposite sides across its
  middle, each on the top edge of the object. If the object is hidden (inside a container whose walls hide it, or under
  other objects), give 2 points around where it must be (e.g. the middle of the container's inside): the skill looks
  there.
- put_in: put the held object into a container (an open box). Give "point": the centre of the container's inside (its
  floor, as seen in this image). The skill finds free space inside the container by itself.
- put_down: put the held object down on the table. Give "point": a free spot on the table, at least 10 cm away from
  every other object and from the image's edges.
Plan the task as a sequence of these skills, in order, and give each step's points in this image. Other objects may
look similar: mark only the ones the task is about.
Respond with this JSON object and nothing else:
{{"steps": [{{"skill": "<skill>", "object": "<pick: a short name of the object grasped: its colour and kind, e.g. \"blue cup\"; put_in: the container; put_down: \"the table\">", "grasp": [[x, y], [x, y]] or "point": [x, y]}}, ...],
 "reasoning": "<1-2 sentences>"}}"""
CLUTTER_SKILLS = {"pick": "pick", "put_in": "in", "put_down": "down"}
ASTRA_PUSH = os.environ.get("CLUTTER_ASTRA_PUSH", "0") == "1"   # (11-04, user) Astra may also plan pushes: the prompt
# says the objects may touch, the push skill takes 2 points (where the fingertips touch the object, where that point
# should end up); ClutterProgram.f_push
PUSH_SKILL_TEXT = """- push: slide an object along the table with the closed gripper, without lifting it, e.g. to move it away from the
  objects it touches so that it can then be grasped. Give "from": the point on the object's outline where the closed
  fingertips touch it, on the side opposite to where it should go, and "to": where that point of the object should be
  at the end of the push (3-6 cm from "from": in this image that is only 10-25 pixels), towards free table, not into
  other objects. The closed fingertips come down onto the table just behind "from": they need about 3 cm of free table
  there, so push from a side that has free table behind it. To free an object that touches others, push the
  neighbour away from it, from the neighbour's far side (the side that faces free table), or push the object itself
  from a free side; never from between two objects that touch.
"""
CLUTTER_HINT = """The objects may lie in a pile, touching each other. The gripper's fingers need about 2 cm of free space on two
opposite sides of an object to grasp it: an object that touches its neighbours on all sides may first have to be
pushed away from them (push it, then pick the same object, by the same name). If it has that room already, pick it
directly.
"""
CLUTTER_PROMPT_PUSH = CLUTTER_PROMPT.replace("- put_in:", PUSH_SKILL_TEXT + "- put_in:", 1).replace(
    "Plan the task as", CLUTTER_HINT + "Plan the task as", 1).replace(
    '"grasp": [[x, y], [x, y]] or "point": [x, y]}}', '"grasp": [[x, y], [x, y]] or "point": [x, y] or (push) "from": [x, y], "to": [x, y]}}', 1).replace(
    'put_down: "the table">', 'put_down: "the table"; push: the object pushed>', 1)
assert (CLUTTER_PROMPT_PUSH.count("- push:") == 1 and CLUTTER_HINT in CLUTTER_PROMPT_PUSH and '"from": [x, y]' in CLUTTER_PROMPT_PUSH
        and "push: the object pushed" in CLUTTER_PROMPT_PUSH)


def task_sentence(L):
    name = NAMES[L["target"]]
    return (f"Task: pick the {name} out of the pile of objects and put it into the white box." if L["scene"] == "pile"
            else f"Task: take the {name} out of the white box and put it down on the table.")


class ClutterPerception(TB.BoxPerception):
    def __init__(self, S, rng, out, tag, L, seed_err=0.0, astra=False):
        super().__init__(S, rng, out, tag, gt=not astra)
        self.L, self.seed_err, self.astra = L, seed_err, astra
        self.task_text = task_sentence(L) if astra else " ".join(f"{s} {o}" for s, o in L["steps"])

    def plan(self, feedback=None):
        return self.plan_astra() if self.astra else self.plan_gt()

    def plan_astra(self):
        """The one Astra call: steps [(skill, object name)] and seeds: each mark's D415 ray and the surface it meets
        there (no depth there: the 3 cm plane), as plan_gt's."""
        obs, img, o, _ = self.frame("d415")
        prompt = CLUTTER_PROMPT_PUSH if ASTRA_PUSH else CLUTTER_PROMPT
        js, txt, info = self.ask([{"role": "user", "content": [
            {"type": "text", "text": prompt.format(task=self.task_text, W=img.shape[1], H=img.shape[0])}, self.img_block(img)]}])
        if js is None and txt:                                     # (11-04: 2 of 24 replies wrote "grasp":[x,y],[x,y]]:
            fixed = re.sub(r'"grasp"\s*:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]\s*,\s*\[', r'"grasp": [[\1, \2], [', txt)   # no plan)
            try:
                js = json.loads(fixed[fixed.index("{"):fixed.rindex("}") + 1])
                info = dict(info, repaired=True)
            except Exception:
                js = None
        self.steps, self.seeds, marks = [], [], []
        skills = dict(CLUTTER_SKILLS, push="push") if ASTRA_PUSH else CLUTTER_SKILLS
        for st in (js or {}).get("steps") or []:
            sk = skills.get(str(st.get("skill", "")).strip())
            if sk is None:
                continue
            seed = None
            try:
                if sk == "push":                                   # (11-04) from: the surface its ray meets; to: its
                    if st.get("from") is None or st.get("to") is None:   # ray at that height (the point slides level)
                        continue
                    uv = np.asarray(st["from"], float) + o
                    ray = self.ray_of(obs, uv); p = ray[2]
                    if p is None or not (-0.75 < p[0] < 0.0 and -0.35 < p[1] < 0.60 and -0.02 < p[2] < 0.25):
                        eye_, d_ = ray[0], ray[1]
                        p = eye_ + d_ * (0.01 - eye_[2]) / d_[2]; ray = (eye_, d_, p)
                    eye2, d2, _ = self.ray_of(obs, np.asarray(st["to"], float) + o)
                    to = eye2 + d2 * (p[2] - eye2[2]) / d2[2]
                    marks += [(tuple(st["from"]), self.COLS["grasp"]), (tuple(st["to"]), self.COLS["place"])]
                    self.steps.append((sk, str(st.get("object", "")).strip().lower()))
                    self.seeds.append(dict(p=np.asarray(p, float), ray=ray, uv=None, to=np.asarray(to, float)))
                    continue
                if st.get("grasp") and len(st["grasp"]) == 2:
                    if any(np.asarray(g_, float).shape != (2,) for g_ in st["grasp"]):
                        continue                                   # (11-04: a mark with one number: no step, no crash)
                    uv = (np.asarray(st["grasp"][0], float) + np.asarray(st["grasp"][1], float)) / 2 + o
                    marks += [(tuple(p), self.COLS["grasp"]) for p in st["grasp"]]
                elif st.get("point") is not None:
                    uv = np.asarray(st["point"], float) + o
                    marks.append((tuple(st["point"]), self.COLS["place"]))
                else:
                    uv = None
                if uv is not None:
                    ray = self.ray_of(obs, uv)
                    p = ray[2]
                    if p is None or not (-0.75 < p[0] < 0.0 and -0.35 < p[1] < 0.60 and -0.02 < p[2] < 0.25):
                        eye_, d_ = ray[0], ray[1]                  # (11-02: a mark on the thin screwdriver: the depth
                        p = eye_ + d_ * (0.01 - eye_[2]) / d_[2]   # there was the floor behind the table; the look was
                        ray = (eye_, d_, p)                        # out of reach): the ray at the table, 1 cm up
                    seed = dict(p=np.asarray(p, float), ray=ray, uv=None)
            except Exception:
                seed = None
            if seed is None:
                continue                                           # (no usable mark: the step cannot run)
            self.steps.append((sk, str(st.get("object", "")).strip().lower()))
            self.seeds.append(seed)
        if marks:
            cv2.imwrite(str(self.dir / f"{self.tag}_plan.jpg"), cv2.cvtColor(self.draw(img, [((float(m_[0][0]), float(m_[0][1])), m_[1]) for m_ in marks
                                                                                              if np.asarray(m_[0]).shape == (2,)]), cv2.COLOR_RGB2BGR))
        self.astra_log.append(dict(t=float(self.S.d.time), keys=["plan"], rounds=[dict(reply=txt, **info)], final=js, img=f"{self.tag}_plan.jpg"))
        return self.steps

    def plan_gt(self):
        """The plan's points from the true state, as marks on the scene camera's image: the D415's ray through the
        point, the surface it meets there (a mark moved by --seed-err may meet a neighbour)."""
        S, m, d, L = self.S, self.S.m, self.S.d, self.L
        eye = d.cam_xpos[m.camera("d415").id].copy(); gid = np.zeros(1, np.int32)
        rng = np.random.default_rng([L["seed"], L["layout"], 77])

        def seed_at(X, err=0.0):
            X = np.asarray(X, float).copy()
            if err > 0:
                a = rng.uniform(0, 2 * math.pi); X[:2] += err * np.r_[math.cos(a), math.sin(a)]
            u = (X - eye) / np.linalg.norm(X - eye)
            dist = S.mj.mj_ray(m, d, eye, u, None, 1, -1, gid)
            hit = eye + dist * u if dist > 0 else X
            return dict(p=hit, ray=(eye, u, hit), uv=None)
        self.steps, self.seeds = [], []
        for sk, nm in L["steps"]:
            if sk == "pick":
                X = obj_extent(S, L["target"]); X[2] -= 0.003
                tb = S.ob[L["target"]]
                for q in [X] + [X + [0.012 * math.cos(a), 0.012 * math.sin(a), 0.0] for a in np.arange(4) * math.pi / 2]:
                    u = (q - eye) / np.linalg.norm(q - eye)          # (the first of the 5 points the scene camera sees
                    S.mj.mj_ray(m, d, eye, u, None, 1, -1, gid)       # ON the target: as Astra would mark what it sees)
                    if gid[0] >= 0 and S.root_of(m.geom_bodyid[gid[0]]) == tb:
                        X = q; break
                if L["scene"] == "bin" and L["seen_rays"] < 2:            # not seen: "it is in the box"
                    bp, _ = S.pose("box_white"); X = bp + [0, 0, 0.004]
                    sd = seed_at(X)
                else:
                    sd = seed_at(X, self.seed_err)
            elif sk == "in":
                bp, _ = S.pose("box_white"); sd = seed_at(bp + [0, 0, 0.004])
            else:
                sd = dict(p=np.r_[L["down"], 0.0], ray=None, uv=None)
            self.steps.append((sk, nm)); self.seeds.append(sd)
        self.astra_log.append(dict(t=float(d.time), keys=["plan (true state)"], rounds=[dict(reply="", s=0.0)], final=None, img=None))
        return self.steps

    def wrist_rgbd(self):
        return B.Perception.observe(self, "L_wrist")

    def scene_depth(self):
        """The scene camera's depth alone (no photoreal colour: fast) and the robot's own pixels (its model at the
        current joint angles, as a real system renders its URDF): for the checks of what came up with the hand."""
        S, m, d, mj = self.S, self.S.m, self.S.d, self.S.mj
        cam = "d415"; rr = self.renderers[B.SIZE[cam]]; Wc, Hc = B.SIZE[cam]
        mj.mj_forward(m, d)
        cid = m.camera(cam).id
        f = Hc / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        K = (Wc, Hc, f, f, Wc / 2, Hc / 2)
        opt = mj.MjvOption(); opt.sitegroup[:] = 0
        refl = rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION]
        rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION] = 0
        rr.enable_depth_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); dep = rr.render().copy(); rr.disable_depth_rendering()
        rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION] = refl
        rr.enable_segmentation_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); seg = rr.render().copy(); rr.disable_segmentation_rendering()
        gid = np.clip(seg[..., 0], 0, m.ngeom - 1)
        arm = (seg[..., 1] == int(mj.mjtObj.mjOBJ_GEOM)) & (seg[..., 0] >= 0) & self.arm_geom[gid]
        depth = np.where(np.isfinite(dep) & (dep < 10), dep, np.inf).astype(np.float32)
        sig = 0.08 * depth ** 2 / (f * B.CAM[cam]["baseline"])
        noisy = depth + self.rng.normal(0, 1, depth.shape).astype(np.float32) * np.where(np.isfinite(sig), sig, 0)
        noisy = np.where(depth > B.MIN_Z, noisy, np.inf).astype(np.float32)
        R_nom, eye_nom = S.nominal_camera(cam)
        return dict(cam=cam, rgb=None, depth=noisy, K=K, R=R_nom, eye=eye_nom, arm=arm, t=float(d.time))

    def segment_points(self, img, pts, labels):
        """SAM3 for clicks (sam3_points_server.py :8134): its 3 masks, best first."""
        buf = io.BytesIO(); np.savez(buf, image=np.ascontiguousarray(img), points=np.asarray(pts, float), labels=np.asarray(labels, int))
        z = np.load(io.BytesIO(urllib.request.urlopen(urllib.request.Request(POINTS_URL, data=buf.getvalue()), timeout=120).read()))
        return [(z["masks"][j], float(z["scores"][j])) for j in np.argsort(-z["scores"])]


# ---------------------------------------------------------------- the grasp planner (depth points, a target mask)
MARGINS = [(0.009, 0.003), (0.006, 0.002), (0.004, 0.0015)]   # (gripper opening beyond the object, each side; the
                                                              # boxes' footprint margin): tried in turn, the roomiest first
TIGHT = os.environ.get("CLUTTER_TIGHT", "0") == "1"          # (11-05, user: "about 0.8 cm gap should be enough") a last
if TIGHT:                                                     # level: 3 mm opening margin, 1 mm footprint margin (with
    MARGINS = MARGINS + [(0.003, 0.001)]                      # the wedge's 3-6 mm tips: ~8 mm free beside the thing)
TOP_DRAG = os.environ.get("CLUTTER_TOP_DRAG", "1") == "1"    # (11-05, user: "push objects from the side, not from the
                                                              # top") 0: no drag on a thing's top (closed fingers pressed
                                                              # on it): every drag is a side push (drag -> side_push_any)
SWEEP = os.environ.get("CLUTTER_SWEEP", "0") == "1"         # (11-05, user: "close the gripper, go to table surface
                                                              # level, and push the disturbing object(s) a few cm away so
                                                              # that the desired object is then graspable") no grasp
                                                              # clears: push_to_clear first (before dragging the target
                                                              # out or picking blockers up); side pushes start with the
                                                              # fingertips 5 mm over the table, 5 mm behind the contact
PUSH = (0.004, 0.0015, 0.008)                                # last resort (CLUTTER_PUSH=1): only the pads' inner 9 mm
                                                              # must clear; their outer part may land on a neighbour's
                                                              # edge and push it aside
CHECKS = os.environ.get("CLUTTER_CHECKS", "1") == "1"         # (11-02, default) the software fixes: after a grasp's
                                                              # close, a lift of 2.5 cm and a scene-camera look at what
                                                              # came up (not the target: set it back, open); closed > 6
                                                              # mm wider than planned: let go; the match's colour (yellow
                                                              # / white); drags: the way also in the scene camera (what
                                                              # the wrist's look does not cover is not free), after it
                                                              # back off what was pushed and check nothing hangs on the
                                                              # fingers (a tape's hole); the travel move's first rise only
                                                              # as high as the arm reaches there. CLUTTER_CHECKS=0: off
RISE_PX = 40                                                  # scene-camera pixels risen >= 8 mm: something came up
COLOUR_RULES = {"yellow": lambda h, s_, v: s_ >= 120 and 15 <= h <= 40,   # (wrist looks 11-02, HSV medians: yellow tape
                "white": lambda h, s_, v: s_ <= 110 and v >= 140}         # S 163-239, off-white S 40-75, H 23-28 both)


def colour_ok(rgb, mask, phrase, colour_rules=None):
    """Does the mask's colour fit the colour word in the name (yellow / white; others not checked: seen from above the
    red tape is mostly its pale top)? colour_rules: COLOUR_RULES (the twin's renders) or the robot's camera's."""
    cr = COLOUR_RULES if colour_rules is None else colour_rules
    rules = [cr[w] for w in phrase.replace("-", " ").lower().split() if w in cr]
    if not rules or rgb is None:
        return True
    mk = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    if mk.sum() < 50:
        return True
    h, s_, v = np.median(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[mk], 0)
    return all(r(h, s_, v) for r in rules)


POUR = os.environ.get("CLUTTER_POUR", "0") == "1"             # (11-02, fix 2) the pick fails in a box: pour the box out
                                                              # onto the table (grip a wall, turn it over), pick again


def rot_axis(axis, t):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K


ROUND_GRASP = os.environ.get("CLUTTER_ROUND_GRASP", "0") == "1"   # (11-05) round things gripped at mid-height (plan_grasps;
                                                              # task_lunch turns it on; off: the clutter bench as it was)
PERSIST = os.environ.get("CLUTTER_PERSIST", "1") == "1"       # (11-02, default) declutter harder: up to 6 things put
                                                              # aside and 3 drags (11-01: 2 + 2); a thing that is not the
                                                              # target may be grasped with PUSH (it may disturb others).
                                                              # Pile 14 -> 17/24, box 6 -> 6/24 (out/clutter_persist).
                                                              # CLUTTER_PERSIST=0: 11-01's skill (out/clutter_final2)
SIDE_PUSH = os.environ.get("CLUTTER_SIDE_PUSH", "1") == "1"   # (11-04) push things from the side (the target out of
                                                              # the crowd, a blocker away from it) before a drag on top
TALL_LOOK = os.environ.get("CLUTTER_TALL_LOOK", "1") == "1"   # (11-04) wrist looks high enough for the things around
                                                              # the target to be beyond the D415's shortest range
                                                              # (ClutterProgram.tall_extra); 0: 2.5 cm beyond it over
                                                              # the target itself, as before
UNSPREAD = os.environ.get("CLUTTER_UNSPREAD", "auto")         # (11-03) a real D415 spreads a nearer surface ~4 px over the
                                                              # farther one (task_fast.EDGE_PX): the gaps between touching
                                                              # things fill in, every grasp looks blocked (realistic depth:
                                                              # pile 19 -> 8/24, the spread alone 4/13). The wrist looks
                                                              # the skill plans in get it taken back (task_fast.unspread).
                                                              # auto: with a spreading depth only (D415_DEPTH=real, the
                                                              # robot); the ideal sim has none (it would shrink things)


def table_heights(obs, tol=0.004, iters=200):
    """Heights over the table in a scene-camera look: z minus a plane fitted to its table points (RANSAC on the points
    within 3 cm of z 0), not z itself: a scene camera 1 deg off its nominal pose tilts the table up to ~1.5 cm across
    the reach (11-03, realistic depth: "no free spot on the table" in 9/24 piles). -> (heights (H, W), ok)"""
    P, ok = F.world(obs)
    cand = ok & (np.abs(P[..., 2]) < 0.03)
    X = P[cand][::7]
    if len(X) < 200:
        return P[..., 2], ok
    rng = np.random.default_rng(0)
    best, best_n = None, -1
    for _ in range(iters):
        a, b, c = X[rng.choice(len(X), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-9 or abs(n[2]) < 0.9 * np.linalg.norm(n):
            continue                                              # (nearly level planes only)
        n = n / np.linalg.norm(n) * np.sign(n[2])
        k = int((np.abs((X - a) @ n) < tol).sum())
        if k > best_n:
            best, best_n = (n, a), k
    if best is None:
        return P[..., 2], ok
    n, a = best
    inl = X[np.abs((X - a) @ n) < tol]
    coef = np.linalg.lstsq(np.c_[inl[:, :2], np.ones(len(inl))], inl[:, 2], rcond=None)[0]   # z = c0 x + c1 y + c2
    return P[..., 2] - (P[..., 0] * coef[0] + P[..., 1] * coef[1] + coef[2]), ok


def clear_height(Pn, obst, c, a, q, cam_sign, fm, pad_u1=None):
    """The lowest TCP height for a grasp at c, closing along angle a, opened to q (half width), at which the fingers,
    the hand and the wrist camera, coming straight down, touch none of the points Pn (N, 3) (5 outliers ignored).
    obst: the points that count (not the target's own between the fingers). cam_sign: +1 the camera on the +v side
    (hand yaw = a), -1 (yaw = a + pi). fm: the boxes' footprint margin."""
    u = np.array([math.cos(a), math.sin(a)]); v = np.array([-u[1], u[0]])
    rel = Pn[:, :2] - c
    pu, pv, z = rel @ u, rel @ v, Pn[:, 2]
    au, av = np.abs(pu), np.abs(pv)
    obst = obst | (av > PAD_HW + 0.003)                            # (the target's own points beside the pads count)
    lo, lim = -1.0, None

    def need(sel, bottom):
        zz = z[sel & obst]
        if len(zz) <= 5:
            return -1.0
        return float(np.partition(zz, len(zz) - 6)[len(zz) - 6]) - bottom
    if GRIPPER == "wedge":                                         # the wedge's 17 sections in one pass: each point needs
        U0, U1, HW, BOT = _FB.T                                    # the TCP over it by its z minus the bottom of the
        if pad_u1 is not None:                                     # lowest section over it; the 6th highest of those
            U1 = np.where(_FB_PAD, np.minimum(U1, pad_u1), U1)     # (5 outliers, as need)
        s0 = obst & (av < HW.max() + fm) & (au > q + U0.min() - fm) & (au < q + U1.max() + fm)
        if s0.sum() > 5:
            au_, av_, z_ = au[s0], av[s0], z[s0]
            inb = (au_[:, None] > q + U0 - fm) & (au_[:, None] < q + U1 + fm) & (av_[:, None] < HW + fm)
            req = np.where(inb, z_[:, None] - BOT, -np.inf)
            kb = req.argmax(1); r_ = req[np.arange(len(req)), kb]
            fin = np.isfinite(r_)
            if fin.sum() > 5:
                j = np.flatnonzero(fin)[np.argsort(r_[fin])[fin.sum() - 6]]
                if r_[j] > lo:
                    lo, lim = float(r_[j]), FINGER_NAMES[kb[j]]
    else:
        for nm, (u0, u1, hw, bot) in zip(FINGER_NAMES, FINGER_BOXES):
            if nm == FINGER_NAMES[0] and pad_u1 is not None:
                u1 = min(u1, pad_u1)
            v_ = need((au > q + u0 - fm) & (au < q + u1 + fm) & (av < hw + fm), bot)
            if v_ > lo:
                lo, lim = v_, nm
    v_ = need((au < HAND_BOX[0] + fm) & (av < HAND_BOX[1] + fm), HAND_BOX[2])
    if v_ > lo:
        lo, lim = v_, "hand"
    v_ = need((cam_sign * pv > CAM_BOX[0] - fm) & (cam_sign * pv < CAM_BOX[1] + fm) & (au < CAM_BOX[2] + fm), CAM_BOX[3])
    if v_ > lo:
        lo, lim = v_, "camera"
    clear_height.lim = lim
    return lo + 0.003


def between(Pn, obst, c, a, q, z_tcp):
    """Points of other things between the open fingers, above the fingertips: they would be squeezed in too."""
    u = np.array([math.cos(a), math.sin(a)]); v = np.array([-u[1], u[0]])
    rel = Pn[:, :2] - c
    return int((obst & (np.abs(rel @ u) < q - 0.002) & (np.abs(rel @ v) < PAD_HW) & (Pn[:, 2] > z_tcp - T.FINGERTIP + 0.003)).sum())


def blocking(Pn, obst, c, a, q, z_tcp, fm):
    """Which points keep a grasp (c, a, q) from going down to z_tcp: a mask over Pn."""
    u = np.array([math.cos(a), math.sin(a)]); v = np.array([-u[1], u[0]])
    rel = Pn[:, :2] - c
    au, av, z = np.abs(rel @ u), np.abs(rel @ v), Pn[:, 2]
    hit = np.zeros(len(Pn), bool)
    for u0, u1, hw, bot in FINGER_BOXES:
        hit |= (au > q + u0 - fm) & (au < q + u1 + fm) & (av < hw + fm) & (z > z_tcp + bot - 0.003)
    return hit & (obst | (av > PAD_HW + 0.003))


def plan_grasps(Q, Pn, is_t, support, margins=MARGINS[0], max_open=B.OPEN_W, avoid=None):
    """Grasp candidates for the target's points Q among all points Pn (is_t: Pn's points on the target, its mask
    dilated): every closing direction (6 deg) and, along a long object, every 1 cm; the opening = the object's width
    there + 2 x margin; the TCP height = the lowest the fingers clear, or the wanted one (fingertips 12 mm / 60 % of the
    height under the top: 20 mm / 70 %). -> (candidates best first, geometry)"""
    om, fm = margins[:2]
    pad_u1 = margins[2] if len(margins) > 2 else None
    top = float(np.percentile(Q[:, 2], 97))
    h = max(top - support, 0.008)
    hull = cv2.convexHull(Q[:, :2].astype(np.float32)).reshape(-1, 2).astype(float)
    w_min, a_min = F.narrowest(Q[:, :2])
    u0 = np.array([math.cos(a_min), math.sin(a_min)]); v0 = np.array([-u0[1], u0[0]])
    c0 = ((hull @ u0).min() + w_min / 2) * u0 + (((hull @ v0).min() + (hull @ v0).max()) / 2) * v0
    coef = np.linalg.lstsq(np.c_[Q[:, :2] - c0, np.ones(len(Q))], Q[:, 2], rcond=None)[0]   # its top's slope: a tilted
    tilt = math.atan(math.hypot(coef[0], coef[1]))                                          # thing is gripped along the
    gdir = coef[:2] / max(math.hypot(coef[0], coef[1]), 1e-9)                               # level direction (both
    sel = np.hypot(*(Pn[:, :2] - c0).T) < 0.17                                              # contacts equally high:
    near, obst = Pn[sel][::2], ~is_t[sel][::2]                                              # no turn as they close)
    out = []
    for a in np.radians(np.arange(0, 180, 6.0)):
        if avoid is not None and abs((a - avoid + math.pi / 2) % math.pi - math.pi / 2) < math.radians(10):
            continue                                              # (a pick again: not across the direction that failed)
        u = np.array([math.cos(a), math.sin(a)]); v = np.array([-u[1], u[0]])
        rel = Q[:, :2] - c0; qu, qv = rel @ u, rel @ v
        vlo, vhi = np.percentile(qv, [1, 99])
        long_ = vhi - vlo > max(1.4 * w_min + 0.01, 0.05)       # several places along a long thing only: on a round
        offs = list(np.arange(vlo + 0.015, vhi - 0.015 + 1e-9, 0.01)) if long_ else [(vlo + vhi) / 2]   # one off its
        for s in offs:                                             # middle the fingers push it out sideways (11-01)
            band = np.abs(qv - s) < 0.015
            if band.sum() < 15:
                continue
            lo_, hi_ = np.percentile(qu[band], [1, 99])
            w = float(hi_ - lo_ + 0.003)                          # (+ 1.5 mm a side: SAM's outline inside the edge)
            q = min(max_open, w / 2 + om)
            if q < w / 2 + 0.003:
                continue                                          # (too wide for the gripper)
            c = c0 + u * (lo_ + hi_) / 2 + v * s
            edge = band & ((qu < lo_ + 0.008) | (qu > hi_ - 0.008))   # its top where the fingers touch it (a tilted
            ltop = float(np.percentile(Q[edge, 2], 97)) if edge.sum() >= 8 else top   # tape's middle, a shaft)
            lh = max(ltop - support, 0.006)
            want = min(0.020, 0.7 * lh)                           # fingertips this far under it (12 mm: a tape resting
            if ROUND_GRASP and lh > 0.03:                         # (11-05, task_lunch: a round thing (fruit) is widest
                outer = band & ((qu < lo_ + 0.005) | (qu > hi_ - 0.005))   # at mid-height; 20 mm under a 52 mm top the
                if outer.sum() >= 6 and float(np.percentile(Q[outer, 2], 10)) - support < 0.8 * lh:    # fingers squeezed a
                    want = max(want, ltop - (support + 0.5 * lh - 0.008))   # strawberry out: its outline far lower than
            z_want = max(support + T.FINGERTIP + 0.004, ltop - want + T.FINGERTIP)   # its top: the pads at its middle)
            level = abs(u @ gdir) * min(tilt / math.radians(20), 1.0)
            if tilt > math.radians(12) and abs(u @ gdir) > math.sin(math.radians(25)):
                continue                                          # (tilted: across the slope it turns out of the grip)
            for cs in (1, -1):
                zf = clear_height(near, obst, c, a, q, cs, fm, pad_u1)
                lim = clear_height.lim
                z_g = max(z_want, zf)
                depth = ltop - (z_g - T.FINGERTIP)
                nb = between(near, obst, c, a, q, z_g)
                s_ = float(s - (vlo + vhi) / 2)                   # (a long thing: within a quarter of its length of
                end = abs(s_) > 0.25 * (vhi - vlo) or w < 0.010    # its middle: held at its end it turns down and
                out.append(dict(c=c, a=float(a if cs > 0 else a + math.pi), close_a=float(a), q=float(q), w=w, z=float(z_g), z_free=float(zf),   # slips
                                z_want=float(z_want), depth=float(depth), want=want, s=s_, top=top, ltop=ltop, lh=lh,   # (11-01: a
                                between=nb, fm=fm, level=level, lim=lim if zf > z_want else None,                       # shaft end))
                                ok=bool(depth >= min(0.006, 0.5 * lh) and nb < 15 and not end)))
    for g in out:
        g["score"] = (3 * min(g["depth"], g["want"]) / g["want"] + min(max((2 * max_open - 0.003 - g["w"]) / 0.015, 0), 1)
                      + w_min / max(g["w"], 1e-3) + 0.5 * min(max(g["z_want"] - g["z_free"], 0) / 0.01, 1) - abs(g["s"]) / 0.05
                      + g["lh"] / h - 2 * g["level"] - 2 * (g["w"] < 0.012))   # (a screwdriver: its handle, not the shaft)
    out.sort(key=lambda g: (not g["ok"], -g["score"]))
    return out, dict(top=top, h=h, w_min=w_min, c0=c0, margins=margins, tilt_deg=math.degrees(tilt))


def iou(a, b):
    u = (a | b).sum()
    return float((a & b).sum()) / u if u else 0.0


def dilate(mask, px):
    return cv2.dilate(mask.astype(np.uint8), np.ones((2 * px + 1, 2 * px + 1), np.uint8)) > 0


# ---------------------------------------------------------------- the program
class ClutterProgram(TB.BoxProgram):
    WIDE_YAWS = True                                              # (looks at any of 8 hand angles: the arm reaches one)
    COLOUR_RULES = COLOUR_RULES                                   # (the robot: its camera's, real_box.RealClutterProgram)
    DRAG_TURN_W = 0.0                                             # (the robot: 20 per rad of wrist turn in a drag's key)
    LOCAL_ASIDE = os.environ.get("CLUTTER_LOCAL_ASIDE", "0") == "1"   # (11-03) things in the way moved a few cm, low
                                                                  # (take_away_near), not lifted 32 cm (the robot: on)
    DESCEND = "descend"                                           # (the grasp's descent's phase: the robot settles it)

    def __init__(self, S, per, mode, L):
        super().__init__(S, per)
        self.mode, self.L = mode, L
        self.set_aside = 0
        self.drags = 0
        self.est = None                                            # the target's last seen place (top centre)
        self.reserved = []                                         # table spots later steps put things on

    def run(self):
        self.targets, self.avoid, self.box = {}, {}, None
        steps = self.per.plan()
        self.steps = steps
        self.event("plan", steps=[f"{s}:{o}" for s, o in steps])
        self.sizes = {}
        self.reserved = [self.per.seeds[j]["p"][:2] for j, (sk, _) in enumerate(steps) if sk == "down"]
        for i, (sk, obj) in enumerate(steps):
            try:
                if sk == "pick" and i + 1 < len(steps) and steps[i + 1][0] in ("on", "in"):
                    self.survey(i + 1)
                r = getattr(self, "f_" + sk)(i)
            except T.Unreachable as e:
                return f"step {i} {sk}: unreachable ({e})"
            except F.Unseen as e:
                return f"step {i} {sk}: {e}"
            if r != "ok":
                return f"step {i} {sk}: {r}"
        return "done"

    # -- debug images: the look, SAM3's masks (the chosen one thick), the grasp's finger pads
    def dbg(self, obs, name, masks=(), chosen=None, grasp=None, pts=None):
        if os.environ.get("CLUTTER_DBG", "1") != "1":
            return
        img = cv2.cvtColor(obs["rgb"], cv2.COLOR_RGB2BGR).copy()
        for j, (mk, sc) in enumerate(masks):
            cnts, _ = cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(img, cnts, -1, (0, 255, 255), 1)
            ys, xs = np.nonzero(mk)
            if len(xs):
                cv2.putText(img, f"{sc:.2f}", (int(xs.mean()), int(ys.mean())), 0, 0.4, (0, 255, 255), 1)
        if chosen is not None:
            cnts, _ = cv2.findContours(chosen.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(img, cnts, -1, (0, 255, 0), 2)
        if pts is not None:
            for X in pts:
                uv = F.project(obs, X)
                cv2.circle(img, (int(uv[0]), int(uv[1])), 3, (255, 0, 255), -1)
        if grasp is not None:
            u = np.array([math.cos(grasp["close_a"]), math.sin(grasp["close_a"])]); v = np.array([-u[1], u[0]])
            for sg in (1, -1):
                cs = [grasp["c"] + sg * u * (grasp["q"] + du) + v * dv for du, dv in ((0, -0.013), (0.017, -0.013), (0.017, 0.013), (0, 0.013))]
                poly = np.array([F.project(obs, np.r_[cc, grasp["top"]]) for cc in cs], np.int32)
                cv2.polylines(img, [poly], True, (0, 0, 255) if grasp["ok"] else (0, 128, 255), 2)
            cv2.putText(img, f"depth {grasp['depth'] * 1000:.0f}mm w {grasp['w'] * 1000:.0f} q {grasp['q'] * 1000:.0f} between {grasp['between']}",
                        (10, 470), 0, 0.5, (0, 0, 255), 1)
        self._dbg_n = getattr(self, "_dbg_n", 0) + 1
        fn = f"{self.per.tag}_{self._dbg_n:02d}_{name}.jpg"
        cv2.imwrite(str(self.per.dir / fn), img)
        self.S.timeline.append((float(self.S.d.time), "dbg", fn))   # (render_clutter_video.py shows it from then on)

    # -- looks
    def unspread_obs(self, obs):
        """A wrist look with the D415's edge spread taken back (UNSPREAD; the raw depth kept as depth_raw)."""
        on = UNSPREAD == "1" or (UNSPREAD == "auto" and (getattr(self.per, "depth_model", None) is not None
                                                         or getattr(self.per, "REAL_DEPTH", False)))
        if not on or obs.get("depth") is None:
            return obs
        return dict({k: v for k, v in obs.items() if k != "_valid"}, depth=F.unspread(obs["depth"]), depth_raw=obs["depth"])

    def look_at(self, X, r, what):
        """The wrist camera straight down over X, seeing a disc of radius r: the observation (colour + depth). If
        something tall stands between the camera and X (the box's open lid: 11-01 a look into the box saw the lid's
        inside, SAM took a dark ring at the image's edge for the target), from the other side(s) of the hand: the
        least hidden look."""
        S = self.S
        hl = self.look_height(r)
        yaws = list(self.look_yaws(X[:2]))
        y = self.best_yaw(np.r_[X[:2], X[2] + hl], yaws)
        extra = self.tall_extra(X, r, hl) if TALL_LOOK else 0.0      # (11-04) higher over taller things around it
        while extra > 0.005:                                       # (as high as the arm reaches, in 2 cm steps)
            R = B.down(y); tcp = self.look_tcp(X, R, X[2] + hl + extra)
            if S.ik(tcp, R, S.q_cmd)[1] < 0.002 or S.ik(tcp, R, S.q_home)[1] < 0.002:
                break
            extra -= 0.02
        if extra > 0.005:
            hl += extra
            self.event("look higher: taller things around it", extra_mm=round(extra * 1000))
        best, tried = None, []
        for k in range(3):
            R = B.down(y)
            tcp = self.look_tcp(X, R, X[2] + hl)
            if S.ik(tcp, R, S.q_cmd)[1] < 0.002 or S.ik(tcp, R, S.q_home)[1] < 0.002 or k == 0:
                self.transit(tcp[:2], R)
                S.move(tcp, R, B.OPEN_W, 1.0, what if k == 0 else what + " (from another side)")
                S.hold(0.2)
                obs = self.unspread_obs(self.per.wrist_rgbd())
                occ = self.hidden(obs, X, r)
                if best is None or occ < best[0]:
                    best = (occ, obs)
                if occ < 0.25:
                    break
                self.event("look: hidden by something tall, from another side", hidden=round(occ, 2))
            tried.append(y)
            y = max(yaws, key=lambda yy: min(abs((yy - t + math.pi) % (2 * math.pi) - math.pi) for t in tried))
        obs = best[1]
        obs["look"] = np.asarray(X, float).copy()
        return obs

    def tall_extra(self, X, r, hl):
        """(11-04) How much higher than hl a wrist look over X must be for the things around X (within r + 6 cm) to be
        beyond the D415's shortest depth range (B.MIN_Z) + 2 cm: their height in the scene camera's depth (98th
        percentile; the robot's own pixels left out). (11-04, the twin with the real arm's softness: a look 30 cm over
        a tape's top had no depth on 81 % of a glue bottle 5 cm taller beside it: the grasp planner took it for free
        space, the fingers came down on it, "blocked on the way down" 12 times in 2 piles; the stiff twin's servo had
        pushed it aside.) <= 12 cm."""
        try:
            so = self.per.scene_depth()
        except Exception:
            return 0.0
        Ps, oks = F.world(so)
        sel = oks & (np.linalg.norm(Ps[..., :2] - np.asarray(X[:2], float), axis=-1) < r + 0.06) & (Ps[..., 2] < 0.30)
        if so.get("arm") is not None:
            sel &= ~dilate(so["arm"], 4)
        if sel.sum() < 30:
            return 0.0
        h = float(np.percentile(Ps[sel][:, 2], 98))
        cam_z = X[2] + hl + self.cam_up                            # (the camera's height in that look)
        return float(min(max(0.0, h + B.MIN_Z + 0.02 - cam_z), 0.12))

    def hidden(self, obs, X, r):
        """How much of the disc (radius r) around X the camera does not see: the fraction of its pixels where the
        depth shows something > 6 cm above X (or X out of the image)."""
        W, Hh = int(obs["K"][0]), int(obs["K"][1])
        ring = [F.project(obs, np.r_[X[:2] + r * np.array([math.cos(a), math.sin(a)]), X[2]]) for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)]
        poly = np.array(ring, np.int32)
        disc = np.zeros((Hh, W), np.uint8); cv2.fillPoly(disc, [poly], 1)
        uv = F.project(obs, X)
        if not (0 <= uv[0] < W and 0 <= uv[1] < Hh) or disc.sum() < 50:
            return 1.0
        P, ok = F.world(obs)
        d_ = disc > 0
        return float(((P[..., 2] > X[2] + 0.06) & ok & d_).sum() + (~ok & d_).sum() * 0.5) / float(d_.sum())

    def masks_of(self, obs, phrase):
        """SAM3's masks for a text prompt in a colour look, with their points (eroded 2 px, valid depth)."""
        P, ok = F.world(obs)
        out = []
        n_bad = 0
        for mk, sc in self.per.segment(obs["rgb"], phrase):
            if mk.sum() > 60000 or (mk & ok).sum() < 40:
                continue
            if CHECKS and sc >= 0.1 and not colour_ok(obs["rgb"], mk, phrase, self.COLOUR_RULES):
                n_bad += 1                                         # (11-02: the off-white tape as "yellow tape roll"
                continue                                           # 0.8: taken out instead of the yellow one)
            out.append(dict(mask=mk, raw=mk, Q=P[mk & ok], score=float(sc)))
        if n_bad:
            self.event("colour check: not " + phrase, masks=n_bad)
        return out

    def find(self, obs, phrase, seed, ray, search=False, zone=None):
        """The target's mask in a colour look: SAM3 on the object's name; of its masks (score >= 0.35) the one nearest
        the point (the scene camera's ray through it: <= 3 cm), else, searching, the best sure one (>= 0.5) in the
        zone (centre, radius), else a weak one (>= 0.15) that a "small object" mask agrees with (IoU >= 0.5: tilted,
        dragged, half hidden, the name scores low). -> (mask, points, info) or None. Keeps all the name's masks
        (>= 0.1) of this look: nothing that overlaps them is taken away as "another thing" (11-01: the target
        itself was put aside as a blocker / as the topmost thing, again and again)."""
        allm = self.masks_of(obs, phrase)
        self._name_obs, self._name_masks = obs, [c["mask"] for c in allm if c["score"] >= 0.1 and c["mask"].sum() < 30000]
        cands = [c for c in allm if c["score"] >= 0.35]
        for c in cands + [c for c in allm if 0.15 <= c["score"] < 0.35]:
            Q = c["Q"]
            dist = float(np.linalg.norm(Q[:, :2] - seed[:2], axis=1).min())
            if ray is not None:
                o, u_, _ = ray; v_ = Q - o
                dist = min(dist, float(np.linalg.norm(v_ - np.outer(v_ @ u_, u_), axis=1).min()))
            c["dist"] = dist
            c["zone"] = None if zone is None else float(np.linalg.norm(np.median(Q[:, :2], 0) - zone[0]))
        self._last_masks = [(c["mask"], c["score"]) for c in cands]
        info = dict(masks=len(cands), scores=[round(c["score"], 2) for c in cands][:6])
        near = [c for c in cands if c["dist"] < 0.03 and (c["score"] >= 0.5 or not search)]
        best = None
        if near:
            best = max(near, key=lambda c: c["score"] - 3 * c["dist"])
        elif search:                                               # (searching: a sure one, the nearest of the sure)
            far = [c for c in cands if c["score"] >= 0.5 and (zone is None or c["zone"] < zone[1])]
            best = max(far, key=lambda c: c["score"] - 2 * c["zone"]) if far else None
            if best is None:
                weak = [c for c in allm if 0.15 <= c["score"] < 0.5 and (zone is None or c["zone"] < zone[1])]
                objs = self.others(obs, check_name=False) if weak else []
                for c in sorted(weak, key=lambda c: -c["score"]):
                    o = max(objs, key=lambda o: iou(o["mask"], c["mask"]), default=None)
                    if o is not None and iou(o["mask"], c["mask"]) >= 0.5:
                        best = dict(o, score=c["score"], dist=c["dist"], zone=c["zone"])
                        info["agreed"] = round(iou(o["mask"], c["mask"]), 2)
                        break
        if best is None:
            return None
        info.update(score=round(best["score"], 2), dist_mm=round(best["dist"] * 1000), n=len(best["Q"]))
        self._raw = best["raw"]
        return best["mask"], best["Q"], info

    def is_target(self, obs, mk):
        """Does this mask overlap one of SAM3's masks for the target's name in this look (score >= 0.1)?"""
        if getattr(self, "_name_obs", None) is not obs:
            self._name_obs = obs
            self._name_masks = [c["mask"] for c in self.masks_of(obs, self._phrase) if c["score"] >= 0.1 and c["mask"].sum() < 30000]
        return any(iou(mk, t) > 0.3 or (mk & t).sum() > 0.5 * min(mk.sum(), t.sum()) for t in self._name_masks)

    def support_under(self, obs, mask, Q):
        """The surface the target stands on: the low end (10th percentile) of the depth just around its mask."""
        P, ok = F.world(obs)
        ring = dilate(mask, 15) & ~dilate(mask, 3) & ok
        top = float(np.percentile(Q[:, 2], 97))
        if ring.sum() < 30:
            return top - 0.02
        return float(min(np.percentile(P[ring][:, 2], 10), top - 0.005))

    def locate(self, i, phrase, digs=3):
        """Look for the step's object (by name) at its point; not there: a wider look (search); still not: take the
        topmost thing there away (it may be under it) and search again (<= digs times); then a look centred on it.
        -> (obs, mask, Q, info)"""
        sd = self.per.seeds[i]; s = sd["p"].copy(); ray = sd.get("ray")
        if self.est is not None:                                   # (seen, dragged before: where it is now)
            s, ray = self.est.copy(), None
        obs = self.look_at(np.r_[s[:2], max(s[2], 0.0)], 0.05, "look at " + phrase)
        f = self.find(obs, phrase, s, ray)
        self.dbg(obs, "look", getattr(self, "_last_masks", ()), None if f is None else f[0])
        zone = (s[:2], 0.16)
        for dig in range(digs + 1):
            if f is not None:
                break
            obs = self.look_at(np.r_[s[:2], max(s[2] - 0.02, 0.0)], 0.13, "search for " + phrase)   # (a box: all of it)
            f = self.find(obs, phrase, s, ray, search=True, zone=zone)
            self.dbg(obs, "search", getattr(self, "_last_masks", ()), None if f is None else f[0])
            self.event("search", found=f is not None, digs=dig, **({} if f is None else f[2]))
            if f is None:
                if dig == digs or not self.dig(obs, zone):
                    raise F.Unseen(f"{phrase} not found")
        mask, Q, info = f
        X = np.r_[np.median(Q[:, :2], 0), float(np.percentile(Q[:, 2], 97))]
        obs = self.look_at(X, 0.05, "look at " + phrase + " (centred)")   # the grasp's view: centred on it (an object
        f2 = self.find(obs, phrase, X, None)                               # cut by the image border: whole)
        if f2 is not None:
            mask, Q, info = f2
        self.est = np.r_[np.median(Q[:, :2], 0), float(np.percentile(Q[:, 2], 97))]
        self.event("found " + phrase, **info)
        return obs, mask, Q, info

    def f_pick(self, i):
        if self.mode == "depth":
            return super().f_pick(i)
        try:
            r = self.pick_seg(i)
        except F.Unseen as e:
            r, err = str(e), e
        else:
            err = None
        if r != "ok" and POUR and not getattr(self, "poured", False):
            land = self.pour_out(self.per.seeds[i]["p"])
            if land is not None:
                self.poured = True
                self.est = np.r_[land, 0.02]                       # (look for it where the things landed)
                r, err = self.pick_seg(i), None
        if err is not None:
            raise err
        if r != "ok":
            return r
        g = self.last_g; H = self.held                            # (as task_box's f_pick: the footprint for put_in)
        R = H["R"]; xa, ya = R[:2, 0], R[:2, 1]
        Qr = g["Q"][:, :2] - g["c"]
        hx_ = np.percentile(Qr @ xa, [2, 98]); hy_ = np.percentile(Qr @ ya, [2, 98])
        H.update(hx=float(np.ptp(hx_) / 2), hy=float(np.ptp(hy_) / 2), cx=float((hx_.max() + hx_.min()) / 2),
                 cy=float((hy_.max() + hy_.min()) / 2), grip=float(-H["bottom_off"]), name=self.steps[i][1],
                 round=len(g["dirs"]) > 90, top_h=float(g["top"] - g["table"]))
        H["h"] = H["top_h"]
        return "ok"

    def covering(self, obs, mask, Q, n_min=150, frac=0.25):
        """Something else over the target: (a) higher points inside its outline's convex hull (a tape half under a
        fallen glue bottle: SAM sees a crescent, a grasp on that closes on nothing), or (b) an occlusion boundary: a
        quarter or more of the band just outside its outline is higher than its top (11-01: a tape half under the
        cable, the visible half's hull holds none of the cable). -> pixel mask of the higher things or None"""
        P, ok = F.world(obs)
        top = float(np.percentile(Q[:, 2], 75))
        cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        hull = np.zeros(mask.shape, np.uint8)
        cv2.fillConvexPoly(hull, cv2.convexHull(np.vstack(cnts)), 1)
        high = ok & (P[..., 2] > top + 0.006)
        cov = (hull > 0) & ~dilate(mask, 2) & high
        band = dilate(mask, 8) & ~dilate(mask, 2)
        hb = band & high
        self._cov_px = int(cov.sum()); self._occ_frac = round(float(hb.sum()) / max(int((band & ok).sum()), 1), 2)
        if cov.sum() >= n_min:
            return cov
        if self._occ_frac >= frac and hb.sum() >= n_min:
            return dilate(hb, 2) & high
        return None

    def config_ok(self, q):
        """(A hook: the robot refuses big wrist turns and elbow twists, real_box.RealClutterProgram.)"""
        return True

    def descent_blocked(self, z_goal, R, q):
        """(A hook: the robot's soft arm stops on what a finger lands on; the sim's stiff one pushes it aside.) True:
        it was, the hand is back up 3 cm, the grasp is off."""
        return False

    def reach(self, X, R):
        q, e = self.S.ik(np.asarray(X, float), R, self.S.q_cmd)
        return e < 0.002 and self.config_ok(q)

    def turn_of(self, R):
        """The hand's turn (rad) from its present angle to R's (about the vertical)."""
        return abs((T.yaw_of(R) - T.yaw_of(self.S.R_cmd) + math.pi) % (2 * math.pi) - math.pi)

    def grasp_allowed(self, c):
        """(A hook: the robot's safety limits, real_box.RealClutterProgram; may raise the grasp c's height.)"""
        return True

    def best_grasp(self, obs, mask, Q, avoid=None, push=False):
        """The best clear grasp the arm reaches for the thing (mask, points Q) in this look, the roomiest margins
        first. -> (grasp or None, all candidates of the last margins tried, geometry, support, Pn, is_t)"""
        S = self.S
        P, ok = F.world(obs)
        Pn = P[ok]; is_t = dilate(mask, 4)[ok]                    # (SAM's outline can sit ~2 mm inside the depth edge)
        support = self.support_under(obs, mask, Q)
        if (Q[:, 2] > support + 0.004).sum() >= 40:                # (not what it stands on: a tape's hole shows the
            Q = Q[Q[:, 2] > support + 0.004]                       # table, which stays there after the lift: the pick
        for mg in MARGINS + ([PUSH] if push or os.environ.get("CLUTTER_PUSH", "0") == "1" else []):   # (read as "wrong object in the hand")
            cands, geo = plan_grasps(Q, Pn, is_t, support, mg, avoid=avoid)
            for c in [c for c in cands if c["ok"]][:12]:              # the best one the arm reaches (both heights)
                R = B.down(c["a"])
                if not self.grasp_allowed(c):
                    continue
                if S.ik(np.r_[c["c"], c["z"]], R, S.q_cmd)[1] < 0.002 and S.ik(np.r_[c["c"], c["z"] + 0.15], R, S.q_cmd)[1] < 0.002:
                    return c, cands, geo, support, Pn, is_t, Q
        return None, cands, geo, support, Pn, is_t, Q

    def pick_seg(self, i, tries=2, aside=2):
        aside, n_drags = (6, 3) if PERSIST else (aside, 2)
        phrase = self.steps[i][1]
        self._phrase = phrase
        avoid, removed, dragged, swept = None, 0, 0, 0
        for attempt in range(tries + aside + 2 + (4 if SWEEP else 0)):
            obs, mask, Q, info = self.locate(i, phrase)
            cov = self.covering(obs, getattr(self, "_raw", mask), Q)
            if cov is not None and removed < aside:                # something lies on it: that first
                self.event("something lies on it: take that away first", points=int(cov.sum()))
                c = self.segment_at(obs, cov, mask)
                if c is not None and self.take_away(obs, c["mask"], c["Q"], "it lay on the target"):
                    removed += 1
                    continue
            g, cands, geo, support, Pn, is_t, Q = self.best_grasp(obs, mask, Q, avoid)
            b = g or (cands[0] if cands else None)
            self.event("grasp plan", candidates=len(cands), clear=sum(c["ok"] for c in cands), top_mm=round(geo["top"] * 1000),
                       covering_px=getattr(self, "_cov_px", None), occluded=getattr(self, "_occ_frac", None), tilt_deg=round(geo["tilt_deg"]),
                       support_mm=round(support * 1000), w_min_mm=round(geo["w_min"] * 1000), found=g is not None, best=None if b is None else dict(
                           a_deg=round(math.degrees(b["close_a"])), w_mm=round(b["w"] * 1000), q_mm=round(b["q"] * 1000), margin_mm=round((b["q"] - b["w"] / 2) * 1000, 1),
                           depth_mm=round(b["depth"] * 1000, 1), z_mm=round(b["z"] * 1000), s_mm=round(b["s"] * 1000), between=b["between"],
                           local_top_mm=round(b["ltop"] * 1000), limit=b["lim"]),
                       limits={k: sum(c["lim"] == k for c in cands) for k in FINGER_NAMES + ("hand", "camera")},
                       between_n=sum(c["between"] >= 15 for c in cands))
            self.dbg(obs, "plan", self._last_masks, mask, b)
            if g is None and GRIPPER == "thin":                    # (thin plates) a tape lying flat, its hole open:
                ring = self.ring_of(obs, mask, Q, support, geo)       # from inside it, no room beside it needed
                self.event("a tape's open hole?", found=ring is not None, why=getattr(self, "_ring_why", None))
                r = self.ring_grasp(ring, Pn, Q, support, geo) if ring is not None else None
                if r == "ok":
                    return "ok"
                if r is not None:
                    self.event("grip from inside failed", result=r)
            if g is None and SWEEP and swept < 4 and self.push_to_clear(obs, mask, Q, Pn, is_t, cands, support):
                swept += 1                                         # (11-05) push what is in a grasp's way aside
                continue
            if g is None:                                          # nothing clears: drag it out of the crowd (twice
                if dragged < n_drags and self.singulate(obs, mask, Q, support):   # at most), then take a blocking thing away
                    dragged += 1
                    continue
                if not cands or removed >= aside:
                    return "no clear grasp"
                self.remove_blocker(obs, Pn, is_t, cands, mask)
                removed += 1
                continue
            r = self.grasp(g, Q, geo, support)
            if r == "ok":
                return "ok"
            avoid = g["close_a"]
            if r == "wrong object":
                self.put_aside_held("wrong object in the hand")
            if attempt >= tries + aside + 1:
                return r
        return "no grasp"

    def grasp(self, g, Q, geo, support, strict=True):
        S = self.S
        R = B.down(g["a"]); c = g["c"]; z_g = g["z"]; q = g["q"]
        o_pre = self.per.scene_depth() if CHECKS and strict else None
        z_hi = max(S.p_cmd[2], geo["top"] + 0.10)
        while z_hi > geo["top"] + 0.06 and S.ik(np.r_[c, z_hi], R, S.q_cmd)[1] > 0.002:   # (11-03, the robot's joint
            z_hi -= 0.02                                                                   # limits: the present height
        S.move(np.r_[c, z_hi], R, q, 1.0, "over the grasp (opened to fit)")                # out of reach over it)
        S.move(np.r_[c, z_g], R, q, 1.2, self.DESCEND)
        S.hold(0.15)
        if self.descent_blocked(z_g, R, q):
            return "blocked on the way down"
        S.move(np.r_[c, z_g], R, 0.0, 0.6, "close")
        for attempt in range(2):
            for _ in range(15):
                w0 = S.finger_width(); S.hold(1 / 15)
                if abs(S.finger_width() - w0) < 0.0003:
                    break
            if S.finger_width() < 2 * q - 0.006 or attempt == 1:
                break
            self.event("gripper blocked: open, 6 mm higher, close again", width_mm=S.finger_width() * 1000)
            z_g += 0.006
            S.move(np.r_[c, z_g], R, q, 0.4); S.move(np.r_[c, z_g], R, 0.0, 0.6, "close again")
        w = S.finger_width()
        self.event("grasp", width_mm=round(w * 1000, 1), expected_mm=round(g["w"] * 1000, 1), depth_mm=round(g["depth"] * 1000, 1),
                   opened_mm=round(2 * q * 1000), a_deg=round(math.degrees(g["close_a"])))
        if CHECKS and w > g["w"] + 0.006:                          # (11-02: 9 grasps closed > 6 mm wider than planned,
            self.event("closed wider than planned: something else in the grip, let go")   # all in failed episodes:
            self.let_go(c, z_g, R, q)                              # the box wall, a neighbour; median -4 mm)
            return "something else in the grip"
        if CHECKS:                                                 # lift a little: did anything else come up?
            o0 = self.per.scene_depth()
            S.move(np.r_[c, z_g + 0.025], R, 0.0, 0.6, "lift a little"); S.hold(0.2)
            r_t = float(np.percentile(np.linalg.norm(Q[:, :2] - c, axis=1), 95))
            n, X = self.risen(o0, self.per.scene_depth(), around=(c, r_t + 0.012))
            load = self.hand_load()                                # (the box came up: > 2 N; one of these things
            self.event("lifted a little", px=n, load_n=round(load, 2), strict=strict)   # (not strict: a thing taken away
            # from the target; lifting the target a little with it is no harm (11-02: a removal that worked was undone))
            if (n >= RISE_PX if strict else n >= 10 * RISE_PX) or load > 2.0:   # weighs <= 113 g: a Franka's force estimate feels
                self.event("something else came up with it: set it back, let go", px=n, load_n=round(load, 2),   # 2 N,
                           at_mm=None if X is None else np.round(X * 1000).tolist())                          # not a tape)
                S.move(np.r_[c, z_g], R, 0.0, 0.5, "set it back down"); S.hold(0.2)
                self.let_go(c, z_g, R, q)
                return "lifted another thing"
        S.move(np.r_[c, max(0.32, z_g + 0.20)], R, 0.0, 1.2, "lift")
        S.hold(0.2)
        if CHECKS and strict:                                      # lifted clear of the box: what hangs at the hand?
            r_t = float(np.percentile(np.linalg.norm(Q[:, :2] - c, axis=1), 95))
            n = self.hanging(r_t, max(geo["top"] - support, 0.01), o_pre)
            if n >= RISE_PX:
                self.event("something else hangs at the hand: put it all back, let go", px=n)
                S.move(np.r_[c, z_g + 0.01], R, 0.0, 1.2, "back down"); S.hold(0.2)
                self.let_go(c, z_g + 0.01, R, q)
                return "lifted another thing"
        held = S.finger_width() > 0.0025
        after = self.per.wrist()                                  # the target's own surface: still there?
        frac, seen, n = F.surface_left(after, Q)
        there = seen >= max(15, 0.15 * n) and frac >= 0.5
        self.event("after the lift", held=held, surface_there=round(frac, 2), surface_seen=f"{seen}/{n}")
        if there:
            return "wrong object" if held else "missed (closed on nothing)"
        if not held:
            return "dropped"
        h_obj = max(geo["top"] - support, 0.01)
        tcp = S.tcp_now()
        off, top = np.zeros(3), geo["top"] + tcp[2] - z_g
        bottom_off = top - h_obj - tcp[2]
        self.held = dict(k=None, R=R, h=h_obj, off=off, bottom_off=bottom_off, width=w)
        aa, ww = F.widths(Q[:, :2])
        self.last_g = dict(Q=Q, c=c, top=geo["top"], table=support, width=g["w"], dirs=aa[ww < geo["w_min"] + 0.002].tolist())
        return "ok"

    def hand_load(self):
        """The upward force the hand (fingers) puts on other things (N): what a wrist F/T sensor / the Franka's
        external-force estimate reads as the load carried (the sim's contact forces, world z)."""
        S, m, d = self.S, self.S.m, self.S.d
        f6 = np.zeros(6); tot = 0.0
        arm = getattr(self, "_arm_bodies", None)
        if arm is None:
            arm = self._arm_bodies = {b for b in range(m.nbody) if m.body(b).name.startswith("L_")}
        for i in range(d.ncon):
            c = d.contact[i]; b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            if (b1 in arm) == (b2 in arm):
                continue
            S.mj.mj_contactForce(m, d, i, f6)
            F_ = c.frame.reshape(3, 3).T @ f6[:3]                 # on geom2, world frame
            tot += F_[2] if b1 in arm else -F_[2]
        return float(tot)

    def ring_of(self, obs, mask, Q, support, geo):
        """A tape lying flat with its hole open, from the target's points (a circle fit) and the depth inside the hole
        (>= 10 mm under its top for 80 %: not covered). -> dict(c, r_in, r_out, top, floor) or None"""
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
        if hole.sum() < 30:
            return None
        hz = P[hole][:, 2]
        self._ring_why = f"hole covered: {(hz < top - 0.010).mean():.2f} open"
        if (hz < top - 0.010).mean() < 0.8:
            return None
        self._ring_why = None
        return dict(c=c, r_in=r_in, r_out=r_out, top=top, floor=float(np.median(hz)))

    def ring_grasp(self, ring, Pn, Q, support, geo, strict=True):
        """Grip a tape from inside its hole: the closed fingers down into it (tips 10 mm under its top, >= 3 mm over
        what is under it), then opened against its inner wall: no room beside the tape needed. Only if the closed
        fingertips fit with 2 mm all round (the thin plates: 23 mm across; not the fin-ray pads: 41 mm in a 37 mm
        hole, 11-02: they wedged). -> "ok" / a reason / None (not possible here)"""
        S = self.S
        if 2 * math.hypot(*CLOSED_HALF) + 0.003 > 2 * ring["r_in"]:   # (1.5 mm all round: the white tape's 26 mm
            self.event("hole too small for the closed fingers", hole_mm=round(2 * ring["r_in"] * 1000))   # hole fits 23)
            return None
        c = ring["c"]
        z_tcp = max(ring["top"] - 0.010, ring["floor"] + 0.003) + T.FINGERTIP
        allp = np.ones(len(Pn), bool)
        R = None
        for a in np.radians([0.0, 45.0, 90.0, 135.0]):
            for cs in (1, -1):
                if clear_height(Pn, allp, c, a, 0.0, cs, 0.002) > z_tcp + 0.002:
                    continue
                Rt = B.down(a if cs == 1 else a + math.pi)
                if S.ik(np.r_[c, z_tcp], Rt, S.q_cmd)[1] < 0.002 and S.ik(np.r_[c, z_tcp + 0.15], Rt, S.q_cmd)[1] < 0.002:
                    R = Rt
                    break
            if R is not None:
                break
        if R is None:
            return None
        self.event("grip from inside the hole", hole_mm=round(2 * ring["r_in"] * 1000), at_mm=np.round(c * 1000).tolist())
        o_pre = self.per.scene_depth() if CHECKS and strict else None
        S.move(np.r_[c, max(S.p_cmd[2], ring["top"] + 0.12)], R, 0.0, 1.0, "over its hole (fingers closed)")
        S.move(np.r_[c, z_tcp], R, 0.0, 1.0, "down into the hole")
        S.hold(0.15)
        q_cmd = min(B.OPEN_W, ring["r_in"] + 0.003)
        S.move(np.r_[c, z_tcp], R, q_cmd, 0.6, "open against its inner wall")
        for _ in range(15):
            w0 = S.finger_width(); S.hold(1 / 15)
            if abs(S.finger_width() - w0) < 0.0003:
                break
        w = S.finger_width()
        if w > 2 * (ring["r_in"] - CLOSED_HALF[0]) + 0.006:       # opened past where the wall is: not in it
            S.move(np.r_[c, z_tcp], R, 0.0, 0.5, "close"); S.move(np.r_[c, z_tcp + 0.10], R, 0.0, 0.6, "back off")
            return "no wall to push on"
        if CHECKS:
            o0 = self.per.scene_depth()
            S.move(np.r_[c, z_tcp + 0.025], R, q_cmd, 0.6, "lift a little"); S.hold(0.2)
            n, X = self.risen(o0, self.per.scene_depth(), around=(c, ring["r_out"] + 0.012))
            load = self.hand_load()
            self.event("lifted a little", px=n, load_n=round(load, 2), strict=strict)
            if (n >= RISE_PX if strict else n >= 10 * RISE_PX) or load > 2.0:
                self.event("something else came up with it: set it back, let go", px=n, load_n=round(load, 2))
                S.move(np.r_[c, z_tcp], R, q_cmd, 0.5, "set it back down"); S.hold(0.2)
                S.move(np.r_[c, z_tcp], R, 0.0, 0.5, "close"); S.move(np.r_[c, z_tcp + 0.10], R, 0.0, 0.6, "back off")
                return "lifted another thing"
        S.move(np.r_[c, max(0.32, z_tcp + 0.20)], R, q_cmd, 1.2, "lift")
        S.hold(0.2)
        if CHECKS and strict and self.hanging(ring["r_out"], max(ring["top"] - support, 0.01), o_pre) >= RISE_PX:
            self.event("something else hangs at the hand: put it all back, let go")
            S.move(np.r_[c, z_tcp + 0.01], R, q_cmd, 1.2, "back down"); S.hold(0.2)
            S.move(S.p_cmd, R, 0.0, 0.5, "close"); S.move(S.p_cmd + [0, 0, 0.10], R, 0.0, 0.6, "back off")
            return "lifted another thing"
        held = S.finger_width() < 2 * q_cmd - 0.002                # (still held open by the wall)
        frac, seen, n_ = F.surface_left(self.per.wrist(), Q)
        there = seen >= max(15, 0.15 * n_) and frac >= 0.5
        self.event("after the lift", held=held, surface_there=round(frac, 2), surface_seen=f"{seen}/{n_}", inside=True)
        if there or not held:
            S.move(S.p_cmd, R, 0.0, 0.4, "close")
            return "missed (the tape stayed)" if there else "dropped"
        h_obj = max(ring["top"] - support, 0.01)
        tcp = S.tcp_now()
        top = ring["top"] + tcp[2] - z_tcp
        self.held = dict(k=None, R=R, h=h_obj, off=np.zeros(3), bottom_off=top - h_obj - tcp[2], width=2 * ring["r_out"],
                         internal=True, hold=q_cmd)
        self.last_g = dict(Q=Q, c=c, top=ring["top"], table=support, width=2 * ring["r_out"], dirs=list(range(120)))
        return "ok"

    def hanging(self, r_t, h_obj, o_pre=None):
        """The hand lifted clear of everything: scene-camera pixels in the air near it (> 13 cm up, within 15 cm), not
        the robot, beyond where the target can be (farther than its radius + 20 mm from the hand's axis, or more than
        25 mm under its bottom, its height taken >= 30 mm): something else hangs on (11-02: inside the box the walls hide a small lift; a cable,
        a second tape came up with 5 thin-finger grasps). -> pixels"""
        o = self.per.scene_depth(); P, ok = F.world(o)
        tcp = self.S.tcp_now()
        dxy = np.linalg.norm(P[..., :2] - tcp[:2], axis=-1)
        air = ok & ~dilate(o["arm"], max(3, self.ARM_GROW)) & (P[..., 2] > 0.13) & (dxy < 0.15)
        if o_pre is not None:                                      # only what changed since before the grasp (the
            air &= ~(np.isfinite(o_pre["depth"]) & (np.abs(o["depth"] - o_pre["depth"]) < 0.01))   # box's open lid
            # stands there too: 27 of 31 alarms false, 11-02)
        side = air & (dxy > r_t + 0.020)                           # (margins: the target's own size is a guess from
        below = air & (P[..., 2] < tcp[2] - T.FINGERTIP - max(h_obj, 0.03) - 0.025)   # its top; tuned 11-02)
        k_ = max(3, self.RISE_OPEN)
        extra = cv2.morphologyEx((side | below).astype(np.uint8), cv2.MORPH_OPEN, np.ones((k_, k_), np.uint8)) > 0
        self.event("hanging at the hand", air_px=int(air.sum()), beyond_target_px=int(extra.sum()), side_px=int(side.sum()),
                   below_px=int(below.sum()))
        return int(extra.sum())

    def pour_out(self, seed):
        """Fix 2: the target is in a box too full to pick from: grip one of the box's walls (fingers across it, 20 mm
        open; the inner pad where the inside is free along the wall: clear_height with the wall as the thing held), lift
        it 15 cm, turn it over (130 deg about the wall, 10 deg steps, the way the arm reaches all along and the contents
        land on free table), shake, turn it back, set it down until it touches, let go. -> where the contents landed (xy)
        or None (not in a box / no wall to grip / not reachable)"""
        S = self.S
        try:
            box, obs = self.look_box(np.r_[seed[:2], 0.0])
        except Exception as e:
            self.event("pour: no box", why=str(e)[:80]); return None
        c, u, v, hu, hv, rim = box["c"], np.asarray(box["u"])[:2], np.asarray(box["v"])[:2], box["hu"], box["hv"], box["rim"]
        rel = np.asarray(seed[:2]) - c
        if abs(rel @ u) > hu + 0.01 or abs(rel @ v) > hv + 0.01 or not (0.03 < rim < 0.10):
            self.event("pour: the target is not in a box", rel_mm=np.round(np.r_[rel @ u, rel @ v] * 1000).tolist(),
                       half_mm=[round(hu * 1000), round(hv * 1000)], rim_mm=round(rim * 1000)); return None
        P, ok = F.world(obs); Pn = P[ok]
        so = self.per.scene_depth(); Ps, oks = F.world(so)
        Ts = Ps[oks & ~dilate(so["arm"], 4) & (Ps[..., 2] > 0.012) & (Ps[..., 2] < 0.25)][:, :2][::3]   # things on the table
        lid_out = None if box["lid"] is None else -np.asarray(box["lid"]["n_in"])
        best = None
        for nrm, h, along, ha in ((u, hu, v, hv), (-u, hu, v, hv), (v, hv, u, hu), (-v, hv, u, hu)):
            if lid_out is not None and float(nrm @ lid_out) > 0.9:
                continue                                           # (not the lid's hinge wall)
            for s_ in (0.0, 0.03, -0.03, 0.06, -0.06):
                g = c + nrm * (h - 0.0015) + along * s_
                rl = Pn[:, :2] - g
                wall = (np.abs(rl @ nrm) < 0.006) & (np.abs(rl @ along) < ha + 0.01)
                a = math.atan2(nrm[1], nrm[0])
                z_tcp = rim - 0.018 + T.FINGERTIP                  # the pads 18 mm down the wall
                for yaw, cs in ((a, 1), (a + math.pi, -1)):
                    if clear_height(Pn, ~wall, g, a, 0.010, cs, 0.002) > z_tcp + 0.002:
                        continue
                    R0 = B.down(yaw)
                    X = np.r_[g, z_tcp + 0.15]
                    q0, e0 = S.ik(np.r_[g, z_tcp], R0, S.q_cmd)
                    if e0 > 0.002:
                        continue
                    ax = np.r_[along, 0.0]                         # (the box's axes are in the table plane)
                    for sense in (1, -1):
                        q, okk = S.ik(X, R0, q0)[0], True
                        for th in np.radians(np.arange(10, 131, 10)):
                            q1, e = S.ik(X, rot_axis(ax * sense, th) @ R0, q)
                            if e > 0.003 or np.abs(q1 - q).max() > 0.6:
                                okk = False; break
                            q = q1
                        if not okk:
                            continue
                        Rb = rot_axis(ax * sense, math.radians(130))  # where the contents go: the box's middle and
                        mid = X + Rb @ np.r_[-nrm * h, -0.03]              # its opening, turned over
                        land = (mid + Rb @ np.r_[0, 0, 0.06])[:2]
                        if not (-0.58 < land[0] < -0.10 and -0.20 < land[1] < 0.44):
                            continue
                        crowd = int((np.linalg.norm(Ts - land, axis=1) < 0.10).sum()) if len(Ts) else 0
                        key = crowd + 30 * abs(s_)
                        if best is None or key < best[0]:
                            best = (key, g, R0, X, z_tcp, ax, sense, land, crowd)
        if best is None:
            self.event("pour: no wall to grip and turn"); return None
        _, g, R0, X, z_tcp, ax, sense, land, crowd = best
        self.event("too full: pour the box out", grip_mm=np.round(g * 1000).tolist(), land_mm=np.round(land * 1000).tolist(),
                   crowd=crowd)
        S.move(S.p_cmd, S.R_cmd, 0.010, 0.4, "open 20 mm")
        self.transit(g, R0)
        S.move(np.r_[g, rim + 0.10], R0, 0.010, 1.0, "over the wall")
        S.move(np.r_[g, z_tcp], R0, 0.010, 1.0, "down over the wall")
        S.move(np.r_[g, z_tcp], R0, 0.0, 0.6, "grip the wall"); S.hold(0.4)
        w = S.finger_width()
        if not (0.001 < w < 0.009):
            self.event("pour: no wall between the fingers", width_mm=round(w * 1000, 1))
            S.move(np.r_[g, z_tcp], R0, B.OPEN_W, 0.4); S.move(np.r_[g, z_tcp + 0.10], R0, B.OPEN_W, 0.6, "back off")
            return None
        S.move(X, R0, 0.0, 1.5, "lift the box")
        for th in np.radians(np.arange(10, 131, 10)):
            S.move(X, rot_axis(ax * sense, th) @ R0, 0.0, 0.3, "turn it over" if th < 0.2 else None)
        S.hold(0.6)
        Rt = rot_axis(ax * sense, math.radians(130)) @ R0
        for k in range(2):                                         # (shake)
            S.move(X, rot_axis(ax * sense, math.radians(-12)) @ Rt, 0.0, 0.25, "shake" if k == 0 else None)
            S.move(X, Rt, 0.0, 0.25)
        S.hold(0.5)
        for th in np.radians(np.arange(120, -1, -10)):
            S.move(X, rot_axis(ax * sense, th) @ R0, 0.0, 0.3, "turn it back" if th > 2.05 else None)
        S.move(np.r_[g, z_tcp + 0.02], R0, 0.0, 1.2, "set it down")
        cmd = S.p_cmd.copy()
        for j in range(40):
            cmd[2] -= 0.001
            S.move(cmd, R0, 0.0, 0.06); S.hold(0.03)
            if self.hand_force() > 3.0:
                break
        S.move(S.p_cmd, R0, B.OPEN_W, 0.5, "let go of the box"); S.hold(0.3)
        S.move(S.p_cmd + [0, 0, 0.12], R0, B.OPEN_W, 0.8, "back off")
        self.box = None
        return land

    def let_go(self, c, z, R, q):
        """Open fully (11-02: opened only to the grasp's width, the pad still rubbed the box wall it had caught, and the
        box came up with the hand, tipped over), rise 2 cm, check nothing came up (else down, open, again), back off."""
        S = self.S
        S.move(np.r_[c, z], R, B.OPEN_W, 0.5, "let go"); S.hold(0.3)
        for k in range(2):
            o0 = self.per.scene_depth()
            S.move(np.r_[c, z + 0.02], R, B.OPEN_W, 0.5, "up a little"); S.hold(0.15)
            n, X = self.risen(o0, self.per.scene_depth())
            if n < RISE_PX:
                break
            self.event("something came up with the open hand: set it down", px=n)
            S.move(np.r_[c, z], R, B.OPEN_W, 0.5, "set it down"); S.hold(0.3)
        S.move(np.r_[c, z + 0.10], R, B.OPEN_W, 0.8, "back off")

    RISE_DZ = 0.008                                               # (11-05) a rise that counts; the robot: 15 mm
    RISE_OPEN = 0                                                 # speckle filter (px) on the risen mask; the robot: 5
    ARM_GROW = 4                                                  # the arm's pixels grown by this; the robot: 8

    def risen(self, o0, o1, around=None, dz=None):
        """Scene-camera pixels where something is now >= dz (RISE_DZ) higher than before (two scene_depth looks), not
        the robot's own (dilated ARM_GROW px) and, with around = (centre xy, radius), not within it (the target, lifted
        on purpose); within 30 cm of the hand; RISE_OPEN: a morphological opening of the mask (11-05, the real scene
        camera: with 8 mm and no opening, 158-1455 noise pixels > 8 mm on the black table, the check fires at 40: two
        good grasps of the yellow tape set back down). -> (pixels, their mean world point)"""
        dz = self.RISE_DZ if dz is None else max(dz, self.RISE_DZ)
        P0, ok0 = F.world(o0); P1, ok1 = F.world(o1)
        sel = ok0 & ok1 & ~dilate(o0["arm"] | o1["arm"], self.ARM_GROW) & (P1[..., 2] - P0[..., 2] > dz) & (P1[..., 2] < 0.45)
        tcp = self.S.tcp_now()
        sel &= (P0[..., 2] > -0.01) & (np.linalg.norm(P1[..., :2] - tcp[:2], axis=-1) < 0.30)   # (near the hand, above
        if self.RISE_OPEN:
            sel = cv2.morphologyEx(sel.astype(np.uint8), cv2.MORPH_OPEN, np.ones((self.RISE_OPEN, self.RISE_OPEN), np.uint8)) > 0
        # the table: the room behind it, metres away, has cm of depth noise: false "came up", 11-02)
        if sel.any():                                              # not lifted, only shifted sideways: an edge that
            G, lo = 0.004, tcp[:2] - 0.36                          # moved a few mm jumps by the thing's height; a
            A = P0[ok0 & ~dilate(o0["arm"], 4)]                    # surface that high within 12 mm before explains it
            A = A[np.all(np.abs(A[:, :2] - tcp[:2]) < 0.355, axis=1)]   # (11-02: 31 false alarms of 43)
            hmap = np.full((180, 180), -1.0, np.float32)
            ij = ((A[:, :2] - lo) / G).astype(int)
            np.maximum.at(hmap, (ij[:, 0], ij[:, 1]), A[:, 2].astype(np.float32))
            hmap = cv2.dilate(hmap, np.ones((7, 7), np.uint8))
            ys, xs = np.nonzero(sel)
            ij = np.clip(((P1[ys, xs, :2] - lo) / G).astype(int), 0, 179)
            shifted = P1[ys, xs, 2] <= hmap[ij[:, 0], ij[:, 1]] + 0.004
            sel[ys[shifted], xs[shifted]] = False
        if around is not None:
            sel &= np.linalg.norm(P1[..., :2] - np.asarray(around[0])[:2], axis=-1) > around[1]
        sel = cv2.morphologyEx(sel.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
        n = int(sel.sum())
        return n, (P1[sel].mean(0) if n else None)

    def transit(self, xy, R=None):
        """task_box's transit, but the first rise only as high as the arm reaches HERE (11-02: after a drag at the
        reach's edge, "up to 34 cm" there was out of reach: the episode ended "unreachable")."""
        if not CHECKS:
            return super().transit(xy, R)
        S = self.S
        R = S.R_cmd if R is None else R
        if np.linalg.norm(S.p_cmd[:2] - xy) < 0.01:
            return

        def top(p, RR):                                            # the highest the arm reaches at p (<= safe height)
            for z_ in np.arange(max(self.safe_z(), S.p_cmd[2]), self.safe_z() - 0.15, -0.02):
                if S.ik(np.r_[p, z_], RR, S.q_cmd)[1] < 0.002:
                    return float(z_)
            return None
        z = top(xy, R)
        z = max(self.safe_z(), S.p_cmd[2]) if z is None else z
        z_up = top(S.p_cmd[:2], S.R_cmd)
        z_up = min(z, z_up) if z_up is not None else S.p_cmd[2]
        if S.p_cmd[2] < z_up - 0.005:
            S.move(np.r_[S.p_cmd[:2], z_up], S.R_cmd, S.g_cmd, 0.5, "up")
        if not self.joint_move(np.r_[xy, z], R, check=True):
            try:
                S.move(np.r_[xy, z], R, S.g_cmd, 1.5, "transit")
            except T.Unreachable:
                self.joint_move(np.r_[xy, z], R)

    # -- making room
    def free_spot(self, near_xy, clear=0.07, r_max=None):
        """A free spot on the table (the scene camera's depth: nothing above 1 cm within `clear`), reachable, not on
        a spot a later step needs, nearest to near_xy (r_max: no farther from it)."""
        S = self.S
        obs = self.per.observe("d415")
        P, ok = F.world(obs)
        hz, _ = table_heights(obs)                                 # (11-03: over the fitted table, not z 0)
        occ = P[ok & (hz > 0.01) & (hz < 0.40)][:, :2][::4]
        base = S.base[:2]
        best = None
        for x in np.arange(-0.58, -0.08, 0.02):
            for y in np.arange(-0.18, 0.44, 0.02):
                p = np.r_[x, y]
                if not 0.38 < np.linalg.norm(p - base) < 0.60:
                    continue
                if any(np.linalg.norm(p - r_) < 0.12 for r_ in self.reserved):
                    continue
                if len(occ) and np.min(np.linalg.norm(occ - p, axis=1)) < clear:
                    continue
                d_ = float(np.linalg.norm(p - near_xy))
                if r_max is not None and d_ > r_max:
                    continue
                if best is None or d_ < best[0]:
                    y1 = math.atan2(*(p - base)[::-1]) + math.pi / 2
                    if S.ik(np.r_[p, 0.10], B.down(y1), S.q_home)[1] < 0.002:
                        best = (d_, p)
        if best is None:
            raise F.Unseen("no free spot on the table")
        self.reserved.append(best[1])
        return best[1]

    def put_down(self, X, what, transit=True):
        """The held object down on the table at X (x, y, table z): over it, down until the hand feels it, open.
        transit=False: from where the hand is, no rise (take_away_near: carried there low)."""
        S, H = self.S, self.held
        gh = H.get("hold", 0.0)                                    # (gripped from inside: held open, let go closing)
        g_rel, g_after = (0.0, 0.0) if H.get("internal") else (min(B.OPEN_W, H["width"] / 2 + 0.008), B.OPEN_W)
        if transit:
            self.transit(self._over(X, 0.06)[:2], H["R"])
            S.move(self._over(X, 0.06), H["R"], gh, 1.2, what)
        S.move(self._over(X, 0.012), H["R"], gh, 0.6, "down to it")
        cmd = S.p_cmd.copy()
        for j in range(40):
            cmd[2] -= 0.001
            S.move(cmd, H["R"], gh, 0.08, "set down" if j == 0 else None); S.hold(0.04)
            if self.hand_force() > 1.5:
                self.event("contact", steps=j + 1); break
        rel = cmd + [0, 0, 0.001]
        S.move(rel, H["R"], gh, 0.15, "unload")
        S.move(rel, H["R"], g_rel, 0.4, "release"); S.hold(0.2)
        S.move(rel + [0, 0, 0.10], H["R"], g_after, 0.8, "retreat")
        self.held = None

    def take_away_near(self, g, Q, geo, support, why, r_max=0.15, rise=0.04):
        """LOCAL_ASIDE (the robot, 11-03 user: "just move it a few cm away"): grasp the thing, lift it `rise` (not to
        32 cm, no wrist-camera check), carry it low to the nearest free table spot within r_max (its bottom 2 cm over
        whatever the scene camera sees on the way), set it down. No spot that near: the usual put-aside. -> bool"""
        S = self.S
        R = B.down(g["a"]); c = g["c"]; z_g = g["z"]; q = g["q"]
        z_hi = max(S.p_cmd[2], geo["top"] + 0.10)
        while z_hi > geo["top"] + 0.06 and S.ik(np.r_[c, z_hi], R, S.q_cmd)[1] > 0.002:
            z_hi -= 0.02
        S.move(np.r_[c, z_hi], R, q, 1.0, "over the grasp (opened to fit)")
        S.move(np.r_[c, z_g], R, q, 1.2, self.DESCEND)
        S.hold(0.15)
        if self.descent_blocked(z_g, R, q):
            self.event("take away failed", why=why, result="blocked on the way down")
            return False
        S.move(np.r_[c, z_g], R, 0.0, 0.6, "close")
        for _ in range(15):
            w0 = S.finger_width(); S.hold(1 / 15)
            if abs(S.finger_width() - w0) < 0.0003:
                break
        w = S.finger_width()
        self.event("grasp (to move it aside)", width_mm=round(w * 1000, 1), expected_mm=round(g["w"] * 1000, 1), a_deg=round(math.degrees(g["close_a"])))
        if w < 0.002 or w > g["w"] + 0.006:                        # (nothing / something else in the grip)
            self.let_go(c, z_g, R, q)
            self.event("take away failed", why=why, result="missed" if w < 0.002 else "something else in the grip")
            return False
        h_obj = max(geo["top"] - support, 0.01)
        S.move(np.r_[c, z_g + rise], R, 0.0, 0.6, "lift a little")
        top_now = geo["top"] + rise
        self.held = dict(k=None, R=R, h=h_obj, off=np.zeros(3), bottom_off=top_now - h_obj - (z_g + rise), width=w)
        try:
            p = self.free_spot(c, clear=0.06, r_max=r_max)
        except F.Unseen:
            self.event("no free spot within %d cm: the usual put-aside" % round(r_max * 100))
            S.move(np.r_[c, max(0.32, z_g + 0.20)], R, 0.0, 1.2, "lift")
            self.put_aside_held(why)
            return True
        obs = self.per.observe("d415")                             # the way there, low: over what the scene camera sees
        P, ok = F.world(obs)
        hz, _ = table_heights(obs)
        u = p - c; L_ = max(float(np.linalg.norm(u)), 1e-6); u = u / L_
        rel = P[..., :2] - c
        along, across = rel @ u, np.abs(rel @ np.array([-u[1], u[0]]))
        way = ok & (along > 0.04) & (along < L_ + 0.04) & (across < 0.05) & (hz > 0.005) & (hz < 0.25)
        z_way = float(np.percentile(hz[way], 98)) if way.sum() > 20 else 0.0
        z_tcp = max(z_g + rise, z_way + 0.02 - self.held["bottom_off"])
        if z_tcp > S.p_cmd[2] + 0.002:
            S.move(np.r_[c, z_tcp], R, 0.0, 0.6, "up over the way")
        S.move(np.r_[p, z_tcp], R, 0.0, 0.8 + 6 * L_, "carry it aside (low)")
        self.event("moved aside", why=why, to_mm=np.round(p * 1000).tolist(), cm=round(L_ * 100, 1), way_top_mm=round(z_way * 1000))
        self.put_down(np.r_[p, 0.0], "over the spot", transit=False)
        self.set_aside += 1
        return True

    def put_aside_held(self, why):
        S = self.S
        H = self.held or dict(R=S.R_cmd, off=np.zeros(3), bottom_off=-0.02, width=S.finger_width(), h=0.02)
        self.held = H
        p = self.free_spot(S.tcp_now()[:2])
        self.event("put aside", why=why, at_mm=np.round(p * 1000).tolist())
        self.put_down(np.r_[p, 0.0], "put aside")
        self.set_aside += 1

    def take_away(self, obs, mask, Q, why):
        """Pick the thing (mask, points Q) in this look and put it on a free spot of the table. -> bool"""
        g, cands, geo, support, Pn, is_t, Q = self.best_grasp(obs, mask, Q, push=PERSIST)
        if self.LOCAL_ASIDE and g is not None:
            return self.take_away_near(g, Q, geo, support, why)
        self.dbg(obs, "take_away", (), mask, g or (cands[0] if cands else None))
        if g is None and GRIPPER == "thin":
            ring = self.ring_of(obs, mask, Q, support, geo)
            if ring is not None and self.ring_grasp(ring, Pn, Q, support, geo, strict=False) == "ok":
                self.put_aside_held(why)
                return True
        if g is None:
            self.event("take away: no clear grasp", why=why)
            return False
        r = self.grasp(g, Q, geo, support, strict=False)
        if r == "ok" or (r == "wrong object" and self.S.finger_width() > 0.004):   # (11-02: not after a let-go)
            self.put_aside_held(why)
            return True
        self.event("take away failed", why=why, result=r)
        return False

    def others(self, obs, exclude=None, check_name=True):
        """Everything SAM3 calls a "small object" in the look (score >= 0.3), not the excluded mask (the target), nor
        anything that looks like the target (is_target)."""
        out = []
        for c in self.masks_of(obs, "small object"):
            if c["score"] < 0.3:
                continue
            ext = np.ptp(c["Q"][:, :2], axis=0)
            if F.narrowest(c["Q"][:, :2])[0] < 0.012 or ext.max() > 0.20:
                continue                                          # (a box's wall, the ruler: not a thing to move)
            if exclude is not None and (c["mask"] & dilate(exclude, 3)).sum() > 0.15 * exclude.sum():
                continue
            if check_name and self.is_target(obs, c["mask"]):
                continue
            out.append(c)
        return out

    def singulate(self, obs, mask, Q, support, D=0.06):
        """Push the target out of the crowd: from the side if it can be (SIDE_PUSH: away from what touches it, little
        in its way; 11-04, the twin with the real arm's softness: drags on its top stalled after 0-8 mm, the closed
        fingers' friction on its top less than its own on the table), else drag it on its top (see drag). -> bool"""
        if SIDE_PUSH:
            Ld, pl = self.side_push_any(obs, mask, Q, support, 0.05, self.away_from_touching(obs, mask, Q, support),
                                        "crowded: push it out from the side", max_in_way=100)
            if Ld > 0.01:
                self.drags += 1
                self.est = np.r_[pl["c"] + Ld * pl["d"], pl["top"]]   # (looked for there next)
                return True
        if SWEEP and SIDE_PUSH and (Ld := self.hole_push_any(obs, mask, Q, support, 0.05, "crowded: push it out from inside its hole")) > 0.01:
            return True
        return self.drag(obs, mask, Q, support, D, what="crowded: drag it out first", track=True)

    def hole_push_any(self, obs, mask, Q, support, L, what, max_in_way=100):
        """(11-05, SWEEP) The thing, a tape lying flat with its hole open, pushed L from inside its hole: the direction
        (of 24) best away from what touches it with no more than max_in_way points of other things in its way.
        -> the distance pushed (0: none)"""
        ring = self.ring_of(obs, mask, Q, support, {})
        if ring is None:
            return 0.0
        ctx = self.push_ctx(obs, mask, Q, support)
        aw = self.away_from_touching(obs, mask, Q, support)
        dirs = [np.array([math.cos(t), math.sin(t)]) for t in np.radians(np.arange(0, 360, 15.0))]
        if aw is not None:
            dirs.sort(key=lambda d: -float(d @ aw))
        why = {}
        for d in dirs:
            pl = self.plan_hole_push(ctx, ring, d, L)
            if pl is None or pl["in_way"] > max_in_way:
                k = "too much in its way" if pl is not None else self._push_why
                why[k] = why.get(k, 0) + 1
                continue
            Ld = self.do_side_push(pl, what)
            if Ld > 0.01:
                self.drags += 1
                self.est = np.r_[ring["c"] + Ld * d, ring["top"]]
            return Ld
        self.event(what + ": none", **{k.replace(" ", "_").replace("'", "").replace("/", ""): v for k, v in why.items()})
        return 0.0   # (TOP_DRAG=0: a side
                                                                                                  # push, see drag)
    PRESS_DEPTH = 0.005                                           # (a drag's press under the top: the sim's 2 N + 3 mm;
                                                                  # the robot: its DRAG depth, press_depth)
    AWAY_W = 30.0                                                 # (11-04) a drag's key: + AWAY_W x (1 - the cosine of
                                                                  # its way with "away from what touches it")

    def press_depth(self):
        return self.PRESS_DEPTH

    def press_blocked(self, cmd, R, top):
        """(A hook: the robot's soft arm is held up by what the closed fingers land on; the sim's stiff one pushes it
        aside.) True: it was, the hand is back up, no drag."""
        return False

    def after_drag(self):
        """(A hook: the robot's arm may stay behind a drag or push (what it pushed held it): real_box resyncs.)"""

    def slide(self, cmd, d, D, R, phase):
        """A drag's / push's slide from the TCP command cmd: D along d in one straight move (the robot: in 1 cm steps,
        stopped where the arm stays behind, real_box). -> the distance slid"""
        self.S.move(np.asarray(cmd, float) + np.r_[D * d, 0.0], R, 0.0, max(1.2, 2.0 * D / 0.06), phase)
        return D

    def look_reachable(self, xy, top):
        """(11-04) Can the arm look at a thing at xy (its top at height top) from above, as locate does? (11-04, Astra's
        push: a tape pushed 10 cm towards the arm's base, the look over it out of reach: the pick failed.)"""
        S = self.S
        X = np.r_[xy, top]; hl = self.look_height(0.05)
        for y in self.look_yaws(np.asarray(xy, float)):
            R = B.down(y)
            if S.ik(self.look_tcp(X, R, top + hl), R, S.q_home)[1] < 0.002:
                return True
        return False

    def hand_clear(self, Po, path, z_tcp, ac, cs):
        """(11-04) The closed hand (the fin-ray fingers' four sections, the hand, the wrist camera: clear_height's boxes;
        closing axis at angle ac, the camera on side cs) at TCP height z_tcp touches none of the points Po at each of
        the path's points. (11-04, the twin with the real arm's softness: a drag's fingers came down on a fallen glue
        bottle beside the tape, only the fingertips' strip had been checked: the arm held 5-7 cm up, the glue pushed
        6 cm, the next move refused, 4 of 24 piles.)"""
        if not len(Po):
            return True
        allp = np.ones(len(Po), bool)
        return all(clear_height(Po, allp, p_, ac, 0.0, cs, 0.003) <= z_tcp + 0.003 for p_ in path)

    def drag(self, obs, mask, Q, support, D=0.06, away=None, what="drag", track=False, max_cnt=400, way=None):
        """Press the closed fingers on a thing's middle (a tape: on its ring) and slide it D along the freest direction
        (of 24: the fewest depth points of other things > 8 mm up in the strip it sweeps, then away from what touches
        it (11-04); the arm reaching its end at the heights a pick needs; towards the arm on a tie); D or, where that
        is not free or not reachable, 4 cm. away: only directions leading away from that point (a blocker: from the
        target). way: only that direction (rad: Astra's push, f_push), D only. The closed hand (fingers, hand, wrist
        camera) clear of other things all along the way at the press height, any of 4 hand angles (11-04); the arm's
        limits (config_ok) where the hand comes down, the press height reached in a straight line from there (11-04:
        config_ok at the press height compared it with the look 30 cm up: every way refused, 8 of 24 piles, and the
        robot's 17:42 run). More than max_cnt points in the way (~2 cm2 of other things' tops): no drag (a full box).
        -> bool (dragged)
        TOP_DRAG=0 (11-05): no drag on its top: a side push instead (side_push_any: the way, away from `away`, or away
        from what touches it, the closer to that the better; up to 300 points of other things in its way: pushed along)."""
        S = self.S
        if not TOP_DRAG:
            c = np.median(Q[:, :2], 0)
            prefer, cone = self.away_from_touching(obs, mask, Q, support), None
            if way is not None:
                prefer, cone = np.array([math.cos(way), math.sin(way)]), math.radians(20)
            elif away is not None and np.linalg.norm(c - away[:2]) > 1e-3:
                prefer, cone = (c - away[:2]) / np.linalg.norm(c - away[:2]), math.radians(75)
            Ld, pl = self.side_push_any(obs, mask, Q, support, min(D, self.PUSH_MAX), prefer, what + " (from the side)",
                                        max_in_way=300, cone=cone)
            if Ld <= 0.01:
                return False
            self.drags += 1
            if track:
                self.est = np.r_[pl["c"] + Ld * pl["d"], pl["top"]]   # (looked for there next)
            return True
        P, ok = F.world(obs)
        c = np.median(Q[:, :2], 0); top = float(np.percentile(Q[:, 2], 90))
        rt = float(np.percentile(np.linalg.norm(Q[:, :2] - c, axis=1), 95))
        O = P[ok & ~dilate(mask, 4) & (P[..., 2] > support + 0.008)][:, :2][::2] - c   # (not the ruler under a pile)
        Os = np.zeros((0, 2))
        if CHECKS:                                                 # the scene camera too: what the wrist's look does
            so = self.per.scene_depth()                            # not cover is not free (11-02: drags inside the box
            Ps, oks = F.world(so)                                  # "0 in the way", into its wall: it tipped)
            sel = oks & ~dilate(so["arm"], 4) & (Ps[..., 2] > support + 0.008) & (Ps[..., 2] < support + 0.25)
            Xs = Ps[sel]
            dd = np.linalg.norm(Xs[:, :2] - c, axis=1)
            Xs = Xs[(dd > rt + 0.004) & (dd < 0.25)]
            Xc = (Xs - obs["eye"]) @ obs["R"]                      # only where the wrist's look has no say: out of its
            W_, H_, fx_, fy_, cx_, cy_ = obs["K"]                  # image or no depth there (11-02: counted twice, a
            u_ = np.round(fx_ * Xc[:, 0] / np.maximum(Xc[:, 2], 1e-6) + cx_).astype(int)   # free drag was refused)
            v_ = np.round(fy_ * Xc[:, 1] / np.maximum(Xc[:, 2], 1e-6) + cy_).astype(int)
            inside = (Xc[:, 2] > 0) & (u_ >= 0) & (u_ < int(W_)) & (v_ >= 0) & (v_ < int(H_))
            covered = np.zeros(len(Xs), bool)
            covered[inside] = ok[v_[inside], u_[inside]]
            Os = Xs[~covered][:, :2] - c
        Po = P[ok & ~dilate(mask, 4) & (P[..., 2] > support + 0.004)]   # (the hand's clearance: other things' points)
        Po = Po[np.linalg.norm(Po[:, :2] - c, axis=1) < 0.22][::2]
        near = O[np.linalg.norm(O, axis=1) < rt + 0.03]            # what touches it: away from it is better
        aw = -near.mean(0) / np.linalg.norm(near.mean(0)) if len(near) >= 30 and np.linalg.norm(near.mean(0)) > 0.003 else None
        cp_of, cp_why = (lambda d: c), None
        if CHECKS:                                                 # a tape whose hole the closed fingers would wedge in
            ring = self.ring_of(obs, mask, Q, support, {})         # (fin-ray: 41 mm across in a 37 mm hole; 11-02 the
            if ring is not None and 2 * math.hypot(*CLOSED_HALF) > 2 * ring["r_in"] - 0.002:   # tape rode away on them,
                cp_of = lambda d: ring["c"] - d * (ring["r_in"] + ring["r_out"]) / 2           # unseen): press on its
                cp_why = ("press on the tape's ring, not into its hole", dict(hole_mm=round(2 * ring["r_in"] * 1000)))   # ring
            elif ring is None and track and GRIPPER != "thin" and any(w in getattr(self, "_phrase", "") for w in ("tape", "roll")):
                cp_of = lambda d: c - d * 0.85 * rt                # (its name says a tape roll, its hole not seen whole:
                cp_why = ("press near the tape's edge, not into its hole", {})   # 0.85 of its radius is on the ring of
                # all three tapes: red 18.5-23.5 mm, yellow 19-26, white 13-25)
        zp = top + T.FINGERTIP - self.press_depth()                # (the press height: the hand's clearance, the reach)
        z_over = max(S.p_cmd[2], top + 0.10)
        Ds = (D,) if way is not None or D <= 0.045 else (D, 0.04)
        best, why = None, {}

        def no(k):
            why[k] = why.get(k, 0) + 1
        for a in ([way] if way is not None else np.radians(np.arange(0, 360, 15.0))):
            d = np.array([math.cos(a), math.sin(a)]); n_ = np.array([-d[1], d[0]])
            if away is not None and (c - away[:2]) @ d < 0.5 * np.linalg.norm(c - away[:2]):
                continue
            cps = [cp_of(d)] + ([c] if cp_why is not None else [])  # (the press point; on a tape's ring, else its middle)
            for D_ in Ds:
                al, ac_ = O @ d, np.abs(O @ n_)
                cnt = int(((al > 0) & (al < rt + D_ + 0.01) & (ac_ < rt + 0.008)).sum())
                if len(Os):                                        # (a scene pixel covers ~4 wrist pixels' area; the
                    al, ac_ = Os @ d, np.abs(Os @ n_)              # larger of the two: the same thing seen twice is
                    cnt = max(cnt, 4 * int(((al > 0) & (al < rt + D_ + 0.01) & (ac_ < rt + 0.008)).sum()))   # not twice in the way)
                for k_cp, cp in enumerate(cps):
                    e = cp + D_ * d
                    y1 = self.default_yaw(e)                       # (where it ends up: still in reach for a pick:
                    if min(S.ik(np.r_[e, z_], B.down(y1 + da), S.q_home)[1] for z_ in (0.10, 0.34) for da in (0.0, math.pi / 2)) > 0.002:
                        no("its end out of a pick's reach")        # 11-01 a drag took a tape out of reach)
                        continue
                    if not self.look_reachable(c + D_ * d, top):
                        no("its end out of a look's reach")
                        continue
                    path = [cp + t * D_ * d for t in (0.0, 1 / 3, 2 / 3, 1.0)]
                    Rs = []
                    for ac, cs in ((a + math.pi / 2, 1), (a + math.pi / 2, -1), (a, 1), (a, -1)):   # (closing axis
                        if not self.hand_clear(Po, path, zp, ac, cs):   # across the way first: the pads' long side)
                            no("the hand would touch something")
                            continue
                        R_ = B.down(ac if cs == 1 else ac + math.pi)
                        q_o, e_o = S.ik(np.r_[cp, z_over], R_, S.q_cmd)
                        if e_o > 0.002 or not self.config_ok(q_o):
                            no("over it: out of reach or the arm's limits")
                            continue
                        if any(S.ik(np.r_[p_, z_], R_, q_o)[1] > 0.002 for p_ in (cp, e) for z_ in (zp, top + T.FINGERTIP)):
                            no("out of reach at the press height")
                            continue
                        Rs.append(R_)
                    if not Rs:
                        continue
                    R = min(Rs, key=self.turn_of)
                    key = (cnt + 200 * float(np.linalg.norm(e - S.base[:2]) - np.linalg.norm(c - S.base[:2])) + self.DRAG_TURN_W * self.turn_of(R)
                           + (0.0 if aw is None else self.AWAY_W * (1.0 - float(d @ aw))) + (0.0 if D_ == D else 20.0) + 30.0 * k_cp)
                    # (towards the arm; the robot: the smaller wrist turn too; away from what touches it; the full
                    # distance; on the tape's ring)
                    if best is None or key < best[0]:
                        best = (key, a, d, R, cnt, D_, cp, k_cp)
        if best is None:
            self.event(what + ": no way", **{k.replace(" ", "_").replace(":", "").replace("'", ""): v for k, v in why.items()})
            return False
        _, a, d, R, cnt, D, cp, k_cp = best
        if cnt > max_cnt:                                          # (no free way: a full box. Not into other things)
            self.event(what + ": no free way", in_the_way=cnt)
            return False
        self.event(what, dir_deg=round(math.degrees(a)), in_the_way=cnt, at_mm=np.round(c * 1000).tolist(), dist_mm=round(D * 1000),
                   away_cos=None if aw is None else round(float(d @ aw), 2))
        if cp_why is not None and k_cp == 0:
            self.event(cp_why[0], **cp_why[1])
        z0 = top + T.FINGERTIP
        S.move(np.r_[cp, z_over], R, 0.0, 1.0, "over it (fingers closed)")   # behind
        S.move(np.r_[cp, z0 + 0.012], R, 0.0, 0.8, "down to it")
        cmd = self.press_closed(S.p_cmd.copy(), R, top)
        if self.press_blocked(cmd, R, top):
            return False
        D = self.slide(cmd, d, D, R, "drag")
        self.after_drag()
        if CHECKS:
            self.shake_off(d)
        S.move(S.p_cmd + [0, 0, 0.10], R, 0.0, 0.6, "up")
        self.drags += 1
        if track:
            self.est = np.r_[c + D * d, top]                       # (looked for there next)
        return True

    PUSH_MAX = 0.06                                               # (11-04) a push's length cap (Astra's marks: ~10 cm)

    def push_ctx(self, obs, mask, Q, support):
        """What a side push of the thing (mask, points Q) needs from a look: its points above what it stands on, its
        middle and top, all points (the hand coming down behind it) and other things' (along the way)."""
        P, ok = F.world(obs)
        if (Q[:, 2] > support + 0.004).sum() >= 40:
            Q = Q[Q[:, 2] > support + 0.004]
        c = np.median(Q[:, :2], 0); top = float(np.percentile(Q[:, 2], 90))
        allp = P[ok]; allp = allp[(np.linalg.norm(allp[:, :2] - c, axis=1) < 0.25) & (allp[:, 2] > support + 0.003)][::2]
        Po = P[ok & ~dilate(mask, 4) & (P[..., 2] > support + 0.004)]
        Po = Po[np.linalg.norm(Po[:, :2] - c, axis=1) < 0.28][::2]
        return dict(Q=Q, c=c, top=top, support=support, allp=allp, Po=Po)

    def plan_side_push(self, ctx, d, L, base=None):
        """(11-04) A side push of the thing along d by L (no motion): the contact = its outline's rearmost point on the
        line through base (default its middle) along d; the closed fingertips at ~half its height, their face 8 mm
        behind the contact, the whole hand clear of everything there (the thing included) and of other things along
        the way; the arm reaching it within its limits; its new place still in a look's reach (L shortened in 1 cm
        steps to 2 cm). -> dict or None (why in self._push_why)"""
        S = self.S
        Q, c, top, support = ctx["Q"], ctx["c"], ctx["top"], ctx["support"]
        a = math.atan2(d[1], d[0]); n_ = np.array([-d[1], d[0]])
        base = c if base is None else np.asarray(base, float)[:2]
        band = np.abs((Q[:, :2] - base) @ n_) < 0.010
        if band.sum() < 20:                                        # (the line through base misses it: its middle's)
            base = c; band = np.abs((Q[:, :2] - base) @ n_) < 0.010
        if band.sum() < 5:
            self._push_why = "no outline there"; return None
        r = Q[band][int(np.argmin((Q[band][:, :2] - base) @ d)), :2]   # the contact: its rearmost point on that line
        while L > 0.019 and not self.look_reachable(c + L * d, top):   # (its new place: the arm still looks at it)
            L -= 0.01
        if L < 0.019:
            self._push_why = "its new place out of a look's reach"; return None
        zfs = [support + min(max(0.45 * (top - support), 0.006), 0.020)]   # the fingertips at ~half its height
        if SWEEP:                                                  # (11-05) near the table first: the closed
            zfs = [support + self.SWEEP_TIP] + zfs                 # fingertips' thin wedge under its widest part
        runup = 0.005 if SWEEP else 0.008                          # (the fingertips' face this far behind the contact)
        z_over = max(S.p_cmd[2], top + 0.10)
        allp, Po = ctx["allp"], ctx["Po"]
        ahead = ctx.get("Po_way", Po)[:, :2] - r                   # (other things in the thing's way: pushed along)
        ext = float(np.ptp((Q[:, :2] - r) @ d)); wid = float(np.ptp((Q[:, :2] - r) @ n_)) / 2
        in_way = int((((ahead @ d) > ext) & ((ahead @ d) < ext + L + 0.01) & (np.abs(ahead @ n_) < wid + 0.005)).sum())
        for zf in zfs:
            z_tcp = zf + T.FINGERTIP
            opts, self._push_why = [], "no room behind it for the fingers"
            for ac, cs, half in ((a + math.pi / 2, 1, CLOSED_HALF[1]), (a + math.pi / 2, -1, CLOSED_HALF[1]), (a, 1, CLOSED_HALF[0]), (a, -1, CLOSED_HALF[0])):
                gap = half + runup
                s0 = r - d * gap
                if clear_height(allp, np.ones(len(allp), bool), s0, ac, 0.0, cs, 0.002) > z_tcp + 0.002:
                    continue                                       # (no room behind it for the fingers going down)
                end = s0 + d * (gap + L)
                if not self.hand_clear(Po, [s0 + t * (end - s0) for t in (1 / 3, 2 / 3, 1.0)], z_tcp, ac, cs):
                    self._push_why = "the hand would touch something on the way"; continue
                R_ = B.down(ac if cs == 1 else ac + math.pi)
                q_o, e_o = S.ik(np.r_[s0, z_over], R_, S.q_cmd)
                if e_o > 0.002 or not self.config_ok(q_o) or any(S.ik(np.r_[p_, z_tcp], R_, q_o)[1] > 0.002 for p_ in (s0, end)):
                    self._push_why = "out of reach / the arm's limits"; continue
                opts.append(dict(turn=self.turn_of(R_), R=R_, s0=s0, end=end, gap=gap, along=bool(ac == a)))
            if opts:
                break
        if not opts:
            return None
        o = min(opts, key=lambda o: o["turn"] + (0.5 if o["along"] and not TOP_DRAG else 0.0))   # (11-05: the closed
        # fingers' side (their edges: upright) before their back (the fin-ray's sloped beam), unless 30 deg more turn)
        return dict(o, d=d, L=L, r=r, zf=zf, z_tcp=z_tcp, z_over=z_over, in_way=in_way, a=a, c=c, top=top)

    def do_side_push(self, pl, what):
        """Run a planned side push. -> the distance the thing was pushed (0: the fingers came down on something)"""
        S = self.S
        R, s0, end, d, z_tcp = pl["R"], pl["s0"], pl["end"], pl["d"], pl["z_tcp"]
        S.move(np.r_[s0, pl["z_over"]], R, 0.0, 1.0, "over the push start (fingers closed)")
        S.move(np.r_[s0, z_tcp], R, 0.0, 1.0, "down beside it")
        if self.descent_blocked(z_tcp, R, 0.0):
            self.event(what + ": the fingers came down on something")
            return 0.0
        run = float(np.linalg.norm(end - s0))
        slid = self.slide(np.r_[s0, z_tcp], d, run, R, "push")
        L = max(0.0, slid - pl["gap"])
        self.after_drag()
        S.move(S.p_cmd + np.r_[-0.01 * d, 0.0], R, 0.0, 0.4, "back off")
        S.move(S.p_cmd + [0, 0, 0.10], R, 0.0, 0.6, "up")
        self.event(what, how="from the side", dir_deg=round(math.degrees(pl["a"])), dist_mm=round(L * 1000), at_mm=np.round(pl["r"] * 1000).tolist(),
                   fingertips_mm=round(pl["zf"] * 1000), in_the_way=pl["in_way"])
        return L

    def side_push_any(self, obs, mask, Q, support, L, prefer, what, max_in_way=150, cone=None):
        """(11-04) A side push of the thing in the best direction it can take (of 24): no more than max_in_way points
        of other things in its way; prefer: the way it should go (the best cosine first: away from what touches it /
        from the target); cone: only directions within that angle of prefer. -> (the distance pushed (0: none), plan)"""
        ctx = self.push_ctx(obs, mask, Q, support)
        dirs = [np.array([math.cos(t), math.sin(t)]) for t in np.radians(np.arange(0, 360, 15.0))]
        if prefer is not None:
            dirs.sort(key=lambda d: -float(d @ prefer))
            if cone is not None:
                dirs = [d for d in dirs if float(d @ prefer) >= math.cos(cone)]
        why = {}
        for d in dirs:
            pl = self.plan_side_push(ctx, d, L)
            if pl is None or pl["in_way"] > max_in_way:
                k = "too much in its way" if pl is not None else self._push_why
                why[k] = why.get(k, 0) + 1
                continue
            return self.do_side_push(pl, what), pl
        self.event(what + ": no side push", **{k.replace(" ", "_").replace("'", "").replace("/", ""): v for k, v in why.items()})
        return 0.0, None

    SWEEP_TIP = 0.005                                             # (SWEEP: a push's fingertips over the table)

    def plan_hole_push(self, ctx, ring, d, L):
        """(11-05, SWEEP) Push a tape lying flat from INSIDE its open hole (ring_of): the closed fingers down in the
        hole's middle (tips 5 mm over its floor), slid along d: they meet its inner wall, then push it L. For things
        boxed in on the side the push must go (11-05 pile 17: the red tape between the block, the glue and the pad;
        a push from outside needs the fingers between it and them). The closed fingers fit in the hole up to the tape's
        top (their corners, WEDGE_STEPS: the red / yellow tapes' 37-38 mm holes, not the white one's 26 mm); the hand
        clear of other things all the way; reach. -> a plan for do_side_push or None (why in self._push_why)"""
        S = self.S
        c, r_in, top, floor = ring["c"], ring["r_in"], ring["top"], ring["floor"]
        for dep in (0.010, 0.007):                                 # (the fingertips this far under its top: the wedge
            zf = max(floor + 0.005, top - dep)                     # is thinner there; SAM's hole reads ~1-2 mm small)
            h_top = (top - zf) * 1000 - 15.7                       # (the tape's top over the TCP, mm)
            U = max([u for h0, h1, u in WEDGE_STEPS if h0 <= h_top] or [WEDGE_STEPS[0][2]]) / 1000
            if math.hypot(U, 0.013) <= r_in - 0.0005:
                break
        else:
            self._push_why = f"the closed fingers do not fit in its hole ({2 * r_in * 1000:.0f} mm)"; return None
        z_tcp = zf + T.FINGERTIP
        a = math.atan2(d[1], d[0]); n_ = np.array([-d[1], d[0]])
        z_over = max(S.p_cmd[2], top + 0.10)
        Q, Po = ctx["Q"], ctx["Po"]
        ahead = ctx.get("Po_way", Po)[:, :2] - c                   # (other things in the tape's way)
        r_out = ring["r_out"]
        in_way = int((((ahead @ d) > r_out) & ((ahead @ d) < r_out + L + 0.01) & (np.abs(ahead @ n_) < r_out + 0.005)).sum())
        if not self.look_reachable(c + L * d, top):
            self._push_why = "its new place out of a look's reach"; return None
        opts, self._push_why = [], "the hand would touch something on the way"
        for ac, cs, lead in ((a + math.pi / 2, 1, 0.013), (a + math.pi / 2, -1, 0.013), (a, 1, U), (a, -1, U)):
            gap = max(r_in - lead, 0.0)                            # (the fingers meet the wall after this)
            s0 = c.copy(); end = c + d * (gap + L)
            if not self.hand_clear(Po, [s0 + t * (end - s0) for t in (0.0, 1 / 3, 2 / 3, 1.0)], z_tcp, ac, cs):
                continue
            R_ = B.down(ac if cs == 1 else ac + math.pi)
            q_o, e_o = S.ik(np.r_[s0, z_over], R_, S.q_cmd)
            if e_o > 0.002 or not self.config_ok(q_o) or any(S.ik(np.r_[p_, z_tcp], R_, q_o)[1] > 0.002 for p_ in (s0, end)):
                self._push_why = "out of reach / the arm's limits"; continue
            opts.append(dict(turn=self.turn_of(R_), R=R_, s0=s0, end=end, gap=gap, along=bool(ac == a)))
        if not opts:
            return None
        o = min(opts, key=lambda o: o["turn"])
        return dict(o, d=d, L=L, r=c, zf=zf, z_tcp=z_tcp, z_over=z_over, in_way=in_way, a=a, c=c, top=top, hole=True)

    SWEEP_L = (0.03, 0.045, 0.06)                                 # (push_to_clear's distances: the shortest that clears)
    PUSH_COLOURS = {"ok": (0, 220, 0), "into the target": (0, 0, 255), "still in the fingers' way": (0, 140, 255),
                    "no room behind it for the fingers": (255, 0, 255), "the hand would touch something on the way": (0, 255, 255),
                    "out of reach / the arm's limits": (255, 128, 0), "too much in its way": (128, 128, 128)}

    def dbg_push(self, obs, mask, b, g, dirs, rs):
        """push_to_clear's debug image: the target (green), the thing in the grasp's way (blue), the grasp's fingers,
        and per direction an arrow from its middle coloured by the outcome (PUSH_COLOURS; white: other)."""
        if os.environ.get("CLUTTER_DBG", "1") != "1":
            return
        img = cv2.cvtColor(obs["rgb"], cv2.COLOR_RGB2BGR).copy()
        for mk, col in ((mask, (0, 255, 0)), (b["mask"], (255, 80, 0))):
            cv2.drawContours(img, cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], -1, col, 2)
        u = np.array([math.cos(g["close_a"]), math.sin(g["close_a"])]); v = np.array([-u[1], u[0]])
        for sg in (1, -1):
            cs_ = [g["c"] + sg * u * (g["q"] + du) + v * dv for du, dv in ((0, -0.013), (0.006, -0.013), (0.006, 0.013), (0, 0.013))]
            cv2.polylines(img, [np.array([F.project(obs, np.r_[cc, g["top"]]) for cc in cs_], np.int32)], True, (0, 0, 255), 2)
        X0 = np.median(b["Q"], 0); p0 = F.project(obs, X0)
        for kd, k in rs.items():
            p1 = F.project(obs, X0 + np.r_[0.04 * dirs[kd], 0.0])
            cv2.arrowedLine(img, tuple(int(x) for x in p0), tuple(int(x) for x in p1), self.PUSH_COLOURS.get(k, (255, 255, 255)), 2, tipLength=0.25)
        y = 16
        for k, col in self.PUSH_COLOURS.items():
            cv2.putText(img, k, (8, y), 0, 0.45, (0, 0, 0), 3); cv2.putText(img, k, (8, y), 0, 0.45, col, 1); y += 16
        self._dbg_n = getattr(self, "_dbg_n", 0) + 1
        fn = f"{self.per.tag}_{self._dbg_n:02d}_push_to_clear.jpg"
        cv2.imwrite(str(self.per.dir / fn), img)
        self.S.timeline.append((float(self.S.d.time), "dbg", fn))

    def push_to_clear(self, obs, mask, Q, Pn, is_t, cands, support):
        """(11-05, SWEEP; user: "close the gripper, go to table surface level, and push the disturbing object(s) a few
        cm away so that the desired object is then graspable") No grasp clears: for the best grasps (up to 6 directions
        25 deg apart; cands: plan_grasps' candidates, best first) the thing whose points keep the fingers from going
        down (blocking, segment_at) is pushed 3, 4.5 or 6 cm in ANY of 24 directions (11-04's blocker push: only away from
        the target, +-60 deg: the fingers had to go between the two, where there is no room) for which its points,
        moved so, no longer limit that grasp (its fingers as clear as without it: another thing may block the other
        finger: pushed next) with nothing of it between them, it runs
        no deeper into the target (its points within 3 mm of the target's: <= 1.2 x + 10), stays on the table, and the push can be
        done (plan_side_push: the closed fingers down beside it 5 mm over the table, the hand clear, reach). The
        shortest, then the least wrist turn. -> bool (pushed)"""
        P, ok = F.world(obs)
        tq = Q[:, :2]; o_ = tq.min(0) - 0.03                       # the target's footprint, 2 mm cells, + 3 mm
        occ = np.zeros(tuple(np.ceil((tq.max(0) + 0.03 - o_) / 0.002).astype(int) + 1), np.uint8)
        ij = np.floor((tq - o_) / 0.002).astype(int); occ[ij[:, 0], ij[:, 1]] = 1
        occ = cv2.dilate(occ, np.ones((5, 5), np.uint8)) > 0

        def on_target(xy):                                         # -> per point: within 3 mm of the target
            k = np.floor((xy - o_) / 0.002).astype(int)
            inside = (k[:, 0] >= 0) & (k[:, 1] >= 0) & (k[:, 0] < occ.shape[0]) & (k[:, 1] < occ.shape[1])
            out = np.zeros(len(xy), bool); out[inside] = occ[k[inside, 0], k[inside, 1]]
            return out

        def into_target(xy):
            return int(on_target(xy).sum())
        dirs = [np.array([math.cos(t), math.sin(t)]) for t in np.radians(np.arange(0, 360, 15.0))]
        seen, tried, why = [], 0, {}
        for g in cands:
            if tried >= 6:
                break
            if any(abs((g["close_a"] - a + math.pi / 2) % math.pi - math.pi / 2) < math.radians(25) for a in seen):
                continue
            seen.append(g["close_a"]); tried += 1
            hit = blocking(Pn, ~is_t, g["c"], g["close_a"], g["q"], g["z_want"], g["fm"])
            if hit.sum() < 10:
                continue
            hp = np.zeros(ok.shape, bool); hp.flat[np.flatnonzero(ok)[hit]] = True
            b = self.segment_at(obs, hp, mask)
            if b is None:
                why["blocking thing not segmented"] = why.get("blocking thing not segmented", 0) + 1
                continue
            bm = dilate(b["mask"], 2)[ok]
            near = np.linalg.norm(Pn[:, :2] - g["c"], axis=1) < 0.13
            rest, rest_ob = Pn[near & ~bm], (~is_t)[near & ~bm]
            Bp = Pn[bm]
            cs = 1 if abs((g["a"] - g["close_a"] + math.pi) % (2 * math.pi) - math.pi) < 1e-6 else -1
            n0 = into_target(Bp[:, :2])
            z_rest = clear_height(rest, rest_ob, g["c"], g["close_a"], g["q"], cs, g["fm"])   # (without it: another thing
            goal = max(z_rest, g["z_want"]) + 0.001                # may block the other finger: that one next, 11-05)
            nb_goal = max(between(rest, rest_ob, g["c"], g["close_a"], g["q"], g["z_want"]), 14)   # (the same: others'
            # points between the fingers that this push cannot move)
            if clear_height(np.vstack([rest, Bp]), np.r_[rest_ob, np.ones(len(Bp), bool)], g["c"], g["close_a"], g["q"], cs, g["fm"]) <= goal:
                why["it is not what limits the grasp"] = why.get("it is not what limits the grasp", 0) + 1; continue
            sup_b = self.support_under(obs, b["mask"], b["Q"])
            ctx = self.push_ctx(obs, b["mask"], b["Q"], sup_b)
            ctx["Po_way"] = ctx["Po"][~on_target(ctx["Po"][:, :2])]   # (the target is not "in its way": into_target)
            ring = self.ring_of(obs, b["mask"], ctx["Q"], sup_b, {})  # (a tape: also from inside its hole)
            opts, rs = [], {}
            for L in self.SWEEP_L:
                for kd, d in enumerate(dirs):
                    mv = Bp + np.r_[L * d, 0.0]
                    if np.abs(np.median(mv[:, :2], 0)).max() > 0.55:
                        k = "off the table"
                    elif into_target(mv[:, :2]) > 1.2 * n0 + 10:
                        k = "into the target"
                    else:
                        allp = np.vstack([rest, mv]); ob = np.r_[rest_ob, np.ones(len(mv), bool)]
                        if clear_height(allp, ob, g["c"], g["close_a"], g["q"], cs, g["fm"]) > goal or \
                                between(allp, ob, g["c"], g["close_a"], g["q"], g["z_want"]) > nb_goal:
                            k = "still in the fingers' way"
                        else:
                            pl = self.plan_side_push(ctx, d, L)
                            if (pl is None or pl["L"] < L - 1e-6) and ring is not None:
                                pl = self.plan_hole_push(ctx, ring, d, L)
                            if pl is None or pl["L"] < L - 1e-6 or pl["in_way"] > 150:
                                k = self._push_why if pl is None else "its new place out of a look's reach" if pl["L"] < L - 1e-6 else "too much in its way"
                            else:
                                k = "ok"
                                opts.append((L, pl["turn"], pl))
                    why[k] = why.get(k, 0) + 1
                    if rs.get(kd) != "ok":
                        rs[kd] = k
                if opts:
                    break
            self.dbg_push(obs, mask, b, g, dirs, rs)
            if not opts:
                continue
            L, _, pl = min(opts, key=lambda o: (o[0], o[1]))
            self.event("no grasp clears: push what is in the fingers' way aside", grasp_a_deg=round(math.degrees(g["close_a"])),
                       at_mm=np.round(np.median(b["Q"], 0) * 1000).tolist(), dir_deg=round(math.degrees(pl["a"])), dist_mm=round(L * 1000),
                       fingertips_mm=round(pl["zf"] * 1000), options=len(opts))
            Ld = self.do_side_push(pl, "push to clear the grasp" + (" (from inside its hole)" if pl.get("hole") else ""))
            if Ld > 0.01:
                self.drags += 1
                return True
        self.event("no grasp clears: no push clears one", tried=tried, **{k.replace(" ", "_").replace("'", "").replace("/", ""): v for k, v in why.items()})
        return False

    def away_from_touching(self, obs, mask, Q, support):
        """The unit vector away from what touches the thing (other things' points > 8 mm up within its radius + 3 cm),
        or None."""
        P, ok = F.world(obs)
        c = np.median(Q[:, :2], 0)
        rt = float(np.percentile(np.linalg.norm(Q[:, :2] - c, axis=1), 95))
        O = P[ok & ~dilate(mask, 4) & (P[..., 2] > support + 0.008)][:, :2][::2] - c
        near = O[np.linalg.norm(O, axis=1) < rt + 0.03]
        if len(near) < 30 or np.linalg.norm(near.mean(0)) < 0.003:
            return None
        return -near.mean(0) / np.linalg.norm(near.mean(0))

    def f_push(self, i):
        """(11-04) Astra's push (CLUTTER_ASTRA_PUSH=1): slide the step's object so that its point "from" (on its
        outline) ends at "to" (the seed's p and to; 2 cm to PUSH_MAX). Found again by its name at "from" (SAM3); a
        side push (plan_side_push: the contact on the line through "from"); no room behind it (or out of reach): the
        closed fingers press on its top and drag it the same way (drag, way=). Not found / not possible: skipped (the
        pick's own declutter follows). -> "ok" """
        sd = self.per.seeds[i]; phrase = self.steps[i][1]
        p0 = np.asarray(sd["p"], float); to = np.asarray(sd.get("to", p0), float)
        disp = (to - p0)[:2]; L = float(np.linalg.norm(disp))
        if L < 0.01:
            self.event("push: too short, skipped", dist_mm=round(L * 1000))
            return "ok"
        d = disp / L; L = min(max(L, 0.02), self.PUSH_MAX); a = math.atan2(d[1], d[0])
        self._phrase = phrase
        obs = self.look_at(np.r_[p0[:2] + 0.02 * d, max(p0[2], 0.0)], 0.06, "look at " + phrase + " (to push)")
        f = self.find(obs, phrase, p0, sd.get("ray"))
        self.dbg(obs, "push", getattr(self, "_last_masks", ()), None if f is None else f[0])
        if f is None:
            self.event("push: " + phrase + " not found, skipped")
            return "ok"
        mask, Q, info = f
        support = self.support_under(obs, mask, Q)
        ctx = self.push_ctx(obs, mask, Q, support)
        pl = self.plan_side_push(ctx, d, L, base=p0[:2])
        if pl is None:                                             # no room behind it: press on its top, drag it
            self.event("push: " + self._push_why + ": drag it on its top", dir_deg=round(math.degrees(a)), dist_mm=round(L * 1000),
                       asked_mm=round(float(np.linalg.norm(disp)) * 1000))
            if self.drag(obs, mask, ctx["Q"], support, L, what="push (on its top)", track=self.pushes_next_pick(i), max_cnt=10 ** 6, way=a):
                self.event("pushed", how="on its top", dir_deg=round(math.degrees(a)), dist_mm=round(L * 1000))
            else:
                self.event("push: not possible, skipped")
            return "ok"
        Ld = self.do_side_push(pl, "pushed")
        if Ld > 0 and self.pushes_next_pick(i):
            self.est = np.r_[pl["c"] + Ld * d, pl["top"]]          # (the pick looks for it there)
        return "ok"

    def pushes_next_pick(self, i):
        """Is step i's pushed thing the one a later pick takes (the same name)?"""
        return any(sk == "pick" and nm == self.steps[i][1] for sk, nm in self.steps[i + 1:])

    def press_closed(self, cmd, R, top):
        """A drag's press: the closed fingers down onto the thing (top: its top) in 1 mm steps until the hand feels
        2 N, then 3 mm more (friction to drag it by). -> the TCP command there. (The robot: by position,
        real_box.RealClutterProgram.)"""
        S = self.S
        for j in range(30):
            cmd[2] -= 0.001
            S.move(cmd, R, 0.0, 0.06, "press" if j == 0 else None); S.hold(0.03)
            if self.hand_force() > 2.0:
                break
        cmd[2] -= 0.003                                           # (a little more: friction to drag it by)
        S.move(cmd, R, 0.0, 0.2); S.hold(0.1)
        return cmd

    def shake_off(self, d):
        """After a drag: back off what the fingers pushed (4 mm against the drag, 3 mm up), lift 3 cm and check in the
        scene camera that nothing came up (11-02: the closed fingers (28 x 26 mm) go into a tape's 37 mm hole; lifted
        straight after the drag, the tape stayed on them for the rest of the episode). Something did: set it down,
        back off 4 mm more / circle 3 mm, lift slowly again (twice); still there: stop (F.Unseen)."""
        S = self.S
        R = S.R_cmd
        S.move(S.p_cmd + np.r_[-0.004 * d, 0.003], R, 0.0, 0.3, "back off what was pushed")
        for k in range(3):
            o0 = self.per.scene_depth()
            S.move(S.p_cmd + [0, 0, 0.03], R, 0.0, 0.6, "up a little"); S.hold(0.15)
            o1 = self.per.scene_depth()
            n, X = self.risen(o0, o1)
            dz = 0.03
            if n < RISE_PX:                                        # and again 7 cm higher (11-02: a tape that stayed
                S.move(S.p_cmd + [0, 0, 0.07], R, 0.0, 0.6, "up"); S.hold(0.15)   # down at 3 cm came up later,
                n, X = self.risen(o1, self.per.scene_depth())                        # fell off elsewhere: lost)
                dz = 0.10
                if n < RISE_PX:
                    return
            self.event("something came up on the fingers: set it down, shake it off", px=n, tries=k, at_cm=round(dz * 100))
            if k == 2:
                raise F.Unseen("something stuck on the fingers")
            S.move(S.p_cmd - [0, 0, dz], R, 0.0, 0.6 + dz * 5, "set it down"); S.hold(0.15)
            if k == 0:
                S.move(S.p_cmd + np.r_[-0.004 * d, 0.0], R, 0.0, 0.3, "back off")
            else:
                p0 = S.p_cmd.copy()
                for a in np.linspace(0, 2 * math.pi, 9)[1:]:
                    S.move(p0 + [0.003 * math.cos(a), 0.003 * math.sin(a), 0.0], R, 0.0, 0.12, "shake" if a < 1 else None)
                S.move(p0, R, 0.0, 0.12)

    def segment_at(self, obs, hp, exclude):
        """The thing at the pixels hp (points that block a grasp / lie over the target): SAM3's masks for a click on
        them (the hp pixel nearest their middle) and the "small object" masks; of those holding >= 30 of them (and
        >= 80 % as many as the best), the best-scoring. -> dict(mask, Q, n) or None"""
        P, ok = F.world(obs)
        cands = [dict(c, n=int((dilate(c["mask"], 2) & hp).sum())) for c in self.others(obs, exclude=exclude)]
        ys, xs = np.nonzero(hp)
        if len(xs):
            k = int(np.argmin((xs - xs.mean()) ** 2 + (ys - ys.mean()) ** 2))
            ex = dilate(exclude, 3) if exclude is not None else None
            for mk, sc in self.per.segment_points(obs["rgb"], [(xs[k], ys[k])], [1]):
                if mk.sum() > 40000 or mk.sum() < 200 or (ex is not None and (mk & ex).sum() > 0.15 * exclude.sum()):
                    continue                                       # (11-01: a mask holding the target too: the target
                                                                   # itself was put aside)
                Q = P[mk & ok]
                if len(Q) < 40 or F.narrowest(Q[:, :2])[0] < 0.012 or np.ptp(Q[:, :2], axis=0).max() > 0.20 or self.is_target(obs, mk):
                    continue
                cands.append(dict(mask=mk, raw=mk, Q=Q, score=float(sc), n=int((dilate(mk, 2) & hp).sum())))
        cands = [c for c in cands if c["n"] >= 30]
        if not cands:
            return None
        n_max = max(c["n"] for c in cands)
        return max([c for c in cands if c["n"] >= 0.8 * n_max], key=lambda c: c["score"])

    def remove_blocker(self, obs, Pn, is_t, cands, mask):
        """Nothing clears: the thing whose points block a good grasp (of the 3 least blocked directions, in turn) is
        picked and put on a free spot (a too-wide one cannot be: the next direction's)."""
        P, ok = F.world(obs)
        tried, seen = 0, []
        for g in cands:
            if tried >= 3:
                break
            if any(abs((g["close_a"] - a + math.pi / 2) % math.pi - math.pi / 2) < math.radians(25) for a in seen):
                continue
            seen.append(g["close_a"]); tried += 1
            hit = blocking(Pn, ~is_t, g["c"], g["close_a"], g["q"], g["z_want"], g["fm"])
            if hit.sum() < 10:
                continue
            hp = np.zeros(ok.shape, bool); hp.flat[np.flatnonzero(ok)[hit]] = True   # (the blocking points' pixels)
            c = self.segment_at(obs, hp, mask)
            if c is None:
                self.event("blocking thing not segmented", a_deg=round(math.degrees(g["close_a"])))
                continue
            X = np.r_[np.median(c["Q"][:, :2], 0), float(np.percentile(c["Q"][:, 2], 97))]
            self.event("blocked: put the blocking thing aside first", a_deg=round(math.degrees(g["close_a"])), at_mm=np.round(X * 1000).tolist(),
                       points=int(hit.sum()), in_mask=c["n"])
            if self.take_away(obs, c["mask"], c["Q"], "it blocked the target"):
                return
            sup = self.support_under(obs, c["mask"], c["Q"])        # (it cannot be picked: slide it away from the
            tc = np.r_[np.median(P[mask & ok][:, :2], 0), 0.0]       # target instead)
            if SIDE_PUSH:                                          # (11-04) from its far side first: room there
                awy = np.median(c["Q"][:, :2], 0) - tc[:2]
                if np.linalg.norm(awy) > 1e-3:
                    Ld, _ = self.side_push_any(obs, c["mask"], c["Q"], sup, 0.05, awy / np.linalg.norm(awy),
                                               "blocked: push the blocking thing away from the side", max_in_way=150, cone=math.radians(60))
                    if Ld > 0.01:
                        return
            if self.drag(obs, c["mask"], c["Q"], sup, 0.05, away=tc, what="blocked: drag the blocking thing away"):
                return                                             # (TOP_DRAG=0: a wider side push, see drag)
        tc = np.median(P[mask & ok][:, :2], 0)                     # none of those: any graspable thing around it,
        near = [c for c in self.others(obs, exclude=mask)         # the highest first (a box: clear it from the top)
                if np.linalg.norm(np.median(c["Q"][:, :2], 0) - tc) < 0.12]
        near.sort(key=lambda c: -float(np.percentile(c["Q"][:, 2], 90)))
        for c in near[:4]:
            self.event("blocked: take something around it away", top_mm=round(float(np.percentile(c["Q"][:, 2], 90)) * 1000), score=round(c["score"], 2))
            if self.take_away(obs, c["mask"], c["Q"], "it crowded the target"):
                return
        raise F.Unseen("could not take a blocking thing away")

    def dig(self, obs, zone):
        """The target is not in sight: take the topmost thing in the zone away (it may lie under it). -> bool"""
        cands = [c for c in self.others(obs) if np.linalg.norm(np.median(c["Q"][:, :2], 0) - zone[0]) < zone[1]]
        cands.sort(key=lambda c: -float(np.percentile(c["Q"][:, 2], 90)))
        for c in cands[:3]:
            self.event("not in sight: take the topmost thing away", top_mm=round(float(np.percentile(c["Q"][:, 2], 90)) * 1000), score=round(c["score"], 2))
            if self.take_away(obs, c["mask"], c["Q"], "it may hide the target"):
                return True
            if self.drag(obs, c["mask"], c["Q"], self.support_under(obs, c["mask"], c["Q"]), 0.05, what="not in sight: slide the topmost thing aside"):
                return True
        return False

    def f_in(self, i):
        """Into the surveyed box: over its middle, the held object's bottom 3 cm over the rim, open. (task_box's
        packing (a spot the lid closes over) is not the point here: a tilted tape's height read 54 mm: 'no spot'.)"""
        S, H = self.S, self.held
        box, _ = self.targets[i]
        X = np.r_[box["c"], box["rim"]]
        self.transit(self._over(X, 0.03)[:2], H["R"])
        gh = H.get("hold", 0.0)
        g_rel, g_after = (0.0, 0.0) if H.get("internal") else (min(B.OPEN_W, H["width"] / 2 + 0.01), B.OPEN_W)
        S.move(self._over(X, 0.03), H["R"], gh, 1.0, "over the box")
        S.move(S.p_cmd, H["R"], g_rel, 0.4, "release"); S.hold(0.3)
        S.move(S.p_cmd + [0, 0, 0.08], H["R"], g_after, 0.6, "retreat")
        self.held = None
        return "ok"

    def f_down(self, i):
        """On the table at the step's point; a point the arm does not reach or with something within 7 cm (an Astra
        mark): the nearest free spot."""
        p = np.asarray(self.per.seeds[i]["p"][:2], float)
        if not self.spot_ok(p):
            q = self.free_spot(p)
            self.event("put down: the planned spot is taken / out of reach: the nearest free one", planned_mm=np.round(p * 1000).tolist(),
                       at_mm=np.round(q * 1000).tolist())
            p = q
        self.down_at = p
        self.put_down(np.r_[p, 0.0], "carry to the table")
        return "ok"

    def spot_ok(self, p, clear=0.07):
        S = self.S
        obs = self.per.observe("d415")
        P, ok = F.world(obs)
        hz, _ = table_heights(obs)
        occ = P[ok & (hz > 0.01) & (hz < 0.15)][:, :2][::4]       # (things on the table, not the arm
        if len(occ) and np.min(np.linalg.norm(occ - p, axis=1)) < clear:     # over it)
            return False
        if not 0.38 < np.linalg.norm(p - S.base[:2]) < 0.60:                # (as free_spot; 11-02 an Astra spot at the
            return False                                                     # reach's edge: reached at 10 cm, not at
        y1 = math.atan2(*(p - S.base[:2])[::-1]) + math.pi / 2               # the carry height: "unreachable")
        return all(S.ik(np.r_[p, z_], B.down(y1), S.q_home)[1] < 0.002 for z_ in (0.10, self.safe_z()))


# ---------------------------------------------------------------- judge, runner
def judge(S, L, z0, xy0, picked, down_xy=None):
    tg = L["target"]
    res = dict(picked=picked)
    if L["scene"] == "pile":
        res["placed"] = TB.inside_box(S, tg)
    else:
        p = obj_extent(S, tg)
        res["placed"] = bool(p[2] < 0.06 and not in_box_xy(S, p, 0.01) and np.linalg.norm(p[:2] - np.asarray(L["down"] if down_xy is None else down_xy)) < 0.06)
    others = [k for k in L["objs"] if k != tg]
    res["moved_mm"] = {k: round(float(np.linalg.norm(S.pose(k)[0][:2] - xy0[k])) * 1000) for k in others}
    res["off_table"] = [k for k in L["objs"] if S.pose(k)[0][2] < -0.01]
    res["box_tilt_deg"] = round(tilt_deg(S, "box_white"), 1)
    if L["scene"] == "bin":
        res["left_box"] = [k for k in others if not in_box_xy(S, obj_extent(S, k))]
    res["ok"] = bool(picked.get("right") and res["placed"] and not res["off_table"] and res["box_tilt_deg"] < 10)
    return res


def run_job(job):
    scene, seed, idx, mode, out, save, err, plan = job
    L = sample_layout(scene, seed, idx)
    if L is None:
        return dict(scene=scene, layout=idx, pick=mode, result="layout rejected")
    S = TB.BoxSim(); apply_layout(S, L)
    tag = f"{scene}_{idx:02d}_{mode}" + ("_astra" if plan == "astra" else "")
    per = ClutterPerception(S, np.random.default_rng([seed, idx, 5]), out, tag, L, err, astra=plan == "astra")
    prog = ClutterProgram(S, per, mode, L)
    tg = L["target"]
    z0 = {k: float(S.pose(k)[0][2]) for k in L["objs"]}
    xy0 = {k: S.pose(k)[0][:2].copy() for k in L["objs"]}
    picked = {}
    orig = prog.f_pick

    def f_pick(i):
        r = orig(i)
        lift = {k: round((float(S.pose(k)[0][2]) - z0[k]) * 1000) for k in L["objs"]}
        tcp = S.tcp_now()                                         # carried along: lifted, and at the hand
        picked.update(result=r, lift_mm=lift, right=bool(r == "ok" and lift[tg] > 50 and S.finger_width() > 0.004),
                      others_lifted=[k for k in L["objs"] if k != tg and lift[k] > 20 and np.linalg.norm(S.pose(k)[0][:2] - tcp[:2]) < 0.12],
                      set_aside=prog.set_aside)
        if picked["others_lifted"]:
            picked["right"] = False
        return r
    prog.f_pick = f_pick
    t0 = time.time()
    try:
        r = prog.run()
    except Exception as e:
        import traceback
        r = f"crash: {e!r}"[:200]; traceback.print_exc()
    wall = time.time() - t0
    res = judge(S, L, z0, xy0, picked, getattr(prog, "down_at", None))
    row = dict(scene=scene, layout=idx, pick=mode, plan=plan, seed_err_mm=round(err * 1000), target=tg, n_objs=len(L["objs"]), touching=L["touching"],
               seen_rays=L["seen_rays"], program=r, result="success" if res.pop("ok") else "fail", **res,
               sim_s=round(float(S.d.time), 1), wall_s=round(wall, 1), events=prog.events)
    if plan == "astra":
        row.update(astra_calls=per.calls, astra_cost=round(per.cost, 4), astra=per.astra_log)
    if save:
        np.savez_compressed(Path(out) / f"trace_{tag}.npz", t=np.array([x[0] for x in S.rec]), qpos=np.array([x[1] for x in S.rec]))
        json.dump(dict(row=row, layout=L, timeline=S.timeline), open(Path(out) / f"trace_{tag}.json", "w"),
                  default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    per.close()
    with open(Path(out) / "runs.jsonl", "a") as f:
        f.write(json.dumps(row, default=lambda o: o.tolist() if hasattr(o, "tolist") else (o if isinstance(o, (int, float, str)) else str(o))) + "\n")
    return row


def summarize(rows, out):
    lines = ["| scene | pick | success | right object picked | wrong / extra object | knocked off | detail |", "|---|---|---|---|---|---|---|"]
    for sc in ("pile", "bin"):
        for md in ("depth", "seg"):
            R = sorted([r for r in rows if r.get("scene") == sc and r.get("pick") == md and r["result"] != "layout rejected"], key=lambda r: r["layout"])
            if not R:
                continue
            ok = sum(r["result"] == "success" for r in R)
            right = sum(bool(r.get("picked", {}).get("right")) for r in R)
            extra = sum(bool(r.get("picked", {}).get("others_lifted")) for r in R)
            off = sum(bool(r.get("off_table")) for r in R)
            det = "; ".join(f'{r["layout"]}: {r["program"]}' for r in R if r["result"] != "success")
            lines.append(f"| {sc} | {md} | {ok}/{len(R)} | {right}/{len(R)} | {extra} | {off} | {det} |")
    txt = "\n".join(lines) + "\n"
    (Path(out) / "summary.md").write_text(txt)
    return txt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scene", nargs="+", default=["pile", "bin"])
    ap.add_argument("--pick", nargs="+", default=["depth", "seg"])
    ap.add_argument("--layouts", type=int, default=12)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--seed-err", type=float, default=0.0)
    ap.add_argument("--plan", choices=["gt", "astra"], default="gt")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--save-traces", action="store_true")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in open(a.out / "runs.jsonl")] if (a.out / "runs.jsonl").exists() else []
    done = {(r["scene"], r["layout"], r["pick"]) for r in rows}
    jobs = [(sc, a.seed, i, md, str(a.out), a.save_traces, a.seed_err, a.plan) for sc in a.scene for i in (a.only or range(a.layouts))
            for md in a.pick if (sc, i, md) not in done]
    print(f"{len(done)} done, {len(jobs)} to run", flush=True)
    t0 = time.time()
    with Pool(max(1, min(a.workers, len(jobs)))) as pool:
        for r in pool.imap_unordered(run_job, jobs):
            rows.append(r)
            print(f"{r['scene']} {r['layout']} {r['pick']} ({time.time() - t0:.0f}s): {r['result']} - {r.get('program')} | target {r.get('target')} "
                  f"picked {r.get('picked', {}).get('right')} extra {r.get('picked', {}).get('others_lifted')} placed {r.get('placed')} "
                  f"off {r.get('off_table')} wall {r.get('wall_s')}s", flush=True)
    print(summarize(rows, a.out))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "layouts":            # clutter_bench.py layouts OUT SCENE N: D415 images
        out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
        for idx in range(int(sys.argv[4])):
            t0 = time.time(); L = sample_layout(sys.argv[3], 11, idx)
            print(idx, "rejected" if L is None else f"tries {L['tries']} target {L['target']} objs {L['objs']} touching {L['touching']} seen {L['seen_rays']}",
                  f"{time.time() - t0:.0f}s", flush=True)
            if L is None:
                continue
            S = TB.BoxSim(); apply_layout(S, L)
            per = ClutterPerception(S, np.random.default_rng(0), out, f"l{idx}", L)
            obs = per.observe("d415")
            img = cv2.cvtColor(obs["rgb"], cv2.COLOR_RGB2BGR)
            cv2.imwrite(str(out / f"{sys.argv[3]}_{idx:02d}.jpg"), img[200:420, 200:560])
            per.close()
    else:
        main()
