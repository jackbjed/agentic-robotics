"""The bio-lab bench (docs/bimanual.md, task biolab_drops): two tube racks next to each other, a 50 mL centrifuge tube
with a screw cap standing in a hole of one of them, a pipette (a dropper with a squeeze bulb) standing in another
hole, and a cup of water. The task: move the tube to the other rack, open it, draw water from the cup with the
pipette, put three drops into the tube, close the tube.

A scene is a JSON-able dict (mm, degrees, colours by name):

    {"kind": "biolab", "seed": 7,
     "racks": [{"cells": [nx, ny], "cell_mm", "height_mm", "mass_kg", "color", "rgb", "pos_mm": [x, y], "yaw_deg"}, {...}],
     "tube": {"rack": 0, "cell": [i, j], "yaw_deg", "cap_color", "cap_rgb"},
     "pipette": {"rack": 1, "cell": [i, j], "yaw_deg", "color", "rgb"},              (the bulb's colour)
     "cup": {"inner_mm", "height_mm", "level_mm", "color", "rgb", "liquid_rgb", "pos_mm": [x, y]},
     "extras": [{"kind": "vial", "rack", "cell", "size_mm": [d, h], ...} | {"kind": "block", "size_mm", "pos_mm", "yaw_deg", ...}],
     "ood": [...]}                                  (which of its aspects are outside the training limits)

    sample(seed)           the scene of episode `seed`: within the training limits below OOD_FROM, and from OOD_FROM on
                           with some aspects outside them (OOD_ASPECTS)
    model_xml(file, scene), layout(scene), paint(model, scene)    the simulation's model and layout (core.BiCore)
    mechanisms(S)          the scene's mechanisms in the simulation S (core.BiCore.mech): the cap's thread, the liquid
    text(scene), judge(S), truth(ep)                              the task's text, verdict and true state (tasks.py)

Bodies: obj_rack_a, obj_rack_b, obj_tube (its cap: tube_cap, on a thread; tube_cap_free once it is off), obj_pipette
(its bulb: PLATES plates pip_b0.. on one degree of freedom), obj_cup, obj_extra_k. Origins: a rack's and the cup's at the
centre of the bottom, the tube's at its bottom tip, the pipette's at its tip.

The cap: as the bottle's (core.py), a threaded body on a hinge and a slide tied by the thread's pitch, swapped for a
free body when it has been turned UNSCREW_DEG; here the free cap is swapped back for the threaded one when it is set
back on the tube's mouth (level, centred, resting on the rim), and can then be screwed on.

The liquid is not simulated as a fluid: the pipette's content follows the bulb's squeeze (mm by which the bulb is
narrower than at rest). Squeezing pushes the content out, letting the bulb out draws in: water if the tip is in the
cup's water, air otherwise; water pushed out in the air leaves as one drop per DROP_MM of squeeze, and a drop falls
straight down: into the tube if its open mouth is below, into the cup, or on whatever else is there (spilled).
"""
from __future__ import annotations

import copy
import functools
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

from .scenes import COLOURS, JITTER, OOD_COLOURS, RULER

KIND = "biolab"
OOD_FROM = 5000                                                 # sample(seed): out-of-distribution scenes from this seed on
DROPS = 3                                                       # the drops asked for
NAMES = ("rack_a", "rack_b")
ZONE = ((-440.0, -110.0), (-200.0, RULER[0] - 7.0))             # mm: where things stand (both arms reach, off the ruler)

# the fixed sizes (mm; the model is built from them)
BASE, WALL = 4.0, 4.0                                           # a rack's base plate and walls
TUBE_D, TUBE_L, TUBE_WALL, MOUTH = 30.0, 115.0, 1.5, 30.0       # the tube; MOUTH: how deep it is hollow from the rim
TUBE_KG, CAP_KG = 0.020, 0.006
CAP_IN, CAP_OUT, CAP_SKIRT, CAP_LID = 33.0, 37.0, 12.0, 2.0     # the cap: diameters inside and outside, skirt, lid
PITCH = 4.0                                                     # mm the cap rises per turn
UNSCREW_DEG = 270.0                                             # turned this far (counter-clockwise from above) the cap is free
CLOSED_DEG = 60.0                                               # the cap counts as closed within this of fully screwed on
K_THREAD = PITCH / 1000 / (2 * math.pi)                         # m per rad
PIP_L, TIP_R, TIP_L = 88.0, 3.0, 24.0                           # the pipette, from its tip up: the tip tube,
PLUG_R, PLUG_Z = 13.0, 26.0                                     # the body (a ball that centres it in a rack's hole),
COLLAR_R, COLLAR_Z = 22.0, (36.0, 40.0)                         # the collar it stands on,
BULB_D, BULB_Z, BULB_TRAVEL = 26.0, (46.0, 86.0), 7.5           # the bulb: PLATES plates, each moves in by up to BULB_TRAVEL
PLATES = 16
PIP_KG = 0.012
BULB_K, BULB_F0 = 200.0 / PLATES, 1.2 / PLATES                  # N/m and N per plate: the bulb's spring and preload
DROP_MM = 1.5                                                   # mm of squeeze per drop
CAPACITY = int(2 * BULB_TRAVEL / DROP_MM)                       # drops the pipette holds
WATER = (70, 140, 235)

# the training limits: what a scene below OOD_FROM is drawn from
TRAIN = dict(long=(2, 4), short=(1, 2), cell=(32.0, 32.0), height=(45.0, 55.0), rack_kg=(0.4, 0.7), gap=(12.0, 40.0), yaw=(-20.0, 20.0),
             cup_inner=(60.0, 76.0), cup_h=(55.0, 70.0), cup_fill=(0.55, 0.8), cup_clear=(35.0, 80.0))
CAP_COLOURS = ("blue", "red", "green", "yellow", "white")
BULB_COLOURS = ("red", "blue", "green", "yellow", "grey")
CUP_COLOURS = ("white", "grey", "blue", "green", "yellow")
# outside them: what only sample(seed >= OOD_FROM) has
OOD_ASPECTS = ("racks", "place", "tube", "pipette", "cup", "extras")
OOD = dict(grids=((5, 1), (3, 3), (5, 2), (4, 3)), cell=(33.5, 36.0), height=((42.0, 43.0), (58.0, 62.0)), gap=(80.0, 160.0),
           yaw=((-60.0, -30.0), (30.0, 60.0)), cup_inner=((46.0, 54.0), (84.0, 96.0)), cup_h=((45.0, 50.0), (75.0, 90.0)),
           liquid={"pink": (235, 120, 165), "yellow": (240, 215, 80), "green": (120, 215, 150)}, vials=(1, 3), blocks=(1, 2))
TRIES = 200


# ---------------------------------------------------------------------- geometry of a scene
def _rot(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s], [s, c]])


def rack_outer(rk):
    """A rack's outer size along its own x and y (mm)."""
    p = rk["cell_mm"] + WALL
    return rk["cells"][0] * p + WALL, rk["cells"][1] * p + WALL


def cell_local(rk, i, j):
    p = rk["cell_mm"] + WALL
    return np.array([(i - (rk["cells"][0] - 1) / 2) * p, (j - (rk["cells"][1] - 1) / 2) * p])


def cell_xy(rk, i, j):
    """The centre of a rack's hole (i, j) on the table (mm), as the scene has the rack."""
    return np.array(rk["pos_mm"]) + _rot(rk["yaw_deg"]) @ cell_local(rk, i, j)


def cells(rk):
    return [(i, j) for i in range(rk["cells"][0]) for j in range(rk["cells"][1])]


def _rect_dist(rk, xy):
    """The distance from a point to a rack's outline (0 inside)."""
    q = np.abs(_rot(rk["yaw_deg"]).T @ (np.asarray(xy, float) - rk["pos_mm"])) - np.array(rack_outer(rk)) / 2
    return float(np.linalg.norm(np.maximum(q, 0)))


def _in_zone(xy, margin=0.0):
    return all(ZONE[k][0] + margin <= xy[k] <= ZONE[k][1] - margin for k in (0, 1))


def _corners(rk):
    a, b = rack_outer(rk)
    return [np.array(rk["pos_mm"]) + _rot(rk["yaw_deg"]) @ np.array([sx * a / 2, sy * b / 2]) for sx in (-1, 1) for sy in (-1, 1)]


def _near(a, b):
    """Two holes of one rack next to each other (also across a corner), or the same."""
    return abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) <= 1


# ---------------------------------------------------------------------- drawing a scene
def _jit(rng, base):
    return [int(np.clip(v + rng.integers(-JITTER, JITTER + 1), 0, 255)) for v in base]


def _pick(rng, r):
    """A value from a range (lo, hi) or from one of several ranges."""
    if isinstance(r[0], tuple):
        r = r[rng.integers(len(r))]
    return float(rng.uniform(*r))


def _colour(rng, table, names=None):
    n = str(rng.choice(sorted(names or table)))
    return n, _jit(rng, table[n])


def _draw(seed, attempt):
    rng = np.random.default_rng([seed, attempt, 4242])
    ood = [str(a) for a in sorted(rng.choice(OOD_ASPECTS, size=int(rng.integers(2, 4)), replace=False))] if seed >= OOD_FROM else []
    T = TRAIN
    racks = []
    for k in range(2):
        if "racks" in ood:
            grid = list(OOD["grids"][rng.integers(len(OOD["grids"]))])
            cell, h = _pick(rng, OOD["cell"]), _pick(rng, OOD["height"])
            col, rgb = _colour(rng, OOD_COLOURS)
        else:
            grid = [int(rng.integers(T["long"][0], T["long"][1] + 1)), int(rng.integers(T["short"][0], T["short"][1] + 1))]
            cell, h = _pick(rng, T["cell"]), _pick(rng, T["height"])
            col, rgb = _colour(rng, COLOURS)
        racks.append(dict(cells=grid, cell_mm=round(cell, 1), height_mm=round(h, 1), mass_kg=round(_pick(rng, T["rack_kg"]), 2), color=col, rgb=rgb))
    # the pair: side by side across the direction u, their long sides along t
    how = str(rng.choice(["apart", "behind", "turned"])) if "place" in ood else ""
    psi = _pick(rng, OOD["yaw"]) if how == "turned" else _pick(rng, T["yaw"]) + (90.0 if how == "behind" else 0.0)
    gap = _pick(rng, OOD["gap"]) if how == "apart" else _pick(rng, T["gap"])
    t, u = _rot(psi) @ [1.0, 0.0], _rot(psi) @ [0.0, 1.0]
    w = [rack_outer(r)[1] for r in racks]
    off = [-(gap / 2 + w[0] / 2) * u + rng.uniform(-15, 15) * t, (gap / 2 + w[1] / 2) * u + rng.uniform(-15, 15) * t]
    for r, o in zip(racks, off):
        r["yaw_deg"] = round(psi + float(rng.uniform(-2, 2)), 1)
    cup = dict(inner_mm=round(_pick(rng, OOD["cup_inner"] if "cup" in ood else T["cup_inner"]), 1),
               height_mm=round(_pick(rng, OOD["cup_h"] if "cup" in ood else T["cup_h"]), 1))
    cup["level_mm"] = round(max(cup["height_mm"] * _pick(rng, T["cup_fill"]), cup["height_mm"] - 25.0), 1)   # (the pipette's tip reaches it)
    cup["color"], cup["rgb"] = _colour(rng, OOD_COLOURS) if "cup" in ood else _colour(rng, COLOURS, CUP_COLOURS)
    cup["liquid"], cup["liquid_rgb"] = _colour(rng, OOD["liquid"]) if "cup" in ood else ("blue", _jit(rng, WATER))
    r_out = cup["inner_mm"] / 2 + 2.5
    front = "place" in ood and how != "behind" and rng.random() < 0.5         # the cup beyond the racks (seen from the robot)
    for _ in range(TRIES):
        c = np.array([rng.uniform(*ZONE[0]), rng.uniform(*ZONE[1])])
        for r, o in zip(racks, off):
            r["pos_mm"] = [round(float(v), 1) for v in c + o]
        if not all(_in_zone(p, 4) for r in racks for p in _corners(r)):
            continue
        for _ in range(40):
            xy = np.array([rng.uniform(*ZONE[0]), rng.uniform(*ZONE[1])])
            dist = [_rect_dist(r, xy) - r_out for r in racks]
            if not (_in_zone(xy, r_out + 2) and T["cup_clear"][0] <= min(dist) <= T["cup_clear"][1]):
                continue
            ahead = xy[0] - max(p[0] for r in racks for p in _corners(r)) - r_out
            if (ahead >= 20) == front and (front or abs((xy - c) @ t) <= max(rack_outer(r)[0] for r in racks) / 2 + 10 or how == "apart"):
                break
        else:
            continue
        cup["pos_mm"] = [round(float(v), 1) for v in xy]
        break
    else:
        return None
    # the tube and the pipette: the pipette not in a hole next to the tube's, and a hole of the other rack left that
    # has nothing standing next to it
    rt = int(rng.integers(2))
    ct = cells(racks[rt])[rng.integers(len(cells(racks[rt])))]
    extras = []
    if "extras" in ood:
        free = [(k, c_) for k in (0, 1) for c_ in cells(racks[k]) if (k, c_) != (rt, ct)]
        for n in rng.permutation(len(free))[:int(rng.integers(OOD["vials"][0], OOD["vials"][1] + 1))]:
            col, rgb = _colour(rng, {**COLOURS, **OOD_COLOURS})
            extras.append(dict(kind="vial", rack=free[n][0], cell=list(free[n][1]), size_mm=[round(float(rng.uniform(22, 27)), 1), round(float(rng.uniform(58, 74)), 1)],
                               color=col, rgb=rgb, yaw_deg=0.0))
    taken = {(e["rack"], tuple(e["cell"])) for e in extras}

    def clear(k, c_, things):                                       # a hole with nothing in it nor in the holes next to it
        return all(not (k == k2 and _near(c_, c2)) for k2, c2 in things)

    options = []
    for k in (0, 1):
        for cp in cells(racks[k]):
            if (k, cp) in taken or (k == rt and _near(cp, ct)):
                continue
            if any(clear(1 - rt, cd, taken | {(k, cp)}) for cd in cells(racks[1 - rt])):
                options.append((k, cp))
    if not options:
        return None
    rp, cp = options[rng.integers(len(options))]
    tube = dict(rack=rt, cell=list(ct), yaw_deg=round(float(rng.uniform(-180, 180)), 1))
    tube["cap_color"], tube["cap_rgb"] = _colour(rng, OOD_COLOURS) if "tube" in ood else _colour(rng, COLOURS, CAP_COLOURS)
    pip = dict(rack=rp, cell=list(cp), yaw_deg=round(float(rng.uniform(-180, 180)), 1))
    pip["color"], pip["rgb"] = _colour(rng, OOD_COLOURS) if "pipette" in ood else _colour(rng, COLOURS, BULB_COLOURS)
    if "extras" in ood:                                             # blocks on the table, clear of the rest
        for _ in range(int(rng.integers(OOD["blocks"][0], OOD["blocks"][1] + 1))):
            size = [round(float(rng.uniform(30, 70)), 1), round(float(rng.uniform(25, 45)), 1), round(float(rng.uniform(15, 40)), 1)]
            rad = math.hypot(size[0], size[1]) / 2
            for _ in range(60):
                xy = np.array([rng.uniform(*ZONE[0]), rng.uniform(*ZONE[1])])
                others = [(e["pos_mm"], math.hypot(*e["size_mm"][:2]) / 2) for e in extras if e["kind"] == "block"] + [(cup["pos_mm"], r_out)]
                if _in_zone(xy, rad + 2) and all(_rect_dist(r, xy) >= rad + 30 for r in racks) and \
                        all(np.linalg.norm(xy - np.array(p)) >= rad + r2 + 30 for p, r2 in others):
                    col, rgb = _colour(rng, {**COLOURS, **OOD_COLOURS})
                    extras.append(dict(kind="block", size_mm=size, pos_mm=[round(float(v), 1) for v in xy], yaw_deg=round(float(rng.uniform(-90, 90)), 1),
                                       color=col, rgb=rgb))
                    break
    if how:
        ood = [a if a != "place" else f"place ({how}{', cup beyond' if front else ''})" for a in ood]
    return dict(kind=KIND, seed=int(seed), racks=racks, tube=tube, pipette=pip, cup=cup, extras=extras, ood=ood)


@functools.lru_cache(maxsize=512)
def _sample(seed):
    for attempt in range(TRIES):
        sc = _draw(seed, attempt)
        if sc is not None:
            sc["attempts"] = attempt + 1
            return sc
    raise RuntimeError(f"no bio-lab scene for seed {seed} in {TRIES} tries")


def sample(seed):
    """The scene of episode `seed` of biolab_drops (the same every time)."""
    return copy.deepcopy(_sample(int(seed)))


def describe(sc):
    """One line of text: what is in the scene."""
    out = []
    for n, r in zip(NAMES, sc["racks"]):
        out.append(f"{n}: {r['color']}, {r['cells'][0]} x {r['cells'][1]} holes of {r['cell_mm']:g}, {r['height_mm']:g} high, at "
                   f"({r['pos_mm'][0]:.0f}, {r['pos_mm'][1]:.0f}) turned {r['yaw_deg']:g} deg")
    t, p, c = sc["tube"], sc["pipette"], sc["cup"]
    out.append(f"tube ({t['cap_color']} cap) in {NAMES[t['rack']]} hole {tuple(t['cell'])}; pipette ({p['color']} bulb) in {NAMES[p['rack']]} hole "
               f"{tuple(p['cell'])}; cup: {c['color']}, {c['inner_mm']:g} across inside, {c['height_mm']:g} high, {c['liquid']} water to {c['level_mm']:g}, "
               f"at ({c['pos_mm'][0]:.0f}, {c['pos_mm'][1]:.0f})")
    for k, e in enumerate(sc["extras"]):
        out.append(f"extra_{k}: {e['color']} {e['kind']} " + (f"in {NAMES[e['rack']]} hole {tuple(e['cell'])}" if e["kind"] == "vial" else
                                                             f"{' x '.join(f'{v:g}' for v in e['size_mm'])} at ({e['pos_mm'][0]:.0f}, {e['pos_mm'][1]:.0f})"))
    return "; ".join(out) + " (mm)" + (f"; out of distribution: {', '.join(sc['ood'])}" if sc["ood"] else "")


# ---------------------------------------------------------------------- the task
_HOW = (
    f"How things are: the tube is {TUBE_D:.0f} mm across and {TUBE_L:.0f} mm long, light (about 25 g), and stands loosely in its hole (the holes "
    "are square, a few mm wider than the tube; the racks are not fixed to the table). Its cap is "
    f"{CAP_OUT:.0f} mm across and {CAP_SKIRT + CAP_LID:.0f} mm high. The cap unscrews counter-clockwise seen from above: after three quarters of a turn "
    f"({UNSCREW_DEG:.0f} degrees) it is free and can be lifted off; unless the tube is held, it turns with the cap. To put the cap back, set it "
    "level and centred on the tube's mouth (within 2 mm, and within a few degrees of the tube's axis: it catches the thread as it comes to rest "
    "on the rim, the tube standing), then turn it clockwise by "
    f"three quarters of a turn, until it stops; it counts as closed within {CLOSED_DEG:.0f} degrees of that. The pipette is {PIP_L:.0f} mm long: "
    f"from below, a tip {TIP_L:.0f} mm long and {2 * TIP_R:.0f} mm across, a round body with a collar {2 * COLLAR_R:.0f} mm across on which it "
    f"stands on the rack (its tip in the hole), and on top the squeeze bulb, {BULB_D:.0f} mm across and {BULB_Z[1] - BULB_Z[0]:.0f} mm high (the "
    f"pipette's top {PIP_L - BULB_Z[0]:.0f} mm). Squeezing the bulb between two fingers (from {BULB_D:.0f} mm down to {BULB_D - 2 * BULB_TRAVEL:.0f} "
    "mm at most) pushes out what is in the pipette, and letting it out again draws in: water if the tip is in the cup's water, air otherwise. "
    f"With water in the pipette and the tip in the air, every {DROP_MM:g} mm of further squeeze lets one drop fall, straight down from the "
    f"tip (air drawn in after the water leaves first, without a drop). The pipette holds up to {CAPACITY} drops. The bulb is soft (about "
    "1 N): what tells how far it is squeezed is the measured opening between the fingers, not the commanded one.")


def _text(cap="", bulb="", cup="", liquid="water"):
    sp = lambda s: (s + " ") if s else ""
    return ("Task (a bio-lab bench): two tube racks stand next to each other on the table in front of the robot. A 50 mL centrifuge tube, "
            f"closed with a {sp(cap)}screw cap, stands in a hole of one of them; a pipette with a {sp(bulb)}squeeze bulb stands in another "
            f"hole; a {sp(cup)}cup with {liquid} stands on the table. Move the tube from the rack it stands in to a hole of the other rack, "
            "open the tube (unscrew its cap), then take the pipette and draw some water from the cup (with the pipette's tip in the water, "
            f"squeeze the bulb and let it out again), then put three drops of water into the open tube, then close the tube (screw its cap "
            f"back on). At the end the tube must stand in a hole of the other rack with its cap screwed on and exactly {DROPS} drops of water "
            "in it, the robot touching neither the tube nor its cap, the cup still standing, and nothing fallen off the table; then call "
            "done() (or let the program return). " + _HOW)


def text(sc):
    """The task's text in this scene: it names the colours of the cap, the bulb and the cup."""
    c = sc["cup"]
    return _text(sc["tube"]["cap_color"], sc["pipette"]["color"], c["color"], "water" if c["liquid"] == "blue" else f"{c['liquid']} water")


TEXT = _text()                                                  # without a scene (a session's prompt)


def names(sc):
    return list(NAMES) + ["tube", "pipette", "cup"] + [f"extra_{k}" for k in range(len(sc["extras"]))]


def _yawq(deg):
    return [math.cos(math.radians(deg) / 2), 0.0, 0.0, math.sin(math.radians(deg) / 2)]


def layout(sc):
    """{object: (x, y, z, qw, qx, qy, qz)} in m, for core.BiCore.reset."""
    out = {}
    for n, r in zip(NAMES, sc["racks"]):
        out[n] = (r["pos_mm"][0] / 1000, r["pos_mm"][1] / 1000, 0.0005, *_yawq(r["yaw_deg"]))

    def in_cell(o, z):
        xy = cell_xy(sc["racks"][o["rack"]], *o["cell"])
        return (xy[0] / 1000, xy[1] / 1000, z / 1000 + 0.0008, *_yawq(o["yaw_deg"]))

    out["tube"] = in_cell(sc["tube"], BASE)
    out["pipette"] = in_cell(sc["pipette"], sc["racks"][sc["pipette"]["rack"]]["height_mm"] - COLLAR_Z[0])
    out["cup"] = (sc["cup"]["pos_mm"][0] / 1000, sc["cup"]["pos_mm"][1] / 1000, 0.0005, 1.0, 0.0, 0.0, 0.0)
    for k, e in enumerate(sc["extras"]):
        out[f"extra_{k}"] = in_cell(e, BASE) if e["kind"] == "vial" else (e["pos_mm"][0] / 1000, e["pos_mm"][1] / 1000, 0.0005, *_yawq(e["yaw_deg"]))
    return out


def paint(m, sc):
    pass


# ---------------------------------------------------------------------- the model
def _f(*v):
    return " ".join(f"{x:.6g}" for x in v)


def _rgba(rgb, a=1.0):
    return _f(*(v / 255 for v in rgb), a)


def _g(body, name, kind, size, pos=(0, 0, 0), rgba="0.5 0.5 0.5 1", mass=0.0, see=True, hit=True, **kw):
    """A geom (m, kg). see: drawn (else group 3: it only collides); hit: it collides (else it is only drawn)."""
    a = dict(name=name, type=kind, size=_f(*size), pos=_f(*pos), rgba=rgba, mass=_f(mass), group="0" if see else "3")
    if not hit:
        a.update(contype="0", conaffinity="0")
    return ET.SubElement(body, "geom", {**a, **kw})


def _prism(body, name, r, half, z, n=8, **kw):
    """A cylinder that collides (radius r, half-height `half`, centre at height z): a prism of 2 n sides made of n boxes
    (a cylinder geom lying or standing on a box has one contact point and never comes to rest)."""
    for k in range(n):
        _g(body, f"{name}{k}", "box", (r * math.cos(math.pi / (2 * n)), r * math.sin(math.pi / (2 * n)), half), (0, 0, z),
           euler=_f(0, 0, k * math.pi / n), **kw)


def _ring(body, name, r_in, r_out, half, z, n=16, mass=0.0, **kw):
    """A tube's wall (radii r_in..r_out, half-height `half`, centre at height z) made of n boxes."""
    r, t = (r_in + r_out) / 2, (r_out - r_in) / 2
    for k in range(n):
        a = 2 * math.pi * k / n
        _g(body, f"{name}{k}", "box", (t, r_out * math.tan(math.pi / n) * 1.02, half), (r * math.cos(a), r * math.sin(a), z), euler=_f(0, 0, a),
           mass=mass / n, **kw)


PLASTIC = dict(friction="0.8 0.02 0.003", condim="4")
GRIP = dict(friction="1.2 0.02 0.002", condim="4")
STIFF = dict(GRIP, solref="0.004 1")                                # (the cap, as stiff as the fingers: softer, they sink 1 mm into its
                                                                    #  2 mm wall, and it then hangs on one finger when they open)
IMPRATIO = "1000"
GUARD = True                                                    # core.BiCore._advance: physics steps without false contacts


def _rack(b, n, rk):
    (X, Y), h, w, p = [v / 1000 for v in rack_outer(rk)], rk["height_mm"] / 1000, WALL / 1000, (rk["cell_mm"] + WALL) / 1000
    rgba, c = _rgba(rk["rgb"]), dict(friction="1.0 0.02 0.005", condim="4")
    nx, ny = rk["cells"]
    _g(b, f"{n}_base", "box", (X / 2, Y / 2, BASE / 2000), (0, 0, BASE / 2000), rgba, 0.6 * rk["mass_kg"], **c)
    mw = 0.4 * rk["mass_kg"] / (nx + ny + 2)
    hz, z = (h - BASE / 1000) / 2, BASE / 1000 + (h - BASE / 1000) / 2
    for i in range(nx + 1):
        _g(b, f"{n}_wx{i}", "box", (w / 2, Y / 2, hz), (-X / 2 + w / 2 + i * p, 0, z), rgba, mw, **c)
    for j in range(ny + 1):
        _g(b, f"{n}_wy{j}", "box", (X / 2, w / 2, hz), (0, -Y / 2 + w / 2 + j * p, z), rgba, mw, **c)


def _cap(b, n, rgb, stop):
    """The cap's geoms about the centre of the lid's underside. stop (the free cap): a step inside, on which it rests on
    the tube's rim where the thread starts."""
    rgba, ro, ri = _rgba(rgb), CAP_OUT / 2000, CAP_IN / 2000
    _g(b, f"{n}_lid", "cylinder", (ro, CAP_LID / 2000), (0, 0, CAP_LID / 2000), rgba, CAP_KG / 3, hit=False)
    _prism(b, f"{n}_l", ro, CAP_LID / 2000, CAP_LID / 2000, see=False, **STIFF)
    _ring(b, f"{n}_w", ri, ro, CAP_SKIRT / 2000, -CAP_SKIRT / 2000, mass=2 * CAP_KG / 3, rgba=rgba, **STIFF)
    if stop:
        rise = K_THREAD * math.radians(UNSCREW_DEG)
        _prism(b, f"{n}_s", TUBE_D / 2000 + 0.0005, rise / 2, -rise / 2, n=4, see=False, **PLASTIC)


def _tube(b, asset, t):
    R, L = TUBE_D / 2000, TUBE_L / 1000
    clear = _rgba((215, 232, 245), 0.45)
    _g(b, "tube_tip", "sphere", (R,), (0, 0, R), see=False, **PLASTIC)
    z0, z1 = R, L - MOUTH / 1000
    _prism(b, "tube_p", R, (z1 - z0) / 2, (z0 + z1) / 2, see=False, **PLASTIC)
    _ring(b, "tube_m", R - TUBE_WALL / 1000, R, MOUTH / 2000, L - MOUTH / 2000, rgba=_rgba((215, 232, 245), 0.6), **PLASTIC)
    _g(b, "tube_body", "cylinder", (R, (z1 - z0) / 2), (0, 0, (z0 + z1) / 2), clear, TUBE_KG, hit=False)
    pts = [(0.0, 0.0, 0.0)] + [(R * math.cos(2 * math.pi * k / 24), R * math.sin(2 * math.pi * k / 24), R) for k in range(24)]
    ET.SubElement(asset, "mesh", name="tube_cone", vertex=_f(*(v for p in pts for v in p)))
    ET.SubElement(b, "geom", name="tube_cone", type="mesh", mesh="tube_cone", rgba=clear, mass="0", contype="0", conaffinity="0", group="0")
    for k in range(CAPACITY):                                       # what has dropped in (drawn larger than it is)
        z = 0.0025 + 0.0016 * k
        _g(b, f"tube_c{k}", "cylinder", (min(0.85 * z, R - 0.003), 0.0008), (0, 0, z), _rgba(WATER, 0.0), hit=False)
    c = ET.SubElement(b, "body", name="tube_cap", pos=_f(0, 0, L))
    ET.SubElement(c, "joint", name="tube_cap_turn", type="hinge", axis="0 0 1", range="0 100", limited="true", frictionloss="0.03",
                  damping="0.005", armature="1e-4", solreffriction="0.004 1", solimpfriction="0.99 0.999 0.001")
    ET.SubElement(c, "joint", name="tube_cap_rise", type="slide", axis="0 0 1", range="-0.001 0.2", limited="true", damping="0.5", armature="1e-4")
    _cap(c, "tcap", t["cap_rgb"], stop=False)


def _pipette(b, p, liquid):
    white, col = _rgba((236, 238, 240)), _rgba(p["rgb"])
    ET.SubElement(b, "geom", name="pip_tip", type="capsule", fromto=_f(0, 0, TIP_R / 1000, 0, 0, TIP_L / 1000), size=_f(TIP_R / 1000),
                  rgba=_rgba((225, 235, 245), 0.5), mass="0.001", group="0", **PLASTIC)
    for k in range(CAPACITY):                                       # the water in it, a piece for each drop
        _g(b, f"pip_l{k}", "cylinder", (0.0022, 0.0012), (0, 0, 0.0035 + 0.0021 * k), _rgba(liquid, 0.0), hit=False)
    _g(b, "pip_drop", "sphere", (0.0026,), (0, 0, -0.0012), _rgba(liquid, 0.0), hit=False)
    _g(b, "pip_plug", "sphere", (PLUG_R / 1000,), (0, 0, PLUG_Z / 1000), white, PIP_KG - 0.007, **PLASTIC)
    cz, ch = (COLLAR_Z[0] + COLLAR_Z[1]) / 2000, (COLLAR_Z[1] - COLLAR_Z[0]) / 2000
    _g(b, "pip_collar", "cylinder", (COLLAR_R / 1000, ch), (0, 0, cz), white, 0.002, hit=False)
    _prism(b, "pip_k", COLLAR_R / 1000, ch, cz, see=False, **PLASTIC)
    _g(b, "pip_neck", "cylinder", (0.007, (BULB_Z[0] - COLLAR_Z[1]) / 2000), (0, 0, (BULB_Z[0] + COLLAR_Z[1]) / 2000), white, hit=False)
    _g(b, "pip_core", "cylinder", (0.004, (BULB_Z[1] - BULB_Z[0]) / 2000), (0, 0, (BULB_Z[0] + BULB_Z[1]) / 2000), _rgba((60, 60, 64)), hit=False)
    _g(b, "pip_top", "cylinder", (0.009, 0.001), (0, 0, BULB_Z[1] / 1000 + 0.001), col, hit=False)
    r, hz = BULB_D / 2000, (BULB_Z[1] - BULB_Z[0]) / 2000
    half = r * math.tan(math.pi / PLATES)
    for k in range(PLATES):                                         # the bulb: plates that move in together (see model_xml)
        pb = ET.SubElement(b, "body", name=f"pip_b{k}", pos=_f(0, 0, (BULB_Z[0] + BULB_Z[1]) / 2000), euler=_f(0, 0, k * 2 * math.pi / PLATES))
        ET.SubElement(pb, "joint", name=f"pip_b{k}", type="slide", axis="-1 0 0", range=_f(0, BULB_TRAVEL / 1000), limited="true",
                      stiffness=_f(BULB_K), springref=_f(-BULB_F0 / BULB_K), damping=_f(4.0 / PLATES), armature=_f(0.016 / PLATES),
                      solreflimit="0.004 1", solimplimit="0.95 0.99 0.001")
        _g(pb, f"pip_v{k}", "box", (0.001, half, hz), (r - 0.001, 0, 0), col, 0.0032 / PLATES, hit=False)
        _g(pb, f"pip_p{k}", "box", (0.001, half - 0.0003, hz), (r - 0.001, 0, 0), col, 0.0008 / PLATES, see=False, contype="2", conaffinity="1", **GRIP)


def _cup(b, c):
    ri, H, L = c["inner_mm"] / 2000, c["height_mm"] / 1000, c["level_mm"] / 1000
    rgba = _rgba(c["rgb"])
    _prism(b, "cup_f", ri + 0.0025, BASE / 2000, BASE / 2000, rgba=rgba, mass=0.03 / 8, **PLASTIC)
    _ring(b, "cup_w", ri, ri + 0.0025, H / 2, H / 2, mass=0.05, rgba=rgba, **PLASTIC)
    _g(b, "cup_water", "cylinder", (ri - 0.0003, (L - BASE / 1000) / 2), (0, 0, (L + BASE / 1000) / 2), _rgba(c["liquid_rgb"], 0.7),
       1000 * math.pi * ri * ri * (L - BASE / 1000), hit=False)


def _extra(b, n, e):
    rgba = _rgba(e["rgb"])
    if e["kind"] == "vial":                                         # a closed vial: a glass body, a cap in its colour
        r, h = e["size_mm"][0] / 2000, e["size_mm"][1] / 1000
        _g(b, f"{n}_glass", "cylinder", (r, (h - 0.012) / 2), (0, 0, (h - 0.012) / 2), _rgba((200, 215, 215), 0.7), 0.02, hit=False)
        _g(b, f"{n}_cap", "cylinder", (r + 0.0005, 0.006), (0, 0, h - 0.006), rgba, 0.003, hit=False)
        _prism(b, f"{n}_c", r, h / 2, h / 2, see=False, **PLASTIC)
    else:
        s = [v / 2000 for v in e["size_mm"]]
        _g(b, n, "box", s, (0, 0, s[2]), rgba, 600 * 8 * s[0] * s[1] * s[2], **PLASTIC)


def _pads(root, back=0.0005):
    """Fingers built of thin slices (the left arm's stepped ones, flush on the inside): one pad over their inner faces, the
    slices' own inner faces 0.5 mm behind it; the shape stays the same. (Each slice collides by itself: an edge that
    lies in one, such as a held cap's top, is pushed along the finger by it, the others hold it by friction, and when the
    fingers open the cap hangs on one of them.)"""
    for b in root.iter("body"):
        sl = [g for g in b.findall("geom") if g.get("type") == "box" and g.get("group") == "3" and float(g.get("size").split()[2]) <= 0.0045]
        if len(sl) < 4:
            continue
        hy = [float(g.get("size").split()[1]) for g in sl]
        z0 = min(float(g.get("pos").split()[2]) - float(g.get("size").split()[2]) for g in sl)
        z1 = max(float(g.get("pos").split()[2]) + float(g.get("size").split()[2]) for g in sl)
        x = sl[0].get("size").split()[0]
        pad = ET.SubElement(b, "geom", dict(sl[0].attrib, size=f"{x} {_f(min(hy), (z1 - z0) / 2)}", pos=_f(0, min(hy), (z0 + z1) / 2)))
        for g, h in zip(sl, hy):
            sx, _, sz = g.get("size").split(); px, _, pz = g.get("pos").split()
            g.set("size", f"{sx} {_f(h - back / 2)} {sz}"); g.set("pos", f"{px} {_f(h + back / 2)} {pz}")


def model_xml(path, sc):
    """The environment's model (models/NAME.xml) with the scene's bodies -> MJCF text (paths absolute)."""
    path = Path(path)
    root = ET.parse(path).getroot()
    comp = root.find("compiler")
    comp.set("meshdir", str((path.parent / comp.get("meshdir", ".")).resolve()))
    wb, asset, eq, con = root.find("worldbody"), root.find("asset"), root.find("equality"), root.find("contact")
    qpos, free = "", " 0 0 0 1 0 0 0"
    _pads(root)
    for o in root.findall("option"):                                # (friction that holds: at the environment's 10, a 25 g tube
        if o.get("impratio"):                                       #  held in the wedge fingers slides out of them at 0.3 mm/s)
            o.set("impratio", IMPRATIO)

    def body(name, k):
        b = ET.SubElement(wb, "body", name=name, pos=_f(0.30 + 0.12 * k, 0.45, 0.2))
        ET.SubElement(b, "freejoint", name=f"{name}_free")
        return b

    for k, (n, rk) in enumerate(zip(NAMES, sc["racks"])):
        _rack(body(f"obj_{n}", k), n, rk); qpos += free
    _tube(body("obj_tube", 2), asset, sc["tube"]); qpos += free + " 0 0"
    _cap(body("tube_cap_free", 3), "fcap", sc["tube"]["cap_rgb"], stop=True); qpos += free
    _pipette(body("obj_pipette", 4), sc["pipette"], sc["cup"]["liquid_rgb"]); qpos += free + " 0" * PLATES
    _cup(body("obj_cup", 5), sc["cup"]); qpos += free
    for k, e in enumerate(sc["extras"]):
        _extra(body(f"obj_extra_{k}", 6 + k), f"extra_{k}", e); qpos += free
    ET.SubElement(eq, "joint", name="tube_thread", joint1="tube_cap_rise", joint2="tube_cap_turn", polycoef=_f(0, K_THREAD, 0, 0, 0),
                  solref="0.004 1", solimp="0.95 0.99 0.001")
    for k in range(1, PLATES):
        ET.SubElement(eq, "joint", name=f"pip_bulb{k}", joint1=f"pip_b{k}", joint2="pip_b0", polycoef="0 1 0 0 0", solref="0.004 1",
                      solimp="0.95 0.99 0.001")
    ET.SubElement(con, "exclude", body1="obj_tube", body2="tube_cap")
    key = root.find("keyframe").find("key")                         # (the new bodies are the last: their qpos too)
    key.set("qpos", key.get("qpos") + qpos)
    return ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------------- the mechanisms: the cap's thread, the liquid
class Mech:
    """The scene's mechanisms in a simulation (core.BiCore.mech): reset() with the episode, step() after every physics
    step, get_state() / set_state() with the simulation's."""
    labels = {"tube_cap": "the tube's cap", "pip_b": "the pipette's bulb"}       # body name prefix -> what a message calls it

    def __init__(self, S):
        self.S, m, sc = S, S.m, S.scene
        jq = lambda n: int(m.jnt_qposadr[m.joint(n).id])
        jv = lambda n: int(m.jnt_dofadr[m.joint(n).id])
        self.b_tube, self.b_cap, self.b_capf = m.body("obj_tube").id, m.body("tube_cap").id, m.body("tube_cap_free").id
        self.b_pip, self.b_cup = m.body("obj_pipette").id, m.body("obj_cup").id
        self.b_racks = [m.body(f"obj_{n}").id for n in NAMES]
        self.q_turn, self.q_rise, self.v_turn, self.v_rise = jq("tube_cap_turn"), jq("tube_cap_rise"), jv("tube_cap_turn"), jv("tube_cap_rise")
        self.q_free, self.v_free = jq("tube_cap_free_free"), jv("tube_cap_free_free")
        self.q_bulb = np.array([jq(f"pip_b{k}") for k in range(PLATES)])
        self.b_bulb = [m.body(f"pip_b{k}").id for k in range(PLATES)]
        self.cap_geoms = [g for g in range(m.ngeom) if m.geom_bodyid[g] == self.b_cap]
        self.capf_geoms = [g for g in range(m.ngeom) if m.geom_bodyid[g] == self.b_capf]
        self.g_liq = [m.geom(f"pip_l{k}").id for k in range(CAPACITY)]
        self.g_in = [m.geom(f"tube_c{k}").id for k in range(CAPACITY)]
        self.g_drop = m.geom("pip_drop").id
        self.cap_pos0, self.cap_quat0 = m.body_pos[self.b_cap].copy(), m.body_quat[self.b_cap].copy()
        self.park = np.array([200.0, 203.0, -0.8, 1, 0, 0, 0])
        self.cup_r, self.cup_level = sc["cup"]["inner_mm"] / 2000, sc["cup"]["level_mm"] / 1000
        self.reset()

    # -- the episode
    def reset(self):
        S, m, d = self.S, self.S.m, self.S.d
        m.body_pos[self.b_cap], m.body_quat[self.b_cap] = self.cap_pos0, self.cap_quat0
        for g in self.capf_geoms:                                   # the free cap: parked, no contacts, until the swap
            m.geom_contype[g] = 0; m.geom_conaffinity[g] = 0
        d.qpos[self.q_free:self.q_free + 7] = self.park
        self.on, self.armed = True, False                           # the cap on its thread; (off:) ready to catch it again
        self.V = self.A = self.pend = self.back = 0.0               # mm of squeeze: water in the pipette, air below it, the
        self.s_prev = 0.0                                           # hanging part of a drop, water pushed back into the cup
        self.drops = []                                             # [{t, where: "tube" | "cup" | "spilled", xy_mm}]
        self.events = dict(moved=None, opened=None, closed=None)    # when (s): the tube first stood in the other rack, the
        self.n_watch = 0                                            # cap first came off, the cap was last screwed on
        self._show()

    def get_state(self):
        m = self.S.m
        return dict(on=self.on, armed=self.armed, V=self.V, A=self.A, pend=self.pend, back=self.back, s_prev=self.s_prev,
                    drops=copy.deepcopy(self.drops), events=dict(self.events), n_watch=self.n_watch,
                    cap_pos=m.body_pos[self.b_cap].copy(), cap_quat=m.body_quat[self.b_cap].copy())

    def set_state(self, st):
        m = self.S.m
        for k in ("on", "armed", "V", "A", "pend", "back", "s_prev", "n_watch"):
            setattr(self, k, st[k])
        self.drops, self.events = copy.deepcopy(st["drops"]), dict(st["events"])
        m.body_pos[self.b_cap], m.body_quat[self.b_cap] = st["cap_pos"], st["cap_quat"]

    # -- the cap
    def cap_angle(self):
        """Degrees the cap is unscrewed (0: screwed on; UNSCREW_DEG: it comes free); None when it is off."""
        return math.degrees(float(self.S.d.qpos[self.q_turn])) if self.on else None

    def closed(self):
        return bool(self.on and self.cap_angle() <= CLOSED_DEG)

    def cap_body(self):
        return self.b_cap if self.on else self.b_capf

    def mouth(self):
        """The centre of the tube's rim and the tube's orientation."""
        d = self.S.d
        R = d.xmat[self.b_tube].reshape(3, 3)
        return d.xpos[self.b_tube] + R[:, 2] * TUBE_L / 1000, R

    def _release(self):
        S, m, d = self.S, self.S.m, self.S.d
        v6 = np.zeros(6); mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, self.b_cap, v6, 0)
        R = d.xmat[self.b_cap].reshape(3, 3)
        a, va = self.q_free, self.v_free
        d.qpos[a:a + 3] = d.xpos[self.b_cap]; d.qpos[a + 3:a + 7] = d.xquat[self.b_cap]
        d.qvel[va:va + 3] = v6[3:]; d.qvel[va + 3:va + 6] = R.T @ v6[:3]
        for g in self.cap_geoms:
            m.geom_contype[g] = 0; m.geom_conaffinity[g] = 0; m.geom_rgba[g, 3] = 0.0
        for g in self.capf_geoms:
            m.geom_contype[g] = S.geom0[0][g]; m.geom_conaffinity[g] = S.geom0[1][g]
        self.on, self.armed = False, False
        if self.events["opened"] is None:
            self.events["opened"] = round(float(d.time), 2)
        self.events["closed"] = None
        mujoco.mj_forward(m, d)

    def _catch(self):
        """The free cap, off the tube: back on the thread when it is set on the mouth again."""
        S, m, d = self.S, self.S.m, self.S.d
        pm, Rt = self.mouth()
        Rc = d.xmat[self.b_capf].reshape(3, 3)
        rel = Rt.T @ (d.xpos[self.b_capf] - pm)                     # the lid's underside above the rim
        off, cos = math.hypot(rel[0], rel[1]), float(Rt[:, 2] @ Rc[:, 2])
        if not self.armed:                                          # (it must first have left the mouth)
            self.armed = bool(rel[2] > 0.006 or off > 0.005 or cos < math.cos(math.radians(20)))
            return
        top = K_THREAD * math.radians(UNSCREW_DEG)                  # where the thread starts
        if not (off < 0.002 and cos > math.cos(math.radians(8)) and top - 0.0006 < rel[2] < top + 0.001 and Rt[2, 2] > 0.9):
            return
        th = min(rel[2] / K_THREAD, math.radians(UNSCREW_DEG - 20))
        Rr = Rt.T @ Rc
        yaw = math.atan2(Rr[1, 0], Rr[0, 0]) - th                   # the cap keeps its place and the way it is turned
        m.body_pos[self.b_cap] = [rel[0], rel[1], TUBE_L / 1000]
        m.body_quat[self.b_cap] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
        vc, vt = np.zeros(6), np.zeros(6)
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, self.b_capf, vc, 0)
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, self.b_tube, vt, 0)
        w = float(Rt[:, 2] @ (vc[:3] - vt[:3]))
        d.qpos[self.q_turn], d.qpos[self.q_rise] = th, K_THREAD * th
        d.qvel[self.v_turn], d.qvel[self.v_rise] = w, K_THREAD * w
        for g in self.cap_geoms:
            m.geom_contype[g] = S.geom0[0][g]; m.geom_conaffinity[g] = S.geom0[1][g]; m.geom_rgba[g, 3] = S.geom0[2][g, 3]
        for g in self.capf_geoms:
            m.geom_contype[g] = 0; m.geom_conaffinity[g] = 0
        d.qpos[self.q_free:self.q_free + 7] = self.park; d.qvel[self.v_free:self.v_free + 6] = 0
        self.on = True
        mujoco.mj_forward(m, d)

    # -- the liquid
    def squeeze(self):
        """mm by which the bulb is narrower than at rest (where it is narrowest: between the fingers that squeeze it)."""
        q = self.S.d.qpos[self.q_bulb]
        return max(0.0, 1000 * float((q[:PLATES // 2] + q[PLATES // 2:]).max()))

    def tip(self):
        return self.S.d.xpos[self.b_pip]

    def pipette_held(self):
        """The arms that touch the pipette (its body or its bulb)."""
        S = self.S
        return sorted({k for b in [self.b_pip] + self.b_bulb for k in S.touching(int(b))[0]})

    def _cup_frame(self, p):
        d = self.S.d
        R = d.xmat[self.b_cup].reshape(3, 3)
        rel = R.T @ (p - d.xpos[self.b_cup])
        return math.hypot(rel[0], rel[1]), float(rel[2]), bool(R[2, 2] > math.cos(math.radians(20)))

    def in_water(self):
        """The pipette's tip is in the cup's water."""
        r, z, up = self._cup_frame(self.tip())
        return bool(up and r < self.cup_r - 0.002 and BASE / 1000 < z < self.cup_level)

    def _drop(self):
        d, p = self.S.d, self.tip()
        pm, Rt = self.mouth()
        where = "spilled"
        if Rt[2, 2] > math.cos(math.radians(25)) and math.hypot(p[0] - pm[0], p[1] - pm[1]) < (TUBE_D / 2 - TUBE_WALL - 1) / 1000 \
                and p[2] > pm[2] - MOUTH / 1000:                    # (over the open mouth, or in it)
            pc = d.xpos[self.b_capf]
            under_cap = self.on or (math.hypot(p[0] - pc[0], p[1] - pc[1]) < CAP_OUT / 2000 and pm[2] - 0.01 < pc[2] < p[2])
            where = "spilled" if under_cap else "tube"
        else:
            r, z, up = self._cup_frame(p)
            if up and r < self.cup_r and z > 0:
                where = "cup"
        self.drops.append(dict(t=round(float(d.time), 2), where=where, xy_mm=[round(float(p[0]) * 1000, 1), round(float(p[1]) * 1000, 1)]))

    def _liquid(self):
        s = self.squeeze()
        ds, self.s_prev = s - self.s_prev, s
        if ds == 0.0:
            return
        shown = (math.ceil(self.V / DROP_MM - 1e-6), self.pend > 0.3, len(self.drops))
        if ds > 0:                                                  # pushed out: the air below the water, then water
            a = min(self.A, ds); self.A -= a
            out = min(self.V, ds - a)
            if out > 0:
                self.V -= out
                if self.in_water():
                    self.back += out
                else:
                    self.pend += out
                    while self.pend >= DROP_MM - 1e-9:
                        self.pend -= DROP_MM; self._drop()
            if self.V <= 1e-9:
                self.V = self.A = 0.0
        elif self.in_water():                                       # drawn in: water
            self.V += -ds + self.pend; self.pend = self.A = 0.0
        else:                                                       # drawn in: the hanging part of a drop, then air
            r = min(self.pend, -ds); self.pend -= r; self.V += r
            if self.V > 0:
                self.A += -ds - r
        if shown != (math.ceil(self.V / DROP_MM - 1e-6), self.pend > 0.3, len(self.drops)):
            self._show()

    def counts(self):
        """{"tube": n, "cup": n, "spilled": n}: where the drops fell."""
        return {w: sum(1 for e in self.drops if e["where"] == w) for w in ("tube", "cup", "spilled")}

    def _show(self):
        """What is seen of the water: in the pipette's tip, hanging from it, in the tube."""
        m = self.S.m
        n, k = math.ceil(self.V / DROP_MM - 1e-6), self.counts()["tube"]
        for i, g in enumerate(self.g_liq):
            m.geom_rgba[g, 3] = 0.9 if i < n else 0.0
        for i, g in enumerate(self.g_in):
            m.geom_rgba[g, 3] = 0.9 if i < k else 0.0
        m.geom_rgba[self.g_drop, 3] = 0.9 if self.pend > 0.3 else 0.0

    # -- where things are
    def in_cell(self, body, z_mm, tilt_deg=20.0):
        """The rack hole a standing body's origin is in, z_mm above the rack's bottom (within 8 mm) -> (rack, i, j) | None."""
        d, sc = self.S.d, self.S.scene
        if d.xmat[body].reshape(3, 3)[2, 2] < math.cos(math.radians(tilt_deg)):
            return None
        for k, (b, rk) in enumerate(zip(self.b_racks, sc["racks"])):
            rel = d.xmat[b].reshape(3, 3).T @ (d.xpos[body] - d.xpos[b]) * 1000
            p = rk["cell_mm"] + WALL
            i, j = math.floor(rel[0] / p + rk["cells"][0] / 2), math.floor(rel[1] / p + rk["cells"][1] / 2)
            zz = z_mm if z_mm is not None else rk["height_mm"] - COLLAR_Z[0]
            if 0 <= i < rk["cells"][0] and 0 <= j < rk["cells"][1] and abs(rel[2] - zz) < 8.0:
                return k, i, j
        return None

    def tube_cell(self):
        return self.in_cell(self.b_tube, BASE)

    def pipette_cell(self):
        return self.in_cell(self.b_pip, None)

    def step(self):
        d = self.S.d
        if self.on:
            if d.qpos[self.q_turn] >= math.radians(UNSCREW_DEG):
                self._release()
            else:
                d.qpos[self.q_free:self.q_free + 7] = self.park; d.qvel[self.v_free:self.v_free + 6] = 0
        else:
            self._catch()
        self._liquid()
        self.n_watch += 1
        if self.n_watch % 25 == 0:
            ev = self.events
            if ev["moved"] is None:
                c = self.tube_cell()
                if c is not None and c[0] != self.S.scene["tube"]["rack"]:
                    ev["moved"] = round(float(d.time), 2)
            if ev["opened"] is not None:
                if not self.closed():
                    ev["closed"] = None
                elif ev["closed"] is None:
                    ev["closed"] = round(float(d.time), 2)


def mechanisms(S):
    return Mech(S)


# ---------------------------------------------------------------------- the judge, the true state
def judge(S):
    """TRUE state: the tube stands in a hole of the other rack, its cap screwed on, with exactly DROPS drops in it; the
    robot touches neither; the cup stands; nothing has fallen off the table. Also told, not judged: the drops that
    fell elsewhere, where the pipette is, and whether the steps came in the order asked."""
    M, sc, d = S.mech, S.scene, S.d
    cell = M.tube_cell()
    moved = bool(cell is not None and cell[0] != sc["tube"]["rack"])
    parts = {}
    for b in (M.b_tube, M.cap_body()):
        for k, v in S.touching(int(b))[0].items():
            parts.setdefault(k, set()).update(v)
    n = M.counts()
    fallen = [x for x in names(sc) if S.object_pose(x)[0][2] < S.table_z - 0.05]
    if d.xpos[M.cap_body()][2] < S.table_z - 0.05:
        fallen.append("tube_cap")
    pc, Rc = S.object_pose("cup")
    cup = bool(Rc[2, 2] > math.cos(math.radians(10)) and abs(pc[2] - S.table_z) < 0.01)
    ev = M.events
    drops_t = [e["t"] for e in M.drops if e["where"] == "tube"]
    order = [ev["moved"], ev["opened"], drops_t[0] if drops_t else None, drops_t[-1] if drops_t else None, ev["closed"]]
    pip = M.pipette_cell()
    held = M.pipette_held()
    return dict(ok=bool(moved and M.closed() and n["tube"] == DROPS and not parts and cup and not fallen),
                moved=moved, tube_in=None if cell is None else f"{NAMES[cell[0]]} hole ({cell[1]}, {cell[2]})",
                closed=M.closed(), cap="off" if not M.on else f"on, {M.cap_angle():.0f} deg from screwed on",
                drops_in_tube=n["tube"], drops_spilled=n["spilled"], drops_in_cup=n["cup"], released=not parts,
                held_by={{"R": "right", "L": "left"}[k]: sorted(v) for k, v in parts.items()}, cup_standing=cup, fallen=fallen,
                pipette="held" if held else "on the table or elsewhere" if pip is None else f"in {NAMES[pip[0]]} hole ({pip[1]}, {pip[2]})",
                in_order=bool(all(v is not None for v in order) and order == sorted(order)),
                times_s=dict(moved=ev["moved"], opened=ev["opened"], drops=drops_t, closed=ev["closed"]))


def truth(ep):
    """The TRUE state for practice (practice.py), mm: each object's reference point (a rack's and the cup's: the centre
    of the bottom; the tube's: its bottom tip; the pipette's: its tip), the holes of the racks, the cap, the liquid."""
    S = ep.S; M, sc = S.mech, S.scene
    mm = lambda p: [round(float(v) * 1000, 1) for v in p]
    out = {n: mm(S.object_pose(n)[0]) for n in names(sc)}
    for k, (n, rk) in enumerate(zip(NAMES, sc["racks"])):
        p, R = S.object_pose(n)
        out[f"{n}_yaw_deg"] = round(math.degrees(math.atan2(R[1, 0], R[0, 0])), 1)
        out[f"{n}_holes"] = {f"{i},{j}": mm(p + R @ [*(cell_local(rk, i, j) / 1000), rk["height_mm"] / 1000]) for i, j in cells(rk)}
    c, pc = M.tube_cell(), M.pipette_cell()
    out.update(tube_mouth=mm(M.mouth()[0]), tube_in=None if c is None else [NAMES[c[0]], c[1], c[2]], tube_started_in=NAMES[sc["tube"]["rack"]],
               cap=mm(S.d.xpos[M.cap_body()]), cap_on=M.on, cap_unscrewed_deg=None if not M.on else round(M.cap_angle(), 1),
               pipette_in=None if pc is None else [NAMES[pc[0]], pc[1], pc[2]], squeeze_mm=round(M.squeeze(), 2),
               water_in_pipette_drops=round(M.V / DROP_MM, 2), tip_in_water=M.in_water(), drops=M.counts(),
               water_level=mm(S.object_pose("cup")[0] + [0, 0, M.cup_level]))
    return out


def sheet(seeds, path, env="sim_bimanual_grippers"):
    """A picture of the scenes of biolab_drops, one row each: the scene camera's first image and the bench from nearer by."""
    import gc
    import cv2
    from .tasks import Episode
    rows = []
    for seed in seeds:
        ep = Episode(env, "biolab_drops", seed=seed, sensors="ideal"); S, sc = ep.S, ep.scene
        cam = mujoco.MjvCamera(); cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        c = np.mean([S.object_pose(n)[0] for n in names(sc)], axis=0)
        r = S.renderer(480, 640)
        tiles = [ep.robot.look(["scene"])["scene"].rgb]
        for az, el, dist in ((205, -35, 0.62), (155, -62, 0.6)):
            cam.lookat[:] = [c[0], c[1], 0.05]; cam.distance, cam.azimuth, cam.elevation = dist, az, el
            r.update_scene(S.d, camera=cam); tiles.append(r.render().copy())
        row = np.hstack(tiles)[..., ::-1].copy()
        cv2.rectangle(row, (0, 0), (row.shape[1], 44), (255, 255, 255), -1)
        cv2.putText(row, f"seed {seed}: racks {' and '.join('%s %dx%d' % (r_['color'], *r_['cells']) for r_ in sc['racks'])}; tube ({sc['tube']['cap_color']} cap) in "
                    f"{NAMES[sc['tube']['rack']]}; pipette ({sc['pipette']['color']}) in {NAMES[sc['pipette']['rack']]}; {sc['cup']['color']} cup"
                    + (f"; {len(sc['extras'])} other objects" if sc["extras"] else ""), (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(row, ("out of distribution: " + ", ".join(sc["ood"])) if sc["ood"] else "in distribution", (8, 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 160) if sc["ood"] else (0, 110, 0), 1, cv2.LINE_AA)
        rows.append(row)
        S.renderers.clear(); del ep, S, r; gc.collect()             # (one GL context at a time, see clutter.sheet)
    cv2.imwrite(str(path), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])


def main(argv=None):
    """Draw scenes and say what they are: python -m agentic_robotics.bimanual.biolab --seeds 0-3,5000-5003"""
    import argparse
    import json
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--seeds", required=True, help="e.g. 2000-2049 or 3,5,5001")
    ap.add_argument("--out", help="a JSON file for the scenes ({seed: scene})")
    ap.add_argument("--sheet", help="a picture of the scenes (a .jpg), see sheet()")
    a = ap.parse_args(argv)
    seeds = [s for part in a.seeds.split(",") for s in (range(int(part.split("-")[0]), int(part.split("-")[1]) + 1) if "-" in part else [int(part)])]
    out = {}
    for s in seeds:
        out[s] = sc = sample(s)
        print(f"seed {s} ({sc['attempts']} tries): {describe(sc)}")
    if a.out:
        Path(a.out).write_text(json.dumps(out) + "\n")
    if a.sheet:
        sheet(seeds, a.sheet)


if __name__ == "__main__":
    main()
