import sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from sweep_blocks.env import SweepBlocks
from sweep_blocks.program_api import TABLE_Z, X5Robot
env = SweepBlocks(layout=4, cameras=()); r = X5Robot(env)
T = lambda n: (env.pose(n)[0] - [0, 0, TABLE_Z]) * 1000
DOWN = (0, 0, -1)
b_R = env.pose("broom")[1]; b = T("broom"); axis = b_R[:, 2]; handle = b - 60.0 * axis
cl = np.cross(axis, [0, 0, 1.0]); cl /= np.linalg.norm(cl)
def show(tag, res=None):
    R = env.pose("broom")[1]
    print(f"{tag:26s} t {r.time_s():5.1f} | brush {T('broom').round(0)} axis z-comp {R[2,2]:+.2f} | pan {T('broom_shovel').round(0)} | left tool {np.round(r.state('left')['tcp_mm'],0)} open {r.state('left')['opening_mm']}", "" if res is None or res['done'] else res['why'][:60], "curved" if res and res.get('curved') else "")
show("start")
show("above", r.move("left", (handle[0], handle[1], 90), DOWN, cl)); show("down", r.move("left", (handle[0], handle[1], 22), DOWN, cl))
r.set_gripper("left", 0); show("closed")
show("lift 110", r.move("left", (handle[0], handle[1], 110), DOWN, cl))
show("lift 200", r.move("left", (handle[0], handle[1], 200), DOWN, cl))
c_new = np.array([0.0, 1.0]) if cl[1] >= 0 else np.array([0.0, -1.0])
phi = np.arctan2(cl[0] * c_new[1] - cl[1] * c_new[0], cl[0] * c_new[0] + cl[1] * c_new[1])
show("turn in place", r.turn("left", (handle[0], handle[1], 200), (0, 0, 1), float(np.degrees(phi))))
for frac in (0.25, 0.5, 0.75, 1.0):
    g = np.array([handle[0], handle[1]]) + frac * (np.array([100.0, -335.0]) - np.array([handle[0], handle[1]]))
    show(f"carry {frac}", r.move("left", (g[0], g[1], 200), DOWN, (c_new[0], c_new[1], 0)))
show("lower to 40", r.move("left", (100, -335, 40), DOWN, (c_new[0], c_new[1], 0)))
r.set_gripper("left", 88); show("released")
show("up", r.move("left", (100, -335, 150), DOWN, (c_new[0], c_new[1], 0)))
env.close()
