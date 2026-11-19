"""The success check against known successes and failures (each through the full platform: world server + sandboxed
program). Slow (~15 s each): `pytest tests/test_judge.py`."""
import json
import textwrap
from pathlib import Path

import pytest

from agentic_robotics import paths
from agentic_robotics.evaluation.suites import episode_seed
from agentic_robotics.execution.episode import run_episode

SKILL = paths.INITIAL_SKILLS / "clutter_pick"


def ep(level, idx, split="test", seed=5, **kw):
    return dict(dict(split=split, level=level, idx=idx, seed=episode_seed(seed, level, idx), scene=dict(preset="d415"),
                     perception="oracle", robot_time_limit_s=60, record=dict(video=False, observations="none")), **kw)


def program(tmp_path, body):
    d = tmp_path / "prog"; (d / "skills").mkdir(parents=True)
    (d / "skills" / "pick.py").write_text(textwrap.dedent(body))
    return d


def test_known_success_parity(tmp_path):
    """test L0 #10: the 11-18 baseline succeeded at 13.43 s; the platform must reproduce it exactly."""
    r = run_episode(SKILL, ep(0, 10, parity=True), tmp_path / "e")
    assert r["success"] and r["category"] == "success" and abs(r["t_success"] - 13.43) < 1e-6


def test_known_failure_blocked(tmp_path):
    """test L0 #61: the baseline reported 'blocked' (no grasp fits) and never lifted the target."""
    r = run_episode(SKILL, ep(0, 61, parity=True), tmp_path / "e")
    assert not r["success"] and r["category"] in ("not_touched", "touched_not_lifted")


def test_doing_nothing_fails(tmp_path):
    d = program(tmp_path, """
        def run(ctx):
            ctx.robot.hold(2.0)
            return dict(result="did nothing")
    """)
    r = run_episode(d, ep(0, 10), tmp_path / "e")
    assert not r["success"] and r["category"] == "not_touched" and r["max_lift_mm"] < 5


def test_wrong_object_detected(tmp_path):
    """The skill is told the OTHER roll's name: it picks that one; the judge (true target) must call it wrong_object."""
    r = run_episode(SKILL, ep(0, 10, test_name_other_roll=True), tmp_path / "e")
    assert not r["success"] and r["category"] == "wrong_object", r["category"]


def test_crash_is_a_failure(tmp_path):
    d = program(tmp_path, """
        def run(ctx):
            raise RuntimeError("boom")
    """)
    r = run_episode(d, ep(0, 10), tmp_path / "e")
    assert not r["success"] and "boom" in (r.get("program_error") or "")


def test_time_limit_refuses_motion(tmp_path):
    d = program(tmp_path, """
        def run(ctx):
            ctx.robot.hold(70.0)
            return dict(result="held")
    """)
    r = run_episode(d, ep(0, 10, robot_time_limit_s=5), tmp_path / "e")
    assert not r["success"] and r["robot_time_s"] < 75
