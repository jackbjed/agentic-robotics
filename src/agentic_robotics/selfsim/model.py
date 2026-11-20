"""The agent's own simulator (real2sim, docs/real2sim.md; the agent's guide: docs/selfsim.md): its declaration
(sim/sim.yaml) and its scene (an MJCF file) on the robot kit (selfsim.KIT).

  load_decl(sim_dir)            sim.yaml, checked -> dict (SimError: what is wrong, for the agent)
  compose(sim_dir, out_xml)     the kit + the agent's scene -> one MJCF file (its keyframe "twin": the arm at home, the
                                agent's bodies where its scene puts them) -> dict(decl, info)

The scene is DATA, parsed here (never run): its top-level sections may be asset, default (named classes only),
worldbody, contact, equality, tendon and visual (headlight only); every file it names must lie inside sim/; its names
must not start with the robot's prefixes (L_, R_, obj_, d415, frame_) or be table_top. The physics options (time step,
integrator, solver) are the robot kit's: the arm must behave as it does in the real world."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import yaml

from . import KIT

FAMILIES = ("place-box", "close-box", "clutter-pick-any")
RESERVED = ("L_", "R_", "obj_", "d415", "frame_")
SECTIONS = ("asset", "default", "worldbody", "contact", "equality", "tendon", "visual")
NAMED = ("body", "geom", "joint", "freejoint", "site", "camera", "light", "mesh", "material", "texture", "hfield", "tendon")
HEADLIGHT = ("ambient", "diffuse", "specular", "active")
DEPTH_KEYS = ("noise", "dropout", "edge_holes_px", "dark_holes", "scale")


class SimError(Exception):
    """The reconstruction cannot be built or used (the message is for the agent)."""


def _num(x, what, n=None):
    try:
        a = np.atleast_1d(np.asarray(x, float))
    except (TypeError, ValueError):
        raise SimError(f"{what}: numbers expected, got {x!r}")
    if n is not None and a.size != n:
        raise SimError(f"{what}: {n} numbers expected, got {x!r}")
    if not np.all(np.isfinite(a)):
        raise SimError(f"{what}: not finite: {x!r}")
    return a


def load_decl(sim_dir):
    sim_dir = Path(sim_dir)
    f = sim_dir / "sim.yaml"
    if not f.exists():
        raise SimError("sim/sim.yaml is missing (docs/selfsim.md)")
    try:
        D = yaml.safe_load(f.read_text()) or {}
    except yaml.YAMLError as e:
        raise SimError(f"sim/sim.yaml: not valid YAML: {e}"[:400])
    if not isinstance(D, dict):
        raise SimError("sim/sim.yaml: a mapping expected")
    bad = set(D) - {"scene", "table", "objects", "box", "sensors", "notes"}
    if bad:
        raise SimError(f"sim/sim.yaml: unknown keys {sorted(bad)} (scene, table, objects, box, sensors, notes)")
    out = dict(scene=str(D.get("scene") or "scene.xml"))
    tb = D.get("table")
    if not tb:
        raise SimError("sim/sim.yaml: `table` (the geom or geoms things rest on) is missing")
    out["table"] = [str(x) for x in (tb if isinstance(tb, list) else [tb])]
    obj = D.get("objects") or {}
    if not isinstance(obj, dict) or not obj:
        raise SimError("sim/sim.yaml: `objects` (the names the tasks use -> your bodies) is missing or empty")
    out["objects"] = {str(k).strip(): str(v) for k, v in obj.items()}
    if len(set(out["objects"].values())) != len(out["objects"]):
        raise SimError("sim/sim.yaml: two names point to the same body")
    bx = D.get("box")
    if bx is not None:
        if not isinstance(bx, dict) or not {"name", "body", "lid_joint", "inside"} <= set(bx):
            raise SimError("sim/sim.yaml: `box` needs name, body, lid_joint, inside")
        ins = _num(bx["inside"], "box.inside", 6).reshape(3, 2)
        if np.any(ins[:, 1] <= ins[:, 0]):
            raise SimError("box.inside: each [lo, hi] needs lo < hi")
        out["box"] = dict(name=str(bx["name"]).strip(), body=str(bx["body"]), lid_joint=str(bx["lid_joint"]),
                          inside=ins.tolist())
        if out["box"]["body"] in out["objects"].values():
            raise SimError("box.body is also one of the objects")
    S = D.get("sensors") or {}
    if not isinstance(S, dict):
        raise SimError("sensors: a mapping expected")
    sens = dict(depth={}, calibration_error={}, colour={})
    dep = S.get("depth") or {}
    if set(dep) - set(DEPTH_KEYS):
        raise SimError(f"sensors.depth: unknown keys {sorted(set(dep) - set(DEPTH_KEYS))} ({', '.join(DEPTH_KEYS)})")
    if "noise" in dep:
        sens["depth"]["noise"] = _num(dep["noise"], "sensors.depth.noise", 2).tolist()
    for k in ("dropout", "scale"):
        if k in dep:
            v = float(_num(dep[k], f"sensors.depth.{k}", 1)[0])
            if not 0 <= v <= 0.5:
                raise SimError(f"sensors.depth.{k}: 0 to 0.5")
            sens["depth"][k] = v
    if "edge_holes_px" in dep:
        sens["depth"]["edge_holes_px"] = int(np.clip(_num(dep["edge_holes_px"], "edge_holes_px", 1)[0], 0, 10))
    if "dark_holes" in dep:
        dh = dep["dark_holes"] or {}
        sens["depth"]["dark_holes"] = dict(below=float(_num(dh.get("below", 0), "dark_holes.below", 1)[0]),
                                           p=float(np.clip(_num(dh.get("p", 0), "dark_holes.p", 1)[0], 0, 1)))
    for cam, v in (S.get("calibration_error") or {}).items():
        if cam not in ("d415", "L_wrist"):
            raise SimError("sensors.calibration_error: cameras d415 and L_wrist")
        a = _num(v, f"calibration_error.{cam}", 2)
        if a[0] > 0.05 or a[1] > 10 or np.any(a < 0):
            raise SimError(f"calibration_error.{cam}: [metres <= 0.05, degrees <= 10]")
        sens["calibration_error"][cam] = a.tolist()
    col = S.get("colour") or {}
    if set(col) - {"gain", "noise"}:
        raise SimError("sensors.colour: gain, noise")
    if "gain" in col:
        sens["colour"]["gain"] = _num(col["gain"], "colour.gain", 2).tolist()
    if "noise" in col:
        sens["colour"]["noise"] = float(_num(col["noise"], "colour.noise", 1)[0])
    out["sensors"] = sens
    return out


def _inside(sim_dir, f):
    p = (Path(sim_dir) / f).resolve()
    if not str(p).startswith(str(Path(sim_dir).resolve()) + os.sep):
        raise SimError(f"scene: file {f!r} is outside sim/")
    if not p.is_file():
        raise SimError(f"scene: file {f!r} not found")
    return str(p)


def _check_scene(sim_dir, root):
    if root.tag != "mujoco":
        raise SimError("scene: the root element must be <mujoco>")
    for c in root:
        if c.tag not in SECTIONS:
            raise SimError(f"scene: <{c.tag}> is not allowed at the top level (allowed: {', '.join(SECTIONS)}; the "
                           "physics options and the robot are the kit's)")
    for e in root.iter():
        if e.tag in ("include", "plugin", "extension", "actuator", "keyframe", "compiler", "option"):
            raise SimError(f"scene: <{e.tag}> is not allowed")
        nm = e.get("name")
        if nm and e.tag in NAMED and (nm.startswith(RESERVED) or nm == "table_top"):
            raise SimError(f"scene: the name {nm!r} is reserved (names may not start with {', '.join(RESERVED)} or be table_top)")
        for att in ("file", "fileright", "fileleft", "fileup", "filedown", "filefront", "fileback"):
            if e.get(att):
                e.set(att, _inside(sim_dir, e.get(att)))
        if e.tag == "camera" and (e.get("name") or "") in ("d415", "L_wrist"):
            raise SimError("scene: the cameras are the robot's")
    df = root.find("default")
    if df is not None:
        for c in df:
            if c.tag != "default":
                raise SimError("scene: <default> may hold named classes only (<default class=\"...\">), not defaults for "
                               "everything (they would change the robot)")
    vi = root.find("visual")
    if vi is not None:
        for c in vi:
            if c.tag != "headlight" or set(c.attrib) - set(HEADLIGHT):
                raise SimError("scene: <visual> may hold <headlight> (ambient, diffuse, specular, active) only")


def compose(sim_dir, out_xml):
    """-> dict(decl, info). Writes out_xml (absolute file paths; the kit's meshdir)."""
    import mujoco
    sim_dir = Path(sim_dir).resolve()
    D = load_decl(sim_dir)
    sf = Path(_inside(sim_dir, D["scene"]))
    try:
        sc = ET.parse(sf).getroot()
    except ET.ParseError as e:
        raise SimError(f"scene: not valid XML: {e}")
    _check_scene(sim_dir, sc)
    kt = ET.parse(KIT); kr = kt.getroot()
    kr.find("compiler").set("meshdir", str((KIT.parent / kr.find("compiler").get("meshdir")).resolve()))
    for c in sc:
        if c.tag == "visual":
            h = c.find("headlight")
            if h is not None:
                kh = kr.find("visual").find("headlight")
                for k, v in h.attrib.items():
                    kh.set(k, v)
            continue
        if c.tag == "default":
            kd = kr.find("default")
            for cls in c:
                kd.append(cls)
            continue
        dst = kr.find(c.tag)
        if dst is None:
            dst = ET.SubElement(kr, c.tag)
        for e in c:
            dst.append(e)
    key = kr.find("keyframe"); kr.remove(key)
    out_xml = Path(out_xml); out_xml.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_xml.with_name(out_xml.stem + "_nokey.xml")
    ET.ElementTree(kr).write(tmp)
    try:
        m1 = mujoco.MjModel.from_xml_path(str(tmp))
    except Exception as e:
        raise SimError(f"scene: MuJoCo cannot compile it with the robot: {e}"[:600])
    mk = mujoco.MjModel.from_xml_path(str(KIT)); kq = mk.key("twin").qpos
    q = m1.qpos0.copy()
    for j in range(mk.njnt):                                  # (the robot and the placeholders: the kit's keyframe)
        nm = mk.joint(j).name
        a0, a1 = mk.jnt_qposadr[j], m1.joint(nm).qposadr[0]
        n = {0: 7, 1: 4}.get(int(mk.jnt_type[j]), 1)
        q[a1:a1 + n] = kq[a0:a0 + n]
    ET.SubElement(ET.SubElement(kr, "keyframe"), "key", {"name": "twin", "qpos": " ".join(f"{v:.9g}" for v in q)})
    ET.ElementTree(kr).write(out_xml)
    tmp.unlink()
    m = mujoco.MjModel.from_xml_path(str(out_xml))
    info = check_refs(m, D)
    return dict(decl=D, info=info)


def free_body(m, name, what):
    try:
        b = m.body(name).id
    except KeyError:
        raise SimError(f"{what}: no body {name!r} in the scene")
    if m.body_parentid[b] != 0:
        raise SimError(f"{what}: body {name!r} must be a child of <worldbody> (with a <freejoint/>)")
    j = m.body_jntadr[b]
    if j < 0 or m.jnt_type[j] != 0:
        raise SimError(f"{what}: body {name!r} needs a <freejoint/> (it is placed and moves freely)")
    return b


def check_refs(m, D):
    """The declaration's names against the compiled model -> a short description of the reconstruction."""
    import mujoco
    tg = []
    for g in D["table"]:
        try:
            tg.append(m.geom(g).id)
        except KeyError:
            raise SimError(f"table: no geom {g!r} in the scene")
    bodies = {}
    for name, b in D["objects"].items():
        bodies[b] = free_body(m, b, f"objects[{name!r}]")
    if D.get("box"):
        bb = free_body(m, D["box"]["body"], "box.body")
        try:
            j = m.joint(D["box"]["lid_joint"]).id
        except KeyError:
            raise SimError(f"box.lid_joint: no joint {D['box']['lid_joint']!r}")
        if m.jnt_type[j] != 3:
            raise SimError("box.lid_joint: a hinge joint expected")
        b = m.jnt_bodyid[j]
        while b != 0 and b != bb:
            b = m.body_parentid[b]
        if b != bb:
            raise SimError("box.lid_joint: the hinge must be inside the box's body")
        bodies[D["box"]["body"]] = bb
    d = mujoco.MjData(m); mujoco.mj_forward(m, d)
    mass = {nm: round(float(m.body_subtreemass[b]), 4) for nm, b in bodies.items()}
    for nm, v in mass.items():
        if v <= 0:
            raise SimError(f"body {nm!r} has no mass")
    return dict(bodies=sorted(bodies), mass_kg=mass, table_geoms=len(tg), ngeom=int(m.ngeom), nmesh=int(m.nmesh),
                nlight=int(m.nlight), ntex=int(m.ntex))
