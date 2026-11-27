"""Read-only: every episode the judge called a success, from its PRIVATE log (true positions at the start, after every
command for what moved, and at the end). Does the measured success differ from the intended task?
  height     each cube's centre height at the success: resting on the pan's floor or ramp is about 14 to 26 mm
  settled    how long before the end each cube last moved (the judge has no settling interval)
  by what    of each cube's travel, how much happened in commands in which the brush moved too (swept with the held
             brush) and how much in commands in which it did not (pushed by a gripper, an arm or the dustpan)"""
import json, re, sys, glob, collections
from pathlib import Path
import numpy as np
NOW = re.compile(r"(\w+) \((-?[\d.]+), (-?[\d.]+), (-?[\d.]+)\)")
def episode(d):
    d = Path(d); res = json.loads((d / "result.json").read_text())
    lines = (d / "output.txt").read_text(errors="replace").splitlines()
    pos, travel, last_move, t_end = {}, collections.defaultdict(lambda: dict(with_brush=0.0, without_brush=0.0, brush_idle_cmds=[])), {}, res["robot_s"]
    start = next(l for l in lines if l.startswith("# TRUE positions at the start"))
    for n, x, y, z in NOW.findall(start.split(":", 1)[1]):
        pos[n] = np.array([float(x), float(y), float(z)])
    pan_travel = 0.0; brush_held_moves = 0
    for l in lines:
        m = re.match(r"# t=\s*([\d.]+) (\w+)\((.*)", l)
        if not m or " | NOW " not in l:
            continue
        t, cmd = float(m.group(1)), m.group(2)
        moved = {n: np.array([float(x), float(y), float(z)]) for n, x, y, z in NOW.findall(l.split(" | NOW ", 1)[1])}
        brush = "broom" in moved and np.linalg.norm(moved["broom"] - pos.get("broom", moved["broom"])) > 2.0
        if "broom_shovel" in moved:
            pan_travel += float(np.linalg.norm((moved["broom_shovel"] - pos["broom_shovel"])[:2]))
        for n, p in moved.items():
            if n.startswith("cube"):
                dxy = float(np.linalg.norm((p - pos[n])[:2]))
                travel[n]["with_brush" if brush else "without_brush"] += dxy
                if not brush and dxy > 3:
                    travel[n]["brush_idle_cmds"].append((t, cmd, round(dxy, 1)))
                last_move[n] = t
            pos[n] = p
    end = json.loads(next(l for l in lines if l.startswith("# TRUE positions at the end")).split(":", 1)[1])
    cubes = sorted(n for n in end if n.startswith("cube"))
    out = dict(episode=str(d), success=res["success"], robot_s=t_end, cube_z=[round(end[n][2], 1) for n in cubes], pan_z=round(end["broom_shovel"][2], 1),
               quiet_s=[round(t_end - last_move.get(n, 0.0), 1) for n in cubes],
               swept_mm=[round(travel[n]["with_brush"]) for n in cubes], unswept_mm=[round(travel[n]["without_brush"]) for n in cubes],
               idle=[travel[n]["brush_idle_cmds"][:3] for n in cubes], pan_slid_mm=round(pan_travel), brush_end_x_minus_pan=round(end["broom"][0] - end["broom_shovel"][0]))
    return out
if __name__ == "__main__":
    rows = []
    for pat in sys.argv[2:]:
        for f in sorted(glob.glob(pat)):
            r = json.loads(Path(f).read_text())
            if r.get("success"):
                rows.append(episode(Path(f).parent))
    json.dump(rows, open(sys.argv[1], "w"), indent=1)
    print("successes examined:", len(rows))
    z = np.array([v for r in rows for v in r["cube_z"]]); q = np.array([v for r in rows for v in r["quiet_s"]])
    print("cube centre height at success (mm): min %.1f, 5%% %.1f, median %.1f, 95%% %.1f, max %.1f | outside 12..28 mm: %d of %d cubes" % (z.min(), np.percentile(z, 5), np.median(z), np.percentile(z, 95), z.max(), int(((z < 12) | (z > 28)).sum()), len(z)))
    print("time since a cube last moved, at success (s): min %.1f, median %.1f | cubes that moved within the last 1.0 s: %d, within 0.5 s: %d" % (q.min(), np.median(q), int((q < 1.0).sum()), int((q < 0.5).sum())))
    sw = np.array([sum(r["swept_mm"]) for r in rows]); un = np.array([sum(r["unswept_mm"]) for r in rows])
    frac = un / np.maximum(sw + un, 1)
    print("share of the cubes' travel made in commands in which the brush did NOT move: median %.0f %%, max %.0f %% | episodes above 25 %%: %d, above 50 %%: %d" % (100 * np.median(frac), 100 * frac.max(), int((frac > 0.25).sum()), int((frac > 0.5).sum())))
    print("pan slid over the episode (mm): median %.0f, max %.0f" % (np.median([r["pan_slid_mm"] for r in rows]), max(r["pan_slid_mm"] for r in rows)))
    for r in rows:
        f = sum(r["unswept_mm"]) / max(sum(r["swept_mm"]) + sum(r["unswept_mm"]), 1)
        flag = [w for w, c in (("HEIGHT", any(v < 12 or v > 28 for v in r["cube_z"])), ("MOVING", min(r["quiet_s"]) < 1.0), ("NOT-BRUSH", f > 0.25)) if c]
        if flag:
            print("  ", "/".join(flag), r["episode"].split("rep0/")[-1], "z", r["cube_z"], "quiet", r["quiet_s"], "swept", r["swept_mm"], "unswept", r["unswept_mm"], [i[:2] for i in r["idle"]])
