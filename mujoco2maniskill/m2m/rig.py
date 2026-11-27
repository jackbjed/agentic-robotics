"""The rig in ManiSkill: what the project's simulation class (bimanual/core.BiCore, grippers.GripperSim) gives the robot
API, for the one-armed scene, on the ManiSkill environment (m2m/env.py). Planning is the project's (straight lines of
the tool point by damped-least-squares IK, a joint-space way for big turns, the rig's safety limits); executing is
ManiSkill's: joint position drives followed at CTRL_HZ, contact forces from PhysX.

Not here (the real rig has none of it either): collision checks of a planned path against the objects.
Metres, radians, table frame."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import torch

from . import env as E
from .kin import Kin, expm, rotvec

MIN_Z = 0.275                                                 # m: the D415s see nothing nearer (core.MIN_Z)
FORCE_NOISE = 0.5                                             # N per axis (core.FORCE_NOISE)
GUARD_N, SAFETY_N = 4.0, 40.0
V_SLOW, V_FREE, W_TURN = 0.04, 0.15, math.radians(45)
LOW_Z = 0.30
OPEN_W = 0.04
REC_FPS = 15
D415_BASELINE = 0.055


class Stop(Exception):
    """A move the arm cannot make: nothing has moved."""


class Rig:
    HANDS = "grippers"
    only = "L"

    def __init__(self, spec, spec_dir=None, seed=0, record=False, depth_noise_px=0.0, shader="default", settle=0.5, min_depth=MIN_Z):
        import gymnasium as gym
        self.spec = spec
        self.env = gym.make("M2M-Scene-v1", scene_spec=spec, scene_dir=spec_dir, obs_mode="none", control_mode="pd_joint_pos",
                            sim_backend="cpu", render_backend="gpu", render_mode="rgb_array" if record else None,
                            shader=shader, record_camera=bool(record))
        self.env.reset(seed=int(seed))
        self.U = U = self.env.unwrapped
        self.robot = U.agent.robot
        a = spec["arm"]
        yaw = 2.0 * math.atan2(a["base_quat"][3], a["base_quat"][0])
        self.kin = Kin(a["base_pos"], yaw, a["home_q"], a["lo"], a["hi"])
        self.home = np.asarray(a["home_q"], float)
        self.q_cmd, self.bias, self.g_cmd = self.home.copy(), np.zeros(7), OPEN_W
        self.dt = 1.0 / E.CTRL_HZ
        self.t = 0.0
        self.table_z = float(spec.get("table_z", 0.0))
        self.rng = np.random.default_rng(int(seed))                 # (IK restarts)
        self.frng = np.random.default_rng(2000 + int(seed))         # (the force estimate's noise)
        self.drng = np.random.default_rng(3000 + int(seed))         # (depth noise, if asked for)
        self.depth_noise_px = float(depth_noise_px)
        self.min_depth = float(min_depth)                           # (m: nothing nearer has depth; 0 = ideal depth, no near limit)
        self.record, self.frames, self.t_rec = bool(record), [], -1.0
        self.last_path = "line"
        self.links = [l for l in self.robot.get_links() if l.name in E.ROBOT_LINKS_FELT]
        self.link_names = [l.name for l in self.links]
        self.safety_stops = 0
        if settle:
            rec, self.record = self.record, False
            self.run(settle)
            self.record = rec

    # ------------------------------------------------------------------ stepping
    def step(self, n=1):
        act = torch.tensor(np.r_[self.q_cmd + self.bias, self.g_cmd], dtype=torch.float32)[None]
        for _ in range(int(n)):
            self.env.step(act)
            self.t += self.dt
            if self.record and self.t - self.t_rec >= 1.0 / REC_FPS - 1e-9:
                self.t_rec = self.t
                fr = self.env.render()
                self.frames.append(np.ascontiguousarray(fr[0].cpu().numpy() if hasattr(fr, "cpu") else np.asarray(fr)[0]))

    def run(self, s):
        self.step(max(int(round(float(s) / self.dt)), 0))

    def write_video(self, path):
        import cv2
        if not self.frames:
            return None
        h, w = self.frames[0].shape[:2]
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), REC_FPS, (w, h))
        for f in self.frames:
            vw.write(f[..., ::-1])
        vw.release()
        return str(path)

    # ------------------------------------------------------------------ what the arm measures
    def q(self):
        return self.robot.get_qpos()[0].cpu().numpy().astype(float)[:7]

    def tool_pose(self):
        T = self.U.agent.tcp.pose.to_transformation_matrix()[0].cpu().numpy().astype(float)
        return T[:3, 3].copy(), T[:3, :3].copy()

    def net_force(self):
        """The net contact force on the arm and hand from everything else (N, table frame): TRUE."""
        f = self.robot.get_net_contact_forces(self.link_names)[0].cpu().numpy().astype(float)
        return f.sum(0)

    def force_estimate(self):
        return self.net_force() + self.frng.normal(0, FORCE_NOISE, 3)

    def width(self):
        q = self.robot.get_qpos()[0].cpu().numpy()
        return float(q[7] + q[8])

    # ------------------------------------------------------------------ cameras
    def observe(self, which):
        """A frame: rgb (H, W, 3 uint8), depth (m, nan = none), K, and the camera's pose as the robot BELIEVES it
        (R camera to table with OpenCV axes, eye): the true pose differs by the episode's calibration error."""
        U = self.U
        U.scene.update_render(update_sensors=True, update_human_render_cameras=False)
        s = U._sensors[which]
        s.capture()
        col = s.camera.get_picture("Color"); pos = s.camera.get_picture("Position")
        col = (col[0] if isinstance(col, (list, tuple)) else col); pos = (pos[0] if isinstance(pos, (list, tuple)) else pos)
        col = col.cpu().numpy(); pos = pos.cpu().numpy()
        col = col[0] if col.ndim == 4 else col; pos = pos[0] if pos.ndim == 4 else pos
        rgb = np.ascontiguousarray((np.clip(col[..., :3], 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
        z = (-pos[..., 2]).astype(np.float64)
        f = float(s.camera.get_intrinsic_matrix()[0].cpu().numpy()[0, 0]) if hasattr(s.camera.get_intrinsic_matrix(), "cpu") else float(np.asarray(s.camera.get_intrinsic_matrix())[0][0, 0])
        if self.depth_noise_px > 0:
            z = z + self.drng.normal(0.0, 1.0, z.shape) * self.depth_noise_px * z * z / (f * D415_BASELINE)
        depth = np.where((z >= max(self.min_depth, 1e-4)) & (z < 10.0), z, np.nan)
        c = self.spec["cameras"][which]; n = c["nominal_in_body"]
        if which == "left_wrist":
            T = U.agent.hand.pose.to_transformation_matrix()[0].cpu().numpy().astype(float)
            Rb, pb = T[:3, :3], T[:3, 3]
        else:
            b = next(x for x in self.spec["bodies"] if x["name"] == c["body"])
            Rb, pb = E.quat_R(b["quat"]), np.asarray(b["pos"], float)
        return dict(camera=which, rgb=rgb, depth=depth, K=(E.W, E.H, f, f, E.W / 2, E.H / 2),
                    eye=pb + Rb @ np.asarray(n["eye"], float), R=Rb @ np.asarray(n["R"], float), t=float(self.t))

    # ------------------------------------------------------------------ planning (core.BiCore's)
    def _follow(self, poses):
        Q = [self.q_cmd.copy()]
        for k, (p, R) in enumerate(poses):
            q, ep, er = self.kin.ik(p, R, Q[-1])
            if ep > 0.002 or er > math.radians(1.5):
                raise Stop(f"out of the arm's reach ({ep * 1000:.0f} mm / {math.degrees(er):.0f} deg short at "
                           f"{100 * (k + 1) / len(poses):.0f}% of the way)")
            if np.abs(q - Q[-1]).max() > 0.2:
                raise Stop("the arm would have to swing its joints around on the way (a wrist flip): try another orientation, "
                           "a smaller step, or go in two moves")
            Q.append(q)
        return np.array(Q)

    def plan(self, p1, R1):
        p0, R0 = self.kin.fk(self.q_cmd)
        dist = float(np.linalg.norm(p1 - p0)); w = rotvec(R0.T @ R1); ang = float(np.linalg.norm(w))
        n = max(int(math.ceil(dist / 0.005)), int(math.ceil(ang / math.radians(2))), 1)
        return self._follow([(p0 + (p1 - p0) * i / n, R0 @ expm(w * i / n)) for i in range(1, n + 1)]), dist, ang

    def _check_rig(self, Q):
        why = self.kin.why_not(Q)
        if why:
            e = Stop(f"the arm's safety limits refuse that move ({why}; m, rad): go another way, or choose another pose")
            e.rig = True
            raise e

    def joint_path(self, p1, R1):
        sols = self.kin.solutions(p1, R1, self.q_cmd, self.rng)
        if not sols:
            raise Stop("out of the arm's reach (no joint solution for that pose)")
        first = None
        for q1 in sols:
            n = max(int(math.ceil(np.abs(q1 - self.q_cmd).max() / 0.01)), 2)
            Q = np.array([self.q_cmd + (q1 - self.q_cmd) * i / n for i in range(n + 1)])
            lim = self.kin.why_not(Q)
            if not lim:
                return Q
            first = first or f"is refused by the arm's safety limits ({lim})"
        raise Stop(f"out of the arm's reach on a straight line, and the way round by the joints {first}: "
                   f"go up first, move in smaller steps, or choose another orientation")

    # ------------------------------------------------------------------ executing
    def _execute(self, Q, T, guarded, u=None):
        """Play joint waypoints Q over T seconds. -> stop reason or None."""
        f0 = self.net_force()
        n = max(int(round(T / self.dt)), 1)
        s = np.linspace(0, len(Q) - 1, n + 1)[1:]
        stop = None
        for si in s:
            k = min(int(si), len(Q) - 2); f = si - k
            self.q_cmd = Q[k] * (1 - f) + Q[k + 1] * f
            self.step()
            net = self.net_force(); fa = float(np.linalg.norm(net))
            if fa > max(SAFETY_N, float(np.linalg.norm(f0)) + 5):
                stop = f"SAFETY STOP: {fa:.0f} N on the left arm"
                self.safety_stops += 1
            elif guarded:
                dF = net - f0
                if (float(dF @ -u) if u is not None else fa - float(np.linalg.norm(f0))) > GUARD_N:
                    stop = "contact"
            if stop:
                break
        if stop:
            self.q_cmd = self.q(); self.bias = np.zeros(7)
            self.run(0.3)
            return stop
        self.run(0.2)
        for _ in range(3):                                          # settle: the drives' remaining error taken out, as the
            if float(np.linalg.norm(self.net_force())) > 1.0:       # real arm's server does; not while something pushes back
                break
            self.bias = np.clip(self.bias + (self.q_cmd - self.q()), -0.05, 0.05)
            self.run(0.15)
        return stop

    def move(self, p1, R1, guarded=False, v=None):
        p1 = np.asarray(p1, float)
        self.last_path = "line"
        p0, R0 = self.kin.fk(self.q_cmd)
        Q = None
        if not guarded and float(np.linalg.norm(rotvec(R0.T @ R1))) > math.radians(40):
            try:
                Q = self.joint_path(p1, R1); self.last_path = "joint"
                dist, ang = float(np.linalg.norm(p1 - p0)), float(np.abs(Q[-1] - Q[0]).max()) * 1.2
            except Stop:
                Q = None
        if Q is None:
            try:
                Q, dist, ang = self.plan(p1, R1)
                self._check_rig(Q)
            except Stop as line:
                if guarded:
                    raise
                try:
                    Q = self.joint_path(p1, R1); self.last_path = "joint"
                except Stop:
                    if getattr(line, "rig", False):
                        raise line from None
                    raise
                dist, ang = float(np.linalg.norm(p1 - p0)), float(np.abs(Q[-1] - Q[0]).max()) * 1.2
        low = min(p0[2], p1[2]) < LOW_Z
        v = v or (V_SLOW if guarded or low else V_FREE)
        u = (p1 - p0) / max(float(np.linalg.norm(p1 - p0)), 1e-9) if np.linalg.norm(p1 - p0) > 0.001 else None
        return self._execute(Q, max(dist / v, ang / W_TURN, 0.3), guarded, u)

    def turn(self, centre, axis, angle_deg, w=math.radians(30)):
        axis = np.asarray(axis, float); axis = axis / max(np.linalg.norm(axis), 1e-9); c = np.asarray(centre, float)
        p0, R0 = self.kin.fk(self.q_cmd)
        th = math.radians(angle_deg); n = max(int(math.ceil(abs(th) / math.radians(2))), 1)
        poses = []
        for i in range(1, n + 1):
            Ei = expm(axis * th * i / n); poses.append((c + Ei @ (p0 - c), Ei @ R0))
        Q = self._follow(poses)
        self._check_rig(Q)
        return self._execute(Q, max(abs(th) / w, 0.3), False)

    def go_home(self):
        n = max(int(math.ceil(np.abs(self.home - self.q_cmd).max() / 0.01)), 2)
        Q = np.array([self.q_cmd + (self.home - self.q_cmd) * i / n for i in range(n + 1)])
        why = self.kin.why_not(Q)
        if why:
            raise Stop(f"the way home by the joints is refused by the arm's safety limits ({why}): move clear first")
        self.last_path = "joint"
        return self._execute(Q, max(float(np.abs(Q[-1] - Q[0]).max()) * 1.2 / W_TURN, 0.3), False)

    def set_gripper(self, width, duration=0.8):
        g0, g1 = self.g_cmd, float(np.clip(width / 2, 0.0, OPEN_W))
        n = max(int(round(duration / self.dt)), 1)
        for i in range(1, n + 1):
            self.g_cmd = g0 + (g1 - g0) * i / n
            self.step()
        self.run(0.4)
        return self.width()

    # ------------------------------------------------------------------ the TRUE state (judges, logs: never the program)
    def names(self):
        """The scene's bodies by name (those fixed to another body too)."""
        return list(self.U.bodies) + list(self.U.attached)

    def body_pose(self, name):
        if name in self.U.attached:                                 # (fixed to another body)
            root, rel = self.U.attached[name]
            T = self.U.bodies[root].pose.to_transformation_matrix()[0].cpu().numpy().astype(float) @ rel.to_transformation_matrix()
            return T[:3, 3].copy(), T[:3, :3].copy()
        a = self.U.bodies[name]
        T = a.pose.to_transformation_matrix()[0].cpu().numpy().astype(float)
        return T[:3, 3].copy(), T[:3, :3].copy()

    def body_speed(self, name):
        a = self.U.bodies[self.U.attached[name][0] if name in self.U.attached else name]
        try:
            return float(np.linalg.norm(a.get_linear_velocity()[0].cpu().numpy()))
        except Exception:
            return 0.0

    def touching(self, name):
        """({"left": parts of the arm} touching that body with force, names of the other bodies touching it)."""
        ent = {o.name for o in self.U.bodies[self.U.attached[name][0] if name in self.U.attached else name]._objs}
        parts, other = set(), set()
        for c in self.U.scene.px.get_contacts():
            a, b = c.bodies[0].entity.name, c.bodies[1].entity.name
            if a not in ent and b not in ent:
                continue
            if not any(float(np.linalg.norm(p.impulse)) > 1e-7 for p in c.points):
                continue
            o = b if a in ent else a
            if o.startswith("panda_"):
                parts.add("finger A" if o == "panda_leftfinger" else "finger B" if o == "panda_rightfinger" else "arm")
            else:
                other.add(o.split("_", 1)[1] if o.startswith("scene-") else o)
        return ({"left": sorted(parts)} if parts else {}), sorted(other)

    def close(self):
        try:
            self.env.close()
        except Exception:
            pass
