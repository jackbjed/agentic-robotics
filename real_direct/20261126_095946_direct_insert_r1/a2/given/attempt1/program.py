"""Put the blue cylinder (hole vertical) onto the vertical stick of the wooden stand.

Plan:
 1. scene camera: coarse positions of the stick and of the cylinder.
 2. wrist camera from above: the stick (tallest thin thing), its top and the board level.
 3. empty gripper: close on the stick low down, twice (90 deg apart) -> the stick is centred on the TCP;
    then touch the stick top with the closed fingertips -> exact top height.
 4. wrist camera: the cylinder; grip it on its support, release, turn 90 deg, grip again -> cylinder centred
    on the TCP; its bottom relative to the TCP known from the fingertip contact with its support.
 5. lower the cylinder onto the stick with stop_on_contact, small spiral search if it lands on the stick top;
    when it passes the top, open, lift, verify with the wrist camera, home.
"""
import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
T_END = 600.0


def log(*a):
    print("[prog]", *a, flush=True)


def cdir(th):
    t = math.radians(th)
    return (math.cos(t), math.sin(t), 0.0)


def ang_of(v):
    return math.degrees(math.atan2(v[1], v[0]))


def ang_diff180(a, b):
    d = (a - b) % 180.0
    return min(d, 180.0 - d)


class Bot:
    def __init__(self, robot):
        self.r = robot
        self.th = -90.0

    def left(self):
        try:
            return T_END - float(self.r.time_s())
        except Exception:
            return 0.0

    def st(self):
        return self.r.state(ARM)

    def tcp(self):
        return np.array(self.st()["tcp_mm"], float)

    def opening(self):
        return float(self.st()["opening_mm"])

    def move(self, xyz, th=None, stop=False, tag=""):
        if th is None:
            th = self.th
        xyz = [float(v) for v in xyz]
        res = self.r.move(ARM, xyz, DOWN, cdir(th), stop_on_contact=bool(stop))
        s = self.st()
        self.th = ang_of(s["closing_dir"])
        log("move %s %s th=%.0f%s -> done=%s moved=%s why=%r%s tcp=%s f=%s" % (
            tag, np.round(xyz, 1).tolist(), th, " STOP" if stop else "",
            res.get("done"), res.get("moved"), res.get("why"),
            " curved" if res.get("curved") else "", np.round(s["tcp_mm"], 1).tolist(),
            np.round(s["force_n"], 1).tolist()))
        return res

    def grip(self, w):
        o = float(self.r.set_gripper(ARM, float(w)))
        log("gripper", w, "->", round(o, 1))
        return o

    def turn(self, ang):
        c = self.tcp()
        res = self.r.turn(ARM, c.tolist(), [0.0, 0.0, 1.0], float(ang))
        s = self.st()
        self.th = ang_of(s["closing_dir"])
        log("turn", ang, "->", res.get("done"), res.get("moved"), res.get("why"), "th=%.0f" % self.th)
        return res

    def turn90(self):
        for a in (90.0, -90.0):
            r = self.turn(a)
            if r.get("done"):
                return True
            if r.get("moved"):
                return False
        return False

    def travel(self, xy, z_up, z_end=None, th=None):
        """Vertical up to z_up, across to xy, then down to z_end (free moves)."""
        p = self.tcp()
        if th is None:
            th = self.th
        if p[2] < z_up - 1.0:
            r = self.move((p[0], p[1], z_up), tag="up")
            if not r.get("done"):
                return False
        r = self.move((xy[0], xy[1], max(z_up, p[2]) if p[2] < 700 else z_up), th=th, tag="across")
        if not r.get("done"):
            r = self.move((xy[0], xy[1], z_up + 60), th=th, tag="across-high")
            if not r.get("done"):
                return False
        if z_end is not None:
            q = self.tcp()
            if abs(q[2] - z_end) > 0.5:
                r = self.move((xy[0], xy[1], z_end), th=th, tag="down")
                return bool(r.get("done"))
        return True

    def look(self, cam):
        return self.r.look([cam])[cam]

    def seg(self, fr, phrase):
        try:
            out = self.r.segment(fr, phrase) or []
        except Exception as e:
            log("segment failed", phrase, repr(e))
            return []
        res = []
        for m, s in out:
            m = np.asarray(m, bool)
            if m.shape == (fr.height, fr.width):
                res.append((m, float(s)))
        return res


# ----------------------------------------------------------------------------------------------- vision
def hsv_masks(rgb):
    h = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)
    H = h[..., 0].astype(int)
    S = h[..., 1].astype(int)
    V = h[..., 2].astype(int)
    blue = (H >= 92) & (H <= 130) & (S >= 110) & (V >= 45)
    wood = (H >= 5) & (H <= 30) & (S >= 70) & (V >= 70)
    return blue, wood


def frame_pts(fr, zmax=None):
    P = np.asarray(fr.points(), float)
    V = np.isfinite(P).all(axis=-1)
    if zmax is not None:
        z = np.where(V, P[..., 2], 1e9)
        V &= z < zmax
    return P, V


def components(mask, min_area, keep=8):
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


def cyl_candidates(bot, fr, zmax=None, guess=None, radius=None):
    P, V = frame_pts(fr, zmax)
    blue, _ = hsv_masks(fr.rgb)
    masks = []
    for ph in ("blue cylinder", "blue ring"):
        for m, s in bot.seg(fr, ph):
            masks.append((m, s, ph))
    for m in components(blue, 25):
        masks.append((m, 0.3, "colour"))
    cands = []
    for m, s, ph in masks:
        if m.sum() < 20:
            continue
        bf = float(blue[m].mean())
        mm = m & V
        if mm.sum() < 10:
            continue
        p = P[mm]
        c = np.median(p, axis=0)
        lo = np.percentile(p[:, :2], 5, axis=0)
        hi = np.percentile(p[:, :2], 95, axis=0)
        ext = float((hi - lo).max())
        if ext > 110 or ext < 6:
            continue
        if guess is not None and math.hypot(c[0] - guess[0], c[1] - guess[1]) > radius:
            continue
        if not (-720 < c[0] < 200 and -450 < c[1] < 650 and -60 < c[2] < 300):
            continue
        score = s + bf - (1.0 if bf < 0.15 else 0.0)
        cands.append(dict(score=score, c=c, mask=mm, ext=ext, bf=bf, ph=ph))
    cands.sort(key=lambda d: -d["score"])
    for d in cands[:4]:
        log("cyl cand", d["ph"], "score %.2f bf %.2f ext %.0f" % (d["score"], d["bf"], d["ext"]),
            np.round(d["c"], 1).tolist())
    return cands, P, V


def cyl_measure(P, V, mask):
    """Centre (xy), top z, outer radius, support z, height of a cylinder seen from above."""
    p = P[mask & V]
    zt = float(np.percentile(p[:, 2], 90))
    top = p[p[:, 2] > zt - 5]
    if len(top) < 8:
        top = p
    lo = np.percentile(top[:, :2], 3, axis=0)
    hi = np.percentile(top[:, :2], 97, axis=0)
    ctr = 0.5 * (0.5 * (lo + hi)) + 0.5 * top[:, :2].mean(axis=0)
    R = float(np.percentile(np.hypot(top[:, 0] - ctr[0], top[:, 1] - ctr[1]), 95))
    R = float(np.clip(R, 8, 45))
    allp = P[V]
    d = np.hypot(allp[:, 0] - ctr[0], allp[:, 1] - ctr[1])
    ring = allp[(d > R + 8) & (d < R + 35)]
    support = float(np.median(ring[:, 2])) if len(ring) > 20 else zt - 30.0
    h = zt - support
    log("cyl measure ctr", np.round(ctr, 1).tolist(), "top %.1f R %.1f support %.1f h %.1f" % (zt, R, support, h))
    return ctr, zt, R, support, h


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
    """Coarse stick position (xy) and top height from the scene camera, or None."""
    P, V = frame_pts(fr)
    _, wood = hsv_masks(fr.rgb)
    masks = []
    for ph in ("wooden stick", "wooden stand", "wooden board"):
        for m, s in bot.seg(fr, ph)[:3]:
            if m.sum() < 15:
                continue
            wf = float(wood[m].mean())
            log("stand mask", ph, "score %.2f wood %.2f area %d" % (s, wf, int(m.sum())))
            if wf >= 0.2:
                masks.append((m, s, ph))
    if not masks:
        for m in components(wood, 60, keep=3):
            masks.append((m, 0.2, "colour"))
    union = np.zeros(wood.shape, bool)
    for m, s, ph in masks:
        union |= m
    tries = [m for m, s, ph in masks] + [union]
    for m in tries:
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
        log("stick (scene) u %.0f v %d..%d base" % (u, v0, vb), np.round(pb, 1).tolist(), "top %.0f" % top)
        return dict(xy=pb[:2], top=top)
    if union.any():
        p = P[union & V]
        if len(p) > 10:
            c = np.median(p, axis=0)
            log("stand (scene) only, centre", np.round(c, 1).tolist())
            return dict(xy=c[:2], top=float(c[2]) + 170.0)
    return None


def find_tall(fr, guess_xy, radius, zmax):
    """The top of a tall thin vertical thing (the stick) near guess_xy, from a downward wrist look."""
    P, V = frame_pts(fr, zmax)
    cloud = P[V]
    d = np.hypot(cloud[:, 0] - guess_xy[0], cloud[:, 1] - guess_xy[1])
    Q = cloud[d < radius]
    if len(Q) < 100:
        log("find_tall: too few points", len(Q))
        return None
    floor = float(np.percentile(Q[:, 2], 20))
    excl = np.zeros(len(Q), bool)
    for _ in range(4):
        tall = Q[(~excl) & (Q[:, 2] > floor + 45)]
        if len(tall) < 12:
            log("find_tall: nothing tall")
            return None
        zt = float(np.percentile(tall[:, 2], 98))
        top = tall[tall[:, 2] > zt - 5]
        xy = np.median(top[:, :2], axis=0)
        upper = tall[tall[:, 2] > zt - 40]
        frac = float((np.hypot(upper[:, 0] - xy[0], upper[:, 1] - xy[1]) < 12).mean())
        topw = float(np.percentile(np.hypot(top[:, 0] - xy[0], top[:, 1] - xy[1]), 90))
        log("find_tall cand", np.round(xy, 1).tolist(), "top %.1f floor %.1f frac %.2f topw %.1f n %d"
            % (zt, floor, frac, topw, len(top)))
        dq = np.hypot(Q[:, 0] - xy[0], Q[:, 1] - xy[1])
        if frac > 0.55 and topw < 13:
            ann = Q[(dq > 15) & (dq < 40) & (Q[:, 2] < zt - 40)]
            board = float(np.median(ann[:, 2])) if len(ann) > 20 else None
            return dict(xy=xy, top=float(np.median(top[:, 2])), board=board, floor=floor, cloud=cloud)
        excl |= dq < 50
    return None


def choose_theta(cloud, cxy, z_tips, half=45.0, prefer=90.0):
    """Closing angle th (deg) such that the fingers at th and th+90 clear what is around."""
    best, best_s = prefer, -1e9
    d = cloud[:, :2] - np.asarray(cxy, float)[None, :]
    near = np.hypot(d[:, 0], d[:, 1]) < half + 40
    d = d[near]
    z = cloud[near, 2]
    for th in range(0, 180, 15):
        hmax = -1e9
        for t in (th, th + 90):
            c = np.array(cdir(t)[:2])
            w = np.array([-c[1], c[0]])
            a = np.abs(d @ c)
            b = np.abs(d @ w)
            sel = (a > half - 14) & (a < half + 16) & (b < 24)
            if sel.sum() > 3:
                hmax = max(hmax, float(np.percentile(z[sel], 97)))
        margin = min(z_tips - hmax, 30.0)
        sc = margin - 0.05 * min(ang_diff180(th, prefer), ang_diff180(th + 90, prefer))
        if sc > best_s:
            best, best_s = float(th), sc
    if ang_diff180(best + 90, prefer) < ang_diff180(best, prefer):
        best = best + 90
    while best - prefer > 90:
        best -= 180
    while best - prefer < -90:
        best += 180
    log("choose_theta -> %.0f (score %.1f)" % (best, best_s))
    return best


def search_pattern(step, rmax):
    pts = []
    n = int(rmax / step) + 2
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            x = step * (i + 0.5 * j)
            y = step * (math.sqrt(3) / 2 * j)
            if math.hypot(x, y) <= rmax + 1e-6:
                pts.append((x, y))
    pts.sort(key=lambda p: (round(math.hypot(*p), 2), math.atan2(p[1], p[0])))
    return pts


# ----------------------------------------------------------------------------------------------- stick
def stick_locate(bot, guess_xy, guess_top):
    gx, gy = float(guess_xy[0]), float(guess_xy[1])
    scan = [(gx, gy), (-330.0, 0.0), (-330.0, -150.0), (-330.0, 150.0), (-200.0, 0.0), (-450.0, 0.0)]
    found = None
    for k, xy in enumerate(scan):
        z = float(np.clip(guess_top + 270.0, 430.0, 620.0)) if k == 0 else 520.0
        if not bot.travel(xy, z):
            continue
        fr = bot.look("left_wrist")
        t = bot.tcp()
        found = find_tall(fr, xy, 180.0 if k == 0 else 260.0, t[2] - 125.0)
        if found is not None:
            break
    if found is None:
        return None
    # closer look, TCP right above the stick
    xy = found["xy"]
    z = found["top"] + 185.0
    if bot.travel(xy, z, z):
        fr = bot.look("left_wrist")
        t = bot.tcp()
        f2 = find_tall(fr, xy, 70.0, t[2] - 125.0)
        if f2 is not None:
            if f2["board"] is None:
                f2["board"] = found["board"]
            found = f2
    if found["board"] is None:
        found["board"] = found["floor"] + 20.0
    log("stick (wrist)", np.round(found["xy"], 1).tolist(), "top %.1f board %.1f" % (found["top"], found["board"]))
    return found


def stick_center(bot, info):
    """Centre the stick between the fingers twice (90 deg apart), then touch its top. Returns dict or None."""
    sx, sy = float(info["xy"][0]), float(info["xy"][1])
    top, board = float(info["top"]), float(info["board"])
    zg = max(board + 30.0, top - 90.0)
    zg = min(zg, top - 12.0)
    th = choose_theta(info["cloud"], (sx, sy), zg - 16.0, 45.0, prefer=bot.th)
    bot.grip(80)
    if not bot.travel((sx, sy), max(top + 60.0, 250.0), top + 40.0, th=th):
        log("stick_center: cannot get above the stick")
        return None
    r = bot.move((sx, sy, zg), th, stop=True, tag="around stick")
    if not r.get("done"):
        z = bot.tcp()[2]
        log("stick_center: stopped at %.1f before %.1f" % (z, zg))
        if z > zg + 3.0:
            bot.move((sx, sy, top + 40.0), tag="back up")
            return None
    w1 = bot.grip(0)
    if w1 < 1.5:
        c = np.array(cdir(bot.th)[:2])
        wv = np.array([-c[1], c[0]])
        for off in (12.0, -12.0, 24.0, -24.0):
            bot.grip(80)
            bot.move((sx + off * wv[0], sy + off * wv[1], zg), tag="shift")
            w1 = bot.grip(0)
            if w1 >= 1.5:
                break
    if not (1.5 <= w1 <= 45.0):
        log("stick_center: bad first grip %.1f" % w1)
        bot.grip(80)
        bot.move(tuple(bot.tcp()[:2]) + (top + 40.0,), tag="back up")
        return None
    bot.grip(80)
    if not bot.turn90():
        p = bot.tcp()
        bot.move((p[0], p[1], top + 40.0), tag="lift for re-orient")
        bot.move((p[0], p[1], top + 40.0), th=bot.th + 90.0, tag="re-orient")
        r = bot.move((p[0], p[1], zg), stop=True, tag="around stick 2")
        if not r.get("done"):
            bot.move((p[0], p[1], top + 40.0), tag="back up")
            return None
    w2 = bot.grip(0)
    p = bot.tcp()
    if not (1.5 <= w2 <= 45.0):
        log("stick_center: bad second grip %.1f" % w2)
        bot.grip(80)
        bot.move((p[0], p[1], top + 40.0), tag="back up")
        return None
    sxy = p[:2].copy()
    log("stick centred at", np.round(sxy, 2).tolist(), "diam %.1f / %.1f, moved %.1f mm from vision"
        % (w1, w2, math.hypot(sxy[0] - sx, sxy[1] - sy)))
    bot.grip(80)
    # the stick's top, touched with the closed fingertips
    bot.move((sxy[0], sxy[1], top + 16.0 + 25.0), tag="above top")
    bot.grip(0)
    r = bot.move((sxy[0], sxy[1], top + 16.0 - 40.0), stop=True, tag="touch top")
    Zs = float(bot.tcp()[2])
    if r.get("done") or not r.get("moved", True):
        log("stick_center: no contact with the stick top")
        bot.move((sxy[0], sxy[1], top + 40.0), tag="back up")
        bot.grip(80)
        return None
    log("stick top touched: TCP z %.1f -> top z %.1f (vision %.1f)" % (Zs, Zs - 16.0, top))
    bot.move((sxy[0], sxy[1], Zs + 10.0), tag="off top")
    bot.grip(80)
    return dict(xy=sxy, Zs=Zs, d=w2, board=board, top_v=top)


# ----------------------------------------------------------------------------------------------- cylinder
def cyl_find_wrist(bot, guess_xy, z_look):
    """Look down at guess_xy with the wrist camera; returns (ctr, top, R, support, h, cloud) or None."""
    if not bot.travel(guess_xy, z_look, z_look):
        return None
    for attempt in range(2):
        fr = bot.look("left_wrist")
        t = bot.tcp()
        cands, P, V = cyl_candidates(bot, fr, zmax=t[2] - 125.0, guess=guess_xy, radius=170.0)
        if not cands:
            return None
        ctr, zt, R, support, h = cyl_measure(P, V, cands[0]["mask"])
        if not (5.0 < h < 150.0):
            log("cylinder height implausible", h)
        if attempt == 0 and math.hypot(ctr[0] - t[0], ctr[1] - t[1]) > 60.0:
            guess_xy = ctr
            bot.move((ctr[0], ctr[1], z_look), tag="re-centre look")
            continue
        return ctr, zt, R, support, h, P[V]
    return ctr, zt, R, support, h, P[V]


def cyl_pick(bot, guess_xy, z_travel):
    """Grip the cylinder centred (two grips 90 deg apart) and lift it. Returns dict, or (None, new_guess)."""
    res = cyl_find_wrist(bot, guess_xy, max(z_travel, 250.0))
    if res is None:
        log("cylinder not seen by the wrist camera")
        return None, guess_xy
    ctr, zt, R, support, h, cloud = res
    cx, cy = float(ctr[0]), float(ctr[1])
    half = max(45.0, R + 22.0)
    th = choose_theta(cloud, (cx, cy), support + 3.0, half, prefer=bot.th)
    bot.grip(80)
    bot.move((cx, cy, max(z_travel, 250.0)), th, tag="over cylinder")
    bot.move((cx, cy, zt + 16.0 + 20.0), th, tag="near cylinder")
    ztarget = max(12.5, support + 16.0 - 10.0)
    r = bot.move((cx, cy, ztarget), th, stop=True, tag="down to support")
    zc = float(bot.tcp()[2])
    if zc - 16.0 > support + max(8.0, 0.5 * h):
        log("cyl_pick: stopped high (%.1f): landed on something" % zc)
        bot.move((cx, cy, zt + 60.0), tag="back up")
        return None, (cx, cy)
    zgrip = zc + (3.0 if h > 14.0 else 1.0)
    bot.move((cx, cy, zgrip), tag="grip height")
    w1 = bot.grip(0)
    if w1 < 4.0:
        log("cyl_pick: missed (%.1f)" % w1)
        bot.grip(80)
        bot.move((cx, cy, zt + 60.0), tag="back up")
        return None, (cx, cy)
    bot.grip(80)
    if not bot.turn90():
        p = bot.tcp()
        bot.move((p[0], p[1], zt + 30.0), tag="lift for re-orient")
        bot.move((p[0], p[1], zt + 30.0), th=bot.th + 90.0, tag="re-orient")
        r = bot.move((p[0], p[1], zgrip), stop=True, tag="down again")
    w2 = bot.grip(0)
    if w2 < 4.0:
        log("cyl_pick: second grip missed (%.1f)" % w2)
        bot.grip(80)
        bot.move(tuple(bot.tcp()[:2]) + (zt + 60.0,), tag="back up")
        return None, (cx, cy)
    p = bot.tcp()
    g = float(p[2] - (zc - 16.0))      # TCP height above the cylinder's bottom
    pick = dict(D=w2, h=float(h), g=g, xy=p[:2].copy(), support_tcp=zc)
    log("cylinder gripped: D %.1f/%.1f h %.1f g %.1f" % (w1, w2, h, g))
    bot.move((p[0], p[1], z_travel), tag="lift cylinder")
    w3 = bot.opening()
    if w3 < 3.0:
        log("cylinder dropped while lifting")
        return None, (cx, cy)
    return pick, None


def put_down(bot, pick, z_travel):
    """Put the held cylinder back where it was picked (after a failed insertion)."""
    xy = pick["xy"]
    bot.travel(xy, z_travel, pick["support_tcp"] + 30.0)
    bot.move((xy[0], xy[1], pick["support_tcp"] + pick["g"] - 16.0 - 2.0), stop=True, tag="put down")
    bot.grip(80)
    bot.move((xy[0], xy[1], z_travel), tag="leave cylinder")


def insert(bot, stick, cyl, z_travel):
    sx, sy = float(stick["xy"][0]), float(stick["xy"][1])
    Zs = stick["Zs"]
    Zt = Zs - 16.0 + cyl["g"]          # TCP height at which the cylinder's bottom is at the stick's top
    depth = float(np.clip(cyl["h"] + 12.0, 15.0, 60.0))
    depth = min(depth, cyl["g"] + 75.0)  # keep the stick's top well below the gripper's body
    if not bot.travel((sx, sy), z_travel, Zt + 25.0):
        log("insert: cannot get above the stick")
        return False
    pts = search_pattern(1.2, 6.0)
    ok = False
    x, y = sx, sy
    for i, (dx, dy) in enumerate(pts):
        if bot.left() < 70.0:
            log("insert: out of time")
            break
        x, y = sx + dx, sy + dy
        bot.move((x, y, Zt + 2.5), tag="above%d" % i)
        r = bot.move((x, y, Zt - depth), stop=True, tag="insert%d (%.1f,%.1f)" % (i, dx, dy))
        z = float(bot.tcp()[2])
        if r.get("done") or z < Zt - 4.0:
            ok = True
            log("insert: the stick is in the hole (TCP %.1f, top contact level %.1f)" % (z, Zt))
            break
    if not ok:
        bot.move((x, y, Zt + 25.0), tag="give up")
        return False
    z = float(bot.tcp()[2])
    if z > Zt - depth + 3.0:
        for k in range(2):
            bot.move((x, y, z + 1.0), tag="wiggle")
            bot.move((x, y, Zt - depth), stop=True, tag="push deeper")
            z = float(bot.tcp()[2])
            if z <= Zt - depth + 3.0:
                break
    bot.grip(80)
    bot.r.wait(0.5)
    bot.move((x, y, Zs + 40.0), tag="leave")
    return True


def verify(bot, stick, cyl):
    """True: cylinder around the stick; False + xy: seen elsewhere; None: not seen."""
    sx, sy = float(stick["xy"][0]), float(stick["xy"][1])
    bot.move((sx, sy, stick["Zs"] + 170.0), tag="verify look")
    fr = bot.look("left_wrist")
    t = bot.tcp()
    tall = find_tall(fr, (sx, sy), 70.0, t[2] - 125.0)
    cands, P, V = cyl_candidates(bot, fr, zmax=t[2] - 125.0, guess=(sx, sy), radius=200.0)
    if not cands:
        return None, None
    ctr, zt, R, support, h = cyl_measure(P, V, cands[0]["mask"])
    ref = tall["xy"] if tall is not None else np.array([sx, sy])
    dist = math.hypot(ctr[0] - ref[0], ctr[1] - ref[1])
    log("verify: cylinder centre %.1f mm from the stick (ref from %s)" % (dist, "image" if tall else "contact"))
    if dist < 14.0:
        return True, ctr
    return False, ctr


# ----------------------------------------------------------------------------------------------- main
PARK = (-480.0, 80.0, 400.0)


def scene_find_cyl(bot):
    """Park the arm out of the scene camera's way and find the cylinder there (coarse xy) or None."""
    bot.travel(PARK[:2], PARK[2], PARK[2])
    sc = bot.look("scene")
    cands, _, _ = cyl_candidates(bot, sc)
    return np.asarray(cands[0]["c"][:2], float) if cands else None


def main(bot):
    bot.grip(80)
    sc = bot.look("scene")
    stick_g = stick_scene(bot, sc)
    cands, _, _ = cyl_candidates(bot, sc)
    cyl_g = np.asarray(cands[0]["c"][:2], float) if cands else None
    log("coarse: stick", None if stick_g is None else np.round(stick_g["xy"], 1).tolist(),
        "cylinder", None if cyl_g is None else np.round(cyl_g, 1).tolist())
    if stick_g is None:
        stick_g = dict(xy=np.array([-300.0, 20.0]), top=200.0)
    p = bot.tcp()
    bot.move((p[0], p[1], max(p[2], 420.0)), th=bot.th, tag="straighten")

    stick, info = None, None
    for attempt in range(3):
        if bot.left() < 200.0:
            break
        info = stick_locate(bot, stick_g["xy"], stick_g["top"])
        if info is None:
            log("stick not found")
            if attempt >= 1:
                break
            continue
        stick = stick_center(bot, info)
        if stick is not None:
            break
        stick_g = dict(xy=info["xy"], top=info["top"])
    if stick is None:
        log("giving up: the stick could not be measured")
        return
    z_travel = max(stick["Zs"] + 45.0, 240.0)

    for attempt in range(4):
        if bot.left() < 110.0:
            log("not enough time left for another attempt")
            break
        if cyl_g is None:
            cyl_g = scene_find_cyl(bot)
            if cyl_g is None:
                log("giving up: cylinder not found")
                return
        pick, newg = cyl_pick(bot, cyl_g, z_travel)
        if pick is None:
            cyl_g = newg
            continue
        ok = insert(bot, stick, pick, z_travel)
        if not ok:
            if bot.left() > 40.0:
                put_down(bot, pick, z_travel)
            if bot.left() > 170.0:
                info2 = dict(xy=stick["xy"], top=stick["Zs"] - 16.0, board=stick["board"], cloud=info["cloud"])
                s2 = stick_center(bot, info2)
                if s2 is not None:
                    stick = s2
                cyl_g = pick["xy"]
                continue
            return
        v, where = verify(bot, stick, pick)
        if v is True:
            log("done: the cylinder is on the stick")
            return
        if v is False:
            log("the cylinder is not on the stick; seen at", np.round(where, 1).tolist())
            cyl_g = np.asarray(where, float)
            continue
        g = scene_find_cyl(bot)
        if g is not None and math.hypot(g[0] - stick["xy"][0], g[1] - stick["xy"][1]) < 45.0:
            log("scene camera: the cylinder is at the stick; done")
            return
        cyl_g = g


def finish(bot, start):
    try:
        bot.grip(80)
    except Exception as e:
        log("finish: gripper", repr(e))
    try:
        p = bot.tcp()
        if p[2] < 290.0:
            bot.move((p[0], p[1], 300.0), tag="final up")
    except Exception as e:
        log("finish: up", repr(e))
    try:
        r = bot.r.home(ARM)
        log("home ->", r.get("done"), r.get("moved"), r.get("why"))
        if not r.get("done"):
            r = bot.r.move(ARM, start["tcp"], start["app"], start["cl"])
            log("to start pose ->", r.get("done"), r.get("moved"), r.get("why"))
            r = bot.r.home(ARM)
            log("home ->", r.get("done"), r.get("moved"), r.get("why"))
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
