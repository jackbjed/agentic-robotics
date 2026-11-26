import numpy as np
import cv2


def run(robot, task):
    tr = robot.truth()
    print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in (tr or {}).items()})
    print("JUDGE0", robot.judge())
    fr = robot.look()
    sc = fr["scene"]
    print("scene eye", sc.eye, "fx", sc.fx, sc.fy, sc.cx, sc.cy)
    img = sc.rgb.copy()
    robot.show("scene", img)
    phrases = ["cylinder with a hole", "ring", "stand with a stick", "stick", "peg", "tube"]
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255)]
    ann = img.copy()
    for ph, col in zip(phrases, cols):
        res = robot.segment(sc, ph)
        out = []
        for mk, scr in res:
            vs, us = np.nonzero(mk)
            u, v = float(us.mean()), float(vs.mean())
            p = sc.locate(int(u), int(v))
            out.append((round(float(scr), 2), int(u), int(v), int(mk.sum()),
                        None if p is None else [round(float(x)) for x in p]))
        print("SEG", ph, out)
        if res:
            mk = res[0][0].astype(np.uint8)
            cnts, _ = cv2.findContours(mk, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(ann, cnts, -1, col, 1)
            cv2.putText(ann, ph, (int(np.nonzero(mk)[1].mean()), int(np.nonzero(mk)[0].mean())),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
    robot.show("seg", ann)
    d = sc.depth
    robot.show("depth", (np.nan_to_num(np.clip((d - 600) / 1200, 0, 1)) * 255).astype(np.uint8))
    try:
        print(sc.depth_report())
    except Exception as e:
        print("dr", e)
    robot.set_gripper("left", 80)
    robot.home("left")
    print("JUDGE1", robot.judge())
