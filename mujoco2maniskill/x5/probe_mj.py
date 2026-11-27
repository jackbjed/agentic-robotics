import sys, numpy as np, cv2
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from x5.sim_mujoco import Sim
from x5 import spec as S
s = Sim()
print("home tcp L", np.round(s.tcp("L")[0], 3), "R", np.round(s.tcp("R")[0], 3), "x axis of R tcp", np.round(s.tcp("R")[1][:, 0], 2))
for _ in range(50): s.step(s.home())
print("after 1 s: max joint drift", np.abs(s.q() - s.home()).max().round(5), "cube z", round(s.cube()[0][2], 4), "ncon", s.d.ncon)
out = S.HERE / "out" / "x5"; out.mkdir(parents=True, exist_ok=True)
cv2.imwrite(str(out / "mj_home_head.png"), s.render("head")[..., ::-1])
s.r = None
cv2.imwrite(str(out / "mj_home_side.png"), s.render("side")[..., ::-1])
for j in range(6):
    q = s.home(); q[8 + j] = 0.6; s.reset(); s.d.qpos[s.qadr] = q
    import mujoco; mujoco.mj_forward(s.m, s.d)
    print("R joint", j + 1, "= 0.6 -> tcp", np.round(s.tcp("R")[0], 3))
