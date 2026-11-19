"""The fidelity conditions: the substitute objects keep masses; SAM3 and the compositor are really used (skipped when
our services are not running: bin/ar services start)."""
import numpy as np
import pytest

from agentic_robotics import services
from agentic_robotics.evaluation.suites import episode_seed


def test_substitute_masses_and_scene():
    import mujoco
    from agentic_robotics import paths
    from agentic_robotics.sim.scene import OBJECT_SETS
    m1 = mujoco.MjModel.from_xml_path(str(paths.VENDOR / "panda_twin/model/twin_6522_wedge.xml"))
    m2 = mujoco.MjModel.from_xml_path(str(paths.SRC / "agentic_robotics" / OBJECT_SETS["substitute"]["model"]))
    for k in ("tape_red", "tape_yellow", "hole_block", "cable", "screwdriver", "glue", "sticky_pad"):
        assert abs(m1.body("obj_" + k).mass[0] - m2.body("obj_" + k).mass[0]) < 1e-5, k      # (0.01 g: per-geom masses rounded to 1 ug)
    # geometry differs: no ring of wall boxes on the substitute target
    assert not any((m2.geom(g).name or "").startswith("obj_tape_red_w") for g in range(m2.ngeom))


@pytest.mark.skipif(not services.alive("compositor"), reason="compositor not running")
def test_splat_colour_differs_from_mujoco():
    from agentic_robotics.sim.scene import ClutterPickScene
    a = ClutterPickScene(colour="splat"); a.reset("val", 1, 3, episode_seed(13, 1, 3))
    rgb_a, dep_a, _ = a.camera("d415")
    rgb_b, _, _ = a.P.render("d415", depth=False, seg=False)[0], None, None
    assert np.abs(rgb_a.astype(int) - rgb_b.astype(int)).mean() > 10      # a real composite, not the MuJoCo frame
    assert np.isfinite(dep_a).mean() > 0.3                                 # depth still the D415 model's


@pytest.mark.skipif(not services.alive("sam3_text"), reason="sam3 not running")
def test_sam3_segments_the_target():
    from agentic_robotics.perception import sam3
    from agentic_robotics.sim.scene import ClutterPickScene
    s = ClutterPickScene(colour="mujoco"); s.reset("test", 0, 10, episode_seed(5, 0, 10))
    rgb, _, _ = s.camera("d415")
    res = sam3.segment(rgb, s.task()["object"])
    lab = s.labels_for(rgb); tid = s.P.obj_keys.index(s.target)
    assert res and (res[0][0] & (lab == tid)).sum() > 0.5 * res[0][0].sum()   # its best mask is mostly the target
