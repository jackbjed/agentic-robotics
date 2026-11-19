"""real/ (the real-robot runner), rehearsed on the twin: the same program sandbox, real/server.py, real/host.py and the
plan drawings, with the simulator standing in for the arm and the cameras. Dry: nothing moves; live: the skill lifts
the target (the twin's judge). Needs our SAM3 (bin/ar services start sam3_text sam3_points)."""
import json
import subprocess
from pathlib import Path

import pytest

from agentic_robotics import paths, services


def run(tmp, mode):
    r = subprocess.run([str(paths.ROOT / "real" / "run"), "episode", "--backend", "twin", "--mode", mode, "--skills",
                        str(paths.ROOT / "initial_skills" / "clutter_pick"), "--tag", "test", "--out-root", str(tmp)],
                       capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
    d = sorted(Path(tmp).glob(f"*_twin_{mode}_test"))
    assert d, r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads((d[-1] / "result.json").read_text()), d[-1]


@pytest.mark.skipif(not services.alive("sam3_text"), reason="sam3 not running")
def test_twin_dry_sends_nothing(tmp_path):
    r, d = run(tmp_path, "dry")
    assert r["trajectories"]["planned"] >= 1 and r["trajectories"]["sent"] == 0
    assert r["verdict"]["success"] is False                       # (nothing moved: the twin's judge sees no lift)
    assert (d / "plan" / "000.jpg").exists() and (d / "plan" / "sheet.jpg").exists()
    assert len(list((d / "frames").glob("*_rgb.png"))) >= 2


@pytest.mark.skipif(not services.alive("sam3_text"), reason="sam3 not running")
def test_twin_live_lifts(tmp_path):
    r, d = run(tmp_path, "live")
    assert r["verdict"]["success"] is True, r.get("program_result")
    assert r["trajectories"]["sent"] == r["trajectories"]["planned"] >= 1
    assert r["program"]["speed"] == 0.5                           # (the real robot's rig.json speed)
    assert (d / "twin" / "video.mp4").exists()
