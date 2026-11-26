import numpy as np


def run(robot, task):
    for p in [(-360, 50, 660), (-340, 40, 700), (-330, 40, 650), (-380, 50, 700), (-300, 40, 600), (-360, 40, 620)]:
        r = robot.move("left", p, (0, 0, -1), (0, -1, 0))
        s = robot.state("left")
        f = robot.look(["left_wrist"])["left_wrist"]
        P = f.points()
        z = P[..., 2]
        ok = np.isfinite(z) & (np.abs(z) < 5)
        xs = P[..., 0][ok]
        ys = P[..., 1][ok]
        print(p, r.get("done"), r.get("why", "")[:60], "tcp", np.round(s["tcp_mm"], 1), "eye", np.round(f.eye, 1),
              "table x %.0f..%.0f y %.0f..%.0f" % (np.percentile(xs, 1), np.percentile(xs, 99),
                                                   np.percentile(ys, 1), np.percentile(ys, 99)) if ok.any() else "")
    robot.home("left")
