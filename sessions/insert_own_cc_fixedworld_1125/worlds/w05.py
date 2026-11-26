import math
import numpy as np


def scene(scene, rng):
    # a fixed stick at (-300, -100), a heavy free stand at (-300, 100) with friction 1
    g = ('<body name="w_fixed" pos="-0.3 -0.1 0"><geom type="cylinder" size="0.008 0.06" pos="0 0 0.06" '
         'rgba="0.8 0.2 0.2 1"/></body>'
         '<body name="w_heavy" pos="-0.3 0.1 0.0003"><freejoint/>'
         '<geom type="box" size="0.05 0.05 0.012" pos="0 0 0.012" density="8000" friction="1 0.005 0.0001" '
         'rgba="0.2 0.2 0.8 1"/><geom type="cylinder" size="0.008 0.05" pos="0 0 0.074" density="8000" '
         'friction="1 0.005 0.0001" rgba="0.2 0.8 0.2 1"/></body>')
    scene["mjcf"] = "<mujoco><worldbody>%s</worldbody></mujoco>" % g
    return scene


def goal(scene, rng):
    return "test"


def success(state):
    return {"ok": False}
