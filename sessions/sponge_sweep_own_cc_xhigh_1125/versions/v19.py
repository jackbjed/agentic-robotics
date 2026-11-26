import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
GX0, GX1, GY0, GY1 = -620.0, -20.0, -320.0, 520.0   # top-down map (1 mm/px); row i: x = GX1 - i, col j: y = GY1 - j
MH, MW = int(GX1 - GX0), int(GY1 - GY0)
LOOK = (-375.0, 110.0, 520.0)
WSX, WSY = (-485.0, -105.0), (-235.0, 455.0)        # where objects are looked for and the arm works
T_END = 560.0                                         # robot time after which no new stroke starts
ZMIN = 12.5                                           # lowest TCP z pointing down (fingertips >= -4 mm, robot frame)

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
        if x.startswith("scrub") and h.get("sponge_col2") is None:
            h["sponge_col2"] = colour_before(w, i, 3)
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


def reach_ok(p, m=0.0):
    """can the TCP pointing down be at p (low over the table)?"""
    x, y = p[0], p[1]
    if y < -230 + m or y > 520 - m:
        return False
    if y < -210:
        lo, hi = -330.0, -150.0
    elif y < -150:
        lo, hi = -400.0, -95.0
    elif y < 0:
        lo, hi = -465.0, -95.0
    elif y <= 120:
        lo, hi = -480.0, -95.0
    elif y < 270:
        f = (y - 120) / 150.0
        lo, hi = -480.0 - 100 * f, -95.0 - 55 * f
    else:
        lo, hi = -580.0, -150.0
    return lo + m <= x <= hi - m


def ring_height(m, rect, top):
    """height of what the sponge lies on: the ring around its rectangle"""
    outer = np.zeros((MH, MW), np.uint8)
    inner = np.zeros((MH, MW), np.uint8)
    cv2.fillPoly(outer, [rect_poly_ij(rect["c"], rect["ax"], rect["a"] + 30, rect["b"] + 30)], 1)
    cv2.fillPoly(inner, [rect_poly_ij(rect["c"], rect["ax"], rect["a"] + 3, rect["b"] + 3)], 1)
    ring = outer.astype(bool) & ~inner.astype(bool) & m.known
    h = m.hm[ring]
    n_all = h.size
    h = h[h < top - 8]
    if h.size < 50:
        return None
    # the highest flat level around it with enough pixels (a stand's rim, else the table)
    bins = np.arange(-6.0, max(top - 8.0, -4.0) + 2.0, 2.0)
    hist, edges = np.histogram(h, bins=bins)
    sm = np.convolve(hist, np.ones(3), mode="same")
    good = np.nonzero(sm >= 0.06 * n_all)[0]
    if good.size == 0:
        return float(np.percentile(h, 50))
    k = int(good.max())
    lo, hi = edges[max(k - 1, 0)], edges[min(k + 2, len(edges) - 1)]
    sel = h[(h >= lo) & (h <= hi)]
    return float(np.median(sel)) if sel.size else float(edges[k] + 1.0)


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


def table_plane(P, zt):
    """least-squares plane z = a x + b y + c through the points near the table level zt"""
    ok = np.isfinite(P[..., 2]) & (np.abs(P[..., 2] - zt) < 8) & (P[..., 0] > -650) & (P[..., 0] < 0)
    X, Y, Z = P[..., 0][ok], P[..., 1][ok], P[..., 2][ok]
    if X.size < 2000:
        return np.array([0.0, 0.0, zt])
    sel = np.linspace(0, X.size - 1, min(X.size, 30000)).astype(int)
    X, Y, Z = X[sel], Y[sel], Z[sel]
    A = np.stack([X, Y, np.ones_like(X)], 1)
    for _ in range(2):
        coef = np.linalg.lstsq(A, Z, rcond=None)[0]
        res = Z - A @ coef
        keep = np.abs(res) < 4
        A, Z = A[keep], Z[keep]
    if abs(coef[0]) > 0.05 or abs(coef[1]) > 0.05:
        return np.array([0.0, 0.0, zt])
    return coef


def scatter_heights(f, zt, smooth=5, grow=5, plane=None):
    d = f.depth
    P = f.points()
    if plane is None:
        plane = np.array([0.0, 0.0, zt])
    dsm = cv2.medianBlur(np.nan_to_num(d, nan=0).astype(np.float32), smooth)
    good = np.isfinite(P[..., 2]) & (dsm > 0) & (P[..., 2] < zt + 300)
    X, Y, Z = P[..., 0][good], P[..., 1][good], P[..., 2][good]
    Z = Z - (plane[0] * X + plane[1] * Y + plane[2]) + zt
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
    if vmax < 470:
        # the fingertips: the image's bottom corners
        fing = (mv >= vmax) & (mv < 468) & (mu >= 178) & (mu < fw.width - 178) & (dep.reshape(MH, MW) > 0)
        valid |= fing
    valid = cv2.erode(valid.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    img = cv2.remap(fw.rgb, mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    Pw = fw.points()
    plane = table_plane(Pw, zt)
    # height of what each map pixel's ray actually sees (tall things seen in perspective)
    zr = np.nan_to_num(Pw[..., 2] - (plane[0] * Pw[..., 0] + plane[1] * Pw[..., 1] + plane[2]), nan=0.0).astype(np.float32)
    zr = cv2.medianBlur(zr, 5)
    hr = cv2.remap(zr, mu, mv, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    hm, known = scatter_heights(fw, zt, plane=plane)
    if fs is not None:
        Ps = fs.points()
        zs = Ps[..., 2][np.isfinite(Ps[..., 2]) & (Ps[..., 0] < -60) & (Ps[..., 0] > -600)]
        zts = table_height(zs) if zs.size > 1000 else None
        if zts is not None:
            hs, ks = scatter_heights(fs, zts, smooth=5, grow=7, plane=table_plane(Ps, zts))
            hs = cv2.medianBlur(hs, 5)
            hs = np.where(hs > 22, hs, 0.0).astype(np.float32)      # the far camera: only clearly tall things
            use = ~cv2.dilate(known.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) & ks
            hm = np.where(use, hs, hm)
            known = known | ks
    m = Map()
    m.zt, m.img, m.valid, m.hm, m.known, m.hr = zt, img, valid, hm, known, hr
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
    # the table's colour as a smooth (quadratic) function of the place: lighting varies across the table
    ii, jj = np.nonzero(base)
    sel = np.linspace(0, ii.size - 1, min(ii.size, 20000)).astype(int)
    ii, jj = ii[sel].astype(np.float64), jj[sel].astype(np.float64)
    L = m.lab[ii.astype(int), jj.astype(int)].astype(np.float64)

    def feats(i, j):
        u = (i - MH / 2) / MH
        v = (j - MW / 2) / MW
        return np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], -1)

    A = feats(ii, jj)
    keep = np.ones(len(ii), bool)
    coef = None
    for _ in range(3):
        coef = np.linalg.lstsq(A[keep], L[keep], rcond=None)[0]
        res = cdist(L, A @ coef)
        s = float(np.median(res[keep])) * 1.4826 + 1e-3
        keep = res < max(3 * s, 6.0)
    I, J = np.mgrid[0:MH, 0:MW]
    model = (feats(I.astype(np.float64), J.astype(np.float64)).reshape(-1, 6) @ coef).reshape(MH, MW, 3).astype(np.float32)
    dg = cdist(m.lab, model)
    med = float(np.median(dg[base]))
    thr_g = max(16.0, 3.0 * med)
    tcol = np.median(L[keep], axis=0).astype(np.float32)
    return tcol, model, dg, thr_g, med


def find_sponge(m, talls, hints, near=None):
    best = None
    sc = hints.get("sponge_col")
    cands = []
    for t in talls:
        if t["hmax"] > 140:
            continue
        top = t["mask"] & (m.hm > t["hmax"] - 10)
        cands.append((top, t["hmax"], t, 0.0))
    scols = [lab_of(c[1]) for c in (sc, hints.get("sponge_col2")) if c is not None]
    if scols:
        # also by the colour(s) the task names (where the depth is poor)
        cm = np.zeros((MH, MW), bool)
        for sl_ in scols:
            cm |= cdist(m.lab, sl_) < 28
        cm &= m.valid
        cm = cv2.morphologyEx(cm.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(cm)
        for k in range(1, n):
            if stats[k, cv2.CC_STAT_AREA] < 2000:
                continue
            mk = lbl == k
            hk = m.hm[mk & m.known]
            hmax = float(np.percentile(hk, 90)) if hk.size > 200 else 30.0
            if hmax < 12 or hmax > 140:
                continue
            cands.append((mk, hmax, {"mask": mk, "hmax": hmax, "area": int(mk.sum())}, -0.5))
    for top, hmax, t, bonus in cands:
        top = cv2.morphologyEx(top.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        if top.sum() < 500:
            continue
        cnts, _ = cv2.findContours(top, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cnts, key=cv2.contourArea)
        rr = cv2.minAreaRect(cnt)
        w, h = rr[1]
        L, S = max(w, h), min(w, h)
        fillr = cv2.contourArea(cnt) / max(w * h, 1)
        score = bonus - (abs(L - 105) / 30.0 + abs(S - 55) / 15.0) + 2.0 * (fillr - 0.8)
        if not (60 < L < 170 and 32 < S < 95):
            score -= 3
        if L < 1.2 * S:
            score -= 3          # a sponge is longer than wide: not a ball, a mug, ...
        col = np.median(m.lab[top.astype(bool)], axis=0)
        if scols:
            score += 3.0 - min(float(cdist(col, s_)) for s_ in scols) / 20.0
        rect = rect_from_minarea(rr)
        if rect["a"] < rect["b"]:
            rect = {"c": rect["c"], "ax": perp(rect["ax"]), "a": rect["b"], "b": rect["a"]}
        if near is not None:
            score -= np.linalg.norm(rect["c"] - near) / 40.0
        if not in_ws(rect["c"], -40):
            score -= 3
        if best is None or score > best["score"]:
            best = {"rect": rect, "top": hmax, "score": score, "comp": t, "L": L, "S": S, "col": col}
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
            if kind not in (kind_h, "?"):
                continue
            score += 2.0 if kind == kind_h else -1.5
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


def find_objects(m, tcol, tmod, thr_g, tg, excl, known_obst=None):
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
    bigs = []
    big = np.zeros((MH, MW), bool)
    tgcol = tg["col"] if tg is not None else None
    for k in range(1, n):
        a = stats[k, cv2.CC_STAT_AREA]
        if a < 10:
            continue
        ext = max(stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT])
        mk = lbl == k
        col = np.median(m.lab[mk], axis=0)
        bcol = np.median(bgl[mk], axis=0)
        if float(cdist(col, np.median(tmod[mk], axis=0))) < thr_g * 0.8:
            continue
        if tgcol is not None and float(cdist(col, tgcol)) < 14:
            continue
        # shadow: same chroma as the background, darker but not black
        dab = math.hypot(col[1] - bcol[1], col[2] - bcol[2])
        if dab < 7 and col[0] < bcol[0] and col[0] > 0.45 * bcol[0]:
            continue
        ci, cj = cent[k][1], cent[k][0]
        x, y = ij_to_xy(ci, cj)
        o = {"p": np.array([x, y]), "r": math.sqrt(a / math.pi), "area": int(a), "col": col}
        if a > 700 or ext > 48:
            # a clump of objects, or something else that is low and long (a pen, a lid, ...)
            w_min = min(stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT])
            o["mask"] = mk
            o["thin"] = a / max(ext, 1) < 14 and ext > 60
            bigs.append(o)
            continue
        objs.append(o)
    had = len(objs) > 0
    ocol = np.median(np.array([o["col"] for o in objs]), axis=0) if had else None
    for o in bigs:
        same = had and float(cdist(o["col"], ocol)) < 20
        if known_obst is not None:
            # after the first look: a clump (or a row) of objects unless it is where an obstacle was seen
            is_obj = not (o["mask"] & known_obst).any()
        else:
            is_obj = same or (not had and not o["thin"])
        if is_obj:
            # a clump: represent it by points 15 mm apart, so that the part of it outside the target is seen
            ii_, jj_ = np.nonzero(o["mask"])
            cells = {}
            for a_, b_ in zip(ii_ // 15, jj_ // 15):
                cells[(int(a_), int(b_))] = cells.get((int(a_), int(b_)), 0) + 1
            for (ci_, cj_), cnt in cells.items():
                if cnt < 15:
                    continue
                sel = (ii_ // 15 == ci_) & (jj_ // 15 == cj_)
                x, y = ij_to_xy(ii_[sel].mean(), jj_[sel].mean())
                objs.append({"p": np.array([x, y]), "r": 7.0, "area": int(cnt), "col": o["col"]})
        else:
            big |= o["mask"]
    return objs, big


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
        self.sweeps = 0
        self.grip_g = None
        self.tried = []
        self.truth0 = robot.truth() or {}

    # ---- motion helpers
    def st(self):
        return self.r.state(ARM)

    def tcp(self):
        return np.array(self.st()["tcp_mm"], float)

    def move(self, p, cd, contact=False, label=""):
        """straight moves; a long move that also changes height goes up first / across / down"""
        p = [float(p[0]), float(p[1]), max(float(p[2]), ZMIN)]
        if not contact:
            cur = self.tcp()
            if math.hypot(p[0] - cur[0], p[1] - cur[1]) > 40 and abs(p[2] - cur[2]) > 60:
                zm = max(min(p[2], 380.0), min(cur[2], 380.0))
                if abs(cur[2] - zm) > 1:
                    r = self.mv([cur[0], cur[1], zm], cd, False, label + "/vertical")
                    if r.get("moved", True) and not r.get("done", False):
                        return r
                r = self.mv([p[0], p[1], zm], cd, False, label + "/across")
                if r.get("moved", True) and not r.get("done", False):
                    return r
        return self.mv(p, cd, contact, label)

    def mv(self, p, cd, contact=False, label=""):
        res = self.r.move(ARM, [float(p[0]), float(p[1]), max(float(p[2]), ZMIN)], DOWN, [float(cd[0]), float(cd[1]), 0.0], contact)
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
        if dn[0] > 0.5:
            # the wrist ended up the other way round (camera on the far side): turn it back
            for ang in (180.0, -180.0):
                r = self.r.turn(ARM, [float(v) for v in self.tcp()], (0.0, 0.0, 1.0), ang)
                if r.get("moved", True) and r.get("done", True):
                    break
            fr = self.r.look(cams)
            fw = fr["left_wrist"]
            dn = np.asarray(fw.R, float)[:, 1]
            log("wrist turned for the look; image down now", np.round(dn, 2))
        want = np.array([-285.0 + 45.0 * dn[0], 110.0])
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
        tcol, tmod, dg, thr_g, med = table_colour(m, tall_all)
        seen_tall = cv2.dilate((m.hr > 15).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
        thr_t = max(9.0, 2.0 * med)
        fg = (dg > thr_t) & m.valid & ~tall_all & ~seen_tall
        if self.hints.get("target_col") is not None:
            hl = lab_of(self.hints["target_col"][1])
            fg |= (cdist(m.lab, hl) < 0.6 * dg) & (dg > 0.7 * thr_t) & m.valid & ~tall_all & ~seen_tall
        fg = cv2.morphologyEx(fg.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
        tg = find_target(m, fg, self.hints)
        if tg is not None:
            if self.tg is None or first:
                self.tg = tg
            else:
                old, new = self.tg["region"], tg["region"]
                ratio = new["a"] * new["b"] / max(old["a"] * old["b"], 1.0)
                same = tg["kind"] == self.tg["kind"] and 0.6 < ratio < 1.6 and np.linalg.norm(new["c"] - old["c"]) < 100
                if same:
                    self.tg = tg
                else:
                    log("target seen differently now (", tg["kind"], np.round(new["c"], 0), "): keeping the first one")
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
        excl = cv2.dilate(tall_all.astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool) | seen_tall
        objs, big = find_objects(m, tcol, tmod, thr_g, self.tg, excl, None if first else self.obst)
        if first:
            if self.tg is not None:
                cut = np.zeros((MH, MW), np.uint8)
                R = self.tg["rect"]
                cv2.fillPoly(cut, [rect_poly_ij(R["c"], R["ax"], R["a"] + 10, R["b"] + 10)], 1)
                big &= ~cut.astype(bool)
            self.obst |= big
            log("low obstacles (mm2)", int(big.sum()))
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
            sup = ring_height(m, sp2["rect"], sp2["top"])
            sp2["support"] = sup if sup is not None else max(0.0, sp2["top"] - 25)
            log("sponge refined", np.round(sp2["rect"]["c"], 1), "L/S", round(sp2["L"]), round(sp2["S"]), "top", round(sp2["top"], 1),
                "support", sup and round(sup, 1), "(was", np.round(c, 1), round(sp["top"], 1), ")")
            self.show(m, "sponge", sp=sp2, talls=talls)
            self.sp = sp2
        else:
            log("sponge refine failed", sp2 and np.round(sp2["rect"]["c"], 1))

    def grasp_sponge(self, c, short_ax, top_abs, sup_abs):
        self.r.set_gripper(ARM, 80)
        cd = short_ax
        r = self.move([c[0], c[1], top_abs + 50], cd, label="above sponge")
        if not r.get("moved", True):
            return False
        # the pads' centre (the TCP) g mm below the sponge top: shallow on a low table, so that the sponge sticks out
        # below the fingertips far enough (the arm may not go below z = 0 even if the table is lower);
        # fingertips at least 5 mm above what it lies on
        # the pads' centre (TCP) 10 mm below the top, the fingertips (16 mm lower) at least 3 mm above what it lies
        # on; on a low table shallower, so that the sponge sticks out below the fingertips (the arm may not go
        # below z = 0 even where the table is lower)
        if self.grip_g is not None:
            z_grasp = top_abs - self.grip_g
        elif self.zt > -2:
            z_grasp = top_abs - 10
        else:
            T = top_abs - sup_abs
            z_grasp = top_abs - float(np.clip(T - 15.5 + self.zt, 5, 10))
        z_grasp = max(z_grasp, sup_abs + 18.5, ZMIN)
        if z_grasp > top_abs - 6:
            # the support seen is implausibly high (a thin sponge would not be in the world here): grip anyway
            z_grasp = max(top_abs - 8, ZMIN)
        r = self.move([c[0], c[1], z_grasp], cd, contact=True, label="down to sponge")
        zs = self.tcp()[2]
        if r.get("why") == "contact" and zs > z_grasp + 5:
            log("a finger landed on something at", round(zs, 1), "(wanted", round(z_grasp, 1), ")")
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
        self.exp_bottom = zs - sup_abs        # expected: the sponge's bottom this far below the TCP
        p = self.tcp()
        self.move([p[0], p[1], max(top_abs + 40, p[2] + 40)], cd, label="lift sponge")
        w2 = self.st()["opening_mm"]
        if w2 < 10:
            log("sponge lost on lift", w2)
            return False
        self.held = True
        return True

    def pick_sponge(self):
        for attempt, off in enumerate((0.0, 6.0, -6.0)):
            sp = self.sp
            rect = sp["rect"]
            sax = perp(rect["ax"])
            ok = self.grasp_sponge(rect["c"] + off * sax, sax, self.zt + sp["top"], self.zt + sp.get("support", sp["top"] - 25))
            if ok:
                self.sp_ax = rect["ax"]
                return True
            # look again from above the sponge
            self.refine_sponge()
        return False

    def touch_table(self, objs):
        avoid = []
        for attempt in range(3):
            p_xy = self.choose_park(objs, avoid)
            if p_xy is None:
                p_xy = self.tcp()[:2]
            avoid.append(np.array(p_xy, float))
            cd = self.cur_cd()
            p = self.tcp()
            if self.path_blocked(p[:2], p_xy, margin=25):
                z = min(350.0, max(p[2], self.zt + self.obst_h + 90))
                self.move([p[0], p[1], z], cd, label="up for touch")
                p = self.tcp()
            self.move([p_xy[0], p_xy[1], p[2]], cd, label="to touch")
            r = self.move([p_xy[0], p_xy[1], ZMIN], cd, contact=True, label="touch")
            z = self.tcp()[2]
            if r.get("why") == "contact" and z - self.zt > self.exp_bottom + 10:
                log("touched something %.0f mm above the table there: another place" % (z - self.zt - self.exp_bottom))
                self.move([p_xy[0], p_xy[1], z + 30], cd)
                continue
            break
        if r.get("why") != "contact":
            log("touch: no contact", r)
            if z < ZMIN + 1.5 and self.grip_g is None:
                # the sponge does not reach the table from the arm's lowest: let it go here and grip it higher
                log("the sponge does not reach the table: grip it higher up")
                self.r.set_gripper(ARM, 80)
                self.held = False
                self.move([p_xy[0], p_xy[1], z + 60], cd)
                th = self.sp["top"] - self.sp.get("support", self.sp["top"] - 30)
                self.sp = {"rect": {"c": np.array(p_xy, float), "ax": self.sp["rect"]["ax"], "a": self.sp["rect"]["a"],
                                    "b": self.sp["rect"]["b"]}, "top": max(th, 20.0), "L": self.sp["L"], "S": self.sp["S"]}
                self.refine_sponge()
                # now it lies on the table: its top tells its thickness
                T = self.sp["top"]
                self.grip_g = float(np.clip(T - 15.5 + min(self.zt, -2.0), 4, 12))
                log("sponge thickness", round(T, 1), "-> grip", round(self.grip_g, 1), "mm below its top")
                if self.pick_sponge():
                    return self.touch_table(objs)
                return False
        self.zc = z
        log("table touch: tcp z", round(z, 1), "table", round(self.zt, 1), "sponge bottom below tcp", round(z - self.zt, 1),
            "force", np.round(self.st()["force_n"], 1))
        self.move([p_xy[0], p_xy[1], z + 25], cd)
        return True

    def cur_cd(self):
        return np.array(self.st()["closing_dir"], float)[:2]

    def release(self, objs):
        """put the held sponge down at a free place near the target"""
        cd = self.cur_cd()
        avoid = []
        for attempt in range(4):
            p_xy = self.choose_park(objs, avoid)
            if p_xy is None:
                p_xy = self.tcp()[:2]
            avoid.append(p_xy)
            r = self.transit(p_xy, cd)
            if not r.get("done", False):
                continue
            r = self.move([p_xy[0], p_xy[1], self.zc + 1.5], cd, label="put down")
            if not r.get("done", False) and r.get("why") != "contact":
                continue
            break
        self.r.set_gripper(ARM, 80)
        q = self.tcp()
        self.park = {"p": q[:2].copy(), "cd": unit(cd), "z": q[2]}
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
        a = np.asarray(a, float)
        b = np.asarray(b, float)
        if np.linalg.norm(b - a) < 1:
            r = max(self.sl, self.sw) / 2 + margin
            poly = rect_poly_ij(a, np.array([1.0, 0.0]), r, r)
        else:
            d = unit(b - a)
            L = np.linalg.norm(b - a)
            poly = rect_poly_ij((a + b) / 2, d, L / 2 + self.sw / 2 + margin, self.sl / 2 + margin)
        j0, i0 = np.maximum(poly.min(axis=0), 0)
        j1, i1 = poly.max(axis=0) + 1
        j1, i1 = min(j1, MW), min(i1, MH)
        if j1 <= j0 or i1 <= i0:
            return False
        sub = self.obst[i0:i1, j0:j1]
        if not sub.any():
            return False
        mk = np.zeros(sub.shape, np.uint8)
        cv2.fillPoly(mk, [poly - np.array([j0, i0])], 1)
        return bool((mk.astype(bool) & sub).any())

    def transit(self, p_xy, cd):
        """move the held sponge (or the hand) to above p_xy at the low transit height, over obstacles if needed"""
        p = self.tcp()
        zl = self.zc + 25
        if self.path_blocked(p[:2], p_xy, margin=25):
            zh = min(350.0, self.zt + self.obst_h + (self.zc - self.zt) + 35)
            self.move([p[0], p[1], max(zh, p[2])], cd, label="up over")
            r = self.move([p_xy[0], p_xy[1], max(zh, p[2])], cd, label="over")
            if not r.get("done", False):
                return r
            return self.move([p_xy[0], p_xy[1], zl], cd, contact=True, label="down after over")
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
        # something a stroke already went for and that is still there: not a loose object (a mark, a stain)
        out = [o for o in out if not any(np.linalg.norm(o["p"] - q) < 12 for q in self.tried)]
        out.sort(key=lambda o: -np.linalg.norm(o["p"] - C))
        done = [False] * len(out)
        strokes = []
        hd = self.sw / 2.0
        for k, o in enumerate(out):
            if done[k]:
                continue
            best = None
            why = {"reach": 0, "blocked": 0}
            aims = list(self.aims(goal, o["p"]))
            tries = [(40.0, q, 0.0) for q in aims] + [(c, q, f * self.sl) for c in (40.0, 22.0) for q in aims
                                                       for f in ((0.0, 0.3, -0.3) if c < 30 else (0.3, -0.3))]
            for clear, aim, lat in tries:
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
                s_start = -(o["r"] + clear)
                sh = perp(d) * lat          # the sponge off-centre: the object in front of one end of its face
                a = o["p"] + d * (s_start - hd - 8) + sh
                b = o["p"] + d * (s_end - hd) + sh
                if not (reach_ok(a, 5) and reach_ok(b, 5)):
                    why["reach"] += 1
                    continue
                if self.path_blocked(a, b, margin=8):
                    why["blocked"] += 1
                    continue
                s_reg = s_in
                for s in np.arange(0, s_in + 2, 2.0):
                    if rect_contains(R, o["p"] + d * s):
                        s_reg = s
                        break
                if tg["kind"] != "tape":
                    # a loose sheet: push them only a little way onto it, or the sheet is pushed along
                    depth = 45.0 if not self.tried else 32.0
                    s_end = min(s_reg + depth, max(dist_c, s_reg + 5.0))
                    b = o["p"] + d * (s_end - hd) + sh
                b1 = o["p"] + d * (max(s_reg - 15.0, s_start - 8) - hd) + sh
                best = (a, b, d, s_start, s_end, b1, lat)
                break
            if best is None:
                log("no stroke for object at", np.round(o["p"], 1), why)
                done[k] = True
                continue
            a, b, d, s_start, s_end, b1, lat0 = best
            members = 0
            for j, q in enumerate(out):
                if done[j]:
                    continue
                rel = q["p"] - o["p"]
                along = rel @ d
                lat = rel @ perp(d) - lat0
                if s_start - 5 <= along <= s_end and abs(lat) < self.sl / 2 - 12:
                    done[j] = True
                    members += 1
            strokes.append({"a": a, "b": b, "d": d, "n": members, "o": o["p"], "b1": b1})
        return strokes, out

    def aims(self, goal, p):
        """points of the goal region to push towards: the centre first, then others by how little they turn"""
        C = goal["c"]
        yield C
        ax, ay = goal["ax"], perp(goal["ax"])
        d0 = unit(C - p)
        pts = []
        for fx in np.linspace(-0.8, 0.8, 5):
            for fy in np.linspace(-0.8, 0.8, 5):
                q = C + fx * goal["a"] * ax + fy * goal["b"] * ay
                pts.append((-(unit(q - p) @ d0), q))
        pts.sort(key=lambda t: t[0])
        for _, q in pts:
            yield q

    def do_stroke(self, s):
        a, b, d = s["a"], s["b"], s["d"]
        cd = d
        r = self.transit(a, cd)
        zl = self.zc + 25
        if not r.get("done", False):
            log("transit to stroke failed:", r.get("why"))
            q = self.tcp()
            self.move([q[0], q[1], max(q[2], zl)], cd)
            return True
        r = self.move([a[0], a[1], self.zc - 3], cd, contact=True, label="lower")
        z = self.tcp()[2]
        if z > self.zc + 4:
            log("landed on something at", round(z - self.zc, 1), "mm above table contact: skip, mark it")
            self.move([a[0], a[1], zl], cd)
            mk = np.zeros((MH, MW), np.uint8)
            i, j = xy_to_ij(a[0], a[1])
            cv2.circle(mk, (int(j), int(i)), 30, 1, -1)
            self.obst |= mk.astype(bool)
            return True
        zs = self.zc - 0.5
        if self.tg["kind"] != "tape":
            # a loose sheet: slide the sponge over it without pressing, or it is pushed along
            b1 = s["b1"]
            if (b1 - a) @ d > 2:
                r = self.move([b1[0], b1[1], zs], cd, label="sweep")
            r = self.move([b[0], b[1], self.zc + 3.0], cd, label="sweep over sheet")
        else:
            r = self.move([b[0], b[1], zs], cd, label="sweep")
        if not r.get("done", True):
            log("sweep stopped:", r.get("why"))
        if self.sweeps < 3:
            self.sweeps += 1
            log("   force after sweep", np.round(self.st()["force_n"], 1))
        q = self.tcp()
        self.move([q[0], q[1], zl], cd, label="lift")
        w = self.st()["opening_mm"]
        if w < 8:
            log("sponge dropped!", w)
            self.held = False
            return False
        return True

    def choose_park(self, objs, avoid=()):
        """a free place on the table for the sponge: off the target, clear of objects and obstacles, reachable"""
        R = self.tg["region"]
        rs = max(self.sl, self.sw) / 2
        dist_obst = cv2.distanceTransform((~self.obst).astype(np.uint8), cv2.DIST_L2, 5)
        cur = self.tcp()[:2]
        best = None
        for x in np.arange(-460.0, -105.0, 15.0):
            for y in np.arange(-215.0, 445.0, 15.0):
                p = np.array([x, y])
                if not reach_ok(p, 15):
                    continue
                if any(np.linalg.norm(p - q) < 40 for q in avoid):
                    continue
                lx, ly = rect_local(R, p)
                dt = math.hypot(max(abs(lx) - R["a"], 0.0), max(abs(ly) - R["b"], 0.0))
                if dt < rs + 25:
                    continue
                i, j = xy_to_ij(x, y)
                if dist_obst[int(i), int(j)] < rs + 20:
                    continue
                dmin = min([np.linalg.norm(o["p"] - p) for o in objs] or [999])
                if dmin < rs + 35:
                    continue
                # near where the arm is, away from the objects, not so near the target that it hides some
                score = -np.linalg.norm(p - cur) / 150.0 + min(dmin, 150) / 50.0 + min(dt, 110) / 60.0 - dt / 300.0
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
        # touch the table where the sponge was (if it lay on the table) or at a free place
        if not self.touch_table(objs):
            log("could not get the sponge onto the table")
            return
        self.sl = float(self.sp["L"])
        self.sw = float(self.grip_w) + 2
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
                self.tried.append(np.array(s["o"], float))
                log("  stroke", k, "n", s["n"], "ok", ok, "t", round(T(), 1))
                if not self.held:
                    break
            if T() > T_END - 60 or not self.held:
                break
            # put the sponge aside, look again
            self.release(objs)
            m = self.look_all(str(rnd), scene=True)
            objs = self.analyse(m, tag=str(rnd))
            self.report(objs, "round %d" % rnd)
            out = [o for o in objs if not rect_contains(self.tg["region"], o["p"], 8)]
            if not out:
                break
            if not self.regrasp():
                break
        if self.held and T() < 580:
            self.release(objs)
        p = self.tcp()
        self.move([p[0], p[1], max(p[2], self.zt + 120)], self.cur_cd())
        log("end t", round(T(), 1))


def run(robot, task):
    Skill(robot, task).run()
