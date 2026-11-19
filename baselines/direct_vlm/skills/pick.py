"""Baseline: direct VLM control (docs/baselines.md). Astra (gpt-6-astra) is the whole controller, as in panda_twin
code/real/astra_e2e.py (11-06): no skill code, no planner; Astra calls tools in a loop (Responses API function calls,
one per turn) until it calls done():
    look()                       both cameras' colour images (the wrist one with the fingertips' straight-down points
                                 and the +x/+y directions drawn on it) + the arm's state
    locate(camera, points)       pixels of the latest look -> 3D points (mm) on the surface seen there (the depth)
    move(x, y, z, yaw_deg, stop_on_contact, [tilt_deg, lean_deg])   the fingertip point along a straight line
    gripper(width_mm)            0 = close with force (grasp), else to that opening
    done(summary)
The tools' code is astra_e2e.py's Episode methods, here over ctx.robot (the same RealSim); Astra is reached through the
world server (ctx.vlm.converse: the keys never enter this sandbox). Changed from the original, and only this:
  - the task sentence (pick and lift, its success check, the 60 s robot-time limit: the clutter-pick task's);
  - the wrist camera's description when it is at its calibrated mount (the fingertips show in its view);
  - the episode ends when robot time is up (no motion is possible after it);
  - the model budget per episode is the run's (max_usd_per_episode), not the original's $6."""
import json
import math
import time
import traceback

import cv2
import numpy as np

from helpers.original import INSTRUCTIONS, TOOLS

MAX_CALLS = 70                                            # tool calls (astra_e2e.py --max-calls default)
EFFORT = "medium"                                         # (astra_e2e.py EFFORT)
V_GUARD, V_LOW, V_FREE = 0.04, 0.03, 0.15                 # m/s (astra_e2e.py): stop_on_contact, low (< 6 cm), free moves
WRIST_TWIN = ("(the fingers are mostly out of its view;\n   it sits beside the hand, so the point under the fingers is "
              "not the image centre)")
WRIST_CALIBRATED = ("(the two fingers show in its lower corners;\n   it sits beside the hand, so the point under the "
                    "fingers is not the image centre)")


def text_item(t):
    return {"type": "input_text", "text": t}


def mm(v):
    return [int(round(float(c) * 1000)) for c in v]


class Episode:
    def __init__(self, ctx):
        self.ctx, self.S = ctx, ctx.robot
        L = ctx.library
        self.B, self.T = L.B, L.T
        self.TIP = L.T.FINGERTIP
        self.RobotStop = L.RB.RobotStop                          # (real_sim.RobotStop, as the original imports it)
        self.last = {}
        self.dir = ctx.out / "astra"; self.dir.mkdir(exist_ok=True)
        self.n_img, self.images = 0, []
        self.log, self.calls, self.cost = [], 0, 0.0
        self.n_tools, self.stops, self.ended = 0, 0, None
        self.exchange = open(ctx.out / "exchange.jsonl", "w")

    # ---------------------------------------------------------------- images: sent with the next model call
    def png_item(self, rgb):
        self.images.append(np.ascontiguousarray(rgb))
        return {"type": "input_image", "image": len(self.images) - 1, "detail": "high"}

    # ---------------------------------------------------------------- the arm's state (astra_e2e.py)
    def state(self):
        S = self.S
        S.flush(); S.sync()
        d = S.d
        R = d.site_xmat[S.tcp].reshape(3, 3).copy(); tcp = d.site_xpos[S.tcp].copy()
        tip = tcp + R[:, 2] * self.TIP
        y = R[:, 1]; yaw = (math.degrees(math.atan2(y[1], y[0])) + 90) % 180 - 90
        w = S.finger_width()
        f = S.force_now()
        return dict(tip=tip, R=R, yaw=yaw, width=w, force=f)

    def state_text(self, st):
        tilt = math.degrees(math.acos(float(np.clip(-st["R"][2, 2], -1, 1))))
        return (f"fingertip point ({', '.join(str(c) for c in mm(st['tip']))}) mm, yaw {st['yaw']:.0f} deg"
                + (f", fingers {tilt:.0f} deg off vertical" if tilt > 2 else "")
                + f", gripper opening {st['width'] * 1000:.1f} mm, force on the hand {np.linalg.norm(st['force']):.1f} N")

    # ---------------------------------------------------------------- tools (astra_e2e.py)
    def look(self):
        S = self.S
        st = self.state()
        items = [text_item(self.state_text(st))]
        for cam, name in (("d415", "scene"), ("L_wrist", "wrist")):
            o = S.observe_frame(cam)
            self.last[name] = o
            img = o["rgb"].copy()
            if name == "wrist":
                img = self.draw_wrist(img, o, st)
            n = self.n_img; self.n_img += 1
            cv2.imwrite(str(self.dir / f"{n:03d}_{name}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            items += [text_item(f"{name} camera ({img.shape[1]}x{img.shape[0]} px):"), self.png_item(img)]
        return items, f"look ({self.state_text(st)})"

    def draw_wrist(self, img, o, st):
        R, tip, w = st["R"], st["tip"], st["width"]
        y = R[:, 1].copy(); y[2] = 0; y /= max(np.linalg.norm(y), 1e-9)
        g = np.r_[tip[:2], 0.0]
        P = np.array([g + y * w / 2, g - y * w / 2, g, g + [0.04, 0, 0], g + [0, 0.04, 0]])
        uv, z = self.B.Perception.project(P, o["R"], o["eye"], o["K"])
        if (z > 0).all():
            q = [tuple(int(round(c)) for c in p) for p in uv]
            cv2.line(img, q[0], q[1], (0, 255, 255), 1, cv2.LINE_AA)
            for p in q[:2]:
                cv2.rectangle(img, (p[0] - 5, p[1] - 5), (p[0] + 5, p[1] + 5), (0, 255, 255), 2)
            cv2.drawMarker(img, q[2], (0, 255, 255), cv2.MARKER_CROSS, 14, 2)
            o0 = np.array([70, img.shape[0] - 70])
            for p, lab, col in ((uv[3], "+x", (255, 80, 80)), (uv[4], "+y", (80, 255, 80))):
                dv = p - uv[2]; dv = dv / max(np.linalg.norm(dv), 1e-9) * 34
                e = tuple(int(c) for c in o0 + dv)
                cv2.arrowedLine(img, tuple(int(c) for c in o0), e, (0, 0, 0), 4, cv2.LINE_AA, tipLength=0.3)
                cv2.arrowedLine(img, tuple(int(c) for c in o0), e, col, 2, cv2.LINE_AA, tipLength=0.3)
                cv2.putText(img, lab, (e[0] + 3, e[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2, cv2.LINE_AA)
        return img

    def locate(self, camera, points):
        o = self.last.get(camera)
        if o is None:
            return [text_item("no look yet: call look() first")], "locate: no look"
        W, H, fx, fy, cx, cy = o["K"]
        res = []
        for p in points[:20]:
            try:
                u, v = float(p[0]), float(p[1])
            except Exception:
                res.append(None); continue
            ui, vi = int(round(u)), int(round(v))
            if not (0 <= ui < W and 0 <= vi < H):
                res.append(None); continue
            win = o["depth"][max(vi - 3, 0):vi + 4, max(ui - 3, 0):ui + 4]
            win = win[np.isfinite(win)]
            if len(win) < 5:
                res.append(None); continue
            zc = float(np.median(win))
            X = o["eye"] + o["R"] @ np.array([(u - cx) / fx * zc, (v - cy) / fy * zc, zc])
            res.append(mm(X))
        txt = json.dumps([{"pixel": p, "xyz_mm": r} for p, r in zip(points[:20], res)])
        return [text_item(txt)], f"locate {camera} {txt[:200]}"

    def R_of(self, yaw_deg, tilt_deg=0.0, lean_deg=0.0):
        B = self.B
        tilt = math.radians(float(np.clip(tilt_deg, -45, 45))); lean = math.radians(float(np.clip(lean_deg, -45, 45)))
        return B.down(math.radians(yaw_deg)) @ B.small_rot([0, tilt, 0]) @ B.small_rot([lean, 0, 0])

    def move(self, x, y, z, yaw_deg, stop_on_contact, tilt_deg=0.0, lean_deg=0.0):
        S, T, TIP = self.S, self.T, self.TIP
        if z < -3:
            return [text_item("refused: the fingertips may not go below z = -3 mm")], "move refused (z)"
        R = self.R_of(yaw_deg, tilt_deg, lean_deg)
        tip = np.array([x, y, z], float) / 1000
        target = tip - R[:, 2] * TIP
        what = f"move ({x:.0f}, {y:.0f}, {z:.0f}) yaw {yaw_deg:.0f}{' tilt %.0f' % tilt_deg if tilt_deg else ''}{' lean %.0f' % lean_deg if lean_deg else ''} {'stop on contact' if stop_on_contact else 'push'}"
        self.ctx.log("astra: " + what)
        msg = ""
        try:
            S.flush()
            p0 = S.p_cmd.copy()
            rot = float(np.linalg.norm(T.rotvec(S.R_cmd.T @ R)))
            low = min(p0[2], target[2]) - TIP < 0.06
            if stop_on_contact:
                if rot > math.radians(2):
                    S.move(p0, R, S.g_cmd, max(1.0, rot * 1.5), phase="astra: turn the hand"); S.flush()
                r = self.guarded(target, R)
                if r.get("contact"):
                    msg = f"stopped by a contact ({r['contact']}) before the target. "
            else:
                dist = float(np.linalg.norm(target - p0))
                S.move(target, R, S.g_cmd, max(1.0, dist / (V_LOW if low else V_FREE)), phase="astra: push" if low else "astra: move")
                if S._pend is not None:                               # (real_sim SETTLE_PHASES: the end settled)
                    S._pend["phase_end"] = "descend"
                S.flush()
        except T.Unreachable as e:
            msg = f"not done: the arm cannot do this move ({e}); "
            try:
                S.resync()
            except Exception:
                pass
        except self.RobotStop as e:
            self.stops += 1
            msg = f"SAFETY STOP: {e}; the arm holds where it stopped. "
            S.resync()
        st = self.state()
        txt = msg + "now: " + self.state_text(st)
        return [text_item(txt)], what + " -> " + txt

    def guarded(self, target, R):
        """RealSim.guarded_line with the force guard only (astra_e2e.py)."""
        S, B, T = self.S, self.B, self.T
        S._set_phase("astra: move (stop on contact)")
        p0 = S.p_cmd.copy()
        n = max(int(round(np.linalg.norm(target - p0) / V_GUARD / B.DT)), 1)
        Q = [S.q_cmd.copy()]
        for i in range(1, n + 1):
            q, e = S.ik(p0 + (target - p0) * i / n, R, Q[-1])
            if e > 0.002 or np.abs(q - Q[-1]).max() > 0.15:
                raise T.Unreachable("no straight line there within the arm's limits" + (f" ({S.last_unsafe})" if e >= 1.0 and getattr(S, "last_unsafe", None) else ""))
            Q.append(q)
        S._queue(Q[1:], S.g_cmd, B.DT * S.speed, kind="guarded")
        r = S.flush(guard=dict(dz=1.0, df=S.rig["contact"]["df"]))
        S.q_cmd = S.q_sent.copy()
        S.p_cmd = S.d.site_xpos[S.tcp].copy(); S.R_cmd = R
        return dict(contact=r.get("contact") if r else None)

    def gripper(self, width_mm):
        S, T = self.S, self.T
        w = float(np.clip(width_mm, 0, 80)) / 1000
        g = 0.0 if w < 0.0005 else w / 2
        self.ctx.log(f"astra: gripper {width_mm:.0f} mm")
        try:
            S.flush()
            S.move(S.p_cmd, S.R_cmd, g, 0.3, phase="close the gripper" if g == 0 else "astra: gripper"); S.flush()
        except self.RobotStop as e:
            self.stops += 1; S.resync()
            return [text_item(f"SAFETY STOP: {e}")], "gripper: stop"
        except T.Unreachable as e:
            return [text_item(f"not done: {e}")], "gripper: unreachable"
        st = self.state()
        txt = f"gripper opening now {st['width'] * 1000:.1f} mm. " + self.state_text(st)
        return [text_item(txt)], f"gripper {width_mm:.0f} -> {st['width'] * 1000:.1f} mm"

    def tool(self, name, kw):
        if name == "look":
            return self.look()
        if name == "locate":
            return self.locate(kw.get("camera", "wrist"), kw.get("points") or [])
        if name == "move":
            return self.move(float(kw["x"]), float(kw["y"]), float(kw["z"]), float(kw["yaw_deg"]), bool(kw.get("stop_on_contact", True)),
                             float(kw.get("tilt_deg") or 0.0), float(kw.get("lean_deg") or 0.0))
        if name == "gripper":
            return self.gripper(float(kw["width_mm"]))
        if name == "done":
            self.ended = "done: " + str(kw.get("summary", ""))[:500]
            return [text_item("episode ended")], self.ended
        return [text_item(f"unknown tool {name}")], f"unknown tool {name}"

    # ---------------------------------------------------------------- Astra, through the world server
    def ask(self, inp, prev=None):
        ims, self.images = self.images, []
        self.exchange.write(json.dumps({"request": {"previous_response_id": prev, "input": inp, "n_images": len(ims)}}) + "\n")
        r = self.ctx.vlm.converse(inp, ims, instructions=self.instructions, tools=TOOLS, previous=prev, effort=EFFORT,
                                  max_output_tokens=20000)
        self.exchange.write(json.dumps({"response": r}) + "\n"); self.exchange.flush()
        if not r.get("ok"):
            raise RuntimeError(f"Astra: {r.get('why')}")
        self.calls += 1; self.cost += float(r.get("usd") or 0)
        self.log.append(dict(robot_s_left=round(self.ctx.time_left(), 2), api=dict(s=r.get("wait_s"), tokens=r.get("tokens"), cost=r.get("usd"),
                                                               status=r.get("status"))))
        return r

    def run(self):
        S, ctx = self.S, self.ctx
        st = self.state()
        ins = INSTRUCTIONS
        if "wrist_cam" in getattr(S, "calib", {}):              # (CHANGED: the calibrated wrist mount sees the fingers)
            assert WRIST_TWIN in ins
            ins = ins.replace(WRIST_TWIN, WRIST_CALIBRATED)
        self.instructions = ins.format(bx=S.base[0] * 1000, by=S.base[1] * 1000, max_calls=MAX_CALLS)
        (ctx.out / "instructions.txt").write_text(self.instructions)
        items, _ = self.look()
        first = [text_item(ctx.task["instruction"] +                        # (CHANGED: the clutter-pick task's terms)
                           " It is done when the object is held up in the gripper (at least 5 cm over where it lay, held "
                           "by both fingers, nothing else lifted) for 1 second; then call done(). The other objects may be "
                           "moved but must stay on the table. The arm stops moving after 60 s of robot time (the time you "
                           "take to think does not count). The arm is at its start pose; this is the first look:")] + items
        resp = self.ask([{"role": "user", "content": first}])
        nudges = 0
        while True:
            outs = [it for it in resp.get("output", []) if it.get("type") == "function_call"]
            texts = [c.get("text", "") for it in resp.get("output", []) if it.get("type") == "message" for c in it.get("content", [])]
            if texts:
                self.log.append(dict(astra_text=" ".join(texts)[:2000]))
            if not outs:
                if nudges >= 2 or resp.get("status") == "incomplete" and nudges >= 1:
                    self.ended = self.ended or "no more tool calls"; break
                nudges += 1
                resp = self.ask([{"role": "user", "content": [text_item("Continue with the tools; call done() when the task is "
                                                                           "finished or cannot be done.")]}], prev=resp["id"])
                continue
            results = []
            for c in outs:
                try:
                    kw = json.loads(c.get("arguments") or "{}")
                except Exception:
                    kw = {}
                t0 = time.time()
                try:
                    items, summ = self.tool(c["name"], kw)
                except Exception as e:
                    traceback.print_exc()
                    items, summ = [text_item(f"error: {e!r}"[:300])], f"{c['name']} error {e!r}"[:300]
                self.n_tools += 1
                self.log.append(dict(tool=c["name"], args=kw, result=summ, wall_s=round(time.time() - t0, 1)))
                ctx.log("tool", n=self.n_tools, name=c["name"], result=summ[:300])
                results.append({"type": "function_call_output", "call_id": c["call_id"], "output": items})
            if self.ended:
                break
            if ctx.time_left() <= 0:                                    # (CHANGED: the episode's robot time is up)
                self.ended = "robot time limit"; break
            if self.n_tools >= MAX_CALLS:
                self.ended = f"tool call limit ({MAX_CALLS})"; break
            if self.n_tools >= MAX_CALLS - 5:
                results.append({"role": "user", "content": [text_item(f"{MAX_CALLS - self.n_tools} tool calls left.")]})
            resp = self.ask(results, prev=resp["id"])


def run(ctx):
    ep = Episode(ctx)
    try:
        ep.run()
    except Exception as e:
        traceback.print_exc(); ep.ended = ep.ended or f"crash: {e!r}"[:300]
    (ctx.out / "run.json").write_text(json.dumps(dict(ended=ep.ended, tool_calls=ep.n_tools, astra_calls=ep.calls,
                                                     cost_usd=round(ep.cost, 4), safety_stops=ep.stops, log=ep.log), default=str))
    ep.exchange.close()
    return dict(result=ep.ended, tool_calls=ep.n_tools, astra_calls=ep.calls, cost_usd=round(ep.cost, 4), safety_stops=ep.stops)
