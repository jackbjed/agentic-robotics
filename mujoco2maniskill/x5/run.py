"""Play the test motion (out/x5/traj.npz) in one simulator; log what happened; record the side camera.

    ../.venv/bin/python -I x5/run.py mujoco            (project .venv)
    <robomme python>    -I x5/run.py maniskill         (ManiSkill environment)
-> out/x5/<sim>/log.npz, video.mp4"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from x5 import spec as S

which = sys.argv[1]
if which == "mujoco":
    from x5.sim_mujoco import Sim
else:
    from x5.sim_maniskill import Sim
z = np.load(S.HERE / "out" / "x5" / "traj.npz", allow_pickle=True)
Q, hz = z["Q"], float(z["hz"])
sim = Sim()
out = S.HERE / "out" / "x5" / which; out.mkdir(parents=True, exist_ok=True)
vw = cv2.VideoWriter(str(out / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 25, (S.SIDE_CAM["width"], S.SIDE_CAM["height"]))
log = dict(t=[], q=[], tcpL=[], tcpR=[], RL=[], RR=[], cube=[], cubeR=[])
for i, tg in enumerate(Q):
    sim.step(tg)
    pL, RL = sim.tcp("L"); pR, RR = sim.tcp("R"); pc, Rc = sim.cube()
    for k, v in zip(log, (sim.t, sim.q(), pL, pR, RL, RR, pc, Rc)):
        log[k].append(np.array(v))
    if i % 2 == 0:
        vw.write(sim.render("side")[..., ::-1])
vw.release()
np.savez(out / "log.npz", cmd=Q, **{k: np.array(v) for k, v in log.items()})
c = np.array(log["cube"])
print(which, "steps", len(Q), "| cube: start", np.round(c[0], 3), "highest z", round(float(c[:, 2].max()), 3), "end", np.round(c[-1], 3))
