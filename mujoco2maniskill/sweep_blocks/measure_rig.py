"""Numbers for the interface text: each arm's reach near the table for two hand orientations, and where the cameras are."""
import sys, math
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from sweep_blocks.program_api import ArmKin, tool_R, X5Robot, TABLE_Z
rng = np.random.default_rng(0)
for arm, side in (("left", "L"), ("right", "R")):
    k = ArmKin(side)
    for label, R in (("fingers down", tool_R((0, 0, -1), (1, 0, 0))), ("fingers forward and 45 deg down", tool_R((0, 0.707, -0.707), (1, 0, 0))), ("fingers forward, level", tool_R((0, 1, 0), (1, 0, 0)))):
        ok = []
        for x in np.arange(-0.70, 0.71, 0.1):
            for y in np.arange(-0.40, 0.51, 0.1):
                good = False
                for seed in [np.zeros(6), np.array([0, 1.2, 1.2, -0.8, 0, 0])] + [rng.uniform(-1.6, 1.6, 6) for _ in range(2)]:
                    q, ep, er = k.ik(np.array([x, y, 0.03]), R, seed, iters=80)
                    if ep < 0.002 and er < 0.02:
                        good = True; break
                if good: ok.append((x, y))
        ok = np.array(ok)
        d = np.linalg.norm(ok - (np.array(k.T0[:2, 3])), axis=1) if len(ok) else np.array([0])
        print(f"{arm:5s} {label:32s}: x {ok[:,0].min()*1000:5.0f}..{ok[:,0].max()*1000:5.0f}  y {ok[:,1].min()*1000:5.0f}..{ok[:,1].max()*1000:5.0f}  farthest from its base {d.max()*1000:4.0f} mm  ({len(ok)} cells)")
from sweep_blocks.env import SweepBlocks
env = SweepBlocks(layout=4, cameras=()); r = X5Robot(env)
fr = r.look()
for c, f in fr.items():
    print(c, "eye", np.round(f.eye, 0), "looks along", np.round(f.R[:, 2], 2), "image up is", np.round(-f.R[:, 1], 2), "image right is", np.round(f.R[:, 0], 2), "fx fy", round(f.fx, 1), round(f.fy, 1))
for a in ("left", "right"):
    s = r.state(a); f = fr[a + "_wrist"]; p = np.array(s["tcp_mm"]); R = env.endpose("L" if a == "left" else "R")[1]
    rel = R.T @ (f.eye - p) 
    print(a, "tcp", s["tcp_mm"], "wrist camera relative to the TCP: along approach", round(rel[0], 0), "along closing", round(rel[1], 0), "along the third axis", round(rel[2], 0), "| angle between its view and approach", round(math.degrees(math.acos(float(f.R[:, 2] @ R[:, 0]))), 0))
    (uv,), (z,) = f.project(p[None]); print("   the TCP shows in its image at", np.round(uv, 0), "depth", round(float(z), 0))
env.close()
