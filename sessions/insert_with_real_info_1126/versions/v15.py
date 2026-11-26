import numpy as np


def run(robot, task):
    t0 = robot.truth()
    p0 = np.array(t0["w_ring_top"])
    for i in range(12):
        robot.wait(5.0)
        t = robot.truth()
        print("t=%.0f ring top %s drift %s stand %s" % (robot.time_s(), np.round(t["w_ring_top"], 2),
              np.round(np.array(t["w_ring_top"]) - p0, 2), np.round(np.array(t["w_stick_top"]) - np.array(t0["w_stick_top"]), 2)))
