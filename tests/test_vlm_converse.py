"""The VLM baselines' multi-turn calls (perception/vlm.py respond), with the provider mocked: images filled in, the
conversation chained, every turn booked and capped; refused unless the run allows it. No money is spent."""
import json

import numpy as np

from agentic_robotics.perception import vlm as V


class _Resp:
    status_code = 200

    def __init__(self, n):
        self.n = n

    def json(self):
        return {"id": f"resp_{self.n}", "status": "completed",
                "output": [{"type": "function_call", "name": "look", "arguments": "{}", "call_id": f"c{self.n}"}],
                "usage": {"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {"cached_tokens": 200}}}


def test_respond(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "LEDGER", tmp_path / "ledger.jsonl")
    sent = []

    def post(url, json=None, timeout=None, headers=None):
        sent.append(json); return _Resp(len(sent))
    import requests
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(V.VLM, "key", lambda self: "test")
    assert not V.VLM(dict(enabled=True), "t:x").respond([], [])["ok"]                 # (converse not allowed)
    m = V.VLM(dict(enabled=True, converse=True, max_calls_per_episode=2, max_usd_per_episode=1.0, run_usd=5.0), "t:x")
    im = np.zeros((4, 4, 3), np.uint8)
    r1 = m.respond([{"role": "user", "content": [{"type": "input_text", "text": "hi"}, {"type": "input_image", "image": 0}]}],
                   [im], instructions="be a robot", tools=[{"type": "function", "name": "look"}])
    assert r1["ok"] and r1["id"] == "resp_1" and r1["output"][0]["name"] == "look"
    c = sent[0]["input"][0]["content"][1]
    assert c["type"] == "input_image" and c["image_url"].startswith("data:image/png;base64,")
    r2 = m.respond([{"type": "function_call_output", "call_id": "c1", "output": "ok"}], [], previous=r1["id"])
    assert r2["ok"] and sent[1]["previous_response_id"] == "resp_1"
    assert not m.respond([], [])["ok"]                                                # (2 calls per episode)
    usd = (800 * 12.5 + 200 * 1.0 + 100 * 50.0) / 1e6
    assert abs(V.spent("t") - 2 * usd) < 1e-9 and abs(m.usage()["model_cost_usd"] - 2 * usd) < 1e-6
    recs = [json.loads(l) for l in open(tmp_path / "ledger.jsonl")]
    assert sum(r["usd"] for r in recs if r.get("reserve") is not None) == 0          # (reservations released)


def test_run_tags_are_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "LEDGER", tmp_path / "ledger.jsonl")
    for tag, usd in (("r1:practice", 1.0), ("r1-dev:dev", 2.0), ("r1-heldout:h", 4.0)):
        V._record(tag=tag, usd=usd)
    assert V.spent("r1") == 1.0 and V.spent("r1-dev") == 2.0 and V.spent() == 7.0
