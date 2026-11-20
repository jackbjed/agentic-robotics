"""Perception for the pack task: the white box (+ its lid) and the named objects, from wrist frames."""
import math
import numpy as np
import cv2

from helpers.geom import pix_to_plane
from helpers.target import mask_circle, hue_ok
from helpers.percep import largest_component

BOX_HALF = 0.096      # outer half size (191 x 193 mm)
BOX_H = 0.058         # wall height

# object kinds: phrases (besides the name), top height (m), plausible size
KINDS = {
    "roll": dict(z=0.021),
    "block": dict(z=0.022),
    "screwdriver": dict(z=0.018),
    "cable": dict(z=0.014),
}


def kind_of(name):
    n = name.lower()
    if "tape" in n or "roll" in n:
        return "roll"
    if "block" in n:
        return "block"
    if "screwdriver" in n:
        return "screwdriver"
    if "cable" in n:
        return "cable"
    return "other"


def phrases_for(name):
    k = kind_of(name)
    col = name.split()[0].lower()
    if k == "roll":
        return [name, f"{col} ring", f"{col} tape"]
    if k == "block":
        return [name, "black square block", "black block"]
    if k == "screwdriver":
        return [name, "screwdriver"]
    if k == "cable":
        return [name, "black cable", "coiled cable"]
    return [name]


def mask_xy(obs, mask, z, shift=(0.0, 0.0), step=2):
    """Mask pixels -> their points on the plane at height z (Nx2)."""
    vv, uu = np.nonzero(mask[::step, ::step])
    uu = uu * step - shift[0]
    vv = vv * step - shift[1]
    W, H, fx, fy, cx, cy = obs["K"]
    R = np.asarray(obs["R"], float)
    e = np.asarray(obs["eye"], float)
    d = np.stack([(uu - cx) / fx, (vv - cy) / fy, np.ones_like(uu, float)], -1) @ R.T
    s = (z - e[2]) / d[:, 2]
    X = e[None, :] + s[:, None] * d
    return X[:, :2]


def shift_delta(obs, u, v, z, shift):
    """World xy correction at pixel (u, v), plane z, for the image shift."""
    if shift == (0.0, 0.0) or shift is None:
        return np.zeros(2)
    a = pix_to_plane(obs, u - shift[0], v - shift[1], z)
    b = pix_to_plane(obs, u, v, z)
    if a is None or b is None:
        return np.zeros(2)
    return (a - b)[:2]


def find_box(ctx, obs, shift=(0.0, 0.0), log="box"):
    """-> dict(c (2,), ang (rad, box axis e1), e1, e2 (towards the lid's hinge), theta (lid deg), lid_n) or None."""
    P, ok = ctx.tools.world(obs)
    z = np.where(ok, P[..., 2], np.nan)
    best = None
    tried = []
    for ph in ("white box", "white cardboard box", "box"):
        try:
            res = ctx.segmenter.segment(obs["rgb"], ph)
        except Exception as e:
            ctx.log(log + "_err", err=repr(e))
            continue
        for m, s in res[:5]:
            m = np.asarray(m, bool)
            if m.sum() < 500:
                continue
            sel = m & ok & (z > -0.01) & (z < 0.075)
            pts = P[sel][:, :2]
            if len(pts) < 200:
                continue
            pts = pts[::max(1, len(pts) // 4000)]
            (cx, cy), (w, h), a = cv2.minAreaRect(pts.astype(np.float32))
            tried.append(dict(ph=ph, s=round(float(s), 3), c=[round(cx, 3), round(cy, 3)], w=round(w, 3),
                              h=round(h, 3), a=round(a, 1)))
            if 0.15 < w < 0.25 and 0.15 < h < 0.25:
                sc = float(s) - 2.0 * (abs(w - 0.192) + abs(h - 0.192))
                if best is None or sc > best[0]:
                    best = (sc, cx, cy, a, m)
        if best is not None:
            break
    if best is None:
        ctx.log(log, ok=False, tried=tried)
        return None
    _, cx, cy, a, m = best
    c = np.array([cx, cy], float)
    # correction for the camera calibration error (image shift seen on the fingers)
    uvc = ctx.tools.project(obs, np.array([cx, cy, 0.03]))[0].ravel()
    c = c + shift_delta(obs, uvc[0], uvc[1], 0.03, shift)
    ang = math.radians(a)
    # the lid: tall points near the box
    d = np.hypot(P[..., 0] - c[0], P[..., 1] - c[1])
    lid = ok & (z > 0.085) & (z < 0.30) & (d < 0.30)
    lp = P[lid]
    sides = [ang + k * math.pi / 2 for k in range(4)]
    theta = None
    lid_n = 0
    if len(lp) > 50:
        v = lp[:, :2].mean(0) - c
        dots = [v[0] * math.cos(s) + v[1] * math.sin(s) for s in sides]
        k = int(np.argmax(dots))
        lid_n = len(lp)
    else:
        # no lid seen: assume the +y side (both probes had it there)
        dots = [math.sin(s) for s in sides]
        k = int(np.argmax(dots))
    ang2 = sides[k]
    e2 = np.array([math.cos(ang2), math.sin(ang2)])
    e1 = np.array([-e2[1], e2[0]])
    if len(lp) > 50:
        off = (lp[:, :2] - c) @ e2 - BOX_HALF
        dz = lp[:, 2] - BOX_H
        good = dz > 0.03
        if good.sum() > 20:
            th = 180.0 - np.degrees(np.arctan2(dz[good], off[good]))
            theta = float(np.median(th))
    out = dict(c=c, e1=e1, e2=e2, theta=theta, lid_n=int(lid_n))
    ctx.log(log, ok=True, c=[round(float(x), 4) for x in c], e2=[round(float(x), 3) for x in e2], theta=theta,
            lid_n=int(lid_n), tried=tried)
    out["ang"] = ang
    return out


def lid_angle_strip(box, P, ok):
    """The lid's opening angle (deg) from depth points in the strip outside the hinge (low points too: a lid
    flung flat open lies only 0-5 cm above the box top). -> (theta or None, n points)."""
    c, e1, e2 = box["c"], box["e1"], box["e2"]
    pts = P[ok]
    pts = pts[np.isfinite(pts).all(1)]
    d = pts[:, :2] - c
    u, v = d @ e1, d @ e2
    dv = v - BOX_HALF
    dz = pts[:, 2] - BOX_H
    m = (np.abs(u) < BOX_HALF - 0.01) & (dv > 0.02) & (dv < 0.22) & (dz > 0.006) & (dz < 0.21)
    if m.sum() < 40:
        return None, int(m.sum())
    ang = np.degrees(np.arctan2(dz[m], dv[m]))
    return float(180.0 - np.median(ang)), int(m.sum())


def lid_from_points(box, lp):
    """Re-derive the lid's side and angle from tall points (N, 3) near the box. -> True if found."""
    c = box["c"]
    lp = lp[np.hypot(lp[:, 0] - c[0], lp[:, 1] - c[1]) < 0.30]
    if len(lp) < 30:
        return False
    ang = box.get("ang", math.atan2(box["e1"][1], box["e1"][0]))
    sides = [ang + k * math.pi / 2 for k in range(4)]
    v = lp[:, :2].mean(0) - c
    k = int(np.argmax([v[0] * math.cos(s) + v[1] * math.sin(s) for s in sides]))
    e2 = np.array([math.cos(sides[k]), math.sin(sides[k])])
    box["e2"] = e2
    box["e1"] = np.array([-e2[1], e2[0]])
    off = (lp[:, :2] - c) @ e2 - BOX_HALF
    dz = lp[:, 2] - BOX_H
    good = dz > 0.03
    if good.sum() > 10:
        box["theta"] = float(np.median(180.0 - np.degrees(np.arctan2(dz[good], off[good]))))
    box["lid_n"] = int(len(lp))
    return True


def in_box(box, xy, margin=0.0):
    if box is None:
        return False
    d = np.asarray(xy, float) - box["c"]
    return abs(d @ box["e1"]) < BOX_HALF + margin and abs(d @ box["e2"]) < BOX_HALF + margin


def white_ok(rgb, mask):
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)
    sel = mask
    if sel.sum() < 20:
        return 0.0
    s = hsv[..., 1][sel]
    v = hsv[..., 2][sel]
    return float(((s < 70) & (v > 120)).mean())


def obstacle_xy(ctx, obs, target_mask, box=None, zmin=0.010):
    """World xy of raised things in a frame other than the target (depth), plus the box's walls."""
    P, ok = ctx.tools.world(obs)
    z = np.where(ok, P[..., 2], 0.0)
    tm = np.zeros(z.shape, bool) if target_mask is None else \
        cv2.dilate(np.asarray(target_mask, np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    sel = ok & (z > zmin) & (z < 0.25) & ~tm
    pts = P[sel][::3, :2]
    if box is not None:
        ss = np.linspace(-BOX_HALF, BOX_HALF, 40)
        w = []
        for s in ss:
            for a, b in ((BOX_HALF, s), (-BOX_HALF, s), (s, BOX_HALF), (s, -BOX_HALF)):
                w.append(box["c"] + a * box["e1"] + b * box["e2"])
        pts = np.vstack([pts, np.array(w)]) if len(pts) else np.array(w)
    return pts


def cloud(ctx, obs, exclude_mask=None, zmin=0.010, zmax=0.30, step=3):
    """Raised world points (N, 3) of a frame, minus a mask (the target)."""
    P, ok = ctx.tools.world(obs)
    z = np.where(ok, P[..., 2], 0.0)
    sel = ok & (z > zmin) & (z < zmax)
    if exclude_mask is not None:
        # wide: the D415's edge fattening spreads the object's own depth beyond its mask (run 013)
        sel &= ~cv2.dilate(np.asarray(exclude_mask, np.uint8), np.ones((17, 17), np.uint8)).astype(bool)
    pts = P[sel]
    return pts[::step]


def box_wall_pts(box, z=BOX_H):
    if box is None:
        return np.zeros((0, 3))
    ss = np.linspace(-BOX_HALF, BOX_HALF, 40)
    w = []
    for s in ss:
        for a, b in ((BOX_HALF, s), (-BOX_HALF, s), (s, BOX_HALF), (s, -BOX_HALF)):
            xy = box["c"] + a * box["e1"] + b * box["e2"]
            w.append([xy[0], xy[1], z])
    return np.array(w)


# a simple hand model, heights above the fingertips (m): fingers, the hand body, the wrist camera
FING_Z = 0.0
BODY_Z = 0.10
BODY_HX, BODY_HY = 0.105, 0.045


def tilt_R(R, beta, sgn):
    """R rotated about its closing axis (column 1) by sgn * beta."""
    k = np.asarray(R, float)[:, 1]
    a = sgn * beta
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    Rot = np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K
    return Rot @ np.asarray(R, float)


def hand_cost(pts, g, yaw, fz, open_half, cam_off, margin=0.006, tilt=0.0):
    """Obstacle points (treated as columns from the table up to their height) inside the hand's parts at a
    grasp at g, closing along yaw, fingertips at fz. tilt (rad, signed): the hand leaning about its closing
    axis, its upper parts shifted across the closing axis by height * sin(tilt). -> (total, per part)."""
    if pts is None or len(pts) == 0:
        return 0.0, (0, 0, 0)
    cl = np.array([math.cos(yaw), math.sin(yaw)])
    ac = np.array([-cl[1], cl[0]])
    d = pts[:, :2] - np.asarray(g)[None, :]
    a = d @ cl
    b = d @ ac
    z = pts[:, 2]
    st, ct = math.sin(tilt), math.cos(tilt)
    # low points (objects, the box's walls) stand on the table: columns; tall ones (the open lid) overhang: only
    # a part at their height meets them (run 015: under the lid's overhang)
    low = z < 0.075

    def hit(zmin, zmax):
        return (z > zmin) & (low | (z < zmax + margin))

    nf = 0
    for sgn in (1, -1):
        aa = sgn * a
        # the fingers: a slanted column, checked by its lower and upper halves
        for h0, h1 in ((0.0, 0.05), (0.05, 0.10)):
            bs = 0.5 * (h0 + h1) * st
            nf += int(((aa > open_half - margin) & (aa < open_half + 0.013 + margin) &
                       (np.abs(b - bs) < 0.0125 + margin + 0.5 * (h1 - h0) * abs(st)) &
                       hit(fz + h0 * ct + 0.002, fz + h1 * ct)).sum())
    nb = 0
    for h0, h1 in ((BODY_Z, BODY_Z + 0.06), (BODY_Z + 0.06, BODY_Z + 0.16)):
        bsh = 0.5 * (h0 + h1) * st
        nb += int(((np.abs(a) < BODY_HX + margin) & (np.abs(b - bsh) < BODY_HY + margin + 0.5 * (h1 - h0) * abs(st))
                   & hit(fz + h0 * ct, fz + h1 * ct)).sum())
    nc = 0
    if cam_off is not None:
        ca = float(cam_off[:2] @ cl)
        cb = float(cam_off[:2] @ ac)
        czmin = fz + 0.0157 + float(cam_off[2]) - 0.025
        nc = int(((np.abs(a - ca) < 0.05 + margin) & (np.abs(b - cb) < 0.022 + margin) &
                  hit(czmin, czmin + 0.05)).sum())
    return float(nf + nb + nc), (nf, nb, nc)


def finger_cost(obst, g, yaw, half_open, margin=0.008):
    """Number of obstacle points in the two finger footprints (fully open, grown by margin)."""
    if obst is None or len(obst) == 0:
        return 0.0
    cl = np.array([math.cos(yaw), math.sin(yaw)])
    ac = np.array([-cl[1], cl[0]])
    d = obst - np.asarray(g)[None, :]
    a = d @ cl
    b = d @ ac
    n = 0.0
    for sgn in (1, -1):
        lo, hi = half_open - margin, half_open + 0.013 + margin
        aa = sgn * a
        inside = (aa > lo) & (aa < hi) & (np.abs(b) < 0.0125 + margin)
        n += float(inside.sum())
    return n


def pca(xy):
    """-> (yaw of the long axis, length, width (2-98 % extents), centre of the extents)."""
    m = xy.mean(0)
    C = np.cov((xy - m).T)
    w, V = np.linalg.eigh(C)
    a = V[:, 1]
    b = np.array([-a[1], a[0]])
    t = (xy - m) @ a
    s = (xy - m) @ b
    t0, t1 = np.percentile(t, [2, 98])
    s0, s1 = np.percentile(s, [2, 98])
    c = m + 0.5 * (t0 + t1) * a + 0.5 * (s0 + s1) * b
    return float(math.atan2(a[1], a[0])), float(t1 - t0), float(s1 - s0), c


def grasp_plan(obj):
    """Grasp for a found object -> dict(g (2,) grasp xy, yaw (closing axis), open (per finger), fz (fingertip z),
    long (yaw of the object's long axis or None), off (object centre - grasp point, in the hand frame: along the
    closing axis, across it))."""
    k = obj["kind"]
    c = np.asarray(obj["c"], float)
    if k == "roll":
        return dict(g=c, yaw=None, open=0.04, fz=0.011, long=None, off=(0.0, 0.0), half=obj["L"] / 2)
    if k == "block":
        # closing across a side; yaw mod pi/2 chosen later
        return dict(g=c, yaw=obj["yaw"], sym=math.pi / 2, open=0.04, fz=0.010, long=None, off=(0.0, 0.0),
                    half=0.036)
    xy = obj["xy"]
    ya = obj["yaw"]
    a = np.array([math.cos(ya), math.sin(ya)])
    b = np.array([-a[1], a[0]])
    t = (xy - c) @ a
    s = (xy - c) @ b
    if k == "screwdriver":
        # the handle: the wide part of the width profile along the axis
        bins = np.arange(t.min(), t.max() + 0.01, 0.01)
        idx = np.digitize(t, bins)
        wid = np.array([np.ptp(s[idx == i]) if (idx == i).sum() > 3 else 0.0 for i in range(1, len(bins) + 1)])
        hb = np.nonzero(wid > 0.6 * wid.max())[0]
        tg = float(np.median(bins[hb] + 0.005)) if len(hb) else 0.0
        near = np.abs(t - tg) < 0.012
        sg = float(np.median(s[near])) if near.sum() > 3 else 0.0
        g = c + tg * a + sg * b
        # a narrower opening (run 008: the screwdriver beside the box, a fully open finger landed on its wall)
        op = float(np.clip(wid.max() / 2 + 0.019, 0.026, 0.04))
        out = dict(g=g, yaw=ya + math.pi / 2, sym=math.pi, open=op, fz=0.008, long=ya,
                   off=(0.0, -tg), half=0.02, tg=tg, hw=float(wid.max()))
        # its middle: ~4.5 cm from the handle's middle towards the shaft (the mask can miss the shaft, run 021)
        nb_ = np.nonzero((wid > 0) & (wid < 0.4 * wid.max()))[0]
        if len(nb_) >= 2:
            ts = float(np.median(bins[nb_] + 0.005))
            out["c_obj"] = g + (0.045 if ts > tg else -0.045) * a
        return out
    if k == "cable":
        # squeeze the whole coil across its minor axis at its centre (one strand only: it swung upright and
        # stood in the box, run 003)
        cen = np.abs(t) < 0.25 * max(obj["L"], 0.04)
        sv = s[cen] if cen.sum() > 10 else s
        s0, s1 = np.percentile(sv, [3, 97])
        g = c + 0.5 * (s0 + s1) * b
        return dict(g=g, yaw=ya + math.pi / 2, sym=math.pi, open=0.04, fz=0.007, long=ya, off=(0.0, 0.0),
                    half=0.035, wmin=float(s1 - s0))
    return dict(g=c, yaw=obj.get("yaw", 0.0), open=0.04, fz=0.010, long=None, off=(0.0, 0.0), half=0.03)


def cable_strand_plans(obj):
    """One-strand grasps at the two ends of the coil's long axis (closing along it, across the strand)."""
    c = np.asarray(obj["c"], float)
    ya = obj["yaw"]
    a = np.array([math.cos(ya), math.sin(ya)])
    xy = obj["xy"]
    t = (xy - c) @ a
    out = []
    t0, t1 = np.percentile(t, [2, 98])
    for te, sgn in ((t1, 1.0), (t0, -1.0)):
        g = c + (te - sgn * 0.008) * a
        # the coil's centre: half its known length (~15 cm) inwards (its mask can be partial)
        out.append(dict(g=g, yaw=ya, sym=math.pi, open=0.022, fz=0.006, long=ya, off=(0.0, 0.0), half=0.02,
                        mode="strand", c_obj=g - sgn * 0.067 * a))
    return out


def find_object(ctx, obs, name, shift=(0.0, 0.0), box=None, expect=None, max_dev=0.08, log="obj", exclude=(),
                occl=None, side=False, partial=False, relaxed=False):
    """-> dict(kind, c (2,), yaw (long / side axis), L, W, score, ...) or None."""
    kind = kind_of(name)
    zt = KINDS.get(kind, dict(z=0.02))["z"]
    col = name.split()[0].lower()
    W_, H_, fx, fy, cx0, cy0 = obs["K"]
    cands = []
    for ph in phrases_for(name):
        try:
            res = ctx.segmenter.segment(obs["rgb"], ph)
        except Exception as e:
            ctx.log(log + "_err", err=repr(e))
            continue
        for m, s in res[:6]:
            m = np.asarray(m, bool)
            if m.sum() < 40:
                continue
            m = largest_component(m)
            xy = mask_xy(obs, m, zt, shift)
            if len(xy) < 10:
                continue
            c = np.median(xy, 0)
            info = dict(ph=ph, s=round(float(s), 3), area=int(m.sum()))
            ok = True
            if kind == "roll":
                ci = mask_circle(m, occl)
                if ci is None:
                    continue
                X = pix_to_plane(obs, ci["u"] - shift[0], ci["v"] - shift[1], zt)
                if X is None:
                    continue
                c = X[:2]
                rng = float(np.linalg.norm(X - np.asarray(obs["eye"])))
                r = (0.5 * max(ci["a"], ci["b"]) if side else 0.25 * (ci["a"] + ci["b"])) / fx * rng
                if col == "white":
                    # the white roll scored 0.37 (run 003), the red one 0.25: also demand no red / yellow
                    hq = white_ok(obs["rgb"], ci["mask"])
                    hr = hue_ok(obs["rgb"], ci["mask"], "red")
                    hy = hue_ok(obs["rgb"], ci["mask"], "yellow")
                    # (cream tape: yellow score 0.36, the yellow roll 1.0, run 004)
                    ok = 0.016 < r < 0.034 and hq > 0.15 and hr < 0.3 and hy < 0.65
                    info.update(hr=round(hr, 2), hy=round(hy, 2))
                else:
                    hq = hue_ok(obs["rgb"], ci["mask"], col)
                    ok = 0.016 < r < 0.034 and hq > 0.7
                info.update(r=round(r, 4), hue=round(hq, 2), touches=ci["touches"])
                yaw, L, Wd = 0.0, 2 * r, 2 * r
            else:
                yaw, L, Wd, cc = pca(xy)
                if kind == "block":
                    (rx, ry), (w, h), a = cv2.minAreaRect(xy.astype(np.float32))
                    c = np.array([rx, ry])
                    yaw = math.radians(a) % (math.pi / 2)
                    L, Wd = max(w, h), min(w, h)
                    ok = 0.055 < L < 0.095 and 0.05 < Wd < 0.09
                elif kind == "screwdriver":
                    ok = 0.12 < L < 0.26 and Wd < 0.06
                elif kind == "cable":
                    c = cc
                    ok = 0.05 < L < 0.22 and Wd > 0.025
                if partial and kind in ("screwdriver", "cable"):
                    # seen from close: may be cut by the image border
                    ok = 0.03 < L < 0.26 and float(s) > 0.3
                if side:
                    # the scene camera's low view: plane-projected sizes are stretched; only a coarse check
                    ok = L < 0.35 and float(s) > 0.3
                info.update(L=round(float(L), 3), W=round(float(Wd), 3), yaw=round(float(yaw), 3))
            if side:
                ok = ok and -0.65 < c[0] < 0.05 and -0.35 < c[1] < 0.35
            wgt = 1.0
            if relaxed and not ok and float(s) > 0.5:
                # partly hidden (under the open lid, run 016): a confident mask of plausible size, ranked lower
                if kind == "roll":
                    rel = 0.010 < info.get("r", 0) < 0.04 and info.get("hue", 0) > 0.5
                elif kind == "block":
                    rel = 0.03 < L < 0.12 and Wd > 0.025
                else:
                    rel = 0.04 < L < 0.26
                if rel:
                    ok, wgt = True, 0.8
                    info["relaxed"] = True
            if in_box(box, c, 0.005):
                ok = False
                info["inbox"] = True
            if any(np.hypot(*(c - np.asarray(e))) < 0.03 for e in exclude):
                ok = False
                info["excluded"] = True
            if expect is not None:
                dev = float(np.hypot(*(c - np.asarray(expect))))
                info["dev"] = round(dev, 3)
                ok = ok and dev < max_dev
            info.update(c=[round(float(c[0]), 4), round(float(c[1]), 4)], ok=bool(ok))
            cands.append(dict(info=info, kind=kind, c=c, yaw=float(yaw), L=float(L), W=float(Wd), score=float(s) * wgt,
                              mask=m, xy=xy, ok=ok))
        if any(cd["ok"] and cd["score"] > 0.4 for cd in cands):
            break
    ctx.log(log, name=name, cands=[cd["info"] for cd in cands])
    good = [cd for cd in cands if cd["ok"]]
    if not good:
        return None
    return max(good, key=lambda cd: cd["score"])
