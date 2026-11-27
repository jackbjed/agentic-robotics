"""The TARGET side of the two-arm task (RoboDojo's sweep_blocks in ManiSkill: sweep_blocks/env.py), as the experiments'
evaluator runs it. The robomme environment's Python. Nothing here is reachable by an agent.

    <robomme python> -I xsim/bi/target_run.py episode --layout SCENE.json --program P.py --out PRIVATE_DIR --task-text-file T [--public PUBLIC_DIR]
    <robomme python> -I xsim/bi/target_run.py look --layout SCENE.json --out DIR

An episode's PRIVATE record: result.json (with the judge's own measurements), output.txt (with TRUE positions), videos/,
frames/, shown/. Its PUBLIC payload (what a development agent may see): episode.json (success by the target's judge, how the
program ended, the times and counts), output.txt (the program's prints and every robot command with its result and the
arm's MEASURED tool position: no true positions, nothing of the judge), videos/ (an overview camera in colour, the
robot's head camera in colour and depth), frames/ (eight frames of each video), shown/."""
import argparse
import contextlib
import json
import linecache
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
FRAMES = 6


def public_log(text):
    """An episode's log without what only the evaluator knows."""
    out = []
    for l in text.splitlines():
        if l.startswith("# TRUE") or l.startswith("# RoboDojo") or l.startswith("# the judge"):
            continue
        out.append(re.sub(r" \| NOW .*$", "", l))
    return "\n".join(out) + "\n"


def episode(layout, program, out, task_text, public=None, segmenter=True, run_id=None):
    from m2m import api
    from m2m.boxed.host import run_program as RUN_PROGRAM
    from sweep_blocks.env import SweepBlocks
    from sweep_blocks.program_api import TABLE_Z, X5Robot, program_robot
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    code = Path(program).read_text()
    t0 = time.time()
    env = SweepBlocks(layout=str(layout), cameras=())
    seg = None
    if segmenter:
        from m2m.seg import Segmenter
        seg = Segmenter()
        if not seg.alive():
            raise RuntimeError(f"the SAM3 text service does not answer at {seg.url}")
    from xsim.bi import media
    rec = media.Recorder()

    def head():
        U = env.U
        U.scene.update_render(update_sensors=True, update_human_render_cameras=False)
        s = U._sensors["head"]; s.capture()
        col = s.camera.get_picture("Color"); pos = s.camera.get_picture("Position")
        col = (col[0] if isinstance(col, (list, tuple)) else col).cpu().numpy(); pos = (pos[0] if isinstance(pos, (list, tuple)) else pos).cpu().numpy()
        col = col[0] if col.ndim == 4 else col; pos = pos[0] if pos.ndim == 4 else pos
        z = (-pos[..., 2]).astype(np.float64) * 1000.0
        return np.ascontiguousarray((np.clip(col[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)), np.where((z > 5) & (z < 9000), z, np.nan)

    def on_step():
        t = env.steps * 0.04
        if env.steps % media.OVER_EVERY == 0:
            rec.add_overview(t, env.image("overview"))
        if env.steps % media.HEAD_EVERY == 0:
            rec.add_head(t, *head())
    robot = X5Robot(env, segmenter=seg, on_step=on_step)
    truth = lambda: {n: [round(float(v) * 1000, 1) for v in (env.pose(n)[0] - [0, 0, TABLE_Z])] for n in env.U.objects}
    rec.add_overview(0.0, env.image("overview")); rec.add_head(0.0, *head())
    with open(out / "output.txt", "w", buffering=1) as log:
        log.write("# the target environment\n")
        given, counts = program_robot(robot, out, trace=log, truth=truth)
        bad = api.check_program(code)
        if bad:
            how = "refused: " + "; ".join(bad)
        else:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                how = RUN_PROGRAM(code, given, task_text, log)   # (in the sandbox: m2m/boxed)
        info = env.info()
        success = bool(env.success or info["success"])
        log.write(f"# ended: {how}\n# the judge's test at the end: {json.dumps(info)}\n# TRUE positions at the end (mm): {json.dumps(truth())}\n")
    rec.add_overview(env.steps * 0.04, env.image("overview")); rec.add_head(env.steps * 0.04, *head())
    res = dict(layout=str(layout), simulator="maniskill (target)", task="robodojo sweep_blocks", success=success, ended=how,
               robot_s=round(env.steps * 0.04, 2), actions=env.steps, commands=counts["commands"], shown=counts["shown"],
               segment_calls=(seg.calls if seg else 0), safety_stops=0, check=info, wall_s=round(time.time() - t0, 1))
    from xsim import provenance
    res["provenance"] = provenance.stamp(run_id, program, layout, started=round(t0, 1))
    (out / "result.json").write_text(json.dumps(res, indent=1))
    env.close()
    if public:
        pub = Path(public); pub.mkdir(parents=True, exist_ok=True)
        ep = dict(success=success, ended=how, robot_s=res["robot_s"], wall_s=res["wall_s"], commands=res["commands"], segmentation_calls=res["segment_calls"])
        (pub / "episode.json").write_text(json.dumps(ep, indent=1))
        (pub / "output.txt").write_text(public_log((out / "output.txt").read_text(errors="replace")))
        if (out / "shown").is_dir():
            shutil.copytree(out / "shown", pub / "shown", dirs_exist_ok=True)
    rec.save(*([out] + ([Path(public)] if public else [])))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["episode", "look"]); ap.add_argument("--layout", required=True); ap.add_argument("--program"); ap.add_argument("--out", required=True)
    ap.add_argument("--task-text-file"); ap.add_argument("--public"); ap.add_argument("--no-segmenter", action="store_true")
    ap.add_argument("--run-id", help="the id of this execution, given by the batch runner: written into result.json (xsim/provenance.py)")
    a = ap.parse_args()
    if a.cmd == "look":
        from sweep_blocks.run_program import look
        look(a.layout, a.out)
        print("XSIM_JSON " + json.dumps(sorted(p.name for p in Path(a.out).iterdir())))
    else:
        r = episode(a.layout, a.program, a.out, Path(a.task_text_file).read_text() if a.task_text_file else "", a.public, segmenter=not a.no_segmenter, run_id=a.run_id)
        print("XSIM_JSON " + json.dumps({k: r[k] for k in ("success", "ended", "robot_s", "commands", "wall_s")}))


if __name__ == "__main__":
    main()
