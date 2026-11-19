"""Layout banks for the clutter pick: one target and 2-4 other objects near it, settled, checked, stored. Ported
2026-11-18 from panda_pick_rgbd pickrgbd/layouts.py (its working tree, = the generator of the vendored banks incl. the
level-3 rules): sample() unchanged; new here: the object set (condition C's substitute objects, docs/fidelity.md), and
the splits' seeds of the substitute banks (distinct from the real banks'). Rejected (and resampled) when:
  - outlines overlap or come closer than the level's gap range allows, leave the bounds, or touch the ruler;
  - after settling (1 s) an object moved > 5 mm, tilted > 5 deg, or any two objects interpenetrate;
  - the hand cannot be at the start pose, or the target is < 50 % / < 60 px visible in the scene camera there;
  - clutter_env.kin_grasps (80 mm opening, 4 mm margin) finds < 2 free closing directions on the target (level 3:
    the reverse, none at all: boxed in).

    bin/ar layouts --object-set substitute --split train --level 0 --n 500 [--workers 32]"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from multiprocessing import get_context

import numpy as np

from .. import paths
from .picktwin import poly_gap


@dataclass
class TaskCfg:                                    # pickrgbd/config.py TaskCfg (its layout fields), unchanged
    targets: tuple = ("tape_yellow", "tape_red")
    color_conflicts: dict = field(default_factory=lambda: {"tape_red": ["glue", "screwdriver"], "tape_yellow": []})
    distractor_pool: tuple = ("tape_red", "tape_yellow", "tape_white", "hole_block", "cable", "screwdriver", "glue", "sticky_pad")
    n_extra: tuple = (1, 3)
    center_x: tuple = (-0.38, -0.24)
    center_y: tuple = (-0.03, 0.07)
    bounds_x: tuple = (-0.46, -0.15)
    bounds_y: tuple = (-0.15, 0.180)
    level_gaps: dict = field(default_factory=lambda: {0: (0.06, 0.10), 1: (0.035, 0.06), 2: (0.015, 0.035), 3: (0.002, 0.012)})
    level_overrides: dict = field(default_factory=lambda: {3: dict(n_extra=(3, 3), max_free_yaws=0)})
    min_gap_others: float = 0.012
    start_offset: float = 0.06
    start_z: tuple = (0.20, 0.26)
    start_yaw_deg: tuple = (-90.0, 90.0)
    settle_s: float = 1.0
    vis_min_frac: float = 0.5
    vis_min_px: int = 60
    grasp_min_yaws: int = 2


SPLIT_SEEDS = {"real": {"train": 101, "val": 202, "test": 303}, "substitute": {"train": 1101, "val": 1202, "test": 1303}}


def _visible(P, k, cam):
    """Pixels of object k visible in cam (everything drawn) and when only k is drawn (unoccluded)."""
    m = P.m
    _, _, gid = P.render(cam, depth=False, seg=True)
    tg = (gid >= 0) & (P.root[np.clip(gid, 0, None)] == P.world.ob[k])
    vis = int(tg.sum())
    g0 = m.geom_group.copy()
    try:
        m.geom_group[P.root != P.world.ob[k]] = 5        # (MjvOption shows groups 0-2 only)
        _, _, gid2 = P.render(cam, depth=False, seg=True)
    finally:
        m.geom_group[:] = g0
    full = int(((gid2 >= 0) & (P.root[np.clip(gid2, 0, None)] == P.world.ob[k])).sum())
    return vis, full


def sample(P, cfg: TaskCfg, split, seed, level, idx, max_tries=60):
    """-> (layout dict, None) or (None, last rejection reason)."""
    import oracle
    w, m, d = P.world, P.m, P.d
    lo, hi = cfg.level_gaps[level]
    ov = getattr(cfg, "level_overrides", {}).get(level, {})       # (level 3: boxed in, config.TaskCfg)
    n_extra = tuple(ov.get("n_extra", cfg.n_extra))
    why = "?"
    for sub in range(max_tries):
        rng = np.random.default_rng([seed, level, idx, sub])
        target = str(rng.choice(cfg.targets))
        other = [t for t in cfg.targets if t != target]
        pool = [k for k in cfg.distractor_pool if k not in other and k != target and k not in cfg.color_conflicts.get(target, [])]
        extra = list(rng.choice(pool, int(rng.integers(n_extra[0], n_extra[1] + 1)), replace=False))
        dis = other + extra
        rng.shuffle(dis)
        c = rng.uniform([cfg.center_x[0], cfg.center_y[0]], [cfg.center_x[1], cfg.center_y[1]])
        pose = {target: (c[0] + rng.uniform(-0.015, 0.015), c[1] + rng.uniform(-0.015, 0.015), rng.uniform(0, 2 * math.pi))}
        polys = {target: P.outline(target, *pose[target])}
        th0 = rng.uniform(0, 2 * math.pi); n = len(dis); ok = True
        gaps = {}
        for i, k in enumerate(dis):
            placed = False
            for _ in range(40):
                th = th0 + 2 * math.pi * i / n + rng.uniform(-0.5, 0.5) * math.pi / n
                gap = rng.uniform(lo, hi); yaw = rng.uniform(0, 2 * math.pi)
                u = np.array([math.cos(th), math.sin(th)])
                a_, b_ = 0.0, 0.40                     # bisection on the distance along th for the wanted gap
                for _ in range(30):
                    r = (a_ + b_) / 2
                    g = poly_gap(polys[target], P.outline(k, *(np.array(pose[target][:2]) + r * u), yaw))
                    a_, b_ = (r, b_) if g < gap else (a_, r)
                xy = np.array(pose[target][:2]) + b_ * u
                poly = P.outline(k, xy[0], xy[1], yaw)
                if not (cfg.bounds_x[0] <= poly[:, 0].min() and poly[:, 0].max() <= cfg.bounds_x[1]
                        and cfg.bounds_y[0] <= poly[:, 1].min() and poly[:, 1].max() <= cfg.bounds_y[1]):
                    continue
                if any(poly_gap(poly, polys[o]) < cfg.min_gap_others for o in polys if o != target):
                    continue
                pose[k] = (xy[0], xy[1], yaw); polys[k] = poly; gaps[k] = poly_gap(poly, polys[target]); placed = True
                break
            if not placed:
                ok = False; why = f"no room for {k}"; break
        tp = polys[target]
        if ok and not (cfg.bounds_x[0] <= tp[:, 0].min() and tp[:, 0].max() <= cfg.bounds_x[1]
                       and cfg.bounds_y[0] <= tp[:, 1].min() and tp[:, 1].max() <= cfg.bounds_y[1]):
            ok = False; why = "target out of bounds"
        if not ok:
            continue
        # the world: objects placed, the hand at its start pose, settled
        P.reset_world(active=pose)
        for k, (x, y, yaw) in pose.items():
            P.put(k, x, y, yaw)
        st_tip = np.r_[np.array(pose[target][:2]) + rng.uniform(-cfg.start_offset, cfg.start_offset, 2), rng.uniform(*cfg.start_z)]
        st_yaw = math.radians(rng.uniform(*cfg.start_yaw_deg))
        q = P.teleport_arm(st_tip, st_yaw)
        if q is None:
            why = "start pose unreachable"; continue
        P.mj.mj_forward(m, d)
        ob = {w.ob[k] for k in pose}
        if any(c.dist < -0.001 and P.root[c.geom1] != P.root[c.geom2] and (P.root[c.geom1] in ob or P.root[c.geom2] in ob)
               and (P.root[c.geom1] in ob or P.root[c.geom1] in P.arm_b) and (P.root[c.geom2] in ob or P.root[c.geom2] in P.arm_b)
               for c in d.contact[:d.ncon]):
            why = "objects (or the hand) interpenetrate"; continue
        p0 = {k: w.pose(k)[0].copy() for k in pose}
        P.settle(cfg.settle_s)
        bad = None
        for k in pose:
            p, R = w.pose(k)
            if np.linalg.norm(p - p0[k]) > 0.005:
                bad = f"{k} moved {np.linalg.norm(p - p0[k]) * 1000:.0f} mm settling"; break
            if math.degrees(math.acos(min(1.0, R[2, 2]))) > 5:
                bad = f"{k} tilted settling"; break
        if bad:
            why = bad; continue
        # the target visible from the scene camera (the hand at its start included)
        vis, full = _visible(P, target, "d415")
        if vis < cfg.vis_min_px or vis < cfg.vis_min_frac * max(full, 1):
            why = f"target {vis}/{full} px visible"; continue
        wvis, wfull = _visible(P, target, "L_wrist")
        # graspable without moving anything: clutter_env's kinematic grasp check, oracle's choice
        P.target = target; P.L = {"objs": list(pose)}
        free, ntried = P.kin_grasps(target, yaws=18, widths=(0.080,), dz=(0.0,), margin=0.004)
        n_free = len({round(f[0], 4) for f in free})
        if "max_free_yaws" in ov:
            if n_free > ov["max_free_yaws"]:
                why = f"{n_free} free grasps (boxed-in level: at most {ov['max_free_yaws']})"; continue
        elif n_free < cfg.grasp_min_yaws:
            why = f"{len(free)} free grasps"; continue
        g = oracle.best_grasp(P, target)
        L = dict(split=split, seed=seed, level=level, idx=idx, sub=sub, target=target, distractors=dis,
                 objs={k: d.qpos[w.oq[k]:w.oq[k] + 7].round(6).tolist() for k in pose},
                 rest_com_z={k: round(P.com_z(k), 5) for k in pose},
                 start=dict(tip=st_tip.round(5).tolist(), yaw=round(st_yaw, 5), q=np.asarray(q).round(6).tolist()),
                 arm_q=d.qpos[w.qadr].round(6).tolist(), center=c.round(4).tolist(),
                 gaps_mm={k: round(v * 1000, 1) for k, v in gaps.items()},
                 vis_scene=[vis, full], vis_wrist=[wvis, wfull], free_yaws=len({round(f[0], 4) for f in free}),
                 oracle_grasp=None if g is None else [round(float(g[0]), 4), round(float(g[1]), 4)])
        return L, None
    return None, why


def _worker(job):
    object_set, split, seed, level, idx = job
    global _P
    if "_P" not in globals():
        from .scene import use_object_set
        use_object_set(object_set)
        from .picktwin import PickTwin
        _P = PickTwin(render=True)
    cfg = TaskCfg()
    t0 = time.time()
    L, why = sample(_P, cfg, split, seed, level, idx)
    if L is not None:
        L["object_set"] = object_set
    return idx, L, why, time.time() - t0


def generate(object_set, split, level, n, workers=32, log=print):
    from .scene import OBJECT_SETS
    out = paths.DATA / OBJECT_SETS[object_set]["banks"]
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{split}_L{level}.jsonl"
    seed = SPLIT_SEEDS[object_set][split]
    jobs = [(object_set, split, seed, level, i) for i in range(n)]
    t0 = time.time(); got, fails = {}, []
    with get_context("spawn").Pool(workers) as pool:
        for idx, L, why, dt in pool.imap_unordered(_worker, jobs, chunksize=1):
            (fails.append((idx, why)) if L is None else got.__setitem__(idx, L))
            if log and (len(got) + len(fails)) % 100 == 0:
                log(f"  {len(got)} ok, {len(fails)} failed, {time.time() - t0:.0f} s")
    with open(f, "w") as fo:
        for i in sorted(got):
            fo.write(json.dumps(got[i]) + "\n")
    subs = [L["sub"] for L in got.values()]
    json.dump(dict(cfg=asdict(TaskCfg()), object_set=object_set, split=split, level=level, seed=seed, n=n, ok=len(got),
                   failed=fails), open(f.with_suffix(".meta.json"), "w"), indent=1, default=list)
    return dict(path=str(f), ok=len(got), n=n, failed=len(fails), resamples=float(np.mean(subs)) if subs else None,
                wall_s=round(time.time() - t0, 1), fails=fails[:5])
