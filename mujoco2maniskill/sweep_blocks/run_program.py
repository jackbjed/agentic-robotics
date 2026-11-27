"""One episode of a skill program on RoboDojo's sweep_blocks in ManiSkill, judged by RoboDojo's success test.

    <robomme python> -I sweep_blocks/run_program.py --layout 4 --program P.py --out DIR [--video] [--no-segmenter]
    <robomme python> -I sweep_blocks/run_program.py --layout 4 --look DIR        (no program: the scene's first look, for a writer)

Writes in --out: result.json, output.txt (the program's prints; every command with the TRUE positions), shown/, video.mp4.
--look writes: look_<camera>.png (colour), look_<camera>_depth.png (depth as a picture), look_<camera>_depth.txt (depth as
numbers, with the intrinsics and the camera's pose), state.txt (both arms)."""
import argparse
import contextlib
import json
import linecache
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def depth_picture(dep):
    """(real/direct_skill.py's) near red, far blue between the 2nd and 98th percentile; the scale on top (mm); no depth = black."""
    ok = dep[dep > 0]
    lo, hi = (float(np.percentile(ok, 2)), float(np.percentile(ok, 98))) if len(ok) else (0.0, 1.0)
    hi = max(hi, lo + 1.0)
    x = np.clip((dep.astype(np.float32) - lo) / (hi - lo), 0, 1)
    out = cv2.applyColorMap((255 * (1 - x)).astype(np.uint8), cv2.COLORMAP_TURBO)
    out[dep == 0] = 0
    out[:14] = cv2.applyColorMap(np.tile(np.linspace(255, 0, out.shape[1]).astype(np.uint8), (14, 1)), cv2.COLORMAP_TURBO)
    for i in range(5):
        cv2.putText(out, f"{lo + (hi - lo) * i / 4:.0f} mm", (min(int(i / 4 * (out.shape[1] - 1)), out.shape[1] - 62), 28), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return out


def depth_numbers(f):
    """(real/direct_skill.py's) the depth as text: statistics and a grid of values; here also the camera's pose."""
    dep = np.nan_to_num(f.depth, nan=0.0)
    H, W = dep.shape
    ok = dep[dep > 0]
    us = sorted({int(round(x)) for x in np.linspace(0, W - 1, 24)}); vs = sorted({int(round(x)) for x in np.linspace(0, H - 1, 18)})
    head = (f"depth in mm along the camera's axis (0 = no depth); the image is {W} x {H}, fx {f.fx:.1f}, fy {f.fy:.1f}, cx {f.cx:.1f}, cy {f.cy:.1f}.\n"
            f"the camera's position (mm, table frame): {np.round(f.eye, 1).tolist()}; its axes in the table frame (x right, y down, z forward): "
            f"{np.round(f.R[:, 0], 3).tolist()}, {np.round(f.R[:, 1], 3).tolist()}, {np.round(f.R[:, 2], 3).tolist()}.\n"
            + (f"{100 * (1 - len(ok) / dep.size):.0f}% of the pixels without depth; of the others: nearest {int(ok.min())}, 5% {int(np.percentile(ok, 5))}, "
               f"median {int(np.median(ok))}, 95% {int(np.percentile(ok, 95))}, farthest {int(ok.max())} mm." if len(ok) else "no pixel has depth."))
    return head + "\n" + "\n".join([" v \\ u " + "".join(f"{u:5d}" for u in us)] + [f"{v:6d} " + "".join(f"{int(dep[v, u]):5d}" for u in us) for v in vs]) + "\n"


def look(layout, out):
    from sweep_blocks.env import SweepBlocks
    from sweep_blocks.program_api import X5Robot
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    env = SweepBlocks(layout=layout, cameras=())
    robot = X5Robot(env)
    fr = robot.look()
    for c, f in fr.items():
        cv2.imwrite(str(out / f"look_{c}.png"), f.rgb[..., ::-1])
        cv2.imwrite(str(out / f"look_{c}_depth.png"), depth_picture(np.nan_to_num(f.depth, nan=0.0)))
        (out / f"look_{c}_depth.txt").write_text(depth_numbers(f))
    st = {a: robot.state(a) for a in ("left", "right")}
    lines = [f"{a} arm: tool point {s['tcp_mm']}, approach_dir {s['approach_dir']}, closing_dir {s['closing_dir']}, opening {s['opening_mm']} mm" for a, s in st.items()]
    (out / "state.txt").write_text("\n".join(lines) + "\n")
    env.close()
    return lines


def episode(layout, program, out, video=False, segmenter=True):
    from m2m import api
    from m2m.boxed.host import run_program as run_boxed
    from sweep_blocks.env import SweepBlocks
    from sweep_blocks.program_api import TABLE_Z, X5Robot, program_robot
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    code = Path(program).read_text()
    t0 = time.time()
    env = SweepBlocks(layout=layout, cameras=())
    seg = None
    if segmenter:
        from m2m.seg import Segmenter
        seg = Segmenter()
        if not seg.alive():
            raise RuntimeError(f"the SAM3 text service does not answer at {seg.url}")
    frames = []

    def on_step():
        if video and env.steps % 2 == 0:
            frames.append(env.image("overview"))
    robot = X5Robot(env, segmenter=seg, on_step=on_step)
    truth = lambda: {n: [round(float(v) * 1000, 1) for v in (env.pose(n)[0] - [0, 0, TABLE_Z])] for n in env.U.objects}
    with open(out / "output.txt", "w", buffering=1) as log:
        log.write(f"# RoboDojo sweep_blocks in ManiSkill, layout {layout}\n# task: {env.instruction}\n")
        given, counts = program_robot(robot, out, trace=log, truth=truth)
        bad = api.check_program(code)
        if bad:
            how = "refused: " + "; ".join(bad)
        else:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                how = run_boxed(code, given, env.instruction, log)   # (in the sandbox: m2m/boxed)
        info = env.info()
        success = bool(env.success or info["success"])
        log.write(f"# ended: {how}\n# RoboDojo's test at the end: {json.dumps(info)}\n# TRUE positions at the end (mm): {json.dumps(truth())}\n")
    res = dict(layout=layout, program=str(program), simulator="maniskill", task="robodojo sweep_blocks", success=success, ended=how,
               robot_s=round(env.steps * 0.04, 2), actions=env.steps, commands=counts["commands"], shown=counts["shown"],
               segment_calls=(seg.calls if seg else 0), check=info, wall_s=round(time.time() - t0, 1))
    if video and frames:
        h, w = frames[0].shape[:2]
        vw = cv2.VideoWriter(str(out / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 12.5, (w, h))
        for f in frames:
            vw.write(f[..., ::-1])
        vw.release(); res["video"] = str(out / "video.mp4")
    (out / "result.json").write_text(json.dumps(res, indent=1))
    env.close()
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", type=int, required=True); ap.add_argument("--program"); ap.add_argument("--out"); ap.add_argument("--look")
    ap.add_argument("--video", action="store_true"); ap.add_argument("--no-segmenter", action="store_true")
    a = ap.parse_args()
    if a.look:
        print("\n".join(look(a.layout, a.look)))
    else:
        r = episode(a.layout, a.program, a.out, video=a.video, segmenter=not a.no_segmenter)
        print(json.dumps({k: r[k] for k in ("layout", "success", "ended", "robot_s", "commands", "wall_s", "check")}))
