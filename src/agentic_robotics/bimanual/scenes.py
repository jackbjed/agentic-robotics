"""Varied scenes of the cylinder-on-a-stick task (docs/bimanual.md, task insert_varied): the cylinder's and the stand's
sizes, colours and densities, where they are, and other objects on the table (distractors).

A scene is a JSON-able dict (mm, degrees, kg/m3, colours by name):

    {"seed": 7,
     "cylinder": {"diameter_mm", "height_mm", "hole_mm", "color", "rgb", "density", "xy_mm"},
     "stand": {"board_mm": [side, side, thickness], "stick_diameter_mm", "stick_height_mm", "color", "rgb",
               "stick_color", "stick_rgb", "density", "xy_mm", "yaw_deg"},
     "distractors": [{"shape", "size_mm", "color", "rgb", "density", "xy_mm", "yaw_deg"}, ...],
     "ood": [...],                                  (which of its aspects are outside the training limits)
     "table": {"height_mm", "pattern", "color", "rgb", "color2", "rgb2", "size_mm", "angle_deg", "texture_seed"}}
                                                    (only the scenes from TABLE_FROM on: another table, see below)

    build(spec, seed)    a scene from a partial one (what a model sets up in a session, improve.py: SceneSession): every
                         value within the TRAINING limits (TRAIN, COLOURS, SHAPES), the rest drawn at random there
    sample(seed)         the scene of episode `seed`: within the training limits below OOD_FROM, and from OOD_FROM on
                         with some aspects outside them (sizes, colours, kinds and number of distractors, placements);
                         from TABLE_FROM on, the scene of seed - TABLE_BACK on another table (sample_table)
    model_xml(file, scene), layout(scene), paint(model, scene)    the simulation's model and layout for a scene (core.BiCore)

The hole is the stick's diameter plus the clearance (2 mm in every sampled scene). Each object's reference point is
the centre of its underside. The training limits are what a session can build and what its evaluation set is drawn
from; nothing built with build() is outside them, so the OOD scenes are new to any session's result.

Another table (scene["table"]): its top at height_mm in the robot's frame instead of 0 (the robot, its cameras and its
frame stay; the table and what stands on it are higher or lower), in another colour, plain or with a pattern (a texture
painted by table_texture). No session has seen a table other than the dark grey one at 0.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

CLEARANCE = 2.0                                                 # mm: the hole's diameter minus the stick's
WALL = 5.0                                                      # mm: the cylinder's wall is at least this thick
OOD_FROM = 5000                                                 # sample(seed): out-of-distribution scenes from this seed on
RULER = (175.0, 235.0)                                          # mm: the strip of y the table's ruler lies in (nothing stands on it)
WOOD = dict(friction="1.0 0.02 0.005", condim="4")
WALLS, FEET, FOOT_R = 24, 6, 0.001                              # the cylinder: scripts/build_bimanual.py add_stick_and_cylinder

# the training limits (ranges, mm): what build() accepts and draws from
TRAIN = dict(stick_d=(8, 20), stick_h=(60, 140), board=(90, 150), board_t=(12, 25), cyl_d=(30, 55), cyl_h=(25, 60),
             clearance=(1.5, 4.0), density=(400, 1500), dis_n=(0, 3), dis_size=(25, 80), dis_h=(15, 70),
             x=(-400, -180), y=(-230, 130), dis_x=(-460, -90), dis_y=(-330, 150), yaw=(-45, 45),
             gap=60, dis_gap=40, dis_dis=15)
COLOURS = {"red": (200, 40, 40), "green": (40, 150, 60), "blue": (40, 90, 205), "yellow": (230, 200, 40),
           "white": (235, 235, 230), "grey": (140, 140, 145), "wood": (224, 181, 102)}
SHAPES = ("box", "cylinder")                                    # size_mm: [side, side, height] | [diameter, height]
# outside the training limits: what only sample(seed >= OOD_FROM) has
OOD = dict(stick_d=((6, 7.5), (22, 26)), stick_h=((40, 55), (150, 180)), board=((65, 85), (160, 200)), board_t=((8, 11), (28, 40)),
           cyl_d=((24, 28), (58, 66)), cyl_h=((18, 23), (64, 80)), density=((150, 300), (2000, 2700)), dis_n=(4, 6))
OOD_COLOURS = {"orange": (240, 130, 30), "purple": (130, 60, 170), "pink": (240, 130, 180), "cyan": (50, 200, 210),
               "lime": (160, 220, 60), "teal": (20, 120, 120), "beige": (215, 200, 170), "maroon": (115, 30, 45)}
OOD_SHAPES = ("ball", "pill", "L", "steps")                    # [diameter] | [diameter, length] | [arm, width, height] | [length, width, height]
OOD_PLACES = ("near", "far", "close", "crowded", "beyond_ruler")
JITTER = 12                                                     # a colour's variation, per channel (of 255)
# another table: sample(seed >= TABLE_FROM) = the scene of seed - TABLE_BACK on the table of sample_table(seed)
TABLE_FROM, TABLE_BACK = 8000, 3000
TABLE_H = (-40.0, 40.0)                                         # mm: the table top's height in the robot's frame
TABLE_COLOURS = {"black": (20, 20, 22), "dark grey": (51, 51, 54), "light grey": (190, 190, 192), "white": (238, 238, 234),
                 "light wood": (205, 165, 115), "dark wood": (100, 66, 40), "green": (40, 115, 75), "blue": (50, 90, 155),
                 "beige": (218, 204, 172), "red brown": (140, 52, 38)}
PATTERNS = ("plain", "checker", "stripes", "speckle", "wood", "blotches")
TEX = 2048                                                      # px: the table's texture (0.6 mm a pixel)
TABLE_TOP = (1221.0, 1224.0)                                    # mm: the table top's sides (models/*.xml: table_top)


# ---------------------------------------------------------------------- scenes
def _rgb(rng, name):
    base = {**COLOURS, **OOD_COLOURS}[name]
    return [int(np.clip(c + rng.integers(-JITTER, JITTER + 1), 0, 255)) for c in base]


def _u(rng, r, step=0.5):
    return round(float(rng.uniform(*r)) / step) * step


def _radius(d):
    """The radius (mm) of the circle about a distractor's reference point that holds its footprint."""
    s, z = d["shape"], d["size_mm"]
    if s in ("box", "steps"):
        return math.hypot(z[0], z[1]) / 2
    if s in ("cylinder", "ball"):
        return z[0] / 2
    if s == "pill":
        return z[1] / 2
    return z[0] / math.sqrt(2)                                  # L


def _distractor(rng, shapes, colours, T=TRAIN):
    s = str(rng.choice(shapes))
    if s == "box":
        size = [_u(rng, T["dis_size"], 1), _u(rng, T["dis_size"], 1), _u(rng, T["dis_h"], 1)]
    elif s == "cylinder":
        size = [_u(rng, T["dis_size"], 1), _u(rng, T["dis_h"], 1)]
    elif s == "ball":
        size = [_u(rng, (30, 70), 1)]
    elif s == "pill":
        size = [_u(rng, (22, 40), 1), _u(rng, (65, 110), 1)]
    elif s == "L":
        size = [_u(rng, (60, 100), 1), _u(rng, (20, 30), 1), _u(rng, (15, 50), 1)]
    else:
        size = [_u(rng, (50, 90), 1), _u(rng, (30, 60), 1), _u(rng, (30, 70), 1)]
    return dict(shape=s, size_mm=size, color=str(rng.choice(colours)), density=_u(rng, T["density"], 10),
                yaw_deg=_u(rng, (-180, 180), 1))


def _off_ruler(y, r):
    return y + r <= RULER[0] or y - r >= RULER[1]


def _arrange(rng, items, gaps, tries=300, rounds=60):
    """Positions for the items (dicts: r, x and y ranges of the centre, xy if fixed, kind "task" | "dis"; `near`:
    (index of an item before it, (gap range)) puts it that close to that item) -> [[x, y], ...]; ValueError if the
    fixed ones break a rule or the rest does not fit."""
    def ok(i, p, placed):
        it = items[i]
        if not _off_ruler(p[1], it["r"]):
            return f"{it['name']} would stand on the ruler (keep it below y = {RULER[0]:.0f} - its half width)"
        for j, q in placed.items():
            o = items[j]
            need = gaps["task" if it["kind"] == o["kind"] == "task" else "mixed" if "task" in (it["kind"], o["kind"]) else "dis"]
            if it.get("near", (None,))[0] == j:
                need = it["near"][1][0]
            free = math.dist(p, q) - it["r"] - o["r"]
            if free < need - 1e-6:
                return f"{it['name']} and {o['name']}: {free:.0f} mm of free table between them (their outlines' circles), at least {need:.0f} needed"
        return ""
    fixed = {i: list(map(float, it["xy"])) for i, it in enumerate(items) if it.get("xy") is not None}
    done = {}
    for i, p in fixed.items():
        it = items[i]
        if not (it["x"][0] <= p[0] <= it["x"][1] and it["y"][0] <= p[1] <= it["y"][1]):
            raise ValueError(f"{it['name']}: xy_mm {p} is outside x {it['x'][0]}..{it['x'][1]}, y {it['y'][0]}..{it['y'][1]}")
        why = ok(i, p, done)
        if why:
            raise ValueError(why)
        done[i] = p
    for _ in range(rounds):
        placed = dict(fixed)
        for i, it in enumerate(items):
            if i in placed:
                continue
            for _ in range(tries):
                if "near" in it:
                    j, g = it["near"]
                    a = rng.uniform(0, 2 * math.pi); dist = items[j]["r"] + it["r"] + rng.uniform(*g)
                    p = [placed[j][0] + dist * math.cos(a), placed[j][1] + dist * math.sin(a)]
                    if not (it["x"][0] <= p[0] <= it["x"][1] and it["y"][0] <= p[1] <= it["y"][1]):
                        continue
                else:
                    p = [float(rng.uniform(*it["x"])), float(rng.uniform(*it["y"]))]
                if not ok(i, p, placed):
                    placed[i] = [round(p[0], 1), round(p[1], 1)]
                    break
            else:
                break
        if len(placed) == len(items):
            return [placed[i] for i in range(len(items))]
    raise ValueError("the objects do not fit on the table with the free space the rules ask for: fewer or smaller objects, or other positions")


def _num(d, key, r, what, default=None):
    """d[key] as a number within the range r (ValueError otherwise); None if absent."""
    if d.get(key) is None:
        return default
    try:
        v = float(d[key])
    except (TypeError, ValueError):
        raise ValueError(f"{what}: {d[key]!r} is not a number")
    if not r[0] - 1e-9 <= v <= r[1] + 1e-9:
        raise ValueError(f"{what}: {v:g} is outside {r[0]:g}..{r[1]:g}")
    return v


def _colour(d, key, what):
    if d.get(key) is None:
        return None
    if d[key] not in COLOURS:
        raise ValueError(f"{what}: {d[key]!r} is not one of {', '.join(COLOURS)}")
    return d[key]


def _known(d, keys, what):
    if not isinstance(d, dict):
        raise ValueError(f"{what}: a JSON object is needed")
    extra = sorted(set(d) - set(keys))
    if extra:
        raise ValueError(f"{what}: unknown field(s) {', '.join(extra)}; there are {', '.join(keys)}")


def build(spec=None, seed=0):
    """A scene within the training limits from a partial one: spec's values where it has them (ValueError if one is
    outside the limits), random ones elsewhere (by `seed`, or spec["seed"])."""
    T, spec = TRAIN, dict(spec or {})
    _known(spec, ("seed", "cylinder", "stand", "hole_clearance_mm", "distractors"), "scene")
    seed = int(spec.get("seed", seed))
    rng = np.random.default_rng([7, seed])
    c, s = dict(spec.get("cylinder") or {}), dict(spec.get("stand") or {})
    _known(c, ("diameter_mm", "height_mm", "color", "density", "xy_mm"), "cylinder")
    _known(s, ("stick_diameter_mm", "stick_height_mm", "board_mm", "color", "stick_color", "density", "xy_mm", "yaw_deg"), "stand")
    names = list(COLOURS)
    stick_d = _num(s, "stick_diameter_mm", T["stick_d"], "stand.stick_diameter_mm")
    cyl_d = _num(c, "diameter_mm", T["cyl_d"], "cylinder.diameter_mm")
    clear = _num(spec, "hole_clearance_mm", T["clearance"], "hole_clearance_mm", CLEARANCE)
    if stick_d is None:
        stick_d = _u(rng, (T["stick_d"][0], min(T["stick_d"][1], (cyl_d if cyl_d is not None else 1e9) - clear - 2 * WALL)))
    if cyl_d is None:
        cyl_d = _u(rng, (max(T["cyl_d"][0], stick_d + clear + 2 * WALL), T["cyl_d"][1]))
    if cyl_d < stick_d + clear + 2 * WALL - 1e-9:
        raise ValueError(f"the cylinder ({cyl_d:g} mm) must be at least {clear + 2 * WALL:g} mm wider than the stick ({stick_d:g} mm): "
                         f"the hole is the stick + {clear:g} mm, the wall at least {WALL:g} mm")
    board = s.get("board_mm")
    if board is not None:
        if not isinstance(board, (list, tuple)) or len(board) != 3:
            raise ValueError("stand.board_mm: [side, side, thickness]")
        bd = dict(a=board[0], b=board[1], t=board[2])
        board = [_num(bd, "a", T["board"], "stand.board_mm side"), _num(bd, "b", T["board"], "stand.board_mm side"),
                 _num(bd, "t", T["board_t"], "stand.board_mm thickness")]
    else:
        board = [_u(rng, T["board"], 1), _u(rng, T["board"], 1), _u(rng, T["board_t"], 1)]
    col = _colour(s, "color", "stand.color") or str(rng.choice(names))
    scol = _colour(s, "stick_color", "stand.stick_color") or (col if rng.random() < 0.5 else str(rng.choice(names)))
    ccol = _colour(c, "color", "cylinder.color") or str(rng.choice(names))
    stand = dict(board_mm=board, stick_diameter_mm=stick_d,
                 stick_height_mm=_num(s, "stick_height_mm", T["stick_h"], "stand.stick_height_mm", None) or _u(rng, T["stick_h"], 1),
                 color=col, rgb=_rgb(rng, col), stick_color=scol, stick_rgb=_rgb(rng, scol),
                 density=_num(s, "density", T["density"], "stand.density") or _u(rng, T["density"], 10),
                 yaw_deg=_num(s, "yaw_deg", T["yaw"], "stand.yaw_deg", None))
    if stand["yaw_deg"] is None:
        stand["yaw_deg"] = _u(rng, T["yaw"], 1)
    cyl = dict(diameter_mm=cyl_d, height_mm=_num(c, "height_mm", T["cyl_h"], "cylinder.height_mm") or _u(rng, T["cyl_h"], 1),
               hole_mm=stick_d + clear, color=ccol, rgb=_rgb(rng, ccol),
               density=_num(c, "density", T["density"], "cylinder.density") or _u(rng, T["density"], 10))
    ds = spec.get("distractors")
    if ds is None:
        ds = int(rng.integers(T["dis_n"][0], T["dis_n"][1] + 1))
    if isinstance(ds, (int, float)) and not isinstance(ds, bool):
        ds = [{} for _ in range(int(_num(dict(n=ds), "n", T["dis_n"], "distractors (how many)")))]
    if not isinstance(ds, list) or len(ds) > T["dis_n"][1]:
        raise ValueError(f"distractors: a number or a list of at most {T['dis_n'][1]}")
    dis, dxy = [], []
    for k, g in enumerate(ds):
        what = f"distractors[{k}]"
        _known(g, ("shape", "size_mm", "color", "density", "xy_mm", "yaw_deg"), what)
        if g.get("shape") is not None and g["shape"] not in SHAPES:
            raise ValueError(f"{what}.shape: {g['shape']!r} is not one of {', '.join(SHAPES)}")
        d = _distractor(rng, [g["shape"]] if g.get("shape") else SHAPES, names)
        if g.get("size_mm") is not None:
            z, n = g["size_mm"], 3 if d["shape"] == "box" else 2
            if not isinstance(z, (list, tuple)) or len(z) != n:
                raise ValueError(f"{what}.size_mm: " + ("[side, side, height]" if n == 3 else "[diameter, height]"))
            zd = {str(i): v for i, v in enumerate(z)}
            d["size_mm"] = [_num(zd, str(i), T["dis_size"] if i < n - 1 else T["dis_h"], f"{what}.size_mm[{i}]") for i in range(n)]
        d["color"] = _colour(g, "color", what + ".color") or d["color"]
        d["density"] = _num(g, "density", T["density"], what + ".density") or d["density"]
        d["yaw_deg"] = _num(g, "yaw_deg", (-360, 360), what + ".yaw_deg", d["yaw_deg"])
        d["rgb"] = _rgb(rng, d["color"])
        dis.append(d); dxy.append(g.get("xy_mm"))
    items = [dict(name="the stand", kind="task", r=math.hypot(board[0], board[1]) / 2, x=T["x"], y=T["y"], xy=s.get("xy_mm")),
             dict(name="the cylinder", kind="task", r=cyl_d / 2, x=T["x"], y=T["y"], xy=c.get("xy_mm"))]
    items += [dict(name=f"distractor {k}", kind="dis", r=_radius(d), x=T["dis_x"], y=T["dis_y"], xy=dxy[k]) for k, d in enumerate(dis)]
    for it in items:
        if it["xy"] is not None and (not isinstance(it["xy"], (list, tuple)) or len(it["xy"]) != 2):
            raise ValueError(f"{it['name']}: xy_mm is [x, y]")
    xy = _arrange(rng, items, dict(task=T["gap"], mixed=T["dis_gap"], dis=T["dis_dis"]))
    stand["xy_mm"], cyl["xy_mm"] = xy[0], xy[1]
    for d, p in zip(dis, xy[2:]):
        d["xy_mm"] = p
    return dict(seed=seed, cylinder=cyl, stand=stand, distractors=dis, ood=[])


def _ood(rng, key):
    return _u(rng, OOD[key][int(rng.integers(len(OOD[key])))])


def sample_ood(seed):
    """A scene with some aspects outside the training limits (scene["ood"] names them: sizes, colours, distractors,
    density, and one placement of OOD_PLACES)."""
    T = TRAIN
    rng = np.random.default_rng([5, int(seed)])
    for _ in range(200):
        which = [a for a in ("sizes", "colours", "distractors", "place") if rng.random() < 0.65]
        if len(which) >= 2:
            break
    if rng.random() < 0.4:
        which.append("density")
    place = str(rng.choice(OOD_PLACES)) if "place" in which else None
    for _ in range(100):                                            # the sizes: each outside its training range with p = 0.6
        o = {k: "sizes" in which and rng.random() < 0.6 for k in ("stick_d", "stick_h", "board", "board_t", "cyl_d", "cyl_h")}
        if "sizes" in which and not ((o["cyl_d"] or o["cyl_h"]) and (o["stick_d"] or o["stick_h"])):
            continue
        z = {k: (_ood(rng, k) if o[k] else _u(rng, T[k])) for k in o}
        if z["cyl_d"] >= z["stick_d"] + CLEARANCE + 2 * WALL and z["stick_h"] >= 0.7 * z["cyl_h"]:
            break
    else:
        raise RuntimeError("no sizes found")
    side2 = _ood(rng, "board") if o["board"] else _u(rng, T["board"], 1)
    names = list(OOD_COLOURS) if "colours" in which else list(COLOURS)
    dens = (lambda: _ood(rng, "density")) if "density" in which else (lambda: _u(rng, T["density"], 10))
    col = str(rng.choice(names)); scol = col if rng.random() < 0.5 else str(rng.choice(names)); ccol = str(rng.choice(names))
    stand = dict(board_mm=[round(z["board"]), round(side2), round(z["board_t"])], stick_diameter_mm=z["stick_d"], stick_height_mm=round(z["stick_h"]),
                 color=col, rgb=_rgb(rng, col), stick_color=scol, stick_rgb=_rgb(rng, scol), density=dens(), yaw_deg=_u(rng, T["yaw"], 1))
    cyl = dict(diameter_mm=z["cyl_d"], height_mm=round(z["cyl_h"]), hole_mm=z["stick_d"] + CLEARANCE, color=ccol, rgb=_rgb(rng, ccol), density=dens())
    if "distractors" in which:
        n = int(rng.integers(2, OOD["dis_n"][1] + 1))
        dis = [_distractor(rng, OOD_SHAPES if rng.random() < 0.75 else SHAPES, names) for _ in range(n)]
    else:
        dis = [_distractor(rng, SHAPES, names) for _ in range(int(rng.integers(T["dis_n"][0], T["dis_n"][1] + 1)))]
    for d in dis:
        d["rgb"] = _rgb(rng, d["color"])
    gaps = dict(task=T["gap"], mixed=T["dis_gap"], dis=T["dis_dis"])
    X, Y = T["x"], T["y"]
    edge = lambda: (-470, -410) if rng.random() < 0.5 else (-170, -110)
    it_s = dict(name="the stand", kind="task", r=math.hypot(*stand["board_mm"][:2]) / 2, x=X, y=Y)
    it_c = dict(name="the cylinder", kind="task", r=cyl["diameter_mm"] / 2, x=X, y=Y)
    its = [dict(name=f"distractor {k}", kind="dis", r=_radius(d), x=T["dis_x"], y=T["dis_y"]) for k, d in enumerate(dis)]
    if place == "near":
        it_s["x"] = it_c["x"] = (-470, -410)
    elif place == "far":
        it_s["x"] = it_c["x"] = (-170, -110)
    elif place == "close":                                          # the cylinder right beside the stand
        it_c.update(near=(0, (15, 40)), x=(-420, -160))
    elif place == "crowded":                                        # distractors right beside the cylinder and the stand
        gaps["mixed"] = 20
        while len(dis) < 2:
            d = _distractor(rng, SHAPES, names); d["rgb"] = _rgb(rng, d["color"]); dis.append(d)
            its.append(dict(name=f"distractor {len(dis) - 1}", kind="dis", r=_radius(d), x=T["dis_x"], y=T["dis_y"]))
        its[0]["near"] = (1, (20, 35)); its[1]["near"] = (0, (20, 35))
    elif place == "beyond_ruler":                                   # the cylinder on the far side of the ruler
        it_c.update(y=(RULER[1] + 20 + it_c["r"], 330), x=(-400, -220)); it_s["y"] = (-120, 100)
    xy = _arrange(rng, [it_s, it_c] + its, gaps)
    stand["xy_mm"], cyl["xy_mm"] = xy[0], xy[1]
    for d, p in zip(dis, xy[2:]):
        d["xy_mm"] = p
    tags = [w for w in which if w != "place"] + ([f"place:{place}"] if place else [])
    return dict(seed=int(seed), cylinder=cyl, stand=stand, distractors=dis, ood=tags)


def sample_table(seed):
    """The table of scene `seed` (>= TABLE_FROM). Any run of consecutive seeds covers the heights evenly and takes the
    patterns in turn; the colours are drawn at random."""
    k = int(seed) - TABLE_FROM
    rng = np.random.default_rng([7, int(seed)])
    h = TABLE_H[0] + (TABLE_H[1] - TABLE_H[0]) * (((k + 0.5) * 0.6180339887) % 1.0)
    names = list(TABLE_COLOURS)
    c1 = str(rng.choice(names)); c2 = str(rng.choice([n for n in names if n != c1]))
    jit = lambda n: [int(np.clip(c + rng.integers(-JITTER, JITTER + 1), 0, 255)) for c in TABLE_COLOURS[n]]
    pat = PATTERNS[k % len(PATTERNS)]
    size = dict(plain=0.0, checker=_u(rng, (25, 80), 1), stripes=_u(rng, (8, 50), 1), speckle=_u(rng, (1.5, 6)),
                wood=_u(rng, (15, 45), 1), blotches=_u(rng, (30, 90), 1))[pat]
    return dict(height_mm=round(h * 2) / 2, pattern=pat, color=c1, rgb=jit(c1), color2=c2, rgb2=jit(c2), size_mm=size,
                angle_deg=_u(rng, (0, 180), 1), texture_seed=int(rng.integers(1 << 30)))


def table_texture(t, n=TEX):
    """The table top's picture, (n, n, 3) uint8 RGB, for a table of sample_table: size_mm is the checker's square, the
    stripes' width, the speckles' radius, the wood's ring spacing, the blotches' size."""
    import cv2
    rng = np.random.default_rng(int(t["texture_seed"]))
    a, b = np.array(t["rgb"], np.float32), np.array(t["rgb2"], np.float32)
    mm = TABLE_TOP[0] / n
    Y, X = np.mgrid[0:n, 0:n].astype(np.float32) * mm
    th = math.radians(t["angle_deg"]); U = X * math.cos(th) + Y * math.sin(th); V = -X * math.sin(th) + Y * math.cos(th)
    s, p = float(t["size_mm"]), t["pattern"]
    smooth = lambda rows, cols: cv2.resize(rng.random((rows, cols), dtype=np.float32), (n, n), interpolation=cv2.INTER_CUBIC)
    if p == "checker":
        w = ((np.floor(U / s) + np.floor(V / s)) % 2).astype(np.float32)
    elif p == "stripes":
        w = (np.floor(U / s) % 2).astype(np.float32)
    elif p == "speckle":                                            # dots of the second colour over about 12 % of the top
        img = np.zeros((n, n), np.uint8)
        count = int(0.12 * TABLE_TOP[0] * TABLE_TOP[1] / (math.pi * s * s))
        for x, y, r in zip(rng.integers(0, n, count), rng.integers(0, n, count), rng.uniform(0.6, 1.4, count) * s / mm):
            cv2.circle(img, (int(x), int(y)), max(int(round(r)), 1), 255, -1, cv2.LINE_AA)
        w = img.astype(np.float32) / 255
    elif p == "wood":                                               # rings bent by slow noise, and streaks along the grain
        rings = 0.5 + 0.5 * np.sin(2 * math.pi * (Y / s + 2.0 * smooth(12, 12)))
        w = 0.35 * rings + 0.3 * smooth(n // 4, 6)
    elif p == "blotches":
        k = max(int(round(TABLE_TOP[0] / s)), 2)
        w = np.clip((smooth(k, k) - 0.5) * 5 + 0.5, 0, 1)
    else:
        raise ValueError(f"unknown table pattern {p!r}")
    img = a + (b - a) * w[..., None] + rng.normal(0, 2.0, (n, n, 1)).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def paint(m, sc):
    """The table's pattern into the compiled model's texture (model_xml declares it flat, of the first colour)."""
    t = sc.get("table")
    if not t or t["pattern"] == "plain":
        return
    i = m.texture("table").id
    n = int(m.tex_width[i])
    a = int(m.tex_adr[i])
    m.tex_rgb[a:a + n * n * 3] = table_texture(t, n).ravel()


def sample(seed):
    """The scene of episode `seed` of insert_varied (the evaluation scenes; a session's own scenes come from build)."""
    seed = int(seed)
    if seed >= TABLE_FROM:
        sc = sample(seed - TABLE_BACK)
        return dict(sc, seed=seed, table=sample_table(seed), ood=sc["ood"] + ["table"])
    if seed >= OOD_FROM:
        return sample_ood(seed)
    rng = np.random.default_rng([3, seed])
    sc = build({}, seed=int(rng.integers(1 << 30)))
    return dict(sc, seed=seed)


def describe(sc):
    """One line of text: what is in the scene."""
    c, s = sc["cylinder"], sc["stand"]
    b = s["board_mm"]
    out = (f"cylinder {c['diameter_mm']:g} across, {c['height_mm']:g} tall, hole {c['hole_mm']:g}, {c['color']}, {c['density']:g} kg/m3, at "
           f"({c['xy_mm'][0]:g}, {c['xy_mm'][1]:g}); stand: board {b[0]:g} x {b[1]:g} x {b[2]:g} {s['color']}, stick {s['stick_diameter_mm']:g} across, "
           f"{s['stick_height_mm']:g} above the board, {s['stick_color']}, {s['density']:g} kg/m3, at ({s['xy_mm'][0]:g}, {s['xy_mm'][1]:g}) turned {s['yaw_deg']:g} deg")
    for k, d in enumerate(sc["distractors"]):
        out += (f"; distractor_{k}: {d['color']} {d['shape']} {' x '.join(f'{v:g}' for v in d['size_mm'])} at ({d['xy_mm'][0]:g}, {d['xy_mm'][1]:g})")
    out += " (mm)"
    t = sc.get("table")
    if t:
        out += (f"; table: top at {t['height_mm']:g} mm in the robot's frame, {t['color']}"
                + ("" if t["pattern"] == "plain" else f" with {t['color2']} {t['pattern']} ({t['size_mm']:g} mm)"))
    return out


# ---------------------------------------------------------------------- the simulation's model
def layout(sc):
    """{object: (x, y, yaw_deg)} in m, for core.BiCore.reset."""
    out = {"stick_stand": (sc["stand"]["xy_mm"][0] / 1000, sc["stand"]["xy_mm"][1] / 1000, sc["stand"]["yaw_deg"]),
           "cylinder": (sc["cylinder"]["xy_mm"][0] / 1000, sc["cylinder"]["xy_mm"][1] / 1000, 0.0)}
    for k, d in enumerate(sc["distractors"]):
        out[f"distractor_{k}"] = (d["xy_mm"][0] / 1000, d["xy_mm"][1] / 1000, d["yaw_deg"])
    return out


def _rgba(rgb):
    return " ".join(f"{v / 255:.3f}" for v in rgb) + " 1"


def _geom(body, name, kind, size, pos, rgb, density, **kw):
    a = dict(name=name, type=kind, size=" ".join(f"{v:.5f}" for v in size), pos=" ".join(f"{v:.5f}" for v in pos),
             rgba=_rgba(rgb), density=f"{density:.1f}", **{**WOOD, **kw})
    return ET.SubElement(body, "geom", a)


def _stand(body, s):
    bx, by, bz = (v / 1000 for v in s["board_mm"])
    r, h = s["stick_diameter_mm"] / 2000, s["stick_height_mm"] / 1000
    _geom(body, "stand_board", "box", (bx / 2, by / 2, bz / 2), (0, 0, bz / 2), s["rgb"], s["density"])
    _geom(body, "stand_stick", "cylinder", (r, h / 2), (0, 0, bz + h / 2), s["stick_rgb"], s["density"])


def _cylinder(body, c):
    """The cylinder as scripts/build_bimanual.py makes it: WALLS boxes about the hole (their inner faces a polygon of
    apothem the hole's radius), standing on FEET points per end face. The boxes overlap: their density is scaled so
    that the cylinder weighs what a ring of its size and density does."""
    R, H, r_in = c["diameter_mm"] / 2000, c["height_mm"] / 1000, c["hole_mm"] / 2000
    t = R - r_in; rc = r_in + t / 2
    hw = R * math.tan(math.pi / WALLS) * 1.05
    lift = 0.0003
    dens = c["density"] * math.pi * (R * R - r_in * r_in) * H / (WALLS * t * 2 * hw * (H - 2 * lift))
    for k in range(WALLS):
        a = 2 * math.pi * k / WALLS
        _geom(body, f"cyl_w{k}", "box", (t / 2, hw, H / 2 - lift), (rc * math.cos(a), rc * math.sin(a), H / 2), c["rgb"], dens,
              euler=f"0 0 {a:.5f}")
    at = max(R - 0.002, r_in + 0.001)
    for end, z in (("b", FOOT_R), ("t", H - FOOT_R)):
        for k in range(FEET):
            a = 2 * math.pi * (k + 0.5) / FEET
            _geom(body, f"cyl_{end}{k}", "sphere", (FOOT_R,), (at * math.cos(a), at * math.sin(a), z), c["rgb"], 0.0,
                  priority="1", solref="0.004 1")


def _distractor_geoms(body, k, d):
    z, n, rgb, den = [v / 1000 for v in d["size_mm"]], f"dis{k}", d["rgb"], d["density"]
    s = d["shape"]
    roll = dict(condim="6", friction="1.0 0.02 0.002")
    if s == "box":
        _geom(body, n, "box", (z[0] / 2, z[1] / 2, z[2] / 2), (0, 0, z[2] / 2), rgb, den)
    elif s == "cylinder":
        _geom(body, n, "cylinder", (z[0] / 2, z[1] / 2), (0, 0, z[1] / 2), rgb, den)
    elif s == "ball":
        _geom(body, n, "sphere", (z[0] / 2,), (0, 0, z[0] / 2), rgb, den, **roll)
    elif s == "pill":
        _geom(body, n, "capsule", (z[0] / 2, (z[1] - z[0]) / 2), (0, 0, z[0] / 2), rgb, den, euler="0 1.5708 0", **roll)
    elif s == "L":                                                  # two arms of a square of side z[0], about its centre
        a, w, h = z
        _geom(body, n + "_a", "box", (a / 2, w / 2, h / 2), (0, -a / 2 + w / 2, h / 2), rgb, den)
        _geom(body, n + "_b", "box", (w / 2, (a - w) / 2, h / 2), (-a / 2 + w / 2, w / 2, h / 2), rgb, den)
    elif s == "steps":                                              # a block with a half-length block on top of one end
        l, w, h = z
        _geom(body, n + "_a", "box", (l / 2, w / 2, h / 4), (0, 0, h / 4), rgb, den)
        _geom(body, n + "_b", "box", (l / 4, w / 2, h / 4), (-l / 4, 0, 3 * h / 4), rgb, den)
    else:
        raise ValueError(f"unknown distractor shape {s!r}")


def model_xml(path, sc):
    """The environment's model (models/NAME.xml) with the scene's stand and cylinder in place of its own, and the
    scene's distractors as further free bodies obj_distractor_k -> MJCF text (paths absolute)."""
    path = Path(path)
    root = ET.parse(path).getroot()
    comp = root.find("compiler")
    comp.set("meshdir", str((path.parent / comp.get("meshdir", ".")).resolve()))
    wb = root.find("worldbody")
    bodies = {b.get("name"): b for b in wb.findall("body")}
    for name, make, spec in (("obj_stick_stand", _stand, sc["stand"]), ("obj_cylinder", _cylinder, sc["cylinder"])):
        for g in bodies[name].findall("geom"):
            bodies[name].remove(g)
        make(bodies[name], spec)
    for k, d in enumerate(sc["distractors"]):
        b = ET.SubElement(wb, "body", name=f"obj_distractor_{k}", pos=f"{0.30 + 0.15 * k:.2f} 0.10 0.0005")
        ET.SubElement(b, "freejoint", name=f"obj_distractor_{k}_free")
        _distractor_geoms(b, k, d)
    t = sc.get("table")
    if t:                                                           # another table: higher or lower, another colour or a pattern
        h = t["height_mm"] / 1000
        shift = lambda e, dz: e.set("pos", " ".join(f"{v + (dz if k == 2 else 0):.5f}" for k, v in enumerate(map(float, e.get("pos").split()))))
        geoms = {g.get("name"): g for g in wb.findall("geom")}
        top = geoms["table_top"]
        shift(top, h); shift(bodies["obj_ruler"], h)
        for k in range(4):                                          # the legs: from the floor to the top's underside
            leg = geoms[f"table_leg{k}"]
            x, y, z = map(float, leg.get("size").split())
            leg.set("size", f"{x:.5f} {y:.5f} {z + h / 2:.5f}"); shift(leg, h / 2)
        if t["pattern"] == "plain":
            top.set("rgba", _rgba(t["rgb"]))
        else:
            asset = root.find("asset")
            ET.SubElement(asset, "texture", name="table", type="2d", builtin="flat", rgb1=_rgba(t["rgb"])[:-2], width=str(TEX), height=str(TEX))
            ET.SubElement(asset, "material", name="table", texture="table", specular="0.1", shininess="0.1")
            del top.attrib["rgba"]; top.set("material", "table")
    key = root.find("keyframe").find("key")                         # (the new bodies are the last: their qpos too)
    key.set("qpos", key.get("qpos") + " 0 0 0 1 0 0 0" * len(sc["distractors"]))
    return ET.tostring(root, encoding="unicode")
