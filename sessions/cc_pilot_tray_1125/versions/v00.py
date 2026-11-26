import numpy as np, cv2, math


def plane_fit(P, rng, iters=300, tol=5.0):
    best, bi = None, -1
    n = len(P)
    for _ in range(iters):
        s = P[rng.integers(0, n, 3)]
        nv = np.cross(s[1] - s[0], s[2] - s[0])
        if np.linalg.norm(nv) < 1e-6:
            continue
        nv = nv / np.linalg.norm(nv)
        if abs(nv[2]) < 0.9:
            continue
        d = -nv @ s[0]
        cnt = np.sum(np.abs(P @ nv + d) < tol)
        if cnt > bi:
            bi, best = cnt, (nv, d)
    nv, d = best
    inl = P[np.abs(P @ nv + d) < tol]
    A = np.c_[inl[:, 0], inl[:, 1], np.ones(len(inl))]
    coef, *_ = np.linalg.lstsq(A, inl[:, 2], rcond=None)
    return coef  # z = a x + b y + c


def run(robot, task):
    print("TASK:", task)
    st = robot.state("left")
    print("STATE", st)
    tr = robot.truth()
    print("TRUTH", tr)
    fr = robot.look()
    for name, f in fr.items():
        print(name, "fx fy cx cy", f.fx, f.fy, f.cx, f.cy, "eye", np.round(f.eye, 1), "R", np.round(f.R, 3).tolist())
        try:
            print("report", f.depth_report())
        except Exception as e:
            print("no report", e)
    S = fr["scene"]
    P = S.points()
    rng = np.random.default_rng(0)
    ok = np.isfinite(P[..., 2]) & (np.abs(P[..., 0]) < 550) & (np.abs(P[..., 1]) < 550) & (np.abs(P[..., 2]) < 90)
    pts = P[ok]
    print("valid table pts", len(pts), "of", np.isfinite(P[..., 2]).sum())
    a, b, c = plane_fit(pts[:: max(1, len(pts) // 20000)], rng)
    print("PLANE z = %.4f x + %.4f y + %.1f ; at (-300,0): %.1f" % (a, b, c, a * -300 + c))
    H = P[..., 2] - (a * P[..., 0] + b * P[..., 1] + c)
    m = np.isfinite(H) & (H > 8) & (H < 150) & (P[..., 0] > -560) & (P[..., 0] < 300) & (np.abs(P[..., 1]) < 590)
    m &= ~((P[..., 0] < -400) & (P[..., 1] < -260))
    m8 = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m8)
    vis = S.rgb.copy()
    for i in range(1, n):
        if stats[i, 4] < 30:
            continue
        Q = P[lab == i]
        rect = cv2.minAreaRect(Q[:, :2].astype(np.float32))
        print("COMP", i, "px", stats[i, 4], "rect", np.round(rect[0], 1), np.round(rect[1], 1), round(rect[2], 1),
              "h95", round(float(np.percentile(H[lab == i], 95)), 1), "hmed", round(float(np.median(H[lab == i])), 1))
        cnts, _ = cv2.findContours((lab == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(vis, cnts, -1, (255, 0, 0), 1)
    robot.show("scene_comps", vis)
    for ph in ["block", "tray", "small box", "cube"]:
        res = robot.segment(S, ph)
        out = []
        for mk, sc in res[:3]:
            Q = P[mk & np.isfinite(P[..., 2])]
            med = np.round(np.median(Q, axis=0), 0) if len(Q) else None
            out.append((round(float(sc), 2), int(mk.sum()), med))
        print("SEG", ph, out)
    W = fr["left_wrist"]
    robot.show("wrist0", W.rgb)
    # wrist camera offset in gripper frame
    st = robot.state("left")
    print("wrist eye - tcp", np.round(np.array(W.eye) - np.array(st["tcp_mm"]), 1))
    # go above the block (truth, probe only)
    bp = np.array(tr["w_block"])
    r = robot.move("left", [bp[0], bp[1], bp[2] + 250], [0, 0, -1], [0, -1, 0])
    print("MOVE above block", r)
    fr = robot.look(["left_wrist"])
    W = fr["left_wrist"]
    st = robot.state("left")
    print("wrist eye - tcp", np.round(np.array(W.eye) - np.array(st["tcp_mm"]), 1), "R", np.round(W.R, 3).tolist())
    pix, dep = W.project(np.array([bp]))
    print("block centre proj", pix, dep)
    u, v = int(pix[0][0]), int(pix[0][1])
    print("locate top centre", W.locate(u, v), "truth top", bp + [0, 0, 0])
    robot.show("wrist_block", W.rgb)
    # limit test: find a free spot
    tp = np.array(tr["w_tray"])
    best = None
    for x in np.arange(-450, -150, 25):
        for y in np.arange(-180, 250, 25):
            d = min(np.hypot(x - bp[0], y - bp[1]), np.hypot(x - tp[0], y - tp[1]) - 120)
            if best is None or d > best[0]:
                best = (d, x, y)
    _, x, y = best
    r = robot.move("left", [x, y, 150], [0, 0, -1], [0, -1, 0])
    print("MOVE free", x, y, r)
    r = robot.move("left", [x, y, 8], [0, 0, -1], [0, -1, 0], True)
    print("MOVE z8", r, robot.state("left")["tcp_mm"])
    r = robot.move("left", [x, y, 13], [0, 0, -1], [0, -1, 0], True)
    print("MOVE z13", r, robot.state("left")["tcp_mm"])
    r = robot.move("left", [x, y, 150], [0, 0, -1], [0, -1, 0])
    print("home", robot.home("left"), robot.time_s())
    print("JUDGE", robot.judge())
