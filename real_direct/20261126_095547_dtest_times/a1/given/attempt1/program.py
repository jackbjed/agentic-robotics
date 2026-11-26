def run(robot, task):
    f = robot.look(["left_wrist"])["left_wrist"]
    robot.show("look", f.rgb)
    print("attempt 0: looked and did nothing")
