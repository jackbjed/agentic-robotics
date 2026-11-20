"""Progress snapshots (learning/agent.py _pump, loop.progress_snapshotter / progress_eval): the session's stream is
copied without its text deltas, each message's final output tokens are counted, the workspace's code is copied at every
multiple of N over the whole run; the curve scores each distinct version once. And the keep rule after real-robot
feedback (loop.select real=True)."""
import io
import json
import sys

from agentic_robotics import paths
from agentic_robotics.learning import agent, loop


def _delta(n, thinking=0):
    return json.dumps(dict(type="stream_event", event=dict(type="message_delta", usage=dict(
        output_tokens=n, output_tokens_details=dict(thinking_tokens=thinking))))) + "\n"


STREAM = [json.dumps(dict(type="system", subtype="init", session_id="abc", model="m")) + "\n",
          json.dumps(dict(type="stream_event", event=dict(type="content_block_delta", delta=dict(text="x" * 50)))) + "\n",
          _delta(30000, 20000),
          json.dumps(dict(type="assistant", message=dict(id="1", content=[dict(type="tool_use", name="Edit")]))) + "\n",
          _delta(30000), "not json\n", _delta(50000, 1000),
          json.dumps(dict(type="result", total_cost_usd=1.5, usage=dict(output_tokens=110000), num_turns=3)) + "\n"]


def test_pump_counts_and_snapshots():
    got, out = [], io.StringIO()
    acct = dict(output_tokens=0, thinking_tokens=0, messages=0, snapshots=0)
    agent._pump(iter(STREAM), out, acct, dict(every=50000, offset=20000, snapshot=lambda at, info: got.append((at, info))), 0.0)
    assert [a for a, _ in got] == [50000, 100000]                    # (run tokens 50k, 80k, 130k)
    assert got[1][1]["run_output_tokens"] == 130000 and got[0][1]["session_thinking_tokens"] == 20000
    assert acct["output_tokens"] == 110000 and acct["messages"] == 3 and acct["snapshots"] == 2
    txt = out.getvalue()
    assert "content_block_delta" not in txt and txt.count('"usage_delta"') == 3 and "not json" in txt
    assert '"type": "result"' in txt


def test_run_session_with_progress(tmp_path, monkeypatch):
    fake = tmp_path / "fake_claude.py"
    fake.write_text("import sys\nfor l in " + repr(STREAM) + ":\n    sys.stdout.write(l); sys.stdout.flush()\n")
    monkeypatch.setattr(agent, "command", lambda *a, **k: [sys.executable, str(fake)])
    monkeypatch.setattr(agent, "cli_info", lambda: {})
    got = []
    res = agent.run_session(tmp_path, "p", "s", tmp_path / "log", wall_limit_s=60,
                            progress=dict(every=40000, offset=0, snapshot=lambda at, info: got.append(at)))
    assert got == [40000, 80000] and res["progress"]["output_tokens"] == 110000 and res["cost_usd"] == 1.5
    assert res["init"]["session_id"] == "abc"


def test_snapshotter_and_curve(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RUNS", tmp_path / "runs")
    rd = loop.run_dir("r"); (rd / "checkpoints" / "000_initial" / "skills").mkdir(parents=True)
    (rd / "checkpoints" / "000_initial" / "skills" / "place.py").write_text("v0\n")
    (rd / "experiment.json").write_text(json.dumps(dict(task="configs/tasks/clutter_pick_A_rig.yaml", vlm=None,
                                                        suites=dict(progress=dict(split="test", n=2, seed=1)))))
    ws = tmp_path / "ws"; (ws / "skills").mkdir(parents=True)
    snap = loop.progress_snapshotter("r", ws, "s01")
    for at, v in ((50000, "v1"), (100000, "v1"), (150000, "v2")):
        (ws / "skills" / "place.py").write_text(v + "\n")
        snap(at, dict(session_output_tokens=at))
    assert sorted(p.name for p in (rd / "progress").iterdir() if p.is_dir()) == ["t00050000_s01", "t00100000_s01", "t00150000_s01"]
    calls = []

    def fake_suite(d, spec, t, out, **k):
        v = (d / "skills" / "place.py").read_text().strip()
        calls.append(v)
        n = {"v0": 0, "v1": 1, "v2": 2}[v]
        return dict(successes=n, n=2, t_success_mean=10.0, by_family={}, platform_errors=0, model_calls=n, model_cost_usd=0.0)
    monkeypatch.setattr(loop, "run_suite", fake_suite)
    rows = loop.progress_eval("r")
    assert calls == ["v0", "v1", "v2"]                                  # (v1 twice: scored once)
    assert [r["successes"] for r in rows] == [0, 1, 1, 2] and [r["at_output_tokens"] for r in rows] == [0, 50000, 100000, 150000]
    assert (rd / "progress" / "curve.svg").read_text().startswith("<svg") and "| 150,000 |" in (rd / "progress" / "curve.md").read_text()


def test_select_after_real_feedback(monkeypatch):
    def fake(gain, dt):
        monkeypatch.setattr(loop, "compare", lambda a, b: dict(b_successes=90 + gain, a_successes=90, only_a=[], only_b=[],
                                                              mcnemar_p=1.0, dt_mean=dt, dt_ci95=None, both=80))
        monkeypatch.setattr(loop, "per_family", lambda a, b: {})
    S, rule = dict(platform_errors=0), dict(min_gain=2, min_dt_s=0.5)
    fake(0, 0.3)
    assert not loop.select("a", "b", S, rule)["keep"] and loop.select("a", "b", S, rule, real=True)["keep"]
    fake(-1, 0.9)
    assert loop.select("a", "b", S, rule, real=True)["keep"]
    fake(-2, 0.0)
    assert not loop.select("a", "b", S, rule, real=True)["keep"]
    fake(0, 1.5)
    assert not loop.select("a", "b", S, rule, real=True)["keep"]
