"""Continuity between iterations: real-robot runs imported into the workspace and announced in the next session's
prompt (learning/real_feedback.py), resumable sessions (learning/agent.py, loop.last_session_id), budget extension."""
import json

import cv2
import numpy as np

from agentic_robotics import paths
from agentic_robotics.execution.versions import tree_hash
from agentic_robotics.learning import agent, loop
from agentic_robotics.learning.real_feedback import add_real, prompt_note


def test_session_flags():
    c = agent.command("p", "s", "m", 1.0)
    assert "--no-session-persistence" in c and "--resume" not in c
    c = agent.command("p", "s", "m", 1.0, persist=True)
    assert "--no-session-persistence" not in c and "--resume" not in c
    c = agent.command("p", "s", "m", 1.0, resume="abc-123")
    assert "--no-session-persistence" not in c and c[c.index("--resume") + 1] == "abc-123"
    for flag in ("--restricted", "--strict-mcp-config", "--disable-slash-commands"):   # (the restrictions stay)
        assert flag in c


def test_last_session_id():
    L = [dict(kind="session", session_id="a", persisted=True), dict(kind="dev"), dict(kind="session", session_id="b", persisted=False)]
    assert loop.last_session_id(L) is None
    assert loop.last_session_id(L[:2]) == "a"


def fake_real_run(d, skills_hash, success=True):
    (d / "frames").mkdir(parents=True); (d / "plan").mkdir(); (d / "program").mkdir()
    cv2.imwrite(str(d / "frames" / "000_d415_rgb.png"), np.zeros((48, 64, 3), np.uint8))
    cv2.imwrite(str(d / "plan" / "sheet.jpg"), np.zeros((48, 64, 3), np.uint8))
    (d / "program" / "program.json").write_text("{}")
    (d / "events.jsonl").write_text(json.dumps(dict(t=0, what="seed")) + "\n")
    (d / "actions.jsonl").write_text(json.dumps(dict(n=0, mode="live", phase="look", dur=1.2, q=[[0] * 7], result=dict(ok=True, hold=[0] * 7))) + "\n")
    (d / "result.json").write_text(json.dumps(dict(
        mode="live", backend="robot", task=dict(instruction="Pick up the red tape roll and lift it."),
        verdict=dict(by="operator", success=success, held=success, knocked_off=False, intervened=False, note="slow descent"),
        robot_time_s=31.2, robot_time_limit_s=150, trajectories=dict(planned=5, sent=5, refused=0),
        program_result=dict(result="ok"), spec=dict(skills_hash=skills_hash, skills="/home/someone/secret/path"))))


def test_add_real_and_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "WORKSPACES", tmp_path / "workspaces")
    monkeypatch.setattr(paths, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(loop, "dev_eval", lambda run_id, name, d, X: dict(
        successes=0, n=90, t_success_mean=None, by_level={1: dict(successes=0, n=30)}, categories={}, platform_errors=0,
        model_calls=0))
    loop.init("t1", "configs/experiments/fidelity2_B.yaml")
    rd = loop.run_dir("t1")
    assert "Real-robot runs." in (paths.WORKSPACES / "t1" / "AGENTS.md").read_text()
    h = tree_hash(rd / "checkpoints" / "000_initial")
    fake_real_run(tmp_path / "r_ok", h, True)
    fake_real_run(tmp_path / "r_other", "nope", False)
    add_real("t1", [str(tmp_path / "r_ok"), str(tmp_path / "r_other")])
    real = paths.WORKSPACES / "t1" / "real"
    s1 = (real / "001_r_ok" / "summary.md").read_text()
    assert "000_initial (the current version)" in s1 and "SUCCESS" in s1 and "slow descent" in s1
    assert "not a version of this run" in (real / "002_r_other" / "summary.md").read_text()
    assert "secret" not in (real / "001_r_ok" / "result.json").read_text()          # (no machine paths copied)
    assert (real / "001_r_ok" / "frames" / "000_d415_rgb.png").exists() and (real / "README.md").exists()
    assert "joint" not in (real / "001_r_ok" / "actions.md").read_text().lower()
    st = loop.state("t1")
    assert len(st["real_pending"]) == 2
    note = prompt_note(st["real_pending"])
    assert "NEW REAL-ROBOT RUNS" in note and "2 live on the robot, 1 of them successes" in note
    B = json.loads((rd / "budget.json").read_text())
    p = loop.session_prompt(3, st, B, loop.read_ledger("t1"), real_note=note, resumed=True)
    assert p.startswith("Session 3 (you are continuing") and "NEW REAL-ROBOT RUNS" in p
    loop.extend("t1", practice=50, agent_usd=10)
    B2 = json.loads((rd / "budget.json").read_text())
    assert B2["practice"]["episodes_total"] == B["practice"]["episodes_total"] + 50
    assert B2["agent"]["usd_total"] == B["agent"]["usd_total"] + 10
