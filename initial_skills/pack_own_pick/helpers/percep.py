"""Perception helpers: masks -> 3D, heightmaps."""
import numpy as np
import cv2


def mask_points(P, ok, mask, zmin=0.004):
    m = mask & ok & np.isfinite(P[..., 2])
    pts = P[m]
    if zmin is not None and len(pts):
        pts = pts[pts[:, 2] > zmin]
    return pts


def mask_stats(P, ok, mask):
    pts = mask_points(P, ok, mask)
    area = int(mask.sum())
    if len(pts) < 20:
        return dict(area=area, n=int(len(pts)))
    c = np.median(pts, axis=0)
    lo = np.percentile(pts, 5, axis=0)
    hi = np.percentile(pts, 95, axis=0)
    return dict(area=area, n=int(len(pts)), c=[round(float(x), 4) for x in c],
                lo=[round(float(x), 4) for x in lo], hi=[round(float(x), 4) for x in hi],
                ztop=round(float(np.percentile(pts[:, 2], 90)), 4))


def fill_holes(mask):
    m = mask.astype(np.uint8)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros_like(m)
    if cnts:
        cv2.drawContours(out, cnts, -1, 1, thickness=-1)
    return out.astype(bool)


def largest_component(mask):
    m = mask.astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return mask
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    return lab == i


def circle_fit(xy):
    """Kasa algebraic circle fit. xy (N,2) -> (cx, cy, r)."""
    x, y = xy[:, 0], xy[:, 1]
    A = np.c_[2 * x, 2 * y, np.ones_like(x)]
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, c = sol
    r = np.sqrt(max(c + cx * cx + cy * cy, 0.0))
    return float(cx), float(cy), float(r)
