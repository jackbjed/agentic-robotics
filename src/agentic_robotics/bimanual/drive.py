"""Direct control from a shell, one tool call per command: for an agent that acts through a terminal (a coding agent
such as Claude Code) rather than through a model API's tool calls. The same tools, texts, limits and judge as
`run --model` (tools.py, tasks.py); the camera images of a look are written as PNG files for the agent to open.

    python -m agentic_robotics.bimanual.drive DIR start --env sim_bimanual_dexterous --task lift [--seed 0] [--hints]
    python -m agentic_robotics.bimanual.drive DIR call look
    python -m agentic_robotics.bimanual.drive DIR call move_hand '{"hand": "left", "x": -350, "y": 70, "z": 230, ...}'
    python -m agentic_robotics.bimanual.drive DIR judge

start   makes the episode in DIR (a new or empty directory) and prints the task, where the instructions are
        (DIR/instructions.md: read them first) and the first look
call    one tool call (the arguments as a JSON object); prints the result's text and the paths of its images
judge   ends the episode: the judge's verdict on the TRUE final state (DIR/result.json). No calls after it.

Between commands the episode lives in DIR/checkpoint.pkl (the simulation's state, the sensors' random state, the
latest frames, the counters): each command loads it, acts, and saves it, so the episode is the one an uninterrupted
process would have run. DIR/calls.jsonl logs the calls (`run --calls DIR/calls.jsonl --video` replays them on video).
The controlled agent should get DIR's path and these three commands, nothing else of the repository: the checkpoint
holds the true state.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

from .api import Frame
from .tasks import TASKS, Episode
from .tools import Toolbox

USAGE = """# How to act

Each action is one shell command (the arguments as one JSON object, in single quotes):

    {cmd} call TOOL 'ARGS'        e.g.  {cmd} call look
    {cmd} judge                   when the task is done or cannot be done (instead of the done tool)

A call prints the result's text; the camera images of a look are PNG files (paths printed): open them to see.

# The tools (name, description, JSON schema of the arguments)

{tools}
"""


def _load(d):
    meta = json.loads((d / "episode.json").read_text())
    ck = pickle.loads((d / "checkpoint.pkl").read_bytes())
    ep = Episode(meta["env"], meta["task"], seed=meta["seed"], sensors=meta["sensors"], time_limit_s=meta["time_limit_s"])
    box = Toolbox(ep.robot, max_calls=meta["max_calls"], hints=meta["hints"])
    ep.S.set_state(ck["state"])
    if ep.S.dm is not None:
        ep.S.dm.rng.bit_generator.state = ck["sensor_rng"]
    ep.robot.t0 = ck["t0"]
    ep.robot.frames = {c: Frame(o) for c, o in ck["frames"].items()}
    box.n_calls, box.ended, box.log = ck["n_calls"], ck["ended"], ck["log"]
    return meta, ep, box


def _save(d, ep, box):
    ck = dict(state=ep.S.get_state(), sensor_rng=None if ep.S.dm is None else ep.S.dm.rng.bit_generator.state,
              t0=ep.robot.t0, frames={c: f._o for c, f in ep.robot.frames.items()}, n_calls=box.n_calls, ended=box.ended, log=box.log)
    tmp = d / "checkpoint.tmp"
    tmp.write_bytes(pickle.dumps(ck)); tmp.replace(d / "checkpoint.pkl")
    (d / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in box.log))


def _show(d, items, k):
    """The items as text; the images to DIR/looks/."""
    import cv2
    out = []
    for i in items:
        if i["type"] == "text":
            out.append(i["text"])
        else:
            p = d / "looks" / f"{k:03d}_{i['name']}.png"
            p.parent.mkdir(exist_ok=True)
            cv2.imwrite(str(p), i["rgb"][..., ::-1])
            out.append(f"  image: {p}")
    return "\n".join(out)


def start(d, env, task, seed=0, sensors="d415", hints=False, max_calls=None, time_limit_s=None):
    if d.exists() and any(d.iterdir()):
        raise SystemExit(f"{d} is not empty: an episode needs a new directory")
    d.mkdir(parents=True, exist_ok=True)
    t = TASKS[task]
    meta = dict(env=env, task=task, seed=int(seed), sensors=sensors, hints=bool(hints), max_calls=int(max_calls or t.max_calls),
                time_limit_s=float(time_limit_s or t.time_limit_s))
    ep = Episode(env, task, seed=meta["seed"], sensors=sensors, time_limit_s=meta["time_limit_s"])
    box = Toolbox(ep.robot, max_calls=meta["max_calls"], hints=meta["hints"])
    cmd = f"python -m agentic_robotics.bimanual.drive {d}"
    tools = "\n\n".join(f"## {s['name']}\n{s['description']}\n\n    {json.dumps(s['parameters'])}" for s in box.specs)
    (d / "instructions.md").write_text(box.instructions + "\n\n" + USAGE.format(cmd=cmd, tools=tools) + "\n# The task\n\n" + ep.text + "\n")
    (d / "episode.json").write_text(json.dumps(meta, indent=1) + "\n")
    first = _show(d, box.call("look", {}, free=True), 0)
    _save(d, ep, box)
    return f"{ep.text}\n\nInstructions and tools: {d / 'instructions.md'} (read them first).\nBoth arms are at their start poses, open; this is the first look:\n{first}"


def call(d, name, args):
    meta, ep, box = _load(d)
    if (d / "result.json").exists():
        return "the episode has been judged: no more calls"
    try:
        a = json.loads(args) if args else {}
    except json.JSONDecodeError as e:
        a = f"not JSON ({e})"
    out = _show(d, box.call(name, a), len(box.log))
    _save(d, ep, box)
    left = box.max_calls - box.n_calls
    return out + (f"\n{left} tool calls left." if not box.ended and left <= 5 else "")


def judge(d):
    if (d / "result.json").exists():
        return (d / "result.json").read_text().strip()
    meta, ep, box = _load(d)
    v = dict(ep.finish(), mode="drive", ended=box.ended or "judge", sensors=meta["sensors"], tool_calls=box.n_calls, hints=meta["hints"])
    import cv2
    cv2.imwrite(str(d / "final.jpg"), ep.S.render_rec()[..., ::-1])
    (d / "result.json").write_text(json.dumps(v, indent=1) + "\n")
    return json.dumps(v)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--env", required=True, choices=["sim_bimanual_grippers", "sim_bimanual_dexterous"])
    s.add_argument("--task", required=True, choices=list(TASKS))
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--sensors", default="d415", choices=["d415", "ideal"])
    s.add_argument("--hints", action="store_true")
    s.add_argument("--max-calls", type=int)
    s.add_argument("--time-limit", type=float)
    c = sub.add_parser("call")
    c.add_argument("name")
    c.add_argument("args", nargs="?", default="")
    sub.add_parser("judge")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    if a.cmd == "start":
        print(start(d, a.env, a.task, a.seed, a.sensors, a.hints, a.max_calls, a.time_limit))
    elif not (d / "checkpoint.pkl").exists():
        raise SystemExit(f"no episode in {d}: start one first")
    elif a.cmd == "call":
        print(call(d, a.name, a.args))
    else:
        print(judge(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
