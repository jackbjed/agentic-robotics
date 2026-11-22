"""Run one episode of a bimanual task (docs/bimanual.md) and judge it.

    python -m agentic_robotics.bimanual.run --env sim_bimanual_grippers --task lift --scripted
    python -m agentic_robotics.bimanual.run --env sim_bimanual_dexterous --task open_bottle --program prog.py --out runs/x
    python -m agentic_robotics.bimanual.run --env ... --task ... --calls calls.jsonl --video --out runs/y
    python -m agentic_robotics.bimanual.run --env ... --task ... --model astra [--hints] --out runs/z      (PAID)

Who controls the robot:
    --scripted        the reference solution (scripted.py): it reads the simulation's TRUE state; it shows that the task
                      can be done in that environment, it is not a baseline
    --program FILE    code: FILE defines run(robot, task), robot an api.Robot, task the task's text. The program runs in
                      this process (not sandboxed) and ends when run() returns or the robot time is spent
    --calls FILE      tool calls replayed from a jsonl file ({"name": ..., "args": {...}} per line), e.g. the calls.jsonl
                      of an earlier episode (to make its video)
    --model KEY       direct control by a model of configs/models.yaml through the tools (tools.py), one tool call at a
                      time, on the OpenAI Responses API (perception/vlm.py: the key file, the price table and the
                      budget ledger of the platform). This costs money: --max-usd caps the episode.

--out DIR gets result.json (the judge's verdict on the TRUE final state, and how the episode went), calls.jsonl and
messages.jsonl (tool modes), final.jpg and, with --video, video.mp4.
"""
from __future__ import annotations

import argparse
import json
import runpy
import sys
import time
import traceback
from pathlib import Path

from .api import TimeUp
from .tasks import TASKS, Episode
from .tools import Toolbox, openai_items, openai_tools, text

FIRST_LOOK = " Both arms are at their start poses, open; this is the first look:"
NUDGE = "Continue with the tools; call done() when the task is finished or cannot be done."


def run_scripted(ep, log=print):
    from .core import Stop
    from .scripted import SOLUTIONS
    try:
        SOLUTIONS[ep.task.name](ep.S, log)
    except Stop as e:
        return f"stop: {e}"
    return "returned"


def run_program(ep, path, log=print):
    """FILE's run(robot, task) on the episode's robot -> how it ended."""
    fn = runpy.run_path(str(path)).get("run")
    if not callable(fn):
        raise SystemExit(f"{path} does not define run(robot, task)")
    try:
        fn(ep.robot, ep.text)
    except TimeUp as e:
        return f"time: {e}"
    except Exception as e:                                          # the program's own failure: the scene is judged as it is
        log(traceback.format_exc())
        return f"error: {type(e).__name__}: {e}"
    return "returned"


def read_calls(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def replay(box, calls, log=print):
    """Tool calls ({"name", "args", optionally "free"}) through the toolbox, until they or the episode end."""
    for c in calls:
        items = box.call(c["name"], c.get("args") or {}, free=bool(c.get("free")))
        log(f"> {c['name']} {json.dumps(c.get('args') or {})[:200]}\n  {_text(items)[:300]}")
        if box.ended:
            break
    return box.ended or "calls replayed"


def run_model(ep, box, vlm, log=print, effort=None, messages=None, max_output_tokens=None):
    """Direct control: the model gets the task, a first look and the tools, and acts one call at a time until it calls
    done(), runs out of tool calls / robot time / budget, or stops calling tools (3 replies in a row without a call). vlm: perception.vlm.VLM (or anything
    with its respond()). -> how it ended."""
    images = []
    inp = [{"role": "user", "content": [{"type": "input_text", "text": ep.text + FIRST_LOOK}]
            + openai_items(box.call("look", {}, free=True), images)}]
    tools, prev, nudges = openai_tools(box.specs), None, 0
    while not box.ended:
        r = vlm.respond(inp, images, instructions=box.instructions, tools=tools, previous=prev, effort=effort,
                        max_output_tokens=max_output_tokens)
        if not r.get("ok"):
            return f"model: {r.get('why')}"
        prev = r["id"]
        calls = [o for o in r["output"] if o.get("type") == "function_call"]
        for o in r["output"]:
            if o.get("type") == "message":
                t = " ".join(c.get("text", "") for c in o.get("content") or [] if c.get("type") == "output_text").strip()
                if t:
                    log(f"model: {t[:600]}")
                    if messages is not None:
                        messages.append(dict(after_call=box.n_calls, text=t))
        inp, images = [], []
        if not calls:
            nudges += 1
            if nudges > 2:
                return "model: stopped calling tools"
            inp = [{"role": "user", "content": [{"type": "input_text", "text": NUDGE}]}]
            continue
        nudges = 0
        for c in calls:
            try:
                args = json.loads(c.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = "not JSON"
            items = box.call(c.get("name"), args)
            log(f"> {c.get('name')} {str(c.get('arguments'))[:200]}\n  {_text(items)[:300]}")
            left = box.max_calls - box.n_calls
            if not box.ended and left <= 5:
                items.append(text(f"{left} tool calls left."))
            inp.append({"type": "function_call_output", "call_id": c.get("call_id"), "output": openai_items(items, images)})
    return box.ended


def _text(items):
    return " ".join(i["text"] for i in items if i["type"] == "text")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", required=True, choices=["sim_bimanual_grippers", "sim_bimanual_dexterous"])
    ap.add_argument("--task", required=True, choices=list(TASKS))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--sensors", default="d415", choices=["d415", "ideal"])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--scripted", action="store_true")
    who.add_argument("--program")
    who.add_argument("--calls")
    who.add_argument("--model")
    ap.add_argument("--hints", action="store_true", help="tool modes, dexterous: add the known grasps to the instructions")
    ap.add_argument("--segmenter", choices=["sam3"], help="--program: robot.segment() through the SAM3 service")
    ap.add_argument("--max-usd", type=float, default=3.0, help="--model: this episode's budget")
    ap.add_argument("--effort", help="--model: reasoning effort (default: the model's in configs/models.yaml)")
    ap.add_argument("--max-output-tokens", type=int, help="--model: per reply, reasoning included (default: the model's)")
    ap.add_argument("--time-limit", type=float, help="robot seconds (default: the task's)")
    ap.add_argument("--max-calls", type=int, help="tool calls (default: the task's)")
    ap.add_argument("--video", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    log = (lambda *x: None) if a.quiet else (lambda *x: print(*x, flush=True))
    seg = None
    if a.segmenter:
        from ..perception import sam3 as seg
    t0 = time.time()
    ep = Episode(a.env, a.task, seed=a.seed, sensors=a.sensors, record=a.video, time_limit_s=a.time_limit, segmenter=seg)
    box, extra, messages = None, {}, []
    if a.scripted:
        mode, how = "scripted", run_scripted(ep, log)
    elif a.program:
        mode, how = "program", run_program(ep, a.program, log)
    else:
        box = Toolbox(ep.robot, max_calls=a.max_calls or ep.task.max_calls, hints=a.hints)
        if a.calls:
            mode, how = "calls", replay(box, read_calls(a.calls), log)
        else:
            from ..perception.vlm import VLM
            vlm = VLM(dict(model=a.model, converse=True, max_calls_per_episode=box.max_calls + 8, max_usd_per_episode=a.max_usd,
                           max_usd_per_call=min(a.max_usd, 0.5)), tag=f"bimanual_{a.task}:{a.env}:{a.seed}")
            mode, how = "model", run_model(ep, box, vlm, log, effort=a.effort, messages=messages,
                                             max_output_tokens=a.max_output_tokens)
            extra = dict(vlm.usage(), effort=a.effort or vlm.m.get("effort"))
    v = dict(ep.finish(), mode=mode, ended=how, sensors=a.sensors, wall_s=round(time.time() - t0, 1), **extra)
    if box is not None:
        v.update(tool_calls=box.n_calls, hints=bool(a.hints))
    if a.out:
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps(v, indent=1) + "\n")
        if box is not None:
            (out / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in box.log))
            (out / "messages.jsonl").write_text("".join(json.dumps(m) + "\n" for m in messages))
        import cv2
        cv2.imwrite(str(out / "final.jpg"), ep.S.render_rec()[..., ::-1])
        if a.video:
            ep.S.write_video(out / "video.mp4")
    print(json.dumps(v))
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
