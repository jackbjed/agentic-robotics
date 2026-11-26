"""real/direct-skill: the skill written on the spot, WITHOUT a simulator, and rewritten after a failure on the robot.
The ablation of the sessions with worlds: the same kind of agent writes the program from one look at the real scene;
the program is run on the robot by `real/run program` as every program is; if the operator's verdict is a failure, the
agent is shown what the episode recorded (the program's output with its trace of commands, frames of the two cameras'
videos, the images the program showed) and a new look at the scene set out again, and writes the whole program
again. At most --rewrites times (default 2: three runs in all). The agent never runs anything: it has no simulator, no
practice command and no shell.

    real/direct-skill --tag NAME --task insert_own_cc [--mode live]
        [--writer claude|astra|file:DIR]  claude (default): an isolated headless Claude Code session per attempt, on the
                                       subscription (file tools in a workspace of its own, NO shell); astra: one call
                                       of the OpenAI model per attempt (PAID, the repository's ledger); file:DIR: the
                                       programs DIR/a0.py, a1.py, ... (tests: no model)
        [--rewrites 2] [--model opus] [--effort xhigh] [--max-usd 5]      per attempt
        [--text TEXT|FILE]             the task as the writer is told it (default: the text `real/run program --task`
                                       gives the program)
        [--rig-notes FILE]             what the writer is told about the real rig (default: real/astra_skill_rig.md; "" = nothing)
        [--backend sim --seed N]       a rehearsal: the simulation stands in for the robot, its judge for the operator
        [-- ...]                       what follows "--" goes to every `real/run program` (--confirm-contact, --world F, ...)

Per attempt k (0, 1, 2), in runs/bimanual/real_direct/<time>_<tag>/a<k>/: prompt.md (all the text the writer got),
given/ (every image and file it got), program.py, write.json (how the writing went, its cost), run.txt (the run's
directory), and for the claude writer claude/ (the CLI's stream). summary.json: the attempts and their verdicts.
The default mode is dry: one look, one program, nothing sent to the arm (as real/astra-skill). The loop needs
--mode live (the operator's LIVE and verdict at the terminal, as ever) or --backend sim."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = [sys.executable, "-I", str(ROOT / "real" / "run.py"), "program"]     # (this interpreter: also from a copy of the checkout)
WORK = Path("/var/tmp/ar-cc")                                      # the claude writer's workspaces: outside every checkout
LOOKS = 12                                                      # the program's looks given with their depth (evenly, if it made more)
VIDEO_FRAMES = 8                                                # per camera, evenly over the episode, the last one included
OUTPUT_LINES = (150, 350)                                       # a long output: its first and its last lines

FIRST = """

Write the program for this task. It is written for the scene as it is now, shown below, and run on it once; the
objects may have been nudged since the look, so the program finds them with the cameras itself.

{how}

The look of the scene as it is now (the arm at its start pose; the scene camera's and the wrist camera's colour images):
{state}
{images}"""

AGAIN = """

Your program was run on {robot} and {judge} judged the episode: {verdict}.
{earlier}What that episode recorded:
- the program as it ran: {program}
- its output: what it printed and, on lines starting with "#", every robot command with its result and the arm's
  MEASURED tool position after it: {output}
- frames of the episode's video, evenly over the episode (t: seconds since its start): {video}
- the images the program handed over with robot.show: {shown}
- the images the program got from its looks, in order (t: seconds on the program's clock), each with its depth:
  NAME.png the colour image, NAME_depth.png the depth as a picture (near red, far blue, the scale on top; black = NO
  depth), NAME_nodepth.png the colour image with the pixels that had no depth in magenta, NAME_depth.txt the depth as
  numbers (mm along the camera's axis, 0 = no depth, on a grid over the image, with the camera's intrinsics): {looks}

The scene has been set out again. Write the WHOLE program again for the scene as it is now, shown below; it is run on
it once; the objects may have been nudged since the look.

{how}

The look of the scene as it is now (the arm at its start pose; the scene camera's and the wrist camera's colour images):
{state}
{images}"""

PROTOCOL = """
The program is run exactly as you write it, on {robot}. You cannot test it: there is nothing you can run and nothing
to try it in. If {judge} judges the episode a failure, you are shown what it recorded and write the program again:
{left}. Write it so that it finds things with the cameras, checks what it measured, and recovers where it can.
"""

WHO = {"robot": ("the real robot", "the operator"), "sim": ("the robot", "the task's judge")}   # (the simulation standing in is not called real)
HOW = {"claude": "Write the program to the file program.py in your working directory (the whole program: a Python file that defines "
                 "run(robot, task)). The images and files named here are in that directory: read them with your file tools.",
       "astra": "Reply with the whole program in one ```python code block (a short note before it is fine).",
       "file": "Reply with the whole program in one ```python code block (a short note before it is fine)."}


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, **kw)


def look(a, k, rest):
    """A DRY run of real/look_program.py: the rig's cameras from where the arm stands -> (its directory, the arm's
    state line, the task's text as `real/run program` gives it to a program)."""
    cmd = RUN + ["--program", str(ROOT / "real" / "look_program.py"), "--tag", f"{a.tag}_look{k}", "--backend", a.backend, "--task", a.task]
    cmd += (["--seed", str(a.seed)] if a.backend == "sim" else []) + [x for x in rest if x not in ("--confirm-contact",)]
    r = sh(cmd)
    m = re.search(r"run on the \w+: (\S+)", r.stdout)
    if r.returncode != 0 or not m:
        raise SystemExit("the look failed:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    d = Path(m.group(1))
    out = (d / "program" / "output.txt").read_text(errors="replace")
    st = next((l for l in out.splitlines() if l.startswith("STATE ")), None)
    task = next((l[8:].strip() for l in out.splitlines() if l.startswith("# task: ")), None)
    if st is None or not list((d / "frames").glob("*_rgb.png")):
        raise SystemExit(f"the look gave no frames or no state: {d}")
    return d, st[6:], task


def give_look(look_dir, given):
    """The look's colour images -> given/look_<camera>.png -> [(camera, path)]."""
    out = []
    for f in sorted((look_dir / "frames").glob("*_rgb.png")):
        cam = "scene" if "d415" in f.name else "left_wrist"
        p = given / f"look_{cam}.png"
        shutil.copyfile(f, p); out.append((cam, p))
    return out


def _depth_picture(dep):
    """A look's depth as a picture: near red, far blue between the 2nd and 98th percentile of what has depth (the
    scale on top, in mm); no depth = black."""
    import cv2
    import numpy as np
    ok = dep[dep > 0]
    lo, hi = (float(np.percentile(ok, 2)), float(np.percentile(ok, 98))) if len(ok) else (0.0, 1.0)
    hi = max(hi, lo + 1.0)
    x = np.clip((dep.astype(np.float32) - lo) / (hi - lo), 0, 1)
    out = cv2.applyColorMap((255 * (1 - x)).astype(np.uint8), cv2.COLORMAP_TURBO)
    out[dep == 0] = 0
    out[:14] = cv2.applyColorMap(np.tile(np.linspace(255, 0, out.shape[1]).astype(np.uint8), (14, 1)), cv2.COLORMAP_TURBO)
    for i in range(5):
        cv2.putText(out, f"{lo + (hi - lo) * i / 4:.0f} mm", (min(int(i / 4 * (out.shape[1] - 1)), out.shape[1] - 62), 28), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return out


def _depth_numbers(dep, K):
    """A look's depth as text: the statistics and a grid of values over the whole image."""
    import numpy as np
    H, W = dep.shape
    ok = dep[dep > 0]
    us = sorted({int(round(x)) for x in np.linspace(0, W - 1, 24)}); vs = sorted({int(round(x)) for x in np.linspace(0, H - 1, 18)})
    head = (f"depth in mm along the camera's axis (0 = no depth); the image is {W} x {H}"
            + (f", fx {K[2]:.1f}, fy {K[3]:.1f}, cx {K[4]:.1f}, cy {K[5]:.1f}" if len(K) == 6 else "") + ".\n"
            + (f"{100 * (1 - len(ok) / dep.size):.0f}% of the pixels without depth; of the others: nearest {int(ok.min())}, 5% {int(np.percentile(ok, 5))}, "
               f"median {int(np.median(ok))}, 95% {int(np.percentile(ok, 95))}, farthest {int(ok.max())} mm." if len(ok) else "no pixel has depth."))
    return head + "\n" + "\n".join([" v \\ u " + "".join(f"{u:5d}" for u in us)] + [f"{v:6d} " + "".join(f"{int(dep[v, u]):5d}" for u in us) for v in vs]) + "\n"


def give_looks(run, g):
    """The program's looks of a run (frames/: colour, 16-bit depth in mm, the intrinsics) -> files under g -> [(label, path)]:
    NAME.png, NAME_depth.png, NAME_nodepth.png, NAME_depth.txt for each, NAME = look_NN_<camera>."""
    import cv2
    import numpy as np
    fr = sorted((run / "frames").glob("*_rgb.png"))
    pick = sorted({int(round(x)) for x in np.linspace(0, len(fr) - 1, min(LOOKS, len(fr)))}) if fr else []
    out = []
    for i in pick:
        f = fr[i]
        stem = f.name[:-len("_rgb.png")]
        cam = "scene" if "d415" in stem else "left_wrist"
        meta = json.loads((f.parent / f"{stem}.json").read_text()) if (f.parent / f"{stem}.json").exists() else {}
        name = f"look_{i:02d}_{cam}"
        rgb = cv2.imread(str(f))
        cv2.imwrite(str(g / f"{name}.png"), rgb)
        dp = f.parent / f"{stem}_depth.png"
        dep = cv2.imread(str(dp), cv2.IMREAD_UNCHANGED) if dp.exists() else None
        if dep is not None and dep.ndim == 2:
            nod = rgb.copy(); nod[dep == 0] = (0.35 * nod[dep == 0] + 0.65 * np.array([255, 0, 255])).astype(np.uint8)
            cv2.imwrite(str(g / f"{name}_depth.png"), _depth_picture(dep)); cv2.imwrite(str(g / f"{name}_nodepth.png"), nod)
            (g / f"{name}_depth.txt").write_text(_depth_numbers(dep, meta.get("K") or []))
        out.append((f"{cam} camera, t {meta['t']:.1f} s" if meta.get("t") is not None else f"{cam} camera", g / f"{name}.png"))
    return out


def give_failure(run, given, k):
    """What a failed episode recorded -> files under given/attempt<k>/ -> dict(program, output, video [(label, path)],
    shown [(label, path)], verdict)."""
    import cv2
    import numpy as np
    g = given / f"attempt{k}"
    g.mkdir(parents=True, exist_ok=True)
    res = json.loads((run / "result.json").read_text())
    v = res.get("verdict") or {}
    words = "FAILED" + ("; the operator intervened" if v.get("intervened") or res.get("interrupted") else "") \
        + ("; something was knocked off or over" if v.get("knocked_off") else "") + (f'; the operator\'s note: "{v["note"]}"' if v.get("note") else "")
    shutil.copyfile(run / "skill" / "program.py", g / "program.py")
    lines = [l for l in (run / "program" / "output.txt").read_text(errors="replace").splitlines()
             if not l.startswith(("# the world's success()", "WORLD ", "# judged"))]
    if len(lines) > sum(OUTPUT_LINES):
        lines = lines[:OUTPUT_LINES[0]] + [f"[... {len(lines) - sum(OUTPUT_LINES)} lines left out ...]"] + lines[-OUTPUT_LINES[1]:]
    (g / "output.txt").write_text("\n".join(lines) + "\n")
    video = []
    for cam in ("scene", "wrist"):
        f = run / "video" / f"{cam}.mp4"
        if not f.exists():
            continue
        cap = cv2.VideoCapture(str(f))
        n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS) or 15.0
        for i in sorted({int(round(x)) for x in np.linspace(0, max(n - 1, 0), VIDEO_FRAMES)}):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, im = cap.read()
            if ok:
                p = g / f"video_{cam}_{i / fps:05.1f}s.png"
                cv2.imwrite(str(p), im); video.append((f"{cam} camera, t {i / fps:.1f} s", p))
        cap.release()
    f = run / "sim" / "video.mp4"                                   # (the simulation standing in: its overview camera's video)
    if not video and f.exists():
        cap = cv2.VideoCapture(str(f))
        n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS) or 15.0
        for i in sorted({int(round(x)) for x in np.linspace(0, max(n - 1, 0), VIDEO_FRAMES)}):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, im = cap.read()
            if ok:
                p = g / f"video_overview_{i / fps:05.1f}s.png"
                cv2.imwrite(str(p), im); video.append((f"an overview camera, t {i / fps:.1f} s", p))
        cap.release()
    if not video:                                                   # (no video at all: the program's own looks stand in)
        fr = sorted((run / "frames").glob("*_rgb.png"))
        for i in sorted({int(round(x)) for x in np.linspace(0, max(len(fr) - 1, 0), VIDEO_FRAMES)}) if fr else []:
            p = g / f"look_{i:02d}_{'scene' if 'd415' in fr[i].name else 'wrist'}.png"
            shutil.copyfile(fr[i], p); video.append((f"the program's look {i}", p))
    shown = []
    for f in sorted((run / "program" / "shown").glob("*.png"))[:8] if (run / "program" / "shown").exists() else []:
        p = g / f"shown_{f.name}"
        shutil.copyfile(f, p); shown.append((f.stem, p))
    return dict(program=g / "program.py", output=g / "output.txt", video=video, shown=shown, verdict=words, looks=give_looks(run, g))


def message(a, k, task, state, looks, fails, given, inline):
    """The writer's message for attempt k. inline: files are named by their path in `given` (the claude writer reads
    them) or their text is put into the message (the astra writer gets the images beside it)."""
    name = lambda p: str(Path(p).relative_to(given))
    how = HOW[a.writer.split(":")[0]]
    st = f"The arm's state: tool point, approach direction, closing direction, opening (mm): {state}"
    imgs = "\n".join(f"{cam}: {name(p)}" for cam, p in looks) if not inline else ""
    if k == 0:
        return task + FIRST.format(how=how, state=st, images=imgs)
    last = fails[-1]
    earlier = ""
    if len(fails) > 1:
        earlier = "Before that, your earlier program had failed too: " + "; ".join(
            f"attempt {i + 1}: {f['verdict']}" + (f" (its program and output: {name(f['program'])}, {name(f['output'])})" if not inline else "")
            for i, f in enumerate(fails[:-1])) + ".\n"
    if inline:
        prog = "\n```python\n" + last["program"].read_text() + "```"
        outp = "\n```\n" + last["output"].read_text() + "```"
        vid = "the images below, each with its label" if last["video"] else "none recorded"
        sho = "the images below" if last["shown"] else "none"
        loo = "not given here"
    else:
        prog, outp = name(last["program"]), name(last["output"])
        vid = ", ".join(f"{name(p)} ({lab})" for lab, p in last["video"]) or "none recorded"
        sho = ", ".join(name(p) for _, p in last["shown"]) or "none"
        loo = ", ".join(f"{name(p)} ({lab})" for lab, p in last.get("looks") or []) or "none recorded"
    return task + AGAIN.format(robot=WHO[a.backend][0], judge=WHO[a.backend][1], verdict=last["verdict"], earlier=earlier, program=prog, output=outp, video=vid, shown=sho, looks=loo, how=how, state=st, images=imgs)


def instructions(a, left, k=0):
    from agentic_robotics.bimanual.practice import RULES
    from agentic_robotics.bimanual.tasks import TASKS
    from agentic_robotics.bimanual.write import instructions as interface
    notes = "" if a.rig_notes == "" else Path(a.rig_notes or ROOT / "real" / "astra_skill_rig.md").read_text().strip()
    if k >= 1 and a.rig_notes_rewrite:                              # (what the rewrites are told about the rig, with the failure)
        notes = Path(a.rig_notes_rewrite).read_text().strip()
    words = {0: "this was the last time", 1: "one more time at most"}.get(left, f"at most {left} more times")
    return (interface("grippers", TASKS["insert_varied"].time_limit_s, segmenter=True, closing="", arm="left") + ("\n" + notes + "\n" if notes else "")
            + "\n" + RULES + "\n" + PROTOCOL.format(robot=WHO[a.backend][0], judge=WHO[a.backend][1],
                                                     left=words if left else "this is the last time: it is not rewritten after this run"))


# ---- the writers
def write_file(a, k, d, instr, text, images):
    src = Path(a.writer.split(":", 1)[1]) / f"a{k}.py"
    return src.read_text(), dict(writer="file", source=str(src), usd=0.0)


def write_astra(a, k, d, instr, text, images):
    import cv2
    from agentic_robotics.bimanual.write import extract_program
    from agentic_robotics.perception import vlm as V
    content, arrs = [{"type": "input_text", "text": text}], []
    for lab, p in images:
        content += [{"type": "input_text", "text": f"{lab}:"}, {"type": "input_image", "image": len(arrs)}]
        arrs.append(cv2.imread(str(p))[..., ::-1].copy())
    vlm = V.VLM(dict(model=a.model if a.model != "opus" else "astra", converse=True, max_calls_per_episode=1, max_usd_per_episode=a.max_usd, max_usd_per_call=a.max_usd),
                tag=f"real_direct:{a.backend}:{a.tag}:a{k}")
    r = vlm.respond([{"role": "user", "content": content}], arrs, instructions=instr, effort=a.effort if a.effort != "xhigh" else "high", max_output_tokens=40000)
    reply = "" if not r.get("ok") else "\n".join(c.get("text", "") for o in r["output"] if o.get("type") == "message"
                                                  for c in o.get("content") or [] if c.get("type") == "output_text")
    (d / "reply.md").write_text(reply + "\n")
    code, why = (None, f"model: {r.get('why')}") if not r.get("ok") else extract_program(reply)
    return code, dict(writer="astra", why=why, status=r.get("status"), **vlm.usage())


def write_claude(a, k, d, instr, text, images):
    """One isolated headless Claude Code session: file tools in a workspace of its own (the given files and nothing
    else), no shell, no web, no MCP, no memory, no settings; the subscription's token, no API key."""
    from agentic_robotics import paths
    from agentic_robotics.learning.agent import parse_stream
    root = WORK / f"direct_{a.tag}_{time.strftime('%H%M%S')}" / f"a{k}"
    ws = root / "workspace"
    for p in (ws, *ws.parents):
        if any((p / n).exists() for n in ("CLAUDE.md", "AGENTS.md", ".claude", ".mcp.json")) and p != Path.home():
            raise SystemExit(f"{p} holds a CLAUDE.md / AGENTS.md / .claude: the writer's workspace must have none above it")
    shutil.copytree(d / "given", ws)
    for n in ("home", "config"):
        (root / n).mkdir(parents=True)
    tok = (paths.local().get("secrets") or {}).get("claude_oauth_token_file")
    fake = os.environ.get("DIRECT_CLAUDE_BIN")
    if not fake and not (tok and Path(tok).expanduser().is_file()):
        raise SystemExit("configs/local.yaml secrets.claude_oauth_token_file names no token file")
    env = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), LANG=os.environ.get("LANG", "C.UTF-8"), HOME=str(root / "home"), CLAUDE_CONFIG_DIR=str(root / "config"),
               TERM="dumb", DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    if not fake:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = Path(tok).expanduser().read_text().strip()
    cmd = [fake or shutil.which("claude") or "claude", "-p", text, "--output-format", "stream-json", "--verbose", "--restricted", "--safe-mode",
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
                      model=(res.get("init") or {}).get("model"), tools=(res.get("init") or {}).get("tools"), mcp_servers=(res.get("init") or {}).get("mcp_servers"),
                      usage=res.get("usage"), wall_s=round(time.time() - t0, 1), exit=p.returncode, workspace=str(ws), files_written=extra)


def write(a, k, out, task, state, look_dir, fails):
    """Attempt k's program -> its directory (SystemExit if there is none that may run)."""
    from agentic_robotics.bimanual.practice import check_program
    d = out / f"a{k}"
    given = d / "given"
    given.mkdir(parents=True)
    looks = give_look(look_dir, given)
    for i, f in enumerate(fails):                                  # (every earlier attempt's files are given again)
        fails[i] = give_failure(Path(f["run"]), given, i + 1) | dict(run=f["run"])
    kind = a.writer.split(":")[0]
    instr = instructions(a, a.rewrites - k, k)
    text = message(a, k, task, state, looks, fails, given, inline=kind != "claude")
    images = [(cam, p) for cam, p in looks]
    if fails and kind != "claude":
        images = [(f"video: {lab}", p) for lab, p in fails[-1]["video"]] + [(f"shown: {lab}", p) for lab, p in fails[-1]["shown"]] + images
    (d / "prompt.md").write_text("# Instructions\n\n" + instr + "\n# Message\n\n" + text + "\n\n# Given\n\n" + "\n".join(
        str(p.relative_to(given)) for p in sorted(given.rglob("*")) if p.is_file()) + "\n")
    print(f"   writing attempt {k + 1} with {a.writer.split(':')[0]}" + (f" ({a.model}, {a.effort}, at most ${a.max_usd:g})" if kind != "file" else "") + " ...", flush=True)
    code, info = {"file": write_file, "astra": write_astra, "claude": write_claude}[kind](a, k, d, instr, text, images)
    bad = check_program(code) if code else []
    try:
        compile(code or "", "program.py", "exec")
        if code and "def run(" not in code:
            bad = bad + ["the program does not define run(robot, task)"]
    except SyntaxError as e:
        bad = bad + [f"the program does not parse: {e}"]
    info = dict(info, ok=bool(code) and not bad, why=info.get("why") or "; ".join(bad), attempt=k + 1, look=str(look_dir), program_lines=len((code or "").splitlines()))
    (d / "write.json").write_text(json.dumps(info, indent=1, default=str) + "\n")
    if code:
        (d / "program.py").write_text(code)
    print("   " + json.dumps({x: info[x] for x in ("ok", "why", "program_lines", "usd") if x in info}), flush=True)
    if not info["ok"]:
        raise SystemExit(f"no program to run ({info['why']}): see {d}")
    return d, info


def run(a, k, d, rest):
    """`real/run program` on attempt k's program, at the terminal -> (the run's directory, its verdict or None)."""
    tag = f"{a.tag}_a{k + 1}"
    root = Path(rest[rest.index("--out-root") + 1]) if "--out-root" in rest else ROOT / "real" / "results"
    before = set(root.glob(f"*_{tag}"))
    cmd = RUN + ["--program", str(d / "program.py"), "--tag", tag, "--mode", a.mode, "--backend", a.backend, "--task", a.task]
    cmd += (["--seed", str(a.seed)] if a.backend == "sim" else []) + rest
    print(f"   {a.mode} run: {' '.join(cmd)}", flush=True)
    subprocess.call(cmd, cwd=ROOT)
    new = sorted(set(root.glob(f"*_{tag}")) - before)
    if not new:
        return None, None
    (d / "run.txt").write_text(str(new[-1]) + "\n")
    f = new[-1] / "result.json"
    return new[-1], ((json.loads(f.read_text()).get("verdict") or None) if f.exists() else None)


def main():
    argv, rest = sys.argv[1:], []
    if "--" in argv:
        argv, rest = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag")
    ap.add_argument("--continue", dest="cont", metavar="RECORD", help="go on from a record (runs/bimanual/real_direct/<time>_<tag>) whose last "
                    "attempt failed: the next attempt is a rewrite after that failure. The other options as they were given the first time")
    ap.add_argument("--task", default="insert_own_cc", help="the task of `real/run program` (its text, its verdict question, its limits)")
    ap.add_argument("--text", help="the task as the writer is told it, or a file with it (default: the text the program is given)")
    ap.add_argument("--mode", default="dry", choices=("dry", "live"))
    ap.add_argument("--backend", default="robot", choices=("robot", "sim"))
    ap.add_argument("--seed", type=int, default=2001, help="--backend sim: the scene")
    ap.add_argument("--writer", default="claude", help="claude | astra | file:DIR")
    ap.add_argument("--rewrites", type=int, default=2, help="how often a failed program is rewritten (default 2: three runs in all)")
    ap.add_argument("--model", default="opus"); ap.add_argument("--effort", default="xhigh")
    ap.add_argument("--max-usd", type=float, default=5.0, help="per attempt"); ap.add_argument("--max-minutes", type=float, default=40.0, help="per attempt (claude)")
    ap.add_argument("--budget-usd", type=float, help="the writer's budget over ALL attempts (then --max-usd is not used: an attempt may spend what is left)")
    ap.add_argument("--rig-notes")
    ap.add_argument("--rig-notes-rewrite", metavar="FILE", help="what the writer is told about the rig from its first REWRITE on, in place of "
                    "--rig-notes (the first attempt keeps --rig-notes)")
    a = ap.parse_args(argv)
    old = None
    if a.cont:
        old = json.loads((Path(a.cont) / "summary.json").read_text())
        a.tag = a.tag or old["tag"]
        if a.tag != old["tag"] or a.task != old["task"] or a.backend != old["backend"]:
            raise SystemExit(f"--continue: that record is tag {old['tag']}, task {old['task']}, backend {old['backend']}")
        if not old["attempts"] or (old["attempts"][-1].get("verdict") or {}).get("success") is not False:
            raise SystemExit("--continue: the record's last attempt has no FAILED verdict: there is nothing to rewrite after")
        if len(old["attempts"]) > a.rewrites:
            raise SystemExit(f"--continue: the record has {len(old['attempts'])} attempts already: no rewrite is left (--rewrites {a.rewrites})")
    if not a.tag or not re.fullmatch(r"[A-Za-z0-9_.-]+", a.tag):
        raise SystemExit("--tag: letters, digits, _ . -")
    if a.rig_notes_rewrite and not Path(a.rig_notes_rewrite).is_file():
        raise SystemExit(f"--rig-notes-rewrite: no such file: {a.rig_notes_rewrite}")
    if a.writer.split(":")[0] not in ("claude", "astra", "file") or not 0 <= a.rewrites <= 5:
        raise SystemExit("--writer claude | astra | file:DIR; --rewrites 0..5")
    now = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
    t_start, started, reset_s, wall_before, pause_s = time.time(), now(), None, 0.0, None
    if old:                                                                   # (go on from a record: its attempts, their cost and times)
        out = Path(a.cont).resolve()
        attempts, spent = old["attempts"], float(old.get("spent_usd") or 0.0)
        fails = [dict(run=r["run"]) for r in attempts]
        ot = old.get("times") or {}
        started, wall_before = ot.get("started") or started, float(ot.get("wall_s") or 0.0)
        if ot.get("ended"):
            pause_s = round(time.time() - time.mktime(time.strptime(ot["ended"], "%Y-%m-%dT%H:%M:%S")), 1)
        print(f"going on from {out}: {len(attempts)} failed attempt(s), ${spent:.2f} spent", flush=True)
    else:
        out = ROOT / "runs" / "bimanual" / "real_direct" / f"{time.strftime('%Y%m%d_%H%M%S')}_{a.tag}"
        out.mkdir(parents=True)
        attempts, fails, spent = [], [], 0.0
    for k in range(len(attempts), a.rewrites + 1):
        print(f"ATTEMPT {k + 1} of at most {a.rewrites + 1}", flush=True)
        T = dict(started=now(), scene_reset_s=reset_s)                        # (wall clock of every step: the ablation's cost in time)
        if pause_s is not None:                                               # (the record was left and taken up again: not in wall_s)
            T["pause_before_s"], pause_s = pause_s, None
        T["rig_notes"] = (a.rig_notes_rewrite if k >= 1 and a.rig_notes_rewrite else ("" if a.rig_notes == "" else a.rig_notes or "real/astra_skill_rig.md"))
        t0 = time.time()
        look_dir, state, task = look(a, k, rest)
        T["look_s"] = round(time.time() - t0, 1)
        print(f"   looked (nothing moved): {look_dir}", flush=True)
        text = (Path(a.text).read_text().strip() if a.text and Path(a.text).is_file() else a.text) or task
        if not text:
            raise SystemExit("the task's text is not known: --text")
        if a.budget_usd is not None:
            a.max_usd = round(a.budget_usd - spent, 2)
            if a.max_usd < 0.5:
                print(f"the writer's budget is used up (${spent:.2f} of ${a.budget_usd:g}): no further attempt", flush=True)
                break
        T["write_started"] = now(); t0 = time.time()
        d, info = write(a, k, out, text, state, look_dir, fails)
        T["write_s"] = round(time.time() - t0, 1)
        spent += float(info.get("usd") or 0.0)
        print(f"   the program: {d / 'program.py'}  (the prompt and what the writer was given are next to it)", flush=True)
        T["run_started"] = now(); t0 = time.time()
        rd, v = run(a, k, d, rest)
        T["run_s"] = round(time.time() - t0, 1)                               # (the operator's LIVE and verdict included)
        res = rd / "result.json" if rd else None
        res = json.loads(res.read_text()) if res and res.exists() else {}
        T.update(robot_time_s=res.get("robot_time_s"), episode_wall_s=res.get("wall_s"), ended=now())
        T["attempt_s"] = round(sum(T.get(x) or 0.0 for x in ("look_s", "write_s", "run_s")), 1)
        rec = dict(attempt=k + 1, dir=str(d), run=str(rd) if rd else None, verdict=v, times=T,
                   write={x: info.get(x) for x in ("usd", "program_lines", "writer", "turns", "wall_s")})
        attempts.append(rec)
        (d / "times.json").write_text(json.dumps(T, indent=1) + "\n")
        tot = lambda x: round(sum((r.get("times") or {}).get(x) or 0.0 for r in attempts), 1)
        times = dict(started=started, ended=now(), wall_s=round(wall_before + time.time() - t_start, 1), look_s=tot("look_s"), write_s=tot("write_s"),
                     run_s=tot("run_s"), robot_time_s=tot("robot_time_s"), scene_reset_s=tot("scene_reset_s"),
                     note="wall-clock seconds. look_s: the look before writing; write_s: the agent writing the program (a rewrite includes "
                          "studying the failure); run_s: the run on the robot from its start to the verdict (the operator's LIVE and "
                          "verdict included); robot_time_s: the episode on the rig's clock; scene_reset_s: the operator setting the scene "
                          "out again before an attempt; wall_s: everything, first look to last verdict")
        (out / "summary.json").write_text(json.dumps(dict(tag=a.tag, task=a.task, writer=a.writer, model=a.model, effort=a.effort, mode=a.mode, backend=a.backend,
                                                          rewrites_allowed=a.rewrites, budget_usd=a.budget_usd, spent_usd=round(spent, 4), times=times, attempts=attempts), indent=1, default=str) + "\n")
        print(f"   times: look {T['look_s']:.0f} s, writing {T['write_s']:.0f} s, run {T['run_s']:.0f} s (robot time {T['robot_time_s']}); so far {times['wall_s']:.0f} s", flush=True)
        if a.mode == "dry" and a.backend != "sim":
            print("a dry run: nothing was sent to the arm, so there is no verdict and no rewrite (--mode live)", flush=True)
            break
        if v is None:
            print("the run has no verdict: the loop stops here", flush=True)
            break
        if v.get("success"):
            print(f"SUCCESS at attempt {k + 1}", flush=True)
            break
        if k == a.rewrites:
            print(f"FAILED after {k + 1} attempts: no rewrite is left", flush=True)
            break
        fails.append(dict(run=str(rd)))
        reset_s = None
        if a.backend == "robot":
            t0 = time.time()
            stop = input("   FAILED. Set the scene out again for the next attempt, then press Enter (q + Enter: stop here) ").strip().lower() == "q"
            reset_s = round(time.time() - t0, 1)
            if stop:
                break
    print(f"the record: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
