"""A skill program written on the spot from one look at the scene, by an isolated coding agent, and run once: the
project's real/direct_skill.py approach (copied: the writer's isolation, the wording of its message) on RoboDojo's
sweep_blocks in ManiSkill. No simulator practice, no rewrite: zero-shot.

    ../.venv/bin/python -I sweep_blocks/zero_shot.py --layout 4 --tag NAME            the look and the texts only (no model)
    ../.venv/bin/python -I sweep_blocks/zero_shot.py --layout 4 --tag NAME --go       ALSO the writer (PAID: one isolated
                                                 headless Claude Code session on the subscription) and the program's run
        [--max-usd 10] [--max-minutes 120] [--model opus] [--effort xhigh]

The writer: file tools (Read, Edit, Write, Glob, Grep) in a workspace of its own under /var/tmp/ar-cc (outside every
checkout, so that no project instruction file is above it), holding the look's files and nothing else; no shell, no
web, no MCP, no memory, no settings. It never runs anything.
In out/zero_shot/<tag>/: given/ (every file the writer gets), prompt.md (all the text it gets), program.py, write.json,
claude/ (the CLI's stream), run/ (the episode: result.json, output.txt, video.mp4), summary.json."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parent
MS_PY = "<HOME>/micromamba/envs/robomme/bin/python"
WORK = Path("/var/tmp/ar-cc")
RULES = ("Imports: only math, numpy, scipy, cv2, itertools, functools, collections, dataclasses, typing, heapq, bisect, statistics, random, "
         "time, copy, enum, operator, numbers, abc (and their submodules). No eval, exec, open, getattr or file access (numpy's and "
         "OpenCV's file functions included), and no attribute whose name starts with an underscore except on self.")
# RoboDojo's own public text of the task (task/RoboDojo/tasks/sweep_blocks.py: gen_instruction, gen_detailed_instruction)
INSTRUCTION = "Pick up the broom, hand it over to the right hand, then use the dustpan to sweep the blocks."
DESCRIPTION = ("Use the brush and dustpan to sweep all target objects from the tabletop into the dustpan. Avoid pushing objects outside the "
               "pan or toward the table edge, and check for objects left in their original positions, scattered nearby, or stopped at the "
               "pan entrance. Continue until every target object is completely inside and remains contained. At completion, the dustpan "
               "must be more than 10 cm to the left of the brush along the world x-axis, measured between their object origins. Finish "
               "with the dustpan level on the table and positioned to the left of the brush, then fully open both grippers and return "
               "both arms to their initial poses.")
SCORING = ("- 0: The blocks are not fully swept into the dustpan.\n- 100: The dustpan is positioned left of the broom, stably placed upright "
           "on the table, all blocks are inside the dustpan, and the robot returns to origin.")
TASK = f"{INSTRUCTION}\n\n{DESCRIPTION}\n\nScoring:\n{SCORING}"
FIRST = """

Write the program for this task. It is written for the scene as it is now, shown below, and run on it once; the
program finds things with the cameras itself.

Write the program to the file program.py in your working directory (the whole program: a Python file that defines
run(robot, task)). The images and files named here are in that directory: read them with your file tools.

The look of the scene as it is now (both arms at their start poses). For each camera: NAME.png the colour image,
NAME_depth.png the depth as a picture (near red, far blue, the scale on top), NAME_depth.txt the depth as numbers (mm
along the camera's axis, on a grid over the image, with the camera's intrinsics and its pose):
{images}
The arms' state:
{state}"""
PROTOCOL = """
The program is run exactly as you write it, on the robot. You cannot test it: there is nothing you can run and nothing
to try it in. It is not rewritten after this run. Write it so that it finds things with the cameras, checks what it
measured, and recovers where it can.
"""


def instructions():
    return (HERE / "sweep_blocks" / "zero_shot_interface.md").read_text().rstrip() + "\n\n" + RULES + "\n" + PROTOCOL


def look(layout, given):
    env = dict(os.environ, PYTHONNOUSERSITE="1", CUDA_VISIBLE_DEVICES=os.environ.get("M2M_GPU", "0"))
    r = subprocess.run([MS_PY, "-I", str(HERE / "sweep_blocks" / "run_program.py"), "--layout", str(layout), "--look", str(given)],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0 or not (given / "state.txt").exists():
        raise SystemExit("the look failed:\n" + r.stderr[-1500:])
    return (given / "state.txt").read_text().strip()


def message(given, state):
    cams = ("head", "left_wrist", "right_wrist")
    images = "\n".join(f"{c}: look_{c}.png, look_{c}_depth.png, look_{c}_depth.txt" for c in cams)
    return TASK + FIRST.format(images=images, state=state)


def write_claude(a, d, instr, text):
    """(real/direct_skill.py write_claude) One isolated headless Claude Code session."""
    sys.path.insert(0, str(PROJECT / "src"))
    from agentic_robotics import paths
    from agentic_robotics.learning.agent import parse_stream
    root = WORK / f"m2m_{a.tag}_{time.strftime('%H%M%S')}"
    ws = root / "workspace"
    for p in (ws, *ws.parents):
        if any((p / n).exists() for n in ("CLAUDE.md", "AGENTS.md", ".claude", ".mcp.json")) and p != Path.home():
            raise SystemExit(f"{p} holds a CLAUDE.md / AGENTS.md / .claude: the writer's workspace must have none above it")
    shutil.copytree(d / "given", ws)
    for n in ("home", "config"):
        (root / n).mkdir(parents=True)
    tok = (paths.local().get("secrets") or {}).get("claude_oauth_token_file")
    if not (tok and Path(tok).expanduser().is_file()):
        raise SystemExit("configs/local.yaml secrets.claude_oauth_token_file names no token file")
    env = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), LANG=os.environ.get("LANG", "C.UTF-8"), HOME=str(root / "home"),
               CLAUDE_CONFIG_DIR=str(root / "config"), TERM="dumb", DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    env["CLAUDE_CODE_OAUTH_TOKEN"] = Path(tok).expanduser().read_text().strip()
    cmd = [shutil.which("claude") or "claude", "-p", text, "--output-format", "stream-json", "--verbose", "--restricted", "--safe-mode",
           "--setting-sources", "", "--tools", "Read,Edit,Write,Glob,Grep", "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep",
           "--permission-mode", "dontAsk", "--permission-prompts", "none", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
           "--max-budget-usd", f"{a.max_usd:.2f}", "--append-system-prompt", instr, "--model", a.model, "--effort", a.effort]
    (d / "claude").mkdir(exist_ok=True)
    t0 = time.time()
    with open(d / "claude" / "claude.jsonl", "w") as out, open(d / "claude" / "claude.stderr", "w") as err:
        p = subprocess.Popen(cmd, cwd=str(ws), env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL)
        try:
            p.wait(timeout=a.max_minutes * 60)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()
    res = parse_stream(d / "claude" / "claude.jsonl")
    f = ws / "program.py"
    code = f.read_text() if f.is_file() else None
    extra = sorted(str(x.relative_to(ws)) for x in ws.rglob("*") if x.is_file() and not (d / "given" / x.relative_to(ws)).exists())
    return code, dict(writer="claude", why="" if code else "the session wrote no program.py", usd=res.get("cost_usd"), turns=res.get("turns"),
                      model=(res.get("init") or {}).get("model"), tools=(res.get("init") or {}).get("tools"),
                      mcp_servers=(res.get("init") or {}).get("mcp_servers"), usage=res.get("usage"), wall_s=round(time.time() - t0, 1),
                      exit=p.returncode, workspace=str(ws), files_written=extra)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", type=int, required=True); ap.add_argument("--tag", required=True)
    ap.add_argument("--go", action="store_true", help="launch the writer (PAID) and run its program")
    ap.add_argument("--max-usd", type=float, default=10.0); ap.add_argument("--max-minutes", type=float, default=120.0)
    ap.add_argument("--model", default="opus"); ap.add_argument("--effort", default="xhigh")
    a = ap.parse_args()
    d = HERE / "out" / "zero_shot" / a.tag
    given = d / "given"
    if not (given / "state.txt").exists():
        given.mkdir(parents=True, exist_ok=True)
        look(a.layout, given)
    state = (given / "state.txt").read_text().strip()
    instr, text = instructions(), message(given, state)
    (d / "prompt.md").write_text("# The writer's instructions (added to its system prompt)\n\n" + instr + "\n\n# The writer's message\n\n" + text + "\n")
    print(f"the look and the texts: {d}/given, {d}/prompt.md ({len(instr.split()) + len(text.split())} words)")
    if not a.go:
        print("nothing launched (--go launches the writer: a paid session)")
        return
    code, info = write_claude(a, d, instr, text)
    (d / "write.json").write_text(json.dumps(info, indent=1, default=str))
    print(f"the writer: {info.get('turns')} turns, ${info.get('usd')}, {info.get('wall_s')} s, {'a program' if code else 'NO program'}")
    summary = dict(tag=a.tag, layout=a.layout, task="robodojo sweep_blocks (ManiSkill)", write=info, result=None)
    if code:
        (d / "program.py").write_text(code)
        env = dict(os.environ, PYTHONNOUSERSITE="1", CUDA_VISIBLE_DEVICES=os.environ.get("M2M_GPU", "0"))
        r = subprocess.run([MS_PY, "-I", str(HERE / "sweep_blocks" / "run_program.py"), "--layout", str(a.layout), "--program", str(d / "program.py"),
                            "--out", str(d / "run"), "--video"], capture_output=True, text=True, env=env)
        if (d / "run" / "result.json").exists():
            summary["result"] = json.loads((d / "run" / "result.json").read_text())
            print("the run:", json.dumps({k: summary["result"][k] for k in ("success", "ended", "robot_s", "commands")}))
        else:
            summary["run_error"] = r.stderr[-1500:]
            print("the run failed:", r.stderr[-600:])
    (d / "summary.json").write_text(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
