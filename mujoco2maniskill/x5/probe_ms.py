import sys, numpy as np, cv2
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from x5.sim_maniskill import Sim
from x5 import spec as S
s = Sim()
print("active joints", [j.name for j in s.robot.get_active_joints()])
print("home tcp L", np.round(s.tcp("L")[0], 3), "R", np.round(s.tcp("R")[0], 3), "x axis of R tcp", np.round(s.tcp("R")[1][:, 0], 2))
for _ in range(50): s.step(s.home())
print("after 1 s: max joint drift", np.abs(s.q() - s.home()).max().round(5), "cube z", round(s.cube()[0][2], 4))
out = S.HERE / "out" / "x5"
cv2.imwrite(str(out / "ms_home_head.png"), s.render("head")[..., ::-1]); cv2.imwrite(str(out / "ms_home_side.png"), s.render("side")[..., ::-1])
q = s.home(); q[8 + 1] = 0.6
for _ in range(100): s.step(q)
print("R joint 2 = 0.6 -> tcp", np.round(s.tcp("R")[0], 3), "(MuJoCo kinematics: [0.301 -0.215 0.786])", "q", np.round(s.q()[8:14], 3))
