import math
import numpy as np

# TARGET world of the task "sweep" (hidden from every agent): ONE fixed sponge, 12 fixed small round things, ONE taped
# rectangle on the table, ONE fixed other thing. Episodes differ only in where the things are. Lengths in mm.
SPONGE = dict(L=110.0, W=60.0, T=32.0, mass=0.020, friction=0.9, rgb=(0.92, 0.80, 0.24))
OBJ = dict(n=12, r=4.5, rho=1200.0, friction=0.5, roll=0.05, rgb=(0.84, 0.70, 0.47))
TARGET = dict(a=100.0, b=80.0, tape=18.0, rgb=(0.16, 0.35, 0.78))        # inner half sizes, the tape's width
OTHER = dict(r=35.0, h=80.0, mass=0.30, rgb=(0.80, 0.20, 0.20))
REST_TCP = (-478.0, 41.0, 350.0)
WS = (-440.0, -170.0, -170.0, 380.0)                                     # x0, x1, y0, y1: where things may be
TEXT = None


def _rgba(c):
    return "%.3f %.3f %.3f 1" % tuple(c)


def _quat(yaw):
    return "%.5f 0 0 %.5f" % (math.cos(yaw / 2), math.sin(yaw / 2))


def _in_rect(p, c, yaw, a, b):
    dx, dy = p[0] - c[0], p[1] - c[1]
    co, si = math.cos(yaw), math.sin(yaw)
    return abs(co * dx + si * dy) <= a and abs(-si * dx + co * dy) <= b


def _layout(rng):
    a, b = TARGET["a"], TARGET["b"]
    for _ in range(4000):
        tyaw = rng.uniform(-0.5, 0.5)
        tc = (rng.uniform(WS[0] + 120, WS[1] - 110), rng.uniform(WS[2] + 110, WS[3] - 110))
        ang = rng.uniform(0, 2 * math.pi); dist = max(a, b) + rng.uniform(90, 150)
        cc = (tc[0] + dist * math.cos(ang), tc[1] + dist * math.sin(ang))
        if not (WS[0] + 40 <= cc[0] <= WS[1] - 40 and WS[2] + 40 <= cc[1] <= WS[3] - 40):
            continue
        pts = []
        for _ in range(400):
            if len(pts) == OBJ["n"]:
                break
            p = (cc[0] + rng.uniform(-45, 45), cc[1] + rng.uniform(-45, 45))
            if all(math.hypot(p[0] - q[0], p[1] - q[1]) > 2 * OBJ["r"] + 4 for q in pts) and not _in_rect(p, tc, tyaw, a + 45, b + 45):
                pts.append(p)
        if len(pts) < OBJ["n"]:
            continue
        sp = (rng.uniform(WS[0] + 70, WS[1] - 70), rng.uniform(WS[2] + 70, WS[3] - 70)); syaw = rng.uniform(0, math.pi)
        ot = (rng.uniform(WS[0] + 50, WS[1] - 50), rng.uniform(WS[2] + 50, WS[3] - 50))
        far = lambda p, q, d: math.hypot(p[0] - q[0], p[1] - q[1]) > d
        if (far(sp, tc, max(a, b) + 110) and far(sp, cc, 130) and far(ot, tc, max(a, b) + 90) and far(ot, cc, 120) and far(ot, sp, 120)
                and not _on_way(ot, cc, tc, 70) and not _on_way(sp, cc, tc, 80)):
            return tc, tyaw, pts, sp, syaw, ot
    raise ValueError("no layout")


def _on_way(p, a, b, d):
    ax, ay = b[0] - a[0], b[1] - a[1]; n = math.hypot(ax, ay)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / (n * n)))
    return math.hypot(p[0] - a[0] - t * ax, p[1] - a[1] - t * ay) < d


def scene(scene, rng):
    m = 0.001
    scene["table"] = {"height_mm": 0.0, "color": "dark grey", "pattern": "plain"}
    tc, tyaw, pts, sp, syaw, ot = _layout(rng)
    a, b, w = TARGET["a"], TARGET["b"], TARGET["tape"]
    g = []
    tape = []
    for (px, py, sx, sy) in ((0, b + w / 2, a + w, w / 2), (0, -(b + w / 2), a + w, w / 2), (a + w / 2, 0, w / 2, b), (-(a + w / 2), 0, w / 2, b)):
        tape.append('<geom type="box" pos="%.5f %.5f 0" size="%.5f %.5f 0.0002" rgba="%s" contype="0" conaffinity="0"/>'
                    % (px * m, py * m, sx * m, sy * m, _rgba(TARGET["rgb"])))
    g.append('<body name="w_tape" pos="%.5f %.5f 0.0002" quat="%s">%s</body>' % (tc[0] * m, tc[1] * m, _quat(tyaw), "".join(tape)))
    r = OBJ["r"]; mass = 4 / 3 * math.pi * (r * m) ** 3 * OBJ["rho"]
    for i, p in enumerate(pts):
        g.append('<body name="w_obj_%d" pos="%.5f %.5f %.5f"><freejoint/><geom type="sphere" size="%.5f" mass="%.6f" rgba="%s" '
                 'friction="%.2f 0.002 %.6f" condim="6" priority="1"/></body>'
                 % (i, p[0] * m, p[1] * m, (r + 0.3) * m, r * m, mass, _rgba(OBJ["rgb"]), OBJ["friction"], r * m * OBJ["roll"]))
    S = SPONGE
    g.append('<body name="w_sponge" pos="%.5f %.5f %.5f" quat="%s"><freejoint/><geom name="w_sponge_foam" type="box" size="%.5f %.5f %.5f" '
             'mass="%.5f" rgba="%s" friction="%.2f 0.005 0.0001" priority="1" solref="0.01 1" solimp="0.8 0.95 0.003"/></body>'
             % (sp[0] * m, sp[1] * m, (S["T"] / 2 + 0.3) * m, _quat(syaw), S["L"] / 2 * m, S["W"] / 2 * m, S["T"] / 2 * m, S["mass"], _rgba(S["rgb"]), S["friction"]))
    O = OTHER
    g.append('<body name="w_other" pos="%.5f %.5f 0.0005"><freejoint/><geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" mass="%.3f" '
             'rgba="%s" friction="0.8 0.005 0.0001"/></body>' % (ot[0] * m, ot[1] * m, O["r"] * m, O["h"] / 2 * m, O["h"] / 2 * m, O["mass"], _rgba(O["rgb"])))
    scene["mjcf"] = "<mujoco><worldbody>%s</worldbody></mujoco>" % "".join(g)
    return scene


def goal(scene, rng):
    return TEXT or "sweep the small things into the marked area with the sponge."


def success(state):
    c = state.pos("w_tape"); R = state.rot("w_tape")
    yaw = math.atan2(R[1][0], R[0][0])
    a, b = TARGET["a"] + TARGET["tape"] / 2, TARGET["b"] + TARGET["tape"] / 2
    inside = sum(1 for k in range(OBJ["n"]) if _in_rect(state.pos("w_obj_%d" % k), c, yaw, a, b) and state.pos("w_obj_%d" % k)[2] < 25.0)
    o0, o1 = state.start["w_other"], state.pos("w_other"); Ro = state.rot("w_other")
    other_ok = math.hypot(o1[0] - o0[0], o1[1] - o0[1]) <= 30 and math.degrees(math.acos(max(-1.0, min(1.0, Ro[2][2])))) <= 25
    released = not state.touching("w_sponge")["robot"]
    rb = state.robot
    home = (float(np.linalg.norm(np.array(rb["tcp_mm"]) - np.array(REST_TCP))) < 25 and rb["opening_mm"] > 70 and rb["approach_dir"][2] < -0.97)
    ok = inside >= math.ceil(0.9 * OBJ["n"]) and other_ok and released and home
    return {"ok": bool(ok), "inside": int(inside), "n": OBJ["n"], "other_undisturbed": bool(other_ok), "sponge_released": bool(released),
            "home_and_open": bool(home)}
