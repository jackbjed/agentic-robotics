"""bimanual/worlds_from_img.py without a model call: a from-nothing Claude Code session that is given two real images.
Checked: its brief is a from-nothing session's plus two additions and nothing taken away, the images are in the
workspace (the 16-bit depth is not: it is read through the command), the command reads their depth, a standard session
has no such sub-command, and a whole session runs through the launcher with the scripted stand-in for the CLI."""
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from agentic_robotics import paths
from agentic_robotics.bimanual import worlds_from_img as W
from agentic_robotics.bimanual.improve_cc import CodeSession

FAKE = str(Path(__file__).parent / "cc" / "fake_agent.py")
ASK = "push the block.\n\nOn the table there is a small block. Push it at least 30 mm from where it starts and take the hand off it."


def made_up_run(tmp):
    """A made-up live run with a depth recording (no real result is used)."""
    run = tmp / "run"
    (run / "video").mkdir(parents=True); (run / "frames").mkdir(); (run / "program").mkdir()
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    dep = np.full((480, 640), 1200, np.uint16); dep[200:260, 300:340] = 900; dep[:20] = 0
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "640x480", "-framerate", "15", "-i", "-",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(run / "video" / "scene.mp4")], input=rgb.tobytes() * 3, check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray16le", "-s", "640x480", "-framerate", "1", "-i", "-",
                    "-c:v", "ffv1", "-level", "3", str(run / "video" / "scene_depth.mkv")], input=dep.tobytes() * 2, check=True)
    (run / "video" / "scene_depth.csv").write_text("i,t_clock,t_run\n0,1.0,0.02\n1,2.0,1.02\n")
    wd = np.full((480, 640), 730, np.uint16); wd[100:140, 100:140] = 560; wd[460:] = 0
    cv2.imwrite(str(run / "frames" / "000_L_wrist_rgb.png"), rgb); cv2.imwrite(str(run / "frames" / "000_L_wrist_depth.png"), wd)
    (run / "frames" / "000_L_wrist.json").write_text(json.dumps(dict(cam="L_wrist", K=[640.0, 480.0, 620.0, 619.0, 312.0, 237.0], t=9.1)))
    (run / "program" / "output.txt").write_text("# t=   9.1 move('left', (-350, 60, 620), (0, 0, -1), (0, -1, 0)) -> done | left tool (-349.2, 62, 617.5)\n"
                                                "# t=   9.1 look([left_wrist]) -> left_wrist: Frame | left tool (-349.2, 62, 617.5)\n")
    return run


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("img")
    W.make_images(made_up_run(tmp), tmp / "images")
    return tmp / "images"


@pytest.fixture(scope="module")
def session(images, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wfi")
    s = W.ImgSession(tmp / "out" / "x", ASK, 5.0, images=images, root=tmp / "root" / "x", episode_s=120.0, log=lambda *x: None)
    return s, tmp


def test_images_folder(images):
    m = {x["name"]: x for x in json.loads((images / "images.json").read_text())["images"]}
    assert sorted(m) == ["left_wrist", "scene"] and m["left_wrist"]["tool_mm"] == [-349.2, 62.0, 617.5]
    assert m["left_wrist"]["approach_dir"] == [0.0, 0.0, -1.0] and m["left_wrist"]["closing_dir"] == [0.0, -1.0, 0.0]
    d = cv2.imread(str(images / "scene_depth16.png"), cv2.IMREAD_UNCHANGED)
    assert d.dtype == np.uint16 and d[230, 320] == 900 and d[300, 100] == 1200 and d[5, 5] == 0      # (the recording's depth, lossless)


def test_brief_is_a_from_nothing_brief_plus_two_additions(session, tmp_path):
    s, tmp = session
    base = CodeSession(tmp_path / "out" / "b", ASK, 5.0, root=tmp_path / "root" / "b", episode_s=120.0, log=lambda *x: None)
    mine, theirs = s.instr.replace(s.cmd, "P"), base.instr.replace(base.cmd, "P")
    assert mine.replace(W.SUB, "").replace(W.PARA.format(wrist=s._wrist()), "") == theirs
    assert "(-349, 62, 618) mm, the hand pointing straight down" in mine
    assert s.first_message().rstrip() == base.first_message().rstrip() + W.FIRST
    for word in ("mm thick", "soft", "finger", "diameter"):                    # (nothing about the real things in words)
        assert word not in W.PARA + W.SUB + W.FIRST


def test_workspace_holds_the_pictures_not_the_raw_depth(session):
    s, tmp = session
    d = s.ws / "pictures" / "real"
    assert sorted(p.name for p in d.iterdir()) == ["left_wrist.png", "left_wrist_depth.png", "left_wrist_nodepth.png", "scene.png", "scene_depth.png", "scene_nodepth.png"]
    assert s.state["kind"] == "worlds_from_img" and not list(s.ws.rglob("*depth16*"))


def test_command_reads_the_depth(session):
    s, tmp = session
    out = s.command(["read_image_depth", "--image", "scene", "--u0", "300", "--v0", "200", "--u1", "339", "--v1", "259"])
    assert "the real image scene" in out and "fx 615.3" in out and "median 900" in out and "[budget:" in out
    out = s.command(["read_image_depth", "--image", "left_wrist"])
    assert "fx 620.0" in out and "nearest 560" in out
    assert "bad call" in s.command(["read_image_depth", "--image", "other"])
    assert "which sub-command" not in s.command(["list_scenes"])


def test_a_standard_session_has_no_such_command(tmp_path):
    base = CodeSession(tmp_path / "out" / "b", ASK, 5.0, root=tmp_path / "root" / "b", episode_s=120.0, log=lambda *x: None)
    out = base.command(["read_image_depth", "--image", "scene"])
    assert "which sub-command" in out and "read_image_depth" not in out


def test_whole_session_through_the_launcher(images, tmp_path):
    cmd = [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from agentic_robotics.bimanual.worlds_from_img import main; sys.exit(main(sys.argv[2:]))",
           str(paths.ROOT / "src"), "run", "--out", str(tmp_path / "out" / "push"), "--root", str(tmp_path / "root" / "push"), "--budget-usd", "5",
           "--episode-s", "120", "--max-hours", "0.5", "--agent-cmd", FAKE, "--ask", ASK, "--images", str(images)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1500, env=dict(os.environ, FAKE_SCENARIO="full", MUJOCO_GL="egl", PYTHONNOUSERSITE="1"))
    last = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    assert last.get("ended") == "submitted", (r.stdout[-1500:], r.stderr[-1500:])
    out, ws = tmp_path / "out" / "push", tmp_path / "root" / "push" / "workspace"
    res = json.loads((out / "result.json").read_text())
    assert res["kind"] == "worlds_from_img" and res["agent"]["kind"] == "claude_code" and (ws / "pictures" / "real" / "scene.png").exists()
    # the practice command of that session knows the images (the session's class is chosen from its state)
    c = subprocess.run([str(ws.parent / "practice"), "read_image_depth", "--image", "scene"], capture_output=True, text=True, cwd=str(ws),
                       env=dict(PATH=os.environ["PATH"], AR_CC_SESSION=str(out)))
    assert "The session has ended" in c.stdout
