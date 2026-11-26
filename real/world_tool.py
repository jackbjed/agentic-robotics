"""A session's WORLD (bimanual worlds: scene(scene, rng), goal(scene, rng), success(state)) for the rehearsal of a
program whose task is the world's own (real/run program --task sweep --backend sim --world FILE). World code is a
session's: like a program it runs only here, inside the program sandbox (real/run.py starts this file there), never in
the operator's process or the episode server's.

    python -I real/world_tool.py scene --world W.py --seed N --out DIR
        the empty table of the seed as the world's scene() fills it -> DIR/scene.json (bimanual/scenes.py's dict: what
        the simulation builds), DIR/task.txt (the world's own task text, its goal(); absent without one), DIR/world.log
    python -I real/world_tool.py judge --world W.py --state STATE.json --out DIR
        the world's success(state) on the state the episode server wrote at the end of the rehearsal (real/server.py
        SimBackend: every body's place, axes, speed and contacts, the joints; the names of practice.world_state)
        -> DIR/world_check.json (a dict with "ok" and what else the check measured)

Needs a checkout whose simulation has worlds (the sim-dev line: bimanual/practice.py _world, bimanual/scenes.py empty)."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:                               # (a worktree runs on the main checkout's environment)
    sys.path.insert(0, str(ROOT / "src"))


class State:
    """practice.world_state's names over a written state (STATE.json): what success(state) reads."""

    def __init__(self, s):
        self.scene, self.start, self.ended, self.time_s = s.get("scene"), s.get("start"), s.get("ended"), s.get("time_s")
        self._s = s

    def _body(self, name):
        b = self._s["bodies"].get(str(name))
        if b is None:
            raise KeyError(f"no body {name!r}: there are {sorted(self._s['bodies'])}")
        return b

    def truth(self):
        return json.loads(json.dumps(self._s["truth"]))

    def pos(self, name):
        return list(self._body(name)["pos"])

    def points(self, name):
        return [list(p) for p in self._body(name)["points"]]

    def rot(self, name):
        r = self._body(name)["rot"]
        if r is None:
            raise KeyError(f"{name!r} is a composite of several bodies: it has a centre (pos), its bodies' places (points), no axes")
        return [list(row) for row in r]

    def speed(self, name):
        return float(self._body(name)["speed"])

    def touching(self, name):
        return json.loads(json.dumps(self._body(name)["touching"]))

    def held(self, name):
        t = self.touching(name)
        return bool(t["robot"]) and not t["others"]

    def joint(self, name):
        if str(name) not in self._s["joints"]:
            raise KeyError(f"no slide or hinge joint {name!r} in the world's MJCF")
        return float(self._s["joints"][str(name)])

    def simulator(self):
        return json.loads(json.dumps(self._s.get("simulator") or dict(ok=False)))


def _load(path, practice, log):
    """The world's namespace after the platform's own gate (practice.check_world), or SystemExit."""
    code = Path(path).read_text()
    bad = practice.check_world(code)
    if bad:
        raise SystemExit("the world is refused: " + "; ".join(bad))
    ns = {"__name__": "world"}
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        exec(compile(code, "world.py", "exec"), ns)
    return ns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("scene", "judge"))
    ap.add_argument("--world", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--state")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import numpy as np
    from agentic_robotics.bimanual import practice, scenes
    if not hasattr(practice, "_world") or not hasattr(scenes, "empty"):
        raise SystemExit("this checkout's simulation has no worlds (bimanual/practice.py _world): a world's rehearsal runs from "
                         "a checkout of the sim-dev line")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    with open(out / "world.log", "a", buffering=1) as log:
        if a.cmd == "scene":
            wns, scene, why = practice._world(a.world, scenes.empty(a.seed), a.seed, log)
            if why:
                raise SystemExit(f"the world cannot be used: {why} ({out / 'world.log'})")
            if "goal" in wns:                                       # (a task of the world's own: its text, as an episode gives it)
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    try:
                        shown = getattr(practice, "scene_shown", lambda s: s)(scene)
                        text = wns["goal"](json.loads(json.dumps(shown)), np.random.default_rng([int(a.seed), 79]))
                    except (Exception, SystemExit) as e:
                        traceback.print_exc(limit=-6)
                        raise SystemExit(f"the world's goal() failed: {type(e).__name__}: {str(e)[:200]}")
                if not isinstance(text, str) or not text.strip():
                    raise SystemExit("the world's goal() gave no text")
                (out / "task.txt").write_text(text)
            (out / "scene.json").write_text(json.dumps(scene) + "\n")
        else:
            wns = _load(a.world, practice, log)
            if "success" not in wns:
                raise SystemExit("the world has no success(state): nothing to judge with")
            state = State(json.loads(Path(a.state).read_text()))
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                r = practice.world_check(wns["success"], state)
            (out / "world_check.json").write_text(json.dumps(r, indent=1) + "\n")
            print(json.dumps(r))


if __name__ == "__main__":
    main()
