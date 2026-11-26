"""worlds_from_img: a from-nothing session with worlds (improve_cc.CodeSession, the Claude Code agent) that is given,
at its start, TWO REAL IMAGES of the real things: one frame of the real scene camera and one of the real wrist camera,
each with its depth. Nothing else differs from a from-nothing session: the same brief, the same starting point (no
program, no world, the simulated empty table), the same sub-commands, plus one that reads the two images' depth as
numbers. No text about the real things is given: what they are is in the images.

    bin/ar-worlds-from-img images --run real/results/<a live run recorded with --depth-fps> --out runs/bimanual/real_images/NAME
    bin/ar-worlds-from-img brief  --ask "..." --images runs/bimanual/real_images/NAME [--diff]
    bin/ar-worlds-from-img run    --ask "..." --images runs/bimanual/real_images/NAME --out runs/bimanual/NAME
                                  [--budget-usd 50] [--max-hours 6] [--episode-s 600] [--model opus] [--effort xhigh] [--resume]

`images` makes the folder the session is given (the operator runs it; the session never sees the run): the scene
camera's first recorded frame (the arm at its start pose) and the program's first wrist look, each as colour, depth
(16-bit mm, 0 = no depth), a depth picture and a no-depth picture, with images.json (the intrinsics, and for the wrist
image where the arm's tool was). The standard sessions (improve_cc.py run) are not changed by this module.
"""
import argparse
import csv
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import improve_cc
from .improve import text
from .improve_cc import CodeSession, EFFORT, MODEL, WORKSPACES, run_session

KIND = "worlds_from_img"
NAMES = ("scene", "left_wrist")
DEPTH_RANGE = {"scene": (600, 1600), "left_wrist": (450, 780)}     # mm: the colours of a depth picture
SCENE_K = (640.0, 480.0, 615.2572021484375, 615.3887329101562, 307.34124755859375, 237.158935546875)   # the rig's scene D415

# The brief's additions (everything else is improve_cc's brief of a from-nothing session, word for word).
SUB = """read_image_depth --image scene|left_wrist [--u0 N] [--v0 N] [--u1 N] [--v1 N]
                              the depth of one of the two REAL images (below), as numbers: the values (mm along the
                              camera's axis, as a Frame's depth; 0 = no depth) on a grid over a window of pixels,
                              and the window's statistics; with the camera's intrinsics
"""
PARA = """
Two REAL images. The real table was photographed once by the real robot's two cameras, with real things on it. The
images are in pictures/real/ of your workspace, for each of scene and left_wrist: NAME.png (colour), NAME_depth.png
(its depth as a picture: near red, far blue, the scale on top; black = NO depth) and NAME_nodepth.png (the colour
image with the pixels that had no depth in magenta); read_image_depth gives the depth as numbers. The cameras are the
real ones that the simulated "scene" and "left_wrist" stand for (640 x 480 pixels; a real D415 gives no depth nearer
than about 275 mm). The scene camera stands where the simulated one stands.
{wrist}
These two images are all you have of the real things: nothing was measured for you, and you cannot take more.
"""
FIRST = ("\n\nThe two real images: [picture: pictures/real/scene.png] [picture: pictures/real/left_wrist.png] (and their "
         "_depth and _nodepth pictures beside them).")


def _depth_picture(dep, cam):
    import cv2
    lo, hi = DEPTH_RANGE[cam]
    x = np.clip((dep.astype(np.float32) - lo) / (hi - lo), 0, 1)
    out = cv2.applyColorMap((255 * (1 - x)).astype(np.uint8), cv2.COLORMAP_TURBO)
    out[dep == 0] = 0
    out[:14] = cv2.applyColorMap(np.tile(np.linspace(255, 0, out.shape[1]).astype(np.uint8), (14, 1)), cv2.COLORMAP_TURBO)
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        cv2.putText(out, f"{v:.0f} mm", (min(int(k / 4 * (out.shape[1] - 1)), out.shape[1] - 62), 28), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return out


def make_images(run, out):
    """A live run recorded with real/run ... --depth-fps -> the folder a worlds_from_img session is given."""
    import cv2
    run, out = Path(run), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    v = run / "video"
    # the scene camera: the recording's first frame (the arm still at its start pose), colour and depth
    cap = cv2.VideoCapture(str(v / "scene.mp4")); ok, rgb = cap.read(); cap.release()
    if not ok or not (v / "scene_depth.mkv").exists():
        raise SystemExit(f"{run}: no scene recording with depth (real/run ... --depth-fps 1)")
    H, W = rgb.shape[:2]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(v / "scene_depth.mkv"), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray16le", "-"],
                         capture_output=True, check=True).stdout
    dep = np.frombuffer(raw, np.uint16).reshape(H, W).copy()
    t = [float(r["t_run"]) for r in csv.DictReader(open(v / "scene_depth.csv"))][0]
    metas = [dict(name="scene", camera="scene", K=list(SCENE_K), run_time_s=round(t, 2))]
    todo = [("scene", rgb, dep)]
    # the wrist camera: the program's first look (frames/: colour, 16-bit depth, the intrinsics), and where the tool was
    j = sorted((run / "frames").glob("*_L_wrist.json"))[0]
    meta = json.loads(j.read_text())
    wrgb = cv2.imread(str(j.with_name(j.stem + "_rgb.png"))); wdep = cv2.imread(str(j.with_name(j.stem + "_depth.png")), cv2.IMREAD_UNCHANGED)
    look = next(l for l in (run / "program" / "output.txt").read_text(errors="replace").splitlines() if l.startswith("# t=") and "look(" in l)
    tool = [float(x) for x in re.search(r"left tool \(([-\d., ]+)\)", look).group(1).split(",")]
    moves = [l for l in (run / "program" / "output.txt").read_text(errors="replace").split(look)[0].splitlines() if l.startswith("# t=") and "move('left'" in l]
    d = re.search(r"move\('left', \([^)]*\), \(([^)]*)\), \(([^)]*)\)", moves[-1]) if moves else None
    metas.append(dict(name="left_wrist", camera="left_wrist", K=meta["K"], tool_mm=tool,
                      approach_dir=[float(x) for x in d.group(1).split(",")] if d else None,
                      closing_dir=[float(x) for x in d.group(2).split(",")] if d else None))
    todo.append(("left_wrist", wrgb, wdep))
    for name, rgb, dep in todo:
        nod = rgb.copy(); nod[dep == 0] = (0.35 * nod[dep == 0] + 0.65 * np.array([255, 0, 255])).astype(np.uint8)
        cv2.imwrite(str(out / f"{name}.png"), rgb)
        cv2.imwrite(str(out / f"{name}_depth16.png"), dep)                                # (16-bit, mm, 0 = no depth: for read_image_depth)
        cv2.imwrite(str(out / f"{name}_depth.png"), _depth_picture(dep, name))
        cv2.imwrite(str(out / f"{name}_nodepth.png"), nod)
    (out / "images.json").write_text(json.dumps(dict(run=run.name, images=metas), indent=1) + "\n")
    return metas


class ImgSession(CodeSession):
    """improve_cc.CodeSession of a from-nothing task, given two real images (the folder `images`)."""

    def __init__(self, out, ask, budget_usd, images=None, **kw):
        sf = Path(out).resolve() / "state.json"
        st0 = json.loads(sf.read_text()) if sf.exists() else {}
        self.img_dir = Path(st0.get("images") or images).resolve()
        self.img = {m["name"]: m for m in json.loads((self.img_dir / "images.json").read_text())["images"]}
        if sorted(self.img) != sorted(NAMES):
            raise ValueError(f"{self.img_dir}: images.json must hold {' and '.join(NAMES)}")
        if kw.get("real") or kw.get("continue_from"):
            raise ValueError("a worlds_from_img session starts from nothing: no --continue-from, no --real-feedback")
        super().__init__(out, ask, budget_usd, **kw)
        for key, now in (("kind", KIND), ("images", str(self.img_dir))):
            if self.state.setdefault(key, now) != now:
                raise ValueError(f"this session was started with {key} = {self.state[key]!r}")
        self._real_pictures()
        self._keep()

    def _wrist(self):
        m = self.img["left_wrist"]
        p, a, c = m["tool_mm"], m.get("approach_dir"), m.get("closing_dir")
        how = ""
        if a and c:
            how = (", the hand pointing " + ("straight down" if tuple(round(x) for x in a) == (0, 0, -1) else f"along ({a[0]:g}, {a[1]:g}, {a[2]:g})")
                   + f", its closing direction along ({c[0]:g}, {c[1]:g}, {c[2]:g})")
        return f"The wrist image was taken with the arm's tool at ({p[0]:.0f}, {p[1]:.0f}, {p[2]:.0f}) mm{how}."

    def _instructions(self, kind):
        t = super()._instructions(kind)
        a = "                              name the version that is your result\n"
        b = "\nNo real episode of this task exists yet"
        if t.count(a) != 1 or t.count(b) != 1:
            raise ValueError("the brief has changed: worlds_from_img's two places are not found")
        t = t.replace(a, a + SUB)
        return t.replace(b, PARA.format(wrist=self._wrist()) + b)

    def base_instructions(self):
        return CodeSession._instructions(self, "grippers")

    def _first(self):
        inp, images = super()._first()
        inp[0]["content"][0]["text"] = inp[0]["content"][0]["text"].rstrip("\n") + FIRST + "\n"
        return inp, images

    def _real_pictures(self):
        d = self.ws / "pictures" / "real"
        if self.ws.exists() and not d.exists():
            d.mkdir(parents=True)
            for n in NAMES:
                for suffix in ("", "_depth", "_nodepth"):
                    shutil.copyfile(self.img_dir / f"{n}{suffix}.png", d / f"{n}{suffix}.png")

    def tool(self, name, a):
        if name != "read_image_depth":
            return super().tool(name, a)
        try:
            return self._read_image_depth(a)
        except (KeyError, ValueError, TypeError, IndexError) as e:
            return [text(f"bad call: {str(e) or type(e).__name__}")]

    def _read_image_depth(self, a):
        import cv2
        from .api import depth_report
        name = str(a.get("image", "")).replace("_nodepth", "").replace("_depth", "").replace(".png", "")
        if name not in self.img:
            raise ValueError(f"no image {name!r}: there are {', '.join(NAMES)}")
        dep = cv2.imread(str(self.img_dir / f"{name}_depth16.png"), cv2.IMREAD_UNCHANGED)
        H, W = dep.shape
        u0, v0 = max(0, int(a.get("u0") or 0)), max(0, int(a.get("v0") or 0))
        u1 = min(W - 1, int(a["u1"]) if a.get("u1") is not None else W - 1); v1 = min(H - 1, int(a["v1"]) if a.get("v1") is not None else H - 1)
        if u1 < u0 or v1 < v0:
            raise ValueError("the window: u0 <= u1 and v0 <= v1")
        win = dep[v0:v1 + 1, u0:u1 + 1]; ok = win[win > 0]
        us = sorted({int(round(x)) for x in np.linspace(u0, u1, min(24, u1 - u0 + 1))}); vs = sorted({int(round(x)) for x in np.linspace(v0, v1, min(18, v1 - v0 + 1))})
        K = self.img[name].get("K") or []
        head = (f"the real image {name}: depth in mm along the camera's axis (0 = no depth); the image is {W} x {H}"
                + (f", fx {K[2]:.1f}, fy {K[3]:.1f}, cx {K[4]:.1f}, cy {K[5]:.1f}" if len(K) == 6 else "") + f".\nWindow u {u0}..{u1}, v {v0}..{v1}: "
                + (f"{100 * (1 - len(ok) / win.size):.0f}% of its pixels without depth; of the others: nearest {int(ok.min())}, 5% {int(np.percentile(ok, 5))}, "
                   f"median {int(np.median(ok))}, 95% {int(np.percentile(ok, 95))}, farthest {int(ok.max())} mm." if len(ok) else "no pixel has depth."))
        grid = "\n".join([" v \\ u " + "".join(f"{u:5d}" for u in us)] + [f"{v:6d} " + "".join(f"{int(dep[v, u]):5d}" for u in us) for v in vs])
        rep = depth_report(np.where(dep > 0, dep.astype(float), np.nan))
        return [text(head + "\n" + grid + "\nThe whole image's depth report (what frame.depth_report() gives for a simulated frame): " + json.dumps(rep))]

    def finish(self, agent=None):
        how = super().finish(agent)
        f = self.dir / "result.json"
        r = json.loads(f.read_text())
        r["kind"], r["images"] = KIND, str(self.img_dir)
        f.write_text(json.dumps(r, indent=1) + "\n")
        return how


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("images"); p.add_argument("--run", required=True); p.add_argument("--out", required=True)
    for name in ("run", "brief"):
        p = sub.add_parser(name)
        p.add_argument("--ask", help="the skill to make: the task's text")
        p.add_argument("--images", help="the folder of the two real images (the `images` command makes it)")
        p.add_argument("--out", required=name == "run", help="the session's record directory (runs/bimanual/NAME)")
        p.add_argument("--budget-usd", type=float, default=50.0); p.add_argument("--episode-s", type=float)
        p.add_argument("--max-hours", type=float, default=6.0)
        p.add_argument("--model", default=MODEL); p.add_argument("--effort", default=EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
        p.add_argument("--no-segmenter", action="store_true")
        p.add_argument("--root", help=f"the session's directory outside the checkouts (default {WORKSPACES}/NAME)")
        p.add_argument("--resume", action="store_true"); p.add_argument("--agent-cmd", help="tests: a stand-in for the claude CLI")
        p.add_argument("--account"); p.add_argument("--diff", action="store_true", help="brief: the diff against a from-nothing session's brief")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    log = lambda *x: print(*x, flush=True)
    if a.cmd == "images":
        for m in make_images(a.run, a.out):
            print(json.dumps(m))
        return 0
    if a.cmd == "brief":
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            s = ImgSession(Path(t) / "out" / (Path(a.out).name if a.out else "NAME"), a.ask, a.budget_usd, images=a.images, root=Path(t) / "root" / "NAME",
                           episode_s=a.episode_s, segmenter=not a.no_segmenter, log=lambda *x: None)
            mine, theirs = s.instr.replace(str(s.cmd), "PRACTICE"), s.base_instructions().replace(str(s.cmd), "PRACTICE")
            if a.diff:
                sys.stdout.writelines(difflib.unified_diff(theirs.splitlines(True), mine.splitlines(True), "from_nothing/instructions.md", f"{KIND}/instructions.md"))
            else:
                print(mine + "\n\n===== THE FIRST MESSAGE =====\n\n" + s.first_message())
        return 0
    out = Path(a.out)
    if (out / "state.json").exists() != a.resume:
        ap.error("--out holds a session: --resume goes on with it" if not a.resume else "--resume: no session in --out")
    if a.resume:
        st0 = json.loads((out / "state.json").read_text())
        s = ImgSession(out, st0.get("ask"), st0.get("budget_usd") or a.budget_usd, episode_s=st0.get("episode_s"), segmenter=bool(st0.get("segmenter", True)),
                       max_hours=a.max_hours, log=log)
    else:
        if not a.ask or not a.images:
            ap.error("--ask and --images")
        s = ImgSession(out, a.ask, a.budget_usd, images=a.images, root=a.root, episode_s=a.episode_s, segmenter=not a.no_segmenter, max_hours=a.max_hours,
                       model=a.model, effort=a.effort, log=log)
        s.state.update(budget_usd=float(a.budget_usd), segmenter=not a.no_segmenter, account=a.account)
        s._keep()
    env = None
    if a.agent_cmd:
        import os
        env = dict(PATH=os.environ.get("PATH", ""), HOME=str(s.dir / "claude" / "home"), AR_CC_SESSION=str(s.dir), AR_CC_PRACTICE=s.cmd,
                   **{k: v for k, v in os.environ.items() if k.startswith("FAKE_")})
    run_session(s, s.state["model"], s.effort, resume=a.resume, claude=a.agent_cmd, env=env, log=log)
    print(json.dumps(dict(ended=s.state["ended"], stopped=None if s.state["ended"] else "unfinished", submitted=s.state["submitted"],
                          turns=s.state["turn"], spent_usd=round(s.spent(), 4))))
    return 0 if s.state["ended"] else 1


if __name__ == "__main__":
    sys.exit(main())
