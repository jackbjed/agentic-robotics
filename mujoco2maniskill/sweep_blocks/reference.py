"""Is RoboDojo's sweep_blocks, as rebuilt here, SOLVABLE through the program interface within its 40 s? A scripted
reference that uses the simulator's TRUE positions (a harness-side check: not a skill, not something an agent gets).

    <robomme python> -I sweep_blocks/reference.py LAYOUT [--video]     one layout -> a line "REFERENCE {json}"
    <robomme python> -I sweep_blocks/reference.py all [--jobs 8]       every standard layout -> out/robodojo_sweep/reference.json

The plan it carries out:
  1. the left arm takes the brush by its handle, carries it HIGH (it droops in the grip and would drag over the table)
     and lays it down in front of the right arm, more than 10 cm to the right of where the dustpan will be;
  2. the right arm picks up each cube and lets go of it over the middle of the dustpan;
  3. both arms go home.
It does not SWEEP: it shows that the task's success test can be met through the interface in the time. (Sweeping with
the closed gripper as a pusher got 1 to 3 of 3 cubes over the pan's lip, depending on small differences.)"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
DOWN = (0, 0, -1)
FACE, HALF, LOW, HIGH = 30.0, 9.6, 20.0, 30.0                  # the closed gripper as a pusher: its face ahead of the tool point; a cube's half size; heights


def solve(k, video=False, verbose=True):
    import cv2
    from m2m.api import TimeUp
    from sweep_blocks.env import SweepBlocks
    from sweep_blocks.program_api import TABLE_Z, X5Robot
    env = SweepBlocks(layout=k, cameras=())
    frames = []
    k = k if isinstance(k, int) else Path(str(k)).stem
    r = X5Robot(env, on_step=(lambda: frames.append(env.image("overview")) if env.steps % 2 == 0 else None) if video else None)
    T = lambda n: (env.pose(n)[0] - [0, 0, TABLE_Z]) * 1000
    say = (lambda *a: print(f"[{r.time_s():5.1f}s]", *a, flush=True)) if verbose else (lambda *a: None)
    rz = lambda v, a: np.array([np.cos(a) * v[0] - np.sin(a) * v[1], np.sin(a) * v[0] + np.cos(a) * v[1]])
    notes = []

    def go(arm, xyz, closing, what):
        res = r.move(arm, xyz, DOWN, closing)
        if not res["done"]:
            notes.append(f"{what}: {res['why'][:70]}"); say("  ", what, "->", res["why"][:90])
        return res["done"]
    try:
        pan_R = env.pose("broom_shovel")[1]; pan = T("broom_shovel")
        m2 = -pan_R[:2, 1] / np.linalg.norm(pan_R[:2, 1]); side = np.array([m2[1], -m2[0]])       # the pan's mouth direction; across it
        M3, S3 = (m2[0], m2[1], 0.0), (side[0], side[1], 0.0)
        cubes = np.array([T(n)[:2] for n in env.cubes])
        a_, b_ = cubes @ m2, cubes @ side
        org_new = pan[:2].copy()                                    # (the pan stays where it is)
        say("pan", pan.round(0), "mouth towards", m2.round(2), "-> to be at", org_new.round(0), "| cubes", cubes.round(0).tolist())
        # 1. the brush
        b_R = env.pose("broom")[1]; b = T("broom"); axis = b_R[:, 2]
        handle = b - 60.0 * axis
        cl = np.cross(axis, [0, 0, 1.0]); cl = cl / np.linalg.norm(cl)
        go("left", (handle[0], handle[1], 90), cl, "above the brush"); go("left", (handle[0], handle[1], 22), cl, "down to the brush")
        w = r.set_gripper("left", 0)
        go("left", (handle[0], handle[1], 200), cl, "brush up")
        # turn the brush to lie along x, by the SMALLER of the two turns (a big turn flings the drooping brush away)
        c_new = np.array([0.0, 1.0]) if cl[1] >= 0 else np.array([0.0, -1.0])
        phi = float(np.arctan2(cl[0] * c_new[1] - cl[1] * c_new[0], cl[0] * c_new[0] + cl[1] * c_new[1]))
        t = r.turn("left", (handle[0], handle[1], 200), (0, 0, 1), float(np.degrees(phi)))
        ax_new = rz(axis[:2], phi) if t["done"] else axis[:2] / np.linalg.norm(axis[:2])
        if not t["done"]:
            notes.append("the brush could not be turned: " + t["why"][:60])
        ncl = (-ax_new[1], ax_new[0], 0.0)
        mid = np.array([org_new[0] + 165.0, -340.0])
        g = mid - 60.0 * ax_new
        h2 = np.array([handle[0], handle[1]])
        for f in (0.5, 1.0):
            q = h2 + f * (g - h2); go("left", (q[0], q[1], 200), ncl, "brush carried")
        go("left", (g[0], g[1], 40), ncl, "brush down"); r.set_gripper("left", 88); go("left", (g[0], g[1], 130), ncl, "up from the brush")
        say("brush grasp", w, "now at", T("broom").round(0), "| pan at", T("broom_shovel").round(0))
        # 2. the cubes, one by one, with the right gripper: picked up and let go over the middle of the pan. (Pushing
        #    them in with the closed gripper works only sometimes: the pan's collision shape has a 7 mm lip and a 13 mm
        #    ridge at its mouth, and a pushed cube often stops on it.)
        r.home("left")
        org = T("broom_shovel")[:2]; pan_R = env.pose("broom_shovel")[1]
        m2 = -pan_R[:2, 1] / np.linalg.norm(pan_R[:2, 1]); side = np.array([m2[1], -m2[0]])
        at = lambda s_, l_: org + s_ * m2 + l_ * side
        for i, n in enumerate(sorted(env.cubes, key=lambda n: float((T(n)[:2] - org) @ m2))):
            if env.done:
                break
            c = T(n); cR = env.pose(n)[1]
            cl2 = (cR[0, 0], cR[1, 0], 0.0)                         # close across the cube's own faces
            go("right", (c[0], c[1], 70), cl2, f"above {n}"); go("right", (c[0], c[1], 15), cl2, f"down to {n}")
            w2 = r.set_gripper("right", 0)
            go("right", (c[0], c[1], 85), cl2, f"{n} up")
            d = at(60.0, (i - (len(env.cubes) - 1) / 2) * 30.0)
            go("right", (d[0], d[1], 85), cl2, f"{n} over the pan"); go("right", (d[0], d[1], 50), cl2, f"{n} lowered")
            r.set_gripper("right", 88)
            go("right", (d[0], d[1], 90), cl2, f"up from {n}")
            say(n, "grasp", w2, "->", T(n).round(0))
        if not env.done:
            r.home("right")
    except TimeUp as e:
        say("TIME:", e)
    info = env.info()
    ok = bool(env.success or info["success"])
    out = dict(layout=k, solved=ok, robot_s=round(env.steps * 0.04, 2), cubes=len(env.cubes), cubes_in=sum(info["cubes_in_dustpan"].values()),
               left_of_broom=info["dustpan_left_of_broom"], not_lifted=info["dustpan_not_lifted"], level=info["dustpan_level"],
               arms_back=all(info["arms_back"].values()), notes=notes[:6])
    if video and frames:
        f = HERE / "out" / "robodojo_sweep" / f"reference_layout_{k}.mp4"
        vw = cv2.VideoWriter(str(f), cv2.VideoWriter_fourcc(*"mp4v"), 12.5, (frames[0].shape[1], frames[0].shape[0]))
        for x in frames:
            vw.write(x[..., ::-1])
        vw.release(); out["video"] = str(f)
    env.close()
    return out


def main():
    if sys.argv[1] == "all":
        jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 8
        ks = sorted(int(f.stem.split("_")[-1]) for f in (HERE / "assets" / "robodojo_sweep" / "layouts").glob("sweep_blocks_[0-9]*.json"))

        def one(k):
            r = subprocess.run([sys.executable, "-I", __file__, str(k), "--quiet"], capture_output=True, text=True)
            l = next((x for x in r.stdout.splitlines() if x.startswith("REFERENCE ")), None)
            return json.loads(l[10:]) if l else dict(layout=k, solved=False, error=r.stderr[-200:])
        with ThreadPoolExecutor(jobs) as ex:
            res = list(ex.map(one, ks))
        for x in res:
            print(x["layout"], "SOLVED" if x["solved"] else "not solved", x.get("robot_s"), f"cubes {x.get('cubes_in')}/{x.get('cubes')}", x.get("notes", [])[:2], flush=True)
        (HERE / "out" / "robodojo_sweep" / "reference.json").write_text(json.dumps(res, indent=1))
        print("solved", sum(x["solved"] for x in res), "of", len(res))
    else:
        out = solve(int(sys.argv[1]) if sys.argv[1].isdigit() else sys.argv[1], video="--video" in sys.argv, verbose="--quiet" not in sys.argv)
        print("REFERENCE " + json.dumps(out))


if __name__ == "__main__":
    main()
