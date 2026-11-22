"""The bimanual tasks (docs/bimanual.md): for each, the text a controller gets, the episode's layout (by seed), the
judge on the simulation's TRUE state, and the limits. The same tasks run in both environments.

    ep = Episode("sim_bimanual_dexterous", "open_bottle", seed=3)     # the simulation, at the episode's start
    ep.robot                    api.Robot: all a controller gets (with ep.text, the task)
    ep.finish()                 -> the verdict: dict(ok, ..., robot_s)

    open_bottle     one hand holds the bottle, the other unscrews its cap and takes it off        (needs both arms)
    move_across     a roll of white tape from the right arm's side of the table to the left arm's      (needs both arms)
    lift            pick a roll of white tape up and hold it                                        (one arm)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import make
from .api import Robot
from .core import BOTTLE_XY

TABLE_HALF = 0.61
ROLL = "tape_white"
ACROSS_Y = 0.40                                                 # move_across: the roll must end left of this line (m)

_BOTTLE = ("The bottle is a plastic bottle, half full (about 0.65 kg), about 30 cm tall with a long neck, and it tips over easily "
           "if pushed near the top; its body is 68 mm across and 200 mm tall, the neck 30 mm across; the red cap (42 mm across, "
           "24 mm high) is screwed on with an ordinary thread: turning the cap counter-clockwise seen from above unscrews it, "
           "about one full turn in all, after which it lifts off. The thread has some friction: if nothing holds the bottle, it "
           "turns with the cap or tips over.")
_ROLL = "The roll (0.06 kg) lies flat on the table: a ring 50 mm across and 27 mm high, with a 26 mm hole in its middle."


@dataclass(frozen=True)
class Task:
    name: str
    text: str
    layout: Callable                                            # seed -> {object: (x, y, yaw_deg)}
    judge: Callable                                             # S -> dict(ok, ...): TRUE state, at the episode's end
    time_limit_s: float                                         # robot time
    max_calls: int                                              # tool calls of a direct-control episode
    arms: int                                                   # how many arms the task needs


def _rng(name, seed):
    return np.random.default_rng([sum(map(ord, name)), int(seed)])


def _bottle_layout(seed):
    """Seed 0: the reference position. Others: within the area where the scripted solution of both environments works
    (the left arm holds from the left, the right one works from above)."""
    if seed == 0:
        return {"bottle": (*BOTTLE_XY, 0.0)}
    r = _rng("open_bottle", seed)
    return {"bottle": (round(float(r.uniform(-0.33, -0.21)), 3), round(float(r.uniform(-0.24, -0.12)), 3), 0.0)}


def _roll_right(seed):
    r = _rng("move_across", seed)                               # where only the right arm reaches
    return {ROLL: (round(float(r.uniform(-0.30, -0.16)), 3), round(float(r.uniform(-0.52, -0.44)), 3), 0.0)}


def _roll_middle(seed):
    r = _rng("lift", seed)                                      # where both arms reach
    return {ROLL: (round(float(r.uniform(-0.38, -0.22)), 3), round(float(r.uniform(-0.12, 0.12)), 3), 0.0)}


def _judge_lift(S):
    p, R = S.object_pose(ROLL)
    parts, other = S.touching(ROLL)
    return dict(ok=S.lifted(ROLL, dz=0.05), height_mm=round(float(p[2]) * 1000), held_by=_held(parts), touches=sorted(other))


def _judge_across(S):
    p, R = S.object_pose(ROLL)
    parts, other = S.touching(ROLL)
    on_table = bool(abs(p[0]) < TABLE_HALF and abs(p[1]) < TABLE_HALF and -0.01 < p[2] < 0.03 and other == {"world"})
    return dict(ok=bool(on_table and not parts and p[1] >= ACROSS_Y), xyz_mm=[round(float(v) * 1000) for v in p], on_table=on_table,
                released=not parts, flat=bool(abs(R[2, 2]) > 0.95), held_by=_held(parts))


def _held(parts):
    return {{"R": "right", "L": "left"}[k]: sorted(v) for k, v in parts.items()}


TASKS = {t.name: t for t in (
    Task("open_bottle",
         "Task: open the bottle that stands on the table in front of the robot: unscrew its cap and take it off. At the end "
         "the cap must be off the bottle (held in a hand or put down on the table), the bottle standing upright on the table. "
         + _BOTTLE, _bottle_layout, lambda S: S.judge_open_bottle(), 600.0, 80, 2),
    Task("move_across",
         "Task: a roll of white tape lies on the table on the robot's right, where only the right arm reaches. Put it down on "
         f"the table on the robot's left, beyond the line y = {ACROSS_Y * 1000:.0f} mm, where only the left arm reaches: at the end "
         "it must rest on the table there, with the robot not touching it. " + _ROLL,
         _roll_right, _judge_across, 400.0, 60, 2),
    Task("lift",
         "Task: pick up the roll of white tape that lies on the table in front of the robot and hold it still, at least "
         "50 mm above the table, touching nothing but the robot; then call done() (or let the program return). " + _ROLL,
         _roll_middle, _judge_lift, 240.0, 40, 1),
)}


class Episode:
    """One episode of a task in an environment: the simulation, the robot a controller gets, the judge."""

    def __init__(self, env, task, seed=0, sensors="d415", record=False, time_limit_s=None, segmenter=None):
        self.task = TASKS[task] if isinstance(task, str) else task
        self.env, self.seed, self.sensors = env, int(seed), sensors
        self.layout = self.task.layout(self.seed)
        self.S = make(env, seed=self.seed, sensors=sensors, layout=self.layout, record=record)
        self.robot = Robot(self.S, time_limit_s=self.task.time_limit_s if time_limit_s is None else time_limit_s, segmenter=segmenter)
        self.text = self.task.text
        self.verdict = None

    def finish(self):
        """Let the scene come to rest for 1 s, then judge it. (Idempotent.)"""
        if self.verdict is None:
            self.S.run(1.0)
            self.verdict = dict(self.task.judge(self.S), task=self.task.name, env=self.env, seed=self.seed,
                                robot_s=round(self.robot.time_s() - 1.0, 1))
        return self.verdict
