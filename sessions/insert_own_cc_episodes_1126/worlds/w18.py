import math
import numpy as np

# w17 (real-like): the real rig's stand (a light-wood board with three holes along its middle and a ~10 mm dowel
# in one of them), a black table at z = 0, various cylinders, platforms under the cylinder and/or the stand,
# distractors (books, sponges, mugs, boxes, pens...), and the real robot's quirks in robot(): moves end a few
# mm off their target (a smooth field + noise), contact moves are slow and drift, the soft (fin-ray) fingers read
# wide things narrower than they are, a look right after a move sees the scene from where the arm still was.
N_SEG = 24
REST_TCP = (-478.0, 41.0, 350.0)
LAST = {}
PKEYS = ["ds", "dh", "od", "H", "Ls", "bt", "pc", "ps", "lean"]


def _hsv(h, s, v):
    i = int(h * 6) % 6; f = h * 6 - int(h * 6)
    p_, q_, t_ = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    return [[v, t_, p_], [q_, v, p_], [p_, v, t_], [p_, q_, v], [t_, p_, v], [v, p_, q_]][i]


def _col(rng, dark_ok=True):
    k = rng.random()
    if dark_ok and k < 0.1:
        return list(rng.uniform(0.03, 0.2, 3))
    k = rng.random()
    if k < 0.15:
        g = rng.uniform(0.3, 0.95)
        return [g, g, g]
    if k < 0.3:
        return list(np.array([0.75, 0.55, 0.35]) * rng.uniform(0.6, 1.2))
    return _hsv(rng.uniform(0, 1), rng.uniform(0.35, 1.0), rng.uniform(0.35, 1.0))


def _rgba(c):
    return "%.3f %.3f %.3f 1" % tuple(min(1.0, max(0.0, x)) for x in c)


def _rect_dist(px, py, cx, cy, yaw, hx, hy):
    co, si = math.cos(yaw), math.sin(yaw)
    dx, dy = px - cx, py - cy
    u, v = co * dx + si * dy, -si * dx + co * dy
    ex, ey = max(abs(u) - hx, 0.0), max(abs(v) - hy, 0.0)
    return math.hypot(ex, ey)


def scene(scene, rng):
    LAST.clear()
    m = 0.001
    # ---- the real table: black (with scratches), its top at 0
    tab = {"height_mm": 0.0, "color": str(rng.choice(["black", "black", "dark grey"]))}
    if rng.random() < 0.5:
        tab.update(pattern="speckle", color2="dark grey", size_mm=float(rng.uniform(2, 5)))
    else:
        tab["pattern"] = "plain"
    scene["table"] = tab
    scene["cameras"] = {
        "depth": {"noise_px": float(rng.uniform(0.12, 0.25))},
        "left_wrist": {"pose_error": {"shift_mm": float(rng.uniform(2, 6)), "turn_deg": float(rng.uniform(0.4, 1.5))}}}
    # ---- the stand: board with 3 holes on its centre line, the dowel in one of them
    Lb, Wb, bt = rng.uniform(270, 290), rng.uniform(78, 90), rng.uniform(18, 24)
    hole_pos = [rng.uniform(50, 60), 0.0, 0.0]
    sp = rng.uniform(82, 92)
    hole_pos[1], hole_pos[2] = hole_pos[0] + sp, hole_pos[0] + 2 * sp
    k_hole = 0 if rng.random() < 0.8 else 1
    ds = rng.uniform(9.5, 12.0)
    Ls = rng.uniform(160, 176)                      # the dowel above the board's top
    lean = rng.uniform(0, 0.8); lean_dir = rng.uniform(0, 2 * math.pi)
    yaw_b = rng.uniform(0, 2 * math.pi)
    # ---- the cylinder
    c = rng.uniform(1.5, 2.5) if rng.random() < 0.5 else rng.uniform(2.5, 6.0)
    dh = ds + c
    od = rng.uniform(max(dh + 8, 24.0), 60.0)
    H = rng.uniform(15, 50)
    # ---- heights: platforms
    ps = rng.uniform(10, 80) if rng.random() < 0.35 else 0.0
    pc = rng.uniform(10, 80) if rng.random() < 0.35 else 0.0
    plat_s = (Lb + rng.uniform(20, 70), Wb + rng.uniform(30, 90)) if ps > 0 else None
    plat_c = (od + rng.uniform(50, 120), od + rng.uniform(50, 120)) if pc > 0 else None
    pyaw_c = rng.uniform(0, math.pi)
    # board centre from the stick's place
    xh = -Lb / 2 + hole_pos[k_hole]                 # the hole along the board's x axis (from its centre)
    for _ in range(3000):
        sx, sy = rng.uniform(-380, -140), rng.uniform(-170, 170)
        bx, by = sx - math.cos(yaw_b) * xh, sy - math.sin(yaw_b) * xh
        cx, cy = rng.uniform(-420, -130), rng.uniform(-180, 180)
        hx, hy = (plat_s[0] / 2, plat_s[1] / 2) if plat_s else (Lb / 2, Wb / 2)
        rc = (max(plat_c) * 0.71) if plat_c else od / 2
        gap = rng.uniform(15, 60)
        if _rect_dist(cx, cy, bx, by, yaw_b, hx, hy) > rc + gap and abs(bx) < 450 and abs(by) < 450:
            break
        yaw_b = rng.uniform(0, 2 * math.pi)
    g = []
    wood = np.array([0.86, 0.64, 0.40]) * rng.uniform(0.85, 1.1)
    stick_c = np.array([0.72, 0.45, 0.25]) * rng.uniform(0.85, 1.15)
    rho_b = rng.uniform(400, 750); fr_b = rng.uniform(0.3, 0.6)
    holes = "".join('<geom type="cylinder" size="%.5f 0.0002" pos="%.5f 0 %.5f" rgba="0.25 0.15 0.08 1" '
                    'contype="0" conaffinity="0"/>' % ((ds / 2 + 0.5) * m, (-Lb / 2 + hp) * m, (bt + 0.1) * m)
                    for i, hp in enumerate(hole_pos) if i != k_hole)
    lx, ly = math.radians(lean) * math.cos(lean_dir), math.radians(lean) * math.sin(lean_dir)
    stick = ('<body name="w_stick" pos="%.5f 0 %.5f" euler="%.5f %.5f 0"><geom type="cylinder" size="%.5f %.5f" '
             'pos="0 0 %.5f" rgba="%s" density="600" friction="0.5 0.005 0.0001"/>'
             '<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" rgba="%s" density="600"/></body>') % (
        xh * m, bt * m, lx, ly, ds / 2 * m, Ls / 2 * m, Ls / 2 * m, _rgba(stick_c),
        ds / 2 * m, 6 * m, -6 * m, _rgba(stick_c))
    g.append('<body name="w_stand" pos="%.5f %.5f %.5f" euler="0 0 %.5f"><freejoint/>'
             '<geom type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.0f" '
             'friction="%.2f 0.005 0.0001" priority="1"/>%s%s</body>' % (
                 bx * m, by * m, (ps + 0.3) * m, yaw_b, Lb / 2 * m, Wb / 2 * m, bt / 2 * m, bt / 2 * m,
                 _rgba(wood), rho_b, fr_b, holes, stick))
    if ps > 0:
        g.append('<body name="w_plat_s" pos="%.5f %.5f %.5f" euler="0 0 %.4f"><geom type="box" size="%.5f %.5f %.5f" '
                 'rgba="%s" friction="0.6 0.005 0.0001"/></body>' % (bx * m, by * m, ps / 2 * m, yaw_b, plat_s[0] / 2 * m,
                                                                      plat_s[1] / 2 * m, ps / 2 * m, _rgba(_col(rng))))
    if pc > 0:
        g.append('<body name="w_plat_c" pos="%.5f %.5f %.5f" euler="0 0 %.4f"><geom type="box" size="%.5f %.5f %.5f" '
                 'rgba="%s" friction="0.6 0.005 0.0001"/></body>' % (cx * m, cy * m, pc / 2 * m, pyaw_c, plat_c[0] / 2 * m,
                                                                      plat_c[1] / 2 * m, pc / 2 * m, _rgba(_col(rng))))
    # ---- the cylinder: 24 wall boxes 0.5 mm short of the bottom + 8 full-height feet inside the wall
    cylc = _col(rng, dark_ok=False)
    if max(cylc) < 0.3:
        cylc = [0.3 + x for x in cylc]
    rin = dh / 2; t = (od - dh) / 2
    Lh = (od / 2) * math.tan(math.pi / N_SEG) + 0.3
    rho_c = rng.uniform(300, 3000); fr_c = "%.2f 0.005 0.0001" % rng.uniform(0.3, 0.9)
    yaw_c = rng.uniform(0, 2 * math.pi)
    segs = []
    hb = 0.5
    for k in range(N_SEG):
        an = yaw_c + 2 * math.pi * k / N_SEG
        rc_ = rin + t / 2
        segs.append('<geom type="box" size="%.5f %.5f %.5f" pos="%.5f %.5f %.5f" euler="0 0 %.5f" rgba="%s" '
                    'density="%.0f" friction="%s" priority="1"/>' % (t / 2 * m, Lh * m, (H - hb) / 2 * m, rc_ * math.cos(an) * m,
                                                       rc_ * math.sin(an) * m, (hb + (H - hb) / 2) * m, an,
                                                       _rgba(cylc), rho_c, fr_c))
    for k in range(8):
        an = yaw_c + 2 * math.pi * (k + 0.5) / 8
        rc_ = rin + t / 2
        segs.append('<geom type="box" size="%.5f %.5f %.5f" pos="%.5f %.5f %.5f" euler="0 0 %.5f" rgba="%s" '
                    'density="%.0f" friction="%s" priority="1"/>' % (t / 2 * 0.9 * m, 2.0 * m, H / 2 * m, rc_ * math.cos(an) * m,
                                                       rc_ * math.sin(an) * m, H / 2 * m, an, _rgba(cylc), rho_c, fr_c))
    g.append('<body name="w_cyl" pos="%.5f %.5f %.5f"><joint type="free" damping="0.003"/>%s</body>' %
             (cx * m, cy * m, (pc + 0.3) * m, "".join(segs)))
    # ---- distractors
    feet_c = [(cx, cy, (max(plat_c) * 0.71) if plat_c else od / 2)]
    nd = int(rng.integers(2, 7))
    dl = []
    for i in range(nd):
        kind = str(rng.choice(["book", "book", "sponge", "mug", "box", "tallbox", "sphere", "decoy", "pen", "disc"]))
        if kind == "book":
            sz = [rng.uniform(150, 240), rng.uniform(100, 180), rng.uniform(8, 30)]
        elif kind == "sponge":
            sz = [rng.uniform(80, 120), rng.uniform(55, 80), rng.uniform(20, 40)]
        elif kind == "box":
            sz = [rng.uniform(25, 70), rng.uniform(25, 70), rng.uniform(15, 70)]
        elif kind == "tallbox":
            sz = [rng.uniform(15, 30), rng.uniform(15, 30), rng.uniform(60, 130)]
        else:
            sz = None
        if sz is not None:
            r = 0.71 * max(sz[0], sz[1])
            geo = 'type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f"' % (sz[0] / 2 * m, sz[1] / 2 * m, sz[2] / 2 * m,
                                                                       sz[2] / 2 * m)
        elif kind == "mug":
            rr, hh = rng.uniform(35, 45), rng.uniform(80, 105); r = rr
            geo = 'type="cylinder" size="%.5f %.5f" pos="0 0 %.5f"' % (rr * m, hh / 2 * m, hh / 2 * m)
        elif kind == "decoy":
            rr, hh = od / 2 * rng.uniform(0.8, 1.2), H * rng.uniform(0.7, 1.3); r = rr
            geo = 'type="cylinder" size="%.5f %.5f" pos="0 0 %.5f"' % (rr * m, hh / 2 * m, hh / 2 * m)
        elif kind == "disc":
            rr, hh = rng.uniform(20, 45), rng.uniform(4, 12); r = rr
            geo = 'type="cylinder" size="%.5f %.5f" pos="0 0 %.5f"' % (rr * m, hh / 2 * m, hh / 2 * m)
        elif kind == "sphere":
            rr = rng.uniform(15, 35); r = rr
            geo = 'type="sphere" size="%.5f" pos="0 0 %.5f"' % (rr * m, rr * m)
        else:
            rr, ll = rng.uniform(4, 9), rng.uniform(100, 150); r = ll / 2 + rr
            geo = 'type="capsule" size="%.5f %.5f" pos="0 0 %.5f" euler="0 1.5708 0"' % (rr * m, ll / 2 * m, rr * m)
        for _ in range(300):
            x, y = rng.uniform(-500, -60), rng.uniform(-300, 350)
            hx, hy = (plat_s[0] / 2, plat_s[1] / 2) if plat_s else (Lb / 2, Wb / 2)
            if _rect_dist(x, y, bx, by, yaw_b, hx, hy) > r + 30 and \
                    all(math.hypot(x - fx, y - fy) > r + fr + 25 for fx, fy, fr in feet_c):
                feet_c.append((x, y, r))
                g.append('<body name="w_d%d" pos="%.5f %.5f 0.0003" euler="0 0 %.4f"><freejoint/><geom %s rgba="%s" '
                         'density="%.0f" friction="%.2f 0.005 0.0001"/></body>' % (
                             i, x * m, y * m, rng.uniform(0, 6.28), geo, _rgba(_col(rng)), rng.uniform(150, 1500),
                             rng.uniform(0.3, 1.0)))
                dl.append(kind)
                break
    P = dict(ds=ds, dh=dh, od=od, H=H, Ls=Ls, bt=bt, pc=pc, ps=ps, lean=lean)
    vals = [float(P[k]) for k in PKEYS]
    vals += [0.0] * (-len(vals) % 3)
    sites = "".join('<site name="w_P%d" pos="%.7f %.7f %.7f" size="0.0005" rgba="0 0 0 0"/>'
                    % (i // 3, vals[i] * 1e-4, vals[i + 1] * 1e-4, vals[i + 2] * 1e-4) for i in range(0, len(vals), 3))
    scene["mjcf"] = "<mujoco><worldbody>%s%s</worldbody></mujoco>" % (sites, "".join(g))
    print("WORLD", {k: round(v, 1) for k, v in P.items()}, "stick", (round(sx), round(sy)), "cyl", (round(cx), round(cy)),
          "hole", k_hole, "distractors", dl)
    return scene


def goal(scene, rng):
    return ("Task: put the cylinder on the stick. On the table there are a cylinder with a hole through it (standing "
            "on one of its flat ends, the hole vertical) and a stand with one vertical stick. Pick up the cylinder and "
            "thread it onto the stick, so that the stick passes through its hole, and let go of it: at the end the "
            "cylinder must be on the stick, the robot not touching it, and the stand upright. Then open the gripper "
            "and return it to its initial pose. The hole can be as little as 1.5 mm wider than the stick. The "
            "cylinder and the stand may stand on the table or on something on it; other objects may be on the table.")


def robot(robot, rng, kit):
    # the real robot's quirks (per episode)
    A = rng.uniform(0.5, 3.5)                       # systematic end-of-move error (mm), smooth over the workspace
    K = [rng.normal(0, 1, 3) * 2 * math.pi / 700 for _ in range(3)]
    PH = rng.uniform(0, 2 * math.pi, 3)
    SIG = rng.uniform(0.2, 0.7)                     # random end-of-move error (mm per axis)
    SOFT = rng.uniform(0, 26) if rng.random() < 0.8 else 0.0   # soft fingers: wide things read narrower
    STALE = rng.uniform(1.0, 5.0)                   # how far behind a look right after a move sees (mm)
    rec = {"t_move": -10.0, "d": np.zeros(3), "defl": 0.0}

    def sys_err(p):
        p = np.asarray(p, float)
        e = np.array([math.sin(float(K[i] @ p) + PH[i]) for i in range(3)]) * A
        e[2] *= 0.5
        return e

    def reading(w):
        if w < 1.5:
            return w, 0.0
        d = 0.3 + SOFT * min(1.0, max(0.0, (w - 14.0) / 22.0))
        r = max(0.4 * w, w - d)
        return r, w - r

    class Real(kit.Robot):
        def rec_state(self, out):
            try:
                s = self.robot.state("left")
                LAST["tcp"] = list(s["tcp_mm"]); LAST["open"] = s["opening_mm"]
                LAST["app"] = list(s["approach_dir"])
            except Exception:
                pass
            return out

        def move(self, arm, xyz, a, b, stop_on_contact=False):
            p0 = np.array(self.robot.state(arm)["tcp_mm"], float)
            x = np.asarray(xyz, float)
            e = sys_err(x) + rng.normal(0, SIG, 3)
            L = float(np.linalg.norm(x - p0))
            if stop_on_contact:
                e[:2] += rng.normal(0, min(3.0, 0.02 * L), 2)
            r = self.robot.move(arm, (x + e).tolist(), a, b, stop_on_contact)
            if not r.get("moved", True):
                r = self.robot.move(arm, x.tolist(), a, b, stop_on_contact)
            if stop_on_contact and L > 2:
                tw = 0.15 * L                       # contact moves are slow on the real rig
                while tw > 0:
                    self.robot.wait(min(10.0, tw)); tw -= 10.0
            p1 = np.array(self.robot.state(arm)["tcp_mm"], float)
            rec["d"] = p1 - p0
            rec["t_move"] = self.robot.time_s()
            return self.rec_state(r)

        def turn(self, arm, centre, axis, ang):
            r = self.robot.turn(arm, centre, axis, ang)
            rec["t_move"] = self.robot.time_s(); rec["d"] = np.zeros(3)
            return self.rec_state(r)

        def home(self, arm):
            return self.rec_state(self.robot.home(arm))

        def set_gripper(self, arm, w):
            out = self.robot.set_gripper(arm, w)
            self.rec_state(out)
            r, rec["defl"] = reading(float(out))
            return r + float(rng.normal(0, 0.1))

        def state(self, arm):
            s = dict(self.robot.state(arm))
            w = float(s["opening_mm"])
            if w > 1.5 and float(s.get("commanded_mm", 80)) < w - 0.5:
                s["opening_mm"] = w - rec["defl"]
            return s

        def look(self, cameras=None):
            out = self.robot.look(cameras)
            if self.robot.time_s() - rec["t_move"] < 0.3 and float(np.linalg.norm(rec["d"])) > 3:
                for name, f in out.items():
                    if name != "left_wrist":
                        continue
                    d = rec["d"] / np.linalg.norm(rec["d"]) * STALE
                    X = np.array(self.robot.state("left")["tcp_mm"], float) - np.array([0, 0, 200.0])
                    uv, _ = f.project(np.array([X, X + d]))
                    du, dv = (uv[1] - uv[0])
                    du, dv = int(round(du)), int(round(dv))
                    rgb = np.roll(f.rgb, (dv, du), axis=(0, 1))
                    dep = np.roll(f.depth, (dv, du), axis=(0, 1))
                    out[name] = kit.frame(f, rgb=rgb, depth=dep)
            return out
    return Real(robot)


def success(state):
    vals = []
    for i in range(8):
        parts = state.scene["mjcf"].split('name="w_P%d" pos="' % i)
        if len(parts) < 2:
            break
        vals += [float(x) * 1e4 for x in parts[1].split('"')[0].split()]
    P = dict(zip(PKEYS, vals))
    pc_, Rc = np.array(state.pos("w_cyl")), np.array(state.rot("w_cyl"))
    Rs = np.array(state.rot("w_stand"))
    base_pt, ax_s = np.array(state.pos("w_stick")), np.array(state.rot("w_stick"))[:, 2]
    H, Ls = P["H"], P["Ls"]
    tilt_s = math.degrees(math.acos(min(1.0, float(Rs[2, 2]))))
    ax_c = Rc[:, 2]
    tilt_rel = math.degrees(math.acos(min(1.0, abs(float(ax_c @ ax_s)))))
    lim = (P["dh"] / 2) / math.cos(math.pi / N_SEG) - P["ds"] / 2 + 0.5
    d, s = [], []
    for q in (pc_, pc_ + H * ax_c):
        v = q - base_pt
        sv = float(v @ ax_s); s.append(sv)
        d.append(float(np.linalg.norm(v - sv * ax_s)))
    on = max(d) <= lim and -3.0 <= s[0] and s[0] + 0.5 <= Ls - min(H, Ls) + 1.0
    tc = state.touching("w_cyl")["robot"]; ts = state.touching("w_stand")["robot"]
    hands_off = not any(tc.values()) if isinstance(tc, dict) else not tc
    hands_off_s = not any(ts.values()) if isinstance(ts, dict) else not ts
    res = {"on_stick": bool(on), "axis_off_mm": round(max(d), 2), "lim_mm": round(lim, 2),
           "bottom_above_base_mm": round(s[0], 1), "stand_tilt_deg": round(tilt_s, 1),
           "cyl_tilt_rel_deg": round(tilt_rel, 1), "hands_off": bool(hands_off and hands_off_s)}
    home_ok = None
    if LAST:
        dist = float(np.linalg.norm(np.array(LAST["tcp"]) - np.array(REST_TCP)))
        home_ok = dist < 25 and LAST.get("open", 0) > 70 and LAST["app"][2] < -0.97
        res["home_dist_mm"] = round(dist, 1); res["final_open_mm"] = round(LAST.get("open", 0), 1)
    res["home_ok"] = home_ok
    res["ok"] = bool(on and tilt_s < 5 and tilt_rel < 8 and res["hands_off"] and home_ok is not False)
    return res
