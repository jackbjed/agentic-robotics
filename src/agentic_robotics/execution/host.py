"""The skill host: runs ONE skill program for one episode, inside the program sandbox (execution/sandbox.py: no
network, read-only code, one writable directory, only the world's socket). It builds the robot (the original RealSim
over the socket), the perception tools and the context, imports the program's entry module from its workspace copy,
calls run(ctx), and sends the program's last word (its result and usage) to the world server, which judges.

    python -I -m agentic_robotics.execution.host --skills <dir> --entry skills/pick.py --sock <world.sock> --out <dir>"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills", required=True, help="the skill code root (a workspace or a frozen snapshot)")
    ap.add_argument("--entry", default="skills/pick.py")
    ap.add_argument("--sock", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ.setdefault("MUJOCO_GL", "osmesa")             # (the program only draws its own robot model: no GPU)
    os.environ.setdefault("PYOPENGL_PLATFORM", os.environ["MUJOCO_GL"])
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    w0 = time.time()
    from ..sim import twin
    library = twin.skill_library()
    from ..robot.client import make_robot
    from ..perception.client import Segmenter, VLMClient, library_perception
    from .context import SkillContext
    robot, link = make_robot(a.sock)
    hello = link.call("hello")[0]
    task = link.call("task")[0]["task"]
    seg = Segmenter(link)
    per = library_perception(robot, seg, out, library)
    ctx = SkillContext(task=task, robot=robot, segmenter=seg, library=library, perception=per, out=out, link=link,
                       t0=float(hello["t0"]), limit_s=float(hello["robot_time_limit_s"]), vlm=VLMClient(link))
    sys.path.insert(0, str(Path(a.skills).resolve()))       # (the program's own helpers: `import helpers.x`)
    result, error = None, None
    w_setup = time.time() - w0
    try:
        spec = importlib.util.spec_from_file_location("skill_entry", str(Path(a.skills) / a.entry))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        result = mod.run(ctx)
        if not isinstance(result, dict):
            result = dict(result=str(result))
    except BaseException as e:                                # (a program's crash is its failure, recorded)
        error = f"{type(e).__name__}: {e}"[:500]
        (out / "program_error.txt").write_text(traceback.format_exc())
    try:
        robot.flush()
    except BaseException as e:
        error = error or f"final flush: {type(e).__name__}: {e}"[:300]
    wall = time.time() - w0
    waited = sum(link.wait.values())
    usage = dict(wall_s=round(wall, 3), setup_wall_s=round(w_setup, 3), waiting_on_world_s=round(waited, 3),
                 compute_wall_s=round(wall - waited - w_setup, 3), requests=dict(link.n), segment_calls=seg.calls,
                 model_calls=0, model_cost_usd=0.0, model_wait_s=0.0)
    link.call("done", result=result, error=error, usage=usage)
    json.dump(dict(result=result, error=error, usage=usage, events=ctx.events), open(out / "program.json", "w"), indent=1,
              default=str)


if __name__ == "__main__":
    main()
