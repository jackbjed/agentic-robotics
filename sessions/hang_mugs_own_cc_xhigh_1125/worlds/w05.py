import math
import numpy as np

# ---------------- difficulty knobs (edited per world version) ----------------
LEVEL = 1          # 0: plain table, no distractors, no platforms; 1: full variation

MUG_COLORS = [(0.95, 0.95, 0.93), (0.8, 0.1, 0.1), (0.15, 0.3, 0.75), (0.95, 0.8, 0.2),
              (0.2, 0.6, 0.3), (0.12, 0.12, 0.12), (0.55, 0.55, 0.58), (0.85, 0.75, 0.6),
              (0.9, 0.45, 0.1), (0.5, 0.2, 0.55), (0.35, 0.7, 0.85)]
RACK_COLORS = [(0.55, 0.35, 0.18), (0.75, 0.58, 0.38), (0.1, 0.1, 0.1), (0.92, 0.92, 0.9),
               (0.7, 0.72, 0.75), (0.3, 0.2, 0.12), (0.8, 0.15, 0.15)]
DIST_COLORS = [(0.9, 0.2, 0.2), (0.2, 0.7, 0.3), (0.2, 0.3, 0.8), (0.9, 0.9, 0.3), (0.95, 0.95, 0.95),
               (0.6, 0.4, 0.2), (0.3, 0.3, 0.3), (0.9, 0.5, 0.7)]
TABLE_COLORS = ["black", "dark grey", "light grey", "white", "light wood", "dark wood", "green",
                "blue", "beige", "red brown"]


def _f(v):
    return " ".join("%.5f" % x for x in v)


def _qz(a):
    return (math.cos(a / 2), 0.0, 0.0, math.sin(a / 2))


def _rgba(c, rng, jitter=0.04):
    c = np.clip(np.array(c) + rng.uniform(-jitter, jitter, 3), 0, 1)
    return "%.3f %.3f %.3f 1" % tuple(c)


def _mug_params(rng, handle=True):
    m = {}
    m["R"] = rng.uniform(0.036, 0.046)
    m["H"] = rng.uniform(0.080, 0.115)
    m["tw"] = rng.uniform(0.003, 0.006)
    m["bt"] = rng.uniform(0.005, 0.009)
    m["rh"] = rng.uniform(0.004, 0.0065)
    m["handle"] = handle
    H, rh = m["H"], m["rh"]
    z_top = H - rng.uniform(0.012, 0.025)
    z_bot = rng.uniform(0.012, 0.028)
    if z_top - z_bot - 2 * rh < 0.036:
        z_bot = max(0.008, z_top - 2 * rh - 0.036)
    m["z_top"], m["z_bot"] = z_top, z_bot
    m["hole_w"] = rng.uniform(0.020, 0.032)
    m["p"] = rng.uniform(2.0, 3.5)
    m["mass"] = rng.uniform(0.22, 0.42)
    m["friction"] = rng.uniform(0.35, 0.75)
    m["n_wall"] = 20
    return m


def _handle_curve(m, n=10):
    """centerline of the handle in the mug frame, (x, z) points in metres."""
    cx = m["R"] - 0.003
    ax = m["hole_w"] + m["rh"] + 0.003
    zc = 0.5 * (m["z_top"] + m["z_bot"])
    az = 0.5 * (m["z_top"] - m["z_bot"])
    pts = []
    for t in np.linspace(math.pi / 2, -math.pi / 2, n):
        c, s = math.cos(t), math.sin(t)
        x = cx + ax * math.copysign(abs(c) ** (2 / m["p"]), c)
        z = zc + az * math.copysign(abs(s) ** (2 / m["p"]), s)
        pts.append((x, z))
    return pts


def _mug_xml(name, m, pos, yaw, rgba, rgba_in):
    R, H, tw, bt, N = m["R"], m["H"], m["tw"], m["bt"], m["n_wall"]
    vol_wall = 2 * math.pi * (R - tw / 2) * tw * (H - bt)
    vol_bot = math.pi * R * R * bt
    curve = _handle_curve(m) if m["handle"] else []
    segs = []
    vol_h = 0.0
    for i in range(len(curve) - 1):
        a, b = curve[i], curve[i + 1]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        segs.append((a, b))
        vol_h += math.pi * m["rh"] ** 2 * L
    rho = m["mass"] / (vol_wall + vol_bot + vol_h)
    # the bottom has priority: the mug's own (ceramic) friction governs mug-on-table contact; the walls and the
    # handle use MuJoCo's max rule (rubber finger pads 1.5, rack: the larger of the two)
    fr = "%.3f 0.005 0.0001" % m["friction"]
    x = ['<body name="%s" pos="%s" quat="%s">' % (name, _f(pos), _f(_qz(yaw))),
         '<joint name="%s_free" type="free" damping="0.0004"/>' % name,
         '<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" mass="%.5f" rgba="%s" friction="%s" priority="1"/>'
         % (R, bt / 2, bt / 2, rho * vol_bot, rgba, fr)]
    Rm = R - tw / 2
    hw = R * math.tan(math.pi / N) + 0.0003
    hh = (H - bt) / 2
    for i in range(N):
        a = 2 * math.pi * (i + 0.5) / N
        x.append('<geom type="box" size="%.5f %.5f %.5f" pos="%s" quat="%s" mass="%.5f" rgba="%s" friction="%s"/>'
                 % (tw / 2, hw, hh, _f((Rm * math.cos(a), Rm * math.sin(a), bt + hh)), _f(_qz(a)),
                    rho * vol_wall / N, rgba, fr))
    # inner bottom colour (visual only)
    x.append('<geom type="cylinder" size="%.5f 0.0005" pos="0 0 %.5f" contype="0" conaffinity="0" mass="0" rgba="%s"/>'
             % (R - tw - 0.0005, bt + 0.0005, rgba_in))
    for (a, b) in segs:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        x.append('<geom type="capsule" fromto="%s" size="%.5f" mass="%.5f" rgba="%s" friction="%s"/>'
                 % (_f((a[0], 0, a[1], b[0], 0, b[1])), m["rh"], rho * math.pi * m["rh"] ** 2 * L, rgba, fr))
    x.append('</body>')
    return "\n".join(x)


def _rack_params(rng, min_hole_w):
    r = {}
    r["base_kind"] = "disc" if rng.random() < 0.6 else "square"
    r["base_r"] = rng.uniform(0.075, 0.11)
    r["base_t"] = rng.uniform(0.012, 0.025)
    r["pole_r"] = rng.uniform(0.008, 0.015)
    r["rb"] = rng.uniform(0.004, min(0.0075, (min_hole_w - 0.008) / 2))
    nlev = 1 if rng.random() < 0.4 else 2
    h1 = rng.uniform(0.15, 0.21)
    if nlev == 1:
        nper = [int(rng.integers(3, 5))]
        hs = [h1]
    else:
        nper = [int(rng.integers(2, 4)), int(rng.integers(2, 4))]
        hs = [h1, h1 + rng.uniform(0.055, 0.085)]
    alpha = rng.uniform(math.radians(15), math.radians(40))
    L = rng.uniform(0.09, 0.14)
    knob = rng.random() < 0.5
    knob_r = min(r["rb"] + rng.uniform(0.0015, 0.004), (min_hole_w - 0.008) / 2) if knob else 0.0
    branches = []
    phi0 = rng.uniform(0, 2 * math.pi)
    for li, (n, h) in enumerate(zip(nper, hs)):
        off = phi0 + (math.pi / n if li == 1 else 0.0)
        for k in range(n):
            phi = off + 2 * math.pi * k / n + rng.uniform(-0.12, 0.12)
            d = np.array([math.cos(alpha) * math.cos(phi), math.cos(alpha) * math.sin(phi), math.sin(alpha)])
            root = np.array([0.5 * r["pole_r"] * math.cos(phi), 0.5 * r["pole_r"] * math.sin(phi),
                             r["base_t"] + h])
            tip = root + d * (L + 0.5 * r["pole_r"])
            branches.append((root.tolist(), tip.tolist()))
    r["branches"] = branches
    r["knob_r"] = knob_r
    r["pole_h"] = max(hs) + rng.uniform(0.03, 0.09)
    r["base_mass"] = rng.uniform(0.6, 1.5)
    r["upper_mass"] = rng.uniform(0.15, 0.4)
    r["friction"] = rng.uniform(0.3, 0.8)
    return r


def _rack_xml(r, pos, yaw, rgba, rgba_base):
    fr = "%.3f 0.005 0.0001" % r["friction"]
    x = ['<body name="w_rack" pos="%s" quat="%s">' % (_f(pos), _f(_qz(yaw))), '<freejoint name="w_rack_free"/>']
    if r["base_kind"] == "disc":
        x.append('<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" mass="%.4f" rgba="%s" friction="%s"/>'
                 % (r["base_r"], r["base_t"] / 2, r["base_t"] / 2, r["base_mass"], rgba_base, fr))
    else:
        s = r["base_r"] * 0.9
        x.append('<geom type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f" mass="%.4f" rgba="%s" friction="%s"/>'
                 % (s, s, r["base_t"] / 2, r["base_t"] / 2, r["base_mass"], rgba_base, fr))
    nb = len(r["branches"])
    mu = r["upper_mass"] / (nb + 2)
    ph = r["pole_h"]
    x.append('<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" mass="%.4f" rgba="%s" friction="%s"/>'
             % (r["pole_r"], ph / 2, r["base_t"] + ph / 2, 2 * mu, rgba, fr))
    for (a, b) in r["branches"]:
        x.append('<geom type="capsule" fromto="%s" size="%.5f" mass="%.4f" rgba="%s" friction="%s"/>'
                 % (_f(list(a) + list(b)), r["rb"], mu, rgba, fr))
        if r["knob_r"] > 0:
            x.append('<geom type="sphere" size="%.5f" pos="%s" mass="0.005" rgba="%s" friction="%s"/>'
                     % (r["knob_r"], _f(b), rgba, fr))
    x.append('</body>')
    return "\n".join(x)


def _dist_xml(i, rng, pos, surf):
    kind = rng.choice(["box", "can", "cup", "ball"])
    rgba = _rgba(DIST_COLORS[int(rng.integers(len(DIST_COLORS)))], rng)
    name = "w_dist%d" % i
    if kind == "cup":
        m = _mug_params(rng, handle=False)
        return _mug_xml(name, m, (pos[0], pos[1], surf + 0.0005), rng.uniform(0, 6.28), rgba, rgba), 0.05
    x = ['<body name="%s" pos="%s" quat="%s">' % (name, _f((pos[0], pos[1], surf)), _f(_qz(rng.uniform(0, 6.28)))),
         '<freejoint name="%s_free"/>' % name]
    if kind == "box":
        sx, sy, sz = rng.uniform(0.02, 0.05), rng.uniform(0.02, 0.05), rng.uniform(0.015, 0.06)
        x.append('<geom type="box" size="%.4f %.4f %.4f" pos="0 0 %.4f" mass="%.3f" rgba="%s"/>'
                 % (sx, sy, sz, sz + 0.0005, rng.uniform(0.05, 0.4), rgba))
        rad = math.hypot(sx, sy)
    elif kind == "can":
        rr, hh = rng.uniform(0.025, 0.04), rng.uniform(0.03, 0.065)
        x.append('<geom type="cylinder" size="%.4f %.4f" pos="0 0 %.4f" mass="%.3f" rgba="%s"/>'
                 % (rr, hh, hh + 0.0005, rng.uniform(0.1, 0.4), rgba))
        rad = rr
    else:
        rr = rng.uniform(0.02, 0.035)
        x.append('<geom type="sphere" size="%.4f" pos="0 0 %.4f" mass="%.3f" rgba="%s"/>'
                 % (rr, rr + 0.0005, rng.uniform(0.05, 0.2), rgba))
        rad = rr
    x.append('</body>')
    return "\n".join(x), rad


def scene(scene, rng):
    seed = int(rng.integers(1 << 30))
    mjcf, table, info = _gen(seed)
    scene["mjcf"] = mjcf.replace("<worldbody>", '<worldbody><body name="w_seed_%d_" pos="0.55 0.55 0.3"/>' % seed, 1)
    if table is not None:
        scene["table"] = table
    return scene


def _info(scene):
    s = scene["mjcf"]
    k = s.index('name="w_seed_') + len('name="w_seed_')
    seed = int(s[k:s.index('_"', k)])
    return _gen(seed)[2]


def _gen(seed):
    rng = np.random.default_rng(seed)
    scene = {}
    full = LEVEL >= 1
    mugs = [_mug_params(rng), _mug_params(rng)]
    min_hole_w = min(m["hole_w"] for m in mugs)
    rack = _rack_params(rng, min_hole_w)
    xml = []
    plat = []
    # rack placement
    rx, ry = rng.uniform(-400, -240), rng.uniform(-100, 300)
    ryaw = rng.uniform(0, 2 * math.pi)
    rack_surf = 0.0
    if full and rng.random() < 0.3:
        rack_surf = rng.uniform(0.015, 0.05)
        s = rack["base_r"] + rng.uniform(0.02, 0.05)
        plat.append('<body name="w_plat_rack" pos="%s"><geom type="box" size="%.4f %.4f %.4f" rgba="%s"/></body>'
                    % (_f((rx / 1000, ry / 1000, rack_surf / 2)), s, s, rack_surf / 2,
                       _rgba((0.6, 0.6, 0.6), rng, 0.3)))
    xml.append(_rack_xml(rack, (rx / 1000, ry / 1000, rack_surf + 0.0005), ryaw,
                         _rgba(RACK_COLORS[int(rng.integers(len(RACK_COLORS)))], rng),
                         _rgba(RACK_COLORS[int(rng.integers(len(RACK_COLORS)))], rng)))
    placed = [(rx, ry, rack["base_r"] * 1000 + 150)]

    def free_spot(rad, tries=400):
        for _ in range(tries):
            x, y = rng.uniform(-500, -150), rng.uniform(-220, 460)
            if x < -380 and y < -130:
                continue
            if all(math.hypot(x - px, y - py) > rad + pr for (px, py, pr) in placed):
                return x, y
        return None

    mug_info = []
    for i, m in enumerate(mugs):
        spot = free_spot(70)
        if spot is None:
            raise RuntimeError("no spot for mug")
        surf = 0.0
        if full and rng.random() < 0.25:
            surf = rng.uniform(0.015, 0.05)
            s = rng.uniform(0.065, 0.09)
            plat.append('<body name="w_plat_mug%d" pos="%s"><geom type="box" size="%.4f %.4f %.4f" rgba="%s"/></body>'
                        % (i, _f((spot[0] / 1000, spot[1] / 1000, surf / 2)), s, s, surf / 2,
                           _rgba((0.6, 0.6, 0.6), rng, 0.3)))
        yaw = rng.uniform(0, 2 * math.pi)
        c = MUG_COLORS[int(rng.integers(len(MUG_COLORS)))]
        cin = c if rng.random() < 0.6 else MUG_COLORS[int(rng.integers(len(MUG_COLORS)))]
        xml.append(_mug_xml("w_mug%d" % i, m, (spot[0] / 1000, spot[1] / 1000, surf + 0.0005), yaw,
                            _rgba(c, rng), _rgba(cin, rng)))
        placed.append((spot[0], spot[1], 70))
        mug_info.append({"curve": [(1000 * a, 1000 * b) for a, b in _handle_curve(m)], "rh": 1000 * m["rh"],
                         "R": 1000 * m["R"], "H": 1000 * m["H"],
                         "desc": "R %.1f H %.1f tw %.1f rh %.1f z_top %.1f z_bot %.1f hole_w %.1f p %.1f m %.0fg mu %.2f" % (
                             1000 * m["R"], 1000 * m["H"], 1000 * m["tw"], 1000 * m["rh"], 1000 * m["z_top"],
                             1000 * m["z_bot"], 1000 * m["hole_w"], m["p"], 1000 * m["mass"], m["friction"])})
    if full:
        nd = int(rng.integers(0, 4))
        for i in range(nd):
            spot = free_spot(60)
            if spot is None:
                break
            d, rad = _dist_xml(i, rng, (spot[0] / 1000, spot[1] / 1000), 0.0)
            xml.append(d)
            placed.append((spot[0], spot[1], 60))
    scene["mjcf"] = "<mujoco><worldbody>\n" + "\n".join(plat + xml) + "\n</worldbody></mujoco>"
    if full:
        tcol = TABLE_COLORS[int(rng.integers(len(TABLE_COLORS)))]
        t = {"height_mm": float(rng.uniform(-40, 40)), "color": tcol}
        pr = rng.random()
        if pr < 0.35:
            t["pattern"] = str(rng.choice(["checker", "stripes", "speckle", "wood", "blotches"]))
            t["color2"] = TABLE_COLORS[int(rng.integers(len(TABLE_COLORS)))]
            t["size_mm"] = float(rng.uniform(20, 80))
            t["angle_deg"] = float(rng.uniform(0, 90))
        scene["table"] = t
    info = {"mugs": mug_info,
            "branches": [([1000 * v for v in a], [1000 * v for v in b]) for a, b in rack["branches"]],
            "rb": 1000 * rack["rb"]}
    return scene["mjcf"], scene.get("table"), info


def goal(scene, rng):
    return ("Hang all the mugs on the mug rack. On the table there are two mugs, standing upright, and one mug "
            "rack (a mug tree: a post on a base, with branches sticking out of the post). Pick up each mug and hang "
            "it on the rack by its handle: a branch of the rack must pass through the handle so that the rack bears "
            "the mug's weight once the gripper lets go of it. Each mug must stay hanging after it is released, and "
            "hanging one mug must not knock the other off. Other things on the table are not to be used. At the end "
            "all mugs hang on the rack, the gripper is open and the arm is back at its start pose.")


def _inside(poly, p):
    x, y = p
    c = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                c = not c
    return c


_PRINTED = {}


def success(state):
    info = _info(state.scene)
    tr = state.truth()
    if not _PRINTED:
        _PRINTED[1] = 1
        print("truth keys:", sorted(tr.keys()))
        for i, mi in enumerate(info["mugs"]):
            print("mug%d params: %s" % (i, mi["desc"]))
        print("rack rb %.1f, branches:" % info["rb"], [[round(v) for v in a + b] for a, b in info["branches"]])
    rp = np.array(state.pos("w_rack"), float)
    rR = np.array(state.rot("w_rack"), float)
    branches = [(rp + rR @ np.array(a), rp + rR @ np.array(b)) for a, b in info["branches"]]
    mug_names = ["w_mug%d" % i for i in range(len(info["mugs"]))]
    allowed = set(["w_rack"] + mug_names)
    res = {"hanging": 0, "mugs": []}
    for i, mi in enumerate(info["mugs"]):
        name = mug_names[i]
        p = np.array(state.pos(name), float)
        R = np.array(state.rot(name), float)
        poly = mi["curve"]
        threaded = False
        for (a, b) in branches:
            qa, qb = R.T @ (a - p), R.T @ (b - p)
            if (qa[1] > 0) == (qb[1] > 0):
                continue
            s = qa[1] / (qa[1] - qb[1])
            q = qa + s * (qb - qa)
            if _inside(poly, (q[0], q[2])):
                threaded = True
        t = state.touching(name)
        robot_touch = any(len(v) > 0 for v in t.get("robot", {}).values())
        others = [o for o in t.get("others", []) if o not in allowed]
        spd = float(np.linalg.norm(state.speed(name)))
        tilt = math.degrees(math.acos(max(-1, min(1, R[2, 2]))))
        ok = threaded and not robot_touch and not others and spd < 30
        res["mugs"].append({"threaded": threaded, "robot": robot_touch, "others": others, "speed": round(spd, 1),
                            "tilt": round(tilt, 1), "z": round(float(p[2]), 1)})
        if ok:
            res["hanging"] += 1
    rack_tilt = math.degrees(math.acos(max(-1, min(1, rR[2, 2]))))
    res["rack_tilt"] = round(rack_tilt, 1)
    res["ok"] = res["hanging"] == len(mug_names) and rack_tilt < 15
    return res
