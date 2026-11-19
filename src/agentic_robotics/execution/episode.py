"""One episode = a fresh world-server process (privileged) + the skill host in its sandbox, joined by one Unix socket.
Writes <out>/: result.json (the judge's verdict, robot and wall times, usage, versions), actions.jsonl, events.jsonl,
observations/ (every camera frame the program got, unless record.observations = none), video.mp4, program/ (the
program's own output: program.json, debug images, errors), spec.json, logs.

    run_episode(skill_root, ep, out_dir, entry="skills/pick.py", sandbox=True)
ep = dict(split, level, idx, seed, scene, perception, robot_time_limit_s, record, parity)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import paths
from . import sandbox as sbx
from .versions import versions

PY = str(paths.ROOT / ".venv" / "bin" / "python")


def base_env(egl_device=None):
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH", "CONDA_PREFIX")}
    env.update(PYTHONNOUSERSITE="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", MUJOCO_GL="egl")
    if egl_device is None:
        try:
            egl_device = (paths.local().get("render") or {}).get("egl_device")
        except FileNotFoundError:
            egl_device = None
    if egl_device is not None:
        env["MUJOCO_EGL_DEVICE_ID"] = str(egl_device)
    return env


def gpu_free_mib(index):
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits",
                            "-i", str(index)], capture_output=True, text=True, timeout=20)
        used, total = map(float, r.stdout.strip().split(","))
        return total - used
    except Exception:
        return None


def wait_for_gpu(max_wait_s=3600):
    """Before starting a world server: wait while the rendering GPU has less free memory than the guard
    (configs/local.yaml render.nvidia_index, render.min_free_mib): the GPU is shared with other people's servers."""
    try:
        R = paths.local().get("render") or {}
    except FileNotFoundError:
        return 0.0
    idx, need = R.get("nvidia_index"), float(R.get("min_free_mib", 4000))
    if idx is None:
        return 0.0
    t0 = time.time()
    while True:
        free = gpu_free_mib(idx)
        if free is None or free >= need:
            return time.time() - t0
        if time.time() - t0 > max_wait_s:
            raise RuntimeError(f"GPU {idx}: only {free:.0f} MiB free for {max_wait_s} s (guard {need:.0f} MiB)")
        time.sleep(5)


def run_episode(skill_root, ep, out_dir, entry="skills/pick.py", sandbox=True, egl_device=None, wall_limit_s=900):
    import fcntl
    lockf = open(paths.RUNS.parent / ".gpu_start.lock", "w")      # (one world server starts at a time, machine-wide:
    fcntl.flock(lockf, fcntl.LOCK_EX)                              # its memory shows before the next one checks)
    try:
        gpu_wait = wait_for_gpu()
        return _run_episode(skill_root, ep, out_dir, entry, sandbox, egl_device, wall_limit_s, gpu_wait, lockf)
    finally:
        lockf.close()


def _run_episode(skill_root, ep, out_dir, entry, sandbox, egl_device, wall_limit_s, gpu_wait, lockf):
    import fcntl
    skill_root = Path(skill_root).resolve()
    out = Path(out_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
    prog_out = out / "program"; prog_out.mkdir(exist_ok=True)
    ep = dict(ep)
    json.dump(ep, open(out / "spec.json", "w"), indent=1)
    sock_dir = Path(tempfile.mkdtemp(prefix="ar-"))            # (short path: Unix socket names are <= 107 bytes)
    sock = sock_dir / "world.sock"
    env = base_env(egl_device)
    t0 = time.time()
    srv = subprocess.Popen([PY, "-I", "-m", "agentic_robotics.sim.server", "--spec", str(out / "spec.json"),
                            "--sock", str(sock), "--out", str(out)], env=env, cwd=str(paths.ROOT),
                           stdout=open(out / "server.log", "w"), stderr=subprocess.STDOUT)
    host = None
    try:
        while not Path(str(sock) + ".ready").exists():
            if srv.poll() is not None:
                raise RuntimeError(f"world server exited ({srv.returncode}): {(out / 'server.log').read_text()[-1500:]}")
            if time.time() - t0 > 180:
                raise RuntimeError("world server did not start in 180 s")
            time.sleep(0.05)
        fcntl.flock(lockf, fcntl.LOCK_UN)                           # (its renderers exist now: the next may check)
        cmd = [PY, "-I", "-m", "agentic_robotics.execution.host", "--skills", str(skill_root), "--entry", entry,
               "--sock", str(sock), "--out", str(prog_out)]
        if sandbox:
            cmd = sbx.program_cmd(cmd, skill_root, prog_out, sock_dir)
            henv = None
        else:
            henv = dict(env, MUJOCO_GL="osmesa", PYOPENGL_PLATFORM="osmesa")
        host = subprocess.Popen(cmd, env=henv, cwd=str(prog_out), stdout=open(prog_out / "host.log", "w"),
                                stderr=subprocess.STDOUT)
        try:
            host.wait(timeout=wall_limit_s)
        except subprocess.TimeoutExpired:
            host.kill(); host.wait()
        try:                                                    # (the server writes the record, then exits)
            srv.wait(timeout=60)
        except subprocess.TimeoutExpired:
            if not (out / "result.json").exists():              # (the program died without its last word)
                _send_done(sock, f"program ended without reporting (exit {host.returncode})")
        try:
            srv.wait(timeout=120)
        except subprocess.TimeoutExpired:
            srv.kill(); srv.wait()
    finally:
        for p in (host, srv):
            if p is not None and p.poll() is None:
                p.kill()
        shutil.rmtree(sock_dir, ignore_errors=True)
    rf = out / "result.json"
    if not rf.exists():
        res = dict(success=False, category="platform_error", error=(out / "server.log").read_text()[-1500:])
    else:
        res = json.load(open(rf))
    res["versions"] = versions(skill_root)
    res["wall"] = dict(res.get("wall") or {}, episode_total=round(time.time() - t0, 3), gpu_guard_wait=round(gpu_wait, 1))
    res["sandbox"] = bool(sandbox)
    json.dump(res, open(rf, "w"), indent=1, default=str)
    return res


def _send_done(sock, why):
    try:
        import zmq
        from . import protocol
        c = zmq.Context(); s = c.socket(zmq.REQ); s.setsockopt(zmq.RCVTIMEO, 60000); s.setsockopt(zmq.LINGER, 0)
        s.connect(f"ipc://{sock}")
        protocol.send(s, dict(cmd="done", result=None, error=why))
        protocol.recv(s)
        s.close(); c.term()
    except Exception:
        pass
