"""real2sim (docs/real2sim.md): the robot kit has no scene; an agent's reconstruction (a crude hand-made one:
tests/fixtures/selfsim_example) composes onto it, its layouts are sampled in the sandbox and checked, its world runs the
real world's success checks; programs plan with the kit and cannot read the scene's model files; the agent's docs give
no sizes; the real world's records carry no verdict."""
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from agentic_robotics import paths
from agentic_robotics.selfsim import KIT, hidden_paths
from agentic_robotics.selfsim.model import SimError, compose
from agentic_robotics.selfsim.sample import check_layout, sample_layouts

FX = paths.ROOT / "tests" / "fixtures" / "selfsim_example"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("selfsim")
    r = compose(FX, d / "model.xml")
    return dict(dir=d, model=str(d / "model.xml"), decl=r["decl"], info=r["info"])


def test_kit_has_no_scene():
    import mujoco
    m = mujoco.MjModel.from_xml_path(str(KIT))
    assert m.nlight == 0 and m.ntex == 0
    names = [m.geom(g).name for g in range(m.ngeom)]
    assert not any(n.startswith(("table_leg", "floor", "obj_tape", "obj_glue")) and not n.endswith("_ph") for n in names)
    assert not any("twin_6522" in (m.mesh(i).name or "") or m.mesh(i).name.startswith("obj_") for i in range(m.nmesh))
    for g in range(m.ngeom):                                     # (placeholders: invisible, no contact, tiny)
        b = m.body(m.geom_bodyid[g]).name
        if b.startswith("obj_") or names[g] == "table_top":
            assert m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0 and m.geom_rgba[g][3] == 0
            assert max(m.geom_size[g]) <= 0.004 + 1e-9
    txt = KIT.read_text()
    assert "damping=\"0.05\"" not in txt.split("obj_box_white")[1][:600]    # (no lid parameters)
    m0 = mujoco.MjModel.from_xml_path(str(paths.VENDOR / "panda_twin/model/twin_6522_wedge.xml"))
    for j in range(m0.njnt):                                     # (the robot at the same keyframe)
        n = m0.joint(j).name
        if n.startswith(("L_", "R_")):
            assert abs(m0.key("twin").qpos[m0.jnt_qposadr[j]] - m.key("twin").qpos[m.joint(n).qposadr[0]]) < 1e-6


def test_compose_and_refusals(built, tmp_path):
    assert set(built["info"]["bodies"]) == {"tape_red", "tape_yellow", "tape_white", "screwdriver", "block", "box"}
    bad = tmp_path / "bad"
    for edit, why in ((lambda s: s.replace("<asset>", "<option timestep=\"0.01\"/><asset>"), "not allowed"),
                      (lambda s: s.replace('name="table"', 'name="L_table"'), "reserved"),
                      (lambda s: s.replace("<asset>", '<asset><mesh name="m" file="../../../x.obj"/>'), "outside sim/"),
                      (lambda s: s.replace('<body name="block" pos="0.5 0.3 0.02"><freejoint/>', '<body name="block" pos="0.5 0.3 0.02">'),
                       "freejoint")):
        if bad.exists():
            shutil.rmtree(bad)
        shutil.copytree(FX, bad)
        (bad / "scene.xml").write_text(edit((FX / "scene.xml").read_text()))
        with pytest.raises(SimError, match=why):
            compose(bad, tmp_path / "m.xml")


def test_layouts_sampled_in_sandbox_and_checked(built, tmp_path):
    Ls = sample_layouts(FX, "place-box", [1, 2, 3], tmp_path / "s")
    L = [check_layout(x, built["decl"], "place-box") for x in Ls]
    assert all(x["target"] in built["decl"]["objects"].values() and "box" in x["place"] for x in L)
    with pytest.raises(SimError, match="target"):
        check_layout(dict(target="kettle", place={"box": [0, 0, 0]}), built["decl"], "place-box")
    with pytest.raises(SimError, match="box"):
        check_layout(dict(target="red tape roll", place={"tape_red": [-0.3, 0, 0]}), built["decl"], "place-box")


def _scene(built, task):
    from agentic_robotics.selfsim.world import SelfSimScene
    return SelfSimScene(dict(model=built["model"], decl=built["decl"]), task)


def test_world_checks_place_and_lid(built):
    from agentic_robotics.sim.twin import B, TIP
    s = _scene(built, "place-box")
    t = s.reset_layout(dict(target="block", place={"box": [-0.36, 0.06, math.pi], "block": [-0.24, -0.14, 0.0],
                                                   "tape_red": [-0.42, -0.10, 0.0]}, joints={"box_lid": 1.9}), 123)
    assert t == dict(kind="place", object="black block with holes", destination="white box",
                     instruction="Put the black block with holes into the white box.")
    assert abs(s.table_z) < 1e-6 and abs(s.rest["block"] - 0.02) < 0.002
    P, S, k = s.P, s.P.S, s.target
    p, _ = P.world.pose(k)
    R = B.down(math.pi / 2)

    def go(pp, grip, T, ph):
        S.move(np.asarray(pp, float) - R[:, 2] * TIP, R, grip, T, ph); S.flush()
    go([p[0], p[1], 0.12], 0.04, 2.5, "transit"); go([p[0], p[1], 0.025], 0.04, 2.0, "descend")
    go([p[0], p[1], 0.025], 0.0, 0.8, "close the gripper"); go([p[0], p[1], 0.14], 0.0, 1.5, "lift")
    assert P.held_by_both(k) and P.com_z(k) - s.rest[k] > 0.05
    bp, _ = P.world.pose(s.box)
    go([bp[0], bp[1], 0.16], 0.0, 2.5, "transit"); go([bp[0], bp[1], 0.09], 0.0, 1.0, "descend")
    go([bp[0], bp[1], 0.09], 0.04, 0.6, "open the gripper"); go([bp[0], bp[1], 0.20], 0.04, 1.0, "retreat")
    for _ in range(15):
        P.settle(0.1); s.jd = s.judge()
    assert s.jd["inside_box"] and s.summary()["category"] == "success"
    c = _scene(built, "close-box")
    L = check_layout(dict(target="white box", place={"white box": [-0.32, 0.0, 0.0]}, joints={"box_lid": 1.7}), built["decl"], "close-box")
    assert c.reset_layout(L, 5)["kind"] == "close_box"
    for _ in range(16):
        c.P.d.qpos[c.lid_q] = 0.0; c.P.settle(0.1); c.jd = c.judge()
    assert c.jd["success"] and c.jd["lid_deg"] < 8


def test_programs_plan_with_the_kit_and_cannot_read_the_scene(built, tmp_path):
    from agentic_robotics.evaluation.runner import snapshot
    from agentic_robotics.selfsim.suite import prepare_sim, run_items
    prog = tmp_path / "prog" / "skills"; prog.mkdir(parents=True)
    hp = [str(p) for p in hidden_paths()]
    (prog / "pick.py").write_text(f"""
import os
def run(ctx):
    seen = {{}}
    for p in {hp!r}:
        try:
            seen[p] = os.listdir(p) if os.path.isdir(p) else len(open(p, 'rb').read())
        except Exception as e:
            seen[p] = type(e).__name__
    ctx.log('probe', seen=seen, nbody=int(ctx.robot.m.nbody), ngeom=int(ctx.robot.m.ngeom))
    return dict(result='ok')
""")
    prep = prepare_sim(FX, tmp_path / "prep")
    assert not prep.get("error"), prep
    snap = snapshot(tmp_path / "prog", tmp_path / "snap")
    (it, r), = run_items(snap, prep, [("clutter-pick-any", 0, 3)], tmp_path / "eps", workers=1)
    assert r["category"] == "not_touched" and not r.get("program_error")
    ev = [json.loads(x) for x in open(tmp_path / "eps" / "clutter-pick-any_0000" / "events.jsonl")]
    seen = ev[0]["data"]["seen"]
    for p, v in seen.items():
        assert v in ([], 0, "PermissionError"), (p, v)
    import mujoco
    assert ev[0]["data"]["ngeom"] == mujoco.MjModel.from_xml_path(str(KIT)).ngeom      # (its model: the kit)


def test_agent_docs_give_no_sizes():
    from agentic_robotics.learning.real2sim import r2s_doc
    txt = (paths.ROOT / "docs" / "task_real2sim.md").read_text() + (paths.ROOT / "docs" / "selfsim.md").read_text()
    for f, src in (("robot.md", "robot.md"), ("perception.md", "perception.md"), ("skill_api.md", "skill_api_scratch.md")):
        txt += r2s_doc(f, (paths.ROOT / "docs" / src).read_text())
    for bad in ("191", "193 ×", "Ø47", "Ø52", "Ø50", "190 mm", "twin", "splat", "kettle"):
        assert bad not in txt, bad


def test_real_world_export_has_no_verdict(tmp_path):
    from agentic_robotics.learning.real2sim import export_real
    priv = tmp_path / "priv"; (priv / "program").mkdir(parents=True)
    (priv / "result.json").write_text(json.dumps(dict(success=True, category="success", task_state={})))
    (priv / "program" / "program.json").write_text(json.dumps(dict(result="ok", usage={})))
    info = export_real(priv, tmp_path / "ws", dict(success=True, category="success", robot_time_s=3.0, program_result="ok"),
                       dict(instruction="Close the lid of the white box."))
    assert not (tmp_path / "ws" / "result.json").exists()
    assert set(info) == {"task", "robot_time_s", "time_limit_hit", "program_result", "program_error", "frames", "platform_error"}
