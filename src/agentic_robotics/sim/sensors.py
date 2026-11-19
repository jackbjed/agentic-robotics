"""Sensor model presets and the per-episode corruption state. Ported 2026-11-18 from panda_pick_rgbd pickrgbd/obs.py
(commit 75c201f; the PRESETS and Corruptor parts, unchanged): the d415 preset = panda_twin's D415 depth model fitted to
real frames (noise, holes, edge fattening, shadow, left band, depth scale, a calibration error per episode) + colour
variation (sensor-inspired, not calibrated)."""
from __future__ import annotations

import math

import cv2
import numpy as np


# ---------------------------------------------------------------- corruption (simulation only)
PRESETS = {
    # exact depth (only the D415's minimum range and the frame's far limit); nominal cameras; clean colour
    "ideal": dict(depth="ideal", calib_scale=0.0, rgb=None),
    # panda_twin's D415 model as fitted to the real frames of 10-27 (noise, holes, edge fattening, shadow, left band,
    # depth scale, a calibration error per episode: scene 5 mm / 1 deg, wrist 3 mm / 1 deg) + moderate colour
    # variation (sensor-inspired, not calibrated)
    "d415": dict(depth="d415", calib_scale=1.0,
                 rgb=dict(gain=(0.8, 1.25), gamma=(0.85, 1.15), wb=0.06, light=(0.7, 1.2), blur_p=0.3, blur_sigma=(0.3, 1.0),
                          noise=(1.0, 4.0))),
    # d415 + camera-pose randomization: per episode, each camera's true pose is moved from its configured mount
    # (twin or the real rig's calibrated poses) by a random direction and a size drawn uniformly up to
    # scene 20 mm / 2 deg, wrist 10 mm / 2 deg (covers calibration residuals and a bumped mount; sensor-inspired)
    "d415_cam": dict(depth="d415", calib_scale=1.0, calib_rand={"d415": (0.020, 2.0), "L_wrist": (0.010, 2.0)},
                     rgb=dict(gain=(0.8, 1.25), gamma=(0.85, 1.15), wb=0.06, light=(0.7, 1.2), blur_p=0.3, blur_sigma=(0.3, 1.0),
                              noise=(1.0, 4.0))),
    # stronger: twice the calibration error, more depth holes and noise, stronger colour changes
    "strong": dict(depth="d415", calib_scale=2.0, depth_kw=dict(subpx=0.30, dark_holes=0.16, dropout=0.01, flying=0.06),
                   rgb=dict(gain=(0.6, 1.5), gamma=(0.75, 1.3), wb=0.12, light=(0.5, 1.4), blur_p=0.5, blur_sigma=(0.5, 1.8),
                            noise=(2.0, 8.0))),
}


class Corruptor:
    """Per-episode persistent properties (calibration offsets, depth scale, exposure, white balance, lighting, latency)
    drawn at reset; per-frame measurement noise drawn at each frame. Colour operations are per pixel or an isotropic
    blur, and both images of a camera are rendered from the same (perturbed) pose: colour-depth alignment is kept."""

    def __init__(self, preset, twin_world, min_z):
        self.preset = preset
        self.p = PRESETS[preset]
        self.world, self.min_z = twin_world, min_z
        self.d415 = None
        self.ep = {}

    def reset(self, rng, latency_range=(0.0, 0.0), light_hook=None):
        from .twin import d415_model  # noqa: F401
        p = self.p
        self.rng = rng
        self.d415 = None
        if p["depth"] == "d415":
            kw = dict(p.get("depth_kw", {}))
            base = d415_model.P["calib"]
            kw["calib"] = {c: (v[0] * p["calib_scale"], v[1] * p["calib_scale"]) for c, v in base.items()}
            if "calib_rand" in p:                                    # (size of the pose error drawn per episode)
                kw["calib"] = {c: (float(rng.uniform(0, dp)), float(rng.uniform(0, dr))) for c, (dp, dr) in p["calib_rand"].items()}
            # (D415Real sets the cameras' true poses = nominal + a random error, on the world)
            self.d415 = d415_model.D415Real(self.world, np.random.default_rng(int(rng.integers(1 << 31))), self.min_z, **kw)
        ep = dict(preset=self.preset, latency_s=float(rng.uniform(*latency_range)))
        if self.d415 is not None:
            ep["calib"] = self.d415.calib; ep["depth_scale"] = {k: round(v, 5) for k, v in self.d415.scale.items()}
        r = p["rgb"]
        if r is not None:
            for cam in ("d415", "L_wrist"):
                ep[cam] = dict(gain=float(math.exp(rng.uniform(math.log(r["gain"][0]), math.log(r["gain"][1])))),
                               gamma=float(rng.uniform(*r["gamma"])),
                               wb=(1 + rng.uniform(-r["wb"], r["wb"], 3)).round(4).tolist(),
                               noise=float(rng.uniform(*r["noise"])))
            ep["light"] = float(rng.uniform(*r["light"]))
            if light_hook is not None:
                light_hook(ep["light"])
        self.ep = ep
        return ep

    def depth(self, z, gid, cam):
        if self.d415 is not None:
            return self.d415.apply(z, gid, cam)
        z = np.where(np.isfinite(z) & (z > self.min_z), z, np.inf).astype(np.float32)
        return z

    def rgb(self, im, cam):
        """Exposure gain x white balance, then gamma (one lookup table per channel and episode), an occasional
        isotropic blur, sensor noise per frame (a slice of a per-episode noise bank: drawing 640x480x3 normals per
        frame cost 10 ms)."""
        r = self.p["rgb"]
        if r is None:
            return im
        e = self.ep[cam]; rng = self.rng
        if "_lut" not in e:
            x = np.arange(256, dtype=np.float32) / 255.0
            e["_lut"] = np.stack([np.clip(np.clip(x * e["gain"] * wb, 0, 1) ** e["gamma"] * 255 + 0.5, 0, 255)
                                  for wb in e["wb"]], 1).astype(np.uint8)[:, None, :]          # (256, 1, 3)
            e["_noise"] = np.clip(rng.normal(0, e["noise"], (2 * im.shape[0], 2 * im.shape[1], 3)), -127, 127).astype(np.int16)
        out = cv2.LUT(im, e["_lut"])
        if rng.random() < r["blur_p"]:
            out = cv2.GaussianBlur(out, (0, 0), float(rng.uniform(*r["blur_sigma"])))
        h, w = im.shape[:2]
        y0, x0 = int(rng.integers(0, h)), int(rng.integers(0, w))
        out = np.clip(out.astype(np.int16) + e["_noise"][y0:y0 + h, x0:x0 + w], 0, 255).astype(np.uint8)
        return out
