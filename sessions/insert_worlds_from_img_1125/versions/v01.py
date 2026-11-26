import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
CL0 = np.array([0.0, -1.0, 0.0])
CAM_D = 300.0          # close-up distance camera -> target surface (mm)
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
class R:
    def __init__(self, robot):
        self.robot = robot
        self.cam_off = np.array([50.8, -2.8, 132.7])   # wrist camera eye - TCP at yaw CL0 (updated from frames)

    def st(self):
        return self.robot.state(ARM)

    def tcp(self):
        return np.array(self.st()["tcp_mm"], float)

    def move(self, p, cl=CL0, contact=False, tag=""):
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
            if abs(np.dot(np.array(s["closing_dir"]), CL0)) > 0.999 and s["approach_dir"][2] < -0.999:
                self.cam_off = np.array(f.eye) - np.array(s["tcp_mm"])
        return fr

    def cam_over(self, p, dist=CAM_D, tag="cam"):
        """move so the wrist camera is `dist` above point p (pointing down, yaw CL0)."""
        tgt = np.array([p[0], p[1], p[2] + dist]) - np.array([self.cam_off[0], self.cam_off[1], self.cam_off[2]])
        tgt[2] = min(tgt[2], 780.0)
        return self.move(tgt, tag=tag)


# ------------------------------------------------------------------ height map from wrist frames
def add_to_map(hmap, f):
    P = f.points().reshape(-1, 3)
    ok = np.isfinite(P[:, 2])
    P = P[ok]
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


def find_sticks(hmap, tz):
    m = (np.nan_to_num(hmap, nan=-999) > tz + 120).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for k in range(1, n):
        area = stats[k, cv2.CC_STAT_AREA] * GRID * GRID
        ii, jj = np.nonzero(lab == k)
        zs = hmap[ii, jj]
        ztop = float(np.percentile(zs, 90))
        sel = zs > ztop - 15
        x, y = cell_xy(ii[sel].mean(), jj[sel].mean())
        if x < -400 and y < -260:
            continue           # the parked right arm
        if area > 2500:
            continue
        out.append(dict(x=x, y=y, z=ztop, area=area))
    out.sort(key=lambda d: -d["z"])
    return out


def find_cyl_candidates(hmap, tz):
    h = np.nan_to_num(hmap, nan=tz).astype(np.float32)
    h = np.maximum(h, tz)
    k = int(round(100 / GRID)) | 1
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    bg = cv2.morphologyEx(h, cv2.MORPH_OPEN, disk)
    th = h - bg
    m = (th > 7).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for k2 in range(1, n):
        blob = (lab == k2).astype(np.uint8)
        cs, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cs:
            continue
        c = max(cs, key=cv2.contourArea)
        area = cv2.contourArea(c) * GRID * GRID
        if area < 200:
            continue
        (ci, cj), rad = cv2.minEnclosingCircle(c)
        rad_mm = rad * GRID + GRID / 2
        d_eq = 2 * math.sqrt(area / math.pi)
        ii, jj = np.nonzero(lab == k2)
        th_max = float(np.percentile(th[ii, jj], 90))
        top = float(np.percentile(h[ii, jj], 90))
        x, y = cell_xy(cj, ci)       # contour points are (col=j, row=i)
        round_ = area / (math.pi * rad_mm ** 2)
        good = (16 <= d_eq <= 80) and (2 * rad_mm <= 95) and (8 <= th_max <= 80) and round_ > 0.45
        if x < -400 and y < -260:
            good = False
        out.append(dict(x=x, y=y, top=top, d=d_eq, dmax=2 * rad_mm, th=th_max, round=round_, good=good))
    return [c for c in out if c["good"]], out


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


# ------------------------------------------------------------------ main
def run(robot, task):
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
