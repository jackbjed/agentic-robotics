"""The skill host for real-robot episodes: src/agentic_robotics/execution/host.py with ONE difference, the program-side
robot is built as on the real robot (panda_twin real_box.make_rig, backend robot): RealSim with the full calib.json
(the cameras' calibrated poses AND the 7.1 mm flange adapter), rig.json's speed (0.5 = half the twin's move times,
unless --speed) and the task timing (real_box run). Everything else (the sandbox, ctx, the library or ctx.tools, the
import guard) is the platform's. Run by real/run.py inside the program sandbox (no network: the real-robot server is
reached through the episode's Unix socket only).

    python -I real/host.py --skills <dir> --entry skills/pick.py --sock <real.sock> --out <dir> [--no-library] [--speed S]"""
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
    ap.add_argument("--skills", required=True); ap.add_argument("--entry", default="skills/pick.py")
    ap.add_argument("--sock", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--no-library", action="store_true")
    ap.add_argument("--speed", type=float, default=None, help="RealSim's time scale (default: rig.json speed)")
    a = ap.parse_args()
    os.environ.setdefault("MUJOCO_GL", "osmesa")
    os.environ.setdefault("PYOPENGL_PLATFORM", os.environ["MUJOCO_GL"])
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    w0 = time.time()
    from agentic_robotics.sim import twin
    from panda_fk import load_calib
    library = None if a.no_library else twin.skill_library()
    from agentic_robotics.robot.client import Link, SocketArm, SocketCams
    from agentic_robotics.perception.client import Segmenter, VLMClient, library_perception
    from agentic_robotics.execution.context import SkillContext
    link = Link(a.sock)
    hello = link.call("hello")[0]
    if hello.get("backend") == "robot":
        calib = load_calib()                                   # (the real rig's: cameras + adapter, as real_box)
    else:                                                      # (the twin standing in: its hand has no adapter)
        calib = twin.camera_calib("calibrated")
    robot = twin.RealSim(SocketArm(link), SocketCams(link), twin.load_rig(), calib, speed=a.speed, v_max=None, log_dir=None)
    robot.task_timing = True                                   # (real_box run: real_sim FREE_PHASES timing)
    task = link.call("task")[0]["task"]
    seg = Segmenter(link)
    per = None if library is None else library_perception(robot, seg, out, library)
    tools = None
    if library is None:
        from agentic_robotics.execution.tools import Tools
        tools = Tools(twin)
    ctx = SkillContext(task=task, robot=robot, segmenter=seg, library=library, perception=per, out=out, link=link,
                       t0=float(hello["t0"]), limit_s=float(hello["robot_time_limit_s"]), vlm=VLMClient(link), tools=tools)
    if library is None:
        from agentic_robotics.execution.guard import install
        install(a.skills)
    sys.path.insert(0, str(Path(a.skills).resolve()))
    result, error = None, None
    w_setup = time.time() - w0
    try:
        spec = importlib.util.spec_from_file_location("skill_entry", str(Path(a.skills) / a.entry))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        result = mod.run(ctx)
        if not isinstance(result, dict):
            result = dict(result=str(result))
    except BaseException as e:
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
                 speed=robot.speed, calib_keys=sorted(calib))
    link.call("done", result=result, error=error, usage=usage)
    json.dump(dict(result=result, error=error, usage=usage, events=ctx.events, timeline=robot.timeline, trajectories=robot.rec),
              open(out / "program.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
