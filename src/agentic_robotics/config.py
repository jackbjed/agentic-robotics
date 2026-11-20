"""Task configurations -> episode specs (one place: the CLI, suites, practice, the learning loop).

A task config (configs/tasks/*.yaml) names its camera config (sensor preset, colour mode) and sets the perception
backend and the object set; together they are a fidelity condition (docs/fidelity.md):
  colour      mujoco (B, C) | splat (A: our compositor)          cameras/*.yaml
  object_set  real (A, B) | substitute (C)                       tasks/*.yaml
  perception  oracle (milestone 1) | sam3 (our SAM3 servers)     tasks/*.yaml
  mounts      twin (the twin's camera mounts) | calibrated (the real rig's calib.json poses)   cameras/*.yaml
  arm_start   (optional) home: every episode starts the arm at its home joints, as the real robot does   tasks/*.yaml"""
from __future__ import annotations

from . import paths


def task_config(path="configs/tasks/clutter_pick.yaml"):
    t = dict(paths.load_yaml(path))
    cams = paths.load_yaml(t["cameras"])
    t.update(preset=cams.get("preset", "d415"), colour_noise=cams.get("colour_noise", False),
             colour=cams.get("colour", "mujoco"), latency_s=cams.get("latency_s", [0.0, 0.0]),
             mounts=cams.get("mounts", "twin"))
    t.setdefault("object_set", "real"); t.setdefault("perception", "oracle")
    t["_path"] = str(path)
    return t


def condition(t):
    return dict(colour=t["colour"], object_set=t["object_set"], perception=t["perception"], preset=t["preset"],
                mounts=t.get("mounts", "twin"))


def episode_base(t, record=None, parity=False, vlm=None, library=True):
    """vlm: None (no model calls) or the experiment's vlm block + tag (+ cache_dir): perception/vlm.py.
    library: False = the program gets no robot skill library (ctx.library / ctx.perception None, its modules not
    importable): skills written from scratch (docs/skill_api_scratch.md)."""
    base = dict(scene=dict(preset=t["preset"], colour_noise=t["colour_noise"], colour=t["colour"], object_set=t["object_set"]),
                perception=t["perception"], robot_time_limit_s=t["robot_time_limit_s"],
                record=record or dict(video=True, observations="none"), parity=bool(parity),
                vlm=vlm if (vlm or {}).get("enabled") else None)
    if t.get("mounts", "twin") != "twin":                      # (absent = the twin's mounts: earlier records' specs)
        base["scene"]["mounts"] = t["mounts"]
    if t.get("arm_start"):                                     # (absent = the layout's own start pose)
        base["scene"]["arm_start"] = t["arm_start"]
    if not library:
        base["library"] = False
    if t.get("program_mirror"):                                # (real2sim: the program plans with the robot kit,
        base["program_mirror"] = t["program_mirror"]           # no scene in its own model; docs/real2sim.md)
    return base
