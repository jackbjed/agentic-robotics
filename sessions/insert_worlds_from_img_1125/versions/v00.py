import numpy as np
import cv2


def run(robot, task):
    st = robot.state("left")
    print("state", st)
    fr = robot.look()
    for name, f in fr.items():
        print(name, "eye", np.round(f.eye, 1), "R", np.round(f.R, 3).tolist(), "fx", f.fx, f.cx, f.cy)
    print("truth", {k: np.round(v, 1).tolist() for k, v in robot.truth().items()})
    sc = fr["scene"]
    img = sc.rgb.copy()
    for ph, col in [("cylinder", (255, 0, 0)), ("stick", (0, 255, 0)), ("wooden board", (0, 0, 255))]:
        res = robot.segment(sc, ph)
        print(ph, [(int(m.sum()), round(float(s), 3)) for m, s in res])
        if res:
            m = res[0][0].astype(np.uint8)
            cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(img, cs, -1, col, 2)
    robot.show("scene_seg", img)
    robot.show("wrist", fr["left_wrist"].rgb)
    # reach map
    down = (0, 0, -1)
    for z in (40, 250):
        for y in (-300, -200, 0, 120, 200, 300, 420):
            row = []
            for x in (-560, -480, -400, -300, -200, -120):
                r = robot.move("left", (x, y, z), down, (0, -1, 0))
                row.append("%d:%s" % (x, "Y" if r["done"] else ("n" if not r["moved"] else "p")))
                if not r["done"]:
                    print("  refuse", x, y, z, r["why"][:90])
            print("z", z, "y", y, " ".join(row), "t", round(robot.time_s(), 1))
    robot.home("left")
