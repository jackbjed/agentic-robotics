"""The learning agent's ONLY command: bin/ar-practice (outside the workspace, not editable by the agent). It acts on the
workspace named by AR_WORKSPACE (set by the launcher; must be the current directory), and only within the run's
permitted ranges and budget (runs/learning/<run>/budget.json + ledger.jsonl: outside the workspace, never writable by
the agent). Every episode runs the workspace's CURRENT skill code, frozen for the call, inside the program sandbox.

  ar-practice status                                budget used / left, the permitted practice ranges
  ar-practice run [--levels 1,2] [--n 4] [--idx 5,17,...] [--seed 123] [--observations none|wrist|all]
                                                    N practice episodes (train layouts) -> practice/runs/NNN/
  ar-practice test                                  pytest on tests/ inside the program sandbox (no world, no network)
  ar-practice scene check practice/scenes/S.yaml     (when allowed) build a scene YOU wrote (sim/custom.py: objects at
                                                    x, y, yaw), check it, report + preview.png -> practice/scenes/S.check/
  ar-practice run --scene practice/scenes/S.yaml --n 3 [--seed S]   episodes on that scene (different sensor seeds)
  ar-practice eval                                  (ablation --add-evaluation-set only) the evaluation set on the
                                                    current code, per-episode results -> practice/eval/NNN/

Each practice run writes practice/runs/NNN/summary.md and, per episode, result.json (the judge's verdict, robot time),
events.jsonl, keyframes.png (a contact sheet), video.mp4, program/ (the program's own output and errors) and, if asked,
observations/ (the frames the program got). Failed and crashed episodes count against the budget like any other."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .. import paths


def fail(msg):
    print(f"ar-practice: {msg}", file=sys.stderr)
    sys.exit(2)


def context():
    ws = os.environ.get("AR_WORKSPACE")
    if not ws:
        fail("AR_WORKSPACE is not set (this command runs inside a learning session)")
    ws = Path(ws).resolve()
    if Path.cwd().resolve() != ws:
        fail(f"run it from the workspace ({ws})")
    meta = json.loads((ws / ".ar_run.json").read_text())
    run_dir = paths.RUNS / "learning" / meta["run_id"]
    budget = json.loads((run_dir / "budget.json").read_text())
    return ws, meta, run_dir, budget


@contextmanager
def ledger_lock(run_dir):
    with open(run_dir / "ledger.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def usage(run_dir, session=None):
    used = dict(episodes=0, robot_s=0.0, wall_s=0.0, runs=0)
    f = run_dir / "ledger.jsonl"
    if f.exists():
        for r in map(json.loads, open(f)):
            if r.get("kind") != "practice" or (session is not None and r.get("session") != session):
                continue
            used["episodes"] += r["episodes"]; used["robot_s"] += r["robot_s"]; used["wall_s"] += r["wall_s"]; used["runs"] += 1
    return used


def family_ranges(budget, fam):
    """(levels, idx_range) a task family may practise on (budget practice.families; else the run's single range)."""
    P = budget["practice"]
    F = (P.get("families") or {}).get(fam)
    if F is None:
        if P.get("families"):
            fail(f"task {fam!r} is not part of this run (tasks: {', '.join(P['families'])})")
        return list(P["levels"]), list(P["idx_range"])
    return list(F["levels"]), list(F["idx_range"])


def default_family(budget):
    fams = list((budget["practice"].get("families") or {}))
    return fams[0] if fams else "clutter-pick"


def cmd_status(a):
    ws, meta, run_dir, budget = context()
    s = meta.get("session")
    u, U = usage(run_dir, s), usage(run_dir)
    P = budget["practice"]
    out = dict(session=s, this_session=u, session_limit_episodes=P["episodes_per_session"],
               run_total=U, run_limit_episodes=P["episodes_total"],
               permitted=dict(split=P["split"], levels=P["levels"], idx_range=P["idx_range"],
                              seed_range=P["seed_range"], max_per_call=P["max_per_call"]))
    if P.get("families"):
        out["permitted"]["tasks"] = {f: dict(levels=v["levels"], idx_range=v["idx_range"]) for f, v in P["families"].items()}
    if (budget.get("scenes") or {}).get("allowed"):          # (the key is there, None, without custom scenes)
        out["scene_checks"] = f"{checks_used(run_dir, s)}/{budget['scenes']['checks_per_session']} this session"
    V = budget.get("vlm") or {}
    if V.get("enabled"):
        from ..perception.vlm import spent
        out["model"] = dict(calls_per_episode=V["max_calls_per_episode"], usd_spent_this_run=round(spent(meta["run_id"]), 3),
                            usd_run_cap=V["run_usd"])
    print(json.dumps(out, indent=1))


def checks_used(run_dir, session):
    f = run_dir / "ledger.jsonl"
    if not f.exists():
        return 0
    R = [r for r in map(json.loads, open(f)) if r.get("session") == session]
    return sum(r.get("kind") == "scene_check" for r in R) - sum(r.get("kind") == "scene_check_void" for r in R)


def eval_runs_used(run_dir, session):
    """Evaluation-set runs that count: those started, minus those spoiled by platform errors. (19-11: this counted the
    'started' and the 'done' record of each run, so the agent got 2 of its 3 runs.)"""
    f = run_dir / "ledger.jsonl"
    if not f.exists():
        return 0
    R = [r for r in map(json.loads, open(f)) if r.get("kind") == "eval_exposed" and r.get("session") == session]
    return sum(r.get("status") == "started" for r in R) - sum(bool(r.get("platform_errors")) for r in R if r.get("status") == "done")


def build_scene(ws, run_dir, budget, scene_path, out_dir, family="clutter-pick"):
    """Build a scene the agent wrote, in a privileged process (sim/custom.py) -> (report, layout or None)."""
    from ..config import task_config
    from ..execution.episode import base_env
    t = task_config(budget["task"])
    sp = (ws / scene_path).resolve()
    if not str(sp).startswith(str(ws / "practice" / "scenes") + os.sep) or sp.suffix not in (".yaml", ".yml"):
        fail("scenes are .yaml files under practice/scenes/")
    if not sp.exists():
        fail(f"{scene_path}: no such file")
    out_dir.mkdir(parents=True, exist_ok=True)
    from ..execution.episode import wait_for_gpu
    wait_for_gpu()                                 # (19-11: checks rendered on a full GPU failed: EGL_NOT_INITIALIZED)
    r = subprocess.run([str(paths.ROOT / ".venv/bin/python"), "-I", "-m", "agentic_robotics.sim.custom", "--scene", str(sp),
                        "--object-set", t["object_set"], "--colour", t["colour"], "--out", str(out_dir), "--task", family,
                        "--mounts", t.get("mounts", "twin")],
                       env=base_env(), cwd=str(paths.ROOT), capture_output=True, text=True, timeout=300)
    rep = json.loads((out_dir / "report.json").read_text()) if (out_dir / "report.json").exists() else \
        dict(ok=False, platform_error=True, why="the scene builder crashed (a platform problem, not your scene; not "
             "counted against your budget): " + r.stderr[-400:])
    lay = json.loads((out_dir / "layout.json").read_text()) if rep.get("ok") and (out_dir / "layout.json").exists() else None
    return rep, lay


def cmd_scene(a):
    ws, meta, run_dir, budget = context()
    S = budget.get("scenes") or {}
    if not S.get("allowed"):
        fail("writing your own scenes is not part of this run")
    if a.action != "check":
        fail("ar-practice scene check practice/scenes/NAME.yaml")
    s = meta.get("session")
    with ledger_lock(run_dir):
        if checks_used(run_dir, s) >= S["checks_per_session"]:
            fail(f"budget: {S['checks_per_session']} scene checks per session used")
        with open(run_dir / "ledger.jsonl", "a") as f:
            f.write(json.dumps(dict(kind="scene_check", session=s, t=time.time(), scene=a.path)) + "\n")
    out = (ws / a.path).with_suffix(".check")
    if out.exists():
        shutil.rmtree(out)
    fam = a.task or default_family(budget); family_ranges(budget, fam)
    rep, _ = build_scene(ws, run_dir, budget, a.path, out, fam)
    if rep.get("platform_error"):
        with ledger_lock(run_dir):
            with open(run_dir / "ledger.jsonl", "a") as f:
                f.write(json.dumps(dict(kind="scene_check_void", session=s, t=time.time(), scene=a.path)) + "\n")
    print(json.dumps(rep, indent=1))
    if rep.get("ok"):
        print(f"preview: {out.relative_to(ws)}/preview.png (scene camera | top view: x away from the robot points down, y right)")


def cmd_run(a):
    ws, meta, run_dir, budget = context()
    P = budget["practice"]
    layout = None
    if a.scene:
        if not (budget.get("scenes") or {}).get("allowed"):
            fail("writing your own scenes is not part of this run")
        if a.idx or a.levels:
            fail("--scene runs that scene only (no --idx / --levels)")
    fam = a.task or default_family(budget)
    lv_ok, idx_range = family_ranges(budget, fam)
    from .. import tasks as TK
    entry = TK.get(fam)["entry"] if P.get("families") else budget.get("entry", "skills/pick.py")
    levels = [int(x) for x in a.levels.split(",")] if a.levels else lv_ok
    if any(lv not in lv_ok for lv in levels):
        fail(f"levels must be within {lv_ok} for {fam}")
    seed = a.seed if a.seed is not None else int(np.random.default_rng().integers(*P["seed_range"]))
    if not P["seed_range"][0] <= seed < P["seed_range"][1]:
        fail(f"seed must be in {P['seed_range']}")
    rng = np.random.default_rng(seed)
    if a.scene:
        eps = [(-1, k) for k in range(a.n)]
    elif a.idx:
        idx = [int(x) for x in a.idx.split(",")]
        eps = [(levels[k % len(levels)], i) for k, i in enumerate(idx)]
    else:
        eps = [(levels[k % len(levels)], int(rng.integers(*idx_range))) for k in range(a.n)]
    if not a.scene and any(not idx_range[0] <= i < idx_range[1] for _, i in eps):
        fail(f"layout indices must be in {idx_range} for {fam}")
    if not 1 <= len(eps) <= P["max_per_call"]:
        fail(f"1 to {P['max_per_call']} episodes per call")
    if a.observations not in ("none", "wrist", "all"):
        fail("observations: none | wrist | all")
    s = meta.get("session")
    with ledger_lock(run_dir):
        u, U = usage(run_dir, s), usage(run_dir)
        if u["episodes"] + len(eps) > P["episodes_per_session"] or U["episodes"] + len(eps) > P["episodes_total"]:
            fail(f"budget: {u['episodes']}/{P['episodes_per_session']} practice episodes used this session, "
                 f"{U['episodes']}/{P['episodes_total']} in the run; asked for {len(eps)}")
        # reserve now (a crash still counts): the record is completed below
        with open(run_dir / "ledger.jsonl", "a") as f:
            f.write(json.dumps(dict(kind="practice", session=s, t=time.time(), episodes=len(eps), robot_s=0.0, wall_s=0.0,
                                    status="started")) + "\n")
    from ..evaluation.suites import episode_seed
    from ..evaluation.runner import snapshot
    from ..execution.episode import run_episode
    from ..config import episode_base, task_config
    t = task_config(budget["task"])
    pr = ws / "practice" / "runs"; pr.mkdir(parents=True, exist_ok=True)
    n = len([d for d in pr.iterdir() if d.is_dir()]) + 1
    out = pr / f"{n:03d}"; out.mkdir()
    if a.scene:
        rep, layout = build_scene(ws, run_dir, budget, a.scene, out / "scene", fam)
        if layout is None:
            print(json.dumps(rep, indent=1))
            if rep.get("platform_error"):
                with ledger_lock(run_dir):
                    with open(run_dir / "ledger.jsonl", "a") as f:
                        f.write(json.dumps(dict(kind="practice", session=s, t=time.time(), episodes=-len(eps), robot_s=0.0,
                                                wall_s=0.0, status="refund", why="scene builder crashed")) + "\n")
                fail("the scene builder crashed (a platform problem; the reserved episodes were refunded)")
            fail("the scene is not valid (nothing was run; the reserved episodes still count)")
    snap = run_dir / "practice_snapshots" / f"{s}_{n:03d}"
    snapshot(ws, snap)                                        # (the code this call ran, kept outside the workspace)
    obs = {"none": "none", "wrist": "all", "all": "all"}[a.observations]
    t0 = time.time(); rows = []
    from concurrent.futures import ThreadPoolExecutor
    def one(k_ep):
        k, (lv, i) = k_ep
        V = dict(budget["vlm"], tag=f"{meta['run_id']}:practice") if (budget.get("vlm") or {}).get("enabled") else None
        ep = dict(episode_base(t, record=dict(video=True, observations=obs, keyframes=True), vlm=V,
                               library=budget.get("library", True)),
                  split=P["split"] if layout is None else "custom", level=lv, idx=i, seed=episode_seed(seed, max(lv, 0), i))
        if layout is not None:
            ep["layout"] = layout
        if fam != "clutter-pick":
            ep["family"] = fam
        d = out / (f"{k:02d}_L{lv}_{i:04d}" if layout is None else f"{k:02d}_scene_{Path(a.scene).stem}")
        r = run_episode(snap, ep, d, entry=entry, sandbox=True, wall_limit_s=t["wall_limit_s"])
        if a.observations == "wrist" and (d / "observations").exists():
            for f in (d / "observations").glob("*d415*"):
                f.unlink()
        return k, lv, i, r
    with ThreadPoolExecutor(max_workers=min(len(eps), budget.get("practice_workers", 8))) as ex:
        for k, lv, i, r in ex.map(one, enumerate(eps)):
            rows.append((k, lv, i, r))
    wall = time.time() - t0
    robot = sum(float(r.get("robot_time_s") or 0) for *_, r in rows)
    with ledger_lock(run_dir):
        with open(run_dir / "ledger.jsonl", "a") as f:
            n_void = sum(r.get("category") == "platform_error" for *_, r in rows)   # (voided: refunded)
            f.write(json.dumps(dict(kind="practice", session=s, t=time.time(), episodes=-n_void, robot_s=round(robot, 2),
                                    wall_s=round(wall, 1), status="done", call=n, seed=seed, platform_errors=n_void,
                                    eps=[[lv, i] for _, lv, i, _ in rows],
                                    successes=sum(bool(r.get("success")) for *_, r in rows))) + "\n")
    where = (f"your scene {a.scene}" if a.scene else "train split") + f", task {fam}"
    L = [f"practice run {n:03d}: seed {seed}, {len(rows)} episodes ({where}), {wall:.0f} s wall", "",
         "| # | level | layout | result | robot s to success | program result | error |", "|---|---|---|---|---|---|---|"]
    for k, lv, i, r in rows:
        pr_ = r.get("program_result") or {}
        lv, i = (lv, i) if layout is None else ("scene", k)
        L.append(f"| {k:02d} | {lv} | {i} | {r.get('category')} | {r.get('t_success')} | "
                 f"{pr_.get('result') if isinstance(pr_, dict) else pr_} | {(r.get('program_error') or '')[:80]} |")
    ok = sum(bool(r.get("success")) for *_, r in rows)
    usd = sum(float((r.get("program") or {}).get("model_cost_usd") or 0) for *_, r in rows)
    calls = sum(int((r.get("program") or {}).get("model_calls") or 0) for *_, r in rows)
    L += ["", f"{ok}/{len(rows)} successes. Model calls {calls} (${usd:.3f}). Per episode: result.json, keyframes.png, "
          f"events.jsonl (incl. any model request/reply), program/, video.mp4"]
    if budget.get("library", True) is False:                  # (skills from scratch: what would get a version rejected)
        from ..execution.guard import violations
        v = violations(snap)
        if v:
            L += ["", "WARNING: this code reaches for the robot's own code; a version like this is rejected:"] + [f"- {x}" for x in v[:10]]
    (out / "summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    u = usage(run_dir, s)
    if n_void:
        print(f"\n{n_void} episode(s) voided by a platform error (category platform_error: not your program's fault); "
              "refunded")
    print(f"\nbudget: {u['episodes']}/{P['episodes_per_session']} practice episodes used this session")


def cmd_eval(a):
    """(ablation only) the evaluation set on the current code: per-episode results into practice/eval/NNN/."""
    ws, meta, run_dir, budget = context()
    E = budget.get("eval_access")
    if not E:
        fail("the evaluation set is not available in this run")
    s = meta.get("session")
    with ledger_lock(run_dir):
        used = eval_runs_used(run_dir, s)
        if used >= E["runs_per_session"]:
            fail(f"budget: {E['runs_per_session']} evaluation-set runs per session used")
        with open(run_dir / "ledger.jsonl", "a") as f:
            f.write(json.dumps(dict(kind="eval_exposed", session=s, t=time.time(), suite=E["suite"], status="started")) + "\n")
    from ..config import task_config
    from ..evaluation.runner import run_suite
    ev = ws / "practice" / "eval"; ev.mkdir(parents=True, exist_ok=True)
    n = len([d for d in ev.iterdir() if d.is_dir()]) + 1
    out = ev / f"{n:03d}"
    V = None
    if (budget.get("vlm") or {}).get("enabled"):
        vb = budget["vlm"]
        if vb.get("eval_usd") is not None:                     # (its own budget; an identical request answered from
            V = dict(vb, tag=f"{meta['run_id']}-eval:eval", run_usd=vb["eval_usd"],          # the cache, free)
                     cache_dir=str(run_dir / "vlm_cache_eval"))
        else:
            V = dict(vb, tag=f"{meta['run_id']}:eval")
    t0 = time.time()
    S = run_suite(ws, E["spec"], task_config(budget["task"]), out, workers=E.get("workers", 16), sandbox=True,
                  record=dict(video=True, observations="none", keyframes=True), entry=budget.get("entry", "skills/pick.py"),
                  log=None, vlm=V, library=budget.get("library", True))
    with ledger_lock(run_dir):
        with open(run_dir / "ledger.jsonl", "a") as f:
            f.write(json.dumps(dict(kind="eval_exposed", session=s, t=time.time(), suite=E["suite"], status="done", call=n,
                                    episodes_run=S["n"], successes=S["successes"], robot_s=S["robot_time_total_s"],
                                    platform_errors=int(S["categories"].get("platform_error", 0)),
                                    wall_s=round(time.time() - t0, 1), model_cost_usd=S["model_cost_usd"])) + "\n")
    print((out / "summary.md").read_text())
    if S["categories"].get("platform_error"):
        print(f"{S['categories']['platform_error']} episodes were voided by platform errors (not your program's fault): "
              "this run does not count against your evaluation runs")
    print(f"per episode: practice/eval/{n:03d}/episodes/<name>/ (result.json, keyframes.png, events.jsonl, program/)")


def cmd_test(a):
    ws, meta, run_dir, budget = context()
    from ..execution import sandbox as sbx
    tmp = paths.RUNS / "learning" / meta["run_id"] / "test_tmp"
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
    py = str(paths.ROOT / ".venv" / "bin" / "python")
    code = (f"import sys; sys.path.insert(0, {str(ws)!r}); import pytest; "
            f"sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', {str(ws / 'tests')!r}]))")
    cmd = sbx.program_cmd([py, "-I", "-c", code], ws, tmp, tmp)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print(r.stdout[-6000:]); print(r.stderr[-2000:], file=sys.stderr)
    sys.exit(r.returncode)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ar-practice")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    s = sub.add_parser("run"); s.set_defaults(fn=cmd_run)
    s.add_argument("--levels"); s.add_argument("--n", type=int, default=4); s.add_argument("--idx"); s.add_argument("--seed", type=int)
    s.add_argument("--observations", default="none"); s.add_argument("--scene"); s.add_argument("--task")
    sub.add_parser("test").set_defaults(fn=cmd_test)
    sub.add_parser("eval").set_defaults(fn=cmd_eval)
    s = sub.add_parser("scene"); s.set_defaults(fn=cmd_scene)
    s.add_argument("action", choices=("check",)); s.add_argument("path"); s.add_argument("--task")
    # real2sim (learning/real2sim.py): the real world, the agent's own simulator, its analysis scripts
    s = sub.add_parser("real"); s.add_argument("action", choices=("look", "run")); s.add_argument("program", nargs="?")
    s.add_argument("--task", required=True); s.add_argument("--idx"); s.add_argument("--n", type=int, default=1)
    s.add_argument("--wrist")
    s = sub.add_parser("sim"); s.add_argument("action", choices=("check", "render", "ready"))
    s.add_argument("--n", type=int, default=3); s.add_argument("--layout"); s.add_argument("--like")
    s = sub.add_parser("py"); s.add_argument("script"); s.add_argument("args", nargs=argparse.REMAINDER)
    argv = list(sys.argv[1:] if argv is None else argv)
    for k in range(len(argv) - 1):                            # (--wrist "-0.30,...": a value that starts with "-")
        if argv[k] == "--wrist" and not argv[k + 1].startswith("--"):
            argv[k:k + 2] = [f"--wrist={argv[k + 1]}", ""]
    argv = [x for x in argv if x != ""]
    a = ap.parse_args(argv)
    if a.cmd in ("real", "sim", "py") or (a.cmd in ("run", "status") and context()[3].get("real2sim")):
        from .real2sim import dispatch
        return dispatch(a)
    a.fn(a)


if __name__ == "__main__":
    main()
