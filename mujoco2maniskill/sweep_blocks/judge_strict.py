"""SENSITIVITY ANALYSES of a sweep_blocks success, separately named and versioned. They are NOT the judge: the
experiment's definition of success is and stays the original judge (sweep_blocks/env.py info(), "judge 1": what the
task text states and what every session was given feedback from). Nothing here changes that judge or replaces a
score; what is here is reported NEXT to it, for every condition alike:

    settled-and-resting    clauses 1 and 2 below (verdict()["settled_and_resting"])
    no-robot-contact       clause 3 below (verdict()["no_robot_contact"])

What judge 1 tests (env.py info(), evaluated after every action; the episode ends, a success, the first time all hold):
  - every cube's CENTRE, seen from above, is inside one of the dustpan's support circles or over its handle's
    rectangle: the XY position only (no height, no contact with the pan, no speed);
  - the dustpan: origin not more than 10 mm above where it started, tilted by less than 5 degrees;
  - the brush's origin more than 100 mm further along +x than the dustpan's origin (nothing about where the brush lies:
    on the table, on something, in a gripper);
  - both hands within 150 mm (each axis) and 20 degrees of their start poses; both grippers open by more than 70 mm;
  - no cube was ever squeezed between the two fingers of one gripper (each more than 1 N, opposite, 3 actions running).
What it therefore lets through: a cube that is above the pan but not in it (on the brush, on another cube, on the rim);
a cube that is passing over the pan at that instant (no settling interval); cubes pushed in with a closed gripper, an
arm or the pan itself, the brush unused.

Strict reading, version "strict-1" (verdict() below), applied to an episode judge 1 called a success:
  1. HELD   the simulation runs on for SETTLE_S with the robot commanded to stay where it is; judge 1's conditions must
            hold at every action of that interval;
  2. RESTS  at its end every cube moves slower than REST_MM_S and its lowest face is between REST_LOW_MM and
            REST_HIGH_MM above the table (on the pan's floor, ramp or ridge; not on the table, not on top of something);
  3. SWEPT  before the success no cube was pushed by the robot itself: no link of either arm pressed on a cube with
            more than PUSH_N for PUSH_ACTIONS actions running (a brush in the gripper pressing on a cube is not that).
Each can only say of a judge-1 success that it does not also meet a further condition; it needs the episode to be
simulated again with its contacts recorded (record() below; on the scenes tested, the target replayed exactly: see
docs/AUDIT_2026-11-27.md). Clause 3 as written also counts a brief contact of the fingers that hold the brush: it
does not by itself mean that a cube was pushed in by the gripper (the trace record() keeps has each cube's travel
during such a contact)."""
from __future__ import annotations

STRICT_VERSION = "strict-1"
ACTION_S = 0.04
SETTLE_S, REST_MM_S, REST_LOW_MM, REST_HIGH_MM, PUSH_N, PUSH_ACTIONS = 1.0, 10.0, 2.0, 20.0, 1.0, 3


def verdict(trace):
    """trace: one record per action, in order, INCLUDING the settling interval after judge 1's first success:
    dict(v1=bool, cubes={name: dict(bottom_mm, speed_mm_s, robot_n, brush_n)}). -> dict(v1, strict, held, rests, swept, why)."""
    k = next((i for i, r in enumerate(trace) if r["v1"]), None)
    if k is None:
        return dict(version=STRICT_VERSION, v1=False, settled_and_resting=False, no_robot_contact=None, strict=False, held=None, rests=None, swept=None,
                    why=["judge 1: no success"])
    n = int(round(SETTLE_S / ACTION_S)); why = []
    window = trace[k:k + n + 1]
    held = len(window) == n + 1 and all(r["v1"] for r in window)
    if not held:
        j = next((i for i, r in enumerate(window) if not r["v1"]), len(window))
        why.append(f"not held: judge 1's conditions stopped holding {j * ACTION_S:.2f} s after the success" if j < len(window) else "not held: the settling interval was not simulated")
    last = window[-1]["cubes"]
    slow = {c: v["speed_mm_s"] < REST_MM_S for c, v in last.items()}
    low = {c: REST_LOW_MM <= v["bottom_mm"] <= REST_HIGH_MM for c, v in last.items()}
    rests = all(slow.values()) and all(low.values())
    why += [f"{c} still moves ({last[c]['speed_mm_s']:.0f} mm/s)" for c, ok in slow.items() if not ok]
    why += [f"{c} does not rest in the pan (its lowest face {last[c]['bottom_mm']:.0f} mm above the table)" for c, ok in low.items() if not ok]
    pushed = {}
    for c in trace[0]["cubes"]:
        run = best = 0
        for r in trace[:k + 1]:
            run = run + 1 if r["cubes"][c]["robot_n"] > PUSH_N else 0
            best = max(best, run)
        pushed[c] = best
    swept = all(v < PUSH_ACTIONS for v in pushed.values())
    why += [f"{c} was pushed by the robot itself ({v} actions running)" for c, v in pushed.items() if v >= PUSH_ACTIONS]
    return dict(version=STRICT_VERSION, v1=True, v1_at_action=k + 1, settled_and_resting=bool(held and rests), no_robot_contact=bool(swept),
                strict=bool(held and rests and swept), held=bool(held), rests=bool(rests), swept=bool(swept), robot_push_actions=pushed, why=why)


def record(layout, program, out, task_text, segmenter=True):
    """Simulate an episode again in the target, exactly as the evaluator does (xsim/bi/target_run.py: the same robot, the
    program in its sandbox), recording after every action what verdict() needs; after judge 1's success the simulation
    runs on for SETTLE_S with the robot's targets unchanged. -> dict(result, strict). Writes out/strict.json."""
    import json
    import time
    from pathlib import Path

    import numpy as np

    from m2m import api
    from m2m.boxed.host import run_program
    from sweep_blocks import env as E
    from sweep_blocks.looks import SweepBlocks
    from sweep_blocks.program_api import TABLE_Z, X5Robot, program_robot
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    env = SweepBlocks(layout=str(layout), cameras=())
    seg = None
    if segmenter:
        from m2m.seg import Segmenter
        seg = Segmenter()
    sc, U = env.U.scene, env.U
    links = [l for l in env.robot.get_links() if l.name[:2] in ("L_", "R_")]
    half = {o["label"]: float(o.get("half", [0.0096])[2] if isinstance(o.get("half"), list) else 0.0096) for o in env.layout["Rigid"]["small_cube"]}
    scale = float((env.layout.get("Variant") or {}).get("cube_scale", 1.0))
    trace, prev = [], {}

    def rec():
        cubes = {}
        for n in env.cubes:
            p = env.pose(n)[0]
            f = lambda other: float(np.linalg.norm(sc.get_pairwise_contact_forces(U.objects[n], other)[0].cpu().numpy()))
            v = float(np.linalg.norm(p - prev[n]) / ACTION_S * 1000) if n in prev else 0.0
            prev[n] = p
            cubes[n] = dict(xy_mm=[round(float(p[0]) * 1000, 1), round(float(p[1]) * 1000, 1)], bottom_mm=round(float(p[2] - TABLE_Z) * 1000 - 9.62 * scale, 2), speed_mm_s=round(v, 1),
                            robot_n=round(max(f(l) for l in links), 2), brush_n=round(f(U.objects["broom"]), 2), pan_n=round(f(U.objects["broom_shovel"]), 2))
        trace.append(dict(v1=bool(env.info()["success"]), cubes=cubes))
    robot = X5Robot(env, segmenter=seg, on_step=rec)
    code = Path(program).read_text()
    t0 = time.time()
    with open(out / "output.txt", "w", buffering=1) as log:
        given, counts = program_robot(robot, out, trace=log, truth=None)
        how = "refused" if api.check_program(code) else run_program(code, given, task_text, log)
    info = env.info(); v1 = bool(env.success or info["success"]); steps = env.steps
    if v1:                                                          # the settling interval: the robot's targets stay as they are
        for _ in range(int(round(SETTLE_S / ACTION_S))):
            for _i in range(E.ACTION_STEPS):
                env._sim(env.target)
            rec()
    res = dict(layout=str(layout), success=v1, ended=how, robot_s=round(steps * ACTION_S, 2), actions=steps, commands=counts["commands"],
               segment_calls=(seg.calls if seg else 0), wall_s=round(time.time() - t0, 1))
    s = verdict(trace) if trace else dict(version=STRICT_VERSION, v1=False, strict=False, why=["no action"])
    k = s.get("v1_at_action")
    summary = dict(result=res, strict=s, at_success={n: trace[k - 1]["cubes"][n] for n in env.cubes} if k else None, after_settling=trace[-1]["cubes"] if v1 else None,
                   max_robot_n={n: max(r["cubes"][n]["robot_n"] for r in trace) for n in env.cubes} if trace else None,
                   actions_with_brush_contact={n: sum(1 for r in trace if r["cubes"][n]["brush_n"] > 0.2) for n in env.cubes} if trace else None,
                   actions_with_robot_contact={n: sum(1 for r in trace if r["cubes"][n]["robot_n"] > 0.2) for n in env.cubes} if trace else None)
    (out / "strict.json").write_text(json.dumps(summary, indent=1))
    (out / "trace.json").write_text(json.dumps(trace))              # (every action: each cube's place, height, speed and contact forces)
    env.close()
    return summary


if __name__ == "__main__":
    import argparse
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True); ap.add_argument("--program", required=True); ap.add_argument("--out", required=True); ap.add_argument("--task-text-file")
    a = ap.parse_args()
    r = record(a.layout, a.program, a.out, Path(a.task_text_file).read_text() if a.task_text_file else "")
    print("STRICT_JSON " + json.dumps(dict(result=r["result"], strict=r["strict"])))
