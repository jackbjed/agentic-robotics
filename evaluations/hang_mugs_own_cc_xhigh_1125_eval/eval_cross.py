"""A session's program in ANOTHER session's world (cross-evaluation: each agent's skill in the other agent's task
world, judged by that world's own check), on fresh seeds, the session's way (practice mode: the world's check needs
it; the programs do not use the truth). Resumable. No model calls.
  eval_cross.py PROGRAM WORLD OUT SEEDS TIME_LIMIT_S LABEL"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from agentic_robotics.bimanual import practice, scenes

prog, world, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
seeds, limit, label = practice.parse_seeds(sys.argv[4]), float(sys.argv[5]), sys.argv[6]
out.mkdir(parents=True, exist_ok=True)
book = out / "episodes.jsonl"
bridge = practice.SegmenterBridge()
def one(s):
    d = out / label / f"s{s}"
    if (d / "result.json").exists():
        return
    t = time.time()
    try:
        r = practice.run_sandboxed("sim_bimanual_grippers", "insert_varied", s, prog, d, sensors="d415", trace=True, bridge=bridge,
                                   time_limit_s=limit, wall_s=max(1800.0, 4 * limit), scene=scenes.empty(s), practice=os.environ.get("EVAL_PRACTICE", "1") != "0", world=world)
    except Exception as e:
        r = dict(ok=False, ended=f"runner error: {e!r}")
    line = dict(label=label, seed=s, ok=bool(r.get("ok")), robot_s=r.get("robot_s"), ended=str(r.get("ended"))[:200], check=r.get("world_check"), wall_s=round(time.time() - t))
    with open(book, "a") as f:
        f.write(json.dumps(line, default=str) + "\n")
    print(label, f"s{s}", "ok" if line["ok"] else "FAIL", line["robot_s"], line["ended"][:70], flush=True)
try:
    with ThreadPoolExecutor(int(os.environ.get("EVAL_WORKERS", "6"))) as ex:
        list(ex.map(one, seeds))
finally:
    bridge.close()
print("done", flush=True)
