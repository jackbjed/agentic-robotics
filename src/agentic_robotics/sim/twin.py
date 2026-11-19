"""The vendored twin and skill library (vendor/SNAPSHOT.md: byte-identical copies of panda_twin, panda_twin_tracking),
imported with the settings the clutter-pick results were produced with. Import this module before anything else
from the vendored code: the vendored modules read these environment variables at import time.

  CLUTTER_GRIPPER=wedge   model twin_6522_wedge.xml: the real fin-ray fingers' outline (the robot's)
  TWIN_COMPLIANT=1        TwinArm with the real arm's joint impedance (not a stiff servo)
  BOX_REAL=0              the twin's own white box (the clutter scenes)
  CLUTTER_TIGHT=1         the pick skill's tightest grasp level (3 mm opening margin), as on the robot (11-05)
The world server renders with EGL (MUJOCO_GL=egl); the sandboxed skill process with OSMesa (MUJOCO_GL=osmesa, set by
execution.host before this import: it only draws the robot's own model)."""
from __future__ import annotations

import os
import sys

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("CLUTTER_GRIPPER", "wedge")
os.environ.setdefault("TWIN_COMPLIANT", "1")
os.environ.setdefault("BOX_REAL", "0")
os.environ.setdefault("CLUTTER_TIGHT", "1")
os.environ.setdefault("MUJOCO_GL", "egl")

from ..paths import VENDOR  # noqa: E402

PT_CODE = VENDOR / "panda_twin" / "code"
for _p in (PT_CODE / "rl", PT_CODE / "real", PT_CODE, VENDOR / "panda_twin_tracking"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import stack_bench as B  # noqa: E402
import task_bench as T  # noqa: E402
import task_box as TB  # noqa: E402
import task_fast as F  # noqa: E402
import clutter_bench as CB  # noqa: E402
import d415_model  # noqa: E402
from real_sim import RealSim, RobotStop  # noqa: E402
from panda_fk import load_rig  # noqa: E402

assert str(B.MODEL).startswith(str(VENDOR)), f"not the vendored model: {B.MODEL}"

TIP = T.FINGERTIP            # fingertip point = TCP + TIP along the hand's z (m)
DT = B.DT                    # control frame (1/30 s)
OPEN_W = B.OPEN_W            # per-finger opening when fully open (80 mm between the fingers)
CAMS = ("d415", "L_wrist")   # scene D415 (fixed) and wrist D415
MIN_Z = B.MIN_Z              # D415 minimum depth at 640x480 (0.275 m)


def skill_library():
    """The robot pick skill's modules (real_box -> track_run -> astra_skills_real), imported from the vendored copies.
    (Those files put ~/tdieudonne/panda_twin/code on sys.path themselves; importing them here first makes every later
    `import` of those names resolve to the vendored modules already loaded.)"""
    argv = sys.argv
    sys.argv = [argv[0]]                                   # (real_box / track_run read sys.argv at import time)
    try:
        import track  # noqa: F401
        import real_box  # noqa: F401
        import track_run  # noqa: F401
        import astra_skills_real as AS
    finally:
        sys.argv = argv
    for name in ("real_box", "track_run", "astra_skills_real", "track", "clutter_bench", "task_box"):
        f = getattr(sys.modules[name], "__file__", "")
        assert str(f).startswith(str(VENDOR)), f"{name} imported from outside the vendored copy: {f}"
    return AS
