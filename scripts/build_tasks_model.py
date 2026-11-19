"""The multi-task model: the vendored twin_6522_wedge.xml (the clutter objects, the boxes with hinged lids, the real
wedge fingers) + the kettle (articulated lid, lid-release button, latch), the wooden stand with three sticks and the
wooden cylinder with a 22 mm bore, copied from panda_twin's twin_precision_b22.xml (6508 scan; the same metric table
frame), with their meshes and the kettle's latch (equality obj_kettle_latch: released by the world when the button is
2.5 mm in: sim/worlds.py). Nothing else changes; the keyframe gets the new joints' default values.

    .venv/bin/python -I scripts/build_tasks_model.py      (needs configs/local.yaml roots.panda_twin)
-> src/agentic_robotics/sim/models/twin_6522_wedge_tasks.xml + sim/models/assets_tasks/ (the 5 meshes, copied)"""
from __future__ import annotations

import copy
import hashlib
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import mujoco  # noqa: E402

from agentic_robotics import paths  # noqa: E402

BASE = paths.VENDOR / "panda_twin/model/twin_6522_wedge.xml"
OUT_DIR = paths.SRC / "agentic_robotics/sim/models"
OUT = OUT_DIR / "twin_6522_wedge_tasks.xml"
BODIES = ["obj_kettle", "obj_peg_stand", "obj_wood_cyl"]


def main():
    src = paths.root_of("panda_twin") / "model/twin_precision_b22.xml"
    S = ET.parse(src).getroot(); T_ = ET.parse(BASE); R = T_.getroot()
    s_meshdir = Path(S.find("compiler").get("meshdir"))
    # meshes: copied next to the model (small, committed); paths relative to the base model's meshdir
    base_meshdir = (BASE.parent / R.find("compiler").get("meshdir", ".")).resolve()
    (OUT_DIR / "assets_tasks").mkdir(parents=True, exist_ok=True)
    bodies = [b for b in S.find("worldbody").iter("body") if b.get("name") in BODIES]
    need = {g.get("mesh") for b in bodies for g in b.iter("geom") if g.get("mesh")}
    asset = R.find("asset"); have = {m.get("name") for m in asset.iter("mesh")}
    for m in S.find("asset").iter("mesh"):
        if m.get("name") in need and m.get("name") not in have:
            f = Path(m.get("file")); f = f if f.is_absolute() else s_meshdir / f
            dst = OUT_DIR / "assets_tasks" / f.name
            shutil.copy2(f, dst)
            m2 = copy.deepcopy(m); m2.set("file", os.path.relpath(dst, base_meshdir))
            asset.append(m2)
    mats = {g.get("material") for b in bodies for g in b.iter("geom") if g.get("material")}
    have_m = {m.get("name") for m in asset.iter("material")}
    for m in S.find("asset").iter("material"):
        if m.get("name") in mats and m.get("name") not in have_m:
            asset.append(copy.deepcopy(m))
    R.find("compiler").set("meshdir", os.path.relpath(base_meshdir, OUT_DIR))   # (relative: no machine path)
    wb = R.find("worldbody")
    for b in bodies:
        wb.append(copy.deepcopy(b))
    eq = R.find("equality")
    if eq is None:
        eq = ET.SubElement(R, "equality")
    for e in S.find("equality"):
        if "kettle" in str(e.attrib):
            eq.append(copy.deepcopy(e))
    # the keyframe: old qpos + the new joints' defaults (they come last: the bodies were appended to the worldbody)
    m0 = mujoco.MjModel.from_xml_path(str(BASE))
    keys = R.find("keyframe")
    saved = copy.deepcopy(keys) if keys is not None else None
    if keys is not None:
        R.remove(keys)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(T_, space=" ")
    with tempfile.NamedTemporaryFile("w", suffix=".xml", dir=OUT.parent, delete=False) as f:
        T_.write(f.name)
    m1 = mujoco.MjModel.from_xml_path(f.name); os.unlink(f.name)
    extra = m1.qpos0[m0.nq:]
    if saved is not None:
        for k in saved:
            q = np.array([float(x) for x in k.get("qpos").split()])
            assert len(q) == m0.nq, (len(q), m0.nq)
            k.set("qpos", " ".join(f"{v:.6g}" for v in np.r_[q, extra]))
            if k.get("ctrl") is None and m1.nu != m0.nu:
                raise SystemExit("new actuators: not expected")
        R.append(saved)
    T_.write(OUT)
    m2 = mujoco.MjModel.from_xml_path(str(OUT))
    print(f"wrote {OUT.relative_to(paths.ROOT)}: nq {m0.nq} -> {m2.nq}, nbody {m0.nbody} -> {m2.nbody}, "
          f"meshes copied {sorted(need)}; keyframes {[m2.key(i).name for i in range(m2.nkey)]}")
    for n in BODIES:
        b = m2.body(n).id
        print(f"  {n}: mass {m2.body_subtreemass[b] * 1000:.1f} g")
    for n in ("obj_tape_red", "obj_box_white", "obj_hole_block"):
        assert abs(m0.body(n).mass[0] - m2.body(n).mass[0]) < 1e-9, n
    print("  the 6522 objects unchanged (masses checked)")


if __name__ == "__main__":
    main()
