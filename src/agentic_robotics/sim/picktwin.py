"""Ported 2026-11-18 from panda_pick_rgbd pickrgbd/sim.py (commit 75c201f, unchanged there): only the imports differ.
PickTwin = the world (task_box.BoxSim: true state) + the robot path (clutter_env._FastArm = TwinArm, compliant) + the
RealSim mirror the reset uses to place the arm.

PickTwin: the world + the robot path (twin.py) for the picking task, and the privileged helpers that the layout
generator, the reward, the evaluator and the scripted demonstrator use. The actor never sees anything from here
except through obs.py (rendered images, the robot's own state).

World = task_box.BoxSim (the truth: objects, contacts). Arm = clutter_env._FastArm (TwinArm, vectorized force estimate).
S = RealSim (the mirror the robot program plans in: no objects, the server's limits). Objects not in the layout are
parked on the floor far behind the scene camera.
"""
from __future__ import annotations

import math

import numpy as np

from .twin import B, TB, CB, RealSim, load_rig, TIP, OPEN_W
import clutter_env as CE  # noqa: E402  (vendored rl/clutter_env.py: _FastArm)

CAM_W, CAM_H = B.SIZE["d415"]

PICKABLE = ["tape_red", "tape_yellow", "tape_white", "hole_block", "cable", "screwdriver", "glue", "sticky_pad"]
PARK_Z = -0.865 + 0.0005                         # floor
Z_MIN_TIP0 = CE.Z_MIN_TIP                        # (the grasp check's lowest fingertip: over the twin's table top, z = 0)


def world_rig(rig, S_):
    """The robot's limits in an object set's world (sim/scene.py OBJECT_SETS): simple_sim's table is lower, so the
    fingertips / links are kept over ITS top (table_z), and the TCP box's floor goes down with it."""
    if S_.get("table_z") is not None:
        sf = rig["safety"]
        sf["table_z"] = float(S_["table_z"]); sf["tcp_lo"] = [*sf["tcp_lo"][:2], float(S_["tcp_lo_z"])]
    return rig


class _NoCams:
    """RealSim wants a camera source; observations are rendered from the world here instead (render())."""

    def get(self, cam, after=0.0, timeout=2.0):
        raise RuntimeError("PickTwin renders cameras itself")

    def info(self):
        return {}


class PickTwin:
    def __init__(self, render=True, world_cls=None):
        from .scene import active_set
        S_ = active_set()
        self.rig = world_rig(load_rig(), S_)
        self.table_z = float(S_.get("table_z") or 0.0)
        CE.Z_MIN_TIP = Z_MIN_TIP0 + self.table_z                         # (kin_grasps: over this world's table)
        self.world = w = (world_cls or TB.BoxSim)()
        self.arm = CE._FastArm(w, self.rig, None)
        self.S = RealSim(self.arm, _NoCams(), self.rig, {}, speed=1.0, v_max=None, log_dir=None)
        self.S.task_timing = True                       # (as clutter_env: settle only where the phase says so)
        m, d, mj = w.m, w.d, w.mj
        self.m, self.d, self.mj = m, d, mj
        self.key0 = m.key("twin").id
        self.kin = mj.MjData(m)                         # (kin_grasps' scratch data, clutter_env's name)
        names = [m.body(b).name for b in range(m.nbody)]
        self.arm_b = {b for b in range(m.nbody) if names[b].startswith("L_")}
        self.root = np.array([w.root_of(m.geom_bodyid[g]) for g in range(m.ngeom)])
        self.robot_geom = np.array([names[b].startswith(("L_", "R_")) for b in m.geom_bodyid])
        self.lfing, self.rfing = m.body("L_left_finger").id, m.body("L_right_finger").id
        self.obj_keys = [k for k in w.ob]
        self.rest_z0 = {k: float(m.key_qpos[self.key0][w.oq[k] + 2]) for k in self.obj_keys}
        self.collide = m.geom_contype.astype(bool) | m.geom_conaffinity.astype(bool)
        self.renderer = None
        if render:
            m.vis.global_.offwidth = max(m.vis.global_.offwidth, CAM_W); m.vis.global_.offheight = max(m.vis.global_.offheight, CAM_H)
            self.renderer = mj.Renderer(m, CAM_H, CAM_W)
        self.light0 = (m.light_diffuse.copy(), m.light_ambient.copy(), m.light_specular.copy(),
                       np.array(m.vis.headlight.diffuse), np.array(m.vis.headlight.ambient))
        self.target = None
        self.L = {"objs": []}
        self._fp = {}

    # ------------------------------------------------------------------ the world's state
    def reset_world(self, active=()):
        """Keyframe state, every object not in `active` parked out of view (no collisions, its weight cancelled: it
        stays put; 11-15: 392 contacts of parked things on the floor tripled the physics cost), camera errors and
        lights back to nominal."""
        m, d, mj, w = self.m, self.d, self.mj, self.world
        mj.mj_resetDataKeyframe(m, d, self.key0)
        if not hasattr(self, "_ctype0"):
            self._ctype0, self._caff0 = m.geom_contype.copy(), m.geom_conaffinity.copy()
            self._odofs = {k: np.array([j for j in range(m.nv) if w.root_of(int(m.dof_bodyid[j])) == w.ob[k]], int)
                           for k in self.obj_keys}
            self._ogeoms = {k: np.nonzero(self.root == w.ob[k])[0] for k in self.obj_keys}
            self._damp0 = m.dof_damping.copy()
            jroot = lambda j: w.root_of(int(m.jnt_bodyid[j]))
            self._sprung = [k for k in self.obj_keys if any(m.jnt_stiffness[j] > 0 and jroot(j) == w.ob[k] for j in range(m.njnt))]
        m.geom_contype[:] = self._ctype0; m.geom_conaffinity[:] = self._caff0
        m.dof_damping[:] = self._damp0
        d.qfrc_applied[:] = 0
        self.parked = [k for k in self.obj_keys if k not in set(active)]
        for k in self.parked:
            m.geom_contype[self._ogeoms[k]] = 0; m.geom_conaffinity[self._ogeoms[k]] = 0
            if k in self._sprung:       # (19-11: a constant force can't hold a spring-loaded object (the kettle's lid
                m.dof_damping[self._odofs[k]] = 1e4     # against its latch): it crept, drifted off, MuJoCo reset the world)
        for j, k in enumerate(self.obj_keys):
            a = w.oq[k]
            d.qpos[a:a + 3] = [3.0 + 0.35 * (j % 6), 2.0 + 0.35 * (j // 6), PARK_Z + self.rest_z0[k] - 0.0005]
            d.qpos[a + 3:a + 7] = [1, 0, 0, 0]
        if hasattr(w, "lid_q"):
            d.qpos[w.lid_q] = 0.0
        d.qvel[:] = 0
        for c in ("d415", "L_wrist"):
            w.set_camera_error(c, [0, 0, 0], [0, 0, 0])
        m.light_diffuse[:], m.light_ambient[:], m.light_specular[:] = self.light0[0], self.light0[1], self.light0[2]
        m.vis.headlight.diffuse[:] = self.light0[3]; m.vis.headlight.ambient[:] = self.light0[4]
        d.ctrl[w.act] = w.q_home; d.ctrl[w.grip] = OPEN_W
        mj.mj_forward(m, d)
        for k in self.parked:                            # (at rest: qfrc_bias = its weight)
            d.qfrc_applied[self._odofs[k]] = d.qfrc_bias[self._odofs[k]]
        w.rec.clear()

    def put(self, k, x, y, yaw, qpos7=None):
        w = self.world; a = w.oq[k]
        if qpos7 is not None:
            self.d.qpos[a:a + 7] = qpos7
        else:
            self.d.qpos[a:a + 3] = [x, y, self.rest_z0[k]]
            self.d.qpos[a + 3:a + 7] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
        dof = self.m.jnt_dofadr[self.m.body_jntadr[w.ob[k]]]
        self.d.qvel[dof:dof + 6] = 0

    def hand_R(self, yaw):
        """The hand pointing straight down, its fingers closing along the table direction yaw (B.down)."""
        return B.down(yaw)

    def teleport_arm(self, tip, yaw, q=None):
        """The arm put (not moved) at the fingertip point tip, straight down at yaw, gripper open; the controller's
        state (TwinArm hold, RealSim command) set to it. -> joints, or None if the arm cannot be there."""
        w, S = self.world, self.S
        R = self.hand_R(yaw)
        if q is None:
            q, e = S.ik(np.asarray(tip, float) - R[:, 2] * TIP, R, S.q_home)
            if e > 0.002:
                return None
        q = np.asarray(q, float)
        d = self.d
        d.qpos[w.qadr] = q; d.qvel[w.dadr] = 0
        d.qpos[w.fing] = OPEN_W; d.ctrl[w.act] = q; d.ctrl[w.grip] = OPEN_W
        self.mj.mj_forward(self.m, d)
        self.sync_controller()
        return q

    def sync_controller(self):
        """TwinArm and RealSim state = the world's arm now (as clutter_env.reset after apply_layout)."""
        w, S, arm = self.world, self.S, self.arm
        arm.hold_q = w.d.qpos[w.qadr].copy(); arm.g_target = float(w.d.ctrl[w.grip]); arm.t_grip = -1.0
        arm.artifact = None; arm.n_traj = 0; arm.moving = False
        S.events_t0 = arm.now(); S._pend = None; S._phase_now = None; S._grip_wait = False
        st = S.sync()
        S.q_sent = np.asarray(st["q"], float).copy(); S.q_cmd = S.q_sent.copy()
        S.p_cmd = S.d.site_xpos[S.tcp].copy(); S.R_cmd = S.d.site_xmat[S.tcp].reshape(3, 3).copy()
        S.g_cmd = S.g_sent = min(OPEN_W, float(st["width"]) / 2); S.d.ctrl[S.grip] = S.g_cmd
        S.rec, S.timeline = [], []

    def settle(self, T_):
        """Physics with the arm held where it is (TwinArm frames: the gripper and the arm as the controller holds them)."""
        self.arm.idle(T_)

    def use_cameras(self, mode="twin"):
        """twin: the twin's nominal camera mounts (training). calibrated: the real rig's calibrated poses
        (calib.json: scene_cam T_world_cam, wrist_cam T_hand_cam, OpenCV axes) -- for checking a policy against the
        real placement; per-episode calibration errors are then drawn around these."""
        m, mj, w = self.m, self.mj, self.world
        if not hasattr(w, "_cam0"):
            w._cam0 = {}
        if not hasattr(self, "_cam_twin"):
            self._cam_twin = {c: (m.cam_pos[m.camera(c).id].copy(), m.cam_quat[m.camera(c).id].copy()) for c in ("d415", "L_wrist")}
        if mode == "twin":
            poses = self._cam_twin
        else:
            import json
            from .twin import PT_CODE as VENDOR
            calib = json.load(open(VENDOR / "real/calib.json"))
            mj.mj_forward(m, self.d)
            poses = {}
            for cam, key, T_ in (("d415", "scene_cam", "T_world_cam"), ("L_wrist", "wrist_cam", "T_hand_cam")):
                T = np.asarray(calib[key][T_], float)
                b = m.cam_bodyid[m.camera(cam).id]
                Rb, pb = self.d.xmat[b].reshape(3, 3), self.d.xpos[b]
                if cam == "d415":                       # (world pose -> the camera body's frame)
                    pos, Rrel = Rb.T @ (T[:3, 3] - pb), Rb.T @ T[:3, :3] @ np.diag([1.0, -1.0, -1.0])
                else:                                   # (already in the hand body's frame)
                    pos, Rrel = T[:3, 3], T[:3, :3] @ np.diag([1.0, -1.0, -1.0])
                q = np.zeros(4); mj.mju_mat2Quat(q, Rrel.flatten())
                poses[cam] = (pos, q)
        for cam, (pos, q) in poses.items():
            cid = m.camera(cam).id
            m.cam_pos[cid] = pos; m.cam_quat[cid] = q
            w._cam0[cam] = (np.array(pos, float).copy(), np.array(q, float).copy())
        # the scene camera's own box and pole stay at the twin's mount: with the camera 5 cm behind it (calibrated)
        # they would fill the top of its view (the real camera never sees its own housing): hidden then
        if not hasattr(self, "_mount_group0"):
            self._mount_g = [m.geom("d415_body").id, m.geom("d415_mount").id]
            self._mount_group0 = m.geom_group[self._mount_g].copy()
        m.geom_group[self._mount_g] = self._mount_group0 if mode == "twin" else 5
        self.camera_mode = mode

    # ------------------------------------------------------------------ geometry
    def footprint(self, k):
        """Object k's collision outline on the table in its body frame: convex hull (counter-clockwise) of its geoms'
        box corners, and its height. (Body frame = resting pose; cached.)"""
        if k in self._fp:
            return self._fp[k]
        m, mj = self.m, self.mj
        b = self.world.ob[k]
        pts = []
        for g in np.nonzero((self.root == b) & self.collide)[0]:
            if m.geom_bodyid[g] != b:
                continue                                 # (child bodies: the white box's lid only; not pickable)
            Rg = np.zeros(9); mj.mju_quat2Mat(Rg, m.geom_quat[g]); Rg = Rg.reshape(3, 3)
            ctr, half = m.geom_aabb[g].reshape(2, 3)
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        pts.append(m.geom_pos[g] + Rg @ (ctr + half * np.array([sx, sy, sz])))
        P = np.array(pts)
        hull = _hull(P[:, :2])
        self._fp[k] = (hull, float(P[:, 2].max()))
        return self._fp[k]

    def outline(self, k, x, y, yaw):
        hull, _ = self.footprint(k)
        c, s = math.cos(yaw), math.sin(yaw)
        return hull @ np.array([[c, s], [-s, c]]) + np.array([x, y])

    def outline_now(self, k):
        p, R = self.world.pose(k)
        return self.outline(k, p[0], p[1], math.atan2(R[1, 0], R[0, 0]))

    # ------------------------------------------------------------------ contacts, the judge's facts (privileged)
    def finger_contacts(self):
        """{root body: (left finger touches, right finger touches)} from the world's contacts."""
        m, d = self.m, self.d
        out = {}
        for c in d.contact[:d.ncon]:
            for a_, b_ in ((c.geom1, c.geom2), (c.geom2, c.geom1)):
                ba = m.geom_bodyid[a_]
                if ba == self.lfing or ba == self.rfing:
                    r = int(self.root[b_])
                    if r in self.arm_b or r == 0:
                        continue
                    lf, rf = out.get(r, (False, False))
                    out[r] = (lf or ba == self.lfing, rf or ba == self.rfing)
        return out

    def held_by_both(self, k, fc=None):
        fc = self.finger_contacts() if fc is None else fc
        lf, rf = fc.get(self.world.ob[k], (False, False))
        return bool(lf and rf and self.world.finger_width() > 0.003)

    def touching_fingers(self, k, fc=None):
        fc = self.finger_contacts() if fc is None else fc
        return any(fc.get(self.world.ob[k], (False, False)))

    def com_z(self, k):
        return float(self.d.xipos[self.world.ob[k]][2])

    def tip_now(self):
        """Fingertip point (measured, world) and the hand's yaw (closing direction, B.down's convention)."""
        d, w = self.d, self.world
        R = d.site_xmat[w.tcp].reshape(3, 3)
        return d.site_xpos[w.tcp] + R[:, 2] * TIP, math.atan2(R[1, 1], R[0, 1])

    # ------------------------------------------------------------------ clutter_env's grasp check (reused as is)
    kin_grasps = CE.ClutterEnv.kin_grasps
    _kin_grasps = CE.ClutterEnv._kin_grasps
    _hand = CE.ClutterEnv._hand

    # ------------------------------------------------------------------ rendering
    def render(self, cam, depth=True, seg=True):
        """The world's camera cam at 640x480 from its TRUE pose (calibration error included): RGB (uint8), ideal
        metric depth along the optical axis (m, inf where none), geom id per pixel (-1 = none)."""
        m, d, mj, rr = self.m, self.d, self.mj, self.renderer
        cid = m.camera(cam).id
        opt = mj.MjvOption(); opt.sitegroup[:] = 0
        rr.update_scene(d, camera=cid, scene_option=opt); rgb = rr.render().copy()
        dep = gid = None
        if depth:
            refl = rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION]
            rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION] = 0     # (a depth sensor sees the surface, not its mirror image)
            rr.enable_depth_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); dep = rr.render().copy()
            rr.disable_depth_rendering()
            rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION] = refl
            dep = np.where(np.isfinite(dep) & (dep < 10), dep, np.inf).astype(np.float32)
        if seg:
            rr.enable_segmentation_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); s = safe_segmentation(rr)
            rr.disable_segmentation_rendering()
            gid = np.where((s[..., 1] == int(mj.mjtObj.mjOBJ_GEOM)) & (s[..., 0] >= 0), s[..., 0], -1).astype(np.int32)
        return rgb, dep, gid

    def intrinsics(self, cam):
        cid = self.m.camera(cam).id
        f = CAM_H / 2 / math.tan(math.radians(self.m.cam_fovy[cid]) / 2)
        return np.array([[f, 0, CAM_W / 2], [0, f, CAM_H / 2], [0, 0, 1.0]])

    def free_view(self, lookat, distance=0.75, azimuth=205.0, elevation=-35.0):
        """A third-person view for videos only (never an observation)."""
        mj, rr = self.mj, self.renderer
        c = mj.MjvCamera(); c.type = mj.mjtCamera.mjCAMERA_FREE
        c.lookat[:] = lookat; c.distance = distance; c.azimuth = azimuth; c.elevation = elevation
        opt = mj.MjvOption(); opt.sitegroup[:] = 0
        rr.update_scene(self.d, camera=c, scene_option=opt)
        return rr.render().copy()

    def close(self):
        r = self.renderer
        if r is not None:                                # (MuJoCo 2.3.7's Renderer has no close())
            for f in (lambda: r._mjr_context.free(), lambda: r._gl_context.free()):
                try:
                    f()
                except Exception:
                    pass
            self.renderer = None


# ---------------------------------------------------------------- 2D convex geometry (footprints)
def _hull(P):
    P = np.unique(np.round(P, 6), axis=0)
    P = P[np.lexsort((P[:, 1], P[:, 0]))]
    if len(P) < 3:
        return P

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in P:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in P[::-1]:
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return np.array(lo[:-1] + hi[:-1])


def _seg_dist(p, a, b):
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(float(ab @ ab), 1e-12), 0, 1)
    return float(np.linalg.norm(p - (a + t * ab)))


def poly_overlap(A, B_):
    """Separating-axis test for two convex polygons."""
    for P in (A, B_):
        for i in range(len(P)):
            e = P[(i + 1) % len(P)] - P[i]
            n = np.array([-e[1], e[0]])
            pa, pb = A @ n, B_ @ n
            if pa.max() < pb.min() or pb.max() < pa.min():
                return False
    return True


def poly_gap(A, B_):
    """Distance between two convex polygons (0 if they overlap)."""
    if poly_overlap(A, B_):
        return 0.0
    best = 1e9
    for P, Q in ((A, B_), (B_, A)):
        for p in P:
            for i in range(len(Q)):
                best = min(best, _seg_dist(p, Q[i], Q[(i + 1) % len(Q)]))
    return best


def safe_segmentation(rr):
    """mujoco.Renderer.render() in segmentation mode (2.3.7's code), but a pixel whose colour decodes to no scene geom
    (multisampling blends the id colours at a few edge pixels: 6 px on the kettle's rim, 2026-11-18) is background
    instead of an IndexError. -> (H, W, 2) int32 (object id, object type), -1 = none."""
    import mujoco
    from mujoco import _render
    sc = rr._scene; fl = sc.flags.copy()
    sc.flags[mujoco.mjtRndFlag.mjRND_SEGMENT] = 1; sc.flags[mujoco.mjtRndFlag.mjRND_IDCOLOR] = 1
    try:
        _render.mjr_render(rr._rect, sc, rr._mjr_context)
        out = np.empty((rr._height, rr._width, 3), np.uint8)
        _render.mjr_readPixels(out, None, rr._rect, rr._mjr_context)
    finally:
        np.copyto(sc.flags, fl)
    im = out.astype(np.uint32)
    segimage = im[..., 0] + im[..., 1] * 256 + im[..., 2] * 65536
    n = sc.ngeom
    lut = np.full((n + 2, 2), -1, np.int32)
    for g in sc.geoms[:n]:
        if g.segid != -1:
            lut[g.segid + 1] = (g.objid, g.objtype)
    segimage = np.where(segimage <= n, segimage, n + 1)          # (no geom: -1, -1)
    return np.flipud(lut[segimage]).copy()
