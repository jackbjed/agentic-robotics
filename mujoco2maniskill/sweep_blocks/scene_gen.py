"""Practice scenes of sweep_blocks IN THE TARGET SIMULATOR, for the condition that practises there (xsim: condition F).
A scene is described the way a developer may ask for it: where the three kinds of things lie on the table, how they are
turned, their colours, the table's look. The things themselves are the target's: RoboDojo's dustpan, brush and three
cubes, with their shapes, masses and frictions. Nothing here can change those, the robot, the sensors or the physics.

    a scene (millimetres and degrees, table frame: z up, the table's surface at z = 0, +x to the robot's right, +y away):
      {"dustpan": {"xy_mm": [x, y], "yaw_deg": a},          a: the direction its mouth faces (0 = +x, 90 = +y)
       "brush":   {"xy_mm": [x, y], "yaw_deg": a},          a: the direction from its handle's end to its head
       "cubes":   [{"xy_mm": [x, y], "yaw_deg": a}, x3],    always three
       "colours": {"dustpan": [r, g, b], "brush": [r, g, b], "cubes": [[r, g, b], x3]},     0..1
       "table":   {"name": one of TABLE_NAMES} or {"rgb": [r, g, b]}}

sample(rng)      a FRESH scene: drawn the way the development scenes were drawn (make_panel.candidate, looks.draw), from
                 the generator's own random stream. It is not checked with the scripted reference, and it is never one
                 of the frozen development or test scenes (those were drawn from other streams and are not read here).
check(scene)     why a requested scene is not valid ([]: it is): everything on the table and clear of the robot's
                 bases and the camera stand, nothing on top of anything, three cubes, colours in range, a known table.
to_layout(scene) the scene as the simulator's layout (RoboDojo's format, with "Appearance"); from_layout: the reverse.
rests(...)       after the simulator has let the scene settle: did everything stay where it was asked to be?"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
A = HERE / "assets" / "robodojo_sweep"
BASE = A / "layouts" / "sweep_blocks_4.json"
TABLE_Z = 0.765
NAMES = ("dustpan", "brush", "cube_0", "cube_1", "cube_2")          # what the things are called in a practice scene's state
SIM_NAME = {"dustpan": "broom_shovel", "brush": "broom"}            # ... and in the simulator
X_MM, Y_MM, EDGE_MM, GAP_MM = (-700.0, 700.0), (-600.0, 500.0), 20.0, 4.0
KEEP_OUT = {"the left arm's base": (-390.0, -540.0, -210.0, -360.0), "the right arm's base": (210.0, -540.0, 390.0, -360.0),
            "the camera stand's foot": (-165.0, -600.0, 165.0, -410.0)}                      # (x0, y0, x1, y1) mm
REST_MM, REST_TILT_DEG = 5.0, 3.0


def table_names():
    from sweep_blocks import looks
    return tuple(looks.TABLES)                                      # (the development tables only: the test's other tables cannot be asked for)


def _R(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _qmul(a, b):
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw]


def _qz(a):
    return [float(math.cos(a / 2)), 0.0, 0.0, float(math.sin(a / 2))]


def _base():
    L = json.loads(BASE.read_text())
    return L, L["Rigid"]["broom_shovel"][0], L["Rigid"]["broom"][0], L["Rigid"]["small_cube"][0]


def _heading(kind, q):
    """The direction a thing 'faces' on the table, degrees from +x: the dustpan's mouth (its own -y), the brush's head
    (its own +z), a cube's own x."""
    R = _R(q)
    v = -R[:2, 1] if kind == "dustpan" else R[:2, 2] if kind == "brush" else R[:2, 0]
    return math.degrees(math.atan2(v[1], v[0]))


def _turned(kind, yaw_deg):
    """The thing's orientation in the simulator, lying as it lies in the target's scenes, facing yaw_deg."""
    _, pan, brush, _ = _base()
    if kind == "cube":
        return _qz(math.radians(yaw_deg))
    q0 = pan["default_ori"] if kind == "dustpan" else brush["default_ori"]
    return _qmul(_qz(math.radians(yaw_deg - _heading(kind, q0))), q0)


def from_layout(L):
    R = L["Rigid"]; pan, br = R["broom_shovel"][0], R["broom"][0]; ap = L.get("Appearance") or {}
    mm = lambda o: [round(float(o["default_pos"][0]) * 1000, 3), round(float(o["default_pos"][1]) * 1000, 3)]
    sc = dict(dustpan=dict(xy_mm=mm(pan), yaw_deg=round(_heading("dustpan", pan["default_ori"]), 4)),
              brush=dict(xy_mm=mm(br), yaw_deg=round(_heading("brush", br["default_ori"]), 4)),
              cubes=[dict(xy_mm=mm(c), yaw_deg=round(_heading("cube", c["default_ori"]), 4)) for c in R["small_cube"]])
    if ap:
        t = ap["table"]
        sc["colours"] = dict(dustpan=list(ap["broom_shovel"]), brush=list(ap["broom"]), cubes=[list(ap["cubes"][c["label"]]) for c in R["small_cube"]])
        sc["table"] = dict(name=t["name"]) if t.get("name") in table_names() else dict(rgb=list(t["rgb"])) if t.get("rgb") else dict(name=t.get("name"))
    return sc


def sample(rng):
    """A fresh scene (see the module's text)."""
    from sweep_blocks import looks
    from sweep_blocks.make_panel import candidate
    names = table_names()
    for _ in range(50):                                             # (a draw that this module's own rules refuse is drawn again)
        L = candidate(rng)
        L["Appearance"] = looks.draw(rng, names[int(rng.integers(0, len(names)))], [o["label"] for o in L["Rigid"]["small_cube"]])
        sc = from_layout(L)
        if not check(sc):
            return sc
    raise RuntimeError("no valid fresh scene was drawn")


def _num(v, n=None):
    try:
        a = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    return a if (n is None or len(a) == n) and all(math.isfinite(x) for x in a) else None


# the things' outlines in their own frames (m), in convex parts, measured on the collision shapes: the dustpan's scoop
# (its mouth at -y) and its handle; the brush's head (+z) and its curved handle; a cube
_PARTS = {
    "dustpan": [[(-0.093, -0.141, 0), (0.093, -0.141, 0), (0.093, -0.02, 0), (0.046, 0.04, 0), (-0.046, 0.04, 0), (-0.093, -0.02, 0)],
                [(-0.017, 0.04, 0), (0.017, 0.04, 0), (0.017, 0.1405, 0), (-0.017, 0.1405, 0)]],
    "brush": [[(0, -0.042, -0.012), (0, 0.018, -0.012), (0, 0.018, 0.126), (0, -0.042, 0.126)],
              [(0, -0.009, -0.126), (0, 0.042, -0.126), (0, 0.042, -0.01), (0, -0.009, -0.01)]],
    "cube": [[(-0.00962, -0.00962, 0), (0.00962, -0.00962, 0), (0.00962, 0.00962, 0), (-0.00962, 0.00962, 0)]],
}


def footprint(kind, xy_mm, yaw_deg):
    """The thing's outline on the table (mm), as it lies there: a list of convex polygons."""
    from scipy.spatial import ConvexHull
    R = _R(_turned(kind, yaw_deg)); out = []
    for part in _PARTS[kind]:
        pts = (np.asarray(part, float) @ R.T)[:, :2] * 1000 + np.asarray(xy_mm, float)
        out.append(pts[ConvexHull(pts).vertices])
    return out


def _apart(P, Q, gap):
    """Are two convex outlines more than `gap` apart? (separating axes)"""
    for A_, B_ in ((P, Q), (Q, P)):
        for i in range(len(A_)):
            e = A_[(i + 1) % len(A_)] - A_[i]; n = np.array([e[1], -e[0]]); n = n / (np.linalg.norm(n) + 1e-12)
            a, b = A_ @ n, B_ @ n
            if a.max() + gap < b.min() or b.max() + gap < a.min():
                return True
    return False


def check(sc):
    """-> the reasons this scene is not built (none: [])."""
    bad = []
    if not isinstance(sc, dict):
        return ["scene() must return the scene dict"]
    things = []
    for kind in ("dustpan", "brush"):
        o = sc.get(kind)
        if not isinstance(o, dict) or _num(o.get("xy_mm"), 2) is None or _num([o.get("yaw_deg")], 1) is None:
            bad.append(f'{kind}: give {{"xy_mm": [x, y], "yaw_deg": a}} with finite numbers')
        else:
            things.append((kind, kind, o))
    cubes = sc.get("cubes")
    if not isinstance(cubes, list) or len(cubes) != 3:
        bad.append("cubes: a list of exactly three (the task has three blocks)")
    else:
        for i, o in enumerate(cubes):
            if not isinstance(o, dict) or _num(o.get("xy_mm"), 2) is None or _num([o.get("yaw_deg")], 1) is None:
                bad.append(f'cubes[{i}]: give {{"xy_mm": [x, y], "yaw_deg": a}} with finite numbers')
            else:
                things.append((f"cube_{i}", "cube", o))
    col = sc.get("colours")
    if col is not None:
        ok = isinstance(col, dict) and all(_num(col.get(k), 3) is not None and all(0 <= v <= 1 for v in _num(col.get(k), 3)) for k in ("dustpan", "brush")) \
            and isinstance(col.get("cubes"), list) and len(col["cubes"]) == 3 and all(_num(c, 3) is not None and all(0 <= v <= 1 for v in _num(c, 3)) for c in col["cubes"])
        if not ok:
            bad.append('colours: {"dustpan": [r, g, b], "brush": [r, g, b], "cubes": [[r, g, b], [r, g, b], [r, g, b]]} with values from 0 to 1')
    t = sc.get("table")
    if t is not None:
        if not isinstance(t, dict) or not ((isinstance(t.get("name"), str) and t["name"] in table_names() and "rgb" not in t)
                                           or (_num(t.get("rgb"), 3) is not None and all(0 <= v <= 1 for v in _num(t.get("rgb"), 3)) and "name" not in t)):
            bad.append(f'table: {{"name": one of {list(table_names())}}} or {{"rgb": [r, g, b]}} with values from 0 to 1')
    if bad:
        return bad[:10]
    shapes = {n: footprint(k, o["xy_mm"], float(o["yaw_deg"])) for n, k, o in things}
    for n, parts in shapes.items():
        P = np.vstack(parts)
        if P[:, 0].min() < X_MM[0] + EDGE_MM or P[:, 0].max() > X_MM[1] - EDGE_MM or P[:, 1].min() < Y_MM[0] + EDGE_MM or P[:, 1].max() > Y_MM[1] - EDGE_MM:
            bad.append(f"{n} would lie over the table's edge (the table is x {X_MM[0]:.0f}..{X_MM[1]:.0f}, y {Y_MM[0]:.0f}..{Y_MM[1]:.0f} mm; keep {EDGE_MM:.0f} mm from the edge)")
        for what, (x0, y0, x1, y1) in KEEP_OUT.items():
            if any(not _apart(Q, np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]), 0.0) for Q in parts):
                bad.append(f"{n} would lie on {what}")
    names = list(shapes)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if any(not _apart(P, Q, GAP_MM) for P in shapes[names[i]] for Q in shapes[names[j]]):
                bad.append(f"{names[i]} and {names[j]} would overlap (every thing starts on the table, at least {GAP_MM:.0f} mm from the next)")
    return bad[:10]


def to_layout(sc):
    """The simulator's layout of a VALID scene (check() first). Shapes, sizes, masses and frictions are the base layout's."""
    L, pan, brush, cube = _base()
    L = copy.deepcopy(L); R = L["Rigid"]
    for kind, o in (("dustpan", R["broom_shovel"][0]), ("brush", R["broom"][0])):
        o["default_pos"][0], o["default_pos"][1] = float(sc[kind]["xy_mm"][0]) / 1000, float(sc[kind]["xy_mm"][1]) / 1000
        o["default_ori"] = [float(v) for v in _turned(kind, float(sc[kind]["yaw_deg"]))]
    cubes = []
    for i, c in enumerate(sc["cubes"]):
        o = copy.deepcopy(R["small_cube"][i % len(R["small_cube"])]); o["label"] = f"cube_{i}"; o["category_idx"] = 0
        o["default_pos"][0], o["default_pos"][1] = float(c["xy_mm"][0]) / 1000, float(c["xy_mm"][1]) / 1000
        o["default_ori"] = _turned("cube", float(c["yaw_deg"]))
        cubes.append(o)
    R["small_cube"] = cubes
    col, t = sc.get("colours"), sc.get("table")
    if col is not None or t is not None:
        from sweep_blocks import looks
        col = col or dict(dustpan=[0.2, 0.4, 0.8], brush=[0.8, 0.3, 0.2], cubes=[[0.9, 0.2, 0.2], [0.2, 0.8, 0.3], [0.9, 0.8, 0.1]])
        t = t or dict(name="grey")
        table = dict(looks.TABLES[t["name"]], name=t["name"]) if "name" in t else dict(rgb=[float(v) for v in t["rgb"]], name="asked")
        L["Appearance"] = dict(table=table, broom_shovel=[float(v) for v in col["dustpan"]], broom=[float(v) for v in col["brush"]],
                               cubes={f"cube_{i}": [float(v) for v in c] for i, c in enumerate(col["cubes"])})
    L["PracticeScene"] = True
    return L


def rests(sc, poses):
    """poses: {name: (position m, 3x3)} after the simulator let the scene settle. -> (how far each thing is from where it
    was asked to be (mm), the reasons the scene does not rest as asked ([]: it does))."""
    moved, bad = {}, []
    want = dict(dustpan=sc["dustpan"], brush=sc["brush"], **{f"cube_{i}": c for i, c in enumerate(sc["cubes"])})
    for n, o in want.items():
        p, R = poses[n]
        d = float(np.linalg.norm(np.asarray(p[:2]) * 1000 - np.asarray(o["xy_mm"], float)))
        moved[n] = round(d, 1)
        if d > REST_MM:
            bad.append(f"{n} did not stay where it was asked to be (it moved {d:.0f} mm while the scene settled)")
    tilt = math.degrees(math.acos(float(np.clip(poses["dustpan"][1][2, 2], -1, 1))))
    if tilt > REST_TILT_DEG:
        bad.append(f"the dustpan does not lie level ({tilt:.0f} degrees)")
    return moved, bad
