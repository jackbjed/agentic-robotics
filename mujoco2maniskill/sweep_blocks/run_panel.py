"""One program on the FIXED panel of sweep_blocks episodes (assets/robodojo_sweep/panel20: 20 initial conditions, each
shown solvable by the scripted reference; see make_panel.py and its verification.json). A round = this, once: the same
20 episodes every time, each from a fresh start.

    <robomme python> -I sweep_blocks/run_panel.py --program P.py --out DIR [--panel DIR] [--jobs 8] [--video]
    <robomme python> -I sweep_blocks/run_panel.py --verify [--panel DIR]      re-run the reference on every episode of the panel

In DIR: ep_NN/ (result.json, output.txt, video.mp4) and summary.json (successes of 20, per episode)."""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
PANEL = HERE / "assets" / "robodojo_sweep" / "panel20"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program"); ap.add_argument("--out"); ap.add_argument("--panel", default=str(PANEL)); ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--video", action="store_true"); ap.add_argument("--no-segmenter", action="store_true")
    ap.add_argument("--verify", action="store_true"); ap.add_argument("--one", help="(internal) one episode's layout file")
    a = ap.parse_args()
    if a.one:
        from sweep_blocks import looks
        looks.install()                                             # (the panel's scenes are shown with their looks)
        from sweep_blocks.run_program import episode
        r = episode(a.one, a.program, a.out, video=a.video, segmenter=not a.no_segmenter)
        print("PANEL " + json.dumps(dict(success=r["success"], ended=r["ended"], robot_s=r["robot_s"])))
        return
    eps = sorted(Path(a.panel).glob("ep_*.json"))
    if a.verify:
        def ref(f):
            r = subprocess.run([sys.executable, "-I", str(HERE / "sweep_blocks" / "reference_sweep.py"), str(f), "--quiet"], capture_output=True, text=True)
            l = next((x for x in r.stdout.splitlines() if x.startswith("REFERENCE ")), None)
            return dict(json.loads(l[10:]) if l else dict(solved=False, error=r.stderr[-200:]), episode=f.stem)
        with ThreadPoolExecutor(a.jobs) as ex:
            res = list(ex.map(ref, eps))
        for r in res:
            print(r["episode"], "SOLVED" if r["solved"] else "NOT SOLVED", r.get("robot_s"), "s", r.get("notes") or "")
        (Path(a.panel) / "reverification.json").write_text(json.dumps(res, indent=1))
        print("the reference solves", sum(r["solved"] for r in res), "of", len(res))
        return
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    def one(f):
        d = out / f.stem
        cmd = [sys.executable, "-I", __file__, "--one", str(f), "--program", a.program, "--out", str(d)] + (["--video"] if a.video else []) + \
              (["--no-segmenter"] if a.no_segmenter else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else dict(success=False, ended="the episode's process failed: " + r.stderr[-300:])
        return dict(episode=f.stem, success=bool(res["success"]), ended=res["ended"], robot_s=res.get("robot_s"), check=res.get("check"))
    with ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(one, eps))
    s = dict(program=str(a.program), panel=str(a.panel), episodes=len(res), successes=sum(r["success"] for r in res), results=res)
    (out / "summary.json").write_text(json.dumps(s, indent=1))
    print(f"{s['successes']} of {s['episodes']} episodes succeeded")
    for r in res:
        print(" ", r["episode"], "success" if r["success"] else "FAILED ", r["robot_s"], "s |", str(r["ended"])[:70])


if __name__ == "__main__":
    main()
