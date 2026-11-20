"""The place-box task (tasks.py, sim/task_layouts.py): what the program is told (kind "place", its five practice objects;
place-box-new: the objects it never practises on), the arm at its home joints (not over the target), and the judge
(in_box): the named object in the box and released = success; another object put in too = not."""
import math

import numpy as np

from agentic_robotics.evaluation.suites import episode_seed
from agentic_robotics.sim.scene import ClutterPickScene

PRACTICE = {"red tape roll", "yellow tape roll", "white tape roll", "coiled black cable", "screwdriver"}


def _scene(task, i=0):
    s = ClutterPickScene(colour="mujoco", mounts="calibrated", task=task)
    t = s.reset("val", 0, i, episode_seed(13, 0, i))
    return s, t


def test_task_text_and_home_start():
    for i in range(3):
        s, t = _scene("place-box", i)
        assert t["kind"] == "place" and t["destination"] == "white box" and t["object"] in PRACTICE
        assert t["instruction"] == f"Put the {t['object']} into the white box."
        q = s.P.d.qpos[s.P.world.qadr]
        assert np.abs(q - s.P.S.q_home).max() < 1e-3                  # (the real robot's start, not over the target)
        assert 1 <= len([k for k in s.distractors if k != "box_white"]) <= 3
    s, t = _scene("place-box-new", 0)
    assert t["kind"] == "place" and t["object"] in {"black block with holes", "glue bottle"}


def _put_in_box(s, k, dx=0.0):
    P, w = s.P, s.P.world
    bp, bR = w.pose("box_white")
    p = bp + bR @ np.array([dx, 0.0, 0.0])
    P.put(k, float(p[0]), float(p[1]), math.atan2(bR[1, 0], bR[0, 0]))
    P.d.qpos[w.oq[k] + 2] += 0.012
    P.mj.mj_forward(P.m, P.d)


def _hold(s, T=1.5):
    for _ in range(int(T / 0.1)):
        s.P.settle(0.1)
        s.jd = s.judge()
    return s.jd


def test_judge_in_box():
    s, t = _scene("place-box", 1)
    jd = _hold(s, 0.3)
    assert not jd["success"] and not jd["inside_box"]
    _put_in_box(s, s.target)
    jd = _hold(s)
    assert jd["success"] and jd["inside_box"], jd
    s, t = _scene("place-box", 1)
    other = [k for k in s.distractors if k != "box_white"][0]
    _put_in_box(s, s.target, -0.045); _put_in_box(s, other, 0.045)
    jd = _hold(s)
    assert not jd["success"] and jd["others_in_box"] == [other]
