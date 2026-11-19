"""RL environment for the clutter task (11-07, user: "can we build an RL environment for training a policy to solve the
clutter task?"). A gymnasium.Env over the robot-path twin, the same motion layer as the real robot: real_sim.RealSim
over TwinArm (TWIN_COMPLIANT: deoxys' joint impedance gains, the server's trajectory checks, the 35 N force stop, the
lag abort), the wedge fin-ray fingers (CLUTTER_GRIPPER=wedge), the realistic D415 depth (d415_model: holes, shadows,
edge fattening, a random calibration error per episode). Scenes: clutter_bench's pile layouts (the target touching
4-6 others, the white box with its lid open) from the banks of rl/make_layouts.py, or the coffee-pod scenes (kcup_0/1).
Task as the bench judges it: the target in the white box, nothing off the table.

Two action levels (mode=):
  "primitive" (default) Box(-1, 1, (7,)) = [k_grasp, k_push, k_place, x, y, yaw, p]: argmax of the first three picks
              the primitive; x, y over the heightmap's region (X_RANGE, Y_RANGE); yaw in [-90, 90) deg = the fingers'
              closing direction (grasp, place) or the push direction (push); p in [-1, 1]:
                grasp(x, y, yaw, opening 30..80 mm)  open, guarded descent (force only, 4 N) to 3 cm under the local
                                                     top seen in the heightmap (>= 4 mm over the table), close with
                                                     force, lift; held = the fingers did not close (> 3 mm)
                push(x, y, yaw, 3..12 cm)            fingers closed, guarded descent at (x, y) to 6 mm over the table
                                                     (stopped higher than 2 cm: blocked, nothing pushed), straight
                                                     along yaw at 3 cm/s, up
                place(x, y, yaw)                     (holding) carried high, guarded descent until what it holds
                                                     touches something, let go, up
              then the hand back to the park pose (out of the scene camera's view). <= 12 per episode.
  "waypoint"  Box(-1, 1, (6,)) = [x, y, z, yaw, width, guarded]: Astra end to end's interface (real/astra_e2e.py):
              the fingertip point straight to (x, y, z 0..300 mm) at yaw (guarded > 0: stops on a contact), then the
              gripper to width 0..80 mm (< 3 mm: close with force). <= 60 per episode.

Observation (Dict; obs= picks the parts):
  heightmap  (3, 192, 256) float32 over X_RANGE x Y_RANGE at 3 mm (row i: x = X_RANGE[0] + (i + .5) RES, column j: y):
             [0] height over the table (m, 0 where unseen), [1] seen (1/0), [2] the target (1/0). From the scene
             D415's depth projected with its NOMINAL pose (the true one is off by the episode's calibration error),
             the robot's own pixels masked (it knows its pose); the target channel = the twin's segmentation (on the
             robot: SAM3's mask of the target's name, clutter_bench).
  proprio    (8,) fingertip point (m), cos/sin 2 yaw, opening (m), holding (1/0), steps used / max
  objects    (138,) PRIVILEGED (the twin's state, for a teacher policy / critic): 11 slots (SLOTS) x [present, target,
             held, position (3), rotation's first two columns (6)] + the white box [position (3), cos/sin yaw, lid]
Reward (reward="shaped", default): -0.02 a step, -0.1 a safety stop / refused or unreachable move, -0.05 an invalid
primitive (place with nothing held; grasp / push while holding); + gamma * phi(s') - phi(s) with phi = 0.3 x the
target held + 0.2 x the fraction of the target's grasps the arm could make (kin_grasps: 12 yaws x 2 heights, the arm's
collision geometry placed by IK touches nothing else); +1 on success (-0.1 per other object in the box); -0.5 and the
end when something falls off the table. reward="sparse": +1 / -0.5 at the end only.
Episode end: success (the target in the box, the hand empty), something off the table, the arm stuck (3 recoveries
failed in a row), or max_steps (truncated).
info: outcome of the step (ok / contact / blocked / unreachable / stop / invalid / empty / lost), held object, the
judge, safety stops, sim / wall time, action_mask [grasp, push, place].

    from rl.clutter_env import ClutterEnv
    env = ClutterEnv(bank="pile_train"); obs, info = env.reset(seed=0); obs, r, term, trunc, info = env.step(a)

Environment variables set here (before the twin modules read them): CLUTTER_GRIPPER=wedge, TWIN_COMPLIANT=1,
BOX_REAL=0, MUJOCO_GL=egl (one EGL context per process; MUJOCO_EGL_DEVICE_ID picks the GPU).
"""
from __future__ import annotations

import os
import sys
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("CLUTTER_GRIPPER", "wedge"); os.environ.setdefault("BOX_REAL", "0")
os.environ.setdefault("TWIN_COMPLIANT", "1"); os.environ.setdefault("MUJOCO_GL", "egl")
import json
import math
import time
from pathlib import Path

import gymnasium as gym
import numpy as np
from gymnasium import spaces

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent / "real"))
import stack_bench as B  # noqa: E402
import task_bench as T  # noqa: E402
import task_box as TB  # noqa: E402
import clutter_bench as CB  # noqa: E402
import d415_model  # noqa: E402
from real_sim import RealSim, RobotStop, TwinArm  # noqa: E402
from panda_fk import load_rig  # noqa: E402

LAYOUTS = B.ROOT / "out/rl_layouts"
RES = 0.003
X_RANGE, Y_RANGE = (-0.676, -0.100), (-0.284, 0.484)                  # 192 x 256 cells: the piles, the box, around
NX, NY = int(round((X_RANGE[1] - X_RANGE[0]) / RES)), int(round((Y_RANGE[1] - Y_RANGE[0]) / RES))
SLOTS = ["tape_red", "tape_yellow", "tape_white", "hole_block", "cable", "screwdriver", "sticky_pad", "glue",
         "box_brown", "kcup", "tomato"]
EXTRA_OBJ = {"kcup": (dict(foot=0.027, h=0.045, desc="the coffee pod"), "small white coffee cup with a pink lid"),
             "tomato": (dict(foot=0.032, h=0.062, desc="the tomato"), "tomato")}
TIP = T.FINGERTIP
V_FREE, V_LOW, V_GUARD, V_APPROACH = 0.15, 0.03, 0.04, 0.08          # m/s (astra_e2e: free, pushes, guarded)
Z_TRANSIT, Z_TRANSIT_HELD = 0.20, 0.26                                 # fingertips, transits (glue upright: 133 mm)
Z_MIN_TIP, GRASP_DEPTH, PUSH_Z, PUSH_BLOCKED = 0.004, 0.030, 0.006, 0.020
HELD_W = 0.003                                                         # the fingers stopped wider: something held
REWARD = dict(step=-0.02, stop=-0.1, unreachable=-0.1, invalid=-0.05, success=1.0, other_in_box=-0.1, off_table=-0.5,
              stuck=-0.5, w_held=0.3, w_clear=0.2, gamma=0.99)
KINDS = ("grasp", "push", "place")
ROLLING = ("tomato",)                                                  # round things: rolling friction on (condim 6)


def load_bank(name):
    f = LAYOUTS / f"{name}.jsonl"
    if not f.exists():
        raise FileNotFoundError(f"{f}: run code/rl/make_layouts.py --bank {name.split('_')[0] if name.startswith('kcup') else name}")
    return [json.loads(s) for s in open(f) if s.strip()]


class _FastArm(TwinArm):
    """TwinArm with the same force estimate and box-artifact check, vectorized over the contacts (TwinArm._force called
    subtree_of, a walk up every body's parents, for every contact: 78 % of the time, 11-07 profile)."""

    def _tables(self):
        w = self.w; m = w.m
        root = np.array([w.root_of(b) for b in range(m.nbody)])
        self._grp = np.where(root != 0, root, -1 - np.arange(m.nbody))   # (subtree_of: its root's tree, or itself)
        self._gb = m.geom_bodyid.copy()
        self._tab = np.array([m.geom(g).name == "table_top" for g in range(m.ngeom)])
        nb = m.nbody; names = [m.body(b).name for b in range(nb)]
        self._box = np.zeros(nb, bool); self._box[list(self.box_b)] = True
        self._armm = np.zeros(nb, bool); self._armm[list(self.arm)] = True
        self._fing = np.zeros(nb, bool); self._fing[list(self.w.fingers)] = True
        self._Lname = np.array(["L_" in n for n in names])

    def _force(self):
        if not hasattr(self, "_grp"):
            self._tables()
        w = self.w; m, d = w.m, w.d
        n = d.ncon
        if n == 0:
            return np.zeros(3)
        g1, g2 = d.contact.geom1[:n], d.contact.geom2[:n]
        b1, b2 = self._gb[g1], self._gb[g2]
        sup1 = self._tab[g1] | self._box[b1]; sup2 = self._tab[g2] | self._box[b2]
        rb = np.r_[b2[sup1], b1[sup2]]                                  # (resting on the table or in the box)
        rb = rb[~self._box[rb] & ~self._armm[rb]]
        rests = np.isin(self._grp, self._grp[rb]) if len(rb) else np.zeros(m.nbody, bool)
        hb = np.r_[b2[self._fing[b1] & ~self._armm[b2] & ~rests[b2]], b1[self._fing[b2] & ~self._armm[b1] & ~rests[b1]]]
        A = self._fing | (np.isin(self._grp, self._grp[hb]) if len(hb) else False)
        sel = (A[b1] != A[b2]) & ~(self._armm[b1] & ~self._fing[b1]) & ~(self._armm[b2] & ~self._fing[b2])
        F, f6 = np.zeros(3), np.zeros(6)
        for i in np.nonzero(sel)[0]:
            w.mj.mj_contactForce(m, d, int(i), f6)
            fw = d.contact.frame[i].reshape(3, 3).T @ f6[:3]
            F += fw if A[b2[i]] else -fw
        return F

    def _frame(self, q):
        if not hasattr(self, "_grp"):
            self._tables()
        w = self.w; m, d = w.m, w.d
        d.ctrl[w.act] = q
        g = float(d.ctrl[w.grip])
        d.ctrl[w.grip] = g + float(np.clip(self.g_target - g, -self.g_speed * B.DT, self.g_speed * B.DT))
        n = d.ncon
        b1, b2 = self._gb[d.contact.geom1[:n]], self._gb[d.contact.geom2[:n]]
        touch = bool(((self._box[b1] != self._box[b2]) & (self._Lname[b1] | self._Lname[b2])).any())
        w.physics()
        if self.rec is not None or self.state_log is not None:
            self._record()
        if self.artifact is None and not touch and np.abs(d.qvel[self.box_dof:self.box_dof + 3]).max() > 1.0:
            self.artifact = f"t {d.time:.1f} s: the box jumped by itself (twin box-table contact instability, {np.abs(d.qvel[self.box_dof:self.box_dof + 3]).max():.1f} m/s, no arm contact)"


class _Cams:
    """The scene / wrist D415 for RealSim.observe_frame: the world's MuJoCo depth (reflections off), the fitted D415
    model on it (or the ideal one + white noise), no colour; keeps the segmentation (geom per pixel) of the last frame."""

    def __init__(self, env):
        self.env, self.gid = env, {}

    def get(self, cam, after=0.0, timeout=2.0):
        e = self.env; w = e.world; m, d, mj = w.m, w.d, w.mj
        mj.mj_forward(m, d)
        rr = e.renderer; cid = m.camera(cam).id
        Wc, Hc = B.SIZE[cam]; f = Hc / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        opt = mj.MjvOption(); opt.sitegroup[:] = 0
        rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION] = 0
        rr.enable_depth_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); dep = rr.render().copy(); rr.disable_depth_rendering()
        rr.enable_segmentation_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); seg = rr.render().copy()
        rr.disable_segmentation_rendering()
        gid = np.where((seg[..., 1] == int(mj.mjtObj.mjOBJ_GEOM)) & (seg[..., 0] >= 0), seg[..., 0], -1)
        depth = np.where(np.isfinite(dep) & (dep < 10), dep, np.inf).astype(np.float32)
        if e.d415 is not None:
            out = e.d415.apply(depth, gid, cam)
        else:
            sig = 0.08 * depth ** 2 / (f * B.CAM[cam]["baseline"])
            out = depth + e.np_random.normal(0, 1, depth.shape).astype(np.float32) * np.where(np.isfinite(sig), sig, 0)
            out = np.where(depth > B.MIN_Z, out, np.inf).astype(np.float32)
        self.gid[cam] = gid
        return dict(rgb=None, depth=out, K=(Wc, Hc, f, f, Wc / 2, Hc / 2), t=float(d.time))


class ClutterEnv(gym.Env):
    metadata = {"render_modes": ["rgb_array"]}

    def __init__(self, bank="pile_train", mode="primitive", obs="all", reward="shaped", depth="real", max_steps=None,
                 layouts=None, render_mode=None, reward_cfg=None, looks=("d415", "L_wrist")):
        assert mode in ("primitive", "waypoint") and obs in ("all", "heightmap", "privileged") and depth in ("real", "ideal")
        self.mode, self.obs_kind, self.reward_kind, self.depth_kind = mode, obs, reward, depth
        self.render_mode = render_mode
        self.looks = tuple(looks)
        self.R = dict(REWARD, **(reward_cfg or {}))
        self.max_steps = max_steps or (12 if mode == "primitive" else 60)
        self.layouts = layouts if layouts is not None else load_bank(bank)
        self.bank = bank
        models = {L.get("model", str(B.MODEL)) for L in self.layouts}
        assert len(models) == 1, f"one model per bank: {models}"
        self._build(Path(models.pop()), {k for L in self.layouts for k in L["objs"]})
        self.action_space = spaces.Box(-1.0, 1.0, (7,) if mode == "primitive" else (6,), np.float32)
        sp = {}
        if obs in ("all", "heightmap"):
            lo = np.zeros((3, NX, NY), np.float32); lo[0] = -0.05
            hi = np.ones((3, NX, NY), np.float32); hi[0] = 0.5
            sp["heightmap"] = spaces.Box(lo, hi, dtype=np.float32)
        sp["proprio"] = spaces.Box(-np.inf, np.inf, (8,), np.float32)
        if obs in ("all", "privileged"):
            sp["objects"] = spaces.Box(-np.inf, np.inf, (len(SLOTS) * 12 + 6,), np.float32)
        self.observation_space = spaces.Dict(sp)

    # ------------------------------------------------------------------ setup
    def _build(self, model, objs):
        """The world (TwinArm's physics), the arm (TwinArm), the mirror (RealSim: what the robot program sees) on the
        bank's model; TB.OBJ / CB.NAMES get the kcup scenes' extra objects while they are built."""
        model0, added = B.MODEL, []
        B.MODEL = model
        for k in objs:
            if k not in TB.OBJ and k in EXTRA_OBJ:
                TB.OBJ[k] = EXTRA_OBJ[k][0]; CB.NAMES[k] = EXTRA_OBJ[k][1]; added.append(k)
        try:
            self.rig = load_rig()
            self.world = TB.BoxSim()
            self.arm = _FastArm(self.world, self.rig, None)
            self.cams = _Cams(self)
            self.S = RealSim(self.arm, self.cams, self.rig, {}, speed=1.0, v_max=None, log_dir=None)
        finally:
            B.MODEL = model0
        self.S.task_timing = True                                       # (astra_e2e: settle where phase_end says so)
        w = self.world; m = w.m
        m.vis.global_.offwidth = max(m.vis.global_.offwidth, 640); m.vis.global_.offheight = max(m.vis.global_.offheight, 480)
        self.renderer = w.mj.Renderer(m, 480, 640)
        self.root = np.array([w.root_of(m.geom_bodyid[g]) for g in range(m.ngeom)])
        names = [m.body(b).name for b in range(m.nbody)]
        self.robot_geom = np.array([names[b].startswith(("L_", "R_")) for b in m.geom_bodyid])
        self.slot_b = {k: (m.body("obj_" + k).id if ("obj_" + k) in names else -1) for k in SLOTS}
        self.obj_keys = [k for k in w.ob if k != "box_white"]
        self.lfing, self.rfing = m.body("L_left_finger").id, m.body("L_right_finger").id
        self.arm_b = {b for b in range(m.nbody) if names[b].startswith("L_")}
        self.kin = w.mj.MjData(m)
        self.d415 = None
        for k in ROLLING:                                                 # (11-07: the lunch tomato's collision ellipsoid
            if self.slot_b.get(k, -1) >= 0:                               # has condim 4: its rolling friction unused, a
                for g in np.nonzero((self.root == self.slot_b[k]) & (m.geom_contype > 0))[0]:   # dropped tomato rolled
                    m.geom_condim[g] = 6                                  # 1.5 m off the table)

    # ------------------------------------------------------------------ reset
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        options = options or {}
        i = int(options["layout"]) if "layout" in options else int(self.np_random.integers(len(self.layouts)))
        self.L = L = self.layouts[i]; self.layout_i = i
        w, S, arm, mj = self.world, self.S, self.arm, self.world.mj
        mj.mj_resetData(w.m, w.d)
        CB.apply_layout(w, L)                                           # (qpos, cameras nominal, arm home, 0.5 s held)
        self.d415 = None
        if self.depth_kind == "real":                                   # a new calibration error, depth scale, noise
            self.d415 = d415_model.D415Real(w, np.random.default_rng(int(self.np_random.integers(1 << 31))), B.MIN_Z)
        arm.hold_q = w.d.qpos[w.qadr].copy(); arm.g_target = float(w.d.ctrl[w.grip]); arm.t_grip = -1.0
        arm.artifact = None; arm.n_traj = 0; arm.moving = False
        S.events_t0 = arm.now(); S._pend = None; S._phase_now = None; S._grip_wait = False
        st = S.sync()
        S.q_sent = np.asarray(st["q"], float).copy(); S.q_cmd = S.q_sent.copy()
        S.p_cmd = S.d.site_xpos[S.tcp].copy(); S.R_cmd = S.d.site_xmat[S.tcp].reshape(3, 3).copy()
        S.g_cmd = S.g_sent = min(B.OPEN_W, float(st["width"]) / 2); S.d.ctrl[S.grip] = S.g_cmd
        S.rec, S.timeline = [], []
        self.park = (S.p_cmd.copy(), S.R_cmd.copy())
        self.target = L["target"]
        self.is_target_geom = self.root == w.ob[self.target]
        self.k0 = {k: w.pose(k)[0].copy() for k in self.obj_keys}
        self.n_steps = self.n_stops = self.n_bad = self.stuck_n = 0
        self.held = None; self.holding = False
        self.t_wall0 = time.time(); self.t_sim0 = float(w.d.time)
        self.phi = self._phi()
        self.events = []
        obs = self._obs()
        return obs, self._info("reset")

    # ------------------------------------------------------------------ step
    def step(self, action):
        a = np.clip(np.asarray(action, np.float32).reshape(-1), -1, 1)
        self.n_steps += 1
        t0 = float(self.world.d.time)
        try:
            outcome, kind = (self._primitive(a) if self.mode == "primitive" else self._waypoint(a))
        except (T.Unreachable, RobotStop) as e:                         # (a recovery itself failed)
            outcome, kind = ("stop" if isinstance(e, RobotStop) else "unreachable"), "?"
            self.events.append(f"unrecovered: {e}"[:300])
        self.holding = self._holding_now()
        self.held = self._held_object() if self.holding else None
        obs = self._obs()
        j = self.judge()
        R = self.R
        r = R["step"]
        r += {"stop": R["stop"], "unreachable": R["unreachable"], "invalid": R["invalid"]}.get(outcome, 0.0)
        phi = self._phi()
        if self.reward_kind == "shaped":
            r += R["gamma"] * phi - self.phi
        self.phi = phi
        terminated = False
        if j["off_table"]:
            r += R["off_table"]; terminated = True
        elif j["ok"] and self.held is None:
            r += R["success"] + R["other_in_box"] * len(j["others_in_box"]); terminated = True
        elif self.stuck_n >= 3:
            r += R["stuck"]; terminated = True
        if self.reward_kind == "sparse":
            r = (R["success"] if (terminated and j["ok"]) else R["off_table"] if terminated else 0.0)
        truncated = not terminated and self.n_steps >= self.max_steps
        info = self._info(outcome, kind=kind, judge=j, dt_sim=float(self.world.d.time) - t0)
        return obs, float(r), terminated, truncated, info

    # ------------------------------------------------------------------ primitives
    def decode(self, a):
        """Primitive action vector -> (kind, x, y, yaw rad, p in [0, 1])."""
        k = KINDS[int(np.argmax(a[:3]))]
        x = X_RANGE[0] + (a[3] + 1) / 2 * (X_RANGE[1] - X_RANGE[0]); y = Y_RANGE[0] + (a[4] + 1) / 2 * (Y_RANGE[1] - Y_RANGE[0])
        return k, float(x), float(y), float(a[5]) * math.pi / 2, float(a[6] + 1) / 2

    @staticmethod
    def encode(kind, x, y, yaw, p):
        """The inverse of decode (scripted policies, demonstrations)."""
        a = np.full(7, -1.0, np.float32); a[KINDS.index(kind)] = 1.0
        a[3] = (x - X_RANGE[0]) / (X_RANGE[1] - X_RANGE[0]) * 2 - 1; a[4] = (y - Y_RANGE[0]) / (Y_RANGE[1] - Y_RANGE[0]) * 2 - 1
        yaw = (yaw + math.pi / 2) % math.pi - math.pi / 2
        a[5] = yaw / (math.pi / 2); a[6] = p * 2 - 1
        return np.clip(a, -1, 1)

    def _primitive(self, a):
        kind, x, y, yaw, p = self.decode(a)
        self.events.append(f"{self.n_steps}: {kind} ({x * 1000:.0f}, {y * 1000:.0f}) yaw {math.degrees(yaw):.0f} p {p:.2f}")
        if (kind == "place") != self.holding:
            return "invalid", kind
        try:
            self.S.resync()
            if kind == "grasp":
                out = self._grasp(x, y, yaw, 0.030 + 0.050 * p)
            elif kind == "push":
                out = self._push(x, y, yaw, 0.03 + 0.09 * p)
            else:
                out = self._place(x, y, yaw)
            self._park()
            self.stuck_n = 0
        except T.Unreachable as e:
            self.events.append(f"  unreachable: {e}"[:300]); out = "unreachable"
            self._recover()
        except RobotStop as e:
            self.events.append(f"  stop: {e}"[:300]); out = "stop"; self.n_stops += 1
            self._recover()
        self.events.append(f"  -> {out}")
        return out, kind

    def _grasp(self, x, y, yaw, width):
        top = self._top_at(x, y, 0.012)
        z = max(Z_MIN_TIP, top - GRASP_DEPTH)
        R = self._hand(x, y, z, yaw)
        if R is None:
            return "unreachable"
        self._over(x, y, R, top + 0.05)
        self._gripper(width)
        c = self._guarded_tip((x, y, max(z, top + 0.015)), R, V_APPROACH) if top + 0.015 > z + 0.005 else None
        if c is None:
            c = self._guarded_tip((x, y, z), R, V_GUARD)
        w = self._gripper(0.0)
        tip = self._tip()
        self._line((tip[0], tip[1], tip[2] + 0.03), R, V_LOW, settle=False)
        self._line((tip[0], tip[1], max(tip[2] + 0.10, top + 0.08)), R, V_FREE, settle=False)
        self.holding = self._holding_now()
        if not self.holding:
            self._gripper(B.OPEN_W * 2)
            return "empty"
        return "contact" if c else "ok"

    def _push(self, x, y, yaw, dist):
        u = np.array([math.cos(yaw), math.sin(yaw)])
        x1, y1 = np.array([x, y]) + u * dist
        R = self._hand(x, y, PUSH_Z, yaw + math.pi / 2, also=[(x1, y1, PUSH_Z)])   # (the closed fingers across the push)
        if R is None:
            return "unreachable"
        top = self._top_at(x, y, 0.015)
        self._gripper(0.0)
        self._over(x, y, R, top + 0.05)
        c = self._guarded_tip((x, y, PUSH_Z), R, V_GUARD)
        if c and self._tip()[2] > PUSH_BLOCKED:
            self._line(self._tip() + [0, 0, 0.05], R, V_FREE, settle=False)
            self._gripper(B.OPEN_W * 2)
            return "blocked"
        self._line((x1, y1, PUSH_Z), R, V_LOW, settle=False)
        self._line(self._tip() + [0, 0, 0.06], R, V_FREE, settle=False)
        self._gripper(B.OPEN_W * 2)
        return "ok"

    def _place(self, x, y, yaw):
        S = self.S
        R = self._hand(x, y, 0.05, yaw)
        if R is None:
            return "unreachable"
        self._over(x, y, R, None, held=True)
        z1 = max(self._top_at(x, y, 0.04) + 0.13, 0.05)              # (what it holds hangs <= ~11 cm under the tips)
        c = self._guarded_tip((x, y, z1), R, V_APPROACH) if z1 < Z_TRANSIT_HELD - 0.01 else None
        if c is None:
            c = self._guarded_tip((x, y, 0.0), R, V_GUARD)
        S.flush()
        w = S.finger_width()
        S.move(S.p_cmd + [0, 0, 0.001], S.R_cmd, min(B.OPEN_W, w / 2 + 0.010), 0.4, phase="release"); S.flush()
        S.hold(0.2)
        self._line(self._tip() + [0, 0, 0.08], R, V_FREE, settle=False)
        self._gripper(B.OPEN_W * 2)
        self.holding = False
        return "ok" if c else "no contact"

    def _waypoint(self, a):
        S = self.S
        x = X_RANGE[0] + (a[0] + 1) / 2 * (X_RANGE[1] - X_RANGE[0]); y = Y_RANGE[0] + (a[1] + 1) / 2 * (Y_RANGE[1] - Y_RANGE[0])
        z = (a[2] + 1) / 2 * 0.30; yaw = float(a[3]) * math.pi / 2; width = (a[4] + 1) / 2 * 0.08; guarded = a[5] > 0
        self.events.append(f"{self.n_steps}: move ({x * 1000:.0f}, {y * 1000:.0f}, {z * 1000:.0f}) yaw {math.degrees(yaw):.0f}"
                           f" {'guarded' if guarded else 'free'} width {width * 1000:.0f}")
        out = "ok"
        try:
            S.resync()
            R = self._hand(x, y, z, yaw)
            if R is None:
                out = "unreachable"
            else:
                rot = float(np.linalg.norm(T.rotvec(S.R_cmd.T @ R)))
                if rot > math.radians(2):
                    S.move(S.p_cmd, R, S.g_cmd, max(1.0, rot * 1.5), phase="rl: turn the hand"); S.flush()
                if guarded:
                    out = "contact" if self._guarded_tip((x, y, z), R, V_GUARD) else "ok"
                else:
                    low = min(self._tip()[2], z) < 0.06
                    self._line((x, y, z), R, V_LOW if low else V_FREE)
                self._gripper(0.0 if width < 0.003 else width)
                self.holding = self._holding_now()
            self.stuck_n = 0
        except T.Unreachable as e:
            self.events.append(f"  unreachable: {e}"[:300]); out = "unreachable"; self._recover(park=False)
        except RobotStop as e:
            self.events.append(f"  stop: {e}"[:300]); out = "stop"; self.n_stops += 1; self._recover(park=False)
        return out, "move"

    # ------------------------------------------------------------------ motion helpers (RealSim: the robot's path)
    def _tip(self):
        S = self.S
        return S.p_cmd + S.R_cmd[:, 2] * TIP

    def _hand(self, x, y, z, yaw, also=()):
        """The hand's rotation for the fingers' closing direction yaw or yaw + 180 (the same grasp), whichever the arm
        reaches (RealSim.ik: the server's limits) at (x, y, z) and at the points `also`, with joint 7 farthest from its
        limits (11-06: Astra's wrist at joint 7's margin locked every later trajectory out). None: neither."""
        S = self.S; best = None
        for yw in (yaw, yaw + math.pi):
            R = B.down(yw); ok = True; marg = 1e9
            for p in [(x, y, z)] + list(also) + [(x, y, max(z, 0.0) + 0.10)]:
                q, e = S.ik(np.asarray(p, float) - R[:, 2] * TIP, R, S.q_cmd)
                if e > 0.002:
                    q, e = S.ik(np.asarray(p, float) - R[:, 2] * TIP, R, S.q_home)
                if e > 0.002:
                    ok = False; break
                marg = min(marg, float(min(q[6] - S.lo[6], S.hi[6] - q[6])))
            if ok and (best is None or marg > best[0]):
                best = (marg, R)
        return None if best is None else best[1]

    def _line(self, tip, R, v, settle=True, phase="rl: move"):
        S = self.S
        S.flush()
        target = np.asarray(tip, float) - R[:, 2] * TIP
        dist = float(np.linalg.norm(target - S.p_cmd))
        S.move(target, R, S.g_cmd, max(0.5, dist / v), phase=phase)
        if settle and S._pend is not None:
            S._pend["phase_end"] = "descend"                              # (SETTLE_PHASES: settled at its end)
        S.flush()

    def _guarded_tip(self, tip, R, v):
        """Straight to the fingertip point tip at v, stopping at the first contact (the force estimate up > 4 N; the
        server's dz test is off: the compliant arm's lag reads as a contact at 4 cm/s, astra_e2e.guarded). The hand
        turned first. -> the contact dict or None."""
        S = self.S
        S.flush()
        rot = float(np.linalg.norm(T.rotvec(S.R_cmd.T @ R)))
        if rot > math.radians(2):
            S.move(S.p_cmd, R, S.g_cmd, max(1.0, rot * 1.5), phase="rl: turn the hand"); S.flush()
        target = np.asarray(tip, float) - R[:, 2] * TIP
        S._set_phase("rl: guarded")
        p0 = S.p_cmd.copy()
        n = max(int(round(np.linalg.norm(target - p0) / v / B.DT)), 1)
        Q = [S.q_cmd.copy()]
        for i in range(1, n + 1):
            q, e = S.ik(p0 + (target - p0) * i / n, R, Q[-1])
            if e > 0.002 or np.abs(q - Q[-1]).max() > 0.15:
                raise T.Unreachable("guarded line: no straight line there within the arm's limits")
            Q.append(q)
        S._queue(Q[1:], S.g_cmd, B.DT * S.speed, kind="guarded")
        r = S.flush(guard=dict(dz=1.0, df=S.rig["contact"]["df"]))
        S.q_cmd = S.q_sent.copy(); S.p_cmd = S.d.site_xpos[S.tcp].copy(); S.R_cmd = R
        return (r or {}).get("contact")

    def _gripper(self, width):
        """0 = close with force (a grasp), else that opening (m). -> the measured opening."""
        S = self.S
        g = 0.0 if width < 0.0005 else min(width, 2 * B.OPEN_W) / 2
        S.flush()
        if abs(g - S.g_sent) > 1e-6 or g == 0.0:
            S.move(S.p_cmd, S.R_cmd, g, 0.3, phase="close the gripper" if g == 0 else "rl: gripper"); S.flush()
        return S.finger_width()

    def _over(self, x, y, R, z_end, held=False):
        """Up to the transit height where the hand is, across to (x, y) turning to R, down to z_end (fingertips; None:
        stay at the transit height). The way down guarded (things taller than the heightmap's local top beside it)."""
        zt = Z_TRANSIT_HELD if held else Z_TRANSIT
        tip = self._tip()
        if tip[2] < zt - 0.01:
            self._line((tip[0], tip[1], zt), self.S.R_cmd, V_FREE, settle=False)
        self._line((x, y, zt), R, V_FREE, settle=z_end is None)
        if z_end is not None and z_end < zt - 0.005:
            self._guarded_tip((x, y, z_end), R, V_APPROACH)

    def _park(self):
        tip = self._tip()
        if tip[2] < Z_TRANSIT - 0.01:
            self._line((tip[0], tip[1], Z_TRANSIT_HELD if self.holding else Z_TRANSIT), self.S.R_cmd, V_FREE, settle=False)
        p, R = self.park
        S = self.S
        S.flush()
        S.move(p, R, S.g_cmd, max(0.5, float(np.linalg.norm(p - S.p_cmd)) / V_FREE), phase="rl: park"); S.flush()

    def _recover(self, park=True):
        """After a stop or a move the arm could not do: the command = where the arm is, up 5 cm, the park pose."""
        S = self.S
        for _ in range(2):
            try:
                S.resync()
                tip = self._tip()
                if tip[2] < Z_TRANSIT - 0.02:
                    S.move(S.p_cmd + [0, 0, 0.05], S.R_cmd, S.g_cmd, 1.0, phase="rl: back off"); S.flush()
                if park:
                    if not self.holding:
                        self._gripper(B.OPEN_W * 2)
                    self._park()
                self.stuck_n = 0
                return True
            except (T.Unreachable, RobotStop) as e:
                self.events.append(f"  recovery: {e}"[:300])
        self.stuck_n += 1
        try:
            S.resync()
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------ state, observation
    def _obs(self):
        o = {}
        if self.obs_kind in ("all", "heightmap"):
            o["heightmap"] = self.heightmap()
        o["proprio"] = self._proprio()
        if self.obs_kind in ("all", "privileged"):
            o["objects"] = self._objects()
        return o

    def heightmap(self):
        """The scene D415 and the wrist D415 (from the park pose: 0.47 m over the table looking down, x -0.59..-0.23,
        y -0.04..0.37, i.e. the piles; ~1-2 mm there vs the scene camera's 5-8 mm at a glancing 1-1.3 m) projected into
        the grid, the wrist camera's cells first."""
        maps = [self._project(cam) for cam in self.looks]
        h, seen, tg = maps[0]
        for h2, s2, t2 in maps[1:]:
            h = np.where(s2, h2, h); seen = seen | s2; tg = np.maximum(tg, t2)
        hm = np.stack([np.where(seen, np.clip(h, -0.02, 0.45), 0.0), seen.astype(np.float32), tg]).reshape(3, NX, NY)
        self.last_hm = hm.astype(np.float32)
        return self.last_hm

    def _project(self, cam):
        fr = self.S.observe_frame(cam)
        gid = self.cams.gid[cam]
        z = fr["depth"]; ok = np.isfinite(z) & ~((gid >= 0) & self.robot_geom[np.clip(gid, 0, None)])
        v, u = np.nonzero(ok); zz = z[v, u]
        W_, H_, fx, fy, cx, cy = fr["K"]
        P = fr["eye"] + np.stack([(u - cx) / fx * zz, (v - cy) / fy * zz, zz], 1) @ fr["R"].T
        i = np.floor((P[:, 0] - X_RANGE[0]) / RES).astype(np.int64); j = np.floor((P[:, 1] - Y_RANGE[0]) / RES).astype(np.int64)
        inb = (i >= 0) & (i < NX) & (j >= 0) & (j < NY) & (P[:, 2] > -0.03) & (P[:, 2] < 0.45)
        flat = i[inb] * NY + j[inb]
        h = np.full(NX * NY, -np.inf, np.float32); np.maximum.at(h, flat, P[inb, 2].astype(np.float32))
        tg = np.zeros(NX * NY, np.float32)
        g = gid[v, u][inb]
        tg[flat[(g >= 0) & self.is_target_geom[np.clip(g, 0, None)]]] = 1.0
        return h, np.isfinite(h), tg

    def _top_at(self, x, y, r):
        """The heightmap's highest seen point within r of (x, y) (the table, 0, if none)."""
        hm = getattr(self, "last_hm", None)
        if hm is None:
            hm = self.heightmap()
        i0, j0 = (x - X_RANGE[0]) / RES - 0.5, (y - Y_RANGE[0]) / RES - 0.5
        n = int(math.ceil(r / RES))
        ii, jj = np.mgrid[-n:n + 1, -n:n + 1]
        sel = ii ** 2 + jj ** 2 <= (r / RES) ** 2
        ii = np.clip(np.round(i0 + ii[sel]).astype(int), 0, NX - 1); jj = np.clip(np.round(j0 + jj[sel]).astype(int), 0, NY - 1)
        hv = hm[0][ii, jj][hm[1][ii, jj] > 0]
        return float(max(0.0, np.percentile(hv, 90))) if len(hv) else 0.0

    def _proprio(self):
        S = self.S
        S.flush(); st = S.sync()
        R = S.d.site_xmat[S.tcp].reshape(3, 3); tip = S.d.site_xpos[S.tcp] + R[:, 2] * TIP
        yaw = math.atan2(R[1, 1], R[0, 1])
        return np.array([*tip, math.cos(2 * yaw), math.sin(2 * yaw), float(st["width"] or 0.0), float(self.holding),
                         self.n_steps / self.max_steps], np.float32)

    def _objects(self):
        w = self.world; d = w.d
        v = np.zeros((len(SLOTS), 12), np.float32)
        for s, k in enumerate(SLOTS):
            b = self.slot_b[k]
            if b < 0 or k not in self.L["objs"]:
                continue
            R = d.xmat[b].reshape(3, 3)
            v[s] = [1.0, float(k == self.target), float(k == self.held), *d.xpos[b], *R[:, 0], *R[:, 1]]
        bp, bR = w.pose("box_white")
        box = [*bp, bR[0, 0], bR[1, 0], float(d.qpos[w.lid_q])]
        return np.r_[v.reshape(-1), box].astype(np.float32)

    def _holding_now(self):
        """What the robot itself knows: the last gripper command a grasp and the fingers stopped open > 3 mm."""
        return bool(self.S.g_sent < 1e-6 and self.S.finger_width() > HELD_W)

    def _held_object(self):
        """The object both fingers touch (the twin's contacts), if the fingers are not closed on nothing."""
        w = self.world; m, d = w.m, w.d
        if w.finger_width() < HELD_W:
            return None
        L_, R_ = set(), set()
        for c in d.contact[:d.ncon]:
            for a_, b_ in ((c.geom1, c.geom2), (c.geom2, c.geom1)):
                ba = m.geom_bodyid[a_]
                if ba == self.lfing or ba == self.rfing:
                    r = self.root[b_]
                    if r not in self.arm_b and r != 0:
                        (L_ if ba == self.lfing else R_).add(r)
        both = L_ & R_
        for k in self.obj_keys:
            if w.ob[k] in both:
                return k
        return None

    def judge(self):
        """clutter_bench / astra_e2e's judge on the twin's true state."""
        w, L = self.world, self.L
        tg = self.target
        inside = TB.inside_box(w, tg)
        off = [k for k in self.obj_keys + ["box_white"] if w.pose(k)[0][2] < -0.01]
        in_box = [k for k in L["objs"] if k != tg and TB.inside_box(w, k)]
        moved = {k: round(float(np.linalg.norm(w.pose(k)[0][:2] - self.k0[k][:2])) * 1000) for k in L["objs"] if k != tg}
        return dict(ok=bool(inside and not off), target_in_box=inside, off_table=off, others_in_box=in_box, others_moved_mm=moved)

    # ------------------------------------------------------------------ shaping: how free the target is
    def kin_grasps(self, k=None, yaws=12, widths=(0.080,), dz=(0.0, 0.012), margin=0.003):
        """The grasps of object k (default: the target) the arm could make now: closing directions every 180/yaws deg x
        fingertip heights (3 cm under its top, >= 4 mm over the table, + dz) x openings, the arm by IK (RealSim.ik: the
        server's limits), its collision geometry (wedge fingers, hand, wrist camera) touching nothing (not even k: the
        fingers do not cut into it), with a margin (the compliant arm's hand is 2-4 mm off its straight line down: the
        joints trail by different lags). (kcup_scene.grasp_check, coarser.) -> list of (yaw, z_tip, width), n tried."""
        w, S = self.world, self.S
        m, d, kd = w.m, w.d, self.kin
        k = k or self.target
        X = CB.obj_extent(w, k)
        free, n = [], 0
        arm_g = np.array([m.geom_bodyid[g] in self.arm_b for g in range(m.ngeom)])
        margin0 = m.geom_margin.copy(); m.geom_margin[arm_g] = margin
        try:
            return self._kin_grasps(k, X, yaws, widths, dz, margin, free, n)
        finally:
            m.geom_margin[:] = margin0

    def _kin_grasps(self, k, X, yaws, widths, dz, margin, free, n):
        w, S = self.world, self.S
        m, d, kd = w.m, w.d, self.kin
        for iy in range(yaws):
            yaw = math.pi * iy / yaws
            R = B.down(yaw)
            for z0 in dz:
                zt = max(Z_MIN_TIP, X[2] - GRASP_DEPTH) + z0
                q, e = S.ik(np.r_[X[0], X[1], zt] - R[:, 2] * TIP, R, S.q_home)
                if e > 0.002:
                    q, e = S.ik(np.r_[X[0], X[1], zt] - R[:, 2] * TIP, B.down(yaw + math.pi), S.q_home)
                if e > 0.002:
                    n += len(widths); continue
                for wd in widths:
                    n += 1
                    kd.qpos[:] = d.qpos; kd.qpos[w.qadr] = q; kd.qpos[w.fing] = wd / 2
                    w.mj.mj_kinematics(m, kd); w.mj.mj_collision(m, kd)
                    hit = False
                    for c in kd.contact[:kd.ncon]:
                        if c.dist > margin - 0.0003:
                            continue
                        a1, a2 = m.geom_bodyid[c.geom1] in self.arm_b, m.geom_bodyid[c.geom2] in self.arm_b
                        if a1 != a2:
                            hit = True; break
                    if not hit:
                        free.append((yaw, zt, wd))
        return free, n

    def _phi(self):
        if self.reward_kind != "shaped":
            return 0.0
        held = float(getattr(self, "held", None) == self.target)
        clear = 1.0 if held else (lambda f: len(f[0]) / max(f[1], 1))(self.kin_grasps())
        return self.R["w_held"] * held + self.R["w_clear"] * min(1.0, clear * 4)

    def _info(self, outcome, kind=None, judge=None, dt_sim=0.0):
        return dict(outcome=outcome, kind=kind, held=getattr(self, "held", None), target=self.target, layout=self.layout_i,
                    bank=self.bank, judge=judge or self.judge(), stops=self.n_stops, steps=self.n_steps,
                    success=bool(judge and judge["ok"] and getattr(self, "held", None) is None),
                    sim_s=round(float(self.world.d.time) - self.t_sim0, 2), wall_s=round(time.time() - self.t_wall0, 2),
                    dt_sim=round(dt_sim, 2), action_mask=np.array([not self.holding, not self.holding, self.holding]))

    # ------------------------------------------------------------------ the scripted expert (rl/oracle.py)
    def expert_action(self):
        """rl/oracle.py's action for the current state (privileged; callable inside vector-env workers:
        envs.call("expert_action"), e.g. demonstrations or DAgger labels). Primitive mode only."""
        import oracle
        if self.n_steps == 0 or not hasattr(self, "_oracle_rng"):
            self._oracle_rng = np.random.default_rng(int(self.np_random.integers(1 << 31)))
        return oracle.act(self, self._oracle_rng)[0]

    # ------------------------------------------------------------------ viewing
    def render(self):
        """A free camera over the pile and the box (TWIN_REC_VIEW=close's view)."""
        w = self.world; mj = w.mj
        pt = w.pose(self.target)[0]; pb = w.pose("box_white")[0]
        c = mj.MjvCamera(); c.type = mj.mjtCamera.mjCAMERA_FREE
        c.lookat[:] = np.r_[0.65 * pt[:2] + 0.35 * pb[:2], 0.04]; c.distance = 0.5 + 0.7 * float(np.linalg.norm(pt[:2] - pb[:2]))
        c.azimuth = 205.0; c.elevation = -40.0
        opt = mj.MjvOption(); opt.sitegroup[:] = 0
        self.renderer.update_scene(w.d, camera=c, scene_option=opt)
        return self.renderer.render().copy()

    def close(self):
        """Free the renderer's EGL context now (MuJoCo 2.3.7's Renderer has no close(): left to __del__ at exit, after
        the EGL display is gone, it prints an EGL_NOT_INITIALIZED traceback per process)."""
        r = getattr(self, "renderer", None)
        if r is not None:
            for f in (lambda: r._mjr_context.free(), lambda: r._gl_context.free()):
                try:
                    f()
                except Exception:
                    pass
            self.renderer = None
