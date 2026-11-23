"""Scripted reference solutions with the environments' own primitives (move, turn, set_gripper, set_hand, finger_ik):
that a task can be done in the simulation, and how. They read the objects' TRUE poses (no cameras): checks of the
simulation, not baselines for an agent.

    open_bottle(S, log=print)      the left arm holds the bottle, the right one unscrews the cap and lifts it off
    lift(S, log=print)             the nearer arm picks the white tape roll up from above and holds it
    move_across(S, log=print)      the right arm brings the tape roll to the middle, the left one takes it to the left
    insert_cylinder(S, log=print)  (grippers) the nearer arm takes the cylinder from above and lowers it onto the stick
    insert_varied(S, log=print)    (grippers) the same in a varied scene: tries arms and closing directions (a check)
    pick_clutter(S, log=print)     (grippers) takes what covers the target off the heap, then the target (clutter.py)
    biolab_open(S, log=print)      (grippers) tube to the other rack, cap off (biolab_ref.py)
    biolab_drops(S, log=print)     (grippers) tube to the other rack, cap off, three drops, cap on (biolab_ref.py)
    SOLUTIONS[task](S, log)        -> the task's judge's verdict is then the caller's business (tasks.Episode.finish)
"""
from __future__ import annotations

import math

import numpy as np

from .core import Stop

rad = math.radians


def _state(S, tag, log):
    c = S.contacts()
    bp, bR = S.bottle_pose()
    log(f"[{tag}] cap {S.cap_angle():.0f} deg free {S.cap_free}; bottle {bp.round(3)} up {bR[2, 2]:.3f}; "
        + "; ".join(f"{P}: " + (", ".join(f"{k} {v:.1f}" for k, v in sorted(c[P][0].items()) if v > 0.2) or "-") for P in "LR"))


def open_bottle_grippers(S, log=print, hold_z=0.12, step=150, cycles=9):
    from .grippers import grip_R
    bx, by = S.bottle_pose()[0][:2]
    Rl = grip_R((0, -1, 0), (1, 0, 0))                                   # from the +y side, a finger either side in x
    log(f"L pre: {S.move('L', np.array([bx, by + 0.20, 0.40]), Rl)} ({S.last_path})")
    log(f"L down: {S.move('L', np.array([bx, by + 0.20, hold_z]), Rl)}")
    log(f"L in: {S.move('L', np.array([bx, by + 0.005, hold_z]), Rl, guarded=True)}")
    S.set_gripper("L", 0.0, 1.0); _state(S, "L holds", log)
    cc = S.cap_pose()[0] + [0, 0, 0.012]                                 # the cap's centre (mid height)
    Rr = grip_R((0, 0, -1), (1, 0, 0))                                   # (the wrist has the most range this way)
    log(f"R pre: {S.move('R', cc + [0, 0, 0.15], Rr)}")
    log(f"R down: {S.move('R', cc, Rr)}")
    back = 0
    for _ in range(8):                                                   # wind the wrist back first: the whole range
        try:                                                             # for the twist
            S.turn("R", cc, (0, 0, 1), -30); back += 30
        except Stop:
            break
    log(f"wound back {back} deg")
    for cyc in range(cycles):
        S.set_gripper("R", 0.0, 0.8); a0 = S.cap_angle()
        for st in (step, 100, 60):                                       # (less where the wrist has less range)
            try:
                r = S.turn("R", cc, (0, 0, 1), st); break
            except Stop as e:
                log(f"turn by {st} refused: {e}")
        else:
            break
        log(f"cycle {cyc}: {r}; cap turned {S.cap_angle() - a0:.0f} (now {S.cap_angle():.0f}) free {S.cap_free}")
        _state(S, "turned", log)
        if S.cap_free:
            break
        S.set_gripper("R", 0.08, 0.6)
        try:
            S.turn("R", cc, (0, 0, 1), -st)
        except Stop as e:
            log(f"turn back refused: {e}"); break
        cc = S.cap_pose()[0] + [0, 0, 0.012]
    if S.cap_free:
        p, R = S.tool_pose("R"); log(f"lift: {S.move('R', p + [0, 0, 0.10], R)}"); _state(S, "lifted", log)
    S.run(1.0)
    return S.judge_open_bottle()


# ---- the Sharpa hands: a three-finger pinch from above (thumb against index + middle), the palm 75 mm over the cap
def _pinch_pose(c, R):
    return c - R[:, 0] * 0.045 - R[:, 2] * 0.075 - R[:, 1] * 0.012


def _pinch_tips(c, R, rr):
    xh, yh = R[:, 0], R[:, 1]; a = rad(40)
    return {"thumb": c + rr * xh, "index": c + rr * (-math.cos(a) * xh + math.sin(a) * yh),
            "middle": c + rr * (-math.cos(a) * xh - math.sin(a) * yh)}


def _fingers(S, P, tg, dur=0.8):
    t, errs = {}, {}; h0 = S.arms[P].h_cmd.copy()
    for f, X in tg.items():
        j, e = S.finger_ik(P, f, X, h0=h0); t.update(j); errs[f] = round(e * 1000, 1)
    S.set_hand(P, t, dur)
    return errs


def _cap_centre(S):
    p, R = S.cap_pose()
    return p + R[:, 2] * 0.012


def lift_cap_dexterous(S, log=print, r_cap=0.021, squeeze=0.006, raise_fingers=0.007):
    """The cap is free and still on the neck, the right hand around it: let go, centre on where the cap is now, pinch it
    lightly with the index and middle tips raised to the thumb's height (a level pinch: lower, they tip the cap over and
    it slips out on the way up; squeezed harder, it shoots out), and lift slowly. The cap ends up held but tilted."""
    def tips(c, R, rr):
        return {f: X + [0, 0, 0.0 if f == "thumb" else raise_fingers] for f, X in _pinch_tips(c, R, rr).items()}
    _, R = S.tool_pose("R")
    _fingers(S, "R", tips(_cap_centre(S), R, r_cap + 0.012))
    c = _cap_centre(S)
    log(f"centre: {S.move('R', _pinch_pose(c, R), R)}")
    log(f"pinch errs {_fingers(S, 'R', tips(c, R, r_cap - squeeze), 1.0)}")
    p, R = S.tool_pose("R")
    log(f"lift: {S.move('R', p + [0, 0, 0.10], R, v=0.03)}"); _state(S, "lifted", log)


def open_bottle_dexterous(S, log=print, cycles=8, r_cap=0.021, hold_dir=-60.0, hold_z=0.105, pinch_palm=(0, 1, 0), checkpoint=None):
    from .dexterous import hand_R
    bx, by = S.bottle_pose()[0][:2]
    # ---- the left hand holds the bottle, thumb up: it stands left of and behind the bottle, its palm facing it (the
    # palm facing straight right with level fingers puts the Panda's wrist at its limits; turned by 30 degrees it is easy)
    pd = np.array([math.cos(rad(hold_dir)), math.sin(rad(hold_dir)), 0.0]); fd = np.cross([0, 0, 1.0], pd)
    Rl = hand_R(fd, pd); grip = np.array([bx, by, hold_z]) - 0.025 * fd
    log(f"L pre: {S.move('L', grip - 0.16 * pd, Rl)} ({S.last_path})")
    S.set_hand("L", {"thumb_CMC_FE": rad(-10), "thumb_CMC_AA": rad(-20), "thumb_MCP_FE": rad(-30)})
    log(f"L approach: {S.move('L', grip - 0.028 * pd, Rl, guarded=True)}; palm {S.tool_pose('L')[0].round(3)}")
    g = {}
    for f in ("index", "middle", "ring"):
        g.update({f"{f}_MCP_FE": rad(55), f"{f}_PIP": rad(75), f"{f}_DIP": rad(45)})
    g.update({"pinky_MCP_FE": rad(30), "pinky_PIP": rad(40), "pinky_DIP": rad(20)})
    S.set_hand("L", g, 1.2); _state(S, "L holds", log)
    # ---- the right hand pinches the cap from above and turns it, 150 degrees at a time
    R0 = hand_R((0, 0, -1), pinch_palm)
    c = _cap_centre(S); p = _pinch_pose(c, R0)
    log(f"R up: {S.move('R', p + [0, 0, 0.20], R0)} ({S.last_path})")
    pre = {"thumb_CMC_FE": rad(95), "thumb_MCP_FE": rad(0)}
    for f in ("ring", "pinky"):
        pre.update({f"{f}_MCP_FE": rad(90), f"{f}_PIP": rad(100), f"{f}_DIP": rad(60)})
    for f in ("index", "middle"):
        pre.update({f"{f}_MCP_FE": rad(5), f"{f}_PIP": rad(15)})
    S.set_hand("R", pre)
    log(f"R down: {S.move('R', p, R0)}")
    log(f"open pinch errs {_fingers(S, 'R', _pinch_tips(c, R0, r_cap + 0.02))}")
    for cyc in range(cycles):
        R = S.tool_pose("R")[1]
        log(f"pinch errs {_fingers(S, 'R', _pinch_tips(c, R, r_cap - 0.007), 1.0)}")
        a0 = S.cap_angle(); r = None
        for ang in (150, 100, 60):                                     # (a smaller turn where the wrist cannot go round)
            try:
                r = S.turn("R", c, (0, 0, 1), ang); break
            except Stop as e:
                log(f"turn by {ang} refused: {e}"); ang = 0
        if not ang:
            break
        log(f"cycle {cyc}: turn -> {r}; cap turned {S.cap_angle() - a0:.0f} deg (now {S.cap_angle():.0f}), free {S.cap_free}")
        _state(S, "turned", log)
        if S.cap_free:
            break
        pr, R = S.tool_pose("R")
        _fingers(S, "R", _pinch_tips(c, R, r_cap + 0.02))
        S.move("R", pr + [0, 0, 0.04], R)
        S.turn("R", c, (0, 0, 1), -ang)
        c = _cap_centre(S); p = _pinch_pose(c, R0)                     # (the bottle gives a little in the left hand)
        S.move("R", p + [0, 0, 0.04], R0); S.move("R", p, R0)
    if S.cap_free:
        if checkpoint:
            checkpoint(S)
        lift_cap_dexterous(S, log, r_cap)
    S.run(1.0)
    return S.judge_open_bottle()


def open_bottle(S, log=print):
    return (open_bottle_grippers if S.HANDS == "grippers" else open_bottle_dexterous)(S, log=log)


# ---- the white tape roll (50 mm across, 27 mm high, lying flat): taken from above, between the gripper's fingers or in
# a three-finger pinch of the hand tilted 40 deg (palm half down, fingers pointing down and back): with the fingers
# straight down, the thumb does not reach as low as the others, and their knuckles land on the table first
ROLL, ROLL_R, ROLL_H = "tape_white", 0.025, 0.027
ROLL_TILT = 40.0


def _roll_centre(S):
    p, R = S.object_pose(ROLL)
    if R[2, 2] < 0.9:
        raise Stop("the tape roll is not lying flat")
    return p + [0, 0, ROLL_H / 2]


def _roll_tips(P, c, d, rr, lift=0.004):
    """Fingertip targets around the roll's centre c: the thumb on the side d (horizontal), the index and middle
    opposite, 20 deg to each side."""
    w = np.cross([0, 0, 1], d) * (1 if P == "R" else -1); a = rad(20)
    up = np.array([0, 0, lift])
    return {"thumb": c + rr * d + up, "index": c + rr * (-math.cos(a) * d + math.sin(a) * w) + up,
            "middle": c + rr * (-math.cos(a) * d - math.sin(a) * w) + up}


def _roll_hand(P, c, d):
    from .dexterous import hand_R
    t = rad(ROLL_TILT); d = np.asarray(d, float)
    R = hand_R(-math.sin(t) * d + [0, 0, -math.cos(t)], math.cos(t) * d + [0, 0, -math.sin(t)])
    return c - R[:, 0] * 0.045 - R[:, 2] * 0.085 - R[:, 1] * (0.012 if P == "R" else -0.012), R


def _flat(v):
    v = np.array([v[0], v[1], 0.0])
    return v / np.linalg.norm(v)


def pick_roll(S, P, log=print, up=0.12, palm=(1, 0, 0)):
    """Arm P takes the tape roll from above and lifts it by `up`."""
    c = _roll_centre(S)
    if S.HANDS == "grippers":
        R = S.tool_pose(P)[1] if abs(S.tool_pose(P)[1][2, 2] + 1) < 1e-3 else S.fk(P, S.arms[P].home)[1]
        c = c + [0, 0, 0.006]                                           # (the fingertips reach 16 mm below the TCP)
        log(f"{P} above: {S.move(P, c + [0, 0, 0.20], R)} ({S.last_path})")
        log(f"{P} down: {S.move(P, c, R)}")
        log(f"{P} closed to {S.set_gripper(P, 0.0) * 1000:.0f} mm")
    else:
        d = _flat(palm)
        p, R = _roll_hand(P, c, d)
        hi = np.array([0, 0, 0.12])
        log(f"{P} above: {S.move(P, p + hi, R)} ({S.last_path})")
        pre = {"thumb_CMC_FE": rad(95), "thumb_MCP_FE": rad(0)}
        for f in ("ring", "pinky"):                                     # (out of the way)
            pre.update({f"{f}_MCP_FE": rad(80), f"{f}_PIP": rad(90), f"{f}_DIP": rad(50)})
        S.set_hand(P, pre)
        log(f"open pinch errs {_fingers(S, P, _roll_tips(P, c + hi, d, ROLL_R + 0.018))}")
        log(f"{P} down: {S.move(P, p, R, v=0.05)}")
        log(f"pinch errs {_fingers(S, P, _roll_tips(P, c, d, ROLL_R - 0.007), 1.0)}")
    p, R = S.tool_pose(P)
    log(f"{P} lift: {S.move(P, p + [0, 0, up], R, v=0.05)}; roll at {S.object_pose(ROLL)[0].round(3)}, "
        f"held by {sorted(S.touching(ROLL)[0].get(P, []))}")


def put_roll(S, P, xy, log=print, up=0.12):
    """Arm P, holding the tape roll, sets it down at xy and lets go."""
    p, R = S.tool_pose(P)
    g = S.object_pose(ROLL)[0]
    off = p - g                                                         # the tool point relative to the roll's foot
    log(f"{P} over: {S.move(P, np.array([xy[0], xy[1], g[2]]) + off, R)} ({S.last_path})")
    log(f"{P} set down: {S.move(P, np.array([xy[0], xy[1], 0.004]) + off, R, v=0.05)}")
    if S.HANDS == "grippers":
        S.set_gripper(P, 0.08)
    else:
        _fingers(S, P, _roll_tips(P, S.object_pose(ROLL)[0] + [0, 0, ROLL_H / 2], _flat(R[:, 0]), ROLL_R + 0.018))
    p, R = S.tool_pose(P)
    log(f"{P} up: {S.move(P, p + [0, 0, up], R)}; roll at {S.object_pose(ROLL)[0].round(3)} up {S.object_pose(ROLL)[1][2, 2]:.3f}")


def lift(S, log=print, P=None):
    """(By default the arm whose base is nearer.)"""
    y = S.object_pose(ROLL)[0][1]
    pick_roll(S, P or ("L" if y > 0.5 * (S.d.xpos[S.arms["L"].base][1] + S.d.xpos[S.arms["R"].base][1]) else "R"), log)


def move_across(S, log=print, mid=(-0.30, -0.12), goal=(-0.30, 0.47)):
    pick_roll(S, "R", log); put_roll(S, "R", mid, log)
    log(f"R home: {S.go_home('R')}")
    pick_roll(S, "L", log); put_roll(S, "L", goal, log)
    log(f"L home: {S.go_home('L')}")


def insert_cylinder(S, log=print, P=None, offset=(0.0, 0.0), over=0.03, engage=0.03):
    """Grippers only. The cylinder is taken from above at mid height; its axis, as it then sits in the gripper (TRUE
    pose), goes over the stick's (plus `offset`, m: for tests of the fit), is lowered until the stick is `engage`
    into the hole, and released."""
    if S.HANDS != "grippers":
        raise NotImplementedError("insert_cylinder: no reference solution for the Sharpa hands yet")
    c, cR = S.object_pose("cylinder")
    P = P or ("L" if c[1] > 0.5 * (S.d.xpos[S.arms["L"].base][1] + S.d.xpos[S.arms["R"].base][1]) else "R")
    R = S.fk(P, S.arms[P].home)[1]
    h = 2 * float(S.m.geom("cyl_w0").size[2])
    g = c + [0, 0, h / 2 + 0.004]
    log(f"{P} above: {S.move(P, g + [0, 0, 0.15], R)} ({S.last_path})")
    log(f"{P} down: {S.move(P, g, R)}")
    log(f"{P} closed to {S.set_gripper(P, 0.0) * 1000:.1f} mm")
    top, ax = S.stick_top()
    p, _ = S.tool_pose(P)
    log(f"{P} lift: {S.move(P, np.array([p[0], p[1], top[2] + over + h]), R, v=0.05)}")
    c = S.object_pose("cylinder")[0]; p, _ = S.tool_pose(P)
    off = p - c                                                         # the tool point relative to the cylinder's foot
    goal = np.array([top[0] + offset[0], top[1] + offset[1], 0.0])
    log(f"{P} over the stick: {S.move(P, goal + [0, 0, top[2] + over] + off, R)} ({S.last_path})")
    c = S.object_pose("cylinder")[0]; p, _ = S.tool_pose(P)             # (once more, at rest over the stick)
    r = S.move(P, goal + [0, 0, top[2] - engage] + (p - c), R, guarded=True, v=0.03)
    c, cR = S.object_pose("cylinder")
    log(f"{P} lowered: {r}; cylinder foot {((c - top) * 1000).round(1)} mm from the stick's top, tilt {math.degrees(math.acos(min(cR[2, 2], 1))):.1f} deg")
    S.set_gripper(P, 0.08)
    p, _ = S.tool_pose(P)
    log(f"{P} up: {S.move(P, p + [0, 0, 0.12], R)}")


def _insert_once(S, P, yaw, log, over=0.03):
    """One attempt of insert_varied: arm P, the fingers closing along the table direction at `yaw` (rad)."""
    from .grippers import grip_R
    c = S.object_pose("cylinder")[0]
    g0 = S.m.geom("cyl_w0")
    h, dia = 2 * float(g0.size[2]), 2 * (float(np.linalg.norm(g0.pos[:2])) + float(g0.size[0]))
    u = np.array([math.cos(yaw), math.sin(yaw), 0.0])
    Rh = S.fk(P, S.arms[P].home)[1]
    R = grip_R((0, 0, -1), u if u @ Rh[:, 1] >= 0 else -u)
    g = c + [0, 0, max(h / 2 + 0.004, 0.014)]
    S.move(P, g + [0, 0, 0.15], R)
    S.set_gripper(P, min(0.08, dia + 0.024), duration=0.3)
    why = S.move(P, g, R)
    w = S.set_gripper(P, 0.0)
    if why or w < 0.5 * dia:
        raise Stop(f"no grasp ({why}, closed to {w * 1000:.0f} mm)")
    top, ax = S.stick_top()
    p, _ = S.tool_pose(P)
    S.move(P, np.array([p[0], p[1], top[2] + over + h]), R, v=0.05)
    c = S.object_pose("cylinder")[0]; p, _ = S.tool_pose(P)
    cmd = np.array([top[0], top[1], top[2] + over]) + (p - c)
    for _ in range(4):                                                  # the arm sags under the load: command the difference
        S.move(P, cmd, R)
        e = S.object_pose("cylinder")[0] - np.array([top[0], top[1], top[2] + over])
        if np.linalg.norm(e[:2]) < 0.00015:
            break
        cmd = cmd - [e[0], e[1], 0.0]
    engage = min(0.03, 0.6 * float(S.m.geom("stand_stick").size[1]) * 2)
    r = S.move(P, cmd - [0, 0, over + engage], R, guarded=True, v=0.03)
    c = S.object_pose("cylinder")[0]
    log(f"{P} yaw {math.degrees(yaw):.0f}: lowered ({r}); cylinder foot {((c - top) * 1000).round(1)} mm from the stick's top")
    S.set_gripper(P, min(0.08, dia + 0.03))
    p, _ = S.tool_pose(P)
    S.move(P, p + [0, 0, 0.10], R)


def insert_varied(S, log=print):
    """Grippers only: insert_cylinder for the varied scenes (scenes.py), as a check that a scene can be done: from a
    snapshot of the start it tries each arm (the nearer first) with the fingers closing along each of 6 directions
    (the one with the most room around the cylinder first, by the TRUE positions of everything) until one attempt
    puts the cylinder on the stick; the simulation is left as the last attempt left it."""
    if S.HANDS != "grippers":
        raise NotImplementedError("insert_varied: no reference solution for the Sharpa hands yet")
    st0 = S.get_state()
    c = S.object_pose("cylinder")[0]
    mid = 0.5 * (S.d.xpos[S.arms["L"].base][1] + S.d.xpos[S.arms["R"].base][1])
    arms = ("L", "R") if c[1] > mid else ("R", "L")
    obst = [S.d.geom_xpos[g][:2] for n in S.layout if n != "cylinder" for g in S.obj_geoms[n]]
    def room(yaw):                                                      # how far the nearest thing is from the hand's line
        u = np.array([math.cos(yaw), math.sin(yaw)])
        return min((abs(float((o - c[:2]) @ [-u[1], u[0]])) + max(abs(float((o - c[:2]) @ u)) - 0.11, 0.0) for o in obst), default=1.0)
    yaws = sorted((math.pi * k / 6 for k in range(6)), key=lambda y: -room(y))
    last = None
    for P in arms:
        for yaw in yaws:
            S.set_state(st0)
            try:
                _insert_once(S, P, yaw, log)
            except Stop as e:
                last = e; log(f"{P} yaw {math.degrees(yaw):.0f}: {e}")
                continue
            last = None
            st1 = S.get_state()
            S.run(1.0)
            ok = S.judge_insert()["ok"]
            S.set_state(st1)
            if ok:
                return
    if last is not None:
        raise last


# ---------------------------------------------------------------------- picking from clutter (clutter.py)
def _cloud(S, name, step=0.006):
    """Points on the surfaces of an object's collision geoms (m, table frame): boxes, spheres and capsules."""
    import mujoco
    m, d = S.m, S.d
    k = np.arange(60) + 0.5
    ball = np.stack([np.cos(math.pi * (1 + 5 ** 0.5) * k) * np.sqrt(1 - (1 - k / 30) ** 2),
                     np.sin(math.pi * (1 + 5 ** 0.5) * k) * np.sqrt(1 - (1 - k / 30) ** 2), 1 - k / 30], axis=1)
    out = []
    for g in S.obj_geoms[name]:
        t, z, p, R = m.geom_type[g], m.geom_size[g], d.geom_xpos[g], d.geom_xmat[g].reshape(3, 3)
        if t == mujoco.mjtGeom.mjGEOM_BOX:
            n = [max(int(2 * h / step) + 1, 2) for h in z]
            for ax in range(3):
                a, b = [i for i in range(3) if i != ax]
                A, B = np.meshgrid(np.linspace(-z[a], z[a], n[a]), np.linspace(-z[b], z[b], n[b]))
                for sg in (-1.0, 1.0):
                    q = np.zeros((A.size, 3)); q[:, a] = A.ravel(); q[:, b] = B.ravel(); q[:, ax] = sg * z[ax]
                    out.append(q @ R.T + p)
        elif t == mujoco.mjtGeom.mjGEOM_SPHERE:
            out.append(ball * z[0] + p)
        elif t == mujoco.mjtGeom.mjGEOM_CAPSULE:
            out += [ball * z[0] + p + R[:, 2] * h for h in np.linspace(-z[1], z[1], max(int(2 * z[1] / step) + 1, 2))]
    return np.vstack(out)


def _grasps(S, name):
    """Top-down grasps of an object as it lies (TRUE poses), best first: [(tool point, closing direction, width m)].
    For each of 12 closing directions and a few lines across the object: what would be between the fingers has to
    fit, the fingers have to come down inside the bin if there is one, and the less of the other objects is where
    the fingers come down, the better."""
    from . import clutter as C
    sc = S.scene
    pts = _cloud(S, name)
    oth = np.vstack([_cloud(S, n) for n in C.names(sc) if n != name])   # (the bin's walls and floor too)
    zmin, zmax = pts[:, 2].min(), pts[:, 2].max()
    zc = max(0.5 * (zmin + zmax) + 0.004, zmin + 0.014)
    com = S.object_pose(name)[0]
    mids = [com] + [S.d.geom_xpos[g] for g in S.obj_geoms[name] if S.m.geom_contype[g]]
    inner = None
    if sc.get("bin"):
        bp, bR = S.object_pose("bin")
        inner = np.array(sc["bin"]["inner_mm"]) / 2000 - 0.010
    out = []
    for k in range(12):
        a = math.pi * k / 12
        u, v = np.array([math.cos(a), math.sin(a), 0.0]), np.array([-math.sin(a), math.cos(a), 0.0])
        pu, pv = pts @ u, pts @ v
        for v0 in sorted({round(float(q @ v), 3) for q in mids}):
            band = (np.abs(pv - v0) < 0.011) & (pts[:, 2] > zc - 0.016)
            if not band.any():
                continue
            lo, hi = pu[band].min(), pu[band].max()
            w, u0 = hi - lo, 0.5 * (lo + hi)
            if not 0.004 < w < 0.070:
                continue
            g = u * u0 + v * v0 + [0, 0, zc]
            if inner is not None and any(np.any(np.abs(((g + sg * u * (w / 2 + 0.018)) - bp) @ bR)[:2] > inner) for sg in (-1, 1)):
                continue
            du, dv = np.abs(oth @ u - u0), np.abs(oth @ v - v0)
            blocked = int(np.sum((dv < 0.014) & (du > w / 2) & (du < w / 2 + 0.030) & (oth[:, 2] > zc - 0.014)))
            out.append((blocked, float(np.linalg.norm((com - g)[:2])), w, g, u))
    out.sort(key=lambda c: (c[0], c[1], c[2]))
    return [(g, u, w) for _, _, w, g, u in out]


def _over(S, P, xyz, u):
    """Arm P's tool point to xyz, pointing down, the fingers closing along the table direction u (either sign): up,
    across, then the wrist's turn, each a move the arm can check. Raises Stop where it cannot."""
    p, R = S.tool_pose(P)
    top = max(xyz[2], p[2], S.table_z + 0.25)
    if np.linalg.norm(p[:2] - xyz[:2]) > 0.005:
        S.move(P, np.array([p[0], p[1], top]), R)
        S.move(P, np.array([xyz[0], xyz[1], top]), R)
    S.move(P, xyz, R)
    y = S.tool_pose(P)[1][:, 1]
    a = math.degrees(math.atan2(y[0] * u[1] - y[1] * u[0], y[0] * u[0] + y[1] * u[1]))
    a = a - 180 if a > 90 else a + 180 if a < -90 else a
    last = None
    for turn in (a, a - 180 if a > 0 else a + 180):
        try:
            if abs(turn) > 1:
                S.turn(P, xyz, (0, 0, 1), turn)
            return
        except Stop as e:
            last = e
    raise last


def _take(S, arms, name, log, tries=4):
    """An arm (the first of `arms` that can) takes an object from above and raises it by 12 cm -> the arm that then
    has it, or None."""
    for i in range(tries):
        cands = _grasps(S, name)
        if not cands:
            log(f"{name}: no grasp from above"); return None
        g, u, w = cands[min(i, len(cands) - 1)]
        z0 = S.object_pose(name)[0][2]
        for P in arms:
            why, wm, at = None, 0.0, False
            try:
                _over(S, P, np.array([g[0], g[1], max(g[2] + 0.15, S.table_z + 0.25)]), u); at = True
                S.set_gripper(P, min(0.08, w + 0.026), duration=0.3)
                p, R = S.tool_pose(P)
                why = S.move(P, g, R)
                wm = S.set_gripper(P, 0.0)
                p, R = S.tool_pose(P)
                S.move(P, p + [0, 0, 0.12], R, v=0.08)
            except Stop as e:
                why = str(e)[:60]
            up = S.object_pose(name)[0][2] - z0
            log(f"{P} {name}: try {i}: width {w * 1000:.0f} mm, down ({why}), closed to {wm * 1000:.0f} mm, rose {up * 1000:.0f} mm")
            if up > 0.07:
                return P
            _retreat(S, P)
            if at:                                                      # (the arm got there: the grasp was the trouble)
                break
            try:
                S.go_home(P)
            except Stop:
                pass
    return None


def _retreat(S, P):
    """Open and straight up (as far as the arm lets it)."""
    S.set_gripper(P, 0.08, duration=0.3)
    p, R = S.tool_pose(P)
    for z in (S.table_z + 0.30, p[2] + 0.10, p[2] + 0.04):
        try:
            S.move(P, np.array([p[0], p[1], max(z, p[2])]), R); return
        except Stop:
            continue


def _push(S, arms, name, d, dist, log):
    """An arm, closed, pushes an object along the table direction d by about `dist` beyond where it lies."""
    pts = _cloud(S, name)
    c = 0.5 * (pts.min(0) + pts.max(0))
    d = np.array([d[0], d[1], 0.0]) / float(np.hypot(d[0], d[1]))
    z = max(c[2], pts[:, 2].min() + 0.014)
    a = c - d * (float(c @ d - (pts @ d).min()) + 0.035); a[2] = z
    b = c + d * dist; b[2] = z
    for P in arms:
        try:
            S.set_gripper(P, 0.0, duration=0.3)
            _over(S, P, np.array([a[0], a[1], max(z + 0.15, S.table_z + 0.25)]), np.array([-d[1], d[0], 0.0]))
            p, R = S.tool_pose(P)
            r1 = S.move(P, a, R, guarded=True)
            p, _ = S.tool_pose(P)
            r2 = S.move(P, np.array([b[0], b[1], p[2]]), R, v=0.08)
            q = _cloud(S, name)
            log(f"{P} pushed {name} along {d[:2].round(2)}: down ({r1}), along ({r2}); it moved {float((0.5 * (q.min(0) + q.max(0)) - c) @ d) * 1000:.0f} mm")
            _retreat(S, P)
            return
        except Stop as e:
            log(f"{P} push of {name}: {str(e)[:60]}")
            _retreat(S, P)
            try:
                S.go_home(P)
            except Stop:
                pass


def pick_clutter(S, log=print, rounds=14):
    """Grippers only: the pick_clutter_* tasks (clutter.py), by the TRUE poses. While the target is covered (by rays
    from above, clutter.visibility), the highest object over it is taken away and dropped on the table beside the heap
    (or pushed aside if no grasp holds it); then the target is taken from above and held up. One go, no going back
    to an earlier state: where it fails, the heap was too much for this procedure, not necessarily for a robot."""
    from . import clutter as C
    if S.HANDS != "grippers":
        raise NotImplementedError("pick_clutter: no reference solution for the Sharpa hands yet")
    sc = S.scene
    t = f"item_{sc['target']}"
    items = [n for n in C.names(sc) if n != "bin"]
    mid = 0.5 * (S.d.xpos[S.arms["L"].base][1] + S.d.xpos[S.arms["R"].base][1])
    arms = ("L", "R") if S.object_pose(t)[0][1] > mid + 0.10 else ("R", "L")
    c0 = S.object_pose("bin")[0][:2].copy() if sc.get("bin") else np.mean([S.object_pose(n)[0][:2] for n in items], axis=0)
    rad = (float(np.linalg.norm(np.array(sc["bin"]["inner_mm"]) / 2000)) + 0.13) if sc.get("bin") else 0.24
    dropped = 0

    def spot(P):
        nonlocal dropped
        xy = [S.object_pose(n)[0][:2] for n in items]
        best = None
        for k in range(16):
            a = (-1 if P == "R" else 1) * math.pi / 2 + 2 * math.pi * ((dropped + k) % 16) / 16 * (-1) ** k
            q = c0 + rad * np.array([math.cos(a), math.sin(a)])
            if not (-0.50 <= q[0] <= -0.10 and (-0.50 if P == "R" else -0.15) <= q[1] <= (0.10 if P == "R" else 0.13)):
                continue
            best = q if best is None else best
            if min(float(np.linalg.norm(q - o)) for o in xy) > 0.09:
                best = q; break
        dropped += 1
        return best if best is not None else c0 + [0.0, -rad if P == "R" else rad]

    def away(P, name):
        """Drop what arm P holds beside the heap, and the arm out of the other's way."""
        q = spot(P)
        p, R = S.tool_pose(P)
        r = float(np.linalg.norm(_cloud(S, name) - S.object_pose(name)[0], axis=1).max())
        try:
            S.move(P, np.array([p[0], p[1], S.table_z + 0.28]), R)
            S.move(P, np.array([q[0], q[1], S.table_z + 0.28]), R)
            S.move(P, np.array([q[0], q[1], S.table_z + r + 0.04]), R, guarded=True)
        except Stop as e:
            log(f"{P} {name} away: {str(e)[:60]}")
        _retreat(S, P)

    for rnd in range(rounds):
        seen, cover = C.visibility(sc, C.poses(S))
        tp = S.object_pose(t)[0]
        if seen["top"] >= 0.7 or not cover:
            P = _take(S, arms, t, log)
            if P:
                p, R = S.tool_pose(P)
                for z in (0.26, 0.22, 0.19):
                    try:
                        S.move(P, np.array([p[0], p[1], S.table_z + z]), R, v=0.08); break
                    except Stop:
                        continue
                S.run(0.5)
                v = C.judge(S)
                log(f"{P} has the target after {rnd} round(s), robot time {S.d.time:.0f} s: {v}")
                if v["held"] and not v["touches"] and v["height_mm"] >= C.LIFT:
                    return
                if v["held"]:                                           # (something came along, or it hangs too low)
                    S.move(P, np.array([p[0], p[1], max(tp[2] + 0.03, S.table_z + 0.05)]), R, guarded=True)
                _retreat(S, P)
                continue
            near = sorted((n for n in items if n != t), key=lambda n: float(np.linalg.norm(S.object_pose(n)[0] - tp)))
            block = near[:1]                                            # (no grasp: the nearest neighbour goes first)
        else:
            block = sorted((f"item_{k}" for k in cover), key=lambda n: -S.object_pose(n)[0][2])
        if not block:
            break
        n = block[0]
        log(f"round {rnd}: the target is seen {seen['top']:.0%} from above; {n} goes")
        P = _take(S, arms, n, log, tries=2)
        if P:
            away(P, n)
        else:
            d = S.object_pose(n)[0][:2] - tp[:2]
            if np.linalg.norm(d) < 0.015:
                d = (c0 - tp[:2]) if sc.get("bin") and np.linalg.norm(c0 - tp[:2]) > 0.02 else np.array([0.0, -1.0 if arms[0] == "R" else 1.0])
            _push(S, arms, n, d, 0.07 if sc.get("bin") else 0.14, log)
    raise Stop(f"the target is not in the hand after {rounds} rounds")


def biolab_open(S, log=print):
    """Grippers only: the tube to the other rack and its cap off (biolab.py), by the TRUE poses; the first stages of biolab_ref.py."""
    from . import biolab_ref
    return biolab_ref.solve(S, log, task="biolab_open")


def biolab_drops(S, log=print):
    """Grippers only: the bio-lab task (biolab.py), by the TRUE poses; the stages are in biolab_ref.py."""
    from . import biolab_ref
    return biolab_ref.solve(S, log)


SOLUTIONS = {"open_bottle": open_bottle, "lift": lift, "move_across": move_across, "insert_cylinder": insert_cylinder,
             "insert_varied": insert_varied, "pick_clutter_medium": pick_clutter, "pick_clutter_hard": pick_clutter,
             "pick_clutter_extra_hard": pick_clutter, "biolab_open": biolab_open, "biolab_drops": biolab_drops}
