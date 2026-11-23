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

A heap is made by simulating it (a model of the table, the bin and the objects, without the robot): the target and a
ring of objects are set on the table and pulled together, then the others are dropped on them one by one, and the
whole is left to come to rest; a heap that does not meet its level's conditions is thrown away and another one drawn.
An object's pose is that of its geometric centre. Object k is the body obj_item_k, the bin obj_bin.
"""
from __future__ import annotations

import functools
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

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
NOUN = {"box": "block", "cylinder": "cylinder", "ball": "ball", "pill": "capsule", "L": "L-shaped block", "steps": "stepped block",
        "T": "T-shaped block", "cross": "cross-shaped block", "hexagon": "hexagonal prism"}

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
    s, z = o["shape"], o["size_mm"]
    if s in ("box", "steps"):
        return math.sqrt(z[0] ** 2 + z[1] ** 2 + z[2] ** 2) / 2
    if s == "cylinder":
        return math.hypot(z[0], z[1]) / 2
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
    raise ValueError(f"unknown shape {s!r}")


def _thin_axis(o):
    """The body axis (0, 1, 2) along which the object is thinnest: up, it lies flat."""
    s, z = o["shape"], o["size_mm"]
    if s == "box":
        return int(np.argmin(z))
    if s == "cylinder":
        return 2 if z[1] <= z[0] else 0
    if s in ("L", "T", "steps", "cross", "hexagon"):
        return 2
    return 2 if s == "ball" else 1 if s == "pill" else 2


def _rgba(rgb):
    return " ".join(f"{v / 255:.3f}" for v in rgb) + " 1"


def _geom(body, name, kind, rgb, density, group, size=None, pos=(0, 0, 0), **kw):
    a = dict(name=name, type=kind, pos=" ".join(f"{v:.5f}" for v in pos), rgba=_rgba(rgb), density=f"{density:.1f}", group=str(group))
    if size is not None:
        a["size"] = " ".join(f"{v:.5f}" for v in size)
    return ET.SubElement(body, "geom", {**a, **kw})


def _object_geoms(body, asset, name, o, group=0):
    """The object's geoms about its centre (group: of the geoms that are seen; those that only collide: group 3)."""
    z, rgb, den, s = [v / 1000 for v in o["size_mm"]], o["rgb"], o["density"], o["shape"]
    g = lambda part, kind, size, pos=(0, 0, 0), c=FLAT, **kw: _geom(body, name + part, kind, rgb, den, group, size, pos, **{**c, **kw})
    if s == "box":
        g("", "box", (z[0] / 2, z[1] / 2, z[2] / 2))
    elif s == "cylinder":                                           # seen as a cylinder (which has the mass); it collides as a
        r, h, N = z[0] / 2, z[1] / 2, FACETS                        # prism of 2 N sides inside it, made of N boxes: on a box
        _geom(body, name, "cylinder", rgb, den, group, (r, h), contype="0", conaffinity="0")   # (the table) a cylinder geom
        for k in range(N):                                          # has one contact point and never comes to rest
            _geom(body, f"{name}_c{k}", "box", rgb, 0, 3, (r * math.cos(math.pi / (2 * N)), r * math.sin(math.pi / (2 * N)), h),
                  euler=f"0 0 {k * math.pi / N:.5f}", **FLAT)
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
    else:
        raise ValueError(f"unknown shape {s!r}")


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


def _free_model(sc):
    """The heap's own model: the table, the bin and the objects (free bodies), without the robot. The target's geoms
    are in group 1, all the others in group 0 (visibility())."""
    root = ET.Element("mujoco", model="clutter")
    ET.SubElement(root, "compiler", angle="radian")
    opt = ET.SubElement(root, "option", timestep="0.002", integrator="implicitfast", cone="elliptic", impratio="10")
    ET.SubElement(opt, "flag", multiccd="enable")                   # (as models/*.xml)
    asset = ET.SubElement(root, "asset")
    wb = ET.SubElement(root, "worldbody")
    ET.SubElement(wb, "geom", name="table_top", type="box", **TABLE)
    if sc.get("bin"):
        b = ET.SubElement(wb, "body", name="obj_bin", pos="0 0 0.001")
        ET.SubElement(b, "freejoint")
        _bin_geoms(b, sc["bin"])
    for k, o in enumerate(sc["objects"]):
        b = ET.SubElement(wb, "body", name=f"obj_item_{k}", pos=f"{2 + 0.3 * k:.1f} 3 1")
        ET.SubElement(b, "freejoint")
        _object_geoms(b, asset, f"item_{k}", o, group=1 if k == sc["target"] else 0)
    import mujoco
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


def _draw(level, seed, attempt):
    """The objects of a scene (not yet a heap): the target first. Below OOD_FROM within the training limits."""
    T = TRAIN
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
    place = str(rng.choice(list(OOD["place"]))) if "place" in which else None
    X, Y = (OOD["place"][place]["x"], OOD["place"][place]["y"]) if place else (T["x"], T["y"])
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
    return sc, rng


@functools.lru_cache(maxsize=512)
def _sample(level, seed):
    if level not in LEVELS:
        raise KeyError(f"unknown level {level!r}: {LEVELS}")
    why = []
    for attempt in range(TRIES):
        sc, rng = _draw(level, seed, attempt)
        w = _make(sc, rng)
        if not w:
            sc = {k: v for k, v in sc.items() if not k.startswith("_")}
            sc["attempts"] = attempt + 1
            return sc
        why.append(w)
    raise RuntimeError(f"no {level} heap for seed {seed} in {TRIES} tries: {sorted(set(why))}")


def sample(level, seed):
    """The scene of episode `seed` of pick_clutter_<level> (the same every time)."""
    import copy
    return copy.deepcopy(_sample(level, int(seed)))


def target_name(sc):
    """The target as the task's text names it: its colour and shape."""
    o = sc["objects"][sc["target"]]
    return f"{o['color']} {NOUN[o['shape']]}"


def describe(sc):
    """One line of text: what is in the scene."""
    t = sc["target"]
    out = (f"{sc['level']}: target item_{t}, the {target_name(sc)}, seen {sc['visible']['scene']:.2f} from the scene camera and "
           f"{sc['visible']['top']:.2f} from above, touches {['item_%d' % k for k in sc['touching']]}, under {['item_%d' % k for k in sc['covered_by']]}")
    for k, o in enumerate(sc["objects"]):
        out += f"; item_{k}: {o['color']} {o['shape']} {' x '.join(f'{v:g}' for v in o['size_mm'])} at ({o['pos_mm'][0]:.0f}, {o['pos_mm'][1]:.0f}, {o['pos_mm'][2]:.0f})"
    b = sc.get("bin")
    if b:
        out += (f"; bin: {b['color']}, inside {b['inner_mm'][0]:g} x {b['inner_mm'][1]:g} x {b['height_mm']:g}, {b['mass_kg']:g} kg, at "
                f"({b['pos_mm'][0]:.0f}, {b['pos_mm'][1]:.0f}) turned {b['yaw_deg']:g} deg")
    return out + " (mm)" + (f"; out of distribution: {', '.join(sc['ood'])}" if sc["ood"] else "")


# ---------------------------------------------------------------------- the task
_HOW = dict(
    medium="It lies on the table among the others, which touch it; it can be seen.",
    hard="It lies under other objects: at the start it cannot be seen, or only a part of it.",
    extra_hard="The heap is in an open bin that stands on the table (not fixed to it), and the {name} lies under other objects: at the "
               "start it cannot be seen, or only a part of it.")


def _text(level, name):
    return (f"Task: take the {name} out of the heap of objects that lies on the table in front of the robot, and hold it up. "
            + _HOW[level].format(name=name) + " The other objects may be pushed aside, or picked up and put down elsewhere on the "
            f"table. At the end the robot must hold the {name} still with its centre at least {LIFT:.0f} mm above the table, touching "
            "nothing but the robot, and nothing may have fallen off the table; then call done() (or let the program return).")


def text(sc):
    """The task's text in this scene: it names the target."""
    return _text(sc["level"], target_name(sc))


# the task's text without a scene (a session's prompt): each episode's own text names its object
TEXT = {level: _text(level, "named object").replace("Task: take the named object", "Task: take one object, named in each episode's "
        "task text by its colour and shape (e.g. \"the red cylinder\"),") for level in LEVELS}


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
    key = root.find("keyframe").find("key")                         # (the new bodies are the last: their qpos too)
    key.set("qpos", key.get("qpos") + " 0 0 0 1 0 0 0" * new)
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


def sheet(level, seeds, path, env="sim_bimanual_grippers"):
    """A picture of the scenes of pick_clutter_<level>, one row each: the scene camera's first image, the heap from
    nearer by, and the same with the other objects made transparent (where the target is)."""
    import gc
    import cv2
    import mujoco
    from .tasks import Episode
    rows = []
    for seed in seeds:
        ep = Episode(env, f"pick_clutter_{level}", seed=seed, sensors="ideal"); S, sc = ep.S, ep.scene
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
    ap.add_argument("--seeds", required=True, help="e.g. 2000-2049 or 3,5,5001")
    ap.add_argument("--out", help="a JSON file for the scenes ({seed: scene})")
    ap.add_argument("--sheet", help="a picture of the scenes (a .jpg), see sheet()")
    a = ap.parse_args(argv)
    seeds = [s for part in a.seeds.split(",") for s in (range(int(part.split("-")[0]), int(part.split("-")[1]) + 1) if "-" in part else [int(part)])]
    out = {}
    for s in seeds:
        t = time.time()
        out[s] = sc = sample(a.level, s)
        print(f"seed {s} ({sc['attempts']} tries, {time.time() - t:.1f} s): {describe(sc)}")
    if a.out:
        Path(a.out).write_text(json.dumps(out) + "\n")
    if a.sheet:
        sheet(a.level, seeds, a.sheet)


if __name__ == "__main__":
    main()
