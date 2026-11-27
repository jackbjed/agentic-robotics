"""A first look at the ManiSkill scene: build it, read the robot, render both cameras. (robomme env)"""
import json, sys, time
from pathlib import Path
import numpy as np, cv2
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
import gymnasium as gym, torch
import m2m.env
sd = HERE / "out/scenes/w16"; spec = json.loads((sd / "2001.json").read_text())
t = time.time()
env = gym.make("M2M-Scene-v1", scene_spec=spec, scene_dir=sd, obs_mode="none", control_mode="pd_joint_pos", sim_backend="cpu", render_backend="gpu", render_mode="rgb_array")
env.reset(seed=2001); U = env.unwrapped
print("built s", round(time.time() - t, 1), "unbuilt", U.unbuilt, "action", env.action_space.shape)
rob = U.agent.robot
print("links", [l.name for l in rob.get_links()])
print("active joints", [j.name for j in rob.get_active_joints()])
print("qpos", rob.get_qpos().cpu().numpy().round(4))
print("tcp", U.agent.tcp.pose.p.cpu().numpy().round(4), "expected", np.round(json.load(open(HERE / "assets/rig_dump.json"))["home_tcp"]["pos"], 4))
print("hand", U.agent.hand.pose.p.cpu().numpy().round(4))
q = np.r_[spec["arm"]["home_q"], 0.04]
t = time.time()
for i in range(100):
    env.step(torch.tensor(q, dtype=torch.float32)[None])
print("100 steps s", round(time.time() - t, 3), "qpos", rob.get_qpos().cpu().numpy().round(4))
print("tcp after 1 s", U.agent.tcp.pose.p.cpu().numpy().round(4))
for n, a in U.bodies.items():
    print(n, a.pose.p.cpu().numpy().round(4)[0], "start", np.round(spec["bodies"][[b["name"] for b in spec["bodies"]].index(n)]["pos"], 4))
print("sensors", list(U._sensors))
U.scene.update_render(update_sensors=True, update_human_render_cameras=False)
out = HERE / "out/probe"; out.mkdir(parents=True, exist_ok=True)
for n, s in U._sensors.items():
    s.capture()
    cam = s.camera
    print(n, type(cam).__name__, [x for x in dir(cam) if "picture" in x or "pose" in x or "intrinsic" in x][:12])
    col = cam.get_picture("Color"); pos = cam.get_picture("Position")
    print("  types", type(col).__name__, type(col[0]).__name__ if isinstance(col, (list, tuple)) else "")
    def npy(x):
        x = x[0] if isinstance(x, (list, tuple)) else x
        x = x.cpu().numpy() if hasattr(x, "cpu") else np.asarray(x)
        return x[0] if x.ndim == 4 else x
    col, pos = npy(col), npy(pos)
    print("  color", col.shape, col.dtype, float(col.max()), "position", pos.shape, pos.dtype, "depth range", float(-pos[..., 2].max()), float(-pos[..., 2].min()))
    rgb = (np.clip(col[..., :3], 0, 1) * 255).astype(np.uint8) if col.dtype != np.uint8 else col[..., :3]
    cv2.imwrite(str(out / f"{n}.png"), rgb[..., ::-1])
    print("  K", np.round(np.asarray(cam.get_intrinsic_matrix().cpu())[0], 1).tolist())
fr = env.render(); fr = fr[0].cpu().numpy() if hasattr(fr, "cpu") else np.asarray(fr)[0]
cv2.imwrite(str(out / "record.png"), fr[..., ::-1])
f = rob.get_net_contact_forces(list(m2m.env.ROBOT_LINKS_FELT)); print("net contact forces", tuple(f.shape), f.abs().max().item())
env.close()
