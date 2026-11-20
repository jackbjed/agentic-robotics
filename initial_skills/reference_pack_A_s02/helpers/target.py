"""Find the target tape roll in a frame."""
import json
import re
import numpy as np
import cv2

from helpers.geom import pix_to_plane
from helpers.percep import fill_holes, largest_component

# outer radii from the closed finger width in practice run 002 (red 46 mm, yellow 51 mm)
ROLL_R = {"red": 0.023, "yellow": 0.0255}
ROLL_H = 0.021      # height (m), wrist depth in run 001


def hue_ok(rgb, mask, color):
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)
    sel = mask & (hsv[..., 1] > 90) & (hsv[..., 2] > 60)
    n = int(sel.sum())
    if n < 20:
        return 0.0
    h = hsv[..., 0][sel].astype(int)
    if color == "red":
        good = (h <= 10) | (h >= 165)
    elif color == "yellow":
        good = (h >= 18) & (h <= 40)
    else:
        return 1.0
    return float(good.mean()) * min(1.0, n / max(1.0, 0.15 * mask.sum()))


def fit_circle(pts):
    """Algebraic (Kasa) circle fit -> (u, v, r) or None."""
    p = np.asarray(pts, float)
    if len(p) < 10:
        return None
    A = np.c_[2 * p[:, 0], 2 * p[:, 1], np.ones(len(p))]
    b = (p ** 2).sum(1)
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    u, v, c = sol
    r2 = c + u * u + v * v
    if r2 <= 0:
        return None
    return float(u), float(v), float(np.sqrt(r2))


def mask_circle(mask, occl=None):
    """Outer contour of the largest blob -> dict(u, v, a, b, mask, vis) or None.
    occl: pixels that can hide the roll (our fingers). When part of the outline is the image border or an occluder,
    the ellipse of the whole contour is pulled towards the visible part (run 030: the roll half hidden at the image
    bottom at hover, the grasp blocked on its wall); then a circle is fitted to the true edge points only."""
    m = largest_component(np.asarray(mask, bool))
    m8 = m.astype(np.uint8)
    cnts, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if len(c) < 6:
        return None
    (u, v), (a, b), ang = cv2.fitEllipse(c)
    x, y, w, h = cv2.boundingRect(c)
    H, W = m.shape
    pts = c.reshape(-1, 2)
    good = (pts[:, 0] > 2) & (pts[:, 0] < W - 3) & (pts[:, 1] > 2) & (pts[:, 1] < H - 3)
    if occl is not None:
        oc = cv2.dilate(np.asarray(occl, np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
        good &= ~oc[pts[:, 1], pts[:, 0]]
    vis = float(good.mean())
    fit = "ellipse"
    if vis < 0.9:
        cf = fit_circle(pts[good])
        # the visible arc must be long enough and the circle plausible (not larger than twice the ellipse)
        if cf is not None and good.sum() > 40 and 0.5 * max(a, b) < 2 * cf[2] < 2.0 * max(a, b) + 40:
            u, v, a, b = cf[0], cf[1], 2 * cf[2], 2 * cf[2]
            fit = "circle"
    return dict(u=float(u), v=float(v), a=float(a), b=float(b), vis=round(vis, 2), fit=fit,
                touches=bool(x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1),
                mask=fill_holes(m))


def find_roll(ctx, obs, phrases, color, z_top=ROLL_H, expect=None, max_dev=0.05, log="find", shift=(0.0, 0.0),
              occl=None):
    """Segment the roll; return dict(x, y, z, r, score, mask, ...) of the best plausible mask, or None.
    shift: the calibration's image shift (pixels), removed before the pixel is turned into a point.
    occl: pixels that may hide the roll (the fingers in the wrist image)."""
    W, H, fx, fy, cx, cy = obs["K"]
    cands = []
    for ph in phrases:
        try:
            res = ctx.segmenter.segment(obs["rgb"], ph)
        except Exception as e:
            ctx.log(log + "_err", ph=ph, err=repr(e))
            continue
        for i, (m, s) in enumerate(res[:5]):
            m = np.asarray(m, bool)
            if m.sum() < 30:
                continue
            ci = mask_circle(m, occl)
            if ci is None:
                continue
            X = pix_to_plane(obs, ci["u"] - shift[0], ci["v"] - shift[1], z_top)
            if X is None:
                continue
            rng = float(np.linalg.norm(X - np.asarray(obs["eye"])))
            r_m = 0.25 * (ci["a"] + ci["b"]) / fx * rng
            hq = hue_ok(obs["rgb"], ci["mask"], color)
            # white roll scored hue 0.47-0.49 for "yellow" (run 004 ep01): demand a clear colour match
            ok = 0.015 < r_m < 0.034 and hq > 0.7
            if ci["touches"]:
                s = s * 0.6
            dev = None
            if expect is not None:
                dev = float(np.hypot(X[0] - expect[0], X[1] - expect[1]))
                ok = ok and dev < max_dev
            cands.append(dict(x=float(X[0]), y=float(X[1]), z=z_top, r=r_m, score=float(s), ph=ph, hue=hq,
                              u=ci["u"], v=ci["v"], ok=bool(ok), dev=dev, mask=ci["mask"], fit=ci["fit"],
                              vis=ci["vis"]))
        if any(c["ok"] and c["score"] > 0.4 for c in cands):
            break
    ctx.log(log, cands=[{k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items() if k != "mask"}
                        for c in cands])
    good = [c for c in cands if c["ok"]]
    if not good:
        return None
    return max(good, key=lambda c: c["score"] * (0.5 + c["hue"]))


def ask_vlm(ctx, obs, name):
    """One model call: the target's pixel in obs. -> (u, v) or None."""
    vlm = getattr(ctx, "vlm", None)
    if vlm is None:
        return None
    try:
        r = vlm.ask(f'This is a top-down camera image of a table. Give the pixel [x, y] of the centre of the {name} '
                    f'(a small ring of {name.split()[0]} tape). Answer only JSON {{"target": [x, y]}}.', [obs])
    except Exception as e:
        ctx.log("vlm_err", err=repr(e))
        return None
    if not r.get("ok"):
        return None
    m = re.search(r"\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]", r.get("text", ""))
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))
