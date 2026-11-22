"""Scripted reference solutions with the environments' own primitives (move, turn, set_gripper, set_hand, finger_ik):
that a task can be done in the simulation, and how. They read the objects' TRUE poses (no cameras): checks of the
simulation, not baselines for an agent.

    open_bottle(S, log=print)      the left arm holds the bottle, the right one unscrews the cap and lifts it off
    lift(S, log=print)             the nearer arm picks the white tape roll up from above and holds it
    move_across(S, log=print)      the right arm brings the tape roll to the middle, the left one takes it to the left
    insert_cylinder(S, log=print)  (grippers) the nearer arm takes the cylinder from above and lowers it onto the stick
    insert_varied(S, log=print)    (grippers) the same in a varied scene: tries arms and closing directions (a check)
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


SOLUTIONS = {"open_bottle": open_bottle, "lift": lift, "move_across": move_across, "insert_cylinder": insert_cylinder,
             "insert_varied": insert_varied}
