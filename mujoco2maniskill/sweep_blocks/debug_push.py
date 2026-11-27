import sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from sweep_blocks.env import SweepBlocks
from sweep_blocks.program_api import TABLE_Z, X5Robot
DOWN = (0, 0, -1)
for low, high, s_low in ((17, 24, 142), (17, 21, 150), (20, 26, 142)):
    env = SweepBlocks(layout=4, cameras=()); r = X5Robot(env)
    T = lambda n: (env.pose(n)[0] - [0, 0, TABLE_Z]) * 1000
    pan_R = env.pose("broom_shovel")[1]; org = T("broom_shovel")[:2]; mouth = -pan_R[:2, 1]; m2 = mouth / np.linalg.norm(mouth); side = np.array([m2[1], -m2[0]])
    S = lambda p: float((p - org) @ m2); at = lambda s_, l_: org + s_ * m2 + l_ * side; S3 = (side[0], side[1], 0.0)
    r.set_gripper("right", 0)
    n = "cube_0"; c = T(n)[:2]; s0, l0 = S(c), float((c - org) @ side)
    p0 = at(s0 + 30 + 9.6 + 25, l0); r.move("right", (p0[0], p0[1], 60), DOWN, S3); r.move("right", (p0[0], p0[1], low), DOWN, S3)
    p1 = at(s_low + 30, l0); a = r.move("right", (p1[0], p1[1], low), DOWN, S3)
    print(f"low {low} high {high}: after the low push to {s_low}: cube along the pan {S(T(n)[:2]):.0f} z {T(n)[2]:.0f} {'' if a['done'] else a['why'][:40]}", end=" | ")
    r.move("right", (p1[0], p1[1], high), DOWN, S3)
    p3 = at(78 + 30, l0 * 0.3); b = r.move("right", (p3[0], p3[1], high), DOWN, S3)
    r.move("right", (p3[0] + 20 * m2[0], p3[1] + 20 * m2[1], 90), DOWN, S3)
    print(f"after the high push: cube along the pan {S(T(n)[:2]):.0f} lateral {float((T(n)[:2]-org)@side):.0f} z {T(n)[2]:.0f} {'' if b['done'] else b['why'][:50]} | pan moved {np.linalg.norm(T('broom_shovel')[:2]-org):.0f} mm | in pan: {env.info()['cubes_in_dustpan'][n]} | t {r.time_s():.1f}")
    env.close()
