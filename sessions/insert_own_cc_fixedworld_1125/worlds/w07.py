import math
import numpy as np

N_SEG = 24            # the ring is N_SEG convex boxes (MuJoCo collides convex shapes only)
REST_TCP = (-478.0, 41.0, 350.0)
LAST = {}             # the robot's state after the program's last command (filled by robot())
PKEYS = ["table_h", "ds", "dh", "od", "H", "L", "bt", "base", "pc", "ps"]
# the clutter: gaps between the footprints (mm), number of distractors, their kinds
GAP, DGAP, ND = 10.0, 15.0, (1, 6)
DARK = 0.12           # share of colours that are dark
PLAT = (15.0, 120.0)  # platform heights
CAMS = True           # random camera calibration errors and depth noise per episode
KINDS = ["box", "sphere", "cyl", "lying", "tallbox", "decoy", "disc"]


def _col(rng):
    k = rng.random()
    if k < DARK:   # dark things (stereo depth loses part of them)
        return list(np.array([rng.uniform(0.02, 0.2), rng.uniform(0.02, 0.2), rng.uniform(0.02, 0.2)]))
    k = rng.random()
    if k < 0.15:   # greys / white / black
        g = rng.uniform(0.08, 0.95)
        return [g, g, g]
    if k < 0.3:    # wood-like
        return list(np.array([0.75, 0.55, 0.35]) * rng.uniform(0.6, 1.2))
    h = rng.uniform(0, 1); s = rng.uniform(0.35, 1.0); v = rng.uniform(0.3, 1.0)
    i = int(h * 6) % 6; f = h * 6 - int(h * 6)
    p_, q_, t_ = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    return [[v, t_, p_], [q_, v, p_], [p_, v, t_], [p_, q_, v], [t_, p_, v], [v, p_, q_]][i]


def _rgba(c):
    return "%.3f %.3f %.3f 1" % tuple(min(1.0, max(0.0, x)) for x in c)


def _fric(rng):
    return "%.2f 0.005 0.0001" % rng.uniform(0.3, 1.0)


def scene(scene, rng):
    P = {}
    # ---- table
    th_ = 0.0 if rng.random() < 0.3 else float(rng.uniform(-40, 40))
    names = ["black", "dark grey", "light grey", "white", "light wood", "dark wood", "green", "blue", "beige",
             "red brown"]
    tab = {"height_mm": th_, "color": str(rng.choice(names))}
    pat = rng.choice(["plain", "plain", "wood", "speckle", "checker", "stripes", "blotches"])
    tab["pattern"] = str(pat)
    if pat != "plain":
        tab["color2"] = str(rng.choice(names))
        if pat in ("wood", "stripes"):
            tab["size_mm"] = float(rng.uniform(16, 44))
        elif pat == "speckle":
            tab["size_mm"] = float(rng.uniform(2, 5.5))
        tab["angle_deg"] = float(rng.uniform(0, 180))
    scene["table"] = tab
    # ---- stick and cylinder sizes
    ds = rng.uniform(5, 20)                       # stick diameter
    c = rng.uniform(1.5, 2.0) if rng.random() < 0.5 else rng.uniform(2.0, 5.0)
    dh = ds + c                                   # hole diameter (inscribed in the 24-gon)
    w = rng.uniform(4, 20)
    od = min(66.0, dh + 2 * w)
    od = max(od, dh + 8, 22.0)
    H = rng.uniform(15, 55)
    L = min(160.0, rng.uniform(H + 20, H + 90))   # stick length above the base
    base_round = rng.random() < 0.5
    a = rng.uniform(60, 140)                      # base side / diameter
    bt = rng.uniform(6, 25)                       # base thickness
    off = [0.0, 0.0]
    if rng.random() < 0.3:
        ang = rng.uniform(0, 2 * math.pi); rr = rng.uniform(0, 0.22 * a)
        off = [rr * math.cos(ang), rr * math.sin(ang)]
    round_top = rng.random() < 0.4
    yaw_s = rng.uniform(0, 2 * math.pi)
    # ---- platforms (the height at which the things are)
    pc = 0.0
    if rng.random() < 0.35:
        pc = rng.uniform(*PLAT)
    pc = max(pc, 30.0 - th_ - H)                  # the cylinder's top must be reachable (TCP z >= 0)
    ps = 0.0
    if rng.random() < 0.35:
        ps = rng.uniform(*PLAT)
    if CAMS:
        scene["cameras"] = {
            "depth": {"noise_px": float(rng.uniform(0.12, 0.3))},
            "scene": {"pose_error": {"shift_mm": float(rng.uniform(2, 8)), "turn_deg": float(rng.uniform(0.3, 1.5))}},
            "left_wrist": {"pose_error": {"shift_mm": float(rng.uniform(1, 5)), "turn_deg": float(rng.uniform(0.3, 2.0))}}}
    if th_ + ps + bt + L < 45:
        ps = 45 - th_ - bt - L
    if ps < 8:
        ps = 0.0 if ps <= 0 else 8.0
    pc = 0.0 if pc <= 0 else max(pc, 8.0)
    plat_c = od + rng.uniform(70, 130) if pc > 0 else 0.0
    ext_s = a * (1.0 if base_round else 1.42) + 2 * math.hypot(*off)
    plat_s = ext_s + rng.uniform(30, 80) if ps > 0 else 0.0
    r_cfoot = max(od / 2 + 45, plat_c * 0.71)
    r_sfoot = max(ext_s / 2 + 10, plat_s * 0.71)
    # ---- placement
    for _ in range(2000):
        cx, cy = rng.uniform(-440, -200), rng.uniform(-170, 230)
        sx, sy = rng.uniform(-440, -200), rng.uniform(-170, 230)
        if math.hypot(cx - sx, cy - sy) > r_cfoot + r_sfoot + GAP:
            break
    feet = [(cx, cy, r_cfoot), (sx, sy, r_sfoot)]
    # ---- MJCF
    cylc, stc, basec = _col(rng), _col(rng), _col(rng)
    if rng.random() < 0.5:
        basec = stc
    g = []
    m = 0.001
    # cylinder (ring) body: origin at the bottom centre
    rin = dh / 2; t = (od - dh) / 2
    Lh = (od / 2) * math.tan(math.pi / N_SEG) + 0.3
    rho_c = rng.uniform(300, 8000); fr_c = _fric(rng)
    yaw_c = rng.uniform(0, 2 * math.pi)
    segs = []
    for k in range(N_SEG):
        an = yaw_c + 2 * math.pi * k / N_SEG
        rc = rin + t / 2
        segs.append('<geom type="box" size="%.5f %.5f %.5f" pos="%.5f %.5f %.5f" euler="0 0 %.5f" rgba="%s" '
                    'density="%.0f" friction="%s"/>' % (t / 2 * m, Lh * m, H / 2 * m, rc * math.cos(an) * m,
                                                       rc * math.sin(an) * m, H / 2 * m, an, _rgba(cylc), rho_c, fr_c))
    g.append('<body name="w_cyl" pos="%.5f %.5f %.5f"><freejoint/>%s</body>' %
             (cx * m, cy * m, (pc + 0.3) * m, "".join(segs)))
    # stand: origin at the bottom centre of the base
    rho_b = rng.uniform(500, 8000); rho_s = rng.uniform(500, 8000); fr_s = _fric(rng)
    if base_round:
        bg = '<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.0f" friction="%s"/>' % (
            a / 2 * m, bt / 2 * m, bt / 2 * m, _rgba(basec), rho_b, fr_s)
    else:
        bg = '<geom type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.0f" friction="%s"/>' % (
            a / 2 * m, a / 2 * m, bt / 2 * m, bt / 2 * m, _rgba(basec), rho_b, fr_s)
    rs = ds / 2
    if round_top:
        Lc = L - rs
        sg = ('<geom type="cylinder" size="%.5f %.5f" pos="%.5f %.5f %.5f" rgba="%s" density="%.0f" friction="%s"/>'
              '<geom type="sphere" size="%.5f" pos="%.5f %.5f %.5f" rgba="%s" density="%.0f" friction="%s"/>') % (
            rs * m, Lc / 2 * m, off[0] * m, off[1] * m, (bt + Lc / 2) * m, _rgba(stc), rho_s, fr_s,
            rs * m, off[0] * m, off[1] * m, (bt + Lc) * m, _rgba(stc), rho_s, fr_s)
    else:
        sg = '<geom type="cylinder" size="%.5f %.5f" pos="%.5f %.5f %.5f" rgba="%s" density="%.0f" friction="%s"/>' % (
            rs * m, L / 2 * m, off[0] * m, off[1] * m, (bt + L / 2) * m, _rgba(stc), rho_s, fr_s)
    # place the stand so that the stick is at (sx, sy)
    co, si = math.cos(yaw_s), math.sin(yaw_s)
    bx = sx - (co * off[0] - si * off[1]); by = sy - (si * off[0] + co * off[1])
    g.append('<body name="w_stand" pos="%.5f %.5f %.5f" euler="0 0 %.5f"><freejoint/>%s%s</body>' %
             (bx * m, by * m, (ps + 0.3) * m, yaw_s, bg, sg))
    # platforms
    if pc > 0:
        pyaw = rng.uniform(0, math.pi / 2)
        g.append('<body name="w_plat_c" pos="%.5f %.5f %.5f" euler="0 0 %.4f"><geom type="box" size="%.5f %.5f %.5f" '
                 'rgba="%s" friction="0.6 0.005 0.0001"/></body>' % (cx * m, cy * m, pc / 2 * m, pyaw, plat_c / 2 * m,
                                                                      plat_c / 2 * m, pc / 2 * m, _rgba(_col(rng))))
    if ps > 0:
        pyaw = rng.uniform(0, math.pi / 2)
        g.append('<body name="w_plat_s" pos="%.5f %.5f %.5f" euler="0 0 %.4f"><geom type="box" size="%.5f %.5f %.5f" '
                 'rgba="%s" friction="0.6 0.005 0.0001"/></body>' % (bx * m, by * m, ps / 2 * m, pyaw, plat_s / 2 * m,
                                                                      plat_s / 2 * m, ps / 2 * m, _rgba(_col(rng))))
    # distractors
    nd = int(rng.integers(ND[0], ND[1] + 1))
    dl = []
    for i in range(nd):
        kind = rng.choice(KINDS)
        if kind == "tallbox":
            sz = [rng.uniform(15, 26), rng.uniform(15, 26), rng.uniform(50, 120)]; r = 0.71 * max(sz[0], sz[1])
            geo = 'type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f"' % (sz[0] / 2 * m, sz[1] / 2 * m, sz[2] / 2 * m,
                                                                       sz[2] / 2 * m)
        elif kind == "decoy":       # a solid cylinder the size of the ring
            rr = od / 2 * rng.uniform(0.8, 1.2); hh = H * rng.uniform(0.7, 1.3); r = rr
            geo = 'type="cylinder" size="%.5f %.5f" pos="0 0 %.5f"' % (rr * m, hh / 2 * m, hh / 2 * m)
        elif kind == "disc":
            rr = rng.uniform(20, 45); hh = rng.uniform(4, 12); r = rr
            geo = 'type="cylinder" size="%.5f %.5f" pos="0 0 %.5f"' % (rr * m, hh / 2 * m, hh / 2 * m)
        elif kind == "box":
            sz = rng.uniform(20, 60, 3); sz[2] = rng.uniform(15, 70); r = 0.71 * max(sz[0], sz[1])
            geo = 'type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f"' % (sz[0] / 2 * m, sz[1] / 2 * m, sz[2] / 2 * m,
                                                                       sz[2] / 2 * m)
        elif kind == "sphere":
            rr = rng.uniform(12, 30); r = rr
            geo = 'type="sphere" size="%.5f" pos="0 0 %.5f"' % (rr * m, rr * m)
        elif kind == "cyl":
            rr = rng.uniform(10, 32); hh = rng.uniform(15, 80); r = rr
            geo = 'type="cylinder" size="%.5f %.5f" pos="0 0 %.5f"' % (rr * m, hh / 2 * m, hh / 2 * m)
        else:
            rr = rng.uniform(5, 15); ll = rng.uniform(40, 120); r = ll / 2 + rr
            geo = 'type="capsule" size="%.5f %.5f" pos="0 0 %.5f" euler="0 1.5708 0"' % (rr * m, ll / 2 * m, rr * m)
        for _ in range(300):
            x, y = rng.uniform(-480, -120), rng.uniform(-240, 300)
            if all(math.hypot(x - fx, y - fy) > r + fr + DGAP for fx, fy, fr in feet):
                feet.append((x, y, r))
                g.append('<body name="w_d%d" pos="%.5f %.5f 0.0003" euler="0 0 %.4f"><freejoint/><geom %s rgba="%s" '
                         'density="%.0f" friction="%s"/></body>' % (i, x * m, y * m, rng.uniform(0, 6.28), geo,
                                                                    _rgba(_col(rng)), rng.uniform(300, 3000), _fric(rng)))
                dl.append(str(kind))
                break
    scene["mjcf"] = "<mujoco><worldbody>%s</worldbody></mujoco>" % "".join(g)
    P.update(dict(table_h=th_, ds=ds, dh=dh, od=od, H=H, L=L, bt=bt, base=a, base_round=base_round,
                  off=off, round_top=round_top, pc=pc, ps=ps, cyl=(cx, cy), stick=(sx, sy), distractors=dl,
                  rho_c=rho_c))
    vals = [float(P[k]) for k in PKEYS] + list(off)
    vals += [0.0] * (-len(vals) % 3)
    sites = "".join('<site name="w_P%d" pos="%.7f %.7f %.7f" size="0.0005" rgba="0 0 0 0"/>'
                    % (i // 3, vals[i] * 1e-4, vals[i + 1] * 1e-4, vals[i + 2] * 1e-4) for i in range(0, len(vals), 3))
    scene["mjcf"] = scene["mjcf"].replace("<worldbody>", "<worldbody>" + sites, 1)
    print("WORLD", {k: (round(v, 1) if isinstance(v, float) else v) for k, v in P.items()})
    return scene


def goal(scene, rng):
    return ("Task: put the cylinder on the stick. On the table there are a cylinder with a hole through it (standing "
            "on one of its flat ends, the hole vertical) and a stand with one vertical stick. Pick up the cylinder and "
            "thread it onto the stick, so that the stick passes through its hole, and let go of it: at the end the "
            "cylinder must be on the stick, the robot not touching it, and the stand upright. Then open the gripper "
            "and return it to its initial pose. The hole can be as little as 1.5 mm wider than the stick. The "
            "cylinder and the stand may stand on the table or on something on it; other objects may be on the table.")


def robot(robot, rng, kit):
    class Rec(kit.Robot):
        def _rec(self, out):
            try:
                st = self.robot.state("left")
                LAST["tcp"] = list(st["tcp_mm"]); LAST["open"] = st["opening_mm"]
                LAST["app"] = list(st["approach_dir"])
            except Exception:
                pass
            return out

        def move(self, arm, xyz, a, b, stop_on_contact=False):
            return self._rec(self.robot.move(arm, xyz, a, b, stop_on_contact))

        def turn(self, arm, centre, axis, ang):
            return self._rec(self.robot.turn(arm, centre, axis, ang))

        def home(self, arm):
            return self._rec(self.robot.home(arm))

        def set_gripper(self, arm, w):
            return self._rec(self.robot.set_gripper(arm, w))
    return Rec(robot)


def success(state):
    vals = []
    for i in range(8):
        parts = state.scene["mjcf"].split('name="w_P%d" pos="' % i)
        if len(parts) < 2:
            break
        vals += [float(x) * 1e4 for x in parts[1].split('"')[0].split()]
    P = dict(zip(PKEYS, vals))
    P["off"] = vals[len(PKEYS):len(PKEYS) + 2]
    pc_, Rc = np.array(state.pos("w_cyl")), np.array(state.rot("w_cyl"))
    ps_, Rs = np.array(state.pos("w_stand")), np.array(state.rot("w_stand"))
    bt, L, H = P["bt"], P["L"], P["H"]
    ax_s = Rs[:, 2]
    base_pt = ps_ + Rs @ np.array([P["off"][0], P["off"][1], bt])
    tilt_s = math.degrees(math.acos(min(1.0, ax_s[2])))
    ax_c = Rc[:, 2]
    tilt_rel = math.degrees(math.acos(min(1.0, abs(float(ax_c @ ax_s)))))
    lim = (P["dh"] / 2) / math.cos(math.pi / N_SEG) - P["ds"] / 2 + 0.5
    d = []
    s = []
    for q in (pc_, pc_ + H * ax_c):             # the hole's bottom and top centres
        v = q - base_pt
        sv = float(v @ ax_s); s.append(sv)
        d.append(float(np.linalg.norm(v - sv * ax_s)))
    on = max(d) <= lim and -3.0 <= s[0] and s[0] + 0.5 <= L - min(H, L) + 1.0
    tc = state.touching("w_cyl")["robot"]; ts = state.touching("w_stand")["robot"]
    hands_off = not any(tc.values()) if isinstance(tc, dict) else not tc
    hands_off_s = not any(ts.values()) if isinstance(ts, dict) else not ts
    res = {"on_stick": bool(on), "axis_off_mm": round(max(d), 2), "lim_mm": round(lim, 2),
           "bottom_above_base_mm": round(s[0], 1), "stand_tilt_deg": round(tilt_s, 1),
           "cyl_tilt_rel_deg": round(tilt_rel, 1), "hands_off": bool(hands_off and hands_off_s)}
    home_ok = None
    if LAST:
        tcp = LAST.get("tcp")
        dist = float(np.linalg.norm(np.array(tcp) - np.array(REST_TCP)))
        home_ok = dist < 25 and LAST.get("open", 0) > 70 and LAST["app"][2] < -0.97
        res["home_dist_mm"] = round(dist, 1); res["final_open_mm"] = round(LAST.get("open", 0), 1)
    res["home_ok"] = home_ok
    res["ok"] = bool(on and tilt_s < 5 and tilt_rel < 8 and res["hands_off"] and home_ok is not False)
    return res
