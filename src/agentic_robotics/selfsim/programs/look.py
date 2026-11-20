"""real2sim's built-in look (ar-practice real look): both cameras at the arm's home pose, then the wrist camera from
each requested hand pose (the hand straight down, its TCP at x, y, z, the fingers along yaw). Its arguments: look_args.json beside this file (written by the practice command)."""
import json
from pathlib import Path

import numpy as np


def run(ctx):
    R, T = ctx.robot, ctx.tools
    args = json.loads((Path(__file__).parent / "look_args.json").read_text())
    for cam in ("d415", "L_wrist"):
        R.observe_frame(cam)
    done = []
    for k, (x, y, z, yaw) in enumerate(args.get("wrist") or []):
        try:
            R.move(np.array([x, y, z], float), T.down(yaw), T.OPEN_W, 2.0, "look")
            R.flush()
            R.observe_frame("L_wrist")
            done.append(k)
        except (T.Unreachable, T.RobotStop) as e:
            ctx.log("look pose skipped", pose=[x, y, z, yaw], why=str(e)[:200])
            try:
                R.resync()
            except Exception:
                pass
    return dict(result="ok", wrist_poses_done=done)
