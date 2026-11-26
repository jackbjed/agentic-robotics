import numpy as np


def run(robot, task):
    print("TASK:", task)
    st = robot.state("left")
    print("state", st)
    tr = robot.truth()
    print("truth", tr)
    fr = robot.look()
    for k, f in fr.items():
        print(k, "fx", f.fx, f.fy, f.cx, f.cy, "eye", np.round(f.eye, 1))
        print(np.round(f.R, 3))
        d = f.depth
        print("depth valid frac", np.mean(np.isfinite(d)))
        robot.show(k, f.rgb)
    # move over the stand and the ring using truth (probe only)
    for name in ["w_stick_top", "w_ring_top"]:
        p = tr[name]
        t0 = robot.time_s()
        r = robot.move("left", [p[0], p[1] - 0, p[2] + 200], [0, 0, -1], [0, -1, 0])
        print("move", name, r, "dt", robot.time_s() - t0)
        f = robot.look(["left_wrist"])["left_wrist"]
        st = robot.state("left")
        print("tcp", np.round(st["tcp_mm"], 1), "eye", np.round(f.eye, 1), "eye-tcp", np.round(np.array(f.eye) - st["tcp_mm"], 1))
        print(np.round(f.R, 3))
        uv, dd = f.project(np.array([p]))
        print("proj of target", uv, dd)
        u, v = int(round(uv[0][0])), int(round(uv[0][1]))
        print("locate at target pixel", f.locate(u, v), "true", p)
        robot.show("wrist_" + name, f.rgb)
        print(f.depth_report())
    # gripper close test on the ring
    p = tr["w_ring_top"]
    pr = tr["w_ring"]
    hh = p[2] - pr[2]
    z = pr[2] + max(18, hh * 0.5)
    r = robot.move("left", [p[0] + 3, p[1] + 4, z + 60], [0, 0, -1], [0, -1, 0])
    r = robot.move("left", [p[0] + 3, p[1] + 4, z], [0, 0, -1], [0, -1, 0])
    print("down", r)
    w = robot.set_gripper("left", 0)
    st = robot.state("left")
    tr2 = robot.truth()
    print("closed width", w, "tcp", np.round(st["tcp_mm"], 2), "ring", np.round(tr2["w_ring"], 2), "force", st["force_n"])
    robot.set_gripper("left", 80)
    r = robot.move("left", [p[0] + 3, p[1] + 4, z], [0, 0, -1], [1, 0, 0])
    print("turn", r)
    w = robot.set_gripper("left", 0)
    st = robot.state("left")
    tr2 = robot.truth()
    print("closed2 width", w, "tcp", np.round(st["tcp_mm"], 2), "ring", np.round(tr2["w_ring"], 2))
    r = robot.move("left", [p[0] + 3, p[1] + 4, z + 100], [0, 0, -1], [1, 0, 0])
    st = robot.state("left")
    print("lifted", r, "force", st["force_n"], "time", robot.time_s())
    segs = robot.segment(fr["scene"], "cylinder with a hole")
    print("segs", [(int(m.sum()), s) for m, s in segs])
    print("judge", robot.judge())
