"""real/run.py: Astra on by default, with the vlm block of the skill's own learning run (else fidelity2_A's); --no-vlm
turns it off; one call per episode at most; the ledger tag 'real' and the cap for all real runs."""
import importlib.util
import json
from types import SimpleNamespace

from agentic_robotics import paths


def _run():
    spec = importlib.util.spec_from_file_location("real_run", paths.ROOT / "real" / "run.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_vlm_default(tmp_path):
    R = _run()
    run = tmp_path / "runs" / "learning" / "x"; ck = run / "checkpoints" / "s02"; ck.mkdir(parents=True)
    (run / "budget.json").write_text("{}")
    (run / "experiment.json").write_text(json.dumps(dict(vlm=dict(enabled=True, model="astra", max_calls_per_episode=3,
                                                                  max_usd_per_episode=0.02))))
    a = SimpleNamespace(no_vlm=False, vlm=None, vlm_usd=2.0)
    V = R.vlm_block(a, ck)
    assert V["tag"] == "real" and V["run_usd"] == 2.0 and V["max_calls_per_episode"] == 1 and V["max_usd_per_episode"] == 0.02
    V = R.vlm_block(a, tmp_path)                                           # (not a run's checkpoint: fidelity2_A's block)
    assert V["model"] == "astra" and V["max_calls_per_episode"] == 1
    assert R.vlm_block(SimpleNamespace(no_vlm=True, vlm=None, vlm_usd=2.0), ck) is None
