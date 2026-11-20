"""The fingers as seen by the wrist camera: a reference for its per-episode calibration error.

The fingers are fixed in the hand, like the camera. With the gripper fully open, where they appear in the wrist image
depends only on the camera's true mount. Their offset from the reference (mean over practice episodes) is the image
shift the calibration error causes at fingertip depth, nearly the same shift as for the roll just below them.
"""
import numpy as np
import cv2


# reference (gripper fully open): mean over ~52 practice episodes (runs 002, 011-014; outliers dropped)
REF = dict(lx=142.8, rx=479.4, ly=400.8, ry=401.5)


def calib_shift(f, log=None):
    """Image shift (du, dv) in pixels of hand-fixed things vs the reference, or (0, 0) if unreliable."""
    if not f:
        return 0.0, 0.0
    sp = f["rx"] - f["lx"]
    du = 0.0
    dlx, drx = f["lx"] - REF["lx"], f["rx"] - REF["rx"]
    if 322 < sp < 356:
        du = 0.5 * (dlx + drx)
    elif min(abs(dlx), abs(drx)) < 25:
        du = dlx if abs(dlx) < abs(drx) else drx     # one blob merged with something green: trust the other
    dl, dr = f["ly"] - REF["ly"], f["ry"] - REF["ry"]
    dv = 0.5 * (dl + dr) if abs(dl - dr) < 10 else (dl if abs(dl) < abs(dr) else dr)
    du = float(np.clip(du, -30, 30))
    dv = float(np.clip(dv, -30, 30))
    return du, dv


def finger_mask(rgb):
    """Pixels of the green finger blobs that reach the bottom edge of the wrist image."""
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)
    m = ((hsv[..., 0] > 40) & (hsv[..., 0] < 85) & (hsv[..., 1] > 80) & (hsv[..., 2] > 30)).astype(np.uint8)
    H, W = m.shape
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    out = np.zeros((H, W), bool)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a >= 1500 and y + h >= H - 3:
            out |= lab == i
    return cv2.dilate(out.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)


def finger_feats(rgb):
    """-> dict(lx, rx, ly, ry): inner edge columns (bottom rows) and top rows of the left / right green fingers."""
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)
    m = ((hsv[..., 0] > 40) & (hsv[..., 0] < 85) & (hsv[..., 1] > 80) & (hsv[..., 2] > 30)).astype(np.uint8)
    H, W = m.shape
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    left = right = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 1500 or y + h < H - 3:          # finger blobs reach the bottom edge
            continue
        # open gripper: the fingers sit in the bottom corners (the right blob can stop ~14 px short of the edge,
        # run 015)
        if x <= 30 and cen[i][0] < 250 and (left is None or a > st[left, 4]):
            left = i
        if x + w >= W - 30 and cen[i][0] > 390 and (right is None or a > st[right, 4]):
            right = i
    if left is None or right is None:
        return None
    out = {}
    for key, i in (("l", left), ("r", right)):
        mm = lab == i
        band = mm[H - 12:H]
        cols = np.nonzero(band.any(0))[0]
        rows = np.nonzero(mm.any(1))[0]
        out[key + "x"] = float(cols.max() if key == "l" else cols.min())
        out[key + "y"] = float(rows.min())
    return out
