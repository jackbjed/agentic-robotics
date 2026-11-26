"""The host of a program written for the bimanual environments' robot API (run(robot, task): docs/bimanual.md) on the
real rig: real/host.py's robot (RealSim over the episode's socket, the full calib.json, rig.json's speed, the task
timing) under the program API of the simulation (src/agentic_robotics/bimanual/real.py: millimetres and degrees, the
left arm only). The program file is the session's, unchanged. Run by real/run.py program inside the program sandbox
(no network: the real-robot server is reached through the episode's Unix socket only).

    python -I real/host_program.py --program <dir>/program.py --sock <real.sock> --out <dir>
                                   [--speed S] [--guard-speed M_PER_S] [--guard-dz M] [--free-push] [--max-stops N]
                                   [--raw-force]

The task's text is the server's (the episode's spec: real/run.py program --task).

Writes in --out: output.txt (the program's prints, every command with its result), shown/ (robot.show), program.json."""
from __future__ import annotations

import argparse
import contextlib
import json
import linecache
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:                               # (a worktree or a copy runs on the main checkout's
    sys.path.insert(0, str(ROOT / "src"))                           #  environment: this checkout's code, not that one's)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", required=True)
    ap.add_argument("--no-settle", action="store_true", help="RealSim's own settles only (default: bimanual/real.py, settle)")
    ap.add_argument("--program-limit", type=float, help="s on the program's clock (default: the server's robot-time limit)")
    ap.add_argument("--robot-clock", action="store_true", help="robot.time_s(): the rig's clock (default: bimanual/real.py, time)")
    ap.add_argument("--clutter-level", help="the task's text: the clutter task's for the twin's target (default: the server's)")
    ap.add_argument("--sock", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--speed", type=float, default=None, help="RealSim's time scale (default: rig.json speed)")
    ap.add_argument("--guard-speed", type=float, default=0.005, help="m/s of the stop_on_contact moves")
    ap.add_argument("--guard-speed-high", type=float, default=None, help="m/s of their part above 150 mm (default: --guard-speed)")
    ap.add_argument("--guard-dz", type=float, default=None, help="m the hand is held up at a contact (default rig.json's)")
    ap.add_argument("--free-push", action="store_true", help="a push down after a contact: a plain move (default: guarded, slow)")
    ap.add_argument("--confirm-contact", action="store_true", help="a guarded line's stop without force is not a contact (bimanual/real.py CONFIRM)")
    ap.add_argument("--max-stops", type=int, default=3, help="safety stops of the arm server before the episode ends")
    ap.add_argument("--raw-force", action="store_true", help="state()'s force_n as the arm reports it (default: minus its "
                    "reading at the start, the hand free and empty)")
    ap.add_argument("--world", help="a rehearsal in a session's world (real/run.py --backend sim --world): the program gets the "
                                    "robot the world's robot() returns, and the world's success() judges the simulation's end state")
    ap.add_argument("--world-seed", type=int, default=0, help="the scene's seed (the world's random numbers are its)")
    a = ap.parse_args()
    os.environ.setdefault("MUJOCO_GL", "osmesa")
    os.environ.setdefault("PYOPENGL_PLATFORM", os.environ["MUJOCO_GL"])
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    w0 = time.time()
    from agentic_robotics.sim import twin
    from panda_fk import load_calib
    from agentic_robotics.robot.client import Link, SocketCams
    from agentic_robotics.perception.client import Segmenter
    from agentic_robotics.bimanual import real
    from agentic_robotics.bimanual.api import TimeUp
    from agentic_robotics.bimanual.practice import check_program
    link = Link(a.sock)
    hello = link.call("hello")[0]
    if hello.get("backend") == "robot":
        calib = load_calib()                                   # (the real rig's: cameras + adapter, as real_box)
    elif hello.get("arm_calib"):                               # (the bimanual simulation standing in: its own mounts)
        calib = hello["arm_calib"]
    else:                                                      # (the twin standing in: its hand has no adapter)
        calib = twin.camera_calib("calibrated")
    S = twin.RealSim(real.ReplyArm(link), SocketCams(link), twin.load_rig(), calib, speed=a.speed, v_max=None, log_dir=None)
    S.task_timing = True                                       # (real_box run: real_sim FREE_PHASES timing)
    if hello.get("arm_q_home") is not None:                    # (the bimanual simulation standing in: its arm starts at ITS
        import numpy as np                                     #  home pose, 55 mm from the twin's, and robot.home() must
        S.q_home = np.asarray(hello["arm_q_home"], float)      #  return there: "back to the initial pose" is judged. On
        #                                                         the rig the arm starts at the twin's home: real_box home.)
    task = link.call("task")[0]["task"]
    if a.clutter_level:
        from agentic_robotics.bimanual import clutter
        text = clutter._text(a.clutter_level, task["object"])
    else:
        text = task["instruction"]
    seg = Segmenter(link)
    code = Path(a.program).read_text()
    bad = check_program(code)
    error = None
    w_setup = time.time() - w0
    with open(out / "output.txt", "w", buffering=1) as log:
        log.write(f"# task: {text}\n# {hello.get('backend')} {hello.get('mode')}: robot time limit {hello['robot_time_limit_s']} s, "
                  f"model calls {'on' if hello.get('ask') else 'off'}\n")
        R = real.RealRobot(S, link, seg, out, hello, ask=bool(hello.get("ask")), guard_speed=a.guard_speed, guard_dz=a.guard_dz,
                           soft_push=not a.free_push, max_stops=a.max_stops, tare_force=not a.raw_force, sim_clock=not a.robot_clock, settle_all=not a.no_settle,
                           program_limit_s=a.program_limit, trace=log, confirm_contact=a.confirm_contact,
                           guard_speed_high=a.guard_speed_high)
        # A world (a rehearsal: the simulation is the robot): as practice.episode does it, in ONE namespace. Its scene()
        # is called again here, for the seed, because its check and its robot may read what scene() noted (the server
        # built the scene from the same call in real/world_tool.py); its robot() wraps the robot the program gets (a
        # world's own record for its check); its success() judges the end state the server gives (world_state).
        wns, w_why, mine = None, None, None
        if a.world:
            import numpy as np
            from agentic_robotics.bimanual import practice, scenes
            wns, _, w_why = practice._world(a.world, scenes.empty(a.world_seed), a.world_seed, log)
        if w_why:
            how = "world: " + w_why
        elif bad:
            how = "refused: " + "; ".join(bad)
        else:
            linecache.cache["program.py"] = (len(code), None, code.splitlines(True), "program.py")
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    given = real.program_robot(R)
                    if wns is not None and "robot" in wns:
                        try:
                            given = wns["robot"](given, np.random.default_rng([int(a.world_seed), 78]), practice.WorldKit)
                            if given is None or not all(hasattr(given, n) for n in ("look", "state", "move", "time_s")):
                                raise ValueError("a world's robot(robot, rng, kit) returns the robot the program gets (a kit.Robot)")
                        except (Exception, SystemExit) as e:
                            traceback.print_exc(limit=-6)
                            raise real.EpisodeEnd(f"world: its robot() failed ({type(e).__name__}: {str(e)[:200]})")
                    ns = {"__name__": "program"}
                    exec(compile(code, "program.py", "exec"), ns)
                    ns["run"](given, text)
                    how = "returned"
                except TimeUp as e:
                    how = f"time: {e}"
                except real.EpisodeEnd as e:
                    how = f"ended: {str(e)[:300]}"
                except (Exception, SystemExit) as e:            # the program's own failure
                    traceback.print_exc(limit=-6)
                    how = f"error: {type(e).__name__}: {str(e)[:200]}"
                    error = how[7:]
                    (out / "program_error.txt").write_text(traceback.format_exc())
        if wns is not None and "success" in wns:                    # the world's own check, on the scene at rest
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from world_tool import State
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                if w_why:
                    mine = dict(ok=False, error="the world failed: " + w_why)
                else:
                    st = link.call("world_state", ended=how)[0]
                    mine = practice.world_check(wns["success"], State(st["state"])) if st.get("state") else \
                        dict(ok=False, error="no end state from the server: " + str(st.get("why")))
            log.write(f"# the world's success(): {json.dumps(mine)}\n")
        log.write(f"# ended: {how}\n")
    try:
        S.flush()
    except BaseException as e:
        error = error or f"final flush: {type(e).__name__}: {e}"[:300]
    c = R.counts()
    wall = time.time() - w0
    waited = sum(link.wait.values())
    result = dict(ended=how, commands=c["commands"], shown=c["shown"], model_calls=c["asks"], model_usd=round(c["ask_usd"], 4),
                  model_wait_s=round(c["ask_wait_s"], 1), safety_stops=c["stops"], refusals=c["refusals"],
                  right_arm_commands=c["right_arm"], unconfirmed_stops=c["unconfirmed"])
    if mine is not None:
        result["world_check"] = mine
    usage = dict(wall_s=round(wall, 3), setup_wall_s=round(w_setup, 3), waiting_on_world_s=round(waited, 3),
                 compute_wall_s=round(wall - waited - w_setup, 3), requests=dict(link.n), segment_calls=seg.calls,
                 speed=S.speed, guard_speed=a.guard_speed, guard=R.guard, push=None if a.free_push else real.PUSH,
                 confirm_contact=real.CONFIRM if a.confirm_contact else None,
                 force_tare_n=None if R.tare is None else [round(float(v), 2) for v in R.tare], calib_keys=sorted(calib),
                 program_clock=dict(kind="robot" if a.robot_clock else "sim", limit_s=R.limit, behind_robot_s=round(R.saved, 1)))
    link.call("done", result=result, error=error, usage=usage)
    json.dump(dict(result=result, error=error, usage=usage, timeline=S.timeline, trajectories=S.rec),
              open(out / "program.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
