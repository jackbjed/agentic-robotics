"""A session's world (a file with scene(), goal(), robot(), success()) -> one scene file per seed that the ManiSkill
side can build (m2m/env.py) WITHOUT MuJoCo: every body the world put on the table (its shapes, mass, inertia, friction,
colour, pose at the episode's start), the table, the cameras' true and nominal poses, the task's text.

It builds the scene exactly as a practice episode does (practice._world, tasks.Episode: the checkout given by --src,
read only) and reads the compiled MuJoCo model. Run in the PROJECT's environment:

    PYTHONNOUSERSITE=1 MUJOCO_GL=egl ../.venv/bin/python -I tools/export_scene.py \
        --world ../runs/bimanual/insert_own_cc_xhigh_1125/worlds/w16.py --seeds 2001-2016 --out out/scenes/w16
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
SRC_DEFAULT = HERE.parents[1] / "agentic-robotics-ownreal" / "src"     # (the checkout with the worlds' code; read only)


def seeds_of(s):
    out = []
    for part in str(s).split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def export(world, seed, src, sensors="d415"):
    import mujoco
    from agentic_robotics.bimanual import practice, scenes
    from agentic_robotics.bimanual.tasks import Episode
    log = io.StringIO()
    base = scenes.empty(seed)
    shown_in = json.loads(json.dumps(practice.scene_shown(base)))
    wns, scene, why = practice._world(str(world), base, seed, log)
    if why:
        raise RuntimeError(f"world: {why}\n{log.getvalue()[-2000:]}")
    ep = Episode("sim_bimanual_grippers", "insert_varied", seed=seed, sensors=sensors, scene=scene)
    S, m, d = ep.S, ep.S.m, ep.S.d
    text = ep.text
    if wns is not None and "goal" in wns:
        text = wns["goal"](json.loads(json.dumps(practice.scene_shown(ep.scene))), np.random.default_rng([int(seed), 79]))
    robot_bodies = {b for b in range(m.nbody) if m.body(b).name.startswith(("L_", "R_"))}
    GT = {int(mujoco.mjtGeom.mjGEOM_BOX): "box", int(mujoco.mjtGeom.mjGEOM_SPHERE): "sphere",
          int(mujoco.mjtGeom.mjGEOM_CYLINDER): "cylinder", int(mujoco.mjtGeom.mjGEOM_CAPSULE): "capsule",
          int(mujoco.mjtGeom.mjGEOM_ELLIPSOID): "ellipsoid", int(mujoco.mjtGeom.mjGEOM_MESH): "mesh",
          int(mujoco.mjtGeom.mjGEOM_PLANE): "plane"}

    def geoms(b):
        out = []
        for g in range(m.ngeom):
            if m.geom_bodyid[g] != b:
                continue
            G = dict(type=GT.get(int(m.geom_type[g]), str(int(m.geom_type[g]))), size=m.geom_size[g].tolist(),
                     pos=m.geom_pos[g].tolist(), quat=m.geom_quat[g].tolist(), rgba=m.geom_rgba[g].tolist(),
                     col=bool(m.geom_contype[g] | m.geom_conaffinity[g]), friction=m.geom_friction[g].tolist(),
                     name=m.geom(g).name, group=int(m.geom_group[g]),
                     # (MuJoCo's contact softness, rolling friction and whose friction counts: PhysX has other means)
                     solref=m.geom_solref[g].tolist(), solimp=m.geom_solimp[g][:3].tolist(), condim=int(m.geom_condim[g]),
                     priority=int(m.geom_priority[g]))
            if m.geom_matid[g] >= 0:
                G["rgba"] = m.mat_rgba[m.geom_matid[g]].tolist()
                G["mat"] = m.material(int(m.geom_matid[g])).name
            if G["type"] == "mesh":
                mid = int(m.geom_dataid[g]); v0, nv = m.mesh_vertadr[mid], m.mesh_vertnum[mid]
                f0, nf = m.mesh_faceadr[mid], m.mesh_facenum[mid]
                G["verts"] = np.round(m.mesh_vert[v0:v0 + nv], 6).tolist(); G["faces"] = m.mesh_face[f0:f0 + nf].tolist()
            out.append(G)
        return out

    def has_joint(b):
        return any(m.jnt_bodyid[j] == b for j in range(m.njnt))

    def root_of(b):
        """The body that b moves with: itself if it has a joint or stands in the world, else its parent's."""
        while not has_joint(b) and int(m.body_parentid[b]) != 0:
            b = int(m.body_parentid[b])
        return b

    def rel(r, p, R):
        """A pose (p, R: world) in body r's frame -> (pos, quat)."""
        Rr = d.xmat[r].reshape(3, 3)
        q = np.zeros(4); mujoco.mju_mat2Quat(q, (Rr.T @ R).flatten())
        return (Rr.T @ (p - d.xpos[r])).tolist(), q.tolist()

    bodies, skipped = [], []
    members = {}
    for b in range(1, m.nbody):
        if b in robot_bodies or int(m.body_rootid[b]) in robot_bodies:
            continue
        if d.xpos[b][0] > 50 or d.xpos[b][1] > 50:                  # (the simulator's own objects, parked out of sight)
            continue
        members.setdefault(root_of(b), []).append(b)
    for r, mem in members.items():
        name = m.body(r).name
        jn = [j for j in range(m.njnt) if m.jnt_bodyid[j] == r]
        free = len(jn) == 1 and m.jnt_type[jn[0]] == mujoco.mjtJoint.mjJNT_FREE
        if jn and not free:
            skipped.append(f"{name}: a {['free', 'ball', 'slide', 'hinge'][int(m.jnt_type[jn[0]])]} joint (built as a fixed body)")
        gs, kids = [], []
        M, c, I = 0.0, np.zeros(3), np.zeros((3, 3))
        for b in mem:                                               # (a body with bodies fixed to it: one rigid body)
            for g in geoms(b):
                Rg = np.zeros(9); mujoco.mju_quat2Mat(Rg, np.asarray(g["quat"], float)); Rg = Rg.reshape(3, 3)
                Rb = d.xmat[b].reshape(3, 3)
                g["pos"], g["quat"] = rel(r, d.xpos[b] + Rb @ np.asarray(g["pos"], float), Rb @ Rg)
                g["body"] = m.body(b).name
                gs.append(g)
            if b != r:
                kp, kq = rel(r, d.xpos[b], d.xmat[b].reshape(3, 3))
                kids.append(dict(name=m.body(b).name, pos=kp, quat=kq))
            M += float(m.body_mass[b]); c += float(m.body_mass[b]) * d.xipos[b]
        if not gs:
            continue
        c = c / M if M > 0 else d.xpos[r].copy()
        for b in mem:
            Ri = d.ximat[b].reshape(3, 3); dd = d.xipos[b] - c; mb = float(m.body_mass[b])
            I += Ri @ np.diag(m.body_inertia[b]) @ Ri.T + mb * (float(dd @ dd) * np.eye(3) - np.outer(dd, dd))
        Rr = d.xmat[r].reshape(3, 3)
        Il = Rr.T @ I @ Rr
        w, V = np.linalg.eigh(Il)
        if np.linalg.det(V) < 0:
            V[:, 2] = -V[:, 2]
        iq = np.zeros(4); mujoco.mju_mat2Quat(iq, V.flatten())
        bodies.append(dict(name=name, free=bool(free), pos=d.xpos[r].tolist(), quat=d.xquat[r].tolist(), mass=M,
                           ipos=(Rr.T @ (c - d.xpos[r])).tolist(), iquat=iq.tolist(), inertia=w.tolist(), geoms=gs,
                           members=kids, damping=float(m.dof_damping[m.jnt_dofadr[jn[0]]]) if free else 0.0))
    world_geoms = geoms(0)
    # the table's texture, if it has one
    tex = None
    for g in world_geoms:
        if g.get("name") == "table_top":
            gid = m.geom("table_top").id; mat = int(m.geom_matid[gid])
            if mat >= 0 and int(np.asarray(m.mat_texid[mat]).ravel()[0]) >= 0:
                t = int(np.asarray(m.mat_texid[mat]).ravel()[0])
                h, w, a = int(m.tex_height[t]), int(m.tex_width[t]), int(m.tex_adr[t])
                tex = dict(h=h, w=w, rgb=m.tex_rgb[a:a + h * w * 3].reshape(h, w, 3), repeat=m.mat_texrepeat[mat].tolist(),
                           uniform=bool(m.mat_texuniform[mat]))
    cams = {}
    for which, cam in (("scene", "d415"), ("left_wrist", "L_wrist")):
        cid = m.camera(cam).id; b = int(m.cam_bodyid[cid]); Rb = d.xmat[b].reshape(3, 3)
        Rn, eye = S.nominal_camera(cam)
        R0 = np.zeros(9); mujoco.mju_quat2Mat(R0, m.cam_quat[cid]); R0 = R0.reshape(3, 3)
        Rt = Rb @ R0 @ np.diag([1.0, -1.0, -1.0]); et = d.xpos[b] + Rb @ m.cam_pos[cid]
        cams[which] = dict(body=m.body(b).name, fovy=float(m.cam_fovy[cid]),
                           nominal_in_body=dict(eye=(Rb.T @ (eye - d.xpos[b])).tolist(), R=(Rb.T @ Rn).tolist()),
                           true_in_body=dict(eye=(Rb.T @ (et - d.xpos[b])).tolist(), R=(Rb.T @ Rt).tolist()))
    A = S.arms["L"]
    lights = [dict(pos=m.light_pos[i].tolist(), dir=m.light_dir[i].tolist(), diffuse=m.light_diffuse[i].tolist(),
                   ambient=m.light_ambient[i].tolist(), directional=bool(m.light_directional[i])) for i in range(m.nlight)]
    spec = dict(seed=int(seed), world=str(world), text=" ".join(str(text).split()), table_z=float(S.table_z),
                shown_in=shown_in, scene_shown=json.loads(json.dumps(practice.scene_shown(ep.scene), default=str)),
                bodies=bodies, world_geoms=world_geoms, cameras=cams, lights=lights, skipped=skipped,
                arm=dict(base_pos=d.xpos[A.base].tolist(), base_quat=d.xquat[A.base].tolist(), home_q=A.home.tolist(),
                         lo=A.lo.tolist(), hi=A.hi.tolist()),
                headlight=dict(ambient=m.vis.headlight.ambient.tolist(), diffuse=m.vis.headlight.diffuse.tolist()),
                world_log=log.getvalue()[-3000:])
    return spec, tex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True); ap.add_argument("--seeds", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--src", default=str(SRC_DEFAULT), help="the checkout's src/ whose practice code builds the scene (read only)")
    ap.add_argument("--sensors", default="d415")
    a = ap.parse_args()
    sys.path.insert(0, a.src)
    import cv2
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for seed in seeds_of(a.seeds):
        spec, tex = export(Path(a.world).resolve(), seed, a.src, a.sensors)
        if tex is not None:
            cv2.imwrite(str(out / f"{seed}_table.png"), tex["rgb"][..., ::-1])
            spec["table_texture"] = dict(file=f"{seed}_table.png", repeat=tex["repeat"], uniform=tex["uniform"])
        (out / f"{seed}.json").write_text(json.dumps(spec))
        nb = [b["name"] + ("+" + "+".join(k["name"] for k in b["members"]) if b["members"] else "") for b in spec["bodies"]]
        print(seed, len(nb), "bodies:", " ".join(nb)[:150], "| skipped:", spec["skipped"], flush=True)


if __name__ == "__main__":
    main()
