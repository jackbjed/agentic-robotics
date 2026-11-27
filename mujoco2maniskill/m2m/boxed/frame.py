"""The camera frame a program gets (plain numpy: this file is also what the sandboxed side imports)."""
import numpy as np


class Frame:
    """One camera frame. rgb (H, W, 3 uint8); depth (H, W) mm along the optical axis, nan = none; fx, fy, cx, cy;
    eye (mm) and R (3x3, camera to table frame; camera axes x right, y down, z forward): the camera's pose as the robot
    believes it."""

    def __init__(self, o):
        self._o = o
        self.camera, self.rgb, self.t = o["camera"], o["rgb"], o["t"]
        self.depth = o["depth"] * 1000.0
        self.width, self.height, self.fx, self.fy, self.cx, self.cy = o["K"]
        self.eye, self.R = o["eye"] * 1000.0, o["R"]

    def locate(self, u, v):
        o = self._o
        Wd, Hd, fx, fy, cx, cy = o["K"]
        ui, vi = int(round(float(u))), int(round(float(v)))
        if not (0 <= ui < Wd and 0 <= vi < Hd):
            return None
        win = o["depth"][max(vi - 3, 0):vi + 4, max(ui - 3, 0):ui + 4]; win = win[np.isfinite(win)]
        if len(win) < 5:
            return None
        z = float(np.median(win))
        return (o["eye"] + o["R"] @ np.array([(float(u) - cx) / fx * z, (float(v) - cy) / fy * z, z])) * 1000.0

    def points(self):
        v, u = np.mgrid[0:self.height, 0:self.width]
        z = self.depth
        C = np.dstack([(u - self.cx) / self.fx * z, (v - self.cy) / self.fy * z, z])
        return self.eye + C @ self.R.T

    def project(self, xyz):
        c = (np.atleast_2d(np.asarray(xyz, float)) - self.eye) @ self.R; z = c[:, 2]
        return np.c_[self.fx * c[:, 0] / z + self.cx, self.fy * c[:, 1] / z + self.cy], z

    def with_pixels(self, rgb=None, depth=None):
        o = dict(self._o)
        if rgb is not None:
            rgb = np.ascontiguousarray(np.asarray(rgb, np.uint8))
            if rgb.shape != self.rgb.shape:
                raise ValueError(f"rgb: {self.rgb.shape} uint8")
            o["rgb"] = rgb
        if depth is not None:
            depth = np.asarray(depth, float)
            if depth.shape != self.depth.shape:
                raise ValueError(f"depth: {self.depth.shape}, mm, nan where there is none")
            o["depth"] = np.where(np.isfinite(depth) & (depth > 0), depth, np.nan) / 1000.0
        return Frame(o)

    # ---- across the boundary: exactly the fields a frame is made of (no reference to anything else)
    def wire(self):
        o = self._o
        return dict(camera=str(o["camera"]), rgb=np.asarray(o["rgb"]), t=float(o["t"]), depth=np.asarray(o["depth"], float),
                    K=[float(v) for v in o["K"]], Kint=[bool(isinstance(v, (int, np.integer))) for v in o["K"]],
                    eye=np.asarray(o["eye"], float), R=np.asarray(o["R"], float))

    @classmethod
    def from_wire(cls, w):
        K = tuple(int(v) if i else float(v) for v, i in zip(w["K"], w["Kint"]))
        return cls(dict(camera=w["camera"], rgb=w["rgb"], t=w["t"], depth=w["depth"], K=K, eye=w["eye"], R=w["R"]))
