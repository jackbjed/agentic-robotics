import math
import numpy as np

# ---------------------------------------------------------------- helpers
WOOD = [(0.86, 0.55, 0.30), (0.80, 0.50, 0.26), (0.90, 0.62, 0.36), (0.75, 0.47, 0.25)]
CYL_COLOURS = [(0.05, 0.45, 0.85), (0.85, 0.10, 0.10), (0.10, 0.65, 0.20), (0.95, 0.80, 0.10),
               (0.92, 0.92, 0.92), (0.12, 0.12, 0.12), (0.55, 0.55, 0.58), (0.75, 0.75, 0.80),
               (0.95, 0.45, 0.05), (0.55, 0.20, 0.70), (0.85, 0.60, 0.35), (0.30, 0.20, 0.12)]
DIS_COLOURS = CYL_COLOURS + [(0.95, 0.95, 0.90), (0.2, 0.5, 0.2), (0.6, 0.1, 0.1)]
TABLES = [dict(color="black"), dict(color="dark grey"), dict(color="light grey"), dict(color="white"),
          dict(color="light wood"), dict(color="dark wood"), dict(color="green"), dict(color="blue"),
          dict(color="beige"), dict(color="dark grey", pattern="speckle", color2="black", size_mm=6),
          dict(color="light wood", pattern="wood", color2="dark wood", size_mm=40),
          dict(color="light grey", pattern="checker", color2="dark grey", size_mm=60)]


def f3(v):
    return " ".join("%.5f" % x for x in v)


def rgba(c, a=1.0):
    return "%.3f %.3f %.3f %.2f" % (c[0], c[1], c[2], a)


def quat_z(a):
    return (math.cos(a / 2), 0.0, 0.0, math.sin(a / 2))


def ring_geoms(name, r_in, r_out, h, n, colour, density, friction, phase=0.0):
    """A tube of n boxes about the body's z axis, its mid-height at the body origin (mm in, m out)."""
    w = r_out - r_in
    half_t = r_out * math.tan(math.pi / n) + 0.0002 * 1000
    out = []
    for k in range(n):
        a = phase + 2 * math.pi * k / n
        rc = (r_in + w / 2) / 1000.0
        out.append('<geom name="%s_%d" type="box" size="%s" pos="%s" quat="%s" rgba="%s" density="%.1f" '
                   'friction="%.3f 0.005 0.0001" condim="4"/>'
                   % (name, k, f3((w / 2000.0, half_t / 1000.0, h / 2000.0)),
                      f3((rc * math.cos(a), rc * math.sin(a), 0)), f3(quat_z(a)), rgba(colour), density, friction))
    return out


def stand_xml(p, x, y, z0, yaw):
    L, W, T = p["L"], p["W"], p["T"]
    sx, sr, sh = p["sx"], p["sr"], p["sh"]
    g = []
    g.append('<geom name="w_board" type="box" size="%s" pos="0 0 %.5f" rgba="%s" density="%.1f" friction="%.3f 0.005 0.0001"/>'
             % (f3((L / 2000, W / 2000, T / 2000)), T / 2000, rgba(p["wood"]), p["dens"], p["fric"]))
    g.append('<geom name="w_stick" type="cylinder" size="%.5f %.5f" pos="%.5f 0 %.5f" rgba="%s" density="%.1f" friction="%.3f 0.005 0.0001"/>'
             % (sr / 1000, sh / 2000, sx / 1000, (T + sh / 2) / 1000, rgba(p["stickc"]), p["dens"], p["sfric"]))
    for k, hx in enumerate(p["holes"]):
        g.append('<geom name="w_hole_%d" type="cylinder" size="%.5f 0.0003" pos="%.5f 0 %.5f" rgba="0.25 0.15 0.08 1" contype="0" conaffinity="0" mass="0"/>'
                 % (k, 0.006, hx / 1000, (T + 0.1) / 1000))
    return ('<body name="w_stand" pos="%s" quat="%s"><freejoint name="w_stand_j"/>%s</body>'
            % (f3((x / 1000, y / 1000, (z0 + 0.3) / 1000)), f3(quat_z(yaw)), "".join(g)))


def cyl_xml(c, x, y, z0, yaw):
    g = ring_geoms("w_cg", c["r_in"], c["r_out"], c["h"], c["n"], c["colour"], c["dens"], c["fric"])
    return ('<body name="w_cyl" pos="%s" quat="%s"><freejoint name="w_cyl_j"/>%s</body>'
            % (f3((x / 1000, y / 1000, (z0 + c["h"] / 2 + 0.3) / 1000)), f3(quat_z(yaw)), "".join(g)))


def distractor_xml(i, kind, d, x, y, z0, yaw):
    nm = "w_dis_%d" % i
    col = rgba(d["colour"])
    if kind == "solid":
        return ('<body name="%s" pos="%s"><freejoint/><geom type="cylinder" size="%.5f %.5f" rgba="%s" density="600"/></body>'
                % (nm, f3((x / 1000, y / 1000, (z0 + d["h"] / 2 + 0.3) / 1000)), d["r"] / 1000, d["h"] / 2000, col))
    if kind == "box":
        return ('<body name="%s" pos="%s" quat="%s"><freejoint/><geom type="box" size="%s" rgba="%s" density="400"/></body>'
                % (nm, f3((x / 1000, y / 1000, (z0 + d["hz"] + 0.3) / 1000)), f3(quat_z(yaw)),
                   f3((d["hx"] / 1000, d["hy"] / 1000, d["hz"] / 1000)), col))
    if kind == "mug":
        g = ring_geoms(nm + "_w", d["r"] - 4, d["r"], d["h"], 16, d["colour"], 1500, 0.8)
        g.append('<geom type="cylinder" size="%.5f 0.003" pos="0 0 %.5f" rgba="%s" density="1500"/>'
                 % ((d["r"] - 2) / 1000, -(d["h"] / 2 - 3) / 1000, col))
        g.append('<geom type="box" size="0.006 0.004 %.5f" pos="%.5f 0 0" rgba="%s" density="1500"/>'
                 % (d["h"] / 3000, (d["r"] + 12) / 1000, col))
        return ('<body name="%s" pos="%s" quat="%s"><freejoint/>%s</body>'
                % (nm, f3((x / 1000, y / 1000, (z0 + d["h"] / 2 + 0.3) / 1000)), f3(quat_z(yaw)), "".join(g)))
    if kind == "tape":
        s, tw = d["s"] / 2000, 0.009
        g = []
        for (px, py, hx, hy) in [(0, s, s + tw, tw), (0, -s, s + tw, tw), (s, 0, tw, s), (-s, 0, tw, s)]:
            g.append('<geom type="box" size="%s" pos="%s" rgba="%s" contype="0" conaffinity="0"/>'
                     % (f3((hx, hy, 0.0002)), f3((px, py, 0.0002 + z0 / 1000)), col))
        return '<body name="%s" pos="%s" quat="%s">%s</body>' % (nm, f3((x / 1000, y / 1000, 0)), f3(quat_z(yaw)), "".join(g))
    raise ValueError(kind)


# ---------------------------------------------------------------- reach model (TCP pointing down)
def reachable(x, y):
    if x < -420 and y < -250:
        return False
    if -210 <= y <= 130:
        return -470 <= x <= -120
    if 130 < y <= 270:
        return -500 <= x <= -150
    if 270 < y <= 450:
        return -560 <= x <= -170
    return False


def scene(scene, rng):
    # table
    tab = dict(TABLES[rng.integers(len(TABLES))])
    tab["height_mm"] = float(rng.uniform(-40, 40))
    scene["table"] = tab
    # stand (the real one; its dimensions estimated from the two real images, with some slack)
    p = dict(L=rng.uniform(265, 295), W=rng.uniform(80, 95), T=rng.uniform(18, 22),
             sr=rng.uniform(5.75, 6.5), sh=rng.uniform(160, 180),
             wood=WOOD[rng.integers(len(WOOD))], stickc=(0.86, 0.66, 0.45),
             dens=rng.uniform(450, 750), fric=rng.uniform(0.3, 0.6), sfric=rng.uniform(0.3, 0.7))
    p["sx"] = p["L"] / 2 - rng.uniform(38, 50)
    p["holes"] = [p["sx"] - 90, p["sx"] - 180]
    # cylinder
    shape = rng.choice(["round", "round", "round", "hex", "oct"])
    n = {"round": 24, "hex": 6, "oct": 8}[shape]
    r_in = p["sr"] + rng.choice([rng.uniform(0.75, 1.5), rng.uniform(1.5, 5.0)])
    wall = rng.uniform(5, 22)
    r_out = min(r_in + wall, 33.0)
    if r_out - r_in < 5:
        r_out = r_in + 5
    c = dict(shape=str(shape), n=n, r_in=float(r_in), r_out=float(r_out), h=float(rng.uniform(12, 50)),
             colour=CYL_COLOURS[rng.integers(len(CYL_COLOURS))],
             dens=float(rng.choice([rng.uniform(300, 1500), rng.uniform(1500, 8000)])),
             fric=float(rng.uniform(0.25, 1.1)))
    # heights: platforms under the stand and / or the cylinder
    st_plat = float(rng.uniform(15, 80)) if rng.random() < 0.35 else 0.0
    cy_plat = float(rng.uniform(15, 100)) if rng.random() < 0.35 else 0.0
    # positions
    placed = []  # (x, y, radius)

    def free(x, y, r):
        return all(math.hypot(x - a, y - b) > r + q for a, b, q in placed)

    for _ in range(2000):
        yaw = rng.uniform(-math.pi, math.pi)
        stx, sty = rng.uniform(-470, -150), rng.uniform(-200, 420)
        if not reachable(stx, sty):
            continue
        bx, by = stx - p["sx"] * math.cos(yaw), sty - p["sx"] * math.sin(yaw)
        # whole board inside the table and clear of the parked arm
        ok = True
        for t in (-p["L"] / 2 - 20, 0, p["L"] / 2 + 20):
            px, py = bx + t * math.cos(yaw), by + t * math.sin(yaw)
            if not (-560 < px < 560 and -560 < py < 560) or (px < -400 and py < -260):
                ok = False
        if ok:
            break
    placed += [(bx + t * math.cos(yaw), by + t * math.sin(yaw), 75) for t in (-p["L"] / 2 + 45, 0, p["L"] / 2 - 45)]
    for _ in range(2000):
        cx, cyy = rng.uniform(-470, -140), rng.uniform(-200, 420)
        if reachable(cx, cyy) and free(cx, cyy, c["r_out"] + 70):
            break
    placed.append((cx, cyy, c["r_out"] + 10))
    cyaw = rng.uniform(-math.pi, math.pi)
    bodies = []
    if st_plat > 0:
        bodies.append('<body name="w_plat_s" pos="%s" quat="%s"><geom type="box" size="%s" rgba="%s"/></body>'
                      % (f3((bx / 1000, by / 1000, st_plat / 2000)), f3(quat_z(yaw)),
                         f3(((p["L"] / 2 + 30) / 1000, (p["W"] / 2 + 30) / 1000, st_plat / 2000)),
                         rgba(DIS_COLOURS[rng.integers(len(DIS_COLOURS))])))
    if cy_plat > 0:
        s = rng.uniform(55, 80)
        bodies.append('<body name="w_plat_c" pos="%s" quat="%s"><geom type="box" size="%s" rgba="%s"/></body>'
                      % (f3((cx / 1000, cyy / 1000, cy_plat / 2000)), f3(quat_z(cyaw)),
                         f3((s / 1000, s / 1000, cy_plat / 2000)), rgba(DIS_COLOURS[rng.integers(len(DIS_COLOURS))])))
        placed[-1] = (cx, cyy, s * 1.42 + 10)
    bodies.append(stand_xml(p, bx, by, st_plat, yaw))
    bodies.append(cyl_xml(c, cx, cyy, cy_plat, cyaw))
    # distractors
    nd = int(rng.integers(0, 5))
    dis = []
    for i in range(nd):
        kind = str(rng.choice(["solid", "box", "mug", "tape", "solid"]))
        d = dict(colour=DIS_COLOURS[rng.integers(len(DIS_COLOURS))])
        if kind == "solid":
            d.update(r=rng.uniform(12, 35), h=rng.uniform(15, 90)); rad = d["r"]
        elif kind == "box":
            d.update(hx=rng.uniform(12, 40), hy=rng.uniform(12, 40), hz=rng.uniform(8, 40)); rad = math.hypot(d["hx"], d["hy"])
        elif kind == "mug":
            d.update(r=rng.uniform(35, 45), h=rng.uniform(80, 105)); rad = d["r"] + 18
        else:
            d.update(s=rng.uniform(90, 150), colour=(0.95, 0.75, 0.15)); rad = d["s"] * 0.72
        for _ in range(300):
            x, y = rng.uniform(-560, 300), rng.uniform(-540, 540)
            if x < -380 and y < -250:
                continue
            if free(x, y, rad + (10 if kind == "tape" else 40)):
                placed.append((x, y, rad))
                bodies.append(distractor_xml(i, kind, d, x, y, 0.0, rng.uniform(-math.pi, math.pi)))
                dis.append(kind)
                break
    info = dict(stick_r=float(p["sr"]), stick_h=float(p["sh"]), stick_x=float(p["sx"]), board_t=float(p["T"]),
                r_in=c["r_in"], r_out=c["r_out"], h=c["h"], dens=c["dens"], fric=c["fric"],
                st_plat=st_plat, cy_plat=cy_plat, table_h=tab["height_mm"])
    print("world:", {k: round(v, 2) for k, v in info.items()}, "shape", c["shape"], "table", tab, "distractors", dis)
    tag = "w_info" + "".join("__%s_%d" % (k, int(round(v * 1000))) for k, v in info.items())
    bodies.append('<body name="%s" pos="0 0 -0.01"/>' % tag)
    scene["mjcf"] = "<mujoco><worldbody>%s</worldbody></mujoco>" % "".join(bodies)
    return scene


KEYS = ["stick_r", "stick_h", "stick_x", "board_t", "r_in", "r_out", "h", "dens", "fric", "st_plat", "cy_plat", "table_h"]


def read_info(scene):
    s = scene["mjcf"]
    i = s.index('name="w_info__') + 14
    j = s.index('"', i)
    out = {}
    for item in s[i:j].split("__"):
        k, v = item.rsplit("_", 1)
        out[k] = float(v) / 1000.0
    return out


def goal(scene, rng):
    return ("Put the cylinder on the stick. On the table there are a cylinder with a hole through it along its axis "
            "(standing on one of its flat ends) and a stand: a wooden board with one vertical wooden stick. Pick up "
            "the cylinder and thread it onto the stick, so that the stick passes through its hole, and let go of it: "
            "at the end the cylinder must be on the stick, the robot not touching it, and the stand upright. Then open "
            "the gripper and return the arm to its initial pose. The hole can be as little as 1.5 mm wider than the "
            "stick. Other objects may be on the table; the stand and the cylinder may stand on raised supports.")


def success(state):
    info = read_info(state.scene)
    sp = np.array(state.pos("w_stand"), float)
    sR = np.array(state.rot("w_stand"), float)
    cp = np.array(state.pos("w_cyl"), float)
    cR = np.array(state.rot("w_cyl"), float)
    axis = sR[:, 2]
    base = sp + sR @ np.array([info["stick_x"], 0.0, info["board_t"]])
    upright = float(math.degrees(math.acos(max(-1.0, min(1.0, axis[2])))))
    clear = info["r_in"] - info["stick_r"]
    rad = []
    ts = []
    for s in (-1, 1):
        e = cp + s * cR[:, 2] * info["h"] / 2
        t = float(np.dot(e - base, axis))
        r = float(np.linalg.norm((e - base) - t * axis))
        rad.append(r)
        ts.append(t)
    on = all(r <= clear + 1.5 for r in rad) and min(ts) < info["stick_h"] - 2 and max(ts) > -2
    tc = state.touching("w_cyl")
    ts_ = state.touching("w_stand")
    robot_cyl = bool(tc["robot"])
    robot_stand = bool(ts_["robot"])
    ok = bool(on and upright < 10 and not robot_cyl and not robot_stand)
    return {"ok": ok, "on_stick": bool(on), "radial_mm": [round(r, 2) for r in rad], "clear_mm": round(clear, 2),
            "along_mm": [round(t, 1) for t in ts], "stand_tilt_deg": round(upright, 1),
            "robot_touch_cyl": robot_cyl, "robot_touch_stand": robot_stand}
