import math
import numpy as np

# ---------------------------------------------------------------- catalogue
TABLES = [("dark grey", (70, 70, 72)), ("light grey", (165, 165, 165)), ("white", (225, 225, 220)),
          ("light wood", (200, 165, 120)), ("dark wood", (105, 72, 50)), ("green", (60, 105, 70)),
          ("blue", (60, 80, 125)), ("beige", (215, 200, 170)), ("black", (32, 32, 34))]

# kind: (text, shape, half-size ranges (mm), colours, density g/cm3, rolls, count range)
KINDS = {
    "lentils": ("lentils", "lens", [(2.6, 3.6), (1.1, 1.4)],
                [(205, 105, 45), (125, 85, 45), (115, 125, 60), (190, 60, 40)], 1.3, False, (14, 28)),
    "beans": ("beans", "ellipsoid", [(6.0, 8.0), (3.5, 4.5), (3.0, 3.6)],
              [(120, 30, 35), (232, 226, 208), (40, 32, 32), (165, 115, 80)], 1.2, False, (8, 18)),
    "chickpeas": ("chickpeas", "sphere", [(4.0, 5.0)], [(215, 180, 120)], 1.2, True, (8, 18)),
    "peas": ("dried peas", "sphere", [(3.0, 4.0)], [(120, 160, 60), (220, 190, 70)], 1.3, True, (10, 22)),
    "coffee": ("coffee beans", "ellipsoid", [(5.0, 6.0), (3.8, 4.5), (2.6, 3.0)], [(85, 48, 28)], 0.7, False, (10, 22)),
    "cereal": ("cereal pieces", "lens", [(5.0, 7.0), (2.0, 3.0)], [(222, 170, 90), (190, 120, 60)], 0.4, False, (8, 16)),
    "pasta": ("pieces of pasta", "capsule", [(3.0, 4.0), (4.0, 6.0)], [(236, 205, 110)], 0.8, False, (8, 16)),
    "beads": ("beads", "sphere", [(4.0, 6.0)], "multi", 1.1, True, (8, 16)),
    "crumbs": ("crumbs", "box", [(2.0, 5.0), (2.0, 5.0), (1.5, 3.0)], [(200, 150, 90), (160, 110, 60)], 0.4, False, (12, 24)),
}
BRIGHT = [(220, 40, 40), (40, 160, 60), (40, 80, 210), (240, 200, 30), (240, 120, 30), (150, 50, 180), (240, 240, 240)]

SPONGE_COLS = [("yellow", (235, 205, 60)), ("green", (70, 170, 80)), ("blue", (60, 120, 210)), ("pink", (235, 130, 160)),
               ("orange", (240, 140, 50)), ("purple", (140, 90, 180)), ("light blue", (140, 190, 230)), ("grey", (140, 140, 140))]
SCRUB_COLS = [("green", (40, 120, 60)), ("dark blue", (30, 50, 110)), ("white", (230, 230, 230)), ("grey", (90, 90, 90))]
PAPER_COLS = [("white", (240, 240, 236)), ("yellow", (245, 230, 140)), ("pink", (245, 185, 195)),
              ("light blue", (170, 210, 240)), ("light green", (180, 230, 170)), ("orange", (245, 160, 80)),
              ("blue", (100, 145, 225)), ("green", (110, 195, 115)), ("red", (215, 80, 80))]
TAPE_COLS = [("blue", (40, 90, 200)), ("red", (200, 40, 40)), ("yellow", (235, 200, 40)), ("green", (40, 160, 70)),
             ("black", (25, 25, 25)), ("white", (235, 235, 235)), ("orange", (240, 130, 30))]
DISTRACTOR_COLS = [(200, 50, 50), (50, 90, 190), (230, 230, 225), (60, 150, 80), (230, 180, 40), (30, 30, 30), (150, 100, 60)]

WS = (-440.0, -150.0, -180.0, 400.0)   # x0, x1, y0, y1: where the work happens (mm)


def _c(rgb, a=1.0):
    return "%.3f %.3f %.3f %.2f" % (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, a)


def _dist(a, b):
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def _cdiff(a, b):
    # colour difference as rendered (the simulator's lighting brightens colours and saturates light ones)
    ra = np.minimum(255.0, 1.3 * np.asarray(a, float))
    rb = np.minimum(255.0, 1.3 * np.asarray(b, float))
    return float(np.linalg.norm(ra - rb))


def _quat(yaw):
    return "%.6f 0 0 %.6f" % (math.cos(yaw / 2), math.sin(yaw / 2))


def _pick_contrast(rng, cols, avoid, min_d=80):
    ok = [c for c in cols if all(_cdiff(c[1], a) >= min_d for a in avoid)]
    if not ok:
        # none contrasts enough: the most contrasting one
        ok = [max(cols, key=lambda c: min(_cdiff(c[1], a) for a in avoid))]
    return ok[int(rng.integers(len(ok)))]


def _in_rect(p, c, yaw, a, b, m=0.0):
    d = np.asarray(p[:2], float) - np.asarray(c[:2], float)
    ca, sa = math.cos(yaw), math.sin(yaw)
    lx = ca * d[0] + sa * d[1]
    ly = -sa * d[0] + ca * d[1]
    return abs(lx) <= a + m and abs(ly) <= b + m


def _build(scene, rng):
    info = {}
    # ------------------------------------------------------------ table
    # table top -28..35 mm; the arm's limits are fixed at z = 0, so on a low table only a thick sponge can reach it
    th = float(rng.uniform(-24, 35))
    tname, trgb = TABLES[int(rng.integers(len(TABLES)))]
    table = {"height_mm": round(th, 1), "rgb": list(trgb), "pattern": "plain"}
    if rng.random() < 0.3:
        pat = ["wood", "speckle", "blotches"][int(rng.integers(3))]
        k = rng.uniform(0.85, 1.12)
        table.update({"pattern": pat, "rgb2": [int(min(255, v * k)) for v in trgb], "angle_deg": float(rng.uniform(0, 180))})
        if pat == "speckle":
            table["size_mm"] = float(rng.uniform(2, 5))
        elif pat == "blotches":
            table["size_mm"] = float(rng.uniform(35, 85))
    scene["table"] = table
    info["table_h"] = th
    xml = []
    foot = []          # footprints (x, y, r) taken

    def free(p, r):
        if not (WS[0] + r * 0.3 <= p[0] <= WS[1] - r * 0.3 and WS[2] + r * 0.3 <= p[1] <= WS[3] - r * 0.3):
            return False
        return all(_dist(p, f[:2]) >= r + f[2] for f in foot)

    # ------------------------------------------------------------ target
    for _ in range(200):
        tc = np.array([rng.uniform(-370, -230), rng.uniform(-90, 290)])
        if not (tc[0] < -400 and tc[1] < -250):
            break
    tyaw = float(rng.uniform(-math.pi / 2, math.pi / 2))
    kind_t = "paper" if rng.random() < 0.5 else "tape"
    if kind_t == "paper":
        sizes = [(105, 148), (148, 210), (100, 100), (150, 150), (125, 175)]
        w, l = sizes[int(rng.integers(len(sizes)))]
        a, b = l / 2.0, w / 2.0
        a = min(a, 118.0)
        pname, prgb = _pick_contrast(rng, PAPER_COLS, [trgb, table.get("rgb2", trgb)], 95)
        u = (rng.uniform(0.35, 0.6) - 0.35) / 0.25
        gsm = 80.0 + 80.0 * u                  # office paper .. thin card (g/m2)
        pmu = 0.4 + 0.3 * u                    # paper on the table: friction 0.4 .. 0.7
        xml.append('<body name="w_paper" pos="%.4f %.4f 0.0003" quat="%s"><freejoint/>'
                   '<geom name="w_paper_g" type="box" size="%.4f %.4f 0.00025" mass="%.4f" rgba="%s" friction="%.2f 0.005 0.0001" priority="1"/></body>'
                   % (tc[0] / 1000, tc[1] / 1000, _quat(tyaw), a / 1000, b / 1000, gsm / 1000 * (4 * a * b / 1e6),
                      _c(prgb), pmu))
        info["target"] = {"kind": "paper", "body": "w_paper", "a": a, "b": b, "yaw": tyaw, "c": tc.tolist()}
        tdesc = "the %s sheet of paper" % pname
        mcol = prgb
        rt = math.hypot(a, b)
    else:
        a = float(rng.uniform(65, 115))
        b = float(rng.uniform(65, 115))
        wt = float(rng.uniform(15, 25))
        cname, crgb = _pick_contrast(rng, TAPE_COLS, [trgb, table.get("rgb2", trgb)], 90)
        g = []
        for sx, sy, hx, hy in [(0, b + wt / 2, a + wt, wt / 2), (0, -(b + wt / 2), a + wt, wt / 2),
                               (a + wt / 2, 0, wt / 2, b), (-(a + wt / 2), 0, wt / 2, b)]:
            g.append('<geom type="box" pos="%.4f %.4f 0" size="%.4f %.4f 0.0002" rgba="%s" contype="0" conaffinity="0"/>'
                     % (sx / 1000, sy / 1000, hx / 1000, hy / 1000, _c(crgb)))
        xml.append('<body name="w_tape" pos="%.4f %.4f 0.0002" quat="%s">%s</body>' % (tc[0] / 1000, tc[1] / 1000, _quat(tyaw), "".join(g)))
        # the area: up to the middle of the tape
        info["target"] = {"kind": "tape", "body": None, "a": a + wt / 2, "b": b + wt / 2, "yaw": tyaw, "c": tc.tolist()}
        tdesc = "the area outlined with %s tape" % cname
        mcol = crgb
        rt = math.hypot(a + wt, b + wt)
    foot.append((tc[0], tc[1], rt))

    # ------------------------------------------------------------ small objects
    for _ in range(20):
        # a kind that can be seen on this table (dark coffee beans on a dark wood table: no)
        kkey = list(KINDS)[int(rng.integers(len(KINDS)))]
        otext, shape, hs, cols, dens, rolls, nr = KINDS[kkey]
        if cols == "multi" or any(_cdiff(c, trgb) >= 70 and _cdiff(c, mcol) >= 60 for c in cols):
            break
    n = int(rng.integers(nr[0], nr[1] + 1))
    if cols == "multi":
        ocol = None
    else:
        ok = [c for c in cols if _cdiff(c, trgb) >= 70 and _cdiff(c, mcol) >= 60]
        ocol = (ok or cols)[int(rng.integers(len(ok or cols)))]
    size = [float(rng.uniform(*r)) for r in hs]
    rad = max(size)
    arr = ["cluster", "spread", "clusters"][int(rng.integers(3))]
    centres = []
    for _ in range(500):
        ang = rng.uniform(0, 2 * math.pi)
        dd = rt + rng.uniform(50, 160)
        cc = tc + dd * np.array([math.cos(ang), math.sin(ang)])
        if WS[0] + 30 <= cc[0] <= WS[1] - 30 and WS[2] + 30 <= cc[1] <= WS[3] - 30 and not (cc[0] < -400 and cc[1] < -250):
            centres.append(cc)
            if arr != "clusters" or len(centres) >= int(rng.integers(2, 4)):
                break
    sig = {"cluster": rng.uniform(10, 30), "spread": rng.uniform(40, 70), "clusters": rng.uniform(8, 22)}[arr]
    opos = []
    tries = 0
    while len(opos) < n and tries < 5000:
        tries += 1
        cc = centres[int(rng.integers(len(centres)))]
        p = cc + rng.normal(0, sig, 2)
        if not (WS[0] + 40 <= p[0] <= WS[1] - 10 and WS[2] + 10 <= p[1] <= WS[3] - 10):
            continue
        if p[1] > 200 and p[0] > -235:
            continue     # the arm cannot get behind them there (its reach at the far left of the table)
        if p[0] < -400 and p[1] < -250:
            continue
        if _in_rect(p, tc, tyaw, info["target"]["a"], info["target"]["b"], 40 + rad):
            continue
        if any(_dist(p, q) < 2 * rad + 1.0 for q in opos):
            continue
        opos.append(p)
    n = len(opos)
    m = dens * 1e-3  # g/mm3
    if shape == "sphere":
        vol = 4 / 3 * math.pi * size[0] ** 3
        gs = "%.5f" % (size[0] / 1000)
        z0 = size[0]
    elif shape == "lens":
        vol = 4 / 3 * math.pi * size[0] ** 2 * size[1]
        z0 = size[1]
    elif shape == "cylinder":
        vol = math.pi * size[0] ** 2 * 2 * size[1]
        gs = "%.5f %.5f" % (size[0] / 1000, size[1] / 1000)
        z0 = size[1]
    elif shape == "ellipsoid":
        vol = 4 / 3 * math.pi * size[0] * size[1] * size[2]
        gs = "%.5f %.5f %.5f" % tuple(s / 1000 for s in size)
        z0 = size[2]
    elif shape == "capsule":
        vol = math.pi * size[0] ** 2 * (2 * size[1] + 4 / 3 * size[0])
        gs = "%.5f %.5f" % (size[0] / 1000, size[1] / 1000)
        z0 = size[0]
    else:
        vol = 8 * size[0] * size[1] * size[2]
        gs = "%.5f %.5f %.5f" % tuple(s / 1000 for s in size)
        z0 = size[2]
    mass = max(vol * m / 1000.0, 0.0001)   # kg (at least 0.1 g, for the simulation's sake)
    mu = float(rng.uniform(0.3, 0.8))
    if rolls or shape == "capsule":
        fr ='friction="%.2f 0.002 %.6f" condim="6"' % (mu, size[0] / 1000 * rng.uniform(0.02, 0.08))
    else:
        fr = 'friction="%.2f 0.005 0.0001"' % mu
    for i, p in enumerate(opos):
        col = ocol if ocol is not None else BRIGHT[int(rng.integers(len(BRIGHT)))]
        jit = [1.0 + rng.uniform(-0.08, 0.08) for _ in size]
        gt = shape
        if shape in ("sphere",):
            g = "%.5f" % (size[0] * jit[0] / 1000)
        elif shape == "lens":
            gt = "ellipsoid"
            g = "%.5f %.5f %.5f" % (size[0] * jit[0] / 1000, size[0] * jit[0] / 1000, size[1] * jit[1] / 1000)
        else:
            g = " ".join("%.5f" % (s * j / 1000) for s, j in zip(size, jit))
        if shape == "capsule":
            q = "0.7071 0 0.7071 0"
            yaw = rng.uniform(0, math.pi)
            # lying capsule: rotate about z after laying down -> use euler-free: axis along x then yaw
            q = "%.5f %.5f %.5f %.5f" % (math.cos(yaw / 2) * 0.7071, -math.sin(yaw / 2) * 0.7071, math.cos(yaw / 2) * 0.7071, math.sin(yaw / 2) * 0.7071)
        else:
            q = _quat(rng.uniform(0, 2 * math.pi))
        xml.append('<body name="w_obj_%d" pos="%.4f %.4f %.4f" quat="%s"><freejoint/>'
                   '<geom type="%s" size="%s" mass="%.6f" rgba="%s" %s priority="1"/></body>'
                   % (i, p[0] / 1000, p[1] / 1000, (z0 + 0.3) / 1000, q, gt, g, mass, _c(col), fr))
    for p in opos:
        foot.append((p[0], p[1], 25.0))
    # corridors from the objects to the target and room behind them
    for p in opos:
        d = tc - p
        L = np.linalg.norm(d)
        u = d / max(L, 1e-6)
        for s in np.arange(-110, L, 30):
            foot.append((p[0] + u[0] * s, p[1] + u[1] * s, 55.0))
    info["n"] = n
    info["kind"] = kkey

    # ------------------------------------------------------------ sponge (maybe on a stand)
    sl = float(rng.uniform(85, 130))
    sw = float(rng.uniform(45, 64))      # the gripper opens 80 mm: wider sponges cannot be taken from above
    sh = float(rng.uniform(min(45.0, max(25.0, 21.0 - th)), 45))   # thinner ones cannot be gripped from above
    sname, srgb = SPONGE_COLS[int(rng.integers(len(SPONGE_COLS)))]
    scrub = rng.random() < 0.5
    stand_h = 0.0 if rng.random() < 0.45 else float(rng.uniform(15, 80))
    sr = math.hypot(sl, sw) / 2 + (25 if stand_h > 0 else 8)
    sp = None
    for _ in range(3000):
        p = np.array([rng.uniform(WS[0] + 20, WS[1] - 10), rng.uniform(WS[2] + 20, WS[3] - 20)])
        if p[0] < -380 and p[1] < -230:
            continue
        if free(p, sr + 10):
            sp = p
            break
    if sp is None:
        raise _NoRoom()
    syaw = float(rng.uniform(0, math.pi))
    dens_s = float(rng.uniform(0.025, 0.12))  # g/cm3 dry .. damp
    smass = sl * sw * sh * dens_s / 1e6
    smu = float(rng.uniform(0.5, 1.1))
    sgeoms = []
    if scrub:
        tsc = float(rng.uniform(3, 7))
        cname2, crgb2 = _pick_contrast(rng, SCRUB_COLS, [srgb], 60)
        top = rng.random() < 0.5
        zs = (sh / 2 - tsc / 2) if top else (-sh / 2 + tsc / 2)
        zf = (-tsc / 2) if top else (tsc / 2)
        sgeoms.append('<geom name="w_sponge_foam" type="box" pos="0 0 %.4f" size="%.4f %.4f %.4f" mass="%.5f" rgba="%s" friction="%.2f 0.005 0.0001" priority="1" solref="0.01 1" solimp="0.8 0.95 0.003"/>'
                      % (zf / 1000, sl / 2000, sw / 2000, (sh - tsc) / 2000, smass * 0.8, _c(srgb), smu))
        sgeoms.append('<geom name="w_sponge_scrub" type="box" pos="0 0 %.4f" size="%.4f %.4f %.4f" mass="%.5f" rgba="%s" friction="%.2f 0.005 0.0001" priority="1" solref="0.01 1" solimp="0.8 0.95 0.003"/>'
                      % (zs / 1000, sl / 2000, sw / 2000, tsc / 2000, smass * 0.2 + 0.002, _c(crgb2), smu * 0.9))
        sdesc = "the %s sponge with the %s scrubbing side" % (sname, cname2)
    else:
        sgeoms.append('<geom name="w_sponge_foam" type="box" size="%.4f %.4f %.4f" mass="%.5f" rgba="%s" friction="%.2f 0.005 0.0001" priority="1" solref="0.01 1" solimp="0.8 0.95 0.003"/>'
                      % (sl / 2000, sw / 2000, sh / 2000, smass, _c(srgb), smu))
        sdesc = "the %s sponge" % sname
    if stand_h > 0:
        stl, stw = sl + rng.uniform(10, 40), sw + rng.uniform(10, 40)
        stcol = DISTRACTOR_COLS[int(rng.integers(len(DISTRACTOR_COLS)))]
        xml.append('<body name="w_stand" pos="%.4f %.4f %.4f" quat="%s"><freejoint/>'
                   '<geom type="box" size="%.4f %.4f %.4f" mass="%.3f" rgba="%s" friction="0.8 0.005 0.0001" priority="1"/></body>'
                   % (sp[0] / 1000, sp[1] / 1000, stand_h / 2000, _quat(syaw + rng.uniform(-0.3, 0.3)), stl / 2000, stw / 2000,
                      stand_h / 2000, rng.uniform(0.3, 0.8), _c(stcol)))
        sdesc += " (it lies on a %s block)" % ("small" if stand_h < 40 else "tall")
    xml.append('<body name="w_sponge" pos="%.4f %.4f %.4f" quat="%s"><freejoint/>%s</body>'
               % (sp[0] / 1000, sp[1] / 1000, (stand_h + sh / 2 + 0.5) / 1000, _quat(syaw), "".join(sgeoms)))
    foot.append((sp[0], sp[1], sr))
    info["sponge"] = {"l": sl, "w": sw, "h": sh, "stand": stand_h, "mass_g": smass * 1000, "mu": smu}

    # ------------------------------------------------------------ distractors
    nd = int(rng.integers(0, 4))
    dnames = []
    for i in range(nd):
        typ = ["mug", "box", "bottle", "fruit", "pen"][int(rng.integers(5))]
        dc = [c for c in DISTRACTOR_COLS if _cdiff(c, srgb) > 90 and _cdiff(c, mcol) > 60] or DISTRACTOR_COLS
        col = dc[int(rng.integers(len(dc)))]
        if typ == "mug":
            r, h = rng.uniform(35, 45), rng.uniform(80, 105)
            geo = '<geom type="cylinder" size="%.4f %.4f" pos="0 0 %.4f" mass="0.3" rgba="%s" priority="1"/>' % (r / 1000, h / 2000, h / 2000, _c(col))
            geo += '<geom type="box" size="0.006 0.018 0.03" pos="%.4f 0 %.4f" mass="0.02" rgba="%s" priority="1"/>' % ((r + 6) / 1000, h / 2000, _c(col))
            fr_ = r + 15
        elif typ == "box":
            bx, by, bz = rng.uniform(25, 60), rng.uniform(25, 50), rng.uniform(20, 70)
            geo = '<geom type="box" size="%.4f %.4f %.4f" pos="0 0 %.4f" mass="0.25" rgba="%s" priority="1"/>' % (bx / 1000, by / 1000, bz / 1000, bz / 1000, _c(col))
            fr_ = math.hypot(bx, by)
        elif typ == "bottle":
            r, h = rng.uniform(30, 38), rng.uniform(150, 210)
            geo = '<geom type="cylinder" size="%.4f %.4f" pos="0 0 %.4f" mass="0.5" rgba="%s" priority="1"/>' % (r / 1000, h / 2000, h / 2000, _c(col))
            fr_ = r
        elif typ == "fruit":
            r = rng.uniform(32, 42)
            geo = '<geom type="sphere" size="%.4f" pos="0 0 %.4f" mass="0.18" rgba="%s" priority="1" condim="6" friction="1 0.01 0.002"/>' % (r / 1000, r / 1000, _c(col))
            fr_ = r
        else:
            geo = '<geom type="capsule" size="0.005 0.065" pos="0 0 0.005" quat="0.7071 0 0.7071 0" mass="0.015" rgba="%s" priority="1"/>' % _c(col)
            fr_ = 72
        dp = None
        for _ in range(2000):
            p = np.array([rng.uniform(-500, -60), rng.uniform(-200, 470)])
            if p[0] < -380 and p[1] < -230:
                continue
            if all(_dist(p, f[:2]) >= fr_ + f[2] + 10 for f in foot):
                dp = p
                break
        if dp is None:
            continue
        nm = "w_dis_%d_%s" % (i, typ)
        xml.append('<body name="%s" pos="%.4f %.4f 0.0005" quat="%s"><freejoint/>%s</body>' % (nm, dp[0] / 1000, dp[1] / 1000, _quat(rng.uniform(0, 6.28)), geo))
        foot.append((dp[0], dp[1], fr_))
        dnames.append(nm)
    info["distractors"] = dnames

    info["sdesc"] = sdesc
    info["odesc"] = otext
    info["tdesc"] = tdesc
    info["template"] = int(rng.integers(3))
    tg = info["target"]
    flat = {"table_h": th, "tkind": tg["kind"], "tbody": tg["body"] or "", "ta": tg["a"], "tb": tg["b"], "tyaw": tg["yaw"],
            "tcx": tg["c"][0], "tcy": tg["c"][1], "n": n, "kind": kkey, "dis": ",".join(dnames), "sdesc": sdesc,
            "odesc": otext, "tdesc": tdesc, "template": info["template"], "sl": sl, "sw": sw, "sh": sh, "stand": stand_h,
            "syaw": math.degrees(syaw)}
    enc = "|".join("%s:%s" % (k, str(v).replace(" ", "+")) for k, v in flat.items())
    xml.append('<site name="w_info|%s" pos="0.55 0.55 0.001" size="0.001" rgba="0 0 0 0"/>' % enc)
    scene["mjcf"] = "<mujoco><worldbody>%s</worldbody></mujoco>" % "".join(xml)
    return scene


class _NoRoom(Exception):
    pass


def scene(scene, rng):
    for _ in range(40):
        try:
            return _build(dict(scene), rng)
        except _NoRoom:
            continue
    raise RuntimeError("no layout found")


def _info(sc):
    s = sc["mjcf"].split('name="w_info|', 1)[1].split('"', 1)[0]
    out = {}
    for kv in s.split("|"):
        k, v = kv.split(":", 1)
        v = v.replace("+", " ")
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    out["n"] = int(out["n"])
    out["template"] = int(out["template"])
    out["distractors"] = [d for d in str(out["dis"]).split(",") if d]
    out["target"] = {"kind": out["tkind"], "body": out["tbody"] or None, "a": out["ta"], "b": out["tb"], "yaw": out["tyaw"],
                     "c": [out["tcx"], out["tcy"]]}
    return out


def goal(scene, rng):
    i = _info(scene)
    t = i["template"]
    if t == 0:
        return ("Pick up %s and use it to sweep the %s on the table into the target area, %s. "
                "Every one of them should end up inside that area. Leave everything else on the table where it is."
                % (i["sdesc"], i["odesc"], i["tdesc"]))
    if t == 1:
        return ("Take %s and sweep all the %s into the target area (%s). Do not disturb the other things on the table."
                % (i["sdesc"], i["odesc"], i["tdesc"]))
    return ("Sweep the %s into the target area, which is %s, using %s: pick up the sponge and push the %s with it. "
            "Other objects on the table must stay where they are." % (i["odesc"], i["tdesc"], i["sdesc"], i["odesc"]))


def success(state):
    info = _info(state.scene)
    th = info["table_h"]
    tg = info["target"]
    if tg["body"]:
        c = state.pos(tg["body"])
        R = state.rot(tg["body"])
        yaw = math.atan2(R[1][0], R[0][0])
    else:
        c = tg["c"]
        yaw = tg["yaw"]
    n = info["n"]
    inside = 0
    out = []
    for k in range(n):
        p = state.pos("w_obj_%d" % k)
        ok = _in_rect(p, c, yaw, tg["a"], tg["b"]) and (p[2] - th) < 25.0
        inside += ok
        if not ok:
            out.append(k)
    frac = inside / max(n, 1)
    moved = {}
    bad_d = []
    for nm in info["distractors"]:
        p0 = state.start[nm]
        p1 = state.pos(nm)
        dxy = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        R = state.rot(nm)
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, R[2][2]))))
        upright = nm.split("_")[-1] in ("mug", "box", "bottle")    # a fruit or a pen may roll a little
        moved[nm] = round(dxy, 1)
        if dxy > 30 or (upright and tilt > 25):
            bad_d.append(nm)
    # the sponge was taken: it is no longer where and as it was (moved, turned, or off its stand)
    s0 = state.start["w_sponge"]
    s1 = state.pos("w_sponge")
    Rs = state.rot("w_sponge")
    turned = abs(math.degrees(math.atan2(Rs[1][0], Rs[0][0])) - info["syaw"]) % 180.0
    turned = min(turned, 180.0 - turned)
    used = math.hypot(s1[0] - s0[0], s1[1] - s0[1]) > 30 or abs(s1[2] - s0[2]) > 8 or turned > 20
    released = not state.touching("w_sponge")["robot"]
    ok = frac >= 0.9 and not bad_d and used and released
    return {"ok": bool(ok), "in": inside, "n": n, "frac": round(frac, 3), "outside": out, "distractors_moved": moved,
            "bad_distractors": bad_d, "sponge_used": used, "sponge_released": released}
