"""Planned trajectories drawn over a real camera frame (real/server.py: every trajectory a program plans, in dry and
live runs): the arm at the trajectory's end (the twin's model at the planned joints, rendered from the scene camera's
calibrated pose with the frame's own intrinsics) blended over the frame, and the fingertip path (start green, end red).

The twin's model has no flange adapter (the real hand sits 7.1 mm nearer the flange: calib.json tool.adapter_dz), and
the frame's principal point is applied as a shift of the rendered image: the drawing is a few mm off the real arm's
pose, enough to see where a move goes, not to measure it."""
from __future__ import annotations

import json
import math

import cv2
import numpy as np


class PlanView:
    def __init__(self):
        from agentic_robotics.sim.picktwin import PickTwin
        from agentic_robotics.sim.twin import PT_CODE, TIP
        self.P = P = PickTwin(render=True)
        P.use_cameras("calibrated")
        m = P.m
        names = [m.body(int(b)).name for b in m.geom_bodyid]
        self.left = np.array([n.startswith("L_") for n in names])
        self.qadr, self.fing = P.world.qadr, P.world.fing
        self.tcp = P.world.tcp
        self.TIP = TIP
        c = json.loads((PT_CODE / "real" / "calib.json").read_text())
        T = np.asarray(c["scene_cam"]["T_world_cam"], float)
        self.R_cv, self.eye = T[:3, :3], T[:3, 3]
        self.fovy0 = float(m.cam_fovy[m.camera("d415").id])

    def tips(self, Q, every=5):
        """Fingertip points (world) along the joint path."""
        P, mj = self.P, self.P.mj
        out = []
        for q in list(Q[::every]) + [Q[-1]]:
            P.d.qpos[self.qadr] = q; mj.mj_forward(P.m, P.d)
            R = P.d.site_xmat[self.tcp].reshape(3, 3)
            out.append(P.d.site_xpos[self.tcp] + R[:, 2] * self.TIP)
        return np.array(out)

    def project(self, X, K):
        W, H, fx, fy, cx, cy = K
        Xc = (np.asarray(X, float) - self.eye) @ self.R_cv
        ok = Xc[:, 2] > 0.05
        uv = np.stack([fx * Xc[:, 0] / np.maximum(Xc[:, 2], 1e-6) + cx, fy * Xc[:, 1] / np.maximum(Xc[:, 2], 1e-6) + cy], 1)
        return uv, ok

    def draw(self, rgb, K, Q, grip_end=None, label=""):
        """rgb: the scene camera's frame (RGB), K: its (W, H, fx, fy, cx, cy); Q: the planned joints (N, 7) -> RGB."""
        P, m, mj = self.P, self.P.m, self.P.mj
        Q = np.asarray(Q, float)
        W, H, fx, fy, cx, cy = K
        cid = m.camera("d415").id
        m.cam_fovy[cid] = math.degrees(2 * math.atan(H / 2 / fy))
        g0 = m.geom_group.copy(); m.geom_group[~self.left] = 5      # (only the arm: nothing of the twin's scene in front)
        try:
            P.d.qpos[self.qadr] = Q[-1]
            if grip_end is not None:
                P.d.qpos[self.fing] = float(grip_end)
            mj.mj_forward(m, P.d)
            ghost, _, gid = P.render("d415", depth=False, seg=True)
        finally:
            m.cam_fovy[cid] = self.fovy0; m.geom_group[:] = g0
        mask = (gid >= 0) & self.left[np.clip(gid, 0, None)]
        A = np.float32([[1, 0, cx - W / 2], [0, 1, cy - H / 2]])            # (the real principal point)
        ghost = cv2.warpAffine(ghost, A, (int(W), int(H)))
        mask = cv2.warpAffine(mask.astype(np.uint8), A, (int(W), int(H))) > 0
        out = np.asarray(rgb).copy()
        tint = (0.45 * ghost + 0.55 * np.array([70, 170, 255])).astype(np.uint8)
        out[mask] = (0.45 * out[mask] + 0.55 * tint[mask]).astype(np.uint8)
        cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, cnts, -1, (40, 120, 255), 1)
        uv, ok = self.project(self.tips(Q), K)
        pts = [tuple(int(round(c)) for c in p) for p, o in zip(uv, ok) if o]
        n = len(pts)
        for i in range(1, n):
            a = i / max(n - 1, 1)
            cv2.line(out, pts[i - 1], pts[i], (int(255 * a), int(255 * (1 - a)), 40), 2, cv2.LINE_AA)
        if pts:
            cv2.circle(out, pts[0], 5, (0, 255, 0), 2); cv2.circle(out, pts[-1], 5, (255, 0, 0), 2)
        bar = np.full((26, out.shape[1], 3), 20, np.uint8)
        cv2.putText(bar, label[:90], (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        return np.concatenate([bar, out], 0)


def sheet(images, cols=3, width=1440):
    """A contact sheet of RGB images (the run's planned moves)."""
    if not images:
        return None
    w = width // cols
    tiles = [cv2.resize(im, (w, int(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA) for im in images]
    h = max(t.shape[0] for t in tiles)
    tiles = [np.pad(t, ((0, h - t.shape[0]), (0, 0), (0, 0))) for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    return np.concatenate([np.concatenate(tiles[k:k + cols], 1) for k in range(0, len(tiles), cols)], 0)
