"""The named settings (xsim/configs/) say what the experiments ran with.

    PYTHONNOUSERSITE=1 ../.venv/bin/python -I tests/test_configs.py [the folder that holds out/xsim and out/xsim_10x10]"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from xsim import config  # noqa: E402

C5 = HERE / "xsim" / "configs" / "sweep_blocks_5x5_usd80_sim300.json"
C10 = HERE / "xsim" / "configs" / "sweep_blocks_10x10_usd120_sim600.json"


def test_1_the_values():
    a, b = config.load(C5), config.load(C10)
    assert (a["budget_usd"]["A"], a["budget_usd"]["build"]) == (20.0, 30.0) == (b["budget_usd"]["A"], b["budget_usd"]["build"])
    assert [a["budget_usd"][c] for c in "BCD"] == [80.0] * 3 and a["target_submissions"] == 5 and [config.scenes(a, c) for c in "BCD"] == [5] * 3 and a["practice_episode_cap"] == 300
    assert [b["budget_usd"][c] for c in "BCD"] == [120.0] * 3 and b["target_submissions"] == 10 and [config.scenes(b, c) for c in "BCD"] == [10] * 3 and b["practice_episode_cap"] == 600
    assert 80 in a["checkpoints_usd"] and 80 in b["checkpoints_usd"] and max(b["checkpoints_usd"]) == 120
    assert "_about" not in a and a["model"] == b["model"] == "claude-opus-5-5" and a["effort"] == b["effort"] == "xhigh" and a["target_panel"] == b["target_panel"] == 20


def against_records(base):
    """The named files against what the sessions' own manifests recorded (their budgets)."""
    out = {}
    for cfgf, root in ((C5, Path(base) / "out" / "xsim" / "sweep_blocks" / "rep0"), (C10, Path(base) / "out" / "xsim_10x10" / "sweep_blocks" / "rep0")):
        c = config.load(cfgf)
        for cond in "BCD":
            for src in ("manifest.json", "state.json"):
                f = root / cond / src
                if not f.exists():
                    continue
                d = json.loads(f.read_text())
                if src == "manifest.json":
                    b = d["budgets"]
                    got = (b["usd"], b["target_submissions"], b["target_panel"], b["practice_episodes"])
                else:
                    k = d.get("cfg") or d.get("config") or {}
                    if not k:
                        continue
                    got = (k["budget_usd"][cond], k["target_submissions"], config.scenes(k, cond), k["practice_episode_cap"] if cond in "CD" else 0)
                want = (c["budget_usd"][cond], c["target_submissions"], config.scenes(c, cond), c["practice_episode_cap"] if cond in "CD" else 0)
                out[f"{root.parent.parent.name}/{cond}/{src}"] = (got == want, got, want)
    return out


if __name__ == "__main__":
    test_1_the_values(); print("PASS  test_1_the_values")
    for base in sys.argv[1:]:
        for k, v in against_records(base).items():
            print("RECORDED", "match" if v[0] else "DIFFERENT", k, v[1], "named:", v[2])
