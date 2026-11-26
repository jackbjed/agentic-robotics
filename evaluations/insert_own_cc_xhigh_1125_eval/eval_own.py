"""Saved versions of a from-nothing session in one of its own worlds on fresh scenes (seeds the session never used),
run the session's own way (practice mode, the session's episode time). Resumable: an episode with a result.json is
not run again. No model calls.
  eval_own.py SESSION OUT WORLD_K SEEDS(a-b) VERSIONS(comma) TIME_LIMIT_S [video]"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from agentic_robotics.bimanual import practice, scenes

R, out, wk = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
seeds = practice.parse_seeds(sys.argv[4])
versions = [int(x) for x in sys.argv[5].split(",")]
limit = float(sys.argv[6])
video = len(sys.argv) > 7 and sys.argv[7] == "video"
world = R / "worlds" / f"w{wk:02d}.py"
out.mkdir(parents=True, exist_ok=True)
book = out / "episodes.jsonl"
bridge = practice.SegmenterBridge()
def one(vs):
    v, s = vs
    d = out / f"v{v:02d}" / f"s{s}@w{wk}"
    if (d / "result.json").exists():
        return
    t = time.time()
    try:
        r = practice.run_sandboxed("sim_bimanual_grippers", "insert_varied", s, R / "versions" / f"v{v:02d}.py", d, sensors="d415", trace=True,
                                   bridge=bridge, video=video, time_limit_s=limit, wall_s=max(1800.0, 4 * limit), scene=scenes.empty(s),
                                   practice=True, world=world)
    except Exception as e:
        r = dict(ok=False, ended=f"runner error: {e!r}")
    line = dict(v=v, seed=s, ok=bool(r.get("ok")), robot_s=r.get("robot_s"), ended=str(r.get("ended"))[:200], judged_by=r.get("judged_by"),
                check=r.get("world_check"), wall_s=round(time.time() - t))
    with open(book, "a") as f:
        f.write(json.dumps(line, default=str) + "\n")
    print(f"v{v} s{s}", "ok" if line["ok"] else "FAIL", line["robot_s"], line["ended"][:70], flush=True)
try:
    with ThreadPoolExecutor(int(os.environ.get("EVAL_WORKERS", "6"))) as ex:
        list(ex.map(one, [(v, s) for v in versions for s in seeds]))
finally:
    bridge.close()
print("done", flush=True)
