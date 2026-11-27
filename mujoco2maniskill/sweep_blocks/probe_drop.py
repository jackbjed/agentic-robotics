import sys
from pathlib import Path
import numpy as np, sapien, torch
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from sweep_blocks.env import SweepBlocks
env = SweepBlocks(layout=4, cameras=())
pan_p, pan_R = env.pose("broom_shovel")
print("pan pos", pan_p.round(4), "pan x axis", pan_R[:, 0].round(2), "y axis", pan_R[:, 1].round(2), "z axis", pan_R[:, 2].round(2))
n = env.cubes[0]; a = env.U.objects[n]
print("cube comps:", [type(c).__name__ for c in a._objs[0].components], "pan shapes:", len(env.U.objects["broom_shovel"]._objs[0].find_component_by_type(sapien.physx.PhysxRigidDynamicComponent).collision_shapes))
for local in ([0.0, -0.053, 0.03], [0.0, -0.10, 0.03], [0.05, -0.03, 0.03]):
    a.set_pose(sapien.Pose(p=pan_p + pan_R @ np.array(local))); a.set_linear_velocity(torch.zeros(3)); a.set_angular_velocity(torch.zeros(3))
    zs = []
    for i in range(300):
        env._sim(env.target)
        if i % 60 == 59: zs.append(round(float((env.pose(n)[0][2] - 0.765) * 1000), 1))
    p = env.pose(n)[0]; loc = pan_R.T @ (p - env.pose("broom_shovel")[0])
    touching = set()
    for c in env.U.scene.px.get_contacts():
        names = (c.bodies[0].entity.name, c.bodies[1].entity.name)
        if any(n in x for x in names) and any(float(np.linalg.norm(pt.impulse)) > 0 for pt in c.points):
            touching.add(names[0] if n not in names[0] else names[1])
    print("dropped at pan-local", local, "-> height above table over time (mm)", zs, "| ends at pan-local", (loc * 1000).round(1), "| touching", touching)
env.close()
