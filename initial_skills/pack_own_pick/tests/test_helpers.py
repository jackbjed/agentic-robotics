import numpy as np

from helpers.geom import project, pix_to_plane
from helpers.grasp import candidates, footprint_block
from helpers.target import mask_circle, hue_ok


def fake_obs():
    R = np.array([[0.0, -1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, -1.0]])   # looking down
    return dict(K=(640, 480, 617.0, 617.0, 320.0, 240.0), R=R, eye=np.array([-0.3, 0.0, 0.33]),
                rgb=np.zeros((480, 640, 3), np.uint8))


def test_roundtrip():
    o = fake_obs()
    X = np.array([-0.32, 0.02, 0.021])
    uv, z = project(o, X)
    Y = pix_to_plane(o, uv[0, 0], uv[0, 1], 0.021)
    assert np.allclose(X, Y, atol=1e-6)


def test_candidates_prefer_free_side():
    o = fake_obs()
    obst = np.zeros((480, 640), bool)
    # obstacle on +x side of the roll
    c = np.array([-0.3, 0.0])
    for x in np.linspace(-0.3 + 0.03, -0.3 + 0.06, 30):
        for y in np.linspace(-0.03, 0.03, 30):
            uv, _ = project(o, [x, y, 0.008])
            obst[int(uv[0, 1]), int(uv[0, 0])] = True
    import cv2
    obst = cv2.dilate(obst.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    cs = candidates(o, obst, c, 0.023, 0.0)
    best = cs[0]
    assert best["score"] < 0.1
    if best["kind"] == "outer":
        assert abs(np.cos(best["yaw"])) < 0.6


def test_drag_candidates():
    from helpers.grasp import drag_candidates
    o = fake_obs()
    obst = np.zeros((480, 640), bool)
    obst[:, 330:] = True          # everything on one side blocked
    ds = drag_candidates(o, obst, np.array([-0.3, 0.0]), 0.023)
    assert ds and ds[0]["score"] < 0.2
    d = np.array(ds[0]["d"])
    uv0, _ = project(o, [-0.3, 0.0, 0.01])
    uv1, _ = project(o, [-0.3 + 0.04 * d[0], 0.04 * d[1], 0.01])
    assert uv1[0, 0] < uv0[0, 0]   # drags away from the blocked side


def test_mask_circle():
    m = np.zeros((480, 640), np.uint8)
    import cv2
    cv2.circle(m, (300, 200), 40, 1, -1)
    cv2.circle(m, (300, 200), 25, 0, -1)
    ci = mask_circle(m.astype(bool))
    assert abs(ci["u"] - 300) < 1 and abs(ci["v"] - 200) < 1
    assert ci["mask"][200, 300]


def test_mask_circle_occluded_by_border_and_finger():
    import cv2
    m = np.zeros((480, 640), np.uint8)
    cv2.circle(m, (400, 430), 90, 1, -1)          # cut by the bottom edge
    occl = np.zeros((480, 640), bool)
    occl[400:, 380:] = True                        # a finger hides the lower right part
    m[occl] = 0
    ci = mask_circle(m.astype(bool), occl)
    assert ci["fit"] == "circle"
    assert abs(ci["u"] - 400) < 3 and abs(ci["v"] - 430) < 3
    m2 = np.zeros((480, 640), np.uint8)            # whole circle: the ellipse as before
    cv2.circle(m2, (320, 240), 60, 1, -1)
    ci2 = mask_circle(m2.astype(bool), occl)
    assert ci2["fit"] == "ellipse" and abs(ci2["u"] - 320) < 1.5
