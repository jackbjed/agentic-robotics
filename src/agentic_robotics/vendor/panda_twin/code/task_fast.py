"""Fast task programs: ONE Astra call per task, the rest by skills with depth geometry (no more Astra calls).

    MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 ~/miniforge3/envs/teleavatar_client/bin/python -I code/task_fast.py \
        --out out/task_fast --layouts 3        (same layouts and judge as task_bench.py; needs composite_server :8132)

The call: the scene camera (D415) image + the task -> Astra returns the plan (a list of skills from the library
below, in order) and, for each step, its points in that image (yellow = the 2 grasp contacts, blue = the place / press
point or the stick's tip, light blue = 2 rim points of a container). Each point becomes a 3D seed (depth).
Skills (task_bench.TaskProgram's motions; perception = geometry on the depth images, no labels, no Astra):
  pick         seed -> the wrist camera over it -> wrist depth: the blob standing above the table at the seed (connected
               component) -> its top face: centre, height, narrowest width and its direction (flats of a ring or
               cube, across an eggplant) -> grasp across the narrowest width -> close, wait -> lift -> the held
               object's offset in the hand: its top seen between the fingers if the wrist camera sees there, else
               the grasp's geometry (the D415 beside the hand does not see between the fingers)
  survey       before each pick, the next step's place target is measured with the EMPTY hand (a held object could hide
               the target from the wrist camera): the top face of an object, the
               rim of a container (circle fit to the points at its height), a stick (circle fit near its tip)
  put_on       held object over the target (hand offset) -> down until the hand feels the contact (> 1.5 N) ->
               unload -> open
  put_in       carry high, over the rim's centre, down to 15 mm over the rim -> open
  put_on_stick ring over the stick -> down 3 cm; if it hits, back up and retry 1.5 / 2.5 mm to the sides -> open
  press        closed fingers, the wrist camera over the button -> wrist depth: the highest small patch at the seed -> one fingertip
               (not the V between the closed tips, where a button wedges) down until the hand feels it -> fingers
               10 mm apart -> up
Force: what a wrist F/T sensor feels = the contact forces on the fingers and on whatever they hold.
"""
from __future__ import annotations

import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import argparse
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_bench as B  # noqa: E402
import task_bench as T  # noqa: E402

SKILLS = {"pick": "pick", "put_on": "on", "put_in": "in", "put_on_stick": "peg", "press": "press"}
PLAN_PROMPT = """{task}
This is a view of the table from a fixed camera, {W}x{H} pixels; pixel coordinates are x from the left edge, y from
the top edge. The robot is one arm with a parallel-jaw gripper that works from above. Its skills:
- pick: grasp an object. Give "grasp": the 2 points where the fingertips should touch it, on opposite sides across its
  middle (blocks and rings: the middles of two opposite parallel flat faces; long objects: across the thick middle),
  each on the top edge of the object.
- put_on: put the held object on top of another object. Give "point": the centre of that object's top face.
- put_in: drop the held object into a container. Give "rim": [the LEFTMOST point, the RIGHTMOST point] of the top edge
  of the container's rim, as seen in this image.
- put_on_stick: thread the held ring onto a vertical stick. Give "point": the top tip of that stick.
- press: press a button from above with the closed gripper. Give "point": the centre of the button.
Plan the task as a sequence of these skills, in order, and give each step's points in this image. Other objects may
look similar: mark only the ones the task is about.
Respond with this JSON object and nothing else:
{{"steps": [{{"skill": "<skill>", "object": "<what it acts on>", "grasp": [[x, y], [x, y]] or "point": [x, y] or "rim": [[x, y], [x, y]]}}, ...],
 "reasoning": "<1-2 sentences>"}}"""


class Unseen(Exception):
    pass


MARGIN = 0.008                       # an object's points are > 8 mm above the table (the depth noise reaches ~2 mm)
EDGE_PX = 4                          # the D415 spreads a nearer surface ~4 px over the farther one (real frames 10-27):
                                     # an object reads ~2 x 4 px wider than it is


def spread(obs, Q):
    """The D415's edge spread (EDGE_PX) in metres at points Q (world) seen in obs: what a footprint reads too wide on
    each side."""
    return EDGE_PX * float(np.median((Q - obs["eye"]) @ obs["R"][:, 2])) / obs["K"][2]


def unspread(z, px=EDGE_PX):
    """Depth with the D415's edge spread taken back: each pixel the farthest depth within px (a nearer surface's spread
    over the farther one goes; in the ideal sim, without the spread, things come out ~px smaller instead). No depth
    stays no depth."""
    zz = np.where(np.isfinite(z) & (z > 0), z, 0).astype(np.float32)
    far = cv2.dilate(zz, np.ones((2 * px + 1, 2 * px + 1), np.uint8))
    return np.where(zz > 0, far, z).astype(np.float32)


def clean(mask):
    """Drop isolated noise pixels (they would bridge separate objects into one blob)."""
    return cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0


# ---------------------------------------------------------------- geometry on a depth image (no labels)
def valid_depth(obs):
    """The depth pixels to trust: valid, and not "flying". At a depth jump a real stereo camera puts pixels between the
    two surfaces (in mid-air: they bridge separate things); such a pixel is off its 5x5 neighbourhood's median by more
    than 4 mm + 3 sigma of the stereo noise (on a smooth surface, even a steep one, the median is the pixel's own depth;
    a true edge pixel on the minority side of the window is lost too: ~1-2 px)."""
    z = obs["depth"]
    hit = obs.get("_valid")
    if hit is not None and hit[0] is z:
        return hit[1]
    ok = np.isfinite(z) & (z > 0) & (z < 10)
    zz = np.where(ok, z, 0).astype(np.float32)
    med = cv2.medianBlur(zz, 5)
    tol = 0.004 + 3 * 0.08 * zz ** 2 / (obs["K"][2] * 0.055)
    ok &= np.abs(zz - med) <= tol
    obs["_valid"] = (z, ok)
    return ok


def group(mask, gap=8):
    """Connected components of mask, pieces less than `gap` px apart joined (a dark object's depth has holes)."""
    n, lab = cv2.connectedComponents(cv2.dilate(mask.astype(np.uint8), np.ones((gap + 1, gap + 1), np.uint8)), connectivity=8)
    return n, np.where(mask, lab, 0)


def world(obs):
    W, Hh, fx, fy, cx, cy = obs["K"]
    v, u = np.mgrid[0:int(Hh), 0:int(W)]
    z = obs["depth"]; ok = valid_depth(obs)
    zz = np.where(ok, z, 0)
    Xc = np.stack([(u - cx) / fx * zz, (v - cy) / fy * zz, zz], -1)
    return Xc @ obs["R"].T + obs["eye"], ok


def project(obs, X):
    W, Hh, fx, fy, cx, cy = obs["K"]
    Xc = (np.asarray(X, float) - obs["eye"]) @ obs["R"]
    return np.array([fx * Xc[0] / Xc[2] + cx, fy * Xc[1] / Xc[2] + cy])


def surface_left(obs, Q, tol=0.008, n_max=400):
    """How much of a surface seen before (world points Q) is still there in depth image obs: each point projected
    into it, the depth there within tol of the point's = still there, deeper (what was behind it seen) = gone, nearer
    or no depth (hidden, e.g. by what the hand holds) = not counted. -> (still-there fraction of the counted, counted,
    points tried)."""
    Q = np.asarray(Q, float)
    Q = Q[::max(1, len(Q) // n_max)]
    W, Hh, fx, fy, cx, cy = obs["K"]
    Xc = (Q - obs["eye"]) @ obs["R"]
    front = Xc[:, 2] > 0.05
    zc = np.where(front, Xc[:, 2], 1.0)
    u = np.round(fx * Xc[:, 0] / zc + cx).astype(int); v = np.round(fy * Xc[:, 1] / zc + cy).astype(int)
    inside = front & (u >= 0) & (u < int(W)) & (v >= 0) & (v < int(Hh))
    d = np.full(len(Q), np.nan)
    d[inside] = obs["depth"][v[inside], u[inside]]
    ok = np.isfinite(d) & (d > 0)
    same = ok & (np.abs(d - Xc[:, 2]) < tol)
    gone = ok & (d > Xc[:, 2] + tol)
    seen = int(same.sum() + gone.sum())
    return (float(same.sum()) / seen if seen else 0.0), seen, len(Q)


def component(mask, px, reach=25):
    """The connected component of mask at pixel px (or the nearest one within `reach` px); pieces < 8 px apart joined."""
    n, lab = group(mask)
    if n <= 1:
        return None
    u, v = int(round(px[0])), int(round(px[1]))
    Hh, W = mask.shape
    y0, y1, x0, x1 = max(v - reach, 0), min(v + reach + 1, Hh), max(u - reach, 0), min(u + reach + 1, W)
    win = lab[y0:y1, x0:x1]
    if not (win > 0).any():
        return None
    yy, xx = np.nonzero(win)
    k = np.argmin((yy + y0 - v) ** 2 + (xx + x0 - u) ** 2)
    return lab == win[yy[k], xx[k]]


def table_height(P, ok, xy, r=0.14):
    """The table near xy: the most common low height (histogram peak of the lowest points)."""
    d = np.hypot(P[..., 0] - xy[0], P[..., 1] - xy[1])
    z = P[ok & (d < r)][:, 2]
    if len(z) < 50:
        return 0.0
    z = z[z < np.percentile(z, 40)]
    h, e = np.histogram(z, bins=np.arange(z.min(), z.max() + 0.002, 0.001))
    return float(e[np.argmax(h)] + 0.0005)


def narrowest(xy):
    """Narrowest width of a 2D point set and the direction across it (rotating calipers on the hull)."""
    hull = cv2.convexHull(xy.astype(np.float32)).reshape(-1, 2).astype(float)
    best = (1e9, 0.0)
    for a in np.radians(np.arange(0, 180, 1.0)):
        u = np.array([math.cos(a), math.sin(a)])
        w = np.ptp(hull @ u)
        if w < best[0]:
            best = (w, a)
    return best


def kasa(xy):
    A = np.c_[2 * xy, np.ones(len(xy))]; b = (xy ** 2).sum(1)
    s = np.linalg.lstsq(A, b, rcond=None)[0]
    return s[:2], math.sqrt(max(s[2] + s[:2] @ s[:2], 1e-9))


def blob(obs, seed, r=0.09, zmax=None, ray=None, max_width=None, whole=True):
    """The object standing on the table at seed: its points, the table height. With ray (origin, unit direction: the
    scene camera's ray through Astra's mark - exact, only the distance along it is unknown): the object the ray
    passes through, among the objects within 12 cm of the seed."""
    P, ok = world(obs)
    tz = table_height(P, ok, seed[:2])
    d = np.hypot(P[..., 0] - seed[0], P[..., 1] - seed[1])
    m = ok & (d < (0.16 if ray is not None else r)) & (P[..., 2] > tz + MARGIN)
    if zmax is not None:
        m &= P[..., 2] < zmax
    m = clean(m)
    if ray is None:
        c = component(m, project(obs, seed))
    else:
        n, lab = group(m)                                       # (pieces of one object, split by holes, joined)
        o, u, mk = ray                                          # mk: the surface point the scene camera saw at the mark
        best, c = 1e9, None
        for j in range(1, n):
            Q = P[lab == j]
            if len(Q) < 30:
                continue
            v = Q - o
            if np.linalg.norm(v - np.outer(v @ u, u), axis=1).min() > 0.015:
                continue                                        # not on the ray
            if max_width is not None:                           # a pick target must fit the gripper and be whole in
                Hh, W = lab.shape                               # view: a blob cut by the search disc or the image
                ys, xs = np.nonzero(lab == j)                   # border is part of something bigger (a basket's rim)
                cut = (d[lab == j] > 0.155).any() or xs.min() < 3 or ys.min() < 3 or xs.max() > W - 4 or ys.max() > Hh - 4
                if (cut and whole) or narrowest(Q[:, :2])[0] - 2 * spread(obs, Q) > max_width:
                    continue
            dist = np.linalg.norm(Q - mk, axis=1).min() if mk is not None else 0.0
            if dist < best:                                     # of the blobs on the ray: the one at the seen surface
                best, c = dist, lab == j
    if c is None or c.sum() < 30:
        raise Unseen("no object at the seed")
    return P[c], tz


def widths(xy):
    """Width of a 2D point set across each direction 0..179 deg."""
    hull = cv2.convexHull(xy.astype(np.float32)).reshape(-1, 2).astype(float)
    a = np.radians(np.arange(180.0))
    U = np.stack([np.cos(a), np.sin(a)], 1)
    proj = hull @ U.T
    return a, proj.max(0) - proj.min(0)


def finger_clear(obs, Q, c, a, z_tcp_grip, z_tcp_start=None, z_max=None, q=0.04):
    """How many depth points are inside the open fingers' collision boxes swept from the TCP height z_tcp_start down
    to z_tcp_grip, for a grasp at c across direction a. The fin-ray finger (task_bench model): the fingertip pad 41-56
    mm from the centre line (from 15 mm under the TCP to 8 mm over it), the middle section 44-71 mm (10-47 mm over the
    TCP), the base 40-84 mm (60-122 mm over it), +-13 / +-13 / +-18 mm wide. z_max: ignore points above it (the robot's
    own fingers, if the wrist camera sees them)."""
    z_tcp_start = z_tcp_grip if z_tcp_start is None else z_tcp_start
    P, ok = world(obs)
    u = np.array([math.cos(a), math.sin(a)]); v = np.array([-u[1], u[0]])
    rel = P[..., :2] - c
    along, across, z = np.abs(rel @ u), np.abs(rel @ v), P[..., 2]
    hit = np.zeros(z.shape, bool)
    for lo, hi, half, dz0, dz1 in ((q + 0.0014, q + 0.0156, 0.0129, -0.015, 0.0082),
                                   (q + 0.0041, q + 0.0307, 0.0129, 0.0103, 0.0465),
                                   (q + 0.0, q + 0.044, 0.018, 0.060, 0.122)):
        hit |= (along > lo) & (along < hi) & (across < half + 0.002) & (z > z_tcp_grip + dz0) & (z < z_tcp_start + dz1)
    hit &= ok
    if z_max is not None:
        hit &= z < z_max
    return int(hit.sum())


def refine_object(obs, seed, zmax=None, ray=None, max_width=None, whole=True):
    """Top face of the object at seed: centre, top height, table height, narrowest width + direction."""
    Q, tz = blob(obs, seed, zmax=zmax, ray=ray, max_width=max_width, whole=whole)
    top = float(np.percentile(Q[:, 2], 97))
    Tf = Q[Q[:, 2] > top - 0.006]
    if len(Tf) < 20:
        Tf = Q
    w, a = narrowest(Q[:, :2])                                  # vertical sides: the footprint = the top's outline
    hull = cv2.convexHull(Q[:, :2].astype(np.float32)).reshape(-1, 2).astype(float)
    u = np.array([math.cos(a), math.sin(a)]); v = np.array([-u[1], u[0]])
    mid_u = (hull @ u).min() + w / 2                           # centre: middle of the extent across and along
    mid_v = ((hull @ v).min() + (hull @ v).max()) / 2
    c = mid_u * u + mid_v * v
    aa, ww = widths(Q[:, :2])                                   # every direction about as narrow (round: all; octagon:
    near = aa[ww < w + 0.002]                                   # its 4 flats directions; cube: 2)
    return dict(c=c, top=top, table=tz, width=float(w), dir=u, n=len(Q), dirs=near.tolist(), Q=Q)


def refine_rim(obs, seed, r_est, rays=None):
    """A round container's rim: its blob (the one the scene camera's rays through the two rim marks pass by), its
    points near the rim's estimated height -> circle fit, checked (radius, residual)."""
    P, ok = world(obs)
    d = np.hypot(P[..., 0] - seed[0], P[..., 1] - seed[1])
    band = ok & (np.abs(P[..., 2] - seed[2]) < 0.03)            # the scene camera's rim height: +-2 cm
    if rays:
        tz = table_height(P, ok, seed[:2])
        m = clean(ok & (d < 0.30) & (P[..., 2] > tz + MARGIN))
        n, lab = cv2.connectedComponents(m.astype(np.uint8), connectivity=8)
        best, c_ = 1e9, None
        for j in range(1, n):
            Q = P[lab == j]
            if len(Q) < 50:
                continue
            dist = 0.0
            for (o, u, _) in rays:
                v = Q[::3] - o
                dist += np.linalg.norm(v - np.outer(v @ u, u), axis=1).min()
            if dist < best:
                best, c_ = dist, lab == j
        if c_ is None or best > 0.06:
            raise Unseen("container not found on the rim marks' rays")
        Q = P[c_ & band]
    else:
        Q = P[band & (d < 1.5 * r_est + 0.01)]
    if len(Q) < 50:
        raise Unseen("no rim at the seed")
    c, r, inl = ransac_circle(Q[:, :2], 0.5 * r_est, 1.6 * r_est)   # robust to a lid, a handle, a neighbour
    if c is None:
        raise Unseen("no rim circle")
    R_ = Q[inl]
    z = float(np.percentile(R_[:, 2], 75))
    c, r = kasa(R_[:, :2])
    res = float(np.median(np.abs(np.hypot(*(R_[:, :2] - c).T) - r)))
    if not (0.5 * r_est < r < 1.6 * r_est) or res > 0.006:
        raise Unseen(f"rim fit implausible (r {r * 1000:.0f} mm vs {r_est * 1000:.0f}, residual {res * 1000:.1f} mm)")
    return dict(c=c, r=r, z=z, n=int(inl.sum()))


def ransac_circle(xy, r_lo, r_hi, tol=0.003, iters=500, seed=0):
    """The circle (radius in [r_lo, r_hi]) through the most points: centre, radius, inlier mask."""
    rng = np.random.default_rng(seed)
    n = len(xy); best = (0, None, None)
    if n < 3:
        return None, None, None
    for _ in range(iters):
        a, b, c3 = xy[rng.choice(n, 3, replace=False)]
        A = np.array([[b[0] - a[0], b[1] - a[1]], [c3[0] - a[0], c3[1] - a[1]]]) * 2
        if abs(np.linalg.det(A)) < 1e-9:
            continue
        rhs = np.array([b @ b - a @ a, c3 @ c3 - a @ a])
        ctr = np.linalg.solve(A, rhs); r = float(np.linalg.norm(a - ctr))
        if not r_lo < r < r_hi:
            continue
        inl = np.abs(np.hypot(*(xy - ctr).T) - r) < tol
        if inl.sum() > best[0]:
            best = (int(inl.sum()), ctr, inl)
    if best[1] is None or best[0] < 30:
        return None, None, None
    ctr, r = kasa(xy[best[2]])
    return ctr, r, best[2]


def refine_tip(obs, seed, below=0.05, above=0.02, r=0.025):
    """A vertical stick near seed: its points just under the tip -> circle fit (axis), tip height."""
    P, ok = world(obs)
    d = np.hypot(P[..., 0] - seed[0], P[..., 1] - seed[1])
    m = ok & (d < r) & (P[..., 2] > seed[2] - below) & (P[..., 2] < seed[2] + above)
    Q = P[m]
    if len(Q) < 30:
        raise Unseen("no stick at the seed")
    tip = float(np.percentile(Q[:, 2], 98))
    S_ = Q[Q[:, 2] > tip - 0.04]
    c, r = kasa(S_[:, :2])
    if not 0.003 < r < 0.02:                                    # a half-seen cylinder can fit badly: use the mean
        c = S_[:, :2].mean(0)
    return dict(c=c, tip=tip)


def refine_button(obs, seed):
    """The button: the highest small patch near seed (within 2 cm, near the seed's height)."""
    P, ok = world(obs)
    d = np.hypot(P[..., 0] - seed[0], P[..., 1] - seed[1])
    m = ok & (d < 0.02) & (np.abs(P[..., 2] - seed[2]) < 0.012)
    Q = P[m]
    if len(Q) < 15:
        raise Unseen("no button at the seed")
    top = float(np.percentile(Q[:, 2], 95))
    Bp = Q[Q[:, 2] > top - 0.003]
    return np.r_[Bp[:, :2].mean(0), top]


def held_in_hand(obs, tcp, R, width):
    """The held object's top between the fingers: its centre's offset from the TCP (world), its top height."""
    P, ok = world(obs)
    L = (P - tcp) @ R                                           # TCP frame: y = across the fingers, z = down
    m = ok & (np.abs(L[..., 1]) < width / 2 - 0.002) & (np.abs(L[..., 0]) < 0.05) & (L[..., 2] > -0.05) & (L[..., 2] < 0.08)
    Q = P[m]
    if len(Q) < 20:
        raise Unseen("held object not seen")
    top = float(np.percentile(Q[:, 2], 97))
    Tf = (Q[Q[:, 2] > top - 0.006] - tcp) @ R                    # top face, TCP frame: centred across the fingers by
    x = (np.percentile(Tf[:, 0], 2) + np.percentile(Tf[:, 0], 98)) / 2   # the grip; along them: the middle of its extent
    return np.r_[(R @ np.r_[x, 0.0, 0.0])[:2], 0.0], top


# ---------------------------------------------------------------- the one Astra call + the skills' perception
class FastPerception(T.AstraTaskPerception):
    def __init__(self, S, rng, out, tag, task):
        super().__init__(S, rng, out, tag, task)
        self.steps, self.seeds = [], []

    def plan(self, feedback=None):
        """The one call: plan + points on the D415 image -> steps [(skill, object)] and 3D seeds. feedback: a second
        call (only when a check of the first answer failed), with the first answer and what was wrong with it."""
        import cv2 as _cv
        obs, img, o, _ = self.frame("d415")
        msgs = [{"role": "user", "content": [{"type": "text", "text": PLAN_PROMPT.format(task=self.task_text, W=img.shape[1], H=img.shape[0])},
                                             self.img_block(img)]}]
        if feedback:
            msgs += [{"role": "assistant", "content": self.astra_log[-1]["rounds"][0]["reply"]},
                     {"role": "user", "content": [{"type": "text", "text": feedback}]}]
        js, txt, info = self.ask(msgs)
        self.steps, self.seeds = [], []
        marks = []
        steps = (js or {}).get("steps") or []
        for st in steps:
            sk = SKILLS.get(str(st.get("skill", "")).strip())
            if sk is None:
                continue
            seed = None
            try:
                if sk == "pick" and st.get("grasp") and len(st["grasp"]) == 2:
                    uv = [np.asarray(p, float) + o for p in st["grasp"]]
                    mid = (uv[0] + uv[1]) / 2
                    zt = self.top_z(obs, mid, 0.012)            # may be an occluder in front: the ray decides later
                    zt = min(zt if zt is not None else 0.03, 0.06)
                    seed = dict(p=self.on_plane(obs, mid, zt), ray=self.ray_of(obs, mid))
                    marks += [(tuple(p), self.COLS["grasp"]) for p in st["grasp"]]
                elif sk == "in" and st.get("rim") and len(st["rim"]) == 2:
                    c = self.rim_centre(obs, st["rim"], o)
                    if c is not None:
                        uv = [np.asarray(p, float) + o for p in st["rim"]]
                        C = [self.on_plane(obs, q, c[2]) for q in uv]
                        seed = dict(p=c, r=float(np.linalg.norm(C[1][:2] - C[0][:2]) / 2), rays=[self.ray_of(obs, q) for q in uv])
                    marks += [(tuple(p), self.COLS["rim"]) for p in st["rim"]]
                elif st.get("point") is not None:
                    uv = np.asarray(st["point"], float) + o
                    z = self.top_z(obs, uv, {"on": 0.012, "peg": 0.004, "press": 0.006}[sk])
                    ray = self.ray_of(obs, uv)
                    if z is not None:
                        seed = dict(p=self.on_plane(obs, uv, z), ray=ray)
                    elif ray[2] is not None:                    # a small target (a button): the surface at the pixel
                        seed = dict(p=ray[2], ray=ray)
                    marks.append((tuple(st["point"]), self.COLS["place"]))
            except Exception:
                seed = None
            self.steps.append((sk, str(st.get("object", ""))))
            self.seeds.append(seed)
        if marks:
            _cv.imwrite(str(self.dir / f"{self.tag}_plan.jpg"), _cv.cvtColor(self.draw(img, [((float(a), float(b)), c) for (a, b), c in marks]), _cv.COLOR_RGB2BGR))
        self.astra_log.append(dict(t=float(self.S.d.time), keys=["plan"] if not feedback else ["plan (retry)"],
                                   rounds=[dict(reply=txt, **info)], final=js, img=f"{self.tag}_plan.jpg"))
        return self.steps

    def wrist(self):
        return self.observe("L_wrist")

    def ray_of(self, obs, uv):
        """The scene camera's ray through pixel uv, and the surface point it saw there (3x3 median depth)."""
        d = self.ray(obs, uv); d = d / np.linalg.norm(d)
        u, v = int(round(uv[0])), int(round(uv[1]))
        win = obs["depth"][max(v - 1, 0):v + 2, max(u - 1, 0):u + 2]
        win = win[np.isfinite(win)]
        mk = None
        if len(win):
            W, Hh, fx, fy, cx, cy = obs["K"]; z = float(np.median(win))
            mk = obs["R"] @ np.array([(uv[0] - cx) / fx * z, (uv[1] - cy) / fy * z, z]) + obs["eye"]
        return (obs["eye"].copy(), d, mk)


# ---------------------------------------------------------------- the program
class FastProgram(T.TaskProgram):
    def __init__(self, S, per, task):
        super().__init__(S, per, task)
        m, d = S.m, S.d
        cid = m.camera("L_wrist").id
        Rt = d.site_xmat[S.tcp].reshape(3, 3); Rc, c_eye = S.nominal_camera("L_wrist")   # (the mount the program believes)
        self.cam_pos = Rt.T @ (c_eye - S.tcp_now())             # the wrist camera in the TCP frame
        self.cam_axis = Rt.T @ Rc[:, 2]
        self.cam_right = Rt.T @ Rc[:, 0]                          # (image right)
        self.cid = cid
        self.cam_up = float((B.down(0.0) @ self.cam_pos)[2])        # the camera over the TCP (hand pointing down)
        Wc, Hc = B.SIZE["L_wrist"]; fc = Hc / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        self.fc = fc
        q_ = Rc.T @ (S.tcp_now() - c_eye)                         # the TCP in the camera frame: between the fingers
        self.held_in_view = bool(q_[2] > 0.02 and abs(fc * q_[0] / q_[2]) < Wc / 2 - 20 and abs(fc * q_[1] / q_[2]) < Hc / 2 - 20)
        self.truth = [k for _, k in T.TASKS[task]["steps"]]      # for logging errors only

    def log_err(self, what, i, est, true):
        self.events.append(dict(t=float(self.S.d.time), what="err " + what, step=i,
                                err_mm=None if est is None else float(np.linalg.norm(np.asarray(est)[:2] - np.asarray(true)[:2]) * 1000),
                                err_z_mm=None if est is None or len(est) < 3 else float((est[2] - true[2]) * 1000)))

    def look_tcp(self, X, R, tcp_z, band=True):
        """TCP position (at height tcp_z, orientation R) whose wrist camera looks at X; with band, X right of the image
        centre by half the D415's blind band (its left F_DEPTH * b / z + BAND_EXTRA px have no depth: X stays in the
        middle of the part with depth). Not for a look into the box: from off its middle the camera sees one inner wall
        aslant, which reads as things on the floor (no free spot for the last item in 8 of 24 layouts)."""
        e = R @ self.cam_pos; a = R @ self.cam_axis
        t = (X[2] - (tcp_z + e[2])) / a[2]
        g = e + t * a                                           # the camera's view point, relative to the TCP
        sh = (B.F_DEPTH * 0.055 + B.BAND_EXTRA * t) / 2 / self.fc if band else 0.0   # half the band, in metres at X
        r = R @ self.cam_right; r = r[:2] / max(np.linalg.norm(r[:2]), 1e-9)
        return np.r_[X[:2] - sh * r - g[:2], tcp_z]

    def look_height(self, r):
        """TCP height over a target for a wrist look (hand pointing down): a disc of radius r (+2 cm) around it inside
        the image's short side, and the camera at least 2.5 cm beyond the D415's shortest depth range."""
        d_ = max((r + 0.02) / math.tan(math.radians(self.S.m.cam_fovy[self.cid]) / 2), B.MIN_Z + 0.025)
        return d_ - self.cam_up

    def hand_force(self):
        """What a wrist F/T sensor feels: contact forces on the fingers and on what they hold, from anything else."""
        S, m, d = self.S, self.S.m, self.S.d
        arm = {b for b in range(m.nbody) if m.body(b).name.startswith("L_")}
        held = set()
        for i in range(d.ncon):
            c = d.contact[i]; b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            if b1 in S.fingers and b2 not in arm:
                held |= S.subtree_of(b2)
            if b2 in S.fingers and b1 not in arm:
                held |= S.subtree_of(b1)
        return S.force(S.fingers | held, set(range(m.nbody)) - arm - held)

    def check_plan(self, steps):
        """Cheap checks of Astra's answer, no motion: every step has a point, and every point is within the arm's reach
        (inverse kinematics 13 cm above it, hand pointing down, at 4 hand angles)."""
        if not steps:
            return "Your answer had no usable steps."
        for i, (sk, obj) in enumerate(steps):
            sd = self.per.seeds[i]
            if sd is None:
                return f"Step {i + 1} ({sk} '{obj}') had no usable point."
            p = sd["p"]; y0 = self.default_yaw(p[:2])
            if all(self.S.ik(np.r_[p[:2], p[2] + 0.13], B.down(y0 + a), self.S.q_home)[1] > 0.003 for a in np.radians([0, 45, 90, 135])):
                return f"The point you marked for step {i + 1} ({sk} '{obj}') is out of the robot arm's reach (far side of the table)."
        return None

    def joint_cost(self, X, a):
        q, e = self.S.ik(np.asarray(X, float), B.down(a), self.S.q_cmd)
        q2, e2 = self.S.ik(np.asarray(X, float), B.down(a + math.pi), self.S.q_cmd)
        return min(np.abs(q - self.S.q_cmd).max() + 10 * (e > 0.002), np.abs(q2 - self.S.q_cmd).max() + 10 * (e2 > 0.002))

    def still_there(self, g):
        """After a lift with the fingers closed flat: the wrist camera from up there, is the object still where it was?
        Its own surface (surface_left, the points the grasp's look saw on it) at least half still there, or a blob
        within 25 mm of its centre, its narrowest width within 40 % (it slid a little). A held one hangs nearer than
        the D415's shortest range and hides the spot: no depth there. (10-30 twin: the blob test alone took a block
        that had slipped back onto the table, joined in the depth by its neighbour (centre 30 mm off, 92 mm wide),
        for a held soft thing: the run went on empty-handed.)"""
        obs, g2 = self.per.wrist(), None
        frac, seen, n = surface_left(obs, g["Q"])
        on_surface = seen >= max(15, 0.15 * n) and frac >= 0.5
        for d in ((0, 0), (0.015, 0), (-0.015, 0), (0, 0.015), (0, -0.015)):   # (a ring's centre is its hole: around it)
            try:
                g2 = refine_object(obs, np.r_[g["c"] + d, g["top"]], zmax=g["top"] + 0.06, max_width=0.075); break
            except Unseen:
                continue
        blob = g2 is not None and np.linalg.norm(np.asarray(g2["c"]) - np.asarray(g["c"])) < 0.025 and \
            abs(g2["width"] - g["width"]) < 0.4 * g["width"]
        self.event("after the lift, at the grasp spot", surface_there=round(frac, 2), surface_seen=f"{seen}/{n}",
                   found_mm=None if g2 is None else np.round(np.asarray(g2["c"]) * 1000).tolist(),
                   width_mm=None if g2 is None else round(g2["width"] * 1000, 1), still_there=bool(on_surface or blob))
        self.last_left = (np.median(np.asarray(g["Q"])[:, :2], axis=0) if on_surface else   # (where it is now: a
                          np.asarray(g2["c"], float) if blob else None)                     # pick again starts there)
        return bool(on_surface or blob)

    def best_yaw(self, X, yaws):
        """Of equivalent hand angles, the one the arm reaches with the smallest joint change (and away from limits)."""
        S, best = self.S, None
        for y in yaws:
            q, e = S.ik(np.asarray(X, float), B.down(y), S.q_cmd)
            score = np.abs(q - S.q_cmd).max() + (10 if e > 0.002 else 0) + 2 * (np.minimum(q - S.lo, S.hi - q).min() < 0.15)
            if best is None or score < best[0]:
                best = (score, y)
        return best[1]

    WIDE_YAWS = False       # (real_box: True) looks at any of 8 hand angles, a round thing grasped at any angle
    LOOK_FLOOR = -1.0       # (real_box: 0.03) the pick's look: the object's top taken as at least this high (m): 10-30,
                            # real, the scene camera put a screwdriver's top 2.6 cm low, the wrist camera came within
                            # the D415's shortest range of its handle (no depth there: "no object at the seed")

    def look_yaws(self, xy):
        """Hand angles for a look straight down at xy, for best_yaw: the default pair (y0, y0 + 180 deg), or with
        WIDE_YAWS also the two across it and the hand's present angle (+ 90, 180, 270 deg): look_tcp centres the camera
        on xy at any angle. (10-30: the default pair only turned the real wrist 60-75 deg to and fro around the looks:
        2-3 s each at the joint speed limit.)"""
        y0 = self.default_yaw(xy)
        if not self.WIDE_YAWS:
            return (y0, y0 + math.pi)
        yn = math.atan2(self.S.R_cmd[1, 1], self.S.R_cmd[0, 1])
        return [y0 + k * math.pi / 2 for k in range(4)] + [yn + k * math.pi / 2 for k in range(4)]

    def survey(self, i):
        """The place target of step i, measured with the EMPTY hand before the pick (the held object could hide it from
        the wrist camera): the wrist camera over it, then depth geometry."""
        S, per = self.S, self.per
        sd = per.seeds[i]; s = sd["p"]; sk = self.steps[i][0]; k = self.ref(i)
        h = self.look_height(sd.get("r", 0.05) if sk == "in" else 0.05)   # (a container: its whole rim in view)
        y0 = self.default_yaw(s[:2])
        R = B.down(self.best_yaw(np.r_[s[:2], s[2] + h], (y0, y0 + math.pi)))
        S.move(self.look_tcp(s, R, s[2] + h), R, B.OPEN_W, 1.5, "survey the target")
        S.hold(0.2)
        obs = per.wrist()
        if sk == "on":
            g = refine_object(obs, s, zmax=s[2] + 0.10, ray=sd.get("ray")); X = np.r_[g["c"], g["top"]]
        elif sk == "peg":
            t_ = refine_tip(obs, s); X = np.r_[t_["c"], t_["tip"]]
        else:
            rim = refine_rim(obs, s, sd.get("r", 0.05), sd.get("rays")); X = np.r_[rim["c"], rim["z"]]
            P, ok = world(obs)                                   # anything standing above the rim next to the opening
            rel = P[..., :2] - rim["c"]                          # (an open lid): the hand will be turned to clear it
            m_ = ok & (P[..., 2] > rim["z"] + 0.015) & (P[..., 2] < rim["z"] + 0.15) & (np.hypot(*np.moveaxis(rel, -1, 0)) < rim["r"] + 0.04)
            if m_.sum() > 100:                                   # the hand angle whose fingers' footprint (+-55 mm along
                O = rel[m_][::4]                                 # the fingers, +-20 mm across) holds the fewest of them
                best = min(np.radians(np.arange(0, 180, 5.0)), key=lambda a: int(
                    ((np.abs(O @ [math.cos(a), math.sin(a)]) < 0.055) & (np.abs(O @ [-math.sin(a), math.cos(a)]) < 0.020)).sum()))
                self.avoid[i] = best
                self.event("obstacle above the rim: fingers turned", finger_axis_deg=math.degrees(best), points=int(m_.sum()))
        if k:
            self.log_err(f"{sk} target (wrist depth, empty hand)", i, X, T.GTPerception(S).place(k, sk, "L_wrist"))
        self.targets[i] = X

    def run(self):
        self.targets, self.avoid = {}, {}
        steps = self.per.plan()
        bad = self.check_plan(steps)
        if bad:                                              # a second call only when the first answer fails a check
            self.event("plan check failed: asking again", why=bad)
            steps = self.per.plan(feedback=bad + " Mark the right objects (the task's objects are within the arm's reach) "
                                  "and respond with the whole plan again, same JSON format.")
        self.steps = steps
        self.event("plan", steps=[f"{s}:{o}" for s, o in steps])
        if [s for s, _ in steps] != [s for s, _ in T.TASKS[self.task]["steps"]]:
            self.event("plan differs from the reference", reference=[s for s, _ in T.TASKS[self.task]["steps"]])
        for i, (sk, obj) in enumerate(steps):
            if self.per.seeds[i] is None:
                return f"step {i} {sk} {obj}: no point from Astra"
            try:
                if sk == "pick" and i + 1 < len(steps) and steps[i + 1][0] in ("on", "in", "peg") and self.per.seeds[i + 1] is not None:
                    self.survey(i + 1)
                r = getattr(self, "f_" + sk)(i)
            except T.Unreachable as e:
                return f"step {i} {sk}: unreachable ({e})"
            except Unseen as e:
                return f"step {i} {sk}: {e}"
            if r != "ok":
                return f"step {i} {sk}: {r}"
        return "done"

    def ref(self, i):
        return self.truth[i] if i < len(self.truth) else None

    # -- skills
    def f_pick(self, i):
        S, per = self.S, self.per
        s = per.seeds[i]["p"]
        y0 = self.default_yaw(s[:2])
        hl = self.look_height(0.05)                             # (the camera >= MIN_Z + 2.5 cm above it)
        zl = max(s[2], self.LOOK_FLOOR)                         # (its top, for the look: at least LOOK_FLOOR)
        R = B.down(self.best_yaw(np.r_[s[:2], zl + hl], self.look_yaws(s[:2])))
        S.move(self.look_tcp(s, R, zl + hl), R, B.OPEN_W, 2.0, "above")   # the wrist camera over it
        S.hold(0.2)                                             # let the arm settle, then two looks (median centre)
        per.last_obs = per.wrist()
        ray = per.seeds[i].get("ray")
        try:
            g = refine_object(per.last_obs, s, zmax=s[2] + 0.10, ray=ray, max_width=0.075)
        except Unseen:                                          # only part of it in view (a long object): look again
            g = refine_object(per.last_obs, s, zmax=s[2] + 0.10, ray=ray, max_width=0.075, whole=False)   # centred on it
            s = np.r_[g["c"], s[2]]
            S.move(self.look_tcp(s, R, zl + hl), R, B.OPEN_W, 0.8, "re-centre"); S.hold(0.2)
            per.last_obs = per.wrist()
            try:
                g = refine_object(per.last_obs, s, zmax=s[2] + 0.10, ray=ray, max_width=0.075)
            except Unseen:                                      # still cut: a long object (not a rim arc: < 25 cm)
                g = refine_object(per.last_obs, s, zmax=s[2] + 0.10, ray=ray, max_width=0.075, whole=False)
                if np.ptp(g["Q"][:, :2], axis=0).max() > 0.25:
                    raise Unseen("only part of a big object in view")
            self.event("re-centred on the object (it was partly out of view)")
        S.hold(0.1)
        g2 = refine_object(per.wrist(), s, zmax=s[2] + 0.10, ray=ray, max_width=0.075)
        if np.linalg.norm(np.asarray(g2["c"]) - g["c"]) < 0.008 and abs(g2["width"] - g["width"]) < 0.008:
            g["c"] = (g["c"] + g2["c"]) / 2; g["top"] = (g["top"] + g2["top"]) / 2
        else:                                                   # the two looks disagree: keep the narrower (10-29, real:
            self.event("the two looks disagree: the narrower kept",   # the second merged the tape with the block beside it,
                       widths_mm=[round(g["width"] * 1000, 1), round(g2["width"] * 1000, 1)],   # 72 vs 51 mm, and the
                       centres_mm=[np.round(np.asarray(g["c"]) * 1000).tolist(), np.round(np.asarray(g2["c"]) * 1000).tolist()])
            g = g if g["width"] <= g2["width"] else g2         # average put the grasp 2 cm off; a merge only widens)
        self.last_g = g                                         # (the look the grasp uses: its footprint, task_box)
        k = self.ref(i)
        if k:
            gt = T.GTPerception(S).grasp(k, "L_wrist")
            self.log_err("grasp centre (wrist depth)", i, np.r_[g["c"], g["top"]], np.r_[(gt["c"][0][:2] + gt["c"][1][:2]) / 2, gt["top"]])
        h_obj = max(g["top"] - g["table"], 0.01)
        z_grip = max(g["table"] + T.FINGERTIP + 0.005, g["top"] - 0.010)
        obs_g = per.last_obs                                    # grasp direction: among the narrowest ones, one whose
        cands = sorted(set(round(x, 2) for x in g["dirs"][::3])) or [math.atan2(g["dir"][1], g["dir"][0])]   # open
        zf = S.tcp_now()[2] - T.FINGERTIP - 0.01                                                            # (not the
        scored = [(finger_clear(obs_g, g["Q"], g["c"], a, z_grip, z_grip + 0.05, z_max=zf), a) for a in cands]  # fingers
        free = [a for n_, a in scored if n_ == 0] or [min(scored)[1]]                                       # hit nothing
        a0 = math.atan2(g["dir"][1], g["dir"][0])
        free.sort(key=lambda a: abs((a - a0 + math.pi / 2) % math.pi - math.pi / 2))
        av = getattr(self, "avoid_dir", None)                   # (a pick again after a miss or a drop: not across
        if av is not None:                                      # the direction that failed, if there is another)
            free = [a for a in free if abs((a - av + math.pi / 2) % math.pi - math.pi / 2) > math.radians(10)] or free
        pool = free[:4]                                         # (WIDE_YAWS, a round thing, a tape: every direction is
        if self.WIDE_YAWS and len(g["dirs"]) > 90:              # as narrow: of all the clear ones, the least joint
            pool = free[::max(1, len(free) // 20)]              # change; 10-30: the hand turned 60 deg for a tape)
        a = free[0] if len(free) == 1 else min(pool, key=lambda a: self.joint_cost(np.r_[g["c"], g["top"] + 0.05], a))
        self.last_grasp_a = a
        self.event("grasp direction", candidates=len(cands), clear=len(free), blocked=sum(n_ > 0 for n_, _ in scored))
        R = B.down(self.best_yaw(np.r_[g["c"], g["top"] + 0.05], (a, a + math.pi)))
        S.move(np.r_[g["c"], z_grip + 0.05], R, B.OPEN_W, 0.8, "pregrasp")
        S.move(np.r_[g["c"], z_grip], R, B.OPEN_W, 0.5, "descend")
        S.hold(0.15)                                            # settle before closing
        S.move(np.r_[g["c"], z_grip], R, 0.0, 0.6, "close")
        for attempt in range(2):
            for _ in range(15):
                w0 = S.finger_width(); S.hold(1 / 15)
                if abs(S.finger_width() - w0) < 0.0003:
                    break
            if S.finger_width() < 0.072 or attempt == 1:
                break
            self.event("gripper blocked: open, 6 mm higher, close again", width_mm=S.finger_width() * 1000)
            z_grip += 0.006
            S.move(np.r_[g["c"], z_grip], R, B.OPEN_W, 0.4); S.move(np.r_[g["c"], z_grip], R, 0.0, 0.6, "close again")
        w = S.finger_width()
        self.event("grasp", width_mm=w * 1000, narrowest_mm=g["width"] * 1000, h_obj_mm=h_obj * 1000)
        closed = w < 0.004                                      # closed flat: nothing, or something soft squeezed flat
        S.move(np.r_[g["c"], max(0.32, z_grip + 0.20)], R, 0.0, 1.0, "lift")   # (10-29, real: the held cable read 1 mm):
        S.hold(0.2)                                             # lift, and then look whether it is still on the table
        if closed or S.finger_width() < 0.004:
            if self.still_there(g):
                return "missed (closed on nothing)" if closed else "dropped"
            self.event("the fingers closed flat but it left the table: held (soft)", width_mm=S.finger_width() * 1000)
            w = max(w, 0.03)                                    # (let go of it wide enough)
        tcp = S.tcp_now()
        try:                                                    # the held object's centre - TCP, its top: seen
            if not self.held_in_view:                           # between the fingers, or else from the grasp
                raise Unseen("the wrist camera does not see between the fingers")
            off, top = held_in_hand(per.wrist(), tcp, R, w)
        except Unseen as e:                                     # (centred across the fingers, at the grasp point
            off, top = np.zeros(3), g["top"] + tcp[2] - z_grip  # along them, lifted with the hand)
            self.event("in hand: from the grasp", why=str(e))
        bottom_off = top - h_obj - tcp[2]
        self.held = dict(k=k, R=R, h=h_obj, off=off, bottom_off=bottom_off, width=w)
        tr = None if k is None else (S.pose(k)[0][:2] - tcp[:2])
        self.event("in hand", off_mm=np.round(off[:2] * 1000, 1).tolist(), bottom_off_mm=float(bottom_off * 1000),
                   err_mm=None if tr is None else float(np.linalg.norm(off[:2] - tr) * 1000))
        return "ok"

    def _over(self, X, clear):
        """TCP that puts the held object's centre over X with its bottom `clear` above X's height."""
        H = self.held
        return np.r_[X[:2] - H["off"][:2], X[2] + clear - H["bottom_off"]]

    def _look(self, s, what, container=False):
        """Move so the wrist camera looks at seed s from >= 24.5 cm (the held object is beside it, not in the way, and at
        least 4 cm above it; over a container at least 30 cm above the table: an open kettle lid stands ~28 cm high)."""
        S, H = self.S, self.held
        z = max(s[2] + self.look_height(0.05), s[2] + 0.04 - H["bottom_off"], (0.30 - H["bottom_off"]) if container else 0.0)
        S.move(self.look_tcp(s, H["R"], z), H["R"], 0.0, 1.5, "look at " + what)
        return self.per.wrist()

    def f_on(self, i, mode="on"):
        S, per, H = self.S, self.per, self.held
        X = self.targets[i]                                  # surveyed before the pick
        S.move(self._over(X, 0.06), H["R"], 0.0, 1.5, "carry over")
        S.move(self._over(X, 0.012 if mode == "on" else 0.01), H["R"], 0.0, 0.8, "down to it")
        cmd = S.p_cmd.copy()
        if mode == "on":
            for j in range(25):
                cmd[2] -= 0.001
                S.move(cmd, H["R"], 0.0, 0.08, "set down" if j == 0 else None); S.hold(0.04)
                if self.hand_force() > 1.5:
                    self.event("contact", steps=j + 1); break
            rel = cmd + [0, 0, 0.001]
            S.move(rel, H["R"], 0.0, 0.15, "unload")
        else:                                                # thread; if it hits, back up and search sideways
            top = cmd.copy()
            for d_ in [(0, 0), (1.5, 0), (-1.5, 0), (0, 1.5), (0, -1.5), (2.5, 0), (-2.5, 0), (0, 2.5), (0, -2.5)]:
                cmd = top + np.r_[np.array(d_) / 1000, 0]
                S.move(cmd, H["R"], 0.0, 0.15)
                hit = False
                for j in range(32):
                    cmd[2] -= 0.001
                    S.move(cmd, H["R"], 0.0, 0.06, "thread" if j == 0 else None); S.hold(0.02)
                    if self.hand_force() > 2.0:
                        hit = True; break
                if not hit:
                    break
                self.event("hit the stick: back up, search", offset_mm=list(d_))
                S.move(top + np.r_[np.array(d_) / 1000, 0], H["R"], 0.0, 0.2)
            rel = cmd.copy()
        S.move(rel, H["R"], B.OPEN_W, 0.5, "release")
        S.move(rel + [0, 0, 0.10], H["R"], B.OPEN_W, 0.8, "retreat"); S.hold(0.6)
        self.held = None
        return "ok"

    def f_peg(self, i):
        return self.f_on(i, mode="peg")

    def f_in(self, i):
        S, per, H = self.S, self.per, self.held
        X = self.targets[i]                                  # surveyed before the pick
        if i in self.avoid:                                  # turn the hand: the fingers' axis where the obstacle
            a = self.avoid[i]                                # is not (the hand is thin across it: +-13 mm)
            off_local = H["R"].T @ np.r_[H["off"][:2], 0.0]
            Rn = B.down(self.best_yaw(np.r_[X[:2], X[2] + 0.2], (a, a + math.pi)))
            H["R"], H["off"] = Rn, np.r_[(Rn @ off_local)[:2], 0.0]
        hi = self._over(X, 0.14); hi[2] = max(hi[2], 0.30 - H["bottom_off"])
        S.move(hi, H["R"], 0.0, 1.5, "carry over")
        low = self._over(X, 0.015); low[2] = max(low[2], X[2] + T.FINGERTIP + 0.015)
        S.move(low, H["R"], 0.0, 0.8, "lower to the rim")
        S.move(S.p_cmd, H["R"], B.OPEN_W, 0.5, "release")
        S.move(S.p_cmd + [0, 0, 0.10], H["R"], B.OPEN_W, 0.8, "retreat"); S.hold(0.8)
        self.held = None
        return "ok"

    def f_press(self, i):
        S, per = self.S, self.per
        s = per.seeds[i]["p"]; k = self.ref(i)
        y0 = self.default_yaw(s[:2])
        R = B.down(self.best_yaw(np.r_[s[:2], s[2] + T.FINGERTIP + 0.12], (y0, y0 + math.pi)))
        S.move(self.look_tcp(s, R, s[2] + max(T.FINGERTIP + 0.12, self.look_height(0.05))), R, 0.0, 1.8, "above the button")
        S.hold(0.2)
        p = refine_button(per.wrist(), s)
        if k:
            self.log_err("button (wrist depth)", i, p, T.GTPerception(S).place(k, "press", "L_wrist"))
        p = p.copy(); p[:2] += R[:2, 1] * 0.0085                 # one fingertip's flat underside on the button (not the
        S.move(np.r_[p[:2], p[2] + T.FINGERTIP + 0.02], R, 0.0, 0.8, "approach")   # V between the closed tips: it wedges)
        cmd = S.p_cmd.copy()
        for j in range(30):
            cmd[2] -= 0.001
            S.move(cmd, R, 0.0, 0.06, "press" if j == 0 else None); S.hold(0.02)
            if self.hand_force() > 3.0 and S.d.qpos[S.btn_q] < -0.003 or cmd[2] < p[2] + T.FINGERTIP - 0.006:
                break
        S.hold(0.2)
        self.event("pressed", button_mm=float(S.d.qpos[S.btn_q] * 1000), latched=S.latched)
        S.move(cmd, R, 0.005, 0.2)                              # fingers 10 mm apart: nothing stays pinched
        S.move(cmd + [0, 0, 0.12], R, 0.005, 0.5, "back up"); S.hold(0.8)
        return "ok"


def _subtree_of(self, b):
    r = self.root_of(b)
    return {c for c in range(self.m.nbody) if self.root_of(c) == r} if r != 0 else {b}


T.TaskSim.subtree_of = _subtree_of


# ---------------------------------------------------------------- runner
def run_job(job):
    task, seed, idx, out, save = job
    L = T.sample_layout(task, seed, idx)
    if L is None:
        return dict(task=task, layout=idx, result="layout rejected")
    S = T.TaskSim(); T.apply_layout(S, L)
    tag = f"{task}_{idx:02d}_fast"
    per = FastPerception(S, np.random.default_rng([seed, idx, 5]), out, tag, task)
    prog = FastProgram(S, per, task)
    t0 = time.time()
    try:
        r = prog.run()
    except Exception as e:
        import traceback
        r = f"crash: {e!r}"[:200]; traceback.print_exc()
    wall = time.time() - t0
    res = T.judge(S, task)
    api = sum(x["s"] for a in per.astra_log for x in a["rounds"])
    row = dict(task=task, layout=idx, perception="fast", program=r, result="success" if res.pop("ok") else "fail", **res,
               sim_s=round(float(S.d.time), 1), wall_s=round(wall, 1), astra_s=round(api, 1), astra_calls=per.calls,
               astra_cost=round(per.cost, 3), events=prog.events[-30:], astra=per.astra_log, layout_spec=L)
    if save:
        np.savez_compressed(Path(out) / f"trace_{tag}.npz", t=np.array([x[0] for x in S.rec]), qpos=np.array([x[1] for x in S.rec]))
        json.dump(dict(row=row, layout=L, timeline=S.timeline), open(Path(out) / f"trace_{tag}.json", "w"),
                  default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    per.close()
    with open(Path(out) / "runs.jsonl", "a") as f:
        f.write(json.dumps(row, default=lambda o: o.tolist() if hasattr(o, "tolist") else (o if isinstance(o, (int, float, str)) else str(o))) + "\n")
    return row


def summarize(rows, out):
    lines = ["| task | success | Astra calls | Astra s | robot motion (sim) s | wall s | results |", "|---|---|---|---|---|---|---|"]
    for t in T.TASKS:
        R = sorted([r for r in rows if r.get("task") == t], key=lambda r: r["layout"])
        if not R:
            continue
        ok = sum(r["result"] == "success" for r in R)
        f = lambda k: np.mean([r.get(k, 0) for r in R])
        det = "; ".join(f'{r["layout"]}: {r["result"]}' + ("" if r["result"] == "success" else f' ({r.get("program")})') for r in R)
        lines.append(f"| {t} | {ok}/{len(R)} | {f('astra_calls'):.1f} | {f('astra_s'):.0f} | {f('sim_s'):.0f} | {f('wall_s'):.0f} | {det} |")
    txt = "\n".join(lines) + f"\n\nTotal {sum(r['result'] == 'success' for r in rows)}/{len(rows)}, Astra ${sum(r.get('astra_cost', 0) for r in rows):.2f}\n"
    (Path(out) / "summary.md").write_text(txt)
    return txt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tasks", nargs="+", default=list(T.TASKS))
    ap.add_argument("--layouts", type=int, default=3)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--save-traces", action="store_true")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in open(a.out / "runs.jsonl")] if (a.out / "runs.jsonl").exists() else []
    done = {(r["task"], r["layout"]) for r in rows}
    jobs = [(t, a.seed, i, str(a.out), a.save_traces) for t in a.tasks for i in (a.only or range(a.layouts)) if (t, i) not in done]
    print(f"{len(done)} done, {len(jobs)} to run", flush=True)
    t0 = time.time()
    with Pool(max(1, min(a.workers, len(jobs)))) as pool:
        for r in pool.imap_unordered(run_job, jobs):
            rows.append(r)
            print(f"{r['task']} {r['layout']} ({time.time() - t0:.0f}s): {r['result']} - {r.get('program')}  calls {r.get('astra_calls')} "
                  f"astra {r.get('astra_s')}s sim {r.get('sim_s')}s wall {r.get('wall_s')}s", flush=True)
    print(summarize(rows, a.out))


if __name__ == "__main__":
    main()
