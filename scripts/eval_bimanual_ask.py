"""Evaluate a bimanual program that calls the model inside its episodes (robot.ask): `practice suite` with an
AskBridge, over several tasks, and resumable (an episode that has its result.json is not run again).

    .venv/bin/python -I scripts/eval_bimanual_ask.py --program runs/bimanual/X/program.py --model astra \\
        --tasks pick_clutter_medium pick_clutter_hard --seeds 2000-2049,5000-5049 --run-usd 12 --out runs/bimanual/X_eval

PAID: every robot.ask is a model call (at most practice.ASK["calls"] an episode), booked in runs/budget/openai.jsonl
under the tag bimanual_eval_<out's name>; --run-usd is this evaluation's hard cap (configs/models.yaml's total cap
holds too). The episodes have the cameras (d415) and SAM3 only, no truth. -> out/<task>/s<seed>/, out/summary.json.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_robotics.bimanual import practice  # noqa: E402
from agentic_robotics.bimanual.tasks import TASKS  # noqa: E402
from agentic_robotics.perception import vlm as V  # noqa: E402

OOD_FROM = 5000


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default="sim_bimanual_grippers")
    ap.add_argument("--tasks", nargs="+", required=True, choices=list(TASKS))
    ap.add_argument("--seeds", required=True, help='e.g. "2000-2049,5000-5049"')
    ap.add_argument("--program", required=True)
    ap.add_argument("--model", required=True, help="a model of configs/models.yaml, for robot.ask")
    ap.add_argument("--run-usd", type=float, required=True, help="this evaluation's hard cap on model spending")
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--sensors", default="d415", choices=["d415", "ideal"])
    ap.add_argument("--no-segmenter", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True)
    tag, seeds = f"bimanual_eval_{out.name}", practice.parse_seeds(a.seeds)
    make = lambda label: V.VLM(dict(model=a.model, max_calls_per_episode=practice.ASK["calls"], max_usd_per_episode=practice.ASK["reserve_usd"],
                                    run_usd=a.run_usd), tag=f"{tag}:ask:{label}")
    todo = [(t, s) for t in a.tasks for s in seeds if not (out / t / f"s{s}" / "result.json").exists()]
    print(f"{len(todo)} of {len(a.tasks) * len(seeds)} episodes to run; ${V.spent(tag):.2f} of this evaluation's ${a.run_usd:.2f} spent", flush=True)
    bridge = None if a.no_segmenter else practice.SegmenterBridge()
    asker = practice.AskBridge(make, dir=bridge.dir if bridge is not None else None)

    def one(ts):
        t, s = ts
        r = practice.run_sandboxed(a.env, t, s, a.program, out / t / f"s{s}", sensors=a.sensors, bridge=bridge, asker=asker, ask_label=f"{t}_s{s}")
        print(f"{t} seed {s}  {'ok  ' if r['ok'] else 'FAIL'}  {r['robot_s'] if r['robot_s'] is not None else '-':>6} s  "
              f"{r.get('model_calls', 0)} calls ${r.get('model_usd', 0.0):.3f}  {str(r['ended'])[:90]}", flush=True)

    try:
        with ThreadPoolExecutor(max(1, a.jobs)) as ex:
            list(ex.map(one, todo))
    finally:
        asker.close()
        if bridge is not None:
            bridge.close()

    summary = dict(program=str(a.program), env=a.env, model=a.model, sensors=a.sensors, spent_usd=round(V.spent(tag), 4), cells={})
    for t in a.tasks:
        for name, ss in (("ID", [s for s in seeds if s < OOD_FROM]), ("OOD", [s for s in seeds if s >= OOD_FROM])):
            res = [json.loads(f.read_text()) for s in ss if (f := out / t / f"s{s}" / "result.json").exists()]
            ok = [r for r in res if r["ok"]]
            mean = lambda x: round(sum(x) / len(x), 2) if x else None
            c = dict(successes=len(ok), episodes=len(ss), no_result=len(ss) - len(res), robot_s_mean_successes=mean([r["robot_s"] for r in ok]),
                     robot_s_mean=mean([r["robot_s"] for r in res if r["robot_s"] is not None]), model_calls_mean=mean([r.get("model_calls", 0) for r in res]),
                     model_usd=round(sum(r.get("model_usd", 0.0) for r in res), 4), failed=[r["seed"] for r in res if not r["ok"]],
                     missing=[s for s in ss if not (out / t / f"s{s}" / "result.json").exists()])
            if ss:
                summary["cells"][f"{t} {name}"] = c
                print(f"{t} {name}: {c['successes']}/{c['episodes']}  {c['robot_s_mean_successes']} s  {c['model_calls_mean']} calls/episode  "
                      f"${c['model_usd']:.2f}" + (f"  NO RESULT: {c['missing']}" if c["missing"] else ""), flush=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"spent ${summary['spent_usd']:.2f}", flush=True)


if __name__ == "__main__":
    main()
