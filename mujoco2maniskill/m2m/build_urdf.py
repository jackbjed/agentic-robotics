"""The rig's arm for ManiSkill: ManiSkill's own Panda (panda_v2.urdf: links, inertias, meshes) with the PROJECT's
gripper on it: the long tapered fingers of the project's MuJoCo model (assets/hand_dump.json, made by
tools/dump_hand.py: the same collision boxes, the same masses), the tool point 180.8 mm from the hand's frame, and the
wrist camera's housing on the hand. -> assets/generated/panda_umi.urdf (+ the fingers' visual mesh).

The arm's kinematics are the Panda's in both simulators; only the gripper had to be carried over."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
ASSETS = HERE / "assets"
GEN = ASSETS / "generated"
TCP_Z = 0.1808
FINGER_RGBA = "0.12 0.12 0.13 1"
CAM_RGBA = "0.25 0.25 0.27 1"


def _quat_R(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _box(parent, tag, pos, half, rgba=None, name=None):
    e = ET.SubElement(parent, tag)
    if name:
        e.set("name", name)
    ET.SubElement(e, "origin", rpy="0 0 0", xyz=" ".join(f"{v:.6f}" for v in pos))
    g = ET.SubElement(e, "geometry")
    ET.SubElement(g, "box", size=" ".join(f"{2 * v:.6f}" for v in half))
    if rgba and tag == "visual":
        m = ET.SubElement(e, "material", name=f"m_{rgba.replace(' ', '_')}")
        ET.SubElement(m, "color", rgba=rgba)
    return e


def build(force=False):
    """-> the URDF's path (built once)."""
    out = GEN / "panda_umi.urdf"
    if out.exists() and not force:
        return out
    from mani_skill import PACKAGE_ASSET_DIR
    src = Path(PACKAGE_ASSET_DIR) / "robots" / "panda"
    GEN.mkdir(parents=True, exist_ok=True)
    hand = json.loads((ASSETS / "hand_dump.json").read_text())["bodies"]
    tree = ET.parse(src / "panda_v2.urdf"); root = tree.getroot()
    for mesh in root.iter("mesh"):                                  # (this file lives outside ManiSkill's assets)
        mesh.set("filename", str(src / mesh.get("filename")))
    # the fingers' visual mesh, in the finger's frame
    F = hand["L_left_finger"]
    z = np.load(ASSETS / "mesh_umi_finger.npz")
    gm = next(g for g in F["geoms"] if g.get("mesh") == "umi_finger")
    V = z["v"] @ _quat_R(gm["quat"]).T + np.asarray(gm["pos"])
    for side, flip in (("left", 1.0), ("right", -1.0)):
        W = V * np.array([flip, flip, 1.0])
        with open(GEN / f"finger_{side}.obj", "w") as f:
            f.writelines(f"v {a:.6f} {b:.6f} {c:.6f}\n" for a, b, c in W)
            f.writelines(f"f {a + 1} {b + 1} {c + 1}\n" for a, b, c in z["f"])
    for side, flip in (("left", 1.0), ("right", -1.0)):
        link = next(l for l in root.findall("link") if l.get("name") == f"panda_{side}finger")
        for ch in list(link):
            link.remove(ch)
        v = ET.SubElement(link, "visual")
        ET.SubElement(v, "origin", rpy="0 0 0", xyz="0 0 0")
        ET.SubElement(ET.SubElement(v, "geometry"), "mesh", filename=str(GEN / f"finger_{side}.obj"))
        ET.SubElement(ET.SubElement(v, "material", name="finger"), "color", rgba=FINGER_RGBA)
        for g in F["geoms"]:
            pos = [flip * g["pos"][0], flip * g["pos"][1], g["pos"][2]]
            if g["type"] == 6 and not g["col"]:                     # (the finger's block at the hand: seen)
                _box(link, "visual", pos, g["size"], FINGER_RGBA)
            if g["type"] == 6 and g["col"]:
                _box(link, "collision", pos, g["size"])
        i = ET.SubElement(link, "inertial")
        ET.SubElement(i, "origin", rpy="0 0 0", xyz=f"0 {flip * 0.012:.4f} 0.06")
        ET.SubElement(i, "mass", value=f"{F['mass']:.4f}")
        ET.SubElement(i, "inertia", ixx="6e-5", ixy="0", ixz="0", iyy="6e-5", iyz="0", izz="8e-6")
    # the wrist camera's housing on the hand (seen, not felt: as in the project's model)
    hl = next(l for l in root.findall("link") if l.get("name") == "panda_hand")
    for g in hand["L_hand"]["geoms"]:
        if g["type"] == 6 and not g["col"]:
            _box(hl, "visual", g["pos"], g["size"], CAM_RGBA)
    for j in root.findall("joint"):
        if j.get("name") == "panda_hand_tcp_joint":
            j.find("origin").set("xyz", f"0 0 {TCP_Z}")
        if j.get("name") in ("panda_leftfinger_pad_joint", "panda_rightfinger_pad_joint"):
            j.find("origin").set("xyz", "0 0 0.1224")               # (the pads: at the tool point's height)
    tree.write(out)
    # (ManiSkill reads the collision pairs to ignore from the file beside the URDF: the Panda's own)
    (GEN / "panda_umi.srdf").write_text((src / "panda_v2.srdf").read_text())
    return out


if __name__ == "__main__":
    print(build(force=True))
