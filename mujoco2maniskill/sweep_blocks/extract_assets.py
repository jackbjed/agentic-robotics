"""RoboDojo's objects for the sweep_blocks task (USD files, read only) -> files ManiSkill can load, in
assets/robodojo_sweep/: for each object a picture mesh (.glb, with its texture), collision shapes as convex pieces
(.obj; RoboDojo's files ask PhysX for a convex decomposition of at most 64 hulls: here CoACD does it), and a .json
(size, mass, RoboDojo's own annotations used by the success check). Also the table's wood picture and the 55 saved
evaluation layouts.

    .venv_usd/bin/python -I sweep_blocks/extract_assets.py        (usd-core, trimesh, coacd: this folder's own venv)"""
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image
from pxr import Usd, UsdGeom, UsdShade

HERE = Path(__file__).resolve().parents[1]
RD = HERE.parents[1] / "RoboDojo"
A = RD / "Assets"
OUT = HERE / "assets" / "robodojo_sweep"
OUT.mkdir(parents=True, exist_ok=True)


def meshes(stage, want):
    cache = UsdGeom.XformCache(); out = []
    for p in stage.Traverse():
        if not p.IsA(UsdGeom.Mesh) or not want(str(p.GetPath())):
            continue
        m = UsdGeom.Mesh(p)
        pts = np.array(m.GetPointsAttr().Get(), float); T = np.array(cache.GetLocalToWorldTransform(p))
        V = pts @ T[:3, :3] + T[3, :3]
        cnt = np.array(m.GetFaceVertexCountsAttr().Get()); idx = np.array(m.GetFaceVertexIndicesAttr().Get())
        assert (cnt == 3).all(), "triangles only"
        F = idx.reshape(-1, 3)
        uv = None
        for name in ("uv", "st"):
            pv = UsdGeom.PrimvarsAPI(p).GetPrimvar(name)
            if pv and pv.Get() is not None and pv.GetInterpolation() == "vertex":
                uv = np.array(pv.Get(), float)
        col = None
        mat = UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
        if mat:
            for s in Usd.PrimRange(mat.GetPrim()):
                for a in s.GetAttributes():
                    if a.GetName() in ("inputs:diffuseColor", "inputs:base_color_factor") and a.HasValue() and col is None:
                        v = a.Get()
                        if hasattr(v, "__len__") and len(v) >= 3:
                            col = [float(v[0]), float(v[1]), float(v[2])]
        out.append(dict(path=str(p.GetPath()), V=V, F=F, uv=uv, color=col))
    return out


def decompose(V, F, name, max_hulls=64, threshold=0.04, simplify_to=60000):
    import coacd
    if len(F) > simplify_to:
        import fast_simplification
        V, F = fast_simplification.simplify(V.astype(np.float32), F.astype(np.int32), target_count=simplify_to)
    coacd.set_log_level("error")
    parts = coacd.run_coacd(coacd.Mesh(np.asarray(V, float), np.asarray(F, np.int32)), threshold=threshold, max_convex_hull=max_hulls,
                            preprocess_resolution=60, resolution=3000, mcts_iterations=120, mcts_max_depth=3)
    d = OUT / f"{name}_collision"                                  # (one file per convex piece: a loader that is given
    if d.exists():                                                  #  them in one file may wrap them all in one hull)
        shutil.rmtree(d)
    d.mkdir()
    for i, (v, t) in enumerate(parts):
        with open(d / f"part_{i:02d}.obj", "w") as f:
            f.writelines(f"v {a:.6f} {b:.6f} {c:.6f}\n" for a, b, c in v)
            f.writelines(f"f {a + 1} {b + 1} {c + 1}\n" for a, b, c in t)
    return len(parts)


def one(name, usd, meta_file=None, visual=lambda p: "collision" not in p, collide=lambda p: "collision" in p, hulls=64, threshold=0.04):
    if (OUT / f"{name}.json").exists() and "--again" not in sys.argv:
        print(name, "| already there (--again to redo)")
        return
    st = Usd.Stage.Open(str(usd))
    vis = meshes(st, visual)
    tex = None
    if str(usd).endswith(".usdz"):
        z = zipfile.ZipFile(usd)
        for s in st.Traverse():
            if s.GetName() == "tex_base_color":
                f = str(s.GetAttribute("inputs:file").Get()).strip("@").lstrip("./")
                tex = Image.open(io.BytesIO(z.read(f))).convert("RGB")
                if max(tex.size) > 2048:
                    tex.thumbnail((2048, 2048))
    scene = trimesh.Scene()
    for i, m in enumerate(vis):
        if tex is not None and m["uv"] is not None:
            visual_ = trimesh.visual.TextureVisuals(uv=m["uv"], image=tex)
        else:
            c = m["color"] or [0.6, 0.6, 0.6]
            visual_ = trimesh.visual.ColorVisuals(vertex_colors=np.tile((np.r_[np.clip(c, 0, 1), 1.0] * 255).astype(np.uint8), (len(m["V"]), 1)))
        scene.add_geometry(trimesh.Trimesh(m["V"], m["F"], visual=visual_, process=False), node_name=f"m{i}")
    scene.export(OUT / f"{name}_visual.glb")
    col = meshes(st, collide)
    V = np.vstack([m["V"] for m in col]); F = np.vstack([m["F"] + sum(len(x["V"]) for x in col[:i]) for i, m in enumerate(col)])
    n = decompose(V, F, name, hulls, threshold)
    info = dict(name=name, source=str(usd.relative_to(RD)), bbox_min=V.min(0).round(5).tolist(), bbox_max=V.max(0).round(5).tolist(),
                collision_parts=n, visual_faces=int(sum(len(m["F"]) for m in vis)), colors=[m["color"] for m in vis][:4])
    if meta_file:
        md = json.loads(Path(meta_file).read_text())
        info.update(physics=md.get("physics"), active=md.get("active"), passive=md.get("passive"))
    (OUT / f"{name}.json").write_text(json.dumps(info, indent=1))
    print(name, "| picture faces", info["visual_faces"], "| collision pieces", n, "| size (m)", (V.max(0) - V.min(0)).round(4).tolist(), flush=True)


def main():
    R = A / "Object" / "RoboDojo"
    one("broom_shovel", R / "Rigid/broom_shovel/00000/object.usdz", R / "Rigid/broom_shovel/00000/metadata.json", threshold=0.015)
    one("broom", R / "Rigid/broom/00000/object.usdz", R / "Rigid/broom/00000/metadata.json")
    one("camera_stand", R / "Geometry/camera_stand/00000/object.usd", R / "Geometry/camera_stand/00000/metadata.json",
        visual=lambda p: True, collide=lambda p: True, hulls=16, threshold=0.08)
    cubes = {}
    for i in range(10):
        d = R / f"Rigid/small_cube/{i:05d}"
        st = Usd.Stage.Open(str(d / "object.usdz")); m = meshes(st, lambda p: "visual" in p)[0]
        md = json.loads((d / "metadata.json").read_text())
        cubes[i] = dict(half=((m["V"].max(0) - m["V"].min(0)) / 2).round(5).tolist(), color=m["color"], physics=md["physics"])
    (OUT / "small_cube.json").write_text(json.dumps(cubes, indent=1))
    print("small_cube: 10 colours, half size (m)", cubes[0]["half"])
    shutil.copy(A / "Material/material_0122/Mahogany_Planks_BaseColor.png", OUT / "table_wood.png")
    L = OUT / "layouts"; L.mkdir(exist_ok=True)
    n = 0
    for f in sorted((A / "Eval_Layout/RoboDojo/arx_x5/0").glob("sweep_blocks_*.json")):
        shutil.copy(f, L / f.name); n += 1
    print("layouts copied:", n)


if __name__ == "__main__":
    main()
