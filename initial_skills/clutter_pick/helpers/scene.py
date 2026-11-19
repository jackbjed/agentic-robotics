"""Helpers over the robot's cameras and the segmenter (no ground truth: only what the cameras deliver)."""
import numpy as np


def world_points(ctx, obs):
    """Every pixel's 3D point (m, the program's believed camera pose) and where the depth is valid."""
    F = ctx.library.F
    return F.world(obs)


def find_in_scene(ctx, phrase, min_px=30):
    """The object by name in the scene camera: its best mask, its 3D points; the seed = their median (x, y) at the
    object's top (97th percentile height, at most 6 cm, as the robot's seed_at). None if not seen."""
    obs = ctx.robot.observe_frame("d415")
    masks = ctx.segmenter.segment(obs["rgb"], phrase)
    if not masks:
        return None
    P, ok = world_points(ctx, obs)
    best = None
    for mk, sc in masks:
        n = int((mk & ok).sum())
        if n >= min_px and (best is None or sc > best[1]):
            best = (mk, sc)
    if best is None:
        return None
    Q = P[best[0] & ok]
    top = float(np.percentile(Q[:, 2], 97))
    return dict(p=[float(np.median(Q[:, 0])), float(np.median(Q[:, 1])), min(top, 0.06)], ray=None)
