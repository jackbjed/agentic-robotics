"""Camera geometry helpers (OpenCV pinhole; R columns = camera axes in world)."""
import numpy as np


def pix_ray(obs, u, v):
    W, H, fx, fy, cx, cy = obs["K"]
    d = np.array([(u - cx) / fx, (v - cy) / fy, 1.0])
    R = np.asarray(obs["R"], float)
    w = R @ d
    return np.asarray(obs["eye"], float), w / np.linalg.norm(w)


def pix_to_plane(obs, u, v, z):
    e, w = pix_ray(obs, u, v)
    if abs(w[2]) < 1e-6:
        return None
    s = (z - e[2]) / w[2]
    return e + s * w


def project(obs, X):
    X = np.atleast_2d(np.asarray(X, float))
    W, H, fx, fy, cx, cy = obs["K"]
    R = np.asarray(obs["R"], float)
    e = np.asarray(obs["eye"], float)
    c = (X - e) @ R
    uv = np.c_[fx * c[:, 0] / c[:, 2] + cx, fy * c[:, 1] / c[:, 2] + cy]
    return uv, c[:, 2]


def yaw_of(R):
    """Closing-axis yaw of a down-pointing hand (closing axis = R[:,1])."""
    a = np.asarray(R)[:, 1]
    return float(np.arctan2(a[1], a[0]))
