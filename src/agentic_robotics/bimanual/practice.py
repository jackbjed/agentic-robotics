"""Run a program run(robot, task) on episodes of a bimanual task inside the program sandbox (docs/bimanual.md): what a
development session (improve.py) practises with, and how its result is evaluated.

    python -m agentic_robotics.bimanual.practice suite --env sim_bimanual_grippers --task insert_cylinder \\
           --program prog.py --seeds 0-2,4-10 --out runs/bimanual/x [--jobs 5] [--segmenter sam3] [--trace] [--video]

At most SLOTS sandboxed episodes run at the same time on the machine, whoever starts them (each renders: more fail).

Each episode is one process in the sandbox (execution/sandbox.py: no network, no home directory, the platform read
only, one writable directory); the GPU's device nodes are passed through, because the simulation renders in that
process. There the program gets a ProgramRobot: the documented calls of api.Robot and show(), nothing else, and it
is refused if it breaks the rules of check_program (imports, file access, private attributes). robot.segment reaches
SAM3 through a Unix socket served by the host (SegmenterBridge).

An episode's directory gets result.json (the judge's verdict on the TRUE final state, how the program ended, the
objects' true positions at the start and the end), output.txt (what the program printed, its traceback; with
--trace also one "#" line per robot command with the true positions then), shown/ (the images of robot.show),
final.jpg and, with --video, video.mp4. `suite` adds summary.json.

A development session's practice episodes (never an evaluation) also run with `practice`: the program can then read
the simulation's TRUE state (robot.truth(), robot.judge(); both give None otherwise), and with a scene of the
session's own (scenes.py; the episode's scene.json) in place of the task's scene for the seed.

(`practice episode ...` is the one episode, in this process: what the sandbox runs.)
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import glob
import io
import json
import linecache
import re
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .api import TimeUp
from .core import ARMS, scene_module
from .tasks import TASKS, Episode

IMPORTS = ("math", "numpy", "scipy", "cv2", "itertools", "functools", "collections", "dataclasses", "typing", "heapq",
           "bisect", "statistics", "random", "time", "copy", "enum", "operator", "numbers", "abc", "__future__")
_NAMES = {"eval", "exec", "compile", "open", "__import__", "input", "breakpoint", "globals", "locals", "vars", "exit",
          "quit", "memoryview", "__builtins__", "getattr", "setattr", "delattr"}
_ATTRS = {"save", "savez", "savez_compressed", "savetxt", "load", "loadtxt", "genfromtxt", "fromfile", "tofile", "memmap",
          "imread", "imwrite", "imshow", "waitKey", "VideoCapture", "VideoWriter", "FileStorage", "ctypeslib", "DataSource"}
RULES = ("Imports: only " + ", ".join(i for i in IMPORTS if i != "__future__") + " (and their submodules). No eval, exec, "
         "open, getattr or file access (numpy's and OpenCV's file functions included), and no attribute whose name "
         "starts with an underscore except on self.")
# what the TRUE positions of truth() are, as told to a developer (improve.py)
POINTS = {"insert_cylinder": ": cylinder is the centre of the cylinder's bottom face, stick_top the centre of the stick's top face, "
                             "stick_stand the centre of the board's underside",
          "insert_varied": ": cylinder is the centre of the cylinder's bottom face, stick_top the centre of the stick's top face, "
                           "stick_stand the centre of the board's underside, distractor_k the centre of that object's underside",
          "open_bottle": ": bottle is the centre of the bottle's bottom, cap the cap's centre"}
SHOWN = 12                                                      # images of robot.show kept per episode
WALL_S = 1500.0                                                 # an episode's process is stopped after this
SLOTS = 14                                                      # sandboxed episodes at the same time on this machine (each renders
                                                                # with EGL: beyond about 16 at once, framebuffers fail)


def check_program(code):
    """-> the reasons this program is not run (none: []): it must parse, define run(robot, task) and keep RULES."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"line {e.lineno}: syntax error: {e.msg}"]
    bad = []
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        bad.append("it does not define run(robot, task)")
    for n in ast.walk(tree):
        at = f"line {getattr(n, 'lineno', '?')}: "
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
            if isinstance(n, ast.ImportFrom) and n.level:
                bad.append(at + "relative import")
            bad += [at + f"import of {m}" for m in mods if m.split(".")[0] not in IMPORTS]
        elif isinstance(n, ast.Name) and n.id in _NAMES:
            bad.append(at + f"{n.id} is not allowed")
        elif isinstance(n, ast.Attribute):
            own = isinstance(n.value, ast.Name) and n.value.id in ("self", "cls")
            if n.attr in _ATTRS or (n.attr.startswith("_") and not own and n.attr != "__init__"):
                bad.append(at + f".{n.attr} is not allowed")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(r"__\w+__", n.value):
            bad.append(at + f"the string {n.value!r} is not allowed")
    return bad[:12]


# ---------------------------------------------------------------------- inside the sandbox: one episode
def truth(ep):
    """The TRUE positions (mm) of the episode's objects: each object's reference point (the cylinder's and the
    bottle's: the centre of the bottom), the stick's top, the bottle's cap."""
    S = ep.S
    if ep.scene is not None and hasattr(scene_module(ep.scene), "truth"):
        return scene_module(ep.scene).truth(ep)
    out = {n: _mm(S.object_pose(n)[0]) for n in ep.layout}
    if "stick_stand" in out:
        out["stick_top"] = _mm(S.stick_top()[0])
    if "bottle" in out:
        out["cap"] = _mm(S.cap_pose()[0]); out["cap_unscrewed_deg"] = round(S.cap_angle(), 1)
    return out


def _mm(p):
    return [round(float(v) * 1000, 1) for v in p]


def _plain(v):
    return json.loads(json.dumps(v))


def _fmt(v):
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, (bool, np.bool_)) or v is None:
        return str(v)
    if isinstance(v, (list, tuple)) and v and all(isinstance(x, str) for x in v):
        return "[" + ", ".join(v) + "]"
    try:
        a = np.asarray(v, float)
    except (TypeError, ValueError):
        return type(v).__name__
    if a.ndim == 0:
        return f"{round(float(a), 2):g}"
    if a.size <= 4:
        return "(" + ", ".join(f"{round(float(x), 2):g}" for x in a.ravel()) + ")"
    return f"<{'x'.join(map(str, a.shape))}>"


def _result(r):
    if isinstance(r, dict) and "done" in r:
        return "done" if r["done"] else ("stopped: " if r["moved"] else "REFUSED: ") + str(r["why"])[:90]
    if isinstance(r, (int, float)):
        return f"{r:g}"
    if isinstance(r, list):
        return f"{len(r)} masks" + (f", best score {r[0][1]:.2f}" if r else "")
    if isinstance(r, dict):
        return ", ".join(f"{k}: {_fmt(v)}" if not isinstance(v, (dict, list)) or len(v) <= 4 else str(k) for k, v in r.items())[:90]
    return ""


class ProgramRobot:
    """What a program gets of the robot: the documented calls of api.Robot and show(), nothing else. With a trace
    file, every command is logged there with its result, the arm's TRUE tool position and the TRUE position of
    every object that moved since it was last logged (read from the simulation's state: no sensor is read, so a
    traced episode is the untraced one). practice: truth() and judge() give the simulation's TRUE state (a session's
    practice; in an evaluation they give None)."""

    def __init__(self, ep, out, trace=None, practice=False):
        robot, S = ep.robot, ep.S
        names = ["look", "state", "move", "turn", "home", "wait", "time_s"]
        names += ["set_gripper"] if robot.kind == "grippers" else ["set_joints", "fingertips"]
        names += ["segment"] if robot.segmenter is not None else []
        counts = dict(commands=0, shown=0)
        last = {}

        def moved():
            now = {n: v for n, v in truth(ep).items()
                   if n not in last or np.abs(np.atleast_1d(v) - np.atleast_1d(last[n])).max() > 0.5}
            last.update(now)
            return " ".join(f"{n} {_fmt(v)}" for n, v in now.items())
        if trace is not None:
            trace.write(f"# TRUE positions at the start (mm): {moved()}\n")

        def log(name, a, k, r):
            arm = a[0] if a and isinstance(a[0], str) and a[0] in ARMS else None
            args = ", ".join([_fmt(x) for x in a if not hasattr(x, "rgb")] + [f"{q}={_fmt(v)}" for q, v in k.items()])
            tool = ""
            if arm:
                tool = f" | {arm} tool {_fmt(_mm(S.tool_pose(ARMS[arm])[0]))}"
                if robot.kind == "grippers":
                    tool += f" open {max(S.width(ARMS[arm]), 0.0) * 1000:.1f}"
            tr = moved()
            trace.write(f"# t={robot.time_s():6.1f} {name}({args}) -> {_result(r)}{tool}{' | NOW ' + tr if tr else ''}\n")

        def wrap(name):
            fn = getattr(robot, name)

            def call(*a, **k):
                r = fn(*a, **k)
                if name not in ("state", "time_s"):
                    counts["commands"] += 1
                    if trace is not None:
                        log(name, a, k, r)
                return r
            call.__name__ = name
            return call

        def show(label, image):
            """Hand an image to the developer (kept in the episode's shown/; the first SHOWN of an episode)."""
            import cv2
            if counts["shown"] >= SHOWN:
                return False
            im = np.asarray(image)
            if im.dtype == bool:
                im = im.astype(np.uint8) * 255
            im = np.clip(np.nan_to_num(im.astype(float)), 0, 255).astype(np.uint8)
            if im.ndim == 2:
                im = np.dstack([im] * 3)
            if im.ndim != 3 or im.shape[2] != 3 or min(im.shape[:2]) < 2:
                raise ValueError("show(label, image): image must be H x W x 3 (RGB) or H x W")
            sc = 960 / max(im.shape[:2])
            if sc < 1:
                im = cv2.resize(im, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
            d = Path(out) / "shown"; d.mkdir(exist_ok=True)
            tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label))[:40]
            cv2.imwrite(str(d / f"{counts['shown']:02d}_{tag}.png"), im[..., ::-1])
            counts["shown"] += 1
            if trace is not None:
                trace.write(f"# t={robot.time_s():6.1f} show({str(label)[:40]!r})\n")
            return True

        for n in names:
            object.__setattr__(self, n, wrap(n))
        object.__setattr__(self, "show", show)
        object.__setattr__(self, "truth", lambda: truth(ep) if practice else None)
        object.__setattr__(self, "judge", lambda: _plain(ep.task.judge(S)) if practice else None)
        object.__setattr__(self, "counts", lambda: dict(counts))

    def __setattr__(self, k, v):
        raise AttributeError("robot has no settable attributes")


class SocketSegmenter:
    """perception.sam3 from inside the sandbox: requests over the host's Unix socket (SegmenterBridge)."""

    def __init__(self, path):
        self.path = str(path)

    def _ask(self, **arrays):
        buf = io.BytesIO(); np.savez(buf, **arrays)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(180); s.connect(self.path)
            s.sendall(struct.pack(">Q", buf.getbuffer().nbytes) + buf.getvalue())
            z = np.load(io.BytesIO(_recv(s, struct.unpack(">Q", _recv(s, 8))[0])))
        if "error" in z:
            raise RuntimeError(f"segmentation service: {str(z['error'])}")
        return [(m.astype(bool), float(c)) for m, c in zip(z["masks"], z["scores"])]

    def segment(self, rgb, phrase):
        return self._ask(op="text", image=np.ascontiguousarray(rgb), phrase=str(phrase))

    def segment_points(self, rgb, pixels, labels):
        return self._ask(op="points", image=np.ascontiguousarray(rgb), points=np.asarray(pixels, float), labels=np.asarray(labels, int))


def _recv(s, n):
    b = bytearray()
    while len(b) < n:
        c = s.recv(min(1 << 20, n - len(b)))
        if not c:
            raise ConnectionError("socket closed")
        b += c
    return bytes(b)


def episode(env, task, seed, program, out, sensors="d415", trace=False, seg_socket=None, video=False, time_limit_s=None,
            scene=None, practice=False):
    """One episode of the program, in this process -> the result (also out/result.json). scene: a scene (scenes.py) or
    its JSON file, in place of the task's own for the seed."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    code = Path(program).read_text()
    t0 = time.time()
    if scene is not None and not isinstance(scene, dict):
        scene = json.loads(Path(scene).read_text())
    ep = Episode(env, task, seed=seed, sensors=sensors, record=video, time_limit_s=time_limit_s,
                 segmenter=SocketSegmenter(seg_socket) if seg_socket else None, scene=scene)
    start = truth(ep)
    bad = check_program(code)
    with open(out / "output.txt", "w", buffering=1) as log:
        robot = ProgramRobot(ep, out, log if trace else None, practice=practice)
        if bad:
            how = "refused: " + "; ".join(bad)
        else:
            linecache.cache["program.py"] = (len(code), None, code.splitlines(True), "program.py")
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    ns = {"__name__": "program"}
                    exec(compile(code, "program.py", "exec"), ns)
                    ns["run"](robot, ep.text)
                    how = "returned"
                except TimeUp as e:
                    how = f"time: {e}"
                except (Exception, SystemExit) as e:                # the program's own failure: the scene is judged as it is
                    traceback.print_exc(limit=-6)
                    how = f"error: {type(e).__name__}: {str(e)[:200]}"
    c = robot.counts()
    v = dict(ep.finish(), mode="program", ended=how, sensors=sensors, commands=c["commands"], shown=c["shown"],
             true_start_mm=start, true_end_mm=truth(ep), wall_s=round(time.time() - t0, 1))
    if ep.scene is not None:
        v["scene"] = ep.scene
    import cv2
    cv2.imwrite(str(out / "final.jpg"), ep.S.render_rec()[..., ::-1])
    if video:
        ep.S.write_video(out / "video.mp4")
    (out / "result.json").write_text(json.dumps(v, indent=1) + "\n")
    return v


# ---------------------------------------------------------------------- the host
class SegmenterBridge:
    """SAM3 (perception/sam3.py, our servers) for sandboxed episodes: a Unix socket in a directory of its own, which
    is bound into each sandbox."""

    def __init__(self):
        from ..perception import sam3
        self.dir = Path(tempfile.mkdtemp(prefix="ar-"))            # (short path: Unix socket names are <= 107 bytes)
        self.path = self.dir / "sam3.sock"

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    z = np.load(io.BytesIO(_recv(self.request, struct.unpack(">Q", _recv(self.request, 8))[0])))
                    if str(z["op"]) == "text":
                        r = sam3.segment(z["image"], str(z["phrase"]))
                    else:
                        r = sam3.segment_points(z["image"], z["points"], z["labels"])
                    h, w = z["image"].shape[:2]
                    rep = dict(masks=np.array([m for m, _ in r], bool).reshape(len(r), h, w), scores=np.array([c for _, c in r], float))
                except Exception as e:                              # the service is down, a bad request
                    rep = dict(error=f"{type(e).__name__}: {str(e)[:200]}")
                buf = io.BytesIO(); np.savez(buf, **rep)
                with contextlib.suppress(OSError):
                    self.request.sendall(struct.pack(">Q", buf.getbuffer().nbytes) + buf.getvalue())

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
        self.server = Server(str(self.path), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)


@contextlib.contextmanager
def _slot():
    """One of the machine's SLOTS for a rendering episode (file locks, so that it holds across processes): waits for one."""
    import fcntl
    from .. import paths
    d = paths.ROOT / "runs" / ".slots"
    d.mkdir(parents=True, exist_ok=True)
    while True:
        for k in range(SLOTS):
            f = open(d / f"{k}.lock", "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                continue
            try:
                yield
            finally:
                f.close()
            return
        time.sleep(0.5)


def run_sandboxed(env, task, seed, program, out, sensors="d415", trace=False, bridge=None, video=False, time_limit_s=None,
                  wall_s=WALL_S, scene=None, practice=False):
    """One episode of the program in the sandbox -> its result (dict(ok=False, ended=...) if the process gave none).
    scene: a scene dict (kept as out/scene.json) in place of the task's own; practice: see the module's text."""
    from .. import paths
    from ..execution import sandbox
    program, out = Path(program).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "agentic_robotics.bimanual.practice", "episode", "--env", env, "--task", task, "--seed", str(seed),
           "--program", str(program), "--out", str(out), "--sensors", sensors]
    cmd += (["--trace"] if trace else []) + (["--video"] if video else [])
    cmd += ["--time-limit", str(time_limit_s)] if time_limit_s else []
    cmd += ["--segmenter-socket", str(bridge.path)] if bridge is not None else []
    cmd += ["--practice"] if practice else []
    if scene is not None:
        (out / "scene.json").write_text(json.dumps(scene) + "\n")
        cmd += ["--scene", str(out / "scene.json")]
    devices = [d for d in sorted(glob.glob("/dev/nvidia*")) + ["/dev/dri"] if Path(d).exists()]
    full = sandbox.program_cmd(cmd, program.parent, out, bridge.dir if bridge is not None else out, devices=devices,
                               extra_env={"MUJOCO_GL": "egl", "PYOPENGL_PLATFORM": "egl", "PYTHONPATH": str(paths.ROOT / "src")})
    why = ""
    try:
        with _slot():
            r = subprocess.run(full, capture_output=True, text=True, timeout=wall_s)
        if r.returncode != 0:
            why = f"the episode's process failed ({r.returncode}): {r.stderr.strip()[-400:]}"
    except subprocess.TimeoutExpired:
        why = f"stopped after {wall_s:.0f} s of wall time (a loop without robot commands?)"
    if (out / "result.json").exists():
        return json.loads((out / "result.json").read_text())
    return dict(ok=False, task=task, env=env, seed=int(seed), ended=why or "no result", robot_s=None)


def suite(env, task, seeds, program, out, jobs=5, segmenter=False, log=print, **kw):
    """The program on several episodes (jobs at a time) -> dict(successes, episodes, robot_s..., results)."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    bridge = SegmenterBridge() if segmenter else None
    try:
        with ThreadPoolExecutor(max(1, int(jobs))) as ex:
            res = list(ex.map(lambda s: run_sandboxed(env, task, s, program, out / f"s{s}", bridge=bridge, **kw), seeds))
    finally:
        if bridge is not None:
            bridge.close()
    for r in res:
        log(f"seed {r['seed']:4d}  {'ok  ' if r['ok'] else 'FAIL'}  {r['robot_s'] if r['robot_s'] is not None else '-':>6} s  {str(r['ended'])[:100]}")
    t = [r["robot_s"] for r in res if r["robot_s"] is not None]
    ts = [r["robot_s"] for r in res if r["ok"]]
    v = dict(task=task, env=env, program=str(program), seeds=list(seeds), successes=sum(bool(r["ok"]) for r in res), episodes=len(res),
             robot_s_mean=round(float(np.mean(t)), 1) if t else None, robot_s_mean_successes=round(float(np.mean(ts)), 1) if ts else None,
             results=res)
    (out / "summary.json").write_text(json.dumps(v, indent=1) + "\n")
    return v


def parse_seeds(text):
    """"0-2,4,7-9" -> [0, 1, 2, 4, 7, 8, 9]"""
    out = []
    for part in str(text).split(","):
        a, _, b = part.strip().partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("episode", "suite"):
        p = sub.add_parser(name)
        p.add_argument("--env", required=True, choices=["sim_bimanual_grippers", "sim_bimanual_dexterous"])
        p.add_argument("--task", required=True, choices=list(TASKS))
        p.add_argument("--program", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--sensors", default="d415", choices=["d415", "ideal"])
        p.add_argument("--trace", action="store_true", help="log every robot command with the objects' true positions")
        p.add_argument("--video", action="store_true")
        p.add_argument("--time-limit", type=float, help="robot seconds (default: the task's)")
        if name == "episode":
            p.add_argument("--seed", type=int, required=True)
            p.add_argument("--segmenter-socket")
            p.add_argument("--scene", help="a scene's JSON file (scenes.py), in place of the task's own for the seed")
            p.add_argument("--practice", action="store_true", help="robot.truth() and robot.judge() give the TRUE state")
        else:
            p.add_argument("--seeds", required=True, help='e.g. "0-2,4-10"')
            p.add_argument("--jobs", type=int, default=5)
            p.add_argument("--segmenter", choices=["sam3"], help="robot.segment() through the SAM3 service")
    a = ap.parse_args(argv)
    if a.cmd == "episode":
        v = episode(a.env, a.task, a.seed, a.program, a.out, sensors=a.sensors, trace=a.trace, seg_socket=a.segmenter_socket,
                    video=a.video, time_limit_s=a.time_limit, scene=a.scene, practice=a.practice)
        print(json.dumps({k: v[k] for k in ("ok", "seed", "robot_s", "ended")}))
        return 0
    v = suite(a.env, a.task, parse_seeds(a.seeds), a.program, a.out, jobs=a.jobs, segmenter=bool(a.segmenter), sensors=a.sensors,
              trace=a.trace, video=a.video, time_limit_s=a.time_limit)
    print(json.dumps({k: v[k] for k in ("successes", "episodes", "robot_s_mean", "robot_s_mean_successes")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
