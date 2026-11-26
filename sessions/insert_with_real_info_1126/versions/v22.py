import numpy as np

DN = [0, 0, -1]


def run(robot, task):
    t0 = robot.truth()
    p0 = np.array(t0["w_ring_top"])
    s = np.array(t0["w_stick_top"])

    def rep(label, r=None):
        t = robot.truth()
        print("%-28s ring drift %s  %s" % (label, np.round(np.array(t["w_ring_top"]) - p0, 2), "" if r is None else r))

    x, y = s[0] + 14, s[1] - 1
    r = robot.move("left", [x, y, 320], DN, [0, -1, 0]); rep("above stand", r)
    r = robot.move("left", [x, y, 49], DN, [0, -1, 0]); rep("down A", r)
    r = robot.move("left", [x, y, 171], DN, [0, -1, 0]); rep("up A", r)
    r = robot.move("left", [x, y, 171], DN, [0.71, -0.71, 0]); rep("rotate 45", r)
    for z in (140, 110, 80, 49):
        r = robot.move("left", [x, y, z], DN, [0.71, -0.71, 0]); rep("down 45 to %d" % z, r)
        robot.show("z%d" % z, robot.look(["scene"])["scene"].rgb)
    robot.wait(3)
    rep("after wait")
