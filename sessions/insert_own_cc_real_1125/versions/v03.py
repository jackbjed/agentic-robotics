import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
C0 = (0.0, -1.0, 0.0)
SURVEY = (-330.0, 60.0, 560.0)


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


# ------------------------------------------------------------------ survey
def survey(robot):
    mv(robot, SURVEY, closing=C0)
    f = robot.look(["left_wrist"])["left_wrist"]
    P = f.points()
    coef = table_plane(P)
    zp = coef[0] * P[..., 0] + coef[1] * P[..., 1] + coef[2]
    h = P[..., 2] - zp
    hf = fill_nan(h)
    rng_mm = float(np.nanmedian(f.depth))
    mmpx = rng_mm / f.fx
    op = cv2.morphologyEx(hf, cv2.MORPH_OPEN, disc(28 / mmpx))
    res = hf - op
    objm = (hf > 5).astype(np.uint8)
    nobj, objlab, objstats, _ = cv2.connectedComponentsWithStats(objm, 8)
    # SAM masks
    sam = {}
    for ph in ("stick", "ring", "cylinder with a hole", "washer"):
        try:
            sam[ph] = [(m.astype(bool), float(s)) for m, s in robot.segment(f, ph)]
        except Exception as e:
            print("segment fail", ph, e)
            sam[ph] = []
    # ---- stick candidates
    sticks = []
    n, lab, stats, _ = cv2.connectedComponentsWithStats((res > 18).astype(np.uint8), 8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 3:
            continue
        comp = lab == i
        hv = np.where(comp, hf, -1e9)
        v, u = np.unravel_index(int(np.argmax(hv)), hv.shape)
        top = float(hf[v, u])
        rise = top - float(op[v, u])
        patch = comp & (hf > top - 6)
        zabs = top + float(zp[v, u]) if np.isfinite(zp[v, u]) else top + coef[2]
        p = ray_point(f, u, v, zabs)
        d_top = float(np.linalg.norm(p - f.eye))
        eqd = 2 * math.sqrt(patch.sum() / math.pi) * d_top / f.fx
        ol = objlab[v, u]
        reg = 2 * math.sqrt(objstats[ol, cv2.CC_STAT_AREA] / math.pi) * rng_mm / f.fx if ol > 0 else 0.0
        samv = 0.0
        for m, s in sam["stick"]:
            if m[max(0, v - 3):v + 4, max(0, u - 3):u + 4].any():
                samv = max(samv, s)
        ringv = 0.0
        for ph in ("ring", "cylinder with a hole", "washer"):
            for m, s in sam[ph][:3]:
                if m[v, u]:
                    ringv = max(ringv, s)
        score = min(1.0, (rise - 12) / 20.0) + (0.8 if eqd < 30 else 0.0) + min(1.0, max(0.0, reg / max(eqd, 5) - 1.5)) \
            + 1.5 * samv - 0.8 * ringv + (0.3 if op[v, u] > 4 else 0.0)
        sticks.append(dict(uv=(int(u), int(v)), xyz=p, top=zabs, rise=rise, eqd=eqd, reg=reg, sam=samv, ringv=ringv,
                           score=score, base=float(op[v, u]) + float(zp[v, u] if np.isfinite(zp[v, u]) else coef[2])))
    sticks.sort(key=lambda s: -s["score"])
    # ---- ring candidates (SAM masks, merged)
    rings = []
    for ph in ("ring", "cylinder with a hole", "washer"):
        for m, s in sam[ph][:4]:
            if m.sum() < 30:
                continue
            for r in rings:
                if mask_iou(r["mask"], m) > 0.5:
                    r["sam"] += s
                    break
            else:
                rings.append(dict(mask=m, sam=s))
    for r in rings:
        m = r["mask"]
        vs, us = np.nonzero(m)
        hm = hf[m]
        top = float(np.percentile(hm, 75))
        ring_out = cv2.dilate(m.astype(np.uint8), disc(15)) & ~cv2.dilate(m.astype(np.uint8), disc(5)).astype(bool)
        sup = float(np.median(hf[ring_out.astype(bool)])) if ring_out.any() else 0.0
        u, v = float(us.mean()), float(vs.mean())
        zabs = top + coef[2] + coef[0] * -300 + coef[1] * 0
        p = ray_point(f, u, v, top + float(np.nanmedian(zp[m])))
        d_top = float(np.linalg.norm(p - f.eye))
        eqd = 2 * math.sqrt(m.sum() / math.pi) * d_top / f.fx
        rise = top - sup
        cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        per = max(cv2.arcLength(max(cnts, key=len), True), 1.0)
        circ = 4 * math.pi * m.sum() / per ** 2
        ok = (8 < rise < 80) and (15 < eqd < 80)
        r.update(uv=(int(u), int(v)), xyz=p, top=top + float(np.nanmedian(zp[m])), rise=rise, eqd=eqd, circ=circ,
                 score=r["sam"] + (1.0 if ok else -1.0) + (0.5 if circ > 0.7 else 0.0))
    rings.sort(key=lambda r: -r["score"])
    zmax = float(np.nanpercentile(P[..., 2], 99.8))
    return dict(f=f, P=P, coef=coef, hf=hf, sticks=sticks, rings=rings, zmax=zmax, mmpx=mmpx)


def cam_offset(f, robot):
    t = tcp(robot)
    return np.asarray(f.eye[:2], float) - t[:2]


# ------------------------------------------------------------------ close-ups
def closeup(robot, xy, top_z, camoff, rise=185.0):
    z = min(780.0, max(top_z + rise, 300.0))
    tx, ty = xy[0] - camoff[0], xy[1] - camoff[1]
    tx = min(90.0, max(-700.0, tx))
    mv(robot, (tx, ty, z), closing=C0)
    f = robot.look(["left_wrist"])["left_wrist"]
    return f


def stick_closeup(robot, f, guess_xy, guess_top):
    P = f.points()
    z = P[..., 2]
    zf = fill_nan(z, default=float(np.nanmin(z)) if np.isfinite(z).any() else 0.0)
    (uv,), _ = f.project(np.array([[guess_xy[0], guess_xy[1], guess_top]]))
    u0, v0 = float(uv[0]), float(uv[1])
    rng_mm = max(150.0, float(f.eye[2] - guess_top))
    pxmm = f.fx / rng_mm
    op = cv2.morphologyEx(zf, cv2.MORPH_OPEN, disc(26 * pxmm))
    res = zf - op
    n, lab, stats, cents = cv2.connectedComponentsWithStats((res > 15).astype(np.uint8), 8)
    best = None
    for i in range(1, n):
        comp = lab == i
        zv = np.where(comp, zf, -1e9)
        v, u = np.unravel_index(int(np.argmax(zv)), zv.shape)
        dpx = math.hypot(u - u0, v - v0)
        if dpx > 45 * pxmm:
            continue
        sc = zv[v, u] - 0.3 * dpx / pxmm
        if best is None or sc > best[0]:
            best = (sc, i, u, v)
    if best is None:
        return None
    _, i, u, v = best
    comp = lab == i
    top = float(np.nanpercentile(np.where(comp, z, np.nan), 97))
    patch = comp & np.isfinite(z) & (z > top - 4)
    if patch.sum() < 3:
        patch = comp & np.isfinite(z) & (z > top - 8)
    xy = P[patch][:, :2].mean(axis=0)
    eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
    # base top: ring around the stick, away from the residual
    yy, xx = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    vs, us = np.nonzero(patch)
    uc, vc = us.mean(), vs.mean()
    rr = np.hypot(xx - uc, yy - vc) / pxmm
    ann = (rr > 14) & (rr < 28) & np.isfinite(z) & (res < 4)
    base = float(np.median(z[ann])) if ann.sum() > 20 else None
    return dict(xy=xy, top=top, eqd=eqd, base=base, uv=(uc, vc))


def ring_closeup(robot, f, guess_xy, guess_top, guess_d):
    P = f.points()
    z = P[..., 2]
    (uv,), _ = f.project(np.array([[guess_xy[0], guess_xy[1], guess_top]]))
    u0, v0 = float(uv[0]), float(uv[1])
    rng_mm = max(150.0, float(f.eye[2] - guess_top))
    pxmm = f.fx / rng_mm
    yy, xx = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    rr = np.hypot(xx - u0, yy - v0) / pxmm
    win = (rr < max(guess_d / 2 + 12, 25)) & np.isfinite(z)
    if win.sum() < 30:
        return None
    ztop = float(np.percentile(z[win], 92))
    patch = (np.abs(np.nan_to_num(z, nan=-1e4) - ztop) < 4) & (rr < guess_d / 2 + 30)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(patch.astype(np.uint8), 8)
    if n < 2:
        return None
    best = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA] - 3 * math.hypot(cents[i][0] - u0, cents[i][1] - v0))
    pm = (lab == best).astype(np.uint8)
    cnts, _ = cv2.findContours(pm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(pm)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    fm = filled.astype(bool)
    hole = fm & ~pm.astype(bool)
    vs, us = np.nonzero(fm)
    uc, vc = us.mean(), vs.mean()
    c3 = ray_point(f, uc, vc, ztop)
    od = 2 * math.sqrt(fm.sum() / math.pi) / pxmm
    hole_d = 2 * math.sqrt(hole.sum() / math.pi) / pxmm
    rc = np.hypot(xx - uc, yy - vc) / pxmm
    ann = (rc > od / 2 + 6) & (rc < od / 2 + 22) & np.isfinite(z) & (z < ztop - 6)
    sup = float(np.median(z[ann])) if ann.sum() > 20 else None
    return dict(xy=c3[:2], top=ztop, od=od, hole_d=hole_d, sup=sup, uv=(uc, vc))


# ------------------------------------------------------------------ squeeze
def squeeze_center(robot, label, min_w):
    w1 = robot.set_gripper(ARM, 0)
    print(label, "squeeze1 w=%.2f" % w1)
    if w1 < min_w:
        robot.set_gripper(ARM, 80)
        a = turn(robot, 90)
        w = robot.set_gripper(ARM, 0)
        print(label, "retry rotated w=%.2f" % w)
        if w < min_w:
            robot.set_gripper(ARM, 80)
            return None
        robot.set_gripper(ARM, 80)
        turn(robot, -a)
        w1 = robot.set_gripper(ARM, 0)
        print(label, "squeeze1b w=%.2f" % w1)
        if w1 < min_w:
            robot.set_gripper(ARM, 80)
            return None
    robot.set_gripper(ARM, min(80, w1 + 25))
    turn(robot, 90)
    w2 = robot.set_gripper(ARM, 0)
    print(label, "squeeze2 w=%.2f" % w2, "tcp", np.round(tcp(robot), 2))
    if w2 < min_w:
        return None
    return w1, w2


# ------------------------------------------------------------------ main
def run(robot, task):
    T0 = truth(robot)
    if T0:
        print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in T0.items()})
    robot.set_gripper(ARM, 80)
    S = survey(robot)
    f = S["f"]
    camoff = cam_offset(f, robot)
    print("plane", np.round(S["coef"], 4), "zmax", round(S["zmax"], 1), "camoff", np.round(camoff, 1))
    for s in S["sticks"][:4]:
        print("STICKC", np.round(s["xyz"], 1), "rise %.1f eqd %.1f reg %.1f sam %.2f ring %.2f score %.2f" % (
            s["rise"], s["eqd"], s["reg"], s["sam"], s["ringv"], s["score"]))
    for r in S["rings"][:4]:
        print("RINGC", np.round(r["xyz"], 1), "rise %.1f eqd %.1f circ %.2f sam %.2f score %.2f" % (
            r["rise"], r["eqd"], r["circ"], r["sam"], r["score"]))
    ann = f.rgb.copy()
    for s in S["sticks"][:3]:
        cv2.circle(ann, s["uv"], 6, (255, 0, 0), 1)
    for r in S["rings"][:3]:
        cv2.circle(ann, r["uv"], 10, (0, 255, 0), 1)
    robot.show("survey", ann)
    if not S["sticks"] or not S["rings"]:
        print("nothing found")
        robot.home(ARM)
        return
    # ---------------- stick
    sc = S["sticks"][0]
    fs = closeup(robot, sc["xyz"][:2], sc["top"], camoff)
    sk = stick_closeup(robot, fs, sc["xyz"][:2], sc["top"])
    print("STICK closeup", None if sk is None else {k: np.round(v, 1) for k, v in sk.items()})
    if sk is None:
        sk = dict(xy=sc["xyz"][:2], top=sc["top"], base=sc["base"], eqd=10)
    if sk["base"] is None:
        sk["base"] = sc["base"]
    base_top, stick_top = sk["base"], sk["top"]
    mv(robot, (sk["xy"][0], sk["xy"][1], stick_top + 50), closing=C0)
    zg = max(base_top + 16 + 9, 14.0)
    zg = min(zg, stick_top - 10)
    r = mv(robot, (sk["xy"][0], sk["xy"][1], zg), stop=True)
    if r.get("why"):
        print("stick descent stopped", r.get("why"), np.round(tcp(robot), 1))
    sq = squeeze_center(robot, "STICK", 2.0)
    Sxy = tcp(robot)[:2].copy()
    ds = sq[1] if sq else None
    t = truth(robot)
    print("STICK at", np.round(Sxy, 2), "ds", ds)
    robot.set_gripper(ARM, 80)
    bias = sk["xy"] - Sxy
    print("closeup bias", np.round(bias, 2))
    if np.linalg.norm(bias) > 15:
        bias = np.zeros(2)
    p = tcp(robot)
    mv(robot, (p[0], p[1], max(stick_top + 45, p[2])))
    # ---------------- cylinder
    rc = S["rings"][0]
    fc = closeup(robot, rc["xyz"][:2], rc["top"], camoff)
    cy = ring_closeup(robot, fc, rc["xyz"][:2], rc["top"], rc["eqd"])
    print("RING closeup", None if cy is None else {k: np.round(v, 1) for k, v in cy.items()})
    if cy is None:
        print("ring closeup failed")
        robot.home(ARM)
        return
    cxy = cy["xy"] - bias
    sup = cy["sup"] if cy["sup"] is not None else cy["top"] - 30
    ctop = cy["top"]
    mv(robot, (cxy[0], cxy[1], ctop + 40), closing=C0)
    zc = max(sup + 19, min(ctop - 10, sup + 40), 13.0)
    r = mv(robot, (cxy[0], cxy[1], zc), stop=True)
    if r.get("why"):
        print("cyl descent stopped", r.get("why"), np.round(tcp(robot), 1))
    sq = squeeze_center(robot, "CYL", 8.0)
    if sq is None:
        print("cylinder grasp failed")
        robot.set_gripper(ARM, 80)
        robot.home(ARM)
        return
    C = tcp(robot)
    db = C[2] - sup
    t = truth(robot)
    if t:
        print("CYL truth err", np.round(np.array(t["w_cyl"][:2]) - C[:2], 2), "db true", round(C[2] - t["w_cyl"][2], 1),
              "db est", round(db, 1))
    # ---------------- insert
    zt = max(stick_top, S["zmax"], ctop) + db + 35
    mv(robot, (C[0], C[1], zt))
    mv(robot, (Sxy[0], Sxy[1], zt))
    z1 = stick_top + 12 + db
    mv(robot, (Sxy[0], Sxy[1], z1))
    zend = max(base_top + 2 + db, base_top + 19, 13.0, stick_top - 95)
    r = mv(robot, (Sxy[0], Sxy[1], zend), stop=True)
    zn = tcp(robot)[2]
    print("insert", r.get("why"), "z", round(zn, 1), "zend", round(zend, 1), "bottom", round(zn - db, 1),
          "stick_top", round(stick_top, 1))
    if r.get("why") and zn - db > stick_top - 10:
        found = False
        for rad in (0.7, 1.4, 2.1, 2.8, 3.5, 4.5):
            for k in range(8):
                a = 2 * math.pi * k / 8 + rad
                x, y = Sxy[0] + rad * math.cos(a), Sxy[1] + rad * math.sin(a)
                mv(robot, (tcp(robot)[0], tcp(robot)[1], zn + 1.5))
                mv(robot, (x, y, zn + 1.5))
                mv(robot, (x, y, zn - 6), stop=True)
                if tcp(robot)[2] < zn - 3:
                    found = True
                    break
            if found:
                break
        print("search", found, np.round(tcp(robot), 2))
        if found:
            p = tcp(robot)
            mv(robot, (p[0], p[1], zend), stop=True)
    robot.set_gripper(ARM, 80)
    p = tcp(robot)
    mv(robot, (p[0], p[1], max(stick_top + 40, p[2] + 10)))
    robot.home(ARM)
    j = None
    try:
        j = robot.judge()
    except Exception:
        pass
    print("JUDGE", j)
