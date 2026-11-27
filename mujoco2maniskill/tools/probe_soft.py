"""Can PhysX (through SAPIEN) give a soft contact? A box pressed on the ground with 10 N: how far does it sink, for
a normal material and for a negative restitution (PhysX's compliant contact: stiffness = -restitution)."""
import numpy as np, sapien, sapien.physx as px
for rest in (0.0, -2000.0, -5000.0, -20000.0):
    scene = sapien.Scene(); scene.set_timestep(0.002)
    mat = px.PhysxMaterial(1.0, 1.0, rest)
    g = scene.create_actor_builder(); g.add_box_collision(half_size=[1, 1, 0.05], material=px.PhysxMaterial(1.0, 1.0, 0.0)); g.initial_pose = sapien.Pose([0, 0, -0.05]); g.build_static("ground")
    b = scene.create_actor_builder(); b.add_box_collision(half_size=[0.05, 0.03, 0.02], material=mat, density=50); b.initial_pose = sapien.Pose([0, 0, 0.0201]); box = b.build("box")
    c = box.find_component_by_type(px.PhysxRigidDynamicComponent)
    zs = []
    for i in range(1500):
        if i >= 500:
            c.add_force_at_point([0, 0, -10.0], box.pose.p)
        scene.step()
        zs.append(box.pose.p[2])
    zs = np.array(zs)
    print(f"restitution {rest:9.0f}: rest height {1000 * (zs[450] - 0.02):6.2f} mm, under 10 N {1000 * (zs[-1] - 0.02):6.2f} mm, wobble at the end {1000 * np.ptp(zs[-200:]):.3f} mm")
