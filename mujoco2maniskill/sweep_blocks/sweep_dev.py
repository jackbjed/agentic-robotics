"""(development) The pieces of a sweeping reference, tried one by one with diagnostics.
    <robomme python> -I sweep_blocks/sweep_dev.py LAYOUT [key=value ...] [--video]"""
import sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from m2m.api import TimeUp
from sweep_blocks.env import SweepBlocks
from sweep_blocks.program_api import TABLE_Z, X5Robot
DOWN = (0, 0, -1)
P = dict(rgrasp="head", hy=-430.0, hx=None, zs=26.0, stop=3.0, lean=0.0, vstroke=1, grasp="head", gz=22.0, carry=120.0, lay=30.0, back=40.0)
for a in sys.argv[2:]:
    if "=" in a:
        k, v = a.split("="); P[k] = v if k in ("grasp", "rgrasp") else float(v)
lay = sys.argv[1]; lay = int(lay) if lay.isdigit() else lay
env = SweepBlocks(layout=lay, cameras=()); frames = []
video = "--video" in sys.argv
r = X5Robot(env, on_step=(lambda: frames.append(env.image("overview")) if env.steps % 2 == 0 else None) if video else None)
T = lambda n: (env.pose(n)[0] - [0, 0, TABLE_Z]) * 1000
say = lambda *a: print(f"[{r.time_s():5.1f}s]", *a, flush=True)


def go(arm, xyz, closing, what, approach=DOWN):
    res = r.move(arm, xyz, approach, closing)
    if not res["done"]:
        say("   ", what, "->", res["why"][:80])
    return res["done"]


def brush():
    R = env.pose("broom")[1]; c = T("broom")
    ax = R[:, 2]; return c, R, ax


try:
    pan_R = env.pose("broom_shovel")[1]; pan = T("broom_shovel")
    m2 = -pan_R[:2, 1] / np.linalg.norm(pan_R[:2, 1]); u = np.array([-m2[1], m2[0]])       # the mouth's direction; across it, away from the robot
    M3, U3 = (m2[0], m2[1], 0.0), (u[0], u[1], 0.0)
    org = pan[:2].copy(); S = lambda p: float((np.asarray(p)[:2] - org) @ m2); Lt = lambda p: float((np.asarray(p)[:2] - org) @ u)
    cubes0 = {n: T(n) for n in env.cubes}
    say("pan", pan.round(0), "mouth", m2.round(2), "| cubes (along, across):", {n: (round(S(c)), round(Lt(c))) for n, c in cubes0.items()})
    # ---- A. the left arm takes the brush (by its head: near its centre of mass) and lays it across the mouth's direction
    c, R, ax = brush()
    say("brush centre", c.round(0), "axis", ax.round(2), "local x (up?)", R[:, 0].round(2), "bristles towards", (-R[:, 1]).round(2))
    gl = np.array([0.0, -0.014, 0.062]) if P["grasp"] == "head" else np.array([0.0, 0.0, -0.060])
    g = c + 1000 * (R @ gl); cl = R[:, 1].copy(); cl[2] = 0
    go("left", (g[0], g[1], 90), cl, "above the brush"); go("left", (g[0], g[1], P["gz"]), cl, "down to the brush")
    w = r.set_gripper("left", 0)
    go("left", (g[0], g[1], P["carry"]), cl, "brush up")
    c, R, ax = brush(); say("brush held: width", w, "axis", ax.round(2))
    phi = float(np.arctan2(ax[0] * u[1] - ax[1] * u[0], ax[0] * u[0] + ax[1] * u[1]))
    t = r.turn("left", (g[0], g[1], P["carry"]), (0, 0, 1), float(np.degrees(phi)))
    if not t["done"]:
        say("    turn ->", t["why"][:80])
    c, R, ax = brush()
    hx = P["hx"] if P["hx"] is not None else float(np.clip(org[0] + 60, -150, 150))
    H = np.array([hx, P["hy"]])                                   # where the handle's grasp point is to lie
    hnow = c[:2] - 60.0 * ax[:2] / np.linalg.norm(ax[:2])
    tp = np.array(r.state("left")["tcp_mm"][:2]); cl2 = env.pose("broom")[1][:, 1].copy(); cl2[2] = 0
    tgt = tp + (H - hnow)
    mid = tp + 0.5 * (tgt - tp)
    go("left", (mid[0], mid[1], P["carry"]), cl2, "brush carried"); go("left", (tgt[0], tgt[1], P["carry"]), cl2, "brush carried")
    go("left", (tgt[0], tgt[1], P["lay"]), cl2, "brush down"); r.set_gripper("left", 88); go("left", (tgt[0], tgt[1], 110), cl2, "up from the brush")
    c, R, ax = brush()
    say("brush laid: centre", c.round(0), "axis", ax.round(2), "(wanted", u.round(2), ") handle point", (c[:2] - 60 * ax[:2]).round(0), "wanted", H.round(0), "| pan", T("broom_shovel").round(0))
    # ---- B. the left arm takes the pan by its handle
    hp = org - 95.0 * m2; hxd = pan_R[:, 0]
    r.set_gripper("left", 70)
    go("left", (hp[0], hp[1], 100), hxd, "above the pan's handle"); go("left", (hp[0], hp[1], 34), hxd, "down to the pan's handle")
    say("pan grasp", r.set_gripper("left", 0), "pan", T("broom_shovel").round(0))
    # ---- C. the right arm takes the brush by its handle
    c, R, ax = brush(); a2 = ax[:2] / np.linalg.norm(ax[:2])
    h = (c + 1000 * (R @ np.array([0.0, -0.014, 0.062])))[:2] if P["rgrasp"] == "head" else c[:2] - 60.0 * a2
    clr = R[:, 1].copy(); clr[2] = 0
    go("right", (h[0], h[1], 90), clr, "above the brush"); go("right", (h[0], h[1], P["gz"]), clr, "down to the brush")
    wr = r.set_gripper("right", 0)
    go("right", (h[0], h[1], P["zs"]), clr, "brush lifted a little")
    th = np.radians(P["lean"]); AP = (np.sin(th) * m2[0], np.sin(th) * m2[1], -np.cos(th))      # (the hand leaning: its top towards the pan)
    c, R, ax = brush(); a2 = ax[:2] / np.linalg.norm(ax[:2])
    bris = -R[:2, 1]; face = 41.0 if bris @ m2 < 0 else 13.0       # the brush's face towards the pan: its bristles' tips, or its back
    say("right holds the brush: width", wr, "axis", ax.round(2), "bristles towards the pan" if face > 20 else "its BACK towards the pan", "| centre z", round(c[2]))
    # ---- D. the stroke(s): the blade (the brush's head: 60..185 mm from the grasp along its axis) behind the cubes, then towards the mouth
    tool = lambda: np.array(r.state("right")["tcp_mm"][:2])
    rel = tool() - c[:2]                                            # (the tool point relative to the brush's centre: kept)
    for k in range(int(P["vstroke"])):
        out = [n for n in env.cubes if not env.info()["cubes_in_dustpan"][n]]
        if not out:
            break
        s_far = max(S(T(n)) for n in out); l_mid = float(np.clip(np.mean([Lt(T(n)) for n in out]), -25, 25))
        # the brush's centre goes to: along = s_far + 12 + face + 15, across = l_mid - 62 (so the head's middle is at l_mid)
        cs = org + (s_far + 12 + face + 18) * m2 + (l_mid - 62.0) * u
        ts = cs + rel
        t0 = tool()
        v1 = org + S(ts) * m2 + Lt(t0) * u                          # first along the mouth's direction (the head stays clear of the cubes) ...
        go("right", (v1[0], v1[1], P["zs"]), clr, "to behind the cubes (along)", AP); go("right", (ts[0], ts[1], P["zs"]), clr, "to behind the cubes (across)", AP)
        ce = org + (140.0 + P["stop"] + 19.2 + face) * m2 + (l_mid - 62.0) * u      # ... the face stops with a cube's length + `stop` left to the lip
        te = ce + rel
        res = r.move("right", (te[0], te[1], P["zs"]), AP, clr)
        c, R, ax = brush()
        say(f"stroke {k}:", "" if res["done"] else res["why"][:60], "| brush centre along", round(S(c)), "axis", ax.round(2), "| cubes (along, across, z):",
            {n: (round(S(T(n))), round(Lt(T(n))), round(T(n)[2])) for n in env.cubes}, "| in:", env.info()["cubes_in_dustpan"], "| pan moved", round(float(np.linalg.norm(T("broom_shovel")[:2] - org)), 1))
        if P["back"] > 0:
            tb = te + P["back"] * m2; go("right", (tb[0], tb[1], P["zs"]), clr, "back", AP)
    # ---- E. let go of everything, go home
    r.set_gripper("right", 88); q = r.state("right")["tcp_mm"]; go("right", (q[0], q[1], 110), clr, "up from the brush"); r.home("right")
    r.set_gripper("left", 88); q = r.state("left")["tcp_mm"]; go("left", (q[0], q[1], 110), hxd, "up from the pan"); r.home("left")
except TimeUp as e:
    say("TIME:", e)
info = env.info(); say("END:", {k: info[k] for k in ("success", "dustpan_left_of_broom", "dustpan_not_lifted", "dustpan_level", "cubes_in_dustpan", "arms_back")})
say("brush", T("broom").round(0), "pan", T("broom_shovel").round(0), "cubes", {n: T(n).round(0).tolist() for n in env.cubes})
print("SOLVED" if (env.success or info["success"]) else "not solved", "in", round(env.steps * 0.04, 2), "s")
if video and frames:
    import cv2
    f = HERE / "out" / "robodojo_sweep" / "sweep_dev.mp4"
    vw = cv2.VideoWriter(str(f), cv2.VideoWriter_fourcc(*"mp4v"), 12.5, (frames[0].shape[1], frames[0].shape[0]))
    for x in frames:
        vw.write(x[..., ::-1])
    vw.release(); print(f)
env.close()
