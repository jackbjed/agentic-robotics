"""One PRACTICE episode in the TARGET SIMULATOR (condition F: oracle-simulator practice): the target's own things, robot,
sensors and physics, in a scene the developer asked for or a fresh one, judged by the developer's OWN self-check.

    <maniskill python> -I xsim/bi/oracle_run.py --setup PRACTICE.py --seed 3 --program P.py --out DIR --private DIR
        --task-text-file T.txt [--validate] [--time-limit 40] [--no-segmenter] [--run-id ID]

PRACTICE.py is the developer's practice file: scene(scene, rng) -> scene (optional: `scene` arrives as a fresh scene for
the seed, sweep_blocks/scene_gen.py; what it returns is what is built, if it is valid) and success(state) -> dict
(optional: the developer's self-check, from the TRUE state of the practice scene). It runs in a sandbox of its own
(m2m/boxed), as a practice-world file does; the skill program runs in another, with the documented robot only.

What this runner does NOT do: ask the target's judge. The official test (sweep_blocks/env.py info()) neither ends a
practice episode nor appears in anything written to --out: the episode ends when the program returns, when the
developer's self-check says so, when the robot time is spent, or on an error. What the judge would have said is kept
in --private only (official.json), for the experiment's own analysis; it is never returned.

--out (what the developer gets): result.json (the self-check's verdict, how the program ended, times), output.txt (the
program's prints, every command, and the TRUE positions of the things: privileged state is the developer's to see, not
the executing skill's), scene.json (the scene as built), shown/, videos/, frames/.
--private: layout.json (the simulator's layout of the scene), official.json."""
import argparse
import contextlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))

SIM = "maniskill (the target simulator, practice)"


def check_practice(code):
    """-> the reasons a practice file is not taken ([]: none). Its rules are a program's (imports, no files)."""
    import ast
    from m2m import api
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"line {e.lineno}: syntax error: {e.msg}"]
    defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    bad = []
    if not defs & {"scene", "success"}:
        bad.append("it defines neither scene(scene, rng) nor success(state)")
    for n in ("robot", "goal", "run"):
        if n in defs:
            bad.append(f"it defines {n}(...): a practice file holds scene(scene, rng) and success(state) only")
    bad += [b for b in api.check_program(code, needs=("scene", "success")) if "defines none of" not in b]
    return bad[:12]


class TargetState:
    """What the developer's success(state) gets in target-simulator practice: the TRUE state of the practice scene's
    things, by the same queries as a practice world's state (xsim/bi/mj.py State): mm, table frame."""

    def __init__(self, env, table_z):
        from sweep_blocks import scene_gen as G
        self._e, self._z = env, table_z
        self._sim = {n: G.SIM_NAME.get(n, n) for n in G.NAMES}
        self.names = list(G.NAMES)
        self._start = {n: env.pose(s) for n, s in self._sim.items()}
        self.start = {n: [round(float(v) * 1000, 2) for v in (p - [0, 0, table_z])] for n, (p, _) in self._start.items()}
        self._prev, self._speed = {n: p.copy() for n, (p, _) in self._start.items()}, {n: 0.0 for n in self.names}

    def tick(self, dt):                                             # (once per action: the things' speeds)
        for n, s in self._sim.items():
            p = self._e.pose(s)[0]
            self._speed[n] = float(np.linalg.norm(p - self._prev[n])) / dt * 1000; self._prev[n] = p

    def _obj(self, name):
        if name not in self._sim:
            raise KeyError(f"no thing called {name!r}: the things are {self.names}")
        return self._e.U.objects[self._sim[name]]

    def pos(self, name):
        self._obj(name)
        return [round(float(v) * 1000, 2) for v in (self._e.pose(self._sim[name])[0] - [0, 0, self._z])]

    def rot(self, name):
        self._obj(name)
        return [[round(float(v), 5) for v in row] for row in self._e.pose(self._sim[name])[1]]

    def start_rot(self, name):
        self._obj(name)
        return [[round(float(v), 5) for v in row] for row in self._start[name][1]]

    def _force(self, a, b):
        return float(np.linalg.norm(self._e.U.scene.get_pairwise_contact_forces(a, b)[0].cpu().numpy()))

    def touching(self, name):
        o = self._obj(name); e = self._e
        robot = any(self._force(o, l) > 0.01 for l in e.robot.get_links() if l.name[:2] in ("L_", "R_"))
        others = [n for n in self.names if n != name and self._force(o, e.U.objects[self._sim[n]]) > 0.01]
        if self._force(o, e.U.world) > 0.01:
            others.append("table")
        return dict(robot=bool(robot), others=sorted(others))

    def speed(self, name):
        self._obj(name)
        return round(self._speed[name], 2)

    def held(self, name):
        """Is the thing squeezed between the two fingers of one gripper (each presses with more than 1 N, from opposite sides)?"""
        o = self._obj(name); sc = self._e.U.scene
        for s, word in (("L", "left"), ("R", "right")):
            fa, fb = [sc.get_pairwise_contact_forces(o, f)[0].cpu().numpy().astype(float) for f in self._e.U.agent.fingers[s]]
            if min(np.linalg.norm(fa), np.linalg.norm(fb)) > 1.0 and float(fa @ fb) < 0:
                return word
        return None

    def arm(self, side):
        from x5 import spec as S
        from sweep_blocks.program_api import MAX_OPEN
        s = {"left": "L", "right": "R"}[side]
        p, R = self._e.endpose(s); p0, R0 = self._e.origin[s]
        q = self._e.q()[6 if s == "L" else 14]
        ang = math.degrees(math.acos(float(np.clip((np.trace(R0.T @ R) - 1) / 2, -1, 1))))
        return dict(tcp_mm=[round(float(v) * 1000, 1) for v in (p + R[:, 0] * S.TCP_X - [0, 0, self._z])], opening_mm=round(float(np.clip(2 * q + 0.0008, 0, MAX_OPEN)) * 1000, 1),
                    from_start_mm=round(float(np.linalg.norm(p - p0)) * 1000, 1), from_start_deg=round(ang, 1))


def episode(setup, seed, program, out, private, task_text, time_limit=None, segmenter=True, validate=False, run_id=None, layout=None):
    """layout: (the harness's own tests only) a prepared simulator layout in place of the practice file's scene."""
    from m2m import api
    from m2m.boxed import host as boxed
    from sweep_blocks import env as E
    from sweep_blocks import scene_gen as G
    from sweep_blocks.looks import SweepBlocks
    from sweep_blocks.program_api import ACTION_S, TABLE_Z, TIME_LIMIT_S, X5Robot, program_robot
    from xsim import provenance
    from xsim.bi import media
    out, private = Path(out), Path(private); out.mkdir(parents=True, exist_ok=True); private.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    res = dict(seed=int(seed), simulator=SIM, ok=False, world_check=None, robot_s=0.0, commands=0,
               provenance=provenance.stamp(run_id, program, setup, seed=int(seed), started=round(t0, 1)))

    def stop(why):
        res.update(ended=why, wall_s=round(time.time() - t0, 1))
        (out / "output.txt").write_text("# " + why + "\n"); (out / "result.json").write_text(json.dumps(res, indent=1))
        return res
    code = Path(setup).read_text()
    bad = check_practice(code)
    if bad:
        return stop("the practice file is not taken: " + "; ".join(bad))
    wd = None
    try:
        wd = boxed.World(code)
        fresh = G.sample(np.random.default_rng([int(seed), 0x5EED]))          # (the generator's own stream for this seed)
        sc = wd.scene(fresh, seed) if wd.has_scene else fresh
    except boxed.WorldFailure as e:
        if wd is not None:
            wd.close()
        return stop(f"the practice file's scene() failed: {e.kind}: {e.msg[:300]}\n" + e.tb[-700:])
    if layout is None:
        bad = G.check(sc)
        if bad:
            wd.close()
            return stop("the scene asked for is not valid: " + "; ".join(bad))
        L = G.to_layout(sc)
    else:
        L = json.loads(Path(layout).read_text()); sc = G.from_layout(L)
    (private / "layout.json").write_text(json.dumps(L)); (out / "scene.json").write_text(json.dumps(sc, indent=1))
    res["scene"] = sc

    class PracticeEnv(SweepBlocks):
        """The target's simulation; the official judge neither ends an episode nor is reported (see the module's text)."""
        self_done = False

        def step(self, action):
            if self.done:
                return self.observe(), True, {}
            obs, _, info = super().step(action)                    # (the parent asks the judge and would end the episode on its yes)
            if info.get("success") and official["first_success_action"] is None:
                official["first_success_action"] = self.steps
            official["last"] = info
            self.success = bool(self.self_done)
            self.done = bool(self.self_done) or self.steps >= E.STEP_LIMIT
            return obs, self.done, {}
    official = dict(first_success_action=None, last=None)
    env = PracticeEnv(layout=str(private / "layout.json"), cameras=())
    state = TargetState(env, TABLE_Z)
    moved, bad = G.rests(sc, {n: env.pose(s) for n, s in state._sim.items()}) if layout is None else ({}, [])
    res["settle"] = moved
    if bad:
        env.close(); wd.close()
        return stop("the scene does not rest as it was asked for: " + "; ".join(bad))
    seg = None
    if segmenter:
        from m2m.seg import Segmenter
        seg = Segmenter()
        if not seg.alive():
            raise RuntimeError(f"the SAM3 text service does not answer at {seg.url}")
    rec = media.Recorder()

    def head():
        U = env.U
        U.scene.update_render(update_sensors=True, update_human_render_cameras=False)
        s = U._sensors["head"]; s.capture()
        col = s.camera.get_picture("Color"); pos = s.camera.get_picture("Position")
        col = (col[0] if isinstance(col, (list, tuple)) else col).cpu().numpy(); pos = (pos[0] if isinstance(pos, (list, tuple)) else pos).cpu().numpy()
        col = col[0] if col.ndim == 4 else col; pos = pos[0] if pos.ndim == 4 else pos
        z = (-pos[..., 2]).astype(np.float64) * 1000.0
        return np.ascontiguousarray((np.clip(col[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)), np.where((z > 5) & (z < 9000), z, np.nan)
    succ, won = wd if wd.has_success else None, {}

    def on_step():
        t = env.steps * ACTION_S
        state.tick(ACTION_S)
        if env.steps % media.OVER_EVERY == 0:
            rec.add_overview(t, env.image("overview"))
        if env.steps % media.HEAD_EVERY == 0:
            rec.add_head(t, *head())
        if succ is not None and not env.done and not validate:     # (the developer's own check ends the episode, as in a practice world)
            try:
                yes, c = succ.success(state)
                if yes:
                    won["check"] = c if isinstance(c, dict) else dict(ok=True)
                    env.self_done = True; env.success = env.done = True
            except boxed.WorldFailure:
                pass
    robot = X5Robot(env, segmenter=seg, on_step=on_step)
    limit = float(time_limit or TIME_LIMIT_S)
    robot.env_limit = staticmethod(lambda: int(round(limit / ACTION_S)))
    truth = lambda: {n: [round(float(v) * 1000, 1) for v in (env.pose(s)[0] - [0, 0, TABLE_Z])] for n, s in state._sim.items()}
    pcode = Path(program).read_text()
    rec.add_overview(0.0, env.image("overview")); rec.add_head(0.0, *head())
    with open(out / "output.txt", "w", buffering=1) as log:
        log.write(f"# practice in the target simulator: practice file {Path(setup).name}, scene {seed}\n# the scene as built: {json.dumps(sc)}\n"
                  f"# while the scene settled, its things moved (mm): {json.dumps(moved)}\n")
        given, counts = program_robot(robot, out, trace=log, truth=truth)
        wd.sink = log.write
        bad = api.check_program(pcode)
        if bad:
            how = "refused: " + "; ".join(bad)
        else:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                how = boxed.run_program(pcode, given, task_text, log)
        if won:
            chk = json.loads(json.dumps(dict(won["check"], held_at_s=round(env.steps * ACTION_S, 2)), default=str))
        elif wd.has_success:
            try:
                yes, chk = wd.success(state)
                if not isinstance(chk, dict):
                    chk = dict(ok=bool(yes))
            except boxed.WorldFailure as e:
                chk = dict(ok=False, error=f"your success() failed: {e.kind}: {e.msg[:200]}")
        else:
            chk = dict(ok=False, note="this practice file has no self-check")
        log.write(f"# ended: {how}\n# your own success(): {json.dumps(chk)}\n# TRUE positions at the end (mm): {json.dumps(truth())}\n")
        wd.sink = None
    wd.close()
    rec.add_overview(env.steps * ACTION_S, env.image("overview")); rec.add_head(env.steps * ACTION_S, *head())
    rec.save(out)
    (private / "official.json").write_text(json.dumps(dict(
        note="what the target's judge would have said of this PRACTICE episode: kept for the experiment's analysis, never returned to the developer",
        first_success_action=official["first_success_action"], at_the_end=env.info(), actions=env.steps), indent=1))
    res.update(ok=bool(chk.get("ok")), world_check=chk, ended=how, robot_s=round(env.steps * ACTION_S, 2), commands=counts["commands"], shown=counts["shown"],
               segment_calls=(seg.calls if seg else 0), wall_s=round(time.time() - t0, 1))
    (out / "result.json").write_text(json.dumps(res, indent=1))
    env.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", required=True); ap.add_argument("--seed", type=int, required=True); ap.add_argument("--program", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--private", required=True); ap.add_argument("--task-text-file"); ap.add_argument("--time-limit", type=float)
    ap.add_argument("--no-segmenter", action="store_true"); ap.add_argument("--validate", action="store_true"); ap.add_argument("--run-id")
    ap.add_argument("--layout", help="(the harness's own tests only) a prepared layout in place of the practice file's scene")
    a = ap.parse_args()
    text = Path(a.task_text_file).read_text() if a.task_text_file else ""
    r = episode(a.setup, a.seed, a.program, a.out, a.private, text, a.time_limit, segmenter=not a.no_segmenter, validate=a.validate, run_id=a.run_id, layout=a.layout)
    print("XSIM_JSON " + json.dumps({k: r.get(k) for k in ("ok", "ended", "robot_s", "world_check", "commands", "wall_s")}))


if __name__ == "__main__":
    main()
