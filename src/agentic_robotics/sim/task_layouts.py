"""Layout banks for the task families other than clutter-pick (tasks.py): random scene specs, each checked and settled
by sim/custom.py's builder (the same rules as the agent's own scenes and the clutter generator), kept when valid.

  pick       the target (tasks.py targets) + 0-2 others, 6-12 cm gaps (no red distractor for the red tape: the clutter
             generator's colour rule)
  place      the open white box (lid 100-120 deg) + the target 6-14 cm outside it + 0-1 other
  close-box  the white box, lid open 70-120 deg, + 0-2 others >= 8 cm away
  press      the kettle at any yaw whose lid-release button the hand reaches from above + 0-2 others
  insert     the stand with three sticks (their tips reachable from above) + the cylinder upright 8-16 cm away + 0-1 other
  pack       the open white box (lid 100-120 deg) + 2-3 of its targets 16-28 cm from its middle, >= 9 cm apart, + 0-1 other
  clutter-pick-any  the target (one of place-box's five) + 2-4 others 1.5-6 cm from its outline (at least one within
             3.5 cm), a straight-down grasp still free (the target chosen per layout index)
  place-box  the open white box (lid 100-120 deg) + the target + 1-3 others anywhere on the free table, 16-32 cm from the
             box's middle, >= 9 cm apart (the target fixed per layout index, as pack's objects); the arm at its home
             joints (as on the real robot), not over the target; place-box-new the same
Splits train / val / test, 300 / 60 / 100 layouts (level 0 only), seeds per task and split (SEEDS).

    bin/ar layouts --task press --split train --n 300"""
from __future__ import annotations

import json
import math
import time
from multiprocessing import get_context

import numpy as np

from .. import paths, tasks as TK

SEEDS = {"pick": 2101, "place": 2201, "close-box": 2301, "press": 2401, "insert": 2501, "pack": 2601, "place-box": 2701,
         "place-box-new": 2801, "clutter-pick-any": 2901}
SPLIT_OFF = {"train": 0, "val": 10, "test": 20, "evalset": 30, "evalhidden": 40}   # (evalset / evalhidden: candidates
N_DEFAULT = {"train": 300, "val": 60, "test": 100, "evalset": 80, "evalhidden": 80}  # of evaluation/evalsets.py)
AREA_X, AREA_Y = (-0.43, -0.18), (-0.13, 0.17)
RED_CONFLICTS = {"tape_red": ["glue", "screwdriver"]}


def _rand_xy(rng, margin=0.0):
    return [float(rng.uniform(AREA_X[0] + margin, AREA_X[1] - margin)), float(rng.uniform(AREA_Y[0] + margin, AREA_Y[1] - margin))]


def _near(rng, c, r_lo, r_hi):
    a = rng.uniform(0, 2 * math.pi); r = rng.uniform(r_lo, r_hi)
    return [float(c[0] + r * math.cos(a)), float(c[1] + r * math.sin(a))]


HALF_ANY = {"screwdriver": 0.10, "cable": 0.07}


def _free_spot(rng, objs, b, k):
    """(place-box) anywhere on the free table: clear of the box and its open lid, >= 9 cm from the others; the long
    objects kept further from the area's edges (pack's rule)."""
    mg = {"screwdriver": 0.10, "cable": 0.06}.get(k, 0.035)
    for _ in range(200):
        c = [float(rng.uniform(-0.49 + mg, -0.12 - mg)), float(rng.uniform(-0.18 + mg, 0.21 - mg))]
        if 0.16 <= math.dist(c, b) <= 0.32 and all(math.dist(c, objs[o][:2]) >= 0.09 for o in objs if o != "box_white"):
            return c
    return _near(rng, b, 0.16, 0.28)


def spec_for(task, rng, rng_choice=None):
    """rng_choice (pack): the objects' choice, fixed per layout index, so that a layout redrawn for a bad placement keeps
    its objects (else the long ones, the screwdriver and the cable, would be under-represented)."""
    td = TK.get(task)
    pool = ["tape_red", "tape_yellow", "tape_white", "hole_block", "cable", "screwdriver", "glue", "sticky_pad"]
    yaw = lambda: float(rng.uniform(0, 360))
    objs, joints = {}, {}
    if task == "pick":
        t = str(rng.choice(td["targets"])); c = _rand_xy(rng, 0.04); objs[t] = c + [yaw()]
        others = [k for k in pool if k != t and k not in RED_CONFLICTS.get(t, [])]
        for k in rng.choice(others, int(rng.integers(0, 3)), replace=False):
            objs[str(k)] = _near(rng, c, 0.10, 0.18) + [yaw()]
    elif task == "place":
        t = str(rng.choice(td["targets"])); b = [float(rng.uniform(-0.40, -0.26)), float(rng.uniform(-0.06, 0.12))]
        objs["box_white"] = b + [yaw()]; joints["box_lid_deg"] = float(rng.uniform(100, 120))
        objs[t] = _near(rng, b, 0.17, 0.25) + [yaw()]
        others = [k for k in pool if k != t and k not in RED_CONFLICTS.get(t, [])]
        if rng.random() < 0.5:
            objs[str(rng.choice(others))] = _near(rng, objs[t][:2], 0.10, 0.16) + [yaw()]
    elif task == "close-box":
        t = "box_white"; b = [float(rng.uniform(-0.38, -0.26)), float(rng.uniform(-0.06, 0.10))]
        objs[t] = b + [yaw()]; joints["box_lid_deg"] = float(rng.uniform(70, 120))
        for k in rng.choice(pool, int(rng.integers(0, 3)), replace=False):
            objs[str(k)] = _near(rng, b, 0.18, 0.26) + [yaw()]
    elif task == "pack":
        b = [float(rng.uniform(-0.37, -0.27)), float(rng.uniform(-0.05, 0.09))]
        objs["box_white"] = b + [yaw()]; joints["box_lid_deg"] = float(rng.uniform(100, 120))
        rc = rng_choice if rng_choice is not None else rng
        ts = [str(k) for k in rc.choice(td["targets"], int(rc.integers(2, 4)), replace=False)]
        others = [k for k in pool if k not in ts and k not in sum((RED_CONFLICTS.get(t_, []) for t_ in ts), [])]
        extra = [str(rc.choice(others))] if rc.random() < 0.4 else []

        HALF = {"screwdriver": 0.10, "cable": 0.06}           # (long objects kept further from the area's edges)

        def free_spot(k):                                      # (anywhere on the free table: clear of the box and
            mg = HALF.get(k, 0.035)                            # its open lid, >= 9 cm from the others)
            for _ in range(200):
                c = [float(rng.uniform(-0.49 + mg, -0.12 - mg)), float(rng.uniform(-0.18 + mg, 0.21 - mg))]
                if 0.16 <= math.dist(c, b) <= 0.32 and all(math.dist(c, objs[o][:2]) >= 0.09 for o in objs if o != "box_white"):
                    return c
            return _near(rng, b, 0.16, 0.28)
        for k in ts + extra:
            objs[k] = free_spot(k) + [yaw()]
        sp = dict(target=ts[0], targets=ts, objects=objs, joints=joints)
        return sp
    elif task == "clutter-pick-any":
        rc = rng_choice if rng_choice is not None else rng
        t = str(rc.choice(td["targets"])); c = _rand_xy(rng, HALF_ANY.get(t, 0.05)); objs[t] = c + [yaw()]
        others = [k for k in pool if k != t and k not in RED_CONFLICTS.get(t, [])]
        for k in rc.choice(others, int(rc.integers(2, 5)), replace=False):
            objs[str(k)] = _near(rng, c, 0.06, 0.13) + [yaw()]
        return dict(target=t, objects=objs, arm_start="home")
    elif task in ("place-box", "place-box-new"):
        b = [float(rng.uniform(-0.37, -0.27)), float(rng.uniform(-0.05, 0.09))]
        objs["box_white"] = b + [yaw()]; joints["box_lid_deg"] = float(rng.uniform(100, 120))
        rc = rng_choice if rng_choice is not None else rng
        t = str(rc.choice(td["targets"]))
        others = [k for k in pool if k != t and k not in RED_CONFLICTS.get(t, [])]
        extra = [str(k) for k in rc.choice(others, int(rc.integers(1, 4)), replace=False)]
        for k in [t] + extra:
            objs[k] = _free_spot(rng, objs, b, k) + [yaw()]
        return dict(target=t, objects=objs, joints=joints, arm_start="home")   # (as the real robot starts)
    elif task == "press":
        t = "kettle"; c = [float(rng.uniform(-0.38, -0.24)), float(rng.uniform(-0.06, 0.12))]; objs[t] = c + [yaw()]
        for k in rng.choice(pool, int(rng.integers(0, 3)), replace=False):
            objs[str(k)] = _near(rng, c, 0.16, 0.24) + [yaw()]
    elif task == "insert":
        t = "wood_cyl"; s = [float(rng.uniform(-0.40, -0.24)), float(rng.uniform(-0.06, 0.12))]
        objs["peg_stand"] = s + [yaw()]; objs[t] = _near(rng, s, 0.12, 0.18) + [yaw()]
        if rng.random() < 0.5:
            objs[str(rng.choice(pool))] = _near(rng, objs[t][:2], 0.10, 0.16) + [yaw()]
    else:
        raise ValueError(task)
    sp = dict(target=t, objects=objs)
    if joints:
        sp["joints"] = joints
    return sp


def reachable_from_above(P, points, dz=0.08):
    """Each point reachable by the fingertips straight down from dz above, at some hand yaw (the robot's IK + limits)."""
    from .twin import B, TIP
    S = P.S
    for p in points:
        ok = False
        for yw in np.linspace(0, math.pi, 7, endpoint=False):
            R = B.down(yw)
            q, e = S.ik(np.asarray(p, float) + [0, 0, dz] - R[:, 2] * TIP, R, S.q_home)
            if e < 0.002:
                ok = True; break
        if not ok:
            return False
    return True


def sample(P, task, seed, idx, max_tries=60):
    from .custom import SceneError, build
    why = "?"
    for sub in range(max_tries):
        rng = np.random.default_rng([seed, idx, sub])
        spec = spec_for(task, rng, np.random.default_rng([seed, idx, 7]))
        try:
            L, rep = build(P, spec, task)
        except SceneError as e:
            why = str(e); continue
        if task == "clutter-pick-any":                           # (clutter: close neighbours, still graspable)
            g = sorted(rep["gaps_to_target_mm"].values())
            if not g or g[0] < 15 or g[0] > 35 or g[-1] > 60 or not L.get("free_yaws"):
                why = f"clutter gaps {g[:3]} mm / free grasp directions {L.get('free_yaws')}"; continue
        if task == "press":
            bp = P.d.xpos[P.m.body("obj_kettle_button").id]
            if not reachable_from_above(P, [bp]):
                why = "button out of reach from above"; continue
        if task == "insert":
            tips = []
            for i in range(3):
                g = P.m.geom(f"obj_peg_stand_peg{i}").id
                tips.append(P.d.geom_xpos[g] + [0, 0, P.m.geom_size[g][1]])
            if not reachable_from_above(P, tips, dz=0.06):
                why = "stick tips out of reach from above"; continue
        if rep.get("target_visible_scene_px", [0])[0] < 60:
            why = "target hidden from the scene camera"; continue
        L.update(split=None, seed=seed, level=0, idx=idx, sub=sub, report=rep)
        L.pop("custom", None)
        return L, None
    return None, why


def _worker(job):
    task, split, seed, idx = job
    global _P
    if "_P" not in globals():
        from .scene import use_object_set, world_class
        os_ = TK.get(task)["object_set"]
        use_object_set(os_)
        from .picktwin import PickTwin
        _P = PickTwin(render=True, world_cls=world_class(os_))
    t0 = time.time()
    L, why = sample(_P, task, seed, idx)
    if L is not None:
        L["split"] = split
    return idx, L, why, time.time() - t0


def generate(task, split, n=None, workers=8, log=print):
    td = TK.get(task)
    n = n or N_DEFAULT[split]
    seed = SEEDS[task] + SPLIT_OFF[split]
    if n <= 0:
        return dict(ok=0, n=0)
    out = paths.DATA / td["banks"]; out.mkdir(parents=True, exist_ok=True)
    f = out / f"{split}_L0.jsonl"
    jobs = [(task, split, seed, i) for i in range(n)]
    t0 = time.time(); got, fails = {}, []
    with get_context("spawn").Pool(workers) as pool:
        for idx, L, why, dt in pool.imap_unordered(_worker, jobs, chunksize=1):
            (fails.append((idx, why)) if L is None else got.__setitem__(idx, L))
            if log and (len(got) + len(fails)) % 50 == 0:
                log(f"  {len(got)} ok, {len(fails)} failed, {time.time() - t0:.0f} s")
    with open(f, "w") as fo:
        for i in sorted(got):
            fo.write(json.dumps(got[i]) + "\n")
    json.dump(dict(task=task, split=split, seed=seed, n=n, ok=len(got), failed=fails), open(f.with_suffix(".meta.json"), "w"), indent=1)
    return dict(path=str(f), ok=len(got), n=n, failed=len(fails), wall_s=round(time.time() - t0, 1), fails=fails[:3])
