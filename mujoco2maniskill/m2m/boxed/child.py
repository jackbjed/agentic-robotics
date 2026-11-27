"""What runs INSIDE the sandbox: the agent-written code, with a robot (or a world state) that is nothing but a socket.

    python -I child.py program FD      a skill program: run(robot, task)
    python -I child.py world FD        a practice-world file: scene(scene, rng), success(state)

This process holds no simulator, no judge, no true state and no path of the experiment: everything it knows came through
the socket FD, and the only things it can ask for are the calls listed by its host."""
import builtins
import linecache
import os
import socket
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import wire  # noqa: E402
from frame import Frame  # noqa: E402


class TimeUp(Exception):
    """The episode's robot time is spent: no further command runs."""


def _raise(err):
    name, msg = str(err.get("type")), str(err.get("msg"))
    args = err.get("args") if isinstance(err.get("args"), list) else [msg]
    if name == "TimeUp":
        raise TimeUp(*args)
    cls = getattr(builtins, name, None)
    if isinstance(cls, type) and issubclass(cls, Exception):
        try:
            x = cls(*args)
        except Exception:
            x = None
        if x is not None:
            raise x
    raise RuntimeError(f"{name}: {msg}")


class _Out:
    """print() and tracebacks go to the host, in order with the robot's commands."""

    def __init__(self, ch):
        self.ch = ch

    def write(self, s):
        if s:
            self.ch.send({"op": "out", "text": str(s)})
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def program(ch):
    hello = ch.recv()
    code, task, consts, names = hello["code"], hello["task"], hello["consts"], hello["names"]

    def remote(name):
        def call(*a, **k):
            ch.send({"op": "call", "name": name, "a": list(a), "k": dict(k)})
            r = ch.recv()
            if "err" in r:
                _raise(r["err"])
            return r["ok"]
        call.__name__ = name
        return call

    class ProgramRobot:
        def __setattr__(self, k, v):
            raise AttributeError("the robot object cannot be changed")
    for k, v in consts.items():
        setattr(ProgramRobot, k, v)
    for name in names:
        setattr(ProgramRobot, name, staticmethod(remote(name)))
    robot = ProgramRobot()
    sys.stdout = sys.stderr = _Out(ch)
    linecache.cache["program.py"] = (len(code), None, code.splitlines(True), "program.py")
    try:
        ns = {"__name__": "program"}
        exec(compile(code, "program.py", "exec"), ns)
        ns["run"](robot, task)
        how = "returned"
    except TimeUp as e:
        how = f"time: {e}"
    except wire.Closed:
        os._exit(3)
    except BaseException as e:                                      # (Exception, SystemExit, KeyboardInterrupt, ...)
        traceback.print_exc(limit=-6)
        how = f"error: {type(e).__name__}: {str(e)[:200]}"
    ch.send({"op": "end", "how": how})


class _State:
    """What a practice world's success(state) gets: the TRUE state of its own things, asked from the host."""

    def __init__(self, ch, names, start):
        self._ch = ch
        self.names, self.start = list(names), dict(start)

    def _ask(self, q, arg):
        self._ch.send({"op": "q", "q": q, "arg": arg})
        r = self._ch.recv()
        if "err" in r:
            _raise(r["err"])
        return r["ok"]

    def pos(self, name):
        return self._ask("pos", name)

    def rot(self, name):
        return self._ask("rot", name)

    def start_rot(self, name):
        return self._ask("start_rot", name)

    def touching(self, name):
        return self._ask("touching", name)

    def speed(self, name):
        return self._ask("speed", name)

    def held(self, name):
        return self._ask("held", name)

    def arm(self, side):
        return self._ask("arm", side)


def world(ch):
    import json
    hello = ch.recv()
    code = hello["code"]
    sys.stdout = sys.stderr = _Out(ch)
    linecache.cache["world.py"] = (len(code), None, code.splitlines(True), "world.py")
    ns = {"__name__": "world"}

    def failure(e, limit):
        return {"err": {"type": type(e).__name__, "msg": str(e), "tb": traceback.format_exc(limit=limit)}}
    try:
        exec(compile(code, "world.py", "exec"), ns)
        ch.send({"op": "ready", "has_success": bool(callable(ns.get("success"))), "has_scene": bool(callable(ns.get("scene")))})
    except BaseException as e:
        ch.send(dict(failure(e, -3), op="ready")); return
    while True:
        try:
            m = ch.recv()
        except wire.Closed:
            return
        try:
            if m["op"] == "scene":
                sc = ns["scene"](m["scene"], np.random.default_rng(int(m["seed"])))
                if isinstance(sc, dict):                           # (only what a scene dict is made of crosses back)
                    sc = json.loads(json.dumps({k: sc[k] for k in sc if isinstance(k, str)}, default=_plain))
                    ch.send({"op": "ret", "ok": {"dict": True, "json": json.dumps(sc)}})
                else:
                    ch.send({"op": "ret", "ok": {"dict": False, "json": "null"}})
            elif m["op"] == "success":
                c = ns["success"](_State(ch, m["names"], m["start"]))
                if isinstance(c, dict):
                    ch.send({"op": "ret", "ok": {"dict": True, "truth": bool(c.get("ok")), "json": json.dumps(c, default=str)}})
                else:
                    ch.send({"op": "ret", "ok": {"dict": False, "truth": bool(c), "json": "null"}})
            else:
                ch.send({"op": "ret", "err": {"type": "ValueError", "msg": "unknown request", "tb": ""}})
        except wire.Closed:
            return
        except BaseException as e:
            ch.send(dict(failure(e, -3), op="ret"))


def _plain(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.floating, np.integer, np.bool_)):
        return v.item()
    return str(v)


def limits():
    """No file can be written (the sandbox has no writable directory but an empty /tmp in memory), no core dump, a
    ceiling on memory. Hard limits: the code that runs next cannot raise them again."""
    import resource
    gb = float(os.environ.get("M2M_BOXED_MEMORY_GB", "32"))
    for what, v in ((resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_CORE, 0), (resource.RLIMIT_AS, int(gb * 2 ** 30))):
        try:
            resource.setrlimit(what, (v, v))
        except (ValueError, OSError):
            pass


def main():
    mode, fd = sys.argv[1], int(sys.argv[2])
    limits()
    sock = socket.socket(fileno=fd)
    ch = wire.Channel(sock, Frame)
    (program if mode == "program" else world)(ch)


if __name__ == "__main__":
    main()
