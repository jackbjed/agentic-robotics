import numpy as np

CAM_OFF = np.array([51.0, -2.9, 133.1])
DN = [0, 0, -1]
CY = [0, -1, 0]


def top_blob(f, z_hint):
    """pixels near image centre whose depth is near the minimum: centroid -> 3D"""
    d = f.depth
    h, w = d.shape
    cu, cv = w // 2, h // 2
    win = d[cv - 120:cv + 120, cu - 120:cu + 120]
    dmin = np.nanpercentile(win, 1)
    m = np.zeros_like(d, bool)
    m[cv - 120:cv + 120, cu - 120:cu + 120] = win < dmin + 6
    vs, us = np.nonzero(m)
    u, v = us.mean(), vs.mean()
    z = np.nanmedian(d[m])
    x = (u - f.cx) / f.fx * z
    y = (v - f.cy) / f.fy * z
    p = np.array(f.eye) + np.array(f.R) @ np.array([x, y, z])
    return p, (u, v), m.sum()


def run(robot, task):
    tr = robot.truth()
    out = {}
    for name in ["w_stick_top", "w_ring_top"]:
        p = np.array(tr[name])
        for yaw_cd in [CY]:
            tcp = p + np.array([-51.0, 2.9, 197.0])
            r = robot.move("left", tcp, DN, yaw_cd)
            f = robot.look(["left_wrist"])["left_wrist"]
            st = robot.state("left")
            q, uv, n = top_blob(f, p[2])
            print(name, "meas", np.round(q, 2), "true", p, "err", np.round(q - p, 2), "uv", np.round(uv, 1), "n", n)
            out[name] = q - p
            robot.show("w_" + name, f.rgb)
    # gripper centering on the ring
    pr = np.array(tr["w_ring"])
    pt = np.array(tr["w_ring_top"])
    hh = pt[2] - pr[2]
    z = max(pr[2] + 18, 13, pt[2] - 0.5 * hh)
    z = min(z, pt[2] - 6)
    for off, cd in [((3, 4), CY), ((3, 4), [1, 0, 0])]:
        r = robot.move("left", [pr[0] + off[0], pr[1] + off[1], pt[2] + 40], DN, cd)
        r = robot.move("left", [pr[0] + off[0], pr[1] + off[1], z], DN, cd)
        print("down", r)
        w = robot.set_gripper("left", 0)
        st = robot.state("left")
        tr2 = robot.truth()
        print("closed", cd, "width", w, "tcp", np.round(st["tcp_mm"], 2), "ring", np.round(tr2["w_ring"], 2),
              "diff", np.round(np.array(tr2["w_ring"][:2]) - st["tcp_mm"][:2], 2))
        robot.set_gripper("left", 80)
    r = robot.move("left", [pr[0] + 3, pr[1] + 4, z], DN, CY)
    w = robot.set_gripper("left", 0)
    st0 = robot.state("left")
    r = robot.move("left", [pr[0] + 3, pr[1] + 4, z + 150], DN, CY)
    st = robot.state("left")
    tr2 = robot.truth()
    print("lifted", r, "force", st["force_n"], "f0", st0["force_n"], "ring", np.round(tr2["w_ring"], 2), "tcp", np.round(st["tcp_mm"], 2))
    # look at the held ring and stick from the stick-view pose
    p = np.array(tr["w_stick_top"])
    r = robot.move("left", p + np.array([-51.0, 2.9, 197.0]), DN, CY)
    f = robot.look(["left_wrist"])["left_wrist"]
    q, uv, n = top_blob(f, p[2])
    print("stick with ring held: err", np.round(q - p, 2), "uv", np.round(uv, 1), "n", n)
    robot.show("held", f.rgb)
    # now place directly above the stick using truth, lower
    tr2 = robot.truth()
    ring = np.array(tr2["w_ring"])
    st = robot.state("left")
    off = ring - np.array(st["tcp_mm"])
    print("ring rel tcp", np.round(off, 2))
    tgt = p - off + np.array([0, 0, 10])
    r = robot.move("left", tgt, DN, CY)
    tgt2 = tgt - np.array([0, 0, 30])
    t0 = robot.time_s()
    r = robot.move("left", tgt2, DN, CY, True)
    st = robot.state("left")
    print("insert", r, "tcp", np.round(st["tcp_mm"], 2), "force", st["force_n"], "dt", robot.time_s() - t0)
    robot.set_gripper("left", 80)
    r = robot.move("left", np.array(st["tcp_mm"]) + np.array([0, 0, 120]), DN, CY)
    r = robot.home("left")
    print("home", r, "time", robot.time_s())
    print("judge", robot.judge())
