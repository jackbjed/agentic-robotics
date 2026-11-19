"""The practice budget's accounting on a synthetic ledger: platform failures are not charged to the agent."""
import json

from agentic_robotics.learning.practice import checks_used, eval_runs_used, usage


def write(tmp_path, rows):
    (tmp_path / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return tmp_path


def test_practice_refunds_platform_errors(tmp_path):
    d = write(tmp_path, [dict(kind="practice", session="s01", episodes=12, robot_s=0, wall_s=0, status="started"),
                         dict(kind="practice", session="s01", episodes=-3, robot_s=100, wall_s=50, status="done", platform_errors=3),
                         dict(kind="practice", session="s01", episodes=4, robot_s=0, wall_s=0, status="started"),
                         dict(kind="practice", session="s01", episodes=-4, robot_s=0, wall_s=0, status="refund"),
                         dict(kind="practice", session="s02", episodes=8, robot_s=0, wall_s=0, status="started")])
    assert usage(d, "s01")["episodes"] == 9 and usage(d)["episodes"] == 17


def test_eval_runs_counted_once_and_void_runs_free(tmp_path):
    d = write(tmp_path, [dict(kind="eval_exposed", session="s01", status="started"),
                         dict(kind="eval_exposed", session="s01", status="done", platform_errors=60),
                         dict(kind="eval_exposed", session="s01", status="started"),
                         dict(kind="eval_exposed", session="s01", status="done", platform_errors=0),
                         dict(kind="eval_exposed", session="s02", status="started")])
    assert eval_runs_used(d, "s01") == 1 and eval_runs_used(d, "s02") == 1


def test_crashed_scene_checks_not_counted(tmp_path):
    d = write(tmp_path, [dict(kind="scene_check", session="s01"), dict(kind="scene_check", session="s01"),
                         dict(kind="scene_check_void", session="s01")])
    assert checks_used(d, "s01") == 1
