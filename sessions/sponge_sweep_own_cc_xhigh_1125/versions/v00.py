import numpy as np
import cv2

DOWN = (0, 0, -1)


def draw_masks(rgb, res, col):
    im = rgb.copy()
    for k, (m, s) in enumerate(res):
        cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(im, cnts, -1, col, 2)
        ys, xs = np.nonzero(m)
        if len(xs):
            cv2.putText(im, "%d:%.2f" % (k, s), (int(xs.mean()), int(ys.mean())), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
    return im


def run(robot, task):
    print("TASK:", task)
    tr = robot.truth()
    for k, v in (tr or {}).items():
        if not k.startswith("w_obj_") or k in ("w_obj_0", "w_obj_1"):
            print("truth", k, np.round(v, 1))
    noun = "small objects"
    t = task.lower()
    if "sweep" in t:
        s = t.split("sweep", 1)[1].replace(" all ", " ").strip()
        if s.startswith("the "):
            s = s[4:]
        s = s.split(" into")[0].split(" on the table")[0]
        noun = s
    f = robot.look()
    sc = f["scene"]
    print("scene eye", sc.eye, "fx", sc.fx, sc.fy, sc.cx, sc.cy)
    robot.show("scene", sc.rgb)
    for ph, col in [("sponge", (255, 0, 0)), ("sheet of paper", (0, 255, 0)), ("tape", (0, 0, 255)), (noun, (255, 0, 255))]:
        res = robot.segment(sc, ph)
        print("scene seg", ph, [(int(m.sum()), round(float(s), 2)) for m, s in res])
        robot.show("seg_scene_" + ph.replace(" ", "_"), draw_masks(sc.rgb, res, col))
    r = robot.move("left", (-300, 100, 420), DOWN, (0, 1, 0))
    print("move high", r)
    f = robot.look(["left_wrist"])
    w = f["left_wrist"]
    print("wrist eye", w.eye, "R", np.round(w.R, 3), "fx", w.fx, w.cx, w.cy)
    robot.show("wrist", w.rgb)
    for ph, col in [("sponge", (255, 0, 0)), (noun, (255, 0, 255))]:
        res = robot.segment(w, ph)
        print("wrist seg", ph, [(int(m.sum()), round(float(s), 2)) for m, s in res])
        robot.show("seg_wrist_" + ph.replace(" ", "_"), draw_masks(w.rgb, res, col))
    # table height from wrist depth
    P = w.points()
    z = P[..., 2]
    print("wrist table z median", np.nanmedian(z), "pct", np.nanpercentile(z, [5, 25, 50, 75, 95]))
    th = robot.state("left")
    # lower and touch the table
    r = robot.move("left", (-300, 100, 80), DOWN, (0, 1, 0))
    print("move low", r)
    r = robot.move("left", (-300, 100, -30), DOWN, (0, 1, 0), stop_on_contact=True)
    st = robot.state("left")
    print("touch", r, "tcp", np.round(st["tcp_mm"], 1), "force", np.round(st["force_n"], 1))
    r = robot.move("left", (-300, 100, 120), DOWN, (0, 1, 0))
    print("up", r)
