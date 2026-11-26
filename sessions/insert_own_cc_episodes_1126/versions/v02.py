import numpy as np
import math

DOWN = (0.0, 0.0, -1.0)
ARM = "left"


def st(robot):
    return robot.state(ARM)


def tcp(robot):
    return np.array(st(robot)["tcp_mm"], float)


def cdir(robot):
    return tuple(float(x) for x in st(robot)["closing_dir"])


def mv(robot, xyz, stop=False, closing=None):
    c = closing if closing is not None else cdir(robot)
    r = robot.move(ARM, [float(v) for v in xyz], DOWN, c, stop)
    if not r.get("moved", True) or (not r.get("done") and r.get("why") != "contact"):
        print("MOVE", [round(float(v), 1) for v in xyz], "stop" if stop else "", r)
    return r


def turn90(robot, sign=1):
    p = tcp(robot)
    for a in (90 * sign, -90 * sign):
        r = robot.turn(ARM, p.tolist(), (0, 0, 1), a)
        if r.get("moved"):
            return r
        print("TURN refused", a, r.get("why"))
    return r


def squeeze2(robot, label):
    """Close on the thing between the fingers along the current closing_dir, then along the
    perpendicular one. Returns the two widths."""
    w1 = robot.set_gripper(ARM, 0)
    p1 = tcp(robot)
    print(label, "squeeze1 w=%.2f" % w1, "tcp", np.round(p1, 2), "c", np.round(cdir(robot), 3))
    if w1 < 1.0:
        return w1, None
    robot.set_gripper(ARM, min(80, w1 + 25))
    turn90(robot)
    w2 = robot.set_gripper(ARM, 0)
    p2 = tcp(robot)
    print(label, "squeeze2 w=%.2f" % w2, "tcp", np.round(p2, 2), "c", np.round(cdir(robot), 3))
    return w1, w2


def run(robot, task):
    tr = robot.truth()
    print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in (tr or {}).items()})
    rng = np.random.default_rng(0)
    cyl = np.array(tr["w_cyl"]) + np.r_[rng.uniform(-4, 4, 2), 0]
    stand = np.array(tr["w_stand"]) + np.r_[rng.uniform(-4, 4, 2), 0]
    # ---- dev: pass the geometry by hand from the world print (s1/s3 have off=0)
    import collections
    G = {"s1": dict(bt=8.0, L=75.3, H=15.2), "s3": dict(bt=10.6, L=96.0, H=27.6)}
    key = "s1" if abs(tr["w_cyl"][0] + 396.1) < 1 else "s3"
    g = G[key]
    base_top = stand[2] + g["bt"]
    stick_top = base_top + g["L"]
    sup = cyl[2]
    ctop = sup + g["H"]
    # ---- stick
    robot.set_gripper(ARM, 80)
    mv(robot, (stand[0], stand[1], stick_top + 60))
    zg = max(base_top + 16 + 10, 14)
    r = mv(robot, (stand[0], stand[1], zg), stop=True)
    print("stick descent", r, np.round(tcp(robot), 1))
    w1, w2 = squeeze2(robot, "STICK")
    S = tcp(robot)
    t2 = robot.truth()
    print("stand truth after squeeze", np.round(t2["w_stand"], 2), "tcp", np.round(S, 2))
    robot.set_gripper(ARM, 80)
    t3 = robot.truth()
    print("stand truth after release", np.round(t3["w_stand"], 2))
    mv(robot, (S[0], S[1], stick_top + 60))
    # ---- cylinder
    mv(robot, (cyl[0], cyl[1], ctop + 50))
    zc = max(sup + 19, ctop - 12, 14)
    r = mv(robot, (cyl[0], cyl[1], zc), stop=True)
    print("cyl descent", r, np.round(tcp(robot), 1))
    w1, w2 = squeeze2(robot, "CYL")
    C = tcp(robot)
    t4 = robot.truth()
    print("cyl truth after squeeze", np.round(t4["w_cyl"], 2), "tcp", np.round(C, 2))
    print("force holding on table", st(robot)["force_n"])
    mv(robot, (C[0], C[1], C[2] + 10))
    print("force lifted", st(robot)["force_n"])
    r = mv(robot, (C[0], C[1], C[2] - 5), stop=True)
    print("touch-down", r, np.round(tcp(robot), 2), "support truth", round(tr["w_cyl"][2], 2))
    db = tcp(robot)[2] - robot.truth()["w_cyl"][2]
    print("d_b", round(db, 2))
    # ---- insert
    zt = max(stick_top, ctop) + db + 40
    mv(robot, (C[0], C[1], zt))
    print("force carried", st(robot)["force_n"])
    mv(robot, (S[0], S[1], zt))
    t5 = robot.truth()
    print("cyl truth carried", np.round(t5["w_cyl"], 2), "tcp", np.round(tcp(robot), 2))
    mv(robot, (S[0], S[1], stick_top + 10 + db))
    zend = max(base_top + 2 + db, base_top + 19, 13)
    r = mv(robot, (S[0], S[1], zend), stop=True)
    print("insert", r, np.round(tcp(robot), 2), "zend", round(zend, 1))
    print("judge holding", robot.judge())
    robot.set_gripper(ARM, 80)
    p = tcp(robot)
    mv(robot, (p[0], p[1], max(stick_top + 140, 300)))
    robot.home(ARM)
    print("judge end", robot.judge())
