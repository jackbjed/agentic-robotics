"""Where can each X5 arm put its tool point, fingers straight down, just above the table? (kinematics only: the
program interface's own inverse kinematics and its sanity rule for joints; no simulation). Also: for every saved layout
and every panel episode, can an arm take the dustpan by its handle, and reach its mouth?"""
import json
import sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from sweep_blocks.program_api import ArmKin, tool_R, BACK_Y
from x5 import spec as S
KIN = {"left": ArmKin("L"), "right": ArmKin("R")}
RNG = np.random.default_rng(0)
SEEDS = [np.zeros(6)] + [RNG.normal(0, 0.5, 6) for _ in range(4)] + [RNG.uniform(-1.6, 1.6, 6) for _ in range(3)]


def reach(arm, x, y, z=0.025, closing=(1, 0, 0), approach=(0, 0, -1)):
    k = KIN[arm]; R = tool_R(approach, closing); p = np.array([x, y, z])
    for R1 in (R, tool_R(approach, -np.asarray(closing, float))):
        for s in SEEDS:
            q, ep, er = k.ik(p, R1, s, iters=80)
            if ep < 0.002 and er < np.radians(1.5):
                T, fr = k.frames(q)
                if all(o[2] > 0.02 and o[1] > BACK_Y - 0.10 for o, _ in fr[2:]):
                    return True
    return False


def _cell(x, y, cl):
    l, r = reach("left", x, y, closing=cl), reach("right", x, y, closing=cl)
    return "B" if l and r else "L" if l else "R" if r else "."


def quat_R(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


if __name__ == "__main__":
    if "--map" in sys.argv:
        xs = np.arange(-0.60, 0.601, 0.05); ys = np.arange(0.40, -0.451, -0.05)
        for cl, name in (((1, 0, 0), "closing along x"), ((0, 1, 0), "closing along y")):
            print(f"\nfingers down, 25 mm above the table, {name}.  L = left only, R = right only, B = both, . = neither   (x from -600 to 600 mm, step 50; rows y)")
            from multiprocessing import Pool
            with Pool(32) as pool:
                cells = pool.starmap(_cell, [(x, y, cl) for y in ys for x in xs])
            for i, y in enumerate(ys):
                print(f"y {y * 1000:5.0f}  {''.join(cells[i * len(xs):(i + 1) * len(xs)])}", flush=True)
    files = sorted((HERE / "assets" / "robodojo_sweep" / "layouts").glob("sweep_blocks_[0-9]*.json"), key=lambda f: int(f.stem.split("_")[-1]))
    for label, fs in (("RoboDojo's 55 layouts", files), ("the panel", sorted((HERE / "assets" / "robodojo_sweep" / "panel20").glob("ep_*.json")))):
        n = dict(handle_left=0, handle_right=0, handle_any=0, mouth_right=0, mouth_left=0, both=0)
        for f in fs:
            L = json.loads(f.read_text())["Rigid"]["broom_shovel"][0]
            p = np.array(L["default_pos"][:2]); R = quat_R(L["default_ori"]); m = -R[:2, 1] / np.linalg.norm(R[:2, 1]); hx = R[:, 0]
            h = p - 0.095 * m; front = p + 0.185 * m                # the handle's grasp point; a point 45 mm in front of the mouth
            hl, hr = reach("left", h[0], h[1], 0.034, closing=hx), reach("right", h[0], h[1], 0.034, closing=hx)
            ml, mr = reach("left", front[0], front[1], closing=(m[0], m[1], 0)), reach("right", front[0], front[1], closing=(m[0], m[1], 0))
            n["handle_left"] += hl; n["handle_right"] += hr; n["handle_any"] += hl or hr; n["mouth_left"] += ml; n["mouth_right"] += mr
            n["both"] += (hl and mr) or (hr and ml)
        print(f"\n{label} ({len(fs)}): the dustpan's handle can be taken from above by the left arm in {n['handle_left']}, by the right in {n['handle_right']}, by either in {n['handle_any']};"
              f" a point 45 mm in front of its mouth is reached by the right arm in {n['mouth_right']}, by the left in {n['mouth_left']};"
              f" one arm on the handle AND the other in front of the mouth: {n['both']}")
