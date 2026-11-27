"""Scenes of sweep_blocks MIRRORED left-right (in the table's x): the brush on the robot's right, the dustpan's mouth
towards -x, the cubes on the left. Looks, sizes and physics stay.

    <robomme python> -I sweep_blocks/mirror_scenes.py --src DIR --out DIR [--jobs 10] [--no-verify]

Every ep_NN.json of --src is written mirrored to --out (same name: scene k of --out is the mirror image of scene k of
--src), then the sweeping reference is run on each (reverification.json; no scene is dropped for it).

How a scene is mirrored: every object's place x -> -x and its turn (w, x, y, z) -> (w, x, -y, -z). That turn is the
mirror image's for a thing that is itself symmetric about its own x = 0 plane: the cubes are; the dustpan's and the
brush's collision pieces are within about 1 to 2 mm of it (mean vertex distance to the mirrored shape 1.6 and 0.8 mm).
The robot and the camera stand are not touched: the two arms are the same arm at x = -0.3 and +0.3.

The TASK is not mirror-symmetric: the judge wants the brush more than 100 mm to the RIGHT of the dustpan at the end in
either case. In a mirrored scene the arm that can sweep is the left one, so the brush has to be brought back across."""
import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def mirror(L):
    L = copy.deepcopy(L)
    for objs in L["Rigid"].values():
        for o in objs:
            o["default_pos"][0] = -float(o["default_pos"][0])
            w, x, y, z = (float(v) for v in o["default_ori"])
            o["default_ori"] = [w, x, -y, -z]
    L["Mirrored"] = True
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True); ap.add_argument("--out", required=True); ap.add_argument("--jobs", type=int, default=10)
    ap.add_argument("--no-verify", action="store_true")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    files = sorted(Path(a.src).glob("ep_*.json"))
    for f in files:
        (out / f.name).write_text(json.dumps(mirror(json.loads(f.read_text()))))
    if (Path(a.src) / "looks.json").exists():
        (out / "looks.json").write_text((Path(a.src) / "looks.json").read_text())
    (out / "mirrored_from.json").write_text(json.dumps(dict(source=str(Path(a.src).resolve().relative_to(HERE)), scenes=[f.name for f in files],
                                                            how="x -> -x; (w, x, y, z) -> (w, x, -y, -z); looks, sizes and physics unchanged"), indent=1))
    print(f"{len(files)} mirrored scenes -> {out}")
    if not a.no_verify:
        r = subprocess.run([sys.executable, "-I", str(HERE / "sweep_blocks" / "run_panel.py"), "--verify", "--panel", str(out), "--jobs", str(a.jobs)])
        sys.exit(r.returncode)


if __name__ == "__main__":
    main()
