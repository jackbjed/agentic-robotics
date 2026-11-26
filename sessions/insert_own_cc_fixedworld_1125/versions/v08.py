import numpy as np

DOWN = (0, 0, -1)


def run(robot, task):
    for name, (x, y) in (("fixed", (-300.0, -100.0)), ("heavy", (-300.0, 100.0))):
        for off in (0.0, 3.0, 6.0):
            robot.set_gripper("left", 80)
            robot.move("left", (x - 2.0, y + off, 120), DOWN, (0, -1, 0))
            robot.move("left", (x - 2.0, y + off, 50), DOWN, (0, -1, 0))
            t0 = robot.state("left")["tcp_mm"]
            w = robot.set_gripper("left", 0)
            s = robot.state("left")
            tr = robot.truth()
            print(name, "off", off, "w %.2f" % w, "tcp before", np.round(t0, 2), "after", np.round(s["tcp_mm"], 2),
                  "force", np.round(s["force_n"], 1), "truth", np.round(tr.get("w_" + name, [x, y, 0]), 2))
            robot.wait(1.0)
            s = robot.state("left")
            print("   after 1s tcp", np.round(s["tcp_mm"], 2), "open", round(s["opening_mm"], 2))
            robot.set_gripper("left", 80)
            robot.move("left", (x - 2.0, y + off, 120), DOWN, (0, -1, 0))
    robot.home("left")
