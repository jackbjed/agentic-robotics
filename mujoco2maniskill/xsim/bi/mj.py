"""The PRACTICE simulator of the two-arm (X5) tasks: RoboDojo's bimanual robot in MuJoCo, with the same program
interface as the target (sweep_blocks/program_api.py X5Robot: the same commands, units, frames, kinematics, speeds and
time limit; only what carries them out differs), the same three cameras, and the things on the table taken from a
practice WORLD file (a MuJoCo XML string written by an agent). Runs in the project's .venv (mujoco; no ManiSkill).

Fixed here, whatever a world file says: the two arms and their drives (RoboDojo's: x5/spec.py), the table's top at
z = 0 of the table frame, the head camera's stand, the cameras, the step (4 ms physics, 25 Hz actions, 1000 actions)."""
from __future__ import annotations

import math
import os
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
os.environ.setdefault("MUJOCO_GL", "egl")
import mujoco  # noqa: E402

if "m2m.rig" not in sys.modules:                                   # (m2m.api imports four names of the ManiSkill rig; not used here)
    _stub = types.ModuleType("m2m.rig"); _stub.GUARD_N, _stub.MIN_Z, _stub.SAFETY_N = 4.0, 0.0, 40.0
    _stub.Stop = type("Stop", (Exception,), {}); sys.modules["m2m.rig"] = _stub
from m2m.api import Frame  # noqa: E402
from sweep_blocks import program_api as PA  # noqa: E402
from x5 import spec as S  # noqa: E402

TABLE_Z = PA.TABLE_Z
W, H = 640, 480
HEAD = dict(pos=(0.0, -0.41, 1.308), ori=(30.0, 0.0, 0.0), fx=W * 10.0 / 22.212, fy=H * 10.0 / 14.266)        # (sweep_blocks/env.py: RoboDojo's)
WRIST = dict(pos=(0.0, 0.0, 0.001), ori=(0.0, -80.0, -90.0), fx=W * 13.0 / 20.955, fy=H * 13.0 / 15.71625)
STAND = dict(pos=(0.0, -0.47, 0.765), quat=(0.707, -0.707, 0.0, 0.0))
ACTION_STEPS, INTERP_STEPS, SETTLE_STEPS, STEP_LIMIT = 10, 8, 300, 1000
GRIP_SCALE = (-0.01, 0.044)
TABLE_DEFAULT = dict(color=[0.62, 0.50, 0.38], friction=1.0)
MAX_BODIES, MAX_GEOMS = 60, 240
ALLOWED_TOP = {"worldbody", "asset", "default"}
BANNED_TAGS = {"include", "mesh", "hfield", "skin", "plugin", "camera", "light", "actuator", "sensor", "option", "compiler", "size", "equality",
               "tendon", "contact", "keyframe", "custom", "extension", "deformable", "composite", "flexcomp", "replicate", "frame"}


def _rot(axis, deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def _quat(R):
    q = np.zeros(4); mujoco.mju_mat2Quat(q, np.asarray(R, float).reshape(9)); return q


def _cam_R(ori):
    """RoboDojo's camera orientation (angles about x, y, z in turn, each about the turned axes; the camera looks along
    its -z with +y up): MuJoCo's camera frame is the same."""
    return _rot("x", ori[0]) @ _rot("y", ori[1]) @ _rot("z", ori[2])


class WorldError(Exception):
    """A practice world cannot be built: said to its author as plain text."""


def check_world_xml(text):
    """-> (the world's <mujoco> element, its problems). What a world's XML may hold: the things on the table."""
    bad = []
    try:
        x = ET.fromstring(text)
    except ET.ParseError as e:
        return None, [f"the XML does not parse: {e}"]
    if x.tag != "mujoco":
        return None, ["the XML's root must be <mujoco>"]
    for c in x:
        if c.tag not in ALLOWED_TOP:
            bad.append(f"<{c.tag}> is not allowed at the top (only <worldbody>, <asset> with materials or built-in textures, <default>)")
    for e in x.iter():
        if e.tag in BANNED_TAGS:
            bad.append(f"<{e.tag}> is not allowed (the robot, the cameras, the lights and the simulation's settings are fixed; no meshes or files)")
        if "file" in e.attrib:
            bad.append(f"<{e.tag} file=...>: files cannot be used")
        n = e.get("name")
        if n is not None and e.tag in ("body", "geom", "joint", "site", "material", "texture") and not n.startswith("w_"):
            bad.append(f'{e.tag} "{n}": names start with w_')
    wb = x.find("worldbody")
    if wb is None:
        bad.append("there is no <worldbody>")
    else:
        for b in wb.iter("body"):
            if b.get("name") is None:
                bad.append("a <body> has no name (name the bodies w_...)")
        if len(list(wb.iter("body"))) > MAX_BODIES or len(list(wb.iter("geom"))) > MAX_GEOMS:
            bad.append(f"too many bodies or geoms (at most {MAX_BODIES} and {MAX_GEOMS})")
    return x, bad[:12]


def build_xml(world_xml, table=None):
    """The fixed rig + a world's things (given in the TABLE frame: z = 0 is the tabletop, metres) -> MuJoCo XML text."""
    table = dict(TABLE_DEFAULT, **(table or {}))
    wx, bad = check_world_xml(world_xml or "<mujoco><worldbody/></mujoco>")
    if bad:
        raise WorldError("; ".join(bad))
    x = ET.parse(S.GEN / "dual_x5.xml").getroot()
    wb = x.find("worldbody")
    for b in list(wb):
        if b.tag == "body" and b.get("name") == "cube" or b.tag == "camera":
            wb.remove(b)
    for b in wb.iter("body"):                                       # (the arms dark, as the target's are)
        if (b.get("name") or "")[:2] in ("L_", "R_"):
            for g in b.findall("geom"):
                g.set("rgba", "0.07 0.07 0.08 1"); g.set("friction", f"0.5 {S.MJ_TORSION} 0.0001")       # (the robot's surface friction: 0.5)
    t = next(g for g in wb.findall("geom") if g.get("name") == "table")
    col = [float(np.clip(v, 0, 1)) for v in list(table.get("color") or TABLE_DEFAULT["color"])[:3]]
    t.set("rgba", " ".join(f"{v:.3f}" for v in col) + " 1")
    t.set("friction", f"{float(np.clip(table.get('friction', 1.0), 0.02, 3.0))} {S.MJ_TORSION} 0.0001")
    # the head camera's stand (fixed; RoboDojo's shape in convex pieces)
    asset = x.find("asset")
    parts = sorted((HERE / "assets" / "robodojo_sweep" / "camera_stand_collision").glob("part_*.obj"))
    st = ET.SubElement(wb, "body", name="camera_stand", pos=" ".join(map(str, STAND["pos"])), quat=" ".join(map(str, STAND["quat"])))
    for i, f in enumerate(parts):
        ET.SubElement(asset, "mesh", name=f"stand_{i}", file=str(f))
        ET.SubElement(st, "geom", type="mesh", mesh=f"stand_{i}", rgba="0.93 0.93 0.93 1", contype="2", conaffinity="3")
    # the cameras (RoboDojo's poses and lenses; the head camera's pixels are not square: see MJTask.frame)
    R = _cam_R(HEAD["ori"])
    ET.SubElement(wb, "camera", name="head", pos=" ".join(map(str, HEAD["pos"])), quat=" ".join(f"{v:.8f}" for v in _quat(R)),
                  fovy=f"{math.degrees(2 * math.atan(H / 2 / HEAD['fy'])):.6f}")
    for side in S.SIDES:
        cb = next(b for b in wb.iter("body") if b.get("name") == f"{side}_camera")
        ET.SubElement(cb, "camera", name=f"{side}_wrist", pos=" ".join(map(str, WRIST["pos"])), quat=" ".join(f"{v:.8f}" for v in _quat(_cam_R(WRIST["ori"]))),
                      fovy=f"{math.degrees(2 * math.atan(H / 2 / WRIST['fy'])):.6f}")
    e, tg = np.array([0.0, 0.75, 1.55]), np.array([0.0, -0.12, 0.78])                   # (for people: not one of the robot's)
    f = (tg - e) / np.linalg.norm(tg - e); r = np.cross(f, [0, 0, 1.0]); r /= np.linalg.norm(r); up = np.cross(r, f)
    ET.SubElement(wb, "camera", name="overview", pos=" ".join(map(str, e)), xyaxes=" ".join(f"{v:.6f}" for v in list(r) + list(up)), fovy=f"{math.degrees(0.9):.4f}")
    vis = x.find("visual"); ET.SubElement(vis, "map", znear="0.004", zfar="30")
    # the world's things
    for tag in ("asset", "default"):
        src = wx.find(tag)
        if src is not None:
            dst = x.find(tag)
            for c in list(src):
                if tag == "default" and c.tag != "default":
                    raise WorldError("in <default>, only named classes (<default class=...>) can be given: the plain defaults are fixed")
                dst.append(c)
    for c in list(wx.find("worldbody")):
        if c.tag not in ("body", "geom", "site"):
            raise WorldError(f"<{c.tag}> directly in <worldbody>: only <body>, <geom> and <site>")
        p = [float(v) for v in (c.get("pos") or "0 0 0").split()]
        if len(p) != 3:
            raise WorldError(f'{c.tag} "{c.get("name")}": pos needs three numbers')
        c.set("pos", f"{p[0]} {p[1]} {p[2] + TABLE_Z}")
        wb.append(c)
    for g in wb.iter("geom"):
        if (g.get("name") or "").startswith("w_") or g.get("name") is None and g.get("mesh") is None and g.get("contype") is None and _in_world(wb, g):
            ghost = g.get("contype") == "0" and g.get("conaffinity") == "0"
            if not ghost:
                g.set("contype", "2"); g.set("conaffinity", "3")
    return ET.tostring(x, encoding="unicode")


def _in_world(wb, g):
    for b in wb.iter("body"):
        if (b.get("name") or "").startswith("w_") and g in list(b.iter("geom")):
            return True
    return g in list(wb) and g.get("name") not in ("table", "floor")


class MJTask:
    """A practice episode's simulation, with what the program interface (X5Robot) needs of a task."""

    def __init__(self, world_xml="", table=None):
        try:
            self.xml = build_xml(world_xml, table)
            self.m = m = mujoco.MjModel.from_xml_string(self.xml)
        except WorldError:
            raise
        except Exception as e:
            raise WorldError(f"MuJoCo does not take the world: {str(e)[:400]}")
        self.d = mujoco.MjData(m)
        names = S.joint_names()
        self.qadr = np.array([m.jnt_qposadr[m.joint(n).id] for n in names])
        self.act = np.array([m.actuator(n).id for n in names])
        self.sub = int(round(S.DT / m.opt.timestep))
        self.hand = {s: m.body(f"{s}_link6").id for s in S.SIDES}
        self.arm_bodies = {s: {i for i in range(m.nbody) if m.body(i).name.startswith(s + "_")} for s in S.SIDES}
        self.bodies = {m.body(i).name: i for i in range(m.nbody) if m.body(i).name.startswith("w_")}
        self.cam = {"head": ("head", HEAD), "left_wrist": ("L_wrist", WRIST), "right_wrist": ("R_wrist", WRIST)}
        self._r = {}
        self.reset()

    # ---- stepping (RoboDojo's: 10 physics steps an action, the target reached over the first 8)
    def reset(self):
        mujoco.mj_resetData(self.m, self.d)
        q = np.zeros(16); q[[6, 7, 14, 15]] = S.GRIP_OPEN
        self.d.qpos[self.qadr] = q; self.d.ctrl[self.act] = q
        mujoco.mj_forward(self.m, self.d)
        p0 = {n: self.d.xpos[i].copy() for n, i in self.bodies.items()}; R0 = {n: self.d.xmat[i].reshape(3, 3).copy() for n, i in self.bodies.items()}
        for _ in range(SETTLE_STEPS):
            mujoco.mj_step(self.m, self.d, self.sub)
        self.settle = {n: dict(moved_mm=round(float(np.linalg.norm(self.d.xpos[i] - p0[n])) * 1000, 1),
                               turned_deg=round(math.degrees(math.acos(float(np.clip((np.trace(R0[n].T @ self.d.xmat[i].reshape(3, 3)) - 1) / 2, -1, 1)))), 1))
                       for n, i in self.bodies.items()}
        self.start = {n: self.pose(n) for n in self.bodies}
        self.origin = {s: self.endpose(s) for s in S.SIDES}
        self.steps, self.done, self.success = 0, False, False

    def q(self):
        return self.d.qpos[self.qadr].copy()

    def step(self, action):
        a = np.asarray(action, float).reshape(14)
        if self.done:
            return
        goal = np.zeros(16)
        for i, off in enumerate((0, 7)):
            g = float(np.clip(a[off + 6], 0, 1)) * (GRIP_SCALE[1] - GRIP_SCALE[0]) + GRIP_SCALE[0]
            goal[8 * i:8 * i + 6] = a[off:off + 6]; goal[8 * i + 6] = goal[8 * i + 7] = g
        q0 = self.q(); q0[[6, 7, 14, 15]] = np.clip(q0[[6, 7, 14, 15]], *GRIP_SCALE)
        for i in range(ACTION_STEPS):
            self.d.ctrl[self.act] = q0 + (goal - q0) * min((i + 1) / INTERP_STEPS, 1.0)
            mujoco.mj_step(self.m, self.d, self.sub)
        self.steps += 1
        self.done = self.steps >= STEP_LIMIT

    # ---- state
    def pose(self, name):
        i = self.bodies[name]
        return self.d.xpos[i].copy(), self.d.xmat[i].reshape(3, 3).copy()

    def endpose(self, side):
        i = self.hand[side]
        return self.d.xpos[i].copy(), self.d.xmat[i].reshape(3, 3).copy()

    def arm_force(self, side):
        """The net contact force on an arm's links (world frame, N)."""
        f, f6, mine = np.zeros(3), np.zeros(6), self.arm_bodies[side]
        for i in range(self.d.ncon):
            c = self.d.contact[i]
            b1, b2 = self.m.geom_bodyid[c.geom1], self.m.geom_bodyid[c.geom2]
            if (b1 in mine) == (b2 in mine):
                continue
            mujoco.mj_contactForce(self.m, self.d, i, f6)
            w = np.asarray(c.frame).reshape(3, 3).T @ f6[:3]        # (on geom2, from geom1)
            f += w if b2 in mine else -w
        return f

    def touching(self, name):
        i, robot, others = self.bodies[name], False, set()
        kids = {b for b in range(self.m.nbody) if self._root(b) == i}
        for k in range(self.d.ncon):
            c = self.d.contact[k]
            if c.dist > 0.001:
                continue
            b1, b2 = self.m.geom_bodyid[c.geom1], self.m.geom_bodyid[c.geom2]
            if (b1 in kids) == (b2 in kids):
                continue
            o = b2 if b1 in kids else b1
            on = self.m.body(o).name
            if on[:2] in ("L_", "R_"):
                robot = True
            else:
                g = c.geom2 if b1 in kids else c.geom1
                others.add(self.m.body(self._root(o)).name if o else ("table" if self.m.geom(g).name == "table" else "floor" if self.m.geom(g).name == "floor" else "world"))
        return dict(robot=robot, others=sorted(others))

    def _root(self, b):
        while b and self.m.body_parentid[b] and self.m.body(self.m.body_parentid[b]).name != "world" and self.m.body_parentid[b] != 0:
            b = self.m.body_parentid[b]
        return b

    def held(self, name):
        """Is the body squeezed between the two fingers of one gripper (each presses with more than 1 N, from opposite sides)?"""
        i = self.bodies[name]; kids = {b for b in range(self.m.nbody) if self._root(b) == i}
        f6 = np.zeros(6)
        for s, word in (("L", "left"), ("R", "right")):
            F = {}
            for k in range(self.d.ncon):
                c = self.d.contact[k]
                b1, b2 = self.m.geom_bodyid[c.geom1], self.m.geom_bodyid[c.geom2]
                for a, b, sign in ((b1, b2, 1.0), (b2, b1, -1.0)):
                    n = self.m.body(a).name
                    if n in (f"{s}_link7", f"{s}_link8") and b in kids:
                        mujoco.mj_contactForce(self.m, self.d, k, f6)
                        F[n] = F.get(n, np.zeros(3)) + sign * (np.asarray(c.frame).reshape(3, 3).T @ f6[:3])
            if len(F) == 2:
                fa, fb = F.values()
                if min(np.linalg.norm(fa), np.linalg.norm(fb)) > 1.0 and float(fa @ fb) < 0:
                    return word
        return None

    def speed(self, name):
        v = np.zeros(6); mujoco.mj_objectVelocity(self.m, self.d, mujoco.mjtObj.mjOBJ_BODY, self.bodies[name], v, 0)
        return float(np.linalg.norm(v[3:])) * 1000

    # ---- cameras
    def _renderer(self, w, h):
        if (w, h) not in self._r:
            self._r[(w, h)] = mujoco.Renderer(self.m, h, w)
        return self._r[(w, h)]

    def image(self, cam, w=W, h=H):
        r = self._renderer(w, h); r.disable_depth_rendering(); r.update_scene(self.d, camera=cam)
        return r.render().copy()

    def frame(self, name, t=0.0):
        """A camera's look as the program gets it: colour, depth along the camera's axis (m; nan where nothing is seen),
        the lens, the camera's place and orientation (table frame; x right, y down, z forward)."""
        import cv2
        cam, c = self.cam[name]
        wr = int(round(W * c["fy"] / c["fx"]))                      # (rendered with square pixels, then squeezed to the lens's width)
        r = self._renderer(wr, H)
        r.disable_depth_rendering(); r.update_scene(self.d, camera=cam); rgb = r.render().copy()
        r.enable_depth_rendering(); r.update_scene(self.d, camera=cam); dep = r.render().copy(); r.disable_depth_rendering()
        if wr != W:
            rgb = cv2.resize(rgb, (W, H), interpolation=cv2.INTER_AREA); dep = cv2.resize(dep, (W, H), interpolation=cv2.INTER_NEAREST)
        dep = dep.astype(np.float64)
        i = self.m.camera(cam).id
        Rm = self.d.cam_xmat[i].reshape(3, 3)
        return Frame(dict(camera=name, rgb=np.ascontiguousarray(rgb), depth=np.where((dep > 0.005) & (dep < 9.0), dep, np.nan), t=t,
                          K=(W, H, float(c["fx"]), float(c["fy"]), W / 2.0, H / 2.0), eye=self.d.cam_xpos[i] - [0, 0, TABLE_Z],
                          R=Rm @ np.diag([1.0, -1.0, -1.0])))

    def close(self):
        for r in self._r.values():
            if hasattr(r, "close"):
                r.close()
        self._r = {}


class X5RobotMJ(PA.X5Robot):
    """The program's robot (sweep_blocks/program_api.py X5Robot, unchanged), carried out by the MuJoCo task."""

    def __init__(self, env, segmenter=None, on_step=None):
        self.env, self.segmenter, self.on_step = env, segmenter, on_step
        self.kin = {a: PA.ArmKin(s) for a, s in PA.ARMS.items()}
        self.rng = np.random.default_rng(0); self.frng = np.random.default_rng(1)
        self.q_cmd = {a: np.zeros(6) for a in PA.ARMS}
        self.g_cmd = {a: 1.0 for a in PA.ARMS}
        self.last_curved = False

    def look(self, cameras=None):
        self._time()
        out = {}
        for c in (cameras or PA.CAMERAS):
            if c not in PA.CAMERAS:
                raise KeyError(f"camera must be one of {list(PA.CAMERAS)}")
            out[c] = self.env.frame(c, t=self.time_s())
        return out

    def _force(self, arm):
        return self.env.arm_force(PA.ARMS[arm])


class State:
    """What a practice world's own success(state) gets: the TRUE state of its things (mm, table frame)."""

    def __init__(self, task):
        self._t = task
        self.names = sorted(task.bodies)
        self.start = {n: [round(float(v) * 1000, 2) for v in (task.start[n][0] - [0, 0, TABLE_Z])] for n in task.bodies}

    def pos(self, name):
        return [round(float(v) * 1000, 2) for v in (self._t.pose(name)[0] - [0, 0, TABLE_Z])]

    def rot(self, name):
        return [[round(float(v), 5) for v in row] for row in self._t.pose(name)[1]]

    def start_rot(self, name):
        return [[round(float(v), 5) for v in row] for row in self._t.start[name][1]]

    def touching(self, name):
        return self._t.touching(name)

    def speed(self, name):
        return round(self._t.speed(name), 2)

    def held(self, name):
        return self._t.held(name)

    def arm(self, side):
        s = {"left": "L", "right": "R"}[side]
        p, R = self._t.endpose(s); p0, R0 = self._t.origin[s]
        q = self._t.q()[6 if s == "L" else 14]
        ang = math.degrees(math.acos(float(np.clip((np.trace(R0.T @ R) - 1) / 2, -1, 1))))
        return dict(tcp_mm=[round(float(v) * 1000, 1) for v in (p + R[:, 0] * S.TCP_X - [0, 0, TABLE_Z])], opening_mm=round(float(np.clip(2 * q + 0.0008, 0, PA.MAX_OPEN)) * 1000, 1),
                    from_start_mm=round(float(np.linalg.norm(p - p0)) * 1000, 1), from_start_deg=round(ang, 1))
