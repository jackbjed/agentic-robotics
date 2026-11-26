"""bimanual/with_real_info.py without a model call: a from-nothing Claude Code session that is given a text about the
real robot. Checked: its brief is a from-nothing session's plus one section and nothing taken away, the first message
is a from-nothing session's, the text is kept in the state (the practice command builds the session from it), and a
whole session runs through the launcher with the scripted stand-in for the CLI."""
import json
import os
import subprocess
import sys
from pathlib import Path

from agentic_robotics import paths
from agentic_robotics.bimanual import with_real_info as W
from agentic_robotics.bimanual.improve_cc import CodeSession

FAKE = str(Path(__file__).parent / "cc" / "fake_agent.py")
ASK = "push the block.\n\nOn the table there is a small block. Push it at least 30 mm from where it starts and take the hand off it."
INFO = "Facts about the rig.\n\n- A made-up fact: the made-up gripper closes with 12 N.\n"


def test_brief_is_a_from_nothing_brief_plus_one_section(tmp_path):
    (tmp_path / "info.md").write_text(INFO)
    s = W.InfoSession(tmp_path / "out" / "x", ASK, 5.0, info=tmp_path / "info.md", root=tmp_path / "root" / "x", episode_s=120.0, log=lambda *x: None)
    base = CodeSession(tmp_path / "out" / "b", ASK, 5.0, root=tmp_path / "root" / "b", episode_s=120.0, log=lambda *x: None)
    mine, theirs = s.instr.replace(str(s.cmd), "P"), base.instr.replace(str(base.cmd), "P")
    added = W.PARA.format(info=INFO.strip())
    assert mine.count(added) == 1 and mine.replace(added, "") == theirs
    assert s.first_message() == base.first_message()
    assert s.state["kind"] == "with_real_info" and s.state["real_info_text"] == INFO.strip()
    again = W.InfoSession(tmp_path / "out" / "x", ASK, 5.0, root=tmp_path / "root" / "x", episode_s=120.0, log=lambda *x: None)   # (from its state)
    assert again.instr == s.instr


def test_whole_session_through_the_launcher(tmp_path):
    (tmp_path / "info.md").write_text(INFO)
    cmd = [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from agentic_robotics.bimanual.with_real_info import main; sys.exit(main(sys.argv[2:]))",
           str(paths.ROOT / "src"), "run", "--out", str(tmp_path / "out" / "push"), "--root", str(tmp_path / "root" / "push"), "--budget-usd", "5",
           "--episode-s", "120", "--max-hours", "0.5", "--agent-cmd", FAKE, "--ask", ASK, "--info", str(tmp_path / "info.md")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1500, env=dict(os.environ, FAKE_SCENARIO="full", MUJOCO_GL="egl", PYTHONNOUSERSITE="1"))
    last = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    assert last.get("ended") == "submitted", (r.stdout[-1500:], r.stderr[-1500:])
    out, ws = tmp_path / "out" / "push", tmp_path / "root" / "push" / "workspace"
    res = json.loads((out / "result.json").read_text())
    assert res["kind"] == "with_real_info" and res["agent"]["kind"] == "claude_code"
    assert "the made-up gripper closes with 12 N" in (out / "instructions.md").read_text()
    c = subprocess.run([str(ws.parent / "practice"), "list_scenes"], capture_output=True, text=True, cwd=str(ws),
                       env=dict(PATH=os.environ["PATH"], AR_CC_SESSION=str(out)))
    assert "The session has ended" in c.stdout
