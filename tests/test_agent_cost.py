"""Agent cost per session (learning/agent.py, loop.session_costs): Claude Code's total_cost_usd is cumulative over a
resumed conversation (2026-11-20: evalset_B_ctrl_r1 reported $14.47, $29.99, $37.35 for its three resumed sessions,
whose own costs were $14.47, $15.52, $7.36), so a session's own cost is that figure minus the previous total."""
import json
import sys

from agentic_robotics.learning import agent, loop


def _rec(session, cost, sid="abc", resumed_from=None, **k):
    return dict(dict(kind="session", session=session, cost_usd=cost, session_id=sid, persisted=True,
                     resumed_from=resumed_from), **k)


def test_legacy_resumed_chain():
    """Records written before the fix hold the CLI's cumulative figure in cost_usd."""
    L = [dict(kind="init"), _rec("s01", 14.47), dict(kind="practice", episodes=3), _rec("s02", 29.99, resumed_from="abc"),
         _rec("s03", 37.35, resumed_from="abc")]
    assert [c for _, c in loop.session_costs(L)] == [14.47, 15.52, 7.36]
    assert abs(loop.agent_spent(L) - 37.35) < 1e-9
    assert loop.cli_cost_total(L, "abc") == 37.35 and loop.cli_cost_total(L, "other") == 0.0


def test_fresh_sessions_unchanged():
    L = [_rec("s01", 2.0, sid="a", persisted=False), _rec("s02", 3.0, sid="b")]
    assert loop.agent_spent(L) == 5.0


def test_new_records_and_mixed():
    """After the fix: own cost in cost_usd, the CLI's figure in cost_usd_total (resuming a legacy chain too)."""
    L = [_rec("s01", 14.47), _rec("s02", 29.99, resumed_from="abc"),
         _rec("s03", 7.36, resumed_from="abc", cost_usd_total=37.35), _rec("s04", None, resumed_from="abc", cost_usd_total=None)]
    assert [c for _, c in loop.session_costs(L)] == [14.47, 15.52, 7.36, 0.0]
    assert loop.cli_cost_total(L, "abc") == 37.35              # (s04 reported nothing: the last known total)


def test_run_session_subtracts_previous_total(tmp_path, monkeypatch):
    stream = [json.dumps(dict(type="system", subtype="init", session_id="abc")) + "\n",
              json.dumps(dict(type="result", total_cost_usd=37.35, usage=dict(output_tokens=10), num_turns=3)) + "\n"]
    fake = tmp_path / "fake_claude.py"
    fake.write_text("import sys\nfor l in " + repr(stream) + ":\n    sys.stdout.write(l)\n")
    monkeypatch.setattr(agent, "command", lambda *a, **k: [sys.executable, str(fake)])
    monkeypatch.setattr(agent, "cli_info", lambda: {})
    r = agent.run_session(tmp_path, "p", "s", tmp_path / "log", wall_limit_s=60, resume="abc", cost_before=29.99)
    assert r["cost_usd"] == 7.36 and r["cost_usd_total"] == 37.35 and r["cost_before_usd"] == 29.99
    r = agent.run_session(tmp_path, "p", "s", tmp_path / "log2", wall_limit_s=60)
    assert r["cost_usd"] == r["cost_usd_total"] == 37.35 and "cost_before_usd" not in r
