"""simple_sim (docs/simple_sim.md): its world (the table 5 cm lower, primitives, the grey box), the robot's limits there
(the world's arm keeps the fingertips over ITS table; the program's planner gets a floor that does not give the height
away), the judges on its box, a scripted pick-and-place through the robot's own motion path (the tasks are doable on
the lower table), and the docs it is given (no table height, no evaluation objects)."""
import math

import numpy as np
import pytest

from agentic_robotics.evaluation.suites import episode_seed
from agentic_robotics.sim.scene import OBJECT_SETS, ClutterPickScene


@pytest.fixture(scope="module")
def place():
    s = ClutterPickScene(colour="mujoco", mounts="calibrated", task="place-box", object_set="simple", arm_start="home")
    return s


def test_world_and_task(place):
    s = place
    t = s.reset("val", 0, 0, episode_seed(11, 0, 0))
    P = s.P
    assert type(P.world).__name__ == "SimpleBoxSim" and s.table_z == -0.05
    g = P.m.geom("table_top").id
    assert abs(P.d.geom_xpos[g][2] + P.m.geom_size[g][2] - (-0.05)) < 1e-6
    assert t["kind"] == "place" and t["destination"] == "grey box" and t["instruction"].endswith("into the grey box.")
    assert t["object"] in OBJECT_SETS["simple"]["phrases"].values()
    for k in s.L["objs"]:                                          # (everything rests on the lowered table)
        assert -0.05 < P.com_z(k) < 0.0, (k, P.com_z(k))
    assert s.target in OBJECT_SETS["simple"]["pool"]


def test_limits(place):
    P = place.P
    sf = P.rig["safety"]
    assert sf["table_z"] == -0.05 and sf["tcp_lo"][2] == -0.10
    assert place.planner == dict(table_z=-0.10, tcp_lo_z=-0.10)    # (the program's planner: a floor, not the table)
    q = P.S.q_home.copy()                                          # (the world's arm refuses fingertips under its table)
    from agentic_robotics.sim.twin import B, TIP
    R = B.down(0.0)
    q_ok, e_ok = P.S.ik(np.array([-0.30, 0.0, -0.030]) - R[:, 2] * TIP, R, q)
    q_bad, e_bad = P.S.ik(np.array([-0.30, 0.0, -0.065]) - R[:, 2] * TIP, R, q)
    assert e_ok < 0.002 and e_bad > 0.5


def test_real_world_unchanged(place):
    s = ClutterPickScene(colour="mujoco", mounts="calibrated", task="place-box", object_set="real")
    sf = s.P.rig["safety"]
    assert sf["table_z"] == 0.0 and sf["tcp_lo"][2] == 0.0 and s.planner is None and s.table_z == 0.0
    assert type(s.P.world).__name__ == "BoxSim"
    t = s.reset("val", 0, 0, episode_seed(11, 0, 0))
    assert t["destination"] == "white box"


def _go(S, p, R, grip, T, phase):
    from agentic_robotics.sim.twin import TIP
    S.move(np.asarray(p, float) - R[:, 2] * TIP, R, grip, T, phase)
    S.flush()


def test_scripted_pick_and_place(place):
    """Privileged (the true pose): grasp the target on the lowered table, lift it, carry it over the box, drop it in."""
    from agentic_robotics.sim.twin import B
    s = place
    s.reset("val", 0, 3, episode_seed(11, 0, 3))
    P, S, k = s.P, s.P.S, s.target
    p, Rk = P.world.pose(k)
    yaw = math.atan2(Rk[1, 0], Rk[0, 0])
    top = p[2] + P.footprint(k)[1]
    R = B.down(yaw)
    _go(S, [p[0], p[1], top + 0.10], R, 0.04, 2.5, "transit")
    _go(S, [p[0], p[1], top - 0.022], R, 0.04, 1.5, "descend")
    _go(S, [p[0], p[1], top - 0.022], R, 0.0, 0.8, "close the gripper")
    _go(S, [p[0], p[1], top + 0.12], R, 0.0, 1.5, "lift")
    assert P.com_z(k) - s.rest[k] > 0.05 and P.held_by_both(k)
    bp, _ = P.world.pose("box_white")                              # (lowered in, released 1 cm over the floor)
    _go(S, [bp[0], bp[1], top + 0.12], R, 0.0, 2.5, "transit")
    _go(S, [bp[0], bp[1], top + 0.012], R, 0.0, 1.5, "descend")
    _go(S, [bp[0], bp[1], top + 0.012], R, 0.04, 0.6, "open the gripper")
    _go(S, [bp[0], bp[1], top + 0.15], R, 0.04, 1.0, "retreat")
    for _ in range(15):
        P.settle(0.1); s.jd = s.judge()
    assert s.jd["inside_box"] and s.jd["success"], s.jd


def test_lid_judge():
    s = ClutterPickScene(colour="mujoco", mounts="calibrated", task="close-box", object_set="simple")
    t = s.reset("val", 0, 1, episode_seed(11, 0, 1))
    assert t["instruction"] == "Close the lid of the grey box."
    P, w = s.P, s.P.world
    assert math.degrees(P.d.qpos[w.lid_q]) > 60
    jid = P.m.joint("obj_box_white_lid_hinge").id
    for _ in range(16):
        P.d.qpos[w.lid_q] = 0.0; P.d.qvel[P.m.jnt_dofadr[jid]] = 0.0
        P.settle(0.1); s.jd = s.judge()
    assert s.jd["success"] and s.jd["lid_deg"] < 8


def test_no_simple_press():
    with pytest.raises(ValueError):
        ClutterPickScene(task="press", object_set="simple")


def test_docs_say_nothing_of_the_table_height():
    from agentic_robotics import paths
    from agentic_robotics.learning.loop import scratch_doc, simple_doc, strip_vlm
    for name, src in (("robot.md", "robot.md"), ("perception.md", "perception.md"), ("skill_api.md", "skill_api_scratch.md")):
        for vlm in (False, True):                                  # (init's order: simple_doc, strip_vlm, scratch_doc)
            txt = simple_doc(name, (paths.ROOT / "docs" / src).read_text())
            txt = scratch_doc(name, txt if vlm else strip_vlm(txt))
            low = txt.lower()
            for bad in ("z = 0", "-0.05", "−0.05", "tape roll", "white box", "noisy", "calibration error", "twin's frame"):
                assert bad not in low, (name, vlm, bad)
    t = (paths.ROOT / "docs" / "task_simple.md").read_text().lower()
    assert "approximate" in t and "table height" in t and "0.05" not in t and "tape" not in t
