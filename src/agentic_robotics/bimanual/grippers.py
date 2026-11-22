"""sim_bimanual_grippers: the rig's UMI fin-ray parallel gripper on each Panda (docs/bimanual.md).
- the pose commanded is the TCP: the point midway between the fingertips' pads;
- orientation: approach (where the fingers point, the hand's z) and closing (the line the fingers close along, y);
- set_gripper(arm, width): the opening between the fingers, 0..0.08 m (position servo, up to 70 N).
"""
from __future__ import annotations

import numpy as np

from .core import BiCore

OPEN_W = 0.04                                                   # per finger


def grip_R(approach, closing):
    """Columns: x = closing x approach, y = closing, z = approach (the Panda hand's frame)."""
    z = np.asarray(approach, float); z = z / max(np.linalg.norm(z), 1e-9)
    y = np.asarray(closing, float); y = y - z * float(y @ z)
    if np.linalg.norm(y) < 1e-6:
        raise ValueError("the closing direction is parallel to the approach direction")
    y /= np.linalg.norm(y)
    return np.column_stack([np.cross(y, z), y, z])


class GripperSim(BiCore):
    NAME = "sim_bimanual_grippers"
    HANDS = "grippers"

    def _init_arm(self, A):
        m, P = self.m, A.P
        A.grip = m.actuator(f"{P}_gripper").id
        A.fq = [m.jnt_qposadr[m.joint(f"{P}_finger_joint{k}").id] for k in (1, 2)]
        A.palm = m.site(f"{P}_tcp").id; A.tips = {}
        for b in range(m.nbody):
            nm = m.body(b).name
            if nm in (f"{P}_left_finger", f"{P}_right_finger"):
                self.part[b] = (P, "finger A" if "left" in nm else "finger B")
            elif nm == f"{P}_hand" or nm.startswith(f"{P}_link"):
                self.part[b] = (P, "arm")

    def _reset_hand(self, A):
        self.d.qpos[A.fq] = OPEN_W; self.d.ctrl[A.grip] = OPEN_W; A.g_cmd = OPEN_W

    def set_gripper(self, P, width, duration=0.8):
        """The opening between the fingers (m, 0..0.08), ramped; then 0.4 s. -> the opening measured."""
        A = self.arms[P]
        g0, g1 = A.g_cmd, float(np.clip(width / 2, 0.0, OPEN_W))
        n = max(int(round(duration / self.m.opt.timestep)), 1)
        for i in range(1, n + 1):
            A.g_cmd = g0 + (g1 - g0) * i / n; self.d.ctrl[A.grip] = A.g_cmd
            self.step()
        self.run(0.4)
        return self.width(P)

    def width(self, P):
        A = self.arms[P]
        return float(self.d.qpos[A.fq[0]] + self.d.qpos[A.fq[1]])
