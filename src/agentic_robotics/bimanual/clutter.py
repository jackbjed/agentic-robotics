"""Picking from clutter (docs/bimanual.md, tasks pick_clutter_*): a heap of objects on the table, one of them named
in the task's text (the target: its colour and shape). Three levels:

    medium       the target lies on the table among other objects that touch it, and can be seen
    hard         the target lies under other objects: it cannot be seen, or only a part of it
    extra_hard   as hard, with the whole heap in an open bin

A scene is a JSON-able dict (mm, kg/m3, colours by name):

    {"kind": "clutter", "level": "hard", "seed": 7,
     "objects": [{"shape", "size_mm", "color", "rgb", "density", "pos_mm": [x, y, z], "quat": [w, x, y, z]}, ...],
     "target": 0,                                   (the index of the target in "objects")
     "bin": None | {"inner_mm": [x, y], "height_mm", "wall_mm", "mass_kg", "color", "rgb", "pos_mm", "quat"},
     "visible": {"scene": 0.12, "top": 0.0},        (the part of the target that is seen, see visibility())
     "touching": [1, 4], "covered_by": [3],         (the objects that touch the target / that hide it from above)
     "ood": [...]}                                  (which of its aspects are outside the training limits)

    sample(level, seed)    the scene of episode `seed`: within the training limits below OOD_FROM, and from OOD_FROM on
                           with some aspects outside them (OOD_ASPECTS)
    model_xml(file, scene), layout(scene), paint(model, scene)    the simulation's model and layout (core.BiCore)
    text(scene), judge(S), truth(ep)                              the task's text, verdict and true state (tasks.py)
    visibility(scene, poses=None)                                 how much of the target is seen, in a scene's state
    build(spec, seed)      a scene set up by hand (a development session's own, clutter_session.py): the level's heap of
                           objects that are given, drawn, or assets of the session's own ("custom": made of parts), or
                           objects set at given poses

A heap is made by simulating it (a model of the table, the bin and the objects, without the robot): the target and a
ring of objects are set on the table and pulled together, then the others are dropped on them one by one, and the
whole is left to come to rest; a heap that does not meet its level's conditions is thrown away and another one drawn.
An object's pose is that of its geometric centre. Object k is the body obj_item_k, the bin obj_bin.
"""
from __future__ import annotations

import functools
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from . import scenes as _scenes
from .scenes import COLOURS, JITTER, OOD_COLOURS, RULER

KIND = "clutter"
LEVELS = ("medium", "hard", "extra_hard")
OOD_FROM = 5000                                                 # sample(level, seed): out-of-distribution scenes from this seed on
LIFT = 150.0                                                    # mm: the target's centre must be held this high above the table
SCENE_EYE = (864.0, -8.0, 572.0)                                # mm: the scene camera (models/*.xml: d415)
TABLE = dict(size="0.6105 0.6120 0.0150", pos="0 0 -0.0150", friction="1.0 0.02 0.005", condim="4")   # (models/*.xml: table_top)
FLAT = dict(friction="0.7 0.02 0.005", condim="4")              # the objects' contacts; balls and capsules roll with friction
ROUND = dict(friction="0.7 0.02 0.002", condim="6")
FACETS = 8                                                      # a cylinder collides as a prism of 2 FACETS sides (see _object_geoms)
RING_WALLS = 24                                                 # a round ring (a world's scene): that many walls about its hole
NOUN = {"box": "block", "cylinder": "cylinder", "ball": "ball", "pill": "capsule", "L": "L-shaped block", "steps": "stepped block",
        "T": "T-shaped block", "cross": "cross-shaped block", "hexagon": "hexagonal prism", "ring": "ring"}

# the training limits: what a scene below OOD_FROM is drawn from (sizes in mm at scale 1, see _size)
SHAPES = ("box", "cylinder", "ball")
TRAIN = dict(n=dict(medium=(5, 8), hard=(7, 11), extra_hard=(6, 9)), density=(300, 1200), x=(-360, -230), y=(-200, 30),
             bin_inner=(210, 280), bin_h=(60, 90), bin_yaw=(-20, 20), bin_mass=(0.3, 0.8))
BIN_COLOURS = {"grey": (125, 125, 130), "cardboard": (176, 137, 92), "white": (232, 232, 226), "black": (38, 38, 42)}
BIN_WALL = 5.0
# the level's conditions on the part of the target that is seen (from the scene camera, from above)
SEEN = dict(medium=dict(scene=(0.5, 1.0), top=(0.85, 1.0)), hard=dict(scene=(0.0, 0.35), top=(0.0, 0.35)),
            extra_hard=dict(scene=(0.0, 0.35), top=(0.0, 0.35)))
# outside the training limits: what only sample(seed >= OOD_FROM) has
OOD_ASPECTS = ("shapes", "colours", "sizes", "count", "density", "place", "bin")
OOD_SHAPES = ("pill", "L", "steps", "T", "cross", "hexagon")
OOD = dict(scale=((0.5, 0.72), (1.3, 1.6)), more=(4, 7), density=((100, 250), (2000, 2700)),
           place=dict(near=dict(x=(-470, -410), y=(-200, 30)), far=dict(x=(-170, -110), y=(-200, 30)),
                      right=dict(x=(-360, -230), y=(-340, -260))),
           bin_inner=((160, 200), (290, 340)), bin_h=((35, 50), (95, 110)), bin_yaw=((-45, -25), (25, 45)))
OOD_BIN_COLOURS = {"blue": (45, 85, 170), "green": (45, 130, 70), "red": (175, 45, 40), "yellow": (225, 195, 50)}
TRIES = 60                                                      # heaps drawn for one scene before giving up
# one-armed scenes (arm="left": the left arm alone within the real rig's safety limits, the right arm parked;
# clutter_one_arm.py, tasks pick_clutter_one_arm_*; medium and hard): where the heaps are (training, and the places
# outside it), the limits of a scene set up by hand, and where an object may lie at the start ("reach", its centre):
# where the arm's gripper gets from above within the rig's limits (scripts: the reach map in docs/bimanual.md) - not
# nearer than x = -480, not in the corner beside the parked arm (x < corner x and y < corner y), not further than
# `radius` from the arm's base
ONE_ARM = dict(x=(-360, -220), y=(-120, 30),
               place=dict(near=dict(x=(-430, -380), y=(-120, 30)), far=dict(x=(-180, -120), y=(-120, 30)),
                          side=dict(x=(-270, -180), y=(-300, -240)), beyond=dict(x=(-380, -240), y=(350, 430))),
               limits=dict(x=(-470.0, -100.0), y=(-360.0, 540.0)),
               reach=dict(x=(-480.0, -60.0), y=(-390.0, 570.0), corner=(-345.0, -225.0), base=(-790.0, 40.0), radius=740.0))
ARMS = (None, "left")


def out_of_reach(P):
    """Which of the points P [n, 2 or 3] (m) lie outside the one-armed scenes' reach (ONE_ARM["reach"])."""
    R = ONE_ARM["reach"]
    x, y = np.asarray(P, float)[:, 0] * 1000, np.asarray(P, float)[:, 1] * 1000
    return ((x < R["x"][0]) | (x > R["x"][1]) | (y < R["y"][0]) | (y > R["y"][1]) | ((x < R["corner"][0]) & (y < R["corner"][1]))
            | (np.hypot(x - R["base"][0], y - R["base"][1]) > R["radius"]))


# ---------------------------------------------------------------------- the objects
def _size(rng, shape, role="any", scale=1.0):
    """An object's sizes (mm), by shape: box [a, b, c] | cylinder [diameter, height] | ball [diameter] | pill
    [diameter, length] | L [arm, width, height] | steps [length, width, height] | T [bar, stem, width, height] | cross
    [length, width, height] | hexagon [across the flats, height]. role: "target" (it fits in the gripper),
    "cover" (a flat one, to lie on the target), or any."""
    u = lambda a, b: float(rng.uniform(a, b))
    if role == "target":
        z = dict(box=lambda: [u(28, 58), u(28, 58), u(28, 58)], cylinder=lambda: [u(28, 58), u(28, 65)], ball=lambda: [u(32, 58)],
                 pill=lambda: [u(24, 40), u(65, 100)], L=lambda: [u(60, 85), u(20, 30), u(20, 45)], steps=lambda: [u(50, 80), u(30, 55), u(30, 60)],
                 T=lambda: [u(60, 85), u(40, 60), u(18, 28), u(20, 40)], cross=lambda: [u(55, 80), u(18, 28), u(20, 40)],
                 hexagon=lambda: [u(30, 55), u(25, 60)])[shape]()
    elif role == "cover":
        z = dict(box=lambda: [u(90, 130), u(55, 75), u(10, 25)], cylinder=lambda: [u(70, 75), u(12, 25)],
                 L=lambda: [u(90, 110), u(28, 30), u(15, 25)], steps=lambda: [u(80, 90), u(50, 60), u(30, 40)],
                 T=lambda: [u(90, 100), u(60, 70), u(25, 28), u(15, 25)], cross=lambda: [u(90, 110), u(25, 30), u(15, 25)],
                 hexagon=lambda: [u(60, 65), u(20, 30)], pill=lambda: [u(35, 40), u(95, 110)], ball=lambda: [u(55, 65)])[shape]()
    else:
        z = dict(box=lambda: [u(25, 80), u(25, 80), u(20, 80)], cylinder=lambda: [u(25, 75), u(20, 90)], ball=lambda: [u(28, 65)],
                 pill=lambda: [u(22, 40), u(65, 110)], L=lambda: [u(60, 100), u(20, 30), u(15, 50)], steps=lambda: [u(50, 90), u(30, 60), u(30, 70)],
                 T=lambda: [u(60, 100), u(40, 70), u(18, 28), u(15, 45)], cross=lambda: [u(55, 100), u(18, 28), u(15, 45)],
                 hexagon=lambda: [u(30, 65), u(20, 70)])[shape]()
    z = [v * scale for v in z]
    if role == "target" and scale > 1:                              # a big target still fits between the fingers (80 mm)
        k = dict(box=int(np.argmin(z)), cylinder=0, ball=0, pill=0, L=1, steps=1, T=2, cross=1, hexagon=0)[shape]
        z[k] = min(z[k], 68.0)
    if shape == "pill":
        z[1] = max(z[1], z[0] + 10)
    return [round(v, 1) for v in z]


def radius(o):
    """The radius (mm) of the sphere about an object's centre that holds it."""
    s, z = o["shape"], o.get("size_mm")
    if s in ("box", "steps"):
        return math.sqrt(z[0] ** 2 + z[1] ** 2 + z[2] ** 2) / 2
    if s == "cylinder":
        return math.hypot(z[0], z[1]) / 2
    if s == "ring":                                                 # (a polygon's corners stand out of its circle)
        return math.hypot(z[0] / math.cos(math.pi / int(o["sides"])) if o.get("sides") else z[0], z[1]) / 2
    if s == "ball":
        return z[0] / 2
    if s == "pill":
        return z[1] / 2
    if s == "L":
        return math.sqrt(2 * z[0] ** 2 + z[2] ** 2) / 2
    if s == "T":
        return math.sqrt(z[0] ** 2 + (z[1] + z[2]) ** 2 + z[3] ** 2) / 2
    if s == "cross":
        return math.sqrt(2 * z[0] ** 2 + z[2] ** 2) / 2
    if s == "hexagon":
        return math.hypot(z[0] / math.sqrt(3), z[1] / 2)
    if s == "custom":
        return max(float(np.linalg.norm(p["pos_mm"])) + _part_radius(p) for p in o["parts"])
    raise ValueError(f"unknown shape {s!r}")


def _part_radius(p):
    if p["type"] == _scenes.HULL:                                   # (a world's custom object: a convex hull of points)
        return max(float(np.linalg.norm(q)) for q in p["points_mm"])
    z = p["size_mm"]
    return {"box": lambda: math.sqrt(z[0] ** 2 + z[1] ** 2 + z[2] ** 2) / 2, "cylinder": lambda: math.hypot(z[0], z[1]) / 2,
            "ball": lambda: z[0] / 2, "capsule": lambda: z[1] / 2}[p["type"]]()


def _euler_quat(e):
    """[rx, ry, rz] (degrees: turns about the fixed x, then y, then z axis) -> (w, x, y, z)."""
    from scipy.spatial.transform import Rotation
    x, y, z, w = Rotation.from_euler("xyz", [float(v) for v in e], degrees=True).as_quat()
    return [float(w), float(x), float(y), float(z)]


def _qmul(a, b):
    w1, x1, y1, z1 = a; w2, x2, y2, z2 = b
    return [w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2]


def _thin_axis(o):
    """The body axis (0, 1, 2) along which the object is thinnest: up, it lies flat."""
    s, z = o["shape"], o.get("size_mm")
    if s == "box":
        return int(np.argmin(z))
    if s in ("cylinder", "ring"):
        return 2 if z[1] <= z[0] else 0
    if s in ("L", "T", "steps", "cross", "hexagon"):
        return 2
    return 2 if s == "ball" else 1 if s == "pill" else 2                # (custom: its own z)


def _rgba(rgb):
    return " ".join(f"{v / 255:.3f}" for v in rgb) + " 1"


def _geom(body, name, kind, rgb, density, group, size=None, pos=(0, 0, 0), **kw):
    a = dict(name=name, type=kind, pos=" ".join(f"{v:.5f}" for v in pos), rgba=_rgba(rgb), density=f"{density:.1f}", group=str(group))
    if size is not None:
        a["size"] = " ".join(f"{v:.5f}" for v in size)
    return ET.SubElement(body, "geom", {**a, **kw})


def _object_geoms(body, asset, name, o, group=0):
    """The object's geoms about its centre (group: of the geoms that are seen; those that only collide: group 3)."""
    z, rgb, den, s = [v / 1000 for v in o.get("size_mm") or []], o["rgb"], o["density"], o["shape"]
    g = lambda part, kind, size, pos=(0, 0, 0), c=FLAT, **kw: _geom(body, name + part, kind, rgb, den, group, size, pos, **{**c, **kw})
    if s == "box":
        g("", "box", (z[0] / 2, z[1] / 2, z[2] / 2))
    elif s == "cylinder":                                           # seen as a cylinder (which has the mass); it collides as a
        r, h, N = z[0] / 2, z[1] / 2, FACETS                        # prism of 2 N sides inside it, made of N boxes: on a box
        _geom(body, name, "cylinder", rgb, den, group, (r, h), contype="0", conaffinity="0")   # (the table) a cylinder geom
        for k in range(N):                                          # has one contact point and never comes to rest
            _geom(body, f"{name}_c{k}", "box", rgb, 0, 3, (r * math.cos(math.pi / (2 * N)), r * math.sin(math.pi / (2 * N)), h),
                  euler=f"0 0 {k * math.pi / N:.5f}", **FLAT)
    elif s == "ring":                                               # (a world's scene) a cylinder with a hole along its axis:
        R, H, r_in = z[0] / 2, z[1], z[2] / 2                       # walls about the hole, as scenes._cylinder's (they overlap:
        N = int(o.get("sides") or RING_WALLS)                       # the density is scaled to a ring's weight); `sides`: its
        t = R - r_in; rc = r_in + t / 2                             # outline and its hole are polygons of that many sides
        hw = R * math.tan(math.pi / N) * (1.0 if o.get("sides") else 1.05)
        dn = den * math.pi * (R * R - r_in * r_in) / (N * t * 2 * hw)
        for k in range(N):
            a = 2 * math.pi * k / N
            _geom(body, f"{name}_w{k}", "box", rgb, dn, group, (t / 2, hw, H / 2), (rc * math.cos(a), rc * math.sin(a), 0),
                  euler=f"0 0 {a:.5f}", **FLAT)
    elif s == "ball":
        g("", "sphere", (z[0] / 2,), c=ROUND)
    elif s == "pill":
        g("", "capsule", (z[0] / 2, (z[1] - z[0]) / 2), c=ROUND, euler="0 1.5708 0")
    elif s == "L":                                                  # two arms of a square of side z[0]
        a, w, h = z
        g("_a", "box", (a / 2, w / 2, h / 2), (0, -a / 2 + w / 2, 0))
        g("_b", "box", (w / 2, (a - w) / 2, h / 2), (-a / 2 + w / 2, w / 2, 0))
    elif s == "steps":                                              # a block with a half-length block on top of one end
        l, w, h = z
        g("_a", "box", (l / 2, w / 2, h / 4), (0, 0, -h / 4))
        g("_b", "box", (l / 4, w / 2, h / 4), (-l / 4, 0, h / 4))
    elif s == "T":                                                  # a bar along x across the end of a stem along y
        bar, stem, w, h = z
        g("_a", "box", (bar / 2, w / 2, h / 2), (0, stem / 2, 0))
        g("_b", "box", (w / 2, stem / 2, h / 2), (0, -w / 2, 0))
    elif s == "cross":                                              # two bars of length z[0] across each other
        a, w, h = z
        g("_a", "box", (a / 2, w / 2, h / 2))
        for k, sy in enumerate((-1, 1)):
            g(f"_b{k}", "box", (w / 2, (a - w) / 4, h / 2), (0, sy * (a + w) / 4, 0))
    elif s == "hexagon":                                            # z[0] across the flats, z[1] high: three boxes across each
        f, h = z                                                    # other (they overlap: half the density each)
        for k in range(3):
            _geom(body, f"{name}_{'abc'[k]}", "box", rgb, den / 2, group, (f / 2, f / (2 * math.sqrt(3)), h / 2),
                  euler=f"0 0 {k * math.pi / 3:.5f}", **FLAT)
    elif s == "custom":                                             # an asset of parts (build()): each at its own pose
        for i, p in enumerate(o["parts"]):
            pz, pos, q = [v / 1000 for v in p.get("size_mm") or []], [v / 1000 for v in p["pos_mm"]], _euler_quat(p["euler_deg"])
            qs = lambda q: " ".join(f"{v:.6f}" for v in q)
            if p["type"] == _scenes.HULL:                           # (a world's scene) the convex hull of its points: a mesh
                _scenes.hull_geom(body, asset, f"{name}_p{i}", p, rgb, den, quat=qs(q), group=str(group), **FLAT)
            elif p["type"] == "box":
                g(f"_p{i}", "box", (pz[0] / 2, pz[1] / 2, pz[2] / 2), pos, quat=qs(q))
            elif p["type"] == "cylinder":                           # (seen as a cylinder, collides as a prism: as above)
                r, h, N = pz[0] / 2, pz[1] / 2, FACETS
                _geom(body, f"{name}_p{i}", "cylinder", rgb, den, group, (r, h), pos, quat=qs(q), contype="0", conaffinity="0")
                for k in range(N):
                    a = k * math.pi / N / 2
                    _geom(body, f"{name}_p{i}_c{k}", "box", rgb, 0, 3, (r * math.cos(math.pi / (2 * N)), r * math.sin(math.pi / (2 * N)), h),
                          pos, quat=qs(_qmul(q, [math.cos(a), 0, 0, math.sin(a)])), **FLAT)
            elif p["type"] == "ball":
                g(f"_p{i}", "sphere", (pz[0] / 2,), pos, c=ROUND)
            else:                                                   # a capsule along the part's z
                g(f"_p{i}", "capsule", (pz[0] / 2, (pz[1] - pz[0]) / 2), pos, c=ROUND, quat=qs(q))
    else:
        raise ValueError(f"unknown shape {s!r}")
    _scenes.contact_attrs(body.findall("geom"), o)                  # (a world's scene: its friction, its contacts' hardness)


def _bin_density(b):
    lx, ly = (v / 1000 for v in b["inner_mm"]); h, w = b["height_mm"] / 1000, b["wall_mm"] / 1000
    vol = (lx + 2 * w) * (ly + 2 * w) * w + 2 * (lx + 2 * w) * w * h + 2 * ly * w * h
    return b["mass_kg"] / vol


def _bin_geoms(body, b, group=0):
    """An open bin: a floor and four walls; its origin: the centre of its underside."""
    lx, ly = (v / 1000 for v in b["inner_mm"]); h, w = b["height_mm"] / 1000, b["wall_mm"] / 1000
    den = _bin_density(b)
    g = lambda part, size, pos: _geom(body, "bin_" + part, "box", b["rgb"], den, group, size, pos, **FLAT)
    g("floor", (lx / 2 + w, ly / 2 + w, w / 2), (0, 0, w / 2))
    for k, sx in enumerate((-1, 1)):
        g(f"x{k}", (w / 2, ly / 2 + w, h / 2), (sx * (lx / 2 + w / 2), 0, w + h / 2))
    for k, sy in enumerate((-1, 1)):
        g(f"y{k}", (lx / 2, w / 2, h / 2), (0, sy * (ly / 2 + w / 2), w + h / 2))


def _fixed_extras(wb, sc, asset):
    """A world's extras (world_scene: all fixed) as bodies without a joint: part of the table."""
    for k, e in enumerate(sc.get("extras") or []):
        b = ET.SubElement(wb, "body", name=f"fixed_extra_{k}", pos=f"{e['xy_mm'][0] / 1000:.5f} {e['xy_mm'][1] / 1000:.5f} {e['z_mm'] / 1000:.5f}",
                          euler=f"0 0 {math.radians(e['yaw_deg']):.5f}")
        _scenes._extra_geoms(b, k, e, asset)


def _free_model(sc):
    """The heap's own model: the table, the bin and the objects (free bodies), without the robot. The target's geoms
    are in group 1, all the others in group 0 (visibility())."""
    root = ET.Element("mujoco", model="clutter")
    ET.SubElement(root, "compiler", angle="radian", autolimits="true")     # (autolimits: as models/*.xml, for a world's MJCF)
    opt = ET.SubElement(root, "option", timestep="0.002", integrator="implicitfast", cone="elliptic", impratio="10")
    ET.SubElement(opt, "flag", multiccd="enable")                   # (as models/*.xml)
    asset = ET.SubElement(root, "asset")
    wb = ET.SubElement(root, "worldbody")
    ET.SubElement(wb, "geom", name="table_top", type="box", **TABLE)
    if sc.get("bin"):
        b = ET.SubElement(wb, "body", name="obj_bin", pos="0 0 0.001")
        ET.SubElement(b, "freejoint")
        _bin_geoms(b, sc["bin"])
    _fixed_extras(wb, sc, asset)
    for k, o in enumerate(sc["objects"]):
        b = ET.SubElement(wb, "body", name=f"obj_item_{k}", pos=f"{2 + 0.3 * k:.1f} 3 1")
        ET.SubElement(b, "freejoint")
        _object_geoms(b, asset, f"item_{k}", o, group=1 if k == sc["target"] else 0)
    import mujoco
    if sc.get("mjcf"):                                              # (a world's own bodies: the last, at their start values)
        _scenes.add_mjcf(root, sc)
        q = _scenes.mjcf_qpos(sc).split()
        m = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
        m.qpos0[m.nq - len(q):] = [float(v) for v in q]
        return m
    return mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))


# ---------------------------------------------------------------------- how much of the target is seen
def _rays(m, d, body, eye=None, n=30):
    """Rays through a grid over the target's bounding disc, from the eye (m) or straight down -> (rays that meet the
    target when it is alone, those of them that meet it first in the scene, {body id: rays it is first on})."""
    import mujoco
    c = d.xipos[body].copy()
    gs = [g for g in range(m.ngeom) if m.geom_bodyid[g] == body]
    r = max(float(np.linalg.norm(d.geom_xpos[g] - c)) + float(m.geom_rbound[g]) for g in gs)
    if eye is None:
        w = np.array([0.0, 0.0, -1.0]); a, b = np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
    else:
        w = c - eye; w /= np.linalg.norm(w)
        a = np.cross(w, [0, 0, 1.0]); a /= np.linalg.norm(a); b = np.cross(w, a)
    only, both = np.array([0, 1, 0, 0, 0, 0], np.uint8), np.array([1, 1, 0, 0, 0, 0], np.uint8)
    gid = np.zeros(1, np.int32)
    alone = seen = 0
    first = {}
    for i in range(n):
        for j in range(n):
            q = c + a * r * (2 * (i + 0.5) / n - 1) + b * r * (2 * (j + 0.5) / n - 1)
            p0 = q - w * 1.0 if eye is None else np.asarray(eye, float)
            v = w if eye is None else (q - p0) / np.linalg.norm(q - p0)
            if mujoco.mj_ray(m, d, p0, v, only, 0, -1, gid) < 0:
                continue
            alone += 1
            mujoco.mj_ray(m, d, p0, v, both, 1, -1, gid)
            hit = int(m.geom_bodyid[gid[0]]) if gid[0] >= 0 else -1
            if hit == body:
                seen += 1
            else:
                first[hit] = first.get(hit, 0) + 1
    return alone, seen, first


def _seen(m, d, sc):
    """-> ({"scene", "top"}: the part of the target's outline that is seen from the scene camera / from straight
    above, the indices of the objects that hide at least a tenth of it from above)."""
    body = m.body(f"obj_item_{sc['target']}").id
    out, cover = {}, []
    for view, eye in (("scene", np.array(SCENE_EYE) / 1000), ("top", None)):
        alone, seen, first = _rays(m, d, body, eye)
        out[view] = round(seen / max(alone, 1), 3)
        if view == "top":
            cover = sorted(int(m.body(b).name.rsplit("_", 1)[1]) for b, k in first.items()
                           if b > 0 and m.body(b).name.startswith("obj_item_") and k >= 0.1 * max(alone, 1))
    return out, cover


def _set_poses(m, d, sc, poses=None):
    """The scene's poses ({name: (pos m, quat)}, default: the scene's own) into the heap's model."""
    import mujoco
    for name, o in [(f"item_{k}", o) for k, o in enumerate(sc["objects"])] + ([("bin", sc["bin"])] if sc.get("bin") else []):
        p, q = poses[name] if poses is not None else (np.array(o["pos_mm"]) / 1000, o["quat"])
        a = m.jnt_qposadr[m.body_jntadr[m.body("obj_" + name).id]]
        d.qpos[a:a + 3] = p; d.qpos[a + 3:a + 7] = q
    mujoco.mj_forward(m, d)


def visibility(sc, poses=None):
    """How much of the target is seen in the scene's state (or with the poses {name: (pos m, quat)} of "item_k" and
    "bin": e.g. the simulation's true ones later in an episode) -> ({"scene", "top"}, covered_by)."""
    import mujoco
    m = _free_model(sc); d = mujoco.MjData(m)
    _set_poses(m, d, sc, poses)
    return _seen(m, d, sc)


# ---------------------------------------------------------------------- making a heap
def _quat(rng, up=None, tilt=0.0):
    """A random orientation: body axis `up` (0, 1, 2; None: any of the six) along the world's z, turned about it by a
    random angle, then tilted by up to `tilt` degrees."""
    import mujoco
    k = int(rng.integers(3)) if up is None else up
    s = 1.0 if rng.random() < 0.5 else -1.0
    z = np.zeros(3); z[k] = s
    x = np.zeros(3); x[(k + 1) % 3] = 1.0
    A = np.column_stack([x, np.cross(z, x), z]).T                   # rows: where the body's axes go -> body to world: A
    th = float(rng.uniform(0, 2 * math.pi)); c, sn = math.cos(th), math.sin(th)
    R = np.array([[c, -sn, 0], [sn, c, 0], [0, 0, 1.0]]) @ A
    if tilt:
        ax = rng.normal(size=3); ax[2] = 0; ax /= np.linalg.norm(ax)
        t = math.radians(float(rng.uniform(-tilt, tilt)))
        K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
        R = (np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K) @ R
    q = np.zeros(4); mujoco.mju_mat2Quat(q, R.flatten())
    return q


class _Heap:
    """The heap's simulation: objects wait far away (no contacts, no weight) until they are put in."""

    def __init__(self, sc, rng):
        import mujoco
        self.mj, self.sc, self.rng = mujoco, sc, rng
        self.m = m = _free_model(sc); self.d = d = mujoco.MjData(m)
        self.n = len(sc["objects"])
        self.body = [m.body(f"obj_item_{k}").id for k in range(self.n)]
        self.adr = [int(m.jnt_qposadr[m.body_jntadr[b]]) for b in self.body]
        self.dof = [int(m.jnt_dofadr[m.body_jntadr[b]]) for b in self.body]
        self.geoms = [np.flatnonzero(m.geom_bodyid == b) for b in self.body]
        self.con = (m.geom_contype.copy(), m.geom_conaffinity.copy())
        self.r = [radius(o) / 1000 for o in sc["objects"]]
        self.inside = set()
        for k in range(self.n):
            m.geom_contype[self.geoms[k]] = 0; m.geom_conaffinity[self.geoms[k]] = 0; m.body_gravcomp[self.body[k]] = 1
        self.bin = None
        if sc.get("bin"):
            b = sc["bin"]
            self.bin = m.body("obj_bin").id
            a = int(m.jnt_qposadr[m.body_jntadr[self.bin]])
            d.qpos[a:a + 3] = [b["xy_mm"][0] / 1000, b["xy_mm"][1] / 1000, 0.0005]
            h = math.radians(b["yaw_deg"]) / 2; d.qpos[a + 3:a + 7] = [math.cos(h), 0, 0, math.sin(h)]
        mujoco.mj_forward(m, d)
        self.run(0.3)

    def run(self, s, pull=None):
        """pull: (centre xy, force as a multiple of the weight, the objects pulled towards it)."""
        m, d = self.m, self.d
        for _ in range(int(round(s / m.opt.timestep))):
            if pull is not None:
                c, f, ks = pull
                for k in ks:
                    v = c - d.xpos[self.body[k]][:2]; dist = float(np.linalg.norm(v))
                    d.xfrc_applied[self.body[k], :2] = v / max(dist, 1e-6) * f * 9.81 * m.body_subtreemass[self.body[k]] * min(dist / 0.01, 1.0)
            self.mj.mj_step(m, d)
        d.xfrc_applied[:] = 0

    def put(self, k, xyz, quat):
        m, d = self.m, self.d
        d.qpos[self.adr[k]:self.adr[k] + 3] = xyz; d.qpos[self.adr[k] + 3:self.adr[k] + 7] = quat
        d.qvel[self.dof[k]:self.dof[k] + 6] = 0
        m.geom_contype[self.geoms[k]] = self.con[0][self.geoms[k]]; m.geom_conaffinity[self.geoms[k]] = self.con[1][self.geoms[k]]
        m.body_gravcomp[self.body[k]] = 0
        self.inside.add(k)
        self.mj.mj_forward(m, d)

    def top(self, xy, r):
        """The height of the heap within r of xy (the highest point of the objects' bounding spheres there)."""
        z = 0.0
        for k in self.inside:
            p = self.d.xpos[self.body[k]]
            if np.linalg.norm(p[:2] - xy) < r + self.r[k]:
                z = max(z, float(p[2]) + self.r[k])
        return z

    def clip(self, xy, r):
        """xy moved into the bin (the object's bounding sphere clear of its walls, where it fits)."""
        if self.bin is None:
            return np.asarray(xy, float)
        b = self.sc["bin"]
        c = self.d.xpos[self.bin][:2]; R = self.d.xmat[self.bin].reshape(3, 3)[:2, :2]
        lim = np.maximum(np.array(b["inner_mm"]) / 2000 - r - 0.003, 0.002)
        return c + R @ np.clip(R.T @ (np.asarray(xy, float) - c), -lim, lim)

    def drop(self, k, xy, quat, s=0.5, gap=0.02):
        xy = self.clip(xy, self.r[k])
        z = self.top(xy, self.r[k]) + self.r[k] + gap
        if self.bin is not None:                                    # into the bin: from above its rim
            z = max(z, (BIN_WALL + self.sc["bin"]["height_mm"]) / 1000 + self.r[k] + 0.005)
        self.put(k, [xy[0], xy[1], z], quat)
        self.run(s)

    def rest(self, limit=6.0):
        """Run until nothing moves (0.4 s below 5 mm/s and 0.1 rad/s) -> False if it has not come to rest."""
        t = still = 0.0
        while t < limit:
            self.run(0.1); t += 0.1
            v = max((float(np.abs(self.d.qvel[a:a + 3]).max()) for a in self.dof), default=0.0)
            w = max((float(np.abs(self.d.qvel[a + 3:a + 6]).max()) for a in self.dof), default=0.0)
            still = still + 0.1 if v < 0.005 and w < 0.1 else 0.0
            if still >= 0.4:
                return True
        return False

    def touching(self, k):
        """The objects in contact with object k, and whether it rests on the table or the bin's floor."""
        m, d = self.m, self.d
        out, floor = set(), False
        for i in range(d.ncon):
            c = d.contact[i]
            b1, b2 = int(m.geom_bodyid[c.geom1]), int(m.geom_bodyid[c.geom2])
            if self.body[k] in (b1, b2) and c.dist < 0.0005:
                o = b2 if b1 == self.body[k] else b1
                if o in self.body:
                    out.add(self.body.index(o))
                else:
                    floor = True
        return sorted(out), floor


def _make(sc, rng):
    """One heap for the scene's objects (objects[0]: the target; then `ring` objects for its neighbours, then `covers`
    to lie on it, then the rest) -> why it is not a scene of its level ("" if it is); the poses go into the scene."""
    level, n = sc["level"], len(sc["objects"])
    H = _Heap(sc, rng)
    d = H.d
    c = np.array(sc["centre_mm"]) / 1000
    if H.bin is not None:                                           # the target: anywhere in the bin
        lim = np.array(sc["bin"]["inner_mm"]) / 2000 - 0.05
        c = H.clip(c + (d.xmat[H.bin].reshape(3, 3)[:2, :2] @ rng.uniform(-lim, lim)), H.r[0])
    ring, covers = sc["_ring"], sc["_covers"]
    H.put(0, [c[0], c[1], H.top(c, H.r[0]) + H.r[0] + 0.004 + (0.005 if H.bin is not None else 0)], _quat(rng, _thin_axis(sc["objects"][0]) if rng.random() < 0.5 else None))
    H.run(0.4)
    a0 = float(rng.uniform(0, 2 * math.pi))
    ks = list(range(1, 1 + ring))
    for i, k in enumerate(ks):                                      # the neighbours: in a ring about the target, then pulled in
        a = a0 + 2 * math.pi * (i + float(rng.uniform(-0.25, 0.25))) / max(ring, 1)
        dist = H.r[0] + H.r[k] + float(rng.uniform(0.004, 0.02))
        xy = d.xpos[H.body[0]][:2] + dist * np.array([math.cos(a), math.sin(a)])
        q = _quat(rng, None if rng.random() < 0.6 else _thin_axis(sc["objects"][k]))
        if H.bin is not None:
            H.drop(k, xy, q, s=0.4)
        else:
            H.put(k, [xy[0], xy[1], H.r[k] + 0.002], q)
    H.run(0.5)
    H.run(0.8, pull=(d.xpos[H.body[0]][:2].copy(), 1.4, ks))
    H.run(0.4)
    spread = max(0.05, 0.6 * max((float(np.linalg.norm(d.xpos[H.body[k]][:2] - d.xpos[H.body[0]][:2])) for k in ks), default=0.05))
    for k in range(1 + ring, 1 + ring + covers):                    # flat on the target
        o = sc["objects"][k]
        H.drop(k, d.xpos[H.body[0]][:2] + rng.uniform(-0.012, 0.012, 2), _quat(rng, _thin_axis(o), tilt=8), s=0.6)
    for k in range(1 + ring + covers, n):                           # the rest: anywhere on the heap (medium: on the ring, not on the target)
        a, rr = float(rng.uniform(0, 2 * math.pi)), float(rng.uniform(0, 1)) ** 0.5
        if level == "medium":
            off = (H.r[0] + H.r[k] + float(rng.uniform(0.02, 0.07))) * np.array([math.cos(a), math.sin(a)])
        else:
            off = spread * rr * np.array([math.cos(a), math.sin(a)])
        H.drop(k, d.xpos[H.body[0]][:2] + off, _quat(rng), s=0.5)
    if not H.rest():
        return "the heap does not come to rest"
    # the conditions
    P = np.array([d.xpos[b] for b in H.body])
    if H.bin is not None:
        bp, bR = d.xpos[H.bin], d.xmat[H.bin].reshape(3, 3)
        if bR[2, 2] < 0.999:
            return "the bin does not stand flat"
        loc = (P - bp) @ bR
        if np.any(np.abs(loc[:, :2]) > np.array(sc["bin"]["inner_mm"]) / 2000) or np.any(loc[:, 2] < 0):
            return "an object is outside the bin"
        ext = float(np.abs(bR[1, :2]) @ (np.array(sc["bin"]["inner_mm"]) / 2000 + BIN_WALL / 1000))
        lo_y, hi_y = bp[1] - ext, bp[1] + ext
    else:
        if np.any(P[:, 2] < 0) or np.any(np.linalg.norm(P[:, :2] - P[0, :2], axis=1) > 0.22):
            return "an object has left the heap"
        lo_y, hi_y = float(np.min(P[:, 1] - np.array(H.r))), float(np.max(P[:, 1] + np.array(H.r)))
    if not (hi_y < RULER[0] / 1000 - 0.005 or lo_y > RULER[1] / 1000 + 0.005):
        return "the heap is on the ruler"
    if not (-0.585 < float(np.min(P[:, 0] - np.array(H.r))) and float(np.max(P[:, 0] + np.array(H.r))) < 0.0 and float(np.min(P[:, 1] - np.array(H.r))) > -0.585):
        return "the heap is too near the table's edge"
    if sc.get("arm") and (np.any(out_of_reach(P)) or float(np.max(P[:, 1] + np.array(H.r))) > 0.585):
        return "an object is out of the arm's reach"
    touch, floor = H.touching(0)
    seen, cover = _seen(H.m, d, sc)
    for view, (lo, hi) in SEEN[level].items():
        if not lo <= seen[view] <= hi:
            return f"the target is seen {seen[view]:.2f} from {view}"
    if level == "medium" and (len(touch) < 2 or not floor):
        return "the target does not touch two neighbours on the table"
    for k, o in enumerate(sc["objects"]):
        o["pos_mm"] = [round(float(v) * 1000, 2) for v in P[k]]
        o["quat"] = [round(float(v), 6) for v in d.xquat[H.body[k]]]
    if H.bin is not None:
        sc["bin"]["pos_mm"] = [round(float(v) * 1000, 2) for v in d.xpos[H.bin]]
        sc["bin"]["quat"] = [round(float(v), 6) for v in d.xquat[H.bin]]
    sc.update(visible=seen, touching=touch, covered_by=cover)
    return ""


# ---------------------------------------------------------------------- scenes
def _jit(rng, base):
    return [int(np.clip(c + rng.integers(-JITTER, JITTER + 1), 0, 255)) for c in base]


def _pick(rng, r):
    """A value in one of the ranges r ((lo, hi) or several of them)."""
    r = r if isinstance(r[0], (tuple, list)) else (r,)
    return float(rng.uniform(*r[int(rng.integers(len(r)))]))


def _draw(level, seed, attempt, arm=None):
    """The objects of a scene (not yet a heap): the target first. Below OOD_FROM within the training limits. arm: a
    one-armed scene (ONE_ARM: the same objects as the two-armed scene of that seed, the heap where that arm reaches)."""
    T = TRAIN
    places, here = (ONE_ARM["place"], ONE_ARM) if arm else (OOD["place"], T)
    rng = np.random.default_rng([13, LEVELS.index(level), int(seed), attempt])
    which = []
    if seed >= OOD_FROM:
        aspects = [a for a in OOD_ASPECTS if a != "bin" or level == "extra_hard"]
        for _ in range(200):
            which = [a for a in aspects if a != "density" and rng.random() < 0.4]
            if len(which) >= 2:
                break
        if rng.random() < 0.3:
            which.append("density")
    place = str(rng.choice(list(places))) if "place" in which else None
    X, Y = (places[place]["x"], places[place]["y"]) if place else (here["x"], here["y"])
    centre = [round(float(rng.uniform(*X)), 1), round(float(rng.uniform(*Y)), 1)]
    n = int(rng.integers(T["n"][level][0], T["n"][level][1] + 1)) + (int(rng.integers(OOD["more"][0], OOD["more"][1] + 1)) if "count" in which else 0)
    box = None
    if level == "extra_hard":
        o = "bin" in which
        inner = [round(_pick(rng, OOD["bin_inner"] if o and rng.random() < 0.6 else T["bin_inner"])) for _ in range(2)]
        height = round(_pick(rng, OOD["bin_h"] if o and rng.random() < 0.6 else T["bin_h"]))
        yaw = round(_pick(rng, OOD["bin_yaw"] if o and rng.random() < 0.6 else T["bin_yaw"]), 1)
        col = str(rng.choice(list(OOD_BIN_COLOURS) if o and rng.random() < 0.6 else list(BIN_COLOURS)))
        if o and not (max(inner) > T["bin_inner"][1] or min(inner) < T["bin_inner"][0] or not T["bin_h"][0] <= height <= T["bin_h"][1]
                      or not T["bin_yaw"][0] <= yaw <= T["bin_yaw"][1] or col in OOD_BIN_COLOURS):
            height = round(_pick(rng, OOD["bin_h"]))
        hx, hy, a = inner[0] / 2 + BIN_WALL, inner[1] / 2 + BIN_WALL, math.radians(yaw)
        if place != "right":                                        # clear of the ruler
            centre[1] = round(min(centre[1], RULER[0] - 12 - abs(math.sin(a)) * hx - abs(math.cos(a)) * hy), 1)
        box = dict(inner_mm=inner, height_mm=height, wall_mm=BIN_WALL, mass_kg=round(float(rng.uniform(*T["bin_mass"])), 2), color=col,
                   rgb=_jit(rng, {**BIN_COLOURS, **OOD_BIN_COLOURS}[col]), xy_mm=list(centre), yaw_deg=yaw)
        n = min(n, max(4, int(inner[0] * inner[1] / 60 ** 2)))      # (no more objects than about what its floor holds)
    palette = {**COLOURS, **OOD_COLOURS}
    names = list(OOD_COLOURS) if "colours" in which else list(COLOURS)
    shape = lambda: str(rng.choice(OOD_SHAPES)) if "shapes" in which and rng.random() < 0.7 else str(rng.choice(SHAPES))
    dens = lambda: round(_pick(rng, OOD["density"] if "density" in which else T["density"]), -1)
    big = bool(rng.random() < 0.5)                                   # (sizes: the scene's objects mostly smaller, or mostly bigger)
    scale = lambda: (_pick(rng, OOD["scale"][int(big if rng.random() < 0.8 else not big)]) if "sizes" in which and rng.random() < 0.8 else 1.0)

    def obj(role="any"):
        s = "box" if role == "cover" and "shapes" not in which else shape()
        return dict(shape=s, size_mm=_size(rng, s, role, scale()), color=str(rng.choice(names)), density=dens())

    covers = 0 if level == "medium" else int(rng.integers(1, 3))
    ring = min(int(rng.integers(3, 6)) if level != "extra_hard" else int(rng.integers(2, 5)), n - 1 - covers)
    objs = [obj("target")]
    for k in range(1, n):                                           # (no other object of the target's colour and shape)
        for _ in range(50):
            o = obj("cover" if 1 + ring <= k < 1 + ring + covers else "any")
            if (o["color"], o["shape"]) != (objs[0]["color"], objs[0]["shape"]):
                break
        objs.append(o)
    for o in objs:
        o["rgb"] = _jit(rng, palette[o["color"]])
    sc = dict(kind=KIND, level=level, seed=int(seed), objects=objs, target=0, bin=box, centre_mm=centre, _ring=ring, _covers=covers,
              ood=[w for w in which if w != "place"] + ([f"place:{place}"] if place else []))
    if arm:
        sc["arm"] = arm
    return sc, rng


@functools.lru_cache(maxsize=512)
def _sample(level, seed, arm=None):
    if level not in LEVELS:
        raise KeyError(f"unknown level {level!r}: {LEVELS}")
    if arm not in ARMS or (arm and level == "extra_hard"):
        raise KeyError('one-armed scenes: arm "left", levels medium and hard')
    why = []
    for attempt in range(TRIES):
        sc, rng = _draw(level, seed, attempt, arm)
        w = _make(sc, rng)
        if not w:
            sc = {k: v for k, v in sc.items() if not k.startswith("_")}
            sc["attempts"] = attempt + 1
            return sc
        why.append(w)
    raise RuntimeError(f"no {level} heap for seed {seed} in {TRIES} tries: {sorted(set(why))}")


def sample(level, seed, arm=None):
    """The scene of episode `seed` of pick_clutter_<level> (the same every time); arm="left": of
    pick_clutter_one_arm_<level> (the scene says so: "arm")."""
    import copy
    return copy.deepcopy(_sample(level, int(seed), arm))


def target_name(sc):
    """The target as the task's text names it: its colour and shape."""
    o = sc["objects"][sc["target"]]
    return f"{o['color']} {noun(o)}"


def noun(o):
    """What the task's text calls an object of this shape (a world's object can carry its own name: "name")."""
    return o["name"] if o["shape"] == "custom" or o.get("name") else NOUN[o["shape"]]


def _what(o):
    """An object in a line of describe()."""
    if o["shape"] == "custom":
        return f"{o['color']} {o['name']} (custom: " + " + ".join(_scenes.part_text(p) for p in o["parts"]) + ")"
    return (f"{o['color']} {o['shape']} {' x '.join(f'{v:g}' for v in o['size_mm'])}" + (f" with {o['sides']} sides" if o.get("sides") else "")
            + (f" (called \"{o['name']}\")" if o.get("name") else ""))


def describe(sc):
    """One line of text: what is in the scene."""
    t = sc["target"]
    out = (f"{sc['level']}: target item_{t}, the {target_name(sc)}, seen {sc['visible']['scene']:.2f} from the scene camera and "
           f"{sc['visible']['top']:.2f} from above, touches {['item_%d' % k for k in sc['touching']]}, under {['item_%d' % k for k in sc['covered_by']]}")
    for k, o in enumerate(sc["objects"]):
        out += f"; item_{k}: {_what(o)}, {o['density']:g} kg/m3, at ({o['pos_mm'][0]:.0f}, {o['pos_mm'][1]:.0f}, {o['pos_mm'][2]:.0f})"
    b = sc.get("bin")
    if b:
        out += (f"; bin: {b['color']}, inside {b['inner_mm'][0]:g} x {b['inner_mm'][1]:g} x {b['height_mm']:g}, {b['mass_kg']:g} kg, at "
                f"({b['pos_mm'][0]:.0f}, {b['pos_mm'][1]:.0f}) turned {b['yaw_deg']:g} deg")
    for k, e in enumerate(sc.get("extras") or []):
        out += (f"; fixed to the table, \"{e['name']}\": " + " + ".join(_scenes.part_text(q) for q in e["parts"])
                + f" at ({e['xy_mm'][0]:g}, {e['xy_mm'][1]:g}, {e['z_mm']:g})")
    return out + " (mm)" + (f"; out of distribution: {', '.join(sc['ood'])}" if sc.get("ood") else "") + _scenes.mjcf_text(sc) + _scenes.cameras_text(sc)


def brief(sc):
    """A shorter line than describe(): the target, how much of it is seen, how many objects."""
    t = sc["target"]
    return (f"{sc['level']}{', placed by hand' if sc.get('placed') else ''}: {len(sc['objects'])} objects; target item_{t}, the {target_name(sc)} "
            f"({_what(sc['objects'][t])}) at ({sc['objects'][t]['pos_mm'][0]:.0f}, {sc['objects'][t]['pos_mm'][1]:.0f}, {sc['objects'][t]['pos_mm'][2]:.0f}), "
            f"seen {sc['visible']['scene']:.2f} from the scene camera and {sc['visible']['top']:.2f} from above, touches "
            f"{len(sc['touching'])} objects, under {['item_%d' % k for k in sc['covered_by']]}")


# ---------------------------------------------------------------------- scenes set up by hand (a session's own)
LIMITS = dict(n=(2, 16), size=(8.0, 160.0), density=(50.0, 5000.0), x=(-480.0, -100.0), y=(-360.0, 330.0), parts=8, part_pos=120.0)
PARTS = dict(box=3, cylinder=2, ball=1, capsule=2)              # a custom asset's parts: how many sizes each has
ROLES = ("neighbour", "cover", "other")
BUILD_TRIES = 30
_SPEC_KEYS = {"level", "seed", "n_objects", "centre_mm", "target", "objects"}
_OBJECT_KEYS = {"shape", "size_mm", "color", "rgb", "density", "role", "name", "parts", "pos_mm", "euler_deg"}


def _num(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"{what}: a number from {lo:g} to {hi:g}")
    return float(v)


def _vec(v, n, lo, hi, what):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise ValueError(f"{what}: {n} number{'s' if n > 1 else ''}")
    return [_num(x, lo, hi, what) for x in v]


def _check_object(o, what, arm=None):
    """An object's spec, checked -> the fields it gives (shape, size_mm, color, rgb, density, role, name, parts, pos_mm,
    euler_deg)."""
    if not isinstance(o, dict):
        raise ValueError(f"{what}: a JSON object")
    if set(o) - _OBJECT_KEYS:
        raise ValueError(f"{what}: unknown field(s) {', '.join(sorted(set(o) - _OBJECT_KEYS))}")
    out, s = {}, o.get("shape")
    if "parts" in o and s is None:
        s = "custom"
    if s is not None:
        if s not in SHAPES + ("custom",):
            raise ValueError(f"{what}: shape is one of {', '.join(SHAPES)}, or custom (with name and parts)")
        out["shape"] = s
    if s == "custom":
        name, parts = o.get("name"), o.get("parts")
        if not isinstance(name, str) or not 2 <= len(name) <= 40 or not all(c.isalnum() or c in " -" for c in name):
            raise ValueError(f"{what}: a custom object needs a name (2 to 40 letters, digits, spaces, hyphens), e.g. \"U-shaped block\"")
        if not isinstance(parts, list) or not 1 <= len(parts) <= LIMITS["parts"]:
            raise ValueError(f"{what}: parts is a list of 1 to {LIMITS['parts']} parts")
        out["name"], out["parts"] = name.strip(), []
        for i, p in enumerate(parts):
            w = f"{what}.parts[{i}]"
            if not isinstance(p, dict) or p.get("type") not in PARTS or set(p) - {"type", "size_mm", "pos_mm", "euler_deg"}:
                raise ValueError(f"{w}: {{type: {' | '.join(PARTS)}, size_mm, pos_mm, euler_deg}}")
            z = _vec(p.get("size_mm"), PARTS[p["type"]], 4.0, LIMITS["size"][1], f"{w}.size_mm")
            if p["type"] == "capsule" and z[1] < z[0] + 2:
                raise ValueError(f"{w}: a capsule's length (the whole of it) is more than its diameter")
            out["parts"].append(dict(type=p["type"], size_mm=z, pos_mm=_vec(p.get("pos_mm", [0, 0, 0]), 3, -LIMITS["part_pos"], LIMITS["part_pos"], f"{w}.pos_mm"),
                                     euler_deg=_vec(p.get("euler_deg", [0, 0, 0]), 3, -360.0, 360.0, f"{w}.euler_deg")))
        if "size_mm" in o:
            raise ValueError(f"{what}: a custom object's sizes are its parts'")
        vol = [dict(box=lambda z: z[0] * z[1] * z[2], cylinder=lambda z: math.pi / 4 * z[0] ** 2 * z[1], ball=lambda z: math.pi / 6 * z[0] ** 3,
                    capsule=lambda z: math.pi / 4 * z[0] ** 2 * (z[1] - z[0] / 3))[p["type"]](p["size_mm"]) for p in out["parts"]]
        c = np.average([p["pos_mm"] for p in out["parts"]], axis=0, weights=vol)        # the object's centre: its parts' (by volume)
        for p in out["parts"]:
            p["pos_mm"] = [round(float(v), 2) for v in np.array(p["pos_mm"]) - c]
    elif "name" in o or "parts" in o:
        raise ValueError(f"{what}: name and parts are a custom object's")
    elif "size_mm" in o:
        if s is None:
            raise ValueError(f"{what}: size_mm needs shape")
        out["size_mm"] = _vec(o["size_mm"], dict(box=3, cylinder=2, ball=1)[s], *LIMITS["size"], f"{what}.size_mm")
    if "rgb" in o:
        out["rgb"] = [int(v) for v in _vec(o["rgb"], 3, 0, 255, f"{what}.rgb")]
    if "color" in o or "rgb" in o:
        c = o.get("color")
        if not isinstance(c, str) or not 2 <= len(c) <= 20 or not c.replace(" ", "").replace("-", "").isalpha():
            raise ValueError(f"{what}: color is a colour's name (rgb needs one too: the task's text names the object by it)")
        if c not in COLOURS and "rgb" not in o:
            raise ValueError(f"{what}: color is one of {', '.join(COLOURS)}, or any name together with rgb")
        out["color"] = c
    if "density" in o:
        out["density"] = _num(o["density"], *LIMITS["density"], f"{what}.density")
    if "role" in o:
        if o["role"] not in ROLES:
            raise ValueError(f"{what}: role is one of {', '.join(ROLES)}")
        out["role"] = o["role"]
    if "pos_mm" in o:
        p = _vec(o["pos_mm"], 3, -600.0, 600.0, f"{what}.pos_mm")
        L = ONE_ARM["limits"] if arm else LIMITS
        out["pos_mm"] = [_num(p[0], *L["x"], f"{what}.pos_mm x"), _num(p[1], *L["y"], f"{what}.pos_mm y"), _num(p[2], 0.0, 400.0, f"{what}.pos_mm z")]
        out["euler_deg"] = _vec(o.get("euler_deg", [0, 0, 0]), 3, -360.0, 360.0, f"{what}.euler_deg")
    elif "euler_deg" in o:
        raise ValueError(f"{what}: euler_deg goes with pos_mm")
    return out


def _fill(rng, given, role):
    """An object: what its spec gives, the rest drawn within the training limits (role: _size's)."""
    o = {k: v for k, v in given.items() if k not in ("role", "pos_mm", "euler_deg")}
    if "shape" not in o:
        o["shape"] = "box" if role == "cover" else str(rng.choice(SHAPES))
    if o["shape"] != "custom" and "size_mm" not in o:
        o["size_mm"] = _size(rng, o["shape"], role)
    if "color" not in o:
        o["color"] = str(rng.choice(list(COLOURS)))
    if "density" not in o:
        o["density"] = round(float(rng.uniform(*TRAIN["density"])), -1)
    if "rgb" not in o:
        o["rgb"] = _jit(rng, COLOURS[o["color"]])
    return o


def _place(sc, rng, poses):
    """The objects set at the given poses [(pos mm, euler deg)], one after another, each left to fall for 0.5 s -> why
    this is not a scene ("" if it is); the poses at rest go into the scene. (No condition of the level is checked.)"""
    H = _Heap(sc, rng); d = H.d
    for k, (p, e) in enumerate(poses):
        H.put(k, [v / 1000 for v in p], _euler_quat(e))
        H.run(0.5)
    if not H.rest():
        return "the objects do not come to rest"
    P, r = np.array([d.xpos[b] for b in H.body]), np.array(H.r)
    if np.any(P[:, 2] < 0) or not (-0.585 < float(np.min(P[:, 0] - r)) and float(np.max(P[:, 0] + r)) < 0.0 and float(np.min(P[:, 1] - r)) > -0.585
                                   and float(np.max(P[:, 1] + r)) < 0.585):
        return "an object ends off the table, or too near its edge or the robots' side (x > 0)"
    if np.any((P[:, 1] + r > RULER[0] / 1000 - 0.005) & (P[:, 1] - r < RULER[1] / 1000 + 0.005)):
        return "an object ends on the ruler"
    if sc.get("arm") and np.any(out_of_reach(P)):
        return "an object ends where the arm does not reach it from above (too near the robot, beside the parked arm, or too far)"
    touch, _ = H.touching(0)
    seen, cover = _seen(H.m, d, sc)
    for k, o in enumerate(sc["objects"]):
        o["pos_mm"] = [round(float(v) * 1000, 2) for v in P[k]]
        o["quat"] = [round(float(v), 6) for v in d.xquat[H.body[k]]]
    sc.update(visible=seen, touching=touch, covered_by=cover)
    return ""


def build(spec, seed=0, levels=LEVELS, tries=BUILD_TRIES, arm=None):
    """A scene set up by hand -> the scene; ValueError with the reason if it cannot be. spec: {"level", "seed"} alone is
    sample(level, seed) (a seed below OOD_FROM). arm="left": a one-armed scene (ONE_ARM's places and limits). With
    more, a heap of its own (not in a bin):

        n_objects    how many objects, the target included (default: as many as the level's heaps have, and no fewer
                     than the target and the objects given; of objects placed by pos_mm: those given)
        centre_mm    [x, y]: where the heap is (about where the target lies)
        target       an object: the target
        objects      objects: others of the heap
        an object    {"shape": "box" | "cylinder" | "ball", "size_mm", "color", "rgb", "density", "role"}, or an asset of
                     its own {"shape": "custom", "name", "parts": [{"type": "box" | "cylinder" | "ball" | "capsule",
                     "size_mm", "pos_mm", "euler_deg"}], "color", ...}; role: "neighbour" (set next to the target), "cover"
                     (dropped flat on it) or "other" (dropped on the heap; medium: around the target)

    Every field can be left out and is then drawn within the training limits, by the seed. The heap is made as
    sample()'s, and meets its level's conditions. If the target and every object has pos_mm ([x, y, z], its centre;
    with euler_deg), they are instead set at these poses one after another and left to come to rest: such a scene
    ("placed") need not meet its level's conditions."""
    if not isinstance(spec, dict):
        raise ValueError("a scene is a JSON object")
    if set(spec) - _SPEC_KEYS:
        raise ValueError(f"scene: unknown field(s) {', '.join(sorted(set(spec) - _SPEC_KEYS))}")
    level, seed = spec.get("level"), spec.get("seed", seed)
    if level not in levels:
        raise ValueError(f"level: one of {', '.join(levels)}")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < OOD_FROM:
        raise ValueError(f"seed: a whole number from 0 to {OOD_FROM - 1}")
    if arm not in ARMS:
        raise ValueError('arm: "left" (a one-armed scene) or none')
    if not set(spec) - {"level", "seed"}:
        return sample(level, seed, arm)
    if level == "extra_hard":
        raise ValueError("extra_hard: only by level and seed")
    if not isinstance(spec.get("objects", []), list):
        raise ValueError("objects: a list of objects")
    tgt = _check_object(spec.get("target", {}), "target", arm)
    others = [_check_object(o, f"objects[{i}]", arm) for i, o in enumerate(spec.get("objects", []))]
    if "role" in tgt:
        raise ValueError("target: the target has no role")
    n_given = None if spec.get("n_objects") is None else int(_num(spec["n_objects"], *LIMITS["n"], "n_objects"))
    if not 1 + len(others) <= (1 + len(others) if n_given is None else n_given) <= LIMITS["n"][1]:
        raise ValueError(f"n_objects: from {LIMITS['n'][0]} to {LIMITS['n'][1]}, and no fewer than the target and the objects given")
    with_pose = ["pos_mm" in o for o in [tgt] + others]
    placed = all(with_pose) and n_given in (None, 1 + len(others))
    if any(with_pose) and not placed:
        raise ValueError("pos_mm: either on the target and on every object (and no object drawn at random), or on none")
    name_of = lambda o: (o.get("color"), o.get("name") if o.get("shape") == "custom" else o.get("shape"))
    if None not in name_of(tgt) and any(name_of(o) == name_of(tgt) for o in others):
        raise ValueError("objects: another object has the target's colour and shape (the task's text could not name the target)")
    L, here = (ONE_ARM["limits"], ONE_ARM) if arm else (LIMITS, TRAIN)
    centre = [_num(spec["centre_mm"][0], *L["x"], "centre_mm x"), _num(spec["centre_mm"][1], *L["y"], "centre_mm y")] \
        if isinstance(spec.get("centre_mm"), (list, tuple)) and len(spec["centre_mm"]) == 2 else None
    if "centre_mm" in spec and centre is None:
        raise ValueError("centre_mm: [x, y]")
    if placed and centre is not None:
        raise ValueError("centre_mm: not with objects placed by pos_mm")
    why = []
    for attempt in range(1 if placed else tries):
        rng = np.random.default_rng([17, LEVELS.index(level), int(seed), attempt])
        n = n_given if n_given is not None else 1 + len(others) if placed else \
            max(1 + len(others), int(rng.integers(TRAIN["n"][level][0], TRAIN["n"][level][1] + 1)))
        c = centre or [round(float(rng.uniform(*here["x"])), 1), round(float(rng.uniform(*here["y"])), 1)]
        covers = 0 if level == "medium" else int(rng.integers(1, 3))
        ring = int(rng.integers(3, 6))
        R = {r: [o for o in others if o.get("role") == r] for r in ROLES}
        free = [o for o in others if "role" not in o]
        covers = max(len(R["cover"]), min(covers, n - 1 - len(R["neighbour"]) - len(R["other"])))
        ring = max(len(R["neighbour"]), min(ring, n - 1 - covers - len(R["other"])))
        if placed:
            order = [(o, "any") for o in others]
        else:
            while free and len(R["neighbour"]) < ring:
                R["neighbour"].append(free.pop(0))
            while free and len(R["cover"]) < covers:
                R["cover"].append(free.pop(0))
            R["other"] += free
            ring, covers = len(R["neighbour"]) + max(0, ring - len(R["neighbour"])), len(R["cover"]) + max(0, covers - len(R["cover"]))
            order = ([(o, "any") for o in R["neighbour"]] + [({}, "any")] * (ring - len(R["neighbour"]))
                     + [(o, "cover") for o in R["cover"]] + [({}, "cover")] * (covers - len(R["cover"])) + [(o, "any") for o in R["other"]])
            order += [({}, "any")] * (n - 1 - len(order))
        objs = [_fill(rng, tgt, "target")]
        for given, role in order:
            for _ in range(50):                                     # (no other object of the target's colour and shape)
                o = _fill(rng, given, role)
                if (o["color"], noun(o)) != (objs[0]["color"], noun(objs[0])):
                    break
            else:
                raise ValueError("objects: another object has the target's colour and shape")
            objs.append(o)
        sc = dict(kind=KIND, level=level, seed=int(seed), objects=objs, target=0, bin=None, centre_mm=c, _ring=ring, _covers=covers, ood=[],
                  own=True, placed=bool(placed))
        if arm:
            sc["arm"] = arm
        w = _place(sc, rng, [(o["pos_mm"], o["euler_deg"]) for o in [tgt] + others]) if placed else _make(sc, rng)
        if not w:
            sc = {k: v for k, v in sc.items() if not k.startswith("_")}
            sc["attempts"] = attempt + 1
            return sc
        why.append(w)
    raise ValueError((f"no {level} heap of these objects in {tries} tries: " if not placed else "placed by hand: ") + "; ".join(sorted(set(why))))


# ---------------------------------------------------------------------- a WORLD's scene (practice.py: world; a session with --worlds)
# The scene after the session's own world code changed it: not held to the limits of build(), only to what the
# simulation can hold (world_scene checks it):
#   objects    up to WORLD["n"]; any shape of WORLD_SIZES (the training shapes, the out-of-distribution ones, and "ring":
#              [outer diameter, height, hole diameter], with "sides": its outline and hole as polygons) or "custom" (parts);
#              "name": what the task's text calls it instead of its shape's noun; pos_mm with quat or euler_deg
#   target     the index of any of them
#   extras     bodies of parts FIXED to the table (scenes.world_extras: a mat, a book, a box, a stand)
#   friction, contact   on an object or an extra: its sliding friction and its contacts' hardness (scenes.CONTACT_KEYS)
#   a part of type "hull"   (a custom object's, an extra's) the convex hull of points_mm: a convex mesh
#   settle     how the heap comes to rest before the episode: "together" (default: all objects set at their poses at once),
#              "in_order" (one after another, 0.5 s each: a heap built from the bottom up) or "none" (taken as given);
#              a scene whose objects and extras the world did not change is taken as it is
WORLD = dict(n=(1, 24), size=(3.0, 300.0), density=(30.0, 9000.0), x=(-700.0, 150.0), y=(-600.0, 600.0), z=(0.0, 400.0), wall=1.5,
             sides=(3, 24), parts=10, part=(1.0, 300.0), part_pos=200.0)
WORLD_SIZES = dict(box=3, cylinder=2, ball=1, pill=2, L=3, steps=3, T=4, cross=3, hexagon=2, ring=3)
SETTLE = ("together", "in_order", "none")
_WORLD_OBJECT = ("shape", "size_mm", "color", "rgb", "density", "pos_mm", "quat", "euler_deg", "name", "parts", "sides") + _scenes.CONTACT_KEYS
_WORLD_KEEPS = ("centre_mm", "own", "placed", "attempts")          # (what build() noted in the scene: kept as it was)


def _world_object(o, what):
    W = WORLD
    if not isinstance(o, dict):
        raise ValueError(f"{what}: a dict")
    if set(o) - set(_WORLD_OBJECT):
        raise ValueError(f"{what}: unknown field(s) {', '.join(sorted(set(o) - set(_WORLD_OBJECT)))}; there are {', '.join(_WORLD_OBJECT)}")
    s = o.get("shape")
    out = dict(shape=s)
    if s == "custom":
        parts = o.get("parts")
        if not isinstance(parts, list) or not 1 <= len(parts) <= W["parts"]:
            raise ValueError(f"{what}.parts: a list of 1 to {W['parts']} parts")
        out["parts"] = []
        for i, p in enumerate(parts):
            w = f"{what}.parts[{i}]"
            if not isinstance(p, dict) or p.get("type") not in tuple(PARTS) + (_scenes.HULL,) or set(p) - {"type", "size_mm", "points_mm", "pos_mm", "euler_deg"}:
                raise ValueError(f"{w}: {{type: {' | '.join(PARTS)} | {_scenes.HULL}, size_mm (a hull: points_mm), pos_mm, euler_deg}}")
            if p["type"] == _scenes.HULL:
                shape = dict(points_mm=_scenes.hull_points(p, w))
            else:
                if p.get("points_mm") is not None:
                    raise ValueError(f"{w}: points_mm are a hull's")
                z = _vec(p.get("size_mm"), PARTS[p["type"]], *W["part"], f"{w}.size_mm")
                if p["type"] == "capsule" and z[1] < z[0] + 1:
                    raise ValueError(f"{w}: a capsule's length (the whole of it) is more than its diameter")
                shape = dict(size_mm=z)
            out["parts"].append(dict(type=p["type"], **shape, pos_mm=_vec(p.get("pos_mm", [0, 0, 0]), 3, -W["part_pos"], W["part_pos"], f"{w}.pos_mm"),
                                     euler_deg=_vec(p.get("euler_deg", [0, 0, 0]), 3, -360.0, 360.0, f"{w}.euler_deg")))
    elif s in WORLD_SIZES:
        if "parts" in o:
            raise ValueError(f"{what}: parts are a custom object's")
        z = out["size_mm"] = _vec(o.get("size_mm"), WORLD_SIZES[s], *W["size"], f"{what}.size_mm ({WORLD_SIZES[s]} for a {s})")
        if s == "ring":
            if z[0] < z[2] + 2 * W["wall"]:
                raise ValueError(f"{what}: a ring is [outer diameter, height, hole diameter], its wall at least {W['wall']:g} mm thick")
            if o.get("sides"):
                out["sides"] = int(_num(o["sides"], *W["sides"], f"{what}.sides"))
        elif o.get("sides"):
            raise ValueError(f"{what}: sides are a ring's")
        if s == "pill" and z[1] < z[0] + 1:
            raise ValueError(f"{what}: a pill is [diameter, whole length], longer than it is thick")
        if s in ("L", "cross") and z[1] >= z[0]:
            raise ValueError(f"{what}: [arm length, arm width, height], the width under the length")
    else:
        raise ValueError(f"{what}: shape is one of {', '.join(WORLD_SIZES)}, or custom (with parts)")
    name = o.get("name")
    if name is not None or s == "custom":
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 40 or not all(c.isalnum() or c in " -" for c in name):
            raise ValueError(f"{what}: name is what the task's text calls the object (2 to 40 letters, digits, spaces, hyphens)"
                             + ("; a custom object needs one" if s == "custom" else ""))
        out["name"] = name.strip()
    c = o.get("color")
    if not isinstance(c, str) or not 2 <= len(c) <= 20 or not c.replace(" ", "").replace("-", "").isalpha():
        raise ValueError(f"{what}: color is the colour's name (the task's text names the target by it)")
    out["color"] = c
    out["rgb"] = [int(round(v)) for v in _vec(o.get("rgb"), 3, 0, 255, f"{what}.rgb")]
    out["density"] = _num(o.get("density"), *W["density"], f"{what}.density")
    out.update(_scenes.world_contact(o, what))
    p = o.get("pos_mm")
    if not isinstance(p, (list, tuple)) or len(p) != 3:
        raise ValueError(f"{what}.pos_mm: [x, y, z], the object's centre")
    out["pos_mm"] = [_num(p[0], *W["x"], f"{what}.pos_mm x"), _num(p[1], *W["y"], f"{what}.pos_mm y"), _num(p[2], *W["z"], f"{what}.pos_mm z")]
    if o.get("euler_deg") is not None and o.get("quat") is None:
        out["quat"] = [round(v, 6) for v in _euler_quat(_vec(o["euler_deg"], 3, -360.0, 360.0, f"{what}.euler_deg"))]
    else:
        q = np.array(_vec(o.get("quat", [1, 0, 0, 0]), 4, -1.0001, 1.0001, f"{what}.quat"))
        if np.linalg.norm(q) < 0.5:
            raise ValueError(f"{what}.quat: [w, x, y, z], a unit quaternion")
        out["quat"] = [round(float(v), 6) for v in q / np.linalg.norm(q)]
    return out


def _settle(sc, how):
    """The heap of a world's scene brought to rest (how: SETTLE) -> the scene with the poses at rest, and what is seen
    of its target; ValueError if it does not come to rest or leaves the table."""
    H = _Heap(sc, np.random.default_rng(0)); d = H.d
    if how != "none":
        for k, o in enumerate(sc["objects"]):
            H.put(k, [v / 1000 for v in o["pos_mm"]], o["quat"])
            if how == "in_order":
                H.run(0.5)
        if not H.rest(limit=8.0):
            raise ValueError("the scene's objects do not come to rest (do they overlap, or roll?)")
        P = np.array([d.xpos[b] for b in H.body])
        if np.any(P[:, 2] < -0.01) or np.any(np.abs(P[:, 0]) > 0.62) or np.any(np.abs(P[:, 1]) > 0.62):
            raise ValueError("an object of the scene ends off the table")
        for k, o in enumerate(sc["objects"]):
            o["pos_mm"] = [round(float(v) * 1000, 2) for v in P[k]]
            o["quat"] = [round(float(v), 6) for v in d.xquat[H.body[k]]]
    else:
        for k, o in enumerate(sc["objects"]):
            H.put(k, [v / 1000 for v in o["pos_mm"]], o["quat"])
    touch, _ = H.touching(sc["target"])
    seen, cover = _seen(H.m, d, sc)
    sc.update(visible=seen, touching=touch, covered_by=cover)
    return sc


def world_scene(sc, before=None):
    """A scene as a world's code returned it -> the scene the simulation builds: every value checked against WORLD
    (ValueError with the reason), unknown fields refused; if the world changed the objects or the extras, the heap is
    brought to rest again (sc["settle"]: SETTLE). before: the scene the world's code got (its level, arm and bin stay)."""
    W = WORLD
    if not isinstance(sc, dict) or sc.get("kind") != KIND:
        raise ValueError('a world\'s scene(...) returns the scene: the dict it got ("kind": "clutter"), changed')
    known = ("kind", "level", "seed", "objects", "target", "bin", "visible", "touching", "covered_by", "ood", "arm", "extras", "settle",
             "mjcf", "joints", "cameras") + _WORLD_KEEPS
    if set(sc) - set(known):
        raise ValueError(f"scene: unknown field(s) {', '.join(sorted(set(sc) - set(known)))}; there are {', '.join(known)}")
    ref = before if before is not None else sc
    if sc.get("level") != ref.get("level") or sc.get("level") not in LEVELS or sc.get("arm") != ref.get("arm") or sc.get("arm") not in ARMS:
        raise ValueError("scene: level and arm stay what they were")
    if json.dumps(sc.get("bin"), sort_keys=True) != json.dumps(ref.get("bin"), sort_keys=True):
        raise ValueError("scene: the bin stays what it was")
    objs = sc.get("objects")
    if not isinstance(objs, list) or not W["n"][0] <= len(objs) <= W["n"][1]:
        raise ValueError(f"objects: a list of {W['n'][0]} to {W['n'][1]} objects")
    t = sc.get("target")
    if isinstance(t, bool) or not isinstance(t, int) or not 0 <= t < len(objs):
        raise ValueError("target: the index of the target in objects")
    how = sc.get("settle", "together")
    if how is False or how is None:
        how = "none"
    if how is True:
        how = "together"
    if how not in SETTLE:
        raise ValueError(f"settle: one of {', '.join(SETTLE)}")
    extras = _scenes.world_extras(sc.get("extras"))
    if any(not e["fixed"] for e in extras):
        raise ValueError('extras: in a heap\'s scene they are fixed to the table ("fixed": True); a free body is an object of "objects"')
    out = dict(kind=KIND, level=sc["level"], seed=int(sc.get("seed", 0)), objects=[_world_object(o, f"objects[{k}]") for k, o in enumerate(objs)],
               target=t, bin=sc.get("bin"), ood=list(sc.get("ood") or []), visible=sc.get("visible"), touching=sc.get("touching"),
               covered_by=sc.get("covered_by"))
    out.update({k: sc[k] for k in _WORLD_KEEPS if k in sc})
    if sc.get("arm"):
        out["arm"] = sc["arm"]
    if extras:
        out["extras"] = extras
    if sc.get("mjcf"):                                              # (bodies of the world's own, as MJCF: scenes.world_mjcf)
        out["mjcf"] = _scenes.world_mjcf(sc["mjcf"])
    j = _scenes.world_joints(sc.get("joints"), sc.get("mjcf"))
    if j:
        out["joints"] = j
    cams = _scenes.world_cameras(sc.get("cameras"))
    if cams:
        out["cameras"] = cams
    same = before is not None and "settle" not in sc and all(
        json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        for a, b in ((sc.get("objects"), before.get("objects")), (sc.get("extras") or [], before.get("extras") or []),
                     (sc.get("mjcf"), before.get("mjcf")), (sc.get("joints") or {}, before.get("joints") or {})))
    if same and isinstance(out["visible"], dict):                  # (only the target, or nothing, was changed)
        if t != before.get("target"):
            _settle(out, "none")
        return out
    return _settle(out, how)


# ---------------------------------------------------------------------- the task
_HOW = dict(
    medium="It lies on the table among the others, which touch it; it can be seen.",
    hard="It lies under other objects: at the start it cannot be seen, or only a part of it.",
    extra_hard="The heap is in an open bin that stands on the table (not fixed to it), and the {name} lies under other objects: at the "
               "start it cannot be seen, or only a part of it.")


def _text(level, name, arm=None):
    return (f"Task: take the {name} out of the heap of objects that lies on the table in front of the robot, and hold it up. "
            + (f"The robot works with its {arm} arm alone (the other one stays parked). " if arm else "")
            + _HOW[level].format(name=name) + " The other objects may be pushed aside, or picked up and put down elsewhere on the "
            f"table. At the end the robot must hold the {name} still with its centre at least {LIFT:.0f} mm above the table, touching "
            "nothing but the robot, and nothing may have fallen off the table; then call done() (or let the program return).")


def text(sc):
    """The task's text in this scene: it names the target."""
    return _text(sc["level"], target_name(sc), sc.get("arm"))


# the task's text without a scene (a session's prompt): each episode's own text names its object
_NAMED = ("Task: take the named object", "Task: take one object, named in each episode's task text by its colour and shape "
          "(e.g. \"the red cylinder\"),")
TEXT = {level: _text(level, "named object").replace(*_NAMED) for level in LEVELS}
TEXT_ONE_ARM = {level: _text(level, "named object", "left").replace(*_NAMED) for level in LEVELS if level != "extra_hard"}


def names(sc):
    return [f"item_{k}" for k in range(len(sc["objects"]))] + (["bin"] if sc.get("bin") else [])


def layout(sc):
    """{object: (x, y, z, qw, qx, qy, qz)} in m, for core.BiCore.reset."""
    out = {f"item_{k}": (*(v / 1000 for v in o["pos_mm"]), *o["quat"]) for k, o in enumerate(sc["objects"])}
    if sc.get("bin"):
        out["bin"] = (*(v / 1000 for v in sc["bin"]["pos_mm"]), *sc["bin"]["quat"])
    return out


def paint(m, sc):
    pass


def model_xml(path, sc):
    """The environment's model (models/NAME.xml) with the scene's objects (and bin) as further free bodies
    obj_item_k (obj_bin) -> MJCF text (paths absolute)."""
    path = Path(path)
    root = ET.parse(path).getroot()
    comp = root.find("compiler")
    comp.set("meshdir", str((path.parent / comp.get("meshdir", ".")).resolve()))
    wb, asset = root.find("worldbody"), root.find("asset")
    new = 0
    if sc.get("bin"):
        b = ET.SubElement(wb, "body", name="obj_bin", pos="0.30 0.40 0.0005")
        ET.SubElement(b, "freejoint", name="obj_bin_free")
        _bin_geoms(b, sc["bin"]); new += 1
    for k, o in enumerate(sc["objects"]):
        b = ET.SubElement(wb, "body", name=f"obj_item_{k}", pos=f"{0.30 + 0.05 * k:.2f} 0.10 0.1")
        ET.SubElement(b, "freejoint", name=f"obj_item_{k}_free")
        _object_geoms(b, asset, f"item_{k}", o); new += 1
    _fixed_extras(wb, sc, asset)
    _scenes.add_mjcf(root, sc)                                      # (a world's own bodies: the last of all)
    key = root.find("keyframe").find("key")                         # (the new bodies are the last: their qpos too)
    key.set("qpos", key.get("qpos") + " 0 0 0 1 0 0 0" * new + _scenes.mjcf_qpos(sc))
    return ET.tostring(root, encoding="unicode")


def poses(S):
    """The TRUE poses of the scene's objects in the simulation S -> {name: (pos m, quat)} (for visibility())."""
    return {n: (S.d.xpos[S.objects[n]].copy(), S.d.xquat[S.objects[n]].copy()) for n in names(S.scene)}


def judge(S):
    """TRUE state: the robot holds the target, its centre LIFT above the table, touching nothing else; nothing has
    fallen off the table."""
    sc = S.scene
    t = f"item_{sc['target']}"
    p, _ = S.object_pose(t)
    parts, other = S.touching(t)
    fallen = [n for n in names(sc) if S.object_pose(n)[0][2] < S.table_z - 0.05]
    held = bool(parts) and not other
    high = bool(p[2] - S.table_z >= LIFT / 1000)
    return dict(ok=bool(held and high and not fallen), target=t, held=held, height_mm=round(float(p[2] - S.table_z) * 1000),
                held_by={{"R": "right", "L": "left"}[k]: sorted(v) for k, v in parts.items()},
                touches=sorted(n.removeprefix("obj_") for n in other), fallen=fallen)


def truth(ep):
    """The TRUE state for practice (practice.py): each object's centre (mm), the target's name, how much of it is seen."""
    S = ep.S
    out = {n: [round(float(v) * 1000, 1) for v in S.object_pose(n)[0]] for n in names(S.scene)}
    seen, cover = visibility(S.scene, poses(S))
    out.update(target=f"item_{S.scene['target']}", target_seen=seen, target_covered_by=[f"item_{k}" for k in cover])
    return out


def sheet(level, seeds, path, env="sim_bimanual_grippers", arm=None):
    """A picture of the scenes of pick_clutter_<level>, one row each: the scene camera's first image, the heap from
    nearer by, and the same with the other objects made transparent (where the target is)."""
    import gc
    import cv2
    import mujoco
    from .tasks import Episode
    rows = []
    for seed in seeds:
        ep = Episode(env, f"pick_clutter_{'one_arm_' if arm else ''}{level}", seed=seed, sensors="ideal"); S, sc = ep.S, ep.scene
        cam = mujoco.MjvCamera(); cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        c = np.mean([S.object_pose(n)[0] for n in names(sc)], axis=0)
        cam.lookat[:] = [c[0], c[1], 0.03]; cam.distance, cam.azimuth, cam.elevation = 0.62, 205, -48
        r = S.renderer(480, 640)
        tiles = [ep.robot.look(["scene"])["scene"].rgb]
        r.update_scene(S.d, camera=cam); tiles.append(r.render().copy())
        others = np.concatenate([S.obj_geoms[n] for n in names(sc) if n != f"item_{sc['target']}"])
        rgba = S.m.geom_rgba[others].copy(); S.m.geom_rgba[others, 3] = 0.18
        r.update_scene(S.d, camera=cam); tiles.append(r.render().copy()); S.m.geom_rgba[others] = rgba
        row = np.hstack(tiles)[..., ::-1].copy()
        cv2.rectangle(row, (0, 0), (row.shape[1], 44), (255, 255, 255), -1)
        cv2.putText(row, f"seed {seed}: the {target_name(sc)}; seen {sc['visible']['scene']:.0%} from the scene camera, {sc['visible']['top']:.0%} from above; "
                    f"{len(sc['objects'])} objects", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(row, ("out of distribution: " + ", ".join(sc["ood"])) if sc["ood"] else "in distribution", (8, 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 160) if sc["ood"] else (0, 110, 0), 1, cv2.LINE_AA)
        rows.append(row)
        S.renderers.clear(); del ep, S, r; gc.collect()             # (one GL context at a time: with the last episode's
                                                                    # still open, the next one's images are not its own)
    cv2.imwrite(str(path), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])


def main(argv=None):
    """Draw scenes and say what they are: python -m agentic_robotics.bimanual.clutter --level hard --seeds 0-3,5000-5003"""
    import argparse
    import json
    import time
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--level", required=True, choices=LEVELS)
    ap.add_argument("--arm", choices=["left"], help="one-armed scenes (pick_clutter_one_arm_*)")
    ap.add_argument("--seeds", required=True, help="e.g. 2000-2049 or 3,5,5001")
    ap.add_argument("--out", help="a JSON file for the scenes ({seed: scene})")
    ap.add_argument("--sheet", help="a picture of the scenes (a .jpg), see sheet()")
    a = ap.parse_args(argv)
    seeds = [s for part in a.seeds.split(",") for s in (range(int(part.split("-")[0]), int(part.split("-")[1]) + 1) if "-" in part else [int(part)])]
    out = {}
    for s in seeds:
        t = time.time()
        out[s] = sc = sample(a.level, s, a.arm)
        print(f"seed {s} ({sc['attempts']} tries, {time.time() - t:.1f} s): {describe(sc)}")
    if a.out:
        Path(a.out).write_text(json.dumps(out) + "\n")
    if a.sheet:
        sheet(a.level, seeds, a.sheet, arm=a.arm)


if __name__ == "__main__":
    main()
