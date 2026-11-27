"""Dump the project's one-armed rig geometry (left Panda, cameras, gripper) from its MuJoCo model -> out/rig_dump.json.
Run in the project's .venv:  PYTHONNOUSERSITE=1 MUJOCO_GL=egl ../.venv/bin/python -I tools/dump_rig.py"""
import json, sys, math
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import mujoco
from agentic_robotics.bimanual import scenes
from agentic_robotics.bimanual.grippers import GripperSim

sc = scenes.sample(2001); sc["arm"] = "left"
S = GripperSim(seed=0, sensors="ideal", scene=sc)
m, d = S.m, S.d
A = S.arms["L"]
out = {}
def pose(bid):
    return dict(pos=d.xpos[bid].tolist(), mat=d.xmat[bid].reshape(3, 3).tolist())
out["L_link0"] = pose(m.body("L_link0").id)
out["home_q"] = A.home.tolist(); out["lo"] = A.lo.tolist(); out["hi"] = A.hi.tolist()
p, R = S.tool_pose("L"); out["home_tcp"] = dict(pos=p.tolist(), mat=R.tolist())
hand = m.body("L_hand").id
out["L_hand"] = pose(hand)
Rh = d.xmat[hand].reshape(3, 3)
out["tcp_in_hand"] = dict(pos=(Rh.T @ (p - d.xpos[hand])).tolist(), mat=(Rh.T @ R).tolist())
for c in ("d415", "L_wrist"):
    cid = m.camera(c).id; b = m.cam_bodyid[cid]
    Rn, eye = S.nominal_camera(c)
    Rb = d.xmat[b].reshape(3, 3)
    out[c] = dict(body=m.body(b).name, fovy=float(m.cam_fovy[cid]), eye=eye.tolist(), R_cv=Rn.tolist(),
                  eye_in_body=(Rb.T @ (eye - d.xpos[b])).tolist(), Rcv_in_body=(Rb.T @ Rn).tolist(),
                  eye_in_tcp=(R.T @ (eye - p)).tolist(), Rcv_in_tcp=(R.T @ Rn).tolist())
# gripper geometry: finger geoms' extent in the TCP frame, fully open and closed
def finger_extent():
    pts = {}
    for g in range(m.ngeom):
        bn = m.body(m.geom_bodyid[g]).name
        if bn in ("L_left_finger", "L_right_finger", "L_hand") and m.geom_contype[g] | m.geom_conaffinity[g]:
            c = d.geom_xpos[g]; Rg = d.geom_xmat[g].reshape(3, 3); s = m.geom_size[g]
            t = int(m.geom_type[g])
            if t == mujoco.mjtGeom.mjGEOM_BOX:
                cs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * s
            elif t == mujoco.mjtGeom.mjGEOM_MESH:
                mid = m.geom_dataid[g]; v0 = m.mesh_vertadr[mid]; nv = m.mesh_vertnum[mid]
                cs = m.mesh_vert[v0:v0 + nv]
            else:
                r = float(s.max()); cs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * r
            W = c + cs @ Rg.T
            L = (W - p) @ R
            pts.setdefault(bn, []).append(L)
    return {k: dict(min=np.vstack(v).min(0).round(4).tolist(), max=np.vstack(v).max(0).round(4).tolist(), ngeom=len(v)) for k, v in pts.items()}
mujoco.mj_forward(m, d)
out["open_width"] = S.width("L"); out["extent_open_in_tcp"] = finger_extent()
out["gripper"] = dict(ctrlrange=m.actuator_ctrlrange[A.grip].tolist(), gainprm=m.actuator_gainprm[A.grip][:3].tolist(),
                      biasprm=m.actuator_biasprm[A.grip][:3].tolist(), forcerange=m.actuator_forcerange[A.grip].tolist(),
                      gear=m.actuator_gear[A.grip][:2].tolist())
out["arm_gain"] = [m.actuator_gainprm[a][0] for a in A.arm_a]; out["arm_bias"] = [m.actuator_biasprm[a][:3].tolist() for a in A.arm_a]
out["timestep"] = m.opt.timestep
out["table_geoms"] = [dict(name=m.geom(g).name, type=int(m.geom_type[g]), size=m.geom_size[g].tolist(), pos=d.geom_xpos[g].tolist(),
                           rgba=m.geom_rgba[g].tolist(), friction=m.geom_friction[g].tolist())
                      for g in range(m.ngeom) if m.body(m.geom_bodyid[g]).name in ("world", "table") and m.geom_size[g].max() > 0.2]
out["rig"] = dict(lo=np.asarray(S.rig.lo).tolist(), hi=np.asarray(S.rig.hi).tolist())
for k in dir(S.rig):
    if not k.startswith("_"):
        v = getattr(S.rig, k)
        if isinstance(v, (int, float, list, tuple, dict, str)): out["rig"][k] = v
        elif isinstance(v, np.ndarray) and v.size < 40: out["rig"][k] = v.tolist()
out["finger_friction"] = [m.geom_friction[g].tolist() for g in range(m.ngeom) if m.body(m.geom_bodyid[g]).name == "L_left_finger" and m.geom_contype[g] | m.geom_conaffinity[g]][:3]
Path(__file__).resolve().parents[1].joinpath("assets", "rig_dump.json").write_text(json.dumps(out, indent=1, default=str))
for k, v in out.items():
    print(k, json.dumps(v, default=str)[:600])
