"""Put the cylinder (with a hole) on the stick of the stand. Entry point: run(robot, task).

The stand is the rig's own: a wooden board (~280 x 85 x 20 mm) with a ~10 mm dowel standing ~170 mm above it.
1. survey: the wrist camera looks down on the work area from high up (far enough forward to see the whole
   stick); table plane; candidates for the stick (thin and much higher than its surroundings) and the ring (SAM
   masks and compact objects).
2. close-ups, all from the same distance (330 mm above the top) and the same camera side: the ring = a round top
   with a deeper hole; the stick = a thin high patch on a plate.
3. the ring is centred between the fingers along two lines (a, b): its axis is then at the TCP; it is let go and
   looked at again from the same distance: seen minus TCP = the camera's error, which corrects the stick's place
   (the stick itself is never squeezed: the light board would slide or tip).
4. the ring is taken again, carried high, the measured TCP brought over the stick (closed loop: the arm's moves
   end a few mm off), lowered by an ordinary move to just above the stick, then a short stop-on-contact move;
   if it stops on the stick's top: a small spiral search. Let go, lift, back to the start pose.
Readings of the opening only say whether something is held (soft fingers read wide things narrower).
Long stop-on-contact moves are avoided (slow and drifting on the real arm); every look waits for the arm to settle.
"""
import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
C0 = (0.0, -1.0, 0.0)
SURVEY = (-340.0, 45.0, 615.0)
HOME = (-478.0, 41.0, 350.0)
D_CLOSE = 330.0           # camera to the top of what a close-up looks at
SETTLE = 0.5              # wait before a look (s)
STICK_H = 160.0           # the dowel stands at least this high above its board


# ------------------------------------------------------------------ robot helpers
def st(robot):
    return robot.state(ARM)


def tcp(robot):
    return np.array(st(robot)["tcp_mm"], float)


def cdir(robot):
    return tuple(float(x) for x in st(robot)["closing_dir"])


def mv(robot, xyz, stop=False, closing=None):
    c = closing if closing is not None else cdir(robot)
    r = robot.move(ARM, [float(v) for v in xyz], DOWN, c, stop)
    if not r.get("moved", True) or (not r.get("done") and r.get("why") != "contact"):
        print("MOVE", [round(float(v), 1) for v in xyz], "stop" if stop else "", r)
    return r


TRAVEL = {"z": 300.0}


def travel(robot, xyz, closing=None):
    """up, across, down (a straight line between places can sweep the open fingers through things)"""
    p = tcp(robot)
    zt = max(TRAVEL["z"], float(xyz[2]))
    if math.hypot(xyz[0] - p[0], xyz[1] - p[1]) > 15:
        if p[2] < zt - 1:
            mv(robot, (p[0], p[1], zt))
        mv(robot, (xyz[0], xyz[1], zt), closing=closing)
    return mv(robot, xyz, closing=closing)


def turn(robot, ang):
    p = tcp(robot)
    r = robot.turn(ARM, p.tolist(), (0, 0, 1), ang)
    if r.get("done"):
        return ang
    if r.get("moved"):
        print("TURN stopped", r.get("why"))
        return 0
    r = robot.turn(ARM, p.tolist(), (0, 0, 1), -ang)
    if r.get("done"):
        return -ang
    print("TURN refused", r.get("why"))
    return 0


def sweep_max(HG, centre, r0, r1=78.0):
    """the highest surface in the ring r0..r1 around centre (what the fingers sweep when turning)"""
    if HG is None:
        return -1e4
    a = np.linspace(0, 2 * math.pi, 48, endpoint=False)
    rr = np.arange(r0, r1 + 0.1, 3.0)
    A, Rr = np.meshgrid(a, rr)
    x = centre[0] + (Rr * np.cos(A)).ravel()
    y = centre[1] + (Rr * np.sin(A)).ravel()
    ix = ((x - HG["x0"]) / HG["cell"]).astype(int)
    iy = ((y - HG["y0"]) / HG["cell"]).astype(int)
    G = HG["G"]
    ok = (ix >= 0) & (ix < G.shape[0]) & (iy >= 0) & (iy < G.shape[1])
    if not ok.any():
        return -1e4
    return float(np.percentile(G[ix[ok], iy[ok]], 98))


def look(robot):
    robot.wait(SETTLE)
    return robot.look(["left_wrist"])["left_wrist"]


def truth(robot):
    try:
        return robot.truth()
    except Exception:
        return None


# ------------------------------------------------------------------ image helpers
def disc(px):
    px = max(3, int(round(px))) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (px, px))


def fill_nan(a, iters=8, default=0.0):
    w = np.where(np.isfinite(a), a, -1e4).astype(np.float32)
    k = np.ones((3, 3), np.uint8)
    for _ in range(iters):
        m = w < -1e3
        if not m.any():
            break
        d = cv2.dilate(w, k)
        w[m] = d[m]
    w[w < -1e3] = default
    return w


def table_plane(P):
    z = P[..., 2]
    v = np.isfinite(z)
    zs = z[v]
    hist, edges = np.histogram(zs, bins=np.arange(-100, 400, 2.0))
    z0 = edges[int(np.argmax(hist))] + 1.0
    X = P[v & (np.abs(z - z0) < 8)]
    if len(X) > 40000:
        X = X[:: len(X) // 40000 + 1]
    coef = np.array([0.0, 0.0, z0])
    for _ in range(3):
        A = np.c_[X[:, 0], X[:, 1], np.ones(len(X))]
        coef = np.linalg.lstsq(A, X[:, 2], rcond=None)[0]
        r = X[:, 2] - A @ coef
        X = X[np.abs(r) < 4]
    return coef


def ray_point(f, u, v, z):
    d = f.R @ np.array([(u - f.cx) / f.fx, (v - f.cy) / f.fy, 1.0])
    t = (z - f.eye[2]) / d[2]
    return np.asarray(f.eye, float) + t * d


def mask_iou(a, b):
    i = np.logical_and(a, b).sum()
    u = np.logical_or(a, b).sum()
    return i / max(u, 1)


def annulus(shape, u, v, r0, r1):
    H, W = shape
    R = int(math.ceil(r1)) + 1
    v0, v1, u0, u1 = max(0, int(v) - R), min(H, int(v) + R + 1), max(0, int(u) - R), min(W, int(u) + R + 1)
    yy, xx = np.mgrid[v0:v1, u0:u1]
    rr = np.hypot(xx - u, yy - v)
    return (slice(v0, v1), slice(u0, u1)), (rr >= r0) & (rr < r1)


def fmt(d):
    return {k: (np.round(v, 1).tolist() if isinstance(v, np.ndarray) else (round(v, 2) if isinstance(v, float) else v))
            for k, v in d.items() if k not in ("mask", "img")}


# ------------------------------------------------------------------ survey
def survey(robot):
    r = mv(robot, SURVEY, closing=C0)
    if not r.get("done"):
        mv(robot, (-420.0, 60.0, 600.0), closing=C0)
    f = look(robot)
    P = f.points().copy()
    dep = f.depth
    Hh, Ww = dep.shape
    bad = ~np.isfinite(dep) | (dep < 300)
    bad[int(Hh * 0.78):, :int(Ww * 0.3)] = True      # the fingertips
    bad[int(Hh * 0.78):, int(Ww * 0.7):] = True
    P[bad] = np.nan
    coef = table_plane(P)
    zp = coef[0] * P[..., 0] + coef[1] * P[..., 1] + coef[2]
    h = P[..., 2] - zp
    h[h > 400] = np.nan
    valid = np.isfinite(h)
    hf = fill_nan(h)
    rng_mm = float(np.nanmedian(dep[valid]))
    pxt = f.fx / rng_mm
    op = cv2.morphologyEx(hf, cv2.MORPH_OPEN, disc(44 * pxt))
    res = hf - op
    sam = {}
    for ph in ("ring", "cylinder with a hole"):
        try:
            sam[ph] = [(m.astype(bool), float(s)) for m, s in robot.segment(f, ph)]
        except Exception as e:
            print("segment fail", ph, e)
            sam[ph] = []
    # ---- stick candidates: thin things much higher than their close surroundings
    sticks = []
    n, lab, stats, _ = cv2.connectedComponentsWithStats((res > 15).astype(np.uint8), 8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 3:
            continue
        x0, y0, w0, h0 = stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3]
        sl = (slice(y0, y0 + h0), slice(x0, x0 + w0))
        comp = lab[sl] == i
        hv = np.where(comp, hf[sl], -1e9)
        vv, uu = np.unravel_index(int(np.argmax(hv)), hv.shape)
        v, u = vv + y0, uu + x0
        top = float(hf[v, u])
        pxmm = f.fx / max(150.0, rng_mm - top)
        win, ann = annulus(hf.shape, u, v, 18 * pxmm, 32 * pxmm)
        hv_ann = h[win][ann]
        hv_ann = hv_ann[np.isfinite(hv_ann)]
        if len(hv_ann) < 10:
            continue
        a60 = float(np.percentile(hv_ann, 60))
        ped = float(np.median(hv_ann))
        lrise = top - a60
        if lrise < 40:
            continue
        patch = comp & (hf[sl] > top - 6)
        eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
        p = ray_point(f, u, v, top + coef[2])
        ringv = max([s for ph in sam for m, s in sam[ph][:3] if m[v, u]] + [0.0])
        edge = min(u, v, Ww - 1 - u, Hh - 1 - v) < 6
        score = min(4.0, lrise / 40.0) + (0.8 if 5 <= eqd < 24 else (-0.5 if eqd < 4 else -0.3)) \
            + (0.6 if ped > 8 else 0.0) - (1.0 if edge else 0.0) - 1.0 * ringv
        sticks.append(dict(uv=(int(u), int(v)), xyz=p, top=float(p[2]), lrise=lrise, eqd=eqd, ped=ped + coef[2],
                           pedh=ped, ringv=ringv, score=score))
    sticks.sort(key=lambda s: -s["score"])
    # ---- ring candidates: SAM masks and compact objects
    cands = []
    for ph in sam:
        for m, s in sam[ph][:4]:
            if m.sum() < 20:
                continue
            for r in cands:
                if mask_iou(r["mask"], m) > 0.5:
                    r["sam"] += s
                    break
            else:
                cands.append(dict(mask=m, sam=s))
    objm = ((hf > 8) & valid).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(objm, 8)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        eqd = 2 * math.sqrt(a / math.pi) / pxt
        if 15 < eqd < 85:
            m = lab == i
            if not any(mask_iou(r["mask"], m) > 0.3 for r in cands):
                cands.append(dict(mask=m, sam=0.0))
    rings = []
    for r in cands:
        m = r["mask"]
        hm = h[m]
        hm = hm[np.isfinite(hm)]
        if len(hm) < 10:
            continue
        top = float(np.percentile(hm, 80))
        m8 = m.astype(np.uint8)
        out = cv2.dilate(m8, disc(50 * pxt)).astype(bool) & ~cv2.dilate(m8, disc(12 * pxt)).astype(bool) & valid
        sup = float(np.percentile(h[out], 25)) if out.sum() > 10 else 0.0
        vs, us = np.nonzero(m)
        u, v = float(us.mean()), float(vs.mean())
        pxmm = f.fx / max(150.0, rng_mm - top)
        p = ray_point(f, u, v, top + coef[2])
        eqd = 2 * math.sqrt(m.sum() / math.pi) / pxmm
        cnts, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        per = max(cv2.arcLength(max(cnts, key=len), True), 1.0)
        circ = 4 * math.pi * m.sum() / per ** 2
        rise = top - sup
        plaus = (10 < rise < 75) and (16 < eqd < 85)
        r.update(uv=(int(u), int(v)), xyz=p, top=float(p[2]), rise=rise, eqd=eqd, circ=circ,
                 score=r["sam"] + (0.8 if plaus else -1.5) + (0.4 if circ > 0.7 else 0.0))
        rings.append(r)
    rings.sort(key=lambda r: -r["score"])
    zmax = float(np.nanpercentile(P[..., 2], 99.8))
    HG = height_grid(P, float(coef[2] + coef[0] * -300 + coef[1] * 50))
    return dict(f=f, coef=coef, sticks=sticks, rings=rings, zmax=zmax, HG=HG)


# ------------------------------------------------------------------ close-ups
def closeup(robot, xy, top_z, camoff):
    z = min(780.0, max(top_z + D_CLOSE - camoff[2], 250.0))
    tx, ty = xy[0] - camoff[0], xy[1] - camoff[1]
    tx = min(90.0, max(-700.0, tx))
    for dz in (0.0, 60.0, 140.0):        # the arm may refuse a pose: go higher
        r = travel(robot, (tx, ty, min(780.0, z + dz)), closing=C0)
        if r.get("done"):
            break
    # always the same camera side (its calibration error turns with it)
    if float(np.dot(st(robot)["closing_dir"], C0)) < 0:
        print("closeup: gripper flipped, turning it back")
        if turn(robot, 180) == 0:
            turn(robot, 90)
            turn(robot, 90)
    return look(robot)


def stick_closeup(f, guess_xy, guess_top, coef=None):
    P = f.points()
    z = P[..., 2]
    if not np.isfinite(z).any():
        return None
    zf = fill_nan(z, default=float(np.nanmin(z)))
    (uv,), _ = f.project(np.array([[guess_xy[0], guess_xy[1], guess_top]]))
    u0, v0 = float(uv[0]), float(uv[1])
    pxmm = f.fx / max(150.0, float(f.eye[2] - guess_top))
    op = cv2.morphologyEx(zf, cv2.MORPH_OPEN, disc(40 * pxmm))
    res = zf - op
    n, lab, stats, cents = cv2.connectedComponentsWithStats((res > 12).astype(np.uint8), 8)
    best = None
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 4:
            continue
        comp = lab == i
        zv = np.where(comp, zf, -1e9)
        v, u = np.unravel_index(int(np.argmax(zv)), zv.shape)
        dpx = math.hypot(u - u0, v - v0)
        if dpx > 45 * pxmm:
            continue
        sc = zv[v, u] - 0.5 * dpx / pxmm
        if best is None or sc > best[0]:
            best = (sc, i)
    if best is None:
        return None
    comp = lab == best[1]
    zc = np.where(comp & np.isfinite(z), z, np.nan)
    if not np.isfinite(zc).any():
        return None
    top = float(np.nanpercentile(zc, 97))
    patch = comp & np.isfinite(z) & (z > top - 4)
    if patch.sum() < 3:
        patch = comp & np.isfinite(z) & (z > top - 8)
    xy = P[patch][:, :2].mean(axis=0)
    eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
    vs, us = np.nonzero(patch)
    uc, vc = float(us.mean()), float(vs.mean())
    rp = eqd / 2
    win, ann = annulus(z.shape, uc, vc, (rp + 3) * pxmm, (rp + 14) * pxmm)
    zz = z[win][ann & (res[win] < 4)]
    zz = zz[np.isfinite(zz)]
    base = float(np.percentile(zz, 70)) if len(zz) > 15 else None
    win2, ann2 = annulus(z.shape, uc, vc, (rp + 5) * pxmm, (rp + 16) * pxmm)
    zz2 = z[win2][ann2]
    zz2 = zz2[np.isfinite(zz2)]
    lrise = top - float(np.percentile(zz2, 75)) if len(zz2) > 10 else 0.0
    plate = 0.0
    if coef is not None:
        zt = coef[0] * xy[0] + coef[1] * xy[1] + coef[2]
        win3, ann3 = annulus(z.shape, uc, vc, (rp + 5) * pxmm, (rp + 20) * pxmm)
        zz3 = z[win3][ann3]
        zz3 = zz3[np.isfinite(zz3)]
        if len(zz3) > 20:
            plate = float(np.mean((zz3 > zt + 3.5) & (zz3 < top - 10)))
    return dict(xy=xy, top=top, eqd=eqd, base=base, lrise=lrise, plate=plate, uv=(uc, vc))


def ring_closeup(f, guess_xy, guess_top, guess_d):
    P = f.points()
    z = P[..., 2]
    (uv,), _ = f.project(np.array([[guess_xy[0], guess_xy[1], guess_top]]))
    u0, v0 = float(uv[0]), float(uv[1])
    pxmm = f.fx / max(150.0, float(f.eye[2] - guess_top))
    gd = min(max(guess_d, 20.0), 75.0)
    yy, xx = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    rr = np.hypot(xx - u0, yy - v0) / pxmm
    win = (rr < gd / 2 + 12) & np.isfinite(z)
    if win.sum() < 30:
        return None
    ztop = float(np.percentile(z[win], 92))
    patch = (np.abs(np.nan_to_num(z, nan=-1e4) - ztop) < 4) & (rr < gd / 2 + 35)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(patch.astype(np.uint8), 8)
    if n < 2:
        return None
    best = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA] - 3 * pxmm * math.hypot(cents[i][0] - u0,
                                                                                             cents[i][1] - v0))
    pm = (lab == best).astype(np.uint8)
    cnts, _ = cv2.findContours(pm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(pm)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    fm = filled.astype(bool)
    hole = fm & ~pm.astype(bool)
    vs, us = np.nonzero(fm)
    uc, vc = float(us.mean()), float(vs.mean())
    c3 = ray_point(f, uc, vc, ztop)
    od = 2 * math.sqrt(fm.sum() / math.pi) / pxmm
    hole_d = 2 * math.sqrt(hole.sum() / math.pi) / pxmm
    rc = np.hypot(xx - uc, yy - vc) / pxmm
    ann = (rc > od / 2 + 6) & (rc < od / 2 + 22) & np.isfinite(z) & (z < ztop - 6)
    sup = float(np.percentile(z[ann], 30)) if ann.sum() > 20 else None
    cen = rc < 2.5
    zc = z[cen]
    deep = float(np.mean(~np.isfinite(zc) | (np.nan_to_num(zc, nan=-1e4) < ztop - 6))) if cen.any() else 0.0
    hd2, lowc = 0.0, 0.0
    nh, hl, hst, hce = cv2.connectedComponentsWithStats(hole.astype(np.uint8), 8)
    for i in range(1, nh):
        if math.hypot(hce[i][0] - uc, hce[i][1] - vc) / pxmm > 4:
            continue
        d_ = 2 * math.sqrt(hst[i, cv2.CC_STAT_AREA] / math.pi) / pxmm
        if d_ > hd2:
            zz_ = z[hl == i]
            hd2 = d_
            lowc = float(np.mean(~np.isfinite(zz_) | (np.nan_to_num(zz_, nan=-1e4) < ztop - 6)))
    img = f.rgb.copy()
    cv2.drawContours(img, cnts, -1, (255, 0, 0), 1)
    cv2.circle(img, (int(round(uc)), int(round(vc))), 3, (0, 255, 0), -1)
    return dict(xy=c3[:2], top=ztop, od=od, hole_d=hole_d, deep=deep, hd2=hd2, lowc=lowc, sup=sup, uv=(uc, vc),
                img=img)


# ------------------------------------------------------------------ free directions for the fingers
def height_grid(P, z_table, x0=-680.0, x1=60.0, y0=-420.0, y1=560.0, cell=3.0):
    nx, ny = int((x1 - x0) / cell), int((y1 - y0) / cell)
    G = np.full((nx, ny), -1e4, np.float32)
    X = P[np.isfinite(P[..., 2])]
    ix = ((X[:, 0] - x0) / cell).astype(int)
    iy = ((X[:, 1] - y0) / cell).astype(int)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    np.maximum.at(G, (ix[ok], iy[ok]), X[ok, 2].astype(np.float32))
    G = fill_nan(np.where(G < -1e3, np.nan, G), iters=4, default=z_table)
    return dict(G=G, x0=x0, y0=y0, cell=cell)


def region_max(HG, centre, cdir_xy, along0, along1, across):
    c = np.asarray(cdir_xy, float)
    c = c / np.linalg.norm(c)
    n = np.array([-c[1], c[0]])
    a = np.arange(along0, along1 + 0.1, 2.5)
    b = np.arange(-across, across + 0.1, 2.5)
    A, B = np.meshgrid(a, b)
    pts = []
    for s in (1, -1):
        pts.append(centre[None, :] + (s * A.ravel())[:, None] * c[None, :] + B.ravel()[:, None] * n[None, :])
    pts = np.concatenate(pts)
    ix = ((pts[:, 0] - HG["x0"]) / HG["cell"]).astype(int)
    iy = ((pts[:, 1] - HG["y0"]) / HG["cell"]).astype(int)
    G = HG["G"]
    ok = (ix >= 0) & (ix < G.shape[0]) & (iy >= 0) & (iy < G.shape[1])
    if not ok.any():
        return -1e4
    return float(np.percentile(G[ix[ok], iy[ok]], 95))


def move_closing(robot, xyz, angles):
    for th in angles:
        c = (math.cos(math.radians(th)), math.sin(math.radians(th)), 0.0)
        r = robot.move(ARM, [float(v) for v in xyz], DOWN, c, False)
        if r.get("done"):
            return th
        print("move with closing", round(th), "refused:", r.get("why", "")[:80])
        if r.get("moved"):
            break
    mv(robot, xyz)
    return None


def best_closing(HG, centre, inner, z_tip, angles=(0, 30, 60, 90, 120, 150), extra=(90,)):
    best = None
    for th in angles:
        hs = []
        for e in (0,) + tuple(extra):
            t = math.radians(th + e)
            hs.append(region_max(HG, np.asarray(centre, float), (math.cos(t), math.sin(t)), inner, 58.0, 21.0))
        clr = z_tip - max(hs)
        if best is None or clr > best[1] + 0.5:
            best = (th, clr)
    return best


# ------------------------------------------------------------------ centring between the fingers
def center_along(robot, label, min_w, max_w, iters=5):
    """Close on the thing between the fingers. A free thing slides to the middle; one that does not pushes the
    compliant arm a little: then step the arm that way and close again. Leaves the gripper closed; returns the
    opening read (soft fingers read wide things narrower than they are: it only says that something is held)."""
    w = 0.0
    for it in range(iters):
        p0 = tcp(robot)
        w = robot.set_gripper(ARM, 0)
        s1 = st(robot)
        p1 = np.array(s1["tcp_mm"], float)
        c = np.array(s1["closing_dir"], float)
        c[2] = 0.0
        c = c / max(np.linalg.norm(c), 1e-6)
        dc = float((p1 - p0) @ c)            # only the push along the closing line says where the thing is
        print(label, "w %.2f push %.2f" % (w, dc))
        if w < min_w or w > max_w or abs(dc) < 0.5:
            return w
        robot.set_gripper(ARM, 80)
        t = p0 + float(np.clip(1.8 * dc, -10.0, 10.0)) * c
        mv(robot, (t[0], t[1], p0[2]))
    return w


def squeeze_at(robot, label, xy, z_safe, z_grip, angles, min_w, max_w, stop_from=None, HG=None, r_in=30.0):
    """Go down at xy to z_grip with one of the closing angles and centre the thing along that line and the one
    at 90 degrees. When the arm refuses a move or a turn, or something is under the fingers, try the next
    angle. Returns the two readings or None."""
    tried = []
    for th in angles:
        if any(abs(((th - t) + 90) % 180 - 90) < 10 for t in tried):
            continue
        tried.append(th)
        robot.set_gripper(ARM, 80)
        travel(robot, (xy[0], xy[1], z_safe))
        if move_closing(robot, (xy[0], xy[1], z_safe), [th]) is None:
            continue
        if stop_from is not None and stop_from < z_safe - 1:
            mv(robot, (xy[0], xy[1], stop_from))
            r = mv(robot, (xy[0], xy[1], z_grip), stop=True)
        else:
            r = mv(robot, (xy[0], xy[1], z_grip))
        p = tcp(robot)
        if not r.get("moved", True) or p[2] > z_grip + 6 or (r.get("why") and r.get("why") != "contact"):
            print(label, "descent blocked/refused at angle", round(th), round(p[2], 1), (r.get("why") or "")[:70])
            mv(robot, (p[0], p[1], z_safe))
            continue
        w1 = center_along(robot, label + "-a", min_w, max_w)
        if not (min_w <= w1 <= max_w):
            print(label, "nothing held at angle", round(th), "w", round(w1, 1))
            robot.set_gripper(ARM, 80)
            mv(robot, (p[0], p[1], z_safe))
            continue
        robot.set_gripper(ARM, 80)
        q = tcp(robot)
        hs = sweep_max(HG, q[:2], r_in)            # lift the open fingers over what they would sweep
        zt = min(z_safe, hs + 16 + 6)
        if zt > q[2] + 1:
            mv(robot, (q[0], q[1], zt))
        a = turn(robot, 90)
        if zt > q[2] + 1:
            mv(robot, q)
        if a == 0:
            robot.set_gripper(ARM, 80)
            mv(robot, (p[0], p[1], z_safe))
            continue
        w2 = center_along(robot, label + "-b", min_w, max_w)
        if not (min_w <= w2 <= max_w):
            print(label, "lost it along b, w", round(w2, 1))
            robot.set_gripper(ARM, 80)
            mv(robot, (p[0], p[1], z_safe))
            continue
        print(label, "readings", round(w1, 1), round(w2, 1), "tcp", np.round(tcp(robot), 2))
        return [w1, w2]
    return None


# ------------------------------------------------------------------ finding things
def find_ring(robot, S, camoff):
    found = []
    for rc in S["rings"][:6]:
        fc = closeup(robot, rc["xyz"][:2], rc["top"], camoff)
        cy = ring_closeup(fc, rc["xyz"][:2], rc["top"], rc["eqd"])
        print("RING closeup", None if cy is None else fmt(cy))
        if cy is None:
            continue
        if len(found) < 2:
            robot.show("ring_closeup", cy["img"])
        plaus = 16 <= cy["od"] <= 72 and (cy["sup"] is None or cy["top"] - cy["sup"] >= 8)
        if not plaus:
            continue
        holed = cy["deep"] >= 0.5 or (cy["hd2"] >= 2.5 and cy["lowc"] >= 0.8)
        hole_ev = 2.0 if holed else min(1.0, cy["deep"] + cy["hd2"] / 5 + cy["lowc"] * 0.3)
        cy["rank"] = hole_ev + min(rc["score"], 3.5)
        found.append(cy)
        if holed and rc["score"] >= 1.5:
            break
    found.sort(key=lambda c: -c["rank"])
    print("ring candidates", [(np.round(c["xy"], 0).tolist(), round(c["rank"], 2)) for c in found])
    return found


def find_stick(robot, S, camoff, ring):
    """the stand's dowel: thin, standing high above a plate (the board). Best first."""
    cands = []
    for sc in S["sticks"]:
        if np.linalg.norm(sc["xyz"][:2] - ring["xy"]) < ring["od"] / 2 + 10:
            continue
        if any(np.linalg.norm(sc["xyz"][:2] - o["xyz"][:2]) < 20 for o in cands):
            continue
        cands.append(sc)
    deferred = []
    for sc in cands[:5]:
        top_est = sc["top"]
        if sc["pedh"] > 8:              # on a plate: the dowel stands ~170 mm above it (the survey may cut it)
            top_est = max(top_est, sc["ped"] + STICK_H)
        fs = closeup(robot, sc["xyz"][:2], top_est, camoff)
        sk = stick_closeup(fs, sc["xyz"][:2], top_est, S["coef"])
        print("STICK closeup", None if sk is None else fmt(sk))
        if sk is None or sk["eqd"] > 22 or sk["lrise"] < 60:
            continue
        if np.linalg.norm(sk["xy"] - ring["xy"]) < ring["od"] / 2 + 8:
            continue
        if sk["base"] is None:
            sk["base"] = sc["ped"]
        c = S["coef"]
        ped = sk["base"] - (c[0] * sk["xy"][0] + c[1] * sk["xy"][1] + c[2])
        sk["score"] = sc["score"]
        print("  pedestal %.1f plate %.2f lrise %.0f" % (ped, sk["plate"], sk["lrise"]))
        if ped >= 6.0 and sk["plate"] >= 0.4 and sk["lrise"] >= 110:
            return sk, fs
        deferred.append((sk, fs))
    if deferred:
        deferred.sort(key=lambda t: -(t[0]["plate"] + t[0]["lrise"] / 100.0))
        return deferred[0]
    return None, None


# ------------------------------------------------------------------ the ring: centre, calibrate, take
def ring_heights(ring):
    sup = ring["sup"] if ring["sup"] is not None else ring["top"] - 30
    # grip low (a tall ring gripped high tips); fingertips 6-8 mm above the support seen
    zc = max(sup + 16.0 + float(np.clip(0.35 * (ring["top"] - sup), 6.0, 11.0)), 12.0)
    return sup, zc


def centre_ring(robot, cxy, ring, HG):
    """centre the ring between the fingers along two lines; returns the TCP (= the ring's axis) or None"""
    sup, zc = ring_heights(ring)
    th, clr = best_closing(HG, np.asarray(cxy, float), max(ring["od"] / 2 + 8, 30.0), zc - 16)
    print("ring closing angle", th, "clearance %.1f" % clr)
    stop_from = ring["top"] + 22 if ring["od"] > 54 else None
    sq = squeeze_at(robot, "CYL", cxy, ring["top"] + 45, zc, [th, th + 45, th - 45, th + 22, th - 22],
                    2.5, 79.0, stop_from, HG, ring["od"] / 2 + 6)
    if sq is None:
        return None
    C = tcp(robot)
    t = truth(robot)
    if t and "w_cyl" in t:
        print("CYL truth err", np.round(np.array(t["w_cyl"][:2]) - C[:2], 2))
    return C


def take_ring(robot, C, ring):
    """the ring is centred at C (released): take it there again with the current closing line"""
    sup, zc = ring_heights(ring)
    p = tcp(robot)
    robot.set_gripper(ARM, 80)
    travel(robot, (C[0], C[1], max(ring["top"] + 45, p[2] if math.hypot(*(p[:2] - C[:2])) < 15 else 0)))
    mv(robot, (C[0], C[1], C[2]))
    for k in range(2):
        q = tcp(robot)
        err = C[:2] - q[:2]
        if np.linalg.norm(err) < 0.8:
            break
        mv(robot, (q[0] + err[0], q[1] + err[1], C[2]))
    w = robot.set_gripper(ARM, 0)
    print("CYL take w %.1f at" % w, np.round(tcp(robot), 1))
    if w < 2.5:
        return None
    G = tcp(robot)
    mv(robot, (G[0], G[1], G[2] + 25))
    w2 = float(st(robot)["opening_mm"])
    if w2 < 2.5:
        print("CYL slipped out on lifting", round(w2, 1))
        return None
    return dict(C=G, db=G[2] - sup, w=w)


# ------------------------------------------------------------------ insert
def insert(robot, Sxy, stick, db, zt, HG=None, od=40.0):
    base_top, stick_top = stick["base"], stick["top"]
    C = tcp(robot)
    mv(robot, (C[0], C[1], zt))
    mv(robot, (Sxy[0], Sxy[1], zt))
    zend = max(base_top + 5 + db, base_top + 23, 12.0, stick_top - 95)
    if HG is not None:
        c = np.array(cdir(robot)[:2])
        a0 = math.degrees(math.atan2(c[1], c[0]))
        th, clr = best_closing(HG, np.asarray(Sxy, float), max(od / 2 + 4, 24.0), stick_top - 15 - 16,
                               angles=[a0 + d for d in (0, 30, -30, 60, -60, 90)], extra=())
        rot = ((th - a0 + 90) % 180) - 90
        if abs(rot) > 5:
            print("insert: turning", round(rot), "clearance %.1f" % clr)
            turn(robot, rot)
    # the arm's moves end a few mm off (and the loaded arm sags): correct until the measured TCP (the ring's
    # axis) is over the stick
    cmd = np.array([Sxy[0], Sxy[1], stick_top + 12 + db], float)
    mv(robot, cmd)
    for z in (cmd[2], stick_top + 5 + db):
        cmd[2] = z
        for _ in range(4):
            q = tcp(robot)
            err = np.asarray(Sxy, float) - q[:2]
            if np.linalg.norm(err) < 0.25 and abs(q[2] - z) < 3:
                break
            cmd[:2] += err
            mv(robot, cmd)
    corr = cmd[:2] - np.asarray(Sxy, float)
    print("insert: xy correction", np.round(corr, 2), "residual", np.round(np.asarray(Sxy) - tcp(robot)[:2], 2))
    zeng = max(zend, stick_top - 14 + db)      # then it is let go and slides down by itself
    r = mv(robot, (cmd[0], cmd[1], zeng), stop=True)
    zn = tcp(robot)[2]
    print("insert", r.get("why"), "z", round(zn, 1), "zeng", round(zeng, 1), "bottom", round(zn - db, 1),
          "stick_top", round(stick_top, 1))
    if not (r.get("why") and zn - db > stick_top - 8):
        return True
    for rad in (0.6, 1.2, 1.8, 2.5, 3.3):
        nk = 6 if rad < 1 else (8 if rad < 1.5 else (10 if rad < 2 else 12))
        for k in range(nk):
            if robot.time_s() > 480:
                print("search: out of time")
                return False
            a = 2 * math.pi * k / nk + rad
            # the probe's place relative to where the measured TCP was over the stick (moves end a little off)
            gx, gy = Sxy[0] + rad * math.cos(a), Sxy[1] + rad * math.sin(a)
            x, y = gx + corr[0], gy + corr[1]
            mv(robot, (x, y, zn + 2.0))
            q = tcp(robot)
            e = np.array([gx - q[0], gy - q[1]])
            if np.linalg.norm(e) > 0.3:
                x, y = x + e[0], y + e[1]
                mv(robot, (x, y, zn + 2.0))
            mv(robot, (x, y, zn - 6), stop=True)
            if tcp(robot)[2] < zn - 3:
                print("search found at offset", round(rad, 1), round(math.degrees(a) % 360))
                if tcp(robot)[2] > zeng + 1:
                    mv(robot, (x, y, zeng), stop=True)
                return True
    print("search failed")
    return False


def finish(robot, z_clear=0.0):
    """open, lift straight up clear of everything, go to the start pose (straight, then by the joints)"""
    robot.set_gripper(ARM, 80)
    p = tcp(robot)
    zc = min(700.0, max(z_clear + 70.0, p[2] + 10, 300.0))
    mv(robot, (p[0], p[1], zc))
    for z in (max(zc, HOME[2]), 500.0):
        mv(robot, (HOME[0], HOME[1], z), closing=C0)
        r = robot.home(ARM)
        if r.get("moved") or r.get("done"):
            return
        print("home refused:", r.get("why"))
    mv(robot, HOME, closing=C0)


def run(robot, task):
    try:
        main(robot, task)
    except Exception as e:
        print("ERROR", repr(e))
        try:
            finish(robot, TRAVEL["z"])
        except Exception as e2:
            print("ERROR in finish", repr(e2))


def calibrate(robot, C, ring, camoff):
    """the ring stands centred at C (let go): look at it from the close-up distance; seen - C = camera error"""
    fc = closeup(robot, C[:2], ring["top"], camoff)
    cy = ring_closeup(fc, C[:2], ring["top"], ring["od"])
    if cy is None or not (16 <= cy["od"] <= 72):
        print("calibration look failed", None if cy is None else fmt(cy))
        return None, cy
    b = cy["xy"] - C[:2]
    print("camera bias (ring seen - centred)", np.round(b, 2), "first look - centred", np.round(ring["xy"] - C[:2], 2))
    if np.linalg.norm(b) > 20:
        return None, cy
    return b, cy


def main(robot, task):
    T0 = truth(robot)
    if T0:
        print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in T0.items()})
    robot.set_gripper(ARM, 80)
    S = survey(robot)
    f = S["f"]
    camoff = np.asarray(f.eye, float) - tcp(robot)
    TRAVEL["z"] = min(600.0, max(200.0, S["zmax"] + 60.0))
    print("plane", np.round(S["coef"], 4), "zmax", round(S["zmax"], 1), "camoff", np.round(camoff, 1))
    for s in S["sticks"][:3]:
        print("STICKC", fmt(s))
    for r in S["rings"][:3]:
        print("RINGC", fmt(r))
    ann = f.rgb.copy()
    for s in S["sticks"][:4]:
        cv2.circle(ann, s["uv"], 6, (255, 0, 0), 1)
    for r in S["rings"][:4]:
        cv2.circle(ann, r["uv"], 10, (0, 255, 0), 1)
    robot.show("survey", ann)
    rings = find_ring(robot, S, camoff)
    if not rings:
        print("no ring found")
        finish(robot, S["zmax"])
        return
    ring = rings[0]
    stick, fs = find_stick(robot, S, camoff, ring)
    if stick is None:
        print("no stick found")
        finish(robot, S["zmax"])
        return
    T = truth(robot)
    if T and "w_stick" in T:
        print("STICK seen - truth (top xy)", np.round(stick["xy"] - np.array(T["w_stick"][:2]), 2))
    ok = False
    bias = np.zeros(2)
    for attempt in range(2):
        C = None
        while C is None:
            C = centre_ring(robot, ring["xy"] - bias, ring, S["HG"])
            if C is None:
                if len(rings) < 2 or robot.time_s() > 400:
                    break
                rings = rings[1:]
                ring = rings[0]
                print("cylinder not held: trying the next candidate at", np.round(ring["xy"], 1))
        if C is None:
            print("cylinder grasp failed")
            break
        # let go, look again: the camera's error
        robot.set_gripper(ARM, 80)
        b, cy = calibrate(robot, C, ring, camoff)
        if b is not None:
            bias = b
        if cy is not None and attempt == 0 and stick is not None:
            pass
        Sxy = stick["xy"] - bias
        if T and "w_stick" in T:
            print("STICK corrected - truth", np.round(Sxy - np.array(T["w_stick"][:2]), 2))
        g = take_ring(robot, C, ring)
        if g is None:
            print("could not take the cylinder again")
            ring["xy"] = C[:2] + bias
            continue
        db = g["db"]
        zt = max(stick["top"], S["zmax"], ring["top"]) + db + 35
        if insert(robot, Sxy, stick, db, zt, S["HG"], ring["od"]):
            ok = True
            robot.set_gripper(ARM, 80)
            robot.wait(0.3)
            break
        # put the cylinder back where it was and look at both again
        C2 = g["C"]
        mv(robot, (tcp(robot)[0], tcp(robot)[1], zt))
        mv(robot, (C2[0], C2[1], zt))
        mv(robot, (C2[0], C2[1], C2[2] - 25 + 2.0))
        robot.set_gripper(ARM, 80)
        ring["xy"] = C2[:2] + bias
        fs2 = closeup(robot, stick["xy"], stick["top"], camoff)
        sk2 = stick_closeup(fs2, stick["xy"], stick["top"], S["coef"])
        if sk2 is not None and sk2["eqd"] <= 22 and np.linalg.norm(sk2["xy"] - stick["xy"]) < 15:
            stick["xy"] = 0.5 * (stick["xy"] + sk2["xy"])
            stick["top"] = 0.5 * (stick["top"] + sk2["top"])
    finish(robot, max(stick["top"], S["zmax"]))
    j = None
    try:
        j = robot.judge()
    except Exception:
        pass
    print("RESULT", ok, "JUDGE", j)
