"""The pack task (tasks.py, sim/task_layouts.py, sim/scene.py judge "packed"): what the program is told, and the judge on
the true state: every named object in the box and the lid shut = success after 1 s; one left out, or the lid open = not."""
import math

import numpy as np

from agentic_robotics.evaluation.suites import episode_seed
from agentic_robotics.sim.scene import ClutterPickScene


def _scene(i=0):
    s = ClutterPickScene(colour="mujoco", mounts="calibrated", task="pack")
    t = s.reset("val", 0, i, episode_seed(13, 0, i))
    return s, t


def _into_box(s, keys):
    P, w = s.P, s.P.world
    bp, bR = w.pose("box_white")
    yaw = math.atan2(bR[1, 0], bR[0, 0])
    offs = [(-0.045, -0.045), (0.045, 0.045), (-0.045, 0.045)]
    for k, (dx, dy) in zip(keys, offs):
        p = bp + bR @ np.array([dx, dy, 0.0])
        P.put(k, float(p[0]), float(p[1]), yaw)
        q = w.oq[k]; P.d.qpos[q + 2] += 0.012                     # (on the box's floor, not the table)
    P.mj.mj_forward(P.m, P.d)


def _hold(s, T=1.5, lid=None):
    P, w = s.P, s.P.world
    for _ in range(int(T / 0.1)):
        if lid is not None:
            P.d.qpos[w.lid_q] = lid; P.d.qvel[P.m.jnt_dofadr[P.m.joint("obj_box_white_lid_hinge").id]] = 0.0
        P.settle(0.1)
        s.jd = s.judge()
    return s.jd


def test_task_text():
    s, t = _scene(0)
    assert t["kind"] == "pack" and t["destination"] == "white box" and 2 <= len(t["objects"]) <= 3
    assert t["instruction"].startswith("Put the ") and t["instruction"].endswith("into the white box, then close its lid.")
    assert all(f"the {n}" in t["instruction"] for n in t["objects"])


def test_judge_packed_and_not():
    s, t = _scene(1)
    _into_box(s, s.targets[:-1])                                   # one left out, lid open
    jd = _hold(s)
    assert not jd["success"] and jd["n_in_box"] == len(s.targets) - 1
    _into_box(s, s.targets)                                        # all in, lid still open
    jd = _hold(s)
    assert not jd["success"] and jd["n_in_box"] == len(s.targets) and jd["lid_deg"] > 8
    jd = _hold(s, 1.6, lid=0.0)                                    # all in, lid shut
    assert jd["success"], jd
