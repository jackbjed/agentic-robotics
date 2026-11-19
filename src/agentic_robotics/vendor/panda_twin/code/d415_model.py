"""A RealSense D415 depth image as the user's two D415s deliver it (fitted 10-27 to real frames: panda_twin/d415_frames,
analysis out/d415_real_1027), instead of the ideal one (MuJoCo depth + per-pixel white noise): switched on with
D415_DEPTH=real (task_box.BoxPerception, stack_bench.Perception.observe).

  noise       sigma_z = 0.18 px * z^2 / (F_DEPTH 625 px * 55 mm), 90 % of it smooth over ~8 px (real, the wrist camera
              looking down at 0.5 m: 0.6 mm frame to frame + 1-2 mm that does not average out; the scene camera on the
              black table at a glancing angle: 5-8 mm at 0.85-1.3 m)
  scale       a per-camera depth scale error, uniform +-0.5 % (the 146 mm white box read 146 mm at 0.35 m)
  fattening   a nearer surface spreads 4 px over the farther one at a depth step > 1 cm (a 3 mm box wall reads 12 px)
  shadow      no depth where the right imager cannot see what the left one does (the band left of a near object,
              width F_DEPTH * b * (1/z_near - 1/z_far) px; real: 8-11 px beside the box wall, this predicts 8.6)
  flying      3 % of the pixels at a step > 2 cm get a depth between the two sides (real: < 1 px per edge)
  dark        objects darker than 0.2 (hole block, cable, screwdriver shaft): 8 % of their pixels lost, in blobs
              (real: 92-100 % valid at 1 m, 85 % at 0.47 m)
  table       the scene camera's view of the black table (glancing): 20 % holes up to 1.1 m, more beyond (50 % at
              1.4 m), in blobs (the wrist camera looking down at it: ~none)
  dropouts    0.2 % random pixels
  left band   no depth in the image's LEFT F_DEPTH * b / z + 30 px (z: the depth there; real 70 px at 1 m, 97 at
              0.5 m, 129 at 0.35 m; nothing on the right)
  min range   nothing nearer than MIN_Z (stack_bench: F_DEPTH * b / 126 px = 0.275 m)
  calibration the cameras' true poses differ from the nominal ones: scene 5 mm / 1 deg, wrist (hand-eye) 3 mm / 1 deg,
              random directions (stack_bench.Sim.set_camera_error; the program keeps using the nominal poses). Not
              fitted: the real scene camera's height / pitch / roll agree with the twin's to 10 mm / 0.35 deg (lateral
              and yaw unchecked); the wrist mount is unchecked.
Override any of them with D415_PARAMS='{"calib": {}, "scale": 0}' (JSON). The guesses before the real frames:
d415_model.py.bak_real_1027 (30 % holes on dark objects and 50 % flying pixels at edges: far too pessimistic; 20 px
side columns and a 0.22 m minimum range: too optimistic).
"""
import math
from statistics import NormalDist

import cv2
import numpy as np

P = dict(subpx=0.18, f_depth=625.0, baseline=0.055, corr=0.9, corr_px=8.0, scale=0.005,
         fatten_px=4, fat_jump=0.01, jump=0.02, flying=0.03,
         dark_lum=0.2, dark_holes=0.08, blob_px=4.0, table_holes=(0.2, 1.1, 1.0, 0.6), dropout=0.002,
         band_extra=30, shadow=True,
         calib={"d415": (0.005, 1.0), "L_wrist": (0.003, 1.0)})


def _unit(rng):
    v = rng.normal(size=3)
    return v / np.linalg.norm(v)


class D415Real:
    def __init__(self, S, rng, min_z, **kw):
        self.p = dict(P, **kw)
        self.rng, self.min_z = rng, min_z
        m = S.m
        rgba = np.where((m.geom_matid >= 0)[:, None], m.mat_rgba[np.clip(m.geom_matid, 0, None)], m.geom_rgba)
        lum = rgba[:, :3] @ [0.299, 0.587, 0.114]
        obj = np.array([m.body(m.geom_bodyid[g]).name.startswith("obj_") for g in range(m.ngeom)])
        self.dark = obj & (lum < self.p["dark_lum"])                 # (dark objects; the table: see table_holes)
        names = [m.geom(g).name for g in range(m.ngeom)]
        self.table = names.index("table_top") if "table_top" in names else -1
        self.scale = {c: float(rng.uniform(-1, 1)) * self.p["scale"] for c in ("d415", "L_wrist")}
        self.calib = {}
        for cam, (dp, dr) in self.p["calib"].items():               # true pose = nominal + error (program: nominal)
            dpos, drot = dp * _unit(rng), dr * _unit(rng)
            S.set_camera_error(cam, dpos, drot)
            self.calib[cam] = dict(dpos_mm=np.round(dpos * 1000, 1).tolist(), drot_deg=np.round(drot, 2).tolist())

    def _field(self, shape, px):
        n = cv2.GaussianBlur(self.rng.normal(0, 1, shape).astype(np.float32), (0, 0), px)
        return n / max(float(n.std()), 1e-6)

    def apply(self, depth, gid, cam):
        """depth: ideal (m, inf = none), gid: geom id per pixel (-1 = none) -> the depth image the program gets."""
        p, rng = self.p, self.rng
        z = np.where(np.isfinite(depth), depth, np.inf).astype(np.float32)
        ok = np.isfinite(z) & (z < 10)
        far = np.where(ok, z, 1e3).astype(np.float32)
        # fattening: block matching spreads a nearer surface over the farther one next to it
        if p["fatten_px"]:
            k = 2 * int(p["fatten_px"]) + 1
            near = cv2.erode(far, np.ones((k, k), np.uint8))
            fat = ok & (z - near > p["fat_jump"])
            z = np.where(fat, near, z).astype(np.float32); far = np.where(ok, z, 1e3).astype(np.float32)
        zz = np.where(ok, z, 0).astype(np.float32)
        # occlusion shadow (left imager = reference): background pixel x is hidden from the right imager when a nearer
        # pixel x + k has a disparity larger by more than k
        fb = p["f_depth"] * p["baseline"]
        disp = np.where(ok, fb / np.maximum(zz, 1e-3), 0).astype(np.float32)
        shadow = np.zeros(z.shape, bool)
        kmax = int(min(80, np.ceil(disp.max() - disp[ok].min()))) if ok.any() and p["shadow"] else 0
        for k in range(1, kmax + 1):
            shadow[:, :-k] |= (disp[:, k:] - disp[:, :-k]) > k
        # (a few) flying pixels at depth jumps
        kk = np.ones((5, 5), np.uint8)
        mx, mn = cv2.dilate(zz, kk), cv2.erode(far, kk)
        fly = ok & (mx - mn > p["jump"]) & (rng.random(z.shape) < p["flying"])
        # noise (mostly smooth + white), depth scale error
        sig = p["subpx"] * zz ** 2 / fb
        n = p["corr"] * self._field(z.shape, p["corr_px"]) + math.sqrt(1 - p["corr"] ** 2) * rng.normal(0, 1, z.shape).astype(np.float32)
        out = np.where(ok, z * (1 + self.scale[cam]) + sig * n, np.inf).astype(np.float32)
        out[fly] = (mn[fly] + rng.random(int(fly.sum())).astype(np.float32) * (mx[fly] - mn[fly])) * (1 + self.scale[cam])
        # holes: dark objects (blobs), the scene camera's glancing view of the black table (blobs), random dropouts,
        # shadow, the left band, too near
        dark = (gid >= 0) & self.dark[np.clip(gid, 0, None)]
        blob = self._field(z.shape, p["blob_px"]) > _q(p["dark_holes"]) if p["dark_holes"] > 0 else np.zeros(z.shape, bool)
        holes = (dark & blob) | (rng.random(z.shape) < p["dropout"]) | shadow
        if cam == "d415" and p["table_holes"] and self.table >= 0:
            base, z0, slope, cap = p["table_holes"]
            rate = np.clip(base + slope * np.maximum(zz - z0, 0), base, cap)
            qs = np.linspace(0.01, 0.99, 99); th = np.array([_q(r) for r in qs])
            holes |= (gid == self.table) & (self._field(z.shape, p["blob_px"]) > np.interp(rate, qs, th))
        if p["band_extra"] is not None:
            zl = zz[:, :200][zz[:, :200] > 0]
            zref = float(np.median(zl)) if len(zl) else 1.0
            holes[:, :int(min(z.shape[1], fb / max(zref, self.min_z) + p["band_extra"]))] = True
        out[holes | ~(out > self.min_z)] = np.inf
        return out


def _q(frac):
    """Threshold of a unit normal field that leaves `frac` of it above."""
    return NormalDist().inv_cdf(1 - frac)
