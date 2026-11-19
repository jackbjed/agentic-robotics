"""Condition C's object set (docs/fidelity.md): the same scene with DIFFERENT objects that keep the clutter pick's
manipulation affordances. Each of the eight clutter objects of the vendored twin_6522_wedge.xml is replaced by another
shape of the same mass, friction and contact settings, the same colours, a footprint and height within its original
bounding box (so graspability across the fingers, height and clutter gaps stay comparable):

  tape rolls (rings of 16 boxes)          -> solid cans (one cylinder, the ring's outer radius and height)
  black block with holes (9 boxes)        -> a plain black box (its bounding box)
  coiled cable (a curved strip of boxes)  -> a black elliptical hoop (20 capsules, the strip's footprint and thickness)
  screwdriver (red handle + thin shaft)   -> a bar: a red box + a grey box of the handle's cross-section
  glue bottle (boxes + cap)               -> a red-and-white cylinder bottle of the same height
  sticky-note pad (76 x 76 x 20 mm box)   -> a purple disc (radius 40 mm, the same height)
Body names stay the same (the layouts' format and the judge use them); the names the PROGRAM is told change
(sim/scene.py PHRASES_SUBSTITUTE). Writes src/agentic_robotics/sim/models/twin_6522_wedge_substitute.xml; prints the
per-object mass / bounding-box check.

    .venv/bin/python -I scripts/build_condition_c.py"""
from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import mujoco  # noqa: E402

from agentic_robotics import paths  # noqa: E402

SRC = paths.VENDOR / "panda_twin/model/twin_6522_wedge.xml"
OUT = paths.SRC / "agentic_robotics/sim/models/twin_6522_wedge_substitute.xml"
OBJECTS = ["tape_red", "tape_yellow", "tape_white", "hole_block", "cable", "screwdriver", "glue", "sticky_pad"]
FRICTION, CONDIM = "1.0 0.02 0.005", "4"


def quat_mat(q):
    R = np.zeros(9); mujoco.mju_quat2Mat(R, np.asarray(q, float)); return R.reshape(3, 3)


def body_aabb(m, b):
    """The axis-aligned box (body frame) of body b's colliding geoms."""
    pts = []
    for g in range(m.ngeom):
        if m.geom_bodyid[g] != b or (m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0):
            continue
        t, s = m.geom_type[g], m.geom_size[g]
        if t == mujoco.mjtGeom.mjGEOM_BOX:
            c = np.array([[sx, sy, sz] for sx in (-s[0], s[0]) for sy in (-s[1], s[1]) for sz in (-s[2], s[2])])
        elif t == mujoco.mjtGeom.mjGEOM_CYLINDER:
            c = np.array([[sx, sy, sz] for sx in (-s[0], s[0]) for sy in (-s[0], s[0]) for sz in (-s[1], s[1])])
        elif t == mujoco.mjtGeom.mjGEOM_CAPSULE:
            c = np.array([[sx, sy, sz] for sx in (-s[0], s[0]) for sy in (-s[0], s[0]) for sz in (-s[1] - s[0], s[1] + s[0])])
        elif t == mujoco.mjtGeom.mjGEOM_MESH:
            mid = m.geom_dataid[g]; a, n = m.mesh_vertadr[mid], m.mesh_vertnum[mid]
            c = m.mesh_vert[a:a + n]
        else:
            continue
        pts.append(c @ quat_mat(m.geom_quat[g]).T + m.geom_pos[g])
    P = np.concatenate(pts)
    return P.min(0), P.max(0)


def rgba_of(m, b, which=0):
    gs = [g for g in range(m.ngeom) if m.geom_bodyid[g] == b and not (m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0)]
    return " ".join(f"{v:.3f}" for v in m.geom_rgba[gs[min(which, len(gs) - 1)]])


def geom(parent, name, mass, rgba, **kw):
    a = dict(name=name, mass=f"{mass:.6f}", rgba=rgba, friction=FRICTION, condim=CONDIM)
    a.update({k: v for k, v in kw.items()})
    ET.SubElement(parent, "geom", a)


def substitute(k, body, m, b):
    lo, hi = body_aabb(m, b)
    c, h = (lo + hi) / 2, (hi - lo) / 2
    M = float(m.body_mass[b])
    H, z0 = hi[2] - lo[2], lo[2]
    if k.startswith("tape_"):
        r = float(max(h[0], h[1]))
        geom(body, f"obj_{k}_can", M, rgba_of(m, b), type="cylinder", size=f"{r:.5f} {H / 2:.5f}",
             pos=f"{c[0]:.5f} {c[1]:.5f} {z0 + H / 2:.5f}")
    elif k == "hole_block":
        geom(body, f"obj_{k}_box", M, rgba_of(m, b), type="box", size=f"{h[0]:.5f} {h[1]:.5f} {h[2]:.5f}",
             pos=f"{c[0]:.5f} {c[1]:.5f} {c[2]:.5f}")
    elif k == "cable":
        t = float(h[2]); Rx, Ry = float(h[0]) - t, float(h[1]) - t; n = 20      # (an elliptical hoop: the strip's footprint)
        for i in range(n):
            a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
            p0 = (c[0] + Rx * math.cos(a0), c[1] + Ry * math.sin(a0)); p1 = (c[0] + Rx * math.cos(a1), c[1] + Ry * math.sin(a1))
            geom(body, f"obj_{k}_h{i}", M / n, rgba_of(m, b), type="capsule", size=f"{t:.5f}",
                 fromto=f"{p0[0]:.5f} {p0[1]:.5f} {z0 + t:.5f} {p1[0]:.5f} {p1[1]:.5f} {z0 + t:.5f}")
    elif k == "screwdriver":
        L = hi[0] - lo[0]; hw = float(min(h[1], h[2]))
        x_split = lo[0] + 0.55 * L
        geom(body, f"obj_{k}_a", M * 0.55, rgba_of(m, b, 0), type="box", size=f"{(x_split - lo[0]) / 2:.5f} {hw:.5f} {hw:.5f}",
             pos=f"{(lo[0] + x_split) / 2:.5f} {c[1]:.5f} {z0 + hw:.5f}")
        geom(body, f"obj_{k}_b", M * 0.45, "0.450 0.450 0.470 1.000", type="box",
             size=f"{(hi[0] - x_split) / 2:.5f} {hw:.5f} {hw:.5f}", pos=f"{(x_split + hi[0]) / 2:.5f} {c[1]:.5f} {z0 + hw:.5f}")
    elif k == "glue":
        r = float(min(h[0], h[1])) * 1.1
        geom(body, f"obj_{k}_low", M * 0.45, rgba_of(m, b, 0), type="cylinder", size=f"{r:.5f} {H * 0.225:.5f}",
             pos=f"{c[0]:.5f} {c[1]:.5f} {z0 + H * 0.225:.5f}")
        geom(body, f"obj_{k}_up", M * 0.55, "0.950 0.950 0.930 1.000", type="cylinder", size=f"{r * 0.8:.5f} {H * 0.275:.5f}",
             pos=f"{c[0]:.5f} {c[1]:.5f} {z0 + H * 0.45 + H * 0.275:.5f}")
    elif k == "sticky_pad":
        geom(body, f"obj_{k}_disc", M, "0.620 0.480 0.860 1.000", type="cylinder", size=f"0.04000 {H / 2:.5f}",
             pos=f"{c[0]:.5f} {c[1]:.5f} {z0 + H / 2:.5f}")


def main():
    m = mujoco.MjModel.from_xml_path(str(SRC))
    tree = ET.parse(SRC); root = tree.getroot()
    comp = root.find("compiler")
    md = Path(comp.get("meshdir", "."))
    comp.set("meshdir", str(Path("../../vendor/panda_twin/model") / md))      # (relative to models/: no machine path)
    for body in root.iter("body"):
        n = body.get("name", "")
        k = n[4:] if n.startswith("obj_") else None
        if k not in OBJECTS:
            continue
        b = m.body(n).id
        for gch in [x for x in body if x.tag == "geom"]:
            body.remove(gch)
        substitute(k, body, m, b)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space=" ")
    tree.write(OUT)
    m2 = mujoco.MjModel.from_xml_path(str(OUT))
    print(f"wrote {OUT.relative_to(paths.ROOT)}")
    print(f"{'object':12s} {'mass orig':>9s} {'mass sub':>9s}   bbox orig (mm)         bbox substitute (mm)")
    for k in OBJECTS:
        b1, b2 = m.body("obj_" + k).id, m2.body("obj_" + k).id
        e1 = np.subtract(*body_aabb(m, b1)[::-1]) * 1000; e2 = np.subtract(*body_aabb(m2, b2)[::-1]) * 1000
        print(f"{k:12s} {m.body_mass[b1] * 1000:8.1f}g {m2.body_mass[b2] * 1000:8.1f}g   {np.round(e1).astype(int)}   {np.round(e2).astype(int)}")


if __name__ == "__main__":
    main()
