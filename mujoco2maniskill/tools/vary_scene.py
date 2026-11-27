"""A scene file with other physical properties (a copy; the shapes, places and colours stay).

    python3 tools/vary_scene.py --scene out/scenes/sweep_w21/2007.json --out out/scenes/sweep_vary/2007_slippery.json \
        [--table-friction F] [--friction F] [--mass-scale S] [--bodies w_obj_] [--note TEXT]

--table-friction   the table top's (and a mat's) sliding friction
--friction         the sliding friction of the bodies whose names start with --bodies (default w_obj_: the small objects)
--mass-scale       their mass and inertia times this
ManiSkill takes the AVERAGE of the two touching surfaces' coefficients (MuJoCo: the larger, or the one with priority)."""
import argparse, json, shutil
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--scene", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--table-friction", type=float); ap.add_argument("--friction", type=float); ap.add_argument("--mass-scale", type=float)
ap.add_argument("--bodies", default="w_obj_"); ap.add_argument("--note", default="")
ap.add_argument("--no-rolling", action="store_true", help="their rolling friction off (round things then roll freely)")
ap.add_argument("--size-scale", type=float, help="their size times this (same density: mass x S^3; they are moved apart "
                "until they no longer overlap each other or the scene's other bodies)")
a = ap.parse_args()
src = Path(a.scene); s = json.loads(src.read_text())
ch = []
if a.table_friction is not None:
    for g in s["world_geoms"]:
        if g.get("name") == "table_top":
            g["friction"][0] = a.table_friction
    for b in s["bodies"]:
        if b["name"] == "w_mat":
            for g in b["geoms"]:
                g["friction"][0] = a.table_friction
    ch.append(f"table friction {a.table_friction}")
n = 0
for b in s["bodies"]:
    if b["name"].startswith(a.bodies):
        n += 1
        if a.friction is not None:
            for g in b["geoms"]:
                g["friction"][0] = a.friction
        if a.mass_scale is not None:
            b["mass"] *= a.mass_scale; b["inertia"] = [v * a.mass_scale for v in b["inertia"]]
        if a.no_rolling:
            for g in b["geoms"]:
                g["friction"][2] = 0.0
if a.size_scale is not None:
    import math
    S = a.size_scale; tz = float(s.get("table_z", 0.0))
    mine = [b for b in s["bodies"] if b["name"].startswith(a.bodies)]
    for b in mine:
        for g in b["geoms"]:
            g["size"] = [v * S for v in g["size"]]; g["pos"] = [v * S for v in g["pos"]]
            g["friction"][2] *= S                                   # (rolling friction is a length: with the radius)
        b["pos"][2] = tz + (b["pos"][2] - tz) * S                   # (it rests on the table: its origin's height scales)
        b["mass"] *= S ** 3; b["inertia"] = [v * S ** 5 for v in b["inertia"]]; b["ipos"] = [v * S for v in b["ipos"]]
    # move them apart: no overlap with each other, nor with the footprints of the other bodies
    rad = lambda b: max(max(g["size"][:2]) if g["type"] != "sphere" else g["size"][0] for g in b["geoms"])
    others = []
    for b in s["bodies"]:
        if b in mine or b["name"] in ("d415_cam", "w_tape", "w_mat"):
            continue
        r = max(math.hypot(g["size"][0], g["size"][1] if g["type"] == "box" else g["size"][0]) for g in b["geoms"])
        others.append((b["pos"][0], b["pos"][1], r))
    for it in range(400):
        moved = False
        for i, b in enumerate(mine):
            for c in mine[i + 1:]:
                d = math.hypot(b["pos"][0] - c["pos"][0], b["pos"][1] - c["pos"][1]); need = rad(b) + rad(c) + 0.003
                if d < need:
                    ux, uy = ((b["pos"][0] - c["pos"][0]) / d, (b["pos"][1] - c["pos"][1]) / d) if d > 1e-6 else (1.0, 0.0)
                    k = (need - d) / 2 + 1e-4
                    b["pos"][0] += ux * k; b["pos"][1] += uy * k; c["pos"][0] -= ux * k; c["pos"][1] -= uy * k; moved = True
            for ox, oy, orad in others:
                d = math.hypot(b["pos"][0] - ox, b["pos"][1] - oy); need = rad(b) + orad + 0.005
                if d < need:
                    ux, uy = ((b["pos"][0] - ox) / d, (b["pos"][1] - oy) / d) if d > 1e-6 else (1.0, 0.0)
                    b["pos"][0] += ux * (need - d + 1e-4); b["pos"][1] += uy * (need - d + 1e-4); moved = True
        if not moved:
            break
    ch.append(f"{a.bodies}* size x {S} (mass x {S ** 3:g}; moved apart, {it + 1} rounds)")
if a.friction is not None:
    ch.append(f"{a.bodies}* friction {a.friction} ({n} bodies)")
if a.mass_scale is not None:
    ch.append(f"{a.bodies}* mass x {a.mass_scale} ({n} bodies)")
if a.no_rolling:
    ch.append(f"{a.bodies}* rolling friction off ({n} bodies)")
s["varied"] = ch + ([a.note] if a.note else [])
out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(s))
t = s.get("table_texture")
if t and not (out.parent / t["file"]).exists():
    shutil.copy(src.parent / t["file"], out.parent / t["file"])
print(out, "|", "; ".join(ch) or "unchanged")
