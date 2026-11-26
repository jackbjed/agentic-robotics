"""The submitted program of the from-nothing insertion session on the simulator's own held-out insertion scenes (the
45 of the earlier insertion versions), as a one-armed scene (the scene's "arm": "left": the robot the program was
written for; the right arm stays parked), evaluation mode (cameras only: no truth, no judge), the simulator's own
judge. Resumable. No model calls.
  eval_heldout.py PROGRAM OUT SEEDS TIME_LIMIT_S [video]"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from agentic_robotics.bimanual import practice, tasks

prog, out = Path(sys.argv[1]), Path(sys.argv[2])
seeds = practice.parse_seeds(sys.argv[3]); limit = float(sys.argv[4])
video = len(sys.argv) > 5 and sys.argv[5] == "video"
T = tasks.TASKS["insert_varied"]
out.mkdir(parents=True, exist_ok=True)
book = out / "episodes.jsonl"
bridge = practice.SegmenterBridge()
def one(s):
    d = out / f"s{s}"
    if (d / "result.json").exists():
        return
    t = time.time()
    try:
        r = practice.run_sandboxed("sim_bimanual_grippers", "insert_varied", s, prog, d, sensors="d415", trace=True, bridge=bridge, video=video,
                                   time_limit_s=limit, wall_s=2400.0, scene=dict(T.scene(s), arm="left"), practice=False)
    except Exception as e:
        r = dict(ok=False, ended=f"runner error: {e!r}")
    line = dict(seed=s, ok=bool(r.get("ok")), robot_s=r.get("robot_s"), ended=str(r.get("ended"))[:300], judged_by=r.get("judged_by"),
                verdict={k: v for k, v in r.items() if k not in ("ended", "trace")}, wall_s=round(time.time() - t))
    with open(book, "a") as f:
        f.write(json.dumps(line, default=str) + "\n")
    print(f"s{s}", "ok" if line["ok"] else "FAIL", line["robot_s"], line["ended"][:90], flush=True)
try:
    with ThreadPoolExecutor(int(os.environ.get("EVAL_WORKERS", "6"))) as ex:
        list(ex.map(one, seeds))
finally:
    bridge.close()
print("done", flush=True)
