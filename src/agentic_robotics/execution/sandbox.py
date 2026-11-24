"""The skill program's sandbox (bubblewrap, unprivileged user namespaces; nothing installed, no root). Enforced by the
kernel, outside any code a learning agent can edit. Ported from embodied-agents src/ea/sandbox.py (program_cmd).

A program gets:
  - no network at all (its own network namespace, no interface): it reaches its world only through the one Unix
    socket bound in;
  - a read-only view of the system (/usr, /etc, /opt), the project's Python environment, the platform's src/ (with
    the vendored library) and its own skill code;
  - ONE writable directory (the episode's program/ output) plus the socket's directory; /tmp is a private tmpfs;
  - no home directory: no keys, no configs/local.yaml, no runs/, no data/ (layouts), no held-out results, no other
    workspaces; no devices beyond /dev's basics (no GPU: it renders its own robot model with OSMesa).
The real robot's arm server is never reachable: it listens on TCP 5590 on the host (no network here) or a Unix socket
outside every bound path."""
from __future__ import annotations

import sys
import shutil
from pathlib import Path

from .. import paths

SYSTEM_RO = ["/usr", "/etc", "/opt"]
SYMLINKS = [("usr/bin", "/bin"), ("usr/sbin", "/sbin"), ("usr/lib", "/lib"), ("usr/lib64", "/lib64"), ("usr/lib32", "/lib32")]


def bwrap():
    b = shutil.which("bwrap")
    if b is None:
        raise RuntimeError("bubblewrap (bwrap) not found: the sandbox needs it")
    return b


def _base(net=False):
    args = [bwrap(), "--die-with-parent", "--new-session", "--unshare-user", "--unshare-ipc", "--unshare-pid",
            "--unshare-uts", "--unshare-cgroup-try"]
    if not net:
        args.append("--unshare-net")
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


def env_ro():
    """The project's environment and platform, read-only (same absolute paths: the venv's links keep working)."""
    R = paths.ROOT
    out = []
    env = (Path(sys.prefix), Path(sys.base_prefix).parent)         # (a git worktree runs on the main checkout's environment:
                                                                   # its venv and the directory of the interpreters its links name)
    for p in dict.fromkeys((R / ".venv", R / ".uv" / "python", R / "src", R / "pyproject.toml") + tuple(e for e in env if R not in e.parents)):
        if p.exists():
            out += ["--ro-bind", str(p), str(p)]
    return out


def hide_args(hide):
    """Paths made invisible inside the sandbox: a directory becomes an empty tmpfs, a file reads as empty."""
    out = []
    for h in hide or ():
        h = Path(h)
        if h.is_dir():
            out += ["--tmpfs", str(h)]
        elif h.exists():
            out += ["--ro-bind", "/dev/null", str(h)]
    return out


def program_cmd(cmd, skills_dir, out_dir, sock_dir, extra_env=None, hide=None, rw=None, devices=None):
    """Wrap a host command line in the program sandbox. hide: paths made invisible (real2sim: the scene's model files,
    selfsim.hidden_paths); rw: more directories bound writable (real2sim's analysis scripts: sim/, analysis/);
    devices: device nodes passed through (bimanual/practice.py: the GPU's, the simulation renders in the sandbox)."""
    args = _base(net=False) + env_ro() + hide_args(hide)
    for d in devices or ():
        args += ["--dev-bind", str(d), str(d)]
    args += ["--ro-bind", str(skills_dir), str(skills_dir)]
    for d in rw or ():
        args += ["--bind", str(d), str(d)]
    args += ["--bind", str(out_dir), str(out_dir), "--bind", str(sock_dir), str(sock_dir)]
    env = {"HOME": "/tmp", "PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "LANG": "C.UTF-8",
           "MUJOCO_GL": "osmesa", "PYOPENGL_PLATFORM": "osmesa"}
    env.update(extra_env or {})
    args += ["--clearenv"]
    for k, v in env.items():
        args += ["--setenv", k, v]
    args += ["--chdir", str(out_dir), "--"]
    return args + list(cmd)


def selftest():
    """Is the program sandbox closed? No network interface but lo; nothing of the home directory but the bound
    platform paths (no keys, no Claude/SSH config, no other projects); no data/, runs/, workspaces/, configs/."""
    import subprocess
    H = Path.home(); R = paths.ROOT
    secret_like = [H / ".ssh", H / ".claude", H / ".config", R / "configs", R / "data", R / "runs", R / "workspaces",
                   R.parent / "embodied-agents", R.parent / "panda_twin"]
    probe = "cat /proc/net/dev | tail -n +3 | cut -d: -f1 | tr -d ' ' | tr '\\n' ' '; echo; " + "; ".join(
        f"test -e {p} && echo VISIBLE {p}" for p in secret_like) + f"; ls {R}"
    r = subprocess.run(_base(net=False) + env_ro() + ["--", "/bin/sh", "-c", probe], capture_output=True, text=True, timeout=20)
    lines = r.stdout.splitlines()
    visible = [x for x in lines if x.startswith("VISIBLE")]
    ok = r.returncode == 0 and bool(lines) and lines[0].strip() in ("lo", "") and not visible
    return dict(ok=bool(ok), interfaces=lines[0].strip() if lines else None, visible=visible,
                repo_view=lines[-1] if lines else None, stderr=r.stderr.strip()[:300])
