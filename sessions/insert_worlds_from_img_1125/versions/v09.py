import numpy as np


def run(robot, task):
    for k in range(6):
        t = robot.truth()
        print("t %.2f cyl %s" % (robot.time_s(), np.round(t["w_cyl"], 1).tolist()))
        if k == 0:
            robot.show("scene0", robot.look(["scene"])["scene"].rgb)
        robot.wait(0.1)
