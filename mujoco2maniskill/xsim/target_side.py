"""The target environment's own jobs that are not skill episodes (ManiSkill environment; never run by an agent):

    <robomme python> -I xsim/target_side.py validate --scene F.json --rules RULES.json --required w_cyl w_stand   -> JSON on stdout
    <robomme python> -I xsim/target_side.py look --scene F.json --out DIR          the first look given to every condition

validate applies the frozen admission rules to one configuration: nothing moves or tips while the scene settles (so it
is stable and nothing started interpenetrating), the required things are inside the arm's working area, and the task
is not already done. It does NOT show that the task can be solved there."""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def validate(scene, rules, required):
    import run_episode as RE
    from m2m import api
    from m2m.rig import Rig
    scene = Path(scene); spec = json.loads(scene.read_text())
    rig = Rig(spec, scene.parent, seed=int(spec["seed"]), min_depth=0.0, settle=0)
    names = [n for n in rig.names() if n.startswith("w_")]
    p0 = {n: rig.body_pose(n) for n in names}
    rig.run(float(rules["settle_s"]))
    why, worst = [], dict(move_mm=0.0, tilt_deg=0.0)
    for n in names:
        p, R = rig.body_pose(n)
        mv = float(np.linalg.norm(p - p0[n][0]) * 1000)
        tilt = math.degrees(math.acos(float(np.clip((np.trace(p0[n][1].T @ R) - 1) / 2, -1, 1))))
        worst["move_mm"] = max(worst["move_mm"], mv); worst["tilt_deg"] = max(worst["tilt_deg"], tilt)
        if mv > rules["max_move_mm"] or tilt > rules["max_tilt_deg"]:
            why.append(f"{n} moved {mv:.1f} mm / turned {tilt:.1f} deg while settling")
    for n in required:
        if n not in names:
            why.append(f"{n} is missing"); continue
        x, y = rig.body_pose(n)[0][:2] * 1000
        if not (rules["reach_x_mm"][0] <= x <= rules["reach_x_mm"][1] and rules["reach_y_mm"][0] <= y <= rules["reach_y_mm"][1]):
            why.append(f"{n} at ({x:.0f}, {y:.0f}) is outside the working area")
    wcode = Path(spec["world"]).read_text()
    wns = {"__name__": "world"}
    exec(compile(wcode, "world.py", "exec"), wns)
    wns["scene"](json.loads(json.dumps(spec["shown_in"])), np.random.default_rng([int(spec["seed"]), 77]))
    robot = api.Robot(rig, time_limit_s=10.0)
    start = {n: [round(float(v) * 1000, 1) for v in rig.body_pose(n)[0]] for n in names}
    chk = RE.world_check(wns["success"], RE.world_state(rig, robot, spec, start, None))
    if chk.get("ok"):
        why.append("the task is already done at the start")
    if chk.get("error"):
        why.append("the evaluator failed on the start state: " + chk["error"][:120])
    rig.close()
    return dict(seed=int(spec["seed"]), admitted=not why, why=why, worst=worst, unbuilt=list(rig.U.unbuilt) + spec.get("skipped", []))


def look(scene, out):
    import cv2
    from m2m import api
    from m2m.rig import Rig
    sys.path.insert(0, str(HERE / "sweep_blocks"))
    from sweep_blocks.run_program import depth_numbers, depth_picture
    scene = Path(scene); spec = json.loads(scene.read_text()); out = Path(out); out.mkdir(parents=True, exist_ok=True)
    rig = Rig(spec, scene.parent, seed=int(spec["seed"]), min_depth=0.0)
    robot = api.Robot(rig, time_limit_s=10.0)
    for c, f in robot.look().items():
        cv2.imwrite(str(out / f"look_{c}.png"), f.rgb[..., ::-1])
        cv2.imwrite(str(out / f"look_{c}_depth.png"), depth_picture(np.nan_to_num(f.depth, nan=0.0)))
        (out / f"look_{c}_depth.txt").write_text(depth_numbers(f))
    s = robot.state("left")
    (out / "state.txt").write_text(f"left arm: tool point {s['tcp_mm']}, approach_dir {s['approach_dir']}, closing_dir {s['closing_dir']}, opening {s['opening_mm']} mm\n")
    rig.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("--scene", required=True); v.add_argument("--rules", required=True); v.add_argument("--required", nargs="*", default=[])
    l = sub.add_parser("look"); l.add_argument("--scene", required=True); l.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "validate":
        print("XSIM_JSON " + json.dumps(validate(a.scene, json.loads(Path(a.rules).read_text()), a.required)))
    else:
        look(a.scene, a.out); print("XSIM_JSON {}")
