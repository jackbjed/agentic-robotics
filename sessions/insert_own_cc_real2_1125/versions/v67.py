def run(robot, task):
    res = []
    for z in (580.0, 540.0, 500.0, 460.0):
        row = []
        for x in (-400.0, -340.0, -280.0, -220.0, -160.0, -100.0):
            ok = []
            for y in (-120.0, 0.0, 120.0, 240.0):
                r = robot.move("left", [x, y, z], (0, 0, -1), (0, -1, 0), False)
                ok.append("o" if r.get("done") else ("x" if not r.get("moved") else "s"))
            row.append("".join(ok))
        print("z", z, " ".join(row))
