"""The bimanual X5 robot in MuJoCo (assets/x5/generated/dual_x5.xml, made by x5/build.py). Same interface as
x5/sim_maniskill.py: reset(), step(target16), q(), tcp(side), cube(), render(cam). Project .venv (mujoco only)."""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MUJOCO_GL", "egl")
import mujoco  # noqa: E402

from x5 import spec as S  # noqa: E402


def _rotvec(R):
    c = float(np.clip((np.trace(R) - 1) / 2, -1, 1)); a = np.arccos(c)
    if a < 1e-9:
        return np.zeros(3)
    if a > np.pi - 1e-4:
        M = (R + np.eye(3)) / 2
        k = int(np.argmax(np.diag(M))); v = M[:, k] / np.sqrt(max(M[k, k], 1e-12))
        return v * a
    return np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * np.sin(a)) * a


class Sim:
    name = "mujoco"

    def __init__(self):
        self.m = m = mujoco.MjModel.from_xml_path(str(S.GEN / "dual_x5.xml"))
        self.d = mujoco.MjData(m)
        self.names = S.joint_names()
        self.qadr = np.array([m.jnt_qposadr[m.joint(n).id] for n in self.names])
        self.vadr = np.array([m.jnt_dofadr[m.joint(n).id] for n in self.names])
        self.act = np.array([m.actuator(n).id for n in self.names])
        self.sub = int(round(S.DT / m.opt.timestep)) * S.CTRL_EVERY
        self.site = {s: m.site(f"{s}_tcp").id for s in S.SIDES}
        self.cube_b = m.body("cube").id
        self.r = None
        self.reset()

    def home(self):
        q = np.zeros(16)
        q[[6, 7, 14, 15]] = S.GRIP_OPEN
        return q

    def reset(self):
        mujoco.mj_resetData(self.m, self.d)
        q = self.home()
        self.d.qpos[self.qadr] = q; self.d.ctrl[self.act] = q
        mujoco.mj_forward(self.m, self.d)
        self.t = 0.0
        return q

    def step(self, target):
        self.d.ctrl[self.act] = np.asarray(target, float)
        mujoco.mj_step(self.m, self.d, self.sub)
        self.t += S.DT * S.CTRL_EVERY

    def q(self):
        return self.d.qpos[self.qadr].copy()

    def tcp(self, side):
        i = self.site[side]
        return self.d.site_xpos[i].copy(), self.d.site_xmat[i].reshape(3, 3).copy()

    def cube(self):
        return self.d.xpos[self.cube_b].copy(), self.d.xmat[self.cube_b].reshape(3, 3).copy()

    def robot_force_on_cube(self):
        f, f6 = 0.0, np.zeros(6)
        for i in range(self.d.ncon):
            c = self.d.contact[i]
            b1, b2 = self.m.geom_bodyid[c.geom1], self.m.geom_bodyid[c.geom2]
            if self.cube_b in (b1, b2) and self.m.body(b1 if b2 == self.cube_b else b2).name[:2] in ("L_", "R_"):
                mujoco.mj_contactForce(self.m, self.d, i, f6); f += abs(f6[0])
        return f

    def render(self, cam="head"):
        c = S.HEAD_CAM if cam == "head" else S.SIDE_CAM
        if self.r is None:
            self.r = mujoco.Renderer(self.m, c["height"], c["width"])
        self.r.update_scene(self.d, camera=cam)
        return self.r.render().copy()

    # ---- kinematics on the model (for planning the test motions; both simulators then get the same joint targets)
    def ik(self, side, p, R, q0, iters=200):
        m, dk = self.m, mujoco.MjData(self.m)
        idx = [self.names.index(f"{side}_{j}") for j in S.ARM_JOINTS]
        qa, va = self.qadr[idx], self.vadr[idx]
        q = np.asarray(q0, float).copy()
        jp, jr = np.zeros((3, m.nv)), np.zeros((3, m.nv))
        for _ in range(iters):
            dk.qpos[:] = self.d.qpos; dk.qpos[qa] = q
            mujoco.mj_kinematics(m, dk); mujoco.mj_comPos(m, dk)
            x = dk.site_xpos[self.site[side]]; Rc = dk.site_xmat[self.site[side]].reshape(3, 3)
            ep = p - x
            w = _rotvec(R @ Rc.T)                                  # (the true angle: a half turn is not "no error")
            if np.linalg.norm(ep) < 1e-4 and np.linalg.norm(w) < 1e-3:
                break
            mujoco.mj_jacSite(m, dk, jp, jr, self.site[side])
            J = np.vstack([jp[:, va], 0.2 * jr[:, va]]); e = np.r_[ep, 0.2 * w]
            dq = J.T @ np.linalg.solve(J @ J.T + 1e-4 * np.eye(6), e)
            s = np.abs(dq).max()
            q = q + (dq if s < 0.2 else dq * 0.2 / s)
        return q, float(np.linalg.norm(ep)), float(np.linalg.norm(w))
