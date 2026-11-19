"""Condition A colour: the splat composite of a camera frame (the request the twin's benches made, vendored
stack_bench.Perception.observe, to our own compositor: configs/services.yaml compositor).

What the image is: the empty-table Gaussian splat (scene scan v6507) with the real arms cut out and darkened where
MuJoCo casts shadows (shade = the frame's luminance over the same frame without the arm and objects); each object's own
Gaussians (cut from the scans) at its simulated pose; MuJoCo's own drawing where no scan exists (the "_inner" fill
surfaces) and for the robot arm, on top by depth. NOT full photorealism: a static background, rigid object
Gaussians, MuJoCo's arm. The depth delivered to programs stays MuJoCo's through the D415 model."""
from __future__ import annotations

import io
import json
import urllib.request

import numpy as np

from .. import services


class Compositor:
    def __init__(self, P):
        self.P = P; m = P.m
        names = [m.body(b).name for b in range(m.nbody)]
        self.arm_geom = np.array([names[b].startswith(("L_", "R_")) for b in m.geom_bodyid])
        self.obj_geom = np.array([names[b].startswith("obj_") for b in m.geom_bodyid])
        self.fill_geom = np.array(["_inner" in (m.geom(g).name or "") for g in range(m.ngeom)])
        self.obj_bodies = [b for b in range(m.nbody) if names[b].startswith("obj_")]
        self.obj_names = [names[b] for b in self.obj_bodies]
        self.geom_group0 = m.geom_group.copy()
        self.url = services.url("compositor", "/composite")

    def photo(self, cam, rgb, dep, gid, K):
        P = self.P; m, d, mj, rr = P.m, P.d, P.mj, P.renderer
        cid = m.camera(cam).id
        is_geom = gid >= 0
        g = np.clip(gid, 0, m.ngeom - 1)
        label = np.select([is_geom & self.arm_geom[g], is_geom & self.fill_geom[g], is_geom & self.obj_geom[g]], [1, 3, 2], 0).astype(np.uint8)
        m.geom_group[self.arm_geom | self.obj_geom] = 5
        try:
            opt = mj.MjvOption(); opt.sitegroup[:] = 0; opt.geomgroup[:] = 1; opt.geomgroup[5] = 0
            rr.update_scene(d, camera=cid, scene_option=opt); rgb_s = rr.render().copy()
        finally:
            m.geom_group[:] = self.geom_group0
        lum = lambda im: im.astype(np.float32).mean(2) + 1.0
        shade = np.clip(lum(rgb) / lum(rgb_s), 0, 1.99); shade[label > 0] = 1.0
        R_true, eye = d.cam_xmat[cid].reshape(3, 3) @ np.diag([1, -1, -1]), d.cam_xpos[cid].copy()
        buf = io.BytesIO()
        np.savez(buf, R_cv=R_true, eye=eye, K=np.array(K, float), rgb=rgb, depth=dep, label=label,
                 shade=shade.astype(np.float32), names=json.dumps(self.obj_names), pos=d.xpos[self.obj_bodies],
                 quat=d.xquat[self.obj_bodies])
        with urllib.request.urlopen(urllib.request.Request(self.url, data=buf.getvalue()), timeout=120) as r:
            return np.load(io.BytesIO(r.read()))["rgb"]
