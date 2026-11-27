"""The arm's kinematics and safety limits on the robot's side (what the real arm's planner and server know): the
project's own plain-numpy Panda model (vendor/panda_twin/code/real/panda_fk.py: imported by file, unchanged), with the
rig's limits (rig.json). Inverse kinematics as the project's simulation does it (core.BiCore.ik: damped least squares,
pulled to the home posture). Metres and radians, table frame."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[2]
FK_FILE = PROJECT / "src" / "agentic_robotics" / "vendor" / "panda_twin" / "code" / "real" / "panda_fk.py"


def _load_fk():
    spec = importlib.util.spec_from_file_location("_m2m_panda_fk", FK_FILE)
    fk = importlib.util.module_from_spec(spec); spec.loader.exec_module(fk)
    return fk


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


def grip_R(approach, closing):
    """(project: bimanual/grippers.grip_R) The tool's orientation from where the fingers point and the line they close
    along: columns x, y (closing), z (approach)."""
    z = np.asarray(approach, float); z = z / np.linalg.norm(z)
    y = np.asarray(closing, float); y = y - (y @ z) * z
    if np.linalg.norm(y) < 1e-6:
        raise ValueError("the closing direction must not be along the approach direction")
    y = y / np.linalg.norm(y)
    return np.column_stack([np.cross(y, z), y, z])


class Kin:
    """FK, IK and the rig's limits for the left arm. base: the arm's link0 in the table frame (pos, yaw)."""

    def __init__(self, base_pos, base_yaw, home_q, lo=None, hi=None, tcp_z=0.1808, fingertip=0.0157):
        self.fk_mod = fk = _load_fk()
        rig = fk.load_rig()
        rig = dict(rig, base_world=dict(pos=list(map(float, base_pos)), yaw=float(base_yaw)),
                   tool=dict(adapter_dz=0.0, tcp_z=float(tcp_z), fingertip=float(fingertip)))
        self.kin = fk.Kin(rig, {})
        self.limits = fk.Limits(rig, self.kin)
        self.home = np.asarray(home_q, float)
        # (the one-armed scene's joint ranges: the model's, within the rig's: core.BiCore.__init__)
        self.lo = np.maximum(np.asarray(lo, float), self.limits.lo) if lo is not None else self.limits.lo.copy()
        self.hi = np.minimum(np.asarray(hi, float), self.limits.hi) if hi is not None else self.limits.hi.copy()

    def fk(self, q):
        T = self.kin.tcp_world(np.asarray(q, float))
        return T[:3, 3].copy(), T[:3, :3].copy()

    def jac(self, q):
        """The tool point's geometric Jacobian (6 x 7: linear, angular), table frame."""
        Fr = [self.kin.T_wb @ F for F in self.kin.frames(q)]
        p = self.kin.tcp_world(q)[:3, 3]
        J = np.zeros((6, 7))
        for i in range(7):
            z = Fr[i][:3, 2]; o = Fr[i][:3, 3]
            J[:3, i] = np.cross(z, p - o); J[3:, i] = z
        return J

    def ik(self, p, R, q0, iters=150):
        q = np.asarray(q0, float).copy(); wr = 0.15
        for _ in range(iters):
            x, Rc = self.fk(q)
            ep, er = p - x, rotvec(R @ Rc.T)
            if np.linalg.norm(ep) < 2e-4 and np.linalg.norm(er) < 2e-3:
                break
            Jf = self.jac(q)
            J = np.vstack([Jf[:3], wr * Jf[3:]]); e = np.r_[ep, wr * er]
            JJ = J @ J.T + 1e-4 * np.eye(6)
            dq = J.T @ np.linalg.solve(JJ, e)
            dq += (np.eye(7) - J.T @ np.linalg.solve(JJ, J)) @ (0.05 * (self.home - q))
            s = np.abs(dq).max()
            if s > 0.15:
                dq *= 0.15 / s
            q = np.clip(q + dq, self.lo, self.hi)
        x, Rc = self.fk(q)
        return q, float(np.linalg.norm(p - x)), float(np.linalg.norm(rotvec(R @ Rc.T)))

    def solutions(self, p, R, q_now, rng, tries=60):
        """(core.BiCore.solutions) IK with restarts, the best first: far from the joint limits, near the home posture,
        the elbow on the arm's own side."""
        sols = []
        for q0 in [self.home, q_now] + [rng.uniform(self.lo, self.hi) for _ in range(tries)]:
            q, ep, er = self.ik(p, R, q0, iters=300)
            if ep < 0.001 and er < math.radians(0.6) and not any(np.abs(q - o).max() < 0.05 for o, _ in sols):
                elbow = (self.kin.T_wb @ self.kin.frames(q)[3])[:3, 3]
                inward = float(-(elbow[1] - self.kin.T_wb[1, 3]))       # (the left arm: the other arm is at -y)
                sc = (float(np.min(np.minimum(q - self.lo, self.hi - q) / (self.hi - self.lo)))
                      - 0.05 * float(np.abs(q - self.home).max()) - 1.0 * max(inward - 0.03, 0.0))
                sols.append((q, sc))
                if len(sols) >= 8:
                    break
        return [q for q, _ in sorted(sols, key=lambda t: -t[1])]

    def why_not(self, Q):
        """Why the rig's limits refuse joint waypoints Q (None: they do not). Not when the arm is already outside
        them: a way out. (core.BiCore._rig_why)"""
        if self.limits.check_q(Q[0]):
            return None
        return next((w for w in (self.limits.check_q(q) for q in list(Q[3::3]) + [Q[-1]]) if w), None)
