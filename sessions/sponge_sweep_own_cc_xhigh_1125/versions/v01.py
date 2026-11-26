import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
GX0, GX1, GY0, GY1 = -620.0, -20.0, -320.0, 520.0   # top-down map extent (mm); rows: x from GX1 down, cols: y from GY1 down
LOOK = (-345.0, 113.0, 520.0)

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
    """colour word(s) among the n words before index i"""
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
        if x in ("paper", "sheet", "card", "napkin", "mat", "towel", "cloth") and h["target_kind"] is None:
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


# ------------------------------------------------------------------ geometry helpers
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


def rect_contains(rect, p, margin=0.0):
    c, ax, a, b = rect["c"], rect["ax"], rect["a"], rect["b"]
    d = np.asarray(p[:2], float) - c
    lx = d @ ax
    ly = d @ np.array([-ax[1], ax[0]])
    return abs(lx) <= a - margin and abs(ly) <= b - margin


def rect_from_minarea(rr):
    (cj, ci), (w, h), ang = rr
    cx, cy = ij_to_xy(ci, cj)
    # direction of the rect's width side in image coords (j right, i down) -> table frame
    th = math.radians(ang)
    dj, di = math.cos(th), math.sin(th)
    ax = np.array([-di, -dj])      # i down = -x, j right = -y
    ax /= np.linalg.norm(ax)
    return {"c": np.array([cx, cy]), "ax": ax, "a": w / 2.0, "b": h / 2.0}


# ------------------------------------------------------------------ perception
class View:
    pass


def table_height(z):
    v = z[np.isfinite(z)]
    if v.size < 1000:
        return None
    lo, hi = np.percentile(v, [1, 60])
    hist, edges = np.histogram(v, bins=np.arange(lo - 2, hi + 2, 1.0))
    k = int(np.argmax(hist))
    sel = v[(v > edges[k] - 3) & (v < edges[k + 1] + 3)]
    return float(np.median(sel))


def perceive(robot, hints, tag, zt_prior=None):
    f = robot.look(["left_wrist"])["left_wrist"]
    P = f.points()
    zt = table_height(P[..., 2])
    if zt is None:
        zt = zt_prior if zt_prior is not None else 0.0
    if zt_prior is not None and abs(zt - zt_prior) > 6:
        zt = zt_prior
    H = int(GX1 - GX0)
    W = int(GY1 - GY0)
    ii, jj = np.mgrid[0:H, 0:W]
    gx = GX1 - ii.astype(np.float64)
    gy = GY1 - jj.astype(np.float64)
    pts = np.stack([gx, gy, np.full_like(gx, zt)], -1).reshape(-1, 3)
    uv, dep = f.project(pts)
    mu = uv[:, 0].reshape(H, W).astype(np.float32)
    mv = uv[:, 1].reshape(H, W).astype(np.float32)
    valid = (mu >= 2) & (mu < f.width - 2) & (mv >= 2) & (mv < f.height - 2) & (dep.reshape(H, W) > 0)
    img = cv2.remap(f.rgb, mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    # height map by scattering the (smoothed) depth
    dsm = cv2.medianBlur(np.nan_to_num(f.depth, nan=0).astype(np.float32), 5)
    good = np.isfinite(P[..., 2]) & (dsm > 0)
    Pz = P[..., 2]
    X, Y, Z = P[..., 0][good], P[..., 1][good], Pz[good]
    gi = np.round(GX1 - X).astype(int)
    gj = np.round(GY1 - Y).astype(int)
    ok = (gi >= 0) & (gi < H) & (gj >= 0) & (gj < W)
    hm = np.full((H, W), -50.0, np.float32)
    np.maximum.at(hm, (gi[ok], gj[ok]), (Z[ok] - zt).astype(np.float32))
    known = hm > -49
    hm2 = cv2.dilate(hm, np.ones((5, 5), np.uint8))
    hm2 = np.where(hm2 < -49, 0.0, hm2)
    hm2 = cv2.medianBlur(hm2.astype(np.float32), 5)
    v = View()
    v.f, v.zt, v.img, v.valid, v.hm, v.H, v.W = f, zt, img, valid, hm2, H, W
    v.lab = to_lab(img)
    return v


def analyse(v, hints, robot=None, show=False, tag=""):
    lab, valid, hm = v.lab, v.valid, v.hm
    out = {}
    # tall things
    tall = ((hm > 14) & valid).astype(np.uint8)
    tall = cv2.morphologyEx(tall, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(tall)
    talls = []
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < 600:
            continue
        m = lbl == k
        hmax = float(np.percentile(hm[m], 95))
        talls.append({"mask": m, "hmax": hmax, "area": int(stats[k, cv2.CC_STAT_AREA])})
    tall_all = np.zeros_like(tall, bool)
    for t in talls:
        tall_all |= t["mask"]
    # table colour
    base = valid & ~cv2.dilate(tall_all.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
    tcol = np.median(lab[base], axis=0)
    dg = cdist(lab, tcol)
    thr_g = max(16.0, 3.0 * float(np.median(dg[base])))
    fg = (dg > thr_g) & valid & ~tall_all
    out["tcol"] = tcol
    # ---- sponge
    sp = find_sponge(v, talls, hints, tcol)
    out["sponge"] = sp
    # ---- target
    tg = find_target(v, fg, hints, tcol, tall_all)
    out["target"] = tg
    # ---- small objects
    fill = img_fill(v.img, valid, tcol)
    bg = cv2.medianBlur(fill, 41)
    dl = cdist(lab, to_lab(bg))
    excl = cv2.dilate(tall_all.astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool)
    thr_l = max(16.0, 4.0 * float(np.median(dl[valid])))
    cand = (dl > thr_l) & valid & ~excl
    if tg is not None and tg.get("mask") is not None and tg["kind"] == "tape":
        cand &= ~cv2.dilate(tg["mask"].astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(cand)
    objs = []
    tgcol = tg["col"] if tg is not None else None
    for k in range(1, n):
        a = stats[k, cv2.CC_STAT_AREA]
        if a < 10 or a > 2500:
            continue
        m = lbl == k
        col = np.median(lab[m], axis=0)
        if cdist(col, tcol) < thr_g * 0.8:
            continue
        if tgcol is not None and cdist(col, tgcol) < 14:
            continue
        ci, cj = cent[k][1], cent[k][0]
        x, y = ij_to_xy(ci, cj)
        r = math.sqrt(a / math.pi)
        objs.append({"p": np.array([x, y]), "r": r, "area": int(a), "col": col})
    out["objects"] = objs
    out["obstacles"] = [t for t in talls if not (sp is not None and t is sp.get("comp"))]
    if show and robot is not None:
        vis = v.img.copy()
        for t in talls:
            cnts, _ = cv2.findContours(t["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(vis, cnts, -1, (255, 0, 0), 1)
        if tg is not None:
            draw_rect(vis, tg["rect"], (0, 255, 255))
        if sp is not None:
            draw_rect(vis, sp["rect"], (255, 0, 255))
        for o in objs:
            i, j = xy_to_ij(*o["p"])
            cv2.circle(vis, (int(j), int(i)), int(o["r"]) + 3, (255, 255, 255), 1)
        robot.show("map_" + tag, vis)
    return out


def img_fill(img, valid, tcol):
    out = img.copy()
    rgb = cv2.cvtColor(np.array([[tcol]], np.float32).astype(np.uint8), cv2.COLOR_LAB2RGB)[0, 0]
    out[~valid] = rgb
    return out


def draw_rect(vis, r, col):
    c, ax, a, b = r["c"], r["ax"], r["a"], r["b"]
    ay = np.array([-ax[1], ax[0]])
    pts = []
    for sx, sy in [(1, 1), (1, -1), (-1, -1), (-1, 1)]:
        p = c + sx * a * ax + sy * b * ay
        i, j = xy_to_ij(*p)
        pts.append([int(j), int(i)])
    cv2.polylines(vis, [np.array(pts, np.int32)], True, col, 1)


def find_sponge(v, talls, hints, tcol):
    best = None
    sc = hints.get("sponge_col")
    for t in talls:
        m = t["mask"]
        # top layer
        top = m & (v.hm > t["hmax"] - 10)
        top = cv2.morphologyEx(top.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        if top.sum() < 500:
            continue
        cnts, _ = cv2.findContours(top, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cnts, key=cv2.contourArea)
        rr = cv2.minAreaRect(cnt)
        w, h = rr[1]
        L, S = max(w, h), min(w, h)
        fillr = cv2.contourArea(cnt) / max(w * h, 1)
        score = 0.0
        score -= abs(L - 105) / 30.0 + abs(S - 58) / 15.0
        score += 2.0 * (fillr - 0.8)
        if not (60 < L < 160 and 35 < S < 90):
            score -= 3
        col = np.median(v.lab[top.astype(bool)], axis=0)
        if sc is not None:
            score += 2.0 - cdist(col, lab_of(sc[1])) / 25.0
        if best is None or score > best["score"]:
            rect = rect_from_minarea(rr)
            # long axis
            if rect["a"] < rect["b"]:
                rect = {"c": rect["c"], "ax": np.array([-rect["ax"][1], rect["ax"][0]]), "a": rect["b"], "b": rect["a"]}
            best = {"rect": rect, "top": t["hmax"], "score": score, "comp": t, "L": L, "S": S, "col": col}
    return best


def find_target(v, fg, hints, tcol, tall_all):
    m = cv2.morphologyEx(fg.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(m)
    best = None
    tc = hints.get("target_col")
    kind_h = hints.get("target_kind")
    for k in range(1, n):
        a = stats[k, cv2.CC_STAT_AREA]
        if a < 1500:
            continue
        mk = (lbl == k)
        cnts, _ = cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cnts, key=cv2.contourArea)
        rr = cv2.minAreaRect(cnt)
        w, h = rr[1]
        if min(w, h) < 60:
            continue
        fillr = a / max(w * h, 1)
        col = np.median(v.lab[mk], axis=0)
        kind = "paper" if fillr > 0.7 else ("tape" if fillr < 0.55 else "?")
        score = min(w, h) / 100.0
        if kind_h is not None:
            score += 2.0 if kind == kind_h else -2.0
        if tc is not None:
            score += 2.0 - cdist(col, lab_of(tc[1])) / 25.0
        if best is None or score > best["score"]:
            rect = rect_from_minarea(rr)
            tw = 0.0
            if kind == "tape":
                per = 2 * (w + h)
                # area = per*t - 4 t^2
                disc = per * per - 16 * a
                tw = (per - math.sqrt(max(disc, 0))) / 8.0
            best = {"rect": rect, "kind": kind, "score": score, "col": col, "mask": mk, "tw": tw, "fill": fillr}
    if best is not None and best["kind"] == "tape":
        r = best["rect"]
        tw = best["tw"]
        best["inner"] = {"c": r["c"], "ax": r["ax"], "a": r["a"] - tw, "b": r["b"] - tw}
        best["region"] = {"c": r["c"], "ax": r["ax"], "a": r["a"] - tw / 2, "b": r["b"] - tw / 2}
    elif best is not None:
        best["inner"] = best["rect"]
        best["region"] = best["rect"]
    return best


# ------------------------------------------------------------------ main
def run(robot, task):
    hints = parse_task(task)
    log("hints", hints)
    r = robot.move(ARM, LOOK, DOWN, (0, -1, 0))
    log("look move", r)
    v = perceive(robot, hints, "0")
    log("table z", round(v.zt, 1))
    res = analyse(v, hints, robot, show=True, tag="0")
    tr = robot.truth() or {}
    sp = res["sponge"]
    if sp:
        log("sponge", np.round(sp["rect"]["c"], 1), "L/S", round(sp["L"]), round(sp["S"]), "top", round(sp["top"], 1),
            "truth", np.round(tr.get("w_sponge", [0, 0, 0]), 1))
    tg = res["target"]
    if tg:
        log("target", tg["kind"], np.round(tg["rect"]["c"], 1), "a/b", round(tg["rect"]["a"]), round(tg["rect"]["b"]),
            "tw", round(tg["tw"], 1), "fill", round(tg["fill"], 2),
            "truth", np.round(tr.get("w_paper", tr.get("w_tape", [0, 0, 0])), 1))
    objs = res["objects"]
    tru = [np.array(vv[:2]) for k, vv in tr.items() if k.startswith("w_obj_")]
    log("objects found", len(objs), "true", len(tru))
    matched = 0
    for o in objs:
        d = min([np.linalg.norm(o["p"] - t) for t in tru] or [999])
        matched += d < 8
        if d >= 8:
            log("  false obj", np.round(o["p"], 1), o["area"], np.round(o["col"], 0), round(d, 1))
    for t in tru:
        d = min([np.linalg.norm(o["p"] - t) for o in objs] or [999])
        if d >= 8:
            log("  missed obj", np.round(t, 1), round(d, 1))
    log("matched", matched)
    log("obstacles", [(round(o["hmax"]), o["area"]) for o in res["obstacles"]])
