"""Franka arm kinematics in plain numpy (no MuJoCo: the arm server runs in the deoxys env), the rig's frames and its
safety limits. Shared by arm_server.py (deoxys env) and the twin-side code (teleavatar_client env).

Frames: base = the arm's link0 (libfranka's O frame); world = the twin's frame (table top at z = 0); flange = link8
(DH, 0.107 past joint 7); hand = flange turned -45 deg about z and moved adapter_dz along z (the real arm carries an
adapter between flange and hand that the twin's model lacks: rig.json "tool.adapter_dz", MEASURE it); TCP = hand +
tcp_z along z (between the fin-ray fingers' pads); the fingertips FINGERTIP below the TCP.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RIG = HERE / "rig.json"
CALIB = HERE / "calib.json"

# Craig (modified) DH of the Panda / FR3 (identical kinematics): a, d, alpha per joint, then the flange
DH = [(0.0, 0.333, 0.0), (0.0, 0.0, -math.pi / 2), (0.0, 0.316, math.pi / 2), (0.0825, 0.0, math.pi / 2),
      (-0.0825, 0.384, -math.pi / 2), (0.0, 0.0, math.pi / 2), (0.088, 0.0, math.pi / 2)]
FLANGE_D = 0.107


def load_rig(path=RIG):
    return json.loads(Path(path).read_text())


def load_calib(path=CALIB):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def _tf(a, d, alpha, theta):
    ca, sa, ct, st = math.cos(alpha), math.sin(alpha), math.cos(theta), math.sin(theta)
    return np.array([[ct, -st, 0, a], [st * ca, ct * ca, -sa, -d * sa], [st * sa, ct * sa, ca, d * ca], [0, 0, 0, 1.0]])


def rotz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def hom(R=None, p=None):
    T = np.eye(4)
    if R is not None:
        T[:3, :3] = R
    if p is not None:
        T[:3, 3] = p
    return T


class Kin:
    """Forward kinematics of the rig's arm: joint frames, flange, hand, TCP, in the base and the world frame."""

    def __init__(self, rig=None, calib=None):
        rig = load_rig() if rig is None else rig
        calib = load_calib() if calib is None else calib
        b = rig["base_world"]
        self.T_wb = hom(rotz(b["yaw"]), b["pos"])                     # world <- base
        t = dict(rig["tool"], **calib.get("tool", {}))
        self.adapter_dz = float(t["adapter_dz"])
        self.tcp_z = float(t["tcp_z"])
        self.fingertip = float(t["fingertip"])
        self.T_fh = hom(rotz(-math.pi / 4), [0, 0, self.adapter_dz])  # flange <- hand
        self.T_ht = hom(None, [0, 0, self.tcp_z])                     # hand <- TCP

    def frames(self, q):
        """Base-frame transforms of joint frames 1..7 and the flange (8 x 4x4)."""
        T, out = np.eye(4), []
        for (a, d, al), th in zip(DH, q):
            T = T @ _tf(a, d, al, th)
            out.append(T)
        out.append(T @ hom(None, [0, 0, FLANGE_D]))
        return out

    def flange_base(self, q):
        return self.frames(q)[-1]

    def hand_world(self, q):
        return self.T_wb @ self.flange_base(q) @ self.T_fh

    def tcp_world(self, q):
        return self.hand_world(q) @ self.T_ht

    def check_points_world(self, q):
        """Points that must stay above the table: joint frame origins 2..7, the flange, the hand's bottom corners
        (task_box's hand model: +-106 mm along the fingers, +-32 mm across, from 110 mm over the TCP), the wrist
        camera."""
        Fr = [self.T_wb @ F for F in self.frames(q)]
        H = Fr[-1] @ self.T_fh
        pts = [F[:3, 3] for F in Fr[1:]]
        for sy in (-0.106, 0.106):                                    # the hand's bottom face (its long axis is
            for sx in (-0.032, 0.032):                                # y, the fingers' travel; 0.11 m over the TCP)
                pts.append((H @ np.r_[sx, sy, self.tcp_z - 0.11, 1.0])[:3])
        for sy in (-0.05, 0.05):                                      # the wrist D415 on its bracket (hand +x)
            pts.append((H @ np.r_[0.083, sy, 0.076, 1.0])[:3])
        return np.array(pts)


class Limits:
    """The rig's safety limits (rig.json "safety"): joint ranges (Panda and FR3 both: their intersection, minus a
    margin), joint speed, the TCP's box, the arm's height over the table. check() -> None or why not."""

    def __init__(self, rig=None, kin=None):
        rig = load_rig() if rig is None else rig
        s = rig["safety"]
        self.kin = Kin(rig) if kin is None else kin
        m = float(s["joint_margin"])
        self.lo = np.array(s["joint_lo"]) + m
        self.hi = np.array(s["joint_hi"]) - m
        self.v_max = np.array(s["v_max"], float)
        self.tcp_lo, self.tcp_hi = np.array(s["tcp_lo"]), np.array(s["tcp_hi"])
        self.table_z = float(s["table_z"])
        self.min_fingertip = float(s["min_fingertip_over_table"])
        self.min_link = float(s["min_link_over_table"])
        self.start_tol = float(s["start_tol"])
        self.keepout = [(k["name"], np.array(k["lo"]), np.array(k["hi"])) for k in s.get("keepout", [])]

    def check_q(self, q):
        q = np.asarray(q, float)
        if q.shape != (7,) or not np.isfinite(q).all():
            return "not 7 finite joint angles"
        bad = np.nonzero((q < self.lo - 1e-6) | (q > self.hi + 1e-6))[0]     # (an interpolation's rounding at a limit)
        if len(bad):
            j = int(bad[0])
            return f"joint {j + 1} at {q[j]:.3f} rad, outside [{self.lo[j]:.3f}, {self.hi[j]:.3f}] (the limits minus the margin)"
        T = self.kin.tcp_world(q)
        p = T[:3, 3]
        if (p < self.tcp_lo).any() or (p > self.tcp_hi).any():
            return f"TCP {np.round(p, 3).tolist()} outside the workspace box {self.tcp_lo.tolist()} .. {self.tcp_hi.tolist()}"
        tip = p + T[:3, 2] * self.kin.fingertip                         # (the TCP's z points along the fingers)
        if tip[2] < self.table_z + self.min_fingertip:
            return f"fingertips at z {tip[2] * 1000:.1f} mm: under the table + {self.min_fingertip * 1000:.0f} mm"
        P = self.kin.check_points_world(q)
        if P[:, 2].min() < self.table_z + self.min_link:
            return f"a link / the hand at z {P[:, 2].min() * 1000:.0f} mm: under the table + {self.min_link * 1000:.0f} mm"
        for name, lo, hi in self.keepout:
            inside = ((np.r_[P, p[None]] >= lo) & (np.r_[P, p[None]] <= hi)).all(1)
            if inside.any():
                return f"the arm enters the keep-out box of {name} at {np.round(np.r_[P, p[None]][np.argmax(inside)], 3).tolist()}"
        return None

    def check_traj(self, t, Q, q_now=None, every=1):
        """A trajectory (times t [n], joint angles Q [n, 7]): every point inside the limits, joint speeds under v_max
        (10 % slack: the server resamples), starting at the arm (within start_tol of q_now)."""
        t, Q = np.asarray(t, float), np.asarray(Q, float)
        if Q.ndim != 2 or Q.shape[1] != 7 or len(t) != len(Q) or len(Q) < 1:
            return "malformed trajectory"
        if not (np.isfinite(t).all() and np.isfinite(Q).all()) or (np.diff(t) <= 0).any() or t[0] < 0:
            return "times must increase"
        if q_now is not None and np.abs(Q[0] - q_now).max() > self.start_tol:
            j = int(np.argmax(np.abs(Q[0] - q_now)))
            return f"starts {abs(Q[0][j] - q_now[j]):.3f} rad from the arm at joint {j + 1} (> {self.start_tol})"
        if len(Q) > 1:
            v = np.abs(np.diff(Q, axis=0)) / np.diff(t)[:, None]
            if (v > 1.1 * self.v_max).any():
                i, j = np.unravel_index(int(np.argmax(v / self.v_max)), v.shape)
                return f"joint {j + 1} at {v[i, j]:.2f} rad/s > {self.v_max[j]:.2f}"
        idx = sorted(set(list(range(0, len(Q), max(1, every))) + [len(Q) - 1]))
        for i in idx:
            why = self.check_q(Q[i])
            if why:
                return f"point {i}/{len(Q) - 1} (t {t[i]:.2f} s): {why}"
        return None


def time_scale_for(t, Q, v_lim):
    """Factor (>= 1) to stretch a trajectory's times so that no joint is faster than v_lim (per joint)."""
    t, Q = np.asarray(t, float), np.asarray(Q, float)
    if len(Q) < 2:
        return 1.0
    v = np.abs(np.diff(Q, axis=0)) / np.diff(t)[:, None]
    return max(1.0, float((v / np.asarray(v_lim)).max()))


def resample(t, Q, rate):
    """Goals at the control rate: Q interpolated (linearly, in time) at k / rate, k = 1 .. (the end exactly)."""
    t, Q = np.asarray(t, float), np.asarray(Q, float)
    T = t[-1] - t[0]
    n = max(1, int(math.ceil(T * rate - 1e-9)))
    ts = t[0] + np.minimum(np.arange(1, n + 1) / rate, T)
    return np.stack([np.interp(ts, t, Q[:, j]) for j in range(Q.shape[1])], 1)
