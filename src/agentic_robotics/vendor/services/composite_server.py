"""Live photoreal composite for the Panda twin (what composite.py does per frame, as a service): the empty-table splat with
the real arms cut out, the objects' own Gaussians at the simulated poses, MuJoCo's arms on top by depth.

    PYTHONNOUSERSITE=1 CUDA_VISIBLE_DEVICES=0 ~/miniforge3/envs/twin/bin/python code/composite_server.py --port 8132

POST /composite, body = np.savez bytes: R_cv (3,3), eye (3,), K (W, H, fx, fy, cx, cy), rgb (H,W,3) uint8, depth (H,W)
float32 m (inf = nothing), label (H,W) uint8 and shade (H,W) float32 from MuJoCo (see render_mj.py), names (json list),
pos (B,3), quat (B,4) of the obj_* bodies -> np.savez bytes with rgb (H,W,3) uint8 and depth (H,W) float32 m (z-depth
of what the image shows: splat surfaces where the splat is drawn, MuJoCo's elsewhere).  GET /ping -> "ok".
"""
import argparse
import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import splat_util  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = threading.Lock()
TOL, SHADOW = 0.12, 0.55
FILL_FRONT = False                                              # --fill-front (see composite.py)


def load(splat, frame, cut, objects, cut_r=0.025):
    global G, OS
    G = splat_util.load(splat, json.load(open(frame)))
    from scipy.spatial import cKDTree
    dist = cKDTree(np.load(cut)).query(G["means"].cpu().numpy(), distance_upper_bound=cut_r)[0]
    G = splat_util.subset(G, torch.from_numpy(~np.isfinite(dist) | (dist >= cut_r)))
    OS = torch.load(objects, weights_only=False)
    recentre(OS, RECENTRE)
    if "obj_kettle" in OS["names"]:
        split_lid(OS)


KETTLE_HINGE = np.array([-0.056, 0.0, 0.165])                   # build_tasks_scene.py: lid hinge in the kettle's frame


def split_lid(OS):
    """The kettle's lid Gaussians (top disc: z > 15 cm, within 5.8 cm of the axis, not the handle) become their own
    object "obj_kettle_lid", in the lid body's frame (origin at the hinge), so the lid opens in the photoreal image.
    Requests from models without a lid body pose it closed (with the kettle)."""
    names, L, M = list(OS["names"]), OS["label"], OS["means"]
    k = names.index("obj_kettle")
    lid = (L == k) & (M[:, 2] > 0.150) & (torch.hypot(M[:, 0], M[:, 1]) < 0.058) & (M[:, 0] > -0.060)
    OS["names"] = names + ["obj_kettle_lid"]
    OS["label"] = torch.where(lid, torch.full_like(L, len(names)), L)
    M[lid] -= torch.tensor(KETTLE_HINGE, dtype=M.dtype)
    print(f"split the kettle's lid: {int(lid.sum())} Gaussians", flush=True)


def with_lid(names, pos, quat):
    """Add a closed lid pose when the request's model has no lid body."""
    names = list(names)
    if "obj_kettle_lid" in names or "obj_kettle" not in names:
        return names, pos, quat
    i = names.index("obj_kettle"); q = np.asarray(quat[i], float)
    w, x, y, z = q
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                  [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                  [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
    return names + ["obj_kettle_lid"], np.vstack([pos, pos[i] + R @ KETTLE_HINGE]), np.vstack([quat, q])


RECENTRE = {"obj_oct_red": 0.0295, "obj_oct_green": 0.0295}       # name: height (m)


def recentre(OS, which):
    """Put an object's Gaussians on its body frame: the scan's cut is off by up to 1.3 cm sideways (red) and sits
    1-1.5 cm low (short objects, see make_objects caveats), so the photoreal piece was not where the physics piece is.
    Shift = (outer-rim circle centre -> 0, top (98th pct) -> +h/2), from the opaque Gaussians."""
    names, L, M = list(OS["names"]), OS["label"].numpy(), OS["means"]
    op = torch.sigmoid(OS["opac"]).numpy().reshape(-1)
    for n, h in which.items():
        if n not in names:                                           # not in this layout
            continue
        sel = L == names.index(n); P = M[torch.from_numpy(sel)].numpy(); Po = P[op[sel] > 0.5]
        c = np.median(Po[:, :2], 0)
        for _ in range(3):
            v = Po[:, :2] - c; a = np.arctan2(v[:, 1], v[:, 0]); r = np.hypot(*v.T); b = ((a + np.pi) / (2 * np.pi) * 36).astype(int) % 36
            rim = np.array([c + np.percentile(r[b == k], 95) * np.array([np.cos((k + .5) / 36 * 2 * np.pi - np.pi), np.sin((k + .5) / 36 * 2 * np.pi - np.pi)])
                            for k in range(36) if (b == k).sum() > 5])
            sol = np.linalg.lstsq(np.c_[2 * rim, np.ones(len(rim))], (rim ** 2).sum(1), rcond=None)[0]; c = sol[:2]
        shift = np.r_[-c, h / 2 - np.percentile(Po[:, 2], 98)]
        M[torch.from_numpy(sel)] += torch.tensor(shift, dtype=M.dtype)
        print(f"recentred {n}: shift {np.round(shift * 1000, 1)} mm", flush=True)


def composite(z):
    W, H, fx, fy, cx, cy = [float(v) for v in z["K"]]
    K = (int(W), int(H), fx, fy, cx, cy)
    mj, md, lab = z["rgb"].astype(np.float32), z["depth"].astype(np.float32), z["label"]
    shade = cv2.GaussianBlur(np.clip(z["shade"].astype(np.float32), 0, 1), (5, 5), 0)
    with LOCK:
        sp, al, sd = splat_util.render(G, z["R_cv"], z["eye"], *K)
        nm, ps, qs = with_lid(json.loads(str(z["names"])), z["pos"], z["quat"])
        Go = splat_util.posed_objects(OS, ps, qs, names=nm)
        o_rgb, o_al, o_d = splat_util.render(Go, z["R_cv"], z["eye"], *K, near=0.03)
    out = sp.astype(np.float32) * (1 - SHADOW * (1 - shade))[..., None]
    out = np.where((al < 0.5)[..., None], mj, out)
    dep = np.where(al < 0.5, md, sd)                                 # depth of what the image shows, layer by layer
    vis = ~(sd < o_d - TOL)
    w = (o_al * vis)[..., None]
    out = o_rgb.astype(np.float32) * vis[..., None] + (1 - w) * out
    od = np.where(o_al > 0.5, o_d, np.inf)
    dep = np.where((o_al > 0.5) & vis, o_d, dep)
    fill = (lab == 3) & ((o_al < 0.5) | (FILL_FRONT & (md < o_d - 0.01))) & (md < sd + TOL)
    out = np.where(fill[..., None], mj, out)
    dep = np.where(fill, md, dep)
    arm = (lab == 1) & (md < od + 0.01) & (md < sd + TOL)
    soft = cv2.GaussianBlur(arm.astype(np.float32), (3, 3), 0)[..., None]
    dep = np.where(arm, md, dep)
    return (soft * mj + (1 - soft) * out).clip(0, 255).astype(np.uint8), dep.astype(np.float32)


class Hd(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def do_POST(self):
        z = np.load(io.BytesIO(self.rfile.read(int(self.headers["Content-Length"]))), allow_pickle=False)
        rgb, dep = composite(z)
        buf = io.BytesIO(); np.savez(buf, rgb=rgb, depth=dep)
        self.send_response(200); self.send_header("Content-Length", str(buf.tell())); self.end_headers()
        self.wfile.write(buf.getvalue())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8132)
    ap.add_argument("--splat", default=os.path.join(ROOT, "data/scene/splat_v6507"))
    ap.add_argument("--frame", default=os.path.join(ROOT, "data/scene/frame.json"))
    ap.add_argument("--cut", default=os.path.join(ROOT, "data/scene/robot_surface.npy"))
    ap.add_argument("--objects", default=os.path.join(ROOT, "data/scene/objsplat_6508.pt"))
    ap.add_argument("--fill-front", action="store_true", help="MuJoCo's fill surfaces also cover object Gaussians > 1 cm behind them")
    a = ap.parse_args()
    FILL_FRONT = a.fill_front
    load(a.splat, a.frame, a.cut, a.objects)
    print(f"composite ready on :{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Hd).serve_forever()
