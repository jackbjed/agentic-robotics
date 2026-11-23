"""Picking from clutter with one arm: clutter.py's environments for the robot's left arm alone (the one with the green
gripper, the arm of the real single-arm rig).

    tasks     pick_clutter_one_arm_medium, pick_clutter_one_arm_hard (tasks.py): the levels, time limits, model calls
              and the judge of pick_clutter_medium / _hard
    the robot core.BiCore with only = "L": the right arm stays parked at its start pose and is not in the interface
              (api.Robot: ARMS ("left",), CAMERAS ("scene", "left_wrist")); the left arm keeps the real rig's safety
              limits (core.rig_limits: the joints' ranges, the TCP box, the fingertips' and the links' height above the
              table, the keep-out box around the parked arm) and a move that breaks one is refused
    the heaps clutter.ONE_ARM: where that arm reaches within those limits, the practice seeds' place (x, y) and the
              out-of-distribution places (place: near, far, side, beyond the ruler); a scene carries arm = "left"
    seeds     as clutter.py's: below clutter.OOD_FROM the environment as described to a session, from it on the
              out-of-distribution scenes (other shapes, colours, sizes, densities, places)

    python -m agentic_robotics.bimanual.clutter --level medium --seeds 0-11 --arm left --out sheet.png
    python -m agentic_robotics.bimanual.improve --env sim_bimanual_grippers --task pick_clutter_one_arm ...
"""
from __future__ import annotations

from . import clutter

ARM = "left"
LEVELS = ("medium", "hard")
TEXT = {l: clutter.TEXT_ONE_ARM[l] for l in LEVELS}
PLACES = clutter.ONE_ARM


def task(level):
    """The task's name (tasks.TASKS) of a level."""
    if level not in LEVELS:
        raise ValueError(f"level: one of {', '.join(LEVELS)}")
    return f"pick_clutter_one_arm_{level}"


def sample(level, seed):
    """The level's scene of a seed for the one-armed robot (clutter.sample)."""
    return clutter.sample(level, seed, ARM)


def build(spec, seed=0, levels=LEVELS, tries=clutter.BUILD_TRIES):
    """A scene set up by hand for the one-armed robot (clutter.build)."""
    return clutter.build(spec, seed=seed, levels=levels, tries=tries, arm=ARM)
