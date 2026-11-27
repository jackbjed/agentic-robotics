"""One robot description for both simulators: two copies of RoboDojo's X5A arm (names prefixed L_ / R_) fixed to one
root at RoboDojo's base poses -> assets/x5/generated/dual_x5.urdf (ManiSkill reads it; MuJoCo compiles the same file,
minus the camera bracket's picture, into dual_x5.xml and gets its table, drives and cameras added there).
Contacts between the links of one arm are switched off in both (the links' collision shapes are the convex hulls of
their meshes, which overlap at the joints).

    <either python> -I x5/build.py            (the MuJoCo part needs the project's .venv)"""
import copy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from x5 import spec as S


def dual_urdf(for_mujoco=False):
    src = ET.parse(S.ASSETS / "X5A.urdf").getroot()
    root = ET.Element("robot", name="dual_x5")
    if for_mujoco:
        mj = ET.SubElement(root, "mujoco")
        ET.SubElement(mj, "compiler", discardvisual="true", fusestatic="false", balanceinertia="true", strippath="false")
    ET.SubElement(root, "link", name="root")
    for side in S.SIDES:
        for el in src:
            if el.tag not in ("link", "joint"):
                continue
            e = copy.deepcopy(el)
            e.set("name", f"{side}_{e.get('name')}")
            for t in ("parent", "child"):
                for x in e.findall(t):
                    x.set("link", f"{side}_{x.get('link')}")
            for m in e.iter("mesh"):
                m.set("filename", str(S.ASSETS / m.get("filename")))
            if e.tag == "link":
                for v in list(e.findall("visual")) + list(e.findall("collision")):
                    fn = v.find("geometry/mesh")
                    if fn is not None and fn.get("filename").endswith(".glb") and (for_mujoco or "camera.glb" in fn.get("filename")):
                        e.remove(v)                                  # (the wrist camera's own picture: 16 MB, not copied)
            if e.tag == "joint" and e.get("type") == "revolute" and e.find("limit") is not None:
                lim = e.find("limit")
                lim.set("effort", str(S.ARM_FORCE))
            root.append(e)
        j = ET.SubElement(root, "joint", name=f"{side}_base", type="fixed")
        ET.SubElement(j, "parent", link="root"); ET.SubElement(j, "child", link=f"{side}_base_link")
        ET.SubElement(j, "origin", xyz=" ".join(map(str, S.BASE[side])), rpy=f"0 0 {S.BASE_YAW}")
    return root


def srdf(urdf_root):
    r = ET.Element("robot", name="dual_x5")
    for side in S.SIDES:
        links = [l.get("name") for l in urdf_root.findall("link") if l.get("name").startswith(side + "_")] + ["root"]
        for i, a in enumerate(links):
            for b in links[i + 1:]:
                ET.SubElement(r, "disable_collisions", link1=a, link2=b, reason="same arm")
    return r


def mujoco_xml():
    import mujoco
    S.GEN.mkdir(parents=True, exist_ok=True)
    u = S.GEN / "dual_x5_mujoco.urdf"
    ET.ElementTree(dual_urdf(for_mujoco=True)).write(u)
    m = mujoco.MjModel.from_xml_path(str(u))
    raw = S.GEN / "dual_x5_raw.xml"
    mujoco.mj_saveLastXML(str(raw), m)
    x = ET.parse(raw).getroot()
    x.find("compiler").set("angle", "radian")
    opt = x.find("option") if x.find("option") is not None else ET.SubElement(x, "option")
    opt.set("timestep", str(S.DT / 4)); opt.set("integrator", "implicitfast"); opt.set("cone", "elliptic"); opt.set("impratio", "10")
    if S.MJ_SOLREF:
        df = x.find("default") if x.find("default") is not None else ET.Element("default")
        if x.find("default") is None:
            x.insert(1, df)
        ET.SubElement(df, "geom", solref=S.MJ_SOLREF, solimp=S.MJ_SOLIMP, condim=str(S.MJ_CONDIM))
    wb = x.find("worldbody")
    for b in wb.iter("body"):
        b.set("gravcomp", "1")                                      # (RoboDojo: the arms' gravity is switched off)
    for g in wb.iter("geom"):
        g.set("rgba", "0.75 0.76 0.78 1"); g.set("contype", "1"); g.set("conaffinity", "2")      # (arm links: touch objects only)
        g.set("friction", f"1.0 {S.MJ_TORSION} 0.0001")
    for j in wb.iter("joint"):
        base = j.get("name")[2:]
        if base in S.ARM_JOINTS:
            j.set("armature", str(S.ARM_ARMATURE))
        j.set("damping", "0")
    for side in S.SIDES:                                            # (fingers: they do touch each other and objects)
        for b in wb.iter("body"):
            if b.get("name") in (f"{side}_link7", f"{side}_link8"):
                for g in b.findall("geom"):
                    g.set("rgba", "0.15 0.15 0.16 1")
    for side in S.SIDES:                                            # (the tool point: 145 mm along the hand, between the fingers)
        for b in wb.iter("body"):
            if b.get("name") == f"{side}_link6":
                ET.SubElement(b, "site", name=f"{side}_tcp", pos=f"{S.TCP_X} 0 0", size="0.004", rgba="0 0 0 0")
    # the table, the floor, a cube, lights, cameras
    t = S.TABLE
    ET.SubElement(wb, "geom", name="table", type="box", size=" ".join(str(v / 2) for v in t["size"]), pos=" ".join(map(str, t["pos"])),
                  rgba=" ".join(map(str, t["rgba"])), friction=f"{t['friction']} {S.MJ_TORSION} 0.0001", contype="2", conaffinity="3")
    ET.SubElement(wb, "geom", name="floor", type="plane", size="4 4 0.1", rgba="0.85 0.85 0.83 1", contype="2", conaffinity="3")
    c = S.CUBE
    cb = ET.SubElement(wb, "body", name="cube", pos=" ".join(map(str, c["pos"])))
    ET.SubElement(cb, "freejoint", name="cube_free")
    ET.SubElement(cb, "geom", name="cube", type="box", size=" ".join([str(c["half"])] * 3), mass=str(c["mass"]), rgba=" ".join(map(str, c["rgba"])),
                  friction=f"{c['friction']} {S.MJ_TORSION} 0.0001", contype="2", conaffinity="3")
    ET.SubElement(wb, "light", pos="0.3 -0.5 2.6", dir="-0.1 0.1 -1", diffuse="0.8 0.8 0.8")
    ET.SubElement(wb, "light", pos="-1.5 -1.5 2.0", dir="0.6 0.5 -0.6", diffuse="0.3 0.3 0.3")
    for name, cam in (("head", S.HEAD_CAM), ("side", S.SIDE_CAM)):
        import numpy as np
        e, tg = np.array(cam["eye"]), np.array(cam["target"])
        f = (tg - e) / np.linalg.norm(tg - e); r = np.cross(f, [0, 0, 1.0]); r /= np.linalg.norm(r); up = np.cross(r, f)
        ET.SubElement(wb, "camera", name=name, pos=" ".join(map(str, e)), xyaxes=" ".join(f"{v:.6f}" for v in list(r) + list(up)), fovy=str(cam["fovy_deg"]))
    act = ET.SubElement(x, "actuator")
    for n in S.joint_names():
        base = n[2:]
        kp, kd, fr = (S.ARM_KP, S.ARM_KD, S.ARM_FORCE) if base in S.ARM_JOINTS else (S.GRIP_KP, S.GRIP_KD, S.GRIP_FORCE)
        ET.SubElement(act, "general", name=n, joint=n, gainprm=f"{kp} 0 0", biastype="affine", biasprm=f"0 {-kp} {-kd}",
                      forcelimited="true", forcerange=f"{-fr} {fr}", ctrllimited="false")
    vis = ET.SubElement(x, "visual"); ET.SubElement(vis, "headlight", ambient="0.45 0.45 0.45", diffuse="0.35 0.35 0.35")
    ET.SubElement(vis, "global", offwidth="1280", offheight="960")
    out = S.GEN / "dual_x5.xml"
    ET.ElementTree(x).write(out)
    m = mujoco.MjModel.from_xml_path(str(out))
    return out, m


def main():
    S.GEN.mkdir(parents=True, exist_ok=True)
    u = dual_urdf()
    ET.ElementTree(u).write(S.GEN / "dual_x5.urdf")
    # (no collision-pair file: ManiSkill keeps only a few such pairs; the arms' links get one "do not touch each
    #  other" group in x5/sim_maniskill.py instead, as MuJoCo's contact masks do here)
    (S.GEN / "dual_x5.srdf").unlink(missing_ok=True)
    print("ManiSkill:", S.GEN / "dual_x5.urdf")
    try:
        out, m = mujoco_xml()
        print("MuJoCo:", out, "| joints", m.njnt, "actuators", m.nu, "bodies", m.nbody, "geoms", m.ngeom)
    except ImportError:
        print("MuJoCo: not in this environment (run x5/build.py once with the project's .venv)")


if __name__ == "__main__":
    main()
