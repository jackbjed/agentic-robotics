import numpy as np
import cv2

DOWN = (0, 0, -1)


def run(robot, task):
    tr = robot.truth()
    print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in (tr or {}).items()})
    r = robot.move("left", (-330, 30, 560), DOWN, (0, -1, 0))
    print("move", r)
    fr = robot.look(["left_wrist"])
    wf = fr["left_wrist"]
    print("wrist eye", wf.eye, "R", np.round(wf.R, 3).tolist())
    img = wf.rgb.copy()
    phrases = ["ring", "cylinder with a hole", "stick", "stand", "washer", "peg", "pole", "rod standing on a base"]
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255), (255, 128, 0),
            (128, 0, 255)]
    ann = img.copy()
    for ph, col in zip(phrases, cols):
        res = robot.segment(wf, ph)
        out = []
        for i, (mk, scr) in enumerate(res):
            vs, us = np.nonzero(mk)
            u, v = float(us.mean()), float(vs.mean())
            p = wf.locate(int(u), int(v))
            out.append((round(float(scr), 2), int(u), int(v), int(mk.sum()),
                        None if p is None else [round(float(x)) for x in p]))
            if i < 2:
                cnts, _ = cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                cv2.drawContours(ann, cnts, -1, col, 1)
                cv2.putText(ann, ph[:6], (int(u), int(v)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, col, 1)
        print("SEG", ph, out)
    robot.show("wrist", img)
    robot.show("seg", ann)
    P = wf.points()
    z = P[..., 2]
    zz = z[np.isfinite(z)]
    print("z pct", np.percentile(zz, [5, 25, 50, 75, 95]))
    h = np.nan_to_num(np.clip((z - np.percentile(zz, 30)) / 120.0, 0, 1)) * 255
    robot.show("height", h.astype(np.uint8))
    print(wf.depth_report())
    robot.home("left")
