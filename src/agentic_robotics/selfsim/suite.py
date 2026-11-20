"""Episodes in the agent's own simulator (real2sim, docs/real2sim.md): practice calls and the development suite.

  prepare_sim(sim_src, dst)              a frozen copy of sim/ + its composed model -> dict(dir, model, decl, info, hash)
  run_items(skill_snap, prep, items, out_root, ...)   items = [(family, idx, seed)]: the layouts sampled by the agent's
                                         sim/layouts.py (one sandboxed call per family), checked, each episode in the
                                         world server over the agent's simulator + the program sandbox -> [(item, result)]
  run_selfsim_suite(skill_root, prep, spec, out, ...)   a suite spec ({families: {F: {n, seed}}}) -> the same files as
                                         evaluation/runner.run_suite (episodes.jsonl, summary.json / .md): the selection
                                         rule's comparison works unchanged"""
from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .. import paths
from ..execution.versions import tree_hash
from .model import SimError, compose
from .sample import check_layout, sample_layouts

TASK_CONFIG = "configs/tasks/selfsim.yaml"
SIM_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "check", "checks", "renders")


def rmtree_ro(d):
    """shutil.rmtree for a frozen (read-only) copy."""
    d = Path(d)
    if not d.exists():
        return
    for p in [d] + list(d.rglob("*")):
        if p.is_dir() and not p.is_symlink():
            p.chmod(0o755)
    shutil.rmtree(d)


def prepare_sim(sim_src, dst):
    """-> dict(dir, model, decl, info, hash) or dict(error=...) (the reconstruction does not build)."""
    dst = Path(dst)
    snap = dst / "sim"
    rmtree_ro(snap)
    if not Path(sim_src).is_dir():
        return dict(error="there is no sim/ (your reconstruction) yet")
    shutil.copytree(sim_src, snap, ignore=SIM_IGNORE)
    for p in snap.rglob("*"):
        p.chmod(0o555 if p.is_dir() else 0o444)
    try:
        r = compose(snap, dst / "compiled" / "model.xml")
    except SimError as e:
        return dict(error=str(e), dir=str(snap))
    return dict(dir=str(snap), model=str(dst / "compiled" / "model.xml"), decl=r["decl"], info=r["info"], hash=tree_hash(snap))


def episode_spec(prep, family, layout, seed, idx, record, library=False):
    from ..config import episode_base, task_config
    t = task_config(TASK_CONFIG)
    ep = dict(episode_base(t, record=record, library=library), family=family, split="selfsim", level=0, idx=int(idx),
              seed=int(seed), layout=layout, selfsim=dict(model=prep["model"], decl=prep["decl"], hash=prep["hash"]))
    return ep, t


def sim_error_result(why, family, idx, seed):
    return dict(success=False, category="sim_error", sim_error=str(why)[:1500], t_success=None, robot_time_s=0.0,
                program_result=None, program_error=None, episode=dict(family=family, split="selfsim", level=0, idx=idx, seed=seed))


def run_items(skill_snap, prep, items, out_root, workers=8, record=None, name=None, log=None):
    """items: [(family, idx, seed)] -> [(item, result)] in that order; episode dirs out_root/<name(item)>/."""
    from .. import tasks as TK
    from ..execution.episode import run_episode
    record = record or dict(video=True, observations="none", keyframes=True)
    name = name or (lambda it: f"{it[0]}_{it[1]:04d}")
    out_root = Path(out_root); out_root.mkdir(parents=True, exist_ok=True)
    lays = {}
    for fam in sorted({it[0] for it in items}):
        mine = [it for it in items if it[0] == fam]
        raw = sample_layouts(prep["dir"], fam, [it[2] for it in mine], out_root / f".sample_{fam}")
        for it, L in zip(mine, raw):
            try:
                lays[it] = check_layout(L, prep["decl"], fam)
            except SimError as e:
                lays[it] = e
    res = {}

    def one(it):
        fam, idx, seed = it
        d = out_root / name(it)
        L = lays[it]
        if isinstance(L, SimError):
            d.mkdir(parents=True, exist_ok=True)
            r = sim_error_result(f"your layout sampler: {L}", fam, idx, seed)
            json.dump(r, open(d / "result.json", "w"), indent=1)
            return it, r
        ep, t = episode_spec(prep, fam, L, seed, idx, record)
        try:
            r = run_episode(skill_snap, ep, d, entry=TK.get(fam)["entry"], sandbox=True, wall_limit_s=t.get("wall_limit_s", 900))
        except Exception as e:
            r = dict(success=False, category="platform_error", error=f"{type(e).__name__}: {e}"[:500])
        r["layout"] = L
        json.dump(r, open(d / "result.json", "w"), indent=1, default=str)
        return it, r

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items)))) as ex:
        futs = [ex.submit(one, it) for it in items]
        for k, f in enumerate(as_completed(futs)):
            it, r = f.result()
            res[it] = r
            if log:
                log(f"  {k + 1}/{len(items)} {it[0]} {it[1]}: {r.get('category')}")
    return [(it, res[it]) for it in items]


def suite_items(spec):
    from ..evaluation.suites import episode_seed
    out = []
    for fam, sub in spec["families"].items():
        for i in range(int(sub.get("start", 0)), int(sub.get("start", 0)) + int(sub["n"])):
            out.append((fam, i, episode_seed(sub.get("seed", 0), 0, i)))
    return out


def run_selfsim_suite(skill_root, prep, spec, out_dir, workers=8, log=None):
    """The development suite in the agent's simulator (prep: prepare_sim). Writes run_suite's files."""
    from ..evaluation.runner import compact, snapshot, summarize, summary_md
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    snap = out / "skill_snapshot"
    if not snap.exists():
        snapshot(skill_root, snap)
    items = suite_items(spec)
    json.dump(dict(spec=spec, sim=dict(hash=prep.get("hash"), dir=prep.get("dir")), skills=tree_hash(snap), n=len(items)),
              open(out / "suite.json", "w"), indent=1)
    t0 = time.time()
    if prep.get("error"):
        rows = [(it, sim_error_result(f"the reconstruction does not build: {prep['error']}", *it)) for it in items]
    else:
        rows = run_items(snap, prep, items, out / "episodes", workers=workers,
                         name=lambda it: f"{it[0]}_selfsim_L0_{it[1]:04d}", log=log)
    recs = []
    for (fam, idx, seed), r in rows:
        ep = dict(family=fam, split="selfsim", level=0, idx=idx, seed=seed)
        recs.append(compact(ep, r))
    with open(out / "episodes.jsonl", "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    S = summarize(recs)
    S["wall_s"] = round(time.time() - t0, 1)
    S["sim_hash"] = prep.get("hash")
    json.dump(S, open(out / "summary.json", "w"), indent=1)
    (out / "summary.md").write_text(summary_md(S, spec))
    return S
