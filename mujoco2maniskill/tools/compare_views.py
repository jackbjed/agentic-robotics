"""The same scene seen by the robot's two cameras in MuJoCo (the project's simulation, with its D415 depth model) and
in ManiSkill (m2m): colour and depth side by side, and how far the depth images differ.

    project env:   ../.venv/bin/python -I tools/compare_views.py mujoco    --world W.py --seed 2001 --out out/compare/w16_2001
    ManiSkill env: <robomme python> -I tools/compare_views.py maniskill --scene out/scenes/w16/2001.json --out out/compare/w16_2001
    either:        ... tools/compare_views.py sheet --out out/compare/w16_2001
"""
import argparse, io, json, sys
from pathlib import Path
import numpy as np
import cv2

HERE = Path(__file__).resolve().parents[1]


def depth_png(z, lo=0.25, hi=1.4):
    z = np.asarray(z, float)
    v = np.clip((z - lo) / (hi - lo), 0, 1)
    im = cv2.applyColorMap((255 * (1 - np.nan_to_num(v, nan=1.0))).astype(np.uint8), cv2.COLORMAP_TURBO)
    im[~np.isfinite(z)] = 0
    return im


def save(out, tag, o):
    cv2.imwrite(str(out / f"{tag}_{o['camera']}_rgb.png"), o["rgb"][..., ::-1])
    cv2.imwrite(str(out / f"{tag}_{o['camera']}_depth.png"), depth_png(o["depth"]))
    np.savez_compressed(out / f"{tag}_{o['camera']}.npz", depth=np.asarray(o["depth"], np.float32), eye=o["eye"], R=o["R"], K=np.asarray(o["K"], float))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("side", choices=["mujoco", "maniskill", "sheet"])
    ap.add_argument("--world"); ap.add_argument("--seed", type=int, default=2001); ap.add_argument("--scene"); ap.add_argument("--out", required=True)
    ap.add_argument("--src", default=str(HERE.parents[1] / "agentic-robotics-ownreal" / "src"))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.side == "mujoco":
        sys.path.insert(0, a.src)
        from agentic_robotics.bimanual import practice, scenes
        from agentic_robotics.bimanual.tasks import Episode
        wns, scene, why = practice._world(str(Path(a.world).resolve()), scenes.empty(a.seed), a.seed, io.StringIO())
        ep = Episode("sim_bimanual_grippers", "insert_varied", seed=a.seed, sensors="d415", scene=scene)
        for c in ("scene", "left_wrist"):
            save(out, "mujoco", ep.S.observe(c))
    elif a.side == "maniskill":
        sys.path.insert(0, str(HERE))
        from m2m.rig import Rig
        sc = Path(a.scene); rig = Rig(json.loads(sc.read_text()), sc.parent, seed=int(sc.stem))
        for c in ("scene", "left_wrist"):
            save(out, "maniskill", rig.observe(c))
        rig.close()
    else:
        rows, rep = [], {}
        for c in ("left_wrist", "scene"):
            ims = [cv2.imread(str(out / f"{t}_{c}_{k}.png")) for t in ("mujoco", "maniskill") for k in ("rgb", "depth")]
            for im, lab in zip(ims, ("MuJoCo colour", "MuJoCo depth (D415 model)", "ManiSkill colour", "ManiSkill depth")):
                cv2.putText(im, f"{lab}, {c}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4); cv2.putText(im, f"{lab}, {c}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            rows.append(np.hstack(ims))
            zm, zs = np.load(out / f"mujoco_{c}.npz")["depth"], np.load(out / f"maniskill_{c}.npz")["depth"]
            both = np.isfinite(zm) & np.isfinite(zs); d = (zs - zm)[both] * 1000
            rep[c] = dict(valid_mujoco=round(float(np.isfinite(zm).mean()), 3), valid_maniskill=round(float(np.isfinite(zs).mean()), 3),
                          median_diff_mm=round(float(np.median(d)), 2), median_abs_diff_mm=round(float(np.median(np.abs(d))), 2),
                          p90_abs_diff_mm=round(float(np.quantile(np.abs(d), 0.9)), 2))
        cv2.imwrite(str(out / "sheet.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])
        (out / "depth_diff.json").write_text(json.dumps(rep, indent=1)); print(json.dumps(rep))


if __name__ == "__main__":
    main()
