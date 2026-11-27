"""Mirrored test scenes (sweep_blocks/mirror_scenes.py, xsim/bi/backend.py make_split / prepare).

    PYTHONNOUSERSITE=1 ../.venv/bin/python -I tests/test_mirrored_split.py [DIR of an existing mirrored split to check]

Checks the OBJECTS' positions and orientations, not the split's name."""
import copy
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from sweep_blocks.mirror_scenes import mirror  # noqa: E402
from xsim import config  # noqa: E402
from xsim.bi import backend  # noqa: E402
from xsim.tasks import TASKS  # noqa: E402


def Rq(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def geometry(L):
    """(the pan's place, its mouth's direction on the table, the brush's place and long axis, the cubes' places) in metres."""
    R = L["Rigid"]; pan, br = R["broom_shovel"][0], R["broom"][0]
    Rp, Rb = Rq(pan["default_ori"]), Rq(br["default_ori"])
    return dict(pan=np.array(pan["default_pos"]), mouth=-Rp[:2, 1] / np.linalg.norm(Rp[:2, 1]), pan_up=Rp[:, 2], brush=np.array(br["default_pos"]),
                brush_axis=Rb[:2, 2] / np.linalg.norm(Rb[:2, 2]), brush_R=Rb, pan_R=Rp, cubes=np.array([c["default_pos"] for c in R["small_cube"]]))


def is_mirrored_arrangement(L):
    g = geometry(L)
    return bool(g["mouth"][0] < -0.9 and g["brush"][0] > g["pan"][0] + 0.1 and (g["cubes"][:, 0] < g["pan"][0] - 0.1).all())


def is_original_arrangement(L):
    """(The generator turns the pan by up to 15 degrees from its base orientation, which is itself a few degrees off +x: up to about 20.)"""
    g = geometry(L)
    along = (g["cubes"][:, :2] - g["pan"][:2]) @ g["mouth"]
    return bool(g["mouth"][0] > np.cos(np.radians(21.0)) and g["brush"][0] < g["pan"][0] - 0.1 and (g["cubes"][:, 0] > g["pan"][0] + 0.1).all()
                and (along > 0.16).all() and (along < 0.295).all())


def a_scene():
    f = sorted((HERE / "assets" / "robodojo_sweep" / "panel20").glob("ep_*.json"))[3]
    return json.loads(f.read_text())


def test_1_mirror_moves_and_turns_every_object():
    L = a_scene(); M = mirror(L); g, m = geometry(L), geometry(M)
    assert is_original_arrangement(L) and not is_mirrored_arrangement(L)
    assert is_mirrored_arrangement(M) and not is_original_arrangement(M) and M["Mirrored"] is True
    S = np.diag([-1.0, 1.0, 1.0])
    for a, b in ((g["pan"], m["pan"]), (g["brush"], m["brush"])):
        assert np.allclose(b, S @ a)
    assert np.allclose(m["cubes"], g["cubes"] @ S)
    for a, b in ((g["pan_R"], m["pan_R"]), (g["brush_R"], m["brush_R"])):       # the mirror image of a thing symmetric about its own x = 0 plane
        assert np.allclose(b, S @ a @ S) and np.isclose(np.linalg.det(b), 1.0)
    assert np.allclose(m["mouth"], [-g["mouth"][0], g["mouth"][1]]) and np.allclose(m["brush_axis"], [-g["brush_axis"][0], g["brush_axis"][1]])
    assert np.allclose(m["pan_up"], [0, 0, 1], atol=1e-6) == np.allclose(g["pan_up"], [0, 0, 1], atol=1e-6)
    for o, p in zip(L["Rigid"]["small_cube"], M["Rigid"]["small_cube"]):
        assert np.allclose(Rq(p["default_ori"]), S @ Rq(o["default_ori"]) @ S)
    back = mirror(M)
    assert all(np.allclose(a["default_pos"], b["default_pos"]) and np.allclose(a["default_ori"], b["default_ori"])
               for k in L["Rigid"] for a, b in zip(L["Rigid"][k], back["Rigid"][k]))
    assert L.get("Appearance") == M.get("Appearance") and json.dumps(L["Rigid"]["small_cube"][0]["physics"]) == json.dumps(M["Rigid"]["small_cube"][0]["physics"])


def _prepare_with_fakes(existing_splits):
    """backend.prepare with scene making replaced by a recorder (no simulator): which split was made how?"""
    calls = []
    L0 = a_scene()

    def fake_make(out, n, seed, looks_seed, log=print, ood=False, jobs=12, mirror=False):
        out = Path(out); out.mkdir(parents=True, exist_ok=True); calls.append(dict(name=out.name, n=n, seed=seed, ood=ood, mirror=mirror))
        files = []
        for k in range(n):
            L = copy.deepcopy(L0); L["Rigid"]["broom_shovel"][0]["default_pos"][1] += 0.001 * (k + 1) + 0.0001 * (seed % 97)
            if mirror:
                from sweep_blocks.mirror_scenes import mirror as mir
                L = mir(L)
            f = out / f"ep_{k:02d}.json"; f.write_text(json.dumps(L)); files.append(f)
        (out / "looks.json").write_text("{}")
        return files

    def fake_variant(files, out, change, log=print):
        real_check = set(change) - set(backend.VARIANT_KEYS)
        if real_check:
            raise ValueError(f"variant() does not make the change(s) {sorted(real_check)}")
        out = Path(out); out.mkdir(parents=True, exist_ok=True); calls.append(dict(name=out.name, variant=dict(change)))
        res = []
        for i, f in enumerate(files):
            L = json.loads(Path(f).read_text()); L["Variant"] = dict(change); g = out / f"ep_{i:02d}.json"; g.write_text(json.dumps(L)); res.append(g)
        return res
    real = backend.make_scenes, backend.variant, backend._ms
    backend.make_scenes, backend.variant = fake_make, fake_variant
    backend._ms = lambda args, timeout=0: type("P", (), dict(returncode=0, stdout="", stderr=""))()
    t = tempfile.mkdtemp(prefix="m2m-prep-")
    try:
        task = TASKS["sweep_blocks"]
        cfg = config.load(splits=dict(test_id=3, ood_appearance=2, ood_dimensions=2, ood_heights=2, ood_physics=2, ood_mirrored=3), target_panel=2, f_pool=2)
        R, T = Path(t) / "sweep_blocks" / "rep7", Path(t) / "sweep_blocks" / "test_private"
        if existing_splits:                                         # (a root prepared BEFORE the mirrored split existed)
            old = dict(task, ood={k: v for k, v in task["ood"].items() if k != "ood_mirrored"})
            try:
                backend.prepare("sweep_blocks", old, 7, cfg, R, T, log=lambda *a: None)
            except (SystemExit, Exception):
                pass
            assert "ood_mirrored" not in json.loads((T / "splits.json").read_text())["splits"]
            calls.clear()
        try:
            backend.prepare("sweep_blocks", task, 7, cfg, R, T, log=lambda *a: None)
        except (SystemExit, Exception):
            pass                                                    # (the shared look needs the simulator: the splits are made before it)
        return calls, json.loads((T / "splits.json").read_text())["splits"]
    finally:
        backend.make_scenes, backend.variant, backend._ms = real


def test_2_a_fresh_preparation_mirrors_the_mirrored_split():
    calls, splits = _prepare_with_fakes(existing_splits=False)
    made = {c["name"]: c for c in calls}
    assert made["ood_mirrored"].get("mirror") is True and not any(c.get("variant") and c["name"] == "ood_mirrored" for c in calls), calls
    assert len(splits["ood_mirrored"]) == 3 and all(is_mirrored_arrangement(json.loads(Path(f).read_text())) for f in splits["ood_mirrored"])
    for name in ("test_id", "ood_appearance", "ood_dimensions", "ood_physics"):
        assert all(is_original_arrangement(json.loads(Path(f).read_text())) for f in splits[name]), name
    assert made["ood_dimensions"]["variant"] == dict(cube_scale=1.3) and made["ood_appearance"]["ood"] is True


def test_3_a_later_added_split_is_made_the_same_way():
    fresh, _ = _prepare_with_fakes(existing_splits=False)
    later, splits = _prepare_with_fakes(existing_splits=True)
    a = next(c for c in fresh if c["name"] == "ood_mirrored"); b = next(c for c in later if c["name"] == "ood_mirrored")
    assert a == b and [c["name"] for c in later] == ["ood_mirrored"], (a, b, later)       # (the same seed, size and way; nothing else is touched)
    assert all(is_mirrored_arrangement(json.loads(Path(f).read_text())) for f in splits["ood_mirrored"])


def test_4_variant_refuses_a_change_it_does_not_make():
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "ep_00.json"; f.write_text(json.dumps(a_scene()))
        try:
            backend.variant([f], Path(t) / "out", dict(mirror=True)); raise AssertionError("variant() took mirror=True and made nothing of it")
        except ValueError:
            pass


def check_existing(d):
    """An existing mirrored split, scene by scene: mirrored for real? And its un-mirrored image: a scene of the generator's family?"""
    d = Path(d); files = sorted(d.glob("ep_*.json")); bad = []
    for f in files:
        L = json.loads(f.read_text())
        if not (L.get("Mirrored") is True and is_mirrored_arrangement(L) and is_original_arrangement(mirror(L))):
            bad.append(f.name)
    cand = sorted((d / "candidates").glob("cand_*.json")) if (d / "candidates").is_dir() else []
    cbad = [f.name for f in cand if not is_mirrored_arrangement(json.loads(f.read_text()))]
    return dict(split=str(d), scenes=len(files), genuinely_mirrored=len(files) - len(bad), not_mirrored=bad, candidates=len(cand), candidates_not_mirrored=cbad)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in tests:
        try:
            f(); print("PASS ", n)
        except Exception as e:
            bad += 1; print("FAIL ", n, "::", type(e).__name__, str(e)[:900])
    print(f"{len(tests) - bad} of {len(tests)} passed")
    for d in sys.argv[1:]:
        print("EXISTING", json.dumps(check_existing(d)))
    sys.exit(1 if bad else 0)
