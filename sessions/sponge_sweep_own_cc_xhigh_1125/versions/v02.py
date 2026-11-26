import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
GX0, GX1, GY0, GY1 = -620.0, -20.0, -320.0, 520.0   # top-down map (1 mm/px); row i: x = GX1 - i, col j: y = GY1 - j
MH, MW = int(GX1 - GX0), int(GY1 - GY0)
LOOK = (-360.0, 110.0, 520.0)
WSX, WSY = (-485.0, -105.0), (-235.0, 455.0)        # where objects are looked for and the arm works
T_END = 560.0                                         # robot time after which no new stroke starts

COLS = [("light blue", (140, 190, 230)), ("dark blue", (30, 50, 110)), ("light green", (175, 225, 165)),
        ("dark green", (30, 90, 40)), ("light grey", (180, 180, 180)), ("dark grey", (70, 70, 70)),
        ("yellow", (235, 205, 60)), ("green", (60, 160, 70)), ("blue", (50, 100, 210)), ("pink", (235, 135, 165)),
        ("orange", (240, 140, 50)), ("purple", (140, 90, 180)), ("grey", (140, 140, 140)), ("gray", (140, 140, 140)),
        ("white", (238, 238, 235)), ("black", (30, 30, 30)), ("red", (200, 40, 40)), ("brown", (130, 85, 50)),
        ("beige", (215, 190, 150))]


def log(*a):
    print(*a)


# ------------------------------------------------------------------ task text
def colour_before(words, i, n=4):
    for k in range(max(0, i - n), i):
        for name, rgb in COLS:
            nw = name.split()
            if words[k:k + len(nw)] == nw and k + len(nw) <= i:
                return name, rgb
    return None


def parse_task(task):
    t = task.lower()
    for ch in ",.;:()!?":
        t = t.replace(ch, " ")
    w = t.split()
    h = {"sponge_col": None, "target_kind": None, "target_col": None, "objects": "small objects"}
    for i, x in enumerate(w):
        if x.startswith("sponge") and h["sponge_col"] is None:
            h["sponge_col"] = colour_before(w, i)
        if x in ("paper", "sheet", "card", "napkin", "mat", "towel", "cloth", "board") and h["target_kind"] is None:
            h["target_kind"] = "paper"
            h["target_col"] = colour_before(w, i, 5)
        if x == "tape" and h["target_kind"] is None:
            h["target_kind"] = "tape"
            h["target_col"] = colour_before(w, i, 3)
    if "sweep" in w:
        i = w.index("sweep") + 1
        s = []
        while i < len(w) and w[i] not in ("into", "onto", "to", "on", "in", "using", "with"):
            if w[i] not in ("all", "the", "of"):
                s.append(w[i])
            i += 1
        if s:
            h["objects"] = " ".join(s)
    return h


# ------------------------------------------------------------------ small helpers
def to_lab(rgb):
    return cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)


def lab_of(rgb):
    return to_lab(np.array([[rgb]], np.uint8))[0, 0]


def cdist(lab, ref, wl=0.7):
    d = lab - ref
    return np.sqrt((wl * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)


def xy_to_ij(x, y):
    return (GX1 - x), (GY1 - y)


def ij_to_xy(i, j):
    return GX1 - i, GY1 - j


def perp(a):
    return np.array([-a[1], a[0]])


def unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def rect_local(rect, p):
    d = np.asarray(p[:2], float) - rect["c"]
    return float(d @ rect["ax"]), float(d @ perp(rect["ax"]))


def rect_contains(rect, p, margin=0.0):
    lx, ly = rect_local(rect, p)
    return abs(lx) <= rect["a"] - margin and abs(ly) <= rect["b"] - margin


def rect_from_minarea(rr):
    (cj, ci), (w, h), ang = rr
    cx, cy = ij_to_xy(ci, cj)
    th = math.radians(ang)
    dj, di = math.cos(th), math.sin(th)
    ax = unit(np.array([-di, -dj]))
    return {"c": np.array([cx, cy]), "ax": ax, "a": w / 2.0, "b": h / 2.0}


def rect_poly_ij(c, ax, a, b):
    ay = perp(ax)
    pts = []
    for sx, sy in [(1, 1), (1, -1), (-1, -1), (-1, 1)]:
        p = c + sx * a * ax + sy * b * ay
        i, j = xy_to_ij(*p)
        pts.append([int(round(j)), int(round(i))])
    return np.array(pts, np.int32)


def draw_rect(vis, r, col, th=1):
    cv2.polylines(vis, [rect_poly_ij(r["c"], r["ax"], r["a"], r["b"])], True, col, th)


def in_ws(p, m=0.0):
    return WSX[0] + m <= p[0] <= WSX[1] - m and WSY[0] + m <= p[1] <= WSY[1] - m and not (p[0] < -400 and p[1] < -260)


# ------------------------------------------------------------------ perception
def table_height(z):
    v = z[np.isfinite(z)]
    if v.size < 1000:
        return None
    lo, hi = np.percentile(v, [1, 60])
    hist, edges = np.histogram(v, bins=np.arange(lo - 2, hi + 2, 1.0))
    k = int(np.argmax(hist))
    sel = v[(v > edges[k] - 3) & (v < edges[k + 1] + 3)]
    return float(np.median(sel))


def scatter_heights(f, zt, smooth=5, grow=5):
    d = f.depth
    P = f.points()
    dsm = cv2.medianBlur(np.nan_to_num(d, nan=0).astype(np.float32), smooth)
    good = np.isfinite(P[..., 2]) & (dsm > 0)
    X, Y, Z = P[..., 0][good], P[..., 1][good], P[..., 2][good]
    gi = np.round(GX1 - X).astype(int)
    gj = np.round(GY1 - Y).astype(int)
    ok = (gi >= 0) & (gi < MH) & (gj >= 0) & (gj < MW)
    hm = np.full((MH, MW), -500.0, np.float32)
    np.maximum.at(hm, (gi[ok], gj[ok]), (Z[ok] - zt).astype(np.float32))
    hm = cv2.dilate(hm, np.ones((grow, grow), np.uint8))
    known = hm > -499
    hm = np.where(known, hm, 0.0).astype(np.float32)
    hm = cv2.medianBlur(hm, 5)
    return hm, known


class Map:
    pass


def build_map(fw, fs=None, zt_prior=None, vmax=385):
    """top-down colour + height map of the table from the wrist frame fw (and the scene frame fs for heights)"""
    zt = table_height(fw.points()[..., 2])
    if zt is None or (zt_prior is not None and abs(zt - zt_prior) > 6):
        zt = zt_prior if zt_prior is not None else 0.0
    ii, jj = np.mgrid[0:MH, 0:MW]
    pts = np.stack([GX1 - ii.astype(np.float64), GY1 - jj.astype(np.float64), np.full((MH, MW), zt)], -1).reshape(-1, 3)
    uv, dep = fw.project(pts)
    mu = uv[:, 0].reshape(MH, MW).astype(np.float32)
    mv = uv[:, 1].reshape(MH, MW).astype(np.float32)
    valid = (mu >= 2) & (mu < fw.width - 2) & (mv >= 2) & (mv < vmax) & (dep.reshape(MH, MW) > 0)
    img = cv2.remap(fw.rgb, mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    hm, known = scatter_heights(fw, zt)
    if fs is not None:
        Ps = fs.points()
        zs = Ps[..., 2][np.isfinite(Ps[..., 2]) & (Ps[..., 0] < -60) & (Ps[..., 0] > -600)]
        zts = table_height(zs) if zs.size > 1000 else None
        if zts is not None:
            hs, ks = scatter_heights(fs, zts, smooth=5, grow=7)
            use = ~cv2.dilate(known.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) & ks
            hm = np.where(use, hs, hm)
            known = known | ks
    m = Map()
    m.zt, m.img, m.valid, m.hm, m.known = zt, img, valid, hm, known
    m.lab = to_lab(img)
    return m


def find_talls(m, thr=14.0, min_area=600):
    tall = ((m.hm > thr)).astype(np.uint8)
    tall = cv2.morphologyEx(tall, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(tall)
    talls = []
    allm = np.zeros((MH, MW), bool)
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < min_area:
            continue
        mk = lbl == k
        talls.append({"mask": mk, "hmax": float(np.percentile(m.hm[mk], 95)), "area": int(stats[k, cv2.CC_STAT_AREA])})
        allm |= mk
    return talls, allm


def table_colour(m, tall_all):
    base = m.valid & ~cv2.dilate(tall_all.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
    if base.sum() < 1000:
        base = m.valid
    tcol = np.median(m.lab[base], axis=0)
    dg = cdist(m.lab, tcol)
    thr_g = max(16.0, 3.0 * float(np.median(dg[base])))
    return tcol, dg, thr_g


def find_sponge(m, talls, hints, near=None):
    best = None
    sc = hints.get("sponge_col")
    for t in talls:
        mk = t["mask"]
        top = mk & (m.hm > t["hmax"] - 10)
        top = cv2.morphologyEx(top.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        if top.sum() < 500:
            continue
        cnts, _ = cv2.findContours(top, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cnts, key=cv2.contourArea)
        rr = cv2.minAreaRect(cnt)
        w, h = rr[1]
        L, S = max(w, h), min(w, h)
        fillr = cv2.contourArea(cnt) / max(w * h, 1)
        score = -(abs(L - 105) / 30.0 + abs(S - 58) / 15.0) + 2.0 * (fillr - 0.8)
        if not (60 < L < 170 and 32 < S < 95):
            score -= 3
        col = np.median(m.lab[top.astype(bool)], axis=0)
        if sc is not None:
            score += 2.0 - float(cdist(col, lab_of(sc[1]))) / 25.0
        rect = rect_from_minarea(rr)
        if rect["a"] < rect["b"]:
            rect = {"c": rect["c"], "ax": perp(rect["ax"]), "a": rect["b"], "b": rect["a"]}
        if near is not None:
            score -= np.linalg.norm(rect["c"] - near) / 40.0
        if not in_ws(rect["c"], -40):
            score -= 3
        if best is None or score > best["score"]:
            best = {"rect": rect, "top": t["hmax"], "score": score, "comp": t, "L": L, "S": S, "col": col}
    return best


def find_target(m, fg, hints):
    mk0 = cv2.morphologyEx(fg.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(mk0)
    best = None
    tc = hints.get("target_col")
    kind_h = hints.get("target_kind")
    for k in range(1, n):
        a = stats[k, cv2.CC_STAT_AREA]
        if a < 1500:
            continue
        mk = lbl == k
        cnts, _ = cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cnts, key=cv2.contourArea)
        rr = cv2.minAreaRect(cnt)
        w, h = rr[1]
        if min(w, h) < 60:
            continue
        fillr = a / max(w * h, 1)
        col = np.median(m.lab[mk], axis=0)
        kind = "paper" if fillr > 0.7 else ("tape" if fillr < 0.6 else "?")
        score = min(w, h) / 100.0 + (1.0 if in_ws(np.array(ij_to_xy(rr[0][1], rr[0][0]))) else -2.0)
        if kind_h is not None:
            score += 2.0 if kind == kind_h else -2.0
        if tc is not None:
            score += 2.0 - float(cdist(col, lab_of(tc[1]))) / 25.0
        if best is None or score > best["score"]:
            rect = rect_from_minarea(rr)
            tw = 0.0
            if kind == "tape":
                per = 2 * (w + h)
                disc = per * per - 16 * a
                tw = (per - math.sqrt(max(disc, 0))) / 8.0
            best = {"rect": rect, "kind": kind, "score": score, "col": col, "mask": mk, "tw": tw, "fill": fillr}
    if best is not None:
        r = best["rect"]
        tw = best["tw"]
        # the region the objects must end in (tape: up to the tape's middle; paper: the sheet)
        best["region"] = {"c": r["c"], "ax": r["ax"], "a": r["a"] - tw / 2, "b": r["b"] - tw / 2}
    return best


def find_objects(m, tcol, thr_g, tg, excl):
    fill = m.img.copy()
    rgb = cv2.cvtColor(np.array([[tcol]], np.float32).astype(np.uint8), cv2.COLOR_LAB2RGB)[0, 0]
    fill[~m.valid] = rgb
    bg = cv2.medianBlur(fill, 41)
    bgl = to_lab(bg)
    dl = cdist(m.lab, bgl)
    thr_l = max(16.0, 4.0 * float(np.median(dl[m.valid])))
    ii, jj = np.mgrid[0:MH, 0:MW]
    X, Y = GX1 - ii, GY1 - jj
    wsm = (X > WSX[0]) & (X < WSX[1]) & (Y > WSY[0]) & (Y < WSY[1])
    cand = (dl > thr_l) & m.valid & ~excl & wsm
    if tg is not None and tg["kind"] == "tape":
        cand &= ~cv2.dilate(tg["mask"].astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(cand)
    objs = []
    tgcol = tg["col"] if tg is not None else None
    for k in range(1, n):
        a = stats[k, cv2.CC_STAT_AREA]
        if a < 10 or a > 2500:
            continue
        mk = lbl == k
        col = np.median(m.lab[mk], axis=0)
        bcol = np.median(bgl[mk], axis=0)
        if float(cdist(col, tcol)) < thr_g * 0.8 and float(cdist(col, bcol)) < thr_l * 1.2:
            continue
        if tgcol is not None and float(cdist(col, tgcol)) < 14:
            continue
        # shadow: same chroma as the background, darker but not black
        dab = math.hypot(col[1] - bcol[1], col[2] - bcol[2])
        if dab < 7 and col[0] < bcol[0] and col[0] > 0.45 * bcol[0]:
            continue
        ci, cj = cent[k][1], cent[k][0]
        x, y = ij_to_xy(ci, cj)
        objs.append({"p": np.array([x, y]), "r": math.sqrt(a / math.pi), "area": int(a)})
    return objs


def show_map(robot, m, tag, tg=None, sp=None, objs=(), talls=(), strokes=()):
    vis = m.img.copy()
    for t in talls:
        cnts, _ = cv2.findContours(t["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(vis, cnts, -1, (255, 0, 0), 1)
    if tg is not None:
        draw_rect(vis, tg["region"], (0, 255, 255))
    if sp is not None:
        draw_rect(vis, sp["rect"], (255, 0, 255))
    for o in objs:
        i, j = xy_to_ij(*o["p"])
        cv2.circle(vis, (int(j), int(i)), int(o["r"]) + 3, (255, 255, 255), 1)
    for s in strokes:
        a = xy_to_ij(*s["a"])
        b = xy_to_ij(*s["b"])
        cv2.arrowedLine(vis, (int(a[1]), int(a[0])), (int(b[1]), int(b[0])), (255, 80, 0), 2)
    ii, jj = xy_to_ij(WSX[1], WSY[1])
    i2, j2 = xy_to_ij(WSX[0], WSY[0])
    vis = vis[max(0, int(ii) - 20):int(i2) + 20, max(0, int(jj) - 20):int(j2) + 20]
    robot.show(tag, vis)


# ------------------------------------------------------------------ the skill
class Skill:
    def __init__(self, robot, task):
        self.r = robot
        self.hints = parse_task(task)
        self.zt = None
        self.tg = None
        self.sp = None
        self.obst = np.zeros((MH, MW), bool)
        self.obst_h = 0.0
        self.zc = None          # TCP z with the held sponge touching the table
        self.held = False
        self.grip_w = None
        self.park = None
        self.shows = 0
        self.truth0 = robot.truth() or {}

    # ---- motion helpers
    def st(self):
        return self.r.state(ARM)

    def tcp(self):
        return np.array(self.st()["tcp_mm"], float)

    def move(self, p, cd, contact=False, label=""):
        res = self.r.move(ARM, [float(p[0]), float(p[1]), float(p[2])], DOWN, [float(cd[0]), float(cd[1]), 0.0], contact)
        if not res.get("moved", True) or (not res.get("done", True) and res.get("why") not in ("contact",)):
            log("move", label, np.round(p, 1), "->", res)
        return res

    def t(self):
        return self.r.time_s()

    def show(self, *a, **k):
        if self.shows < 11:
            self.shows += 1
            show_map(self.r, *a, **k)

    # ---- looking
    def look_all(self, tag, pose=LOOK, scene=True, cd=(0, -1, 0)):
        cams = ["left_wrist", "scene"] if scene else ["left_wrist"]
        r = self.move(pose, cd, label="look")
        fr = self.r.look(cams)
        fw = fr["left_wrist"]
        dn = np.asarray(fw.R, float)[:, 1]
        want = np.array([-272.0 + 45.0 * dn[0], 110.0])
        eye = np.asarray(fw.eye, float)
        if np.linalg.norm(eye[:2] - want) > 20:
            q = self.tcp()
            q[:2] += want - eye[:2]
            self.move(q, cd, label="look adjust")
            fr = self.r.look(cams)
        m = build_map(fr["left_wrist"], fr.get("scene"), self.zt)
        if self.zt is None:
            self.zt = m.zt
        return m

    def analyse(self, m, first=False, tag=""):
        talls, tall_all = find_talls(m)
        tcol, dg, thr_g = table_colour(m, tall_all)
        fg = (dg > thr_g) & m.valid & ~tall_all
        tg = find_target(m, fg, self.hints)
        if tg is not None:
            self.tg = tg
        sp = None
        if first:
            sp = find_sponge(m, talls, self.hints)
            self.sp = sp
            obst = tall_all.copy()
            if sp is not None:
                poly = rect_poly_ij(sp["rect"]["c"], sp["rect"]["ax"], sp["rect"]["a"] + 8, sp["rect"]["b"] + 8)
                cut = np.zeros((MH, MW), np.uint8)
                cv2.fillPoly(cut, [poly], 1)
                obst &= ~cut.astype(bool)
            self.obst = obst
            self.obst_h = max([t["hmax"] for t in talls if (obst & t["mask"]).sum() > 200] or [0.0])
        excl = cv2.dilate(tall_all.astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool)
        objs = find_objects(m, tcol, thr_g, self.tg, excl)
        self.show(m, "map" + tag, tg=self.tg, sp=sp, objs=objs, talls=talls)
        return objs

    # ---- sponge
    def cam_offset(self):
        f = self.r.look(["left_wrist"])["left_wrist"]
        return np.asarray(f.eye, float) - self.tcp(), f

    def refine_sponge(self):
        sp = self.sp
        c = sp["rect"]["c"]
        top_abs = self.zt + sp["top"]
        off, _ = self.cam_offset()
        p = np.array([c[0] - off[0], c[1] - off[1], max(top_abs + 330 - off[2], self.zt + 200)])
        self.move(p, (0, -1, 0), label="sponge look")
        f = self.r.look(["left_wrist"])["left_wrist"]
        m = build_map(f, None, self.zt, vmax=470)
        talls, _ = find_talls(m)
        sp2 = find_sponge(m, talls, self.hints, near=c)
        if sp2 is not None and np.linalg.norm(sp2["rect"]["c"] - c) < 60:
            log("sponge refined", np.round(sp2["rect"]["c"], 1), "L/S", round(sp2["L"]), round(sp2["S"]), "top", round(sp2["top"], 1),
                "(was", np.round(c, 1), round(sp["top"], 1), ")")
            self.show(m, "sponge", sp=sp2, talls=talls)
            self.sp = sp2
        else:
            log("sponge refine failed", sp2 and np.round(sp2["rect"]["c"], 1))

    def grasp_sponge(self, c, short_ax, top_abs, z_grasp=None):
        self.r.set_gripper(ARM, 80)
        cd = short_ax
        r = self.move([c[0], c[1], top_abs + 50], cd, label="above sponge")
        if not r.get("moved", True):
            return False
        if z_grasp is None:
            z_grasp = max(top_abs - 15, self.zt + 13)
        r = self.move([c[0], c[1], z_grasp], cd, contact=True, label="down to sponge")
        zs = self.tcp()[2]
        if r.get("why") == "contact" and zs > top_abs - 4:
            log("finger landed on the sponge top at", round(zs, 1))
            self.move([c[0], c[1], top_abs + 50], cd)
            return False
        w = self.r.set_gripper(ARM, 0)
        log("grip opening", round(w, 1), "tcp z", round(zs, 1), "top", round(top_abs, 1))
        if w < 12:
            self.r.set_gripper(ARM, 80)
            self.move([c[0], c[1], top_abs + 50], cd)
            return False
        self.grip_z = zs
        self.grip_w = w
        p = self.tcp()
        self.move([p[0], p[1], max(top_abs + 40, p[2] + 40)], cd, label="lift sponge")
        w2 = self.st()["opening_mm"]
        if w2 < 10:
            log("sponge lost on lift", w2)
            return False
        self.held = True
        return True

    def pick_sponge(self):
        for attempt in range(3):
            sp = self.sp
            rect = sp["rect"]
            ok = self.grasp_sponge(rect["c"], perp(rect["ax"]), self.zt + sp["top"])
            if ok:
                self.sp_ax = rect["ax"]
                return True
            # look again from above the sponge
            self.refine_sponge()
        return False

    def touch_table(self, p_xy):
        cd = self.cur_cd()
        p = self.tcp()
        if self.path_blocked(p[:2], p_xy, margin=25):
            z = max(p[2], self.zt + self.obst_h + 90)
            self.move([p[0], p[1], z], cd, label="up for touch")
            p = self.tcp()
        self.move([p_xy[0], p_xy[1], p[2]], cd, label="to touch")
        r = self.move([p_xy[0], p_xy[1], self.zt + 11], cd, contact=True, label="touch")
        z = self.tcp()[2]
        if r.get("why") != "contact":
            log("touch: no contact", r)
        self.zc = z
        log("table touch: tcp z", round(z, 1), "table", round(self.zt, 1), "sponge bottom below tcp", round(z - self.zt, 1),
            "force", np.round(self.st()["force_n"], 1))
        self.move([p_xy[0], p_xy[1], z + 25], cd)

    def cur_cd(self):
        return np.array(self.st()["closing_dir"], float)[:2]

    def release(self, p_xy, cd):
        """put the held sponge down at p_xy"""
        p = self.tcp()
        self.transit(p_xy, cd)
        self.move([p_xy[0], p_xy[1], self.zc + 1.5], cd, label="put down")
        self.r.set_gripper(ARM, 80)
        q = self.tcp()
        self.park = {"p": np.array(p_xy, float), "cd": unit(cd), "z": q[2]}
        self.held = False
        self.move([q[0], q[1], q[2] + 45], cd)

    def regrasp(self):
        pk = self.park
        self.r.set_gripper(ARM, 80)
        self.move([pk["p"][0], pk["p"][1], pk["z"] + 45], pk["cd"], label="above parked")
        self.move([pk["p"][0], pk["p"][1], pk["z"]], pk["cd"], label="down parked")
        w = self.r.set_gripper(ARM, 0)
        self.move([pk["p"][0], pk["p"][1], pk["z"] + 25], pk["cd"])
        w = self.st()["opening_mm"]
        log("regrasp opening", round(w, 1))
        if w < 10:
            return False
        self.held = True
        return True

    # ---- paths
    def swept_mask(self, a, b, d, half_len, half_dep):
        """footprint of the sponge moved from TCP a to TCP b (its long side across d)"""
        mk = np.zeros((MH, MW), np.uint8)
        c = (np.asarray(a) + np.asarray(b)) / 2
        L = np.linalg.norm(np.asarray(b) - np.asarray(a))
        cv2.fillPoly(mk, [rect_poly_ij(c, d, L / 2 + half_dep, half_len)], 1)
        return mk.astype(bool)

    def path_blocked(self, a, b, margin=15):
        if np.linalg.norm(np.asarray(b) - np.asarray(a)) < 1:
            r = max(self.sl, self.sw) / 2 + margin
            mk = self.swept_mask(a, a, np.array([1.0, 0.0]), r, r)
        else:
            d = unit(np.asarray(b) - np.asarray(a))
            mk = self.swept_mask(a, b, d, self.sl / 2 + margin, self.sw / 2 + margin)
        return bool((mk & self.obst).any())

    def transit(self, p_xy, cd):
        """move the held sponge (or the hand) to above p_xy at the low transit height, over obstacles if needed"""
        p = self.tcp()
        zl = self.zc + 25
        if self.path_blocked(p[:2], p_xy, margin=25):
            zh = self.zt + self.obst_h + (self.zc - self.zt) + 35
            self.move([p[0], p[1], max(zh, p[2])], cd, label="up over")
            self.move([p_xy[0], p_xy[1], max(zh, p[2])], cd, label="over")
            return self.move([p_xy[0], p_xy[1], zl], cd, label="down after over")
        if p[2] < zl - 1:
            self.move([p[0], p[1], zl], cd)
        return self.move([p_xy[0], p_xy[1], zl], cd, label="transit")

    # ---- strokes
    def plan(self, objs):
        tg = self.tg
        R = tg["region"]
        C = R["c"]
        goal = {"c": C, "ax": R["ax"], "a": max(R["a"] - 18, 10), "b": max(R["b"] - 18, 10)}
        out = [o for o in objs if not rect_contains(R, o["p"], 8)]
        out.sort(key=lambda o: -np.linalg.norm(o["p"] - C))
        done = [False] * len(out)
        strokes = []
        hd = self.sw / 2.0
        for k, o in enumerate(out):
            if done[k]:
                continue
            best = None
            for aim in self.aims(goal, o["p"]):
                d = unit(aim - o["p"])
                # where the ray enters the goal region
                s_in = None
                for s in np.arange(0, 600, 2.0):
                    if rect_contains(goal, o["p"] + d * s):
                        s_in = s
                        break
                if s_in is None:
                    continue
                dist_c = float((aim - o["p"]) @ d)
                s_end = min(s_in + 30.0, max(dist_c, s_in + 5.0))
                s_start = -(o["r"] + 30.0)
                a = o["p"] + d * (s_start - hd - 8)
                b = o["p"] + d * (s_end - hd)
                if not (in_ws(a, -30) and in_ws(b, -30)):
                    continue
                if self.path_blocked(a, b):
                    continue
                best = (a, b, d, s_start, s_end)
                break
            if best is None:
                log("no stroke for object at", np.round(o["p"], 1))
                done[k] = True
                continue
            a, b, d, s_start, s_end = best
            members = 0
            for j, q in enumerate(out):
                if done[j]:
                    continue
                rel = q["p"] - o["p"]
                along = rel @ d
                lat = rel @ perp(d)
                if s_start - 5 <= along <= s_end and abs(lat) < self.sl / 2 - 12:
                    done[j] = True
                    members += 1
            strokes.append({"a": a, "b": b, "d": d, "n": members, "o": o["p"]})
        return strokes, out

    def aims(self, goal, p):
        C = goal["c"]
        yield C
        ax, ay = goal["ax"], perp(goal["ax"])
        for s in (0.5, -0.5):
            yield C + s * goal["a"] * ax
            yield C + s * goal["b"] * ay

    def do_stroke(self, s):
        a, b, d = s["a"], s["b"], s["d"]
        cd = d
        r = self.transit(a, cd)
        if not r.get("moved", True) and not r.get("done", True):
            return False
        zl = self.zc + 25
        for k in range(2):
            r = self.move([a[0], a[1], self.zc - 3], cd, contact=True, label="lower")
            z = self.tcp()[2]
            if z > self.zc + 4:
                log("landed on something at", round(z - self.zc, 1), "mm above table contact; backing off")
                self.move([a[0], a[1], zl], cd)
                a = a - d * 20
                self.move([a[0], a[1], zl], cd)
                continue
            break
        zs = self.zc - 1.0
        r = self.move([b[0], b[1], zs], cd, label="sweep")
        if not r.get("done", True):
            log("sweep stopped:", r.get("why"))
        q = self.tcp()
        self.move([q[0], q[1], zl], cd, label="lift")
        w = self.st()["opening_mm"]
        if w < 8:
            log("sponge dropped!", w)
            self.held = False
            return False
        return True

    def choose_park(self, objs):
        tg = self.tg["region"]
        C = tg["c"]
        rad = math.hypot(tg["a"], tg["b"]) + max(self.sl, self.sw) / 2 + 30
        best = None
        for ang in np.linspace(0, 2 * math.pi, 24, endpoint=False):
            for rr in (rad, rad + 40):
                p = C + rr * np.array([math.cos(ang), math.sin(ang)])
                if not in_ws(p, -10):
                    continue
                if self.path_blocked(p, p + 0.1, margin=20):
                    continue
                dmin = min([np.linalg.norm(o["p"] - p) for o in objs] or [999])
                if dmin < max(self.sl, self.sw) / 2 + 30:
                    continue
                cur = self.tcp()[:2]
                score = -np.linalg.norm(p - cur) / 100.0 + min(dmin, 150) / 50.0
                if best is None or score > best[0]:
                    best = (score, p)
        return None if best is None else best[1]

    # ---- truth (practice only)
    def report(self, objs, tag):
        tr = self.r.truth()
        if not tr:
            return
        tru = [np.array(v[:2]) for k, v in tr.items() if k.startswith("w_obj_")]
        miss = sum(1 for t in tru if min([np.linalg.norm(o["p"] - t) for o in objs] or [999]) > 20)
        false = sum(1 for o in objs if min([np.linalg.norm(o["p"] - t) for t in tru] or [999]) > 20)
        j = self.r.judge() or {}
        log("[%s] objects seen %d true %d missed %d false %d | judge in %s/%s" % (tag, len(objs), len(tru), miss, false, j.get("in"), j.get("n")))

    def run(self):
        T = self.t
        log("hints", self.hints)
        m = self.look_all("0")
        objs = self.analyse(m, first=True, tag="0")
        log("table z", round(self.zt, 1), "objects", len(objs))
        if self.tg is None or self.sp is None:
            log("target or sponge not found", self.tg is not None, self.sp is not None)
            return
        log("target", self.tg["kind"], np.round(self.tg["region"]["c"], 1), "a/b", round(self.tg["region"]["a"]), round(self.tg["region"]["b"]))
        log("sponge", np.round(self.sp["rect"]["c"], 1), "L/S", round(self.sp["L"]), round(self.sp["S"]), "top", round(self.sp["top"], 1))
        self.report(objs, "first look")
        self.refine_sponge()
        if not self.pick_sponge():
            log("could not pick the sponge")
            return
        self.sl = float(self.sp["L"])
        self.sw = float(self.grip_w) + 2
        # touch the table where the sponge was (if it lay on the table) or near the first stroke
        strokes, out = self.plan(objs)
        tp = strokes[0]["a"] if strokes else self.sp["rect"]["c"]
        self.touch_table(tp)
        rnd = 0
        while True:
            rnd += 1
            strokes, out = self.plan(objs)
            log("round", rnd, "t", round(T(), 1), "outside", len(out), "strokes", len(strokes))
            if not strokes:
                break
            for k, s in enumerate(strokes):
                if T() > T_END:
                    break
                ok = self.do_stroke(s)
                log("  stroke", k, "n", s["n"], "ok", ok, "t", round(T(), 1))
                if not self.held:
                    break
            if T() > T_END - 60 or not self.held:
                break
            # put the sponge aside, look again
            park = self.choose_park(objs)
            if park is None:
                park = self.tcp()[:2]
            self.release(park, self.cur_cd())
            m = self.look_all(str(rnd), scene=False)
            objs = self.analyse(m, tag=str(rnd))
            self.report(objs, "round %d" % rnd)
            out = [o for o in objs if not rect_contains(self.tg["region"], o["p"], 8)]
            if not out:
                break
            if not self.regrasp():
                break
        if self.held:
            park = self.choose_park(objs)
            if park is not None and T() < 585:
                self.release(park, self.cur_cd())
        p = self.tcp()
        self.move([p[0], p[1], max(p[2], self.zt + 120)], self.cur_cd())
        log("end t", round(T(), 1))


def run(robot, task):
    Skill(robot, task).run()
