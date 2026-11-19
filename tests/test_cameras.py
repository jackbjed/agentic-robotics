"""Camera mounts (configs/cameras/*.yaml mounts): the world renders from the pose the program believes, plus only the
episode's calibration error; calibrated = the real rig's calib.json poses."""
import numpy as np

from agentic_robotics.evaluation.suites import episode_seed


def _believed_and_true(mounts):
    from agentic_robotics.sim import twin
    from agentic_robotics.sim.scene import ClutterPickScene
    s = ClutterPickScene(colour="mujoco", mounts=mounts)
    s.reset("val", 1, 3, episode_seed(13, 1, 3))
    P = s.P; m, d, mj = P.m, P.d, P.mj
    S = twin.RealSim(P.arm, None, twin.load_rig(), twin.camera_calib(mounts), speed=1.0, v_max=None, log_dir=None)
    q = d.qpos[P.world.qadr].copy()
    S.d.qpos[S.qadr] = q; mj.mj_forward(S.m, S.d)
    out = {}
    for cam in ("d415", "L_wrist"):
        R_b, eye_b = S.nominal_camera(cam)
        cid = m.camera(cam).id
        mj.mj_forward(m, d)
        R_t, eye_t = d.cam_xmat[cid].reshape(3, 3) @ np.diag([1.0, -1.0, -1.0]), d.cam_xpos[cid].copy()
        dpos = float(np.linalg.norm(eye_t - eye_b))
        dang = float(np.degrees(np.arccos(np.clip((np.trace(R_b.T @ R_t) - 1) / 2, -1, 1))))
        out[cam] = (dpos, dang, eye_b)
    return out, s.ep_corrupt.get("calib")


def test_calibrated_mounts_match_the_program_belief():
    import json
    from agentic_robotics.sim import twin
    out, calib = _believed_and_true("calibrated")
    c = json.loads((twin.PT_CODE / "real" / "calib.json").read_text())
    assert np.allclose(out["d415"][2], np.asarray(c["scene_cam"]["T_world_cam"])[:3, 3], atol=1e-6)
    # the world's true pose = the believed one + this episode's calibration error (scene 5 mm / 1 deg, wrist 3 mm / 1 deg)
    assert out["d415"][0] < 0.0051 and out["d415"][1] < 1.01, out["d415"]
    assert out["L_wrist"][0] < 0.0031 and out["L_wrist"][1] < 1.01, out["L_wrist"]


def test_twin_mounts_unchanged():
    out, _ = _believed_and_true("twin")
    assert np.allclose(out["d415"][2], [0.814, -0.040, 0.563], atol=0.002), out["d415"][2]
    assert out["d415"][0] < 0.0051 and out["L_wrist"][0] < 0.0031
