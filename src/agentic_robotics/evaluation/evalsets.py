"""Evaluation sets: fixed episodes per task family, each one shown to be solvable (2026-11-19, user: "an evaluation set
that the agent can train against ... 25 randomized test episodes per task ... randomized positions and orientations
but all be solvable"; docs/evalsets.md). For one family and one split (evalset: the set an agent may run in the
ablation; evalhidden: the same kind of set, never shown to any agent):

  1. candidates: the family's own layout generator (sim/task_layouts.py) with the split's own seeds: random positions
     and orientations, each layout checked as every bank's is (inside the work area, nothing overlapping, nothing
     moving when settled, the target seen by the scene camera, the button / sticks reachable from above; clutter: the
     neighbours' gaps and a free straight-down grasp) -> data/<banks>/<split>_L0.jsonl
  2. certification: reference programs on every candidate, through the full platform (world server, sandbox, the
     judge), with exact segmentation (the oracle) and the experiment's cameras and arm start. The first attempt uses
     the set's own reset seed, so an episode certified then is exactly the episode later evaluated; a candidate that
     fails gets further attempts (other sensor seeds, the next reference program). Certified = solved once.
  3. the set: the first N certified candidates in candidate order (never chosen by difficulty); with --per-target K,
     the first K certified of each of the family's objects (a set balanced over the objects)
     -> data/<banks>/<split>_L0.certified.json (all certified indices, which program solved each, in how much robot
     time) and the suite spec {split, idxs, seed} for the experiment.

    ar evalset build --task place-box --split evalset --n 25 [--per-target 5] [--candidates 80] [--seed 31]
        [--task-config configs/tasks/tasks_B_rig_home_oracle.yaml] [--solver initial_skills/tasks] [--solver DIR:scratch]
        [--attempts 2] [--workers 12] [--vlm EXPERIMENT --vlm-usd X]
    ar evalset show [--task T] [--split S]

A reference program is a skill folder (default initial_skills/tasks: the robot's own hand-written skills, with the
skill library); DIR:scratch runs one written without the library (an agent's checkpoint); DIR...@CONFIG runs it in
that task config (an agent's program needs SAM3: the oracle knows the objects' names only)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import paths, tasks as TK

DEFAULT_SOLVER = "initial_skills/tasks"
DEFAULT_CONFIG = "configs/tasks/tasks_B_rig_home_oracle.yaml"
SET_SEED = {"evalset": 31, "evalhidden": 41}


def bank_file(task, split):
    return paths.DATA / TK.get(task)["banks"] / f"{split}_L0.jsonl"


def cert_file(task, split):
    return paths.DATA / TK.get(task)["banks"] / f"{split}_L0.certified.json"


def _solver(s, default_cfg):
    """DIR[:scratch][@TASK_CONFIG] -> (skill root, library?, task config): an agent's program asks for objects in its
    own words, which only SAM3 understands (the oracle knows the objects' names only): give it the SAM3 condition."""
    s, _, cfg = str(s).partition("@")
    root, _, mode = s.partition(":")
    return (Path(root) if Path(root).is_absolute() else paths.ROOT / root), mode != "scratch", cfg or default_cfg


def certify(task, split, candidates, task_cfg, solvers, attempts, workers, seed, vlm=None, log=print, run_suite=None):
    """-> {idx: dict(solver, seed, attempt, t_success, robot_s)} for the candidates solved, and the attempts' records."""
    from ..config import task_config
    if run_suite is None:
        from .runner import run_suite
    out_root = paths.RUNS / "evalsets" / task / split
    solved, tries, remaining = {}, [], list(range(candidates))
    plan = [(k, s) for k in range(attempts) for s in solvers]
    for n_try, (k, s) in enumerate(plan):
        if not remaining:
            break
        root, lib, cfg = _solver(s, task_cfg)
        sd = seed + 1000 * k                                    # (attempt 0: the set's own reset seeds)
        out = out_root / f"{root.name}_{Path(cfg).stem}_s{sd}"   # (stable: a rebuild reuses finished episodes)
        log(f"[{task} {split}] attempt {n_try}: {root.name} ({'library' if lib else 'scratch'}, {Path(cfg).stem}), seed {sd}, "
            f"{len(remaining)} candidates")
        S = run_suite(root, dict(family=task, split=split, idxs=remaining, seed=sd), task_config(cfg), out, workers=workers, sandbox=True,
                      entry=TK.get(task)["entry"], log=None, vlm=vlm, library=lib)
        want = set(remaining)                                   # (the file may hold earlier runs' episodes too)
        recs = [r for r in (json.loads(l) for l in open(out / "episodes.jsonl")) if r["idx"] in want] \
            if (out / "episodes.jsonl").exists() else []
        voided = 0
        for r in recs:
            if r.get("category") == "platform_error":
                voided += 1
            elif r.get("success") and r["idx"] not in solved:
                solved[r["idx"]] = dict(solver=str(s), seed=sd, attempt=n_try, t_success=r.get("t_success"),
                                        robot_s=r.get("robot_time_s"))
        remaining = [i for i in remaining if i not in solved]
        tries.append(dict(attempt=n_try, solver=str(s), seed=sd, episodes=S["n"], successes=S["successes"],
                          platform_errors=voided, out=str(out.relative_to(paths.ROOT)) if out.is_relative_to(paths.ROOT) else str(out)))
        log(f"  solved {S['successes']}/{S['n']} (platform errors {voided}); certified so far {len(solved)}/{candidates}")
    return solved, tries


def choose(certified, target_of, n, per_target=None, targets=None):
    """The set: the first n certified (candidate order), or the first per_target certified of each target.
    -> (indices, shortfall per target or total)"""
    if not per_target:
        return certified[:n], max(0, n - len(certified))
    by = {}
    for i in certified:
        by.setdefault(target_of[i], []).append(i)
    chosen = sorted(i for t in targets for i in by.get(t, [])[:per_target])
    return chosen, {t: per_target - len(by.get(t, [])[:per_target]) for t in targets if len(by.get(t, [])) < per_target}


def build(task, split, n=25, candidates=80, seed=None, task_cfg=DEFAULT_CONFIG, solvers=(DEFAULT_SOLVER,), attempts=2,
          workers=12, vlm=None, log=print, run_suite=None, generate=None, per_target=None):
    td = TK.get(task)
    seed = SET_SEED.get(split, 31) if seed is None else int(seed)
    f = bank_file(task, split)
    meta = f.with_suffix(".meta.json")
    have = json.loads(meta.read_text()).get("n") if meta.exists() else None
    if not f.exists() or have != candidates:                    # (deterministic per index: a rebuild is identical)
        if generate is None:
            from ..sim.task_layouts import generate
        r = generate(task, split, candidates, workers=workers, log=None)
        log(f"[{task} {split}] candidates: {r.get('ok')} valid of {candidates} drawn")
    N = sum(1 for line in open(f) if line.strip())
    t0 = time.time()
    solved, tries = certify(task, split, N, task_cfg, list(solvers), attempts, workers, seed, vlm, log, run_suite)
    certified = sorted(solved)
    L = [json.loads(line) for line in open(f) if line.strip()]
    chosen, short = choose(certified, [x["target"] for x in L], n, per_target, td.get("targets"))
    rec = dict(task=task, split=split, family_targets=td.get("targets"), seed=seed, n_wanted=n, per_target=per_target,
               candidates_drawn=candidates, candidates_valid=N, certified=certified, set=chosen, short=short,
               per_idx={str(i): solved[i] for i in certified}, attempts=tries, task_config=task_cfg,
               solvers=list(solvers), built=time.strftime("%Y-%m-%d %H:%M"), wall_s=round(time.time() - t0, 1),
               suite=dict(split=split, idxs=chosen, seed=seed))
    rec["targets_certified"] = {k: sum(1 for i in certified if L[i]["target"] == k) for k in sorted({x["target"] for x in L})}
    rec["targets_candidates"] = {k: sum(1 for x in L if x["target"] == k) for k in sorted({x["target"] for x in L})}
    if chosen:
        rec["targets_in_set"] = {k: sum(1 for i in chosen if L[i]["target"] == k) for k in sorted({L[i]["target"] for i in chosen})}
    cert_file(task, split).write_text(json.dumps(rec, indent=1))
    log(f"[{task} {split}] certified {len(certified)}/{N}; the set: {len(chosen)} episodes"
        + (f" (SHORT by {rec['short']}: draw more candidates)" if rec["short"] else "") + f" -> {cert_file(task, split)}")
    return rec


def show(task=None, split=None):
    out = []
    for name, td in TK.TASKS.items():
        if task and name != task:
            continue
        for sp in ("evalset", "evalhidden"):
            if split and sp != split:
                continue
            c = cert_file(name, sp)
            if c.exists():
                r = json.loads(c.read_text())
                out.append(r)
                print(f"{name} {sp}: {len(r['set'])}/{r['n_wanted']} (certified {len(r['certified'])} of {r['candidates_valid']} "
                      f"candidates); targets {r.get('targets_in_set')}\n  suite: {json.dumps(r['suite'])}")
    return out


def cli(a):
    import argparse
    ap = argparse.ArgumentParser(prog=f"ar evalset {a.action}")
    ap.add_argument("--task"); ap.add_argument("--split", default="evalset", choices=("evalset", "evalhidden"))
    ap.add_argument("--n", type=int, default=25); ap.add_argument("--candidates", type=int, default=80)
    ap.add_argument("--per-target", type=int, help="the first K certified of each of the family's objects")
    ap.add_argument("--seed", type=int); ap.add_argument("--task-config", default=DEFAULT_CONFIG)
    ap.add_argument("--solver", action="append", help="a reference program: DIR[:scratch][@TASK_CONFIG]; repeatable")
    ap.add_argument("--attempts", type=int, default=2); ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--vlm", help="the vlm block of this experiment for the reference programs (default: none)")
    ap.add_argument("--vlm-usd", type=float, default=0.0)
    o = ap.parse_args(a.rest)
    if a.action == "show":
        show(o.task, o.split if "--split" in a.rest else None)
        return
    if not o.task:
        raise SystemExit("--task is needed")
    V = None
    if o.vlm and o.vlm_usd > 0:
        V = dict(paths.load_yaml(o.vlm)["vlm"], tag=f"evalset-{o.task}-{o.split}:certify", run_usd=o.vlm_usd)
        V["max_calls_per_episode"] = 1
    build(o.task, o.split, o.n, o.candidates, o.seed, o.task_config, o.solver or [DEFAULT_SOLVER], o.attempts, o.workers, V,
          per_target=o.per_target)
