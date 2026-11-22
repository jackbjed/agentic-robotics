"""An example program for `lift` in sim_bimanual_grippers (docs/bimanual.md): the roll from the scene camera's depth,
the table by touch. An example of the program interface, not a tuned skill (it lifts the roll in 5 of seeds 0-5).

    python -m agentic_robotics.bimanual.run --env sim_bimanual_grippers --task lift --program examples/bimanual_lift.py --video --out runs/bimanual/x
"""
import numpy as np


def run(robot, task):
    P = robot.look(["scene"])["scene"].points()                  # (H, W, 3) mm in the table frame, nan where no depth
    near = np.isfinite(P[..., 2]) & (np.abs(P[..., 1]) < 250) & (P[..., 0] > -450) & (P[..., 0] < -150) & (np.abs(P[..., 2]) < 80)
    Q = P[near]
    top = Q[Q[:, 2] > np.median(Q[:, 2]) + 15]                   # the camera's calibration is off by some mm: heights relative
    if len(top) < 30:                                            # to the table as this camera sees it
        raise RuntimeError("no roll found")
    x, y = np.median(top[:, :2], axis=0)                         # the ring's top face is seen whole: its middle is the roll's
    arm = "left" if y > -175 else "right"
    down = dict(dir_a=(0, 0, -1), dir_b=(0, 1, 0))
    robot.move(arm, (x, y, 150), **down)
    robot.move(arm, (x, y, 10), stop_on_contact=True, **down)   # the fingers pass on either side of the roll and
    z = robot.state(arm)["tcp_mm"][2]                               # stop on the table
    robot.move(arm, (x, y, z + 4), **down)
    robot.set_gripper(arm, 0)
    robot.move(arm, (x, y, z + 130), **down)
