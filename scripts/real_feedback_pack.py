"""Real-robot episodes of a bimanual program (real/run program ... --mode live) as a pack that a development session
can read (bimanual/improve.py --real-feedback PACK: the tools list_real_episodes, read_real_output, view_real_images,
read_real_depth).

    .venv/bin/python -I scripts/real_feedback_pack.py --manifest runs/bimanual/real_feedback/NAME/manifest.json

manifest.json (written by the operator), next to which the pack is written:
    {"rig": "text: what the real robot is and how it answers the program's commands (facts, not a diagnosis)",
     "episodes": [{"name": "R1", "run": "real/results/<run>", "version": 21, "setup": "what stood where, what the
                   operator changed", "contact": "which setting of the moves until contact the episode ran with"}]}

Per episode, in <pack>/<name>/: episode.json (the facts: verdict, the operator's note, the setup, robot time, the
moves until contact as the program saw them and the arm's own server recorded its guarded lines, the images' names),
output.txt (the program's prints and every command with its result and the arm's MEASURED tool position), program.py
(what ran), images/ (what the program's cameras gave it at each look, the same with the pixels WITHOUT depth marked,
each look's depth as a picture, the images the program handed over with robot.show, and frames of the two videos),
depth/ (each look's depth as the camera gave it: 16-bit PNG, mm, 0 = no depth). Nothing of the run is changed; the
pack holds copies. Only the listed runs go in. An episode the operator stopped (no result.json) goes in without a
verdict."""
import argparse
import ast
import json
import re
import shutil
from pathlib import Path

import cv2
import numpy as np

VIDEO_FRAMES = 8                                                # per camera, evenly over the episode, the last one included
MOVE = re.compile(r"# t=\s*([\d.]+) move\('left', \(([-\d., ]+)\), .*?(, True)?\) -> (.*?) \| left tool \(([-\d., ]+)\)")
DEPTH_RANGE = {"scene": (600, 1600), "left_wrist": (250, 500)}   # mm: the colours of a look's depth picture


def _nodepth(rgb, depth):
    out = rgb.copy()
    out[depth == 0] = (0.35 * out[depth == 0] + 0.65 * np.array([255, 0, 255])).astype(np.uint8)   # (BGR: magenta)
    return out


def _depth_picture(dep, cam):
    """A look's depth as a picture: near = red, far = blue over DEPTH_RANGE (clipped), no depth = black; the scale on top."""
    lo, hi = DEPTH_RANGE.get(cam, (250, 1600))
    x = np.clip((dep.astype(np.float32) - lo) / (hi - lo), 0, 1)
    out = cv2.applyColorMap((255 * (1 - x)).astype(np.uint8), cv2.COLORMAP_TURBO)
    out[dep == 0] = 0
    bar = cv2.applyColorMap(np.tile(np.linspace(255, 0, out.shape[1]).astype(np.uint8), (14, 1)), cv2.COLORMAP_TURBO)
    out[:14] = bar
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        cv2.putText(out, f"{v:.0f} mm", (min(int(k / 4 * (out.shape[1] - 1)), out.shape[1] - 62), 28), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def _video(path, out, cam):
    cap = cv2.VideoCapture(str(path))
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS) or 15.0
    names = []
    for k in sorted({int(round(i)) for i in np.linspace(0, max(n - 1, 0), VIDEO_FRAMES)}):
        cap.set(cv2.CAP_PROP_POS_FRAMES, k)
        ok, im = cap.read()
        if ok:
            name = f"video_{cam}_{k / fps:05.1f}s.jpg"
            cv2.imwrite(str(out / name), im, [cv2.IMWRITE_JPEG_QUALITY, 88]); names.append(name)
    cap.release()
    return names


def _contact_moves(run):
    """-> (the moves until contact as the program saw them: output.txt, with how many stops of the arm's server were
    not passed on to it and how many ordinary steps were taken on the way (real/run program --confirm-contact);
    every guarded line the arm's server ran, with what it recorded: actions.jsonl)."""
    lines = []
    f = run / "actions.jsonl"
    for l in (f.read_text().splitlines() if f.exists() else []):
        if not l.strip():
            continue
        a = json.loads(l)
        if a.get("kind") == "execute" and a.get("guard"):
            r = a.get("result") or {}
            try:
                r = ast.literal_eval(r) if isinstance(r, str) else r
            except (ValueError, SyntaxError):
                r = {}
            c = r.get("contact") or {}
            lines.append(dict(lag_allowed_mm=round(a["guard"]["dz"] * 1000, 1), force_limit_n=a["guard"]["df"],
                              ended="stopped by the server" if c else "ran to its end", seconds=c.get("t"),
                              hand_behind_mm=c.get("held_mm"), came_down_mm=c.get("down_mm"), force_change_n=c.get("dfz_n")))
    seen, held, steps = [], 0, 0
    for l in (run / "program" / "output.txt").read_text(errors="replace").splitlines():
        if "the arm's server stopped the line" in l:
            held += 1
        elif "(an ordinary step of" in l:
            steps += 1
        m = MOVE.match(l)
        if m and m.group(3):
            seen.append(dict(t=float(m.group(1)), goal_mm=[float(x) for x in m.group(2).split(",")], result=m.group(4),
                             tool_after_mm=[float(x) for x in m.group(5).split(",")], server_stops_not_passed_on=held, ordinary_steps=steps))
            held = steps = 0
    return seen, lines


def episode(e, pack, root):
    run = (root / e["run"]).resolve()
    out = pack / e["name"]
    if out.exists():
        shutil.rmtree(out)
    (out / "images").mkdir(parents=True); (out / "depth").mkdir()
    spec = json.loads((run / "spec.json").read_text())
    res = json.loads((run / "result.json").read_text()) if (run / "result.json").exists() else {}
    if spec.get("mode") != "live" or spec.get("backend") != "robot":
        raise SystemExit(f"{run.name}: not a live episode on the robot")
    shutil.copy(run / "program" / "output.txt", out / "output.txt")
    shutil.copy(run / "skill" / "program.py", out / "program.py")
    text = (out / "output.txt").read_text(errors="replace")
    looks = []
    for j in sorted((run / "frames").glob("*.json")):
        meta = json.loads(j.read_text())
        cam = {"d415": "scene", "L_wrist": "left_wrist"}.get(meta["cam"], meta["cam"])
        k = int(j.stem.split("_")[0])
        rgb = cv2.imread(str(j.with_name(j.stem + "_rgb.png")))
        dep = cv2.imread(str(j.with_name(j.stem + "_depth.png")), cv2.IMREAD_UNCHANGED)
        if rgb is None or dep is None:
            continue
        base = f"look_{k:02d}_{cam}"
        cv2.imwrite(str(out / "images" / f"{base}.jpg"), rgb, [cv2.IMWRITE_JPEG_QUALITY, 90])
        cv2.imwrite(str(out / "images" / f"{base}_nodepth.jpg"), _nodepth(rgb, dep), [cv2.IMWRITE_JPEG_QUALITY, 90])
        cv2.imwrite(str(out / "images" / f"{base}_depth.jpg"), _depth_picture(dep, cam), [cv2.IMWRITE_JPEG_QUALITY, 92])
        shutil.copy(j.with_name(j.stem + "_depth.png"), out / "depth" / f"{base}.png")       # (16-bit, mm, 0 = no depth)
        v = dep[dep > 0]
        looks.append(dict(image=base, camera=cam, robot_time_s=meta.get("t"), K=meta.get("K"), pixels_without_depth=round(float((dep == 0).mean()), 3),
                          depth_mm=dict(nearest=int(v.min()), p5=int(np.percentile(v, 5)), median=int(np.median(v)), p95=int(np.percentile(v, 95))) if len(v) else None))
    shown = []
    for p in sorted((run / "program" / "shown").glob("*.png")) if (run / "program" / "shown").exists() else []:
        shutil.copy(p, out / "images" / f"shown_{p.name}"); shown.append(f"shown_{p.stem}")
    videos = {}
    for cam in ("scene", "wrist"):
        v = run / "video" / f"{cam}.mp4"
        if v.exists():
            names = [n[:-4] for n in _video(v, out / "images", cam)]
            if names:
                videos[cam] = names
    v = res.get("verdict")
    times = [float(m) for m in re.findall(r"^# t=\s*([\d.]+) ", text, flags=re.M)]
    moves, lines = _contact_moves(run)
    ended = (res.get("program_result") or {}).get("ended") or next((l[9:] for l in reversed(text.splitlines()) if l.startswith("# ended: ")), None)
    meta = dict(name=e["name"], run=run.name, when=run.name[:15], setup=e.get("setup", ""), contact=e.get("contact", ""),
                verdict=("success" if v.get("success") else "FAILED") if v else "NO VERDICT (the operator stopped the episode)",
                operator_note=(v or {}).get("note", ""), operator_intervened=bool((v or {}).get("intervened")) or not v or bool(res.get("interrupted")),
                knocked_off=bool((v or {}).get("knocked_off")),
                robot_time_s=res.get("robot_time_s") or (times[-1] if times else None), program_ended=ended,
                moves_until_contact=moves, server_guarded_lines=lines, looks=looks, shown=shown, video_frames=videos,
                output_lines=len(text.splitlines()))
    if e.get("version") is not None:
        meta["version"] = e["version"]
    (out / "episode.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", default=".", help="what the manifest's run paths are relative to (default: here)")
    a = ap.parse_args()
    mf = Path(a.manifest).resolve()
    M = json.loads(mf.read_text())
    names = [e["name"] for e in M["episodes"]]
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[A-Za-z0-9]+", n) for n in names):
        raise SystemExit("episode names: distinct, letters and digits only")
    (mf.parent / "rig.md").write_text(M["rig"].strip() + "\n")
    for d in mf.parent.iterdir():                                   # (only the manifest's episodes are in a pack)
        if d.is_dir() and d.name not in names and (d / "episode.json").exists():
            shutil.rmtree(d)
    for e in M["episodes"]:
        m = episode(e, mf.parent, Path(a.root).resolve())
        print(f"{m['name']}: {m['run']}: {m['verdict']} ({m['operator_note'][:60]}); {len(m['looks'])} looks, {len(m['shown'])} shown, "
              f"{sum(len(x) for x in m['video_frames'].values())} video frames, {len(m['moves_until_contact'])} moves until contact "
              f"({len(m['server_guarded_lines'])} guarded lines)")
    (mf.parent / "pack.json").write_text(json.dumps(dict(episodes=names), indent=1) + "\n")


if __name__ == "__main__":
    main()
