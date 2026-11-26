import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
CL0 = np.array([0.0, -1.0, 0.0])
CAM_D = 320.0         # close-up distance camera -> target surface (mm)
GRID = 3.0             # height-map cell (mm)
X0, X1, Y0, Y1 = -700.0, 200.0, -480.0, 650.0


def log(*a):
    print(*a)


# ------------------------------------------------------------------ debug (truth; None when evaluated)
def dbg_truth(robot):
    try:
        t = robot.truth()
    except Exception:
        return None
    return t if t else None


def dbg(robot, label, **meas):
    t = dbg_truth(robot)
    if t is None:
        return
    out = []
    for k, v in meas.items():
        if k == "tip" and "w_tip" in t:
            out.append("tip err %s" % np.round(np.array(v[:len(v)]) - np.array(t["w_tip"])[:len(v)], 2).tolist())
        if k == "cyl" and "w_cyl" in t:
            out.append("cyl err xy %s (true %s)" % (np.round(np.array(v[:2]) - np.array(t["w_cyl"][:2]), 2).tolist(),
                                                   np.round(t["w_cyl"], 1).tolist()))
    log("DBG", label, "; ".join(out))


# ------------------------------------------------------------------ robot helpers
def unit(v):
    v = np.array(v, float)
    return v / np.linalg.norm(v)


def yaw_cl(deg):
    """closing direction CL0 turned by deg about +z."""
    a = math.radians(deg)
    return np.array([math.cos(a) * CL0[0] - math.sin(a) * CL0[1], math.sin(a) * CL0[0] + math.cos(a) * CL0[1], 0.0])


class R:
    def __init__(self, robot):
        self.robot = robot
        self.off_g = np.array([3.3, -50.4, -133.3])   # wrist camera eye - TCP in the gripper frame (cl, approach x cl, approach)
        self.cl = CL0.copy()                          # the measuring yaw
        self.hmap = None                              # survey height map (for clear paths)
        self.hang = 0.0                               # how far a held object hangs below the TCP

    def st(self):
        return self.robot.state(ARM)

    def tcp(self):
        return np.array(self.st()["tcp_mm"], float)

    def cl_now(self):
        return np.array(self.st()["closing_dir"], float)

    def corridor_max(self, a, b, rad=60.0):
        """highest surface (survey height map) within rad of the segment a-b (xy)."""
        if self.hmap is None:
            return -1e9
        n = int(math.hypot(b[0] - a[0], b[1] - a[1]) / 10.0) + 2
        r = int(rad / GRID)
        best = -1e9
        for t in np.linspace(0.0, 1.0, n):
            i = int((a[0] + t * (b[0] - a[0]) - X0) / GRID)
            j = int((a[1] + t * (b[1] - a[1]) - Y0) / GRID)
            sub = self.hmap[max(0, i - r):max(0, i + r + 1), max(0, j - r):max(0, j + r + 1)]
            if sub.size and np.isfinite(sub).any():
                best = max(best, float(np.nanmax(sub)))
        return best

    def move(self, p, cl=None, contact=False, tag=""):
        cl = self.cl if cl is None else cl
        if not contact and self.hmap is not None:
            cur = self.tcp()
            if math.hypot(p[0] - cur[0], p[1] - cur[1]) > 40:
                need = self.corridor_max(cur, p) + max(16.0, self.hang) + 30.0
                if min(cur[2], p[2]) < need:
                    z_up = min(max(need, cur[2]), 700.0)
                    if z_up > cur[2] + 1:
                        self.robot.move(ARM, [float(cur[0]), float(cur[1]), z_up], DOWN, [float(v) for v in self.cl_now()], False)
                    self.robot.move(ARM, [float(p[0]), float(p[1]), max(z_up, float(p[2]))], DOWN, [float(v) for v in cl], False)
        r = self.robot.move(ARM, [float(v) for v in p], DOWN, [float(v) for v in cl], contact)
        if not r.get("done"):
            log("move", tag, np.round(p, 1).tolist(), "->", r.get("why", "")[:120], "moved", r.get("moved"))
        return r

    def grip(self, w):
        return self.robot.set_gripper(ARM, float(w))

    def look(self, cams=("left_wrist",)):
        fr = self.robot.look(list(cams))
        if "left_wrist" in fr:
            f = fr["left_wrist"]
            s = self.st()
            if s["approach_dir"][2] < -0.995:
                ex, ez = unit(s["closing_dir"]), unit(s["approach_dir"])
                ey = np.cross(ez, ex)
                d = np.array(f.eye) - np.array(s["tcp_mm"])
                self.off_g = np.array([d @ ex, d @ ey, d @ ez])
        return fr

    def cam_offset(self, cl):
        ex = unit(cl)
        ez = np.array(DOWN)
        ey = np.cross(ez, ex)
        return self.off_g[0] * ex + self.off_g[1] * ey + self.off_g[2] * ez

    def cam_over(self, p, dist=CAM_D, tag="cam", cl=None):
        """move so the wrist camera is `dist` above point p, pointing down, closing along cl (default: the measuring yaw)."""
        cl = unit(self.cl if cl is None else cl)
        r = {}
        for _ in range(2):
            tgt = np.array([p[0], p[1], p[2] + dist]) - self.cam_offset(cl)
            tgt[2] = min(tgt[2], 780.0)
            r = self.move(tgt, cl=cl, tag=tag)
            if not r.get("done") and not r.get("moved"):
                # refused there (reach, keep-out box): the same view with the gripper turned
                for deg in (90.0, -90.0, 45.0, -45.0, 135.0, -135.0):
                    a = math.radians(deg)
                    cl2 = np.array([math.cos(a) * cl[0] - math.sin(a) * cl[1], math.sin(a) * cl[0] + math.cos(a) * cl[1], 0.0])
                    tgt = np.array([p[0], p[1], p[2] + dist]) - self.cam_offset(cl2)
                    tgt[2] = min(tgt[2], 780.0)
                    r = self.move(tgt, cl=cl2, tag=tag + "-alt")
                    if r.get("done"):
                        cl = cl2
                        break
            if not r.get("done"):
                return r
            act = self.cl_now()
            if act @ cl > 0.98:
                return r
            log("wrist took the other closing sign", np.round(act, 2).tolist())
            cl = act
        return r


# ------------------------------------------------------------------ height map from wrist frames
def add_to_map(hmap, f, min_dist=320.0):
    P = f.points().reshape(-1, 3)
    D = f.depth.copy()
    D[350:, :230] = np.nan          # the fingertips (and flying pixels at their edges)
    D[350:, 410:] = np.nan
    D = D.reshape(-1)
    ok = np.isfinite(P[:, 2]) & np.isfinite(D) & (D > min_dist)
    P = P[ok].copy()
    # the camera's small tilt error makes the flat table look tilted: fit the table plane, take the tilt out
    z = P[:, 2]
    hist, edges = np.histogram(z, bins=np.arange(np.min(z) - 2, np.max(z) + 4, 3.0))
    k = int(np.argmax(hist))
    sel = np.abs(z - 0.5 * (edges[k] + edges[k + 1])) < 15
    for _ in range(3):
        if sel.sum() < 3000:
            break
        Q = P[sel][::7]
        A = np.c_[Q[:, 0], Q[:, 1], np.ones(len(Q))]
        coef = np.linalg.lstsq(A, Q[:, 2], rcond=None)[0]
        res = z - (coef[0] * P[:, 0] + coef[1] * P[:, 1] + coef[2])
        sel = np.abs(res) < 4
    else:
        if abs(coef[0]) < 0.07 and abs(coef[1]) < 0.07:
            xc, yc = P[sel, 0].mean(), P[sel, 1].mean()
            P[:, 2] = z - (coef[0] * (P[:, 0] - xc) + coef[1] * (P[:, 1] - yc))
    ix = ((P[:, 0] - X0) / GRID).astype(int)
    iy = ((P[:, 1] - Y0) / GRID).astype(int)
    ok = (ix >= 0) & (ix < hmap.shape[0]) & (iy >= 0) & (iy < hmap.shape[1])
    np.fmax.at(hmap, (ix[ok], iy[ok]), P[ok, 2])


def cell_xy(i, j):
    return X0 + (i + 0.5) * GRID, Y0 + (j + 0.5) * GRID


def table_height(hmap):
    v = hmap[np.isfinite(hmap)]
    hist, edges = np.histogram(v, bins=np.arange(-80, 120, 2.0))
    k = int(np.argmax(hist))
    sel = v[(v > edges[k] - 4) & (v < edges[k + 1] + 4)]
    return float(np.median(sel))


def level_blobs(hmap, tz):
    """component tree of the height map: small isolated blobs at each level (3 mm steps)."""
    h = np.nan_to_num(hmap, nan=-1e3)
    k3 = np.ones((3, 3), np.uint8)
    found = []
    for L in np.arange(tz + 5.0, tz + 360.0, 3.0):
        m = (h > L).astype(np.uint8)
        if m.sum() < 3:
            break
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k3)
        n, lab, stats, cent = cv2.connectedComponentsWithStats(m, connectivity=8)
        for k in range(1, n):
            a = stats[k, cv2.CC_STAT_AREA]
            if a < 4 or a * GRID * GRID > 9000:
                continue
            x0, y0, w, hh = stats[k, 0], stats[k, 1], stats[k, 2], stats[k, 3]
            sub = (lab[y0:y0 + hh, x0:x0 + w] == k).astype(np.uint8)
            cs, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            fill = np.zeros_like(sub)
            cv2.drawContours(fill, cs, -1, 1, -1)
            af = float(fill.sum()) * GRID * GRID
            pts = np.argwhere(fill > 0)            # (row=i, col=j) in the sub window
            ci, cj = pts[:, 0].mean() + y0, pts[:, 1].mean() + x0
            r_enc = math.sqrt(np.max((pts[:, 0] + y0 - ci) ** 2 + (pts[:, 1] + x0 - cj) ** 2)) * GRID + GRID / 2
            zz = h[y0:y0 + hh, x0:x0 + w][sub > 0]
            x, y = cell_xy(ci, cj)
            found.append(dict(L=float(L), x=x, y=y, d=2 * math.sqrt(af / math.pi), dmax=2 * r_enc,
                              round=af / (math.pi * r_enc ** 2), top=float(np.percentile(zz, 95))))
    # cluster across levels
    clusters = []
    for b in found:
        for c in clusters:
            if math.hypot(b["x"] - c["x"], b["y"] - c["y"]) < 12 and abs(b["d"] - c["d"]) < 0.35 * c["d"] + 6:
                c["items"].append(b)
                break
        else:
            clusters.append(dict(x=b["x"], y=b["y"], d=b["d"], items=[b]))
    out = []
    for c in clusters:
        it = c["items"]
        Ls = [b["L"] for b in it]
        out.append(dict(x=float(np.median([b["x"] for b in it])), y=float(np.median([b["y"] for b in it])),
                        d=float(np.median([b["d"] for b in it])), dmax=float(np.median([b["dmax"] for b in it])),
                        round=float(np.median([b["round"] for b in it])), top=float(max(b["top"] for b in it)),
                        Lmin=min(Ls), Lmax=max(Ls), n=len(it)))
    return out


def find_sticks(blobs, tz):
    out = []
    for b in blobs:
        if b["x"] < -400 and b["y"] < -260:
            continue
        if 9 <= b["d"] <= 42 and tz + 140 < b["top"] < tz + 380 and (b["Lmax"] - b["Lmin"]) >= 50:
            out.append(dict(x=b["x"], y=b["y"], z=b["top"], d=b["d"], span=b["Lmax"] - b["Lmin"]))
    out.sort(key=lambda s: abs(s["span"] - 165) + 2 * abs(s["d"] - 24))
    return out


def find_cyl_candidates(blobs, tz):
    out = []
    for b in blobs:
        if b["x"] < -400 and b["y"] < -260:
            continue
        hgt = b["top"] - b["Lmin"] + 5
        if 16 <= b["d"] <= 82 and b["dmax"] <= 95 and b["round"] >= 0.55 and b["n"] >= 2 and 7 <= hgt <= 75:
            c = dict(b)
            c["h"] = hgt
            out.append(c)
    out.sort(key=lambda c: -(c["round"] + 0.02 * min(c["n"], 10)))
    return out


# ------------------------------------------------------------------ close-up measurements
def backproject(f, u, v, depth):
    ray = np.array([(u - f.cx) / f.fx, (v - f.cy) / f.fy, 1.0])
    return np.array(f.eye) + np.array(f.R) @ (ray * depth)


def measure_top(f, p_guess, kind):
    """kind 'stick': the tip disk; 'cyl': the top face. Returns dict or None."""
    P = f.points()
    Z = P[:, :, 2]
    D = f.depth
    (uv,), _ = f.project(np.array([p_guess], float))
    u0, v0 = float(uv[0]), float(uv[1])
    H, W = Z.shape
    rad_px = 90 if kind == "cyl" else 60
    vv, uu = np.mgrid[0:H, 0:W]
    win = (uu - u0) ** 2 + (vv - v0) ** 2 < rad_px ** 2
    zw = Z[win & np.isfinite(Z)]
    if zw.size < 30:
        return None
    if kind == "stick":
        zt = float(np.percentile(zw, 99.5))
        top_mask = win & np.isfinite(Z) & (Z > zt - 6)
    else:
        # the top face: the highest substantial level in the window
        zs = np.sort(zw)[::-1]
        zt = None
        for q in (2, 5, 10, 20, 30):
            cand = float(np.percentile(zw, 100 - q))
            near = np.sum(np.abs(zw - cand) < 3)
            if near > 150:
                zt = float(np.median(zw[np.abs(zw - cand) < 3]))
                break
        if zt is None:
            return None
        top_mask = win & np.isfinite(Z) & (np.abs(Z - zt) < 4)
    m = top_mask.astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n < 2:
        return None
    # component nearest the guess with enough area
    best, bd = None, 1e9
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < 20:
            continue
        d = math.hypot(cent[k][0] - u0, cent[k][1] - v0)
        if d < bd:
            best, bd = k, d
    if best is None:
        return None
    blob = (lab == best).astype(np.uint8)
    cs, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea)
    filled = np.zeros_like(blob)
    cv2.drawContours(filled, [c], -1, 1, -1)
    mo = cv2.moments(filled, binaryImage=True)
    if mo["m00"] < 1:
        return None
    uc, vc = mo["m10"] / mo["m00"], mo["m01"] / mo["m00"]
    dcam = float(np.nanmedian(D[blob > 0]))
    p = backproject(f, uc, vc, dcam)
    px_mm = dcam / f.fx
    d_eq = 2 * math.sqrt(mo["m00"] / math.pi) * px_mm
    (_, _), rr = cv2.minEnclosingCircle(c)
    res = dict(x=float(p[0]), y=float(p[1]), z=float(np.median(Z[blob > 0])), u=uc, v=vc, d=d_eq,
               dmax=2 * rr * px_mm, dcam=dcam, filled=filled, blob=blob, px_mm=px_mm)
    if kind == "cyl":
        # hole: pixels inside the filled outline that are not top face
        r_in_px = 3.5 / px_mm
        cen = ((uu - uc) ** 2 + (vv - vc) ** 2) < r_in_px ** 2
        frac_top = float(np.sum(cen & (blob > 0))) / max(1, np.sum(cen))
        holeish = (filled > 0) & (blob == 0)
        hm = holeish.astype(np.uint8)
        n2, lab2, st2, ce2 = cv2.connectedComponentsWithStats(hm, connectivity=8)
        hole = None
        for k in range(1, n2):
            if st2[k, cv2.CC_STAT_AREA] < 15:
                continue
            if math.hypot(ce2[k][0] - uc, ce2[k][1] - vc) < 8 / px_mm:
                hole = dict(u=ce2[k][0], v=ce2[k][1], d=2 * math.sqrt(st2[k, cv2.CC_STAT_AREA] / math.pi) * px_mm)
        res["frac_top_centre"] = frac_top
        res["hole"] = hole
        if hole is not None:
            ph = backproject(f, hole["u"], hole["v"], dcam)
            res["hole_xy"] = (float(ph[0]), float(ph[1]))
        # local support level just outside the outline
        ring = cv2.dilate(filled, np.ones((15, 15), np.uint8)) - cv2.dilate(filled, np.ones((5, 5), np.uint8))
        zr = Z[(ring > 0) & np.isfinite(Z)]
        res["support"] = float(np.percentile(zr, 50)) if zr.size > 10 else None
    return res


def draw(f, res_list):
    img = f.rgb.copy()
    for r in res_list:
        if r is None:
            continue
        cs, _ = cv2.findContours(r["filled"], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cs, -1, (255, 0, 255), 1)
        cv2.circle(img, (int(r["u"]), int(r["v"])), 3, (0, 255, 0), -1)
    return img


# ------------------------------------------------------------------ old main (unused)
def run_v1(robot, task):
    rb = R(robot)
    rb.grip(80)
    t0 = robot.time_s()
    # 1. survey with the wrist camera from high up
    hmap = np.full((int((X1 - X0) / GRID), int((Y1 - Y0) / GRID)), np.nan, np.float32)
    shots = []
    for (x, y) in [(-420.0, -10.0), (-420.0, 300.0)]:
        r = rb.move((x, y, 620.0), tag="survey")
        if not r.get("moved") and not r.get("done"):
            r = rb.move((x, y, 520.0), tag="survey-low")
        f = rb.look()["left_wrist"]
        add_to_map(hmap, f)
        shots.append(f)
    tz = table_height(hmap)
    log("table z %.1f" % tz, "cam_off", np.round(rb.cam_off, 1).tolist())
    sticks = find_sticks(hmap, tz)
    good, allc = find_cyl_candidates(hmap, tz)
    log("sticks", [{k: round(v, 1) for k, v in s.items()} for s in sticks])
    log("cyl cands", [{k: (round(v, 1) if not isinstance(v, (bool, np.bool_)) else bool(v)) for k, v in c.items()} for c in allc])
    if sticks:
        dbg(robot, "survey stick", tip=[sticks[0]["x"], sticks[0]["y"], sticks[0]["z"]])
    for c in good:
        dbg(robot, "survey cand", cyl=[c["x"], c["y"]])
    vis = np.nan_to_num(hmap - tz, nan=0)
    vis = np.clip(vis * 2, 0, 255).astype(np.uint8)
    robot.show("hmap", cv2.applyColorMap(vis, cv2.COLORMAP_JET)[:, :, ::-1])
    if not sticks:
        log("no stick found")
        robot.home(ARM)
        return
    stick = sticks[0]
    # 2. close-up of each candidate
    cyl = None
    for c in sorted(good, key=lambda c: -c["round"]):
        rb.cam_over((c["x"], c["y"], c["top"]), tag="cand")
        f = rb.look()["left_wrist"]
        m = measure_top(f, (c["x"], c["y"], c["top"]), "cyl")
        if m is None:
            log("cand", round(c["x"]), round(c["y"]), "no measurement")
            continue
        # second, centred look
        rb.cam_over((m["x"], m["y"], m["z"]), tag="cand2")
        f = rb.look()["left_wrist"]
        m2 = measure_top(f, (m["x"], m["y"], m["z"]), "cyl")
        if m2 is not None:
            m = m2
        robot.show("cand_%d_%d" % (c["x"], c["y"]), draw(f, [m]))
        log("cand", round(c["x"]), round(c["y"]), "meas xy %.1f %.1f z %.1f d %.1f dmax %.1f support %s frac_top %.2f hole %s"
            % (m["x"], m["y"], m["z"], m["d"], m["dmax"], m["support"], m["frac_top_centre"],
               None if m["hole"] is None else round(m["hole"]["d"], 1)))
        dbg(robot, "close cand", cyl=[m["x"], m["y"]])
        if m["hole"] is not None and m["frac_top_centre"] < 0.3 and m["dmax"] < 85:
            cyl = m
            break
    # 3. stick close-up
    rb.cam_over((stick["x"], stick["y"], stick["z"]), tag="stick")
    f = rb.look()["left_wrist"]
    s1 = measure_top(f, (stick["x"], stick["y"], stick["z"]), "stick")
    if s1 is not None:
        rb.cam_over((s1["x"], s1["y"], s1["z"]), tag="stick2")
        f = rb.look()["left_wrist"]
        s2 = measure_top(f, (s1["x"], s1["y"], s1["z"]), "stick")
        if s2 is not None:
            s1 = s2
        robot.show("stick", draw(f, [s1]))
        log("stick meas %.1f %.1f %.1f d %.1f" % (s1["x"], s1["y"], s1["z"], s1["d"]))
        dbg(robot, "stick close", tip=[s1["x"], s1["y"], s1["z"]])
    log("time", round(robot.time_s() - t0, 1))
    robot.home(ARM)


# ------------------------------------------------------------------ main
def new_map():
    return np.full((int((X1 - X0) / GRID), int((Y1 - Y0) / GRID)), np.nan, np.float32)


def merge_found(lists, key):
    """candidates from several views: the same object seen twice (within 40 mm) counts once."""
    out = []
    for c in sorted([c for l in lists for c in l], key=key):
        if all(math.hypot(c["x"] - o["x"], c["y"] - o["y"]) > 40 for o in out):
            out.append(c)
    return out


def analyse_view(rb, f, tzs, sticks_l, cands_l):
    """one wrist view: its own height map (views are not merged for detection: each camera pose has its own
    small error, two views of one object do not coincide), plus the merged map used for clear paths."""
    hv = new_map()
    add_to_map(hv, f)
    add_to_map(rb.hmap, f)
    if not np.isfinite(hv).any():
        return
    tz = table_height(hv)
    tzs.append(tz)
    blobs = level_blobs(hv, tz)
    sticks_l.append(find_sticks(blobs, tz))
    cands_l.append(find_cyl_candidates(blobs, tz))


STICK_KEY = lambda s: abs(s["span"] - 165) + 2 * abs(s["d"] - 24)
CAND_KEY = lambda c: -(c["round"] + 0.02 * min(c["n"], 10))


def survey(robot, rb):
    rb.hmap = new_map()
    tzs, sticks_l, cands_l = [], [], []
    for (x, y) in [(-420.0, -10.0), (-420.0, 300.0)]:
        r = rb.move((x, y, 620.0), tag="survey")
        if not r.get("done"):
            rb.move((x, y, 520.0), tag="survey-low")
        analyse_view(rb, rb.look()["left_wrist"], tzs, sticks_l, cands_l)
    tz = float(np.median(tzs)) if tzs else 0.0
    rb.tz = tz
    sticks = merge_found(sticks_l, STICK_KEY)
    cands = merge_found(cands_l, CAND_KEY)
    blobs = []
    for l in cands_l:
        blobs += l
    log("survey: table z %.1f; sticks %s; cyl cands %s" % (
        tz, [(round(s["x"]), round(s["y"]), round(s["z"]), round(s["d"]), round(s["span"])) for s in sticks[:3]],
        [(round(c["x"]), round(c["y"]), round(c["top"]), round(c["d"]), round(c["h"])) for c in cands[:5]]))
    t = dbg_truth(robot)
    if t is not None and "w_cyl" in t and not any(math.hypot(c["x"] - t["w_cyl"][0], c["y"] - t["w_cyl"][1]) < 25 for c in cands):
        near = [b for b in blobs if math.hypot(b["x"] - t["w_cyl"][0], b["y"] - t["w_cyl"][1]) < 60]
        log("DBG cylinder not a candidate; blobs near it:")
        for b in near:
            log("  DBG blob", " ".join("%s %.1f" % kv for kv in b.items()))
    vis = np.clip(np.nan_to_num(rb.hmap - tz, nan=0) * 2, 0, 255).astype(np.uint8)
    robot.show("hmap", cv2.applyColorMap(vis, cv2.COLORMAP_JET)[:, :, ::-1])
    return tz, sticks, cands


def ang_of(cl):
    return math.atan2(cl[1], cl[0])


def rot2(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def measure_twice(robot, rb, p, kind, label, cl=None):
    r = rb.cam_over(p, tag=label, cl=cl)
    if not r.get("done"):
        return None, None
    f = rb.look()["left_wrist"]
    m = measure_top(f, p, kind)
    if m is None:
        return None, f
    if math.hypot(m["x"] - p[0], m["y"] - p[1]) > 4 or abs(m["z"] - p[2]) > 8:
        rb.cam_over((m["x"], m["y"], m["z"]), tag=label + "2", cl=rb.cl_now())
        f = rb.look()["left_wrist"]
        m2 = measure_top(f, (m["x"], m["y"], m["z"]), kind)
        if m2 is not None:
            m = m2
    m["ang"] = ang_of(rb.cl_now())
    return m, f


def two_yaw(robot, rb, m1):
    """second view of the cylinder with the gripper turned 90 deg about the camera's axis.
    model: measured = C + Rz(yaw) e_g. Returns (C (2,), e_g (2,)) or (None, None)."""
    s = rb.st()
    eye = np.array(s["tcp_mm"]) + rb.cam_offset(s["closing_dir"])
    m2 = None
    for ang in (90, -90):
        r = robot.turn(ARM, eye.tolist(), [0, 0, 1], ang)
        if r.get("done"):
            m2, f = measure_twice(robot, rb, (m1["x"], m1["y"], m1["z"]), "cyl", "yaw2", cl=rb.cl_now())
            break
        log("yaw2 turn", ang, r.get("why", "")[:60])
        if r.get("moved"):
            robot.turn(ARM, eye.tolist(), [0, 0, 1], -ang)
    if m2 is None:
        # the arm cannot turn there in place: come over the cylinder again with the closing turned 90 deg
        cl1 = np.array(s["closing_dir"])
        for ang in (90, -90):
            cl2 = np.array([math.cos(math.radians(ang)) * cl1[0] - math.sin(math.radians(ang)) * cl1[1],
                            math.sin(math.radians(ang)) * cl1[0] + math.cos(math.radians(ang)) * cl1[1], 0.0])
            r = rb.cam_over((m1["x"], m1["y"], m1["z"]), tag="yaw2-move", cl=cl2)
            if r.get("done") and abs(rb.cl_now() @ unit(cl1)) < 0.5:
                m2, f = measure_twice(robot, rb, (m1["x"], m1["y"], m1["z"]), "cyl", "yaw2", cl=rb.cl_now())
                break
    if m2 is None:
        return None, None
    A = rot2(m1["ang"]) - rot2(m2["ang"])
    if abs(np.linalg.det(A)) < 0.5:
        return None, None
    d = np.array([m1["x"] - m2["x"], m1["y"] - m2["y"]])
    e_g = np.linalg.solve(A, d)
    C = np.array([m1["x"], m1["y"]]) - rot2(m1["ang"]) @ e_g
    log("two-yaw: m1 %s m2 %s -> C %s e_g %s" % (np.round([m1["x"], m1["y"]], 2).tolist(), np.round([m2["x"], m2["y"]], 2).tolist(),
                                                np.round(C, 2).tolist(), np.round(e_g, 2).tolist()))
    dbg(robot, "two-yaw C", cyl=[C[0], C[1]])
    return C, e_g


def find_cylinder(robot, rb, cands):
    for c in cands[:6]:
        m, f = measure_twice(robot, rb, (c["x"], c["y"], c["top"]), "cyl", "cand")
        if m is None:
            log("cand", round(c["x"]), round(c["y"]), "no measurement")
            continue
        log("cand", round(c["x"]), round(c["y"]), "meas xy %.1f %.1f z %.1f d %.1f dmax %.1f support %s frac_top %.2f hole %s"
            % (m["x"], m["y"], m["z"], m["d"], m["dmax"], m["support"], m["frac_top_centre"],
               None if m["hole"] is None else round(m["hole"]["d"], 1)))
        dbg(robot, "close cand", cyl=[m["x"], m["y"]])
        if m["hole"] is not None and m["frac_top_centre"] < 0.3 and m["dmax"] < 88 and m["support"] is not None:
            robot.show("cyl", draw(f, [m]))
            return m
    return None


def grasp_at(robot, rb, xy, top, support, od, yaw_cl=None):
    """open, come down over xy, grip. returns (opening, tcp_z) or (None, None)."""
    yaw_cl = rb.cl if yaw_cl is None else yaw_cl
    zg = max(support + 18.0, top - 14.0, 13.0)
    w_open = min(80.0, od + 22.0)
    rb.grip(w_open)
    r = rb.move((xy[0], xy[1], top + 50.0), cl=yaw_cl, tag="pre-grasp")
    if not r.get("done"):
        for deg in (90.0, -90.0, 45.0, -45.0):
            a = math.radians(deg)
            cl2 = np.array([math.cos(a) * yaw_cl[0] - math.sin(a) * yaw_cl[1], math.sin(a) * yaw_cl[0] + math.cos(a) * yaw_cl[1], 0.0])
            r = rb.move((xy[0], xy[1], top + 50.0), cl=cl2, tag="pre-grasp-alt")
            if r.get("done"):
                yaw_cl = cl2
                break
    if not r.get("done"):
        return None, None
    r = rb.move((xy[0], xy[1], zg), cl=yaw_cl, contact=True, tag="descend")
    z_now = rb.tcp()[2]
    if r.get("why") == "contact":
        if z_now - 16.0 > support + 9.0:
            log("descent stopped on something at z %.1f (wanted %.1f)" % (z_now, zg))
            rb.move((xy[0], xy[1], top + 50.0), cl=yaw_cl, tag="up")
            return None, None
        # fingertips on the support: come up a little
        rb.move((xy[0], xy[1], z_now + 2.0), cl=yaw_cl, tag="off-support")
        z_now += 2.0
    w = rb.grip(0)
    return w, z_now


def centre_grasp(robot, rb, C, top, support, od):
    """grip at C, open, turn 90 deg, grip again: the cylinder ends centred on the TCP (both axes).
    Leaves the gripper open 40 mm above, turned back. Returns (T, opening) or None."""
    w, zg = grasp_at(robot, rb, (C[0], C[1]), top, support, od)
    log("grasp 1: opening", w, "tcp z", zg)
    if w is None or w < 3:
        if w is not None:
            rb.grip(80)
            T = rb.tcp()
            rb.move((T[0], T[1], top + 50), cl=rb.cl_now(), tag="up")
        return None
    T = rb.tcp()
    rb.grip(min(80.0, w + 12))
    rb.move((T[0], T[1], T[2] + 3.0), cl=rb.cl_now(), tag="lift-3")
    pivot = (T + [0, 0, 3.0]).tolist()
    ang = 90
    r = robot.turn(ARM, pivot, [0, 0, 1], ang)
    if not r.get("done"):
        log("turn", ang, r.get("why", "")[:100])
        if r.get("moved"):
            robot.turn(ARM, pivot, [0, 0, 1], -ang)
        ang = -90
        r = robot.turn(ARM, pivot, [0, 0, 1], ang)
    w2 = rb.grip(0)
    T = rb.tcp()
    log("grasp 2 (turned): opening", w2, "turn", r.get("done"))
    dbg(robot, "centred", cyl=[T[0], T[1]])
    rb.grip(min(80.0, max(w, w2) + 12))
    rb.move((T[0], T[1], T[2] + 40.0), cl=rb.cl_now(), tag="up")
    if r.get("done"):
        robot.turn(ARM, (T + [0, 0, 40.0]).tolist(), [0, 0, 1], -ang)
    if w2 < 3:
        return None
    return T, w2


def run_v7(robot, task):
    rb = R(robot)
    rb.grip(80)
    t0 = robot.time_s()
    tz, sticks, cands = survey(robot, rb)
    if not sticks:
        log("no stick found")
        robot.home(ARM)
        return
    stick = sticks[0]
    # stick close-up first (same geometry as the later bias calibration); pick a yaw the arm can take there
    s = None
    for deg in (0.0, 90.0, -90.0, 45.0, -45.0):
        rb.cl = yaw_cl(deg)
        r = rb.cam_over((stick["x"], stick["y"], stick["z"]), tag="stick-yaw")
        if r.get("done"):
            rb.cl = rb.cl_now()
            s, f = measure_twice(robot, rb, (stick["x"], stick["y"], stick["z"]), "stick", "stick")
            break
    if s is None:
        log("stick close-up failed")
        robot.home(ARM)
        return
    robot.show("stick", draw(f, [s]))
    log("stick meas %.2f %.2f %.1f d %.1f (closing %s)" % (s["x"], s["y"], s["z"], s["d"], np.round(rb.cl, 2).tolist()))
    dbg(robot, "stick close", tip=[s["x"], s["y"], s["z"]])
    tip_z = s["z"]
    cyl = find_cylinder(robot, rb, cands)
    if cyl is None:
        log("no cylinder found")
        robot.home(ARM)
        return
    top, support, od = cyl["z"], cyl["support"], cyl["dmax"]
    h = top - support
    log("cyl top %.1f support %.1f h %.1f od %.1f" % (top, support, h, od))
    # second view turned 90 deg: separates the camera's bias from the cylinder's position
    C, e_g = two_yaw(robot, rb, cyl)
    if C is None:
        C, e_g = np.array([cyl["x"], cyl["y"]]), None
    e_use = e_g
    T = None
    for attempt in range(3):
        res = centre_grasp(robot, rb, C, top, support, od)
        if res is None:
            break
        T, w2 = res
        # bias calibration: look at the cylinder, now known to be at T, with the stick's geometry
        c2, f = measure_twice(robot, rb, (T[0], T[1], top), "cyl", "cal", cl=rb.cl_now())
        if c2 is None:
            log("calibration look failed")
            break
        m2 = np.array([c2["x"], c2["y"]])
        e_cal = rot2(-c2["ang"]) @ (m2 - T[:2])
        robot.show("cal", draw(f, [c2]))
        if e_g is None:
            e_use = e_cal
            break
        C_now = m2 - rot2(c2["ang"]) @ e_g
        log("calibration: e_cal %s e_two_yaw %s; cylinder now %.1f from T" % (
            np.round(e_cal, 2).tolist(), np.round(e_g, 2).tolist(), np.linalg.norm(C_now - T[:2])))
        if np.linalg.norm(C_now - T[:2]) < 2.5:
            e_use = e_cal
            break
        log("cylinder not where it was left: centre again")
        C = C_now
        T = None
    if T is None or e_use is None:
        log("could not centre the cylinder")
        if T is None:
            robot.home(ARM)
            return
        e_use = np.zeros(2)
    S = np.array([s["x"], s["y"]]) - rot2(s["ang"]) @ e_use
    log("stick corrected %s" % np.round(S, 2).tolist())
    dbg(robot, "stick corrected", tip=[S[0], S[1]])
    # grip again (the cylinder is at T)
    w3, zg = grasp_at(robot, rb, (T[0], T[1]), top, support, od)
    log("grasp 3: opening", w3, "tcp z", zg)
    if w3 is None or w3 < 3:
        log("re-grasp failed")
        robot.home(ARM)
        return
    below = zg - support                      # TCP height above the cylinder's bottom
    rb.hang = below
    z_travel = max(tip_z + below + 40.0, top + 60.0)
    rb.move((T[0], T[1], z_travel), tag="lift")
    z_above = tip_z + below + 12.0
    cl_ins = None
    for deg in (0.0, 90.0, -90.0, 45.0, -45.0, 135.0, -135.0):
        cl_try = yaw_cl(deg)
        r = rb.move((S[0], S[1], z_travel), cl=cl_try, tag="carry")
        if not r.get("done"):
            continue
        r = rb.move((S[0], S[1], z_above), cl=cl_try, tag="above stick")
        if r.get("done"):
            cl_ins = cl_try
            break
        rb.move((S[0], S[1], z_travel), cl=cl_try, tag="back up")
    if cl_ins is None:
        log("cannot reach above the stick")
        cl_ins = rb.cl
    log("inserting with closing", np.round(cl_ins, 2).tolist())
    z_in = tip_z + below - 22.0
    ok = False
    z_touch = None
    offsets = [(0.0, 0.0)]
    for rad in (0.8, 1.6, 2.4, 3.2, 4.0, 4.8):
        nang = max(6, int(round(2 * math.pi * rad / 0.85)))
        for k in range(nang):
            a = 2 * math.pi * (k + 0.5 * (rad / 0.8 % 2)) / nang
            offsets.append((rad * math.cos(a), rad * math.sin(a)))
    for i, (dx, dy) in enumerate(offsets):
        if robot.time_s() - t0 > 500:
            break
        p = (S[0] + dx, S[1] + dy)
        rb.move((p[0], p[1], z_above), cl=cl_ins, tag="search-up")
        r = rb.move((p[0], p[1], z_in), cl=cl_ins, contact=True, tag="insert")
        z_now = rb.tcp()[2]
        deep = z_now < tip_z + below - 9.0 or (z_touch is not None and z_now < z_touch - 5.0)
        if r.get("done") or z_now < z_in + 4 or deep:
            log("inserted at offset %d (%.1f, %.1f), z %.1f (%s)" % (i, dx, dy, z_now, r.get("why")))
            ok = True
            break
        z_touch = z_now if z_touch is None else min(z_touch, z_now)
        if i < 3 or i % 10 == 0:
            log("try %d (%.1f,%.1f): stopped at z %.1f (%s)" % (i, dx, dy, z_now, r.get("why")))
    dbg(robot, "insert", tip=[rb.tcp()[0], rb.tcp()[1]])
    rb.grip(min(80.0, w3 + 14))
    rb.hang = 0.0
    robot.wait(1.0)
    T2 = rb.tcp()
    rb.move((T2[0], T2[1], max(tip_z + 60.0, T2[2] + 40)), cl=cl_ins, tag="retreat")
    if not ok:
        log("insertion not found")
    rb.grip(80)
    r = robot.home(ARM)
    log("home", r.get("done"), r.get("why", "")[:80], "time", round(robot.time_s() - t0, 1))


# ------------------------------------------------------------------ main (v10): stages with retries
def measure_stick(robot, rb, sticks):
    for stick in sticks[:3]:
        for deg in (0.0, 90.0, -90.0, 45.0, -45.0):
            rb.cl = yaw_cl(deg)
            r = rb.cam_over((stick["x"], stick["y"], stick["z"]), tag="stick-yaw")
            if not r.get("done"):
                continue
            rb.cl = rb.cl_now()
            s, f = measure_twice(robot, rb, (stick["x"], stick["y"], stick["z"]), "stick", "stick")
            if s is not None and 8 <= s["d"] <= 30:
                robot.show("stick", draw(f, [s]))
                log("stick meas %.2f %.2f %.1f d %.1f (closing %s)" % (s["x"], s["y"], s["z"], s["d"], np.round(rb.cl, 2).tolist()))
                dbg(robot, "stick close", tip=[s["x"], s["y"], s["z"]])
                return s
            log("stick candidate rejected", None if s is None else round(s["d"], 1))
            break
    return None


def rescan(robot, rb, tz, fresh=False):
    """more survey views (lower, other places) merged into the height map (or a new one)."""
    if fresh:
        rb.hmap = np.full_like(rb.hmap, np.nan)
    tzs, sticks_l, cands_l = [], [], []
    for (x, y, z) in [(-380.0, -150.0, 520.0), (-380.0, 150.0, 520.0), (-330.0, 380.0, 520.0), (-300.0, 0.0, 560.0)]:
        r = rb.move((x, y, z), cl=CL0, tag="rescan")
        if r.get("done"):
            analyse_view(rb, rb.look()["left_wrist"], tzs, sticks_l, cands_l)
    sticks, cands = merge_found(sticks_l, STICK_KEY), merge_found(cands_l, CAND_KEY)
    log("rescan: sticks %s; cyl cands %s" % (
        [(round(s["x"]), round(s["y"]), round(s["z"])) for s in sticks[:3]],
        [(round(c["x"]), round(c["y"]), round(c["top"]), round(c["d"])) for c in cands[:6]]))
    return sticks, cands, None


def free_spot(rb, tz, avoid, near):
    """a flat free place on the table (survey height map) with room to turn the gripper around, nearest to `near`."""
    spots = [(float(x), float(y)) for x in range(-420, -139, 40) for y in range(-140, 421, 40)
             if not (x < -360 and y < -100)]
    spots.sort(key=lambda q: math.hypot(q[0] - near[0], q[1] - near[1]))
    for (x, y) in spots:
        if any(math.hypot(x - a[0], y - a[1]) < 130 for a in avoid):
            continue
        i, j = int((x - X0) / GRID), int((y - Y0) / GRID)
        r = int(90 / GRID)
        sub = rb.hmap[i - r:i + r + 1, j - r:j + r + 1]
        if sub.size and np.isfinite(sub).sum() > 0.5 * sub.size:
            med = float(np.nanmedian(sub))
            if float(np.nanpercentile(sub, 99)) < med + 6 and abs(med - tz) < 15:
                return np.array([x, y])
    return None


def relocate(robot, rb, cyl, tz, avoid):
    """pick the cylinder up (roughly centred) and set it down on a free spot. Returns the spot or None."""
    spot = free_spot(rb, tz, avoid, (cyl["x"], cyl["y"]))
    if spot is None:
        log("no free spot to relocate to")
        return None
    top, support, od = cyl["z"], cyl["support"], cyl["dmax"]
    w, zg = grasp_at(robot, rb, (cyl["x"], cyl["y"]), top, support, od)
    if w is None or w < 3:
        rb.grip(80)
        return None
    below = zg - support
    rb.hang = below
    T = rb.tcp()
    z_up = max(top, tz + below) + 60.0
    rb.move((T[0], T[1], z_up), cl=rb.cl_now(), tag="reloc-up")
    rb.move((spot[0], spot[1], z_up), cl=rb.cl_now(), tag="reloc-carry")
    rb.move((spot[0], spot[1], tz + below - 6.0), cl=rb.cl_now(), contact=True, tag="reloc-down")
    rb.grip(min(80.0, w + 14))
    rb.hang = 0.0
    T = rb.tcp()
    rb.move((T[0], T[1], T[2] + 60.0), cl=rb.cl_now(), tag="reloc-leave")
    log("relocated the cylinder to %s" % np.round(spot).tolist())
    return spot


def acquire_cylinder(robot, rb, cands, tz=None, avoid=()):
    """find, measure (two yaws), centre in the gripper, calibrate the camera bias. Leaves the gripper open above it."""
    cyl = find_cylinder(robot, rb, cands)
    if cyl is None:
        return None
    top, support, od = cyl["z"], cyl["support"], cyl["dmax"]
    log("cyl top %.1f support %.1f h %.1f od %.1f" % (top, support, top - support, od))
    C, e_g = two_yaw(robot, rb, cyl)
    if C is None and tz is not None:
        spot = relocate(robot, rb, cyl, tz, avoid)
        if spot is not None:
            h = top - support
            cyl2, f = measure_twice(robot, rb, (spot[0], spot[1], tz + h), "cyl", "reloc-look")
            if cyl2 is not None and cyl2["support"] is not None:
                cyl = cyl2
                top, support = cyl["z"], cyl["support"]
                C, e_g = two_yaw(robot, rb, cyl)
    if C is None:
        C, e_g = np.array([cyl["x"], cyl["y"]]), None
    e_use, T = e_g, None
    for attempt in range(3):
        res = centre_grasp(robot, rb, C, top, support, od)
        if res is None:
            break
        T, w2 = res
        c2, f = measure_twice(robot, rb, (T[0], T[1], top), "cyl", "cal", cl=rb.cl_now())
        if c2 is None:
            log("calibration look failed")
            break
        m2 = np.array([c2["x"], c2["y"]])
        e_cal = rot2(-c2["ang"]) @ (m2 - T[:2])
        robot.show("cal", draw(f, [c2]))
        if e_g is None:
            e_use = e_cal
            break
        C_now = m2 - rot2(c2["ang"]) @ e_g
        log("calibration: e_cal %s e_two_yaw %s; cylinder now %.1f from T" % (
            np.round(e_cal, 2).tolist(), np.round(e_g, 2).tolist(), np.linalg.norm(C_now - T[:2])))
        if np.linalg.norm(C_now - T[:2]) < 2.5:
            e_use = e_cal
            break
        log("cylinder not where it was left: centre again")
        C, T = C_now, None
    if T is None:
        return None
    if e_use is None:
        e_use = np.zeros(2)
    return dict(T=T, e=e_use, top=top, support=support, od=od)


def place_on_stick(robot, rb, s, acq, t_end):
    """grip the centred cylinder, carry it over the stick, thread it (searching if needed), let go.
    Returns 'ok', 'returned' (not threaded, put back where it was) or 'lost'."""
    T, top, support, od = acq["T"], acq["top"], acq["support"], acq["od"]
    S = np.array([s["x"], s["y"]]) - rot2(s["ang"]) @ acq["e"]
    tip_z = s["z"]
    log("stick corrected %s" % np.round(S, 2).tolist())
    dbg(robot, "stick corrected", tip=[S[0], S[1]])
    w3, zg = grasp_at(robot, rb, (T[0], T[1]), top, support, od)
    log("grasp 3: opening", w3, "tcp z", zg)
    if w3 is None or w3 < 3:
        rb.grip(80)
        return "lost"
    below = zg - support
    rb.hang = below
    z_travel = max(tip_z + below + 40.0, top + 60.0)
    rb.move((T[0], T[1], z_travel), tag="lift")
    z_above = tip_z + below + 12.0
    cl_ins = None
    for deg in (0.0, 90.0, -90.0, 45.0, -45.0, 135.0, -135.0):
        cl_try = yaw_cl(deg)
        r = rb.move((S[0], S[1], z_travel), cl=cl_try, tag="carry")
        if not r.get("done"):
            continue
        r = rb.move((S[0], S[1], z_above), cl=cl_try, tag="above stick")
        if r.get("done"):
            cl_ins = cl_try
            break
        rb.move((S[0], S[1], z_travel), cl=cl_try, tag="back up")
    if cl_ins is None:
        log("cannot reach above the stick")
        cl_ins = rb.cl_now()
    if rb.st()["opening_mm"] < 2.0:
        log("cylinder lost on the way")
        rb.hang = 0.0
        rb.grip(80)
        return "lost"
    z_in = tip_z + below - 22.0
    ok, z_touch = False, None
    offsets = [(0.0, 0.0)]
    for rad in (0.8, 1.6, 2.4, 3.2, 4.0, 4.8):
        nang = max(6, int(round(2 * math.pi * rad / 0.85)))
        for k in range(nang):
            a = 2 * math.pi * (k + 0.5 * (rad / 0.8 % 2)) / nang
            offsets.append((rad * math.cos(a), rad * math.sin(a)))
    for i, (dx, dy) in enumerate(offsets):
        if robot.time_s() > t_end:
            break
        p = (S[0] + dx, S[1] + dy)
        rb.move((p[0], p[1], z_above), cl=cl_ins, tag="search-up")
        r = rb.move((p[0], p[1], z_in), cl=cl_ins, contact=True, tag="insert")
        z_now = rb.tcp()[2]
        deep = z_now < tip_z + below - 9.0 or (z_touch is not None and z_now < z_touch - 5.0)
        if r.get("done") or z_now < z_in + 4 or deep:
            log("inserted at offset %d (%.1f, %.1f), z %.1f (%s)" % (i, dx, dy, z_now, r.get("why")))
            ok = True
            break
        z_touch = z_now if z_touch is None else min(z_touch, z_now)
        if i < 3 or i % 15 == 0:
            log("try %d (%.1f,%.1f): stopped at z %.1f (%s)" % (i, dx, dy, z_now, r.get("why")))
    dbg(robot, "insert", tip=[rb.tcp()[0], rb.tcp()[1]])
    if not ok:
        # put it back where it was, to try again
        log("insertion not found: putting the cylinder back")
        rb.move((S[0], S[1], z_travel), cl=cl_ins, tag="up")
        rb.move((T[0], T[1], z_travel), cl=cl_ins, tag="back")
        rb.move((T[0], T[1], zg + 3.0), cl=cl_ins, contact=True, tag="down")
        rb.grip(min(80.0, w3 + 14))
        rb.hang = 0.0
        T2 = rb.tcp()
        rb.move((T2[0], T2[1], top + 60.0), cl=cl_ins, tag="up")
        return "returned"
    rb.grip(min(80.0, w3 + 14))
    rb.hang = 0.0
    robot.wait(1.0)
    T2 = rb.tcp()
    rb.move((T2[0], T2[1], max(tip_z + 60.0, T2[2] + 40)), cl=cl_ins, tag="retreat")
    return "ok"


def run(robot, task):
    rb = R(robot)
    rb.grip(80)
    t0 = robot.time_s()
    tz, sticks, cands = survey(robot, rb)
    s = measure_stick(robot, rb, sticks) if sticks else None
    rescanned = False
    if s is None or not cands:
        log("rescanning (stick %s, %d cylinder candidates)" % (s is not None, len(cands)))
        sticks2, cands2, _ = rescan(robot, rb, tz)
        rescanned = True
        if s is None and sticks2:
            s = measure_stick(robot, rb, sticks2)
        if not cands:
            cands = cands2
    if s is None:
        log("no stick found")
        robot.home(ARM)
        return
    result = None
    for attempt in range(3):
        if robot.time_s() - t0 > 400:
            break
        avoid = [(s["x"], s["y"])]
        acq = acquire_cylinder(robot, rb, cands, tz, avoid)
        if acq is None and not rescanned:
            _, cands, _ = rescan(robot, rb, tz)
            rescanned = True
            acq = acquire_cylinder(robot, rb, cands, tz, avoid)
        if acq is None:
            log("no cylinder could be taken")
            break
        result = place_on_stick(robot, rb, s, acq, t0 + 520)
        log("attempt %d: %s" % (attempt, result))
        if result == "ok":
            break
        if result == "lost":
            # look again for where it went
            _, cands, _ = rescan(robot, rb, tz, fresh=True)
            rescanned = True
    rb.grip(80)
    r = robot.home(ARM)
    log("home", r.get("done"), r.get("why", "")[:80], "time", round(robot.time_s() - t0, 1))
