"""One episode of a skill program in the ManiSkill rig, judged by the world's own success().

    <robomme python> -I run_episode.py --scene out/scenes/w16/2001.json --program P.py --out DIR
                                       [--world W.py] [--time-limit 600] [--video] [--no-segmenter] [--depth-noise-px X]
                                       [--plain-robot]

--scene   a scene file made by tools/export_scene.py (it names its world; --world overrides)
--plain-robot   the program gets the rig's robot as it is. Default: the world's robot() wraps it, as in the project's
                practice episodes (the first session's world only records the robot's last state for its check; a
                world of a real-feedback round also changes how the robot behaves: use --plain-robot there).

Writes in --out: result.json, output.txt (the program's prints and every command with the TRUE positions), shown/,
video.mp4 (--video). Runs in the ManiSkill environment (see README.md); nothing of the project's MuJoCo code is used."""
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
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _plain(x):
    return json.loads(json.dumps(x, default=lambda v: v.tolist() if hasattr(v, "tolist") else str(v)))


def world_state(rig, robot, spec, start, ended):
    """(practice.world_state) What a world's success(state) gets: the TRUE state of the ManiSkill scene, read only."""
    def need(name):
        if name not in rig.names():
            raise KeyError(f"no body {name!r}: there are {sorted(rig.names())}")
        return name

    def pos(name):
        return [round(float(v) * 1000, 1) for v in rig.body_pose(need(name))[0]]

    def rot(name):
        return [[round(float(v), 5) for v in row] for row in rig.body_pose(need(name))[1]]

    def touching(name):
        parts, other = rig.touching(need(name))
        return dict(robot=parts, others=other)

    def held(name):
        t = touching(name)
        return bool(t["robot"]) and not t["others"]

    def truth():
        return {n: pos(n) for n in rig.names() if n.startswith("w_")}

    def no(what):
        def f(*a, **k):
            raise KeyError(f"state.{what}: not available in the ManiSkill rig")
        return f
    p, R = rig.tool_pose()
    arm = dict(tcp_mm=[round(float(v) * 1000, 1) for v in p], approach_dir=[round(float(v), 3) for v in R[:, 2]],
               opening_mm=round(max(rig.width(), 0.0) * 1000, 1))    # (for a TARGET world's evaluator: the arm at the end)
    return SimpleNamespace(robot=arm, scene=_plain(spec["scene_shown"]), start=_plain(start), ended=ended,
                           time_s=round(float(robot.time_s()), 1), truth=truth, pos=pos, rot=rot, points=lambda n: [pos(n)],
                           joint=no("joint"), speed=lambda n: rig.body_speed(need(n)) * 1000.0, touching=touching, held=held,
                           simulator=no("simulator"))


def world_check(fn, state):
    """(practice.world_check)"""
    try:
        r = fn(state)
        if isinstance(r, (bool, np.bool_)):
            r = dict(ok=bool(r))
        if not isinstance(r, dict) or not isinstance(r.get("ok"), (bool, np.bool_)):
            raise ValueError('success(state) returns True or False, or a dict with "ok"')
        r = _plain(r)
        return dict(r, ok=bool(r["ok"]))
    except (Exception, SystemExit) as e:
        traceback.print_exc(limit=-6)
        return dict(ok=False, error=f"the world's success() failed: {type(e).__name__}: {str(e)[:300]}")


def public_log(text, task=None):
    """An episode's log as a development agent may see it: what the program printed and every robot command with its
    result and the arm's MEASURED tool position. Without: the true positions of the scene's bodies, what the hidden
    world printed, the evaluator's own measurements."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("# TRUE positions at the start")), -1)
    out = ["# an episode in the target environment" + (f"\n# task: {task}" if task else "")]
    for l in lines[start + 1:]:
        if l.startswith(("# TRUE positions", "# the world's success()", "# NOT built", "# soft contacts")):
            continue
        out.append(l.split(" | NOW ")[0] if l.startswith("# t=") else l)
    return "\n".join(out)


def episode(scene, program, out, world=None, time_limit_s=600.0, video=False, segmenter=True, depth_noise_px=0.0,
            plain_robot=False, shader="default", min_depth=None, task_text=None):
    from m2m import api
    from m2m.rig import Rig
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    scene = Path(scene); spec = json.loads(scene.read_text())
    seed = int(spec["seed"])
    world = Path(world or spec["world"])
    code = Path(program).read_text()
    t0 = time.time()
    with open(out / "output.txt", "w", buffering=1) as log:
        log.write(f"# ManiSkill rig: scene {scene.name} of world {world.name}, seed {seed}\n# task: {spec['text']}\n")
        # the world's code, in one namespace as in a practice episode: its scene() is called again for the seed (its
        # check and its robot may read what scene() noted), its robot() wraps the robot, its success() judges
        wns, w_why = None, None
        wcode = world.read_text()
        bad = api.check_program(wcode, needs=api.WORLD_FUNCTIONS)
        if bad:
            w_why = "refused: " + "; ".join(bad)
        else:
            linecache.cache["world.py"] = (len(wcode), None, wcode.splitlines(True), "world.py")
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    wns = {"__name__": "world"}
                    exec(compile(wcode, "world.py", "exec"), wns)
                    if "scene" in wns:
                        wns["scene"](json.loads(json.dumps(spec["shown_in"])), np.random.default_rng([seed, 77]))
                except (Exception, SystemExit) as e:
                    traceback.print_exc(limit=-6)
                    w_why = f"{type(e).__name__}: {str(e)[:300]}"
        kw = {} if min_depth is None else dict(min_depth=min_depth)
        rig = Rig(spec, scene.parent, seed=seed, record=video, depth_noise_px=depth_noise_px, shader=shader, **kw)
        if task_text:                                               # (an experiment's own text of the task, the same everywhere)
            spec = dict(spec, text=" ".join(str(task_text).split()))
        if rig.U.soft or rig.U.rolling:
            log.write(f"# soft contacts (N/m): {rig.U.soft}; spin damping for rolling friction (1/s): {dict(list(rig.U.rolling.items())[:3])}{' ...' if len(rig.U.rolling) > 3 else ''}\n")
        if rig.U.unbuilt or spec.get("skipped"):
            log.write(f"# NOT built in ManiSkill: {rig.U.unbuilt + spec.get('skipped', [])}\n")
        seg = None
        if segmenter:
            from m2m.seg import Segmenter
            seg = Segmenter()
            if not seg.alive():
                raise RuntimeError(f"the SAM3 text service does not answer at {seg.url}")
        robot = api.Robot(rig, time_limit_s=time_limit_s, segmenter=seg)
        truth = lambda: {n: [round(float(v) * 1000, 1) for v in rig.body_pose(n)[0]] for n in rig.names() if n.startswith("w_")}
        start = truth()
        given, counts = api.program_robot(robot, out, trace=log, truth=truth)
        bad = api.check_program(code)
        w_setup = time.time() - t0
        if w_why:
            how = "world: " + w_why
        elif bad:
            how = "refused: " + "; ".join(bad)
        else:
            linecache.cache["program.py"] = (len(code), None, code.splitlines(True), "program.py")
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    if not plain_robot and wns is not None and "robot" in wns:
                        given = wns["robot"](given, np.random.default_rng([seed, 78]), api.WorldKit)
                    ns = {"__name__": "program"}
                    exec(compile(code, "program.py", "exec"), ns)
                    ns["run"](given, spec["text"])
                    how = "returned"
                except api.TimeUp as e:
                    how = f"time: {e}"
                except (Exception, SystemExit) as e:
                    traceback.print_exc(limit=-6)
                    how = f"error: {type(e).__name__}: {str(e)[:200]}"
        robot_s = round(robot.time_s(), 1)
        rig.run(1.0)                                                # (tasks.Episode.finish: the scene comes to rest)
        check = None
        if wns is not None and "success" in wns and not w_why:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                check = world_check(wns["success"], world_state(rig, robot, spec, start, how))
            log.write(f"# the world's success(): {json.dumps(check)}\n")
        log.write(f"# ended: {how}\n# TRUE positions at the end (mm): {json.dumps(truth())}\n")
        res = dict(seed=seed, scene=str(scene), world=str(world), program=str(program), simulator="maniskill",
                   success=bool(check and check.get("ok")), ended=how, robot_s=robot_s, commands=counts["commands"],
                   shown=counts["shown"], segment_calls=(seg.calls if seg else 0), safety_stops=rig.safety_stops,
                   check=check, wall_s=round(time.time() - t0, 1), setup_wall_s=round(w_setup, 1),
                   depth_noise_px=depth_noise_px, plain_robot=bool(plain_robot), time_limit_s=time_limit_s)
        if video:
            res["video"] = rig.write_video(out / "video.mp4")
        (out / "result.json").write_text(json.dumps(res, indent=1))
        rig.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True); ap.add_argument("--program", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--world"); ap.add_argument("--time-limit", type=float, default=600.0)
    ap.add_argument("--video", action="store_true"); ap.add_argument("--no-segmenter", action="store_true")
    ap.add_argument("--depth-noise-px", type=float, default=0.0); ap.add_argument("--plain-robot", action="store_true")
    ap.add_argument("--shader", default="default")
    ap.add_argument("--min-depth", type=float, help="m: nothing nearer has depth (default 0.275, the D415's; 0 = ideal depth)")
    ap.add_argument("--task-text-file", help="the task's text given to the program (default: the scene file's)")
    a = ap.parse_args()
    r = episode(a.scene, a.program, a.out, world=a.world, time_limit_s=a.time_limit, video=a.video, segmenter=not a.no_segmenter,
                depth_noise_px=a.depth_noise_px, plain_robot=a.plain_robot, shader=a.shader, min_depth=a.min_depth,
                task_text=Path(a.task_text_file).read_text() if a.task_text_file else None)
    print(json.dumps({k: r[k] for k in ("seed", "success", "ended", "robot_s", "commands", "wall_s", "check")}))


if __name__ == "__main__":
    main()
