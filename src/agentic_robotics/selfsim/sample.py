"""The agent's layout sampler (its sim/layouts.py), run INSIDE the program sandbox (no network, its sim/ read-only, one
writable directory), and the platform's check of what it returns (real2sim, docs/selfsim.md).

    python -I -m agentic_robotics.selfsim.sample --sim <sim dir> --family F --seeds 1,2,3 --out <file.json>

layouts.py: sample(task, rng) -> dict(
    target = the name of the object the task names (place-box, clutter-pick-any) or the box's name (close-box),
    place  = {body: [x, y, yaw] (resting on the table, upright) | [x, y, z, qw, qx, qy, qz], ...}: what is on the table,
    joints = {joint: value, ...} (e.g. the box's lid opening, rad))

  sample_layouts(sim_dir, family, seeds, work_dir) -> [layout | dict(error=...)]   (the sandboxed run)
  check_layout(L, decl, family) -> the layout with the target as a body (SimError: what is wrong)"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
import traceback
from pathlib import Path

import numpy as np

from .model import SimError


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True); ap.add_argument("--family", required=True)
    ap.add_argument("--seeds", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = []
    try:
        sys.path.insert(0, a.sim)
        spec = importlib.util.spec_from_file_location("layouts", str(Path(a.sim) / "layouts.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    except BaseException:
        json.dump([dict(error="sim/layouts.py failed to load:\n" + traceback.format_exc()[-1500:])] * len(a.seeds.split(",")),
                  open(a.out, "w"))
        return
    for s in a.seeds.split(","):
        try:
            L = mod.sample(a.family, np.random.default_rng(int(s)))
            out.append(_jsonable(L))
        except BaseException:
            out.append(dict(error=f"sample({a.family!r}, rng(seed {s})) raised:\n" + traceback.format_exc()[-1500:]))
    json.dump(out, open(a.out, "w"))


def sample_layouts(sim_dir, family, seeds, work_dir, timeout=300):
    """sim/layouts.py in the program sandbox -> [layout dict | dict(error=...)] (one per seed, unchecked)."""
    from .. import paths
    from ..execution import sandbox as sbx
    from ..execution.episode import base_env
    from . import hidden_paths
    work = Path(work_dir); work.mkdir(parents=True, exist_ok=True)
    f = work / "layouts.json"
    py = str(paths.ROOT / ".venv" / "bin" / "python")
    cmd = sbx.program_cmd([py, "-I", "-m", "agentic_robotics.selfsim.sample", "--sim", str(Path(sim_dir).resolve()),
                           "--family", family, "--seeds", ",".join(str(int(s)) for s in seeds), "--out", str(f)],
                          Path(sim_dir).resolve(), work.resolve(), work.resolve(), hide=hidden_paths())
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=base_env())
    except subprocess.TimeoutExpired:
        return [dict(error=f"sim/layouts.py: no answer in {timeout} s")] * len(seeds)
    if not f.exists():
        return [dict(error="sim/layouts.py: the sampler crashed: " + (r.stderr or r.stdout)[-1200:])] * len(seeds)
    return json.load(open(f))


def check_layout(L, decl, family):
    """-> dict(target=<body>, place={body: [3 or 7 numbers]}, joints={name: float}); SimError if it is not usable."""
    if not isinstance(L, dict):
        raise SimError(f"layout: a dict expected, got {type(L).__name__}")
    if L.get("error"):
        raise SimError(L["error"])
    if set(L) - {"target", "place", "joints", "notes"}:
        raise SimError(f"layout: unknown keys {sorted(set(L) - {'target', 'place', 'joints', 'notes'})}")
    names = dict(decl["objects"])
    box = decl.get("box")
    if box:
        names[box["name"]] = box["body"]
    bodies = set(names.values())
    t = L.get("target")
    if family == "close-box":
        if not box or t not in (box["name"], box["body"]):
            raise SimError(f"close-box: the target is the box ({box['name'] if box else 'no box declared'})")
    elif t not in decl["objects"] and t not in decl["objects"].values():
        raise SimError(f"{family}: target {t!r} is not one of the objects' names ({', '.join(decl['objects'])})")
    tb = names.get(t, t)
    place = L.get("place")
    if not isinstance(place, dict) or not place:
        raise SimError("layout: `place` (body -> pose) is missing or empty")
    out = {}
    for b, v in place.items():
        b = names.get(b, b)
        if b not in bodies:
            raise SimError(f"layout: {b!r} is not a declared body (objects, box)")
        try:
            a = [float(x) for x in v]
        except (TypeError, ValueError):
            raise SimError(f"layout: the pose of {b!r} is not numbers: {v!r}")
        if len(a) not in (3, 7) or not all(math.isfinite(x) for x in a):
            raise SimError(f"layout: the pose of {b!r} needs [x, y, yaw] or [x, y, z, qw, qx, qy, qz]: {v!r}")
        if abs(a[0]) > 1.5 or abs(a[1]) > 1.5 or (len(a) == 7 and not -0.5 < a[2] < 1.0):
            raise SimError(f"layout: {b!r} at {a[:3]} is far outside the table")
        if len(a) == 7:
            n = math.sqrt(sum(x * x for x in a[3:]))
            if n < 1e-6:
                raise SimError(f"layout: the quaternion of {b!r} is zero")
            a[3:] = [x / n for x in a[3:]]
        out[b] = a
    if tb not in out:
        raise SimError(f"layout: the target {t!r} is not placed")
    if family in ("place-box", "close-box") and box["body"] not in out:
        raise SimError(f"{family}: the box is not placed")
    joints = {}
    for j, v in (L.get("joints") or {}).items():
        try:
            joints[str(j)] = float(v)
        except (TypeError, ValueError):
            raise SimError(f"layout: joint {j!r} value {v!r} is not a number")
    return dict(target=tb, place=out, joints=joints)


if __name__ == "__main__":
    main()
