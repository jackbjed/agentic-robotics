"""Configuration consistency (2026-11-19: two new task configs lacked wall_limit_s and every practice call crashed;
`ar-practice status` crashed in every run without custom scenes): every task config has what episodes and practice
read; every experiment's task families, practice ranges and suites agree; status works with scenes off."""
import json

import pytest

from agentic_robotics import paths, tasks as TK
from agentic_robotics.config import task_config
from agentic_robotics.learning import loop, practice

TASK_CONFIGS = sorted((paths.CONFIGS / "tasks").glob("*.yaml"))
EXPERIMENTS = sorted((paths.CONFIGS / "experiments").glob("*.yaml"))


@pytest.mark.parametrize("f", TASK_CONFIGS, ids=lambda f: f.stem)
def test_task_config_complete(f):
    t = task_config(str(f.relative_to(paths.ROOT)))
    for k in ("cameras", "perception", "object_set", "robot_time_limit_s", "wall_limit_s", "preset", "colour", "mounts"):
        assert t.get(k) is not None, (f.name, k)
    assert t.get("arm_start") in (None, "home")


@pytest.mark.parametrize("f", EXPERIMENTS, ids=lambda f: f.stem)
def test_experiment_consistent(f):
    X = paths.load_yaml(f)
    task_config(X["task"])
    assert (paths.ROOT / X["skill"]).is_dir()
    for spec in X["suites"].values():
        if spec.get("task"):
            task_config(spec["task"])
    if not X.get("tasks"):
        return
    fams = list(X["tasks"]) + list(X.get("test_tasks") or [])
    for f_ in fams:
        TK.get(f_)
    if X.get("real2sim"):                                      # (practice in the agent's own simulator: the real
        assert set(X["real2sim"]["families"]) == set(X["tasks"])   # world's ranges per task instead)
    else:
        for f_ in X["tasks"]:
            assert f_ in X["practice"]["families"], (f.name, f_)
    for name, spec in X["suites"].items():
        assert set(spec.get("families", {})) <= set(fams), (f.name, name)
    L = X.get("learning") or {}
    if L.get("exposed_eval_suite"):
        assert L["exposed_eval_suite"] in X["suites"]


def test_status_without_custom_scenes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "WORKSPACES", tmp_path / "ws"); monkeypatch.setattr(paths, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(loop, "dev_eval", lambda run_id, name, d, X: dict(successes=0, n=4, t_success_mean=None, by_level={},
                                                                            by_family={}, categories={}, platform_errors=0, model_calls=0))
    loop.init("st", "configs/experiments/evalset_B.yaml")
    ws = paths.WORKSPACES / "st"
    assert json.loads((loop.run_dir("st") / "budget.json").read_text())["scenes"] is None
    monkeypatch.chdir(ws); monkeypatch.setenv("AR_WORKSPACE", str(ws))
    practice.cmd_status(None)
    out = capsys.readouterr().out
    assert '"session_limit_episodes": 300' in out
