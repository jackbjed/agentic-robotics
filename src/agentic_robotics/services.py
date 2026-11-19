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


def ports(name):
    c = config()[name]
    return [c["port"] + 10 * i for i in range(int(c.get("instances", 1)))]


def url(name, path="", instance=None):
    """The service's URL; with several identical instances, this process's (pid modulo the count: a world server
    keeps one instance for its episode; every instance renders the same)."""
    P = ports(name)
    i = (os.getpid() % len(P)) if instance is None else instance
    return f"http://127.0.0.1:{P[i]}{path}"


def alive(name, timeout=2.0, instance=0):
    try:
        with urllib.request.urlopen(url(name, config()[name].get("ping", "/ping"), instance), timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start(name, wait_s=600):
    return "; ".join(_start(name, i, wait_s) for i in range(len(ports(name))))


def _start(name, i, wait_s):
    if alive(name, instance=i):
        return f"#{i} running"
    c = config()[name]; L = paths.local().get("services") or {}
    py = (L.get("python") or {}).get(c["python"])
    if not py or not Path(py).exists():
        raise RuntimeError(f"{name}: configs/local.yaml services.python.{c['python']} is not set or missing")
    DIR.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH", "CONDA_PREFIX")}
    env.update(PYTHONNOUSERSITE="1", HF_HUB_OFFLINE="1", CUDA_VISIBLE_DEVICES=str(L.get("cuda_device", 0)))
    port = ports(name)[i]; tag = name if i == 0 else f"{name}{i}"
    cmd = [py, str(paths.ROOT / c["script"]), "--port", str(port)] + [
        str(paths.ROOT / a) if isinstance(a, str) and a.startswith("data/") else str(a) for a in c.get("args", [])]
    log = open(DIR / f"{tag}.log", "w")
    p = subprocess.Popen(cmd, env=env, cwd=str(paths.ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (DIR / f"{tag}.pid").write_text(str(p.pid))
    t0 = time.time()
    while not alive(name, instance=i):
        if p.poll() is not None:
            raise RuntimeError(f"{tag} exited: {(DIR / f'{tag}.log').read_text()[-1500:]}")
        if time.time() - t0 > wait_s:
            raise RuntimeError(f"{tag} not up after {wait_s} s")
        time.sleep(2)
    return f"#{i} started (pid {p.pid}, port {port}, {time.time() - t0:.0f} s)"


def stop(name):
    return "; ".join(_stop(name if i == 0 else f"{name}{i}", name) for i in range(len(ports(name))))


def _stop(tag, name):
    f = DIR / f"{tag}.pid"
    if not f.exists():
        return f"{tag}: no pid file"
    pid = int(f.read_text())
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
    except FileNotFoundError:
        f.unlink(); return "not running"
    if config()[name]["script"].split("/")[-1] not in cmd:
        return f"{tag}: pid {pid} is not ours ({cmd[:80]}): left alone"
    os.kill(pid, 15); f.unlink()
    return f"{tag}: stopped pid {pid}"


def status():
    out = {}
    for n in config():
        for i, port in enumerate(ports(n)):
            tag = n if i == 0 else f"{n}{i}"
            out[tag] = dict(port=port, alive=alive(n, instance=i), pid=(DIR / f"{tag}.pid").read_text() if (DIR / f"{tag}.pid").exists() else None)
    return out
