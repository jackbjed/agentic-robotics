"""Our own GPU services (configs/services.yaml): start, stop, status, URLs. Each runs as its own process with an
existing Python environment (configs/local.yaml services.python), logs and a pid file in runs/services/. Stopped by
PID, never by pattern."""
from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from pathlib import Path

from . import paths

DIR = paths.RUNS / "services"


def config():
    return paths.load_yaml("configs/services.yaml")


def url(name, path=""):
    return f"http://127.0.0.1:{config()[name]['port']}{path}"


def alive(name, timeout=2.0):
    try:
        with urllib.request.urlopen(url(name, config()[name].get("ping", "/ping")), timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start(name, wait_s=600):
    if alive(name):
        return "running"
    c = config()[name]; L = paths.local().get("services") or {}
    py = (L.get("python") or {}).get(c["python"])
    if not py or not Path(py).exists():
        raise RuntimeError(f"{name}: configs/local.yaml services.python.{c['python']} is not set or missing")
    DIR.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH", "CONDA_PREFIX")}
    env.update(PYTHONNOUSERSITE="1", HF_HUB_OFFLINE="1", CUDA_VISIBLE_DEVICES=str(L.get("cuda_device", 0)))
    cmd = [py, str(paths.ROOT / c["script"]), "--port", str(c["port"])] + [
        str(paths.ROOT / a) if isinstance(a, str) and a.startswith("data/") else str(a) for a in c.get("args", [])]
    log = open(DIR / f"{name}.log", "w")
    p = subprocess.Popen(cmd, env=env, cwd=str(paths.ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (DIR / f"{name}.pid").write_text(str(p.pid))
    t0 = time.time()
    while not alive(name):
        if p.poll() is not None:
            raise RuntimeError(f"{name} exited: {(DIR / f'{name}.log').read_text()[-1500:]}")
        if time.time() - t0 > wait_s:
            raise RuntimeError(f"{name} not up after {wait_s} s")
        time.sleep(2)
    return f"started (pid {p.pid}, {time.time() - t0:.0f} s)"


def stop(name):
    f = DIR / f"{name}.pid"
    if not f.exists():
        return "no pid file"
    pid = int(f.read_text())
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
    except FileNotFoundError:
        f.unlink(); return "not running"
    if config()[name]["script"].split("/")[-1] not in cmd:
        return f"pid {pid} is not ours ({cmd[:80]}): left alone"
    os.kill(pid, 15); f.unlink()
    return f"stopped pid {pid}"


def status():
    return {n: dict(port=c["port"], alive=alive(n), pid=(DIR / f"{n}.pid").read_text() if (DIR / f"{n}.pid").exists() else None)
            for n, c in config().items()}
