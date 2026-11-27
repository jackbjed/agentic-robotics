# A stand-in practice world for the harness's own tests (not an agent's, not used in any experiment): a dustpan made of
# boxes with its mouth towards +x, a brush made of two boxes, three cubes in front of the mouth.
import math


def scene(scene, rng):
    px, py, yaw = rng.uniform(-0.05, 0.05), rng.uniform(-0.06, 0.0), rng.uniform(-0.2, 0.2)
    c, s = math.cos(yaw), math.sin(yaw)
    q = "%.5f 0 0 %.5f" % (math.cos(yaw / 2), math.sin(yaw / 2))
    cubes = ""
    for i in range(3):
        a, b = rng.uniform(0.17, 0.29), rng.uniform(-0.035, 0.035)
        col = " ".join("%.2f" % v for v in rng.uniform(0.1, 1.0, 3))
        cubes += ('<body name="w_cube_%d" pos="%.4f %.4f 0.0097"><freejoint/><geom type="box" size="0.0096 0.0096 0.0096" rgba="%s 1" mass="0.03" '
                  'friction="0.8 0.03 0.0001"/></body>' % (i, px + a * c - b * s, py + a * s + b * c, col))
    bx, by = rng.uniform(-0.33, -0.25), rng.uniform(-0.05, 0.05)
    scene["mjcf"] = ('<mujoco><worldbody>'
                     '<body name="w_pan" pos="%.4f %.4f 0.0" quat="%s"><freejoint/>'
                     '<geom type="box" size="0.10 0.09 0.002" pos="0.04 0 0.002" rgba="0.9 0.8 0.2 1" mass="0.08" friction="0.5 0.03 0.0001"/>'
                     '<geom type="box" size="0.10 0.003 0.02" pos="0.04 0.09 0.02" rgba="0.9 0.8 0.2 1" mass="0.015"/>'
                     '<geom type="box" size="0.10 0.003 0.02" pos="0.04 -0.09 0.02" rgba="0.9 0.8 0.2 1" mass="0.015"/>'
                     '<geom type="box" size="0.003 0.09 0.02" pos="-0.06 0 0.02" rgba="0.9 0.8 0.2 1" mass="0.015"/>'
                     '<geom type="box" size="0.045 0.012 0.008" pos="-0.105 0 0.015" rgba="0.9 0.8 0.2 1" mass="0.025"/></body>'
                     '<body name="w_brush" pos="%.4f %.4f 0.012" euler="0 0 1.2"><freejoint/>'
                     '<geom type="box" size="0.06 0.012 0.011" pos="-0.06 0 0" rgba="0.3 0.6 0.9 1" mass="0.015"/>'
                     '<geom type="box" size="0.06 0.025 0.012" pos="0.06 0 0" rgba="0.3 0.6 0.9 1" mass="0.025"/></body>'
                     '%s</worldbody></mujoco>' % (px, py, q, bx, by, cubes))
    scene["table"] = {"color": [float(v) for v in rng.uniform(0.2, 0.9, 3)], "friction": 0.8}
    return scene


def success(state):
    pan, R = state.pos("w_pan"), state.rot("w_pan")
    inside = []
    for n in state.names:
        if n.startswith("w_cube"):
            p = state.pos(n)
            dx, dy = p[0] - pan[0], p[1] - pan[1]
            a, b = dx * R[0][0] + dy * R[1][0], dx * R[0][1] + dy * R[1][1]
            inside.append(-60 < a < 135 and abs(b) < 88)
    arms = [state.arm(s) for s in ("left", "right")]
    held = [n for n in state.names if n.startswith("w_cube") and state.held(n)]
    ok = all(inside) and state.pos("w_brush")[0] - pan[0] > 100 and all(a["from_start_mm"] < 150 and a["from_start_deg"] < 20 for a in arms)
    return {"ok": bool(ok), "inside": inside, "held_now": held}
