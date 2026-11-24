"""real/astra-skill's first step: one look with the rig's cameras from where the arm stands, nothing moved (the
frames are kept in the run's frames/; the arm's state is printed for the writer's prompt)."""


def run(robot, task):
    robot.look()
    st = robot.state("left")
    print("STATE", list(st["tcp_mm"]), list(st["approach_dir"]), list(st["closing_dir"]), st["opening_mm"])
