"""The bimanual environments' common part (docs/bimanual.md): the rig's two Panda arms in the twin's scene as a plain
MuJoCo simulation. Ported from embodied-rl/bimanual (bi_sim.py, astra_bigrip_real.py; 11-17) and generalised: the
platform's scene, objects and cameras; layouts; the per-episode sensor errors.

- arms "R" (the robot's right, at -y) and "L": straight-line moves of the tool point (damped least-squares IK, the
  orientation interpolated), arcs about an axis (turn), a collision-checked joint-space path for free moves with a big
  change of orientation. The arms are stiff joint position servos (the twin's gains) whose steady-state error is taken
  out after each move, as the real arm's server does: NOT the single-arm platform's compliant controller.
- guarded moves stop at GUARD_N of new force against the motion on that arm; any move stops at SAFETY_N net force;
- sensing as on the rig: the scene D415 and a D415 on each wrist, 640x480 colour + depth; depth through the model fitted
  to the real cameras (vendor/panda_twin/code/d415_model.D415Real: noise, scale, fattening, shadows, flying pixels,
  holes, the left band, nothing nearer than 0.275 m) and a per-episode calibration error of each camera (the pose
  reported is the nominal one); the arm's external-force estimate (net contact force + 0.5 N noise per axis);
- objects: every `obj_*` body with a free joint; reset(layout) puts the named ones on the table and parks the others
  out of sight; the bottle's screw cap (hinge + slide tied by the pitch, friction in the thread) comes free after
  UNSCREW_DEG: the threaded body is swapped for a free one in the same place.
Frame: the rig's (z up, the table top at z = 0, the robot at -x facing +x, +y to its left); metres, radians.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

from agentic_robotics import paths

os.environ.setdefault("MUJOCO_GL", "egl")
try:
    _dev = (paths.local().get("render") or {}).get("egl_device")
    if _dev is not None:
        os.environ.setdefault("MUJOCO_EGL_DEVICE_ID", str(_dev))
except FileNotFoundError:
    pass
import mujoco  # noqa: E402
import numpy as np  # noqa: E402

MODELS = Path(__file__).resolve().parent / "models"
SIDE = {"R": "right", "L": "left"}
ARMS = {"right": "R", "left": "L"}
CAMS = {"scene": "d415", "right_wrist": "R_wrist", "left_wrist": "L_wrist"}
W, H = 640, 480
MIN_Z = 0.275                                                 # m: the D415s see nothing nearer
CALIB = {"d415": (0.005, 1.0), "R_wrist": (0.003, 1.0), "L_wrist": (0.003, 1.0)}   # per-episode pose error (m, deg)
FORCE_NOISE = 0.5                                             # N per axis, the arm's external-force estimate
GUARD_N, SAFETY_N = 4.0, 40.0                                 # (4 N: the real arm layer's contact threshold)
V_SLOW, V_FREE, W_TURN = 0.04, 0.15, math.radians(45)
LOW_Z = 0.30                                                  # below this a free move goes at V_SLOW
REC_FPS = 15
UNSCREW_DEG = 360.0
TABLE_Z = 0.0005                                              # the objects' rest height (their origin: the bottom)
FLOOR_Z = -0.865
PARK = 200.0                                                  # m: where the objects not in the layout lie, on the floor
#                                                               beyond the cameras' far plane (80 m): never rendered
BOTTLE_XY = (-0.21, -0.175)


def rotvec(R):
    c = float(np.clip((np.trace(R) - 1) / 2, -1, 1)); a = math.acos(c)
    if a < 1e-9:
        return np.zeros(3)
    if a > math.pi - 1e-4:
        M = (R + np.eye(3)) / 2
        k = int(np.argmax(np.diag(M))); v = M[:, k] / math.sqrt(max(M[k, k], 1e-12))
        return v * a
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(a))
    return v * a


def expm(v):
    a = float(np.linalg.norm(v))
    if a < 1e-12:
        return np.eye(3)
    k = v / a; K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K


def yaw_quat(yaw_deg):
    h = math.radians(yaw_deg) / 2
    return [math.cos(h), 0.0, 0.0, math.sin(h)]


class Stop(Exception):
    """A move the arm cannot make: nothing has moved."""


class Arm:
    pass


def scene_module(scene):
    """The module of a scene's kind: scenes.py (the varied cylinder-and-stick scenes), clutter.py or biolab.py. It gives
    the scene's model_xml(), paint() and layout(), and for some kinds the task's text(), judge() and truth(), and
    mechanisms() (an object with reset(), step(), get_state(), set_state() and labels: BiCore.mech)."""
    from . import biolab, clutter, scenes
    return {clutter.KIND: clutter, biolab.KIND: biolab}.get(scene.get("kind"), scenes)


class BiCore:
    NAME = None                                                # the model's name (models/NAME.xml)

    def __init__(self, seed=0, sensors="d415", out: Path | None = None, record=False, layout="bottle", scene=None):
        """scene: a varied scene (scenes.py: a cylinder, a stick and distractors; clutter.py: a heap of objects): the
        model gets its objects, and the layout is the scene's."""
        f = MODELS / f"{self.NAME}.xml"
        if not f.exists():
            raise FileNotFoundError(f"{f} missing: build it with scripts/build_bimanual.py")
        if sensors not in ("d415", "ideal"):
            raise ValueError('sensors: "d415" (the real cameras\' depth and calibration errors) or "ideal"')
        self.scene = scene
        if scene is None:
            self.m = m = mujoco.MjModel.from_xml_path(str(f))
        else:
            mod = scene_module(scene)
            self.m = m = mujoco.MjModel.from_xml_string(mod.model_xml(f, scene))
            mod.paint(m, scene)
            layout = mod.layout(scene)
        # the table top's height in this frame (the robot's): 0 but for the scenes on another table (scenes.py)
        self.table_z = float(scene["table"]["height_mm"]) / 1000 if scene and scene.get("table") else 0.0
        self.d = mujoco.MjData(m); self.dk = mujoco.MjData(m)
        self.sensors, self.out = sensors, (Path(out) if out else None)
        self.arms, self.part = {}, {}
        for P in SIDE:
            A = Arm(); A.P = P; A.side = SIDE[P]
            A.arm_j = [m.joint(f"{P}_joint{i + 1}").id for i in range(7)]
            A.qa = np.array([m.jnt_qposadr[j] for j in A.arm_j]); A.va = np.array([m.jnt_dofadr[j] for j in A.arm_j])
            A.arm_a = np.array([m.actuator(f"{P}_a{i + 1}").id for i in range(7)])
            A.lo, A.hi = m.jnt_range[A.arm_j, 0] + 0.02, m.jnt_range[A.arm_j, 1] - 0.02
            A.home = m.key("home").qpos[A.qa].copy()
            A.base, A.elbow = m.body(f"{P}_link0").id, m.body(f"{P}_link4").id
            self._init_arm(A)
            self.arms[P] = A
        self.geom_arm = np.array([self.part.get(int(m.geom_bodyid[g]), ("", ""))[0] for g in range(m.ngeom)], dtype=object)
        self.geom_part = np.array([self.part.get(int(m.geom_bodyid[g]), ("", ""))[1] for g in range(m.ngeom)], dtype=object)
        self.robot_geom = self.geom_arm != ""
        # objects: the free bodies named obj_*
        self.objects = {}
        for b in range(m.nbody):
            n = m.body(b).name; j = m.body_jntadr[b]
            if n.startswith("obj_") and j >= 0 and m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                self.objects[n[4:]] = b
        self.b_bottle, self.b_thr, self.b_free = m.body("obj_bottle").id, m.body("bottle_cap").id, m.body("bottle_cap_free").id
        self.j_turn = m.joint("cap_turn").id; self.j_capfree = m.joint("bottle_cap_free_j").id
        self.thr_geoms = [g for g in range(m.ngeom) if m.geom_bodyid[g] == self.b_thr]
        self.free_geoms = [g for g in range(m.ngeom) if m.geom_bodyid[g] == self.b_free]
        self.geom0 = (m.geom_contype.copy(), m.geom_conaffinity.copy(), m.geom_rgba.copy())
        root = m.body_rootid
        self.obj_geoms = {n: np.flatnonzero(root[m.geom_bodyid] == b) for n, b in self.objects.items()}
        self.obj_bodies = {n: np.flatnonzero(root == b) for n, b in self.objects.items()}
        self._cam0 = {c: (m.cam_pos[m.camera(c).id].copy(), m.cam_quat[m.camera(c).id].copy()) for c in CAMS.values()}
        self.renderers = {}
        self.record = bool(record)
        self.frames, self.t_rec = [], -1.0
        self.rec_cam = mujoco.MjvCamera(); self.rec_cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.rec_cam.lookat[:] = [-0.30, -0.175, 0.17]; self.rec_cam.distance = 1.25; self.rec_cam.azimuth = 160; self.rec_cam.elevation = -22
        self.last_path = "line"
        self.mech = mod.mechanisms(self) if scene is not None and hasattr(mod, "mechanisms") else None
        self.guard, self.glitches = bool(scene is not None and getattr(mod, "GUARD", False)), 0
        self.reset(layout, seed)

    # ------------------------------------------------------------------ per hand type (grippers.py, dexterous.py)
    def _init_arm(self, A):
        raise NotImplementedError

    def _reset_hand(self, A):
        raise NotImplementedError

    # ------------------------------------------------------------------ scene
    def reset(self, layout="bottle", seed=0, joints=None, settle=0.5):
        """A new episode. layout: {object: (x, y, yaw_deg)} (or a name in LAYOUTS): those objects stand on the table,
        every other one is parked out of sight; or {object: (x, y, z, qw, qx, qy, qz)}: the object's whole pose (a heap). joints: {joint name: value} (e.g. a box lid's hinge). seed: the
        episode's sensor errors (camera calibration, depth scale, noise)."""
        m, d = self.m, self.d
        if isinstance(layout, str):
            layout = LAYOUTS[layout]
        layout = dict(layout or {})
        unknown = sorted(set(layout) - set(self.objects))
        if unknown:
            raise KeyError(f"unknown objects {unknown}: this scene has {sorted(self.objects)}")
        m.geom_contype[:], m.geom_conaffinity[:], m.geom_rgba[:] = self.geom0
        self.glitches = 0
        for g in self.free_geoms:                                   # the free cap: parked, no contacts, until the swap
            m.geom_contype[g] = 0; m.geom_conaffinity[g] = 0
        for c, (p0, q0) in self._cam0.items():
            m.cam_pos[m.camera(c).id] = p0; m.cam_quat[m.camera(c).id] = q0
        mujoco.mj_resetDataKeyframe(m, d, m.key("home").id)
        for i, (n, b) in enumerate(sorted(self.objects.items())):
            a = m.jnt_qposadr[m.body_jntadr[b]]
            if n in layout:
                if len(layout[n]) == 7:
                    d.qpos[a:a + 7] = layout[n]; d.qpos[a + 2] += self.table_z
                else:
                    x, y, yaw = (list(layout[n]) + [0.0])[:3]
                    d.qpos[a:a + 3] = [x, y, self.table_z + TABLE_Z]; d.qpos[a + 3:a + 7] = yaw_quat(yaw)
                m.body_gravcomp[self.obj_bodies[n]] = 0
            else:                                                   # parked: out of sight, no contacts, no weight
                d.qpos[a:a + 3] = [PARK + 0.6 * i, PARK, FLOOR_Z + TABLE_Z]; d.qpos[a + 3:a + 7] = [1, 0, 0, 0]
                m.geom_contype[self.obj_geoms[n]] = 0; m.geom_conaffinity[self.obj_geoms[n]] = 0
                m.body_gravcomp[self.obj_bodies[n]] = 1
        for n, v in (joints or {}).items():
            d.qpos[m.jnt_qposadr[m.joint(n).id]] = v
        a = m.jnt_qposadr[self.j_capfree]
        d.qpos[a:a + 7] = [PARK, PARK + 1.0, FLOOR_Z + TABLE_Z, 1, 0, 0, 0]; self.park = d.qpos[a:a + 7].copy()
        for A in self.arms.values():
            d.qpos[A.qa] = A.home; d.ctrl[A.arm_a] = A.home
            A.q_cmd = A.home.copy(); A.bias = np.zeros(7)
            self._reset_hand(A)
        self.layout = layout
        self.cap_free = False; self.t_free = None
        if self.mech:
            self.mech.reset()
        self.frames, self.t_rec = [], -1.0
        self.seed = seed
        self.rng = np.random.default_rng(seed)                      # (IK restarts)
        self.frng = np.random.default_rng(2000 + seed)              # (the force estimate's noise)
        self.dm = None
        if self.sensors == "d415":
            sys.path.insert(0, str(paths.VENDOR / "panda_twin" / "code"))
            import d415_model
            rng = np.random.default_rng(1000 + seed)
            self.dm = d415_model.D415Real(self, rng, MIN_Z, calib=CALIB)
            self.dm.scale = {c: float(rng.uniform(-1, 1)) * self.dm.p["scale"] for c in CAMS.values()}
        mujoco.mj_forward(m, d)
        if settle:
            rec, self.record = self.record, False
            self.run(settle)
            for _ in range(3):                                          # (the servos' steady-state error taken out)
                for A in self.arms.values():
                    A.bias = np.clip(A.bias + (A.q_cmd - d.qpos[A.qa]), -0.05, 0.05); d.ctrl[A.arm_a] = A.q_cmd + A.bias
                self.run(0.15)
            self.record = rec

    def get_state(self):
        """Everything that defines the episode at this instant (a snapshot for set_state: checkpoints, rollouts)."""
        m, d = self.m, self.d
        return dict(env=self.NAME, time=float(d.time), qpos=d.qpos.copy(), qvel=d.qvel.copy(), act=d.act.copy(), ctrl=d.ctrl.copy(),
                    warm=d.qacc_warmstart.copy(), contype=m.geom_contype.copy(), conaffinity=m.geom_conaffinity.copy(),
                    rgba=m.geom_rgba.copy(), gravcomp=m.body_gravcomp.copy(), cap_free=self.cap_free, t_free=self.t_free,
                    layout=dict(self.layout), park=self.park.copy(),
                    arms={P: {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in vars(A).items()
                              if k in ("q_cmd", "bias", "g_cmd", "h_cmd")} for P, A in self.arms.items()},
                    rng=self.rng.bit_generator.state, frng=self.frng.bit_generator.state,
                    mech=self.mech.get_state() if self.mech else None)

    def set_state(self, st):
        m, d = self.m, self.d
        if st["env"] != self.NAME:
            raise ValueError(f"a state of {st['env']}, not of {self.NAME}")
        d.time = st["time"]; d.qpos[:] = st["qpos"]; d.qvel[:] = st["qvel"]; d.act[:] = st["act"]; d.ctrl[:] = st["ctrl"]
        d.qacc_warmstart[:] = st["warm"]
        m.geom_contype[:] = st["contype"]; m.geom_conaffinity[:] = st["conaffinity"]; m.geom_rgba[:] = st["rgba"]
        m.body_gravcomp[:] = st["gravcomp"]
        self.cap_free, self.t_free, self.layout, self.park = st["cap_free"], st["t_free"], dict(st["layout"]), st["park"].copy()
        for P, a in st["arms"].items():
            for k, v in a.items():
                setattr(self.arms[P], k, v.copy() if isinstance(v, np.ndarray) else v)
        self.rng.bit_generator.state = st["rng"]; self.frng.bit_generator.state = st["frng"]
        if self.mech:
            self.mech.set_state(st["mech"])
        mujoco.mj_forward(m, d)

    def place(self, name, x, y, yaw_deg=0.0, z=None):
        """Object `name` (parked or not) on the table at (x, y), turned by yaw_deg, at rest."""
        m, d = self.m, self.d
        z = self.table_z + TABLE_Z if z is None else z
        g = self.obj_geoms[name]
        if name == "bottle":
            g = np.array([i for i in g if i not in (self.thr_geoms if self.cap_free else [])], int)
        m.geom_contype[g] = self.geom0[0][g]; m.geom_conaffinity[g] = self.geom0[1][g]
        m.body_gravcomp[self.obj_bodies[name]] = 0
        self.layout[name] = (x, y, yaw_deg)
        j = m.body_jntadr[self.objects[name]]; a = m.jnt_qposadr[j]; va = m.jnt_dofadr[j]
        d.qpos[a:a + 3] = [x, y, z]; d.qpos[a + 3:a + 7] = yaw_quat(yaw_deg); d.qvel[va:va + 6] = 0
        mujoco.mj_forward(m, d)

    def object_pose(self, name):
        """TRUE pose (for judges and tests; a controller must use the cameras)."""
        b = self.objects[name]
        return self.d.xpos[b].copy(), self.d.xmat[b].reshape(3, 3).copy()

    def cap_angle(self):
        """Degrees the cap has been unscrewed (UNSCREW_DEG = it comes free)."""
        return math.degrees(float(self.d.qpos[self.m.jnt_qposadr[self.j_turn]]))

    def cap_pose(self):
        b = self.b_free if self.cap_free else self.b_thr
        return self.d.xpos[b].copy(), self.d.xmat[b].reshape(3, 3).copy()

    def bottle_pose(self):
        return self.d.xpos[self.b_bottle].copy(), self.d.xmat[self.b_bottle].reshape(3, 3).copy()

    def _release_cap(self):
        m, d = self.m, self.d
        v6 = np.zeros(6); mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, self.b_thr, v6, 0)
        R = d.xmat[self.b_thr].reshape(3, 3)
        a = m.jnt_qposadr[self.j_capfree]; va = m.jnt_dofadr[self.j_capfree]
        d.qpos[a:a + 3] = d.xpos[self.b_thr]; d.qpos[a + 3:a + 7] = d.xquat[self.b_thr]
        d.qvel[va:va + 3] = v6[3:]; d.qvel[va + 3:va + 6] = R.T @ v6[:3]
        for g in self.thr_geoms:
            m.geom_contype[g] = 0; m.geom_conaffinity[g] = 0; m.geom_rgba[g, 3] = 0.0
        for g in self.free_geoms:
            m.geom_contype[g] = self.geom0[0][g]; m.geom_conaffinity[g] = self.geom0[1][g]
        self.cap_free = True; self.t_free = float(d.time)
        mujoco.mj_forward(m, d)

    # ------------------------------------------------------------------ physics, contacts, recording
    def contacts(self):
        """{arm: ({part: |force| N}, net force vector on that arm + hand)} from everything that is not that arm."""
        m, d = self.m, self.d
        out = {P: ({}, np.zeros(3)) for P in self.arms}
        n = d.ncon
        if n == 0:
            return out
        g1, g2 = d.contact.geom1[:n], d.contact.geom2[:n]
        idx = np.nonzero(self.robot_geom[g1] | self.robot_geom[g2])[0]
        f6 = np.zeros(6)
        for i in idx:
            a1, a2 = self.geom_arm[g1[i]], self.geom_arm[g2[i]]
            if a1 == a2:
                continue
            mujoco.mj_contactForce(m, d, int(i), f6)
            F = d.contact.frame[i].reshape(3, 3).T @ f6[:3]             # on geom2, by geom1
            for a, g, Fa in ((a1, g1[i], -F), (a2, g2[i], F)):
                if a:
                    parts, net = out[a]; net += Fa
                    p = self.geom_part[g]; parts[p] = parts.get(p, 0.0) + float(np.linalg.norm(F))
        return out

    def force_estimate(self, P):
        """What the arm reports: the net external force on it (N, world frame), noisy."""
        return self.contacts()[P][1] + self.frng.normal(0, FORCE_NOISE, 3)

    def touching(self, body):
        """({arm: parts} touching that body, names of other bodies touching it). TRUE state (judges, tests)."""
        m, d = self.m, self.d
        if isinstance(body, str):
            body = self.objects[body] if body in self.objects else m.body(body).id
        parts, other = {}, set()
        for i in range(d.ncon):
            c = d.contact[i]
            b1, b2 = int(m.geom_bodyid[c.geom1]), int(m.geom_bodyid[c.geom2])
            if body not in (b1, b2):
                continue
            o = b2 if b1 == body else b1
            if o in self.part:
                parts.setdefault(self.part[o][0], set()).add(self.part[o][1])
            else:
                other.add(m.body(o).name or "world")
        return parts, other

    def _false_contacts(self):
        """The smaller geoms of the contacts that cannot be: deeper than 30 mm, or 10 mm outside that geom's bounding
        sphere. (MuJoCo's box-box collision makes such a contact now and then for a small box that lies flat on a large
        one, here the table: half a metre away from the box and a metre deep, and the object is thrown off.)"""
        m, d = self.m, self.d
        if not d.ncon:
            return []
        c = d.contact
        g1, g2 = c.geom1, c.geom2
        r1, r2 = m.geom_rbound[g1], m.geom_rbound[g2]
        g = np.where((r1 <= r2) & (r1 > 0) | (r2 <= 0), g1, g2)
        bad = (c.dist < -0.03) | (np.linalg.norm(c.pos - d.geom_xpos[g], axis=1) > m.geom_rbound[g] + 0.01 + np.abs(c.dist))
        return np.unique(g[bad]) if bad.any() else []

    def _advance(self):
        """One physics step; with the guard (a scene kind's GUARD), without the contacts that cannot be (_false_contacts:
        their smaller geom does not collide during this step; counted in self.glitches)."""
        m, d = self.m, self.d
        if not self.guard:
            mujoco.mj_step(m, d)
            return
        mujoco.mj_step1(m, d)
        bad = self._false_contacts()
        if len(bad):
            keep = m.geom_contype[bad].copy(), m.geom_conaffinity[bad].copy()
            m.geom_contype[bad] = 0; m.geom_conaffinity[bad] = 0
            mujoco.mj_step1(m, d)
            m.geom_contype[bad], m.geom_conaffinity[bad] = keep
            self.glitches += 1
        mujoco.mj_step2(m, d)

    def step(self, n=1):
        for _ in range(n):
            self._advance()
            if not self.cap_free:
                if self.cap_angle() >= UNSCREW_DEG:
                    self._release_cap()
                else:
                    a = self.m.jnt_qposadr[self.j_capfree]; va = self.m.jnt_dofadr[self.j_capfree]
                    self.d.qpos[a:a + 7] = self.park; self.d.qvel[va:va + 6] = 0
            if self.mech:
                self.mech.step()
            if self.record and self.d.time - self.t_rec >= 1.0 / REC_FPS - 1e-9:
                self.t_rec = self.d.time; self.frames.append(self.render_rec())

    def run(self, s):
        self.step(int(round(s / self.m.opt.timestep)))

    def render_rec(self):
        r = self.renderer(480, 640); r.update_scene(self.d, camera=self.rec_cam)
        return r.render().copy()

    def renderer(self, h, w, seg=False):
        """seg: a renderer of geom ids, without multisampling (where geoms intersect, the samples of a pixel are
        averaged: ids that belong to nothing, and mujoco.Renderer then fails on those out of range)."""
        if (h, w, seg) not in self.renderers:
            n = self.m.vis.quality.offsamples
            if seg:
                self.m.vis.quality.offsamples = 0
            self.renderers[(h, w, seg)] = r = mujoco.Renderer(self.m, h, w)
            self.m.vis.quality.offsamples = n
            if seg:
                r.enable_segmentation_rendering()
        return self.renderers[(h, w, seg)]

    def write_video(self, path):
        if not self.frames:
            return None
        h, w = self.frames[0].shape[:2]
        p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                              "-r", str(REC_FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", str(path)],
                             stdin=subprocess.PIPE)
        for f in self.frames:
            p.stdin.write(f.tobytes())
        p.stdin.close(); p.wait()
        return path

    # ------------------------------------------------------------------ cameras
    def set_camera_error(self, cam, dpos, drot_deg):
        """(d415_model.D415Real calls this.) The camera's TRUE pose = its nominal mount moved by dpos (m), drot (deg)."""
        m, cid = self.m, self.m.camera(cam).id
        p0, q0 = self._cam0[cam]
        R0 = np.zeros(9); mujoco.mju_quat2Mat(R0, q0); R0 = R0.reshape(3, 3)
        R1 = R0 @ expm(np.radians(np.asarray(drot_deg, float)))
        q1 = np.zeros(4); mujoco.mju_mat2Quat(q1, R1.flatten())
        m.cam_pos[cid] = p0 + np.asarray(dpos); m.cam_quat[cid] = q1

    def nominal_camera(self, cam):
        """(R camera-to-world with OpenCV axes, eye) from the NOMINAL mount: the pose the robot believes."""
        m, d, cid = self.m, self.d, self.m.camera(cam).id
        p0, q0 = self._cam0[cam]
        R0 = np.zeros(9); mujoco.mju_quat2Mat(R0, q0); R0 = R0.reshape(3, 3)
        b = m.cam_bodyid[cid]; Rb = d.xmat[b].reshape(3, 3)
        return Rb @ R0 @ np.diag([1.0, -1.0, -1.0]), d.xpos[b] + Rb @ p0

    def observe(self, which):
        """A frame of camera `which` (scene | right_wrist | left_wrist): rgb (H, W, 3 uint8), depth (m, nan = none),
        K = (W, H, fx, fy, cx, cy), R (camera to world, OpenCV axes: x right, y down, z forward) and eye: the camera's
        pose as the robot believes it (with sensors="d415" the true one differs by the episode's calibration error)."""
        cam = CAMS[which]
        m, d = self.m, self.d
        mujoco.mj_forward(m, d)
        r = self.renderer(H, W); cid = m.camera(cam).id
        r.update_scene(d, camera=cid); rgb = r.render().copy()
        r.enable_depth_rendering(); r.update_scene(d, camera=cid); dep = r.render().copy(); r.disable_depth_rendering()
        depth = np.where(np.isfinite(dep) & (dep < 10), dep, np.inf).astype(np.float32)
        if self.dm is not None:
            a0 = m.geom_rgba[:, 3].copy()                               # (see-through geoms blend their ids: opaque here)
            m.geom_rgba[(a0 > 0) & (a0 < 1), 3] = 1.0
            rs = self.renderer(H, W, seg=True); rs.update_scene(d, camera=cid); seg = rs.render().copy()
            m.geom_rgba[:, 3] = a0
            gid = np.where((seg[..., 1] == int(mujoco.mjtObj.mjOBJ_GEOM)) & (seg[..., 0] >= 0), seg[..., 0], -1)
            depth = self.dm.apply(depth, gid, cam)
        depth = depth.astype(np.float64); depth[~np.isfinite(depth)] = np.nan
        f = H / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        Rn, eye = self.nominal_camera(cam)
        return dict(camera=which, rgb=rgb, depth=depth, K=(W, H, f, f, W / 2, H / 2), eye=eye, R=Rn, t=float(d.time))

    @staticmethod
    def project(P, o):
        _, _, fx, fy, cx, cy = o["K"]
        c = (np.atleast_2d(P) - o["eye"]) @ o["R"]; z = c[:, 2]
        return np.c_[fx * c[:, 0] / z + cx, fy * c[:, 1] / z + cy], z

    @staticmethod
    def unproject(o, u, v):
        """The 3D point seen at pixel (u, v) of frame o (the median depth in a 7x7 window), or None."""
        Wd, Hd, fx, fy, cx, cy = o["K"]
        ui, vi = int(round(u)), int(round(v))
        if not (0 <= ui < Wd and 0 <= vi < Hd):
            return None
        win = o["depth"][max(vi - 3, 0):vi + 4, max(ui - 3, 0):ui + 4]; win = win[np.isfinite(win)]
        if len(win) < 5:
            return None
        z = float(np.median(win))
        return o["eye"] + o["R"] @ np.array([(u - cx) / fx * z, (v - cy) / fy * z, z])

    # ------------------------------------------------------------------ the arms
    def fk(self, P, q):
        A, dk = self.arms[P], self.dk
        dk.qpos[:] = self.d.qpos; dk.qpos[A.qa] = q
        mujoco.mj_kinematics(self.m, dk)
        return dk.site_xpos[A.palm].copy(), dk.site_xmat[A.palm].reshape(3, 3).copy()

    def ik(self, P, p, R, q0, iters=150):
        A, m, dk = self.arms[P], self.m, self.dk
        dk.qpos[:] = self.d.qpos
        q = q0.copy(); jp, jr = np.zeros((3, m.nv)), np.zeros((3, m.nv)); wr = 0.15
        for _ in range(iters):
            dk.qpos[A.qa] = q
            mujoco.mj_kinematics(m, dk); mujoco.mj_comPos(m, dk)
            x = dk.site_xpos[A.palm]; Rc = dk.site_xmat[A.palm].reshape(3, 3)
            ep, er = p - x, rotvec(R @ Rc.T)
            if np.linalg.norm(ep) < 2e-4 and np.linalg.norm(er) < 2e-3:
                break
            mujoco.mj_jacSite(m, dk, jp, jr, A.palm)
            J = np.vstack([jp[:, A.va], wr * jr[:, A.va]]); e = np.r_[ep, wr * er]
            JJ = J @ J.T + 1e-4 * np.eye(6)
            dq = J.T @ np.linalg.solve(JJ, e)
            dq += (np.eye(7) - J.T @ np.linalg.solve(JJ, J)) @ (0.05 * (A.home - q))
            s = np.abs(dq).max()
            if s > 0.15:
                dq *= 0.15 / s
            q = np.clip(q + dq, A.lo, A.hi)
        dk.qpos[A.qa] = q; mujoco.mj_kinematics(m, dk)
        ep = float(np.linalg.norm(p - dk.site_xpos[A.palm]))
        er = float(np.linalg.norm(rotvec(R @ dk.site_xmat[A.palm].reshape(3, 3).T)))
        return q, ep, er

    def _follow(self, P, poses):
        """Joint waypoints through a list of (p, R) from the commanded joints; Stop if the arm cannot."""
        A = self.arms[P]; Q = [A.q_cmd.copy()]
        for k, (p, R) in enumerate(poses):
            q, ep, er = self.ik(P, p, R, Q[-1])
            if ep > 0.002 or er > math.radians(1.5):
                raise Stop(f"out of the arm's reach ({ep * 1000:.0f} mm / {math.degrees(er):.0f} deg short at "
                           f"{100 * (k + 1) / len(poses):.0f}% of the way)")
            if np.abs(q - Q[-1]).max() > 0.2:
                raise Stop("the arm would have to swing its joints around on the way (a wrist flip): try another orientation, "
                           "a smaller step, or go in two moves")
            Q.append(q)
        return np.array(Q)

    def plan(self, P, p1, R1):
        p0, R0 = self.fk(P, self.arms[P].q_cmd)
        dist = float(np.linalg.norm(p1 - p0)); w = rotvec(R0.T @ R1); ang = float(np.linalg.norm(w))
        n = max(int(math.ceil(dist / 0.005)), int(math.ceil(ang / math.radians(2))), 1)
        return self._follow(P, [(p0 + (p1 - p0) * i / n, R0 @ expm(w * i / n)) for i in range(1, n + 1)]), dist, ang

    def solutions(self, P, p, R, tries=60):
        """IK with restarts, the best first: far from the joint limits (a free move that ends in a branch at a limit
        leaves no room for the next straight line), near the home posture, and the elbow on the arm's own side (the
        two bases are 0.43 m apart: an elbow swung towards the other arm is in its way)."""
        A, m, dk = self.arms[P], self.m, self.dk; sols = []
        for q0 in [A.home, A.q_cmd] + [self.rng.uniform(A.lo, A.hi) for _ in range(tries)]:
            q, ep, er = self.ik(P, p, R, q0, iters=300)
            if ep < 0.001 and er < math.radians(0.6) and not any(np.abs(q - o).max() < 0.05 for o, _ in sols):
                inward = float((dk.xpos[A.elbow][1] - m.body_pos[A.base][1]) * (1 if P == "R" else -1))   # (ik left dk at q)
                sc = (float(np.min(np.minimum(q - A.lo, A.hi - q) / (A.hi - A.lo))) - 0.05 * float(np.abs(q - A.home).max())
                      - 1.0 * max(inward - 0.03, 0.0))
                sols.append((q, sc))
                if len(sols) >= 8:
                    break
        return [q for q, _ in sorted(sols, key=lambda t: -t[1])]

    def solve(self, P, p, R, tries=60):
        sols = self.solutions(P, p, R, tries)
        return sols[0] if sols else None

    def held_bodies(self, P):
        """Bodies arm P touches right now (what it holds): not obstacles for its own joint path."""
        m, d = self.m, self.d; out = set()
        for i in range(d.ncon):
            c = d.contact[i]
            a1, a2 = self.geom_arm[c.geom1], self.geom_arm[c.geom2]
            if (a1 == P) != (a2 == P):
                out.add(int(m.geom_bodyid[c.geom2 if a1 == P else c.geom1]))
        return out

    def _body_label(self, b):
        n = self.m.body(b).name
        if not n or n == "world":
            return "the table or the arms' frame"
        if n.startswith("bottle_cap"):
            return "the bottle's cap"
        for prefix, label in (self.mech.labels.items() if self.mech else ()):
            if n.startswith(prefix):
                return label
        return "the " + n.removeprefix("obj_").replace("_", " ")

    def collides(self, P, q, ignore=()):
        """What arm P (with its hand as it is) would touch with its arm joints at q: a name, or None."""
        A, m, dk = self.arms[P], self.m, self.dk
        dk.qpos[:] = self.d.qpos; dk.qpos[A.qa] = q
        mujoco.mj_fwdPosition(m, dk)
        for i in range(dk.ncon):
            c = dk.contact[i]
            a1, a2 = self.geom_arm[c.geom1], self.geom_arm[c.geom2]
            if (a1 == P) != (a2 == P) and c.dist < 0:
                o = c.geom2 if a1 == P else c.geom1
                if int(m.geom_bodyid[o]) in ignore:
                    continue
                return "the other arm" if self.geom_arm[o] else self._body_label(int(m.geom_bodyid[o]))
        return None

    def arms_collide(self, P, q):
        """Would the two arms run into each other with arm P's joints at q? Fingers (and palms) touching each other
        do not count: two hands may hold the same thing."""
        A, m, dk = self.arms[P], self.m, self.dk
        dk.qpos[:] = self.d.qpos; dk.qpos[A.qa] = q
        mujoco.mj_fwdPosition(m, dk)
        for i in range(dk.ncon):
            c = dk.contact[i]
            a1, a2 = self.geom_arm[c.geom1], self.geom_arm[c.geom2]
            if a1 and a2 and a1 != a2 and c.dist < 0 and "arm" in (self.geom_part[c.geom1], self.geom_part[c.geom2]):
                return True
        return False

    def _check_arms(self, P, Q):
        """Stop when the waypoints Q would run arm P into the other arm (not when they already touch: a way out)."""
        if self.arms_collide(P, Q[0]):
            return
        for q in list(Q[2::2]) + [Q[-1]]:
            if self.arms_collide(P, q):
                raise Stop("the two arms would run into each other on the way: move the other arm out of the way first, "
                           "go round (up, then across), or choose another orientation")

    def joint_path(self, P, p1, R1):
        """A joint-space path to the pose: the best IK solution whose path touches nothing (the arm and the hand, as
        they are; what the hand holds is not in its way)."""
        A = self.arms[P]
        sols = self.solutions(P, p1, R1)
        if not sols:
            raise Stop("out of the arm's reach (no joint solution for that pose)")
        held = self.held_bodies(P); first = None
        for q1 in sols:
            n = max(int(math.ceil(np.abs(q1 - A.q_cmd).max() / 0.01)), 2)
            Q = np.array([A.q_cmd + (q1 - A.q_cmd) * i / n for i in range(n + 1)])
            hit = next((h for h in (self.collides(P, q, held) for q in list(Q[1::2]) + [Q[-1]]) if h), None)
            if not hit:
                return Q
            first = first or hit
        raise Stop(f"out of the arm's reach on a straight line, and the way round by the joints would hit {first}: "
                   f"go up first, move in smaller steps, or choose another orientation")

    def _execute(self, P, Q, T, guarded, u=None):
        """Play joint waypoints Q over T seconds. -> stop reason or None."""
        A = self.arms[P]
        f0 = self.contacts()[P][1]
        n = max(int(round(T / self.m.opt.timestep)), 1)
        every = max(int(round(0.005 / self.m.opt.timestep)), 1)        # (the force check: every 5 ms)
        s = np.linspace(0, len(Q) - 1, n + 1)[1:]
        stop = None
        for i, si in enumerate(s):
            k = min(int(si), len(Q) - 2); f = si - k
            q = Q[k] * (1 - f) + Q[k + 1] * f
            self.d.ctrl[A.arm_a] = q + A.bias; A.q_cmd = q
            self.step()
            if i % every == 0:
                net = self.contacts()[P][1]; fa = float(np.linalg.norm(net))
                if fa > max(SAFETY_N, float(np.linalg.norm(f0)) + 5):
                    stop = f"SAFETY STOP: {fa:.0f} N on the {SIDE[P]} arm"
                elif guarded:
                    dF = net - f0
                    if (float(dF @ -u) if u is not None else fa - float(np.linalg.norm(f0))) > GUARD_N:
                        stop = "contact"
                if stop:
                    break
        if stop:
            A.q_cmd = self.d.qpos[A.qa].copy(); self.d.ctrl[A.arm_a] = A.q_cmd + A.bias
            self.run(0.3)
            return stop
        self.run(0.2)
        for _ in range(3):                                            # settle: the servos' steady-state error (gravity:
            if float(np.linalg.norm(self.contacts()[P][1])) > 1.0:    # ~9 mm at the hand) is taken out, as the real
                break                                                 # arm's server does; not while something pushes back
            A.bias = np.clip(A.bias + (A.q_cmd - self.d.qpos[A.qa]), -0.05, 0.05)
            self.d.ctrl[A.arm_a] = A.q_cmd + A.bias
            self.run(0.15)
        return stop

    def move(self, P, p1, R1, guarded=False, v=None):
        """The tool point of arm P along a straight line to p1 with orientation R1 (or, a free move the arm cannot do
        as a line, a joint-space path: self.last_path = "joint"). Returns the stop reason or None; raises Stop (nothing
        moved) when the arm cannot."""
        A = self.arms[P]
        p1 = np.asarray(p1, float)
        self.last_path = "line"
        p0, R0 = self.fk(P, A.q_cmd)
        Q = None
        if not guarded and float(np.linalg.norm(rotvec(R0.T @ R1))) > math.radians(40):
            try:                                                      # a big turn: by the joints, to the IK solution
                Q = self.joint_path(P, p1, R1); self.last_path = "joint"   # with the most room (a line's IK ended at
                dist, ang = float(np.linalg.norm(p1 - p0)), float(np.abs(Q[-1] - Q[0]).max()) * 1.2   # a limit)
            except Stop:
                Q = None
        if Q is None:
            try:
                Q, dist, ang = self.plan(P, p1, R1)
                self._check_arms(P, Q)
            except Stop:
                if guarded:
                    raise
                Q = self.joint_path(P, p1, R1); self.last_path = "joint"
                dist, ang = float(np.linalg.norm(p1 - p0)), float(np.abs(Q[-1] - Q[0]).max()) * 1.2
        low = min(p0[2], p1[2]) < LOW_Z
        v = v or (V_SLOW if guarded or low else V_FREE)
        u = (p1 - p0) / max(float(np.linalg.norm(p1 - p0)), 1e-9) if np.linalg.norm(p1 - p0) > 0.001 else None
        return self._execute(P, Q, max(dist / v, ang / W_TURN, 0.3), guarded, u)

    def turn(self, P, centre, axis, angle_deg, w=math.radians(30)):
        """Rotate the whole hand of arm P rigidly by angle_deg about the line through centre along axis (right-hand
        rule: axis (0, 0, 1) and a positive angle = counter-clockwise seen from above). -> stop reason or None."""
        A = self.arms[P]
        axis = np.asarray(axis, float); axis = axis / max(np.linalg.norm(axis), 1e-9); c = np.asarray(centre, float)
        p0, R0 = self.fk(P, A.q_cmd)
        th = math.radians(angle_deg); n = max(int(math.ceil(abs(th) / math.radians(2))), 1)
        poses = []
        for i in range(1, n + 1):
            E = expm(axis * th * i / n); poses.append((c + E @ (p0 - c), E @ R0))
        Q = self._follow(P, poses)
        self._check_arms(P, Q)
        return self._execute(P, Q, max(abs(th) / w, 0.3), False)

    def go_home(self, P):
        """Back to the start pose by the joints (collision-checked)."""
        p, R = self.fk(P, self.arms[P].home)
        A = self.arms[P]
        n = max(int(math.ceil(np.abs(A.home - A.q_cmd).max() / 0.01)), 2)
        Q = np.array([A.q_cmd + (A.home - A.q_cmd) * i / n for i in range(n + 1)])
        held = self.held_bodies(P)
        for q in list(Q[1::2]) + [Q[-1]]:
            hit = self.collides(P, q, held)
            if hit:
                raise Stop(f"the way home by the joints would hit {hit}: move clear first")
        self.last_path = "joint"
        return self._execute(P, Q, max(float(np.abs(Q[-1] - Q[0]).max()) * 1.2 / W_TURN, 0.3), False)

    # ------------------------------------------------------------------ state
    def tool_pose(self, P):
        """The tool point's pose (the gripper's TCP / the hand's palm centre): position, 3x3 orientation."""
        A = self.arms[P]
        return self.d.site_xpos[A.palm].copy(), self.d.site_xmat[A.palm].reshape(3, 3).copy()

    palm_pose = tool_pose

    def lifted(self, name, dz=0.03):
        """TRUE state: the object is clear of the table by dz and touches nothing but the robot."""
        p, _ = self.object_pose(name)
        parts, other = self.touching(name)
        return bool(p[2] > self.table_z + TABLE_Z + dz and parts and not other)

    def judge_open_bottle(self):
        """TRUE state: the cap off the bottle (free, at least 30 mm from where it sat on the neck, not resting on the
        bottle), the bottle upright and not knocked off the table."""
        m = self.m
        bp, bR = self.bottle_pose(); cp, _ = self.cap_pose()
        neck = bp + bR @ np.array([0, 0, float(m.body_pos[self.b_thr][2])])
        sep = float(np.linalg.norm(cp - neck))
        upright = bool(bR[2, 2] > 0.95)
        parts, other = self.touching(self.b_free) if self.cap_free else ({}, set())
        on_bottle = "obj_bottle" in other
        ok = bool(self.cap_free and sep >= 0.03 and not on_bottle and upright and bp[2] > self.table_z - 0.02 and cp[2] > self.table_z - 0.05)
        return dict(ok=ok, cap_free=self.cap_free, unscrewed_deg=round(self.cap_angle()), cap_from_neck_mm=round(sep * 1000),
                    cap_on_bottle=on_bottle, bottle_upright=upright, bottle_xyz_mm=[round(float(v) * 1000) for v in bp],
                    cap_xyz_mm=[round(float(v) * 1000) for v in cp],
                    cap_held_by={SIDE[k]: sorted(v) for k, v in parts.items()})

    def stick_top(self):
        """TRUE state: the centre of the stick's top, the stick's axis."""
        g = self.m.geom("stand_stick")
        p, R = self.object_pose("stick_stand")
        return p + R @ (g.pos + [0, 0, g.size[1]]), R[:, 2].copy()

    def judge_insert(self):
        """TRUE state: the stick passes through the cylinder's hole (the cylinder's middle is below the stick's top
        and within 4 mm of its axis: the hole's wall leaves no other way), the robot does not touch the cylinder,
        the stand is upright on the table."""
        top, ax = self.stick_top()
        sp, sR = self.object_pose("stick_stand"); cp, cR = self.object_pose("cylinder")
        h = 2 * float(self.m.geom("cyl_w0").size[2])
        v = cp + cR[:, 2] * h / 2 - top
        below = -float(v @ ax); side = float(np.linalg.norm(v + below * ax))
        parts, other = self.touching("cylinder")
        on = bool(below > 0 and side < 0.004)
        upright = bool(sR[2, 2] > 0.95 and abs(sp[2] - self.table_z) < 0.01)
        return dict(ok=bool(on and not parts and upright), on_stick=on, released=not parts, stand_upright=upright,
                    stick_through_mm=round(float(np.clip(below + h / 2, 0, h)) * 1000, 1) if side < 0.004 else 0.0, off_axis_mm=round(side * 1000, 1),
                    cylinder_xyz_mm=[round(float(x) * 1000) for x in cp], stick_top_xyz_mm=[round(float(x) * 1000) for x in top],
                    cylinder_touches=sorted(other), held_by={SIDE[k]: sorted(x) for k, x in parts.items()})


# name -> {object: (x, y, yaw_deg)}; any dict of that form is a layout (docs/bimanual.md: the shared workspace)
LAYOUTS = {
    "empty": {},
    "bottle": {"bottle": (*BOTTLE_XY, 0.0)},
}
