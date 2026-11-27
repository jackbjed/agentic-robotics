"""The host side of the execution boundary: what the episode runners call.

    how = run_program(code, given, task_text, log)      a skill program, in the sandbox, driving `given` (the logged robot
                                                        object of program_robot): -> "returned" | "time: ..." | "error: ..."
    w = World(code); sc = w.scene(base_scene, seed); c = w.success(state, sink); w.close()
                                                        a practice-world file, in a sandbox of its own

The sandbox is the project's (src/agentic_robotics/execution/sandbox.py: bubblewrap, unprivileged namespaces), with a
narrower view: the system and the Python environment read-only, this folder's three files, NOTHING of the repository,
the experiment's records or the home directory, no network, no device, no writable file. The child is handed one end
of a socket pair; the host answers on the other end only the calls named in ALLOWED (a program) or STATE_QUERIES (a
world's success(state))."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from . import wire
from .frame import Frame

HERE = Path(__file__).resolve().parent
SYSTEM_RO = ["/usr", "/etc", "/opt"]
SYMLINKS = [("usr/bin", "/bin"), ("usr/sbin", "/sbin"), ("usr/lib", "/lib"), ("usr/lib64", "/lib64"), ("usr/lib32", "/lib32")]
STATE_QUERIES = ("pos", "rot", "start_rot", "touching", "speed", "held", "arm")
THREADS = "4"


class BoundaryError(RuntimeError):
    """The sandbox could not be started (not: the agent's code failed)."""


def bwrap():
    b = shutil.which("bwrap")
    if b is None:
        raise BoundaryError("bubblewrap (bwrap) not found: agent-written code is not run without the sandbox")
    return b


def base_args():
    """(execution/sandbox.py _base(net=False), the same flags: tests/test_boundary.py compares them when the project's
    module can be imported.)"""
    args = [bwrap(), "--die-with-parent", "--new-session", "--unshare-user", "--unshare-ipc", "--unshare-pid",
            "--unshare-uts", "--unshare-cgroup-try", "--unshare-net"]
    for p in SYSTEM_RO:
        if Path(p).exists():
            args += ["--ro-bind", p, p]
    for target, link in SYMLINKS:
        if Path(link).is_symlink():
            args += ["--symlink", target, link]
        elif Path(link).exists():
            args += ["--ro-bind", link, link]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    return args


def child_cmd(mode, fd):
    """The command line of a sandboxed child: this interpreter's environment read-only, /boxed = this folder."""
    prefix, base = os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix)
    args = base_args()
    for p in dict.fromkeys([prefix, base]):
        args += ["--ro-bind", p, p]
    exe = os.path.join(prefix, "bin", "python")
    for alias, real in _aliases(exe, [prefix, base]):               # (a venv's python may point through an alias directory of its base)
        args += ["--symlink", real, alias]
    args += ["--ro-bind", str(HERE), "/boxed"]
    env = {"HOME": "/tmp", "PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8",
           "OMP_NUM_THREADS": THREADS, "OPENBLAS_NUM_THREADS": THREADS, "MKL_NUM_THREADS": THREADS, "M2M_BOXED": "1"}
    args += ["--clearenv"]
    for k, v in env.items():
        args += ["--setenv", k, v]
    return args + ["--chdir", "/tmp", "--", exe, "-I", "/boxed/child.py", mode, str(fd)]


def _aliases(exe, bound):
    """Directory symlinks on the way from an interpreter's path to its real file that lie outside what is bound:
    [(the alias, its real directory)], to be made again inside the sandbox."""
    out, p, seen = [], exe, set()
    inside = lambda q: any(q == b or q.startswith(b + os.sep) for b in bound)
    while os.path.islink(p) and p not in seen:
        seen.add(p)
        t = os.readlink(p); p = t if os.path.isabs(t) else os.path.normpath(os.path.join(os.path.dirname(p), t))
        q = p
        while q not in ("/", ""):
            q = os.path.dirname(q)
            if os.path.islink(q) and not inside(q) and inside(os.path.realpath(q)) and (q, os.path.realpath(q)) not in out:
                out.append((q, os.path.realpath(q)))
    return out


class _Child:
    def __init__(self, mode):
        self.a, b = socket.socketpair()
        self.err = tempfile.TemporaryFile()
        try:
            self.p = subprocess.Popen(child_cmd(mode, b.fileno()), pass_fds=[b.fileno()], stdin=subprocess.DEVNULL, stdout=self.err, stderr=self.err,
                                      close_fds=True)
        finally:
            b.close()
        self.ch = wire.Channel(self.a, Frame)

    def stderr_tail(self, n=600):
        try:
            self.err.seek(0); return self.err.read().decode(errors="replace").strip()[-n:]
        except Exception:
            return ""

    def close(self):
        try:
            self.a.close()
        except OSError:
            pass
        if self.p.poll() is None:
            try:
                self.p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.p.kill(); self.p.wait()
        code = self.p.returncode
        try:
            self.err.close()
        except Exception:
            pass
        return code


def interface_of(given):
    """A robot object's documented interface, as it crosses the boundary: (the names of its calls, its constants)."""
    names, consts = [], {}
    for k in dir(given):
        if k.startswith("_"):
            continue
        v = getattr(given, k)
        if callable(v):
            names.append(k)
        else:
            consts[k] = v
    return names, consts


def _err(e):
    args = list(getattr(e, "args", ()) or ())
    plain = all(a is None or isinstance(a, (str, int, float, bool)) for a in args)
    return {"type": type(e).__name__, "msg": str(e), "args": args if plain else [str(e)]}


def run_program(code, given, task_text, log):
    """Run a skill program in the sandbox against `given`. Everything the program prints goes to `log`, in order with
    its commands. -> how it ended."""
    names, consts = interface_of(given)
    allowed = set(names)
    c = _Child("program")
    how = None
    try:
        c.ch.send({"code": code, "task": task_text, "consts": consts, "names": names})
        while True:
            m = c.ch.recv()
            op = m.get("op") if isinstance(m, dict) else None
            if op == "out":
                log.write(str(m.get("text", "")))
            elif op == "call":
                name = m.get("name")
                try:
                    if name not in allowed:
                        raise AttributeError(f"the robot has no {name!r}")
                    a, k = m.get("a") or [], m.get("k") or {}
                    if not isinstance(a, list) or not isinstance(k, dict):
                        raise TypeError("bad arguments")
                    r = getattr(given, name)(*a, **k)
                    try:
                        c.ch.send({"ok": r})
                    except (TypeError, ValueError) as e:            # (a result that cannot cross: never the case for the documented calls)
                        c.ch.send({"err": {"type": "RuntimeError", "msg": f"the result of {name} could not be passed on: {e}"}})
                except wire.Closed:
                    raise
                except Exception as e:                              # (TimeUp, a refusal's ValueError/KeyError, ...: raised again in the program)
                    c.ch.send({"err": _err(e)})
            elif op == "end":
                how = str(m.get("how", ""))[:300]
                if not how.startswith(("returned", "time: ", "error: ")):
                    how = "error: " + how
                break
            else:
                raise wire.Closed("an unknown message")
    except (wire.Closed, ValueError, TypeError, KeyError) as e:
        try:
            c.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            c.p.kill(); c.p.wait()
        tail = c.stderr_tail()
        code_ = c.close()
        return f"error: the program's process ended without a result (exit code {code_}; {type(e).__name__})" + (f": {tail[-200:]}" if tail else "")
    c.close()
    return how


class WorldFailure(Exception):
    """The world file's own code failed: .kind, .msg, .tb (its traceback's text)."""

    def __init__(self, err):
        self.kind, self.msg, self.tb = str(err.get("type")), str(err.get("msg")), str(err.get("tb") or "")
        super().__init__(f"{self.kind}: {self.msg}")


class World:
    """A practice-world file, loaded in a sandbox of its own (kept for the whole episode: the file's module state stays)."""

    def __init__(self, code, sink=None):
        self.c = _Child("world"); self.sink = sink
        try:
            self.c.ch.send({"code": code})
            r = self._ret(("ready",))
        except wire.Closed as e:
            code_ = self.c.close()
            raise BoundaryError(f"the world's process did not start (exit code {code_}): {e}")
        self.has_success, self.has_scene = bool(r.get("has_success")), bool(r.get("has_scene"))

    def _ret(self, ops, state=None):
        while True:
            m = self.c.ch.recv()
            op = m.get("op") if isinstance(m, dict) else None
            if op == "out":
                if self.sink is not None:
                    self.sink(str(m.get("text", "")))
            elif op == "q" and state is not None:
                try:
                    q = m.get("q")
                    if q not in STATE_QUERIES:
                        raise AttributeError(f"the state has no {q!r}")
                    arg = m.get("arg")
                    if not isinstance(arg, str):
                        raise KeyError(arg)
                    self.c.ch.send({"ok": getattr(state, q)(arg)})
                except wire.Closed:
                    raise
                except Exception as e:
                    self.c.ch.send({"err": _err(e)})
            elif op in ops:
                if "err" in m:
                    raise WorldFailure(m["err"])
                return m if op == "ready" else m["ok"]
            else:
                raise wire.Closed("an unknown message")

    def scene(self, base, seed):
        """-> what the file's scene(scene, rng) returned, if a dict (its JSON form); None otherwise. Raises WorldFailure."""
        self.c.ch.send({"op": "scene", "scene": base, "seed": int(seed)})
        r = self._ret(("ret",))
        return json.loads(r["json"]) if r.get("dict") else None

    def success(self, state):
        """-> (truth, the dict it returned or None). Raises WorldFailure."""
        self.c.ch.send({"op": "success", "names": list(state.names), "start": dict(state.start)})
        r = self._ret(("ret",), state)
        return bool(r.get("truth")), (json.loads(r["json"]) if r.get("dict") else None)

    def close(self):
        return self.c.close()
