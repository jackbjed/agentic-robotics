"""Suites: fixed, reproducible lists of episodes. A suite spec (configs/experiments/*.yaml):
    {split: train|val|test, levels: [..], n: per level | idx: [start, stop], seed: s, parity: false}
An episode = (split, level, idx) of the layout banks + the reset seed s * 1000003 + level * 10007 + idx (the formula of
panda_pick_rgbd's evaluation: seed 5 on test = the episodes of its RL and skill-baseline results)."""
from __future__ import annotations


def episode_seed(seed, level, idx):
    return int(seed) * 1_000_003 + int(level) * 10007 + int(idx)


def episodes(spec):
    """spec: {family (tasks.py; default clutter-pick), split, levels, n | idx, seed} or {families: {name: spec, ...}}
    (a multi-task suite: the union, in that order)."""
    if "families" in spec:
        out = []
        for fam, sub in spec["families"].items():
            out += episodes(dict(sub, family=fam, seed=sub.get("seed", spec.get("seed", 0))))
        return out
    lo, hi = spec["idx"] if "idx" in spec else (spec.get("start", 0), spec.get("start", 0) + spec["n"])
    fam = spec.get("family", "clutter-pick")
    out = []
    for lv in spec.get("levels", [0]):
        for i in range(int(lo), int(hi)):
            ep = dict(split=spec["split"], level=int(lv), idx=i, seed=episode_seed(spec["seed"], lv, i))
            if fam != "clutter-pick":
                ep["family"] = fam
            out.append(ep)
    return out


def key(ep):
    fam = ep.get("family", "clutter-pick")
    base = f"{ep['split']}_L{ep['level']}_{ep['idx']:04d}"
    return base if fam == "clutter-pick" else f"{fam}_{base}"
