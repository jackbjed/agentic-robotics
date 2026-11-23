"""real/run program: a bimanual session's program on the real path (real/host_program.py, bimanual/real.py), with the
bimanual simulation standing in for the robot (--backend sim: left arm only, the task's judge). Needs our SAM3 and the
insertion session's program (runs/ is not in git)."""
import json
import subprocess
from pathlib import Path

import pytest

from agentic_robotics import paths, services

PROGRAM = paths.ROOT / "runs" / "bimanual" / "varied_tables_1122" / "program.py"
needs = pytest.mark.skipif(not (PROGRAM.exists() and services.alive("sam3_text")), reason="no session program or sam3 not running")


def run(tmp, *args):
    return subprocess.run([str(paths.ROOT / "real" / "run"), "program", "--program", str(PROGRAM), "--tag", "test",
                           "--out-root", str(tmp), *args], capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)


@needs
def test_sim_live_inserts(tmp_path):
    r = run(tmp_path, "--backend", "sim", "--mode", "live", "--seed", "2001")
    d = sorted(Path(tmp_path).glob("*_sim_live_test"))
    assert d, r.stdout[-2000:] + r.stderr[-2000:]
    res = json.loads((d[-1] / "result.json").read_text())
    assert res["verdict"]["success"] is True, res.get("program_result")
    assert res["trajectories"]["sent"] == res["trajectories"]["planned"] >= 10 and res["trajectories"]["refused"] == 0
    clock = res["program"]["program_clock"]                       # (the guarded descents at 5 mm/s, not 40)
    assert clock["kind"] == "sim" and clock["behind_robot_s"] > 30
    assert (d[-1] / "sim" / "video.mp4").exists()


@pytest.mark.skipif(not PROGRAM.exists(), reason="no session program")
def test_refusals(tmp_path):
    r = run(tmp_path, "--task", "clutter", "--mode", "dry")       # (both arms: not on the robot)
    assert r.returncode != 0 and "both arms" in r.stderr + r.stdout
    r = run(tmp_path, "--backend", "twin")                        # (the twin has no cylinder)
    assert r.returncode != 0 and "--backend sim" in r.stderr + r.stdout
    assert not list(Path(tmp_path).glob("*"))
