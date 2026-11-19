"""Paired comparison of two skill versions on the SAME episodes (episodes.jsonl of two suite runs): successes (exact
McNemar test on the discordant pairs), robot time on the episodes both solve (mean difference, bootstrap 95% CI),
model calls/cost. compare(a_dir, b_dir) -> dict; compare_md(...) -> a markdown table."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np


def load(d):
    return {r["key"]: r for r in map(json.loads, open(Path(d) / "episodes.jsonl"))}


def mcnemar_p(b, c):
    """Two-sided exact McNemar p-value: b = only A succeeds, c = only B succeeds."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def compare(a_dir, b_dir, seed=0):
    A, B = load(a_dir), load(b_dir)
    keys = sorted(set(A) & set(B))
    a = np.array([A[k]["success"] for k in keys]); b = np.array([B[k]["success"] for k in keys])
    only_a, only_b = int((a & ~b).sum()), int((~a & b).sum())
    both = a & b
    d = np.array([B[k]["t_success"] - A[k]["t_success"] for k, x in zip(keys, both) if x])
    ci = (None, None)
    if len(d) > 1:
        rng = np.random.default_rng(seed)
        m = rng.choice(d, size=(5000, len(d))).mean(1)
        ci = (round(float(np.percentile(m, 2.5)), 3), round(float(np.percentile(m, 97.5)), 3))
    return dict(n=len(keys), a_successes=int(a.sum()), b_successes=int(b.sum()), only_a=only_a, only_b=only_b,
                mcnemar_p=round(mcnemar_p(only_a, only_b), 5), both=int(both.sum()),
                dt_mean=round(float(d.mean()), 3) if len(d) else None, dt_ci95=ci,
                b_faster_frac=round(float((d < 0).mean()), 3) if len(d) else None,
                a_t=round(float(np.mean([A[k]["t_success"] for k in keys if A[k]["success"]])), 3) if a.any() else None,
                b_t=round(float(np.mean([B[k]["t_success"] for k in keys if B[k]["success"]])), 3) if b.any() else None,
                a_cost=round(sum(A[k].get("model_cost_usd") or 0 for k in keys), 4),
                b_cost=round(sum(B[k].get("model_cost_usd") or 0 for k in keys), 4))


def compare_md(c, a_name="A", b_name="B"):
    return (f"| | {a_name} | {b_name} |\n|---|---|---|\n"
            f"| successes / {c['n']} | {c['a_successes']} | {c['b_successes']} |\n"
            f"| robot s per success | {c['a_t']} | {c['b_t']} |\n"
            f"| model cost (USD) | {c['a_cost']} | {c['b_cost']} |\n\n"
            f"discordant: only {a_name} {c['only_a']}, only {b_name} {c['only_b']} (McNemar p = {c['mcnemar_p']}); "
            f"on the {c['both']} both solve, {b_name} - {a_name} = {c['dt_mean']} s (95% CI {c['dt_ci95']}), "
            f"{b_name} faster on {c['b_faster_frac']}\n")
