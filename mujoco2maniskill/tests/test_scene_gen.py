"""Practice scenes in the target simulator (sweep_blocks/scene_gen.py): asked-for scenes become the simulator's layouts
with the target's own things; invalid requests are refused. No simulator.

    PYTHONNOUSERSITE=1 $M2M_MANISKILL_PYTHON -I tests/test_scene_gen.py      (the environment with ManiSkill: looks and reach)"""
import copy
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from sweep_blocks import scene_gen as G  # noqa: E402

PANEL = sorted((HERE / "assets" / "robodojo_sweep" / "panel20").glob("ep_*.json"))


def test_1_a_development_scene_goes_there_and_back():
    for f in PANEL[:6]:
        L = json.loads(f.read_text()); sc = G.from_layout(L)
        assert G.check(sc) == [], (f.name, G.check(sc))
        M = G.to_layout(sc)
        for k in ("broom_shovel", "broom"):
            a, b = L["Rigid"][k][0], M["Rigid"][k][0]
            assert np.allclose(a["default_pos"], b["default_pos"], atol=1e-6) and np.allclose(G._R(a["default_ori"]), G._R(b["default_ori"]), atol=1e-6), (f.name, k)
        for a, b in zip(L["Rigid"]["small_cube"], M["Rigid"]["small_cube"]):
            assert np.allclose(a["default_pos"], b["default_pos"], atol=1e-6) and np.allclose(G._R(a["default_ori"]), G._R(b["default_ori"]), atol=1e-6)
        assert M["Appearance"]["broom_shovel"] == L["Appearance"]["broom_shovel"] and M["Appearance"]["table"].get("texture") == L["Appearance"]["table"].get("texture")


def test_2_the_things_themselves_cannot_be_changed():
    L0 = json.loads(G.BASE.read_text()); sc = G.from_layout(json.loads(PANEL[0].read_text()))
    sc2 = copy.deepcopy(sc); sc2["dustpan"]["mass"] = 9.0; sc2["cubes"][0]["half"] = [0.05] * 3; sc2["cubes"][0]["physics"] = dict(mass=1.0, friction=0.01)
    sc2["robot"] = "another"; sc2["Variant"] = dict(cube_scale=2.0)
    M = G.to_layout(sc2)
    for k in ("broom_shovel", "broom"):
        assert M["Rigid"][k][0]["physics"] == L0["Rigid"][k][0]["physics"] and M["Rigid"][k][0]["scale"] == L0["Rigid"][k][0]["scale"]
    assert all(c["physics"] == L0["Rigid"]["small_cube"][0]["physics"] for c in M["Rigid"]["small_cube"]) and len(M["Rigid"]["small_cube"]) == 3
    assert "Variant" not in M and set(M) - set(L0) == {"Appearance", "PracticeScene"}, set(M) - set(L0)
    for k in ("Geometry", "Table", "Room", "Ground"):
        assert M[k] == L0[k]                                        # (the table, the camera stand, the room: the target's)


def test_3_invalid_requests_are_refused():
    ok = G.from_layout(json.loads(PANEL[0].read_text()))
    def bad(change, word):
        sc = copy.deepcopy(ok); change(sc); r = G.check(sc)
        assert r and any(word in x for x in r), (word, r)
    bad(lambda s: s["cubes"].pop(), "exactly three")
    bad(lambda s: s["cubes"].append(dict(xy_mm=[300, 200], yaw_deg=0)), "exactly three")
    bad(lambda s: s["cubes"][0].update(xy_mm=s["dustpan"]["xy_mm"]), "overlap")                    # a cube put IN the dustpan
    bad(lambda s: s["cubes"][1].update(xy_mm=s["cubes"][0]["xy_mm"]), "overlap")                   # one cube on another
    bad(lambda s: s["brush"].update(xy_mm=[690, 0]), "edge")
    bad(lambda s: s["dustpan"].update(xy_mm=[0, 900]), "edge")
    bad(lambda s: s["cubes"][0].update(xy_mm=[300, -450]), "right arm's base")
    bad(lambda s: s["cubes"][0].update(xy_mm=[0, -470]), "camera stand")
    bad(lambda s: s["dustpan"].update(yaw_deg=float("nan")), "finite")
    bad(lambda s: s["colours"].update(brush=[1.5, 0, 0]), "colours")
    bad(lambda s: s.update(table=dict(name="checker")), "table")                                   # a table of the final appearance test
    bad(lambda s: s.update(table=dict(name="black")), "table")
    bad(lambda s: s.update(table=dict(texture="tables/checker.png")), "table")
    assert G.check("not a dict") and G.check(None)
    sc = copy.deepcopy(ok); sc["table"] = dict(rgb=[0.1, 0.5, 0.9]); sc["dustpan"].update(xy_mm=[-200, 250], yaw_deg=200); sc["brush"].update(xy_mm=[350, 100], yaw_deg=-30)
    sc["cubes"] = [dict(xy_mm=[-400 + 60 * i, -100], yaw_deg=10 * i) for i in range(3)]
    assert G.check(sc) == [], G.check(sc)                                                          # anywhere valid on the table, any turn, any plain colour
    M = G.to_layout(sc)
    assert abs(G._heading("dustpan", M["Rigid"]["broom_shovel"][0]["default_ori"]) - (200 - 360)) < 1e-6 and M["Appearance"]["table"] == dict(rgb=[0.1, 0.5, 0.9], name="asked")


def test_4_fresh_scenes():
    a, b, c = G.sample(np.random.default_rng([7, 1])), G.sample(np.random.default_rng([7, 1])), G.sample(np.random.default_rng([8, 1]))
    assert a == b and a != c
    frozen = {json.dumps(json.loads(f.read_text())["Rigid"]["broom_shovel"][0]["default_pos"][:2]) for f in PANEL}
    for s in range(40):
        sc = G.sample(np.random.default_rng([s, 0x5EED]))
        assert G.check(sc) == [], (s, G.check(sc))
        assert sc["table"]["name"] in G.table_names() and len(sc["cubes"]) == 3
        assert json.dumps([v / 1000 for v in sc["dustpan"]["xy_mm"]]) not in frozen


def test_5_resting():
    sc = G.from_layout(json.loads(PANEL[0].read_text())); I = np.eye(3)
    at = lambda o, d=0.0: (np.array([o["xy_mm"][0] / 1000 + d, o["xy_mm"][1] / 1000, 0.78]), I)
    poses = dict(dustpan=at(sc["dustpan"]), brush=at(sc["brush"]), **{f"cube_{i}": at(c) for i, c in enumerate(sc["cubes"])})
    assert G.rests(sc, poses)[1] == []
    poses["cube_1"] = at(sc["cubes"][1], 0.02)
    moved, bad = G.rests(sc, poses)
    assert moved["cube_1"] == 20.0 and "cube_1 did not stay" in bad[0]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in tests:
        try:
            f(); print("PASS ", n)
        except Exception as e:
            import traceback
            bad += 1; print("FAIL ", n, "::", type(e).__name__, str(e)[:500], traceback.format_exc()[-400:])
    print(f"{len(tests) - bad} of {len(tests)} passed")
    sys.exit(1 if bad else 0)
