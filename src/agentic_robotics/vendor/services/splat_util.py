"""Splat helpers (twin env: gsplat + torch): load a splat.pt into a similarity frame, render perspective / top-down views.

    G = load(splat_dir, frame)          # frame = frame.json dict (x_twin = s * R @ (x_colmap - o)) or None (COLMAP frame)
    rgb, alpha, depth = render(G, R_cv, eye, W, H, fx, fy=None, cx=None, cy=None)   # camera-to-world R_cv (OpenCV axes)
    rgb, alpha, height = topdown(G, x0, x1, y0, y1, res, cam_z)                    # orthographic, looking down -z
"""
import os

import numpy as np
import torch
from gsplat import rasterization
from scipy.spatial.transform import Rotation as Rot


def load(splat_dir, frame=None, device="cuda"):
    ck = torch.load(os.path.join(splat_dir, "splat.pt"), weights_only=False, map_location="cpu")
    p = ck["params"]
    means, quats, scales = p["means"].float(), torch.nn.functional.normalize(p["quats"].float(), dim=1), p["scales"].float()
    if frame is not None:
        s, R, o = frame["scale"], np.array(frame["R"]), np.array(frame["origin_colmap"])
        means = s * (means - torch.tensor(o, dtype=torch.float32)) @ torch.tensor(R, dtype=torch.float32).T
        Rg = Rot.from_quat(quats[:, [1, 2, 3, 0]].numpy()).as_matrix()
        quats = torch.tensor(Rot.from_matrix(R @ Rg).as_quat()[:, [3, 0, 1, 2]], dtype=torch.float32)
        scales = scales + float(np.log(s))
    G = dict(means=means, quats=quats, scales=scales, opac=p["opacities"].float(),
             col=torch.cat([p["sh0"], p["shN"]], 1).float())
    G = {k: v.to(device) for k, v in G.items()}
    G["sh"] = ck["sh_degree"]
    return G


def subset(G, keep):
    keep = torch.as_tensor(keep, device=G["means"].device)
    return {k: (v[keep] if torch.is_tensor(v) else v) for k, v in G.items()}


def _raster(G, V, K, W, H, **kw):
    with torch.no_grad():
        img, al, _ = rasterization(G["means"], G["quats"], torch.exp(G["scales"]), torch.sigmoid(G["opac"]), G["col"],
                                   torch.tensor(V, dtype=torch.float32, device=G["means"].device)[None],
                                   torch.tensor(K, dtype=torch.float32, device=G["means"].device)[None], W, H,
                                   sh_degree=G["sh"], render_mode="RGB+ED", **kw)
    img, al = img[0].cpu().numpy(), al[0, ..., 0].cpu().numpy()
    return img[..., :3], al, img[..., 3]


def render(G, R_cv, eye, W, H, fx, fy=None, cx=None, cy=None, bg=0.0, near=0.15):
    """Perspective render. R_cv: camera-to-world rotation with OpenCV axes (x right, y down, z forward). Gaussians
    closer than `near` (m) are dropped: away from the scanned path a floater can sit right in front of the camera."""
    R_cv, eye = np.asarray(R_cv, float), np.asarray(eye, float)
    V = np.eye(4); V[:3, :3] = R_cv.T; V[:3, 3] = -R_cv.T @ eye
    if near:
        Vt = torch.tensor(V, dtype=torch.float32, device=G["means"].device)
        G = subset(G, G["means"] @ Vt[2, :3] + Vt[2, 3] > near)
    K = np.array([[fx, 0, W / 2 if cx is None else cx], [0, fx if fy is None else fy, H / 2 if cy is None else cy], [0, 0, 1]])
    rgb, al, d = _raster(G, V, K, W, H, rasterize_mode="antialiased")
    rgb = np.clip(rgb + bg * (1 - al[..., None]), 0, 1)
    return (rgb * 255).astype(np.uint8), al, np.where(al > 0.5, d, np.inf)


def topdown(G, x0, x1, y0, y1, res, cam_z=3.0, bg=1.0):
    """Orthographic view straight down: pixel (i, j) = twin point (x0 + (j+.5) res, y1 - (i+.5) res)."""
    W, H = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    R = np.array([[1.0, 0, 0], [0, -1, 0], [0, 0, -1]]); eye = np.array([(x0 + x1) / 2, (y0 + y1) / 2, cam_z])
    V = np.eye(4); V[:3, :3] = R; V[:3, 3] = -R @ eye
    K = np.array([[1 / res, 0, W / 2], [0, 1 / res, H / 2], [0, 0, 1]])
    rgb, al, d = _raster(G, V, K, W, H, camera_model="ortho")
    rgb = np.clip(rgb + bg * (1 - al[..., None]), 0, 1)
    return (rgb * 255).astype(np.uint8), al, np.where(al > 0.5, cam_z - d, np.nan)


def quat_to_mat(q):
    """wxyz quaternions [N,4] (torch) -> rotation matrices [N,3,3]."""
    q = torch.nn.functional.normalize(q, dim=-1)
    w, x, y, z = q.unbind(-1)
    return torch.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
                        2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
                        2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1).reshape(q.shape[:-1] + (3, 3))


def quat_mul(a, b):
    """Hamilton product of wxyz quaternions (torch, broadcast)."""
    aw, ax, ay, az = a.unbind(-1); bw, bx, by, bz = b.unbind(-1)
    return torch.stack([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                        aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw], -1)


def quat_conj(q):
    return q * torch.tensor([1.0, -1, -1, -1], dtype=q.dtype, device=q.device)


def posed_objects(OS, pos, quat, names=None, device="cuda"):
    """The object Gaussians of object_splats.py at body poses pos [B,3], quat [B,4] (wxyz, same body order as OS["names"]
    unless `names` gives the order of pos/quat). View-dependent colour (SH) is not rotated with the object."""
    if names is not None:
        order = [list(names).index(n) for n in OS["names"]]
        pos, quat = np.asarray(pos)[order], np.asarray(quat)[order]
    L = OS["label"].to(device)
    p = torch.as_tensor(np.asarray(pos), dtype=torch.float32, device=device)
    q = torch.as_tensor(np.asarray(quat), dtype=torch.float32, device=device)
    R = quat_to_mat(q)
    means = torch.einsum("nij,nj->ni", R[L], OS["means"].to(device)) + p[L]
    quats = torch.nn.functional.normalize(quat_mul(q[L], OS["quats"].to(device)), dim=-1)
    return dict(means=means, quats=quats, scales=OS["scales"].to(device), opac=OS["opac"].to(device),
                col=OS["col"].to(device), sh=OS["sh"])
