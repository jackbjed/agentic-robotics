"""Finding the task's things again in the scene camera after they were moved (10-30): no new Astra call. At the plan,
each step's thing is cut out of the scene image around Astra's marks (a pick: around its two grasp points; the box:
around its put_in point, bigger); later, before the step, a fresh scene image is searched for it (normalized
cross-correlation, colour, a round mask, the patch turned in 20-degree steps). If it is clearly gone from where it was
(the match there poor) and clearly somewhere else (a good match there), the step's point moves there; the wrist camera's
look then measures it as before.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

ANGLES = np.arange(0, 360, 20)
SCALES = (0.7, 0.85, 1.0, 1.15, 1.3)     # (10-30, real: a mouse moved from the front of the table to the back looked
                                         # ~20 % smaller in the scene camera: not found at its own size)


def _disc(n):
    yy, xx = np.mgrid[0:n, 0:n] - (n - 1) / 2
    return ((xx ** 2 + yy ** 2) <= (n / 2) ** 2).astype(np.float32)


def make_template(rgb, uv, size):
    """The patch (size x size px, odd) of rgb around uv (x, y), for match(). -> dict or None (off the image)."""
    n = int(size) | 1
    x, y = int(round(uv[0])), int(round(uv[1]))
    h = n // 2
    if x - h < 0 or y - h < 0 or x + h + 1 > rgb.shape[1] or y + h + 1 > rgb.shape[0]:
        return None
    p = rgb[y - h:y + h + 1, x - h:x + h + 1].astype(np.float32)
    rots = []                                                 # (scale, angle, patch, mask): turned and resized
    for sc in SCALES:
        ns = max(int(round(n * sc)) | 1, 9)
        ps = cv2.resize(p, (ns, ns), interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_LINEAR)
        hs = ns // 2
        m = cv2.merge([_disc(ns)] * 3)
        for a in ANGLES:
            M = cv2.getRotationMatrix2D((hs, hs), float(a), 1.0)
            rots.append((sc, int(a), cv2.warpAffine(ps, M, (ns, ns), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT), m))
    half = [(sc, a, cv2.resize(r, (max(r.shape[1] // 2, 5) | 1,) * 2, interpolation=cv2.INTER_AREA),   # (the coarse
             cv2.resize(m, (max(m.shape[1] // 2, 5) | 1,) * 2, interpolation=cv2.INTER_NEAREST)) for sc, a, r, m in rots]   # pass)
    return dict(uv=np.array([x, y], float), n=n, rots=rots, half=half)


def match(rgb, tpl, roi=None, top=3):
    """Best place of the template in rgb (any of its turns and sizes): (uv, score, angle deg). roi = (x0, y0, x1, y1) to
    search. Coarse to fine (10-30: 4.5 s at full size): at half size everywhere, then at full size within 6 px of the
    `top` best (size, turn) places."""
    img = rgb.astype(np.float32)
    x0, y0 = 0, 0
    if roi is not None:
        x0, y0, x1, y1 = [int(v) for v in roi]
        img = img[y0:y1, x0:x1]
    if img.shape[0] * img.shape[1] > 120 * 120 and "half" in tpl:
        small = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA)
        cands = []
        for k, (s_, a, r, m3) in enumerate(tpl["half"]):
            if r.shape[0] > small.shape[0] or r.shape[1] > small.shape[1]:
                continue
            res = np.nan_to_num(cv2.matchTemplate(small, r, cv2.TM_CCOEFF_NORMED, mask=m3), nan=-1.0, posinf=-1.0, neginf=-1.0)
            _, sc, _, loc = cv2.minMaxLoc(res)
            cands.append((sc, k, (2 * loc[0] + r.shape[1], 2 * loc[1] + r.shape[0])))   # (centre, full-size px in img)
        best = (None, -1.0, 0)
        for sc0, k, (cx, cy) in sorted(cands, reverse=True)[:top]:
            s_, a, r, m3 = tpl["rots"][k]
            h = r.shape[0] // 2
            wx0, wy0 = max(cx - h - 6, 0), max(cy - h - 6, 0)
            wx1, wy1 = min(cx + h + 7, img.shape[1]), min(cy + h + 7, img.shape[0])
            win = img[wy0:wy1, wx0:wx1]
            if win.shape[0] < r.shape[0] or win.shape[1] < r.shape[1]:
                continue
            res = np.nan_to_num(cv2.matchTemplate(win, r, cv2.TM_CCOEFF_NORMED, mask=m3), nan=-1.0, posinf=-1.0, neginf=-1.0)
            _, sc, _, loc = cv2.minMaxLoc(res)
            if sc > best[1]:
                best = (np.array([loc[0] + wx0 + x0 + h, loc[1] + wy0 + y0 + h], float), float(sc), a)
        if best[0] is not None:
            return best
    best = (None, -1.0, 0)
    for s_, a, r, m3 in tpl["rots"]:
        if r.shape[0] > img.shape[0] or r.shape[1] > img.shape[1]:
            continue
        res = cv2.matchTemplate(img, r, cv2.TM_CCOEFF_NORMED, mask=m3)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, sc, _, loc = cv2.minMaxLoc(res)
        if sc > best[1]:
            best = (np.array([loc[0] + x0 + r.shape[1] // 2, loc[1] + y0 + r.shape[0] // 2], float), float(sc), a)
    return best


def score_at(rgb, tpl, uv, r=5):
    """The template's best score within r px of uv, at its own size and about its own turn (0, +-20 deg): is it still
    there, unmoved? (10-30: with every size and turn, a box slid 7 cm still scored 0.65 on the place it left.)"""
    tpl = dict(tpl, rots=[q for q in tpl["rots"] if q[0] == 1.0 and q[1] in (0, 20, 340)])
    tpl.pop("half", None)
    n = tpl["n"]; h = n // 2
    x, y = int(round(uv[0])), int(round(uv[1]))
    x0, y0 = max(x - h - r, 0), max(y - h - r, 0)
    x1, y1 = min(x + h + r + 1, rgb.shape[1]), min(y + h + r + 1, rgb.shape[0])
    if x1 - x0 < n or y1 - y0 < n:
        return -1.0
    return match(rgb, tpl, (x0, y0, x1, y1))[1]


def pick_size(g1, g2):
    """A pick's patch size from Astra's two grasp points (px): about the thing itself, 34-72 px (10-30: 2.2 x took in a
    neighbour 3 cm away: moved alone, it no longer matched)."""
    d = float(np.linalg.norm(np.asarray(g1, float) - np.asarray(g2, float)))
    return int(np.clip(1.6 * d, 34, 72))
