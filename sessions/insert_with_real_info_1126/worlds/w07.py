import numpy as np

# Task world: a cylinder with a through hole (ring) and a stand with one vertical stick.
# Units in MJCF: metres. Positions count from the table top.

LEVEL = 2          # 0: plain (no platforms, no distractors, table at 0); 1: + platforms/table height; 2: + distractors
HARD = True        # wider size ranges, bigger wrist-camera pose error, more depth noise, more/closer distractors
TABLE_COLORS = ["black", "dark grey", "light grey", "white", "light wood", "dark wood", "green", "blue", "beige",
                "red brown"]


def _rgba(rng, lo=0.05, hi=0.95):
    c = rng.uniform(lo, hi, 3)
    return "%.3f %.3f %.3f 1" % tuple(c)


def _ring(rng, name, pos, r_in, r_out, h, n, rgba, density, fr):
    t = r_out - r_in
    rm = 0.5 * (r_in + r_out)
    half = r_out * np.tan(np.pi / n) + 0.0003
    g = []
    a0 = rng.uniform(0, 2 * np.pi)
    for k in range(n):
        a = a0 + 2 * np.pi * k / n
        g.append('<geom type="box" size="%.5f %.5f %.5f" pos="%.5f %.5f %.5f" euler="0 0 %.5f" rgba="%s" '
                 'density="%.1f" friction="%.3f 0.005 0.0001"/>'
                 % (t / 2, half, h / 2, rm * np.cos(a), rm * np.sin(a), h / 2, a, rgba, density, fr))
    # w_ring_top: the top centre; w_ring_rin: a marker at (r_in, 0, 0) in the ring's frame (for the check)
    return ('<body name="%s" pos="%.5f %.5f %.5f"><freejoint/>%s<body name="%s_top" pos="0 0 %.5f"/>'
            '<body name="%s_rin" pos="%.6f 0 0"/></body>'
            % (name, pos[0], pos[1], pos[2], "".join(g), name, h, name, r_in))


def _free_ok(c, r, placed, gap):
    for (c2, r2) in placed:
        if np.hypot(c[0] - c2[0], c[1] - c2[1]) < r + r2 + gap:
            return False
    return True


def scene(scene, rng):
    lvl = LEVEL
    hard = HARD
    # ---- the stick and the ring
    stick_d = rng.uniform(0.005, 0.022) if hard else rng.uniform(0.006, 0.020)
    clear = rng.uniform(0.0015, 0.004) if rng.uniform() < 0.7 else rng.uniform(0.0015, 0.0022)
    r_in = (stick_d + clear) / 2
    wall = rng.uniform(0.005, 0.024) if hard else rng.uniform(0.006, 0.022)
    r_out = min(r_in + wall, 0.032 if hard else 0.030)
    r_out = max(r_out, r_in + 0.005)
    h = rng.uniform(0.015, 0.07) if hard else rng.uniform(0.018, 0.06)
    n = int(rng.integers(14, 41)) if hard else int(rng.integers(20, 33))
    m_ring = float(np.exp(rng.uniform(np.log(0.015), np.log(0.4))))
    ring_density = m_ring / (np.pi * (r_out ** 2 - r_in ** 2) * h)
    ring_density = float(np.clip(ring_density, 150, 9000))
    ring_fr = rng.uniform(0.3, 1.0)
    ring_rgba = _rgba(rng)
    # ---- the stand
    stick_len = h + (rng.uniform(0.015, 0.09) if hard else rng.uniform(0.015, 0.07))
    stick_len = max(stick_len, 0.05)
    base_t = rng.uniform(0.008, 0.025)
    round_base = rng.uniform() < 0.4
    base_w = rng.uniform(0.09, 0.15)
    base_l = rng.uniform(0.09, 0.15)
    # density from a target mass (0.12 .. 1.5 kg, log-uniform)
    m_stand = float(np.exp(rng.uniform(np.log(0.12), np.log(1.5))))
    v_base = (np.pi * (base_w / 2) ** 2 if round_base else base_w * base_l) * base_t
    v_stick = np.pi * (stick_d / 2) ** 2 * stick_len
    stand_density = m_stand / (v_base + v_stick)
    stand_fr = rng.uniform(0.3, 1.0)
    stand_rgba = _rgba(rng)
    stick_rgba = stand_rgba if rng.uniform() < 0.5 else _rgba(rng)
    capsule_tip = rng.uniform() < 0.35
    # ---- placement
    placed = []
    def place(rad, tries=200):
        for _ in range(tries):
            c = (rng.uniform(-0.46, -0.20), rng.uniform(-0.17, 0.30))
            if _free_ok(c, rad, placed, 0.05):
                return c
        return None
    # supports (platforms)
    ring_plat = (lvl >= 1) and rng.uniform() < 0.45
    stand_plat = (lvl >= 1) and rng.uniform() < 0.45
    table_h = float(rng.uniform(-30, 40)) if lvl >= 1 else 0.0
    ph_max = 0.12 if hard else 0.10
    ring_ph = rng.uniform(0.02, ph_max) if ring_plat else 0.0
    stand_ph = rng.uniform(0.02, ph_max) if stand_plat else 0.0
    # the gripper's tool point may not go below z = 10 mm (robot frame): keep the ring's top >= 28 mm
    need = 0.028 - (table_h / 1000.0 + ring_ph + h)
    if need > 0:
        if ring_plat or rng.uniform() < 0.5:
            ring_plat = True
            ring_ph += need + rng.uniform(0, 0.02)
        else:
            h += need + rng.uniform(0, 0.01)
            stick_len = max(stick_len, h + rng.uniform(0.015, 0.05))
    ring_pr = r_out + (rng.uniform(0.03, 0.06) if ring_plat else 0.0)
    stand_pr = 0.5 * np.hypot(base_w, base_l if not round_base else base_w) + (rng.uniform(0.02, 0.05) if stand_plat else 0.0)
    sc = place(stand_pr)
    placed.append((sc, stand_pr + 0.02))
    rc = place(ring_pr)
    placed.append((rc, ring_pr + (0.012 if hard else 0.04)))
    body = []
    if ring_plat:
        s = ring_pr
        body.append('<body name="w_plat_ring" pos="%.5f %.5f %.5f"><geom type="box" size="%.5f %.5f %.5f" rgba="%s"/></body>'
                    % (rc[0], rc[1], ring_ph / 2, s, s, ring_ph / 2, _rgba(rng)))
    if stand_plat:
        s = stand_pr
        body.append('<body name="w_plat_stand" pos="%.5f %.5f %.5f"><geom type="box" size="%.5f %.5f %.5f" rgba="%s"/></body>'
                    % (sc[0], sc[1], stand_ph / 2, s, s, stand_ph / 2, _rgba(rng)))
    body.append(_ring(rng, "w_ring", (rc[0], rc[1], ring_ph + 0.0003), r_in, r_out, h, n, ring_rgba, ring_density, ring_fr))
    yaw = rng.uniform(-np.pi, np.pi)
    if round_base:
        bg = '<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.1f" friction="%.3f 0.005 0.0001"/>' % (
            base_w / 2, base_t / 2, base_t / 2, stand_rgba, stand_density, stand_fr)
    else:
        bg = '<geom type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.1f" friction="%.3f 0.005 0.0001"/>' % (
            base_w / 2, base_l / 2, base_t / 2, base_t / 2, stand_rgba, stand_density, stand_fr)
    sr = stick_d / 2
    if capsule_tip:
        sg = '<geom type="capsule" size="%.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.1f" friction="%.3f 0.005 0.0001"/>' % (
            sr, stick_len / 2 - sr, base_t + stick_len / 2, stick_rgba, stand_density, stand_fr)
    else:
        sg = '<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.1f" friction="%.3f 0.005 0.0001"/>' % (
            sr, stick_len / 2, base_t + stick_len / 2, stick_rgba, stand_density, stand_fr)
    body.append('<body name="w_stand" pos="%.5f %.5f %.5f" euler="0 0 %.5f"><freejoint/>%s%s'
                '<body name="w_stick_base" pos="0 0 %.5f"/><body name="w_stick_top" pos="0 0 %.5f"/></body>'
                % (sc[0], sc[1], stand_ph + 0.0003, yaw, bg, sg, base_t, base_t + stick_len))
    # ---- distractors
    nd = (int(rng.integers(2, 7)) if hard else int(rng.integers(1, 5))) if lvl >= 2 else 0
    dgap = 0.035 if hard else 0.06
    for i in range(nd):
        kind = rng.choice(["box", "sphere", "cylinder", "box"])
        if kind == "box":
            sx, sy, sz = rng.uniform(0.015, 0.045), rng.uniform(0.015, 0.045), rng.uniform(0.01, 0.06)
            rad = np.hypot(sx, sy)
            geom = '<geom type="box" size="%.4f %.4f %.4f" pos="0 0 %.4f"' % (sx, sy, sz, sz)
        elif kind == "sphere":
            r = rng.uniform(0.015, 0.035)
            rad = r
            geom = '<geom type="sphere" size="%.4f" pos="0 0 %.4f"' % (r, r)
        else:
            r, hz = rng.uniform(0.015, 0.04), rng.uniform(0.01, 0.06)
            rad = r
            geom = '<geom type="cylinder" size="%.4f %.4f" pos="0 0 %.4f"' % (r, hz, hz)
        c = None
        for _ in range(200):
            cc = (rng.uniform(-0.55, -0.12), rng.uniform(-0.25, 0.40))
            if _free_ok(cc, rad, placed, dgap):
                c = cc
                break
        if c is None:
            continue
        placed.append((c, rad))
        body.append('<body name="w_d%d" pos="%.5f %.5f 0.0003" euler="0 0 %.4f"><freejoint/>%s rgba="%s" density="%.0f"/></body>'
                    % (i, c[0], c[1], rng.uniform(-3, 3), geom, _rgba(rng), rng.uniform(300, 1500)))
    prm = {"r_in_mm": r_in * 1000, "r_out_mm": r_out * 1000, "h_mm": h * 1000,
           "stick_d_mm": stick_d * 1000, "stick_len_mm": stick_len * 1000, "capsule": float(capsule_tip),
           "ring_plat_mm": ring_ph * 1000, "stand_plat_mm": stand_ph * 1000}
    txt = ";".join("%s=%.4f" % (k, v) for k, v in prm.items())
    scene["mjcf"] = "<mujoco><worldbody>%s</worldbody></mujoco>" % "".join(body)
    if lvl >= 1:
        tab = {"height_mm": table_h, "color": str(rng.choice(TABLE_COLORS))}
        p = rng.uniform()
        if p < 0.25:
            pat = str(rng.choice(["speckle", "wood", "blotches", "stripes", "checker"]))
            tab["pattern"] = pat
            tab["color2"] = str(rng.choice(TABLE_COLORS))
            if pat == "speckle":
                tab["size_mm"] = float(rng.uniform(2, 5))
            elif pat in ("checker", "stripes"):
                tab["size_mm"] = float(rng.uniform(12, 45))
            tab["angle_deg"] = float(rng.uniform(0, 90))
        scene["table"] = tab
    if hard:
        scene["cameras"] = {"depth": {"noise_px": float(rng.uniform(0.15, 0.3))},
                            "left_wrist": {"pose_error": {"shift_mm": float(rng.uniform(3, 6)),
                                                          "turn_deg": float(rng.uniform(1.0, 1.8))}}}
    print("params", txt, "table", scene.get("table"))
    return scene


def _params(scene):
    s = scene["mjcf"]
    s = s[s.index("WPARAMS") + 7: s.index("WEND")]
    out = {}
    for kv in s.strip().split(";"):
        k, v = kv.split("=")
        out[k.strip()] = float(v)
    return out


def goal(scene, rng):
    return ("Put the cylinder on the stick. On the table there is a cylinder with a hole through it along its axis, "
            "standing on one of its flat ends, and a stand with one vertical stick. Pick up the cylinder and thread "
            "it onto the stick, so that the stick passes through its hole, and let go of it: at the end the cylinder "
            "must be on the stick, the robot not touching it, and the stand upright. Then open the gripper and "
            "return the arm to its initial pose. The hole can be as little as 1.5 mm wider than the stick. The "
            "cylinder and the stand may stand on the table or on raised supports, and other objects may be on the "
            "table.")


def success(state):
    P = np.array(state.pos("w_ring"), float)
    R = np.array(state.rot("w_ring"), float)
    B = np.array(state.pos("w_stick_base"), float)
    T = np.array(state.pos("w_stick_top"), float)
    RS = np.array(state.rot("w_stand"), float)
    h = float((R.T @ (np.array(state.pos("w_ring_top"), float) - P))[2])
    p = {"r_in_mm": float((R.T @ (np.array(state.pos("w_ring_rin"), float) - P))[0])}
    b = R.T @ (B - P)
    t = R.T @ (T - P)
    out = {}
    up_cos = float(RS[2, 2])
    out["stand_tilt_deg"] = round(float(np.degrees(np.arccos(np.clip(up_cos, -1, 1)))), 1)
    out["stick_top_above_ring_bottom_mm"] = round(float(t[2]), 1)
    rads = []
    ok_axis = False
    if t[2] - b[2] > 1e-6:
        for z0 in (0.0, min(t[2], h)):
            s = (z0 - b[2]) / (t[2] - b[2])
            q = b + s * (t - b)
            rads.append(float(np.hypot(q[0], q[1])))
        ok_axis = max(rads) <= p["r_in_mm"] + 1.0
    out["axis_off_mm"] = [round(r, 2) for r in rads]
    touch = state.touching("w_ring")
    robot_touch = bool(touch["robot"]) and any(len(v) > 0 for v in touch["robot"].values()) if isinstance(touch["robot"], dict) else bool(touch["robot"])
    out["robot_touching"] = robot_touch
    spd = float(np.linalg.norm(state.speed("w_ring")))
    out["ring_speed"] = round(spd, 1)
    threaded = ok_axis and t[2] >= 0.5 * h
    out["threaded"] = bool(threaded)
    out["ok"] = bool(threaded and up_cos > np.cos(np.radians(12)) and not robot_touch and spd < 30)
    return out
