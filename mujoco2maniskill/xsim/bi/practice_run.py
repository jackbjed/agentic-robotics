"""One practice episode of a two-arm task, in MuJoCo (xsim/bi/mj.py): a practice world's scene of a seed, a program,
the world's own self-check at the end. Project .venv.

    ../.venv/bin/python -I xsim/bi/practice_run.py --world W.py --seed 3 --program P.py --out DIR --task-text-file T.txt
        [--time-limit 40] [--no-segmenter]

A world file defines scene(scene, rng) -> scene (scene["mjcf"]: the things on the table as MuJoCo XML, table frame,
metres; scene["table"]: {"color": [r, g, b], "friction": f}) and, optionally, success(state) -> {"ok": bool, ...}.
In DIR: result.json, output.txt (the program's prints, every robot command with its result, and the TRUE positions of
the world's bodies: it is the author's own world), shown/ (the images the program showed), videos/ and frames/ (an
overview camera in colour; the robot's head camera in colour and depth)."""
import argparse
import contextlib
import json
import linecache
import sys
import time
import traceback
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from xsim.bi import mj  # noqa: E402
from m2m import api  # noqa: E402
from m2m.boxed import host as boxed  # noqa: E402
from sweep_blocks import program_api as PA  # noqa: E402

WORLD_NEEDS = ("scene",)


def check_world(code):
    """-> the reasons a world file is not taken ([]: none). The file's rules are a program's (imports, no files)."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"line {e.lineno}: syntax error: {e.msg}"]
    defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    bad = []
    if "scene" not in defs:
        bad.append("it does not define scene(scene, rng)")
    for n in ("robot", "goal"):
        if n in defs:
            bad.append(f"it defines {n}(...): the robot, its sensing and the task's text are fixed in these experiments")
    bad += [b for b in api.check_program(code, needs=("scene",)) if "defines none of" not in b]
    return bad[:12]


def load_world(path, seed):
    """-> (the world: the file loaded in its sandbox, m2m/boxed; its scene dict for the seed). Raises mj.WorldError with
    what is wrong. The caller closes the world."""
    code = Path(path).read_text()
    bad = check_world(code)
    if bad:
        raise mj.WorldError("the world file is not taken: " + "; ".join(bad))
    world = None
    try:
        world = boxed.World(code)
        sc = world.scene(dict(mjcf="", table=dict(mj.TABLE_DEFAULT, size_mm=[1400, 1100])), seed)
    except boxed.WorldFailure as e:
        if world is not None:
            world.close()
        raise mj.WorldError(f"the world's scene() failed: {e.kind}: {e.msg[:300]}\n" + e.tb[-700:])
    if not isinstance(sc, dict) or not isinstance(sc.get("mjcf"), str) or not sc["mjcf"].strip():
        world.close()
        raise mj.WorldError('scene() must return the scene dict with scene["mjcf"] filled (a MuJoCo XML string)')
    return world, sc


def episode(world, seed, program, out, task_text, time_limit=PA.TIME_LIMIT_S, segmenter=True, validate=False):
    """validate: the harness's interface check of a world: the world's self-check does not end the episode early (it is
    evaluated once, at the end, on the scene as the check left it)."""
    import cv2
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    res = dict(seed=int(seed), simulator="mujoco (practice)", ok=False, world_check=None, robot_s=0.0, commands=0)
    try:
        wd = None
        wd, sc = load_world(world, seed)
        task = mj.MJTask(sc["mjcf"], sc.get("table") if isinstance(sc.get("table"), dict) else None)
    except mj.WorldError as e:
        if wd is not None:
            wd.close()
        res.update(ended=f"the world does not build: {str(e)[:900]}", wall_s=round(time.time() - t0, 1))
        (out / "output.txt").write_text("# " + res["ended"] + "\n"); (out / "result.json").write_text(json.dumps(res, indent=1))
        return res
    seg = None
    if segmenter:
        from m2m.seg import Segmenter
        seg = Segmenter()
        if not seg.alive():
            seg = None
    from xsim.bi import media
    rec = media.Recorder()

    def shoot():
        t = task.steps * PA.ACTION_S
        f = task.frame("head", t=t)
        rec.add_head(t, f.rgb, f.depth)

    state, succ, won = mj.State(task), wd if wd.has_success else None, {}

    def on_step():
        if task.steps % media.OVER_EVERY == 0:
            rec.add_overview(task.steps * PA.ACTION_S, task.image("overview"))
        if task.steps % media.HEAD_EVERY == 0:
            shoot()
        if succ is not None and not task.done and not validate:     # (as in the target: an episode ends, a success, as soon as the check holds)
            try:
                yes, c = succ.success(state)                       # (asked of the world's sandboxed process)
                if yes:
                    won["check"] = c if isinstance(c, dict) else dict(ok=True)
                    task.success = task.done = True
            except boxed.WorldFailure:
                pass
    robot = mj.X5RobotMJ(task, segmenter=seg, on_step=on_step)
    robot.env_limit = staticmethod(lambda: int(round(float(time_limit) / PA.ACTION_S)))
    truth = lambda: {n: [round(float(v) * 1000, 1) for v in (task.pose(n)[0] - [0, 0, mj.TABLE_Z])] for n in task.bodies}
    code = Path(program).read_text()
    rec.add_overview(0.0, task.image("overview")); shoot()
    with open(out / "output.txt", "w", buffering=1) as log:
        log.write(f"# practice world {Path(world).name}, scene {seed} (MuJoCo)\n# while the scene settled, its bodies moved: {json.dumps(task.settle)}\n")
        given, counts = PA.program_robot(robot, out, trace=log, truth=truth)
        wd.sink = log.write                                         # (what the world's success() prints: into the episode's log, as before)
        bad = api.check_program(code)
        if bad:
            how = "refused: " + "; ".join(bad)
        else:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                how = boxed.run_program(code, given, task_text, log)   # (in the sandbox: m2m/boxed)
        chk = None
        if won:
            chk = json.loads(json.dumps(dict(won["check"], held_at_s=round(task.steps * PA.ACTION_S, 2)), default=str))
        elif wd.has_success:
            try:
                yes, chk = wd.success(mj.State(task))
                if not isinstance(chk, dict):
                    chk = dict(ok=bool(yes))
            except boxed.WorldFailure as e:
                chk = dict(ok=False, error=f"the world's success() failed: {e.kind}: {e.msg[:200]}")
        else:
            chk = dict(ok=False, note="this world has no self-check")
        log.write(f"# ended: {how}\n# the world's own success(): {json.dumps(chk)}\n# TRUE positions at the end (mm): {json.dumps(truth())}\n")
        wd.sink = None
    wd.close()
    rec.add_overview(task.steps * PA.ACTION_S, task.image("overview")); shoot()
    rec.save(out)
    res.update(ok=bool(chk.get("ok")), world_check=chk, ended=how, robot_s=round(task.steps * PA.ACTION_S, 2), commands=counts["commands"], shown=counts["shown"],
               segment_calls=(seg.calls if seg else 0), settle=task.settle, wall_s=round(time.time() - t0, 1))
    (out / "result.json").write_text(json.dumps(res, indent=1))
    task.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True); ap.add_argument("--seed", type=int, required=True); ap.add_argument("--program", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--task-text-file"); ap.add_argument("--time-limit", type=float, default=PA.TIME_LIMIT_S)
    ap.add_argument("--no-segmenter", action="store_true"); ap.add_argument("--check-world", action="store_true"); ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    if a.check_world:
        print("XSIM_JSON " + json.dumps(check_world(Path(a.world).read_text())))
        return
    text = Path(a.task_text_file).read_text() if a.task_text_file else ""
    r = episode(a.world, a.seed, a.program, a.out, text, a.time_limit, segmenter=not a.no_segmenter, validate=a.validate)
    print("XSIM_JSON " + json.dumps({k: r.get(k) for k in ("ok", "ended", "robot_s", "world_check", "commands", "wall_s")}))


if __name__ == "__main__":
    main()
