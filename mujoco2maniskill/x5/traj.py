"""One test motion for both simulators, as joint targets at 50 Hz (16 joints: spec.joint_names()):
  the LEFT arm moves freely (lifts, swings, turns its wrist, closes and opens its gripper);
  the RIGHT arm picks the cube from the table from above, carries it 15 cm to the side, puts it down, lets go.
The right arm's poses come from inverse kinematics on the MuJoCo model; both simulators then get the SAME joint targets.

    ../.venv/bin/python -I x5/traj.py            -> out/x5/traj.npz"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from x5 import spec as S

HZ = 1.0 / (S.DT * S.CTRL_EVERY)


def _ease(n):
    s = np.linspace(0, 1, n + 1)[1:]
    return 10 * s ** 3 - 15 * s ** 4 + 6 * s ** 5


def build():
    from x5.sim_mujoco import Sim
    sim = Sim()
    home = sim.home()
    c = np.array(S.CUBE["pos"])
    R = np.column_stack([[0, 0, -1.0], [1.0, 0, 0], [0, -1.0, 0]])     # the hand points down, the fingers close along x
    rng = np.random.default_rng(0)
    last = [np.array([0.0, 1.2, 1.2, -0.8, 0.0, 0.0])]

    def ik(p):
        best = None
        for seed in [last[0]] + [np.array([0.0, 1.2, 1.2, -0.8, 0.0, 0.0])] + [rng.uniform(-1.5, 1.5, 6) for _ in range(40)]:
            q, ep, er = sim.ik("R", np.asarray(p, float), R, seed)
            q = (q + np.pi) % (2 * np.pi) - np.pi
            if ep < 1e-3 and er < 1e-2 and (best is None or np.abs(q - last[0]).max() < np.abs(best - last[0]).max()):
                best = q
                if np.abs(q - last[0]).max() < 0.6:
                    break
        if best is None:
            raise RuntimeError(f"no joint solution for {p}")
        last[0] = best
        return best
    # the fingertips are 12.6 mm past the tool point: they stop 3 mm over the table
    grasp_z = S.TABLE_Z + 0.0126 + 0.003
    place = c + np.array([-0.15, 0.03, 0.0])
    R_keys = [("above", ik([c[0], c[1], grasp_z + 0.10]), S.GRIP_OPEN, 2.5),
              ("down", ik([c[0], c[1], grasp_z]), S.GRIP_OPEN, 1.5),
              ("close", ik([c[0], c[1], grasp_z]), 0.0, 1.0),
              ("lift", ik([c[0], c[1], grasp_z + 0.15]), 0.0, 1.5),
              ("carry", ik([place[0], place[1], grasp_z + 0.15]), 0.0, 2.0),
              ("lower", ik([place[0], place[1], grasp_z + 0.002]), 0.0, 1.5),
              ("open", ik([place[0], place[1], grasp_z + 0.002]), S.GRIP_OPEN, 1.0),
              ("up", ik([place[0], place[1], grasp_z + 0.12]), S.GRIP_OPEN, 1.5),
              ("home", np.zeros(6), S.GRIP_OPEN, 2.5)]
    L_keys = [(np.array([0.0, 1.0, 0.9, 0.0, 0.0, 0.0]), S.GRIP_OPEN, 2.5),
              (np.array([0.6, 1.2, 0.7, 0.4, 0.5, 1.0]), S.GRIP_OPEN, 2.5),
              (np.array([0.6, 1.2, 0.7, 0.4, 0.5, 1.0]), 0.005, 1.0),
              (np.array([-0.5, 0.8, 1.2, -0.5, -0.6, -1.2]), 0.005, 3.0),
              (np.array([-0.5, 0.8, 1.2, -0.5, -0.6, -1.2]), S.GRIP_OPEN, 1.0),
              (np.array([0.0, 1.4, 1.4, 0.8, 0.0, 0.0]), S.GRIP_OPEN, 2.5),
              (np.zeros(6), S.GRIP_OPEN, 2.5)]

    def play(keys, q0, g0):
        out, q, g = [], q0.copy(), g0
        for k in keys:
            q1, g1, T = (k[1], k[2], k[3]) if isinstance(k[0], str) else k
            for s in _ease(int(round(T * HZ))):
                out.append(np.r_[q + (q1 - q) * s, g + (g1 - g) * s, g + (g1 - g) * s])
            q, g = np.asarray(q1, float), g1
        return np.array(out)
    A = play(L_keys, np.zeros(6), S.GRIP_OPEN); B = play(R_keys, np.zeros(6), S.GRIP_OPEN)
    n = max(len(A), len(B)) + int(HZ)
    pad = lambda X: np.vstack([X, np.repeat(X[-1:], n - len(X), 0)])
    Q = np.hstack([pad(A), pad(B)])
    marks = {}
    t = 0.0
    for k in R_keys:
        t += k[3]; marks[k[0]] = t
    return Q, marks, home


if __name__ == "__main__":
    Q, marks, home = build()
    out = S.HERE / "out" / "x5"; out.mkdir(parents=True, exist_ok=True)
    np.savez(out / "traj.npz", Q=Q, hz=HZ, marks=np.array(list(marks.items()), dtype=object), names=np.array(S.joint_names()))
    print("steps", len(Q), "seconds", len(Q) / HZ, "marks", {k: round(v, 1) for k, v in marks.items()})
