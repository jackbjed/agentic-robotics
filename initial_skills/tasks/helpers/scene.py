"""Helpers over the robot's cameras and the segmenter (no ground truth: only what the cameras deliver)."""
import numpy as np


def world_points(ctx, obs):
    """Every pixel's 3D point (m, the program's believed camera pose) and where the depth is valid."""
    return ctx.library.F.world(obs)


def find_in_scene(ctx, phrase, min_px=30, cap_z=0.06, top_frac=None):
    """The object by name in the scene camera: its best mask's 3D points; the seed = their median (x, y) at the
    object's top (97th percentile height; capped at cap_z for small things, as the robot's seed_at; cap_z=None for
    tall objects). top_frac: only the points within top_frac m of that top (e.g. the stick tips of a stand).
    -> dict(p=[x, y, z], ray=None, n=points) or None."""
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
    if top_frac is not None:
        Q = Q[Q[:, 2] > top - top_frac]
    z = top if cap_z is None else min(top, cap_z)
    return dict(p=[float(np.median(Q[:, 0])), float(np.median(Q[:, 1])), z], ray=None, n=int(len(Q)))
