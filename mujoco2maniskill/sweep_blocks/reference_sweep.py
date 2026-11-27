"""Is RoboDojo's sweep_blocks, as rebuilt here, solvable BY SWEEPING through the program interface within its 40 s?
A scripted reference that uses the simulator's TRUE positions (the harness's check: not a skill, never given to an
agent). Nothing is picked up and put into the dustpan: the cubes get there only by being pushed with the brush.

    <robomme python> -I sweep_blocks/reference_sweep.py LAYOUT|FILE [--video] [key=value ...]   -> a line "REFERENCE {json}"

What it does:
  1. the left arm takes the brush by its handle, carries it over and lays it down in front of the robot, across the
     direction the dustpan's mouth faces (the hand-over: by way of the table);
  2. the left arm takes the dustpan by its handle and holds it;
  3. the right arm takes the brush (by its head: held there it stays balanced; held by the handle it swings round in
     the grip as soon as it meets anything), turns it to lie across the mouth's direction, and sweeps: the head is
     set down behind the cubes and drawn towards the mouth in short steps, its heading corrected on the way, raised
     over the pan's lip and ridge as it reaches them (a cube pushed low jams against the 5 mm lip; pushed at its top
     it tips over it), then lifted straight up (drawn back low it takes cubes out again). A later stroke is centred
     on one cube left outside: a cube far from the grip is passed over at the lip, the brush see-saws about the grip.
  4. the brush is put down to the right of the dustpan, both grippers open, both arms go home.

A MIRRORED scene (the dustpan's mouth towards -x: sweep_blocks/mirror_scenes.py) is done with the arms' roles swapped (the
right arm fetches the brush and holds the pan, the left arm sweeps). Its end differs, because the judge is not mirrored
(the brush must end to the RIGHT of the dustpan): the pan's arm goes home first, then the left arm carries the brush
round to the pan's right and lays it down there."""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
DOWN = (0, 0, -1)
HEAD = np.array([[x, y, z] for x in (-0.019, 0.019) for y in (-0.041, 0.013) for z in (0.0, 0.125)])      # the brush's head (its own frame, m)
P0 = dict(mode="broad", rgrasp="head", hy=-340.0, cx=60.0, carry=170.0, lay=40.0, gz=22.0, zs=22.0, zt=75.0, step=12.0, stop=88.0, lean=0.0, back=45.0, strokes=4,
          yaw_tol=4.0, lip_lift=3.0,
          fz=120.0, fright=130.0, fy=-330.0, fback=165.0)                    # (mirrored scenes only: the carry height; where the brush is left: right of the pan's middle, y; the place before it)


def solve(k, video=False, verbose=True, **over):
    import cv2
    from m2m.api import TimeUp
    from sweep_blocks.looks import SweepBlocks
    from sweep_blocks.program_api import TABLE_Z, X5Robot
    P = dict(P0, **over)
    env = SweepBlocks(layout=k, cameras=())
    name = k if isinstance(k, int) else Path(str(k)).stem
    frames = []
    r = X5Robot(env, on_step=(lambda: frames.append(env.image("overview")) if env.steps % 2 == 0 else None) if video else None)
    T = lambda n: (env.pose(n)[0] - [0, 0, TABLE_Z]) * 1000
    say = (lambda *a: print(f"[{r.time_s():5.1f}s]", *a, flush=True)) if verbose else (lambda *a: None)
    notes, log, tried, side = [], {}, {}, [1.0]

    def go(arm, xyz, closing, what, approach=DOWN):
        res = r.move(arm, xyz, approach, closing)
        if not res["done"]:
            notes.append(f"{what}: {res['why'][:60]}"); say("   ", what, "->", res["why"][:80])
        return res["done"]

    def brush():
        R = env.pose("broom")[1]; c = T("broom")
        return c, R, R[:2, 2] / np.linalg.norm(R[:2, 2])

    inside = lambda: env.info()["cubes_in_dustpan"]
    try:
        pan_R = env.pose("broom_shovel")[1]; pan = T("broom_shovel")
        m2 = -pan_R[:2, 1] / np.linalg.norm(pan_R[:2, 1]); u = np.array([-m2[1], m2[0]])      # the mouth's direction; across it (away from the robot)
        org = pan[:2].copy()
        mir = bool(m2[0] < 0)                                       # the MIRRORED arrangement (mouth towards -x, brush on the right): the arms swap roles
        HOLD, SWEEP = ("right", "left") if mir else ("left", "right")
        log["mirrored"] = mir
        S = lambda p: float((np.asarray(p)[:2] - org) @ m2); Lt = lambda p: float((np.asarray(p)[:2] - org) @ u)
        say("pan", pan.round(0), "mouth", m2.round(2), "| cubes (along, across):", {n: (round(S(T(n))), round(Lt(T(n)))) for n in env.cubes})
        # ---- 1. the left arm: the brush, from where it lies to in front of the robot, lying across the mouth's direction
        c, R, a2 = brush()
        g = c[:2] - 60.0 * a2; cl = R[:, 1].copy(); cl[2] = 0
        go(HOLD, (g[0], g[1], 90), cl, "above the brush"); go(HOLD, (g[0], g[1], P["gz"]), cl, "down to the brush")
        log["left_grasp_mm"] = r.set_gripper(HOLD, 0)
        go(HOLD, (g[0], g[1], P["carry"]), cl, "brush up")
        c, R, a2 = brush()
        ex = np.array([1.0, 0.0]) if a2[0] >= 0 else np.array([-1.0, 0.0])          # along x, by the smaller turn
        phi = float(np.degrees(np.arctan2(a2[0] * ex[1] - a2[1] * ex[0], a2 @ ex)))
        t = r.turn(HOLD, (g[0], g[1], P["carry"]), (0, 0, 1), phi)
        if not t["done"]:
            notes.append("turn with the brush: " + t["why"][:50])
        c, R, a2 = brush()
        C = np.array([-P["cx"] if mir else P["cx"], P["hy"]])                                # where the brush's middle is to lie
        tp = np.array(r.state(HOLD)["tcp_mm"][:2]); cl2 = R[:, 1].copy(); cl2[2] = 0
        tgt = tp + (C - c[:2])
        for f in (0.5, 1.0):
            q = tp + f * (tgt - tp); go(HOLD, (q[0], q[1], P["carry"]), cl2, "brush carried")
        go(HOLD, (tgt[0], tgt[1], P["lay"]), cl2, "brush down"); r.set_gripper(HOLD, 88); go(HOLD, (tgt[0], tgt[1], 110), cl2, "up from the brush")
        c, R, a2 = brush()
        log["brush_laid"] = dict(t=r.time_s(), centre=c.round(0).tolist(), axis=a2.round(2).tolist())
        say("brush laid", log["brush_laid"], "| pan", T("broom_shovel").round(0))
        # ---- 2. the left arm: the dustpan, by its handle, held
        hp = org - 95.0 * m2; hxd = pan_R[:, 0]
        r.set_gripper(HOLD, 70)
        go(HOLD, (hp[0], hp[1], 100), hxd, "above the pan's handle"); go(HOLD, (hp[0], hp[1], 34), hxd, "down to the pan's handle")
        log["pan_grasp_mm"] = r.set_gripper(HOLD, 0)
        say("pan held", log["pan_grasp_mm"])
        # ---- 3. the right arm: the brush
        c, R, a2 = brush()
        h = (c + 1000 * (R @ np.array([0.0, -0.014, 0.062])))[:2] if P["rgrasp"] == "head" else c[:2] - 60.0 * a2
        clr = R[:, 1].copy(); clr[2] = 0
        go(SWEEP, (h[0], h[1], 90), clr, "above the brush"); go(SWEEP, (h[0], h[1], P["gz"]), clr, "down to the brush")
        log["right_grasp_mm"] = r.set_gripper(SWEEP, 0)
        tool = lambda: np.array(r.state(SWEEP)["tcp_mm"])

        def blade():
            """The brush's head now: its foremost point towards the pan (along), its middle (across), its heading error."""
            p, Rb = env.pose("broom"); W = ((p - [0, 0, TABLE_Z]) + HEAD @ Rb.T) * 1000
            a = Rb[:2, 2] / np.linalg.norm(Rb[:2, 2])
            if P["mode"] == "tip":                                  # the head's END leads: the brush points at the mouth
                uu = -m2; Wt = W[HEAD[:, 2] > 0.1]
                psi = float(np.degrees(np.arctan2(a[0] * uu[1] - a[1] * uu[0], a @ uu)))
                return min(S(w) for w in Wt), float(np.mean([Lt(w) for w in Wt])), psi, Rb
            uu = u * side[0]
            psi = float(np.degrees(np.arctan2(a[0] * uu[1] - a[1] * uu[0], a @ uu)))
            return min(S(w) for w in W), float(np.mean([Lt(w) for w in W])), psi, Rb

        def closing():
            Rb = env.pose("broom")[1]; v = Rb[:, 1].copy(); v[2] = 0
            return v

        def shift(d_along, d_across, z, what):
            t0 = tool(); q = t0[:2] + d_along * m2 + d_across * u
            return go(SWEEP, (q[0], q[1], z), closing(), what)

        def over_lip(s_front):
            """How far the brush is raised with its head's front at s_front: on the table up to 150 mm, then clear of the
            pan's lip and ridge (the collision shape's profile: 5 mm at the edge, 13 mm at 95 mm) by lip_lift."""
            if s_front > 150:
                return 0.0
            h = float(np.interp(s_front, [74, 80, 86, 92, 98, 104, 110, 116, 122, 128, 134, 140, 150], [8, 10, 12, 13, 13, 12, 11, 11, 9.6, 8.6, 7, 5, 0]))
            return h + P["lip_lift"] if s_front <= 141 else (5 + P["lip_lift"]) * (150 - s_front) / 9.0

        def straighten(z, times=1):
            for _ in range(times):
                s_, l_, psi, _ = blade()
                if abs(psi) <= P["yaw_tol"]:
                    break
                t0 = tool(); t = r.turn(SWEEP, (t0[0], t0[1], z), (0, 0, 1), float(np.clip(psi, -45, 45)))
                if P.get("dbg"):
                    say(f"    straighten: heading off by {psi:.0f} deg, side {side[0]:+.0f}, turned by {float(np.clip(psi, -45, 45)):.0f}: {t['done']} {t['why'][:40]} -> now off by {blade()[2]:.0f}")
                if not t["done"] and "short" not in t["why"]:
                    notes.append("straightening: " + t["why"][:50]); break
        a_now = brush()[2]; side[0] = 1.0 if a_now @ u >= 0 else -1.0  # (the head's end towards +u or -u: as it is held)
        if mir:                                                     # (mirrored: always the head's end towards the ROBOT, the handle away from it. The other
            side[0] = 1.0 if u[1] < 0 else -1.0                     #  way round the handle swings into the sweeping arm itself and the brush slips in the grip;
                                                                    #  as it is laid the two are a near-tie, which a few degrees decide)
        t0 = tool(); go(SWEEP, (t0[0], t0[1], P["zt"]), closing(), "brush up"); straighten(P["zt"], 5)
        for k_ in range(int(P["strokes"])):
            out = [n for n in env.cubes if not inside()[n]]
            if not out or env.done:
                break
            # the stroke's line: centred on the cubes still outside (the head, 125 mm long, must stay within the mouth)
            #   the first stroke takes them all; a later one is centred on ONE cube, the nearest (a cube far from the grip
            #   is passed over at the lip: the brush see-saws about the grip)
            l_mid = float(np.clip(np.median([Lt(T(n)) for n in out]), -23, 23))
            if k_ > 0:
                one = min(out, key=lambda n: (tried.get(n, 0), S(T(n))))
                l_mid = float(np.clip(Lt(T(one)), -30, 30))
            mine = [n for n in out if abs(Lt(T(n)) - l_mid) <= 56] or out
            for n in mine:
                tried[n] = tried.get(n, 0) + 1
            if P["mode"] == "tip":                                  # one cube a stroke: the one nearest the mouth, on its own line
                first = min(out, key=lambda n: (tried.get(n, 0), S(T(n))))
                l_mid = float(np.clip(Lt(T(first)), -62, 62)); mine = [first]
            s_far = max(S(T(n)) for n in mine)
            straighten(P["zt"])
            s_, l_, psi, _ = blade(); shift(s_far + 9.6 + 22.0 - s_, l_mid - l_, P["zt"], "to behind the cubes")
            straighten(P["zt"])
            s_, l_, psi, _ = blade(); shift(s_far + 9.6 + 22.0 - s_, l_mid - l_, P["zs"], "brush down behind the cubes")
            n_step = 0
            while True:
                s_, l_, psi, _ = blade()
                if s_ <= P["stop"] + 0.7 or n_step > 40 or env.done:
                    break
                d = min(P["step"], s_ - P["stop"])
                z = P["zs"] + over_lip(s_ - d)
                t0 = tool(); q = t0[:2] - d * m2 + float(np.clip(l_mid - l_, -6, 6)) * u
                res = r.move(SWEEP, (q[0], q[1], z), DOWN, closing())
                n_step += 1
                if P.get("dbg"):
                    pb, Rb = env.pose("broom"); W = ((pb - [0, 0, TABLE_Z]) + HEAD @ Rb.T) * 1000
                    say(f"    step {n_step}: tool z {tool()[2]:.0f} | head front {blade()[0]:.0f}, its lowest point {W[:, 2].min():.0f}, bristle-side bottom {min(w[2] for w in W if S(w) < blade()[0] + 8):.0f}"
                        f" | cubes (along, z):", {n: (round(S(T(n))), round(T(n)[2])) for n in env.cubes}, "" if res["done"] else res["why"][:40])
                if n_step % 2 == 0:
                    straighten(z)
                s1 = blade()[0]
                if s_ - s1 < 1.0 and not res["done"]:               # (held up: no further)
                    notes.append(f"stroke {k_} held up at {s1:.0f} mm"); break
            s_, l_, psi, _ = blade()
            say(f"stroke {k_} on line {l_mid:+.0f}: head's front at {s_:.0f}, heading off by {psi:.0f} deg | cubes (along, across, z):",
                {n: (round(S(T(n))), round(Lt(T(n))), round(T(n)[2])) for n in env.cubes}, "| in:", inside(),
                "| pan moved", round(float(np.linalg.norm(T("broom_shovel")[:2] - org)), 1))
            log.setdefault("strokes", []).append(dict(line=l_mid, t=r.time_s(), front=round(s_), inside=sum(inside().values())))
            if not env.done:                                        # straight up first: drawn back low, the brush takes cubes out again
                t0 = tool(); go(SWEEP, (t0[0], t0[1], P["zt"]), closing(), "brush up from the mouth")
        # ---- 4. everything let go, both arms home
        if mir and not env.done:
            # mirrored, the sweeping arm is the LEFT one and its strokes end with the brush on the pan's left, but the judge
            # wants the brush's middle more than 100 mm to the pan's RIGHT: the pan is let go of and its arm goes home
            # (it stands where the brush has to go), then the brush is carried round in ONE move that also turns it
            # (by less than 90 degrees: the nearer closing line is then the one meant) to lie along x, its middle to the
            # right of the pan's, in front of the robot
            ex = np.array([-1.0, 0.0]); off = lambda a: float(np.degrees(np.arctan2(a[0] * ex[1] - a[1] * ex[0], a @ ex)))
            r.set_gripper(HOLD, 88); q = r.state(HOLD)["tcp_mm"]; go(HOLD, (q[0], q[1], 110), hxd, "up from the pan"); r.home(HOLD)
            t0 = tool(); go(SWEEP, (t0[0], t0[1], P["fz"]), closing(), "brush up, to be carried")
            c, R, a2 = brush(); tp = tool()[:2]
            th = np.radians(float(np.clip(off(a2), -80, 80))); Rz = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
            Cf = np.array([T("broom_shovel")[0] + P["fright"], P["fy"]])
            # by a place fback short of where it is to lie, still high (over the pan's corner), turned there; then down and
            # along x in one slanting move: the handle's end goes in UNDER the right arm's gripper, which rests at 156 mm
            cl = closing(); tgt = Cf - Rz @ (c[:2] - tp) - [P["fback"], 0.0]
            go(SWEEP, (tgt[0], tgt[1], P["fz"]), (*(Rz @ cl[:2]), 0.0), "brush carried round the pan")
            c, R, a2 = brush()
            if abs(off(a2)) > 6:
                t0 = tool(); r.turn(SWEEP, (t0[0], t0[1], P["fz"]), (0, 0, 1), float(np.clip(off(a2), -45, 45)))
            c, R, a2 = brush(); tp = tool()[:2]; tgt = tp + (Cf - c[:2])
            go(SWEEP, (tgt[0], tgt[1], P["zs"] + 6), closing(), "brush down, to the pan's right")
            log["brush_left_at"] = dict(t=r.time_s(), centre=brush()[0].round(0).tolist(), pan=T("broom_shovel").round(0).tolist())
        elif not env.done:
            shift(max(180.0 - blade()[0], 0.0), 0.0, P["zt"], "back, clear of the pan"); t0 = tool(); go(SWEEP, (t0[0], t0[1], P["zs"] + 6), closing(), "brush down")
        dbg = lambda what: say(f"    {what}: cubes (along, z):", {n: (round(S(T(n))), round(T(n)[2])) for n in env.cubes}, "in", sum(inside().values())) if P.get("dbg") else None
        dbg("after the back-off")
        if not env.done:
            r.set_gripper(SWEEP, 88); dbg("brush let go"); t0 = tool(); go(SWEEP, (t0[0], t0[1], 110), closing(), "up from the brush"); dbg("right up"); r.home(SWEEP)
        dbg("right home")
        if not env.done and not mir:
            r.set_gripper(HOLD, 88); q = r.state(HOLD)["tcp_mm"]; go(HOLD, (q[0], q[1], 110), hxd, "up from the pan"); r.home(HOLD)
    except TimeUp as e:
        say("TIME:", e)
    info = env.info()
    ok = bool(env.success or info["success"])
    out = dict(layout=name, solved=ok, how="swept with the brush", robot_s=round(env.steps * 0.04, 2), cubes=len(env.cubes),
               cubes_in=sum(info["cubes_in_dustpan"].values()), left_of_broom=info["dustpan_left_of_broom"], not_lifted=info["dustpan_not_lifted"],
               level=info["dustpan_level"], arms_back=all(info["arms_back"].values()), log=log, notes=notes[:8])
    say("END:", {k_: info[k_] for k_ in ("success", "dustpan_left_of_broom", "dustpan_not_lifted", "dustpan_level", "cubes_in_dustpan", "arms_back")})
    if video and frames:
        f = HERE / "out" / "robodojo_sweep" / f"reference_sweep_{name}.mp4"
        vw = cv2.VideoWriter(str(f), cv2.VideoWriter_fourcc(*"mp4v"), 12.5, (frames[0].shape[1], frames[0].shape[0]))
        for x in frames:
            vw.write(x[..., ::-1])
        vw.release(); out["video"] = str(f)
    env.close()
    return out


if __name__ == "__main__":
    a = sys.argv[1]
    over = {}
    for x in sys.argv[2:]:
        if "=" in x:
            k_, v = x.split("="); over[k_] = v if k_ in ("rgrasp", "mode") else float(v)
    res = solve(int(a) if a.isdigit() else a, video="--video" in sys.argv, verbose="--quiet" not in sys.argv, **over)
    print("REFERENCE " + json.dumps(res))
