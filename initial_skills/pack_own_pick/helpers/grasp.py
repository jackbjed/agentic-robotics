"""Grasp candidates around a tape roll, scored against an obstacle mask in a top-down frame."""
import math
import numpy as np

from helpers.geom import project, pix_to_plane
from helpers.fingers import finger_mask

FINGER_T = 0.011     # finger thickness along the closing axis, pad face outward (m), assumed + margin
FINGER_HW = 0.0115   # half width across the closing axis (m)
D_IN = 0.004         # wall grasp: inner pad's distance from the roll centre (hole radius ~14 mm; 8 mm hit the wall, run 003)


def obstacle_mask(ctx, obs, P, ok, c, r, target_mask=None, phrases=("small object",)):
    """Pixels that are (probably) something other than the target: segmenter masks + raised depth."""
    H, W = obs["rgb"].shape[:2]
    seg = np.zeros((H, W), bool)
    for ph in phrases:
        try:
            for m, s in ctx.segmenter.segment(obs["rgb"], ph):
                if s > 0.15:
                    seg |= np.asarray(m, bool)
        except Exception as e:
            ctx.log("obst_err", err=repr(e))
    zz = np.where(ok, P[..., 2], 0.0)
    high = ok & (zz > HTH) & (zz < 0.25)
    # flat things under the fingertips (sticky pad, cable) do not block; masked pixels without depth do
    obst = high | (seg & ~ok)
    # heights of the blocking surfaces. Masked pixels without depth: dark ones are black objects (3 cm assumed);
    # bright ones are too near the camera for depth, i.e. tall (at least eye height - MIN_DEPTH; run 036: a box top
    # at 0.053+ with no depth); their true height is unknown: taken as TALL_H
    hgt = np.where(high, zz, 0.0)
    nod = seg & ~ok
    hgt[nod] = 0.03
    try:
        import cv2
        val = cv2.cvtColor(np.ascontiguousarray(obs["rgb"]), cv2.COLOR_RGB2HSV)[..., 2]
        if float(np.asarray(obs["eye"], float)[2]) - 0.28 > 0.03:
            # only large blobs: depth noise leaves small holes on low bright things too (run 038)
            # and not near our fingers: they cast a depth shadow on flat things beside them (run 039)
            fm = cv2.dilate(finger_mask(obs["rgb"]).astype(np.uint8), np.ones((61, 61), np.uint8)).astype(bool)
            tb = (nod & (val > 90) & ~fm).astype(np.uint8)
            n, lab, st, _ = cv2.connectedComponentsWithStats(tb, 8)
            big = np.nonzero(st[1:, 4] >= TALL_PX)[0] + 1
            if len(big):
                hgt[np.isin(lab, big)] = TALL_H
    except Exception:
        pass
    obst &= ~finger_mask(obs["rgb"])          # our own fingers (start frame)
    # remove the target's own disc (on the plane z=0.01 and on the depth points)
    Wd, Hd, fx, fy, cx, cy = obs["K"]
    vv, uu = np.mgrid[0:H, 0:W]
    R = np.asarray(obs["R"], float)
    e = np.asarray(obs["eye"], float)
    d = np.stack([(uu - cx) / fx, (vv - cy) / fy, np.ones_like(uu, float)], -1) @ R.T
    near = np.zeros((H, W), bool)
    for zp in (0.004, ROLLTOP):
        s = (zp - e[2]) / d[..., 2]
        X = e[None, None, :] + s[..., None] * d
        dist = np.hypot(X[..., 0] - c[0], X[..., 1] - c[1])
        obst &= ~(dist < r + 0.003)
        near |= dist < r + 0.006
    if target_mask is not None:
        # the target's mask can leak onto a neighbour: trust it only close to the roll
        obst &= ~(target_mask & near)
    if not HEIGHTS:
        return obst
    return np.where(obst, hgt, 0.0).astype(np.float32)


def blocked_at(obst, vi, ui, z):
    """Is the finger sample at height z (pixel (ui, vi)) blocked? obst: bool mask, or surface heights (m). With
    heights, a sample is blocked when the visible surface on its ray is at least about its height: it is inside or
    behind something (run 033: a fingertip hit the top of a tall leaning box 5 cm up; the base looked free)."""
    o = obst[vi, ui]
    if obst.dtype == bool:
        return o.astype(float)
    return (o >= max(HTH, z - 0.006)).astype(float)


def debug_image(obs, obst, target_mask, cand, c):
    img = np.asarray(obs["rgb"]).copy()
    obst = np.asarray(obst) > 0
    img[obst] =(0.5 * img[obst] + [127, 0, 0]).astype(np.uint8)
    if target_mask is not None:
        img[target_mask] = (0.5 * img[target_mask] + [0, 127, 0]).astype(np.uint8)
    if cand is not None:
        u = np.array([math.cos(cand["yaw"]), math.sin(cand["yaw"])])
        cc = np.asarray(c[:2], float)
        if cand["kind"] == "outer":
            segs = [(cc, u, cand["w"]), (cc, -u, cand["w"])]
        else:
            ro = cand["w"] * 2 + D_IN
            segs = [(cc, u, ro)]
        for cen, uu, a in segs:
            n = np.array([-uu[1], uu[0]])
            for s in np.linspace(a, a + FINGER_T, 8):
                for t in np.linspace(-FINGER_HW, FINGER_HW, 12):
                    p = cen + s * uu + t * n
                    uv, _ = project(obs, [p[0], p[1], 0.011])
                    x, y = int(uv[0, 0]), int(uv[0, 1])
                    if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
                        img[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = (0, 0, 255)
    return img


ROLLTOP = 0.021
HTH = 0.009          # heights above this (m) block a finger (fingertips at 0.011)
HEIGHTS = True       # obstacle map with surface heights; fingers also checked higher up (HIGH_Z)
HIGH_Z = (0.03, 0.045)
TALL_H = 0.12        # height given to bright segmented pixels with no depth seen from >= 0.31 m (too near: tall)
TALL_PX = 1500       # ... in blobs of at least this many pixels


def footprint_cost(obs, obst, centre, u, a, b):
    """Blocked fraction of the footprint, and of it grown by 4 and 8 mm (clearance for errors), combined."""
    b0 = footprint_block(obs, obst, centre, u, a, b)
    b4 = footprint_block(obs, obst, centre, u, a, b + 0.004, m=0.004)
    b8 = footprint_block(obs, obst, centre, u, a, b + 0.008, m=0.008)
    return b0 + 0.5 * b4 + 0.25 * b8, (round(b0, 3), round(b4, 3), round(b8, 3))


def footprint_block(obs, obst, centre, u, a, b, z=0.011, m=0.0):
    """Fraction of the finger footprint (from a to b along u from centre, +-(FINGER_HW+m) across) that is blocked."""
    n = np.array([-u[1], u[0]])
    ss = np.linspace(a, b, 7)
    tt = np.linspace(-FINGER_HW - m, FINGER_HW + m, 11)
    S, Tt = np.meshgrid(ss, tt)
    xy = centre[None, :] + S.reshape(-1, 1) * u[None, :] + Tt.reshape(-1, 1) * n[None, :]
    zs = (z, 0.016) if obst.dtype == bool else (z, 0.016) + HIGH_Z
    H, W = obst.shape
    blks = []
    for zz in zs:
        X = np.c_[xy, np.full(len(xy), zz)]
        uv, _ = project(obs, X)
        ui = np.round(uv[:, 0]).astype(int)
        vi = np.round(uv[:, 1]).astype(int)
        inside = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
        blk = np.full(len(X), 0.5)
        blk[inside] = blocked_at(obst, vi[inside], ui[inside], zz)
        blks.append(blk)
    return float(np.concatenate(blks).mean())


HAND_L = 0.105       # the hand body's half length along the closing axis (m), assumed (Panda hand ~0.2 m)
HAND_W = 0.03        # its half width across
HAND_Z = (0.07, 0.09)  # heights of its underside at the grasp (fingertips at 0.011, fingers ~5 cm long)
HAND_COST = 2.0      # run 045 (0.0) vs 043 (2.0) on 16 L3: +0.14 s/episode of noise; 697 (tall box): 26 s -> 11 s


def hand_block(obs, obst, centre, u):
    """Blocked fraction of the hand body's footprint at the grasp (tall things only; run 032/033: the hand hit a
    tall box ~6 cm beyond a free finger). 0 for a plain bool mask."""
    if obst.dtype == bool:
        return 0.0
    u = np.asarray(u, float)
    n = np.array([-u[1], u[0]])
    S, Tt = np.meshgrid(np.linspace(-HAND_L, HAND_L, 15), np.linspace(-HAND_W, HAND_W, 5))
    xy = np.asarray(centre, float)[None, :] + S.reshape(-1, 1) * u[None, :] + Tt.reshape(-1, 1) * n[None, :]
    H, W = obst.shape
    blks = []
    for zz in HAND_Z:
        uv, _ = project(obs, np.c_[xy, np.full(len(xy), zz)])
        ui = np.round(uv[:, 0]).astype(int)
        vi = np.round(uv[:, 1]).astype(int)
        inside = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
        blk = np.zeros(len(xy))            # outside the frame: unknown, taken as free (tall things are rare)
        blk[inside] = blocked_at(obst, vi[inside], ui[inside], zz)
        blks.append(blk)
    return float(np.concatenate(blks).mean())


def disc_block(obs, obst, centre, rad, z=0.012, skip=None):
    """Blocked fraction of a disc (sampled on a grid) at height z; skip=(c, r): leave out points within r of c."""
    g = np.linspace(-rad, rad, 13)
    X, Y = np.meshgrid(g, g)
    sel = X ** 2 + Y ** 2 <= rad * rad
    P = np.c_[centre[0] + X[sel], centre[1] + Y[sel], np.full(int(sel.sum()), z)]
    if skip is not None:
        keep = np.hypot(P[:, 0] - skip[0][0], P[:, 1] - skip[0][1]) > skip[1]
        P = P[keep]
        if len(P) == 0:
            return 0.0
    uv, _ = project(obs, P)
    H, W = obst.shape
    ui = np.round(uv[:, 0]).astype(int)
    vi = np.round(uv[:, 1]).astype(int)
    inside = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    blk = np.full(len(P), 0.5)
    blk[inside] = blocked_at(obst, vi[inside], ui[inside], z)
    return float(blk.mean())


DRAG_L = 0.045       # hand travel of a drag (the roll moves ~L - slack in the hole)


def drag_candidates(obs, obst, c, r):
    """Drags: finger A in the hole (pad D_IN from the centre along n), finger B 2w further on free table; the hand
    moves DRAG_L along d. -> list of dict(n_yaw, w, d, tcp0, score), best first."""
    c = np.asarray(c[:2], float)
    dirs = [np.array([math.cos(a), math.sin(a)]) for a in np.arange(16) * math.pi / 8]
    # where can the roll go: free disc around its new centre (its present disc is already free of obstacles)
    # in level 3 every direction is blocked within a few mm: score only the area the roll sweeps into, and accept
    # pushing a neighbour a little (run 019: a 0.3 cut-off left no drag at all)
    dcost = []
    skip = (c, r + 0.002)
    for d in dirs:
        c2 = c + (DRAG_L - 0.006) * d
        c1 = c + 0.5 * (DRAG_L - 0.006) * d
        dcost.append(disc_block(obs, obst, c2, r + 0.004, skip=skip) + 0.5 * disc_block(obs, obst, c1, r + 0.004,
                                                                                         skip=skip))
    out = []
    for k in range(16):
        a = k * math.pi / 8
        n = np.array([math.cos(a), math.sin(a)])
        for w in (0.03, 0.04):
            lo, hi = D_IN + 2 * w, D_IN + 2 * w + FINGER_T
            b0, _ = footprint_cost(obs, obst, c, n, lo, hi)
            tcp0 = c + (D_IN + w) * n
            hb = hand_block(obs, obst, tcp0, n)
            for j, d in enumerate(dirs):
                bm = footprint_block(obs, obst, c + 0.5 * DRAG_L * d, n, lo, hi, m=0.003)
                be = footprint_block(obs, obst, c + DRAG_L * d, n, lo, hi, m=0.003)
                sc = b0 + bm + be + 2.0 * dcost[j] + HAND_COST * hb
                out.append(dict(n_yaw=a, w=w, d=(float(d[0]), float(d[1])), tcp0=(float(tcp0[0]), float(tcp0[1])),
                                score=sc, dc=round(dcost[j], 3), b=(round(b0, 3), round(bm, 3), round(be, 3))))
    out.sort(key=lambda o: o["score"])
    return out


def candidates(obs, obst, c, r, yaw_now):
    """List of grasps dict(kind, yaw, gx, gy, w, score), best first."""
    c = np.asarray(c[:2], float)
    out = []
    for k in range(18):                                   # outer grasp, closing axis yaw in [0, pi)
        yaw = k * math.pi / 18
        u = np.array([math.cos(yaw), math.sin(yaw)])
        hb = hand_block(obs, obst, c, u)
        for clr in (0.007, 0.011, 0.016):
            w = r + clr
            s1, b1 = footprint_cost(obs, obst, c, u, w, w + FINGER_T)
            s2, b2 = footprint_cost(obs, obst, c, -u, w, w + FINGER_T)
            sc = max(s1, s2) + 0.3 * min(s1, s2) - 0.002 * clr / 0.004 + HAND_COST * hb
            out.append(dict(kind="outer", yaw=yaw, gx=float(c[0]), gy=float(c[1]), w=float(min(w, 0.04)),
                            score=sc, b=(b1, b2), clr=clr, hb=round(hb, 3)))
    for k in range(36):                                   # wall grasp, outer finger on side u
        ang = k * math.pi / 18
        u = np.array([math.cos(ang), math.sin(ang)])
        for clr in (0.004, 0.007):
            ro = r + clr
            so, bo = footprint_cost(obs, obst, c, u, ro, ro + FINGER_T)
            g = c + u * (D_IN + ro) / 2
            w = (ro - D_IN) / 2
            hb = hand_block(obs, obst, g, u)
            sc = so + 0.04 + HAND_COST * hb   # small preference for the outer grasp when both are free
            out.append(dict(kind="wall", yaw=ang, gx=float(g[0]), gy=float(g[1]), w=float(w),
                            score=sc, b=(bo,), clr=clr, side=(float(u[0]), float(u[1])), hb=round(hb, 3)))
    for o in out:
        dy = (o["yaw"] - yaw_now + math.pi / 2) % math.pi - math.pi / 2
        o["score"] += 0.01 * abs(dy)
    out.sort(key=lambda o: o["score"])
    return out
