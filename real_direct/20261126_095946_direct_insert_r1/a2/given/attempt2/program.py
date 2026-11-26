"""Put the blue cylinder (hole vertical) onto the vertical stick of the wooden stand -- attempt 2.

What went wrong the first time, and what this program does about it:
 - the stick was first looked for among any tall points: it is now looked for among wood-coloured points, not too
   near the camera, close to where the scene camera saw it;
 - the stick top was 'touched' with a spurious contact 28 mm too high: the touch is checked against the camera's
   height of the top and repeated when they disagree;
 - turn_gripper moved the TCP by up to 9 mm, which undid the centring of the cylinder in the fingers: every grip
   is now used only along its own closing direction (the held thing's coordinate along it = TCP . closing_dir,
   measured while gripping), and the centre is where the lines of two grips 90 degrees apart cross;
 - the stand is light and tips at a fraction of a newton: gripping the stick would only drag it to the TCP, so the
   stick is not gripped; its top is measured by the wrist camera from a fixed 'standard' geometry, and the
   camera's error at that geometry is measured on the cylinder after the grips have put it at a known place
   (the cylinder slides and stays there; it does not spring back);
 - the arm ends ordinary moves 1-4 mm away from the commanded point: positions are corrected from the measured
   TCP; the insertion is a series of short guarded probes on a 1 mm grid around the estimate, each judged by how
   far the cylinder went below the measured contact level (a cylinder 40 mm wide can only get below the stick's
   top if the stick is in its hole); the descents are short so that a missed probe never pushes hard.
"""
import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
T_END = 600.0
TIP = 14.0          # the fingertips' ends below the TCP on the real rig


def log(*a):
    print("[prog]", *a, flush=True)


def r1(v):
    return np.round(np.asarray(v, float), 1).tolist()


def cdir(th):
    t = math.radians(th)
    return (math.cos(t), math.sin(t), 0.0)


def ang_of(v):
    return math.degrees(math.atan2(float(v[1]), float(v[0])))


def unit2(v):
    c = np.array([float(v[0]), float(v[1])])
    n = float(np.linalg.norm(c))
    return c / n if n > 1e-6 else np.array([1.0, 0.0])


def wrap90(a):
    return (a + 90.0) % 180.0 - 90.0


def rot2(v, deg):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def solve2(c1, a1, c2, a2):
    A = np.array([c1, c2], float)
    if abs(float(np.linalg.det(A))) < 0.4:
        return None
    return np.linalg.solve(A, np.array([a1, a2], float))


def hex_offsets(step, rmax):
    pts = []
    n = int(rmax / step) + 2
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            x = step * (i + 0.5 * j)
            y = step * (math.sqrt(3.0) / 2.0 * j)
            if math.hypot(x, y) <= rmax + 1e-6:
                pts.append(np.array([x, y]))
    pts.sort(key=lambda p: (round(math.hypot(p[0], p[1]), 3), math.atan2(p[1], p[0])))
    return pts


class Bot:
    def __init__(self, robot):
        self.r = robot
        self.th = -90.0
        self.nstop = 0

    def now(self):
        try:
            return float(self.r.time_s())
        except Exception:
            return T_END

    def left(self):
        return T_END - self.now()

    def st(self):
        return self.r.state(ARM)

    def tcp(self):
        return np.array(self.st()["tcp_mm"], float)

    def move(self, xyz, th=None, stop=False, tag=""):
        if th is None:
            th = self.th
        xyz = [float(v) for v in xyz]
        res = self.r.move(ARM, xyz, DOWN, cdir(th), stop_on_contact=bool(stop)) or {}
        s = self.st()
        self.th = ang_of(s["closing_dir"])
        why = str(res.get("why", "") or "")
        if "SAFETY" in why.upper():
            self.nstop += 1
        p = np.array(s["tcp_mm"], float)
        log("move %-16s %s th=%.0f%s -> done=%s moved=%s why=%r%s | tcp %s f %s t=%.1f" % (
            tag, r1(xyz), th, " GUARD" if stop else "", res.get("done"), res.get("moved"), why,
            " curved" if res.get("curved") else "", r1(p), r1(s["force_n"]), self.now()))
        return res, p

    def goto(self, xy, z, th=None, tol=0.7, ztol=2.5, iters=3, bias=None, tag="goto"):
        """Ordinary moves until the MEASURED TCP is within tol (xy) / ztol (z) of the target.
        Returns (not refused, measured tcp, last command)."""
        tgt = np.array([float(xy[0]), float(xy[1]), float(z)])
        cmd = tgt.copy()
        if bias is not None:
            cmd[:2] -= np.clip(np.asarray(bias, float), -5.0, 5.0)
        p = self.tcp()
        for i in range(iters):
            res, p = self.move(cmd, th, tag="%s.%d" % (tag, i))
            if not res.get("moved", True):
                return False, p, cmd
            e = tgt - p
            if math.hypot(e[0], e[1]) <= tol and abs(e[2]) <= ztol:
                break
            cmd = cmd + np.clip(e, -5.0, 5.0)
            d = cmd - tgt
            n = float(np.linalg.norm(d))
            if n > 9.0:
                cmd = tgt + d * (9.0 / n)
        e = tgt - p
        log("goto %s: residual xy %.2f z %.2f" % (tag, math.hypot(e[0], e[1]), e[2]))
        return True, p, cmd

    def grip(self, w):
        o = float(self.r.set_gripper(ARM, float(w)))
        log("gripper %.0f -> %.1f  t=%.1f" % (w, o, self.now()))
        return o

    def grip_rec(self):
        """Close; the gripped thing's coordinate along the closing direction is TCP . c (measured now)."""
        w = self.grip(0)
        s = self.st()
        p = np.array(s["tcp_mm"], float)
        c = unit2(s["closing_dir"])
        rec = dict(w=w, p=p, c=c, a=float(p[:2] @ c))
        log("grip record: w %.1f tcp %s c %s" % (w, r1(p), np.round(c, 3).tolist()))
        return rec

    def turn(self, ang):
        c = self.tcp()
        res = self.r.turn(ARM, [float(v) for v in c], [0.0, 0.0, 1.0], float(ang)) or {}
        s = self.st()
        self.th = ang_of(s["closing_dir"])
        log("turn %.0f -> done=%s moved=%s why=%r | tcp %s th %.0f" % (
            ang, res.get("done"), res.get("moved"), res.get("why"), r1(s["tcp_mm"]), self.th))
        return res

    def turn90(self, first=90.0):
        for a in (first, -first):
            res = self.turn(a)
            if res.get("moved"):
                return a
        return 0.0

    def look(self, cam):
        return self.r.look([cam])[cam]

    def seg(self, fr, phrase):
        try:
            out = self.r.segment(fr, phrase) or []
        except Exception as e:
            log("segment failed", phrase, repr(e))
            return []
        res = []
        for item in out:
            try:
                m, s = item
            except Exception:
                continue
            m = np.asarray(m, bool)
            if m.shape == (fr.height, fr.width):
                res.append((m, float(s)))
        return res


# ============================================================================================== vision
def hsv_arrays(rgb):
    h = cv2.cvtColor(np.ascontiguousarray(rgb, dtype=np.uint8), cv2.COLOR_RGB2HSV)
    return h[..., 0].astype(np.int32), h[..., 1].astype(np.int32), h[..., 2].astype(np.int32)


def blue_mask(rgb):
    H, S, V = hsv_arrays(rgb)
    return (H >= 92) & (H <= 130) & (S >= 130) & (V >= 40)


def wood_mask(rgb):
    H, S, V = hsv_arrays(rgb)
    return (H >= 3) & (H <= 30) & (S >= 60) & (V >= 60)


def cloud(fr):
    """(P with NaN replaced by 1e6, valid mask, distance from the camera)."""
    P = np.asarray(fr.points(), float)
    V = np.isfinite(P).all(axis=-1)
    P0 = np.where(V[..., None], P, 1e6)
    eye = np.asarray(fr.eye, float)
    D = np.linalg.norm(P0 - eye[None, None, :], axis=-1)
    return P0, V, D


def components(mask, min_area, keep=10):
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    idx = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]
    idx.sort(key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    return [lab == i for i in idx[:keep]]


def ray(fr, u, v):
    d = np.array([(u - fr.cx) / fr.fx, (v - fr.cy) / fr.fy, 1.0])
    d = np.asarray(fr.R, float) @ d
    return np.asarray(fr.eye, float), d / np.linalg.norm(d)


def ray_vertical_z(fr, u, v, xy):
    e, d = ray(fr, u, v)
    dh = d[:2]
    t = float(np.dot(np.asarray(xy, float) - e[:2], dh) / max(float(np.dot(dh, dh)), 1e-9))
    return float(e[2] + t * d[2])


def thin_top(m):
    rows = np.where(m.any(axis=1))[0]
    if len(rows) < 12:
        return None
    v0 = int(rows[0])
    us, vb = [], None
    for v in range(v0, int(rows[-1]) + 1):
        cols = np.where(m[v])[0]
        if len(cols) == 0:
            if v - v0 < 3:
                continue
            break
        if cols[-1] - cols[0] + 1 > 16:
            break
        us.append(cols.mean())
        vb = v
    if vb is None or vb - v0 < 12:
        return None
    return float(np.median(us)), v0, int(vb)


def stick_scene(bot, fr):
    """Coarse stick position (xy of its foot) and top height from the scene camera, or None."""
    wood = wood_mask(fr.rgb)
    masks = []
    for ph in ("wooden stick", "wooden stand"):
        for m, s in bot.seg(fr, ph)[:3]:
            if m.sum() < 15:
                continue
            wf = float(wood[m].mean())
            log("scene mask %r score %.2f wood %.2f area %d" % (ph, s, wf, int(m.sum())))
            if wf >= 0.2:
                masks.append(m)
    if not masks:
        masks = components(wood, 60, keep=3)
    union = np.zeros(wood.shape, bool)
    for m in masks:
        union |= m
    for m in masks + [union]:
        t = thin_top(m)
        if t is None:
            continue
        u, v0, vb = t
        pb = None
        for dv in (3, 6, 10, 14, 18):
            for du in (0, -4, 4):
                q = fr.locate(int(round(u + du)), int(min(fr.height - 1, vb + dv)))
                if q is not None and np.all(np.isfinite(q)):
                    pb = np.asarray(q, float)
                    break
            if pb is not None:
                break
        if pb is None:
            continue
        top = ray_vertical_z(fr, u, v0, pb[:2])
        if not (-720 < pb[0] < 200 and -450 < pb[1] < 650 and top - pb[2] > 40):
            continue
        log("stick (scene): u %.0f v %d..%d foot %s top %.0f" % (u, v0, vb, r1(pb), top))
        return dict(xy=pb[:2].copy(), top=float(top))
    return None


def scene_cyl(bot, fr):
    """Coarse xy of the blue cylinder from the scene camera, or None."""
    P0, V, _ = cloud(fr)
    blue = blue_mask(fr.rgb)
    masks = components(blue, 25, keep=8)
    if not masks:
        for m, s in bot.seg(fr, "blue cylinder"):
            if m.sum() > 20 and float(blue[m].mean()) > 0.3:
                masks.append(m & blue)
    best = None
    for m in masks:
        mm = m & V
        if mm.sum() < 12:
            continue
        p = P0[mm]
        c = np.median(p, axis=0)
        ext = float((np.percentile(p[:, :2], 95, axis=0) - np.percentile(p[:, :2], 5, axis=0)).max())
        if not (6.0 < ext < 80.0):
            continue
        if not (-720 < c[0] < 150 and -450 < c[1] < 650 and -60 < c[2] < 250):
            continue
        n = int(mm.sum())
        log("cylinder (scene) candidate %s ext %.0f n %d" % (r1(c), ext, n))
        if best is None or n > best[0]:
            best = (n, c)
    return None if best is None else best[1][:2].copy()


def find_stick_wrist(fr, guess, radius, zmax):
    """Top of the stick (a tall, thin, wood-coloured thing) near guess, seen from above by the wrist camera."""
    P0, V, D = cloud(fr)
    H, S, Vv = hsv_arrays(fr.rgb)
    woodish = ((H <= 35) | (H >= 170)) & (S >= 45) & (Vv >= 35)
    Z = P0[..., 2]
    ok = V & (D > 240.0) & (Z < zmax) & (Z > -40.0)
    dist = np.hypot(P0[..., 0] - guess[0], P0[..., 1] - guess[1])
    reg = ok & (dist < radius)
    if int(reg.sum()) < 150:
        log("find_stick: too few points (%d)" % int(reg.sum()))
        return None
    floor = float(np.percentile(Z[reg], 25))
    cand = reg & woodish & (Z > floor + 55.0)
    excl = np.zeros_like(cand)
    for k in range(4):
        sel = cand & ~excl
        if int(sel.sum()) < 10:
            break
        z = Z[sel]
        pts = P0[sel]
        zt = float(np.percentile(z, 98))
        topm = pts[z > zt - 6.0]
        xy = np.median(topm[:, :2], axis=0)
        up = pts[z > zt - 45.0]
        frac = float((np.hypot(up[:, 0] - xy[0], up[:, 1] - xy[1]) < 12.0).mean())
        topw = float(np.percentile(np.hypot(topm[:, 0] - xy[0], topm[:, 1] - xy[1]), 90))
        log("find_stick cand %s top %.1f floor %.1f frac %.2f topw %.1f n %d" % (
            r1(xy), zt, floor, frac, topw, len(topm)))
        dxy = np.hypot(P0[..., 0] - xy[0], P0[..., 1] - xy[1])
        if frac > 0.5 and topw < 14.0 and len(topm) >= 6:
            ann = reg & (dxy > 18.0) & (dxy < 60.0) & (Z < zt - 50.0)
            board = float(np.median(Z[ann])) if int(ann.sum()) > 30 else floor
            return dict(xy=xy.copy(), top=float(np.median(topm[:, 2])), board=board, floor=floor)
        excl |= dxy < 45.0
    return None


def find_cyl_wrist(bot, fr, guess, radius, use_seg=True):
    """The blue cylinder near guess in a downward wrist look: centre, top, radius, support, height."""
    P0, V, D = cloud(fr)
    blue = blue_mask(fr.rgb)
    ok = V & (D > 240.0)
    masks = components(blue, 40, keep=8)
    if use_seg and not masks:
        for m, s in bot.seg(fr, "blue cylinder"):
            if m.sum() > 30 and float(blue[m].mean()) > 0.3:
                masks.append(m & blue)
    best = None
    for m in masks:
        mm = m & ok
        if int(mm.sum()) < 20:
            continue
        p = P0[mm]
        c = np.median(p, axis=0)
        dg = math.hypot(c[0] - guess[0], c[1] - guess[1])
        ext = float((np.percentile(p[:, :2], 95, axis=0) - np.percentile(p[:, :2], 5, axis=0)).max())
        log("cyl cand %s dist %.0f ext %.0f n %d" % (r1(c), dg, ext, int(mm.sum())))
        if dg > radius or ext < 12.0 or ext > 100.0 or c[2] > 220.0 or c[2] < -40.0:
            continue
        key = dg - 0.01 * float(mm.sum())
        if best is None or key < best[0]:
            best = (key, mm)
    if best is None:
        return None
    mm = best[1]
    p = P0[mm]
    zt = float(np.percentile(p[:, 2], 90))
    top = p[p[:, 2] > zt - 5.0]
    if len(top) < 10:
        top = p
    lo = np.percentile(top[:, :2], 3, axis=0)
    hi = np.percentile(top[:, :2], 97, axis=0)
    ctr = 0.5 * np.median(top[:, :2], axis=0) + 0.5 * (0.5 * (lo + hi))
    R = float(np.clip(np.percentile(np.hypot(top[:, 0] - ctr[0], top[:, 1] - ctr[1]), 95), 8.0, 40.0))
    dist = np.hypot(P0[..., 0] - ctr[0], P0[..., 1] - ctr[1])
    ring = ok & ~blue & (dist > R + 10.0) & (dist < R + 45.0)
    sup = float(np.median(P0[ring][:, 2])) if int(ring.sum()) > 30 else zt - 30.0
    near = ok & (dist < 300.0)
    table = float(np.percentile(P0[near][:, 2], 15)) if int(near.sum()) > 200 else sup
    h = zt - sup
    log("cylinder (wrist): ctr %s top %.1f R %.1f support %.1f (table %.1f) h %.1f" % (
        r1(ctr), zt, R, sup, table, h))
    return dict(ctr=ctr, top=zt, R=R, support_abs=sup, support_rel=sup - table, h=h, P0=P0, ok=ok)


def choose_theta(P0, ok, ctr, floor_z, prefer=-90.0, half=40.0):
    """Closing angle th such that the open fingers at th AND th+90 clear what is around ctr."""
    d = np.hypot(P0[..., 0] - ctr[0], P0[..., 1] - ctr[1])
    sel = ok & (d < half + 50.0)
    pts = P0[sel]
    if len(pts) < 50:
        return prefer
    dd = pts[:, :2] - np.asarray(ctr, float)[None, :]
    z = pts[:, 2]
    best, bests = prefer, -1e9
    for k in range(-3, 3):
        th = prefer + 15.0 * k
        worst = 0.0
        for t in (th, th + 90.0):
            c = np.array(cdir(t)[:2])
            w = np.array([-c[1], c[0]])
            a = np.abs(dd @ c)
            b = np.abs(dd @ w)
            box = (a > half - 10.0) & (a < half + 28.0) & (b < 24.0)
            if int(box.sum()) > 5:
                worst = max(worst, float(np.percentile(z[box], 95)) - floor_z)
        score = -max(worst - 6.0, 0.0) - 0.05 * abs(15.0 * k)
        if score > bests:
            best, bests = th, score
    log("choose_theta -> %.0f (score %.1f)" % (best, bests))
    return best


# ============================================================================================== motion
def travel(bot, xy, z, th=None, tag="travel"):
    """Straight up (keeping the orientation) to z, then across to xy at z (alternatives if refused)."""
    if th is None:
        th = bot.th
    p = bot.tcp()
    if p[2] < z - 3.0:
        res, p = bot.move((p[0], p[1], z), None, tag=tag + " up")
    for dx, dz in ((0.0, 0.0), (0.0, -50.0), (-30.0, -90.0), (0.0, 60.0)):
        zz = z + dz
        if dz < 0:
            zz = max(zz, min(z, 230.0))
        tgt = (xy[0] + dx, xy[1], zz)
        res, q = bot.move(tgt, th, tag=tag)
        if res.get("done"):
            return q
        if res.get("moved"):
            return q if math.hypot(q[0] - tgt[0], q[1] - tgt[1]) < 15.0 else None
    return None


LOOK_TH = -85.0


def look_pose(bot, xy, zt):
    """TCP placed so that the wrist camera (50 mm beside the TCP: +x for closing dir -y) is above xy."""
    u = np.array([-math.sin(math.radians(LOOK_TH)), math.cos(math.radians(LOOK_TH))])
    return travel(bot, (xy[0] - 50.0 * u[0], xy[1] - 50.0 * u[1]), zt, th=LOOK_TH, tag="look")


def look_at(bot, xy, zt):
    """Wrist look from above xy; if the camera did not end up above xy (the wrist took the other way round),
    shift the TCP and look again. Returns the frame or None."""
    if look_pose(bot, xy, zt) is None:
        return None
    fr = bot.look("left_wrist")
    e = np.asarray(fr.eye, float)[:2]
    if math.hypot(e[0] - xy[0], e[1] - xy[1]) > 35.0:
        p = bot.tcp()
        res, q = bot.move((p[0] + xy[0] - e[0], p[1] + xy[1] - e[1], p[2]), None, tag="look fix")
        if res.get("done"):
            fr = bot.look("left_wrist")
    return fr


def locate_stick(bot, g):
    plans = []
    if g is not None:
        plans.append((np.asarray(g["xy"], float), float(np.clip(g["top"], 120.0, 330.0)), 140.0))
    plans += [(np.array([-260.0, -40.0]), 230.0, 320.0), (np.array([-260.0, 150.0]), 230.0, 320.0)]
    for k, (xy, topg, rad) in enumerate(plans):
        if bot.left() < 300.0:
            break
        first = (k == 0 and g is not None)
        zl = float(np.clip(topg + 190.0, 330.0, 470.0)) if first else 470.0
        fr = look_at(bot, xy, zl)
        if fr is None:
            continue
        f = find_stick_wrist(fr, xy, rad, (topg + 110.0) if first else 360.0)
        if f is None:
            continue
        f = stick_precise(bot, f)
        log("stick (wrist): %s top %.1f board %.1f" % (r1(f["xy"]), f["top"], f["board"]))
        return f
    return None


LOOK_DZ = 190.0     # standard look: TCP this much above the target's top, the camera right above the target


def stick_precise(bot, f):
    """Look at the stick's top from the standard geometry; remembers where the camera was relative to the TCP
    (the camera's error is then measured on the cylinder from the same geometry)."""
    f["eye_off"] = None
    for k in range(2):
        fr = look_at(bot, f["xy"], f["top"] + LOOK_DZ)
        if fr is None:
            break
        f2 = find_stick_wrist(fr, f["xy"], 50.0, f["top"] + 60.0)
        if f2 is None:
            break
        f2["eye_off"] = np.asarray(fr.eye, float)[:2] - bot.tcp()[:2]
        moved = math.hypot(f2["xy"][0] - f["xy"][0], f2["xy"][1] - f["xy"][1])
        f = f2
        if moved < 6.0:
            break
    return f


def camera_bias(bot, H, top_v, stick):
    """The wrist camera's error (measured - true) at the standard geometry, from the cylinder left at H, which
    the grips placed in the arm's own frame; turned into the hand's yaw of the stick's look."""
    if stick is None or stick.get("eye_off") is None:
        return None
    fr = look_at(bot, H, top_v + LOOK_DZ)
    if fr is None:
        return None
    c2 = find_cyl_wrist(bot, fr, H, 45.0, use_seg=False)
    if c2 is None:
        log("camera bias: the cylinder is not seen")
        return None
    eo = np.asarray(fr.eye, float)[:2] - bot.tcp()[:2]
    b = np.asarray(c2["ctr"], float) - np.asarray(H, float)
    d = (ang_of(eo) - ang_of(stick["eye_off"]) + 180.0) % 360.0 - 180.0
    b1 = rot2(b, -d)
    log("camera bias: %s (seen %s, placed %s, yaw diff %.1f)" % (r1(b1), r1(c2["ctr"]), r1(H), d))
    if float(np.linalg.norm(b1)) > 15.0:
        log("camera bias too large: not used")
        return None
    return b1


def reorient_grip(bot, xy, zg, th_new, z_safe):
    """Fallback when the wrist cannot turn in place: up, re-orient, down (guarded), grip."""
    p = bot.tcp()
    bot.move((p[0], p[1], z_safe), None, tag="up to re-orient")
    bot.move((xy[0], xy[1], z_safe), th_new, tag="re-orient")
    res, p = bot.move((xy[0], xy[1], zg), th_new, stop=True, tag="down again")
    if p[2] > zg + 5.0:
        return None
    return bot.grip_rec()


def touch_top(bot, xy, top_v):
    """TCP height at which the closed fingertips touch the stick's top (checked against the camera).
    A push straight down on the stick cannot tip the stand."""
    bot.grip(80)
    zs = top_v + TIP + 14.0
    p = bot.tcp()
    if p[2] < zs:
        bot.move((p[0], p[1], zs + 5.0), None, tag="up")
    ok, p, cmd = bot.goto(xy, zs, None, tol=1.5, ztol=3.0, iters=2, tag="above top")
    w = bot.grip(0)
    if w > 1.5:
        log("touch: the stick is between the fingertips: its top is higher than seen")
        bot.grip(80)
        zs += 25.0
        top_v += 25.0
        res, p = bot.move((p[0], p[1], zs), None, tag="higher")
        bot.grip(0)
    target = top_v + TIP - 16.0
    vals = []
    top_tcp = None
    for k in range(3):
        p0 = bot.tcp()
        res, p = bot.move((p0[0], p0[1], target), None, stop=True, tag="touch top%d" % k)
        z = float(p[2])
        if res.get("done") and z < target + 2.0:
            log("touch: no contact down to %.1f" % z)
            break
        vals.append(z)
        if top_v - 12.0 <= z - TIP <= top_v + 9.0:
            top_tcp = z
            break
        if len(vals) >= 2 and abs(vals[-1] - vals[-2]) < 2.5:
            top_tcp = 0.5 * (vals[-1] + vals[-2])
            break
        log("touch: contact at %.1f disagrees with the camera's top %.1f: again" % (z - TIP, top_v))
        bot.move((p[0], p[1], z + 4.0), None, tag="re-touch")
    p = bot.tcp()
    # up to where the closed fingertips cleared the top before opening them (they may stand beside the stick)
    bot.move((p[0], p[1], max(p[2] + 10.0, zs)), None, tag="off top")
    bot.grip(80)
    if top_tcp is None:
        top_tcp = top_v + TIP
        log("touch: using the camera's top %.1f" % top_v)
    else:
        log("touch: stick top at TCP %.1f -> top z %.1f (camera %.1f)" % (top_tcp, top_tcp - TIP, top_v))
    return float(top_tcp)


# ============================================================================================== cylinder
def see_cylinder(bot, guess):
    """Wrist look above guess (and a wider one if needed); returns the find_cyl_wrist dict or None."""
    gx, gy = float(guess[0]), float(guess[1])
    for zl, rad in ((300.0, 170.0), (430.0, 300.0)):
        fr = look_at(bot, (gx, gy), zl)
        if fr is None:
            continue
        cy = find_cyl_wrist(bot, fr, (gx, gy), rad)
        if cy is None:
            continue
        c = cy["ctr"]
        if math.hypot(c[0] - gx, c[1] - gy) > 40.0 or zl > 300.0:
            fr = look_at(bot, c, 300.0)
            if fr is not None:
                cy2 = find_cyl_wrist(bot, fr, c, 60.0)
                if cy2 is not None:
                    cy = cy2
        return cy
    return None


def pick_cylinder(bot, guess, z_travel, stick=None):
    """Centre the cylinder with two grips 90 degrees apart (it slides to the TCP's line each time), leave it,
    look at it from the standard geometry (-> the camera's error), grip it again; returns (cyl, None) or
    (None, new guess)."""
    cy = see_cylinder(bot, guess)
    if cy is None:
        log("cylinder not seen by the wrist camera")
        return None, None
    cx, cyy = float(cy["ctr"][0]), float(cy["ctr"][1])
    h = float(np.clip(cy["h"], 8.0, 80.0))
    support = 0.0 if cy["support_rel"] < 8.0 else float(cy["support_rel"])
    th1 = choose_theta(cy["P0"], cy["ok"], (cx, cyy), cy["support_abs"], prefer=-90.0)
    bot.grip(80)
    z_near = support + h + TIP + 20.0
    p = bot.tcp()
    bot.move((cx, cyy, max(p[2], z_near)), th1, tag="over cylinder")
    bot.move((cx, cyy, z_near), th1, tag="near cylinder")
    target = max(10.5, support + TIP - 6.0)
    res, p = bot.move((cx, cyy, target), th1, stop=True, tag="down to support")
    zs = float(p[2])
    if zs - TIP > support + max(9.0, 0.5 * h):
        log("pick: stopped at %.1f: a finger landed on something" % zs)
        bot.move((p[0], p[1], z_near + 20.0), None, tag="back up")
        return None, (cx, cyy)
    zg = zs + 5.0
    res, p = bot.move((p[0], p[1], zg), th1, tag="grip height")
    A = bot.grip_rec()
    if A["w"] < 4.0:
        log("pick: first grip empty (%.1f)" % A["w"])
        bot.grip(80)
        bot.move((p[0], p[1], z_near + 20.0), None, tag="back up")
        return None, (cx, cyy)
    bot.grip(80)
    ang = bot.turn90(90.0)
    if ang != 0.0:
        p = bot.tcp()
        if abs(p[2] - zg) > 3.0:
            bot.move((p[0], p[1], zg), None, tag="grip height 2")
        B = bot.grip_rec()
    else:
        B = reorient_grip(bot, A["p"][:2], zg, ang_of(A["c"]) + 90.0, z_near)
    if B is None or B["w"] < 4.0:
        log("pick: second grip failed")
        bot.grip(80)
        p = bot.tcp()
        bot.move((p[0], p[1], z_near + 20.0), None, tag="back up")
        return None, (cx, cyy)
    H = solve2(A["c"], A["a"], B["c"], B["a"])
    if H is None:
        H = B["p"][:2].copy()
    log("pick: cylinder pushed to %s (in the arm's frame), widths %.1f %.1f" % (r1(H), A["w"], B["w"]))
    # leave it there: it stays where the fingers put it (it slides on the table, it does not spring back)
    bot.grip(80)
    p = bot.tcp()
    bot.move((p[0], p[1], support + h + TIP + 25.0), None, tag="clear cylinder")
    bias = camera_bias(bot, H, cy["top"], stick) if stick is not None else None
    # grip it again, with the TCP on its centre
    thg = min((th1, th1 + 90.0), key=lambda t: abs(wrap90(t - bot.th)))
    z_above = support + h + TIP + 15.0
    ok, p, cmd = bot.goto(H, z_above, thg, tol=1.2, ztol=4.0, iters=2, tag="above cylinder")
    res, p = bot.move((H[0], H[1], zg), thg, stop=True, tag="down to grip")
    if p[2] > zg + 6.0:
        log("pick: stopped at %.1f above the grip height %.1f" % (p[2], zg))
        bot.move((p[0], p[1], z_near + 20.0), None, tag="back up")
        return None, (float(H[0]), float(H[1]))
    C = bot.grip_rec()
    if C["w"] < 4.0:
        log("pick: final grip empty (%.1f)" % C["w"])
        bot.grip(80)
        p = bot.tcp()
        bot.move((p[0], p[1], z_near + 20.0), None, tag="back up")
        return None, (float(H[0]), float(H[1]))
    wv = np.array([-C["c"][1], C["c"][0]])
    hole = solve2(C["c"], C["a"], wv, float(np.asarray(H) @ wv))
    if hole is None:
        hole = C["p"][:2].copy()
    off = hole - C["p"][:2]
    g = float(C["p"][2] - (zs - TIP))
    cyl = dict(off=off, cB=C["c"].copy(), th=bot.th, g=g, h=h, H=hole.copy(), support=support, zs=zs,
               zg=float(C["p"][2]), w=C["w"], bias=bias, top_v=float(cy["top"]))
    log("pick: held, hole %s = TCP + %s, bottom %.1f mm below the TCP, height %.1f" % (
        r1(hole), r1(off), g, h))
    res, p = bot.move((C["p"][0], C["p"][1], z_travel), None, tag="lift cylinder")
    w = float(bot.st()["opening_mm"])
    if w < 3.0:
        log("pick: the cylinder was lost while lifting (opening %.1f)" % w)
        bot.grip(80)
        return None, (float(hole[0]), float(hole[1]))
    cyl["th"] = bot.th
    return cyl, None


def offset_now(bot, cyl):
    """The hole's offset from the TCP (table frame), turned with the hand's yaw since the grip."""
    c_now = unit2(bot.st()["closing_dir"])
    d = wrap90(ang_of(c_now) - ang_of(cyl["cB"]))
    if abs(d) > 15.0:
        log("offset_now: the hand turned %.1f deg since the grip" % d)
    return rot2(cyl["off"], d)


def probe(bot, p, zref, D, tag):
    """Guarded descent straight down from p. 'in' when the cylinder went (nearly) the whole depth D below the
    stick's top level zref: a cylinder 40 mm wide can only get there with the stick in its hole."""
    goal = zref - 7.0
    res, q = bot.move((p[0], p[1], goal), None, stop=True, tag=tag)
    z = float(q[2])
    if not res.get("done") and (z > p[2] - 1.5 or z > zref + 2.5):
        # stopped at once or above the expected level: perhaps a spurious stop in free air; once more
        res, q = bot.move((q[0], q[1], goal), None, stop=True, tag=tag + "b")
        z = float(q[2])
    if zref - 4.5 <= z < zref - 2.0:
        res, q = bot.move((q[0], q[1], goal), None, stop=True, tag=tag + " push")
        z = float(q[2])
    if z < zref - 4.5:
        res2, q2 = bot.move((q[0], q[1], zref - D), None, stop=True, tag=tag + " thread")
        z2 = float(q2[2])
        if z2 > zref - D + 3.5 and z2 < zref - 8.0:
            res2, q2 = bot.move((q2[0], q2[1], zref - D), None, stop=True, tag=tag + " thread2")
            z2 = float(q2[2])
        if z2 < zref - D + 3.5:
            return "in", q2
        return "land", q2
    return "land", q


def insert(bot, m, cyl):
    S = np.asarray(m["S"], float)
    th = cyl["th"]
    Zc = m["top_tcp"] + cyl["zg"] - cyl["zs"]       # TCP height with the cylinder's bottom on the stick's top
    D = float(np.clip(cyl["h"] - 7.0, 8.0, 20.0))
    off = offset_now(bot, cyl)
    T = S - off
    log("insert: stick %s, hole offset %s -> TCP %s, contact level %.1f, depth %.0f" % (
        r1(S), r1(off), r1(T), Zc, D))
    if travel(bot, T, cyl["z_travel"], th=th, tag="to stick") is None:
        return "fail", None
    off = offset_now(bot, cyl)
    T = S - off
    ok, p, cmd = bot.goto(T, Zc + 14.0, th, tol=0.8, ztol=3.0, iters=3, tag="hover")
    if not ok:
        return "fail", None
    bias = p[:2] - cmd[:2]
    cands = hex_offsets(1.0, 5.0)
    tried = [0] * len(cands)
    probed = []
    lands = []
    zref = Zc
    for n in range(80):
        if bot.left() < 75.0:
            log("insert: out of time")
            break
        if bot.nstop >= 2:
            log("insert: two safety stops, stopping")
            break
        idx = None
        for i, cd in enumerate(cands):
            if tried[i] >= 2:
                continue
            if any(math.hypot(cd[0] - pr[0], cd[1] - pr[1]) < 0.55 for pr in probed):
                continue
            idx = i
            break
        if idx is None:
            log("insert: search grid exhausted")
            break
        tried[idx] += 1
        off = offset_now(bot, cyl)
        T = S - off
        tgt = T + cands[idx]
        hz = zref + (12.0 if not lands else 4.0)
        ok, p, cmd = bot.goto(tgt, hz, th, tol=0.7, ztol=2.5, iters=2, bias=bias, tag="hover%d" % n)
        if not ok:
            log("insert: hover refused")
            break
        bias = p[:2] - cmd[:2]
        verdict, q = probe(bot, p, zref, D, "probe%d" % n)
        if verdict == "in":
            log("insert: IN at %s (offset %s from the estimate), TCP z %.1f, contact level %.1f" % (
                r1(q[:2]), r1(q[:2] - T), q[2], zref))
            return "in", q
        lands.append(float(q[2]))
        zref = float(np.median(lands))
        probed.append(q[:2] - T)
        log("probe %d: landed at %.1f (level %.1f) at offset %s (aimed %s)" % (
            n, q[2], zref, r1(q[:2] - T), r1(cands[idx])))
    p = bot.tcp()
    bot.move((p[0], p[1], zref + 25.0), None, tag="give up")
    return "fail", None


def release(bot, top_tcp):
    bot.grip(80)
    bot.r.wait(0.6)
    p = bot.tcp()
    bot.move((p[0], p[1], max(p[2] + 40.0, top_tcp + 45.0)), None, tag="leave")


def verify(bot, S, top):
    """True: the cylinder is around the stick; False: seen elsewhere (with its xy); None: not seen."""
    fr = look_at(bot, S, float(np.clip(top + 190.0, 330.0, 470.0)))
    if fr is None:
        return None, None
    cy = find_cyl_wrist(bot, fr, S, 180.0)
    stk = find_stick_wrist(fr, S, 60.0, top + 40.0)
    if cy is None:
        log("verify: the cylinder is not seen")
        return None, None
    ref = stk["xy"] if stk is not None else np.asarray(S, float)
    d = math.hypot(cy["ctr"][0] - ref[0], cy["ctr"][1] - ref[1])
    # off the stick, the cylinder's centre is at least its radius + the stick's (~25 mm) from the stick's axis
    thr = 18.0 if stk is not None else 20.0
    log("verify: cylinder centre %.1f mm from the stick (%s); stick top %s" % (
        d, "same image" if stk is not None else "measured axis",
        "%.1f" % stk["top"] if stk is not None else "not seen"))
    return (d < thr), cy["ctr"]


def put_down(bot, cyl):
    """Put the held cylinder back where it was picked."""
    off = offset_now(bot, cyl)
    xy = np.asarray(cyl["H"], float) - off
    if travel(bot, xy, cyl["z_travel"], th=bot.th, tag="to put down") is None:
        return
    bot.move((xy[0], xy[1], cyl["zg"] + 15.0), None, tag="above spot")
    bot.move((xy[0], xy[1], cyl["zg"] - 4.0), None, stop=True, tag="put down")
    bot.grip(80)
    p = bot.tcp()
    bot.move((p[0], p[1], cyl["z_travel"]), None, tag="leave cylinder")


def scene_relook(bot):
    """Park the arm out of the scene camera's way and find the cylinder there."""
    travel(bot, (-480.0, 80.0), 400.0, th=-90.0, tag="park")
    return scene_cyl(bot, bot.look("scene"))


# ============================================================================================== main
def main(bot):
    bot.grip(80)
    sc = bot.look("scene")
    stick_g = stick_scene(bot, sc)
    cyl_g = scene_cyl(bot, sc)
    log("coarse: stick %s, cylinder %s" % (None if stick_g is None else r1(stick_g["xy"]),
                                           None if cyl_g is None else r1(cyl_g)))
    p = bot.tcp()
    bot.move((p[0], p[1], max(p[2], 380.0)), -90.0, tag="straighten")

    f = None
    for attempt in range(2):
        if bot.left() < 300.0:
            break
        f = locate_stick(bot, stick_g)
        if f is not None:
            break
        log("stick not found")
    if f is None:
        log("giving up: the stick is not found")
        return
    stick_cam = np.asarray(f["xy"], float)
    top_tcp = touch_top(bot, stick_cam, float(f["top"]))
    top_abs = top_tcp - TIP
    z_travel = max(top_abs + 70.0, 250.0)
    m = dict(S=stick_cam.copy(), top_tcp=top_tcp)
    bias_known = None

    for attempt in range(4):
        if bot.left() < 110.0:
            log("not enough time for another attempt")
            break
        if cyl_g is None:
            cyl_g = scene_relook(bot)
            if cyl_g is None:
                log("giving up: the cylinder is not found")
                return
        cyl, newg = pick_cylinder(bot, cyl_g, z_travel, f)
        if cyl is None:
            cyl_g = newg
            continue
        if cyl.get("bias") is not None:
            bias_known = cyl["bias"]
        m["S"] = stick_cam - bias_known if bias_known is not None else stick_cam.copy()
        log("stick top centre in the arm's frame: %s (camera %s, camera bias %s)" % (
            r1(m["S"]), r1(stick_cam), None if bias_known is None else r1(bias_known)))
        cyl["z_travel"] = max(z_travel, top_abs + cyl["g"] + 35.0)
        if cyl["z_travel"] > z_travel + 1.0:
            p = bot.tcp()
            bot.move((p[0], p[1], cyl["z_travel"]), None, tag="higher")
        status, q = insert(bot, m, cyl)
        if status == "in":
            release(bot, m["top_tcp"])
            ok, where = verify(bot, m["S"], top_abs)
            if ok is True:
                log("done: the cylinder is on the stick")
                return
            if ok is None:
                log("the cylinder is not seen near the stick from above; assuming it is on it")
                return
            log("the cylinder is not on the stick; seen at %s" % r1(where))
            cyl_g = np.asarray(where, float)
            continue
        if bot.left() > 45.0:
            put_down(bot, cyl)
        else:
            return
        if bot.left() < 150.0:
            return
        cyl_g = np.asarray(cyl["H"], float)


def finish(bot, start):
    try:
        bot.grip(80)
    except Exception as e:
        log("finish: gripper", repr(e))
    try:
        p = bot.tcp()
        if p[2] < 300.0:
            bot.move((p[0], p[1], 320.0), None, tag="final up")
    except Exception as e:
        log("finish: up", repr(e))
    try:
        r = bot.r.home(ARM) or {}
        log("home -> done=%s moved=%s why=%r" % (r.get("done"), r.get("moved"), r.get("why")))
        if not r.get("done"):
            r = bot.r.move(ARM, start["tcp"], start["app"], start["cl"]) or {}
            log("to start pose -> done=%s moved=%s why=%r" % (r.get("done"), r.get("moved"), r.get("why")))
            r = bot.r.home(ARM) or {}
            log("home -> done=%s moved=%s why=%r" % (r.get("done"), r.get("moved"), r.get("why")))
    except Exception as e:
        log("finish: home", repr(e))


def run(robot, task):
    bot = Bot(robot)
    log("task:", task)
    s0 = bot.st()
    start = dict(tcp=[float(v) for v in s0["tcp_mm"]], app=[float(v) for v in s0["approach_dir"]],
                 cl=[float(v) for v in s0["closing_dir"]])
    bot.th = ang_of(s0["closing_dir"])
    try:
        main(bot)
    except Exception as e:
        log("ERROR", repr(e))
    finally:
        finish(bot, start)
