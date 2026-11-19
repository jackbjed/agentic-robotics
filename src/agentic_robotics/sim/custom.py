"""Test scenes written by the learning agent (practice only). A scene file (YAML) places the objects:

    target: tape_red                  # the object the task names: tape_red | tape_yellow (what they are called in
                                      # this run's condition: the brief / docs/task_clutter_pick.md)
    objects:                          # object id: [x, y, yaw_deg]  (world frame, m / deg; docs/robot.md)
      tape_red:    [-0.30, 0.020, 0]
      tape_yellow: [-0.30, 0.075, 0]
      hole_block:  [-0.25, 0.020, 45]
    arm_start: [-0.30, 0.00, 0.22, 0] # optional: fingertip x, y, z (m), hand yaw (deg); default over the target, 22 cm up

Checks (the layout generator's, sim/layouts.py): 2-6 objects of the condition's set, the target among them; every
outline inside the work area (x -0.49..-0.12, y -0.18..0.21) and no two overlapping; the hand reachable at its start
(12-30 cm up); nothing interpenetrating; after 1 s of settling nothing moved > 2 cm, tilted > 20 deg or fell (moves
> 5 mm are reported). Reported: gaps to the target, the target's visibility (scene / wrist camera), the number of free
straight-down grasp directions (of 18; 0 = boxed in). -> a layout in the banks' format + a preview image.

    python -I -m agentic_robotics.sim.custom --scene S.yaml --object-set real --colour mujoco --out DIR"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import yaml

TARGETS = ("tape_red", "tape_yellow")
POOL = ("tape_red", "tape_yellow", "tape_white", "hole_block", "cable", "screwdriver", "glue", "sticky_pad")
POOL_TASKS = POOL + ("box_white", "kettle", "peg_stand", "wood_cyl")
JOINTS = {"box_lid_deg": "obj_box_white_lid_hinge"}               # friendly name -> joint (degrees in the file)
BOUNDS_X, BOUNDS_Y = (-0.49, -0.12), (-0.18, 0.21)


class SceneError(ValueError):
    pass


def parse(spec, task="clutter-pick"):
    from .. import tasks as TK
    td = TK.get(task)
    targets = tuple(td.get("targets", TARGETS))
    pool = POOL_TASKS if td["object_set"] == "tasks" else POOL + ("box_white",)
    if not isinstance(spec, dict):
        raise SceneError("a scene is a mapping with target, objects, [arm_start], [joints]")
    t = spec.get("target")
    if t not in targets:
        raise SceneError(f"task {task}: target must be one of {targets}")
    objs = spec.get("objects") or {}
    lo = 1 if task in ("pick", "close-box", "press") else 3 if task == "pack" else 2
    if not isinstance(objs, dict) or not lo <= len(objs) <= 6:
        raise SceneError(f"objects: {lo} to 6 entries id: [x, y, yaw_deg]")
    if t not in objs:
        raise SceneError("the target must be among the objects")
    for f in td.get("fixed", []):
        if f not in objs:
            raise SceneError(f"task {task}: the scene needs the {f}")
    pose = {}
    for k, v in objs.items():
        if k not in pool:
            raise SceneError(f"unknown object {k!r} (ids: {', '.join(pool)})")
        if not (isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v)):
            raise SceneError(f"{k}: [x, y, yaw_deg] numbers")
        pose[k] = (float(v[0]), float(v[1]), math.radians(float(v[2])))
    st = spec.get("arm_start")
    if st is not None and not (isinstance(st, (list, tuple)) and len(st) == 4 and all(isinstance(x, (int, float)) for x in st)):
        raise SceneError("arm_start: [x, y, z, yaw_deg]")
    joints = {}
    for jn, v in (spec.get("joints") or {}).items():
        if jn not in JOINTS or not isinstance(v, (int, float)):
            raise SceneError(f"joints: {', '.join(JOINTS)} (degrees)")
        if jn == "box_lid_deg" and not ("box_white" in pose and 0 <= v <= 120):
            raise SceneError("box_lid_deg: 0 (closed) to 120, with the box_white in the scene")
        joints[JOINTS[jn]] = math.radians(float(v))
    if "box_white" in pose and "obj_box_white_lid_hinge" not in joints:
        joints["obj_box_white_lid_hinge"] = math.radians(110.0 if task != "close-box" else 100.0)   # (open by default)
    return t, pose, st, joints


def build(P, spec, task="clutter-pick"):
    """-> (layout dict, report). P = a PickTwin with the task's object set. Raises SceneError."""
    from .picktwin import poly_gap
    w, m, d = P.world, P.m, P.d
    target, pose, st, joints = parse(spec, task)
    polys = {k: P.outline(k, *p) for k, p in pose.items()}
    for k, pl in polys.items():
        if not (BOUNDS_X[0] <= pl[:, 0].min() and pl[:, 0].max() <= BOUNDS_X[1] and BOUNDS_Y[0] <= pl[:, 1].min() and pl[:, 1].max() <= BOUNDS_Y[1]):
            raise SceneError(f"{k} is (partly) outside the work area x {BOUNDS_X}, y {BOUNDS_Y}")
    ks = list(pose)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            if poly_gap(polys[ks[i]], polys[ks[j]]) < 0.0005:
                raise SceneError(f"{ks[i]} and {ks[j]} overlap (outline gap {poly_gap(polys[ks[i]], polys[ks[j]]) * 1000:.1f} mm)")
    gaps = {k: round(poly_gap(polys[k], polys[target]) * 1000, 1) for k in ks if k != target}
    P.reset_world(active=pose)
    for k, (x, y, yaw) in pose.items():
        P.put(k, x, y, yaw)
    if hasattr(w, "latch"):                                       # (the kettle: latched)
        w.set_latch(True)
    for jn, v in joints.items():
        d.qpos[m.joint(jn).qposadr[0]] = v
    P.mj.mj_forward(m, d)
    if st is None:
        top = max(float(P.footprint(k)[1]) for k in pose if np.linalg.norm(np.array(pose[k][:2]) - pose[target][:2]) < 0.12)
        st_tip, st_yaw = np.r_[pose[target][:2], min(0.30, max(0.22, top + 0.08))], 0.0
    else:
        st_tip, st_yaw = np.array(st[:3], float), math.radians(float(st[3]))
    if not (0.12 <= st_tip[2] <= 0.30 and BOUNDS_X[0] - 0.05 <= st_tip[0] <= BOUNDS_X[1] + 0.05 and BOUNDS_Y[0] - 0.05 <= st_tip[1] <= BOUNDS_Y[1] + 0.05):
        raise SceneError("arm_start must be over the work area, fingertips 12-30 cm up")
    q = P.teleport_arm(st_tip, st_yaw)
    if q is None:
        raise SceneError("the hand cannot be at arm_start (out of the arm's reach / limits)")
    P.mj.mj_forward(m, d)
    ob = {w.ob[k] for k in pose}
    for c in d.contact[:d.ncon]:
        r1, r2 = P.root[c.geom1], P.root[c.geom2]
        if c.dist < -0.001 and r1 != r2 and (r1 in ob or r2 in ob) and (r1 in ob or r1 in P.arm_b) and (r2 in ob or r2 in P.arm_b):
            raise SceneError("objects (or the hand) interpenetrate")
    p0 = {k: w.pose(k)[0].copy() for k in pose}
    P.settle(1.0)
    moved, warn = {}, []
    for k in pose:
        p, R = w.pose(k)
        mv = float(np.linalg.norm(p - p0[k])); tilt = math.degrees(math.acos(min(1.0, R[2, 2])))
        moved[k] = round(mv * 1000, 1)
        if P.com_z(k) < -0.05:
            raise SceneError(f"{k} fell off the table while settling")
        if mv > 0.02 or tilt > 20:
            raise SceneError(f"{k} is not stable there (moved {mv * 1000:.0f} mm, tilted {tilt:.0f} deg while settling)")
        if mv > 0.005:
            warn.append(f"{k} moved {mv * 1000:.0f} mm settling")
    from .layouts import _visible
    vis, full = _visible(P, target, "d415"); wvis, wfull = _visible(P, target, "L_wrist")
    P.target = target; P.L = {"objs": list(pose)}
    if target in ("box_white", "kettle"):                         # (not grasped: no grasp count)
        n_free = None
    else:
        free, _ = P.kin_grasps(target, yaws=18, widths=(0.080,), dz=(0.0,), margin=0.004)
        n_free = len({round(f[0], 4) for f in free})
    others = [k for k in pose if k != target]
    targets = [target]
    if task == "pack":                                            # (several targets: all of them into the box)
        from .. import tasks as TK
        targets = list(spec.get("targets") or [target])
        allowed = TK.get(task)["targets"]
        if not 2 <= len(targets) <= 3 or len(set(targets)) != len(targets) or targets[0] != target or \
                any(k not in pose or k not in allowed for k in targets):
            raise SceneError(f"pack: targets = 2-3 distinct objects of the scene from {allowed}, the first = target")
    L = dict(split="custom", seed=None, level=-1, idx=-1, sub=0, target=target, targets=targets, distractors=others,
             objs={k: d.qpos[w.oq[k]:w.oq[k] + 7].round(6).tolist() for k in pose},
             rest_com_z={k: round(P.com_z(k), 5) for k in pose},
             start=dict(tip=np.round(st_tip, 5).tolist(), yaw=round(st_yaw, 5), q=np.asarray(q).round(6).tolist()),
             arm_q=d.qpos[w.qadr].round(6).tolist(), center=np.round(np.array(pose[target][:2]), 4).tolist(),
             gaps_mm=gaps, vis_scene=[vis, full], vis_wrist=[wvis, wfull], free_yaws=n_free, oracle_grasp=None,
             joints={jn: round(float(d.qpos[m.joint(jn).qposadr[0]]), 5) for jn in joints}, task=task, custom=spec)
    report = dict(ok=True, target=target, gaps_to_target_mm=gaps, moved_settling_mm=moved, warnings=warn,
                  target_visible_scene_px=[vis, full], target_visible_wrist_px=[wvis, wfull],
                  free_straight_down_grasp_directions=None if n_free is None else f"{n_free} of 18" + (" (boxed in)" if n_free == 0 else ""),
                  joints_deg={jn: round(math.degrees(v), 1) for jn, v in L["joints"].items()})
    return L, report


def preview(scene, path):
    """The scene camera's frame (the condition's colour, as the program would get it) | a top view without the robot
    (x, away from the robot, points DOWN the image; y to the right), written to path (PNG)."""
    import cv2
    rgb, _, _ = scene.camera("d415")
    tp = scene.xy0.get(scene.target, np.zeros(2))
    P = scene.P; m = P.m
    arm = np.array([m.body(int(b)).name.startswith(("L_", "R_")) for b in m.geom_bodyid])
    g0 = m.geom_group.copy(); m.geom_group[arm] = 5               # (the top view without the robot over it)
    try:
        top = P.free_view(np.r_[tp, 0.0], distance=0.45, azimuth=180.0, elevation=-89.0)
    finally:
        m.geom_group[:] = g0
    im = np.concatenate([cv2.resize(rgb, (480, 360)), cv2.resize(top, (480, 360))], 1)
    cv2.imwrite(str(path), cv2.cvtColor(im, cv2.COLOR_RGB2BGR))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True); ap.add_argument("--object-set", default="real")
    ap.add_argument("--colour", default="mujoco"); ap.add_argument("--out", required=True)
    ap.add_argument("--task", default="clutter-pick"); ap.add_argument("--mounts", default="twin")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    from .scene import ClutterPickScene
    try:
        spec = yaml.safe_load(Path(a.scene).read_text())
        sc = ClutterPickScene(colour=a.colour, object_set=a.object_set, task=a.task, mounts=a.mounts)
        L, rep = build(sc.P, spec, a.task)
        sc.reset_layout(L, 0)
        preview(sc, out / "preview.png")
        json.dump(L, open(out / "layout.json", "w"))
        rep["preview"] = "preview.png"
    except (SceneError, yaml.YAMLError, OSError) as e:
        rep = dict(ok=False, why=str(e))
    json.dump(rep, open(out / "report.json", "w"), indent=1)
    print(json.dumps(rep))
    import os, sys
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
