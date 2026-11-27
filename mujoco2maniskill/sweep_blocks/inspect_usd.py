"""What is inside one of RoboDojo's object files (object.usdz): prims, meshes, sizes, physics settings."""
import sys
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade
st = Usd.Stage.Open(sys.argv[1])
print("up axis", UsdGeom.GetStageUpAxis(st), "metres per unit", UsdGeom.GetStageMetersPerUnit(st), "default prim", st.GetDefaultPrim().GetPath() if st.GetDefaultPrim() else None)
cache = UsdGeom.XformCache()
for p in st.Traverse():
    apis = [a for a in p.GetAppliedSchemas()]
    line = f"{p.GetPath()}  [{p.GetTypeName()}] {apis if apis else ''}"
    if p.IsA(UsdGeom.Mesh):
        m = UsdGeom.Mesh(p); pts = np.array(m.GetPointsAttr().Get()); T = np.array(cache.GetLocalToWorldTransform(p))
        W = pts @ T[:3, :3] + T[3, :3]
        line += f" verts {len(pts)} faces {len(m.GetFaceVertexCountsAttr().Get())} world min {W.min(0).round(4)} max {W.max(0).round(4)}"
    for a in p.GetAttributes():
        n = a.GetName()
        if n.startswith(("physics:", "physx", "xformOp")) and a.HasValue():
            v = a.Get()
            line += f"\n      {n} = {str(v)[:120]}"
    print(line)
