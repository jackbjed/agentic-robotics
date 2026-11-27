import math
import numpy as np

# TARGET world of the task "insert" (hidden from every agent): ONE fixed ring, ONE fixed stand, two fixed other things.
# Episodes differ only in where the things stand and how they are turned. Same format as the sessions' worlds
# (scene / goal / success), so that the same exporter and runner serve it. Lengths in mm.
N_SEG = 24
OD, DH, H = 44.0, 13.0, 24.0             # the ring: outer diameter, hole, height
DS, L = 10.0, 85.0                       # the stick: diameter, length above the base
BASE, BT = 110.0, 12.0                   # the stand's square base: side, thickness
RING_RGB, STAND_RGB, STICK_RGB = (0.92, 0.45, 0.10), (0.78, 0.62, 0.42), (0.55, 0.38, 0.22)
RING_RHO, RING_FR, STAND_RHO, STAND_FR = 900.0, 0.6, 700.0, 0.7
REST_TCP = (-478.0, 41.0, 350.0)
GAP = 25.0
TEXT = None                              # (filled by the experiment: the task's text is the same everywhere)


def _rgba(c):
    return "%.3f %.3f %.3f 1" % tuple(c)


def scene(scene, rng):
    m = 0.001
    scene["table"] = {"height_mm": 0.0, "color": "dark grey", "pattern": "plain"}
    r_c, r_s = OD / 2 + 45, BASE * 0.71 + 10
    for _ in range(4000):
        cx, cy = rng.uniform(-430, -210), rng.uniform(-150, 220)
        sx, sy = rng.uniform(-430, -210), rng.uniform(-150, 220)
        if math.hypot(cx - sx, cy - sy) > r_c + r_s + GAP:
            break
    feet = [(cx, cy, r_c), (sx, sy, r_s)]
    g = []
    rin, t = DH / 2, (OD - DH) / 2
    Lh = (OD / 2) * math.tan(math.pi / N_SEG) + 0.3
    yaw_c = rng.uniform(0, 2 * math.pi)
    fr = "%.2f 0.005 0.0001" % RING_FR
    segs = []
    for k in range(N_SEG):
        an = yaw_c + 2 * math.pi * k / N_SEG; rc = rin + t / 2
        segs.append('<geom type="box" size="%.5f %.5f %.5f" pos="%.5f %.5f %.5f" euler="0 0 %.5f" rgba="%s" density="%.0f" friction="%s"/>'
                    % (t / 2 * m, Lh * m, (H - 0.5) / 2 * m, rc * math.cos(an) * m, rc * math.sin(an) * m, (0.5 + (H - 0.5) / 2) * m, an,
                       _rgba(RING_RGB), RING_RHO, fr))
    for k in range(8):
        an = yaw_c + 2 * math.pi * (k + 0.5) / 8; rc = rin + t / 2
        segs.append('<geom type="box" size="%.5f %.5f %.5f" pos="%.5f %.5f %.5f" euler="0 0 %.5f" rgba="%s" density="%.0f" friction="%s"/>'
                    % (t / 2 * 0.9 * m, 2.0 * m, H / 2 * m, rc * math.cos(an) * m, rc * math.sin(an) * m, H / 2 * m, an, _rgba(RING_RGB), RING_RHO, fr))
    g.append('<body name="w_cyl" pos="%.5f %.5f 0.0003"><joint type="free" damping="0.003"/>%s</body>' % (cx * m, cy * m, "".join(segs)))
    yaw_s = rng.uniform(0, 2 * math.pi)
    fs = "%.2f 0.005 0.0001" % STAND_FR
    g.append('<body name="w_stand" pos="%.5f %.5f 0.0003" euler="0 0 %.5f"><freejoint/>'
             '<geom type="box" size="%.5f %.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.0f" friction="%s"/>'
             '<geom type="cylinder" size="%.5f %.5f" pos="0 0 %.5f" rgba="%s" density="%.0f" friction="%s"/></body>'
             % (sx * m, sy * m, yaw_s, BASE / 2 * m, BASE / 2 * m, BT / 2 * m, BT / 2 * m, _rgba(STAND_RGB), STAND_RHO, fs,
                DS / 2 * m, L / 2 * m, (BT + L / 2) * m, _rgba(STICK_RGB), STAND_RHO, fs))
    others = [('type="box" size="0.020 0.015 0.0175" pos="0 0 0.0175"', 26.0, (0.15, 0.35, 0.75)),
              ('type="cylinder" size="0.018 0.025" pos="0 0 0.025"', 18.0, (0.20, 0.60, 0.30))]
    for i, (geo, r, col) in enumerate(others):
        for _ in range(400):
            x, y = rng.uniform(-470, -130), rng.uniform(-230, 290)
            if all(math.hypot(x - fx, y - fy) > r + fr_ + 10 for fx, fy, fr_ in feet):
                feet.append((x, y, r))
                g.append('<body name="w_d%d" pos="%.5f %.5f 0.0003" euler="0 0 %.4f"><freejoint/><geom %s rgba="%s" density="900" '
                         'friction="0.6 0.005 0.0001"/></body>' % (i, x * m, y * m, rng.uniform(0, 6.28), geo, _rgba(col)))
                break
    scene["mjcf"] = "<mujoco><worldbody>%s</worldbody></mujoco>" % "".join(g)
    return scene


def goal(scene, rng):
    return TEXT or "put the cylinder on the stick."


def success(state):
    pc, Rc = np.array(state.pos("w_cyl")), np.array(state.rot("w_cyl"))
    ps, Rs = np.array(state.pos("w_stand")), np.array(state.rot("w_stand"))
    ax_s, ax_c = Rs[:, 2], Rc[:, 2]
    base_pt = ps + Rs @ np.array([0.0, 0.0, BT])
    tilt_s = math.degrees(math.acos(min(1.0, ax_s[2])))
    tilt_rel = math.degrees(math.acos(min(1.0, abs(float(ax_c @ ax_s)))))
    lim = (DH / 2) / math.cos(math.pi / N_SEG) - DS / 2 + 0.5
    d, s = [], []
    for q in (pc, pc + H * ax_c):
        v = q - base_pt; sv = float(v @ ax_s); s.append(sv)
        d.append(float(np.linalg.norm(v - sv * ax_s)))
    on = max(d) <= lim and -3.0 <= s[0] and s[0] + 0.5 <= L - min(H, L) + 1.0
    off = not state.touching("w_cyl")["robot"] and not state.touching("w_stand")["robot"]
    rb = state.robot
    home = (float(np.linalg.norm(np.array(rb["tcp_mm"]) - np.array(REST_TCP))) < 25 and rb["opening_mm"] > 70 and rb["approach_dir"][2] < -0.97)
    return {"ok": bool(on and tilt_s < 5 and tilt_rel < 8 and off and home), "on_stick": bool(on), "stand_tilt_deg": round(tilt_s, 1),
            "hands_off": bool(off), "home_and_open": bool(home), "axis_off_mm": round(max(d), 2)}
