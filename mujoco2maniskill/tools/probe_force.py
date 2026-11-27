import json, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
import gymnasium as gym, torch
import m2m.env
sd = HERE / "out/scenes/w16"; spec = json.loads((sd / "2001.json").read_text())
env = gym.make("M2M-Scene-v1", scene_spec=spec, scene_dir=sd, obs_mode="none", control_mode="pd_joint_pos", sim_backend="cpu", render_backend="gpu")
env.reset(seed=2001); U = env.unwrapped; rob = U.agent.robot
names = [l.name for l in rob.get_links()]
q = np.r_[spec["arm"]["home_q"], 0.04]
for k in range(3):
    for i in range(20): env.step(torch.tensor(q, dtype=torch.float32)[None])
    f = rob.get_net_contact_forces(names)[0].cpu().numpy()
    print({n: np.round(v, 1).tolist() for n, v in zip(names, f) if np.abs(v).max() > 0.5})
# contacts of the physx scene
ps = U.scene.px
cs = ps.get_contacts()
print("contacts", len(cs))
for c in cs[:12]:
    a, b = c.bodies[0].entity.name, c.bodies[1].entity.name
    imp = np.sum([p.impulse for p in c.points], axis=0)
    print("  ", a, "|", b, "impulse", np.round(imp, 5).tolist(), "sep", [round(float(p.separation), 4) for p in c.points][:3])
print("timestep", ps.timestep)
env.close()
