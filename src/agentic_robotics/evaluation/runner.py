"""Run a FROZEN skill version on a suite: every episode in its own world server + sandboxed program (episode.py), N
in parallel. The skill code is copied once into <out>/skill_snapshot (read-only inside the sandbox): nothing can edit
it during the evaluation. Resumable (episodes with a result.json are kept).

    run_suite(skill_root, spec, task_cfg, out_dir, workers=8) -> summary dict
Writes <out>/episodes/<key>/..., <out>/episodes.jsonl (one compact record per episode), <out>/summary.json/.md."""
from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from ..execution.episode import run_episode
from ..execution.versions import tree_hash
from . import suites


def snapshot(skill_root, dst):
    dst = Path(dst)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(skill_root, dst, ignore=shutil.ignore_patterns("__pycache__", "practice", "runs", "*.pyc", "notes.md", "reference", "docs", "AGENTS.md", "CLAUDE.md", ".ar_run.json"))
    for p in dst.rglob("*"):
        p.chmod(0o555 if p.is_dir() else 0o444)
    return dst


def compact(ep, r):
    pr = r.get("program") or {}
    return dict(key=suites.key(ep), family=ep.get("family", "clutter-pick"), split=ep["split"], level=ep["level"], idx=ep["idx"], seed=ep["seed"],
                success=bool(r.get("success")), category=r.get("category"), t_success=r.get("t_success"),
                robot_time_s=r.get("robot_time_s"), time_limit_hit=r.get("time_limit_hit"),
                program_result=(r.get("program_result") or {}).get("result") if isinstance(r.get("program_result"), dict) else r.get("program_result"),
                program_error=r.get("program_error"), compute_wall_s=pr.get("compute_wall_s"),
                segment_calls=pr.get("segment_calls"), model_calls=pr.get("model_calls", 0),
                model_cost_usd=pr.get("model_cost_usd", 0.0), model_wait_s=pr.get("model_wait_s", 0.0),
                episode_wall_s=(r.get("wall") or {}).get("episode_total"), max_disturb_mm=r.get("max_disturb_mm"),
                skills=(r.get("versions") or {}).get("skills"))


def run_suite(skill_root, spec, task_cfg, out_dir, workers=8, sandbox=True, record=None, entry="skills/pick.py", log=print,
              vlm=None):
    """spec may name its own task config (spec["task"]): a suite with a FIXED condition, whatever the experiment's
    training condition (the fidelity study's common held-out target distribution)."""
    if spec.get("task"):
        from ..config import task_config
        task_cfg = task_config(spec["task"])
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    snap = out / "skill_snapshot"
    if not snap.exists():
        snapshot(skill_root, snap)
    eps = suites.episodes(spec)
    record = record or dict(video=True, observations="none")
    from ..config import episode_base
    base = episode_base(task_cfg, record=record, parity=spec.get("parity", False), vlm=vlm)
    json.dump(dict(spec=spec, task=task_cfg, base=base, skills=tree_hash(snap), n=len(eps)), open(out / "suite.json", "w"), indent=1)
    t0 = time.time()
    results = {}

    def one(ep):
        d = out / "episodes" / suites.key(ep)
        rf = d / "result.json"
        if rf.exists():
            r = json.load(open(rf))
            if r.get("category") != "platform_error":
                return ep, r
        fam = ep.get("family", "clutter-pick")
        from .. import tasks as TK
        e_ = entry if fam == "clutter-pick" else TK.get(fam)["entry"]
        try:
            return ep, run_episode(snap, dict(base, **ep), d, entry=e_, sandbox=sandbox,
                                   wall_limit_s=task_cfg.get("wall_limit_s", 900))
        except Exception as e:                                  # (the platform failed, not the program: void)
            return ep, dict(success=False, category="platform_error", error=f"{type(e).__name__}: {e}"[:500])

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, ep) for ep in eps]
        for k, f in enumerate(as_completed(futs)):
            ep, r = f.result()
            results[suites.key(ep)] = compact(ep, r)
            if log and (k + 1) % max(1, len(eps) // 20) == 0:
                ok = sum(x["success"] for x in results.values())
                log(f"  {k + 1}/{len(eps)} episodes, {ok} successes ({time.time() - t0:.0f} s)")
    recs = [results[suites.key(ep)] for ep in eps]
    with open(out / "episodes.jsonl", "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    S = summarize(recs)
    S["wall_s"] = round(time.time() - t0, 1)
    json.dump(S, open(out / "summary.json", "w"), indent=1)
    (out / "summary.md").write_text(summary_md(S, spec))
    return S


def summarize(recs):
    ok = [r for r in recs if r["success"]]
    t = np.array([r["t_success"] for r in ok], float)
    S = dict(n=len(recs), successes=len(ok), success_rate=round(len(ok) / max(1, len(recs)), 4),
             t_success_mean=round(float(t.mean()), 3) if len(t) else None,
             t_success_median=round(float(np.median(t)), 3) if len(t) else None,
             robot_time_total_s=round(sum(r["robot_time_s"] or 0 for r in recs), 1),
             time_limit_hits=sum(bool(r.get("time_limit_hit")) for r in recs),
             program_errors=sum(bool(r.get("program_error")) for r in recs),
             platform_errors=sum(r["category"] == "platform_error" for r in recs),
             model_calls=sum(r.get("model_calls") or 0 for r in recs),
             model_cost_usd=round(sum(r.get("model_cost_usd") or 0 for r in recs), 4),
             compute_wall_s_mean=round(float(np.mean([r["compute_wall_s"] or 0 for r in recs])), 3) if recs else None,
             categories={}, by_level={}, program_results={}, by_family={})
    for r in recs:
        S["categories"][r["category"]] = S["categories"].get(r["category"], 0) + 1
        pr = str(r.get("program_result"))
        S["program_results"][pr] = S["program_results"].get(pr, 0) + 1
    for fam in sorted({r.get("family", "clutter-pick") for r in recs}):
        R = [r for r in recs if r.get("family", "clutter-pick") == fam]; O = [r for r in R if r["success"]]
        S["by_family"][fam] = dict(n=len(R), successes=len(O),
                                   t_success_mean=round(float(np.mean([r["t_success"] for r in O])), 3) if O else None)
    for lv in sorted({r["level"] for r in recs}):
        R = [r for r in recs if r["level"] == lv]; O = [r for r in R if r["success"]]
        S["by_level"][str(lv)] = dict(n=len(R), successes=len(O),
                                      t_success_mean=round(float(np.mean([r["t_success"] for r in O])), 3) if O else None)
    return S


def summary_md(S, spec):
    L = [f"suite {spec}", "", f"success {S['successes']}/{S['n']} = {S['success_rate']:.3f}; robot s per success "
         f"{S['t_success_mean']} (median {S['t_success_median']}); model calls {S['model_calls']} (${S['model_cost_usd']})", "",
         "| level | n | successes | s / success |", "|---|---|---|---|"]
    for lv, d in S["by_level"].items():
        L.append(f"| {lv} | {d['n']} | {d['successes']} | {d['t_success_mean']} |")
    if len(S.get("by_family") or {}) > 1:
        L += ["", "| task | n | successes | s / success |", "|---|---|---|---|"]
        for fam, d in S["by_family"].items():
            L.append(f"| {fam} | {d['n']} | {d['successes']} | {d['t_success_mean']} |")
    L += ["", f"categories: {S['categories']}", f"program results: {S['program_results']}"]
    return "\n".join(L) + "\n"
