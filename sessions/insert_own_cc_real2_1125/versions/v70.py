"""Put the cylinder (with a hole) on the stick of the stand. Entry point: run(robot, task).

Made for the real rig: a black table at z = 0, a stand that is a long wooden board with ONE tall thin stick
(~10 mm, ~170 mm above the board) in one of its holes, cylinders of many kinds, things possibly on platforms.

1. survey: the wrist camera looks down on the work area from high up (TCP (-350, 60, 620)); table plane fit;
   stick candidates (thin things much higher than their close surroundings) and cylinder candidates (SAM masks
   and compact objects).
2. close-ups, always with the camera 380 mm above the thing's top, right over it, the gripper turned the same
   way: the cylinder (a round top with a deep hole), the stick (a thin top far above a board).
3. the cylinder is centred between the fingers (closing along two lines 90 degrees apart, the fingers opened
   by what the camera saw, never by what they read: soft fingers read narrower than what they hold): its
   position is then the TCP's. It is put down there, the camera looks at it again from the same distance:
   seen minus true is the camera's calibration error, the same for the stick's top seen the same way.
4. grasped again, carried high, the measured TCP brought over the stick's corrected top, lowered with
   stop-on-contact (short); if it stops on the stick's top: a small spiral search; released once on the stick.
5. open, lift clear, back to the start pose.
"""
import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
C0 = (0.0, -1.0, 0.0)
SURVEYS = [(-350.0, 60.0, 620.0), (-420.0, 60.0, 600.0), (-300.0, -150.0, 560.0)]
HOME = (-478.0, 41.0, 350.0)
LOOK_D = 380.0           # the camera's distance above a thing's top in the close-ups (D415: no depth < ~300)
TIP = 16.0               # the fingertips below the TCP (14 on the real gripper: 2 mm more margin there)
CAM = {"up": 133.0}
TRAVEL = {"z": 300.0}
T_LIMIT = 560.0


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


def travel(robot, xyz, closing=None):
    """up, across, down (a straight line between places could sweep the fingers through things)"""
    p = tcp(robot)
    zt = max(TRAVEL["z"], float(xyz[2]))
    if math.hypot(xyz[0] - p[0], xyz[1] - p[1]) > 15:
        if p[2] < zt - 1:
            mv(robot, (p[0], p[1], zt))
        r = mv(robot, (xyz[0], xyz[1], zt), closing=closing)
        if abs(zt - float(xyz[2])) < 0.5 or not r.get("done"):
            return r
    return mv(robot, xyz, closing=closing)


def open_wide(robot):
    if float(st(robot)["opening_mm"]) < 78.0:
        robot.set_gripper(ARM, 80)


def turn(robot, ang):
    p = tcp(robot)
    r = robot.turn(ARM, p.tolist(), (0, 0, 1), ang)
    if r.get("moved"):
        return ang
    r = robot.turn(ARM, p.tolist(), (0, 0, 1), -ang)
    if r.get("moved"):
        return -ang
    print("TURN refused", r.get("why"))
    return 0


def truth(robot):
    try:
        return robot.truth()
    except Exception:
        return None


def fmt(d):
    return {k: (np.round(v, 1).tolist() if isinstance(v, np.ndarray) else (round(v, 2) if isinstance(v, float) else v))
            for k, v in d.items() if k not in ("mask", "img")}


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


# ------------------------------------------------------------------ survey
def survey(robot, pose):
    r = mv(robot, pose, closing=C0)
    if not r.get("done"):
        return None
    f = robot.look(["left_wrist"])["left_wrist"]
    P = f.points().copy()
    dep = f.depth
    Hh, Ww = dep.shape
    bad = ~np.isfinite(dep) | (dep < 330)
    bad[int(Hh * 0.78):, :int(Ww * 0.3)] = True      # the fingertips
    bad[int(Hh * 0.78):, int(Ww * 0.7):] = True
    P[bad] = np.nan
    coef = table_plane(P)
    zp = coef[0] * P[..., 0] + coef[1] * P[..., 1] + coef[2]
    h = P[..., 2] - zp
    h[(h > 400) | (h < -25)] = np.nan                 # (beyond the table: the floor, the robot's rail)
    P[~np.isfinite(h)] = np.nan
    valid = np.isfinite(h)
    hf = fill_nan(h)
    rng_mm = float(np.nanmedian(dep[valid]))
    pxt = f.fx / rng_mm
    op = cv2.morphologyEx(hf, cv2.MORPH_OPEN, disc(44 * pxt))
    res = hf - op
    sam = {}
    sam["stick"], sam["washer"] = [], []
    for ph in ("ring", "cylinder with a hole"):
        try:
            sam[ph] = [(m.astype(bool), float(s)) for m, s in robot.segment(f, ph)]
        except Exception as e:
            print("segment fail", ph, e)
            sam[ph] = []
    # ---- the stick: thin, far higher (~170 mm) than the board around it
    sticks = []
    n, lab, stats, _ = cv2.connectedComponentsWithStats((res > 40).astype(np.uint8), 8)
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
        patch = comp & (hf[sl] > top - 8)
        eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
        if lrise < 90 or eqd > 40:
            continue
        p = ray_point(f, u, v, top + coef[2])
        samv = max([s for m, s in sam["stick"] if m[max(0, v - 4):v + 5, max(0, u - 4):u + 5].any()] + [0.0])
        score = min(4.0, lrise / 40.0) + (0.8 if 5 <= eqd < 25 else 0.0) + (0.5 if ped > 8 else 0.0) + samv
        sticks.append(dict(uv=(int(u), int(v)), xyz=p, top=float(p[2]), lrise=lrise, eqd=eqd, ped=ped + coef[2],
                           score=score))
    sticks.sort(key=lambda s: -s["score"])
    # ---- cylinder candidates: SAM masks and compact objects
    cands = []
    for ph in ("ring", "cylinder with a hole", "washer"):
        for m, s in sam[ph][:4]:
            if m.sum() < 20:
                continue
            for r in cands:
                if mask_iou(r["mask"], m) > 0.5:
                    r["sam"] += s
                    break
            else:
                cands.append(dict(mask=m, sam=s))
    # compact things above the table, and compact things above what is around them (on a platform)
    res2 = hf - cv2.morphologyEx(hf, cv2.MORPH_OPEN, disc(75 * pxt))
    for k, objm in enumerate((((hf > 8) & valid).astype(np.uint8), ((res2 > 11) & valid).astype(np.uint8))):
        n, lab, stats, _ = cv2.connectedComponentsWithStats(objm, 8)
        for i in range(1, n):
            a = stats[i, cv2.CC_STAT_AREA]
            if k == 1 and a >= 20:           # its tallest part only (a platform's rim may hang on to it)
                m = lab == i
                m = m & (res2 > max(11.0, 0.6 * float(np.percentile(res2[m], 95))))
                a = int(m.sum())
            eqd = 2 * math.sqrt(a / math.pi) / pxt
            if 15 < eqd < 85:
                m = (lab == i) if k == 0 else m
                if k == 1:                   # (depth noise makes small bumps: only round, solid ones)
                    cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                    per = max(cv2.arcLength(max(cnts, key=len), True), 1.0)
                    if 4 * math.pi * a / per ** 2 < 0.6 or float(np.median(res2[m])) < 11:
                        continue
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
        if float(np.percentile(hm, 50)) - sup < 5:      # mostly flat table: not a thing
            continue
        plaus = (8 < rise < 160) and (16 < eqd < 85)      # (rise: from the table, it may be on something)
        # a hole already seen from up here: its middle lower than its top (or no depth there)
        rh = max(1.5, 0.12 * eqd * pxmm)
        win, cdisc = annulus(h.shape, u, v, 0.0, rh)
        hc = h[win][cdisc]
        holey = float(np.mean(~np.isfinite(hc) | (np.nan_to_num(hc, nan=-1e4) < top - 6))) if hc.size else 0.0
        r.update(uv=(int(u), int(v)), xyz=p, top=float(p[2]), rise=rise, eqd=eqd, circ=circ, holey=holey,
                 score=r["sam"] + (0.8 if plaus else -1.5) + (0.4 if circ > 0.7 else 0.0)
                 + (1.0 if holey > 0.5 and plaus else 0.0))
        rings.append(r)
    rings.sort(key=lambda r: -r["score"])
    zmax = float(np.nanpercentile(P[..., 2], 99.8))
    HG = height_grid(P, float(coef[2] + coef[0] * -300 + coef[1] * 50))
    return dict(f=f, coef=coef, sticks=sticks, rings=rings, zmax=zmax, HG=HG)


def cam_offset(f, robot):
    t = tcp(robot)
    return np.asarray(f.eye[:2], float) - t[:2], float(f.eye[2] - t[2])


# ------------------------------------------------------------------ close-ups
def closeup(robot, xy, top_z, camoff):
    """the camera LOOK_D above top_z, right over xy, the gripper turned as in the survey"""
    z = min(780.0, max(top_z + LOOK_D - CAM["up"], 200.0))
    tx, ty = xy[0] - camoff[0], xy[1] - camoff[1]
    tx = min(90.0, max(-700.0, tx))
    CAM["reached"] = False
    ok = False
    # (near the parked arm higher poses are free; for a high top (a stand on something) the arm may not reach
    # that high: a little nearer, or from nearer the arm's base, the thing then off the image's centre)
    for dx, dz in ((0, 0), (0, 60), (0, -40), (-60, 0), (-60, -40), (0, 120), (-120, -20), (0, -70)):
        r = travel(robot, (tx + dx, ty, min(780.0, z + dz)), closing=C0)
        if r.get("done"):
            CAM["reached"] = dx == 0 and dz == 0
            ok = True
            break
    if not ok:
        print("closeup: no pose reached for", np.round(xy, 1), round(top_z, 1))
        return None
    if float(np.dot(st(robot)["closing_dir"], C0)) < 0:
        print("closeup: gripper flipped, turning it back")
        if turn(robot, 180) == 0:
            turn(robot, 90)
            turn(robot, 90)
    if float(np.dot(st(robot)["closing_dir"], C0)) < 0.9:
        CAM["reached"] = False             # (the camera is not on its usual side: its error is another)
    return robot.look(["left_wrist"])["left_wrist"]


def free_spot(S, od, avoid):
    """a free place on the table in the middle of the work area (for the cylinder, to look at it there)"""
    HG, c = S["HG"], S["coef"]
    best = None
    for x in np.arange(-460.0, -144.0, 25.0):
        for y in np.arange(-190.0, 251.0, 25.0):
            if any(math.hypot(x - a[0], y - a[1]) < 80 for a in avoid) or (x < -400 and y < -150):
                continue
            zt = c[0] * x + c[1] * y + c[2]
            h = region_max(HG, np.array([x, y]), (1.0, 0.0), 0.0, od / 2 + 34, od / 2 + 34, pct=99) - zt
            if h > 10:
                continue
            d = math.hypot(x + 300.0, y - 30.0)
            if best is None or d < best[0]:
                best = (d, x, y)
    return None if best is None else np.array([best[1], best[2]])


def stick_closeup(f, guess_xy, guess_top, coef=None):
    if f is None:
        return None
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
    n, lab, stats, cents = cv2.connectedComponentsWithStats((res > 25).astype(np.uint8), 8)
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
    vs, us = np.nonzero(patch)
    uc, vc = float(us.mean()), float(vs.mean())
    pxmm = f.fx / max(150.0, float(f.eye[2] - top))
    xy = ray_point(f, uc, vc, top)[:2]
    eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
    rp = eqd / 2
    win2, ann2 = annulus(z.shape, uc, vc, (rp + 5) * pxmm, (rp + 16) * pxmm)
    zz2 = z[win2][ann2]
    zz2 = zz2[np.isfinite(zz2)]
    lrise = top - float(np.percentile(zz2, 75)) if len(zz2) > 10 else 0.0
    base = float(np.percentile(zz2, 70)) if len(zz2) > 10 else None
    plate = 0.0
    if coef is not None:
        zt = coef[0] * xy[0] + coef[1] * xy[1] + coef[2]
        win3, ann3 = annulus(z.shape, uc, vc, (rp + 5) * pxmm, (rp + 20) * pxmm)
        zz3 = z[win3][ann3]
        zz3 = zz3[np.isfinite(zz3)]
        if len(zz3) > 20:
            plate = float(np.mean((zz3 > zt + 3.5) & (zz3 < top - 10)))
    cen = math.hypot(uc - f.cx, vc - f.cy) / pxmm          # how far from the image's centre (mm at the top)
    return dict(xy=xy, top=top, eqd=eqd, base=base, lrise=lrise, plate=plate, uv=(uc, vc), cen=cen)


def ring_closeup(f, guess_xy, guess_top, guess_d):
    if f is None:
        return None
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
    zh = z[hole]
    low = float(np.mean(~np.isfinite(zh) | (np.nan_to_num(zh, nan=-1e4) < ztop - 6))) if hole.any() else 0.0
    zhf = zh[np.isfinite(zh)]
    high = float(np.mean(zhf > ztop + 5)) if len(zhf) > 3 else 0.0     # a dome (a sphere), not a hole
    rc = np.hypot(xx - uc, yy - vc) / pxmm
    ann = (rc > od / 2 + 6) & (rc < od / 2 + 22) & np.isfinite(z) & (z < ztop - 6)
    sup = float(np.percentile(z[ann], 30)) if ann.sum() > 20 else None
    # a support only a little wider than the cylinder (a tower of blocks): right around it, mostly higher
    near = (rc > od / 2 + 1.0) & (rc < od / 2 + 10) & np.isfinite(z) & (z < ztop - 4)
    tower = False
    if near.sum() > 12:
        zn_ = z[near]
        hi = zn_[zn_ > (sup if sup is not None else float(np.min(zn_))) + 8]
        if len(hi) >= 12 and len(hi) >= 0.15 * len(zn_):
            sup, tower = float(np.percentile(hi, 40)), True
    cen = rc < 2.5
    zc = z[cen]
    deep = float(np.mean(~np.isfinite(zc) | (np.nan_to_num(zc, nan=-1e4) < ztop - 6))) if cen.any() else 0.0
    hd2, lowc, lowfin = 0.0, 0.0, 0.0
    nh, hl, hst, hce = cv2.connectedComponentsWithStats(hole.astype(np.uint8), 8)
    for i in range(1, nh):
        if math.hypot(hce[i][0] - uc, hce[i][1] - vc) / pxmm > 4:
            continue
        d_ = 2 * math.sqrt(hst[i, cv2.CC_STAT_AREA] / math.pi) / pxmm
        if d_ > hd2:
            zz_ = z[hl == i]
            hd2 = d_
            lowc = float(np.mean(~np.isfinite(zz_) | (np.nan_to_num(zz_, nan=-1e4) < ztop - 6)))
            lowfin = float(np.mean(np.nan_to_num(zz_, nan=1e4) < ztop - 6))   # seen through: the support
    img = f.rgb.copy()
    cv2.drawContours(img, cnts, -1, (255, 0, 0), 1)
    cv2.circle(img, (int(round(uc)), int(round(vc))), 3, (0, 255, 0), -1)
    return dict(xy=c3[:2], top=ztop, od=od, hole_d=hole_d, low=low, high=high, deep=deep, hd2=hd2, lowc=lowc,
                lowfin=lowfin, tower=tower,
                sup=sup, uv=(uc, vc), img=img, cen=math.hypot(uc - f.cx, vc - f.cy) / pxmm)


# ------------------------------------------------------------------ free directions for the fingers and the hand
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


def region_max(HG, centre, cdir_xy, along0, along1, across, pct=95):
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
    return float(np.percentile(G[ix[ok], iy[ok]], pct))


def best_closing(HG, centre, inner, z_tip, z_body=None, angles=(0, 30, 60, 90, 120, 150), extra=(45, 90)):
    """the closing direction (deg from +x) whose finger regions (and those turned by `extra`) are clearest
    below the fingertips (z_tip), and whose hand (+-105 mm along, +-60 across) clears z_body"""
    best = None
    for th in angles:
        hs, hb = [], []
        for e in (0,) + tuple(extra):
            t = math.radians(th + e)
            cd = (math.cos(t), math.sin(t))
            hs.append(region_max(HG, np.asarray(centre, float), cd, inner, 58.0, 21.0))
            if z_body is not None:
                hb.append(region_max(HG, np.asarray(centre, float), cd, 0.0, 105.0, 60.0, pct=100))
        clr = z_tip - max(hs)
        if hb:
            clr = min(clr, z_body - max(hb))
        if best is None or clr > best[1] + 0.5:
            best = (th, clr)
    return best


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


# ------------------------------------------------------------------ centring a thing between the fingers
def center_along(robot, label, min_w, max_w, open_w, iters=2, push_min=3.0, max_step=5.0):
    """close on the thing along the current closing line. A free thing is pushed to the middle; if the arm is
    pushed instead (a heavy thing), step toward it and close again. Between closes the fingers open to open_w
    (from what the camera saw: soft fingers read narrower than what they hold). Leaves it closed; returns w."""
    w = 0.0
    for it in range(iters):
        p0 = tcp(robot)
        w = robot.set_gripper(ARM, 0)
        s1 = st(robot)
        p1 = np.array(s1["tcp_mm"], float)
        d = p1 - p0
        d[2] = 0.0
        nd = float(np.linalg.norm(d))
        print(label, "w %.2f push %.2f" % (w, nd))
        if w < min_w or w > max_w or nd < push_min or it == iters - 1:
            return w
        let_go(robot, w, open_w)
        t = p0 + min(1.5 * nd, max_step) * d / nd
        mv(robot, (t[0], t[1], p0[2]))
    return w


def let_go(robot, w, open_w):
    """open gently: first just loose (a sudden wide opening can fling a light thing), then wide"""
    w1 = min(open_w, max(w + 4.0, open_w - 22.0))
    if w1 < open_w - 1:
        robot.set_gripper(ARM, w1)
        robot.wait(0.2)
    robot.set_gripper(ARM, open_w)


def squeeze_center(robot, label, min_w, max_w, open_w, single=False):
    """centre it along the closing line, then (turned 90 degrees) along the other. Returns the widths, None
    (nothing there) or "TURNFAIL"."""
    w1 = center_along(robot, label + "-a", min_w, max_w, open_w)
    if w1 < min_w:                      # missed it (not where it was seen, or pushed away): look again
        robot.set_gripper(ARM, open_w)
        return "MISS"
    if not (min_w <= w1 <= max_w):
        robot.set_gripper(ARM, open_w)
        return None
    if single:
        return [w1]
    let_go(robot, w1, open_w)
    if turn(robot, 90) == 0:
        return "TURNFAIL"
    w2 = center_along(robot, label + "-b", min_w, max_w, open_w)
    if w2 < min_w:
        robot.set_gripper(ARM, open_w)
        return "MISS"
    if not (min_w <= w2 <= max_w):
        robot.set_gripper(ARM, open_w)
        return None
    print(label, "widths", round(w1, 2), round(w2, 2), "tcp", np.round(tcp(robot), 2))
    return [w1, w2]


def squeeze_at(robot, label, xy, z_safe, z_grip, angles, min_w, max_w, open_w, z_pre=None, z_g1=None,
               single=False, relook=None):
    """go down at xy to z_grip with one of the closing angles (plain to z_pre, until contact to z_g1, plain
    on) and centre the thing there; when a move or a turn is refused, come back up and try the next angle;
    when the fingers close on nothing, look again (relook() -> its place) and try the next angle."""
    tried = []
    reset = False
    misses = 0
    angles = list(angles)
    k = 0
    while k < len(angles):
        th = angles[k]
        k += 1
        if any(abs(((th - t) + 90) % 180 - 90) < 10 for t in tried):
            continue
        tried.append(th)
        cd = (math.cos(math.radians(th)), math.sin(math.radians(th)), 0.0)
        r = travel(robot, (xy[0], xy[1], z_safe), closing=cd)
        if not r.get("done"):
            print(label, "closing angle", round(th), "refused:", (r.get("why") or "")[:70])
            if k == len(angles) and not reset:      # the arm may be wound up: back to its start, all again
                reset = True
                p = tcp(robot)
                mv(robot, (p[0], p[1], max(p[2], TRAVEL["z"])))
                robot.home(ARM)
                tried, k = [], 0
            continue
        open_wide(robot)
        if z_pre is not None:
            if z_pre < z_safe - 2:
                mv(robot, (xy[0], xy[1], max(z_pre, z_grip)))
            zg1 = max(z_grip, z_g1 if z_g1 is not None else z_grip)
            r = mv(robot, (xy[0], xy[1], zg1), stop=True)
            if not r.get("why") and tcp(robot)[2] < zg1 + 3 and zg1 > z_grip + 0.5:
                r = mv(robot, (xy[0], xy[1], z_grip))      # the fingers are clear of its top: on, plain
        else:
            r = mv(robot, (xy[0], xy[1], z_grip))
        p = tcp(robot)
        if not r.get("moved", True) or (r.get("why") and r.get("why") != "contact"):
            print(label, "descent refused/stopped at angle", round(th), (r.get("why") or "")[:70])
            mv(robot, (p[0], p[1], z_safe))
            continue
        if r.get("why") or p[2] > z_grip + 3:
            print(label, "descent held up at", np.round(p, 1), "angle", round(th))
            if p[2] > z_grip + 8:            # something under the fingers: try another direction
                mv(robot, (p[0], p[1], z_safe))
                continue
            mv(robot, (p[0], p[1], p[2] + 2))
        ws = squeeze_center(robot, label, min_w, max_w, open_w, single)
        if ws == "MISS":
            misses += 1
            print(label, "missed at angle", round(th), "tcp", np.round(tcp(robot), 1))
            robot.set_gripper(ARM, 80)
            p = tcp(robot)
            mv(robot, (p[0], p[1], z_safe + 10))
            if misses >= 3 or relook is None:
                return None
            nxy = relook()
            if nxy is None:
                return None
            xy = nxy
            continue
        if isinstance(ws, str):
            print(label, "turn refused at angle", round(th))
            robot.set_gripper(ARM, 80)
            p = tcp(robot)
            mv(robot, (p[0], p[1], z_safe))
            continue
        CAM["angle"] = th
        return ws
    return None


# ------------------------------------------------------------------ the steps
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


def ring_support(ring, coef):
    sup = ring["sup"] if ring["sup"] is not None else ring["top"] - 30
    if ring["top"] - sup > 58:           # no cylinder is that tall: its support was not seen
        sup = ring["top"] - 25.0
    if abs(sup) < 9:                     # on the table (the real table's top is at z = 0)
        return 0.0, True
    return sup, False


def find_ring(robot, S, camoff):
    found = []
    def reachable(p):
        return -620 < p[0] < -95 and -400 < p[1] < 600 and not (p[1] > 250 and p[0] > -150)
    # not flat things, not big ones, not out of the arm's reach
    sk0 = S["sticks"][0]["xyz"][:2] if S["sticks"] else np.array([1e4, 1e4])
    cands = [rc for rc in S["rings"] if rc["rise"] >= 6 and rc["eqd"] <= 90 and reachable(rc["xyz"])
             and np.linalg.norm(rc["xyz"][:2] - sk0) > 25]
    print("cylinder candidates to look at:", len(cands))
    for rc in cands[:9]:
        if robot.time_s() > 250:
            break
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
        # a hole: deeper than the top, and the support seen through it (a dark thing's top only loses depth)
        holed = (cy["deep"] >= 0.5 or (cy["hd2"] >= 2.5 and cy["lowc"] >= 0.8)) and cy["high"] < 0.15 \
            and cy["lowfin"] >= 0.1
        # (on the black table nothing is seen through the hole: a hole of a plausible size in a thing that the
        # segmentation calls a ring is one too)
        holed_w = (not holed and (cy["deep"] >= 0.5 or (cy["hd2"] >= 2.5 and cy["lowc"] >= 0.8))
                   and cy["high"] < 0.15 and 0.15 <= cy["hd2"] / max(cy["od"], 1.0) <= 0.75 and rc["sam"] >= 0.5)
        hole_ev = 2.0 if holed else min(1.0, cy["deep"] + cy["hd2"] / 5 + cy["lowc"] * 0.3) - 2 * cy["high"] \
            - (1.0 if cy["lowfin"] < 0.1 else 0.0)
        cy["rank"] = (10.0 if holed else (6.0 if holed_w else 0.0)) + hole_ev + min(rc["score"], 3.5)
        found.append(cy)
        if holed or holed_w:
            break
    found.sort(key=lambda c: -c["rank"])
    print("ring candidates", [(np.round(c["xy"], 0).tolist(), round(c["rank"], 2)) for c in found])
    return found


def find_stick(robot, S, camoff, ring):
    """the stick's top seen from LOOK_D above it, nearly at the image's centre"""
    cands = []
    for sc in S["sticks"]:
        if np.linalg.norm(sc["xyz"][:2] - ring["xy"]) < ring["od"] / 2 + 10:
            continue
        if any(np.linalg.norm(sc["xyz"][:2] - o["xyz"][:2]) < 25 for o in cands):
            continue
        cands.append(sc)
    for sc in cands[:4]:
        xy, top = sc["xyz"][:2], sc["top"]
        sk, prev = None, None
        for k in range(3):
            fs = closeup(robot, xy, top, camoff)
            if fs is None and prev is not None:      # (not from right above it: the look before will do)
                sk, fs = prev
                break
            sk = stick_closeup(fs, xy, top, S["coef"])
            prev = (sk, fs)
            print("STICK closeup", None if sk is None else fmt(sk))
            if sk is None or not (sk["eqd"] <= 24 and sk["lrise"] >= 90):
                sk = None
                break
            if sk["cen"] < 12 and abs(sk["top"] - top) < 8:
                break
            xy, top = sk["xy"], sk["top"]        # look again from right above it
        if sk is not None:
            robot.show("stick_closeup", fs.rgb)
            return sk
    return None


def grasp_ring(robot, cxy, ring, S, known=False, single=False, first_angle=None):
    """centre the cylinder between the fingers low on it; returns dict(C=TCP, db=TCP z - its bottom, w) or
    None. known: its position is known from a grasp (plain moves all the way down)"""
    sup, on_table = ring_support(ring, S["coef"])
    Hc = ring["top"] - sup
    # (on something: its top is seen only, and may be soft or uneven: the fingertips well clear of it)
    clr = 4.0 if on_table else float(np.clip(0.3 * Hc, 5.0, 7.0))
    zc = max(sup + TIP + min(clr, max(2.0, Hc / 2)), 12.0)
    od_c = ring["od"] * 1.1                          # (a hexagon's corners)
    open_w = min(80.0, od_c + 20.0)
    z_safe = ring["top"] + TIP + 8.0
    th, clr_ = best_closing(S["HG"], np.asarray(cxy, float), max(ring["od"] / 2 + 8, 30.0), zc - TIP,
                            z_body=zc + 112.0)
    print("ring closing angle", th, "clearance %.1f" % clr_, "zc %.1f" % zc, "sup %.1f" % sup)
    margin = (80.0 - od_c) / 2 - 3.0
    # until contact only while the fingertips pass its top (they might land on it), plain before and after
    z_pre = None if (known or margin > 16) else ring["top"] + TIP + 3.0
    z_g1 = ring["top"] + TIP - 10.0
    if not on_table:                       # its support's height is seen only: until contact to the end
        z_pre, z_g1 = (z_pre if z_pre is not None else ring["top"] + TIP + 3.0), zc
    angles = [th, th + 90, th + 45, th - 45, th + 22, th - 22]
    if known and CAM.get("angle") is not None:
        angles = [CAM["angle"]] + angles          # the one that worked here before
    if first_angle is not None:
        angles = [first_angle] + angles
    def relook():
        """the fingers closed on nothing: where is it now? (None: not there any more)"""
        p = tcp(robot)
        fc = closeup(robot, p[:2], ring["top"], CAM["off"])
        cy = ring_closeup(fc, p[:2], ring["top"], ring["od"]) if fc is not None else None
        print("CYL re-look", None if cy is None else fmt(cy))
        if cy is None or abs(cy["od"] - ring["od"]) > 0.35 * ring["od"] or abs(cy["top"] - ring["top"]) > 12:
            return None
        return np.asarray(cy["xy"], float) - CAM.get("bias", np.zeros(2))
    sq = squeeze_at(robot, "CYL", cxy, z_safe, zc, angles, 3.0, 80.0, open_w, z_pre=z_pre, z_g1=z_g1,
                    single=single, relook=relook)
    if sq is None:
        robot.set_gripper(ARM, 80)
        p = tcp(robot)
        mv(robot, (p[0], p[1], z_safe + 20))
        return None
    C = tcp(robot)
    t = truth(robot)
    if t:
        print("CYL truth err", np.round(np.array(t["w_cyl"][:2]) - C[:2], 2), "db true", round(C[2] - t["w_cyl"][2], 1),
              "db est", round(C[2] - sup, 1))
    return dict(C=C, db=C[2] - sup, w=sq, open_w=open_w, sup=sup)


def insert(robot, Sxy, stop, db, zt, open_w):
    """the held cylinder's axis (the TCP) over the stick's top, down onto it; a small search if it sits on
    the top. Returns True when it is on the stick."""
    C = tcp(robot)
    mv(robot, (C[0], C[1], zt))
    z1 = stop + 6 + db                     # its bottom 6 mm above the stick's top
    c = np.array(cdir(robot)[:2])
    a0 = math.degrees(math.atan2(c[1], c[0]))
    reached = False
    for dz in (0.0, 60.0):                 # near the parked arm some turns of the hand are refused
        for d in (0, 45, -45, 90, 135, -90):
            t = math.radians(a0 + d)
            cd = (math.cos(t), math.sin(t), 0.0)
            r = robot.move(ARM, [float(Sxy[0]), float(Sxy[1]), zt + dz], DOWN, cd, False)
            if not r.get("done"):
                print("insert: approach refused at", d, (r.get("why") or "")[:60])
                continue
            r = robot.move(ARM, [float(Sxy[0]), float(Sxy[1]), z1], DOWN, cd, False)
            if r.get("done"):
                reached = True
                break
            print("insert: descent refused at", d, (r.get("why") or "")[:60])
            mv(robot, (Sxy[0], Sxy[1], zt + dz))
        if reached:
            break
    if not reached:
        print("insert: the stick cannot be reached")
        return False
    cmd = np.array([Sxy[0], Sxy[1], z1], float)
    best = None
    for _ in range(4):                     # the arm ends a little off (and sags under a load): close the loop
        err = np.asarray(Sxy, float) - tcp(robot)[:2]
        if best is None or np.linalg.norm(err) < best[0]:
            best = (float(np.linalg.norm(err)), cmd.copy())
        if np.linalg.norm(err) < 0.8 or np.linalg.norm(err) > 15:
            break
        cmd[:2] += err
        if not mv(robot, cmd).get("done"):
            break
    err = np.asarray(Sxy, float) - tcp(robot)[:2]
    if np.linalg.norm(err) > best[0] + 0.3:
        cmd = best[1]
        mv(robot, cmd)
    corr = cmd[:2] - np.asarray(Sxy, float)
    print("insert: xy correction", np.round(corr, 2), "residual", np.round(np.asarray(Sxy) - tcp(robot)[:2], 2))
    zeng = stop - 14 + db                  # 14 mm onto the stick, then let go (it slides down)
    r = mv(robot, (cmd[0], cmd[1], zeng), stop=True)   # (the top's height is known to a few mm only)
    zn = tcp(robot)[2]
    print("insert", r.get("why"), "z", round(zn, 1), "zeng", round(zeng, 1), "bottom", round(zn - db, 1),
          "stick_top", round(stop, 1))
    if zn - db < stop - 8:
        return True
    zs = zn
    # first the same place again, from higher up (it may have caught askew), then around it
    mv(robot, (cmd[0], cmd[1], zs + 6.0))
    mv(robot, (cmd[0], cmd[1], zs - 7), stop=True)
    if tcp(robot)[2] < zs - 4:
        print("search: found it on a second try at the same place")
        mv(robot, (cmd[0], cmd[1], zeng), stop=True)
        return tcp(robot)[2] - db < stop - 8 or tcp(robot)[2] < zs - 8
    zs = min(zs, tcp(robot)[2])
    for rad in (0.8, 1.6, 2.4, 3.2):
        nk = 6 if rad < 1 else (8 if rad < 2 else 12)
        for k in range(nk):
            if robot.time_s() > T_LIMIT - 60:
                print("search: out of time")
                return False
            a = 2 * math.pi * k / nk + rad
            x, y = Sxy[0] + corr[0] + rad * math.cos(a), Sxy[1] + corr[1] + rad * math.sin(a)
            mv(robot, (x, y, zs + 2.0))
            mv(robot, (x, y, zs - 7), stop=True)
            if tcp(robot)[2] < zs - 4:
                print("search found at offset", round(rad, 1), round(math.degrees(a) % 360))
                mv(robot, (x, y, zeng), stop=True)
                return tcp(robot)[2] - db < stop - 8
    print("search failed")
    return False


def relocate(robot, S, ring, avoid, g2):
    """carry the held cylinder (g2: its grasp) to a free spot on the table, put it down, centre it there and
    let go; returns (ring as it is now, its true place) or None"""
    spot = free_spot(S, ring["od"], avoid)
    print("relocating the cylinder to a free spot:", spot)
    ring = dict(ring)
    zsup = 0.0
    if spot is None:                       # nowhere free on the table: back where it was
        spot, zsup = np.asarray(avoid[1], float), g2["sup"]
    else:
        ring["top"], ring["sup"], ring["tower"] = ring["top"] - g2["sup"], 0.0, False
    zput = zsup + g2["db"] + 1.0
    travel(robot, (spot[0], spot[1], zput + 10.0))
    mv(robot, (spot[0], spot[1], zput), stop=True)
    C = tcp(robot)
    let_go(robot, g2["w"][-1], g2["open_w"])
    mv(robot, (C[0], C[1], ring["top"] + TIP + 8))
    # centre it here: one close only held it, so it may be off the TCP across that close's line (by the
    # camera's error): the fingers along that line first, the way down until contact
    a0, CAM["angle"] = CAM.get("angle"), None
    g3 = grasp_ring(robot, C[:2], ring, S, first_angle=a0)
    if g3 is None:
        return None
    C = g3["C"]
    let_go(robot, g3["w"][-1], g3["open_w"])
    mv(robot, (C[0], C[1], ring["top"] + TIP + 8))
    return ring, C


def run(robot, task):
    try:
        main(robot, task)
    except Exception as e:                  # whatever went wrong: let go and go home
        print("ERROR", repr(e))
        try:
            finish(robot, TRAVEL["z"])
        except Exception as e2:
            print("ERROR in finish", repr(e2))


def main(robot, task):
    T0 = truth(robot)
    if T0:
        print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in T0.items()})
    robot.set_gripper(ARM, 80)
    S = None
    for pose in SURVEYS:
        S = survey(robot, pose)
        if S is not None and S["sticks"] and S["rings"]:
            break
        print("survey at", pose, "found", None if S is None else (len(S["sticks"]), len(S["rings"])))
    if S is None:
        finish(robot)
        return
    f = S["f"]
    camoff, CAM["up"] = cam_offset(f, robot)
    CAM["off"] = camoff
    TRAVEL["z"] = min(600.0, max(200.0, S["zmax"] + 60.0))
    print("plane", np.round(S["coef"], 4), "zmax", round(S["zmax"], 1), "camoff", np.round(camoff, 1),
          "cam up", round(CAM["up"], 1))
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
    for pose in SURVEYS[1:]:                # (things under the fingertips' corners of the image: look again)
        if rings:
            break
        S2 = survey(robot, pose)
        if S2 is not None:
            rings = find_ring(robot, S2, camoff)
    if not rings:
        print("no ring found")
        finish(robot, S["zmax"])
        return
    ring = rings[0]
    stick = find_stick(robot, S, camoff, ring)
    for pose in SURVEYS[1:]:
        if stick is not None:
            break
        print("no stick seen: another survey from", pose)
        S2 = survey(robot, pose)
        if S2 is not None:
            stick = find_stick(robot, S2, camoff, ring)
            S["zmax"] = max(S["zmax"], S2["zmax"])
    if stick is None:
        print("no stick found")
        finish(robot, S["zmax"])
        return
    T = truth(robot)
    if T:
        tk = np.array(T.get("w_sticktop", T["w_stick"]), float)
        print("STICK vision err (no bias)", np.round(stick["xy"] - tk[:2], 2), "top", round(stick["top"], 1))
    # ---- grasp the cylinder: its true place. On something (a box, a book, a narrow tower of blocks): one close
    # only, carried to the table and centred there (centring pushed it off a tower's edge in the simulation;
    # the real soft fingers closed on nothing after turns on boxes and books, R6-R8, never on the table)
    tower = bool(ring.get("tower")) or not ring_support(ring, None)[1]
    g = grasp_ring(robot, ring["xy"], ring, S, single=tower)
    while g is None and len(rings) > 1 and robot.time_s() < 300:
        rings = rings[1:]
        ring = rings[0]
        tower = bool(ring.get("tower")) or not ring_support(ring, None)[1]
        print("cylinder grasp failed: trying the next candidate at", np.round(ring["xy"], 1))
        g = grasp_ring(robot, ring["xy"], ring, S, single=tower)
    if g is None:
        print("cylinder grasp failed")
        finish(robot, max(stick["top"], S["zmax"]))
        return
    C = g["C"]
    r2 = None
    if tower:                               # carry it to the table and centre it there
        rel = relocate(robot, S, ring, [stick["xy"], C[:2]], g)
        if rel is not None:
            ring, C = rel
            fc = closeup(robot, C[:2], ring["top"], camoff)
            r2 = ring_closeup(fc, C[:2], ring["top"], ring["od"])
        else:
            print("relocation failed")
            finish(robot, max(stick["top"], S["zmax"]))
            return
    else:
        # ---- put it down where it is, look at it as at the stick: the camera's error
        let_go(robot, g["w"][-1], g["open_w"])
        mv(robot, (C[0], C[1], ring["top"] + TIP + 8))
        fc = closeup(robot, C[:2], ring["top"], camoff)
        r2 = ring_closeup(fc, C[:2], ring["top"], ring["od"]) if CAM["reached"] else None
    if r2 is None or abs(r2["od"] - ring["od"]) > 0.3 * ring["od"]:
        # the camera cannot look at it from right above (near the parked arm): put it somewhere it can
        print("cannot look at the cylinder there")
        if robot.time_s() < 330:
            g2 = grasp_ring(robot, C[:2], ring, S, known=True, single=True)   # (turns may be refused here)
            rel = relocate(robot, S, ring, [stick["xy"], C[:2]], g2) if g2 is not None else None
            if rel is not None:
                ring, C = rel
                fc = closeup(robot, C[:2], ring["top"], camoff)
                r2 = ring_closeup(fc, C[:2], ring["top"], ring["od"])
    print("RING bias look", None if r2 is None else fmt(r2))
    T2 = truth(robot)
    if T2 and r2 is not None:
        print("RING truth at the bias look: C - true", np.round(C[:2] - np.array(T2["w_cyl"][:2]), 2),
              "seen - true", np.round(r2["xy"] - np.array(T2["w_cyl"][:2]), 2))
    bias = np.zeros(2)
    if r2 is not None and abs(r2["od"] - ring["od"]) < 0.3 * ring["od"]:
        bias = np.asarray(r2["xy"], float) - C[:2]
        if np.linalg.norm(bias) > 20:
            print("bias too large, not used", np.round(bias, 2))
            bias = np.zeros(2)
    Sxy = np.asarray(stick["xy"], float) - bias
    CAM["bias"] = bias
    if r2 is not None and r2["sup"] is not None and abs(r2["sup"]) < 9 and ring_support(ring, None)[1]:
        # the table (at z = 0) seen around it from about as far: the camera's height error
        stick = dict(stick)
        stick["top"] -= float(np.clip(r2["sup"], -6.0, 6.0))
    print("bias", np.round(bias, 2), "stick at", np.round(Sxy, 2), "top", round(stick["top"], 1))
    if T:
        print("STICK err after bias", np.round(Sxy - tk[:2], 2))
    ok = False
    for attempt in range(2):
        g = grasp_ring(robot, C[:2], ring, S, known=True)
        if g is not None:                  # does it hold? lift a little and see
            mv(robot, (g["C"][0], g["C"][1], g["C"][2] + 15))
            if float(st(robot)["opening_mm"]) < 1.5:
                print("cylinder lost when lifted: grasp it again")
                robot.set_gripper(ARM, 80)
                g = grasp_ring(robot, C[:2], ring, S, known=True)
        if g is None:
            print("cylinder re-grasp failed")
            break
        C = g["C"]
        db = g["db"]
        zt = max(stick["top"], S["zmax"], ring["top"]) + db + 35
        if insert(robot, Sxy, stick["top"], db, zt, g["open_w"]):
            ok = True
            robot.set_gripper(ARM, min(80.0, g["open_w"] - 10))
            robot.wait(0.3)
            break
        if robot.time_s() > T_LIMIT - 120:
            break
        # put it back where it was and try once more
        mv(robot, (tcp(robot)[0], tcp(robot)[1], zt))
        mv(robot, (C[0], C[1], zt))
        mv(robot, (C[0], C[1], C[2] + 10.0))
        mv(robot, (C[0], C[1], C[2] + 1.0), stop=True)
        robot.set_gripper(ARM, g["open_w"])
        mv(robot, (C[0], C[1], ring["top"] + TIP + 8))
    finish(robot, max(stick["top"], S["zmax"]))
    j = None
    try:
        j = robot.judge()
    except Exception:
        pass
    print("RESULT", ok, "JUDGE", j)
