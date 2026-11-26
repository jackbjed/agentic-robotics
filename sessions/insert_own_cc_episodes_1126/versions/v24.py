def run(robot, task):
    for i in range(4):
        t = robot.truth()
        print("t=%.1f" % robot.time_s(), {k: [round(float(x), 1) for x in v] for k, v in t.items() if k in ("w_cyl", "w_stand")})
        robot.wait(0.5)
    print(robot.judge())
