import sys, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from x5 import spec as S
which = sys.argv[1]
z = np.load(S.HERE / "out" / "x5" / "traj.npz", allow_pickle=True); Q = z["Q"]
if which == "mujoco":
    from x5.sim_mujoco import Sim
    import mujoco
    s = Sim()
    for i in range(200): s.step(Q[i])
    m, d = s.m, s.d
    for i in range(d.ncon):
        c = d.contact[i]; f = np.zeros(6); mujoco.mj_contactForce(m, d, i, f)
        print("  ", m.body(m.geom_bodyid[c.geom1]).name, "|", m.body(m.geom_bodyid[c.geom2]).name, "dist", round(c.dist, 4), "normal force", round(f[0], 1))
    for n in ("R_link2", "R_link3", "R_link4", "R_link5", "R_link6", "R_link7"):
        print("  ", n, "z", round(float(d.xpos[m.body(n).id][2]), 3))
else:
    from x5.sim_maniskill import Sim
    s = Sim()
    for i in range(200): s.step(Q[i])
    for c in s.U.scene.px.get_contacts():
        imp = np.sum([p.impulse for p in c.points], axis=0)
        if np.linalg.norm(imp) > 1e-6:
            print("  ", c.bodies[0].entity.name, "|", c.bodies[1].entity.name, "force", np.round(imp / S.DT, 1), "sep", round(min(float(p.separation) for p in c.points), 4))
    for l in s.robot.get_links():
        if l.name in ("R_link2", "R_link3", "R_link4", "R_link5", "R_link6", "R_link7"):
            print("  ", l.name, "z", round(float(l.pose.p[0, 2]), 3))
print(which, "q-cmd R arm", np.round(s.q()[8:14] - Q[199][8:14], 3), "tcp", np.round(s.tcp("R")[0], 3))
