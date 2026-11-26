import math
import numpy as np
import cv2

ARM = "left"
DOWN = np.array([0.0, 0.0, -1.0])
K3 = np.ones((3, 3), np.uint8)
K5 = np.ones((5, 5), np.uint8)
LOOK_D = 320.0          # camera distance above a target's top for the precise looks
SPIRAL_END_T = 520.0    # stop searching for the hole after this robot time


# ----------------------------------------------------------------------------- utils

def log(*a):
    try:
        print(*a, flush=True)
    except Exception:
        pass


def fmt(v, nd=1):
    try:
        return "[" + ", ".join(("%." + str(nd) + "f") % float(x) for x in v) + "]"
    except Exception:
        return str(v)


def unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def clamp_target(p):
    x, y, z = float(p[0]), float(p[1]), float(p[2])
    x = min(max(x, -715.0), 95.0)
    y = min(max(y, -415.0), 595.0)
    z = min(max(z, 12.0), 790.0)
    if x < -412.0 and y < -272.0:
        x = -412.0
    return [x, y, z]


def ok_move(r):
    return bool(r.get("moved", True)) and (bool(r.get("done", False)) or r.get("why", "") == "contact")


def colors(rgb):
    a = np.asarray(rgb).astype(np.int32)
    return a[..., 0], a[..., 1], a[..., 2]


def blue_mask(rgb):
    R, G, B = colors(rgb)
    return (B > 25) & (B > 1.3 * R + 8) & (B > 1.1 * G + 5)


def yellow_mask(rgb):
    R, G, B = colors(rgb)
    return (R > 90) & (R - B > 35) & (G - B > 12) & (G > 0.45 * R)


def opened(m, k=K3):
    return cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, k) > 0


def dilated(m, n):
    return cv2.dilate(m.astype(np.uint8), np.ones((n, n), np.uint8)) > 0


def fill_mask(m):
    m8 = (np.asarray(m) > 0).astype(np.uint8)
    cs, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros_like(m8)
    if cs:
        cv2.drawContours(out, cs, -1, 1, thickness=-1)
    return out > 0


def hull_of(m):
    m8 = (np.asarray(m) > 0).astype(np.uint8)
    cs, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros_like(m8)
    if not cs:
        return out > 0, None
    pts = np.vstack(cs)
    h = cv2.convexHull(pts)
    cv2.fillConvexPoly(out, h, 1)
    return out > 0, h


def components(mask, min_area=5, max_n=60):
    m8 = (np.asarray(mask) > 0).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m8, connectivity=8)
    idx = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]
    idx.sort(key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    res = []
    for i in idx[:max_n]:
        res.append((lab == i, int(stats[i, cv2.CC_STAT_AREA]), stats[i], cent[i]))
    return res


def elongation(mask):
    ys, xs = np.nonzero(mask)
    if xs.size < 5:
        return 99.0
    c = np.cov(np.vstack([xs.astype(float), ys.astype(float)]))
    ev = np.linalg.eigvalsh(c)
    return float(math.sqrt(max(ev[1], 1e-6) / max(ev[0], 1e-6)))


def centroid(mask):
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


def ray_plane(f, u, v, z):
    d = np.asarray(f.R, dtype=float) @ np.array([(u - f.cx) / f.fx, (v - f.cy) / f.fy, 1.0])
    e = np.asarray(f.eye, dtype=float)
    if abs(d[2]) < 1e-6:
        return None
    t = (z - e[2]) / d[2]
    if t <= 0:
        return None
    return e + t * d


def local_table_z(P, uv, radius, exclude):
    H, W = P.shape[:2]
    u0 = int(round(float(uv[0])))
    v0 = int(round(float(uv[1])))
    x0, x1 = max(0, u0 - radius), min(W, u0 + radius + 1)
    y0, y1 = max(0, v0 - radius), min(H, v0 + radius + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    sub = P[y0:y1, x0:x1, 2]
    okm = np.isfinite(sub)
    if exclude is not None:
        okm &= ~exclude[y0:y1, x0:x1]
    zz = sub[okm]
    if zz.size < 30:
        return None
    m = float(np.median(zz))
    sel = zz[np.abs(zz - m) < 10.0]
    if sel.size < 20:
        return m
    return float(np.median(sel))


def spread_xy(p):
    return max(float(np.percentile(p[:, 0], 90) - np.percentile(p[:, 0], 10)),
               float(np.percentile(p[:, 1], 90) - np.percentile(p[:, 1], 10)))


def in_work(q):
    return -720 < q[0] < 250 and -460 < q[1] < 650 and -50 < q[2] < 220


def sam_masks(robot, f, phrase):
    try:
        res = robot.segment(f, phrase)
    except Exception as ex:
        log("segment error", phrase, repr(ex))
        return []
    out = []
    for item in res:
        try:
            m, s = item[0], item[1]
            out.append((np.asarray(m, dtype=bool), float(s)))
        except Exception:
            pass
    return out


def sam_near(robot, f, phrases, uv, rad, max_area):
    for ph in phrases:
        best = None
        for m, s in sam_masks(robot, f, ph):
            a = int(m.sum())
            if a < 6 or a > max_area:
                continue
            cu, cv_ = centroid(m)
            d = math.hypot(cu - uv[0], cv_ - uv[1])
            if d > rad:
                continue
            if best is None or d < best[0]:
                best = (d, m)
        if best is not None:
            log("SAM(%s) found mask near" % ph, fmt(uv))
            return best[1]
    return None


# ----------------------------------------------------------------------------- robot wrapper

class Ctl:
    def __init__(self, robot):
        self.robot = robot
        self.off_g = None
        self.opt_g = None

    def state(self):
        return self.robot.state(ARM)

    def tcp(self):
        return np.array(self.state()["tcp_mm"], dtype=float)

    def closing(self):
        c = np.array(self.state()["closing_dir"], dtype=float)
        return unit(c)

    def time(self):
        try:
            return float(self.robot.time_s())
        except Exception:
            return 0.0

    def move(self, xyz, c=None, contact=False, quiet=False):
        if c is None:
            c = self.closing()
        c = np.array(c, dtype=float)
        c[2] = 0.0
        c = unit(c)
        tgt = clamp_target(xyz)
        res = self.robot.move(ARM, tgt, [float(v) for v in DOWN], [float(v) for v in c],
                              stop_on_contact=bool(contact))
        if not isinstance(res, dict):
            res = {"done": bool(res), "moved": True, "why": "", "curved": False}
        if quiet and res.get("moved", True) and (res.get("done", False) or res.get("why", "") == "contact"):
            return res
        t = self.tcp()
        log("  move", fmt(tgt), "C" if contact else "F",
            "done=%s moved=%s why=%r curved=%s" % (res.get("done"), res.get("moved"), res.get("why"),
                                                   res.get("curved")),
            "at", fmt(t), "t=%.1f" % self.time())
        return res

    def calib_cam(self, f):
        s = self.state()
        tcp = np.array(s["tcp_mm"], dtype=float)
        a = unit(s["approach_dir"])
        c = unit(s["closing_dir"])
        b = unit(np.cross(a, c))
        off = np.asarray(f.eye, dtype=float) - tcp
        opt = np.asarray(f.R, dtype=float)[:, 2]
        self.off_g = np.array([off @ a, off @ c, off @ b])
        self.opt_g = np.array([opt @ a, opt @ c, opt @ b])

    def cam_geom(self, c):
        a = DOWN
        c = np.array(c, dtype=float)
        c[2] = 0.0
        c = unit(c)
        b = unit(np.cross(a, c))
        off = self.off_g[0] * a + self.off_g[1] * c + self.off_g[2] * b
        opt = unit(self.opt_g[0] * a + self.opt_g[1] * c + self.opt_g[2] * b)
        return off, opt

    def place_camera(self, T, D, c):
        """Put the wrist camera so that target T is on its optical axis at distance D.
        Returns (ok, actual closing dir)."""
        c = unit(np.array(c, dtype=float))
        for _ in range(3):
            off, opt = self.cam_geom(c)
            eye = np.asarray(T, dtype=float) - D * opt
            tcp = eye - off
            cur = self.tcp()
            if cur[2] < 150.0 and np.linalg.norm(cur[:2] - tcp[:2]) > 30.0:
                self.move([cur[0], cur[1], max(150.0, tcp[2])], c=self.closing())
            r = self.move(tcp, c=c)
            if not r.get("moved", True) and not r.get("done", False):
                return False, c
            ca = self.closing()
            if float(ca @ c) > 0.9:
                return bool(r.get("done", False)), ca
            log("  wrist took the other closing sign:", fmt(ca, 3))
            c = ca
        return True, self.closing()


# ----------------------------------------------------------------------------- rough detection

def stick_top_scene(f, base_mask, base_xyz, table_z):
    try:
        R, G, B = colors(f.rgb)
        white = (R > 140) & (G > 140) & (B > 125) & (np.abs(R - G) < 35) & (np.abs(R - B) < 55)
        ys, xs = np.nonzero(base_mask)
        u0, u1 = int(xs.min()) - 6, int(xs.max()) + 6
        v0, v1 = int(ys.min()), int(ys.max())
        win = np.zeros_like(white)
        win[max(0, v0 - 200):v1 + 1, max(0, u0):u1 + 1] = True
        best = None
        for m, a, st, c in components(white & win, 5):
            x, y, w, h = [int(t) for t in st[:4]]
            if w > 25 or h < 6:
                continue
            if y + h < v0 - 3:
                continue
            if best is None or h > best[0]:
                best = (h, y)
        if best is None:
            return None
        vt = best[1]
        zs = np.arange(0.0, 400.0, 1.0)
        pts = np.stack([np.full_like(zs, base_xyz[0]), np.full_like(zs, base_xyz[1]), zs + table_z], 1)
        uv, _ = f.project(pts)
        uv = np.asarray(uv, dtype=float)
        i = int(np.argmin(np.abs(uv[:, 1] - vt)))
        return float(zs[i])
    except Exception as ex:
        log("stick_top_scene error", repr(ex))
        return None


def rough_detect(robot, f, name):
    out = {"cyl": None, "base": None}
    if f is None:
        return out
    try:
        P = f.points()
    except Exception as ex:
        log("points failed", repr(ex))
        return out
    rgb = f.rgb
    blue = opened(blue_mask(rgb))
    yel = opened(yellow_mask(rgb))
    objs = dilated(blue | yel, 9)

    def pts_of(m):
        p = P[m]
        return p[np.isfinite(p[:, 2])]

    # --- stand base
    best = None
    for m, a, st, c in components(blue, 30):
        p = pts_of(m)
        if p.shape[0] < 8:
            continue
        med = np.median(p, axis=0)
        if not in_work(med) or spread_xy(p) > 260:
            continue
        if best is None or a > best[1]:
            best = (m, a, med, np.array(c, dtype=float), p)
    if best is None:
        for ph in ("blue block", "blue stand"):
            for m, s in sam_masks(robot, f, ph):
                p = pts_of(m)
                if p.shape[0] < 8:
                    continue
                med = np.median(p, axis=0)
                if in_work(med) and spread_xy(p) < 260:
                    best = (m, int(m.sum()), med, np.array(centroid(m)), p)
                    break
            if best is not None:
                break
    if best is not None:
        m, a, med, c, p = best
        tz = local_table_z(P, c, 70, objs)
        top = float(np.percentile(p[:, 2], 85) - tz) if tz is not None else None
        stop = None
        if name == "scene" and tz is not None:
            stop = stick_top_scene(f, m, med, tz)
        out["base"] = {"xyz": med, "uv": c, "top": top, "stick_top": stop}

    # --- cylinder
    bestc = None
    for m, a, st, c in components(yel, 8):
        p = pts_of(m)
        if p.shape[0] < 3:
            continue
        med = np.median(p, axis=0)
        if not in_work(med) or spread_xy(p) > 90:
            continue
        el = elongation(fill_mask(m))
        if el > 4.0:
            continue
        score = a / (1.0 + max(0.0, el - 2.5))
        if bestc is None or score > bestc[0]:
            bestc = (score, m, med, np.array(c, dtype=float), p)
    if bestc is None:
        for ph in ("yellow cylinder", "small cylinder"):
            for m, s in sam_masks(robot, f, ph):
                p = pts_of(m)
                if p.shape[0] < 3:
                    continue
                med = np.median(p, axis=0)
                if in_work(med) and spread_xy(p) < 90:
                    bestc = (0, m, med, np.array(centroid(m)), p)
                    break
            if bestc is not None:
                break
    if bestc is not None:
        _, m, med, c, p = bestc
        tz = local_table_z(P, c, 70, objs)
        top = float(np.percentile(p[:, 2], 90) - tz) if tz is not None else None
        out["cyl"] = {"xyz": med, "uv": c, "top": top}
    log("rough(%s):" % name,
        "cyl", None if out["cyl"] is None else (fmt(out["cyl"]["xyz"]), out["cyl"]["top"]),
        "base", None if out["base"] is None else (fmt(out["base"]["xyz"]), out["base"]["top"],
                                                   out["base"]["stick_top"]))
    return out


# ----------------------------------------------------------------------------- precise wrist detectors

def make_det_cyl(ctx, robot):
    def det(f, P, uv, rad, D):
        Z = P[..., 2]
        H, W = Z.shape
        yel = opened(yellow_mask(f.rgb))
        best = None
        for m, a, st, c in components(yel, 25):
            x, y, w, h = [int(t) for t in st[:4]]
            border = x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1
            fm = fill_mask(m)
            fa = int(fm.sum())
            cu, cv_ = centroid(fm)
            d = math.hypot(cu - uv[0], cv_ - uv[1])
            if d > rad:
                continue
            el = elongation(fm)
            holefrac = max(0, fa - a) / float(max(fa, 1))
            score = d + (40.0 if el > 1.8 else 0.0) + (25.0 if holefrac < 0.03 else 0.0) + (60.0 if border else 0.0)
            if best is None or score < best[0]:
                best = (score, m, fm, cu, cv_, fa, a, el, holefrac)
        if best is None:
            mk = sam_near(robot, f, ["yellow cylinder", "cylinder"], uv, rad, 20000)
            if mk is None:
                return None
            m = mk & yel if (mk & yel).sum() > 20 else mk
            fm = fill_mask(mk)
            fa = int(fm.sum())
            cu, cv_ = centroid(fm)
            best = (0, m, fm, cu, cv_, fa, int(m.sum()), elongation(fm), 0.0)
        score, m, fm, cu, cv_, fa, a, el, holefrac = best
        zz = Z[m]
        zz = zz[np.isfinite(zz)]
        ztop = None
        if zz.size >= 5:
            hi = float(np.percentile(zz, 90))
            ztop = float(np.median(zz[zz > hi - 6.0]))
        zuse = ztop if ztop is not None else float(ctx.get("cyl_z", 45.0))
        xyz = ray_plane(f, cu, cv_, zuse)
        if xyz is None:
            return None
        depth = float(np.dot(xyz - np.asarray(f.eye, dtype=float), np.asarray(f.R, dtype=float)[:, 2]))
        ppm = float(f.fx) / max(depth, 50.0)
        return {"u": cu, "v": cv_, "ztop": ztop, "xyz": xyz, "mask": fm, "el": el,
                "dia_mm": 2.0 * math.sqrt(fa / math.pi) / ppm,
                "hole_mm": 2.0 * math.sqrt(max(fa - a, 0) / math.pi) / ppm, "holefrac": holefrac}
    return det


def make_det_stick(ctx, robot):
    def det(f, P, uv, rad, D):
        Z = P[..., 2]
        H, W = Z.shape
        blue = blue_mask(f.rgb)
        bm = cv2.morphologyEx(blue.astype(np.uint8), cv2.MORPH_CLOSE, K3)
        bm = cv2.morphologyEx(bm, cv2.MORPH_OPEN, K3) > 0
        bestb = None
        for m, a, st, c in components(bm, 150):
            hm, h = hull_of(m)
            if h is None:
                continue
            dist = cv2.pointPolygonTest(h, (float(uv[0]), float(uv[1])), True)
            if dist < -rad:
                continue
            key = dist + 0.001 * a
            if bestb is None or key > bestb[0]:
                bestb = (key, m, hm)
        if bestb is None:
            log("  stick det: no blue base near", fmt(uv))
            return None
        _, base, hullm = bestb
        bz = Z[base]
        bz = bz[np.isfinite(bz)]
        base_top_z = float(np.median(bz)) if bz.size > 30 else None
        hull_er = cv2.erode(hullm.astype(np.uint8), K5) > 0
        inner = opened(hull_er & ~blue)
        ppm = float(f.fx) / D
        cands = []
        for m, a, st, c in components(inner, 6):
            fm = fill_mask(m)
            fa = int(fm.sum())
            dia = 2.0 * math.sqrt(fa / math.pi) / ppm
            if dia < 3.0 or dia > 30.0:
                continue
            cu, cv_ = centroid(fm)
            d = math.hypot(cu - uv[0], cv_ - uv[1])
            if d > rad:
                continue
            zz = Z[fm]
            zz = zz[np.isfinite(zz)]
            hgt = None
            if zz.size >= 3 and base_top_z is not None:
                hgt = float(np.percentile(zz, 75)) - base_top_z
            el = elongation(fm)
            score = d + (25.0 if el > 2.0 else 0.0)
            if hgt is not None:
                score += -20.0 if hgt > 15.0 else 30.0
            cands.append((score, fm, cu, cv_, zz, dia, hgt))
        dcand = None
        if base_top_z is not None:
            high = opened(hull_er & np.isfinite(Z) & (Z > base_top_z + 15.0))
            for m, a, st, c in components(high, 4):
                d = math.hypot(c[0] - uv[0], c[1] - uv[1])
                if d > rad:
                    continue
                if dcand is None or d < dcand[0]:
                    dcand = (d, m, float(c[0]), float(c[1]))
        chosen = None
        cands.sort(key=lambda t: t[0])
        if cands:
            top = cands[0]
            if top[6] is not None and top[6] > 15.0:
                chosen = top
            elif dcand is not None:
                near = [t for t in cands if math.hypot(t[2] - dcand[2], t[3] - dcand[3]) < 12.0]
                chosen = near[0] if near else None
            else:
                chosen = top
        cu = cv_ = None
        ztop = None
        dia = None
        if chosen is not None:
            _, fm, cu, cv_, zz, dia, hgt = chosen
            if zz.size >= 3:
                hi = float(np.percentile(zz, 80))
                ztop = float(np.median(zz[zz > hi - 6.0]))
        elif dcand is not None:
            _, m, cu, cv_ = dcand
            zz = Z[m]
            zz = zz[np.isfinite(zz)]
            if zz.size >= 3:
                ztop = float(np.median(zz))
            log("  stick det: using depth blob")
        else:
            mk = sam_near(robot, f, ["white stick", "stick", "pole"], uv, rad, 3000)
            if mk is None:
                log("  stick det: no stick top found in base")
                return None
            cu, cv_ = centroid(mk)
            zz = Z[mk]
            zz = zz[np.isfinite(zz)]
            if zz.size >= 3:
                ztop = float(np.percentile(zz, 80))
        if ztop is not None and base_top_z is not None:
            if not (12.0 < ztop - base_top_z < 320.0):
                ztop = None
        if ztop is not None:
            zuse = ztop
        elif base_top_z is not None:
            zuse = base_top_z + float(ctx.get("stick_h", 80.0))
        else:
            zuse = float(ctx.get("stick_z", 100.0))
        xyz = ray_plane(f, cu, cv_, zuse)
        if xyz is None:
            return None
        return {"u": cu, "v": cv_, "ztop": ztop, "xyz": xyz, "base_top_z": base_top_z,
                "hull": hullm, "dia_mm": dia}
    return det


def servo(ctl, T0, det, c, name, D=LOOK_D, iters=4, first_mm=80.0):
    robot = ctl.robot
    T = np.array(T0, dtype=float)
    best = None
    Dcur = D
    c_act = c
    for it in range(iters + 1):
        okp, c_act = ctl.place_camera(T, Dcur, c_act)
        if not okp:
            okp, c_act = ctl.place_camera(T, Dcur + 60.0, c_act)
            if not okp:
                log("servo", name, ": cannot place camera over", fmt(T))
                return best, c_act
        f = robot.look(["left_wrist"])["left_wrist"]
        P = f.points()
        uv = np.asarray(f.project(np.array([T], dtype=float))[0], dtype=float)[0]
        ppm = float(f.fx) / Dcur
        rad = (first_mm if best is None else 30.0) * ppm
        d = det(f, P, uv, rad, Dcur)
        if d is None or d.get("xyz") is None:
            log("servo", name, it, ": not found near", fmt(uv))
            if best is not None:
                return best, c_act
            Dcur = D + 150.0
            continue
        Tn = np.asarray(d["xyz"], dtype=float)
        sh = float(np.linalg.norm(Tn[:2] - T[:2]))
        dz = float(abs(Tn[2] - T[2]))
        d["frame"] = f
        d["P"] = P
        d["tcp"] = ctl.tcp()
        d["shift"] = sh
        log("servo", name, it, "px", fmt([d["u"], d["v"]]), "exp", fmt(uv), "xyz", fmt(Tn),
            "ztop", d.get("ztop"), "shift %.2f dz %.2f D %.0f" % (sh, dz, Dcur))
        converged = (Dcur == D) and best is not None and sh < 1.0 and dz < 5.0
        best = d
        T = Tn
        if converged:
            break
        Dcur = D
    return best, c_act


# ----------------------------------------------------------------------------- manipulation

def grasp(ctl, cxy, h_c, c0):
    robot = ctl.robot
    gz = 20.0 if h_c >= 32.0 else max(14.0, min(20.0, h_c - 12.0))
    za = max(h_c + 70.0, 110.0)
    robot.set_gripper(ARM, 80.0)
    r = ctl.move([cxy[0], cxy[1], za], c=c0)
    if not ok_move(r):
        return None
    r = ctl.move([cxy[0], cxy[1], gz], c=c0, contact=True)
    z = ctl.tcp()[2]
    if not ok_move(r) or (r.get("why") == "contact" and z > gz + 6.0):
        log("grasp: unexpected stop at z=%.1f" % z)
        ctl.move([cxy[0], cxy[1], za], c=c0)
        return None
    w1 = float(robot.set_gripper(ARM, 0.0))
    log("grip 1 width %.1f" % w1)
    if w1 < 4.0 or w1 > 78.0:
        robot.set_gripper(ARM, 80.0)
        ctl.move([cxy[0], cxy[1], za], c=c0)
        return None
    robot.set_gripper(ARM, 80.0)
    tcp = ctl.tcp()
    c_before = ctl.closing()
    turned = False
    for ang in (90.0, -90.0):
        try:
            r = robot.turn(ARM, [float(v) for v in tcp], [0.0, 0.0, 1.0], ang)
        except Exception as ex:
            log("turn error", repr(ex))
            r = {"moved": False}
        log("turn %.0f ->" % ang, r)
        if r.get("moved", False):
            ca = ctl.closing()
            turned = abs(float(ca @ c_before)) < 0.2
            break
    if not turned:
        c1 = np.array([-c_before[1], c_before[0], 0.0])
        t2 = ctl.tcp()
        ctl.move([t2[0], t2[1], za], c=ctl.closing())
        ctl.move([tcp[0], tcp[1], za], c=c1)
        r = ctl.move([tcp[0], tcp[1], gz], c=c1, contact=True)
        ca = ctl.closing()
        if abs(float(ca @ c_before)) > 0.3:
            log("grasp: could not turn the gripper; single grip only")
    w2 = float(robot.set_gripper(ARM, 0.0))
    tcp2 = ctl.tcp()
    log("grip 2 width %.1f tcp %s closing %s" % (w2, fmt(tcp2), fmt(ctl.closing(), 3)))
    if w2 < 4.0 or w2 > 78.0:
        robot.set_gripper(ARM, 80.0)
        ctl.move([tcp2[0], tcp2[1], za])
        return None
    return {"xy": tcp2[:2].copy(), "g": float(tcp2[2]), "w1": w1, "w2": w2, "za": za}


def spiral_offsets(step, rmax):
    pts = [(0.0, 0.0)]
    k = 1
    while k * step <= rmax + 1e-9:
        r = k * step
        n = max(6, int(math.ceil(2.0 * math.pi * r / step)))
        ph = 0.7 * k
        for i in range(n):
            a = ph + 2.0 * math.pi * i / n
            pts.append((r * math.cos(a), r * math.sin(a)))
        k += 1
    return pts


def insert(ctl, sxy, g, stick_top, base_top):
    robot = ctl.robot
    c = ctl.closing()
    z_tr = max(stick_top + g + 45.0, 130.0)
    tcp = ctl.tcp()
    ctl.move([tcp[0], tcp[1], z_tr], c=c)
    w = float(ctl.state().get("opening_mm", 0.0))
    if w < 3.0:
        log("insert: cylinder lost (opening %.1f)" % w)
        return "dropped", sxy, None
    r = ctl.move([sxy[0], sxy[1], z_tr], c=c)
    if not ok_move(r):
        return "fail", sxy, None
    z_final = max(base_top + g + 2.0, stick_top - 94.0)
    r = ctl.move([sxy[0], sxy[1], z_final], c=c, contact=True)
    z = ctl.tcp()[2]
    if r.get("done", False):
        return "down", sxy, z
    if r.get("why", "") != "contact":
        log("insert: descent refused/stopped:", r)
        return "fail", sxy, None
    bottom = z - g
    log("insert: first contact, cylinder bottom at %.1f (stick top est %.1f, base top %.1f)" % (bottom, stick_top, base_top))
    if bottom < base_top + 6.0:
        return "down", sxy, z
    if bottom < stick_top - 12.0:
        return "jam", sxy, z
    zc = z
    offs = spiral_offsets(0.9, 7.2)
    n = 0
    for (dx, dy) in offs[1:]:
        if ctl.time() > SPIRAL_END_T:
            log("insert: search out of time")
            break
        n += 1
        cur = ctl.tcp()
        ctl.move([cur[0], cur[1], zc + 3.0], c=c, quiet=True)
        x, y = sxy[0] + dx, sxy[1] + dy
        ctl.move([x, y, zc + 3.0], c=c, quiet=True)
        r = ctl.move([x, y, zc - 6.0], c=c, contact=True, quiet=True)
        z = ctl.tcp()[2]
        log("  probe %d off (%.2f, %.2f) stop z %.2f (zc %.2f) why=%r t=%.1f"
            % (n, dx, dy, z, zc, r.get("why"), ctl.time()))
        if r.get("done", False) or z < zc - 3.0:
            log("insert: entered at offset (%.2f, %.2f) after %d probes" % (dx, dy, n))
            r = ctl.move([x, y, z_final], c=c, contact=True)
            z = ctl.tcp()[2]
            if r.get("done", False) or (z - g) < base_top + 6.0:
                return "down", (x, y), z
            return "jam", (x, y), z
        if abs(z - zc) > 2.5:
            log("insert: probe contact at unusual height %.1f (zc %.1f)" % (z, zc))
    cur = ctl.tcp()
    ctl.move([cur[0], cur[1], z_tr], c=c)
    return "fail", sxy, None


def finish(ctl, res, xy, z_at, g, stick_top, base_top, h_c, w2, hole_mm, stick_dia):
    robot = ctl.robot
    c = ctl.closing()
    robot.set_gripper(ARM, 80.0)
    robot.wait(0.5)
    tcp = ctl.tcp()
    if res == "jam":
        bottom = tcp[2] - g
        cyl_top = bottom + h_c
        z_start = cyl_top + 16.0 + 8.0
        ctl.move([tcp[0], tcp[1], z_start], c=c)
        sd = stick_dia if (stick_dia is not None and 3.0 < stick_dia < 30.0) else 10.0
        hm = hole_mm if (hole_mm is not None and sd < hole_mm < w2) else sd + 3.0
        wp = 0.5 * (w2 + hm)
        wp = min(max(wp, sd + 4.0), w2 - 5.0)
        robot.set_gripper(ARM, wp)
        z_goal = base_top + h_c + 16.0 - 1.0
        r = ctl.move([xy[0], xy[1], z_goal], c=c, contact=True)
        z = ctl.tcp()[2]
        log("push: stopped at %.1f (goal %.1f)" % (z, z_goal))
        if not r.get("done", False) and z > base_top + h_c + 16.0 + 5.0:
            ctl.move([xy[0], xy[1], base_top + h_c + 16.0 + 1.0], c=c)
        robot.set_gripper(ARM, 80.0)
        robot.wait(0.3)
    tcp = ctl.tcp()
    z_up = min(max(stick_top + 140.0, tcp[2] + 60.0), 600.0)
    ctl.move([tcp[0], tcp[1], z_up], c=c)


def put_back(ctl, cxy, g):
    robot = ctl.robot
    c = ctl.closing()
    tcp = ctl.tcp()
    zh = max(tcp[2], 160.0)
    ctl.move([tcp[0], tcp[1], zh], c=c)
    ctl.move([cxy[0], cxy[1], zh], c=c)
    ctl.move([cxy[0], cxy[1], g + 1.0], c=c, contact=True)
    robot.set_gripper(ARM, 80.0)
    ctl.move([cxy[0], cxy[1], 160.0], c=c)


def verify(ctl, sxy, stick_top, base_top, h_c):
    robot = ctl.robot
    c = ctl.closing()
    T = np.array([sxy[0], sxy[1], max(stick_top, base_top + h_c)])
    okp, c = ctl.place_camera(T, 330.0, c)
    if not okp:
        return None
    f = robot.look(["left_wrist"])["left_wrist"]
    P = f.points()
    Z = P[..., 2]
    uv = np.asarray(f.project(np.array([T]))[0], dtype=float)[0]
    ppm = float(f.fx) / 330.0
    yel = opened(yellow_mask(f.rgb))
    blue = blue_mask(f.rgb)
    best = None
    for m, a, st, cc in components(yel, 25):
        fm = fill_mask(m)
        if elongation(fm) > 3.0:
            continue
        cu, cv_ = centroid(fm)
        d = math.hypot(cu - uv[0], cv_ - uv[1]) / ppm
        if best is None or d < best[0]:
            best = (d, m, cu, cv_)
    if best is None:
        log("verify: no cylinder seen")
        return None
    d_mm, m, cu, cv_ = best
    zz = Z[m]
    zz = zz[np.isfinite(zz)]
    tz = local_table_z(P, uv, 220, dilated(yel | blue, 15))
    top_rel = float(np.percentile(zz, 90) - tz) if (zz.size > 5 and tz is not None) else None
    log("verify: cylinder %.1f mm from stick axis (image), top %s above table (expected ~%.1f on base)"
        % (d_mm, None if top_rel is None else "%.1f" % top_rel, base_top + h_c))
    if d_mm < 18.0:
        return True
    if d_mm > 30.0 and top_rel is not None and top_rel < h_c + 8.0:
        return False
    return None


# ----------------------------------------------------------------------------- main

def attempt_once(ctl, rough, c0):
    robot = ctl.robot
    cyl0 = rough["cyl"]["xyz"]
    cyl_top0 = rough["cyl"].get("top")
    if cyl_top0 is None or not (12.0 < cyl_top0 < 130.0):
        cyl_top0 = 45.0
    base0 = rough["base"]["xyz"]
    btop0 = rough["base"].get("top")
    if btop0 is None or not (3.0 < btop0 < 80.0):
        btop0 = 20.0
    stop0 = rough["base"].get("stick_top")
    if stop0 is None or not (btop0 + 15.0 < stop0 < 320.0):
        stop0 = btop0 + 80.0
    ctx = {"stick_h": stop0 - btop0, "stick_z": stop0, "cyl_z": cyl_top0}

    stick, c_look = servo(ctl, [base0[0], base0[1], stop0], make_det_stick(ctx, robot), c0, "stick")
    if stick is None:
        log("attempt: stick not found")
        return "retry"
    cyl, c_look2 = servo(ctl, [cyl0[0], cyl0[1], cyl_top0], make_det_cyl(ctx, robot), c_look, "cyl")
    if cyl is None:
        log("attempt: cylinder not found")
        return "retry"
    if float(c_look2 @ c_look) < 0.9:
        log("WARNING: wrist orientation differed between the two looks")

    # heights relative to the table, from the wrist camera
    PS = stick["P"]
    tzS = local_table_z(PS, (stick["u"], stick["v"]), 230, dilated(stick["hull"], 25))
    base_top = btop0
    stick_top = stop0
    if tzS is not None and stick.get("base_top_z") is not None:
        bt = stick["base_top_z"] - tzS
        if 3.0 < bt < 80.0:
            base_top = bt
    if tzS is not None and stick.get("ztop") is not None:
        stt = stick["ztop"] - tzS
        if base_top + 12.0 < stt < 330.0:
            stick_top = stt
    PC = cyl["P"]
    tzC = local_table_z(PC, (cyl["u"], cyl["v"]), 230, dilated(cyl["mask"], 25))
    h_c = cyl_top0
    if tzC is not None and cyl.get("ztop") is not None:
        hh = cyl["ztop"] - tzC
        if 12.0 < hh < 130.0:
            h_c = hh
    log("heights: base top %.1f, stick top %.1f, cylinder %.1f; cyl dia %.1f hole %.1f, stick dia %s"
        % (base_top, stick_top, h_c, cyl["dia_mm"], cyl["hole_mm"], stick.get("dia_mm")))
    s_meas = np.asarray(stick["xyz"], dtype=float)[:2]
    c_meas = np.asarray(cyl["xyz"], dtype=float)[:2]
    log("measured (wrist frame): stick", fmt(s_meas, 2), "cylinder", fmt(c_meas, 2))

    if ctl.time() > 500:
        return "stop"
    g = grasp(ctl, c_meas, h_c, c_look2)
    if g is None:
        log("attempt: grasp failed")
        return "retry"
    e = c_meas - g["xy"]
    log("camera error e =", fmt(e, 2), "|e| = %.2f" % float(np.linalg.norm(e)))
    if float(np.linalg.norm(e)) > 35.0:
        log("WARNING: large e, ignoring it")
        e = np.zeros(2)
    s_true = s_meas - e
    log("stick (true) estimate", fmt(s_true, 2))

    res, xy, z_at = insert(ctl, s_true, g["g"], stick_top, base_top)
    log("insert result:", res, fmt(xy, 2))
    if res == "dropped":
        robot.set_gripper(ARM, 80.0)
        return "retry"
    if res == "fail":
        put_back(ctl, g["xy"], g["g"])
        return "retry"
    finish(ctl, res, xy, z_at, g["g"], stick_top, base_top, h_c, g["w2"], cyl.get("hole_mm"), stick.get("dia_mm"))
    try:
        v = verify(ctl, xy, stick_top, base_top, h_c)
    except Exception as ex:
        log("verify error", repr(ex))
        v = None
    if res == "down":
        return "success"
    if v is False:
        return "retry"
    return "success"


def main(ctl):
    robot = ctl.robot
    s0 = ctl.state()
    log("start:", fmt(s0["tcp_mm"]), fmt(s0["approach_dir"], 3), fmt(s0["closing_dir"], 3), s0.get("opening_mm"))
    c0 = np.array(s0["closing_dir"], dtype=float)
    c0[2] = 0.0
    c0 = unit(c0) if np.linalg.norm(c0) > 0.5 else np.array([0.0, -1.0, 0.0])
    frames = robot.look()
    fs = frames.get("scene")
    fw = frames.get("left_wrist")
    ctl.calib_cam(fw)
    log("wrist cam offset (a,c,b):", fmt(ctl.off_g), "axis:", fmt(ctl.opt_g, 3), "fx %.1f" % float(fw.fx))
    rough = rough_detect(robot, fs, "scene")
    rw = rough_detect(robot, fw, "wrist")
    for k in ("cyl", "base"):
        if rough.get(k) is None and rw.get(k) is not None:
            rough[k] = rw[k]
    if rough.get("cyl") is None or rough.get("base") is None:
        log("rough detection incomplete: overview look")
        ctl.move([-330.0, -40.0, 520.0], c=c0)
        fo = robot.look(["left_wrist"])["left_wrist"]
        ro = rough_detect(robot, fo, "overview")
        for k in ("cyl", "base"):
            if rough.get(k) is None and ro.get(k) is not None:
                rough[k] = ro[k]
    if rough.get("cyl") is None or rough.get("base") is None:
        log("could not find the objects")
        return
    for attempt in range(3):
        if ctl.time() > 430:
            break
        log("=== attempt %d t=%.1f" % (attempt, ctl.time()))
        res = attempt_once(ctl, rough, c0)
        log("attempt result:", res)
        if res in ("success", "stop"):
            return
        try:
            fs = robot.look(["scene"])["scene"]
            r2 = rough_detect(robot, fs, "scene")
            for k in ("cyl", "base"):
                if r2.get(k) is not None:
                    rough[k] = r2[k]
        except Exception as ex:
            log("re-detect error", repr(ex))


def run(robot, task):
    log("task:", task)
    ctl = Ctl(robot)
    try:
        main(ctl)
    except Exception as ex:
        log("EXCEPTION:", repr(ex))
        try:
            s = ctl.state()
            if float(s.get("opening_mm", 0.0)) > 3.0:
                log("releasing after exception")
            robot.set_gripper(ARM, 80.0)
            t = ctl.tcp()
            ctl.move([t[0], t[1], min(t[2] + 60.0, 600.0)])
        except Exception as ex2:
            log("cleanup failed:", repr(ex2))
    log("end t=%.1f" % ctl.time())
