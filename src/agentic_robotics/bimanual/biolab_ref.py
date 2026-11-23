"""The bio-lab task's reference solution (grippers), with the environment's own primitives and the objects' TRUE poses:
a check that the task can be done in the simulation, and how; not a baseline for an agent.

The cap arm (C) carries the tube by its cap to the other rack; the other arm (H) holds the tube there from the side
while C unscrews the cap, then lets go. The pipette arm (P) takes the pipette by its bulb, squeezes it, dips it in the
cup, lets the bulb out, squeezes three drops into the tube and puts the pipette back. P is C (design 1: C puts the cap
down on the table meanwhile and takes it again) or H (design 2: C waits with the cap in its fingers). H holds the
tube again and C sets the cap on and screws it down.

    solve(S, log=print)      plan(S, K, log), then STAGES[K["design"]][1:] over the context dict K
"""
from __future__ import annotations

import math

import numpy as np

from . import biolab as B
from .core import Stop
from .grippers import grip_R

OPEN = 0.055                # the opening before a grasp
TRAVEL = 0.140              # the height things are carried at (the tube's bottom, the pipette's tip) above the table
CAP_UP = 0.007              # the tool point above the underside of the cap's lid (the fingertips at the skirt's middle)
PIP_GRIP = (B.BULB_Z[0] + B.BULB_Z[1]) / 2000 + 0.002     # the tool point above the pipette's tip (the bulb's middle)
LIFT = 1.2                  # mm over a hole's bottom at which the tube is held (pressed on it, it kicks out sideways)
DOWN = (0, 0, -1)
ANGLES = (0, 180, 90, 270, 45, 225, 135, 315)
COMPASS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]


def _u(deg):
    return np.array([math.cos(math.radians(deg)), math.sin(math.radians(deg)), 0.0])


def _down(deg):
    """The tool pointing down, its fingers closing along the table direction at deg."""
    return grip_R(DOWN, _u(deg))


def _free(S, P, p, R, ignore=(), back=(), opening=None):
    """A joint solution of arm P at (p, R) that touches nothing (nor at the distances `back` behind, along the
    approach), its fingers as they are or at that opening -> q | None."""
    A = S.arms[P]; keep = S.d.qpos[A.fq].copy()
    if opening is not None:
        S.d.qpos[A.fq] = opening / 2
    try:
        for q in S.solutions(P, np.asarray(p, float), R, tries=12):
            if S.collides(P, q, ignore):
                continue
            q2 = q
            for b in back:
                q2, ep, er = S.ik(P, p - b * R[:, 2], R, q2)
                if ep > 0.001 or er > 0.01 or S.collides(P, q2, ignore):
                    break
            else:
                return q
        return None
    finally:
        S.d.qpos[A.fq] = keep


def _route(S, P, q, pts, R):
    """Can arm P, its joints at q (the tool at pts[0]), follow the straight lines through pts with the orientation R?
    -> the joints at the end | None."""
    for a, b in zip(pts[:-1], pts[1:]):
        a, b = np.asarray(a, float), np.asarray(b, float)
        n = max(int(math.ceil(np.linalg.norm(b - a) / 0.01)), 1)
        for i in range(1, n + 1):
            q2, ep, er = S.ik(P, a + (b - a) * i / n, R, q)
            if ep > 0.002 or er > math.radians(1.5) or np.abs(q2 - q).max() > 0.3:
                return None
            q = q2
    return q


def _go(S, P, p, R, heights=(0.30, 0.38, 0.24)):
    """A free hand of arm P to the pose (p, R): up, across and down, the turn at the far end or before leaving, at
    several heights (lines are not checked against the things on the table: they are flown over); or by the joints
    (checked), from above where it is, from above the pose, or straight from where it is."""
    p, T, errs = np.asarray(p, float), S.table_z, []
    ways = [(h, w) for h in heights for w in "ab"] + [(heights[0], "c"), (heights[0], "d"), (0.0, "e")]
    for h, way in ways:
        p0, R0 = S.tool_pose(P)
        a, b = np.array([p0[0], p0[1], max(T + h, p0[2])]), np.array([p[0], p[1], max(T + h, p[2])])
        if way in "cde" and math.acos(np.clip((np.trace(R0.T @ R) - 1) / 2, -1, 1)) < math.radians(45):
            continue
        steps = {"a": [(a, R0), (b, R0), (b, R), (p, R)], "b": [(a, R0), (a, R), (b, R), (p, R)],
                 "c": [(a, R0), (p + [0, 0, 0.12], R), (p, R)], "d": [(a, R0), (p, R)], "e": [(p, R)]}[way]
        try:
            for q, Rq in steps:
                pc, Rc = S.tool_pose(P)
                if np.linalg.norm(pc - q) > 0.001 or np.abs(Rc - Rq).max() > 0.01:
                    S.move(P, q, Rq)
            return
        except Stop as e:
            errs.append(f"{way}{h:g}: {str(e)[:70]}")
    raise Stop("no way there: " + "; ".join(errs))


def _top(S, P, xyz, R, up):
    """Arm P's tool at (xyz, R), coming straight down from `up` m above."""
    xyz = np.asarray(xyz, float)
    _go(S, P, xyz + [0, 0, up], R)
    S.move(P, xyz, R)


def _shift(S, P, d, v=None, guarded=False):
    p, R = S.tool_pose(P)
    return S.move(P, p + np.asarray(d, float), R, v=v, guarded=guarded)


def _ids(S, *names):
    return {S.m.body(n).id for n in names}


def _pip_ids(S):
    return _ids(S, "obj_pipette", *(f"pip_b{k}" for k in range(B.PLATES)))


def _tube_ids(S):
    return _ids(S, "obj_tube", "tube_cap", "tube_cap_free")


def _hole(S, k, i, j):
    rk = S.scene["racks"][k]
    pr, Rr = S.object_pose(B.NAMES[k])
    return pr + Rr @ [*(B.cell_local(rk, i, j) / 1000), 0.0]


def _tilt(S, name):
    return math.degrees(math.acos(min(1.0, S.object_pose(name)[1][2, 2])))


# ---------------------------------------------------------------- what an arm can do

def _cap_ways(S, C, rd, hole):
    """The closing directions (deg) with which arm C can take the tube by its cap and carry it to that hole."""
    M, T = S.mech, S.table_z
    cap = S.d.xpos[M.b_cap] + [0, 0, CAP_UP]
    lift = TRAVEL - (S.object_pose("tube")[0][2] - T)
    xy = _hole(S, rd, *hole)
    pts = [cap, cap + [0, 0, lift], [xy[0], xy[1], cap[2] + lift], [xy[0], xy[1], T + (B.BASE + B.TUBE_L) / 1000 + CAP_UP]]
    out = []
    for a in ANGLES:
        q = _free(S, C, cap, _down(a), _tube_ids(S), back=(0.05,))
        if q is not None and _route(S, C, q, pts, _down(a)) is not None:
            out.append(a)
    return out


def _pip_points(S, mouth_xy):
    """The tool point's way with the pipette: out of its hole, over the cup, in, out, over the tube's mouth."""
    M, T, c = S.mech, S.table_z, S.scene["cup"]
    pp, Rp = S.object_pose("pipette")
    g, cup = pp + Rp[:, 2] * PIP_GRIP, S.object_pose("cup")[0]
    top, dip = T + TRAVEL + PIP_GRIP, T + max(c["level_mm"] - 10.0, B.BASE + 6.0) / 1000 + PIP_GRIP
    return [g, [g[0], g[1], top], [cup[0], cup[1], top], [cup[0], cup[1], dip], [cup[0], cup[1], top], [mouth_xy[0], mouth_xy[1], top],
            [g[0], g[1], top]]


def _pip_ways(S, P, mouth_xy):
    pts = _pip_points(S, mouth_xy); out = []
    for a in ANGLES:
        q = _free(S, P, pts[0], _down(a), _pip_ids(S), back=(0.04,))
        if q is not None and _route(S, P, q, pts, _down(a)) is not None:
            out.append(a)
    return out


def _hold_poses(S, H, rd, hole, other=None, limit=99):
    """Ways arm H can hold the tube that stands in that hole, from the side (level, or coming down at an angle):
    [(height, Rh, out)]. other = (arm, q): with the other arm's joints there meanwhile."""
    rk = S.scene["racks"][rd]
    pr, Rr = S.object_pose(B.NAMES[rd])
    i, j = hole; nx, ny = rk["cells"]
    xy = _hole(S, rd, i, j)
    edge = [v for v in COMPASS if all((c == 0) or (c < 0 and k == 0) or (c > 0 and k == n - 1) for c, k, n in ((v[0], i, nx), (v[1], j, ny)))]
    z_mid, z_high = (rk["height_mm"] + 98.0) / 2000, min(rk["height_mm"] + 34.0, 88.0) / 1000   # (the fingers stay 5 mm under the cap)
    every = edge + [v for v in COMPASS if v not in edge]
    cands = [(v, z_mid, 0) for v in edge] + [(v, z_high, 0) for v in every] + [(v, z_high, tilt) for tilt in (30, 50) for v in every]
    out = []
    if other:
        A = S.arms[other[0]]; keep = S.d.qpos[A.qa].copy(); S.d.qpos[A.qa] = other[1]
    try:
        for v, z, tilt in cands:
            o = Rr @ [v[0], v[1], 0.0]; o = o / np.linalg.norm(o)
            ap = -o * math.cos(math.radians(tilt)) - np.array([0, 0, 1.0]) * math.sin(math.radians(tilt))   # (from above, at that angle)
            for sgn in (1, -1):
                Rh = grip_R(ap, sgn * np.cross([0, 0, 1], o))
                if _free(S, H, np.array([xy[0], xy[1], pr[2] + z]), Rh, _tube_ids(S), back=(0.05, 0.10)) is not None:
                    out.append((float(pr[2] + z), Rh, o))
                    if len(out) >= limit:
                        return out
    finally:
        if other:
            S.d.qpos[A.qa] = keep
    return out


def plan(S, K, log):
    """Which hole the tube goes to, which arm carries and turns the cap, which holds the tube, which works the pipette."""
    M, sc, T = S.mech, S.scene, S.table_z
    for P in "LR":
        S.set_gripper(P, OPEN, 0.4)
    K["home"] = {P: [v.tolist() for v in S.tool_pose(P)] for P in "LR"}
    rd = 1 - M.tube_cell()[0]
    taken = set()
    pc = M.pipette_cell()
    if pc:
        taken.add(pc)
    for k, e in enumerate(sc["extras"]):
        if e["kind"] == "vial":
            c = M.in_cell(S.m.body(f"obj_extra_{k}").id, B.BASE)
            if c:
                taken.add(c)
    holes = [(i, j) for i, j in B.cells(sc["racks"][rd]) if (rd, i, j) not in taken]
    lone = lambda c: not any(k == rd and B._near(c, (i, j)) for k, i, j in taken)
    holes.sort(key=lambda c: (not lone(c),))
    why = []
    for i, j in holes:
        xy = _hole(S, rd, i, j)
        caps = {C: _cap_ways(S, C, rd, (i, j)) for C in "LR"}
        pips = {P: _pip_ways(S, P, xy) for P in "LR"}
        for design, C in ((1, "L"), (1, "R"), (2, "L"), (2, "R")):
            H = "R" if C == "L" else "L"
            P = C if design == 1 else H
            if not caps[C] or not pips[P]:
                continue
            qc = S.solve(C, np.array([xy[0], xy[1], T + (B.BASE + B.TUBE_L) / 1000 + CAP_UP]), _down(caps[C][0]), 12)
            if not _hold_poses(S, H, rd, (i, j), other=(C, qc), limit=1):
                why.append(f"({i}, {j}) {design}{C}: no hold"); continue
            K.update(C=C, H=H, P=P, design=design, rack=rd, hole=(i, j))
            log(f"plan {design}: tube to {B.NAMES[rd]} hole ({i}, {j}){'' if lone((i, j)) else ' (a neighbour is taken)'}; {C} takes the cap "
                f"{caps[C]}, {H} holds the tube, {P} works the pipette {pips[P]}")
            return
        why.append(f"({i}, {j}): cap {caps}, pipette {pips}")
    raise Stop("no way found: " + "; ".join(why)[:400])


# ---------------------------------------------------------------- the stages

def take_tube(S, K, log):
    """The cap arm takes the tube by its cap, carries it over to the hole and sets it in."""
    M, C, T = S.mech, K["C"], S.table_z
    rk = S.scene["racks"][K["rack"]]
    tip = lambda: S.object_pose("tube")[0]
    hole = lambda: _hole(S, K["rack"], *K["hole"])[:2]
    cap = S.d.xpos[M.b_cap] + [0, 0, CAP_UP]
    lift = TRAVEL - (tip()[2] - T)
    pts = [cap, cap + [0, 0, lift], [*hole(), cap[2] + lift], [*hole(), T + (B.BASE + B.TUBE_L) / 1000 + CAP_UP]]
    err = Stop("no way to take the tube")
    for a in _cap_ways(S, C, K["rack"], K["hole"]):
        try:
            _top(S, C, cap, _down(a), 0.10)
        except Stop as e:
            err = e; continue
        if _route(S, C, S.arms[C].q_cmd, pts, _down(a)) is not None:    # (from the joints the arm has come with)
            break
    else:
        raise err
    w = S.set_gripper(C, 0.0, 0.8)
    log(f"{C} holds the cap ({a} deg, {w * 1000:.1f} mm)")
    _shift(S, C, [0, 0, TRAVEL - (tip()[2] - T)])
    for _ in range(2):
        d = hole() - tip()[:2]
        _shift(S, C, [d[0], d[1], 0])
    _shift(S, C, [0, 0, rk["height_mm"] / 1000 + 0.004 - (tip()[2] - T)])
    d = hole() - tip()[:2]
    _shift(S, C, [d[0], d[1], 0])
    r1 = _shift(S, C, [0, 0, (B.BASE + 4.0) / 1000 - (tip()[2] - T)], v=0.02)
    r2 = _shift(S, C, [0, 0, (B.BASE + LIFT) / 1000 - (tip()[2] - T)], v=0.01, guarded=True)
    log(f"tube set in ({r1}, {r2}): its tip {((tip() - [*hole(), T]) * 1000).round(1)} mm from the hole's centre on the table; cell {M.tube_cell()}")


def hold(S, K, log):
    """The holding arm takes the tube's body from the side, over the rack, and sets it upright."""
    H = K["H"]
    err = Stop("no way to hold the tube")
    for z, Rh, o in _hold_poses(S, H, K["rack"], K["hole"]):
        pt, Rt = S.object_pose("tube")
        ax = pt + Rt[:, 2] * (z - pt[2]) / max(Rt[2, 2], 0.5)            # the tube's axis at the height of the hold
        try:
            _go(S, H, ax - 0.10 * Rh[:, 2], Rh, heights=(0.24, 0.30, 0.18))
            r = S.move(H, ax, Rh)
            if r:                                                       # (it touched something on the way in)
                S.move(H, ax - 0.10 * Rh[:, 2], Rh)
                raise Stop(f"the way in is not free: {r}")
        except Stop as e:
            err = e; continue
        w = S.set_gripper(H, 0.0, 0.8)
        t0 = _tilt(S, "tube")
        if not S.mech.on:                                               # (the cap arm does not hold it)
            _shift(S, H, [0, 0, 0.002])
            for _ in range(3):
                pt, Rt = S.object_pose("tube")
                n = np.cross(Rt[:, 2], [0, 0, 1.0]); t = math.degrees(math.asin(min(1.0, np.linalg.norm(n))))
                if t < 0.7:
                    break
                try:
                    S.turn(H, S.tool_pose(H)[0], n / np.linalg.norm(n), t)
                except Stop as e:
                    log(f"not set upright: {e}"); break
            pt = S.object_pose("tube")[0]
            d = [*(_hole(S, K["rack"], *K["hole"])[:2] - pt[:2]), S.table_z + (B.BASE + LIFT) / 1000 - pt[2]]
            _shift(S, H, d)                                             # its tip over the hole's middle
        log(f"{H} holds the tube from {math.degrees(math.atan2(o[1], o[0])):.0f} deg, {(z - S.table_z) * 1000:.0f} mm up ({w * 1000:.1f} mm); "
            f"tube tilt {t0:.1f} -> {_tilt(S, 'tube'):.1f} deg")
        return
    raise err


def let_go(S, K, log):
    H = K["H"]
    S.set_gripper(H, OPEN, 0.4)
    _shift(S, H, -0.10 * S.tool_pose(H)[1][:, 2])
    retire(S, K, log)


def retire(S, K, log):
    """The holding arm out of the way (home), if it can get there now."""
    H = K["H"]
    if np.abs(S.arms[H].q_cmd - S.arms[H].home).max() < 0.01:
        return
    for up in (0.0, 0.10, 0.20, None):
        try:
            if up:
                _shift(S, H, [0, 0, up])
            elif up is None:
                _go(S, H, np.array(K["home"][H][0]), np.array(K["home"][H][1]))
            S.go_home(H)
            return
        except Stop as e:
            err = e
    log(f"{H} not home: {err}")


def unscrew(S, K, log, step=150):
    M, C = S.mech, K["C"]
    cc = S.tool_pose(C)[0]
    S.set_gripper(C, OPEN, 0.4)
    back = 0
    for _ in range(8):
        try:
            S.turn(C, cc, (0, 0, 1), -30); back += 30
        except Stop:
            break
    for cyc in range(8):
        S.set_gripper(C, 0.0, 0.6); a0 = M.cap_angle()
        for st in (step, 100, 60, 30):
            try:
                r = S.turn(C, cc, (0, 0, 1), st); break
            except Stop as e:
                r = str(e)
        else:
            raise Stop(f"the cap arm cannot turn: {r}")
        log(f"unscrew {cyc}: turned {st} ({r}); cap {'off' if not M.on else f'{M.cap_angle():.0f} deg'} (from {a0:.0f}); wound back {back}")
        if not M.on:
            break
        S.set_gripper(C, OPEN, 0.4)
        for _ in range(int(st // 30)):
            try:
                S.turn(C, cc, (0, 0, 1), -30)
            except Stop:
                break
    if M.on:
        raise Stop("the cap did not come off")
    _shift(S, C, [0, 0, 0.06])


def cap_away(S, K, log):
    """The cap arm, the cap in its fingers, out of the way (home)."""
    C = K["C"]
    K["R_cap"] = S.tool_pose(C)[1].tolist()
    try:
        S.go_home(C)
    except Stop as e:
        log(f"{C} not home: {e}")
    log(f"{C} waits with the cap: {sorted(S.touching(S.mech.b_capf)[0].get(C, []))}")


def cap_back(S, K, log):
    """The cap arm brings the cap over the tube's mouth."""
    M, C = S.mech, K["C"]
    d = S.tool_pose(C)[0] - S.d.xpos[M.b_capf]
    _go(S, C, M.mouth()[0] + [0, 0, 0.06] + d, np.array(K["R_cap"]))
    if C not in S.touching(M.b_capf)[0]:
        raise Stop("the cap is not in the fingers")


def cap_down(S, K, log):
    """(design 1) The cap onto a free place on the table."""
    M, C, sc, T = S.mech, K["C"], S.scene, S.table_z
    pm = M.mouth()[0]
    p, R = S.tool_pose(C)
    pip, cup = S.object_pose("pipette")[0][:2] * 1000, S.object_pose("cup")[0][:2] * 1000
    clear = []
    for x in np.arange(-0.48, -0.06, 0.02):
        for y in np.arange(-0.24, 0.17, 0.02):
            q = np.array([x, y]) * 1000
            if min(B._rect_dist(dict(r, pos_mm=(S.object_pose(n)[0][:2] * 1000).tolist()), q) for n, r in zip(B.NAMES, sc["racks"])) < 55:
                continue
            if np.linalg.norm(q - cup) < sc["cup"]["inner_mm"] / 2 + 55:
                continue
            if any(e["kind"] == "block" and np.linalg.norm(q - S.object_pose(f"extra_{k}")[0][:2] * 1000) < math.hypot(*e["size_mm"][:2]) / 2 + 50
                   for k, e in enumerate(sc["extras"])):
                continue
            clear.append((float(np.linalg.norm([x - pm[0], y - pm[1]])), x, y))
    held, err = S.held_bodies(C), Stop("no free place on the table for the cap")
    d = p - S.d.xpos[M.b_capf]
    for _, x, y in sorted(clear):
        g = np.array([x + d[0], y + d[1], T + (B.CAP_SKIRT + 1.5) / 1000 + d[2]])
        top = np.array([g[0], g[1], max(g[2] + 0.08, p[2])])
        if _free(S, C, g, R, held, opening=OPEN) is None or _route(S, C, S.arms[C].q_cmd, [p, [p[0], p[1], top[2]], top, g], R) is None:
            continue
        try:
            S.move(C, np.array([p[0], p[1], top[2]]), R); S.move(C, top, R); break
        except Stop as e:
            err = e
    else:
        raise err
    _shift(S, C, [0, 0, T + (B.CAP_SKIRT + 1.5) / 1000 - S.d.xpos[M.b_capf][2]])
    S.set_gripper(C, OPEN, 0.4)
    K["R_down"] = S.tool_pose(C)[1].tolist()                            # the hand fits there this way round
    _shift(S, C, [0, 0, 0.10])
    S.run(0.3)
    pc = S.d.xpos[M.b_capf]
    log(f"cap on the table at ({pc[0] * 1000:.0f}, {pc[1] * 1000:.0f}), {(pc[2] - T) * 1000:.1f} mm up, level {S.d.xmat[M.b_capf].reshape(3, 3)[2, 2]:.3f}")


def cap_up(S, K, log):
    """(design 1) The cap arm takes the cap from the table again."""
    M, C = S.mech, K["C"]
    pc = S.d.xpos[M.b_capf].copy()
    g, err = pc + [0, 0, CAP_UP], Stop("no way to take the cap from the table")
    for R in [np.array(K["R_down"])] + [_down(a) for a in ANGLES]:
        if _free(S, C, g, R, _tube_ids(S), opening=OPEN) is None:
            continue
        try:
            _go(S, C, g + [0, 0, 0.08], R)
            r = S.move(C, g, R)
            if r:
                S.move(C, g + [0, 0, 0.08], R)
                raise Stop(f"the way down to the cap is not free: {r}")
            break
        except Stop as e:
            err = e
    else:
        raise err
    w = S.set_gripper(C, 0.0, 0.8)
    _shift(S, C, [0, 0, 0.10])
    log(f"{C} has the cap again ({w * 1000:.1f} mm): held {sorted(S.touching(M.b_capf)[0].get(C, []))}, level {S.d.xmat[M.b_capf].reshape(3, 3)[2, 2]:.3f}")
    if C not in S.touching(M.b_capf)[0]:
        raise Stop("the cap is not in the fingers")


def take_pipette(S, K, log):
    M, P = S.mech, K["P"]
    K["pip_home"] = M.pipette_cell()
    pts, err = _pip_points(S, M.mouth()[0]), Stop("no way to take the pipette")
    for a in _pip_ways(S, P, M.mouth()[0]):
        try:
            _top(S, P, pts[0], _down(a), 0.08)
        except Stop as e:
            err = e; continue
        if _route(S, P, S.arms[P].q_cmd, pts, _down(a)) is not None:
            break
    else:
        raise err
    K["w_hold"] = S.set_gripper(P, 0.022, 0.8)
    _shift(S, P, [0, 0, TRAVEL - (M.tip()[2] - S.table_z)])
    log(f"{P} holds the pipette ({a} deg): opening {K['w_hold'] * 1000:.1f} mm, squeeze {M.squeeze():.2f} mm, tilt {_tilt(S, 'pipette'):.1f} deg")


def _tip_over(S, P, xy):
    M = S.mech
    for _ in range(2):
        d = np.asarray(xy) - M.tip()[:2]
        if np.linalg.norm(d) > 0.0008:
            _shift(S, P, [d[0], d[1], 0])


def draw(S, K, log):
    M, P, c = S.mech, K["P"], S.scene["cup"]
    _tip_over(S, P, S.object_pose("cup")[0][:2])
    w = S.set_gripper(P, K["w_hold"] - 0.009, 0.8)
    _shift(S, P, [0, 0, max(c["level_mm"] - 10.0, B.BASE + 6.0) / 1000 - (M.tip()[2] - S.table_z)])
    log(f"tip in the water: {M.in_water()} (opening {w * 1000:.1f}, squeeze {M.squeeze():.2f})")
    w = S.set_gripper(P, K["w_hold"], 0.8)
    log(f"drawn: {M.V / B.DROP_MM:.1f} drops in the pipette (opening {w * 1000:.1f}, squeeze {M.squeeze():.2f})")
    _shift(S, P, [0, 0, TRAVEL - (M.tip()[2] - S.table_z)])


def drop(S, K, log, mm=(B.DROPS + 0.5) * B.DROP_MM):
    """Over the tube's mouth, the fingers closer by what three drops take (and half a drop), read on the opening."""
    M, P = S.mech, K["P"]
    _tip_over(S, P, M.mouth()[0][:2])
    w0 = S.width(P); goal = w0 - mm / 1000; cmd = goal
    for _ in range(4):
        w = S.set_gripper(P, cmd, 1.0)
        if abs(w - goal) < 0.00015:
            break
        cmd += goal - w
    S.run(0.3)
    log(f"squeezed by {(w0 - w) * 1000:.2f} mm: drops {M.counts()}; left in the pipette {M.V / B.DROP_MM:.1f}")


def return_pipette(S, K, log):
    M, P, T = S.mech, K["P"], S.table_z
    k, i, j = K["pip_home"]
    rk = S.scene["racks"][k]
    xy = _hole(S, k, i, j)[:2]
    _tip_over(S, P, xy)
    _shift(S, P, [0, 0, (rk["height_mm"] + 6.0) / 1000 - (M.tip()[2] - T)])
    _tip_over(S, P, xy)
    _shift(S, P, [0, 0, (rk["height_mm"] - B.COLLAR_Z[0] + 2.0) / 1000 - (M.tip()[2] - T)], v=0.02)
    S.set_gripper(P, OPEN, 0.5)
    _shift(S, P, [0, 0, 0.10])
    log(f"pipette back: cell {M.pipette_cell()}, drops {M.counts()}")


def cap_on(S, K, log):
    """The cap over the mouth, the wrist wound forward (so it can screw down at once), then down until the thread catches."""
    M, C = S.mech, K["C"]
    cap = lambda: S.d.xpos[M.b_capf].copy()

    def over(tol=0.0003):
        for _ in range(3):
            pm, Rt = M.mouth()
            e = (pm + Rt[:, 2] * (cap()[2] - pm[2]) / Rt[2, 2] - cap())[:2]      # the tube's axis at the cap's height
            if np.linalg.norm(e) < tol:
                break
            _shift(S, C, [e[0], e[1], 0], v=0.02)

    over(0.001)
    for _ in range(5):
        try:
            S.turn(C, S.tool_pose(C)[0], (0, 0, 1), 30)
        except Stop:
            break
    _shift(S, C, [0, 0, M.mouth()[0][2] + (B.CAP_SKIRT + 3.0) / 1000 - cap()[2]])    # the skirt 3 mm over the rim
    over()
    _shift(S, C, [0, 0, M.mouth()[0][2] + 0.006 - cap()[2]], v=0.02)
    top = B.K_THREAD * math.radians(B.UNSCREW_DEG)
    for k in range(16):
        if M.on:
            break
        over()
        rel = cap() - M.mouth()[0]
        if M.on or rel[2] < top - 0.0015:
            break
        _shift(S, C, [0, 0, -min(0.001, max(rel[2] - top + 0.0002, 0.0003))], v=0.01)
    pm, Rt = M.mouth()
    rel = Rt.T @ (S.d.xpos[M.cap_body()] - pm) * 1000
    log(f"cap set on: caught {M.on} ({'' if not M.on else f'{M.cap_angle():.0f} deg, '}cap from the rim {rel.round(2)} mm, "
        f"tilts {_tilt(S, 'tube'):.1f} / {math.degrees(math.acos(min(1.0, S.d.xmat[M.cap_body()].reshape(3, 3)[2, 2]))):.1f} deg)")
    if not M.on:
        raise Stop("the cap did not catch the thread")


def screw(S, K, log, step=150):
    M, C = S.mech, K["C"]
    cc = S.tool_pose(C)[0]
    for cyc in range(10):
        if not M.on:
            raise Stop("the cap came off again")
        a0 = M.cap_angle()
        if a0 < 8:
            break
        if cyc:
            S.set_gripper(C, OPEN, 0.4)
            fwd = 0
            while fwd < min(step, a0 + 10):                               # wind forward what the turn back needs
                try:
                    S.turn(C, cc, (0, 0, 1), 30); fwd += 30
                except Stop:
                    break
            S.set_gripper(C, 0.0, 0.6)
        r = "no turn"
        for st in sorted({min(s, a0 - 3) for s in (step, 100, 60, 30)}, reverse=True):
            try:
                r = S.turn(C, cc, (0, 0, 1), -st); break
            except Stop as e:
                r = str(e)[:60]
        log(f"screw {cyc}: turned -{st:.0f} ({r}); cap {M.cap_angle() if M.on else -1:.0f} deg (from {a0:.0f})")


def leave(S, K, log):
    C = K["C"]
    S.set_gripper(C, OPEN, 0.4)
    _shift(S, C, [0, 0, 0.12])
    let_go(S, K, log)
    try:
        S.go_home(C)
    except Stop as e:
        log(f"{C} not home: {e}")
    retire(S, K, log)


STAGES = {1: [plan, take_tube, hold, unscrew, let_go, cap_down, retire, take_pipette, draw, drop, return_pipette, cap_up, cap_away, hold, cap_back, cap_on, screw, leave],
          2: [plan, take_tube, hold, unscrew, cap_away, let_go, take_pipette, draw, drop, return_pipette, hold, cap_back, cap_on, screw, leave]}


def solve(S, log=print):
    """Grippers only: plan(), then the stages of the design it chose, over one context dict."""
    if S.HANDS != "grippers":
        raise NotImplementedError("biolab_drops: no reference solution for the Sharpa hands yet")
    K = {}
    plan(S, K, log)
    for f in STAGES[K["design"]][1:]:
        f(S, K, log)
