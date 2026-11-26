import numpy as np


def run(robot, task):
    T = robot.truth()
    print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in T.items()} if T else None)
    for p in [(-350, 80, 700), (-330, 60, 720), (-300, 80, 700), (-350, 300, 700), (-350, -150, 700)]:
        r = robot.move("left", p, (0, 0, -1), (0, -1, 0), False)
        st = robot.state("left")
        print("POSE", p, r.get("done"), r.get("why", "")[:60], np.round(st["tcp_mm"], 1))
        if r.get("done"):
            f = robot.look(["left_wrist"])["left_wrist"]
            P = f.points()
            z = P[..., 2]
            v = np.isfinite(z)
            print("  eye", np.round(f.eye, 1), "valid %.2f" % v.mean(), "x %.0f..%.0f y %.0f..%.0f" % (
                np.nanmin(P[..., 0]), np.nanmax(P[..., 0]), np.nanmin(P[..., 1]), np.nanmax(P[..., 1])))
            if T:
                for k in ("w_cyl", "w_stick"):
                    if k in T:
                        q = np.array(T[k], float)
                        if k == "w_stick":
                            q = q + np.array([0, 0, 170.0])
                        (uv,), _ = f.project(q[None])
                        print("  ", k, "px", np.round(uv, 0))
            robot.show("survey", f.rgb)
    robot.home("left")
    print("JUDGE", robot.judge())
