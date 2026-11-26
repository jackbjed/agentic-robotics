import numpy as np

DOWN = (0, 0, -1)


def run(robot, task):
    tr = robot.truth()
    print("table z (truth of stand base bottom)", tr.get("w_stand"))
    for x, y in ((-330.0, 0.0), (-300.0, 60.0)):
        robot.move("left", (x, y, 80), DOWN, (0, -1, 0))
        robot.move("left", (x, y, 30), DOWN, (0, -1, 0))
        r = robot.move("left", (x, y, -10), DOWN, (0, -1, 0), True)
        s = robot.state("left")
        print("touch", r, "tcp", np.round(s["tcp_mm"], 2), "force", np.round(s["force_n"], 1))
        robot.move("left", (x, y, 80), DOWN, (0, -1, 0))
    robot.set_gripper("left", 0)
    robot.move("left", (-330, 0, 30), DOWN, (0, -1, 0))
    r = robot.move("left", (-330, 0, -10), DOWN, (0, -1, 0), True)
    s = robot.state("left")
    print("touch closed", r, "tcp", np.round(s["tcp_mm"], 2), "force", np.round(s["force_n"], 1))
    robot.move("left", (-330, 0, 80), DOWN, (0, -1, 0))
    robot.home("left")
