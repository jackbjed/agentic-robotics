"""A scripted policy for rl/clutter_env.py's primitive mode that reads the twin's true state (11-07): the check that
the environment can be solved through its own action space, demonstrations (behaviour cloning / RL from demos), and a
baseline for the learned policies.
  holding the target                 place it at the box's middle
  holding something else             place it on a free table spot (in reach, > 9 cm from everything, off the pile)
  the target has a free grasp        grasp it there (ClutterEnv.kin_grasps: the arm's geometry touches nothing else)
  else, a neighbour has a free grasp grasp the nearest neighbour (then put aside)
  else                               push the nearest neighbour away from the target (from its far side... none:
                                     the target out of the pile, from the side facing the pile's middle)

    MUJOCO_GL=egl ~/miniforge3/envs/teleavatar_client/bin/python -I code/rl/oracle.py --bank pile_test --layouts 0-23 \
        --workers 12 [--save-demos out/rl_demos/oracle_test.npz]
"""
from __future__ import annotations

import os
import sys
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clutter_env as E  # noqa: E402
import clutter_bench as CB  # noqa: E402


def neighbours(env, k, r=0.03):
    """Objects whose collision geometry comes within r of object k's (the twin, margins), nearest first."""
    w = env.world; m, d, mj = w.m, w.d, w.mj
    gk = np.nonzero(env.root == w.ob[k])[0]
    margin0 = m.geom_margin.copy()
    m.geom_margin[gk] = r; mj.mj_forward(m, d)
    near = {}
    for c in d.contact[:d.ncon]:
        r1, r2 = env.root[c.geom1], env.root[c.geom2]
        if w.ob[k] in (r1, r2):
            o = r2 if r1 == w.ob[k] else r1
            for kk in env.obj_keys:
                if w.ob[kk] == o and kk != k:
                    near[kk] = min(near.get(kk, 1.0), float(c.dist))
    m.geom_margin[:] = margin0; mj.mj_forward(m, d)
    return sorted(near, key=near.get)


def corners(env, k):
    w = env.world; m, d = w.m, w.d
    pts = []
    for g in np.nonzero(env.root == w.ob[k])[0]:
        if not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        c, Rg = d.geom_xpos[g], d.geom_xmat[g].reshape(3, 3)
        ctr, half = m.geom_aabb[g].reshape(2, 3)
        pts += [c + Rg @ (ctr + half * np.array([sx, sy, sz])) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    return np.array(pts)


def best_grasp(env, k, tried=()):
    free, n = env.kin_grasps(k, yaws=18, widths=(0.060, 0.080), dz=(0.0,), margin=0.004)
    free = [f for f in free if not any(abs(((f[0] - t + math.pi / 2) % math.pi) - math.pi / 2) < math.radians(15) for t in tried)]
    if not free:
        return None
    by = {}
    for yaw, zt, wd in free:                                         # (the middle of the widest run of free yaws)
        by.setdefault(round(yaw, 4), []).append(wd)
    yaws = sorted(by)
    run = max(((y, sum(1 for y2 in yaws if abs(((y2 - y + math.pi / 2) % math.pi) - math.pi / 2) < math.pi / 18 * 1.6)) for y in yaws),
              key=lambda t: t[1])[0]
    return run, min(by[run])


def hand_free(env, x, y, z, yaw_closing, width, margin=0.003):
    """The arm by IK with the fingertips at (x, y, z), closing direction yaw, opening width: touches nothing (margin)."""
    w, S = env.world, env.S; m, d, kd = w.m, w.d, env.kin
    R = env._hand(x, y, z, yaw_closing)
    if R is None:
        return False
    q, e = S.ik(np.r_[x, y, z] - R[:, 2] * E.TIP, R, S.q_home)
    if e > 0.002:
        return False
    arm_g = np.array([m.geom_bodyid[g] in env.arm_b for g in range(m.ngeom)])
    margin0 = m.geom_margin.copy(); m.geom_margin[arm_g] = margin
    try:
        kd.qpos[:] = d.qpos; kd.qpos[w.qadr] = q; kd.qpos[w.fing] = width / 2
        w.mj.mj_kinematics(m, kd); w.mj.mj_collision(m, kd)
        for c in kd.contact[:kd.ncon]:
            if c.dist < margin - 0.0003 and ((m.geom_bodyid[c.geom1] in env.arm_b) != (m.geom_bodyid[c.geom2] in env.arm_b)):
                return False
        return True
    finally:
        m.geom_margin[:] = margin0


def plan_push(env, k, away_from, tried):
    """Push object k: every 30 deg, the closed fingers (across the push) started beside it on the far side, 6 mm over
    the table, free; the direction scored by how far from away_from it goes and the free run ahead. -> (x, y, yaw, p)."""
    w = env.world
    P = corners(env, k); c = P[:, :2].mean(0)
    others = np.array([CB.obj_extent(w, o)[:2] for o in env.obj_keys if o != k and o in env.L["objs"]] + [w.pose("box_white")[0][:2]])
    best = None
    for a in np.radians(np.arange(0, 360, 30)):
        if any(abs(((a - t + math.pi) % (2 * math.pi)) - math.pi) < math.radians(20) for t in tried):
            continue
        u = np.array([math.cos(a), math.sin(a)])
        r_back = float(((P[:, :2] - c) @ -u).max())
        s = c - u * (r_back + 0.013 + 0.006)
        end = c + u * 0.08
        if not (E.X_RANGE[0] + 0.04 < end[0] < E.X_RANGE[1] - 0.04 and E.Y_RANGE[0] + 0.04 < end[1] < E.Y_RANGE[1] - 0.04):
            continue
        if CB.in_box_xy(w, np.r_[end, 0], slack=0.03):
            continue
        if not hand_free(env, s[0], s[1], E.PUSH_Z, a + math.pi / 2, 0.0):
            continue
        ahead = [np.linalg.norm(o - c) for o in others if (o - c) @ u > 0 and abs(np.cross(u, o - c)) < 0.05]
        score = (min(ahead) if ahead else 0.3) + 0.5 * float(u @ (c - away_from) / max(np.linalg.norm(c - away_from), 1e-6))
        if best is None or score > best[0]:
            best = (score, s, a)
    if best is None:
        return None
    return best[1][0], best[1][1], best[2], (0.07 - 0.03) / 0.09


def free_spot(env, rng, avoid_xy):
    w = env.world
    P = np.array([w.pose(k)[0][:2] for k in env.obj_keys] + [w.pose("box_white")[0][:2]])
    for _ in range(400):
        p = rng.uniform([E.X_RANGE[0] + 0.05, E.Y_RANGE[0] + 0.05], [E.X_RANGE[1] - 0.05, E.Y_RANGE[1] - 0.05])
        if not 0.38 < np.linalg.norm(p - w.base[:2]) < 0.62:
            continue
        if np.linalg.norm(P - p, axis=1).min() < 0.09 or CB.in_box_xy(w, np.r_[p, 0], slack=0.05):
            continue
        if np.linalg.norm(p - avoid_xy) < 0.15:
            continue
        return p
    return None


def act(env, rng):
    """-> (action vector, why). Remembers (env._tried) what failed this episode: a grasp yaw on an object, a push
    direction, so it does not repeat it."""
    w = env.world; tg = env.target
    if env.n_steps == 0:                                               # (a new episode in the same env)
        env._tried, env._last = {}, None
    T_ = env.__dict__.setdefault("_tried", {})
    last = env.__dict__.get("_last")
    if last is not None and env.events and env.events[-1].strip() in ("-> empty", "-> blocked", "-> unreachable", "-> stop", "-> contact"):
        T_.setdefault(last[:2], []).append(last[2])
    env._last = None
    X = CB.obj_extent(w, tg)
    if env.holding:
        if env.held == tg or env.held is None:
            bp, bR = w.pose("box_white")
            yaw = math.atan2(bR[1, 0], bR[0, 0])
            return env.encode("place", bp[0], bp[1], yaw, 0.5), "target -> box"
        p = free_spot(env, rng, X[:2])
        if p is None:
            p = X[:2] + 0.2 * (X[:2] - w.pose("box_white")[0][:2]) / max(1e-6, np.linalg.norm(X[:2] - w.pose("box_white")[0][:2]))
        return env.encode("place", p[0], p[1], 0.0, 0.5), f"{env.held} -> aside"
    g = best_grasp(env, tg, T_.get(("grasp", tg), ()))
    if g is not None:
        env._last = ("grasp", tg, g[0])
        return env.encode("grasp", X[0], X[1], g[0], (g[1] - 0.030) / 0.050), "grasp target"
    nb = [k for k in neighbours(env, tg) if k in env.L["objs"]]
    for k in nb:
        g = best_grasp(env, k, T_.get(("grasp", k), ()))
        if g is not None:
            Y = CB.obj_extent(w, k)
            env._last = ("grasp", k, g[0])
            return env.encode("grasp", Y[0], Y[1], g[0], (g[1] - 0.030) / 0.050), f"grasp neighbour {k}"
    pile = np.mean([CB.obj_extent(w, k)[:2] for k in env.L["objs"]], axis=0)
    for k, away in [(tg, pile)] + [(k, X[:2]) for k in nb]:
        pp = plan_push(env, k, away, T_.get(("push", k), ()))
        if pp is not None:
            env._last = ("push", k, pp[2])
            return env.encode("push", *pp), ("push target out" if k == tg else f"push {k} away")
    for k in [tg] + nb:                                               # (nothing planned: grasp anyway, a new yaw)
        Y = CB.obj_extent(w, k); tried = T_.get(("grasp", k), [])
        yaw = float(rng.uniform(0, math.pi)) if not tried else (tried[-1] + math.pi / 3)
        env._last = ("grasp", k, yaw)
        return env.encode("grasp", Y[0], Y[1], yaw, 1.0), f"grasp {k} (unplanned)"


def run(job):
    bank, li, seed, mode = job
    env = E.ClutterEnv(bank=bank)
    rng = np.random.default_rng([seed, li])
    obs, info = env.reset(seed=seed * 1000 + li, options=dict(layout=li))
    steps, ret = [], 0.0
    while True:
        a, why = act(env, rng)
        obs2, r, term, trunc, info = env.step(a)
        ret += r
        steps.append(dict(why=why, outcome=info["outcome"], r=round(r, 3), held=info["held"], dt=info["dt_sim"]))
        if term or trunc:
            break
    res = dict(layout=li, target=env.target, success=info["success"], ret=round(ret, 3), steps=steps, stops=info["stops"],
               sim_s=info["sim_s"], wall_s=info["wall_s"], judge=info["judge"], events=env.events)
    env.close()
    return res


def main():
    import argparse, json, time
    import multiprocessing as mp
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bank", default="pile_test")
    ap.add_argument("--layouts", default="0-23")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lo, hi = (int(v) for v in a.layouts.split("-")) if "-" in a.layouts else (int(a.layouts), int(a.layouts))
    jobs = [(a.bank, i, a.seed, "primitive") for i in range(lo, hi + 1)]
    t0 = time.time(); rows = []
    with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=1) as pool:   # (spawn: the parent's EGL display does not
                                                                                  # survive a fork)
        for r in pool.imap_unordered(run, jobs):
            rows.append(r)
            print(f"layout {r['layout']:3d} {r['target']:12s} {'SUCCESS' if r['success'] else 'fail   '} {len(r['steps']):2d} steps "
                  f"ret {r['ret']:+.2f} stops {r['stops']} sim {r['sim_s']:.0f} s wall {r['wall_s']:.0f} s | "
                  + " ; ".join(f"{s['why']}: {s['outcome']}" for s in r["steps"]), flush=True)
    n = len(rows); ok = sum(r["success"] for r in rows)
    print(f"\n{a.bank}: {ok}/{n} success; mean sim {np.mean([r['sim_s'] for r in rows]):.0f} s, wall {np.mean([r['wall_s'] for r in rows]):.0f} s "
          f"per episode; total wall {time.time() - t0:.0f} s with {a.workers} workers")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rows, default=str))


if __name__ == "__main__":
    main()
