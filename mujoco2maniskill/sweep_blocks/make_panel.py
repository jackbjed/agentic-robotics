"""A panel of initial conditions of sweep_blocks that are VERIFIED solvable by sweeping: candidates are drawn, each is
run with the scripted sweeping reference (sweep_blocks/reference_sweep.py), and the first N it solves are kept. The
objects are RoboDojo's; only where they stand and how they are turned varies.

    <robomme python> -I sweep_blocks/make_panel.py --n 20 --seed 5 --candidates 36 --out assets/robodojo_sweep/panel20 [--jobs 12]

The candidates' ranges are in candidate() (narrower than RoboDojo's own layouts, most of which this reference does not
solve). Kept in <out>/ep_NN.json (RoboDojo's layout format) with verification.json (every candidate's outcome)."""
import argparse
import copy
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from sweep_blocks.reach_map import reach  # noqa: E402
BASE = HERE / "assets" / "robodojo_sweep" / "layouts" / "sweep_blocks_4.json"


def qmul(a, b):
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw]


def yaw(a):
    return [float(np.cos(a / 2)), 0.0, 0.0, float(np.sin(a / 2))]


def candidate(rng):
    """(table frame, mm) The dustpan near the middle, its mouth within 15 degrees of +x; 3 cubes 25 to 150 mm in front of
    its lip and within 35 mm of its axis (the scripted sweep gets cubes in that are near the middle of the mouth);
    the brush on the left, within the left arm's reach."""
    L = copy.deepcopy(json.loads(BASE.read_text())); R = L["Rigid"]
    pan, broom = R["broom_shovel"][0], R["broom"][0]
    px, py = rng.uniform(-50, 50), rng.uniform(-60, 0)
    yw = np.radians(rng.uniform(-15, 15))
    pan["default_pos"][0], pan["default_pos"][1] = px / 1000, py / 1000
    q0 = pan["default_ori"]; pan["default_ori"] = qmul(yaw(yw), q0)
    w, x, y, z = q0; m0 = -np.array([2 * (x * y - z * w), 1 - 2 * (x * x + z * z)])       # the mouth's direction before the turn (-local y)
    m = np.array([np.cos(yw) * m0[0] - np.sin(yw) * m0[1], np.sin(yw) * m0[0] + np.cos(yw) * m0[1]]); u = np.array([-m[1], m[0]])
    b0 = broom["default_ori"]
    while True:                                                     # ... its handle where the left arm can take it from above and lift it
        bx, by, byaw = rng.uniform(-350, -230), rng.uniform(-60, 110), np.radians(rng.uniform(-25, 25))
        q = qmul(yaw(byaw), b0); w_, x_, y_, z_ = q
        ax = np.array([2 * (x_ * z_ + y_ * w_), 2 * (y_ * z_ - x_ * w_)]); ax = ax / np.linalg.norm(ax)      # the brush's long axis (local z) on the table
        h = np.array([bx, by]) / 1000 - 0.060 * ax; cl = (-ax[1], ax[0], 0)
        if reach("left", h[0], h[1], 0.022, closing=cl) and reach("left", h[0], h[1], 0.170, closing=cl):
            break
    broom["default_pos"][0], broom["default_pos"][1] = bx / 1000, by / 1000
    broom["default_ori"] = q
    pts = []
    while len(pts) < 3:
        p = np.array([px, py]) + rng.uniform(165, 290) * m + rng.uniform(-35, 35) * u
        if p[0] < 345 and p[1] < 40 and all(np.linalg.norm(p - q) > 40 for q in pts):
            pts.append(p)
    cubes = R["small_cube"][:3]
    for c, p in zip(cubes, pts):
        c["default_pos"][0], c["default_pos"][1] = float(p[0]) / 1000, float(p[1]) / 1000
        c["default_ori"] = yaw(rng.uniform(0, np.pi / 2)); c["category_idx"] = int(rng.integers(0, 10))
    R["small_cube"] = cubes
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20); ap.add_argument("--seed", type=int, default=1); ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=8); ap.add_argument("--candidates", type=int, default=64)
    ap.add_argument("--reference", default="reference_sweep.py", help="the scripted reference that must solve an episode (in sweep_blocks/)")
    ap.add_argument("--mirror", action="store_true", help="every candidate mirrored left-right (sweep_blocks/mirror_scenes.py): the brush on the right, "
                    "the dustpan's mouth towards -x, the cubes on the left")
    a = ap.parse_args()
    out = Path(a.out); (out / "candidates").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    files = []
    for i in range(a.candidates):
        L = candidate(rng)
        if a.mirror:
            from sweep_blocks.mirror_scenes import mirror
            L = mirror(L)
        f = out / "candidates" / f"cand_{i:03d}.json"; f.write_text(json.dumps(L)); files.append(f)

    def one(f):
        r = subprocess.run([sys.executable, "-I", str(HERE / "sweep_blocks" / a.reference), str(f), "--quiet"], capture_output=True, text=True)
        l = next((x for x in r.stdout.splitlines() if x.startswith("REFERENCE ")), None)
        return dict(json.loads(l[10:]) if l else dict(solved=False, error=r.stderr[-200:]), candidate=f.name)
    with ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(one, files))
    kept = [r for r in res if r.get("solved")][:a.n]
    for i, r in enumerate(kept):
        (out / f"ep_{i:02d}.json").write_text((out / "candidates" / r["candidate"]).read_text()); r["episode"] = i
    (out / "verification.json").write_text(json.dumps(dict(
        how=f"each candidate was run with the scripted reference {a.reference} (true positions) under the task's own 40 s and success test; the first N solved are the panel",
        seed=a.seed, mirrored=bool(a.mirror), candidates=len(res), solved=sum(1 for r in res if r.get("solved")), kept=len(kept), panel=kept, all=res), indent=1))
    print(f"candidates {len(res)}, solved by the reference {sum(1 for r in res if r.get('solved'))}, kept {len(kept)}")
    print("robot time of the kept ones (s):", [r["robot_s"] for r in kept])
    print("why the others failed (cubes in / of):", [f"{r.get('cubes_in')}/{r.get('cubes')}" for r in res if not r.get("solved")][:30])


if __name__ == "__main__":
    main()
