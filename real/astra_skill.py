"""real/astra-skill: code as policy on the real robot. The model sees the scene ONCE and writes the skill program in
ONE call; the program is then run on the robot by `real/run program`, exactly as a session's program is (sandbox,
limits, record, the operator's LIVE and verdict).

    real/astra-skill --tag NAME [--mode live]            # look (nothing moves) -> one model call -> the program runs
        [--text "Pick up ..."]         the task as the model is told it (default: the insertion task's text)
        [--model astra] [--effort high] [--max-usd 3] [--max-output-tokens 40000]
        [--key-file F] [--ledger F]    another OpenAI key file / spending ledger than the repository's
        [--rig-notes FILE]             what the model is told about the real rig (default: real/astra_skill_rig.md; "" = nothing)
        [--write-only]                 stop after the program is written (read it, then --program)
        [--program DIR/program.py]     skip the look and the model call: run a program written before
        [--backend sim --seed N]       a rehearsal: the bimanual simulation stands in for the robot (nothing real moves)
        [-- ...]                       what follows "--" goes to `real/run program` (--guard-dz 0.01, --time-limit, ...)

Steps: 1. `real/run program --program real/look_program.py` as a DRY run: the rig's two cameras from where the arm
stands; 2. the model gets the program interface of the one-armed robot (bimanual/write.py), the rig notes, the task
and that look's colour images, and replies with the program; it is checked (practice.check_program) and kept in
runs/bimanual/real_cap/<time>_<tag>/ with the prompt and the reply; 3. `real/run program --program ... --mode MODE`.
The default mode is dry (nothing is sent to the arm); live asks for LIVE at the terminal, as ever."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = [str(ROOT / "real" / "run"), "program"]

_ASK = """

Write the program for this task. It is written for the scene as it is now, shown below, and run on it once; the
objects may have been nudged since the look, so the program finds them with the cameras itself.

Reply with the whole program in one ```python code block (a short note before it is fine).

The look of the scene as it is now (the arm at its start pose; the scene camera's and the wrist camera's colour images):
{state}"""

_ONCE = """
The program is run exactly as you write it, once, on the real robot: you cannot test it, see its output or revise it.
Write it so that it finds things with the cameras, checks what it measured, and recovers where it can.
"""


def look(a, passthrough):
    """Step 1 -> (the look run's directory, the arm's state line)."""
    cmd = RUN + ["--program", str(ROOT / "real" / "look_program.py"), "--tag", f"{a.tag}_look", "--backend", a.backend]
    cmd += ["--seed", str(a.seed)] if a.backend == "sim" else []
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    m = re.search(r"run on the \w+: (\S+)", r.stdout)
    if r.returncode != 0 or not m:
        raise SystemExit("the look failed:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    d = Path(m.group(1))
    out = (d / "program" / "output.txt").read_text(errors="replace")
    st = next((l for l in out.splitlines() if l.startswith("STATE ")), None)
    if st is None or not list((d / "frames").glob("*_rgb.png")):
        raise SystemExit(f"the look gave no frames or no state: {d}")
    return d, st[6:]


def write(a, look_dir, state):
    """Step 2: one model call -> the directory with program.py (SystemExit if the reply holds no allowed program)."""
    import cv2
    from agentic_robotics.bimanual.practice import RULES, check_program
    from agentic_robotics.bimanual.tasks import TASKS
    from agentic_robotics.bimanual.write import extract_program, instructions
    from agentic_robotics.perception import vlm as V
    if a.ledger:
        V.LEDGER = Path(a.ledger).resolve()
    if a.key_file:
        key = Path(a.key_file).expanduser().resolve()
        V.VLM.key = lambda self: key.read_text().strip()
    task = TASKS["insert_varied"]
    notes = "" if a.rig_notes == "" else Path(a.rig_notes or ROOT / "real" / "astra_skill_rig.md").read_text().strip()
    instr = instructions("grippers", task.time_limit_s, segmenter=True, closing="", arm="left") + ("\n" + notes + "\n" if notes else "") + "\n" + RULES + "\n" + _ONCE
    text = (a.text or task.text) + _ASK.format(state=f"The arm's state: tool point, approach direction, closing direction, opening (mm): {state}")
    frames = sorted((look_dir / "frames").glob("*_rgb.png"))
    images, content = [], [{"type": "input_text", "text": text}]
    for f in frames:
        cam = "scene" if "d415" in f.name else "left_wrist"
        content += [{"type": "input_text", "text": f"{cam}:"}, {"type": "input_image", "image": len(images)}]
        images.append(cv2.imread(str(f))[..., ::-1].copy())
    out = ROOT / "runs" / "bimanual" / "real_cap" / f"{time.strftime('%Y%m%d_%H%M%S')}_{a.tag}"
    out.mkdir(parents=True)
    (out / "prompt.md").write_text("# Instructions\n\n" + instr + "\n# Message\n\n" + text + "\n\n"
                                   + "\n".join(f"[image: {f}]" for f in frames) + "\n")
    vlm = V.VLM(dict(model=a.model, converse=True, max_calls_per_episode=1, max_usd_per_episode=a.max_usd, max_usd_per_call=a.max_usd),
                tag=f"real_cap:{a.backend}:{a.tag}")
    print(f"asking {a.model} for the program (one call, at most ${a.max_usd:g}; this can take some minutes) ...", flush=True)
    r = vlm.respond([{"role": "user", "content": content}], images, instructions=instr, effort=a.effort, max_output_tokens=a.max_output_tokens)
    reply = "" if not r.get("ok") else "\n".join(c.get("text", "") for o in r["output"] if o.get("type") == "message"
                                                  for c in o.get("content") or [] if c.get("type") == "output_text")
    (out / "reply.md").write_text(reply + "\n")
    code, why = (None, f"model: {r.get('why')}") if not r.get("ok") else extract_program(reply)
    bad = check_program(code) if code else []
    info = dict(ok=bool(code) and not bad, why=why or "; ".join(bad), look=str(look_dir), backend=a.backend, text=a.text or task.text,
                status=r.get("status"), program_lines=len((code or "").splitlines()), effort=a.effort or vlm.m.get("effort"), **vlm.usage())
    (out / "write.json").write_text(json.dumps(info, indent=1) + "\n")
    if code:
        (out / "program.py").write_text(code)
    print(json.dumps({k: info[k] for k in ("ok", "why", "program_lines", "usd") if k in info}), flush=True)
    if not info["ok"]:
        raise SystemExit(f"no program to run ({info['why']}): see {out}/reply.md")
    return out


def main():
    argv, rest = sys.argv[1:], []
    if "--" in argv:
        argv, rest = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--text", help="the task as the model is told it (default: the insertion task's text)")
    ap.add_argument("--mode", default="dry", choices=("dry", "live"))
    ap.add_argument("--backend", default="robot", choices=("robot", "sim"))
    ap.add_argument("--seed", type=int, default=2001, help="--backend sim: the scene")
    ap.add_argument("--model", default="astra"); ap.add_argument("--effort", default="high")
    ap.add_argument("--max-usd", type=float, default=3.0); ap.add_argument("--max-output-tokens", type=int, default=40000)
    ap.add_argument("--key-file"); ap.add_argument("--ledger"); ap.add_argument("--rig-notes")
    ap.add_argument("--write-only", action="store_true"); ap.add_argument("--program")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", a.tag):
        raise SystemExit("--tag: letters, digits, _ . -")
    if a.program:
        program = Path(a.program).resolve()
    else:
        d, state = look(a, rest)
        print(f"1. looked (nothing moved): {d}", flush=True)
        out = write(a, d, state)
        program = out / "program.py"
        print(f"2. the program: {program}  (the prompt and the reply are next to it)", flush=True)
    if a.write_only:
        return 0
    cmd = RUN + ["--program", str(program), "--tag", a.tag, "--mode", a.mode, "--backend", a.backend]
    cmd += (["--seed", str(a.seed)] if a.backend == "sim" else []) + rest
    print(f"3. {a.mode} run: {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
