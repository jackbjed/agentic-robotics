import math
import numpy as np
import cv2

# Thread the cylinder (vertical hole) onto the stand's vertical stick.
#
# Precision idea:
#  * the cylinder is centred exactly on the TCP by gripping it twice on the table
#    (closing along y, then along x): a parallel gripper centres what it squeezes;
#  * the wrist camera's calibration bias is measured on that centred cylinder (true xy =
#    TCP xy) and cancelled on the stick top, both seen from the same relative pose
#    (camera straight above the feature, same distance, same gripper orientation);
#  * the descent onto the stick is done with stop_on_contact; a miss is found by the height
#    where it stops, and a small spiral search follows.

ARM = "left"
DOWN = [0.0, 0.0, -1.0]
D_OBS = 330.0          # camera-to-feature distance for the precise looks (depth works > 275 mm)
PALM = 114.0           # TCP to the hand's body between the fingers, along approach
TCP_LIM = ((-715.0, 95.0), (-415.0, 595.0), (12.0, 790.0))


def log(*a):
    print("[peg]", *a, flush=True)


def fmt(v):
    if v is None:
        return "None"
    try:
        return "[" + ", ".join("%.1f" % float(x) for x in np.asarray(v, float).reshape(-1)) + "]"
    except Exception:
        return str(v)


def clamp_tcp(p):
    p = np.array(p, float).reshape(3)
    for i in range(3):
        p[i] = min(max(p[i], TCP_LIM[i][0]), TCP_LIM[i][1])
    return p


def rotz(v, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1], 0.0])


# ----------------------------------------------------------------------------- robot wrapper

class Bot:
    def __init__(self, robot):
        self.r = robot
        st = robot.state(ARM)
        cd = np.array(st["closing_dir"], float).reshape(3)
        cd[2] = 0.0
        n = float(np.linalg.norm(cd))
        self.cd0 = cd / n if n > 0.5 else np.array([0.0, -1.0, 0.0])
        self.frame = None
        self.frame_tcp = None

    def state(self):
        return self.r.state(ARM)

    def tcp(self):
        return np.array(self.r.state(ARM)["tcp_mm"], float).reshape(3)

    def t(self):
        try:
            return float(self.r.time_s())
        except Exception:
            return 0.0

    def move(self, p, contact=False, cd=None, tag=""):
        p = clamp_tcp(p)
        cdv = self.cd0 if cd is None else np.asarray(cd, float)
        res = self.r.move(ARM, [float(v) for v in p], list(DOWN), [float(v) for v in cdv], bool(contact))
        if not isinstance(res, dict):
            res = {"done": bool(res), "moved": True, "why": "", "curved": False}
        if (not res.get("done", False)) or res.get("curved", False):
            log("move %s -> %s %s: %s | tcp %s" % (tag, fmt(p), "(contact)" if contact else "",
                                                   res, fmt(self.tcp())))
        return res

    def grip(self, w):
        try:
            return float(self.r.set_gripper(ARM, float(w)))
        except Exception as e:
            log("gripper error", repr(e))
            return float(self.state()["opening_mm"])

    def look(self, cam="left_wrist"):
        try:
            f = self.r.look([cam])[cam]
        except Exception:
            f = self.r.look()[cam]
        if cam == "left_wrist":
            self.frame = f
            self.frame_tcp = self.tcp()
        return f

    def look_all(self):
        try:
            fr = self.r.look()
        except Exception as e:
            log("look error", repr(e))
            return {}
        if "left_wrist" in fr:
            self.frame = fr["left_wrist"]
            self.frame_tcp = self.tcp()
        return fr


def safe_turn(b, ang):
    try:
        rr = b.r.turn(ARM, [float(v) for v in b.tcp()], [0.0, 0.0, 1.0], float(ang))
    except Exception as e:
        log("turn error", repr(e))
        return {"moved": False, "done": False, "why": repr(e)}
    if not isinstance(rr, dict):
        rr = {"moved": bool(rr), "done": bool(rr), "why": ""}
    log("turn %.0f deg -> %s" % (ang, rr))
    return rr


# ----------------------------------------------------------------------------- camera geometry

def cam_R(f):
    return np.asarray(f.R, float).reshape(3, 3)


def cam_eye(f):
    return np.asarray(f.eye, float).reshape(3)


def proj(f, P):
    pc = cam_R(f).T @ (np.asarray(P, float).reshape(3) - cam_eye(f))
    if pc[2] <= 1.0:
        return None
    return (f.fx * pc[0] / pc[2] + f.cx, f.fy * pc[1] / pc[2] + f.cy, float(pc[2]))


def pix_to_z(f, u, v, z):
    d = cam_R(f) @ np.array([(u - f.cx) / f.fx, (v - f.cy) / f.fy, 1.0])
    e = cam_eye(f)
    if abs(d[2]) < 1e-9:
        return None
    t = (z - e[2]) / d[2]
    if t <= 0:
        return None
    return e + t * d


def goto_cam_above(b, P, D, tag="cam-above"):
    """Put the wrist camera on the vertical through P, D mm above it (same orientation)."""
    f = b.frame
    o = cam_eye(f) - b.frame_tcp
    a = cam_R(f)[:, 2]
    if a[2] > -0.9:
        a = np.array([0.0, 0.0, -1.0])
    eye = np.asarray(P, float).reshape(3) - a * D
    return b.move(eye - o, tag=tag)


# ----------------------------------------------------------------------------- image helpers

def hsv_of(f):
    rgb = np.asarray(f.rgb)
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    rgb = np.ascontiguousarray(rgb[:, :, :3])
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    return hsv[..., 0].astype(np.int32), hsv[..., 1].astype(np.int32), hsv[..., 2].astype(np.int32)


def m_yellow(H, S, V):
    return (H >= 10) & (H <= 40) & (S >= 30) & (V >= 70)


def m_blue(H, S, V):
    return (H >= 95) & (H <= 135) & (S >= 90) & (V >= 35)


def u8(m):
    return np.ascontiguousarray(m.astype(np.uint8))


def clean(m, k=3):
    ker = np.ones((k, k), np.uint8)
    return cv2.morphologyEx(u8(m), cv2.MORPH_OPEN, ker) > 0


def close_m(m, k):
    ker = np.ones((k, k), np.uint8)
    return cv2.morphologyEx(u8(m), cv2.MORPH_CLOSE, ker) > 0


def dil(m, r):
    if r <= 0:
        return m.astype(bool)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(u8(m), k) > 0


def ero(m, r):
    if r <= 0:
        return m.astype(bool)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.erode(u8(m), k) > 0


def fill_holes(m):
    res = cv2.findContours(u8(m).copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cnts = res[-2]
    out = np.zeros(m.shape, np.uint8)
    if len(cnts) > 0:
        cv2.drawContours(out, cnts, -1, 1, thickness=-1)
    return out > 0


def comps(m, min_area):
    n, lab, stats, cents = cv2.connectedComponentsWithStats(u8(m), connectivity=8)
    out = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a >= min_area:
            out.append({"i": i, "area": a, "c": (float(cents[i][0]), float(cents[i][1])),
                        "bbox": (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3]))})
    return lab, out


def centroid(m):
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    return (float(xs.mean()), float(ys.mean()))


def elong(m):
    ys, xs = np.nonzero(m)
    if len(xs) < 5:
        return 99.0
    pts = np.stack([xs, ys], axis=1).astype(np.float32)
    rect = cv2.minAreaRect(pts)
    w, h = rect[1]
    return max(w, h) / max(1.0, min(w, h))


def valid_pts(P, m):
    if P is None:
        return np.zeros((0, 3))
    v = P[m]
    if v.size == 0:
        return np.zeros((0, 3))
    return v[np.isfinite(v).all(axis=1)]


def ref_pixel(f, prior):
    if prior is not None:
        pp = proj(f, prior)
        if pp is not None and -200 < pp[0] < f.width + 200 and -200 < pp[1] < f.height + 200:
            return (pp[0], pp[1])
    return (f.cx, f.cy)


def seg_pick(b, f, phrase, ref, min_area=40, max_area=60000):
    if b is None:
        return None
    try:
        res = b.r.segment(f, phrase)
    except Exception as e:
        log("segment error", repr(e))
        return None
    best = None
    for item in res:
        m = np.asarray(item[0], bool)
        s = float(item[1])
        a = int(m.sum())
        if a < min_area or a > max_area:
            continue
        c = centroid(m)
        d = math.hypot(c[0] - ref[0], c[1] - ref[1])
        sc = s - 0.002 * d
        if best is None or sc > best[0]:
            best = (sc, m)
    return None if best is None else best[1]

# ----------------------------------------------------------------------------- detection

def detect_cyl(f, P, prior=None, obs=False, b=None):
    """Yellow cylinder. obs=True: camera roughly straight above its top face."""
    H, S, V = hsv_of(f)
    ym = clean(m_yellow(H, S, V))
    if not obs:
        ym = close_m(ym, 7)     # joins the ruler tape's pieces (cut by its ticks) into one long strip
    lab, cs = comps(ym, 30)
    ref = ref_pixel(f, prior)
    lim = 140.0 if obs else 300.0
    best = None
    for c in cs:
        if (not obs) and c["area"] < 80:
            continue
        d = math.hypot(c["c"][0] - ref[0], c["c"][1] - ref[1])
        if d > lim:
            continue
        m = lab == c["i"]
        if elong(m) > 3.5:
            continue
        sc = d - 0.01 * c["area"]
        if best is None or sc < best[0]:
            best = (sc, m)
    if best is None:
        m = seg_pick(b, f, "yellow cylinder", ref)
        if m is None:
            return None
        c0 = centroid(m)
        if math.hypot(c0[0] - ref[0], c0[1] - ref[1]) > lim:
            return None
        log("cylinder found by segmentation")
        ym = ym | m
    else:
        m = best[1]
    filled = fill_holes(m)
    uv = centroid(filled)
    pts = valid_pts(P, m)
    z_top = None
    xy = None
    if len(pts) >= 8:
        z_top = float(np.percentile(pts[:, 2], 80.0 if obs else 92.0))
        top = pts[pts[:, 2] >= z_top - 12.0]
        if len(top) >= 3:
            xy = np.median(top[:, :2], axis=0)
    if xy is None and uv is not None:
        q = pix_to_z(f, uv[0], uv[1], z_top if z_top is not None else 40.0)
        if q is not None:
            xy = q[:2]
    ring = dil(filled, 45) & ~dil(filled, 14) & ~ym & (S < 60)
    tp = valid_pts(P, ring)
    table_z = float(np.median(tp[:, 2])) if len(tp) >= 30 else None
    A = float(filled.sum())
    Am = float(m.sum())
    return {"uv": uv, "z_top": z_top, "xy": xy, "table_z": table_z, "area": A,
            "outer_px": 2.0 * math.sqrt(A / math.pi),
            "hole_px": 2.0 * math.sqrt(max(A - Am, 0.0) / math.pi)}


def nearest_comp(lab, cs, ref, lim):
    best = None
    for c in cs:
        d = math.hypot(c["c"][0] - ref[0], c["c"][1] - ref[1])
        if d > lim:
            continue
        if best is None or d < best[0]:
            best = (d, c)
    if best is None:
        return None, None
    return lab == best[1]["i"], best[1]


def detect_stick(f, P, prior=None, obs=False):
    """Blue stand and its stick. obs=True: camera roughly straight above the stick top."""
    H, S, V = hsv_of(f)
    bm = clean(m_blue(H, S, V))
    lab, cs = comps(bm, 150)
    ref = ref_pixel(f, prior)
    best = None
    for c in cs:
        d = math.hypot(c["c"][0] - ref[0], c["c"][1] - ref[1])
        if obs and d > 250:
            continue
        sc = c["area"] - 2.0 * d
        if best is None or sc > best[0]:
            best = (sc, c)
    if best is None:
        return None
    base = lab == best[1]["i"]
    sil = fill_holes(base)
    bp = valid_pts(P, ero(base, 2))
    if len(bp) < 20:
        bp = valid_pts(P, base)
    base_z = float(np.median(bp[:, 2])) if len(bp) >= 10 else None
    base_xy = np.median(bp[:, :2], axis=0) if len(bp) >= 10 else None
    ring = dil(sil, 40) & ~dil(sil, 12) & ~bm & (S < 60)
    tp = valid_pts(P, ring)
    table_z = float(np.median(tp[:, 2])) if len(tp) >= 30 else None
    out = {"base_z": base_z, "base_xy": base_xy, "table_z": table_z, "base_uv": centroid(sil),
           "uv": None, "z_top": None, "xy": None, "d_px": None}
    zz = P[..., 2] if P is not None else None
    if obs:
        # depth: what stands well above the base (unambiguous, edges less exact)
        uv_d = None
        z_d = None
        if zz is not None and base_z is not None:
            dm = clean(dil(sil, 10) & np.isfinite(zz) & (zz > base_z + 30.0))
            lab3, cs3 = comps(dm, 10)
            m3, c3 = nearest_comp(lab3, cs3, ref, 80.0)
            if m3 is not None:
                uv_d = centroid(m3)
                p3 = valid_pts(P, m3)
                if len(p3) >= 3:
                    z_d = float(np.percentile(p3[:, 2], 80))
        # colour: the (white/grey) stick top inside the blue base's silhouette
        cand = clean(dil(sil, 3) & ~bm & (S < 80) & (V > 45))
        lab2, cs2 = comps(cand, 12)
        if uv_d is not None:
            m2, c2 = nearest_comp(lab2, cs2, uv_d, 15.0)
        else:
            m2, c2 = nearest_comp(lab2, cs2, ref, 80.0)
        uv_c = None
        z_c = None
        if m2 is not None:
            uv_c = centroid(m2)
            out["d_px"] = 2.0 * math.sqrt(c2["area"] / math.pi)
            p2 = valid_pts(P, m2)
            if base_z is not None:
                p2 = p2[p2[:, 2] > base_z + 15.0]
            if len(p2) >= 5:
                z_c = float(np.percentile(p2[:, 2], 80))
        if uv_c is not None:
            out["uv"] = uv_c
            out["z_top"] = z_c if z_c is not None else z_d
        elif uv_d is not None:
            out["uv"] = uv_d
            out["z_top"] = z_d
        out["uv_color"] = uv_c
        out["uv_depth"] = uv_d
    else:
        if zz is not None and base_z is not None:
            dm = dil(sil, 70) & np.isfinite(zz) & (zz > base_z + 30.0) & (zz < base_z + 400.0)
            pts = valid_pts(P, dm)
            if len(pts) >= 8:
                zt = float(np.percentile(pts[:, 2], 97))
                top = pts[pts[:, 2] >= zt - 10.0]
                out["xy"] = np.median(top[:, :2], axis=0)
                out["z_top"] = zt
        if out["xy"] is None and base_xy is not None:
            out["xy"] = base_xy
            out["z_top"] = (base_z if base_z is not None else 20.0) + 110.0
            out["guess"] = True
    return out


def scene_coarse(f):
    if f is None:
        return None, None
    try:
        H, S, V = hsv_of(f)
        P = np.asarray(f.points(), float)
        ym = close_m(clean(m_yellow(H, S, V) & (S >= 70), 2), 5)
        lab, cs = comps(ym, 20)
        best = None
        for c in cs:
            x, y, w, h = c["bbox"]
            if w > 70 or h > 90:
                continue
            m = lab == c["i"]
            if elong(m) > 4.0:
                continue
            if best is None or c["area"] > best[0]["area"]:
                best = (c, m)
        cyl = None
        if best is not None:
            pts = valid_pts(P, best[1])
            if len(pts) >= 5:
                cyl = np.median(pts, axis=0)
            else:
                cyl = pix_to_z(f, best[0]["c"][0], best[0]["c"][1], 30.0)
        bm = clean(m_blue(H, S, V))
        lab, cs = comps(bm, 50)
        stand = None
        if cs:
            c = max(cs, key=lambda c: c["area"])
            pts = valid_pts(P, lab == c["i"])
            if len(pts) >= 5:
                stand = np.median(pts, axis=0)
            else:
                stand = pix_to_z(f, c["c"][0], c["c"][1], 20.0)
        for nm, v in (("cylinder", cyl), ("stand", stand)):
            if v is not None and not (-650 < v[0] < 650 and -650 < v[1] < 650):
                log("scene: implausible", nm, fmt(v))
        if cyl is not None and not (-650 < cyl[0] < 650 and -650 < cyl[1] < 650):
            cyl = None
        if stand is not None and not (-650 < stand[0] < 650 and -650 < stand[1] < 650):
            stand = None
        log("scene: cylinder ~", fmt(cyl), " stand ~", fmt(stand))
        return cyl, stand
    except Exception as e:
        log("scene detection error", repr(e))
        return None, None


def survey(b, sc_cyl, sc_stand):
    centers = []
    if sc_cyl is not None and sc_stand is not None:
        centers.append(0.5 * (np.asarray(sc_cyl) + np.asarray(sc_stand)))
    elif sc_cyl is not None:
        centers.append(np.asarray(sc_cyl))
    elif sc_stand is not None:
        centers.append(np.asarray(sc_stand))
    centers.append(np.array([-230.0, -50.0, 0.0]))
    cyl = None
    stand = None
    for cc in centers:
        for zc in (560.0, 680.0):
            x = min(max(float(cc[0]), -420.0), -120.0)
            y = min(max(float(cc[1]), -200.0), 160.0)
            goto_cam_above(b, [x, y, 0.0], zc, "survey")
            f = b.look()
            P = np.asarray(f.points(), float)
            if cyl is None:
                d = detect_cyl(f, P, sc_cyl, obs=False, b=b)
                if d is not None and d.get("xy") is not None:
                    if d.get("z_top") is None:
                        d["z_top"] = 50.0
                    cyl = d
                    log("survey: cylinder top ~", fmt(d["xy"]), "z %.1f" % d["z_top"],
                        "table z", d.get("table_z"))
            if stand is None:
                d = detect_stick(f, P, sc_stand, obs=False)
                if d is not None and d.get("xy") is not None:
                    stand = d
                    log("survey: stick top ~", fmt(d["xy"]), "z %.1f" % d["z_top"], "base z", d.get("base_z"),
                        "table z", d.get("table_z"), "(guess)" if d.get("guess") else "")
            if cyl is not None and stand is not None:
                return cyl, stand
    return cyl, stand


def observe(b, kind, P0, iters=4, tol=2.5):
    """Centre the camera over a feature (cylinder top / stick top) and measure it."""
    P = np.array(P0, float).reshape(3)
    best = None
    D = D_OBS
    for it in range(iters):
        goto_cam_above(b, P, D, "obs-" + kind)
        f = b.look()
        Pts = np.asarray(f.points(), float)
        if kind == "cyl":
            det = detect_cyl(f, Pts, P, obs=True, b=b)
        else:
            det = detect_stick(f, Pts, P, obs=True)
        if det is None or det.get("uv") is None:
            log("observe %s it %d: nothing found" % (kind, it))
            if D < D_OBS + 100:
                D = D_OBS + 120
                continue
            break
        u, v = det["uv"]
        z = det.get("z_top")
        if z is None or abs(z - P[2]) > 60:
            z = P[2]
        Pn = pix_to_z(f, u, v, z)
        if Pn is None:
            break
        off = math.hypot(u - f.cx, v - f.cy)
        pr = proj(f, Pn)
        det.update({"P": Pn, "off_px": off, "depth": (pr[2] if pr else D), "fx": float(f.fx),
                    "eye_off": cam_eye(f) - b.frame_tcp, "tcp": b.frame_tcp.copy()})
        log("observe %s it %d: uv (%.1f, %.1f) off %.1f px, P %s, depth %.1f, table z %s" % (
            kind, it, u, v, off, fmt(Pn), det["depth"], det.get("table_z")))
        if kind == "stick":
            log("   stick uv colour %s depth %s, d_px %s" % (det.get("uv_color"), det.get("uv_depth"),
                                                            det.get("d_px")))
        best = det
        P = Pn
        if D != D_OBS:
            D = D_OBS
            continue
        if off <= tol:
            break
    return best

# ----------------------------------------------------------------------------- manipulation

def center_cylinder(b, C, Hc):
    """Grip the standing cylinder low, along y and along x, so its axis ends exactly at the TCP."""
    z1 = min(max(0.4 * Hc, 18.0), max(18.0, Hc - 12.0))
    above = Hc + 35.0
    b.grip(80.0)
    b.move([C[0], C[1], above], tag="over-cyl")
    b.move([C[0], C[1], z1], contact=True, tag="down-centre")
    t = b.tcp()
    if t[2] > z1 + 6.0:
        log("descent onto the cylinder blocked at", fmt(t))
        b.move([t[0], t[1], above + 20.0], tag="up")
        return None, None
    widths = []
    w = b.grip(0.0)
    b.grip(80.0)
    log("centring grip 1: %.1f mm" % w)
    if w < 8.0 or w > 76.0:
        log("nothing between the fingers")
        b.move([t[0], t[1], above + 20.0], tag="up")
        return None, None
    widths.append(w)
    turned = 0.0
    for a in (90.0, -90.0):
        rr = safe_turn(b, a)
        if rr.get("moved", False):
            turned = a
            break
    if turned != 0.0:
        w = b.grip(0.0)
        b.grip(80.0)
        log("centring grip 2: %.1f mm" % w)
        widths.append(w)
        rr = safe_turn(b, -turned)
        if not rr.get("moved", False):
            t = b.tcp()
            b.move([t[0], t[1], Hc + 70.0], cd=rotz(b.cd0, turned), tag="lift-to-unturn")
            b.move([t[0], t[1], Hc + 70.0], tag="unturn")
            b.move([t[0], t[1], z1], contact=True, tag="down-again")
        else:
            t = b.tcp()
            b.move([t[0], t[1], t[2]], tag="re-orient")
    else:
        t = b.tcp()
        cdr = rotz(b.cd0, 90.0)
        b.move([t[0], t[1], Hc + 70.0], tag="lift-rot")
        rr = b.move([t[0], t[1], Hc + 70.0], cd=cdr, tag="rotate")
        if rr.get("done", False):
            b.move([t[0], t[1], z1], contact=True, cd=cdr, tag="down-rot")
            t2 = b.tcp()
            if t2[2] <= z1 + 6.0:
                w = b.grip(0.0)
                b.grip(80.0)
                log("centring grip 2 (rotated by move): %.1f mm" % w)
                widths.append(w)
            b.move([t[0], t[1], Hc + 70.0], cd=cdr, tag="lift-rot2")
        else:
            log("could not rotate the gripper: centred along one axis only")
        b.move([t[0], t[1], Hc + 70.0], tag="rotate-back")
        b.move([t[0], t[1], z1], contact=True, tag="down-again")
    t = b.tcp()
    if t[2] <= z1 + 6.0:
        w = b.grip(0.0)
        b.grip(80.0)
        log("centring grip 3: %.1f mm" % w)
        widths.append(w)
    G = b.tcp()
    st = b.state()
    log("cylinder centred at TCP", fmt(G), "closing", fmt(st["closing_dir"]), "widths", fmt(widths))
    return G, float(np.median(widths))


def spiral(step, rings):
    offs = []
    for k in range(1, rings + 1):
        n = 6 * k
        for j in range(n):
            a = 2.0 * math.pi * (j + 0.5 * (k % 2)) / n
            offs.append((k * step * math.cos(a), k * step * math.sin(a)))
    return offs


def thread(b, T, S_top, B_top, z_g, clear):
    """The gripped cylinder (axis at the TCP, bottom z_g below it) onto the stick at T."""
    z_travel = S_top + 45.0 + z_g
    t = b.tcp()
    if t[2] < z_travel - 1.0:
        b.move([t[0], t[1], z_travel], tag="lift-held")
    b.move([T[0], T[1], z_travel], contact=True, tag="over-stick")
    t = b.tcp()
    if math.hypot(t[0] - T[0], t[1] - T[1]) > 1.0 or abs(t[2] - z_travel) > 3.0:
        b.move([T[0], T[1], z_travel], tag="over-stick-2")
        t = b.tcp()
        if math.hypot(t[0] - T[0], t[1] - T[1]) > 1.0:
            log("cannot get over the stick", fmt(t))
            return False
    floor = max(B_top + z_g - 4.0, S_top - PALM + 22.0)
    floor_bottom = floor - z_g
    log("threading: T %s, stick top %.1f, base top %.1f, z_g %.1f, floor TCP %.1f" % (
        fmt(T), S_top, B_top, z_g, floor))

    def descend(x, y):
        b.move([x, y, floor], contact=True, tag="thread-down")
        return b.tcp()[2] - z_g

    def settle(zb):
        tries = 0
        while zb > floor_bottom + 2.0 and tries < 3 and b.t() < 570:
            tt = b.tcp()
            b.move([tt[0], tt[1], tt[2] + 1.5], tag="unjam")
            zb = descend(tt[0], tt[1])
            tries += 1
        log("cylinder bottom at %.1f after settling (floor %.1f)" % (zb, floor_bottom))
        return True

    zb = descend(T[0], T[1])
    log("first descent: cylinder bottom stopped at %.1f" % zb)
    if zb <= floor_bottom + 2.0 or zb < S_top - 20.0:
        return settle(zb)
    S_hi = zb
    step = min(max(0.8 * clear, 0.6), 2.0)
    offs = spiral(step, max(3, int(math.ceil(5.0 / step))))
    log("miss (stick top met at %.1f); searching with step %.2f mm, %d points" % (zb, step, len(offs)))
    for (dx, dy) in offs:
        if b.t() > 560:
            log("search: out of time")
            break
        zh = S_hi + 6.0 + z_g
        tt = b.tcp()
        b.move([tt[0], tt[1], zh], tag="hop")
        x, y = T[0] + dx, T[1] + dy
        b.move([x, y, zh], contact=True, tag="shift")
        zb = descend(x, y)
        if zb <= floor_bottom + 2.0 or zb < S_hi - 12.0:
            log("entered at offset (%.2f, %.2f): bottom %.1f" % (dx, dy, zb))
            return settle(zb)
        S_hi = max(S_hi, zb)
    return False


def place_back(b, G, z_g, S_top):
    zt = S_top + 45.0 + z_g
    t = b.tcp()
    b.move([t[0], t[1], max(zt, t[2])], tag="lift-held")
    b.move([G[0], G[1], max(zt, t[2])], contact=True, tag="back-over")
    b.move([G[0], G[1], max(12.0, z_g - 3.0)], contact=True, tag="set-down")
    b.grip(80.0)
    t = b.tcp()
    b.move([t[0], t[1], t[2] + 60.0], tag="up")


def verify(b, stick, Hc):
    S_cam = stick["S_cam"]
    tz = stick["table_z"] if stick["table_z"] is not None else 0.0
    prior = np.array([S_cam[0], S_cam[1], tz + stick["B_top"] + Hc])
    goto_cam_above(b, S_cam, D_OBS, "verify")
    f = b.look()
    P = np.asarray(f.points(), float)
    d = detect_cyl(f, P, prior, obs=True, b=b)
    if d is None or d.get("uv") is None:
        log("verify: no cylinder seen around the stick")
        return False
    z = d["z_top"] if d.get("z_top") is not None else prior[2]
    q = pix_to_z(f, d["uv"][0], d["uv"][1], z)
    if q is None:
        return False
    off = float(np.linalg.norm(q[:2] - S_cam[:2]))
    log("verify: cylinder top %s vs stick %s: xy offset %.1f mm, top %.1f above the table" % (
        fmt(q), fmt(S_cam), off, z - tz))
    return off < 10.0


def measure_stick(b, stand_s):
    Sp = np.array([stand_s["xy"][0], stand_s["xy"][1], stand_s["z_top"]], float)
    det = observe(b, "stick", Sp)
    if det is None:
        log("stick top not seen from close; using the survey estimate (less precise)")
        det = {"P": Sp, "table_z": stand_s.get("table_z"), "base_z": stand_s.get("base_z"),
               "eye_off": None, "depth": None, "fx": None, "d_px": None}
    S_cam = np.asarray(det["P"], float)
    tz = det.get("table_z") if det.get("table_z") is not None else stand_s.get("table_z")
    bz = det.get("base_z") if det.get("base_z") is not None else stand_s.get("base_z")
    S_top = S_cam[2] - (tz if tz is not None else 0.0)
    if bz is not None:
        B_top = bz - (tz if tz is not None else 0.0)
    else:
        B_top = 15.0
    B_top = min(max(B_top, 0.0), max(0.0, S_top - 30.0))
    d_mm = None
    if det.get("d_px") and det.get("depth") and det.get("fx"):
        d_mm = det["d_px"] * det["depth"] / det["fx"]
    log("stick: top (camera) %s, height above table %.1f, base top %.1f, diameter ~%s mm" % (
        fmt(S_cam), S_top, B_top, ("%.1f" % d_mm) if d_mm else "?"))
    return {"S_cam": S_cam, "S_top": float(S_top), "B_top": float(B_top), "table_z": tz,
            "eye_off": det.get("eye_off"), "d_mm": d_mm, "det": det}


def attempt_once(b, cyl_prior, stick, cyl_table):
    cdet = observe(b, "cyl", cyl_prior)
    if cdet is None:
        log("cylinder not found near", fmt(cyl_prior))
        return False, None
    C = cdet["P"]
    tz = cdet.get("table_z") if cdet.get("table_z") is not None else cyl_table
    Hc = C[2] - (tz if tz is not None else 0.0)
    if not (15.0 <= Hc <= 160.0):
        log("implausible cylinder height %.1f; assuming 60" % Hc)
        Hc = 60.0
    outer_mm = cdet["outer_px"] * cdet["depth"] / cdet["fx"]
    hole_mm = cdet["hole_px"] * cdet["depth"] / cdet["fx"]
    log("cylinder: top (camera) %s, height %.1f, outer ~%.1f mm, hole ~%.1f mm" % (fmt(C), Hc, outer_mm, hole_mm))

    G, Dw = center_cylinder(b, C, Hc)
    if G is None:
        t = b.tcp()
        b.move([t[0], t[1], max(t[2], Hc + 60.0)], tag="up")
        return False, np.array([C[0], C[1], C[2]])

    # calibration look: the cylinder is now exactly at G
    b.move([G[0], G[1], Hc + 40.0], tag="clear-up")
    cdet2 = observe(b, "cyl", np.array([G[0], G[1], C[2]]))
    delta = np.zeros(2)
    if cdet2 is None:
        log("calibration look failed: no bias correction")
    else:
        if stick.get("eye_off") is not None:
            de = float(np.linalg.norm(cdet2["eye_off"] - stick["eye_off"]))
            if de > 15.0:
                log("WARNING: camera offset differs from the stick look by %.1f mm; flipping" % de)
                for a in (180.0, -180.0):
                    rr = safe_turn(b, a)
                    if rr.get("moved", False):
                        break
                b.look()
                c3 = observe(b, "cyl", np.array([G[0], G[1], C[2]]))
                if c3 is not None:
                    cdet2 = c3
        delta = np.asarray(cdet2["P"][:2], float) - G[:2]
        nd = float(np.linalg.norm(delta))
        log("camera bias at the cylinder: %s (|%.1f| mm), off %.1f px vs stick %.1f px, depth %.1f vs %s" % (
            fmt(delta), nd, cdet2["off_px"], stick["det"].get("off_px", -1),
            cdet2["depth"], stick["det"].get("depth")))
        if nd > 25.0:
            log("bias implausibly large; capping")
            delta = delta * (25.0 / nd)
        hole2 = cdet2["hole_px"] * cdet2["depth"] / cdet2["fx"]
        outer2 = cdet2["outer_px"] * cdet2["depth"] / cdet2["fx"]
        log("cylinder seen: outer %.1f (gripper says %.1f), hole %.1f mm" % (outer2, Dw, hole2))
        hole_mm = hole2

    # final grasp, high on the cylinder
    z_g = min(max(Hc - 18.0, 20.0), 60.0)
    b.move([G[0], G[1], Hc + 30.0], tag="over-grasp")
    b.move([G[0], G[1], z_g], contact=True, tag="down-grasp")
    t = b.tcp()
    if t[2] > z_g + 6.0:
        log("grasp descent blocked at", fmt(t))
        b.move([t[0], t[1], Hc + 60.0], tag="up")
        return False, np.array([G[0], G[1], C[2]])
    w = b.grip(0.0)
    log("grasp: opening %.1f mm at TCP %s" % (w, fmt(t)))
    if w < 8.0 or w > 76.0:
        b.grip(80.0)
        b.move([t[0], t[1], Hc + 60.0], tag="up")
        return False, np.array([G[0], G[1], C[2]])
    z_g = float(t[2])
    G = t

    clear = 1.2
    if stick.get("d_mm") and hole_mm and hole_mm > stick["d_mm"]:
        clear = 0.5 * (hole_mm - stick["d_mm"])
    clear = min(max(clear, 0.6), 4.0)
    T = np.asarray(stick["S_cam"][:2], float) - delta
    log("target TCP xy for threading %s (stick seen at %s), clearance ~%.1f mm" % (
        fmt(T), fmt(stick["S_cam"][:2]), clear))

    S_top = stick["S_top"]
    b.move([G[0], G[1], S_top + 45.0 + z_g], tag="lift-held")
    op = float(b.state()["opening_mm"])
    if op < 8.0:
        log("cylinder lost after lifting (opening %.1f)" % op)
        return False, None
    ok = thread(b, T, S_top, stick["B_top"], z_g, clear)
    if not ok:
        log("threading failed; putting the cylinder back down")
        place_back(b, G, z_g, S_top)
        return False, np.array([G[0], G[1], C[2]])
    b.grip(80.0)
    t = b.tcp()
    b.move([t[0], t[1], max(t[2] + 30.0, S_top + 70.0)], tag="retreat")
    good = verify(b, stick, Hc)
    log("verification:", "cylinder on the stick" if good else "NOT confirmed")
    return True, None


def solve(b):
    b.grip(80.0)
    frames = b.look_all()
    sc_cyl, sc_stand = scene_coarse(frames.get("scene"))
    if b.frame is None:
        b.look()
    cyl_s, stand_s = survey(b, sc_cyl, sc_stand)
    if stand_s is None or cyl_s is None:
        log("could not find both objects (cylinder %s, stand %s)" % (cyl_s is not None, stand_s is not None))
        return
    stick = measure_stick(b, stand_s)
    cyl_prior = np.array([cyl_s["xy"][0], cyl_s["xy"][1], cyl_s["z_top"]], float)
    cyl_table = cyl_s.get("table_z")
    for attempt in range(3):
        if b.t() > 470:
            log("not enough time for another attempt")
            break
        log("=== attempt %d at t=%.1f s" % (attempt + 1, b.t()))
        ok, new_prior = attempt_once(b, cyl_prior, stick, cyl_table)
        if ok:
            return
        if new_prior is None:
            break
        cyl_prior = new_prior


def run(robot, task):
    log("task:", task)
    b = Bot(robot)
    try:
        st = b.state()
        log("start: tcp", fmt(st["tcp_mm"]), "approach", fmt(st["approach_dir"]),
            "closing", fmt(st["closing_dir"]), "opening", st["opening_mm"])
    except Exception as e:
        log("state error", repr(e))
    try:
        solve(b)
    except Exception as e:
        log("ERROR:", repr(e))
    log("done at t=%.1f s" % b.t())
