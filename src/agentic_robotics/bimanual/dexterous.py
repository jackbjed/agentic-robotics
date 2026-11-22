"""sim_bimanual_dexterous: a Sharpa Wave hand (22 joints, position servos) on each Panda (docs/bimanual.md).
- the pose commanded is the palm's centre; orientation: where the straight fingers point (the hand's z) and where the
  palm faces (x); the right hand's thumb is on its +y side, the left hand's on -y;
- set_hand(arm, {joint: rad}): hand joint targets, ramped, optionally each finger stopping at a contact force;
- finger_ik(arm, finger, point): the finger's joints that bring its tip's pad centre to a point.
"""
from __future__ import annotations

import mujoco
import numpy as np

from .core import SAFETY_N, BiCore

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
JOINTS = ("thumb_CMC_FE", "thumb_CMC_AA", "thumb_MCP_FE", "thumb_MCP_AA", "thumb_IP",
          "index_MCP_FE", "index_MCP_AA", "index_PIP", "index_DIP",
          "middle_MCP_FE", "middle_MCP_AA", "middle_PIP", "middle_DIP",
          "ring_MCP_FE", "ring_MCP_AA", "ring_PIP", "ring_DIP",
          "pinky_CMC", "pinky_MCP_FE", "pinky_MCP_AA", "pinky_PIP", "pinky_DIP")


def hand_R(fingers, palm):
    """Hand orientation from where the straight fingers point and where the palm faces. Columns: palm (x), z cross x
    (y: the RIGHT hand's thumb side; the left hand's thumb is on -y), fingers (z)."""
    z = np.asarray(fingers, float); z = z / max(np.linalg.norm(z), 1e-9)
    x = np.asarray(palm, float); x = x - z * float(x @ z)
    if np.linalg.norm(x) < 1e-6:
        raise ValueError("the palm direction is parallel to the fingers' direction")
    x /= np.linalg.norm(x)
    return np.column_stack([x, np.cross(z, x), z])


class DexterousSim(BiCore):
    NAME = "sim_bimanual_dexterous"
    HANDS = "dexterous"

    def _init_arm(self, A):
        m, P, side = self.m, A.P, A.side
        A.hj = {n: m.joint(f"{P}_{side}_{n}").id for n in JOINTS}
        A.ha = np.array([m.actuator(f"{P}_{side}_{n}_ctrl").id for n in JOINTS])
        A.hq = np.array([m.jnt_qposadr[A.hj[n]] for n in JOINTS])
        A.palm = m.site(f"{P}_palm").id; A.tips = {f: m.site(f"{P}_tip_{f}").id for f in FINGERS}
        hroot = m.body(f"{P}_{side}_hand_C_MC").id
        for b in range(m.nbody):
            nm = m.body(b).name
            if nm.startswith(f"{P}_{side}_"):
                f = next((f for f in FINGERS if f"_{f}_" in nm), None)
                self.part[b] = (P, "palm" if b == hroot or f is None else
                                f"{f}:{'tip' if nm.endswith('DP') else 'base' if nm.endswith(('MC', 'CMC_VL')) else 'link'}")
            elif nm == f"{P}_hand" or nm.startswith(f"{P}_link"):
                self.part[b] = (P, "arm")

    def _reset_hand(self, A):
        self.d.qpos[A.hq] = 0.0; self.d.ctrl[A.ha] = 0.0; A.h_cmd = np.zeros(len(JOINTS))

    def hand_range(self, P, n):
        return self.m.jnt_range[self.arms[P].hj[n]]

    def set_hand(self, P, targets: dict, duration=0.8, guard_n=None):
        """Hand joint targets (rad), ramped over duration. guard_n: each finger stops where it is as soon as the contact
        force on it exceeds guard_n newtons (the others go on). -> the fingers stopped that way."""
        A = self.arms[P]
        h0 = A.h_cmd.copy(); h1 = h0.copy()
        for k, v in targets.items():
            lo, hi = self.hand_range(P, k); h1[JOINTS.index(k)] = float(np.clip(v, lo, hi))
        n = max(int(round(duration / self.m.opt.timestep)), 1)
        every = max(int(round(0.005 / self.m.opt.timestep)), 1)
        active = np.ones(len(JOINTS), bool); stopped = []
        step = (h1 - h0) / n
        for i in range(1, n + 1):
            A.h_cmd = A.h_cmd + step * active; self.d.ctrl[A.ha] = A.h_cmd
            self.step()
            if i % every == 0:
                parts, net = self.contacts()[P]
                if np.linalg.norm(net) > SAFETY_N:
                    break
                if guard_n is not None:
                    for f in FINGERS:
                        if f not in stopped and sum(v for k, v in parts.items() if k.startswith(f + ":")) > guard_n:
                            stopped.append(f)
                            active[[j for j, nme in enumerate(JOINTS) if nme.startswith(f + "_")]] = False
        self.run(0.4)
        return stopped

    def finger_ik(self, P, f, target, h0=None, iters=200):
        """-> ({joint: rad} for finger f that bring its tip to target, the distance left (m)). Damped least squares
        from the finger's commanded joints (h0), drawn towards a half-bent rest pose; where that ends more than 2 mm
        short (a local minimum, or out of reach), also from the rest pose, the open hand and mid-range: the nearest."""
        A, m, dk = self.arms[P], self.m, self.dk
        names = [n for n in JOINTS if n.startswith(f + "_")]
        ids = [A.hj[n] for n in names]
        qa = np.array([m.jnt_qposadr[j] for j in ids]); va = np.array([m.jnt_dofadr[j] for j in ids])
        lo, hi = m.jnt_range[ids, 0], m.jnt_range[ids, 1]
        dk.qpos[:] = self.d.qpos; dk.qpos[A.qa] = A.q_cmd
        rest = np.array([0.6 if n.endswith(("_FE", "_PIP", "_DIP", "_IP")) else 0.0 for n in names]) * (hi > 0.5)
        jp = np.zeros((3, m.nv))

        def solve(h):
            h = np.clip(h, lo, hi)
            for _ in range(iters):
                dk.qpos[qa] = h
                mujoco.mj_kinematics(m, dk); mujoco.mj_comPos(m, dk)
                e = target - dk.site_xpos[A.tips[f]]
                if np.linalg.norm(e) < 2e-4:
                    break
                mujoco.mj_jacSite(m, dk, jp, None, A.tips[f])
                J = jp[:, va]; JJ = J @ J.T + 2e-5 * np.eye(3)
                dq = J.T @ np.linalg.solve(JJ, e) + (np.eye(len(h)) - J.T @ np.linalg.solve(JJ, J)) @ (0.05 * (rest - h))
                sc = np.abs(dq).max()
                if sc > 0.1:
                    dq *= 0.1 / sc
                h = np.clip(h + dq, lo, hi)
            dk.qpos[qa] = h; mujoco.mj_kinematics(m, dk)
            return h, float(np.linalg.norm(target - dk.site_xpos[A.tips[f]]))

        h, err = solve((A.h_cmd if h0 is None else h0)[[JOINTS.index(n) for n in names]].copy())
        if err > 2e-3:
            for seed in (rest, np.zeros(len(names)), 0.5 * (lo + hi)):
                h2, e2 = solve(seed.copy())
                if e2 < err - 1e-3:
                    h, err = h2, e2
        return dict(zip(names, h)), err

    def hand_q(self, P):
        return self.d.qpos[self.arms[P].hq].copy()

    def tip_pos(self, P):
        return {f: self.d.site_xpos[s].copy() for f, s in self.arms[P].tips.items()}
