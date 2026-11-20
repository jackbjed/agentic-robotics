"""Checks and renders of the agent's own simulator (real2sim; ar-practice sim check / sim render). A privileged
process like the world server (it renders on the GPU), running no agent code: the scene is data, the layouts arrive as
checked JSON (selfsim/sample.py).

    python -I -m agentic_robotics.selfsim.check --prep prep.json --job job.json --out DIR

job = dict(kind="check", layouts={family: [layout, ...]})
      -> per layout: the settle (how far each body moved in 1 s, anything below the table, MuJoCo's warnings, the
         deepest interpenetration), a preview (scene camera | wrist camera at home) -> DIR/report.json, DIR/*.png
job = dict(kind="render", layout=layout, real=<a real frame's .json>, q=[7 joints] | None)
      -> the agent's simulator with that layout, the arm at the real frame's joints, from the camera pose the program
         believed: DIR/compare.png (real | yours, colour and depth, and the depth difference), DIR/stats.json"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .model import SimError


def depth_vis(z, lo=0.25, hi=1.6):
    import cv2
    ok = np.isfinite(z)
    v = np.clip((np.where(ok, z, hi) - lo) / (hi - lo), 0, 1)
    im = cv2.applyColorMap((255 * (1 - v)).astype(np.uint8), cv2.COLORMAP_TURBO)
    im[~ok] = 0
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def label(im, txt):
    import cv2
    im = im.copy()
    cv2.rectangle(im, (0, 0), (im.shape[1], 22), (20, 20, 20), -1)
    cv2.putText(im, txt, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return im


def save(path, rgb):
    import cv2
    cv2.imwrite(str(path), cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR))


def scene_for(prep, family):
    from .world import SelfSimScene
    return SelfSimScene(dict(model=prep["model"], decl=prep["decl"]), family)


def do_check(prep, job, out):
    rep = dict(ok=True, layouts={})
    for fam, Ls in job["layouts"].items():
        rows = []
        try:
            s = scene_for(prep, fam)
        except (SimError, Exception) as e:
            rep["ok"] = False; rep["layouts"][fam] = [dict(ok=False, why=f"{type(e).__name__}: {e}"[:600])]
            continue
        for k, L in enumerate(Ls):
            if isinstance(L, dict) and L.get("error"):
                rows.append(dict(ok=False, why=L["error"][:800])); rep["ok"] = False
                continue
            try:
                P = s.P
                s.reset_layout(L, 1000 + k)
                before = {b: P.world.pose(b)[0].copy() for b in L["place"]}
                nbad0 = int(P.d.warning[1].number)
                P.settle(1.0)
                moved = {s.names.get(b, b): round(float(np.linalg.norm(P.world.pose(b)[0] - before[b])) * 1000, 1) for b in L["place"]}
                below = [s.names.get(b, b) for b in L["place"] if P.com_z(b) < s.table_z - 0.05]
                pen = min([float(c.dist) for c in P.d.contact[:P.d.ncon]] + [0.0])
                warn = int(P.d.warning[1].number) - nbad0
                ims = []
                for cam in ("d415", "L_wrist"):
                    rgb, z, _ = s.camera(cam)
                    ims.append(label(rgb, f"{fam} #{k} {cam} (yours)"))
                    ims.append(label(depth_vis(z), f"{cam} depth (yours)"))
                im = np.concatenate([np.concatenate(ims[:2], 1), np.concatenate(ims[2:], 1)], 0)
                save(out / f"{fam}_{k}.png", im)
                ok = warn == 0 and not below and max(moved.values()) < 30
                rows.append(dict(ok=ok, target=s.names.get(L["target"], L["target"]), task=s.task(),
                                 moved_in_1s_mm=moved, below_table=below, deepest_penetration_mm=round(-pen * 1000, 2),
                                 physics_warnings=warn, preview=f"{fam}_{k}.png",
                                 lid_deg=round(math.degrees(P.d.qpos[s.lid_q]), 1) if s.box and fam != "clutter-pick-any" else None))
                rep["ok"] &= ok
            except Exception as e:
                rows.append(dict(ok=False, why=f"{type(e).__name__}: {e}"[:600])); rep["ok"] = False
        rep["layouts"][fam] = rows
    return rep


def do_render(prep, job, out):
    import cv2
    fr = json.loads(Path(job["real"]).read_text())
    cam = fr["cam"]
    base = str(job["real"])[:-len(".json")]
    real_rgb = cv2.cvtColor(cv2.imread(base + "_rgb.png"), cv2.COLOR_BGR2RGB)
    dz = cv2.imread(base + "_depth.png", cv2.IMREAD_UNCHANGED).astype(np.float32) / 1000.0
    real_z = np.where(dz > 0, dz, np.inf)
    fam = "place-box" if prep["decl"].get("box") and prep["decl"]["box"]["body"] in job["layout"]["place"] else "clutter-pick-any"
    s = scene_for(prep, fam)
    L = dict(job["layout"])
    s.reset_layout(L, 7)
    P = s.P
    q = job.get("q") or fr["q"]
    P.teleport_arm(None, 0.0, q=np.asarray(q, float))
    w = fr.get("width")
    if w is not None:
        for a in P.world.fing:
            P.d.qpos[a] = float(w) / 2
        P.d.ctrl[P.world.grip] = float(w) / 2
    P.mj.mj_forward(P.m, P.d)
    for c in ("d415", "L_wrist"):                              # (the pose the program believed: no error drawn)
        P.world.set_camera_error(c, [0, 0, 0], [0, 0, 0])
    rgb, z, _ = P.render(cam, depth=True, seg=False)
    z = np.where(np.isfinite(z) & (z > 0.275), z, np.inf)
    both = np.isfinite(z) & np.isfinite(real_z)
    diff = np.where(both, z - real_z, np.nan)
    ad = np.abs(diff[both])
    stats = dict(cam=cam, real_frame=str(job["real"]), valid_real=round(float(np.isfinite(real_z).mean()), 3),
                 valid_yours=round(float(np.isfinite(z).mean()), 3), valid_both=round(float(both.mean()), 3),
                 depth_abs_diff_mm=dict(median=round(float(np.median(ad)) * 1000, 1) if ad.size else None,
                                        p90=round(float(np.percentile(ad, 90)) * 1000, 1) if ad.size else None,
                                        mean_signed=round(float(np.nanmean(diff)) * 1000, 1) if ad.size else None),
                 colour_abs_diff_mean=round(float(np.abs(rgb.astype(np.float32) - real_rgb.astype(np.float32)).mean()), 1),
                 note="depth difference = yours - real (red: yours is farther, blue: nearer), on pixels valid in both")
    dv = np.zeros(rgb.shape, np.uint8)
    if both.any():
        c = np.clip(np.nan_to_num(diff) / 0.03, -1, 1)
        dv[..., 0] = (255 * np.clip(c, 0, 1)).astype(np.uint8); dv[..., 2] = (255 * np.clip(-c, 0, 1)).astype(np.uint8)
        dv[..., 1] = (60 * both).astype(np.uint8)
    blend = (0.5 * rgb.astype(np.float32) + 0.5 * real_rgb.astype(np.float32)).astype(np.uint8)
    top = np.concatenate([label(real_rgb, f"real {cam}"), label(rgb, "yours"), label(blend, "overlay 50/50")], 1)
    bot = np.concatenate([label(depth_vis(real_z), "real depth"), label(depth_vis(z), "your depth (exact)"),
                          label(dv, "yours - real: red farther, blue nearer (+-3 cm)")], 1)
    save(out / "compare.png", np.concatenate([top, bot], 0))
    save(out / "yours_rgb.png", rgb)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep", required=True); ap.add_argument("--job", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    prep, job = json.loads(Path(a.prep).read_text()), json.loads(Path(a.job).read_text())
    try:
        r = do_check(prep, job, out) if job["kind"] == "check" else do_render(prep, job, out)
    except Exception as e:
        import traceback
        r = dict(ok=False, why=f"{type(e).__name__}: {e}"[:800], trace=traceback.format_exc()[-1500:])
    json.dump(r, open(out / ("report.json" if job["kind"] == "check" else "stats.json"), "w"), indent=1, default=str)
    import os
    os._exit(0)


if __name__ == "__main__":
    main()
