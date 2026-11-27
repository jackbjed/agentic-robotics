"""Does the success check behave as RoboDojo's would? The objects are PLACED by hand (no robot skill): the dustpan
more than 10 cm left of the brush, the cubes dropped into it; then each condition is broken in turn.

    <robomme python> -I sweep_blocks/test_check.py [layout]"""
import sys
from pathlib import Path

import cv2
import numpy as np
import sapien
import torch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from sweep_blocks.env import SweepBlocks  # noqa: E402

k = int(sys.argv[1]) if len(sys.argv) > 1 else 4
env = SweepBlocks(layout=k, cameras=("overview",))
hold = np.r_[np.zeros(6), 1.0, np.zeros(6), 1.0]


def put(name, p, q=None):
    a = env.U.objects[name]
    a.set_pose(sapien.Pose(p=p, q=q if q is not None else a.pose.q[0].cpu().numpy()))
    a.set_linear_velocity(torch.zeros(3)); a.set_angular_velocity(torch.zeros(3))


def show(tag):
    info = env.info()
    print(f"{tag:46s} success {info['success']!s:5s} left {info['dustpan_left_of_broom']!s:5s} not lifted {info['dustpan_not_lifted']!s:5s} "
          f"level {info['dustpan_level']!s:5s} cubes {sum(info['cubes_in_dustpan'].values())}/{len(info['cubes_in_dustpan'])} arms {info['arms_back']}")
    return info


show("start")
pan_p, pan_R = env.pose("broom_shovel")
put("broom", [pan_p[0] + 0.25, 0.10, env.pose("broom")[0][2]])
c = pan_p + pan_R @ np.array([0.0, -0.053, 0.03])                    # above the dustpan's support circle
for i, n in enumerate(env.cubes):
    put(n, c + pan_R @ np.array([0.03 * (i - (len(env.cubes) - 1) / 2), 0.0, 0.0]))
for _ in range(400):                                           # (physics only: an env.step would stop at the first success)
    env._sim(env.target)
info = show("brush 25 cm to the right, cubes dropped in the pan")
print("   cubes rest at (mm above the table):", [round(float((env.pose(n)[0][2] - 0.765) * 1000), 1) for n in env.cubes], "| the pan moved (mm):",
      round(float(np.linalg.norm(env.pose("broom_shovel")[0] - pan_p) * 1000), 1))
out = HERE / "out" / "robodojo_sweep"; cv2.imwrite(str(out / f"check_{k}.jpg"), env.image("overview")[..., ::-1])
p0 = env.pose(env.cubes[0])[0].copy()
put(env.cubes[0], [0.30, 0.20, 0.80])
for _ in range(10):
    env._sim(env.target)
show("one cube taken out")
put(env.cubes[0], p0 + [0, 0, 0.01])
for _ in range(60):
    env._sim(env.target)
show("put back")
bp = env.pose("broom")[0]
put("broom", [env.pose("broom_shovel")[0][0] + 0.05, bp[1], bp[2]])
for _ in range(10):
    env._sim(env.target)
show("brush only 5 cm to the right")
put("broom", bp)
for _ in range(10):
    env._sim(env.target)
away = hold.copy(); away[1] = 1.2; away[2] = 1.0
env.done = False
for _ in range(40):
    env.step(away)
env.done = False
show("left arm raised (not back at its start)")
for _ in range(60):
    env.step(hold)
show("left arm back")
env.close()
