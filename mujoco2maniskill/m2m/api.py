"""What a program gets: the project's robot API (docs/bimanual.md; bimanual/api.py Robot and Frame, practice.py
ProgramRobot and check_program), for the one-armed rig with grippers, on the ManiSkill rig (m2m/rig.py). Copied from the
project so that this folder runs without its MuJoCo code; the calls, units (MILLIMETRES and DEGREES, table frame),
results and refusal texts are the project's. No true object state passes through here."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import numpy as np

from .kin import grip_R
from .rig import GUARD_N, MIN_Z, SAFETY_N, Stop

SHOWN = 12
IMPORTS = ("math", "numpy", "scipy", "cv2", "itertools", "functools", "collections", "dataclasses", "typing", "heapq",
           "bisect", "statistics", "random", "time", "copy", "enum", "operator", "numbers", "abc", "__future__")
_NAMES = {"eval", "exec", "compile", "open", "__import__", "input", "breakpoint", "globals", "locals", "vars", "exit",
          "quit", "memoryview", "__builtins__", "getattr", "setattr", "delattr"}
_ATTRS = {"save", "savez", "savez_compressed", "savetxt", "load", "loadtxt", "genfromtxt", "fromfile", "tofile", "memmap",
          "imread", "imwrite", "imshow", "waitKey", "VideoCapture", "VideoWriter", "FileStorage", "ctypeslib", "DataSource"}
WORLD_FUNCTIONS = ("scene", "robot", "goal", "success")


class TimeUp(Exception):
    """The episode's robot time is spent: no further command runs."""


def check_program(code, needs=None):
    """(practice.check_program) -> the reasons this program is not run (none: [])."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"line {e.lineno}: syntax error: {e.msg}"]
    bad = []
    if needs:
        has = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in needs}
        if not has:
            bad.append("it defines none of scene(scene, rng), robot(robot, rng, kit), goal(scene, rng) and success(state)")
    elif not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
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


class Frame:
    """One camera frame. rgb (H, W, 3 uint8); depth (H, W) mm along the optical axis, nan = none; fx, fy, cx, cy;
    eye (mm) and R (3x3, camera to table frame; camera axes x right, y down, z forward): the camera's pose as the robot
    believes it."""

    def __init__(self, o):
        self._o = o
        self.camera, self.rgb, self.t = o["camera"], o["rgb"], o["t"]
        self.depth = o["depth"] * 1000.0
        self.width, self.height, self.fx, self.fy, self.cx, self.cy = o["K"]
        self.eye, self.R = o["eye"] * 1000.0, o["R"]

    def locate(self, u, v):
        o = self._o
        Wd, Hd, fx, fy, cx, cy = o["K"]
        ui, vi = int(round(float(u))), int(round(float(v)))
        if not (0 <= ui < Wd and 0 <= vi < Hd):
            return None
        win = o["depth"][max(vi - 3, 0):vi + 4, max(ui - 3, 0):ui + 4]; win = win[np.isfinite(win)]
        if len(win) < 5:
            return None
        z = float(np.median(win))
        return (o["eye"] + o["R"] @ np.array([(float(u) - cx) / fx * z, (float(v) - cy) / fy * z, z])) * 1000.0

    def points(self):
        v, u = np.mgrid[0:self.height, 0:self.width]
        z = self.depth
        C = np.dstack([(u - self.cx) / self.fx * z, (v - self.cy) / self.fy * z, z])
        return self.eye + C @ self.R.T

    def project(self, xyz):
        c = (np.atleast_2d(np.asarray(xyz, float)) - self.eye) @ self.R; z = c[:, 2]
        return np.c_[self.fx * c[:, 0] / z + self.cx, self.fy * c[:, 1] / z + self.cy], z

    def with_pixels(self, rgb=None, depth=None):
        o = dict(self._o)
        if rgb is not None:
            rgb = np.ascontiguousarray(np.asarray(rgb, np.uint8))
            if rgb.shape != self.rgb.shape:
                raise ValueError(f"rgb: {self.rgb.shape} uint8")
            o["rgb"] = rgb
        if depth is not None:
            depth = np.asarray(depth, float)
            if depth.shape != self.depth.shape:
                raise ValueError(f"depth: {self.depth.shape}, mm, nan where there is none")
            o["depth"] = np.where(np.isfinite(depth) & (depth > 0), depth, np.nan) / 1000.0
        return Frame(o)


def _refused(why):
    return dict(done=False, moved=False, why=why, curved=False)


def _mm(v):
    return [round(float(c) * 1000, 1) for c in v]


def _dir(v):
    return [round(float(c), 3) for c in v]


class Robot:
    """(bimanual/api.py Robot, one-armed scene, grippers.)"""
    CAMERAS = ("scene", "left_wrist")
    ARMS = ("left",)
    GUARD_N, SAFETY_N, MIN_DEPTH_MM = GUARD_N, SAFETY_N, MIN_Z * 1000
    kind = "grippers"

    def __init__(self, S, time_limit_s=None, segmenter=None):
        self.S, self.segmenter = S, segmenter
        self.t0, self.limit = float(S.t), time_limit_s
        self.frames = {}

    def time_s(self):
        return float(self.S.t) - self.t0

    def _time(self):
        if self.limit is not None and self.time_s() >= self.limit:
            raise TimeUp(f"the episode's robot time ({self.limit:.0f} s) is spent")

    def wait(self, seconds):
        self._time()
        self.S.run(float(np.clip(seconds, 0.0, 10.0)))

    def look(self, cameras=None):
        self._time()
        out = {}
        for c in (cameras or self.CAMERAS):
            if c not in self.CAMERAS:
                raise KeyError(f"camera must be one of {list(self.CAMERAS)}")
            out[c] = self.frames[c] = Frame(self.S.observe(c))
        return out

    def segment(self, frame, phrase):
        if self.segmenter is None:
            raise RuntimeError("no segmentation service in this episode")
        return self.segmenter.segment(frame.rgb, str(phrase))

    def state(self, arm):
        self._arm(arm)
        S = self.S
        p, R = S.tool_pose()
        f = [round(float(v), 1) for v in S.force_estimate()]
        return dict(tcp_mm=_mm(p), approach_dir=_dir(R[:, 2]), closing_dir=_dir(R[:, 1]),
                    opening_mm=round(max(S.width(), 0.0) * 1000, 1), commanded_mm=round(S.g_cmd * 2000, 1), force_n=f)

    def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
        self._arm(arm)
        xyz = np.asarray(xyz, float).reshape(3)
        if not np.all(np.isfinite(xyz)) or xyz[2] < 10.0:
            return _refused("the tool point may not go below 10 mm above the table")
        try:
            R = self._orient(dir_a, dir_b, self.S.tool_pose()[1])
        except ValueError as e:
            return _refused(str(e))
        return self._motion(lambda: self.S.move(xyz / 1000.0, R, guarded=bool(stop_on_contact)))

    def turn(self, arm, centre, axis, angle_deg):
        self._arm(arm)
        axis = np.asarray(axis, float).reshape(3); centre = np.asarray(centre, float).reshape(3)
        if not np.linalg.norm(axis) > 1e-6 or not abs(float(angle_deg)) <= 360:
            return _refused("give a non-zero axis and an angle within +-360 degrees")
        return self._motion(lambda: self.S.turn(centre / 1000.0, axis, float(angle_deg)))

    def home(self, arm):
        self._arm(arm)
        return self._motion(lambda: self.S.go_home())

    def set_gripper(self, arm, width_mm):
        self._arm(arm); self._time()
        return round(max(self.S.set_gripper(float(np.clip(width_mm, 0, 80)) / 1000.0), 0.0) * 1000, 1)

    def _arm(self, arm):
        if arm not in self.ARMS:
            raise KeyError(f'arm must be "{self.ARMS[0]}": this robot uses only its {self.ARMS[0]} arm')

    def _orient(self, a, b, cur=None):
        a = np.asarray(a, float).reshape(3); b = np.asarray(b, float).reshape(3)
        if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b)) and np.linalg.norm(a) > 1e-6):
            raise ValueError("give two non-zero directions")
        try:
            R = grip_R(a, b)
        except ValueError:
            raise ValueError("the closing direction is parallel to the approach direction")
        if cur is not None and float(R[:, 1] @ cur[:, 1]) < 0:
            R = grip_R(a, -np.asarray(b, float))
        return R

    def _motion(self, fn):
        self._time()
        self.S.last_path = "line"
        try:
            r = fn()
        except Stop as e:
            return _refused(str(e))
        return dict(done=r is None, moved=True, why=r or "", curved=self.S.last_path == "joint")


class WorldRobot:
    """(bimanual/api.py WorldRobot) The robot a program gets in a world: every call is passed on."""

    def __init__(self, robot):
        self.robot = robot

    def look(self, cameras=None):
        return self.robot.look(cameras)

    def state(self, arm):
        return self.robot.state(arm)

    def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
        return self.robot.move(arm, xyz, dir_a, dir_b, stop_on_contact)

    def turn(self, arm, centre, axis, angle_deg):
        return self.robot.turn(arm, centre, axis, angle_deg)

    def home(self, arm):
        return self.robot.home(arm)

    def wait(self, seconds):
        return self.robot.wait(seconds)

    def time_s(self):
        return self.robot.time_s()

    def set_gripper(self, arm, width_mm):
        return self.robot.set_gripper(arm, width_mm)

    def __getattr__(self, name):                                    # (segment, show, ask, truth, judge, the constants)
        return getattr(self.robot, name)


class WorldKit:
    Robot = WorldRobot

    @staticmethod
    def frame(f, rgb=None, depth=None):
        return f.with_pixels(rgb=rgb, depth=depth)


def _fmt(v):
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} {_fmt(x)}" for k, x in v.items()) + "}"
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


def _rgb8(image, what, side):
    import cv2
    im = np.asarray(image)
    if im.dtype == bool:
        im = im.astype(np.uint8) * 255
    im = np.clip(np.nan_to_num(im.astype(float)), 0, 255).astype(np.uint8)
    if im.ndim == 2:
        im = np.dstack([im] * 3)
    if im.ndim != 3 or im.shape[2] != 3 or min(im.shape[:2]) < 2:
        raise ValueError(f"{what}: image must be H x W x 3 (RGB) or H x W")
    sc = side / max(im.shape[:2])
    if sc < 1:
        im = cv2.resize(im, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(im)


def program_robot(robot, out, trace=None, truth=None):
    """(practice.ProgramRobot) What the program gets of a Robot: the documented calls and show(); every command logged
    to `trace` with its result, the arm's TRUE tool position and the TRUE positions of what moved (truth(): a callable
    giving {name: mm}; the log is the developer's, never the program's). truth() and judge() give None, as in an
    evaluation; ask() answers that there are no model calls."""
    S = robot.S
    counts = dict(commands=0, shown=0, asks=0)
    last = {}

    def moved():
        if truth is None:
            return ""
        now = {n: v for n, v in truth().items() if n not in last or np.abs(np.asarray(v) - np.asarray(last[n])).max() > 1.0}
        last.update(now)
        return " ".join(f"{n} {_fmt(v)}" for n, v in now.items())
    if trace is not None:
        trace.write(f"# TRUE positions at the start (mm): {moved()}\n")

    def log(name, a, k, r):
        args = ", ".join([_fmt(x) for x in a if not hasattr(x, "rgb")] + [f"{q}={_fmt(v)}" for q, v in k.items()])
        tool = f" | left tool {_fmt(_mm(S.tool_pose()[0]))} open {max(S.width(), 0.0) * 1000:.1f}"
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
        import cv2
        if counts["shown"] >= SHOWN:
            return False
        im = _rgb8(image, "show(label, image)", 960)
        d = Path(out) / "shown"; d.mkdir(exist_ok=True)
        tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label))[:40]
        cv2.imwrite(str(d / f"{counts['shown']:02d}_{tag}.png"), im[..., ::-1])
        counts["shown"] += 1
        if trace is not None:
            trace.write(f"# t={robot.time_s():6.1f} show({str(label)[:40]!r})\n")
        return True

    def ask(question, images=(), effort="low"):
        return dict(ok=False, why="no model calls in this run", calls_left=0)

    class ProgramRobot:
        CAMERAS, ARMS, kind = robot.CAMERAS, robot.ARMS, robot.kind
        GUARD_N, SAFETY_N, MIN_DEPTH_MM = robot.GUARD_N, robot.SAFETY_N, robot.MIN_DEPTH_MM

        def __setattr__(self, k, v):
            raise AttributeError("the robot object cannot be changed")
    names = ["look", "state", "move", "turn", "home", "wait", "time_s", "set_gripper"] + (["segment"] if robot.segmenter is not None else [])
    for name in names:
        setattr(ProgramRobot, name, staticmethod(wrap(name)))
    ProgramRobot.show = staticmethod(show); ProgramRobot.ask = staticmethod(ask)
    ProgramRobot.truth = staticmethod(lambda: None); ProgramRobot.judge = staticmethod(lambda: None)
    return ProgramRobot(), counts
