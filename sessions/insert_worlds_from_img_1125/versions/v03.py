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
def add_to_map(hmap, f, min_dist=320.0):
    P = f.points().reshape(-1, 3)
    D = f.depth.copy()
    D[350:, :230] = np.nan          # the fingertips (and flying pixels at their edges)
    D[350:, 410:] = np.nan
    D = D.reshape(-1)
    ok = np.isfinite(P[:, 2]) & np.isfinite(D) & (D > min_dist)
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
def survey(robot, rb):
    hmap = np.full((int((X1 - X0) / GRID), int((Y1 - Y0) / GRID)), np.nan, np.float32)
    for (x, y) in [(-420.0, -10.0), (-420.0, 300.0)]:
        r = rb.move((x, y, 620.0), tag="survey")
        if not r.get("done"):
            rb.move((x, y, 520.0), tag="survey-low")
        f = rb.look()["left_wrist"]
        add_to_map(hmap, f)
    tz = table_height(hmap)
    blobs = level_blobs(hmap, tz)
    sticks = find_sticks(blobs, tz)
    cands = find_cyl_candidates(blobs, tz)
    log("table z %.1f" % tz, "cam_off", np.round(rb.cam_off, 1).tolist())
    log("sticks", [{k: round(v, 1) for k, v in s.items()} for s in sticks[:4]])
    log("cyl cands", [{k: round(v, 1) for k, v in c.items()} for c in cands[:8]])
    if sticks:
        dbg(robot, "survey stick", tip=[sticks[0]["x"], sticks[0]["y"], sticks[0]["z"]])
    for c in cands[:6]:
        dbg(robot, "survey cand", cyl=[c["x"], c["y"]])
    vis = np.clip(np.nan_to_num(hmap - tz, nan=0) * 2, 0, 255).astype(np.uint8)
    robot.show("hmap", cv2.applyColorMap(vis, cv2.COLORMAP_JET)[:, :, ::-1])
    return tz, sticks, cands


def measure_twice(robot, rb, p, kind, label):
    rb.cam_over(p, tag=label)
    f = rb.look()["left_wrist"]
    m = measure_top(f, p, kind)
    if m is None:
        return None, f
    if math.hypot(m["x"] - p[0], m["y"] - p[1]) > 4 or abs(m["z"] - p[2]) > 8:
        rb.cam_over((m["x"], m["y"], m["z"]), tag=label + "2")
        f = rb.look()["left_wrist"]
        m2 = measure_top(f, (m["x"], m["y"], m["z"]), kind)
        if m2 is not None:
            m = m2
    return m, f


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


def grasp_at(robot, rb, xy, top, support, od, yaw_cl=CL0):
    """open, come down over xy, grip. returns (opening, tcp_z) or (None, None)."""
    zg = max(support + 17.0, top - 14.0, 13.0)
    zg = min(zg, top - 4.0) if top - 4.0 > 13.0 else zg
    w_open = min(80.0, od + 22.0)
    rb.grip(w_open)
    r = rb.move((xy[0], xy[1], top + 50.0), cl=yaw_cl, tag="pre-grasp")
    if not r.get("done"):
        return None, None
    r = rb.move((xy[0], xy[1], zg), cl=yaw_cl, contact=True, tag="descend")
    z_now = rb.tcp()[2]
    if r.get("why") == "contact" and z_now > zg + 6:
        log("descent stopped early at z %.1f (wanted %.1f)" % (z_now, zg))
        rb.move((xy[0], xy[1], top + 50.0), cl=yaw_cl, tag="up")
        return None, None
    w = rb.grip(0)
    return w, z_now


def run(robot, task):
    rb = R(robot)
    rb.grip(80)
    t0 = robot.time_s()
    tz, sticks, cands = survey(robot, rb)
    if not sticks:
        log("no stick found")
        robot.home(ARM)
        return
    stick = sticks[0]
    # stick close-up first (same geometry as the later bias calibration)
    s, f = measure_twice(robot, rb, (stick["x"], stick["y"], stick["z"]), "stick", "stick")
    if s is None:
        log("stick close-up failed")
        robot.home(ARM)
        return
    robot.show("stick", draw(f, [s]))
    log("stick meas %.2f %.2f %.1f d %.1f" % (s["x"], s["y"], s["z"], s["d"]))
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
    # grasp, then grasp again turned 90 deg: the cylinder ends centred on the TCP
    w, zg = grasp_at(robot, rb, (cyl["x"], cyl["y"]), top, support, od)
    log("grasp 1: opening", w, "tcp z", zg)
    if w is None or w < 3:
        log("grasp failed")
        robot.home(ARM)
        return
    T = rb.tcp()
    rb.grip(min(80.0, w + 14))
    r = robot.turn(ARM, T.tolist(), [0, 0, 1], 90)
    if not r.get("done"):
        r = robot.turn(ARM, T.tolist(), [0, 0, 1], -90)
    w2 = rb.grip(0)
    log("grasp 2 (turned): opening", w2, "turn", r.get("done"))
    dbg(robot, "centred", cyl=[T[0], T[1]])
    st = rb.st()
    rb.grip(min(80.0, w2 + 14))
    rb.robot.move(ARM, [T[0], T[1], T[2] + 60], DOWN, st["closing_dir"], False)
    # bias calibration: measure the (now known) cylinder position with the same geometry as the stick
    c2, f = measure_twice(robot, rb, (T[0], T[1], top), "cyl", "cal")
    if c2 is None:
        log("calibration look failed; no correction")
        E = np.zeros(2)
    else:
        E = np.array([c2["x"] - T[0], c2["y"] - T[1]])
        robot.show("cal", draw(f, [c2]))
    log("camera bias E", np.round(E, 2).tolist())
    S = np.array([s["x"], s["y"]]) - E
    dbg(robot, "stick corrected", tip=[S[0], S[1]])
    # grip again (the cylinder is at T)
    w3, zg = grasp_at(robot, rb, (T[0], T[1]), top, support, od)
    log("grasp 3: opening", w3, "tcp z", zg)
    if w3 is None or w3 < 3:
        log("re-grasp failed")
        robot.home(ARM)
        return
    below = zg - support                      # TCP height above the cylinder's bottom
    z_travel = max(tip_z + below + 40.0, top + 60.0)
    rb.move((T[0], T[1], z_travel), tag="lift")
    rb.move((S[0], S[1], z_travel), tag="carry")
    z_above = tip_z + below + 12.0
    rb.move((S[0], S[1], z_above), tag="above stick")
    st = rb.st()
    log("holding: opening %.1f force %s" % (st["opening_mm"], np.round(st["force_n"], 1).tolist()))
    z_in = tip_z + below - 22.0
    ok = False
    offsets = [(0.0, 0.0)]
    for rad in (0.6, 1.2, 1.8, 2.4, 3.0, 3.6):
        nang = max(6, int(round(2 * math.pi * rad / 0.6)))
        for k in range(nang):
            a = 2 * math.pi * k / nang
            offsets.append((rad * math.cos(a), rad * math.sin(a)))
    for i, (dx, dy) in enumerate(offsets):
        if robot.time_s() - t0 > 480:
            break
        p = (S[0] + dx, S[1] + dy)
        rb.move((p[0], p[1], z_above), tag="search-up")
        r = rb.move((p[0], p[1], z_in), contact=True, tag="insert")
        z_now = rb.tcp()[2]
        if r.get("done") or z_now < z_in + 4:
            log("inserted at offset %d (%.1f, %.1f), z %.1f" % (i, dx, dy, z_now))
            ok = True
            break
        if i < 3 or i % 10 == 0:
            log("try %d (%.1f,%.1f): stopped at z %.1f (%s)" % (i, dx, dy, z_now, r.get("why")))
    dbg(robot, "insert", tip=[rb.tcp()[0], rb.tcp()[1]])
    rb.grip(min(80.0, w3 + 14))
    robot.wait(1.0)
    T2 = rb.tcp()
    rb.move((T2[0], T2[1], max(tip_z + 60.0, T2[2] + 40)), tag="retreat")
    if not ok:
        log("insertion not found")
    j = None
    try:
        j = robot.judge()
    except Exception:
        pass
    log("judge before home", j)
    rb.grip(80)
    r = robot.home(ARM)
    log("home", r.get("done"), r.get("why", "")[:80], "time", round(robot.time_s() - t0, 1))
