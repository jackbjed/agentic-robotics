import math
import numpy as np

# World: a rectangular block lies on the table beside a shallow rectangular tray (both loose, free joints).
# Task: pick the block up, put it in the tray, let go, open the gripper, go home.

TABLE_COLORS = ["black", "dark grey", "light grey", "white", "light wood", "dark wood", "green", "blue",
                "beige", "red brown"]
PATTERNS = ["plain", "plain", "plain", "checker", "stripes", "speckle", "wood", "blotches"]


def _rgb(rng):
    k = rng.random()
    if k < 0.15:
        return [float(v) for v in rng.uniform(0.02, 0.15, 3)]          # very dark
    if k < 0.30:
        g = float(rng.uniform(0.25, 0.95))
        return [g, g, g]                                                # grey / white
    return [float(v) for v in rng.uniform(0.05, 0.95, 3)]


def _rect_dist(px, py, cx, cy, yaw, hl, hw):
    c, s = math.cos(yaw), math.sin(yaw)
    dx, dy = px - cx, py - cy
    u = c * dx + s * dy
    v = -s * dx + c * dy
    return math.hypot(max(abs(u) - hl, 0.0), max(abs(v) - hw, 0.0))


def _sample(rng):
    th = float(rng.uniform(-40, 40))                       # table top height in the robot frame (mm)
    # block (mm): a <= b horizontal, c height; one horizontal side always fits the gripper
    a = float(rng.uniform(20, 60))
    b = float(min(rng.uniform(a, a + 35), 95))
    # the fingertips cannot go below robot z = -4: the block's top must stand at least ~22 mm above z = 0
    hmin = max(20.0, 22.0 - th)
    c = float(rng.uniform(hmin, max(hmin + 5, 70)))
    bmass = float(rng.uniform(0.02, 0.50))                # kg
    bfric = float(rng.uniform(0.3, 1.0))
    # tray (mm)
    tf = float(rng.uniform(3, 8))                          # floor thickness
    tw = float(rng.uniform(3, 10))                         # wall thickness
    wall = float(rng.uniform(12, 40))                      # wall height above the floor
    inL = float(rng.uniform(max(130, b + 90), 260))
    inW = float(rng.uniform(max(110, b + 80), max(max(110, b + 80), min(inL, 220))))
    L, W, H = inL + 2 * tw, inW + 2 * tw, tf + wall
    tmass = float(rng.uniform(0.15, 0.8))
    tfric = float(rng.uniform(0.3, 0.9))
    rb = 0.5 * math.hypot(a, b)
    for _ in range(2000):
        tyaw = float(rng.uniform(0, math.pi))
        tx, ty = float(rng.uniform(-440, -150)), float(rng.uniform(-220, 340))
        byaw = float(rng.uniform(0, math.pi))
        bx, by = float(rng.uniform(-450, -150)), float(rng.uniform(-210, 290))
        if bx < -390 and by < -200:
            continue
        if _rect_dist(bx, by, tx, ty, tyaw, L / 2, W / 2) < rb + 30:
            continue
        # tray stays on the table top (|x|,|y| < 590)
        cs, sn = abs(math.cos(tyaw)), abs(math.sin(tyaw))
        ex, ey = cs * L / 2 + sn * W / 2, sn * L / 2 + cs * W / 2
        if tx - ex < -590 or ty - ey < -590 or ty + ey > 590:
            continue
        break
    else:
        raise RuntimeError("no placement")
    return dict(th=th, a=a, b=b, c=c, bmass=bmass, bfric=bfric, tf=tf, tw=tw, wall=wall, inL=inL, inW=inW,
                L=L, W=W, H=H, tmass=tmass, tfric=tfric, tx=tx, ty=ty, tyaw=tyaw, bx=bx, by=by, byaw=byaw,
                brgb=_rgb(rng), trgb=_rgb(rng))


def _q(yaw):
    return "%.6f 0 0 %.6f" % (math.cos(yaw / 2), math.sin(yaw / 2))


def _mjcf(I):
    m = lambda v: "%.5f" % (v / 1000.0)
    a, b, c = I["a"], I["b"], I["c"]
    L, W, H, tf, tw = I["L"], I["W"], I["H"], I["tf"], I["tw"]
    # tray mass split by volume
    vols = [L * W * tf, 2 * tw * W * (H - tf), 2 * (L - 2 * tw) * tw * (H - tf)]
    tot = sum(vols)
    mf, mx, my = (I["tmass"] * vols[0] / tot, I["tmass"] * vols[1] / tot / 2, I["tmass"] * vols[2] / tot / 2)
    trgba = "%.3f %.3f %.3f 1" % tuple(I["trgb"])
    brgba = "%.3f %.3f %.3f 1" % tuple(I["brgb"])
    tfr = "%.3f 0.005 0.0001" % I["tfric"]
    bfr = "%.3f 0.005 0.0001" % I["bfric"]
    wz = (tf + H) / 2
    whz = (H - tf) / 2
    g = []
    g.append('<geom name="w_tray_floor" type="box" size="%s %s %s" pos="0 0 %s" rgba="%s" mass="%.4f" friction="%s"/>'
             % (m(L / 2), m(W / 2), m(tf / 2), m(tf / 2), trgba, mf, tfr))
    for s in (-1, 1):
        g.append('<geom name="w_tray_wx%d" type="box" size="%s %s %s" pos="%s 0 %s" rgba="%s" mass="%.4f" friction="%s"/>'
                 % (s + 1, m(tw / 2), m(W / 2), m(whz), m(s * (L / 2 - tw / 2)), m(wz), trgba, mx, tfr))
        g.append('<geom name="w_tray_wy%d" type="box" size="%s %s %s" pos="0 %s %s" rgba="%s" mass="%.4f" friction="%s"/>'
                 % (s + 1, m(L / 2 - tw), m(tw / 2), m(whz), m(s * (W / 2 - tw / 2)), m(wz), trgba, my, tfr))
    x = """<mujoco>
  <worldbody>
    <body name="w_tray" pos="%s %s 0.0002" quat="%s">
      <freejoint name="w_tray_free"/>
      %s
    </body>
    <body name="w_block" pos="%s %s %s" quat="%s">
      <freejoint name="w_block_free"/>
      <geom name="w_block_geom" type="box" size="%s %s %s" rgba="%s" mass="%.4f" friction="%s"/>
    </body>
  </worldbody>
</mujoco>""" % (m(I["tx"]), m(I["ty"]), _q(I["tyaw"]), "\n      ".join(g),
                m(I["bx"]), m(I["by"]), m(c / 2 + 0.2), _q(I["byaw"]),
                m(a / 2), m(b / 2), m(c / 2), brgba, I["bmass"], bfr)
    return x


def scene(scene, rng):
    I = _sample(rng)
    scene["mjcf"] = _mjcf(I)
    pat = PATTERNS[int(rng.integers(len(PATTERNS)))]
    trgb = [float(v) for v in rng.uniform(0.05, 0.85, 3)]
    tab = {"height_mm": I["th"], "rgb": [int(255 * v) for v in trgb], "pattern": pat}
    # low contrast: block and/or tray coloured like the table
    if rng.random() < 0.25:
        I["brgb"] = [float(np.clip(v + rng.uniform(-0.05, 0.05), 0, 1)) for v in trgb]
    if rng.random() < 0.2:
        I["trgb"] = [float(np.clip(v + rng.uniform(-0.05, 0.05), 0, 1)) for v in trgb]
    scene["mjcf"] = _mjcf(I)
    scene["cameras"] = {"depth": {"noise_px": float(rng.uniform(0.12, 0.3))},
                        "scene": {"pose_error": {"shift_mm": float(rng.uniform(3, 8)), "turn_deg": float(rng.uniform(0.5, 1.5))}},
                        "left_wrist": {"pose_error": {"shift_mm": float(rng.uniform(2, 5)), "turn_deg": float(rng.uniform(0.5, 1.5))}}}
    if pat != "plain":
        tab["color2"] = TABLE_COLORS[int(rng.integers(len(TABLE_COLORS)))]
        rr = {"checker": (25, 80), "stripes": (8, 50), "speckle": (1.5, 6)}.get(pat)
        if rr:
            tab["size_mm"] = float(rng.uniform(*rr))
        tab["angle_deg"] = float(rng.uniform(0, 180))
    scene["table"] = tab
    print("WORLD table %.1f %s %s | block %.0fx%.0fx%.0f mm %.0f g mu %.2f at (%.0f, %.0f) yaw %.0f | "
          "tray inner %.0fx%.0f wall %.0f floor %.1f thick %.1f %.0f g at (%.0f, %.0f) yaw %.0f"
          % (I["th"], str(tab["rgb"]), pat, I["a"], I["b"], I["c"], I["bmass"] * 1000, I["bfric"], I["bx"], I["by"],
             math.degrees(I["byaw"]), I["inL"], I["inW"], I["wall"], I["tf"], I["tw"], I["tmass"] * 1000,
             I["tx"], I["ty"], math.degrees(I["tyaw"])))
    return scene


def goal(scene, rng):
    return ("Task: on the table there are a small block and a shallow tray. Pick up the block and put it in the "
            "tray, and let go of it: at the end the block must lie in the tray, the robot not touching it. Then "
            "open the gripper and return the arm to its initial pose. "
            "The block is a rectangular box, 20 to 100 mm on a side and 20 to 70 mm tall; at least one of its "
            "horizontal sides is at most 60 mm. The tray is a rectangular open tray with a flat floor and straight "
            "walls 15 to 50 mm high, 110 to 260 mm long and wide inside; it is loose on the table: do not push it "
            "away. Sizes, colours, weights, frictions and places of both vary, and the table's top may be up to "
            "40 mm above or below z = 0.")


def _size(s, name):
    i = s.index('name="%s"' % name)
    i = s.index('size="', i) + len('size="')
    return [1000.0 * float(v) for v in s[i:s.index('"', i)].split()]


def _info(scene):
    s = scene["mjcf"]
    hb, fl, wx = _size(s, "w_block_geom"), _size(s, "w_tray_floor"), _size(s, "w_tray_wx0")
    tw = 2 * wx[0]
    return {"a": 2 * hb[0], "b": 2 * hb[1], "c": 2 * hb[2], "tf": 2 * fl[2],
            "inL": 2 * fl[0] - 2 * tw, "inW": 2 * fl[1] - 2 * tw}


def success(state):
    I = _info(state.scene)
    p = np.array(state.pos("w_block"), float)
    Rb = np.array(state.rot("w_block"), float)
    t = np.array(state.pos("w_tray"), float)
    Rt = np.array(state.rot("w_tray"), float)
    q = Rt.T @ (p - t)
    half = np.array([I["a"], I["b"], I["c"]]) / 2
    ext = np.abs(Rt.T @ Rb) @ half
    low = q[2] - ext[2] - I["tf"]                # block's lowest point above the tray floor's top (mm)
    inside = bool(abs(q[0]) + ext[0] <= I["inL"] / 2 + 2 and abs(q[1]) + ext[1] <= I["inW"] / 2 + 2)
    on_floor = bool(abs(low) < 4)
    tray_up = bool(Rt[2, 2] > 0.98)
    t0 = np.array(state.start["w_tray"], float)
    tray_moved = float(np.linalg.norm(t[:2] - t0[:2]))
    touch = state.touching("w_block")
    robot_touch = bool(any(len(v) for v in touch["robot"].values())) if isinstance(touch["robot"], dict) \
        else bool(touch["robot"])
    tt = state.touching("w_tray")["robot"]
    tray_touch = bool(any(len(v) for v in tt.values())) if isinstance(tt, dict) else bool(tt)
    robot_touch = robot_touch or tray_touch
    speed = float(np.linalg.norm(state.speed("w_block")))
    ok = inside and on_floor and tray_up and (not robot_touch) and tray_moved < 30 and speed < 20
    return {"ok": bool(ok), "inside": inside, "on_floor": on_floor, "low_mm": round(float(low), 1),
            "in_tray_xy": [round(float(q[0]), 1), round(float(q[1]), 1)], "tray_up": tray_up,
            "tray_moved_mm": round(tray_moved, 1), "robot_touch": robot_touch, "speed": round(speed, 1)}
