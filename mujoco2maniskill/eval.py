"""A skill program on many exported scenes in the ManiSkill rig, episodes in parallel (one process each).

    <robomme python> -I eval.py --scenes out/scenes/w16 --program P.py --out out/eval/v38_w16 [--seeds 2001-2016]
                                [--jobs 6] [--video] [--time-limit 600] [--plain-robot] [--depth-noise-px X] [--gpu 0]

Writes --out/<seed>/ (run_episode.py's files), --out/episodes.jsonl and --out/summary.json."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent


def seeds_of(s):
    out = []
    for part in str(s).split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", required=True); ap.add_argument("--program", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--seeds"); ap.add_argument("--jobs", type=int, default=6); ap.add_argument("--video", action="store_true")
    ap.add_argument("--time-limit", type=float, default=600.0); ap.add_argument("--plain-robot", action="store_true")
    ap.add_argument("--depth-noise-px", type=float, default=0.0); ap.add_argument("--gpu", default="0")
    ap.add_argument("--wall-limit", type=float, default=1500.0); ap.add_argument("--label", default="")
    a = ap.parse_args()
    scenes = Path(a.scenes); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in scenes.glob("*.json") if f.stem.isdigit())
    if a.seeds:
        want = set(seeds_of(a.seeds)); files = [f for f in files if int(f.stem) in want]
    env = dict(os.environ, PYTHONNOUSERSITE="1", CUDA_VISIBLE_DEVICES=str(a.gpu))

    def one(f):
        d = out / f.stem
        cmd = [sys.executable, "-I", str(HERE / "run_episode.py"), "--scene", str(f), "--program", a.program, "--out", str(d),
               "--time-limit", str(a.time_limit), "--depth-noise-px", str(a.depth_noise_px)]
        cmd += ["--video"] if a.video else []
        cmd += ["--plain-robot"] if a.plain_robot else []
        t = time.time()
        try:
            p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=a.wall_limit)
            err = None if p.returncode == 0 else (p.stderr or "")[-600:]
        except subprocess.TimeoutExpired:
            err = f"the episode's process was stopped after {a.wall_limit:.0f} s"
        r = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() and not err else \
            dict(seed=int(f.stem), success=False, ended="crash: " + str(err)[-300:], robot_s=None, check=None)
        r["wall_s"] = round(time.time() - t, 1)
        print(f"{r['seed']} {'ok  ' if r['success'] else 'FAIL'} {r.get('robot_s')} s  {str(r['ended'])[:70]}  "
              f"{json.dumps({k: v for k, v in (r.get('check') or {}).items() if k in ('on_stick', 'axis_off_mm', 'bottom_above_base_mm', 'stand_tilt_deg', 'hands_off', 'home_ok', 'error')})}", flush=True)
        return r
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        res = list(ex.map(one, files))
    res.sort(key=lambda r: r["seed"])
    with open(out / "episodes.jsonl", "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in res)
    ok = [r for r in res if r["success"]]
    summ = dict(label=a.label, program=a.program, scenes=str(scenes), simulator="maniskill", episodes=len(res), successes=len(ok),
                failed_seeds=[r["seed"] for r in res if not r["success"]],
                robot_s_of_successes=round(sum(r["robot_s"] for r in ok) / len(ok), 1) if ok else None,
                plain_robot=a.plain_robot, depth_noise_px=a.depth_noise_px, time_limit_s=a.time_limit, wall_s=round(time.time() - t0, 1))
    (out / "summary.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ))


if __name__ == "__main__":
    main()
