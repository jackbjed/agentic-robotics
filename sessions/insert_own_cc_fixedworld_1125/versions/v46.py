import math


def run(robot, task):
    A = "left"
    D = (0, 0, -1)
    pts = [(-115, -115), (-120, 100), (-135, -114), (-100, 0), (-150, 200), (-110, -200)]
    for (x, y) in pts:
        for z in (240.0, 60.0, 24.0):
            ok = []
            for th in (0, 30, 60, 90, 120, 150):
                c = (math.cos(math.radians(th)), math.sin(math.radians(th)), 0)
                r = robot.move(A, (x, y, 300), D, c, False)
                r = robot.move(A, (x, y, z), D, c, False)
                if not r.get("done"):
                    ok.append("%d:X" % th)
                    continue
                t1 = robot.turn(A, (x, y, z), (0, 0, 1), 90).get("moved")
                t2 = robot.turn(A, (x, y, z), (0, 0, 1), -90 if t1 else -90).get("moved")
                ok.append("%d:%s%s" % (th, "+" if t1 else "-", "+" if t2 else "-"))
            print("REACH", (x, y, z), " ".join(ok))
        robot.move(A, (-420, 60, 400), D, (0, -1, 0), False)
