"""ctx.tools for programs written from scratch (runs without the robot skill library, docs/skill_api_scratch.md): the
robot's conventions and the camera geometry. Nothing here looks, plans or moves: that is the program's own code.

  tools.down(yaw)            the hand pointing straight down, the fingers closing along the table direction yaw (rad)
  tools.world(obs)           (P, ok): every pixel's 3D point (world frame, m) from the frame's depth and the camera pose
                             the program believes (obs R, eye); ok = the pixel has depth. Raw: no filtering of noise or
                             flying pixels
  tools.project(obs, X)      world points X (3,) or (N, 3) -> pixels (u, v) (and depth along the optical axis)
  tools.FINGERTIP            m: the fingertip point = TCP + R[:, 2] * FINGERTIP
  tools.OPEN_W               m per finger: fully open (80 mm between the fingers)
  tools.MIN_DEPTH            m: the D415's minimum range (nothing nearer has depth)
  tools.Unreachable, tools.RobotStop   the robot's two errors (docs/robot.md)"""
from __future__ import annotations

import math

import numpy as np


class Tools:
    def __init__(self, twin):
        self.FINGERTIP = float(twin.TIP)
        self.OPEN_W = float(twin.OPEN_W)
        self.MIN_DEPTH = float(twin.MIN_Z)
        self.Unreachable = twin.T.Unreachable
        self.RobotStop = twin.RobotStop

    @staticmethod
    def down(yaw):
        y = np.array([math.cos(yaw), math.sin(yaw), 0.0]); z = np.array([0.0, 0.0, -1.0])
        return np.stack([np.cross(y, z), y, z], 1)

    @staticmethod
    def world(obs):
        W, H, fx, fy, cx, cy = obs["K"]
        v, u = np.mgrid[0:int(H), 0:int(W)]
        z = np.asarray(obs["depth"], float)
        ok = np.isfinite(z) & (z > 0)
        zz = np.where(ok, z, 0.0)
        Xc = np.stack([(u - cx) / fx * zz, (v - cy) / fy * zz, zz], -1)
        return Xc @ np.asarray(obs["R"], float).T + np.asarray(obs["eye"], float), ok

    @staticmethod
    def project(obs, X):
        W, H, fx, fy, cx, cy = obs["K"]
        X = np.asarray(X, float)
        Xc = (X - np.asarray(obs["eye"], float)) @ np.asarray(obs["R"], float)
        u = fx * Xc[..., 0] / Xc[..., 2] + cx
        v = fy * Xc[..., 1] / Xc[..., 2] + cy
        return np.stack([u, v], -1), Xc[..., 2]
