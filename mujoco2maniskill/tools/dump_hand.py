"""Dump the left arm's hand and fingers (bodies, joints, collision and visual geoms, in their body frames) -> out/hand_dump.json.
Run in the project's .venv."""
import json, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import mujoco
from agentic_robotics.bimanual import scenes
from agentic_robotics.bimanual.grippers import GripperSim
sc = scenes.sample(2001); sc["arm"] = "left"
S = GripperSim(seed=0, sensors="ideal", scene=sc); m, d = S.m, S.d
out = {"bodies": {}}
names = ["L_link7", "L_hand", "L_left_finger", "L_right_finger"]
extra = [m.body(b).name for b in range(m.nbody) if m.body(m.body_parentid[b]).name in names and m.body(b).name not in names]
for n in names + extra:
    b = m.body(n).id
    B = dict(parent=m.body(m.body_parentid[b]).name, pos=m.body_pos[b].tolist(), quat=m.body_quat[b].tolist(),
             mass=float(m.body_mass[b]), ipos=m.body_ipos[b].tolist(), inertia=m.body_inertia[b].tolist(), iquat=m.body_iquat[b].tolist(), joints=[], geoms=[])
    for j in range(m.njnt):
        if m.jnt_bodyid[j] == b:
            B["joints"].append(dict(name=m.joint(j).name, type=int(m.jnt_type[j]), axis=m.jnt_axis[j].tolist(), range=m.jnt_range[j].tolist(),
                                    damping=float(m.dof_damping[m.jnt_dofadr[j]]), armature=float(m.dof_armature[m.jnt_dofadr[j]]),
                                    frictionloss=float(m.dof_frictionloss[m.jnt_dofadr[j]])))
    for g in range(m.ngeom):
        if m.geom_bodyid[g] == b:
            G = dict(name=m.geom(g).name, type=int(m.geom_type[g]), size=m.geom_size[g].tolist(), pos=m.geom_pos[g].tolist(), quat=m.geom_quat[g].tolist(),
                     rgba=m.geom_rgba[g].tolist(), col=bool(m.geom_contype[g] | m.geom_conaffinity[g]), friction=m.geom_friction[g].tolist(),
                     group=int(m.geom_group[g]), solref=m.geom_solref[g].tolist(), solimp=m.geom_solimp[g].tolist(), margin=float(m.geom_margin[g]))
            if G["type"] == int(mujoco.mjtGeom.mjGEOM_MESH):
                mid = m.geom_dataid[g]; v0, nv = m.mesh_vertadr[mid], m.mesh_vertnum[mid]; f0, nf = m.mesh_faceadr[mid], m.mesh_facenum[mid]
                G["mesh"] = m.mesh(mid).name; G["nvert"] = int(nv)
                np.savez(Path(__file__).resolve().parents[1] / "assets" / f"mesh_{m.mesh(mid).name}.npz", v=m.mesh_vert[v0:v0 + nv], f=m.mesh_face[f0:f0 + nf])
            B["geoms"].append(G)
    out["bodies"][n] = B
out["sites"] = {m.site(s).name: dict(body=m.body(m.site_bodyid[s]).name, pos=m.site_pos[s].tolist(), quat=m.site_quat[s].tolist()) for s in range(m.nsite) if m.site(s).name.startswith("L_")}
out["equality"] = [dict(type=int(m.eq_type[e]), o1=int(m.eq_obj1id[e]), o2=int(m.eq_obj2id[e]), data=m.eq_data[e][:6].tolist()) for e in range(m.neq)]
out["tendon"] = int(m.ntendon)
out["link_chain"] = {m.body(b).name: dict(parent=m.body(m.body_parentid[b]).name, pos=m.body_pos[b].tolist(), quat=m.body_quat[b].tolist(), mass=float(m.body_mass[b]))
                     for b in range(m.nbody) if m.body(b).name.startswith("L_link")}
out["link_joints"] = {m.joint(j).name: dict(axis=m.jnt_axis[j].tolist(), range=m.jnt_range[j].tolist(), damping=float(m.dof_damping[m.jnt_dofadr[j]]),
                                             armature=float(m.dof_armature[m.jnt_dofadr[j]]), frictionloss=float(m.dof_frictionloss[m.jnt_dofadr[j]]))
                      for j in range(m.njnt) if m.joint(j).name.startswith("L_joint")}
out["gripper_actuator"] = dict(trn=m.actuator_trnid[S.arms["L"].grip].tolist(), trntype=int(m.actuator_trntype[S.arms["L"].grip]))
Path(__file__).resolve().parents[1].joinpath("assets", "hand_dump.json").write_text(json.dumps(out, indent=1))
for n, B in out["bodies"].items():
    print(n, "parent", B["parent"], "pos", np.round(B["pos"], 4).tolist(), "quat", np.round(B["quat"], 4).tolist(), "mass", round(B["mass"], 3), "joints", [(j["name"], j["type"], j["axis"], j["range"], j["damping"]) for j in B["joints"]])
    for G in B["geoms"]:
        print("   ", G["name"], "type", G["type"], "size", np.round(G["size"], 4).tolist(), "pos", np.round(G["pos"], 4).tolist(), "quat", np.round(G["quat"], 3).tolist(), "col", G["col"], "grp", G["group"], G.get("mesh", ""), G.get("nvert", ""), "fr", G["friction"][0])
print("sites", out["sites"]); print("eq", out["equality"], "tendons", out["tendon"], out["gripper_actuator"])
print({k: (np.round(v["pos"], 4).tolist(), np.round(v["quat"], 4).tolist(), round(v["mass"], 2)) for k, v in out["link_chain"].items()})
print(out["link_joints"])
