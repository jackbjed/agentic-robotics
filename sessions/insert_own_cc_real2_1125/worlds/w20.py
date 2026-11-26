import math
import numpy as np

# w17 (real table): the real rig's black table (z = 0 +- 1 mm), its stand (a long wooden board with three holes
# in a row and ONE stick in an end hole) and its flat book; cylinders of many kinds; platforms; the real robot's
# habits (arrival errors of plain moves, soft fingers that read narrower than what they hold, TCP z >= 10).
N_SEG = 24
REST_TCP = (-478.0, 41.0, 350.0)
LAST = {}
RIG = {}
PKEYS = ["ds", "dh", "od", "H", "Ls", "bt", "pc", "ps", "hexa"]
GAP, DGAP, ND = 6.0, 8.0, (0, 4)
PLAT = (15.0, 100.0)
KINDS = ["box", "sphere", "cyl", "lying", "tallbox", "decoy", "disc"]


def _col(rng, dark=0.12):
    k = rng.random()
    if k < dark:
        return list(np.array([rng.uniform(0.02, 0.2), rng.uniform(0.02, 0.2), rng.uniform(0.02, 0.2)]))
    k = rng.random()
    if k < 0.15:
        g = rng.uniform(0.08, 0.95)
        return [g, g, g]
    if k < 0.3:
        return list(np.array([0.75, 0.55, 0.35]) * rng.uniform(0.6, 1.2))
    h = rng.uniform(0, 1); s = rng.uniform(0.35, 1.0); v = rng.uniform(0.3, 1.0)
    i = int(h * 6) % 6; f = h * 6 - int(h * 6)
    p_, q_, t_ = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    return [[v, t_, p_], [q_, v, p_], [p_, v, t_], [p_, q_, v], [t_, p_, v], [v, p_, q_]][i]


def _rgba(c):
    return "%.3f %.3f %.3f 1" % tuple(min(1.0, max(0.0, x)) for x in c)


def _fr(x):
    return "%.2f 0.005 0.0001" % x


def _box(sx, sy, sz, px, py, pz, yaw, rgba, extra):
    m = 0.001
    return ('<geom type="box" size="%.5f %.5f %.5f" pos="%.5f %.5f %.5f" euler="0 0 %.5f" rgba="%s" %s/>'
            % (sx * m, sy * m, sz * m, px * m, py * m, pz * m, yaw, rgba, extra))


def scene(scene, rng):
    LAST.clear(); RIG.clear()
    m = 0.001
    scene["table"] = {"height_mm": float(rng.uniform(-1, 1)), "color": "black"}
    scene["cameras"] = {
        "depth": {"noise_px": float(rng.uniform(0.12, 0.3))},
        "scene": {"pose_error": {"shift_mm": float(rng.uniform(2, 8)), "turn_deg": float(rng.uniform(0.3, 1.5))}},
        "left_wrist": {"pose_error": {"shift_mm": float(rng.uniform(1, 5)), "turn_deg": float(rng.uniform(0.3, 2.0))}}}
    # ---- the stand (the real one, measured from the real episodes, with some slack)
    Lb, Wb, bt = rng.uniform(270, 290), rng.uniform(80, 92), rng.uniform(19, 23)
    ds = rng.uniform(9.2, 10.2)                    # the stick's diameter
    Ls = rng.uniform(165, 175)                     # the stick above the board
    lean = rng.uniform(0, 1.2); lean_az = rng.uniform(0, 2 * math.pi)
    end = 50.0 + rng.uniform(-3, 3)                # the stick's hole from the board's end
    # ---- the cylinder
    hexa = 1.0 if rng.random() < 0.5 else 0.0
    c = rng.uniform(1.5, 2.5) if rng.random() < 0.4 else rng.uniform(2.5, 6.0)
    dh = ds + c
    od = dh + 2 * rng.uniform(4, 20)
    od = min(max(od, dh + 8, 24.0), 62.0 if hexa else 66.0)
    H = rng.uniform(12, 50)
    pc = rng.uniform(*PLAT) if rng.random() < 0.3 else 0.0
    ps = rng.uniform(*PLAT) if rng.random() < 0.3 else 0.0
    yaw_s = rng.uniform(0, 2 * math.pi)
    co, si = math.cos(yaw_s), math.sin(yaw_s)
    plat_c = od + rng.uniform(60, 120) if pc > 0 else 0.0
    r_c = max(od * 0.6 + 40, plat_c * 0.71)
    # the stand's footprint: two circles along the board (and its platform's margin)
    marg = rng.uniform(15, 40) if ps > 0 else 0.0
    for _ in range(5000):
        sx, sy = rng.uniform(-470, -120), rng.uniform(-190, 230)     # the stick
        # the board's centre: from the stick towards the other end
        bx, by = sx + co * (Lb / 2 - end), sy + si * (Lb / 2 - end)
        foot = [(bx + co * k * Lb / 4, by + si * k * Lb / 4, Lb / 4 + Wb / 2 + marg) for k in (-1, 1)]
        if not all(-560 < f[0] < -60 and -260 < f[1] < 320 for f in foot):
            continue
        cx, cy = rng.uniform(-460, -130), rng.uniform(-190, 230)
        if all(math.hypot(cx - fx, cy - fy) > r_c + fr + GAP for fx, fy, fr in foot):
            break
    feet = [(cx, cy, r_c)] + foot
    g = []
    # ring: origin at the bottom centre
    cylc = _col(rng)
    cylc = cylc if max(cylc) >= 0.3 else [0.3 + x for x in cylc]
    rho_c = rng.uniform(300, 8000); fr_c = _fr(rng.uniform(0.3, 1.0))
    yaw_c = rng.uniform(0, 2 * math.pi)
    rin = dh / 2; hb = 0.5
    # priority: its own friction holds in all its contacts (MuJoCo would otherwise take the larger one, and the
    # simulator's table has 1.0: every cylinder would grip the table like rubber)
    ex = 'density="%.0f" friction="%s" priority="1"' % (rho_c, fr_c)
    rg = _rgba(cylc)
    segs = []
    if hexa:
        t_in = 2.0
        Lh = (rin + t_in) * math.tan(math.pi / N_SEG) + 0.3
        for k in range(N_SEG):
            an = yaw_c + 2 * math.pi * k / N_SEG
            rc = rin + t_in / 2
            segs.append(_box(t_in / 2, Lh, (H - hb) / 2, rc * math.cos(an), rc * math.sin(an), hb + (H - hb) / 2,
                             an, rg, ex))
        tb = od / 2 - rin - 1.0
        for k in range(6):
            an = yaw_c + 2 * math.pi * k / 6
            rc = od / 2 - tb / 2
            segs.append(_box(tb / 2, od / (2 * math.sqrt(3)), (H - hb) / 2, rc * math.cos(an), rc * math.sin(an),
                             hb + (H - hb) / 2, an, rg, ex))
    else:
        t = (od - dh) / 2
        Lh = (od / 2) * math.tan(math.pi / N_SEG) + 0.3
        for k in range(N_SEG):
            an = yaw_c + 2 * math.pi * k / N_SEG
            rc = rin + t / 2
            segs.append(_box(t / 2, Lh, (H - hb) / 2, rc * math.cos(an), rc * math.sin(an), hb + (H - hb) / 2,
                             an, rg, ex))
    tf = min(4.0, (od / 2 - rin) * 0.8)
    for k in range(8):                  # the feet at the outer edge: it stands as wide as a real one
        an = yaw_c + 2 * math.pi * (k + 0.5) / 8
        rc = od / 2 - tf / 2 - 0.3
        segs.append(_box(tf / 2 * 0.9, 2.0, H / 2, rc * math.cos(an), rc * math.sin(an), H / 2, an, rg, ex))
    g.append('<body name="w_cyl" pos="%.5f %.5f %.5f"><joint type="free" damping="0.003"/>%s</body>' %
             (cx * m, cy * m, (pc + 0.3) * m, "".join(segs)))
    # stand: origin at the board's bottom centre, x along the board
    wood = np.array([0.88, 0.62, 0.36]) * rng.uniform(0.85, 1.1)
    stick_c = np.array([0.62, 0.42, 0.25]) * rng.uniform(0.8, 1.15)
    rho_b = rng.uniform(450, 800); fr_b = _fr(rng.uniform(0.3, 0.7))
    sg = ('<geom type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.0f" friction="%s"/>'
          % (Lb / 2 * m, Wb / 2 * m, bt / 2 * m, bt / 2 * m, _rgba(wood), rho_b, fr_b))
    hx = -Lb / 2 + end
    for k in (1, 2):                    # the two empty holes (marks)
        sg += ('<geom type="cylinder" size="%.5f 0.0002" pos="%.5f 0 %.5f" rgba="0.25 0.17 0.1 1" contype="0" '
               'conaffinity="0"/>' % (5.2 * m, (hx + 90 * k) * m, bt * m))
    # the stick: its own frame at its foot (the board's top), z along it (it may lean a little)
    lx = math.sin(math.radians(lean)) * math.cos(lean_az); ly = math.sin(math.radians(lean)) * math.sin(lean_az)
    zax = np.array([lx, ly, math.sqrt(max(0.0, 1 - lx * lx - ly * ly))])
    xax = np.cross([0, 1, 0], zax); xax /= np.linalg.norm(xax)
    sk = ('<body name="w_stick" pos="%.5f 0 %.5f" xyaxes="%.5f %.5f %.5f %.5f %.5f %.5f">'
          '<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.0f" friction="%s"/>'
          '<body name="w_sticktop" pos="0 0 %.5f"/></body>'
          % (hx * m, bt * m, *xax, *np.cross(zax, xax), ds / 2 * m, Ls / 2 * m, Ls / 2 * m, _rgba(stick_c),
             rng.uniform(600, 900), fr_b, Ls * m))
    g.append('<body name="w_stand" pos="%.5f %.5f %.5f" euler="0 0 %.5f"><freejoint/>%s%s</body>' %
             (bx * m, by * m, (ps + 0.3) * m, yaw_s, sg, sk))
    if pc > 0:
        pyaw = rng.uniform(0, math.pi / 2)
        g.append('<body name="w_plat_c" pos="%.5f %.5f %.5f" euler="0 0 %.4f"><geom type="box" size="%.5f %.5f %.5f" '
                 'rgba="%s" friction="0.6 0.005 0.0001"/></body>' % (cx * m, cy * m, pc / 2 * m, pyaw, plat_c / 2 * m,
                                                                      plat_c / 2 * m, pc / 2 * m, _rgba(_col(rng))))
    if ps > 0:
        g.append('<body name="w_plat_s" pos="%.5f %.5f %.5f" euler="0 0 %.4f"><geom type="box" size="%.5f %.5f %.5f" '
                 'rgba="%s" friction="0.6 0.005 0.0001"/></body>' % (bx * m, by * m, ps / 2 * m, yaw_s,
                                                                      (Lb / 2 + marg) * m, (Wb / 2 + marg) * m,
                                                                      ps / 2 * m, _rgba(_col(rng))))
    # distractors: the flat book (most of the time) and 0-4 others
    dl = []
    if rng.random() < 0.85:
        bw, bd_, bh = rng.uniform(190, 240), rng.uniform(130, 170), rng.uniform(10, 22)
        bc = [0.45, 0.65, 0.8] if rng.random() < 0.6 else _col(rng, 0.0)
        for _ in range(300):
            x, y = rng.uniform(-470, -120), rng.uniform(-200, 260)
            r = 0.5 * math.hypot(bw, bd_)
            if all(math.hypot(x - fx, y - fy) > r + fr + DGAP for fx, fy, fr in feet):
                feet.append((x, y, r))
                g.append('<body name="w_book" pos="%.5f %.5f 0.0003" euler="0 0 %.4f"><freejoint/><geom type="box" '
                         'size="%.5f %.5f %.5f" pos="0 0 %.5f" rgba="%s" density="600" friction="0.5"/></body>'
                         % (x * m, y * m, rng.uniform(0, 3.14), bw / 2 * m, bd_ / 2 * m, bh / 2 * m, bh / 2 * m,
                            _rgba(bc)))
                dl.append("book")
                break
    nd = int(rng.integers(ND[0], ND[1] + 1))
    for i in range(nd):
        kind = rng.choice(KINDS)
        if kind == "tallbox":
            sz = [rng.uniform(15, 26), rng.uniform(15, 26), rng.uniform(50, 120)]; r = 0.71 * max(sz[0], sz[1])
            geo = 'type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f"' % (sz[0] / 2 * m, sz[1] / 2 * m, sz[2] / 2 * m,
                                                                       sz[2] / 2 * m)
        elif kind == "decoy":
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
            x, y = rng.uniform(-480, -100), rng.uniform(-240, 300)
            if all(math.hypot(x - fx, y - fy) > r + fr + DGAP for fx, fy, fr in feet):
                feet.append((x, y, r))
                g.append('<body name="w_d%d" pos="%.5f %.5f 0.0003" euler="0 0 %.4f"><freejoint/><geom %s rgba="%s" '
                         'density="%.0f" friction="%s"/></body>' % (i, x * m, y * m, rng.uniform(0, 6.28), geo,
                                                                    _rgba(_col(rng)), rng.uniform(300, 3000),
                                                                    _fr(rng.uniform(0.3, 1.0))))
                dl.append(str(kind))
                break
    scene["mjcf"] = "<mujoco><worldbody>%s</worldbody></mujoco>" % "".join(g)
    P = dict(ds=ds, dh=dh, od=od, H=H, Ls=Ls, bt=bt, pc=pc, ps=ps, hexa=hexa)
    vals = [float(P[k]) for k in PKEYS]
    vals += [0.0] * (-len(vals) % 3)
    sites = "".join('<site name="w_P%d" pos="%.7f %.7f %.7f" size="0.0005" rgba="0 0 0 0"/>'
                    % (i // 3, vals[i] * 1e-4, vals[i + 1] * 1e-4, vals[i + 2] * 1e-4) for i in range(0, len(vals), 3))
    scene["mjcf"] = scene["mjcf"].replace("<worldbody>", "<worldbody>" + sites, 1)
    P.update(cyl=(round(cx), round(cy)), stick=(round(sx), round(sy)), lean=round(lean, 2), distractors=dl,
             rho_c=round(rho_c))
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
    # the real robot's habits: a plain move ends a little off its goal (the arm reports where it is);
    # the soft fingers read narrower than what they hold; the TCP may not go below z = 10 mm.
    soft = float(rng.uniform(0.0, 0.6))
    ebias = rng.normal(0, 0.5, 3)            # the arm's own offset this episode (as on the real arm)
    RIG["t"] = 0.0

    class Real(kit.Robot):
        def _rec(self, out):
            try:
                st = self.robot.state("left")
                LAST["tcp"] = list(st["tcp_mm"]); LAST["open"] = st["opening_mm"]
                LAST["app"] = list(st["approach_dir"])
            except Exception:
                pass
            return out

        def move(self, arm, xyz, a, b, stop_on_contact=False):
            if float(xyz[2]) < 10.0:
                return {"done": False, "moved": False, "why": "the tool point may not go below z = 10 mm",
                        "curved": False}
            p0 = np.array(self.robot.state(arm)["tcp_mm"], float)
            r = self.robot.move(arm, xyz, a, b, stop_on_contact)
            p1 = np.array(self.robot.state(arm)["tcp_mm"], float)
            dist = float(np.linalg.norm(p1 - p0))
            if stop_on_contact:
                RIG["t"] += 2.0 + dist / 3.5
            else:
                RIG["t"] += 2.5 + dist / (40.0 if min(p0[2], p1[2]) < 300 else 150.0)
                if r.get("done") and dist > 0.3:
                    sg = min(1.4, 0.25 + 0.006 * dist)
                    e = np.clip(ebias * min(1.0, dist / 20.0) + rng.normal(0, [sg, sg, 0.8 * sg]), -3.5, 3.5)
                    self.robot.move(arm, list(np.asarray(xyz, float) + e), a, b, False)
            return self._rec(r)

        def turn(self, arm, centre, axis, ang):
            RIG["t"] += 2.0 + abs(ang) / 30.0
            return self._rec(self.robot.turn(arm, centre, axis, ang))

        def home(self, arm):
            RIG["t"] += 6.0
            return self._rec(self.robot.home(arm))

        def look(self, cameras=None):
            RIG["t"] += 0.5
            return self.robot.look(cameras)

        def set_gripper(self, arm, w):
            RIG["t"] += 1.0
            r = self._rec(self.robot.set_gripper(arm, w))
            if w < 5 and r > 12:
                r = r - soft * (r - 12)
            return r
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
    pk, Rk = np.array(state.pos("w_stick")), np.array(state.rot("w_stick"))
    Ls, H = P["Ls"], P["H"]
    ax_s = Rk[:, 2]
    tilt_s = math.degrees(math.acos(min(1.0, Rs[2, 2])))
    ax_c = Rc[:, 2]
    tilt_rel = math.degrees(math.acos(min(1.0, abs(float(ax_c @ ax_s)))))
    lim = (P["dh"] / 2) / math.cos(math.pi / N_SEG) - P["ds"] / 2 + 0.5
    d, s = [], []
    for q in (pc_, pc_ + H * ax_c):
        v = q - pk
        sv = float(v @ ax_s); s.append(sv)
        d.append(float(np.linalg.norm(v - sv * ax_s)))
    on = max(d) <= lim and -3.0 <= s[0] and s[0] + 0.5 <= Ls - min(H, Ls) + 1.0
    tc = state.touching("w_cyl")["robot"]; ts = state.touching("w_stand")["robot"]
    hands_off = not any(tc.values()) if isinstance(tc, dict) else not tc
    hands_off_s = not any(ts.values()) if isinstance(ts, dict) else not ts
    res = {"on_stick": bool(on), "axis_off_mm": round(max(d), 2), "lim_mm": round(lim, 2),
           "bottom_above_board_mm": round(s[0], 1), "stand_tilt_deg": round(tilt_s, 1),
           "cyl_tilt_rel_deg": round(tilt_rel, 1), "hands_off": bool(hands_off and hands_off_s),
           "rig_time_est_s": round(RIG.get("t", 0.0), 1)}
    home_ok = None
    if LAST:
        tcp = LAST.get("tcp")
        dist = float(np.linalg.norm(np.array(tcp) - np.array(REST_TCP)))
        home_ok = dist < 25 and LAST.get("open", 0) > 70 and LAST["app"][2] < -0.97
        res["home_dist_mm"] = round(dist, 1); res["final_open_mm"] = round(LAST.get("open", 0), 1)
    res["home_ok"] = home_ok
    res["ok"] = bool(on and tilt_s < 5 and tilt_rel < 8 and res["hands_off"] and home_ok is not False)
    return res
