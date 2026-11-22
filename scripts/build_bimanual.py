"""Build the two bimanual simulated environments (docs/bimanual.md; 2026-11-21, user: "add two simulated scenes: one
with a bimanual robot + grippers, the other one with a bimanual robot + dexterous sharpa hands ... the robot is in
front of the table. the fixed scene camera should be the same one we already have, and the robot should also have the
wrist cameras ... sim_bimanual_grippers and sim_bimanual_dexterous").

Both are the twin's own scene (vendor/panda_twin/model/twin_6522_wedge.xml, read only): the table, the rig's TWO Panda
arms at their calibrated bases on the frame at the table's -x edge (R_ = the robot's right, at -y; L_ = its left), the
scanned objects, the lights. What changes:

  both      - both arms are driven (the single-arm twin parks the right one);
            - the scene D415 (camera `d415`) stands at the rig's calibrated pose (vendored real/calib.json scene_cam:
              the pose the platform's default camera config, d415_rig, uses);
            - a bottle with a screw cap (`obj_bottle`, `bottle_cap`, `bottle_cap_free`): a two-handed fixture, ported
              from embodied-rl/bimanual/build_model.py (11-17);
            - a stick stand and a cylinder with a hole (`obj_stick_stand`, `obj_cylinder`): the precision task
              (2026-11-21, user: "insert a cylinder through a vertical stick ... the cylinder's hole should only be
              about 2 mm larger ... so that the task requires mm level precision"), after panda_twin's precision
              bench (11-16): the hole's wall is CYL_WALLS boxes whose inner faces make a polygon around the stick;
            - a keyframe "home" replaces the twin's: both arms in the same pose, the flange HOME_X ahead of its base
              at height HOME_Z, pointing straight down, hands open.
  grippers  - the fin-ray grippers as in the twin; each wrist D415 (`R_wrist`, `L_wrist`) at the calibrated mount
              (calib.json wrist_cam T_hand_cam, measured on the left hand; the right hand's is assumed the same).
  dexterous - each gripper is replaced by a Sharpa Wave hand (right hand on R, left on L; Sharpa's official MJCF,
              assets/manifest.yaml sharpa_right / sharpa_left), 22 position servos each. MuJoCo 2.3.7 has no
              dampratio / inheritrange: the servos are <general> with kv = 0.9 * 2 sqrt(kp M0) and the URDF's torque
              limits (two passes: the joint inertias M0 come from the compiled model). The wrist D415 sits 75 mm
              behind the back of the hand and looks along the fingers, turned 10 deg towards the palm (as
              panda_twin_dex). Time step 1 ms (2 ms: the hands' light links go unstable in a grasp).

Mesh files are named relative to src/agentic_robotics (compiler meshdir "../.."): the Panda's and the objects' in
vendor/panda_twin/model/assets, the hands' in bimanual/models/assets/sharpa.

    .venv/bin/python -I scripts/build_bimanual.py            # both
    .venv/bin/python -I scripts/build_bimanual.py grippers   # one
"""
from __future__ import annotations

import copy
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src/agentic_robotics"
TWIN = PKG / "vendor/panda_twin/model/twin_6522_wedge.xml"
CALIB = PKG / "vendor/panda_twin/code/real/calib.json"
OUT = PKG / "bimanual/models"
SHARPA = OUT / "assets/sharpa"
PANDA_DIR = "vendor/panda_twin/model/assets/"
SHARPA_DIR = "bimanual/models/assets/sharpa/"
NAMES = {"grippers": "sim_bimanual_grippers", "dexterous": "sim_bimanual_dexterous"}
TIMESTEP = {"grippers": 0.002, "dexterous": 0.001}
SIDE = {"R": "right", "L": "left"}
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
DAMPRATIO = 0.9
PALM = "0.021 0 0.085"                          # the palm centre on its surface, in the hand's frame (palm faces +x)
WRIST_CAM_X, WRIST_CAM_TILT = -0.075, 10.0      # dexterous: the wrist D415 behind the back of the hand
# the bottle (m): as embodied-rl/bimanual
BOTTLE_XY = (-0.21, -0.175)                     # between the arms, 0.58 m in front of their bases
BODY_R, BODY_H = 0.034, 0.200
NECK_R, NECK_H = 0.0150, 0.100                  # the neck: z BODY_H .. BODY_H + NECK_H
CAP_R, CAP_H, CAP_WALL = 0.0210, 0.024, 0.0030  # outer radius, height, wall
CAP_Z = BODY_H + NECK_H - CAP_H + 0.004         # the cap's bottom when screwed tight (its top 4 mm over the neck's)
PITCH = 0.004                                   # m per turn
THREAD_FRICTION = 0.05                          # N m
# the stick stand and the cylinder (m): the stick's top is flat, its edge square
STAND_BOARD = (0.120, 0.120, 0.022)             # a wooden board, free on the table
STICK_R, STICK_H = 0.0090, 0.100                # the stick, and its height above the board
CYL_R, CYL_H, CYL_BORE, CYL_WALLS = 0.020, 0.040, 0.020, 24   # the hole: 2 mm wider than the stick (1 mm all round)
CYL_FEET, CYL_FOOT_R, CYL_FOOT_AT = 6, 0.001, 0.018           # what the cylinder stands on (see add_stick_and_cylinder)
WOOD = dict(density="600", friction="1.0 0.02 0.005", condim="4")
HOME_X, HOME_Z = 0.31, 0.53                     # the arms' home: the flange in front of the base, above the table
CAP_PARK = "3 3 -0.8645"                        # the free cap until the threaded one comes off (on the floor, far away)


def find_body(root, name):
    return next(b for b in root.iter("body") if b.get("name") == name)


def fmt(v, n=6):
    return " ".join(f"{float(x):.{n}g}" for x in np.asarray(v).ravel())


def urdf_effort(side):
    u = ET.parse(SHARPA / side / f"{side}_sharpa_wave_with_flange.urdf").getroot()
    return {j.get("name"): float(j.find("limit").get("effort")) for j in u.iter("joint")
            if j.get("type") == "revolute" and j.find("limit") is not None}


def cap_geoms(body, prefix, rgba):
    """A hollow cap: a lid disc and a 16-sided wall (boxes), origin at the centre of its bottom opening."""
    ET.SubElement(body, "geom", name=prefix + "_lid", type="cylinder", size=f"{CAP_R} 0.0015", pos=f"0 0 {CAP_H - 0.0015}",
                  rgba=rgba, friction="1.2 0.02 0.002", condim="4", mass="0.002")
    n = 16; rm = CAP_R - CAP_WALL / 2
    for i in range(n):
        a = 2 * math.pi * i / n
        ET.SubElement(body, "geom", name=f"{prefix}_w{i}", type="box",
                      size=f"{CAP_WALL / 2} {rm * math.tan(math.pi / n) + 0.0005:.5f} {CAP_H / 2}",
                      pos=f"{rm * math.cos(a):.5f} {rm * math.sin(a):.5f} {CAP_H / 2}", euler=f"0 0 {a:.5f}",
                      rgba=rgba, friction="1.2 0.02 0.002", condim="4", mass="0.0004")
    ET.SubElement(body, "geom", name=prefix + "_mark", type="box", size="0.004 0.0015 0.0008", pos=f"{CAP_R - 0.006} 0 {CAP_H}",
                  rgba="1 1 1 1", contype="0", conaffinity="0", mass="0")


def add_bottle(wb, eq, con):
    bt = ET.SubElement(wb, "body", name="obj_bottle", pos=f"{BOTTLE_XY[0]} {BOTTLE_XY[1]} 0.0005")
    ET.SubElement(bt, "freejoint", name="obj_bottle_free")
    col = "0.25 0.55 0.85 0.75"
    ET.SubElement(bt, "geom", name="bottle_body", type="cylinder", size=f"{BODY_R} {BODY_H / 2 - 0.006}", pos=f"0 0 {BODY_H / 2 - 0.006}",
                  rgba=col, mass="0.33", friction="0.9 0.005 0.0001", condim="4")
    ET.SubElement(bt, "geom", name="bottle_shoulder", type="cylinder", size=f"{BODY_R * 0.72} 0.006", pos=f"0 0 {BODY_H - 0.006}",
                  rgba=col, mass="0.01", friction="0.9 0.005 0.0001", condim="4")
    ET.SubElement(bt, "geom", name="bottle_neck", type="cylinder", size=f"{NECK_R} {NECK_H / 2}", pos=f"0 0 {BODY_H + NECK_H / 2}",
                  rgba="0.85 0.92 1 0.9", mass="0.01", friction="0.6 0.005 0.0001")
    ET.SubElement(bt, "geom", name="bottle_label", type="cylinder", size=f"{BODY_R + 0.0004} 0.03", pos=f"0 0 {BODY_H * 0.5}",
                  rgba="0.95 0.95 0.9 1", contype="0", conaffinity="0", mass="0")
    ET.SubElement(bt, "geom", name="bottle_water", type="cylinder", size=f"{BODY_R - 0.003} 0.045", pos="0 0 0.048",   # (half full:
                  rgba="0.2 0.4 0.9 0.35", contype="0", conaffinity="0", mass="0.30")                               # heavy at the bottom)
    ct = ET.SubElement(bt, "body", name="bottle_cap", pos=f"0 0 {CAP_Z}")
    ET.SubElement(ct, "joint", name="cap_turn", type="hinge", axis="0 0 1", range="0 100", limited="true", frictionloss=str(THREAD_FRICTION),
                  damping="0.01", armature="2e-4", solreffriction="0.004 1", solimpfriction="0.99 0.999 0.001")   # (a light joint's
    # friction loss is soft, i.e. viscous, with the default impedance: 0.01 N m turned the cap 40 deg in a second)
    ET.SubElement(ct, "joint", name="cap_rise", type="slide", axis="0 0 1", range="-0.001 0.2", limited="true", damping="0.5", armature="1e-4")
    cap_geoms(ct, "capt", "0.9 0.15 0.12 1")
    ET.SubElement(eq, "joint", name="thread", joint1="cap_rise", joint2="cap_turn", polycoef=f"0 {PITCH / (2 * math.pi):.8f} 0 0 0",
                  solref="0.004 1", solimp="0.95 0.99 0.001")
    ET.SubElement(con, "exclude", body1="obj_bottle", body2="bottle_cap")
    cf = ET.SubElement(wb, "body", name="bottle_cap_free", pos=CAP_PARK)
    ET.SubElement(cf, "freejoint", name="bottle_cap_free_j")
    cap_geoms(cf, "capf", "0.9 0.15 0.12 1")


def add_stick_and_cylinder(wb):
    st = ET.SubElement(wb, "body", name="obj_stick_stand", pos="0.30 0.30 0.0005")
    ET.SubElement(st, "freejoint", name="obj_stick_stand_free")
    bx, by, bz = STAND_BOARD
    ET.SubElement(st, "geom", name="stand_board", type="box", size=f"{bx / 2} {by / 2} {bz / 2}", pos=f"0 0 {bz / 2}",
                  rgba="0.88 0.71 0.40 1", **WOOD)
    ET.SubElement(st, "geom", name="stand_stick", type="cylinder", size=f"{STICK_R} {STICK_H / 2}", pos=f"0 0 {bz + STICK_H / 2}",
                  rgba="0.86 0.70 0.47 1", **WOOD)
    cy = ET.SubElement(wb, "body", name="obj_cylinder", pos="0.30 0.45 0.0005")
    ET.SubElement(cy, "freejoint", name="obj_cylinder_free")
    r_in = CYL_BORE / 2; t = CYL_R - r_in; rc = r_in + t / 2
    hw = CYL_R * math.tan(math.pi / CYL_WALLS) * 1.05
    lift = 0.0003
    for k in range(CYL_WALLS):                                    # (the inner faces: a polygon of apothem r_in)
        a = 2 * math.pi * k / CYL_WALLS
        ET.SubElement(cy, "geom", name=f"cyl_w{k}", type="box", size=f"{t / 2:.5f} {hw:.5f} {CYL_H / 2 - lift:.5f}",
                      pos=f"{rc * math.cos(a):.5f} {rc * math.sin(a):.5f} {CYL_H / 2:.5f}", euler=f"0 0 {a:.5f}",
                      rgba="0.16 0.36 0.80 1", **WOOD)
    # The cylinder stands on CYL_FEET points per end face, 0.3 mm proud of the walls, with a stiff contact of their
    # own (priority): with the 24 boxes flat on the table (96 contacts) it was kicked off the table now and then, at
    # rest. (With the scene's soft contact, things sink 0.5 mm into the table: the feet would not hold the walls off.)
    for end, z in (("b", CYL_FOOT_R), ("t", CYL_H - CYL_FOOT_R)):
        for k in range(CYL_FEET):
            a = 2 * math.pi * (k + 0.5) / CYL_FEET
            ET.SubElement(cy, "geom", name=f"cyl_{end}{k}", type="sphere", size=f"{CYL_FOOT_R}",
                          pos=f"{CYL_FOOT_AT * math.cos(a):.5f} {CYL_FOOT_AT * math.sin(a):.5f} {z:.5f}",
                          rgba="0.16 0.36 0.80 1", priority="1", solref="0.004 1", **dict(WOOD, density="0"))


def calibrated_scene_camera(wb, calib):
    """The scene D415's body at calib.json's T_world_cam (OpenCV axes -> MuJoCo's: x right, y up, looking along -z)."""
    T = np.asarray(calib["scene_cam"]["T_world_cam"], float)
    Rm = T[:3, :3] @ np.diag([1.0, -1.0, -1.0])
    b = find_body(wb, "d415_cam")
    b.set("pos", fmt(T[:3, 3])); b.set("xyaxes", fmt(np.r_[Rm[:, 0], Rm[:, 1]]))
    mount = next(g for g in wb if g.tag == "geom" and g.get("name") == "d415_mount")
    lo = mount.get("fromto").split()[3:]
    mount.set("fromto", fmt(T[:3, 3] - [0, 0, 0.02]) + " " + " ".join(lo))


def calibrated_wrist_camera(hand, P, calib):
    """The gripper's wrist D415 at calib.json's T_hand_cam; its box and bracket move with it."""
    T = np.asarray(calib["wrist_cam"]["T_hand_cam"], float)
    Rm = T[:3, :3] @ np.diag([1.0, -1.0, -1.0])
    cam = next(c for c in hand if c.tag == "camera" and c.get("name") == f"{P}_wrist")
    d = T[:3, 3] - np.array([float(v) for v in cam.get("pos").split()])
    cam.set("pos", fmt(T[:3, 3])); cam.set("xyaxes", fmt(np.r_[Rm[:, 0], Rm[:, 1]]))
    kids = list(hand); i = kids.index(next(c for c in kids if c.get("name") == f"{P}_d415w"))
    for g in kids[i:i + 2]:                                           # (the camera's box and the bracket after it)
        g.set("pos", fmt(np.array([float(v) for v in g.get("pos").split()]) + d))


def sharpa_hand(hand, P, dflt, asset, act, con, kv):
    """Replace the gripper in body {P}_hand (the flange frame) by the Sharpa Wave hand of that side."""
    import mujoco
    side = SIDE[P]
    for c in list(hand):
        hand.remove(c)
    ET.SubElement(hand, "inertial", pos="0 0 0", mass="0.05", diaginertia="2e-5 2e-5 2e-5")
    ET.SubElement(hand, "site", name=f"{P}_flange", pos="0 0 0", size="0.004", rgba="0.2 0.2 1 0.8", group="4")
    # the wrist camera: a D415 behind the back of the hand (-x), looking along the fingers (+z), turned towards the palm
    t = math.radians(WRIST_CAM_TILT)
    ET.SubElement(hand, "geom", name=f"{P}_d415w", type="box", size="0.0115 0.0495 0.010", pos=f"{WRIST_CAM_X:.4f} 0 0.030",
                  material="cam_black", contype="0", conaffinity="0", group="2")
    ET.SubElement(hand, "geom", type="box", size="0.010 0.018 0.008", pos=f"{WRIST_CAM_X / 2 - 0.004:.4f} 0 0.030",
                  material="bracket_white", contype="0", conaffinity="0", group="2")
    ET.SubElement(hand, "camera", name=f"{P}_wrist", pos=f"{WRIST_CAM_X:.4f} 0 0.040", fovy="42.5",
                  xyaxes=f"0 1 0 {math.cos(t):.5f} 0 {-math.sin(t):.5f}")
    sw = ET.parse(SHARPA / side / f"{side}_sharpa_wave_with_flange.xml").getroot()
    if P == "R":
        for c in sw.find("default"):
            dflt.append(copy.deepcopy(c))
    for m in sw.find("asset"):
        m = copy.deepcopy(m); m.set("name", f"sw{P}_" + m.get("name")); m.set("file", f"{SHARPA_DIR}{side}/meshes/" + m.get("file"))
        asset.append(m)
    hr = copy.deepcopy(sw.find("worldbody").find("body"))
    hr.set("childclass", "sw")
    for el in hr.iter():
        if el.tag in ("body", "joint", "geom") and el.get("name"):
            el.set("name", f"{P}_" + el.get("name"))
        if el.tag == "geom":
            el.set("mesh", f"sw{P}_" + el.get("mesh")); el.attrib.pop("rgba", None)
            if el.get("group") == "1":
                el.set("group", "2"); nm = el.get("name") or ""
                el.set("material", "sw_pad" if "elastomer" in nm else "sw_dark" if nm.endswith(("MC_visual", "wrist_visual")) else "sw_shell")
            else:
                el.set("group", "3"); el.set("friction", "1.0 0.02 0.005"); el.set("condim", "4")
    ET.SubElement(hr, "site", name=f"{P}_palm", pos=PALM, size="0.006", rgba="1 0.1 0.1 0.9", group="4")
    for f in FINGERS:                                                # a site at each fingertip pad's centre
        tip = find_body(hr, f"{P}_{side}_{f}_DP")
        el = next(g for g in tip if g.tag == "geom" and "elastomer" in (g.get("name") or "") and g.get("group") == "3")
        R = np.zeros(9); mujoco.mju_quat2Mat(R, np.array([float(v) for v in el.get("quat").split()]))
        p = R.reshape(3, 3) @ np.array([0.0, -(0.027611 if f == "thumb" else 0.026009), 0.0])
        ET.SubElement(tip, "site", name=f"{P}_tip_{f}", pos=" ".join(f"{v:.5f}" for v in p), size="0.004", rgba="0.1 1 0.1 0.9", group="4")
        el.set("name", f"{P}_pad_{f}")
    hand.append(hr)
    jrange = {j.get("name"): j.get("range") for j in sw.iter("joint")}
    effort = urdf_effort(side)
    for i, a in enumerate(sw.find("actuator")):
        j = a.get("joint"); kp = float(a.get("kp"))
        kvv = 0.0 if kv is None else kv[P][i]
        ET.SubElement(act, "general", name=f"{P}_" + a.get("name"), joint=f"{P}_" + j, biastype="affine", gainprm=f"{kp}",
                      biasprm=f"0 {-kp} {-kvv:.6g}", ctrlrange=jrange[j], ctrllimited="true",
                      forcerange=f"{-effort[j]} {effort[j]}", forcelimited="true")
    for e in sw.find("contact"):
        ET.SubElement(con, "exclude", body1=f"{P}_" + e.get("body1"), body2=f"{P}_" + e.get("body2"))
    for b in ("link6", "link7", "hand"):
        ET.SubElement(con, "exclude", body1=f"{P}_{b}", body2=f"{P}_{side}_hand_C_MC")


def build(hands, kv=None, key=None):
    tw = ET.parse(TWIN).getroot()
    calib = json.loads(CALIB.read_text())
    root = ET.Element("mujoco", model=NAMES[hands])
    ET.SubElement(root, "compiler", angle="radian", meshdir="../..", autolimits="true")
    for o in tw.findall("option"):
        o = copy.deepcopy(o)
        if o.get("timestep"):
            o.set("timestep", str(TIMESTEP[hands]))
        root.append(o)
    dflt = copy.deepcopy(tw.find("default")); root.append(dflt)
    asset = copy.deepcopy(tw.find("asset")); root.append(asset)
    for el in asset:
        if el.get("file"):
            el.set("file", PANDA_DIR + el.get("file"))
    wb = copy.deepcopy(tw.find("worldbody")); root.append(wb)
    eq = ET.SubElement(root, "equality"); act = ET.SubElement(root, "actuator"); con = ET.SubElement(root, "contact")
    calibrated_scene_camera(wb, calib)
    if hands == "dexterous":
        dflt.append(ET.fromstring('<default class="sw"><geom contype="1" conaffinity="1"/></default>'))
        ET.SubElement(asset, "material", name="sw_shell", rgba="0.80 0.82 0.86 1", specular="0.3", shininess="0.3")
        ET.SubElement(asset, "material", name="sw_dark", rgba="0.16 0.16 0.18 1", specular="0.2", shininess="0.2")
        ET.SubElement(asset, "material", name="sw_pad", rgba="0.12 0.12 0.13 1", specular="0.1", shininess="0.1")
    for P in ("R", "L"):
        hand = find_body(wb, f"{P}_hand")
        for a in tw.find("actuator"):
            if a.get("name", "").startswith(f"{P}_a") or (hands == "grippers" and a.get("name") == f"{P}_gripper"):
                act.append(copy.deepcopy(a))
        if hands == "grippers":
            calibrated_wrist_camera(hand, P, calib)
            eq.append(copy.deepcopy(next(e for e in tw.find("equality") if e.get("joint1") == f"{P}_finger_joint1")))
        else:
            sharpa_hand(hand, P, dflt, asset, act, con, kv)
    add_bottle(wb, eq, con)
    add_stick_and_cylinder(wb)
    root.append(copy.deepcopy(tw.find("visual")))
    ET.SubElement(root, "statistic", center="-0.2 -0.15 0.2", extent="1.6")
    if key is not None:
        k = ET.SubElement(ET.SubElement(root, "keyframe"), "key", name="home")
        k.set("qpos", fmt(key[0], 8)); k.set("ctrl", fmt(key[1], 8))
    return root


def home_key(m, tw_model):
    """qpos / ctrl of the keyframe "home": each arm's flange HOME_X in front of its base at HOME_Z, pointing straight
    down, square to the table (the joint solution nearest the twin keyframe's), the grippers open (hands: flat)."""
    import mujoco
    import numpy as np
    d = mujoco.MjData(m); mujoco.mj_resetData(m, d)
    k0 = tw_model.key("twin").qpos
    R1 = np.diag([1.0, -1.0, -1.0])
    jp, jr = np.zeros((3, m.nv)), np.zeros((3, m.nv))
    for P in ("R", "L"):
        ja = [m.joint(f"{P}_joint{i}").id for i in range(1, 8)]
        qa, va = m.jnt_qposadr[ja], m.jnt_dofadr[ja]
        q0 = np.array([float(k0[tw_model.joint(f"{P}_joint{i}").qposadr[0]]) for i in range(1, 8)])
        b = m.body(f"{P}_hand").id
        base = m.body(f"{P}_link0").pos
        p1 = np.array([base[0] + HOME_X, base[1], HOME_Z]).round(3)
        q = q0.copy()
        for _ in range(500):
            d.qpos[qa] = q; mujoco.mj_kinematics(m, d); mujoco.mj_comPos(m, d)
            Rc = d.xmat[b].reshape(3, 3); E = R1 @ Rc.T
            w = np.zeros(3); qe = np.zeros(4); mujoco.mju_mat2Quat(qe, E.flatten()); mujoco.mju_quat2Vel(w, qe, 1.0)
            e = np.r_[p1 - d.xpos[b], w]
            if np.abs(e).max() < 1e-7:
                break
            mujoco.mj_jacBody(m, d, jp, jr, b)
            J = np.vstack([jp[:, va], jr[:, va]]); JJ = J @ J.T + 1e-8 * np.eye(6)
            q = q + J.T @ np.linalg.solve(JJ, e) + (np.eye(7) - J.T @ np.linalg.solve(JJ, J)) @ (0.1 * (q0 - q))
        assert np.abs(e).max() < 1e-6 and (q > m.jnt_range[ja, 0] + 0.1).all() and (q < m.jnt_range[ja, 1] - 0.1).all(), (P, e, q)
        for i in range(7):
            d.qpos[qa[i]] = q[i]; d.ctrl[m.actuator(f"{P}_a{i + 1}").id] = q[i]
        if mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{P}_gripper") >= 0:
            for j in (1, 2):
                d.qpos[m.joint(f"{P}_finger_joint{j}").qposadr[0]] = 0.04
            d.ctrl[m.actuator(f"{P}_gripper").id] = 0.04
        print(f"  home {P}: flange {p1.tolist()}, joints {q.round(4).tolist()}")
    return d.qpos.copy(), d.ctrl.copy()


def write(hands):
    import mujoco
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{NAMES[hands]}.xml"
    tw_model = mujoco.MjModel.from_xml_path(str(TWIN))

    def dump(**kw):
        r = build(hands, **kw); ET.indent(r, space=" ")
        out.write_text(ET.tostring(r, encoding="unicode") + "\n")
        return mujoco.MjModel.from_xml_path(str(out))
    m = dump()
    kv = None
    if hands == "dexterous":                                         # servo damping from the compiled joint inertias
        d = mujoco.MjData(m); mujoco.mj_forward(m, d)
        kv = {}
        for P, side in SIDE.items():
            sw = ET.parse(SHARPA / side / f"{side}_sharpa_wave_with_flange.xml").getroot()
            kv[P] = []
            for a in sw.find("actuator"):
                dof = m.jnt_dofadr[m.joint(f"{P}_" + a.get("joint")).id]
                kv[P].append(float(a.get("dampratio", DAMPRATIO)) * 2 * math.sqrt(float(a.get("kp")) * float(d.qM[m.dof_Madr[dof]])))
        m = dump(kv=kv)
    m = dump(kv=kv, key=home_key(m, tw_model))
    print(f"wrote {out.relative_to(ROOT)}: nq {m.nq}, nu {m.nu}, ngeom {m.ngeom}, nbody {m.nbody}, ncam {m.ncam}, dt {m.opt.timestep}")


if __name__ == "__main__":
    for h in (sys.argv[1:] or list(NAMES)):
        write(h)
