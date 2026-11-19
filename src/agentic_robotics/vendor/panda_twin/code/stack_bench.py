"""Stack bench for the Panda table twin: "put the red octagon on the green one" with the left arm, on randomized layouts,
judged on the TRUE state; the program sees the pieces only through its perception (or ground truth).

    MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 ~/miniforge3/envs/teleavatar_client/bin/python -I code/stack_bench.py \
        --out out/stack_bench --layouts 24 --conditions gt:open gt:precise d415:open d415:precise ...
    (camera conditions need code/composite_server.py on :8132 (photoreal images) and the SAM3 server on :8131)

Program (both variants): locate red and green with the scene camera -> top grasp of red (fingers closing across the
direction from the arm base) -> lift (gripper-width check) -> carry over green -> set down -> open -> retreat.
  open     aims ONCE: the hand goes to green's estimate + the piece-in-hand offset assumed at the grasp, lets go 4 mm
           above the seat (the TeleAvatar program's way).
  precise  servoes the RELATIVE red-to-green offset measured in ONE camera image (calibration errors mostly cancel):
           at 3 cm and at 8 mm above the seat, then lowers in 1 mm steps until the arm feels the contact (the hand lags
           its command), unloads, opens.
  precise3d  as precise, in 3D: the first measurement 10 cm above the seat (before descending), then 3 cm and 8 mm,
           correcting the height too from the gap between red's bottom and green's top in the same image (the scene
           camera's height of green is not trusted).
Perception (condition "<perception>:<program>"):
  gt           true poses.
  d415         the scene camera (RealSense D415, 640x480 colour + aligned depth): SAM3 mask on the photoreal composite
               -> 3D points from the simulated depth (MuJoCo depth + RealSense noise sigma_z = 0.08 px z^2 / (f b))
               -> the top face (points near the highest) -> rim circle fit -> centre. NOMINAL camera pose.
  wrist        d415 to find the pieces, then the left wrist D415 to re-locate red before the grasp and for the
               relative alignment over green.
  astra        as wrist, but Astra (gpt-6-astra) marks the points instead of SAM3: 2 grasp contact points on red (the
               fingers then close across them) and the place point on green, in the D415 and then the wrist image; while
               holding, red's and green's top centres in the wrist image. Pixels -> 3D with the cameras' depth.
  *_uncal     the cameras' TRUE poses differ from the nominal ones (calibration error): D415 +-1.5 cm / +-2 deg (as the
               TeleAvatar head in its bench), wrist D415 hand-eye +-3 mm / +-1.5 deg (as its wrist).
Controller: position-servoed joints (the twin's actuators) + gravity compensation (the real Franka has it built in),
straight TCP lines with IK every 1/30 s.
Judge (as the TeleAvatar bench): red on green, centres < 2 cm apart, red's bottom within 8 mm of green's top, tilt < 15 deg.
"""
from __future__ import annotations

import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import argparse
import io
import json
import math
import time
import urllib.request
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "model/twin_6508.xml"
ARM = "L_"
DT = 1 / 30
OPEN_W = 0.04
H = 0.0295
PIECE = {"red": dict(body="obj_oct_red", flats=0.050, prompts=("red octagonal block", "red block", "red object")),
         "green": dict(body="obj_oct_green", flats=0.078, prompts=("green octagonal block", "green octagon"))}
RIM = {k: 0.5 * (v["flats"] / 2 + v["flats"] / 2 / math.cos(math.pi / 8)) for k, v in PIECE.items()}
CAM = {"d415": dict(baseline=0.055, jit_pos=0.015, jit_deg=2.0), "L_wrist": dict(baseline=0.055, jit_pos=0.003, jit_deg=1.5)}   # both RealSense D415
SIZE = {"d415": (640, 480), "L_wrist": (640, 480)}        # both RealSense D415 colour at 640x480 (42.5 deg vertical; user ref videos 10-26)
F_DEPTH = 625.0   # the real D415s' depth focal length at 640x480 (620 / 629 px, read from both cameras 10-27)
MIN_Z = 0.275     # D415 depth at 640x480: nothing nearer than F_DEPTH x 55 mm / 126 px of disparity search (0.273 m)
BAND_EXTRA = 30   # D415 blind band at the image's LEFT edge: F_DEPTH x 55 mm / z + BAND_EXTRA px (real: 70 px at 1 m, 129 at 0.35)
WORKSPACE = np.array([[x, y, z] for x in (-0.55, 0.0) for y in (-0.17, 0.45) for z in (0.0, 0.10)])   # the left arm's side
SAM_URL, COMP_URL = "http://127.0.0.1:8131/segment", "http://127.0.0.1:8132/composite"


def down(yaw):
    """TCP frame pointing straight down, its y axis (the fingers' closing direction) at angle yaw in the table plane."""
    y = np.array([math.cos(yaw), math.sin(yaw), 0.0]); z = np.array([0, 0, -1.0])
    return np.stack([np.cross(y, z), y, z], 1)


def small_rot(rv):
    th = np.linalg.norm(rv)
    if th < 1e-12:
        return np.eye(3)
    k = rv / th; Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * Kx + (1 - math.cos(th)) * Kx @ Kx


class Sim:
    def __init__(self, noslip=0, gravcomp=True):
        import mujoco
        self.mj = mujoco
        m = self.m = mujoco.MjModel.from_xml_path(str(MODEL)); d = self.d = mujoco.MjData(m)
        m.opt.noslip_iterations = noslip
        mujoco.mj_resetDataKeyframe(m, d, m.key("twin").id); mujoco.mj_forward(m, d)
        self.tcp = m.site(ARM + "tcp").id
        jid = [m.joint(f"{ARM}joint{i}").id for i in range(1, 8)]
        self.qadr = np.array([m.jnt_qposadr[j] for j in jid]); self.dadr = np.array([m.jnt_dofadr[j] for j in jid])
        self.act = [m.actuator(f"{ARM}a{i}").id for i in range(1, 8)]
        self.grip = m.actuator(ARM + "gripper").id
        self.fing = [m.jnt_qposadr[m.joint(ARM + j).id] for j in ("finger_joint1", "finger_joint2")]
        self.lo, self.hi = m.jnt_range[jid].T
        self.q_home = d.qpos[self.qadr].copy()
        self.body = {k: m.body(v["body"]).id for k, v in PIECE.items()}
        self.fadr = {k: m.jnt_qposadr[m.body_jntadr[b]] for k, b in self.body.items()}
        self.base = d.xpos[m.body(ARM + "link0").id].copy()
        self.sub = int(round(DT / m.opt.timestep))
        self.gravcomp = gravcomp
        # all dofs of this arm (joints + fingers) for gravity compensation
        self.arm_dofs = np.array([m.jnt_dofadr[j] for j in range(m.njnt) if m.joint(j).name.startswith(ARM)])
        self.k = mujoco.MjData(m)
        self.rec, self.timeline = [], []
        self.sync_cmd()

    def sync_cmd(self):
        self.q_cmd = self.d.qpos[self.qadr].copy()
        self.p_cmd = self.d.site_xpos[self.tcp].copy(); self.R_cmd = self.d.site_xmat[self.tcp].reshape(3, 3).copy()
        self.g_cmd = float(self.d.ctrl[self.grip])

    def ik(self, pos, R, q0, iters=100):
        m, k, mj = self.m, self.k, self.mj
        k.qpos[:] = self.d.qpos; k.qpos[self.qadr] = q0
        for _ in range(iters):
            mj.mj_kinematics(m, k); mj.mj_comPos(m, k)
            e_p = pos - k.site_xpos[self.tcp]
            Rc = k.site_xmat[self.tcp].reshape(3, 3)
            e_r = 0.5 * sum(np.cross(Rc[:, i], R[:, i]) for i in range(3))
            if np.linalg.norm(e_p) < 5e-5 and np.linalg.norm(e_r) < 5e-4:
                break
            jp, jr = np.zeros((3, m.nv)), np.zeros((3, m.nv))
            mj.mj_jacSite(m, k, jp, jr, self.tcp)
            J = np.vstack([jp, jr])[:, self.dadr]
            dq = J.T @ np.linalg.solve(J @ J.T + 1e-4 * np.eye(6), np.r_[e_p, e_r])
            dq += 0.05 * (np.eye(7) - np.linalg.pinv(J) @ J) @ (self.q_home - k.qpos[self.qadr])
            k.qpos[self.qadr] = np.clip(k.qpos[self.qadr] + dq, self.lo, self.hi)
        return k.qpos[self.qadr].copy(), float(np.linalg.norm(e_p))

    def physics(self, n_frames=1):
        m, d, mj = self.m, self.d, self.mj
        for _ in range(n_frames):
            for _ in range(self.sub):
                if self.gravcomp:
                    d.qfrc_applied[self.arm_dofs] = d.qfrc_bias[self.arm_dofs]
                mj.mj_step(m, d)
            self.rec.append((float(d.time), d.qpos.copy()))

    def move(self, target, R, grip, T, phase=None):
        """Straight TCP line from the commanded pose (smoothstep), IK every frame; the gripper ramps over the first half."""
        if phase:
            self.timeline.append((float(self.d.time), "phase", phase))
        n = max(int(round(T / DT)), 1); worst = 0.0
        p0, g0 = self.p_cmd.copy(), self.g_cmd
        target = np.asarray(target, float)
        for i in range(1, n + 1):
            s = i / n; s = s * s * (3 - 2 * s)
            q, e = self.ik(p0 + (target - p0) * s, R, self.q_cmd); worst = max(worst, e)
            self.q_cmd = q
            self.d.ctrl[self.act] = q
            self.d.ctrl[self.grip] = g0 + (grip - g0) * min(1.0, 2 * s)
            self.physics()
        self.p_cmd, self.R_cmd, self.g_cmd = target.copy(), R, grip
        return worst

    def hold(self, T):
        self.physics(max(int(round(T / DT)), 1))

    def tcp_now(self):
        return self.d.site_xpos[self.tcp].copy()

    def finger_width(self):
        return float(sum(self.d.qpos[a] for a in self.fing))

    def contact_force(self, ka, kb):
        """Normal force (N) between two pieces: what a wrist F/T sensor (or the Franka's external-force estimate) feels."""
        m, d, ba, bb = self.m, self.d, self.body[ka], self.body[kb]
        f6, tot = np.zeros(6), 0.0
        for i in range(d.ncon):
            c = d.contact[i]
            if {m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]} == {ba, bb}:
                self.mj.mj_contactForce(m, d, i, f6); tot += abs(f6[0])
        return tot

    def piece(self, k):
        b = self.body[k]
        z = self.d.xmat[b].reshape(3, 3)[:, 2]
        return self.d.xpos[b].copy(), float(math.degrees(math.acos(np.clip(z[2], -1, 1))))

    def place_piece(self, k, x, y, yaw):
        a = self.fadr[k]
        self.d.qpos[a:a + 3] = [x, y, H / 2 + 0.0005]
        self.d.qpos[a + 3:a + 7] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
        self.d.qvel[self.m.jnt_dofadr[self.m.body_jntadr[self.body[k]]]:][:6] = 0

    def set_camera_error(self, cam, dpos, drot_deg):
        """The camera's TRUE pose = nominal (its body frame) moved by dpos and turned by drot (deg, camera axes)."""
        m, cid = self.m, self.m.camera(cam).id
        if not hasattr(self, "_cam0"):
            self._cam0 = {}
        if cam not in self._cam0:
            self._cam0[cam] = (m.cam_pos[cid].copy(), m.cam_quat[cid].copy())
        p0, q0 = self._cam0[cam]
        R0 = np.zeros(9); self.mj.mju_quat2Mat(R0, q0); R0 = R0.reshape(3, 3)
        R1 = R0 @ small_rot(np.radians(drot_deg))
        q1 = np.zeros(4); self.mj.mju_mat2Quat(q1, R1.flatten())
        m.cam_pos[cid] = p0 + np.asarray(dpos); m.cam_quat[cid] = q1

    def nominal_camera(self, cam):
        """(R_cv camera-to-world with OpenCV axes, eye) from the NOMINAL mount: what the program believes."""
        m, d, cid = self.m, self.d, self.m.camera(cam).id
        p0, q0 = getattr(self, "_cam0", {}).get(cam, (m.cam_pos[cid], m.cam_quat[cid]))
        R0 = np.zeros(9); self.mj.mju_quat2Mat(R0, q0); R0 = R0.reshape(3, 3)
        b = m.cam_bodyid[cid]; Rb = d.xmat[b].reshape(3, 3)
        return Rb @ R0 @ np.diag([1, -1, -1]), d.xpos[b] + Rb @ p0


# ---------------------------------------------------------------- layouts
def sample_layout(seed, idx):
    rng = np.random.default_rng([seed, idx])
    S = Sim()
    m, d, mj = S.m, S.d, S.mj
    piece_geoms = {k: [g for g in range(m.ngeom) if m.geom_bodyid[g] == b] for k, b in S.body.items()}
    ok_other = {m.geom("table_top").id}
    for tries in range(300):
        xy = {}
        for k in ("green", "red"):
            for _ in range(200):
                p = rng.uniform([-0.50, -0.12], [-0.05, 0.40])       # the left arm's side (the right arm is 43 cm away)
                r = np.linalg.norm(p - S.base[:2])
                if not 0.33 < r < 0.66:
                    continue
                if k == "red" and np.linalg.norm(p - xy["green"]) < 0.13:
                    continue
                xy[k] = p; break
        if len(xy) < 2:
            continue
        yaws = {k: float(rng.uniform(0, 2 * math.pi)) for k in xy}
        mj.mj_resetDataKeyframe(m, d, m.key("twin").id)
        for k in xy:
            S.place_piece(k, xy[k][0], xy[k][1], yaws[k])
        margin0 = m.geom_margin.copy()
        for k in xy:
            m.geom_margin[piece_geoms[k]] = 0.05                   # room for the open fingers
        mj.mj_forward(m, d)
        bad = False
        for i in range(d.ncon):
            c = d.contact[i]; g1, g2 = c.geom1, c.geom2
            for k in xy:
                if g1 in piece_geoms[k] or g2 in piece_geoms[k]:
                    other = g2 if g1 in piece_geoms[k] else g1
                    if other not in ok_other and other not in piece_geoms[k]:
                        bad = True
        m.geom_margin[:] = margin0
        if bad:
            continue
        # reachable top grasps over both pieces (IK to 2 mm) without the left arm touching the right arm or the frame
        reach = True
        yaw = math.atan2(*(xy["red"] - S.base[:2])[::-1]) + math.pi / 2
        q_keep = d.qpos.copy()

        def arm_touches():
            out = set()
            for i in range(d.ncon):
                c = d.contact[i]
                n1, n2 = m.body(m.geom_bodyid[c.geom1]).name, m.body(m.geom_bodyid[c.geom2]).name
                if {n1, n2} & {"world"} and ("finger" in n1 + n2):
                    continue                                             # fingertips on the table at the grasp height
                if (n1.startswith("L_") and not n2.startswith(("L_", "obj_oct"))) or (n2.startswith("L_") and not n1.startswith(("L_", "obj_oct"))):
                    out.add((n1, n2))
            return out
        mj.mj_forward(m, d); rest = arm_touches()                    # the base standing on the frame
        for k in xy:
            for dz in (0.0, 0.03, 0.07, 0.15):
                q, e = S.ik(np.r_[xy[k], H / 2 + dz + (H if k == "green" else 0)], down(yaw), S.q_home)
                reach &= e < 0.002
                d.qpos[S.qadr] = q; mj.mj_forward(m, d)
                if arm_touches() - rest:
                    reach = False
        d.qpos[:] = q_keep
        if not reach:
            continue
        # both pieces visible to the scene camera (not hidden behind the basket, the mug tree, ...): rays from the
        # D415 to the centre and 4 rim points of each top face; at least 4 of 5 must reach the piece first
        mj.mj_forward(m, d)
        eye = d.cam_xpos[m.camera("d415").id].copy(); gid = np.zeros(1, np.int32); vis = True
        for k in xy:
            c = d.xpos[S.body[k]]
            pts = [c + [0, 0, H / 2 - 0.002]] + [c + [0.7 * RIM[k] * math.cos(a), 0.7 * RIM[k] * math.sin(a), H / 2 - 0.002] for a in np.arange(4) * math.pi / 2]
            hits = 0
            for q in pts:
                v = q - eye
                mj.mj_ray(m, d, eye, v / np.linalg.norm(v), None, 1, -1, gid)
                hits += gid[0] >= 0 and m.geom_bodyid[gid[0]] == S.body[k]
            vis &= hits >= 4
        if not vis:
            continue
        cams = {c: dict(dpos=(rng.uniform(-1, 1, 3) * v["jit_pos"]).tolist(), drot_deg=(rng.uniform(-1, 1, 3) * v["jit_deg"]).tolist())
                for c, v in CAM.items()}
        return dict(seed=seed, layout=idx, red=xy["red"].tolist(), green=xy["green"].tolist(), red_yaw=yaws["red"],
                    green_yaw=yaws["green"], cams=cams, tries=tries)
    return None


def apply_layout(S, L, uncal):
    for k in ("red", "green"):
        S.place_piece(k, L[k][0], L[k][1], L[k + "_yaw"])
    S.mj.mj_forward(S.m, S.d)
    for c in CAM:                                        # remember the nominal mounts, then (maybe) move them
        S.set_camera_error(c, [0, 0, 0], [0, 0, 0])
        if uncal:
            S.set_camera_error(c, L["cams"][c]["dpos"], L["cams"][c]["drot_deg"])
    S.d.ctrl[S.act] = S.q_home; S.d.ctrl[S.grip] = OPEN_W
    S.hold(0.5)                                          # settle
    S.rec.clear()
    S.sync_cmd(); S.p_cmd = S.tcp_now(); S.q_cmd = S.q_home.copy(); S.g_cmd = OPEN_W


# ---------------------------------------------------------------- perception
class Perception:
    """locate(piece, cam) -> centre (3,) or None; relative(cam) -> red centre - green centre (3,) or None."""

    def __init__(self, S, kind, rng):
        self.S, self.kind, self.rng = S, kind, rng
        self.log, self.last = [], {}
        self.renderer = None
        if kind != "gt":
            m = S.m
            m.vis.global_.offwidth = max(m.vis.global_.offwidth, 1280); m.vis.global_.offheight = max(m.vis.global_.offheight, 720)
            self.renderers = {wh: S.mj.Renderer(m, wh[1], wh[0]) for wh in set(SIZE.values())}
            self.renderer = True
            names = [m.body(b).name for b in range(m.nbody)]
            self.arm_geom = np.array([names[b].startswith(("L_", "R_")) for b in m.geom_bodyid])
            self.obj_geom = np.array([names[b].startswith("obj_") for b in m.geom_bodyid])
            self.fill_geom = np.array(["_inner" in (m.geom(g).name or "") for g in range(m.ngeom)])  # never scanned: MuJoCo draws them
            self.obj_bodies = [b for b in range(m.nbody) if names[b].startswith("obj_")]
            self.obj_names = [names[b] for b in self.obj_bodies]
            self.geom_group0 = m.geom_group.copy()

    # -- sensing
    def observe(self, cam):
        S, m, d, mj = self.S, self.S.m, self.S.d, self.S.mj
        rr = self.renderers[SIZE[cam]]; Wc, Hc = SIZE[cam]
        mj.mj_forward(m, d)
        cid = m.camera(cam).id
        f = Hc / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        K = (Wc, Hc, f, f, Wc / 2, Hc / 2)
        opt = mj.MjvOption(); opt.sitegroup[:] = 0
        rr.update_scene(d, camera=cid, scene_option=opt); rgb = rr.render().copy()
        refl = rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION]   # a depth sensor sees the steel, not its mirror image
        rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION] = 0      # (MuJoCo draws reflections into the depth buffer)
        rr.enable_depth_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); dep = rr.render().copy(); rr.disable_depth_rendering()
        rr.scene.flags[mj.mjtRndFlag.mjRND_REFLECTION] = refl
        rr.enable_segmentation_rendering(); rr.update_scene(d, camera=cid, scene_option=opt); seg = rr.render().copy(); rr.disable_segmentation_rendering()
        gid = np.clip(seg[..., 0], 0, m.ngeom - 1)
        is_geom = (seg[..., 1] == int(mj.mjtObj.mjOBJ_GEOM)) & (seg[..., 0] >= 0)
        label = np.select([is_geom & self.arm_geom[gid], is_geom & self.fill_geom[gid], is_geom & self.obj_geom[gid]], [1, 3, 2], 0).astype(np.uint8)
        m.geom_group[self.arm_geom | self.obj_geom] = 5
        opt_s = mj.MjvOption(); opt_s.sitegroup[:] = 0; opt_s.geomgroup[:] = 1; opt_s.geomgroup[5] = 0
        rr.update_scene(d, camera=cid, scene_option=opt_s); rgb_s = rr.render().copy()
        m.geom_group[:] = self.geom_group0
        lum = lambda im: im.astype(np.float32).mean(2) + 1.0
        shade = np.clip(lum(rgb) / lum(rgb_s), 0, 1.99); shade[label > 0] = 1.0
        Rm = d.cam_xmat[cid].reshape(3, 3)
        R_true, eye_true = Rm @ np.diag([1, -1, -1]), d.cam_xpos[cid].copy()
        depth = np.where(np.isfinite(dep) & (dep < 10), dep, np.inf).astype(np.float32)
        buf = io.BytesIO()
        np.savez(buf, R_cv=R_true, eye=eye_true, K=np.array(K, float), rgb=rgb, depth=depth, label=label,
                 shade=shade.astype(np.float32), names=json.dumps(self.obj_names), pos=d.xpos[self.obj_bodies],
                 quat=d.xquat[self.obj_bodies])
        r = urllib.request.urlopen(urllib.request.Request(COMP_URL, data=buf.getvalue()), timeout=120)
        photo = np.load(io.BytesIO(r.read()))["rgb"]        # (the splat's own depth is too rough: the sensor sees MuJoCo's)
        # the depth sensor: RealSense stereo noise (sigma_z = 0.08 px * z^2 / (f * baseline)), per pixel
        sig = 0.08 * depth ** 2 / (f * CAM[cam]["baseline"])
        noisy = depth + self.rng.normal(0, 1, depth.shape).astype(np.float32) * np.where(np.isfinite(sig), sig, 0)
        noisy = np.where(depth > MIN_Z, noisy, np.inf).astype(np.float32)   # (too near: no depth)
        if getattr(self, "depth_model", None) is not None:                  # D415_DEPTH=real (d415_model.py)
            noisy = self.depth_model.apply(depth, np.where(is_geom, gid, -1), cam)
        R_nom, eye_nom = S.nominal_camera(cam)
        return dict(cam=cam, rgb=photo, depth=noisy, K=K, R=R_nom, eye=eye_nom, R_true=R_true, eye_true=eye_true, t=float(d.time))

    def segment(self, img, phrase, box=None):
        """SAM3 masks (best first, at most 4). box (x0, y0, x1, y1): segment that crop, enlarged to 1008 px on its long
        side (small far objects), and paste the masks back."""
        import cv2
        x0, y0, x1, y1 = box if box is not None else (0, 0, img.shape[1], img.shape[0])
        crop = img[y0:y1, x0:x1]
        sc = max(1.0, 1008 / max(crop.shape[:2]))
        big = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC) if sc > 1 else crop
        buf = io.BytesIO(); np.savez(buf, image=np.ascontiguousarray(big), phrases=json.dumps([phrase]))
        r = urllib.request.urlopen(urllib.request.Request(SAM_URL, data=buf.getvalue()), timeout=120)
        z = np.load(io.BytesIO(r.read()))
        out = []
        for i in range(min(6, len(z["scores_0"]))):
            mk = z["masks_0"][i]
            if sc > 1:
                mk = cv2.resize(mk.astype(np.uint8), (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_AREA) > 0
            full = np.zeros(img.shape[:2], bool); full[y0:y1, x0:x1] = mk
            out.append((full, float(z["scores_0"][i])))
        return out

    def roi(self, obs, pts, pad=20):
        """Image box around world points (nominal camera), clipped to the image."""
        uv, dep = self.project(np.asarray(pts, float), obs["R"], obs["eye"], obs["K"])
        uv = uv[dep > 0.02]
        W, Hh = int(obs["K"][0]), int(obs["K"][1])
        x0, y0 = np.clip(np.floor(uv.min(0)) - pad, 0, [W, Hh]).astype(int)
        x1, y1 = np.clip(np.ceil(uv.max(0)) + pad, 0, [W, Hh]).astype(int)
        return (x0, y0, x1, y1) if x1 - x0 > 20 and y1 - y0 > 20 else None

    @staticmethod
    def _mask_px(mask):
        import cv2
        return cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0

    def table_height(self, obs, mask, d_piece):
        """The table's height right around the mask (nominal camera): the low surface in a ring around it, not in front
        of the piece - the same (mis)calibration as the piece's own points, so their difference is right."""
        import cv2
        m8 = mask.astype(np.uint8)
        ring = (cv2.dilate(m8, np.ones((41, 41), np.uint8)) > 0) & ~(cv2.dilate(m8, np.ones((11, 11), np.uint8)) > 0)
        P = self.lift(obs, ring, erode=False, band=(d_piece - 0.03, d_piece + 0.25))
        return None if P is None else float(np.percentile(P[:, 2], 30))

    # -- geometry
    @staticmethod
    def lift(obs, mask, erode=True, band=None):
        """3D points (nominal camera) of the mask's pixels; band (near, far) in m: keep depths within it."""
        import cv2
        if erode:
            mask = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        ys, xs = np.nonzero(mask & np.isfinite(obs["depth"]))
        z = obs["depth"][ys, xs]
        if band is not None:
            keep = (z > band[0]) & (z < band[1]); ys, xs, z = ys[keep], xs[keep], z[keep]
        if len(xs) < 10:
            return None
        W, Hh, fx, fy, cx, cy = obs["K"]
        Xc = np.stack([(xs - cx) / fx * z, (ys - cy) / fy * z, z], 1)
        return Xc @ obs["R"].T + obs["eye"]

    @staticmethod
    def top_rim_fit(P, sigma):
        """Centre of an upright prism from its points: the top face (points near the highest), rim by angular bins,
        circle fit (Kasa). Returns centre xy, radius, top z, rim coverage (fraction of 32 bins)."""
        z_top = np.percentile(P[:, 2], 97)
        T = P[P[:, 2] > z_top - max(0.005, 2.5 * sigma)]
        if len(T) < 12:
            return None
        c = np.median(T[:, :2], 0)
        for _ in range(3):
            v = T[:, :2] - c; ang = np.arctan2(v[:, 1], v[:, 0]); r = np.hypot(v[:, 0], v[:, 1])
            bins = ((ang + math.pi) / (2 * math.pi) * 32).astype(int) % 32
            rim = []
            for b in range(32):
                s = bins == b
                if s.sum() >= 2:
                    rr = np.percentile(r[s], 85); a = (b + 0.5) / 32 * 2 * math.pi - math.pi
                    rim.append(c + rr * np.array([math.cos(a), math.sin(a)]))
            if len(rim) < 6:
                return None
            rim = np.array(rim)
            A = np.c_[2 * rim, np.ones(len(rim))]; bb = (rim ** 2).sum(1)
            sol = np.linalg.lstsq(A, bb, rcond=None)[0]
            c = sol[:2]; rad = math.sqrt(max(sol[2] + c @ c, 1e-8))
        return c, rad, float(z_top), len(rim) / 32

    @staticmethod
    def project(X, R, eye, K):
        Xc = (np.asarray(X, float) - eye) @ R
        W, Hh, fx, fy, cx, cy = K
        return np.c_[fx * Xc[:, 0] / Xc[:, 2] + cx, fy * Xc[:, 1] / Xc[:, 2] + cy], Xc[:, 2]

    def silhouette_fit(self, obs, mask, k, z_bot, ignore=None):
        """(x, y) of the upright nominal-size prism standing at height z_bot whose projected outline (convex hull of
        its top and bottom rims) best overlaps the mask (IoU), coarse-to-fine from the ray through the mask centroid:
        the TeleAvatar bench's fit, with the pinhole RealSense model. Returns centre (3,), IoU."""
        import cv2
        R, eye, K = obs["R"], obs["eye"], obs["K"]
        ys, xs = np.nonzero(mask)
        pad = 30
        x0, y0 = max(xs.min() - pad, 0), max(ys.min() - pad, 0)
        Mc = mask[y0:ys.max() + pad, x0:xs.max() + pad]
        Ic = np.zeros_like(Mc) if ignore is None else (ignore[y0:ys.max() + pad, x0:xs.max() + pad] & ~Mc)
        ring = RIM[k] * np.stack([np.cos(np.linspace(0, 2 * np.pi, 32, endpoint=False)), np.sin(np.linspace(0, 2 * np.pi, 32, endpoint=False))], 1)
        W, Hh, fx, fy, cx, cy = K
        ray = R @ np.array([(xs.mean() - cx) / fx, (ys.mean() - cy) / fy, 1.0])
        zc = z_bot + H / 2
        p0 = eye + ray * (zc - eye[2]) / ray[2]

        def iou(xy):
            pts = np.vstack([np.c_[ring + xy, np.full(32, z_bot)], np.c_[ring + xy, np.full(32, z_bot + H)]])
            uv, dep = self.project(pts, R, eye, K)
            if (dep <= 0.02).any():
                return 0.0
            P = np.zeros_like(Mc, np.uint8)
            cv2.fillConvexPoly(P, cv2.convexHull(np.round(uv - [x0, y0]).astype(np.int32)), 1)
            return np.logical_and(P, Mc).sum() / max((np.logical_or(P, Mc) & ~Ic).sum(), 1)   # occluded pixels say nothing
        best = p0[:2].copy(); bi = iou(best)
        for half, step in ((0.03, 0.003), (0.004, 0.0008), (0.0008, 0.0002)):
            g = np.arange(-half, half + 1e-9, step); centre = best.copy()
            for dx in g:
                for dy in g:
                    v = iou(centre + [dx, dy])
                    if v > bi:
                        bi, best = v, centre + [dx, dy]
        return np.r_[best, zc], float(bi)

    @staticmethod
    def above(obs, z):
        """Pixels whose surface (from the depth) is higher than z: in front of a piece whose top is at z (the held
        piece, the fingers) - they hide it."""
        W, Hh, fx, fy, cx, cy = obs["K"]
        v, u = np.mgrid[0:int(Hh), 0:int(W)]
        dep = obs["depth"]
        ray_z = (obs["R"] @ np.stack([(u - cx) / fx, (v - cy) / fy, np.ones_like(u, float)]).reshape(3, -1))[2].reshape(dep.shape)
        zw = obs["eye"][2] + ray_z * np.where(np.isfinite(dep), dep, 1e3)
        return np.isfinite(dep) & (zw > z)

    def measure(self, obs, k, prior=None, resting=True, near=None, box=None):
        """The piece in this observation. Candidates: SAM3 masks for its prompts (most specific prompt first); each
        gets its height from the depth (top of the masked points) and a silhouette fit. Plausible = IoU > 0.5, the top
        at a resting piece's height (resting) or above the table and within 4 cm of `near` (a held piece), within 6 cm
        of the prior estimate if any. The best IoU of the first prompt with a plausible candidate wins."""
        for phrase in PIECE[k]["prompts"]:
            best = None
            for mask, score in self.segment(obs["rgb"], phrase, box):
                if mask.sum() < 30:
                    continue
                P0 = self.lift(obs, mask)
                if P0 is None:
                    continue
                # the piece's own pixels: depth near the mask's median (a mask bleeding onto the basket rim in front,
                # or onto the table behind, must not set its height)
                dm = obs["depth"][self._mask_px(mask)]; d_med = float(np.median(dm[np.isfinite(dm)]))
                P = self.lift(obs, mask, band=(d_med - 0.04, d_med + 0.04))
                if P is None:
                    continue
                z_top = float(np.percentile(P[:, 2], 80))
                z_tab = self.table_height(obs, mask, d_med)
                if z_tab is None:
                    continue
                on_table = abs(z_top - z_tab - H) < 0.012
                if resting and not on_table or not resting and z_top < z_tab + H + 0.012:
                    continue
                z_bot = z_tab if on_table else z_top - H
                c, i = self.silhouette_fit(obs, mask, k, z_bot, self.above(obs, z_bot + H + 0.006))
                if i < 0.5 or (prior is not None and np.linalg.norm(c[:2] - prior[:2]) > 0.06) or \
                        (near is not None and np.linalg.norm(c[:2] - near[:2]) > 0.04):
                    continue
                if best is None or i > best["iou"]:
                    best = dict(centre=c, iou=i, score=score, phrase=phrase, px=int(mask.sum()))
            if best is not None:
                return best
        return None

    def _log(self, what, k, obs, est, truth):
        e = None if est is None else (np.asarray(est) - truth)
        self.log.append(dict(t=obs["t"] if obs else float(self.S.d.time), what=what, obj=k, cam=obs["cam"] if obs else None,
                             err_mm=None if e is None else float(np.linalg.norm(e[:2]) * 1000),
                             err_z_mm=None if e is None else float(e[2] * 1000),
                             est=None if est is None else np.asarray(est).tolist(), true=np.asarray(truth).tolist()))

    def locate(self, k, cam):
        p_true = self.S.piece(k)[0]
        if self.kind == "gt":
            return p_true
        obs = self.observe(cam)
        box = self.roi(obs, WORKSPACE) if cam == "d415" else None
        r = self.measure(obs, k, self.last.get(k), box=box)
        est = None if r is None else r["centre"]
        if est is not None:
            self.last[k] = est
        self._log("locate", k, obs, est, p_true)
        return est

    def relative(self, cam):
        rel_true = self.S.piece("red")[0] - self.S.piece("green")[0]
        if self.kind == "gt":
            return rel_true
        obs = self.observe(cam)
        tcp = self.S.tcp_now()                                                    # from the joints
        box = self.roi(obs, tcp + np.array([[x, y, z] for x in (-0.09, 0.09) for y in (-0.09, 0.09) for z in (-0.08, 0.04)])) if cam == "d415" else None
        a = self.measure(obs, "red", resting=False, near=tcp, box=box)           # held: near the hand
        b = self.measure(obs, "green", self.last.get("green"), box=box)
        est = None if (a is None or b is None) else a["centre"] - b["centre"]
        self._log("relative", "red-green", obs, est, rel_true)
        return est

    def close(self):
        if self.renderer is not None:
            for rr in self.renderers.values():
                getattr(rr, "close", lambda: None)()


ASTRA_MODEL, ASTRA_EFFORT = "gpt-6-astra", "medium"
ASTRA_PRICE = {"in": 10.0, "cached": 1.0, "out": 50.0}                   # $ / M tokens
ASTRA_TASK = "Task: pick up the RED octagonal block and stack it, centred, on top of the GREEN octagonal block."
ASTRA_PICK = """{task}
This is {what}, {W}x{H} pixels; pixel coordinates are x from the left edge, y from the top edge.
The gripper is a parallel-jaw gripper that grasps from above.
Mark{n_items}:
- grasp: the 2 points where the 2 fingertips should touch the red octagonal block - the middles of two OPPOSITE,
  PARALLEL flat side faces (never corners), each on the top edge of the block (the border of its top face). Other
  blocks may look similar (an orange octagon, a red pepper, a red cube): only the RED OCTAGONAL block counts.{place}
If a block is not visible, give null for its points.
Respond with this JSON object and nothing else:
{{"grasp": [[x, y], [x, y]],{place_json} "reasoning": "<1-2 sentences>"}}"""
ASTRA_PLACE = """
- place: the point where the red block's centre has to be put: the centre of the GREEN octagonal block's top face."""
ASTRA_CHECK = """Here is the same image with your marks drawn as crosses ({legend}).
Check them carefully against the image. If a mark is not exactly where you intended, give corrected coordinates
(same pixel frame); otherwise repeat them. Respond with the same JSON object and nothing else."""
ASTRA_SERVO = """{task}
This is the wrist camera on the gripper, looking straight down from beside the hand (the fingers are out of view),
{W}x{H} pixels; pixel coordinates are x from
the left edge, y from the top edge. The gripper holds the red octagonal block above the green octagonal block.
Mark, as precisely as you can:
- held: the centre of the held RED block's top face (it has a hole in the middle).
- place: the centre of the GREEN block's top face - the point the red block's centre has to be lowered onto. If the
  red block hides part of the green block, infer the green centre from its visible outline (an octagon, 78 mm across
  the flats; the red one is 50 mm).
If a block is not visible, give null for its point.
Respond with this JSON object and nothing else:
{{"held": [x, y], "place": [x, y], "reasoning": "<1-2 sentences>"}}"""


class AstraPerception(Perception):
    """The program's eyes are Astra: on a camera image it marks the grasp contact points on red (2 yellow crosses) and
    the place point on green (a blue cross), as pixels; a second call shows it its marks drawn and lets it correct
    them. Pixels -> 3D with the camera's depth (nominal camera model): each pixel's ray meets the top face's plane,
    the top face's height being the depth around the mark (the pieces have a hole in the middle). While holding red
    over green, Astra marks red's top centre and green's top centre in the wrist image (relative, as precise).
    Astra's calls and the marked images go to <out>/astra/."""

    COLS = {"grasp": (255, 230, 0), "held": (255, 230, 0), "place": (0, 120, 255)}
    COLNAME = {"grasp": "YELLOW", "held": "YELLOW", "place": "BLUE", "rim": "LIGHT BLUE"}

    def __init__(self, S, kind, rng, out, tag):
        super().__init__(S, kind, rng)
        self.contacts, self.cache, self.calls, self.cost = {}, {}, 0, 0.0
        self.dir = Path(out) / "astra"; self.dir.mkdir(parents=True, exist_ok=True)
        self.tag, self.astra_log = tag, []

    # -- Astra
    def ask(self, messages):
        import requests
        body = {"model": ASTRA_MODEL, "messages": messages, "max_completion_tokens": 6000, "reasoning_effort": ASTRA_EFFORT}
        for attempt in range(5):
            t = time.time()
            try:
                r = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=300,
                                  headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
                if r.status_code == 200:
                    break
                err = f"{r.status_code}: {r.text[:300]}"
            except Exception as e:
                err = repr(e)[:300]
            if attempt == 4:
                raise RuntimeError("Astra: " + err)
            time.sleep(5 * 2 ** attempt)
        j = r.json(); u = j.get("usage", {})
        pin, out = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        cost = ((pin - cached) * ASTRA_PRICE["in"] + cached * ASTRA_PRICE["cached"] + out * ASTRA_PRICE["out"]) / 1e6
        self.calls += 1; self.cost += cost
        txt = (j["choices"][0]["message"]["content"] or "").strip()
        try:
            js = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        except Exception:
            js = None
        return js, txt, dict(s=round(time.time() - t, 1), cost=round(cost, 4), tokens_in=pin, tokens_out=out)

    @staticmethod
    def img_block(img):
        import base64
        import cv2
        b = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()
        return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b).decode(), "detail": "high"}}

    @staticmethod
    def draw(img, marks):
        import cv2
        out = img.copy()
        for (x, y), col in marks:
            q = (int(round(x)), int(round(y)))
            cv2.drawMarker(out, q, (0, 0, 0), cv2.MARKER_CROSS, 17, 4); cv2.drawMarker(out, q, col, cv2.MARKER_CROSS, 15, 2)
        return out

    def mark(self, img, prompt, keys, check):
        """Astra's marks on img: {key: [x, y] or [[x, y], ...] or None}, with one check round if check."""
        import cv2
        n = len(self.astra_log)
        msgs = [{"role": "user", "content": [{"type": "text", "text": prompt}, self.img_block(img)]}]
        js, txt, info = self.ask(msgs)
        rounds = [dict(reply=txt, **info)]
        cols = self.COLS

        def marks_of(js):
            if not isinstance(js, dict):
                return None
            mk = []
            for k in keys:
                v = js.get(k)
                if v is None:
                    continue
                pts = v if any(isinstance(q, (list, tuple)) or q is None for q in v) else [v]
                mk += [((float(p[0]), float(p[1])), cols[k]) for p in pts if isinstance(p, (list, tuple)) and len(p) == 2
                       and all(isinstance(c, (int, float)) for c in p)]
            return mk
        if check and marks_of(js):
            drawn = self.draw(img, marks_of(js))
            legend = "; ".join(f"{self.COLNAME[k]} = {k}" for k in keys if js.get(k) is not None)
            msgs += [{"role": "assistant", "content": txt},
                     {"role": "user", "content": [{"type": "text", "text": ASTRA_CHECK.format(legend=legend)}, self.img_block(drawn)]}]
            js2, txt2, info2 = self.ask(msgs)
            rounds.append(dict(reply=txt2, **info2))
            if marks_of(js2):
                js = js2
        mk = marks_of(js)
        if mk:
            cv2.imwrite(str(self.dir / f"{self.tag}_{n:02d}.jpg"), cv2.cvtColor(self.draw(img, mk), cv2.COLOR_RGB2BGR))
        self.astra_log.append(dict(t=float(self.S.d.time), keys=keys, rounds=rounds, final=js, img=f"{self.tag}_{n:02d}.jpg"))
        return js if isinstance(js, dict) else {}

    # -- pixels -> 3D (nominal camera)
    @staticmethod
    def ray(obs, uv):
        W, Hh, fx, fy, cx, cy = obs["K"]
        return obs["R"] @ np.array([(uv[0] - cx) / fx, (uv[1] - cy) / fy, 1.0])

    def on_plane(self, obs, uv, z):
        d = self.ray(obs, uv)
        return obs["eye"] + d * (z - obs["eye"][2]) / d[2]

    def disc(self, obs, uv, r_world, r_in=0.0):
        """Pixels within r_world (m) of the mark (annulus if r_in), sized by the depth there."""
        W, Hh, fx, fy, cx, cy = obs["K"]
        u, v = int(round(uv[0])), int(round(uv[1]))
        win = obs["depth"][max(v - 6, 0):v + 7, max(u - 6, 0):u + 7]
        z = float(np.median(win[np.isfinite(win)])) if np.isfinite(win).any() else 0.5
        yy, xx = np.ogrid[:int(Hh), :int(W)]
        r2 = (xx - uv[0]) ** 2 + (yy - uv[1]) ** 2
        return (r2 <= (fx * r_world / z) ** 2) & (r2 >= (fx * r_in / z) ** 2), z

    def top_z(self, obs, uv, r_world=0.018):
        """Height of the top face under a mark: the high points in a disc around it (the hole and the rim's
        mixed pixels are low)."""
        m, z = self.disc(obs, uv, r_world)
        dm = obs["depth"][m]; dm = dm[np.isfinite(dm)]
        if len(dm) < 10:
            return None
        P = self.lift(obs, m, erode=False, band=(np.median(dm) - 0.04, np.median(dm) + 0.04))
        return None if P is None else float(np.percentile(P[:, 2], 80))

    def table_z(self, obs, uv, below):
        """The table around a resting piece (annulus 5-8 cm from its centre): low points under `below`."""
        m, _ = self.disc(obs, uv, 0.08, 0.05)
        P = self.lift(obs, m, erode=False)
        if P is None:
            return None
        P = P[P[:, 2] < below]
        return None if len(P) < 20 else float(np.percentile(P[:, 2], 30))

    def full(self, obs, box):
        return obs["rgb"] if box is None else obs["rgb"][box[1]:box[3], box[0]:box[2]]

    # -- the perception interface
    def locate(self, k, cam):
        p_true = self.S.piece(k)[0]
        if k in self.cache:                                  # green from the same call as red (static scene)
            obs, est = self.cache.pop(k)
            self._log("locate", k, obs, est, p_true)
            return est
        obs = self.observe(cam)
        box = self.roi(obs, WORKSPACE) if cam == "d415" else None
        img = self.full(obs, box); o = np.array([0, 0] if box is None else box[:2], float)
        what = ("a view of the table from a fixed camera" if cam == "d415"
                else "the wrist camera on the gripper, looking straight down from beside the hand, above the red block")
        want_place = cam == "d415"
        js = self.mark(img, ASTRA_PICK.format(task=ASTRA_TASK, what=what, W=img.shape[1], H=img.shape[0],
                                              n_items=" two things" if want_place else "",
                                              place=ASTRA_PLACE if want_place else "",
                                              place_json=' "place": [x, y],' if want_place else ""),
                       ["grasp", "place"] if want_place else ["grasp"], check=True)
        red = None
        g = js.get("grasp")
        if g and len(g) == 2 and g[0] is not None and g[1] is not None:
            uv = [np.asarray(p, float) + o for p in g]
            zt = self.top_z(obs, (uv[0] + uv[1]) / 2)
            if zt is not None:
                C = [self.on_plane(obs, q, zt) for q in uv]
                red = np.r_[(C[0][:2] + C[1][:2]) / 2, zt - H / 2]
                self.contacts["red"] = C
                yaw = math.degrees(math.atan2(*(C[1] - C[0])[:2][::-1]))
                b = self.S.body["red"]; Rr = self.S.d.xmat[b].reshape(3, 3)
                dd = (yaw - math.degrees(math.atan2(Rr[1, 0], Rr[0, 0]))) % 45
                self.astra_log[-1].update(contact_sep_mm=float(np.linalg.norm((C[1] - C[0])[:2]) * 1000),
                                          off_flats_deg=float(min(dd, 45 - dd)))
        if want_place:
            p = js.get("place"); green = None
            if p is not None:
                uv = np.asarray(p, float) + o
                zt = self.top_z(obs, uv, 0.022)
                if zt is not None:
                    green = np.r_[self.on_plane(obs, uv, zt)[:2], zt - H / 2]
            if green is not None:
                self.last["green"] = green
            self.cache["green"] = (obs, green)
        if red is not None:
            self.last["red"] = red
        self._log("locate", k, obs, red if k == "red" else None, p_true)
        return red

    def relative(self, cam):
        rel_true = self.S.piece("red")[0] - self.S.piece("green")[0]
        obs = self.observe(cam)
        img = obs["rgb"]
        js = self.mark(img, ASTRA_SERVO.format(task=ASTRA_TASK, W=img.shape[1], H=img.shape[0]), ["held", "place"], check=False)
        est = None
        if js.get("held") is not None and js.get("place") is not None:
            uh, ug = np.asarray(js["held"], float), np.asarray(js["place"], float)
            zr = self.top_z(obs, uh)
            if zr is not None:
                zg = self.table_z(obs, ug, below=zr - H - 0.004)
                if zg is not None:
                    r = np.r_[self.on_plane(obs, uh, zr)[:2], zr - H / 2]
                    g = np.r_[self.on_plane(obs, ug, zg + H)[:2], zg + H / 2]
                    est = r - g
        self._log("relative", "red-green", obs, est, rel_true)
        return est


# ---------------------------------------------------------------- the program
class Program:
    def __init__(self, S, per, variant, fine_cam, iters=8, tol=0.0008):
        self.S, self.per, self.variant, self.fine = S, per, variant, fine_cam
        self.iters, self.tol = iters, tol                     # servo: iterations per height, stop when |error| < tol
        self.events = []

    def event(self, what, **kw):
        self.events.append(dict(t=float(self.S.d.time), what=what, **kw))
        self.S.timeline.append((float(self.S.d.time), "event", what))

    def grasp_yaw(self, red):
        """Fingers' closing direction: across the contact points the perception chose (Astra), else across the
        direction from the arm base. Of the two equivalent ones (yaw, yaw + pi) the nearer to the default."""
        y0 = math.atan2(*(red[:2] - self.S.base[:2])[::-1]) + math.pi / 2
        c = getattr(self.per, "contacts", {}).get("red")
        if c is None:
            return y0
        y = math.atan2(*(c[1] - c[0])[:2][::-1])
        return min((y, y + math.pi), key=lambda v: abs((v - y0 + math.pi) % (2 * math.pi) - math.pi))

    def run(self):
        S, per = self.S, self.per
        red, green = per.locate("red", "d415"), per.locate("green", "d415")
        if red is None or green is None:
            return "perception lost (scene camera)"
        yaw = self.grasp_yaw(red)
        R = down(yaw)
        S.move(red + [0, 0, 0.15], R, OPEN_W, 2.0, "above red")
        if self.fine == "L_wrist":
            r2 = per.locate("red", "L_wrist")
            if r2 is not None:
                red = r2
                if "red" in getattr(per, "contacts", {}):     # Astra's new contact points (other perceptions keep the yaw)
                    yaw = self.grasp_yaw(red); R = down(yaw)
                S.move(red + [0, 0, 0.15], R, OPEN_W, 0.5, "re-aim over red")
        g_xy = red[:2].copy()
        z_grip = red[2] + 0.002
        if self.variant == "precise3d":
            # the fingertips reach 15.7 mm below the TCP: at red's centre (+2 mm) they clear the table by ~1 mm only, and a
            # few mm of height error jams them on it (the fingers then shut during the lift, on red's rim or hole).
            # Grasp the top part instead: TCP 8 mm under the top face (fingertips ~6 mm above the table)
            z_grip = red[2] + H / 2 - 0.008
        S.move(np.r_[g_xy, z_grip + 0.04], R, OPEN_W, 1.0, "pregrasp")
        S.move(np.r_[g_xy, z_grip], R, OPEN_W, 0.6, "descend")
        red_before, tcp_err = S.piece("red")[0], S.tcp_now() - S.p_cmd
        S.move(np.r_[g_xy, z_grip], R, 0.0, 0.8, "close")
        S.hold(0.2)
        if self.variant == "precise3d":
            for attempt in range(2):                          # wait for the gripper to stop, check it holds a piece
                for _ in range(15):
                    w0 = S.finger_width(); S.hold(1 / 15)
                    if abs(S.finger_width() - w0) < 0.0003:
                        break
                if S.finger_width() < 0.065 or attempt == 1:
                    break
                self.event("gripper did not close (blocked): open, 6 mm higher, close again", width_mm=S.finger_width() * 1000)
                z_grip += 0.006
                S.move(np.r_[g_xy, z_grip], R, OPEN_W, 0.6)
                S.move(np.r_[g_xy, z_grip], R, 0.0, 0.8, "close again")
        self.event("diag grasp", tcp_err_mm=float(np.linalg.norm(tcp_err[:2]) * 1000),
                   red_moved_by_close_mm=float(np.linalg.norm((S.piece("red")[0] - red_before)[:2]) * 1000))
        if S.finger_width() < 0.01:
            self.event("missed (fingers closed on nothing)")
            return "missed"
        off = np.r_[g_xy, z_grip] - red                       # the piece-in-hand offset the program assumes
        seat = green[2] + H                                   # red's centre when it sits on green
        S.move(np.r_[g_xy, seat + 0.12 + off[2]], R, 0.0, 1.0, "lift")
        if S.finger_width() < 0.01:
            return "dropped"
        true_off = S.tcp_now() - S.piece("red")[0]
        self.event("diag in-hand", assumed_vs_true_offset_mm=float(np.linalg.norm((off - true_off)[:2]) * 1000))
        goal = np.r_[green[:2] + off[:2], seat + off[2]]
        S.move(goal + [0, 0, 0.10], R, 0.0, 2.0, "carry over green")
        if self.variant == "open":
            S.move(goal + [0, 0, 0.004], R, 0.0, 1.2, "set down")
            S.hold(0.2)
            self.event("diag before release", red_green_mm=float(np.linalg.norm((S.piece("red")[0] - S.piece("green")[0])[:2]) * 1000))
            rel = S.p_cmd.copy()
        elif self.variant == "precise":
            cam = self.fine
            corr = np.zeros(2)
            for dz in (0.03, 0.008):
                cmd = goal + np.r_[corr, dz]
                S.move(cmd, R, 0.0, 0.8, f"to {dz * 1000:.0f} mm above the seat")
                for it in range(self.iters):
                    S.hold(0.25)
                    e = per.relative(cam)
                    if e is None:
                        self.event("relative offset not seen", dz=dz, it=it)
                        break
                    self.event("servo", dz=dz, it=it, err_mm=float(np.linalg.norm(e[:2]) * 1000))
                    if np.linalg.norm(e[:2]) < self.tol:
                        break
                    corr = corr - 0.8 * e[:2]
                    S.move(goal + np.r_[corr, dz], R, 0.0, 0.3)
        if self.variant == "precise3d":
            # as precise, in 3D: the first measurement 10 cm above the (estimated) seat, before any descent, and the
            # height too - the gap between red's bottom and green's top measured in the same image (red centre -
            # green centre - H), corrected to the stage's dz; the scene camera's height of green is not trusted
            cam = self.fine
            corr = np.zeros(3)
            for dz in (0.10, 0.03, 0.008):
                S.move(goal + corr + [0, 0, dz], R, 0.0, 0.8, f"to {dz * 1000:.0f} mm above the seat")
                for it in range(self.iters):
                    S.hold(0.25)
                    e = per.relative(cam)
                    if e is None:
                        self.event("relative offset not seen", dz=dz, it=it)
                        break
                    ez = (e[2] - H) - dz                         # + : red is higher than wanted
                    self.event("servo", dz=dz, it=it, err_mm=float(np.linalg.norm(e[:2]) * 1000), gap_err_mm=float(ez * 1000))
                    if np.linalg.norm(e[:2]) < self.tol and abs(ez) < 0.002:
                        break
                    step = -0.8 * np.r_[e[:2], ez]
                    corr = corr + step
                    S.move(goal + corr + [0, 0, dz], R, 0.0, max(0.3, float(np.linalg.norm(step)) / 0.08))
        if self.variant in ("precise", "precise3d"):
            # lower 1 mm at a time until the hand feels red resting on green (> 1.5 N), unload, open
            cmd = S.p_cmd.copy()
            for i in range(25):
                cmd[2] -= 0.001
                S.move(cmd, R, 0.0, 0.1, "set down" if i == 0 else None)
                S.hold(0.05)
                if S.contact_force("red", "green") > 1.5:
                    self.event("contact", steps=i + 1)
                    break
            rel = cmd + [0, 0, 0.001]
            S.move(rel, R, 0.0, 0.2, "unload")
            self.event("diag before release", red_green_mm=float(np.linalg.norm((S.piece("red")[0] - S.piece("green")[0])[:2]) * 1000))
        S.move(rel, R, OPEN_W, 0.6, "release")
        S.move(rel + [0, 0, 0.10], R, OPEN_W, 1.0, "retreat")
        S.hold(1.0)
        return "placed"


def judge(S):
    (r, tilt), (g, _) = S.piece("red"), S.piece("green")
    off = float(np.linalg.norm((r - g)[:2])); dz = float((r[2] - H / 2) - (g[2] + H / 2))
    ok = off < 0.02 and abs(dz) < 0.008 and tilt < 15
    return dict(placed=bool(ok), offset_mm=off * 1000, dz_mm=dz * 1000, tilt_deg=tilt)


def run_job(job):
    seed, idx, conds, out, save = job
    L = sample_layout(seed, idx)
    if L is None:
        return [dict(layout=idx, result="layout rejected")]
    rows = []
    for cond in conds:
        pkind, variant = cond.split(":")
        uncal = pkind.endswith("_uncal")
        fine = "L_wrist" if pkind.startswith(("wrist", "astra")) else "d415"
        S = Sim()
        apply_layout(S, L, uncal)
        rng = np.random.default_rng([seed, idx, 5])
        if pkind.startswith("astra"):                       # Astra marks the points (fewer, looser servo steps: each is a call)
            per = AstraPerception(S, "cam", rng, out, f"{idx:04d}_{cond.replace(':', '_')}")
            prog = Program(S, per, variant, fine, iters=4, tol=0.0015)
        else:
            per = Perception(S, "gt" if pkind == "gt" else "cam", rng)
            prog = Program(S, per, variant, fine)
        t0 = time.time()
        try:
            r = prog.run()
        except Exception as e:                                # a crash is a result too
            r = f"crash: {e!r}"[:200]
        res = judge(S)
        row = dict(layout=idx, cond=cond, program=r, result="success" if res["placed"] else "fail", **res,
                   sim_s=round(float(S.d.time), 1), wall_s=round(time.time() - t0, 1),
                   events=prog.events[-20:], perception=per.log)
        if isinstance(per, AstraPerception):
            row.update(astra_calls=per.calls, astra_cost=round(per.cost, 3), astra=per.astra_log)
        rows.append(row)
        if save:
            tag = f"trace_{idx:04d}_{cond.replace(':', '_')}"
            np.savez_compressed(Path(out) / f"{tag}.npz", t=np.array([x[0] for x in S.rec]), qpos=np.array([x[1] for x in S.rec]))
            json.dump(dict(row=row, layout=L, timeline=S.timeline), open(Path(out) / f"{tag}.json", "w"),
                      default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
        per.close()
    with open(Path(out) / "runs.jsonl", "a") as f:
        for r in rows:
            f.write(json.dumps(r, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)) + "\n")
    return rows


def summarize(rows, conds, out):
    lines = ["| condition | success | failures | placed offset mm (median / 90th / max) | locate error mm (median / 90th) | relative error mm (median / 90th) | sim s |",
             "|---|---|---|---|---|---|---|"]
    for c in conds:
        r = [x for x in rows if x.get("cond") == c]
        if not r:
            continue
        ok = [x for x in r if x["result"] == "success"]
        off = np.array([x["offset_mm"] for x in r if x["offset_mm"] < 100])
        reasons = {}
        for x in r:
            if x["result"] != "success":
                k = x["program"] if x["program"] != "placed" else f"placed off ({x['offset_mm']:.0f} mm)" if x["offset_mm"] >= 20 else "placed: tilt/height"
                k = k if not k.startswith("placed off") else "placed > 2 cm off"
                reasons[k] = reasons.get(k, 0) + 1
        le = [L["err_mm"] for x in r for L in x["perception"] if L["what"] == "locate" and L["err_mm"] is not None]
        re_ = [L["err_mm"] for x in r for L in x["perception"] if L["what"] == "relative" and L["err_mm"] is not None]
        f = lambda v: f"{np.median(v):.1f} / {np.percentile(v, 90):.1f}" if len(v) else "-"
        lines.append(f"| {c} | {len(ok)}/{len(r)} | {'; '.join(f'{k} x{v}' for k, v in reasons.items()) or '-'} | "
                     f"{np.median(off):.1f} / {np.percentile(off, 90):.1f} / {off.max():.1f} | {f(le)} | {f(re_)} | "
                     f"{np.median([x['sim_s'] for x in r]):.0f} |")
    txt = "\n".join(lines)
    (Path(out) / "summary.md").write_text(txt + "\n")
    return txt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--layouts", type=int, default=24)
    ap.add_argument("--conditions", nargs="+", default=["gt:open", "gt:precise"])
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--save-traces", action="store_true")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in open(a.out / "runs.jsonl")] if (a.out / "runs.jsonl").exists() else []
    done = {r["layout"] for r in rows if {x.get("cond") for x in rows if x["layout"] == r["layout"]} >= set(a.conditions)}
    rows = [r for r in rows if r["layout"] in done]
    jobs = [(a.seed, i, a.conditions, str(a.out), a.save_traces) for i in (a.only or range(a.layouts)) if i not in done]
    print(f"{len(done)} layouts done, {len(jobs)} to run", flush=True)
    t0 = time.time()
    with Pool(max(1, min(a.workers, len(jobs)))) as pool:
        for r in pool.imap_unordered(run_job, jobs):
            rows += r
            print(f"layout {r[0]['layout']} ({time.time() - t0:.0f}s): " +
                  ", ".join(f"{x.get('cond')}:{x['result']}{'' if x.get('offset_mm') is None else ' %.1fmm' % x['offset_mm']}" for x in r), flush=True)
    print(summarize(rows, a.conditions, a.out))


if __name__ == "__main__":
    main()
