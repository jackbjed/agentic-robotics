"""The task families' judges (tasks.py, sim/scene.py _judge_task) on the true state, without a program: each layout
starts not done and stays not done when nothing happens; the success state, set directly (the kettle's latch through
its mechanism: the button pushed in by a force), is judged a success after the 1 s hold. Each case runs in its own process (the
object set is process-wide). ~10 s each: `pytest tests/test_tasks.py`."""
import json
import os
import subprocess
import sys
import textwrap

import pytest

from agentic_robotics import paths

PROBE = textwrap.dedent("""
    import json, sys
    import numpy as np
    from agentic_robotics.sim.scene import ClutterPickScene
    task, idx, action = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    sc = ClutterPickScene(task=task); sc.reset("val", 0, idx, 5)
    P = sc.P; w = P.world; m, d = P.m, P.d

    def run(T):
        ok = False
        for _ in range(int(T * 30)):
            P.arm.idle(1 / 30); jd = sc.judge(); ok |= jd["success"]
        return ok, {k: v for k, v in jd.items() if k not in ("disturb",) and not isinstance(v, np.ndarray)}

    def free(k):
        return m.jnt_dofadr[m.body_jntadr[w.ob[k]]]

    start, _ = run(0.1)
    if action == "nothing":
        ok, jd = run(2.0)
    elif action == "press_button":                     # a 6 N push on the button for 0.3 s (the latch gives at 2.5 mm)
        b = m.jnt_dofadr[m.joint("obj_kettle_button_slide").id]
        d.qfrc_applied[b] = -6.0; ok, jd = run(0.3); d.qfrc_applied[b] = 0.0; ok, jd = run(3.0)
    elif action == "close_lid":
        d.qpos[w.lid_q] = 0.0; d.qvel[:] = 0; ok, jd = run(2.0)
    elif action == "onto_stick":                       # the cylinder threaded on stick 1, 4 cm under its tip
        g = m.geom("obj_peg_stand_peg1").id; tip = d.geom_xpos[g][2] + m.geom_size[g][1]
        a = w.oq["wood_cyl"]; d.qpos[a:a + 3] = [*d.geom_xpos[g][:2], tip - 0.04]; d.qpos[a + 3:a + 7] = [1, 0, 0, 0]
        d.qvel[free("wood_cyl"):free("wood_cyl") + 6] = 0; ok, jd = run(2.0)
    elif action == "beside_stick":                     # on the table 6 cm from the stand's middle stick
        g = m.geom("obj_peg_stand_peg1").id
        a = w.oq["wood_cyl"]; d.qpos[a:a + 2] = d.geom_xpos[g][:2] + [0.0, 0.06]
        d.qvel[free("wood_cyl"):free("wood_cyl") + 6] = 0; ok, jd = run(2.0)
    elif action == "into_box":                         # the target dropped into the open box's middle
        k = sc.target; bp = w.pose("box_white")[0]; a = w.oq[k]
        d.qpos[a:a + 3] = [bp[0], bp[1], P.rest_z0[k] + 0.02]; d.qpos[a + 3:a + 7] = [1, 0, 0, 0]
        d.qvel[free(k):free(k) + 6] = 0; ok, jd = run(3.0)
    elif action == "parked_idle":                      # 40 s: the parked (sprung) kettle must stay put
        a = w.oq["kettle"]; q0 = d.qpos[a:a + 3].copy(); t0 = d.time
        ok, jd = run(40.0)
        jd = dict(jd, kettle_moved_mm=float(np.abs(d.qpos[a:a + 3] - q0).max() * 1000), resets=int(d.warning[1].number),
                  clock_s=float(d.time - t0), parked="kettle" in P.parked)
    print("RESULT " + json.dumps(dict(start=bool(start), ok=bool(ok), target=sc.target, jd=jd), default=str))
""")


def probe(task, idx, action):
    env = dict(os.environ, PYTHONNOUSERSITE="1", MUJOCO_GL="egl")
    env.pop("VIRTUAL_ENV", None)
    r = subprocess.run([sys.executable, "-I", "-c", PROBE, task, str(idx), action], capture_output=True, text=True,
                       env=env, timeout=600, cwd=paths.ROOT)
    line = [x for x in r.stdout.splitlines() if x.startswith("RESULT ")]
    assert line, r.stderr[-2000:]
    return json.loads(line[0][7:])


def first_idx(task, pred):
    for i, l in enumerate(open(paths.DATA / f"layouts_{task}" / "val_L0.jsonl")):
        if pred(json.loads(l)):
            return i
    pytest.skip(f"no {task} layout fits")


@pytest.mark.parametrize("task,action", [("press", "nothing"), ("close-box", "nothing"), ("insert", "nothing"),
                                         ("place", "nothing")])
def test_nothing_done_is_not_success(task, action):
    r = probe(task, 0, action)
    assert not r["start"] and not r["ok"], r


def test_press_button_releases_lid():
    r = probe("press", 0, "press_button")
    assert r["ok"] and not r["jd"]["latched"] and r["jd"]["kettle_lid_deg"] > 30, r


def test_close_box_closed_lid():
    r = probe("close-box", 0, "close_lid")
    assert r["ok"] and r["jd"]["lid_deg"] < 8, r


def test_insert_on_stick():
    r = probe("insert", 0, "onto_stick")
    assert r["ok"] and r["jd"]["on_stick"], r


def test_insert_beside_stick_fails():
    r = probe("insert", 0, "beside_stick")
    assert not r["ok"] and not r["jd"]["on_stick"], r


def test_place_in_box():
    i = first_idx("place", lambda L: L["target"].startswith("tape"))
    r = probe("place", i, "into_box")
    assert r["ok"] and r["jd"]["inside_box"], r


def test_parked_kettle_stays_put():
    """19-11: in insert scenes the parked kettle crept (its lid spring against the latch), drifted off and MuJoCo reset
    the world after ~31 s (the robot clock went negative). Parked sprung objects are now damped still."""
    r = probe("insert", 2, "parked_idle")
    jd = r["jd"]
    assert jd["parked"] and jd["resets"] == 0 and jd["kettle_moved_mm"] < 1.0 and jd["clock_s"] > 39, jd
