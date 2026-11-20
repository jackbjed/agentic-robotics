"""simple_sim's model (docs/simple_sim.md): the twin's scene made simple and INACCURATE on purpose, to see what training
in such a simulator gives (2026-11-20, user: "train an agent in a much simpler environment ... it should reflect what
happens when we train in a simple and inaccurate sim; it should have the table 5 cm lower, no photorealism, table brown
instead of the real color, as objects just default red, green and blue cubes and red and yellow cylinders (basic ones,
unrelated to the real ones), and for box basic mujoco box unrelated to the real one"). From the vendored
twin_6522_wedge.xml (the same robot, fingers, cameras, lights):

  table          its top 5 cm lower (z = -0.05 instead of 0; the legs shortened), plain brown
  ruler          removed (the real table's clutter)
  objects        five MuJoCo primitives, pure colours, 40 g-ish: a red, a green and a blue cube (40 mm), a red and a
                 yellow cylinder (40 mm across, 50 mm tall), resting on the lowered table
  box            a plain grey box with a hinged lid (220 x 170 x 70 mm, 4 mm walls; the white box is 191 x 193 x 58
                 mm with 3 mm walls and a flap)
Body names stay the twin's (the vendored world, task_box.BoxSim, needs its OBJ bodies; layouts and judges use the
names); the names the PROGRAM is told are sim/scene.py's PHRASES_SIMPLE:

  obj_tape_red -> red cylinder    obj_tape_yellow -> yellow cylinder    obj_tape_white -> red cube
  obj_hole_block -> blue cube     obj_sticky_pad -> green cube          obj_box_white -> grey box
The other bodies (glue, cable, screwdriver, brown box) keep their shapes and are never placed (parked off the table).
Writes src/agentic_robotics/sim/models/simple_sim.xml; prints each object's rest check.

    .venv/bin/python -I scripts/build_simple_sim.py"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import mujoco  # noqa: E402

from agentic_robotics import paths  # noqa: E402

SRC = paths.VENDOR / "panda_twin/model/twin_6522_wedge.xml"
OUT = paths.SRC / "agentic_robotics/sim/models/simple_sim.xml"
DZ = -0.05                                                     # the table top: 5 cm lower
BROWN = "0.50 0.33 0.18 1"
FRICTION, CONDIM, DENSITY = "1.0 0.02 0.005", "4", "700"
CUBE, CYL_R, CYL_H = 0.040, 0.020, 0.050
SHAPES = {"tape_red": ("cylinder", "1 0 0 1"), "tape_yellow": ("cylinder", "1 1 0 1"), "tape_white": ("cube", "1 0 0 1"),
          "hole_block": ("cube", "0 0 1 1"), "sticky_pad": ("cube", "0 1 0 1")}
BOX_L, BOX_W, BOX_H, BOX_T = 0.220, 0.170, 0.070, 0.004       # outer x, outer y, height, walls / floor / lid
GREY = "0.60 0.60 0.62 1"
FREE = ["box_white", "box_brown", "hole_block", "glue", "cable", "tape_red", "tape_yellow", "tape_white", "sticky_pad",
        "screwdriver"]


def f(*v):
    return " ".join(f"{x:.5f}" for x in v)


def geom(parent, name, **a):
    g = ET.SubElement(parent, "geom", dict(name=name, **{k: str(v) for k, v in a.items()}))
    return g


def object_geoms(body, k):
    kind, rgba = SHAPES[k]
    for c in [c for c in body if c.tag == "geom"]:
        body.remove(c)
    if kind == "cube":
        geom(body, f"obj_{k}", type="box", size=f(CUBE / 2, CUBE / 2, CUBE / 2), pos=f(0, 0, CUBE / 2), rgba=rgba,
             density=DENSITY, friction=FRICTION, condim=CONDIM)
    else:
        geom(body, f"obj_{k}", type="cylinder", size=f(CYL_R, CYL_H / 2), pos=f(0, 0, CYL_H / 2), rgba=rgba,
             density=DENSITY, friction=FRICTION, condim=CONDIM)


def box_geoms(body):
    lid = body.find("body")
    hinge = lid.find("joint")
    for c in [c for c in body if c.tag == "geom"]:
        body.remove(c)
    L, W, H, t = BOX_L, BOX_W, BOX_H, BOX_T
    common = dict(rgba=GREY, density="600.0", friction=FRICTION, condim=CONDIM)
    geom(body, "obj_box_white_floor", type="box", size=f(L / 2, W / 2, t / 2), pos=f(0, 0, t / 2), **common)
    for n, x in (("wx0", -1), ("wx1", 1)):
        geom(body, f"obj_box_white_{n}", type="box", size=f(t / 2, W / 2, H / 2), pos=f(x * (L / 2 - t / 2), 0, H / 2), **common)
    for n, y in (("wy0", -1), ("wy1", 1)):
        geom(body, f"obj_box_white_{n}", type="box", size=f(L / 2 - t, t / 2, H / 2), pos=f(0, y * (W / 2 - t / 2), H / 2), **common)
    for c in list(lid):
        lid.remove(c)
    lid.set("pos", f(0, -W / 2, H))
    lid.append(hinge)                                          # (the white box's hinge: axis, range, friction)
    geom(lid, "obj_box_white_lidp", type="box", size=f(L / 2, W / 2, t / 2), pos=f(0, W / 2, t / 2), **common)


def main():
    tree = ET.parse(SRC); root = tree.getroot()
    root.set("model", "simple_sim (panda table twin made simple)")
    wb = root.find("worldbody")
    for g in wb.findall("geom"):
        n = g.get("name")
        if n == "table_top":
            g.set("pos", f(0, 0, -0.0150 + DZ)); g.set("rgba", BROWN)
        elif n and n.startswith("table_leg"):
            p = [float(v) for v in g.get("pos").split()]; s = [float(v) for v in g.get("size").split()]
            top, bottom = -0.0300 + DZ, p[2] - s[2]
            g.set("pos", f(p[0], p[1], (top + bottom) / 2)); g.set("size", f(s[0], s[1], (top - bottom) / 2)); g.set("rgba", BROWN)
    for b in list(wb.findall("body")):
        n = b.get("name")
        if n == "obj_ruler":
            wb.remove(b); continue
        if n and n.startswith("obj_") and n[4:] in FREE:
            p = [float(v) for v in b.get("pos").split()]
            b.set("pos", f(p[0], p[1], p[2] + DZ))
            if n[4:] in SHAPES:
                object_geoms(b, n[4:])
            elif n == "obj_box_white":
                box_geoms(b)
    src_m = mujoco.MjModel.from_xml_path(str(SRC))             # (the keyframe: the same joints, objects 5 cm lower)
    key = root.find("keyframe").find("key")
    q = np.array([float(v) for v in key.get("qpos").split()])
    for k in FREE:
        a = src_m.jnt_qposadr[src_m.body_jntadr[src_m.body("obj_" + k).id]]
        q[a + 2] += DZ
    key.set("qpos", " ".join(f"{v:.6g}" for v in q))
    comp = root.find("compiler")                                # (the robot's meshes: the vendored ones, relative to
    comp.set("meshdir", str(Path("../../vendor/panda_twin/model") / comp.get("meshdir", ".")))   # models/: no machine path)
    ET.indent(tree, space=" ", level=0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(ET.tostring(root, encoding="unicode"))
    m = mujoco.MjModel.from_xml_path(str(OUT)); d = mujoco.MjData(m)
    mujoco.mj_resetDataKeyframe(m, d, m.key("twin").id); mujoco.mj_forward(m, d)
    g = m.geom("table_top").id
    top = d.geom_xpos[g][2] + m.geom_size[g][2]
    print(f"{OUT}: table top z = {top:+.4f} m")
    for k in list(SHAPES) + ["box_white"]:                     # (the rest check with the arm held: sim/picktwin.py)
        b = m.body("obj_" + k).id
        print(f"  {k:12s} mass {m.body_subtreemass[b] * 1000:6.1f} g  bottom z {d.xpos[b][2]:+.4f}")


if __name__ == "__main__":
    main()
