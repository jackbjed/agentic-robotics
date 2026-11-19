"""The learning agent's ONLY command: bin/ar-practice (outside the workspace, not editable by the agent). It acts on the
workspace named by AR_WORKSPACE (set by the launcher; must be the current directory), and only within the run's
permitted ranges and budget (runs/learning/<run>/budget.json + ledger.jsonl: outside the workspace, never writable by
the agent). Every episode runs the workspace's CURRENT skill code, frozen for the call, inside the program sandbox.

  ar-practice status                                budget used / left, the permitted practice ranges
  ar-practice run [--levels 1,2] [--n 4] [--idx 5,17,...] [--seed 123] [--observations none|wrist|all]
                                                    N practice episodes (train layouts) -> practice/runs/NNN/
  ar-practice test                                  pytest on tests/ inside the program sandbox (no world, no network)

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


def cmd_status(a):
    ws, meta, run_dir, budget = context()
    s = meta.get("session")
    u, U = usage(run_dir, s), usage(run_dir)
    P = budget["practice"]
    print(json.dumps(dict(session=s, this_session=u, session_limit_episodes=P["episodes_per_session"],
                          run_total=U, run_limit_episodes=P["episodes_total"],
                          permitted=dict(split=P["split"], levels=P["levels"], idx_range=P["idx_range"],
                                         seed_range=P["seed_range"], max_per_call=P["max_per_call"])), indent=1))


def cmd_run(a):
    ws, meta, run_dir, budget = context()
    P = budget["practice"]
    levels = [int(x) for x in a.levels.split(",")] if a.levels else list(P["levels"])
    if any(lv not in P["levels"] for lv in levels):
        fail(f"levels must be within {P['levels']}")
    seed = a.seed if a.seed is not None else int(np.random.default_rng().integers(*P["seed_range"]))
    if not P["seed_range"][0] <= seed < P["seed_range"][1]:
        fail(f"seed must be in {P['seed_range']}")
    rng = np.random.default_rng(seed)
    if a.idx:
        idx = [int(x) for x in a.idx.split(",")]
        eps = [(levels[k % len(levels)], i) for k, i in enumerate(idx)]
    else:
        eps = [(levels[k % len(levels)], int(rng.integers(*P["idx_range"]))) for k in range(a.n)]
    if any(not P["idx_range"][0] <= i < P["idx_range"][1] for _, i in eps):
        fail(f"layout indices must be in {P['idx_range']}")
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
    from ..cli import task_config
    t = task_config(budget["task"])
    pr = ws / "practice" / "runs"; pr.mkdir(parents=True, exist_ok=True)
    n = len([d for d in pr.iterdir() if d.is_dir()]) + 1
    out = pr / f"{n:03d}"; out.mkdir()
    snap = run_dir / "practice_snapshots" / f"{s}_{n:03d}"
    snapshot(ws, snap)                                        # (the code this call ran, kept outside the workspace)
    obs = {"none": "none", "wrist": "all", "all": "all"}[a.observations]
    t0 = time.time(); rows = []
    from concurrent.futures import ThreadPoolExecutor
    def one(k_ep):
        k, (lv, i) = k_ep
        ep = dict(split=P["split"], level=lv, idx=i, seed=episode_seed(seed, lv, i),
                  scene=dict(preset=t["preset"], colour_noise=t["colour_noise"]), perception=t["perception"],
                  robot_time_limit_s=t["robot_time_limit_s"], record=dict(video=True, observations=obs, keyframes=True), parity=False)
        d = out / f"{k:02d}_L{lv}_{i:04d}"
        r = run_episode(snap, ep, d, entry=budget.get("entry", "skills/pick.py"), sandbox=True, wall_limit_s=t["wall_limit_s"])
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
            f.write(json.dumps(dict(kind="practice", session=s, t=time.time(), episodes=0, robot_s=round(robot, 2),
                                    wall_s=round(wall, 1), status="done", call=n, seed=seed,
                                    eps=[[lv, i] for _, lv, i, _ in rows],
                                    successes=sum(bool(r.get("success")) for *_, r in rows))) + "\n")
    L = [f"practice run {n:03d}: seed {seed}, {len(rows)} episodes (train split), {wall:.0f} s wall", "",
         "| # | level | layout | result | robot s to success | program result | error |", "|---|---|---|---|---|---|---|"]
    for k, lv, i, r in rows:
        pr_ = r.get("program_result") or {}
        L.append(f"| {k:02d} | {lv} | {i} | {r.get('category')} | {r.get('t_success')} | "
                 f"{pr_.get('result') if isinstance(pr_, dict) else pr_} | {(r.get('program_error') or '')[:80]} |")
    ok = sum(bool(r.get("success")) for *_, r in rows)
    L += ["", f"{ok}/{len(rows)} successes. Per episode: result.json, keyframes.png, events.jsonl, program/, video.mp4"]
    (out / "summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    u = usage(run_dir, s)
    print(f"\nbudget: {u['episodes']}/{P['episodes_per_session']} practice episodes used this session")


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
    s.add_argument("--observations", default="none")
    sub.add_parser("test").set_defaults(fn=cmd_test)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
