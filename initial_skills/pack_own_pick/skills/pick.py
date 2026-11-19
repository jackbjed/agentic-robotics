"""Clutter pick of a tape roll (v2).

1. Wrist camera at the start pose: find the roll (SAM3 text masks, colour-checked); fallbacks: scene camera, one VLM call.
2. Move the camera over the roll (~0.31 m): re-find it, build an obstacle mask (SAM3 'small object' + raised depth).
3. Score grasps: outer (both fingers outside, partial opening) and wall (one finger in the hole), best free first.
4. Hover 4 cm above, re-find the roll with the camera close (less calibration error), descend, close, check, lift.
5. On a miss: re-open, back up, re-observe, next candidate.
"""
import math
import numpy as np

from helpers.geom import yaw_of, pix_to_plane
from helpers.target import find_roll, ask_vlm, ROLL_R, ROLL_H
from helpers.grasp import obstacle_mask, candidates, debug_image, drag_candidates, DRAG_L
from helpers.fingers import finger_feats, calib_shift, finger_mask

FTZ = 0.011          # fingertip height at the grasp (m); the roll is 21 mm tall
LOOK_Z = 0.20        # TCP height for the planning look
USE_INNER = False
USE_DRAG = True      # one finger in the hole, slide the roll out of the cluster when boxed in
DRAG_SCORE = 0.6     # best grasp score above which the drag comes first (else only after a blocked grasp)
FORCE_VLM = False    # True only to test the model fallback (run 017: points within 1 px of SAM3)
SKIP_LOOK = True     # plan from the start frame when the roll is well inside it
USE_SHIFT = True     # correct the hover estimate by the fingers' image shift (wrist calibration error)
USE_JOINT = True     # long transits as joint-space moves (faster than a Cartesian line with a wrist turn)
JOINT_TURN_S = 3.5   # estimated joint-move time above which the transit goes joint-space
TURN_W = 0.03        # grasp score per second of estimated wrist turn (was 0.02: run 026 took a 14.7 s turn)
HIGH_BLOCK_DZ = 0.011   # a descent stopped this far above the grasp height: blocked by something tall
HIGH_BLOCK_AXIS = 0.35  # ... then hand axes within this (rad) of it are skipped
FAST_DESCENT = False  # correction + narrowing merged with the descent's upper part: no faster (run 042, the twin
                      # stretches the descent), one more blocked wall grasp: off
LIFT_Z = 0.10        # TCP height of the lift (0.09 saved nothing in run 042)
USE_OCCL = True     # circle fit on the roll's visible edge only (not the image border / our fingers)
DEBUG = False       # True: save plan overlays and the wrist frame of blocked descents in program/


def _r(x):
    return [round(float(v), 4) for v in np.asarray(x).ravel()]


class Skill:
    def __init__(self, ctx):
        self.ctx = ctx
        self.T = ctx.tools
        self.rb = ctx.robot
        self.name = ctx.task.get("object", "red tape roll")
        self.color = self.name.split()[0].lower()
        self.r = ROLL_R.get(self.color, 0.024)
        self.phr = [self.name, f"{self.color} ring", f"{self.color} tape"]
        self.first = None

    def occl(self, o):
        """Our fingers in a wrist frame (they can hide part of the roll), or None."""
        if not USE_OCCL:
            return None
        try:
            return finger_mask(o["rgb"])
        except Exception as e:
            self.log("occl_err", err=repr(e))
            return None

    def log(self, what, **kw):
        self.ctx.log(what, **kw)

    def ik_eval(self, pos, R):
        """-> (margin to the joint limits (rad), joint move time (s) at 0.3 rad/s) for the IK solution, or None.
        margin -1: no solution within the limits."""
        rb = self.rb
        try:
            q0 = np.asarray(rb.q_cmd, float).ravel()[:7]
            res = rb.ik(np.asarray(pos, float), R, q0)
            err = 0.0
            if isinstance(res, dict):
                q, err = res.get("q"), float(res.get("err", 0.0) or 0.0)
            elif isinstance(res, (tuple, list)) and len(res) == 2 and np.ndim(res[1]) == 0:
                q, err = res[0], float(res[1])
            else:
                q = res
            if q is None or err > 0.01:
                return -1.0, 99.0
            q = np.asarray(q, float).ravel()[:7]
            lo = np.asarray(rb.lo, float).ravel()[:7]
            hi = np.asarray(rb.hi, float).ravel()[:7]
            return float(np.min(np.minimum(q - lo, hi - q))), float(np.max(np.abs(q - q0)) / 0.3)
        except Exception as e:
            if not getattr(self, "_ik_logged", False):
                self._ik_logged = True
                self.log("ik_err", err=repr(e))
            return None

    def joint_margin(self, pos, R):
        r = self.ik_eval(pos, R)
        return None if r is None else r[0]

    def rank(self, cands, n=14):
        """Re-rank the best candidates by score + the wrist-turn time, choosing per candidate the hand orientation
        (yaw or yaw+pi) that is far from the joint limits and quick to reach (run 020: an 11 s wrist turn)."""
        top = cands[:n]
        for c in top:
            pos = [c["gx"], c["gy"], FTZ + self.T.FINGERTIP]
            best = None
            for R in (self.T.down(c["yaw"]), self.T.down(c["yaw"] + math.pi)):
                ev = self.ik_eval(pos, R)
                if ev is None:
                    return cands                    # no IK available: keep the plain order
                m, tt = ev
                pen = tt + (0.0 if m >= 0.25 else (8.0 if m > 0 else 60.0))
                if best is None or pen < best[0]:
                    best = (pen, R, m, tt)
            c["R"] = best[1]
            c["turn_s"] = round(best[3], 2)
            c["total"] = c["score"] + TURN_W * best[0]
        top.sort(key=lambda c: c["total"])
        return top + cands[n:]

    def orient(self, yaw, pos=None):
        """down(yaw) or down(yaw+pi): the nearer the present hand (less wrist turn), unless its IK solution is
        close to a joint limit (run 019: a blocked descent pushed joint 7 past its limit, then every move was
        refused)."""
        y0 = yaw_of(self.rb.R_cmd)
        d1 = abs((yaw - y0 + math.pi) % (2 * math.pi) - math.pi)
        d2 = abs((yaw + math.pi - y0 + math.pi) % (2 * math.pi) - math.pi)
        Rs = [self.T.down(yaw), self.T.down(yaw + math.pi)] if d1 <= d2 else \
            [self.T.down(yaw + math.pi), self.T.down(yaw)]
        if pos is not None:
            m = [self.joint_margin(pos, R) for R in Rs]
            if m[0] is not None and m[1] is not None and m[0] < 0.3 and m[1] > m[0] + 0.05:
                Rs = Rs[::-1]
            self.log("orient", yaw=yaw, margins=m)
        return Rs

    def move(self, p, R, grip, T, phase):
        self.rb.move(np.asarray(p, float), R, grip, T, phase)

    def transit(self, p, R, grip, phase="above", up=0.03):
        """A free transit: joint-space to `up` over p (a Cartesian line with a big wrist turn ran ~1.7x slower, run
        026: 14.7 s for an 8 s turn), then a short straight line down to p. Falls back to the straight line."""
        p = np.asarray(p, float)
        ev = self.ik_eval(p, R) if USE_JOINT else None
        self.log("transit", turn_s=None if ev is None else round(ev[1], 2))
        # only for long turns: for short ones the joint move was slower (run 027) and could pick a different arm
        # configuration that slowed the later lift
        if ev is not None and ev[0] > 0 and ev[1] > JOINT_TURN_S:
            try:
                if up > 0:
                    self.rb.move_joint(p + np.array([0.0, 0.0, up]), R, grip, 1.0, phase)
                    self.rb.move(p, R, grip, 0.4, phase)
                else:
                    self.rb.move_joint(p, R, grip, 1.0, phase)
                self.rb.flush()
                return
            except self.T.Unreachable as e:
                self.log("joint_unreachable", err=repr(e))
                self.rb.resync()
        self.move(p, R, grip, 1.0, phase)
        self.rb.flush()

    def locate_initial(self):
        o = self.rb.observe_frame("L_wrist")
        t = find_roll(self.ctx, o, self.phr, self.color, log="find0", occl=self.occl(o))
        if FORCE_VLM:        # test of the fallback path only
            uv = ask_vlm(self.ctx, o, self.name)
            X = pix_to_plane(o, uv[0], uv[1], ROLL_H) if uv is not None else None
            self.log("vlm_test", uv=uv, X=None if X is None else _r(X),
                     seg=None if t is None else [t["u"], t["v"], t["x"], t["y"]])
            if X is not None:
                return float(X[0]), float(X[1])
        if t is not None:
            # well inside the frame, above the fingers: plan from this frame, no separate look
            if 130 < t["u"] < 510 and 90 < t["v"] < 370 and SKIP_LOOK:
                self.first = (o, t)
            return t["x"], t["y"]
        so = self.rb.observe_frame("d415")
        t = find_roll(self.ctx, so, self.phr + [f"{self.color} tape roll on the table"], self.color,
                      z_top=0.012, log="find0_scene")
        if t is not None:
            return t["x"], t["y"]
        uv = ask_vlm(self.ctx, o, self.name)
        self.log("vlm_point", uv=uv)
        if uv is not None:
            X = pix_to_plane(o, uv[0], uv[1], ROLL_H)
            if X is not None and -0.6 < X[0] < 0.0 and -0.3 < X[1] < 0.3:
                return float(X[0]), float(X[1])
        return None

    def look(self, x, y, R):
        try:
            self.transit([x, y, LOOK_Z], R, self.T.OPEN_W, phase="look", up=0.0)
        except (self.T.Unreachable, self.T.RobotStop) as e:
            self.log("look_err", err=repr(e))
            self.rb.resync()
            try:
                self.move([x, y, LOOK_Z], self.T.down(0.0), self.T.OPEN_W, 1.0, "look")
                self.rb.flush()
            except (self.T.Unreachable, self.T.RobotStop) as e2:
                self.log("look_err2", err=repr(e2))
                self.rb.resync()
        o = self.rb.observe_frame("L_wrist")
        t = find_roll(self.ctx, o, self.phr, self.color, expect=(x, y), max_dev=0.06, log="find1", occl=self.occl(o))
        return o, t

    def attempt(self, cand, cx, cy):
        T, rb = self.T, self.rb
        self.block_dz = 0.0
        if cand.get("R") is not None:
            Rs = [cand["R"], self.T.down(yaw_of(cand["R"]) + math.pi)]
        else:
            Rs = self.orient(cand["yaw"], pos=[cand["gx"], cand["gy"], FTZ + T.FINGERTIP])
        offx, offy = cand["gx"] - cx, cand["gy"] - cy
        w = cand["w"]
        hover = FTZ + T.FINGERTIP + ROLL_H + 0.035
        R = None
        for Rc in Rs:
            try:
                # fully open while looking: narrow fingers hide the roll's near edge from the wrist camera
                self.transit([cand["gx"], cand["gy"], hover], Rc, T.OPEN_W)
                R = Rc
                break
            except T.Unreachable as e:
                self.log("unreachable", err=repr(e))
                rb.resync()
        if R is None:
            return "unreachable"
        # refine with the camera close to the roll (RGB only, plane at the roll top)
        cx, cy = self.hover_refine(cand["gx"], cand["gy"], R, cx, cy)
        self.last_c = (cx, cy)
        gx, gy = cx + offx, cy + offy
        gz = FTZ + T.FINGERTIP
        try:
            if FAST_DESCENT:
                # the correction and the narrowing ride on the upper part of the descent (was 0.5 s + 1.0 s)
                self.move([gx, gy, gz + 0.025], R, w, 0.5, "above")
                self.move([gx, gy, gz], R, w, 0.5, "descend")
            else:
                self.move([gx, gy, hover], R, w, 0.5, "above")
                self.move([gx, gy, gz], R, w, 1.0, "descend")
            rb.flush()
        except T.RobotStop as e:
            self.log("descend_stop", err=repr(e))
            self.retreat()
            return "blocked"
        tcp = np.asarray(rb.tcp_now())
        self.log("at_grasp", tcp=_r(tcp), want=_r([gx, gy, gz]), fw=float(rb.finger_width()))
        self.block_dz = float(tcp[2] - gz)
        if tcp[2] > gz + 0.006 or np.hypot(tcp[0] - gx, tcp[1] - gy) > 0.02:
            if DEBUG:
                ob = rb.observe_frame("L_wrist")
                self.ctx.save_image(f"blocked_{cand['kind']}_{int(self.ctx.time_left())}", ob["rgb"])
            self.retreat()
            return "blocked"
        inner = cand["kind"] == "inner"
        g_hold = T.OPEN_W if inner else 0.0

        def holding(fw):
            return (0.012 < fw < 0.036) if inner else fw > 0.003

        if inner:
            self.move([gx, gy, gz], R, T.OPEN_W, 0.6, "open inside the roll")
        else:
            self.move([gx, gy, gz], R, 0.0, 0.6, "close the gripper")
        rb.flush()
        fw = float(rb.finger_width())
        self.log("closed", fw=fw, kind=cand["kind"])
        if not holding(fw):
            self.retreat()
            return "missed"
        self.move([gx, gy, LIFT_Z], R, g_hold, 1.0, "lift")
        rb.flush()
        fw2 = float(rb.finger_width())
        self.log("lifted", fw=fw2)
        if not holding(fw2):
            self.move([gx, gy, LIFT_Z], R, T.OPEN_W, 0.5, "release")
            rb.flush()
            return "dropped"
        rb.hold(1.3)
        rb.flush()
        fw3 = float(rb.finger_width())
        self.log("held", fw=fw3)
        if not holding(fw3):
            return "dropped"
        return "ok"

    def hover_refine(self, x, y, R, cx, cy, log="find2"):
        """At (x, y, hover) with the gripper open: re-find the roll from close, corrected by the fingers' shift."""
        rb = self.rb
        o2 = rb.observe_frame("L_wrist")
        shift = (0.0, 0.0)
        try:
            ff = finger_feats(o2["rgb"])
            if USE_SHIFT:
                shift = calib_shift(ff)
            self.log("fingers", f=ff, fw=float(rb.finger_width()), shift=shift)
        except Exception as e:
            self.log("fingers_err", err=repr(e))
        t2 = find_roll(self.ctx, o2, self.phr, self.color, expect=(cx, cy), max_dev=0.025, log=log, shift=shift,
                       occl=self.occl(o2))
        if t2 is not None:
            return t2["x"], t2["y"]
        return cx, cy

    def retreat(self, z_up=0.05):
        """Straight up from where the arm is, keeping the grip (opening inside the hole would shove the roll)."""
        rb = self.rb
        try:
            rb.resync()
            p = np.asarray(rb.tcp_now(), float)
            g = float(rb.g_cmd) if rb.g_cmd is not None else self.T.OPEN_W
            self.move([p[0], p[1], max(p[2] + z_up, 0.09)], np.asarray(rb.R_cmd), g, 1.0, "retreat")
            rb.flush()
        except Exception as e:
            self.log("retreat_err", err=repr(e))
            try:
                rb.resync()
            except Exception:
                pass

    def drag(self, dc, cx, cy):
        """Finger A in the hole, finger B on free table; slide the roll DRAG_L along d. -> new roll estimate."""
        T, rb = self.T, self.rb
        n_yaw = dc["n_yaw"]
        if "flip" in dc:
            Rs = [T.down(n_yaw + math.pi * dc["flip"]), T.down(n_yaw + math.pi * (1 - dc["flip"]))]
        else:
            Rs = self.orient(n_yaw, pos=[dc["tcp0"][0], dc["tcp0"][1], 0.010 + T.FINGERTIP])
        w = dc["w"]
        d = np.asarray(dc["d"])
        hover = FTZ + T.FINGERTIP + ROLL_H + 0.035
        off = np.asarray(dc["tcp0"]) - np.array([cx, cy])
        R = None
        for Rc in Rs:
            try:
                self.transit([dc["tcp0"][0], dc["tcp0"][1], hover], Rc, T.OPEN_W)
                R = Rc
                break
            except T.Unreachable as e:
                self.log("unreachable", err=repr(e))
                rb.resync()
        if R is None:
            return None
        cx, cy = self.hover_refine(dc["tcp0"][0], dc["tcp0"][1], R, cx, cy, log="find2d")
        p0 = np.array([cx, cy]) + off
        gz = 0.010 + T.FINGERTIP
        try:
            self.move([p0[0], p0[1], hover], R, w, 0.5, "above")
            self.move([p0[0], p0[1], gz], R, w, 1.0, "descend")
            rb.flush()
        except T.RobotStop as e:
            self.log("drag_stop", err=repr(e))
            self.retreat()
            return None
        tcp = np.asarray(rb.tcp_now())
        self.log("drag_at", tcp=_r(tcp), want=_r([p0[0], p0[1], gz]))
        if tcp[2] > gz + 0.006 or np.hypot(tcp[0] - p0[0], tcp[1] - p0[1]) > 0.02:
            self.retreat()
            return None
        p1 = p0 + DRAG_L * d
        try:
            self.move([p1[0], p1[1], gz], R, w, 1.5, "push the roll")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("drag_push_err", err=repr(e))
        self.retreat()
        moved = np.asarray(rb.tcp_now())[:2] - p0
        s = float(np.dot(moved, d))
        self.log("dragged", s=s)
        return cx + max(0.0, s - 0.006) * d[0], cy + max(0.0, s - 0.006) * d[1]

    def run(self):
        rb = self.rb
        self.log("start", tcp=_r(rb.tcp_now()), yaw=yaw_of(rb.R_cmd), task=self.name)
        p = self.locate_initial()
        if p is None:
            return dict(result="target not found")
        x, y = p
        R0 = np.asarray(rb.R_cmd)
        tried = []
        last = None
        drags = 0
        for k in range(6):
            if self.ctx.time_left() < 9.0:
                break
            if k == 0 and self.first is not None:
                o, t = self.first
            else:
                o, t = self.look(x, y, R0 if k == 0 else np.asarray(rb.R_cmd))
            if t is None:
                self.log("lost_target", k=k)
                if k > 0:
                    continue
                t = dict(x=x, y=y, mask=None)
            x, y = t["x"], t["y"]
            P, ok = self.T.world(o)
            obst = obstacle_mask(self.ctx, o, P, ok, (x, y), self.r, target_mask=t.get("mask"))
            cands = candidates(o, obst, (x, y), self.r, yaw_of(rb.R_cmd))
            # skip what failed already (same kind and similar yaw)
            def axis_d(a, b):
                return abs(((a - b) + math.pi / 2) % math.pi - math.pi / 2)

            def seen(c):
                # a descent stopped well above any roll top (fingertip > ~2 cm): something tall along that hand
                # axis (the hand body / a lying bottle, run 047: blocked 3 times on axes 1.05-1.57, ok at 0.70):
                # avoid the axis for every kind
                return any((c["kind"] == a["kind"] and axis_d(c["yaw"], a["yaw"]) < 0.2) or
                           (a.get("high_block") and axis_d(c["yaw"], a["yaw"]) < HIGH_BLOCK_AXIS) for a in tried)
            cands = [c for c in cands if not seen(c)] or cands
            inner = dict(kind="inner", yaw=yaw_of(rb.R_cmd), gx=x, gy=y, w=0.0, score=0.0)
            if USE_INNER and not seen(inner):     # run 006: opening force too weak to hold; off
                cands.insert(0, inner)
            cands = self.rank(cands)
            self.log("plan", k=k, top=[{kk: (round(v, 3) if isinstance(v, float) else v) for kk, v in c.items()
                                        if kk != "R"} for c in cands[:5]])
            c = cands[0]
            if DEBUG:
                try:
                    self.ctx.save_image(f"plan{k}", debug_image(o, obst, t.get("mask"), c, (x, y)))
                    if obst.dtype != bool:
                        hi = np.clip(obst / 0.12 * 255, 0, 255).astype(np.uint8)
                        zr = np.clip(np.where(ok, P[..., 2], -0.01) / 0.12 * 255, 0, 255).astype(np.uint8)
                        self.ctx.save_image(f"hgt{k}", np.stack([hi, zr, (~ok).astype(np.uint8) * 255], -1))
                        self.log("hgt", eye=_r(o["eye"]), max=float(obst.max()), nodepth=int((~ok).sum()),
                                 zmax=float(np.max(np.where(ok, P[..., 2], 0))))
                except Exception as e:
                    self.log("dbg_err", err=repr(e))
            # boxed in (a grasp was blocked / missed, or every grasp looks blocked): slide the roll out first
            want_drag = USE_DRAG and drags < 1 and self.ctx.time_left() > 22.0 and (
                last in ("blocked", "missed", "error") or c["score"] > DRAG_SCORE)
            if want_drag:
                drags += 1
                dcs = drag_candidates(o, obst, (x, y), self.r)
                for dc in dcs[:12]:
                    pos = [dc["tcp0"][0], dc["tcp0"][1], 0.010 + self.T.FINGERTIP]
                    ev = [self.ik_eval(pos, self.T.down(dc["n_yaw"] + s)) for s in (0.0, math.pi)]
                    if any(e is None for e in ev):
                        break
                    pens = [e[1] + (0.0 if e[0] >= 0.25 else (8.0 if e[0] > 0 else 60.0)) for e in ev]
                    dc["flip"] = int(pens[1] < pens[0])
                    dc["total"] = dc["score"] + TURN_W * min(pens)
                if dcs and "total" in dcs[0]:
                    head = sorted([d for d in dcs[:12] if "total" in d], key=lambda d: d["total"])
                    dcs = head + dcs[len(head):]
                self.log("drag_plan", top=dcs[:3])
                # up to two drags, the second with a clearly different finger direction if the first one's descent
                # was blocked (run 023)
                picks = []
                for dc in dcs:
                    if dc["score"] >= 2.0 or len(picks) == 2:
                        break
                    if all(abs((dc["n_yaw"] - p["n_yaw"] + math.pi) % (2 * math.pi) - math.pi) > 0.6 for p in picks):
                        picks.append(dc)
                nxy = None
                for dc in picks:
                    if self.ctx.time_left() < 20.0:
                        break
                    try:
                        nxy = self.drag(dc, x, y)
                    except (self.T.RobotStop, self.T.Unreachable) as e:
                        self.log("drag_err", err=repr(e))
                        self.retreat()
                        nxy = None
                    if nxy is not None:
                        break
                if nxy is not None:
                    x, y = nxy
                    tried = [a for a in tried if a.get("high_block")]   # the tall thing is still there
                    last = "dragged"
                    continue
            try:
                res = self.attempt(c, x, y)
            except (self.T.RobotStop, self.T.Unreachable) as e:
                self.log("attempt_err", err=repr(e))
                self.retreat()
                res = "error"
            self.log("attempt", k=k, kind=c["kind"], yaw=c["yaw"], res=res)
            if res == "ok":
                return dict(result="ok", attempts=k + 1)
            if res == "blocked" and getattr(self, "block_dz", 0.0) > HIGH_BLOCK_DZ:
                c = dict(c, high_block=True)
            tried.append(c)
            last = res
        return dict(result="failed", tried=len(tried))


def run(ctx):
    try:
        return Skill(ctx).run()
    except Exception as e:
        import traceback
        ctx.log("crash", err=traceback.format_exc()[-1500:])
        return dict(result="crash " + repr(e))
