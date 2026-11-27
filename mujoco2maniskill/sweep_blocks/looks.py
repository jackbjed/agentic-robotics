"""Looks of a sweep_blocks scene: the colours of the dustpan, the brush and the cubes, and the table's appearance (a
few plain colours and textures). Only what the cameras see changes: shapes, masses, frictions and places stay.

    <robomme python> -I sweep_blocks/looks.py --make-textures                    the table textures -> assets/robodojo_sweep/tables/
    <robomme python> -I sweep_blocks/looks.py --panel DIR [--seed 11]            draw looks for every ep_NN.json of a panel (written into
                                                                                 the files, under "Appearance")

A layout file with an "Appearance" entry is shown with it by `looks.SweepBlocks` (the task of env.py, plus the looks);
`looks.install()` makes every later `from sweep_blocks.env import SweepBlocks` give that class.

How the looks are drawn (colours as sRGB 0..1; differences as CIE Lab distances, where about 2 is just visible):
  the table      one of TABLES, in turn over the panel (shuffled), so that each is used about equally often
  the objects    random hue, strong or pale, or a neutral; every object at least 50 away from the table's colour (for a
                 texture: from its mean, and 35 from its darkest and lightest tenth); every cube at least 30 from the
                 dustpan and 25 from the other cubes; the brush at least 25 from the dustpan."""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
A = HERE / "assets" / "robodojo_sweep"
TABLES = {                                                         # name -> a plain colour (sRGB) or a picture (under assets/robodojo_sweep)
    "wood_red": dict(texture="table_wood.png"),                   # (RoboDojo's own)
    "wood_pale": dict(texture="tables/wood_pale.png"),
    "wood_grey": dict(texture="tables/wood_grey.png"),
    "speckled": dict(texture="tables/speckled.png"),
    "white": dict(rgb=[0.84, 0.84, 0.82]),
    "grey": dict(rgb=[0.50, 0.51, 0.53]),
    "slate": dict(rgb=[0.17, 0.19, 0.23]),
    "sand": dict(rgb=[0.80, 0.71, 0.55]),
    "green": dict(rgb=[0.30, 0.47, 0.37]),
    "blue": dict(rgb=[0.22, 0.36, 0.58]),
}
TABLES_OOD = {                                                     # (never used in development: the final appearance test)
    "black": dict(rgb=[0.07, 0.07, 0.08]),
    "red": dict(rgb=[0.62, 0.13, 0.11]),
    "yellow": dict(rgb=[0.90, 0.78, 0.22]),
    "wood_dark": dict(texture="tables/wood_dark.png"),
    "checker": dict(texture="tables/checker.png"),
}
D_TABLE, D_TEX, D_PAN, D_CUBES, D_BRUSH = 50.0, 35.0, 30.0, 25.0, 25.0


def lab(rgb):
    return cv2.cvtColor(np.asarray(rgb, np.float32).reshape(1, -1, 3), cv2.COLOR_RGB2Lab).reshape(-1, 3)


def linear(rgb):
    c = np.asarray(rgb, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def table_colours(t):
    """A table's colours to keep away from (sRGB): its colour, or its picture's mean, darkest tenth and lightest tenth."""
    if t.get("rgb"):
        return [t["rgb"]], []
    im = cv2.cvtColor(cv2.imread(str(A / t["texture"])), cv2.COLOR_BGR2RGB).reshape(-1, 3).astype(np.float32) / 255.0
    lum = im @ [0.299, 0.587, 0.114]; lo, hi = np.quantile(lum, [0.1, 0.9])
    return [im.mean(0).tolist()], [im[lum <= lo].mean(0).tolist(), im[lum >= hi].mean(0).tolist()]


def make_textures():
    (A / "tables").mkdir(exist_ok=True)
    wood = cv2.imread(str(A / "table_wood.png"))
    L = cv2.cvtColor(wood, cv2.COLOR_BGR2Lab).astype(np.float32)
    pale = L.copy(); pale[..., 0] = np.clip(150 + (L[..., 0] - L[..., 0].mean()) * 0.8, 0, 255) + 45; pale[..., 1] = 128 + (L[..., 1] - 128) * 0.35
    pale[..., 2] = 128 + (L[..., 2] - 128) * 0.75
    cv2.imwrite(str(A / "tables" / "wood_pale.png"), cv2.cvtColor(np.clip(pale, 0, 255).astype(np.uint8), cv2.COLOR_Lab2BGR))
    grey = L.copy(); grey[..., 0] = np.clip(95 + (L[..., 0] - L[..., 0].mean()) * 1.1, 0, 255); grey[..., 1] = 128; grey[..., 2] = 126
    cv2.imwrite(str(A / "tables" / "wood_grey.png"), cv2.cvtColor(np.clip(grey, 0, 255).astype(np.uint8), cv2.COLOR_Lab2BGR))
    rng = np.random.default_rng(0)
    sp = np.full((2048, 2048, 3), (176, 180, 182), np.float32)                 # a light stone with FINE flecks (1.5 to 4 mm on the table:
    for col, n, r in (((112, 115, 120), 9000, (1, 2)), ((214, 216, 216), 7000, (1, 2)), ((146, 151, 157), 8000, (1, 3))):   # far smaller than a cube)
        for _ in range(n):
            cv2.circle(sp, (int(rng.integers(0, 2048)), int(rng.integers(0, 2048))), int(rng.integers(r[0], r[1] + 1)), col, -1, cv2.LINE_AA)
    cv2.imwrite(str(A / "tables" / "speckled.png"), cv2.GaussianBlur(sp, (0, 0), 0.8).astype(np.uint8))
    dark = L.copy(); dark[..., 0] = np.clip(L[..., 0] * 0.45, 0, 255); dark[..., 1] = 128 + (L[..., 1] - 128) * 0.6; dark[..., 2] = 128 + (L[..., 2] - 128) * 0.6
    cv2.imwrite(str(A / "tables" / "wood_dark.png"), cv2.cvtColor(np.clip(dark, 0, 255).astype(np.uint8), cv2.COLOR_Lab2BGR))
    ck = np.zeros((1120, 1120, 3), np.uint8)                                    # squares of about 5 cm in two greys
    for i in range(28):
        for j in range(28):
            ck[i * 40:(i + 1) * 40, j * 40:(j + 1) * 40] = (150, 150, 150) if (i + j) % 2 else (105, 105, 105)
    cv2.imwrite(str(A / "tables" / "checker.png"), ck)
    return sorted(p.name for p in (A / "tables").iterdir())


def a_colour(rng):
    """A random object colour (sRGB): mostly a clear hue, strong or pale; sometimes a neutral. Never very dark (the
    robot's arms are black and so are shadows)."""
    k = rng.random()
    if k < 0.15:
        v = rng.choice([0.93, 0.62, 0.36]); return [float(v)] * 3
    h, s, v = rng.uniform(0, 180), (rng.uniform(0.25, 0.45) if k < 0.35 else rng.uniform(0.6, 1.0)), rng.uniform(0.5, 1.0)
    c = cv2.cvtColor(np.array([[[h * 2, s, v]]], np.float32), cv2.COLOR_HSV2RGB)[0, 0]
    return [round(float(x), 3) for x in c]


def draw(rng, table_name, cube_labels, tables=None):
    t = dict((tables or TABLES)[table_name], name=table_name)
    main, extra = table_colours(t)
    far = lambda c, others, d: all(float(np.linalg.norm(lab(c)[0] - lab(o)[0])) >= d for o in others)
    ok_table = lambda c: far(c, main, D_TABLE) and far(c, extra, D_TEX)
    for _ in range(20000):
        pan, brush = a_colour(rng), a_colour(rng)
        if not (ok_table(pan) and ok_table(brush) and far(brush, [pan], D_BRUSH)):
            continue
        cubes = []
        for _ in range(400):
            c = a_colour(rng)
            if ok_table(c) and far(c, [pan], D_PAN) and far(c, cubes, D_CUBES):
                cubes.append(c)
                if len(cubes) == len(cube_labels):
                    break
        if len(cubes) == len(cube_labels):
            d = lambda a, b: round(float(np.linalg.norm(lab(a)[0] - lab(b)[0])), 1)
            return dict(table=t, broom_shovel=pan, broom=brush, cubes=dict(zip(cube_labels, cubes)),
                        lab_distances=dict(table_min=min(d(c, main[0]) for c in [pan, brush] + cubes), cubes_to_pan_min=min(d(c, pan) for c in cubes)))
    raise RuntimeError("no looks found")


def _shapes(actor):
    import sapien
    return actor._objs[0].find_component_by_type(sapien.render.RenderBodyComponent).render_shapes


def apply(task):
    """Give a built task (SweepBlocks) the looks its layout names under "Appearance" (nothing, if it names none)."""
    import sapien
    ap = task.layout.get("Appearance")
    if not ap:
        return False
    U = task.U
    rgba = lambda c: [float(v) for v in linear(c)] + [1.0]         # (the renderer's colours are linear)
    for name in ("broom_shovel", "broom"):
        for s in _shapes(U.objects[name]):
            for p in s.parts:
                p.material.base_color_texture = None; p.material.set_base_color(rgba(ap[name]))
    for label, c in ap["cubes"].items():
        for s in _shapes(U.objects[label]):
            s.material.set_base_color(rgba(c))
    m = _shapes(U.world)[0].material                               # (the world's first box is the tabletop)
    t = ap["table"]
    if t.get("texture"):
        m.set_base_color([1, 1, 1, 1]); m.base_color_texture = sapien.render.RenderTexture2D(filename=str(A / t["texture"]))
    else:
        m.base_color_texture = None; m.set_base_color(rgba(t["rgb"]))
    return True


def _base():
    import sweep_blocks.env as E
    return getattr(E, "_SweepBlocksPlain", E.SweepBlocks)


class SweepBlocks(_base()):
    """env.SweepBlocks, shown with its layout's looks."""

    def reset(self):
        super().reset()
        apply(self)
        return self.observe()


def install():
    import sweep_blocks.env as E
    if not hasattr(E, "_SweepBlocksPlain"):
        E._SweepBlocksPlain = E.SweepBlocks
    E.SweepBlocks = SweepBlocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-textures", action="store_true"); ap.add_argument("--panel"); ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--ood", action="store_true", help="the tables of the out-of-distribution appearance split")
    a = ap.parse_args()
    if a.make_textures:
        print(make_textures())
    if a.panel:
        files = sorted(Path(a.panel).glob("ep_*.json"))
        rng = np.random.default_rng(a.seed)
        names = []
        TB = TABLES_OOD if a.ood else TABLES
        while len(names) < len(files):                              # every table in turn, shuffled
            names += [str(n) for n in rng.permutation(list(TB))]
        for f, tn in zip(files, names):
            L = json.loads(f.read_text())
            L["Appearance"] = draw(rng, tn, [o["label"] for o in L["Rigid"]["small_cube"]], TB)
            f.write_text(json.dumps(L))
            x = L["Appearance"]
            print(f.stem, f"table {tn:10s}", "pan", x["broom_shovel"], "brush", x["broom"], "cubes", list(x["cubes"].values()), x["lab_distances"])
        (Path(a.panel) / "looks.json").write_text(json.dumps(dict(seed=a.seed, tables=TB, rules=dict(
            object_to_table=D_TABLE, object_to_texture_extremes=D_TEX, cube_to_pan=D_PAN, cube_to_cube=D_CUBES, brush_to_pan=D_BRUSH),
            note="colours sRGB 0..1; distances CIE Lab; physics unchanged"), indent=1))


if __name__ == "__main__":
    main()
