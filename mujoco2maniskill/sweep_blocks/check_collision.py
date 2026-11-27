"""How well do the collision pieces follow an object's real surface? Rays from above, over a grid: the height where
the picture mesh is first met, and where the collision pieces are. (A dustpan whose hollow is filled is no dustpan.)

    .venv_usd/bin/python -I sweep_blocks/check_collision.py broom_shovel"""
import sys
from pathlib import Path
import numpy as np
import trimesh
A = Path(__file__).resolve().parents[1] / "assets" / "robodojo_sweep"
name = sys.argv[1]
vis = trimesh.load(A / f"{name}_visual.glb", force="mesh", process=False)
parts = [trimesh.load(f, force="mesh", process=False) for f in sorted((A / f"{name}_collision").glob("part_*.obj"))]
hull = trimesh.util.concatenate([p.convex_hull for p in parts])
lo, hi = vis.bounds
xs, ys = np.linspace(lo[0], hi[0], 15)[1:-1], np.linspace(lo[1], hi[1], 23)[1:-1]
G = np.array([[x, y] for y in ys for x in xs])
def top(mesh):
    o = np.c_[G, np.full(len(G), hi[2] + 0.05)]; d = np.tile([0, 0, -1.0], (len(G), 1))
    loc, ray, _ = mesh.ray.intersects_location(o, d, multiple_hits=False)
    z = np.full(len(G), np.nan); z[ray] = loc[:, 2]
    return z
zv, zc = top(vis), top(hull)
both = np.isfinite(zv) & np.isfinite(zc)
d = (zc - zv)[both] * 1000
print(name, "pieces", len(parts), "| collision surface above the real one (mm): median", round(float(np.median(d)), 1), "p90", round(float(np.quantile(d, 0.9)), 1), "max", round(float(d.max()), 1),
      "| real surface height range (mm)", round(float(np.nanmin(zv) * 1000), 1), round(float(np.nanmax(zv) * 1000), 1))
print("real surface (mm above the object's bottom), rows = y from handle side to mouth, columns = x:")
for j in range(len(ys)):
    print("  y %+.3f  " % ys[j] + " ".join("%4.0f" % ((zv[j * len(xs) + i] - lo[2]) * 1000) if np.isfinite(zv[j * len(xs) + i]) else "   ." for i in range(len(xs)))
          + "   | collision: " + " ".join("%4.0f" % ((zc[j * len(xs) + i] - lo[2]) * 1000) if np.isfinite(zc[j * len(xs) + i]) else "   ." for i in range(len(xs))))
