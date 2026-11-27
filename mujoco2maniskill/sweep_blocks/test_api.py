"""Is the program interface right? (1) The cameras: each cube's TRUE place, projected into every camera and read back
from its depth. (2) The arm: go above a cube's TRUE place, come down until contact, close, lift, carry, let go."""
import sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from sweep_blocks.env import SweepBlocks
from sweep_blocks.program_api import TABLE_Z, X5Robot
env = SweepBlocks(layout=4, cameras=())
r = X5Robot(env)
true = {n: (env.pose(n)[0] - [0, 0, TABLE_Z]) * 1000 for n in env.U.objects}
fr = r.look()
for c, f in fr.items():
    for n in env.cubes:
        top = true[n] + [0, 0, 9.6]
        (uv,), (z,) = f.project(top[None])
        if 0 <= uv[0] < 640 and 0 <= uv[1] < 480 and z > 0:
            X = f.locate(uv[0], uv[1])
            print(f"{c:11s} {n}: pixel ({uv[0]:5.1f}, {uv[1]:5.1f})  read back {None if X is None else np.round(X, 1)}  true top {np.round(top, 1)}  off {None if X is None else round(float(np.linalg.norm(X - top)), 1)} mm")
        else:
            print(f"{c:11s} {n}: not in view")
n = env.cubes[1]; c = true[n]
down, along = (0, 0, -1), (1, 0, 0)
print("start", r.state("right"))
print("above ", r.move("right", (c[0], c[1], 120), down, along), r.state("right")["tcp_mm"], "t", r.time_s())
print("down  ", r.move("right", (c[0], c[1], 5), down, along, True), r.state("right")["tcp_mm"], "force", r.state("right")["force_n"])
p = r.state("right")["tcp_mm"]
print("up 3  ", r.move("right", (p[0], p[1], p[2] + 3), down, along)["done"])
print("close ", r.set_gripper("right", 0))
print("lift  ", r.move("right", (c[0], c[1], 150), down, along)["done"], "cube z", round(float((env.pose(n)[0][2] - TABLE_Z) * 1000), 1), "opening", r.state("right")["opening_mm"])
print("carry ", r.move("right", (c[0] - 100, c[1] + 60, 150), down, along)["done"], "cube", np.round((env.pose(n)[0] - [0, 0, TABLE_Z]) * 1000, 1))
print("turn  ", r.turn("right", r.state("right")["tcp_mm"], (0, 0, 1), 45), r.state("right")["closing_dir"])
print("open  ", r.set_gripper("right", 88), "cube", np.round((env.pose(n)[0] - [0, 0, TABLE_Z]) * 1000, 1))
print("left  ", r.move("left", (-200, -50, 200), (0, 0.5, -0.87), (1, 0, 0)), r.state("left")["tcp_mm"])
print("home  ", r.home("right")["done"], r.home("left")["done"], "t", r.time_s(), "info", {k: v for k, v in env.info().items() if k in ("arms_back", "success")})
env.close()
