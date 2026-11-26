import math
import numpy as np
import cv2

ARM = "left"
DOWN = [0.0, 0.0, -1.0]
TIP = 16.0          # TCP -> fingertip ends (mm)
BODY = 110.0        # TCP -> gripper body (fingers stick out 130 mm), with margin
OPEN = 80.0
CYL_GRIP_Z = 19.0   # TCP height when gripping the cylinder standing on the table


def log(*a):
    print(*a, flush=True)


def fl(v):
    return [float(x) for x in v]


def r1(v, n=1):
    return np.round(np.asarray(v, dtype=float), n).tolist()


def in_reach(xy):
    return (-560.0 <= float(xy[0]) <= -40.0) and (-300.0 <= float(xy[1]) <= 260.0)


# ---------------------------------------------------------------- image helpers
def hsv_of(fr):
    rgb = np.ascontiguousarray(np.asarray(fr.rgb)[..., :3]).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)


def hsv_parts(hsv):
    return (hsv[..., 0].astype(np.int32), hsv[..., 1].astype(np.int32),
            hsv[..., 2].astype(np.int32))


def blue_mask(hsv):
    h, s, v = hsv_parts(hsv)
    return (h >= 95) & (h <= 135) & (s >= 90) & (v >= 35)


def tan_mask(hsv):
    h, s, v = hsv_parts(hsv)
    return (h >= 6) & (h <= 40) & (s >= 30) & (v >= 60)


def comps(mask, min_area, max_n=12):
    m = np.asarray(mask, dtype=np.uint8)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
    idx = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a >= min_area:
            idx.append((a, i))
    idx.sort(key=lambda t: -t[0])
    out = []
    for a, i in idx[:max_n]:
        out.append({"mask": lab == i, "area": a, "c": np.array(cents[i], dtype=float)})
    return out


def pts_in(P, mask):
    m = np.asarray(mask, dtype=bool)
    q = P[m]
    if q.shape[0] == 0:
        return np.zeros((0, 3))
    ok = np.all(np.isfinite(q), axis=1)
    return q[ok]


def z_mode(z, bw=3.0, win=None):
    z = np.asarray(z, dtype=float)
    z = z[np.isfinite(z)]
    if z.size == 0:
        return None
    if win is None:
        win = 2.0 * bw
    lo = float(np.percentile(z, 2))
    hi = float(np.percentile(z, 98))
    if hi - lo < 2.0 * bw:
        return float(np.median(z))
    edges = np.arange(lo, hi + bw, bw)
    if len(edges) < 3:
        return float(np.median(z))
    hist, edges = np.histogram(z, bins=edges)
    i = int(np.argmax(hist))
    c = 0.5 * (edges[i] + edges[i + 1])
    sel = z[np.abs(z - c) < win]
    return float(np.median(sel)) if sel.size else float(c)


def table_z_near(P, xy, r_in, r_out, exclude=None, bw=3.0):
    Q = P.reshape(-1, 3)
    ok = np.all(np.isfinite(Q), axis=1)
    if exclude is not None:
        ok &= ~np.asarray(exclude, dtype=bool).reshape(-1)
    Q = Q[ok]
    if Q.shape[0] == 0:
        return None
    d = np.hypot(Q[:, 0] - xy[0], Q[:, 1] - xy[1])
    z = Q[(d > r_in) & (d < r_out), 2]
    if z.size < 30:
        return None
    return z_mode(z, bw, 3.0 * bw)


def depth_of(fr, p):
    R = np.asarray(fr.R, dtype=float)
    e = np.asarray(fr.eye, dtype=float)
    return float((R.T @ (np.asarray(p, dtype=float) - e))[2])


def ray_to_z(fr, u, v, z):
    R = np.asarray(fr.R, dtype=float)
    e = np.asarray(fr.eye, dtype=float)
    d = R @ np.array([(u - fr.cx) / fr.fx, (v - fr.cy) / fr.fy, 1.0])
    if abs(d[2]) < 1e-6:
        return None
    t = (z - e[2]) / d[2]
    if t <= 0:
        return None
    return e + t * d


def find_contours(m8):
    res = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return res[-2]


# ---------------------------------------------------------------- stand / stick
def analyze_stand(fr, near_xy=None, coarse=False):
    P = np.asarray(fr.points(), dtype=float)
    hsv = hsv_of(fr)
    blue = blue_mask(hsv)
    tan = tan_mask(hsv)
    bw = 5.0 if coarse else 2.5
    best = None
    for c in comps(blue, 60)[:6]:
        q = pts_in(P, c["mask"])
        if q.shape[0] < 25:
            continue
        bt = z_mode(q[:, 2], bw, 2.0 * bw)
        top = q[np.abs(q[:, 2] - bt) < 2.0 * bw]
        if top.shape[0] < 10:
            top = q
        cxy = np.median(top[:, :2], axis=0)
        rad = float(np.percentile(np.hypot(top[:, 0] - cxy[0], top[:, 1] - cxy[1]), 95))
        score = float(q.shape[0])
        if near_xy is not None:
            dd = float(np.hypot(cxy[0] - near_xy[0], cxy[1] - near_xy[1]))
            if dd > 220.0:
                continue
            score /= (1.0 + dd / 60.0)
        if best is None or score > best[0]:
            best = (score, c, cxy, bt, rad, int(q.shape[0]))
    if best is None:
        return None
    score, c, cxy, bt, rad, n = best
    excl = cv2.dilate((blue | tan).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    tz = table_z_near(P, cxy, rad + 25.0, rad + 200.0, excl, bw)
    out = {"xy": np.asarray(cxy, dtype=float), "top": float(bt), "rad": rad, "tz": tz,
           "n": n, "stick": None, "area": c["area"]}
    # stick: points clearly above the base top, over the base's footprint
    Q = P.reshape(-1, 3)
    ok = np.all(np.isfinite(Q), axis=1) & ~tan.reshape(-1)
    Qs = Q[ok]
    if Qs.shape[0] > 0:
        d = np.hypot(Qs[:, 0] - cxy[0], Qs[:, 1] - cxy[1])
        thr = 22.0 if coarse else 10.0
        el = Qs[(d < rad + 5.0) & (Qs[:, 2] > bt + thr) & (Qs[:, 2] < bt + 320.0)]
        if el.shape[0] >= 4:
            zt = float(np.percentile(el[:, 2], 92))
            sel = el[el[:, 2] > zt - 8.0]
            if sel.shape[0] < 3:
                sel = el
            out["stick"] = {"xy": np.median(sel[:, :2], axis=0), "top": zt,
                            "n": int(el.shape[0]), "src": "depth"}
    if out["stick"] is None and not coarse:
        # fallback: light, unsaturated pixels inside the base outline (seen from above)
        h, s, v = hsv_parts(hsv)
        light = (s <= 70) & (v >= 110)
        m8 = c["mask"].astype(np.uint8)
        cnts = find_contours(m8)
        if cnts:
            hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
            hm = np.zeros_like(m8)
            cv2.fillConvexPoly(hm, hull, 1)
            cc = comps(light & (hm > 0), 6)
            if cc:
                u, v0 = cc[0]["c"]
                p = ray_to_z(fr, float(u), float(v0), bt + 90.0)
                if p is not None:
                    out["stick"] = {"xy": np.asarray(p[:2], dtype=float), "top": None,
                                    "n": 0, "src": "color"}
    return out


# ---------------------------------------------------------------- cylinder
def analyze_cyl(robot, fr, near_xy=None, max_dist=None, avoid_xy=None, avoid_r=0.0,
                use_sam=True, coarse=False):
    P = np.asarray(fr.points(), dtype=float)
    hsv = hsv_of(fr)
    blue = blue_mask(hsv)
    tan = tan_mask(hsv)
    sat = hsv[..., 1].astype(np.float32)
    bw = 5.0 if coarse else 2.5
    big = cv2.dilate((tan | blue).astype(np.uint8), np.ones((21, 21), np.uint8)) > 0

    def evaluate(mask, prior, src):
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != blue.shape:
            return None
        area = int(mask.sum())
        if area < 15:
            return None
        if int((mask & blue).sum()) > 0.25 * area:
            return None
        if float(sat[mask].mean()) < 35.0:
            return None
        q = pts_in(P, mask)
        if q.shape[0] < 8:
            return None
        sx = float(np.percentile(q[:, 0], 90) - np.percentile(q[:, 0], 10))
        sy = float(np.percentile(q[:, 1], 90) - np.percentile(q[:, 1], 10))
        if max(sx, sy) > 90.0:
            return None
        zt = float(np.percentile(q[:, 2], 92))
        top = q[q[:, 2] > zt - max(6.0, 2.0 * bw)]
        if top.shape[0] < 3:
            top = q
        cxy = np.median(top[:, :2], axis=0)
        excl = big | (cv2.dilate(mask.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0)
        tz = table_z_near(P, cxy, 40.0, 200.0, excl, bw)
        if tz is None:
            return None
        h = zt - tz
        if h < (15.0 if coarse else 8.0) or h > 160.0:
            return None
        dist = 0.0
        if near_xy is not None:
            dist = float(np.hypot(cxy[0] - near_xy[0], cxy[1] - near_xy[1]))
            if max_dist is not None and dist > max_dist:
                return None
        if avoid_xy is not None:
            if float(np.hypot(cxy[0] - avoid_xy[0], cxy[1] - avoid_xy[1])) < avoid_r:
                return None
        D_img = None
        d_hole = None
        m8 = mask.astype(np.uint8)
        cnts = find_contours(m8)
        if cnts:
            cnt = max(cnts, key=cv2.contourArea)
            (uc, vc), rpx = cv2.minEnclosingCircle(cnt)
            filled = np.zeros_like(m8)
            cv2.drawContours(filled, [cnt], -1, 1, -1)
            hole_px = int(((filled > 0) & ~mask).sum())
            dep = depth_of(fr, [cxy[0], cxy[1], zt])
            if dep > 1.0:
                mpp = dep / float(fr.fx)
                D_img = 2.0 * float(rpx) * mpp
                d_hole = 2.0 * math.sqrt(hole_px / math.pi) * mpp
        score = float(q.shape[0]) * (1.0 + prior) / (1.0 + dist / 40.0)
        return {"xy": np.asarray(cxy, dtype=float), "top": zt, "tz": tz, "h": h,
                "spread": max(sx, sy), "D_img": D_img, "d_hole": d_hole,
                "score": score, "src": src, "n": int(q.shape[0])}

    cands = []
    for c in comps(tan, 15)[:10]:
        e = evaluate(c["mask"], 0.5, "color")
        if e is not None:
            cands.append(e)
    if not cands and use_sam:
        for phrase in ("cylinder", "tube", "small cylinder with a hole"):
            try:
                segs = robot.segment(fr, phrase)
            except Exception as ex:
                log("segment failed:", repr(ex))
                segs = []
            for item in list(segs or [])[:6]:
                try:
                    m, sc = item[0], item[1]
                except Exception:
                    continue
                e = evaluate(m, float(sc), "sam:" + phrase)
                if e is not None:
                    cands.append(e)
            if cands:
                break
    if not cands:
        return None
    cands.sort(key=lambda d: -d["score"])
    return cands[0]


def fmt_cyl(c):
    if c is None:
        return "None"
    return ("xy=%s top=%.1f tz=%.1f h=%.1f spread=%.1f D_img=%s hole=%s src=%s n=%d" % (
        r1(c["xy"]), c["top"], c["tz"], c["h"], c["spread"],
        None if c["D_img"] is None else round(c["D_img"], 1),
        None if c["d_hole"] is None else round(c["d_hole"], 1), c["src"], c["n"]))


def fmt_stand(s):
    if s is None:
        return "None"
    st = s["stick"]
    sts = "None" if st is None else "xy=%s top=%s n=%d src=%s" % (
        r1(st["xy"]), None if st["top"] is None else round(st["top"], 1), st["n"], st["src"])
    return "base xy=%s top=%.1f rad=%.1f tz=%s n=%d | stick %s" % (
        r1(s["xy"]), s["top"], s["rad"], None if s["tz"] is None else round(s["tz"], 1),
        s["n"], sts)


# ---------------------------------------------------------------- the worker
class Worker:
    def __init__(self, robot):
        self.r = robot
        self.cam_cache = []
        self.safe_z = 260.0
        self.turn_acc = 0.0
        self.t0 = self.time()

    # --- basics
    def time(self):
        try:
            return float(self.r.time_s())
        except Exception:
            return 0.0

    def state(self):
        return self.r.state(ARM)

    def tcp(self):
        return np.array(self.state()["tcp_mm"], dtype=float)

    def closing(self):
        c = np.array(self.state()["closing_dir"], dtype=float)
        c[2] = 0.0
        n = float(np.linalg.norm(c))
        if n < 1e-6:
            return np.array([0.0, -1.0, 0.0])
        return c / n

    def try_call(self, label, fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            log("analysis error (%s): %r" % (label, e))
            return None

    def move(self, p, closing=None, contact=False, label=""):
        if closing is None:
            closing = self.closing()
        p = np.asarray(p, dtype=float).copy()
        p[0] = min(max(p[0], -715.0), 95.0)
        p[1] = min(max(p[1], -415.0), 595.0)
        p[2] = min(max(p[2], 17.0), 795.0)
        res = self.r.move(ARM, fl(p), DOWN, fl(closing), stop_on_contact=bool(contact))
        if not isinstance(res, dict):
            res = {"done": bool(res), "moved": True, "why": "", "curved": False}
        t = self.tcp()
        log("[%6.1fs] move %-22s to %s c=%d: done=%s moved=%s why='%s' curved=%s -> tcp %s" % (
            self.time() - self.t0, label, r1(p), int(bool(contact)), res.get("done"),
            res.get("moved"), res.get("why"), res.get("curved"), r1(t, 2)))
        return res

    def travel(self, p, safe_z=None, closing=None, label=""):
        if safe_z is None:
            safe_z = self.safe_z
        p = np.asarray(p, dtype=float)
        cur = self.tcp()
        hz = max(safe_z, p[2])
        res = {"done": True, "moved": False}
        if cur[2] < hz - 1.0:
            res = self.move([cur[0], cur[1], hz], closing, label=label + "/up")
            if not res.get("done"):
                return res
            cur = self.tcp()
        if math.hypot(cur[0] - p[0], cur[1] - p[1]) > 0.3:
            res = self.move([p[0], p[1], max(cur[2], hz)], closing, label=label + "/across")
            if not res.get("done"):
                return res
            cur = self.tcp()
        if abs(cur[2] - p[2]) > 0.3:
            res = self.move(p, closing, label=label + "/down")
        return res

    def set_grip(self, w):
        v = self.r.set_gripper(ARM, float(w))
        try:
            return float(v)
        except Exception:
            return float(self.state()["opening_mm"])

    # --- wrist camera
    def record_cam(self, fr):
        s = self.state()
        off = np.asarray(fr.eye, dtype=float) - np.asarray(s["tcp_mm"], dtype=float)
        self.cam_cache.append((np.asarray(s["closing_dir"], dtype=float), off))

    def look_wrist(self):
        fr = self.r.look(["left_wrist"])["left_wrist"]
        self.record_cam(fr)
        return fr

    def cam_offset(self):
        c = np.asarray(self.state()["closing_dir"], dtype=float)
        for cc, off in reversed(self.cam_cache):
            if float(np.dot(cc, c)) > 0.995:
                return off
        self.look_wrist()
        return self.cam_cache[-1][1]

    def look_above(self, xy, cam_z, label=""):
        off = self.cam_offset()
        base = np.array([xy[0] - off[0], xy[1] - off[1], cam_z - off[2]])
        for dz in (0.0, -50.0, 50.0, -100.0):
            p = base + np.array([0.0, 0.0, dz])
            res = self.travel(p, label="look " + label)
            if res.get("done"):
                break
        return self.look_wrist()

    # --- turning the gripper 90 degrees about the vertical through the TCP
    def turn90(self, lift_z, down_z):
        t = self.tcp()
        c0 = self.closing()
        first = -1.0 if self.turn_acc > 0 else 1.0
        for sgn in (first, -first):
            ang = 90.0 * sgn
            res = self.r.turn(ARM, fl(t), [0.0, 0.0, 1.0], ang)
            if not isinstance(res, dict):
                res = {"done": bool(res), "moved": True}
            c1 = self.closing()
            log("turn %+.0f: done=%s moved=%s why='%s' closing %s (dot %.3f) tcp %s" % (
                ang, res.get("done"), res.get("moved"), res.get("why"), r1(c1, 3),
                float(np.dot(c0, c1)), r1(self.tcp(), 2)))
            if res.get("done"):
                self.turn_acc += ang
                return True
            if res.get("moved"):
                break
        # fallback: lift, reorient with a joint move, come back down
        target = np.array([-c0[1], c0[0], 0.0])
        t2 = self.tcp()
        self.move([t2[0], t2[1], lift_z], label="lift to reorient")
        res = self.move([t[0], t[1], lift_z], closing=target, label="reorient")
        if not res.get("done"):
            return False
        self.turn_acc = 0.0
        self.move([t[0], t[1], down_z], contact=True, label="down after reorient")
        return abs(self.tcp()[2] - down_z) < 3.0

    # --- centre the stick between the fingers along two perpendicular lines
    def center_stick(self, sxy, base_top, stick_top):
        grip_z = max(base_top + 22.0, stick_top - 98.0, 24.0)
        above_z = max(stick_top + TIP + 22.0, grip_z + 10.0)
        self.set_grip(OPEN)
        res = self.travel([sxy[0], sxy[1], above_z], label="above stick")
        if not res.get("done"):
            log("cannot reach above the stick")
            return None
        self.move([sxy[0], sxy[1], grip_z], contact=True, label="down around stick")
        t = self.tcp()
        if t[2] > grip_z + 2.5:
            tipz = t[2] - TIP
            if tipz > base_top + 12.0:
                log("blocked at fingertip z %.1f (base top %.1f)" % (tipz, base_top))
                self.move([t[0], t[1], above_z], label="retreat")
                return None
            log("fingertips touched the base at %.1f; lifting 3 mm" % tipz)
            self.move([t[0], t[1], t[2] + 3.0], label="off the base")
            t = self.tcp()
        ws = []
        for k in range(3):
            w = self.set_grip(0.0)
            tk = self.tcp()
            ws.append(w)
            log("stick close %d: opening %.2f closing %s tcp %s" % (
                k, w, r1(self.closing(), 3), r1(tk, 2)))
            self.set_grip(OPEN)
            if k >= 1 and 1.5 <= ws[-1] <= 40.0 and 1.5 <= ws[-2] <= 40.0:
                if abs(ws[-1] - ws[-2]) > 2.0:
                    log("WARNING: the two closes on the stick differ (%.2f vs %.2f): "
                        "the stand may not have slid into the centre" % (ws[-2], ws[-1]))
                return {"xy": tk[:2].copy(), "d": 0.5 * (ws[-1] + ws[-2]),
                        "grip_z": float(tk[2]), "above_z": above_z}
            if k == 2:
                break
            if not self.turn90(above_z, t[2]):
                log("turn failed at the stick")
                break
        t = self.tcp()
        self.move([t[0], t[1], above_z], label="retreat")
        return None

    def touch_stick_top(self, sxy, stick_top, base_top):
        z_start = stick_top + TIP + 25.0
        closed = False
        for k in range(3):
            self.move([sxy[0], sxy[1], z_start], label="above stick top")
            w = self.set_grip(0.0)
            if w < 2.0:
                closed = True
                break
            log("something between the fingers (%.1f mm) above the stick; going higher" % w)
            self.set_grip(OPEN)
            z_start += 40.0
        if not closed:
            return None
        z_end = max(base_top + TIP + 12.0, stick_top + TIP - 25.0)
        res = self.move([sxy[0], sxy[1], z_end], contact=True, label="touch stick top")
        t = self.tcp()
        top = None
        if (not res.get("done")) and t[2] > z_end + 1.0:
            top = float(t[2] - TIP)
        self.move([t[0], t[1], t[2] + 12.0], label="off stick top")
        self.set_grip(OPEN)
        return top

    # --- grasp the cylinder, centring it on the TCP along two perpendicular lines
    def grasp_cyl(self, cxy, H):
        self.set_grip(OPEN)
        above = max(H + TIP + 20.0, 70.0)
        res = self.travel([cxy[0], cxy[1], above], label="above cyl")
        if not res.get("done"):
            log("cannot reach above the cylinder")
            return None
        gz = CYL_GRIP_Z
        self.move([cxy[0], cxy[1], gz], contact=True, label="down to cyl")
        t = self.tcp()
        if t[2] > gz + 4.0:
            log("blocked while descending to the cylinder at %.1f" % t[2])
            self.move([t[0], t[1], above], label="retreat")
            return None
        ws = []
        for k in range(3):
            w = self.set_grip(0.0)
            ws.append(w)
            log("cyl close %d: opening %.2f closing %s tcp %s" % (
                k, w, r1(self.closing(), 3), r1(self.tcp(), 2)))
            if (k >= 1 and 6.0 <= ws[-1] <= 77.0 and 6.0 <= ws[-2] <= 77.0
                    and abs(ws[-1] - ws[-2]) < 6.0):
                tt = self.tcp()
                return {"tcp": tt, "D": w, "g": float(tt[2])}
            self.set_grip(OPEN)
            if k == 2:
                break
            if not self.turn90(above, t[2]):
                log("turn failed at the cylinder")
                break
        t = self.tcp()
        self.move([t[0], t[1], above], label="retreat")
        return None

    # --- put the held cylinder on the stick
    def search(self, S, zc, deadline):
        offs = []
        for rad, n in ((0.6, 6), (1.2, 8), (1.8, 12), (2.4, 14), (3.0, 18), (3.6, 20)):
            for k in range(n):
                a = 2.0 * math.pi * k / n + rad
                offs.append((rad * math.cos(a), rad * math.sin(a)))
        for dx, dy in offs:
            if self.time() > deadline:
                log("search: out of time")
                break
            p = np.array([S[0] + dx, S[1] + dy])
            self.move([p[0], p[1], zc + 3.0], label="srch %+.1f,%+.1f" % (dx, dy))
            self.move([p[0], p[1], zc - 9.0], contact=True, label="srch down")
            if self.tcp()[2] < zc - 5.0:
                log("search: entered at offset", r1([dx, dy], 2))
                return p
        return None

    def insert(self, S, g, stick_top, stick_top_hi, base_top, deadline):
        zt = stick_top_hi + g + 25.0
        t = self.tcp()
        res = self.move([t[0], t[1], zt], label="lift cyl")
        if not res.get("done"):
            log("lift failed")
            return "fail"
        w = float(self.state()["opening_mm"])
        log("holding, opening %.1f" % w)
        if w < 3.0:
            return "lost"
        res = self.move([S[0], S[1], zt], label="over stick")
        if not res.get("done"):
            log("cannot move over the stick")
            return "fail"
        w = float(self.state()["opening_mm"])
        if w < 3.0:
            return "lost"
        z_end = max(base_top + g - 3.0, stick_top_hi - BODY + 8.0, 17.0)
        res = self.move([S[0], S[1], z_end], contact=True, label="insert")
        if res.get("moved") is False:
            log("insert move refused")
            return "miss"
        t = self.tcp()
        bottom = t[2] - g
        log("insert stop: tcp z %.1f, cylinder bottom %.1f (stick top %.1f, base top %.1f)" % (
            t[2], bottom, stick_top, base_top))
        if t[2] > z_end + 1.5 and bottom > stick_top - 8.0:
            log("the cylinder sits on the stick's tip: searching")
            p = self.search(S, t[2], deadline)
            if p is None:
                tt = self.tcp()
                self.move([tt[0], tt[1], zt], label="up after failed search")
                return "miss"
            self.move([p[0], p[1], z_end], contact=True, label="insert 2")
            t = self.tcp()
            bottom = t[2] - g
            log("insert 2 stop: cylinder bottom %.1f" % bottom)
            if bottom > stick_top - 5.0:
                self.move([t[0], t[1], zt], label="up after failed insert")
                return "miss"
        self.set_grip(OPEN)
        log("released with cylinder bottom at %.1f" % bottom)
        t = self.tcp()
        self.move([t[0], t[1], max(stick_top_hi + TIP + 40.0, t[2] + 10.0)], label="retreat up")
        return "ok"

    def put_back(self, xy, g, hold_safe):
        self.travel([xy[0], xy[1], hold_safe], safe_z=hold_safe, label="carry back")
        self.move([xy[0], xy[1], g + 1.0], contact=True, label="set down")
        self.set_grip(OPEN)
        t = self.tcp()
        self.move([t[0], t[1], max(self.safe_z, t[2] + 30.0)], label="up after set down")

    def verify(self, S, stick_top_hi, avoid_r=20.0):
        fr = self.look_above(S, max(470.0, stick_top_hi + 340.0), "verify")
        cy = self.try_call("cyl/verify", analyze_cyl, self.r, fr, near_xy=S, use_sam=False)
        if cy is None:
            cy = self.try_call("cyl/verify sam", analyze_cyl, self.r, fr, near_xy=S, use_sam=True)
        st = self.try_call("stand/verify", analyze_stand, fr, near_xy=S)
        log("verify: cylinder", fmt_cyl(cy))
        log("verify: stand", fmt_stand(st))
        if cy is None:
            return None, None
        d = float(np.hypot(cy["xy"][0] - S[0], cy["xy"][1] - S[1]))
        log("verify: cylinder centre %.1f mm from the stick" % d)
        return d < avoid_r, cy

    def find_cylinder(self, avoid_xy):
        fr = self.r.look(["scene"])["scene"]
        c0 = self.try_call("cyl/scene again", analyze_cyl, self.r, fr, avoid_xy=avoid_xy,
                           avoid_r=40.0, use_sam=True, coarse=True)
        log("scene cylinder:", fmt_cyl(c0))
        if c0 is None or not in_reach(c0["xy"]):
            return None
        fr = self.look_above(c0["xy"], 470.0, "cyl")
        cy = self.try_call("cyl/wrist again", analyze_cyl, self.r, fr, near_xy=c0["xy"],
                           max_dist=120.0, avoid_xy=avoid_xy, avoid_r=35.0)
        log("wrist cylinder:", fmt_cyl(cy))
        return cy if cy is not None else c0

    # --- the whole task
    def main(self, task):
        r = self.r
        log("task:", task)
        s0 = self.state()
        log("start tcp", r1(s0["tcp_mm"]), "approach", r1(s0["approach_dir"], 3),
            "closing", r1(s0["closing_dir"], 3), "opening", s0["opening_mm"])
        if float(s0["opening_mm"]) < 75.0:
            self.set_grip(OPEN)
        frames = r.look()
        scene = frames["scene"] if "scene" in frames else None
        wr0 = frames["left_wrist"] if "left_wrist" in frames else None
        if wr0 is not None:
            self.record_cam(wr0)
            log("wrist cam offset from tcp:", r1(self.cam_cache[-1][1]))
        for nm, fr in (("scene", scene), ("wrist", wr0)):
            if fr is not None:
                log("%s: fx=%.1f fy=%.1f cx=%.1f cy=%.1f eye=%s" % (
                    nm, fr.fx, fr.fy, fr.cx, fr.cy, r1(fr.eye)))

        # ---- coarse positions
        stand0 = None
        cyl0 = None
        sources = [("scene", scene, True), ("wrist0", wr0, False)]
        for nm, fr, coarse in sources:
            if fr is None:
                continue
            if stand0 is None:
                s = self.try_call("stand/" + nm, analyze_stand, fr, coarse=coarse)
                log("coarse stand (%s): %s" % (nm, fmt_stand(s)))
                if s is not None and in_reach(s["xy"]):
                    stand0 = s
            if cyl0 is None:
                av = stand0["xy"] if stand0 is not None else None
                c = self.try_call("cyl/" + nm, analyze_cyl, r, fr, avoid_xy=av, avoid_r=45.0,
                                  use_sam=(nm == "scene"), coarse=coarse)
                log("coarse cylinder (%s): %s" % (nm, fmt_cyl(c)))
                if c is not None and in_reach(c["xy"]):
                    cyl0 = c
        if stand0 is None or cyl0 is None:
            fr = self.look_above([-290.0, -40.0], 650.0, "survey")
            if stand0 is None:
                s = self.try_call("stand/survey", analyze_stand, fr)
                log("survey stand:", fmt_stand(s))
                if s is not None and in_reach(s["xy"]):
                    stand0 = s
            if cyl0 is None:
                av = stand0["xy"] if stand0 is not None else None
                c = self.try_call("cyl/survey", analyze_cyl, r, fr, avoid_xy=av, avoid_r=45.0)
                log("survey cylinder:", fmt_cyl(c))
                if c is not None and in_reach(c["xy"]):
                    cyl0 = c
        stand_xy = np.array(stand0["xy"], dtype=float) if stand0 is not None else np.array([-265.0, 80.0])
        cyl_xy = np.array(cyl0["xy"], dtype=float) if cyl0 is not None else np.array([-275.0, -155.0])
        stick_guess = 140.0
        if (stand0 is not None and stand0["stick"] is not None and stand0["stick"]["top"] is not None
                and stand0["tz"] is not None):
            sg = stand0["stick"]["top"] - stand0["tz"]
            if 30.0 < sg < 260.0:
                stick_guess = sg
        H = 40.0
        if cyl0 is not None and 10.0 < cyl0["h"] < 150.0:
            H = cyl0["h"]
        log("coarse: stand", r1(stand_xy), "stick top guess %.1f" % stick_guess,
            "cylinder", r1(cyl_xy), "H %.1f" % H)

        # ---- the stand from above with the wrist camera
        cam_z = max(480.0, stick_guess + 340.0)
        st = None
        tgt = stand_xy.copy()
        for k in range(3):
            fr = self.look_above(tgt, cam_z, "stand")
            s = self.try_call("stand/wrist", analyze_stand, fr, near_xy=tgt)
            if s is None:
                s = self.try_call("stand/wrist any", analyze_stand, fr)
            log("wrist stand %d: %s" % (k, fmt_stand(s)))
            if s is None:
                continue
            st = s
            if s["stick"] is not None and s["stick"]["src"] == "depth":
                break
            tgt = s["xy"]
        base_top = 20.0
        stick_top = stick_guess
        S0 = stand_xy.copy()
        if st is not None:
            tz = st["tz"] if st["tz"] is not None else 0.0
            base_top = st["top"] - tz
            if st["stick"] is not None:
                S0 = np.array(st["stick"]["xy"], dtype=float)
                if st["stick"]["top"] is not None:
                    stick_top = st["stick"]["top"] - tz
            else:
                S0 = np.array(st["xy"], dtype=float)
        elif stand0 is not None and stand0["stick"] is not None:
            S0 = np.array(stand0["stick"]["xy"], dtype=float)
        base_top = float(min(max(base_top, 3.0), 80.0))
        stick_top = float(min(max(stick_top, base_top + 20.0), 320.0))
        self.safe_z = max(self.safe_z, stick_top + TIP + 50.0)
        log("stand: stick xy %s, stick top %.1f, base top %.1f" % (r1(S0), stick_top, base_top))

        # ---- the cylinder from above with the wrist camera
        fr = self.look_above(cyl_xy, 470.0, "cyl")
        cy = self.try_call("cyl/wrist", analyze_cyl, r, fr, near_xy=cyl_xy, max_dist=120.0,
                           avoid_xy=S0, avoid_r=35.0)
        if cy is None:
            cy = self.try_call("cyl/wrist any", analyze_cyl, r, fr, avoid_xy=S0, avoid_r=35.0)
        log("wrist cylinder:", fmt_cyl(cy))
        if cy is not None:
            cyl_xy = np.array(cy["xy"], dtype=float)
            if 10.0 < cy["h"] < 150.0:
                H = cy["h"]
        log("cylinder: xy %s H %.1f" % (r1(cyl_xy), H))

        # ---- find the stick exactly: centre it between the fingers
        S = S0.copy()
        cen = None
        for k in range(2):
            cen = self.center_stick(S, base_top, stick_top)
            if cen is not None:
                break
            fr = self.look_above(S, cam_z, "stand again")
            s = self.try_call("stand/wrist again", analyze_stand, fr, near_xy=S)
            log("wrist stand again:", fmt_stand(s))
            if s is not None:
                tz = s["tz"] if s["tz"] is not None else 0.0
                if s["stick"] is not None:
                    S = np.array(s["stick"]["xy"], dtype=float)
                    if s["stick"]["top"] is not None:
                        stick_top = float(min(max(s["stick"]["top"] - tz, base_top + 20.0), 320.0))
                else:
                    S = np.array(s["xy"], dtype=float)
        if cen is not None:
            S = np.array(cen["xy"], dtype=float)
            log("stick centred at %s (camera said %s), stick diameter %.2f" % (
                r1(S, 2), r1(S0), cen["d"]))
        else:
            log("WARNING: stick centring failed; using the camera's estimate", r1(S))

        top_c = self.touch_stick_top(S, stick_top, base_top)
        log("stick top by touch:", top_c, "camera: %.1f" % stick_top)
        stick_top_hi = stick_top + 5.0
        if top_c is not None and base_top + 15.0 < top_c < 330.0:
            if abs(top_c - stick_top) < 15.0:
                stick_top_hi = max(top_c, stick_top) + 2.0
            else:
                stick_top_hi = top_c + 3.0
            stick_top = top_c
        self.safe_z = max(120.0, stick_top_hi + TIP + 40.0)
        log("stick top %.1f (hi %.1f), safe z %.1f" % (stick_top, stick_top_hi, self.safe_z))

        # ---- grasp, carry, thread, verify (with retries)
        deadline = self.t0 + 480.0
        for attempt in range(3):
            if self.time() > self.t0 + 500.0:
                log("out of time")
                break
            grab = None
            for k in range(2):
                grab = self.grasp_cyl(cyl_xy, H)
                if grab is not None:
                    break
                fr = self.look_above(cyl_xy, 470.0, "cyl again")
                c = self.try_call("cyl/again", analyze_cyl, r, fr, near_xy=cyl_xy,
                                  max_dist=150.0, avoid_xy=S, avoid_r=35.0)
                log("wrist cylinder again:", fmt_cyl(c))
                if c is not None:
                    cyl_xy = np.array(c["xy"], dtype=float)
                    if 10.0 < c["h"] < 150.0:
                        H = c["h"]
            if grab is None:
                log("could not grasp the cylinder")
                break
            g = grab["g"]
            log("grasped: tcp %s, width %.2f, g %.1f" % (r1(grab["tcp"], 2), grab["D"], g))
            hold_safe = stick_top_hi + g + 25.0
            res = self.insert(S, g, stick_top, stick_top_hi, base_top, deadline)
            log("insert result:", res)
            if res == "ok":
                on, c = self.verify(S, stick_top_hi)
                if on is None or on:
                    log("done: the cylinder is on the stick" if on else "done (not verified)")
                    break
                if c is not None and in_reach(c["xy"]):
                    cyl_xy = np.array(c["xy"], dtype=float)
                    if 10.0 < c["h"] < 150.0 and abs(c["h"] - H) < 12.0:
                        log("cylinder ended up elsewhere, upright: retrying")
                        cen = self.center_stick(S, base_top, stick_top)
                        if cen is not None:
                            S = np.array(cen["xy"], dtype=float)
                        continue
                    log("cylinder ended up elsewhere and not upright (h %.1f): giving up" % c["h"])
                break
            if res == "miss":
                self.put_back(cyl_xy, g, hold_safe)
                cen = self.center_stick(S, base_top, stick_top)
                if cen is not None:
                    S = np.array(cen["xy"], dtype=float)
                continue
            if res == "lost":
                self.set_grip(OPEN)
                t = self.tcp()
                self.move([t[0], t[1], max(self.safe_z, t[2])], label="up after loss")
                c = self.find_cylinder(S)
                if c is None:
                    log("cylinder not found after loss")
                    break
                cyl_xy = np.array(c["xy"], dtype=float)
                if 10.0 < c["h"] < 150.0:
                    H = c["h"]
                continue
            # "fail": give up safely, letting go of nothing on the stand
            break
        st_end = self.state()
        log("end: tcp", r1(st_end["tcp_mm"]), "opening", st_end["opening_mm"],
            "time %.1f" % (self.time() - self.t0))


def run(robot, task):
    w = Worker(robot)
    try:
        w.main(task)
    except Exception as e:
        log("EXCEPTION:", repr(e))
        raise
