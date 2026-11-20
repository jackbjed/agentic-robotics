"""ar: the command line (bin/ar wraps it with the project's .venv).

  ar check                                   assets (sha256), the program sandbox, the Claude CLI
  ar run   [--skills DIR] --split S --level L --idx I [--seed 5] [--parity] [--no-sandbox] [--out DIR]
                                             one episode: video, observations, actions, events, result.json
  ar suite --suite NAME [--experiment configs/experiments/m1_clutter_pick.yaml] [--skills DIR] [--workers N] [--out DIR]
                                             a frozen skill version on a suite of the experiment
  ar compare DIR_A DIR_B                     paired comparison of two suite runs (same episodes)
  ar layouts --object-set substitute --split train --level L --n N   layout banks (condition C)
  ar services start|stop|status [NAME ...]   our own SAM3 and splat-compositor servers (configs/services.yaml)
  ar evalset build|show --task T --split evalset|evalhidden ...   evaluation sets of solvable episodes (evaluation/evalsets.py)
  ar learn init|run|status|evaluate|progress|add-real|extend ...   the skill-improvement loop (learning/loop.py)"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import paths


from .config import episode_base, task_config  # noqa: E402


def cmd_check(a):
    import subprocess
    r = subprocess.run([sys.executable, "-I", str(paths.ROOT / "scripts/link_assets.py"), "--check"], capture_output=True, text=True)
    print(r.stdout.strip()); ok = r.returncode == 0
    from .execution import sandbox
    s = sandbox.selftest(); print("program sandbox:", s); ok &= s["ok"]
    from .learning import agent
    print("claude cli:", agent.cli_info())
    sys.exit(0 if ok else 1)


def cmd_run(a):
    from .evaluation.suites import episode_seed
    from .execution.episode import run_episode
    t = task_config(a.task)
    skills = Path(a.skills or paths.INITIAL_SKILLS / "clutter_pick")
    out = Path(a.out or paths.RUNS / "manual" / f"{time.strftime('%Y%m%d_%H%M%S')}_{a.split}_L{a.level}_{a.idx}{'_parity' if a.parity else ''}")
    V = None
    if a.vlm:                                                       # (an experiment's model block; tag manual: its own cap)
        V = dict(paths.load_yaml(a.vlm)["vlm"], tag="manual", run_usd=a.vlm_usd)
    ep = dict(episode_base(t, record=dict(video=True, observations=a.observations, video_view=a.video_view,
                                          video_colour=a.video_colour), parity=a.parity, vlm=V),
              split=a.split, level=a.level, idx=a.idx, seed=episode_seed(a.seed, a.level, a.idx))
    if a.family != "clutter-pick":
        from . import tasks as TK
        ep["family"] = a.family
        a.entry = TK.get(a.family)["entry"] if a.entry == "skills/pick.py" else a.entry
        if not a.skills:
            skills = paths.INITIAL_SKILLS / "tasks"
    r = run_episode(skills, ep, out, entry=a.entry, sandbox=not a.no_sandbox, wall_limit_s=t["wall_limit_s"])
    keys = ("category", "success", "t_success", "robot_time_s", "program_result", "program_error")
    print(json.dumps({k: r.get(k) for k in keys}, default=str))
    pr = r.get("program") or {}
    print(f"wall {r['wall'].get('episode_total')} s (program compute {pr.get('compute_wall_s')} s); segment calls "
          f"{pr.get('segment_calls')}; model calls {pr.get('model_calls', 0)} (${pr.get('model_cost_usd', 0)})")
    print("record:", out, "| video:", out / "video.mp4")


def cmd_suite(a):
    from .evaluation.runner import run_suite
    X = paths.load_yaml(a.experiment)
    spec = dict(X["suites"][a.suite])
    if spec.get("protected") and not a.allow_protected:
        sys.exit(f"suite {a.suite} is protected (held-out): pass --allow-protected (never from a learning session)")
    t = task_config(X.get("task", "configs/tasks/clutter_pick.yaml"))
    skills = Path(a.skills or paths.ROOT / X["skill"])
    out = Path(a.out or paths.RUNS / "suites" / f"{a.suite}_{time.strftime('%Y%m%d_%H%M%S')}")
    if a.tasks:
        from .learning.loop import filter_suite
        from . import tasks as TK
        spec = filter_suite(spec, TK.parse(a.tasks))
    V = None
    if a.vlm_usd > 0 and (X.get("vlm") or {}).get("enabled"):     # (the one model call per episode, capped for this suite)
        V = dict(X["vlm"], tag=f"suite:{out.name}", run_usd=a.vlm_usd)
    print(f"{a.suite}: {spec} -> {out}")
    lib = (X.get("learning") or {}).get("library", True) if a.library == "experiment" else a.library == "on"
    S = run_suite(skills, spec, t, out, workers=a.workers, sandbox=not a.no_sandbox, entry=X.get("entry", "skills/pick.py"),
                  vlm=V, library=lib)
    print((out / "summary.md").read_text())


def cmd_services(a):
    from . import services
    names = a.names or list(services.config())
    for n in names:
        if a.action == "start":
            print(n, services.start(n))
        elif a.action == "stop":
            print(n, services.stop(n))
    if a.action == "status":
        print(json.dumps(services.status(), indent=1))


def cmd_layouts(a):
    if a.task and a.task != "clutter-pick":
        from .sim.task_layouts import generate as gen_task
        r = gen_task(a.task, a.split, a.n, workers=a.workers)
    else:
        from .sim.layouts import generate
        r = generate(a.object_set, a.split, a.level, a.n, workers=a.workers)
    print(json.dumps(r))


def cmd_compare(a):
    from .evaluation.compare import compare, compare_md
    c = compare(a.a, a.b)
    print(compare_md(c, a.names[0], a.names[1]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("run"); s.set_defaults(fn=cmd_run)
    s.add_argument("--skills"); s.add_argument("--entry", default="skills/pick.py")
    s.add_argument("--vlm", metavar="EXPERIMENT", help="allow the one model call, with this experiment's vlm block")
    s.add_argument("--vlm-usd", type=float, default=0.5, help="the cap for --vlm's manual runs (all of them, ledger tag 'manual')")
    s.add_argument("--family", default="clutter-pick", help="the task: clutter-pick | pick | place | close-box | press | insert")
    s.add_argument("--task", default="configs/tasks/clutter_pick.yaml")
    s.add_argument("--split", default="test"); s.add_argument("--level", type=int, default=0); s.add_argument("--idx", type=int, default=10)
    s.add_argument("--seed", type=int, default=5, help="suite seed (5 = the test seed of panda_pick_rgbd's results)")
    s.add_argument("--parity", action="store_true", help="give the program the 11-18 baseline's truth hint (parity checks only)")
    s.add_argument("--observations", default="all", choices=("all", "none"))
    s.add_argument("--no-sandbox", action="store_true"); s.add_argument("--out")
    s.add_argument("--video-view", default="overview", choices=("overview", "cameras"),
                   help="cameras: the video shows the scene D415 | the wrist D415 (default: an overview | the wrist)")
    s.add_argument("--video-colour", default="mujoco", choices=("mujoco", "condition"),
                   help="condition: video frames in the condition's colour (A: the splat composite, slow)")
    s = sub.add_parser("suite"); s.set_defaults(fn=cmd_suite)
    s.add_argument("--suite", required=True); s.add_argument("--experiment", default="configs/experiments/m1_clutter_pick.yaml")
    s.add_argument("--skills"); s.add_argument("--workers", type=int, default=8); s.add_argument("--out")
    s.add_argument("--no-sandbox", action="store_true"); s.add_argument("--allow-protected", action="store_true")
    s.add_argument("--vlm-usd", type=float, default=0.0, help="allow the experiment's one model call per episode, this suite capped at $X")
    s.add_argument("--tasks", help="multi-task suites: only these tasks")
    s.add_argument("--library", default="experiment", choices=("experiment", "on", "off"),
                   help="the robot skill library for the program (off: skills from scratch); default: the experiment's")
    s = sub.add_parser("services"); s.set_defaults(fn=cmd_services)
    s.add_argument("action", choices=("start", "stop", "status")); s.add_argument("names", nargs="*")
    s = sub.add_parser("layouts"); s.set_defaults(fn=cmd_layouts)
    s.add_argument("--object-set", default="substitute"); s.add_argument("--split", required=True)
    s.add_argument("--level", type=int, default=0); s.add_argument("--n", type=int)
    s.add_argument("--task", default=None, help="pick | place | close-box | press | insert (sim/task_layouts.py)")
    s.add_argument("--workers", type=int, default=32)
    s = sub.add_parser("compare"); s.set_defaults(fn=cmd_compare)
    s.add_argument("a"); s.add_argument("b"); s.add_argument("--names", nargs=2, default=("A", "B"))
    s = sub.add_parser("evalset"); s.set_defaults(fn=lambda a: __import__("agentic_robotics.evaluation.evalsets", fromlist=["cli"]).cli(a))
    s.add_argument("action", choices=("build", "show")); s.add_argument("rest", nargs=argparse.REMAINDER)
    s = sub.add_parser("learn"); s.set_defaults(fn=lambda a: __import__("agentic_robotics.learning.loop", fromlist=["cli"]).cli(a))
    s.add_argument("action", choices=("init", "run", "status", "evaluate", "add-real", "extend", "progress"))
    s.add_argument("rest", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
