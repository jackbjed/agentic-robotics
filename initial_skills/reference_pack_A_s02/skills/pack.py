"""Pack: put the named objects into the white box, then close its lid.

1. Survey: the wrist camera high over the work area (sees the box, its lid and the objects); the box's footprint and
   the lid's side from depth; each object located by SAM3 text masks (checked by size, colour, not in the box).
2. Per object: a close look from above, a grasp from its mask (roll: across; block: across a side; screwdriver: across
   the handle; cable: across one strand of the coil), descend, close, check the width, lift.
3. Carry over its slot in the box (a layout per set of objects), lower inside the walls, open, up.
4. The lid: fingers behind the lid's outer face, push it over the hinge and follow it down along an arc around the
   hinge to closed; check from above, press / repeat once.
"""
import math
import numpy as np

from helpers.geom import yaw_of, pix_to_plane
from helpers.fingers import finger_feats, calib_shift, finger_mask
from helpers.packp import (find_box, find_object, grasp_plan, kind_of, cloud, box_wall_pts, hand_cost, tilt_R,
                           lid_from_points, cable_strand_plans, lid_angle_strip,
                           BOX_HALF, BOX_H)

SURVEY = (-0.31, 0.02, 0.48)   # TCP for the survey look
LOOK_Z = 0.25                  # TCP height of the close look over an object
CARRY_Z = 0.17                 # TCP height of carries
LIFT_Z = 0.15
INNER = 0.086                  # inner half size of the box usable by fingers / objects
DEBUG = True
USE_START_AWAY = False        # move away from the box before rising (run 036: fired almost always, 1.5-4 s,
                              # boxes still lost at the start)
USE_BOX_DRAG = True          # slide the empty box away from objects lying at its hinge side (session 2)
USE_PROP = False              # pushing the lid up to near vertical: it fell shut in 5 of ~12 uses (runs 011-014)


def _r(x, n=4):
    return [round(float(v), n) for v in np.asarray(x).ravel()]


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class Pack:
    def __init__(self, ctx):
        self.ctx = ctx
        self.T = ctx.tools
        self.rb = ctx.robot
        self.names = list(ctx.task.get("objects") or [ctx.task.get("object")])
        # the coil first: laid down on an empty floor it lies flat (run 034: laid last onto the screwdriver it
        # stayed upright and held the lid at 13 deg)
        self.names.sort(key=lambda n: 0 if kind_of(n) == "cable" else 1)
        self.box = None
        self.shift = (0.0, 0.0)
        self.cam_h = None
        self.tall = None
        self.propped = False
        self.sizes = {}
        self.failed = []
        self.drags = {}
        self.last_try = None
        self.survey_obs = {}
        self.survey_frame = None

    def log(self, what, **kw):
        self.ctx.log(what, **kw)

    # ---------------------------------------------------------------- motion
    def ik_eval(self, pos, R):
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
            self.log("ik_err", err=repr(e))
            return None

    def pick_yaw(self, pos, yaws):
        """Among candidate closing yaws, the one with a safe IK solution and the least joint motion."""
        best = None
        for y in yaws:
            ev = self.ik_eval(pos, self.T.down(y))
            if ev is None:
                return yaws[0]
            m, tt = ev
            pen = tt + (0.0 if m >= 0.25 else (8.0 if m > 0 else 60.0))
            if best is None or pen < best[0]:
                best = (pen, y)
        return best[1]

    def go(self, p, R, grip, phase, T=1.0, joint_if=3.5):
        """Free move: joint-space when the wrist turn is long, else a straight line. Flushes."""
        p = np.asarray(p, float)
        ev = self.ik_eval(p, R)
        if ev is not None and ev[0] > 0 and ev[1] > joint_if:
            try:
                self.rb.move_joint(p, R, grip, T, phase)
                self.rb.flush()
                return
            except self.T.Unreachable as e:
                self.log("joint_unreachable", err=repr(e))
                self.rb.resync()
        self.rb.move(p, R, grip, T, phase)
        self.rb.flush()

    def travel(self, p, R, grip, phase="transit", T=1.0, extra=0.0):
        """A free move that climbs over tall things (the open lid: run 005, a transit at 0.25 m swept through it,
        shoved the box and flung the lid open): up, across, down when the straight path passes near them."""
        rb = self.rb
        p = np.asarray(p, float)
        p0 = np.asarray(rb.tcp_now(), float)
        tall = self.tall
        safe = None
        if tall is not None and len(tall):
            s = np.linspace(0.0, 1.0, 25)[:, None]
            seg = p0[None, :2] * (1 - s) + p[None, :2] * s
            d = np.min(np.linalg.norm(tall[None, :, :2] - seg[:, None, :], axis=2), axis=0)
            near = tall[d < 0.14]
            if len(near):
                safe = float(np.percentile(near[:, 2], 98)) + 0.05 + extra
        if safe is not None and phase == "look":
            p = p.copy()
            p[2] = max(p[2], safe)
        if safe is not None and min(p0[2], p[2]) < safe:
            self.log("travel_over", safe=round(safe, 3), frm=_r(p0), to=_r(p))
            # high transits can be out of reach near the robot (run 008): lower ones next
            for h in (safe, safe - 0.04, safe - 0.08):
                if h < max(p0[2], p[2]) - 1e-3 and h != safe:
                    break
                try:
                    R0 = np.asarray(rb.R_cmd)
                    if p0[2] < h:
                        rb.move(np.array([p0[0], p0[1], h]), R0, grip, T, "up")
                    self.go([p[0], p[1], max(h, p[2])], R, grip, phase, T=T)
                    if p[2] < h:
                        rb.move(p, R, grip, T, "descend" if phase in ("above", "look") else phase)
                        rb.flush()
                    return
                except self.T.Unreachable as e:
                    self.log("travel_unreachable", h=round(h, 3), err=repr(e)[:160])
                    rb.resync()
                    p0 = np.asarray(rb.tcp_now(), float)
        self.go(p, R, grip, phase, T=T)

    def up(self, dz=0.06, zmin=0.12, phase="retreat"):
        rb = self.rb
        try:
            rb.resync()
            p = np.asarray(rb.tcp_now(), float)
            g = float(rb.g_cmd) if rb.g_cmd is not None else self.T.OPEN_W
            rb.move([p[0], p[1], max(p[2] + dz, zmin)], np.asarray(rb.R_cmd), g, 1.0, phase)
            rb.flush()
        except Exception as e:
            self.log("up_err", err=repr(e))
            try:
                rb.resync()
            except Exception:
                pass

    def get_shift(self, o):
        try:
            ff = finger_feats(o["rgb"])
            sh = calib_shift(ff)
            self.log("fingers", f=ff, shift=sh)
            return sh
        except Exception as e:
            self.log("fingers_err", err=repr(e))
            return (0.0, 0.0)

    # ---------------------------------------------------------------- survey
    def escape_start(self):
        """The arm can start right beside the open lid's top edge (run 008: the survey move with a wrist turn
        hooked the lid and lifted the box). Look first; if tall things are near the hand, move away from them
        horizontally before going up."""
        T, rb = self.T, self.rb
        # the box from the scene camera: a hand starting beside the box (not over it) can be under the open
        # lid's top edge, out of the wrist camera's view (run 033: going up tipped the box): first away from it
        try:
            if getattr(self, "_start_done", False) or not USE_START_AWAY:
                raise RuntimeError("only at the start")
            self._start_done = True
            so = rb.observe_frame("d415")
            P, ok = T.world(so)
            best = None
            for m, s in self.ctx.segmenter.segment(so["rgb"], "white box")[:3]:
                m = np.asarray(m, bool) & ok
                pts = P[m]
                pts = pts[(pts[:, 2] > -0.01) & (pts[:, 2] < 0.08)]
                if len(pts) > 150 and (best is None or s > best[0]):
                    best = (s, np.median(pts[:, :2], 0))
            if best is not None:
                cb = best[1]
                p = np.asarray(rb.tcp_now(), float)
                d = p[:2] - cb
                dist = float(np.linalg.norm(d))
                self.log("start_box", c=_r(cb), dist=round(dist, 3))
                if 0.10 < dist < 0.26 and dist > 1e-6:
                    q = p.copy()
                    q[:2] = p[:2] + d / dist * min(0.12, 0.30 - dist)
                    try:
                        rb.move(q, np.asarray(rb.R_cmd), T.OPEN_W, 1.0, "retreat")
                        rb.flush()
                        self.log("start_away", to=_r(q))
                    except (T.Unreachable, T.RobotStop) as e:
                        self.log("start_away_err", err=repr(e)[:200])
                        rb.resync()
                    return
        except Exception as e:
            self.log("start_box_err", err=repr(e)[:200])
        o0 = rb.observe_frame("L_wrist")
        try:
            tall0 = cloud(self.ctx, o0, zmin=0.085, zmax=0.32, step=3)
        except Exception as e:
            self.log("escape_err", err=repr(e))
            return
        p = np.asarray(rb.tcp_now(), float)
        # the lid's top edge can be nearer the camera than its minimum range: bright pixels with no depth
        # (run 009: none of the lid had depth; going up lifted the box by its lid)
        try:
            import cv2
            P0, ok0 = T.world(o0)
            hsv = cv2.cvtColor(np.ascontiguousarray(o0["rgb"]), cv2.COLOR_RGB2HSV)
            nod = (~ok0) & (hsv[..., 2] > 140) & (hsv[..., 1] < 70) & ~finger_mask(o0["rgb"])
            nod = cv2.morphologyEx(nod.astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)).astype(bool)
            nn = int(nod.sum())
            if nn > 4000:
                vv, uu = np.nonzero(nod[::4, ::4])
                pts = [pix_to_plane(o0, 4 * u_, 4 * v_, p[2] - 0.03) for u_, v_ in zip(uu, vv)]
                pts = np.array([q[:2] for q in pts if q is not None])
                if len(pts):
                    extra = np.c_[pts, np.full(len(pts), p[2] - 0.03)]
                    tall0 = np.vstack([tall0, extra]) if len(tall0) else extra
            self.log("escape_nodepth", px=nn)
        except Exception as e:
            self.log("escape_err", err=repr(e))
        if len(tall0) == 0:
            return
        d = np.linalg.norm(tall0[:, :2] - p[:2], axis=1)
        near = tall0[d < 0.16]
        self.log("escape", n=int(len(near)), dmin=float(d.min()), zmax=float(tall0[:, 2].max()))
        if len(near) < 20:
            return
        away = p[:2] - near[:, :2].mean(0)
        nrm = float(np.linalg.norm(away))
        away = away / nrm if nrm > 1e-6 else np.array([-1.0, 0.0])
        step = max(0.0, 0.17 - float(d.min()))
        q = p.copy()
        q[:2] = p[:2] + away * step
        try:
            rb.move(q, np.asarray(rb.R_cmd), T.OPEN_W, 1.0, "retreat")
            rb.flush()
        except (T.Unreachable, T.RobotStop) as e:
            self.log("escape_err", err=repr(e))
            rb.resync()

    def survey(self):
        T, rb = self.T, self.rb
        self.escape_start()
        # no wrist turn (it swept the hand through the lid, run 008): straight up, then across
        R0 = np.asarray(rb.R_cmd)
        try:
            p = np.asarray(rb.tcp_now(), float)
            rb.move(np.array([p[0], p[1], SURVEY[2]]), R0, T.OPEN_W, 1.0, "look")
            rb.move(np.array(SURVEY), R0, T.OPEN_W, 1.0, "look")
            rb.flush()
        except (T.Unreachable, T.RobotStop) as e:
            self.log("survey_err", err=repr(e))
            rb.resync()
            try:
                rb.move(np.array([SURVEY[0], SURVEY[1], SURVEY[2] - 0.06]), R0, T.OPEN_W, 1.0, "look")
                rb.flush()
            except (T.Unreachable, T.RobotStop) as e2:
                self.log("survey_err2", err=repr(e2))
                rb.resync()
        o = self.rb.observe_frame("L_wrist")
        self.note_cam(o)
        self.survey_frame = o
        if DEBUG:
            self.ctx.save_image("survey", o["rgb"])
        self.shift = self.get_shift(o)
        # the fingers' image shift holds at their depth (~0.18 m), not at 0.6 m: not applied here
        self.box = find_box(self.ctx, o, (0.0, 0.0))
        if self.box is not None and self.box.get("lid_n", 0) == 0:
            # the lid not seen from above (run 011: then the wrong side was closed): the scene camera
            try:
                so = rb.observe_frame("d415")
                lp = cloud(self.ctx, so, zmin=0.09, zmax=0.30, step=2)
                okl = lid_from_points(self.box, lp)
                self.log("lid_scene", ok=okl, e2=_r(self.box["e2"], 3), theta=self.box.get("theta"))
                if okl:
                    cc = self.box["c"]
                    self.scene_tall = lp[np.hypot(lp[:, 0] - cc[0], lp[:, 1] - cc[1]) < 0.30]
            except Exception as e:
                self.log("lid_scene_err", err=repr(e))
        try:
            self.tall = cloud(self.ctx, o, zmin=0.075, zmax=0.30, step=2)
            if getattr(self, "scene_tall", None) is not None and len(self.scene_tall):
                self.tall = np.vstack([self.tall, self.scene_tall]) if len(self.tall) else self.scene_tall
            self.log("tall", n=int(len(self.tall)),
                     zmax=float(self.tall[:, 2].max()) if len(self.tall) else None)
        except Exception as e:
            self.log("tall_err", err=repr(e))
        locs = {}
        for n in self.names:
            ob = find_object(self.ctx, o, n, (0.0, 0.0), box=self.box, log="survey_obj", relaxed=True,
                             exclude=[v for v in locs.values() if v is not None])
            locs[n] = None if ob is None else np.asarray(ob["c"], float)
            if ob is not None:
                self.sizes[n] = (ob["L"], ob["W"])
                self.survey_obs[n] = ob
        missing = [n for n in self.names if locs[n] is None]
        if missing:
            # outside the survey view (run 006: the block beyond its far edge): the scene camera
            so = self.rb.observe_frame("d415")
            for n in missing:
                ob = find_object(self.ctx, so, n, (0.0, 0.0), box=self.box, log="scene_obj", side=True,
                                 exclude=[v for v in locs.values() if v is not None])
                locs[n] = None if ob is None else np.asarray(ob["c"], float)
        missing = [n for n in self.names if locs[n] is None]
        if missing and self.box is not None:
            # hidden behind / under the open lid from both cameras (run 026): look straight down beside the
            # hinge, from above the lid's top
            try:
                b = self.box
                tgt = b["c"] + (BOX_HALF + 0.09) * b["e2"]
                R0 = np.asarray(rb.R_cmd)
                co = self.cam_off(R0)
                self.travel([tgt[0] - co[0], tgt[1] - co[1], 0.40], R0, T.OPEN_W, "look", T=1.0)
                o3 = rb.observe_frame("L_wrist")
                if DEBUG:
                    self.ctx.save_image("lidside", o3["rgb"])
                for n in missing:
                    ob = find_object(self.ctx, o3, n, (0.0, 0.0), box=self.box, log="lidside_obj", relaxed=True,
                                     exclude=[v for v in locs.values() if v is not None])
                    if ob is not None:
                        locs[n] = np.asarray(ob["c"], float)
                        self.survey_obs[n] = ob
            except (T.RobotStop, T.Unreachable) as e:
                self.log("lidside_err", err=repr(e)[:200])
                rb.resync()
        return o, locs

    # ---------------------------------------------------------------- layout
    def layout(self):
        """Slots in the box frame (u along e1, v along e2 = towards the hinge): name -> (u, v, long angle or None)."""
        kinds = {n: kind_of(n) for n in self.names}
        names = list(self.names)
        slots = {}
        if "screwdriver" in kinds.values():
            sd = [n for n in names if kinds[n] == "screwdriver"][0]
            slots[sd] = (0.0, 0.0, math.pi / 4)
            rest = [n for n in names if n != sd]
            rest.sort(key=lambda n: kinds[n] != "cable")     # the cable away from the hinge (run 007)
            corners = [(0.045, -0.05), (-0.042, 0.048)]
            for n, cc in zip(rest, corners):
                la = math.pi / 4 if kinds[n] == "cable" else None
                if kinds[n] == "cable":
                    cc = (0.035, -0.035) if cc[1] < 0 else (-0.035, 0.035)
                slots[n] = (cc[0], cc[1], la)
            return slots
        cables = [n for n in names if kinds[n] == "cable"]
        rest = [n for n in names if kinds[n] != "cable"]
        if len(names) == 2:
            pos = [(0.0, -0.035 if cables else -0.045), (0.0, 0.045)]
            order = cables + rest
            for n, p in zip(order, pos):
                slots[n] = (p[0], p[1], 0.0 if kinds[n] == "cable" else None)
            return slots
        # three objects, no screwdriver
        if cables:
            slots[cables[0]] = (0.0, -0.035, 0.0)
            quads = [(-0.045, 0.045), (0.045, 0.045)]
            for n, p in zip(rest, quads):
                slots[n] = (p[0], p[1], None)
        else:
            quads = [(-0.045, -0.045), (0.045, -0.045), (0.0, 0.045)]
            for n, p in zip(rest, quads):
                slots[n] = (p[0], p[1], None)
        return slots

    def to_world(self, u, v):
        b = self.box
        return b["c"] + u * b["e1"] + v * b["e2"]

    def ang_world(self, a):
        """box-frame angle -> world yaw"""
        e1 = self.box["e1"]
        return math.atan2(e1[1], e1[0]) + a

    # ---------------------------------------------------------------- one object
    def cam_off(self, R):
        """World offset eye - TCP for hand orientation R."""
        if self.cam_h is None:
            return np.zeros(3)
        return np.asarray(R, float) @ self.cam_h

    def note_cam(self, o):
        try:
            Rh = np.asarray(self.rb.R_cmd, float)
            self.cam_h = Rh.T @ (np.asarray(o["eye"], float) - np.asarray(self.rb.tcp_now(), float))
        except Exception as e:
            self.log("cam_err", err=repr(e))

    def refine(self, name, kind, gp, ob, yaw_g, Rg, gz, hover_p=None):
        """At a hover over the grasp: re-find the object from close (corrected by the fingers' image shift)."""
        T, rb = self.T, self.rb
        top = {"roll": 0.021, "block": 0.022, "cable": 0.015}.get(kind, 0.02)
        hz = gz + top + (0.035 if kind == "roll" else 0.09)
        g = gp["g"]
        if hover_p is not None:
            # a tilted grasp: hover back along the fingers (outside an overhang), not straight above
            self.travel(hover_p, Rg, T.OPEN_W, "above", T=1.0)
        else:
            self.travel([g[0], g[1], hz], Rg, T.OPEN_W, "above", T=1.0)
        o = rb.observe_frame("L_wrist")
        sh = self.get_shift(o)
        try:
            occl = finger_mask(o["rgb"])
        except Exception:
            occl = None
        ob2 = find_object(self.ctx, o, name, sh, box=self.box, expect=ob["c"], max_dev=0.05, log="hover_obj",
                          occl=occl, partial=kind in ("screwdriver", "cable"))
        if ob2 is None:
            if DEBUG:
                self.ctx.save_image(f"hover_lost_{int(self.ctx.time_left())}", o["rgb"])
            return gp, ob, yaw_g, hz
        gp2 = grasp_plan(ob2)
        if kind == "screwdriver":
            # partly in view from close: only the correction across its axis (run 013: 1 cm off, the fingers
            # came down on the handle)
            a = np.array([math.cos(ob["yaw"]), math.sin(ob["yaw"])])
            b = np.array([-a[1], a[0]])
            # across: from the mask's points near the grasp along the axis
            xy2 = ob2["xy"]
            tt = (xy2 - np.asarray(gp["g"])) @ a
            near = np.abs(tt) < 0.02
            if near.sum() > 10:
                ds = float(np.median((xy2[near] - np.asarray(gp["g"])) @ b))
                if abs(ds) < 0.025:
                    g2 = np.asarray(gp["g"]) + ds * b
                    gp2 = dict(gp, g=g2)
                    if "c_obj" in gp:
                        gp2["c_obj"] = np.asarray(gp["c_obj"]) + ds * b
                    ob2 = dict(ob, c=np.asarray(ob["c"]) + ds * b)
                else:
                    gp2, ob2 = gp, ob
            else:
                gp2, ob2 = gp, ob
        if kind == "block" and hover_p is None:
            # 4.5 mm per side (run 035: descents ~5 mm off, a finger on the block's top): centre the block between
            # the fingers in the image itself (the camera's u axis is the closing axis), no calibration involved
            try:
                gp2 = self.servo_block(o, ob2, gp2)
            except Exception as e:
                self.log("servo_err", err=repr(e)[:200])
        if kind == "cable" and ob2["L"] < 0.9 * ob["L"]:
            # coil partly out of the close view: keep its axis and its position along it, take the cross position
            a = np.array([math.cos(ob["yaw"]), math.sin(ob["yaw"])])
            b = np.array([-a[1], a[0]])
            g2 = np.asarray(gp["g"]) + b * float((np.asarray(gp2["g"]) - np.asarray(gp["g"])) @ b)
            gp2 = dict(gp, g=g2)
            ob2 = dict(ob, c=np.asarray(ob["c"]) + (g2 - np.asarray(gp["g"])))
        if gp2.get("yaw") is not None and kind in ("block", "cable", "screwdriver"):
            sym = gp2.get("sym") or math.pi
            d = (gp2["yaw"] - yaw_g + sym / 2) % sym - sym / 2
            if abs(d) < 0.35:
                yaw_g = yaw_g + d
        self.log("refined", name=name, g=_r(gp2["g"]), dg=_r(np.asarray(gp2["g"]) - np.asarray(gp["g"])),
                 yaw=yaw_g)
        return gp2, ob2, yaw_g, hz

    def servo_block(self, o, ob2, gp2):
        """The block's centre along the closing axis from the image: its mask's mid-column vs the open fingers'
        mid-column, scaled at the block top's depth. -> gp2 with the grasp moved along the closing axis."""
        ff = finger_feats(o["rgb"])
        if not ff:
            return gp2
        m = np.asarray(ob2["mask"], bool)
        top = int(min(ff["ly"], ff["ry"])) - 6
        rows = [r for r in range(0, max(top, 0)) if m[r].any()]
        if len(rows) < 20:
            return gp2
        lr = np.array([(np.nonzero(m[r])[0][0], np.nonzero(m[r])[0][-1]) for r in rows], float)
        wd = lr[:, 1] - lr[:, 0]
        good = wd > 0.6 * wd.max()
        # rows touching the image's side borders are cut: skip them
        good &= (lr[:, 0] > 2) & (lr[:, 1] < m.shape[1] - 3)
        if good.sum() < 10:
            return gp2
        u_blk = float(np.median(0.5 * (lr[good, 0] + lr[good, 1])))
        u_fin = 0.5 * (ff["lx"] + ff["rx"])
        W_, H_, fx, fy, cx, cy = o["K"]
        depth = float(np.asarray(o["eye"])[2]) - 0.022
        off = (u_blk - u_fin) * depth / fx
        Rc = np.asarray(o["R"], float)
        uw = Rc[:2, 0] / max(1e-6, np.linalg.norm(Rc[:2, 0]))
        tcp = np.asarray(self.rb.tcp_now(), float)
        g_img = tcp[:2] + off * uw
        g_ref = np.asarray(gp2["g"], float)
        corr = float((g_img - g_ref) @ uw)
        self.log("servo_block", u_blk=round(u_blk, 1), u_fin=u_fin, off=round(off, 4), corr=round(corr, 4))
        if abs(corr) > 0.02:
            return gp2              # implausible: keep the world estimate
        return dict(gp2, g=g_ref + corr * uw)

    def pick_and_place(self, name, loc, slot, first=False):
        T, rb = self.T, self.rb
        kind = kind_of(name)
        so0 = self.survey_obs.get(name)
        if first and so0 is not None and so0["score"] >= 0.6 and not so0["info"].get("relaxed") \
                and self.survey_frame is not None and not self.near_lid(so0["c"]) \
                and np.hypot(*(np.asarray(so0["c"]) - np.asarray(loc))) < 0.01:
            # a clear survey detection: plan from the survey frame, no separate look (the hover re-look still
            # corrects the position); saves ~4 s per object
            o = self.survey_frame
            ob = dict(so0, from_survey=True)
            self.log("skip_look", name=name)
        else:
            # close look, the camera over the object
            R_look = np.asarray(rb.R_cmd)
            co = self.cam_off(R_look)
            try:
                self.travel([loc[0] - co[0], loc[1] - co[1], LOOK_Z], R_look, T.OPEN_W, "look", T=1.0)
            except (T.Unreachable, T.RobotStop) as e:
                self.log("look_err", err=repr(e))
                rb.resync()
            o = rb.observe_frame("L_wrist")
            ob = find_object(self.ctx, o, name, (0.0, 0.0), box=self.box, expect=loc, max_dev=0.08,
                             log="look_obj", relaxed=True)
        so_ = self.survey_obs.get(name)
        if ob is None and so_ is not None and np.hypot(*(np.asarray(so_["c"]) - np.asarray(loc))) < 0.03 \
                and self.near_lid(loc):
            # hidden under the open lid from straight above (run 015), seen in the survey at an angle: use the
            # survey's detection; a tilted grasp from outside reaches under the lid, its hover view looks in
            ob = dict(so_, from_survey=True)
            self.log("use_survey", name=name, c=_r(ob["c"]))
        if ob is None:
            if DEBUG:
                self.ctx.save_image(f"lost_{name.replace(' ', '_')}", o["rgb"])
            if USE_PROP and not self.propped and self.near_lid(loc):
                # hidden under the lid's overhang (run 007)
                return "lid_in_way", None
            return "lost", None
        gp = grasp_plan(ob)
        self.log("grasp_plan", name=name, kind=kind, g=_r(gp["g"]), yaw=gp["yaw"], L=ob["L"], W=ob["W"],
                 extra={k: v for k, v in gp.items() if k in ("tg", "hw", "sg")})
        # slot: world position and wanted hand yaw at the place
        u, v, la = slot
        sxy = self.to_world(u, v)
        # candidate grasp yaws, scored by a hand model against the depth points (the box's walls, the lid, other
        # objects; runs 004-005: fingers came down on the box's wall, the hand hit the open lid)
        try:
            if ob.get("from_survey"):
                # the mask is another frame's: drop the object's own points by its footprint
                cl_ = cloud(self.ctx, o, zmin=0.008)
                fxy = np.asarray(ob["xy"])[::max(1, len(ob["xy"]) // 300)]
                if len(cl_) and len(fxy):
                    dmin = np.min(np.linalg.norm(cl_[:, None, :2] - fxy[None, :, :], axis=2), axis=1)
                    cl_ = cl_[(dmin > 0.012) | (cl_[:, 2] > 0.075)]
            else:
                cl_ = cloud(self.ctx, o, exclude_mask=ob["mask"], zmin=0.008)
            parts = [cl_, box_wall_pts(self.box)]
            if self.tall is not None and len(self.tall):
                parts.append(self.tall)
            pts = np.vstack([p for p in parts if len(p)])
        except Exception as e:
            self.log("obst_err", err=repr(e))
            pts = None
        e1a = self.ang_world(0.0)
        y0 = yaw_of(rb.R_cmd)
        if kind == "roll":
            yaws = [e1a + k * math.pi / 12 for k in range(24)]
        elif kind == "block":
            yaws = [gp["yaw"] + k * math.pi / 2 for k in range(4)]
        else:
            yaws = [gp["yaw"], gp["yaw"] + math.pi]
        gz = gp["fz"] + T.FINGERTIP
        g0 = np.asarray(gp["g"], float)
        # grasp variants: (plan, its yaws)
        variants = [(gp, yaws)]
        if kind == "screwdriver" and gp.get("long") is not None:
            # anywhere along the handle
            a_ = np.array([math.cos(gp["long"]), math.sin(gp["long"])])
            variants = [(dict(gp, g=g0 + dt * a_), yaws) for dt in (0.0, -0.015, 0.015, -0.03, 0.03)]
        if kind == "cable" and ob["L"] >= 0.12:
            # one strand at either end of the coil (run 012: the coil beside the box wall, a squeezing finger
            # came down on the wall every time); only with the whole coil in its mask (run 013: half masks put
            # the 'ends' off the strand, 6 misses)
            for sp in cable_strand_plans(ob):
                variants.append((sp, [sp["yaw"], sp["yaw"] + math.pi]))
        scored = []
        tilts = [(0.0, 1)] + [(bt, s_) for bt in (math.radians(20), math.radians(32)) for s_ in (1, -1)]
        for gi, (gpv, yws) in enumerate(variants):
            gc = np.asarray(gpv["g"], float)
            half_open = gpv["open"] if kind in ("screwdriver", "cable") else 0.04
            for y in yws:
                R0y = T.down(y)
                ac = np.array([-math.sin(y), math.cos(y)])
                for ti, (bt, sg_) in enumerate(tilts):
                    Ry = tilt_R(R0y, bt, sg_) if bt > 0 else R0y
                    up_ = -Ry[:, 2]
                    tl = math.asin(float(np.clip(up_[:2] @ ac, -1, 1)))
                    hc, per = hand_cost(pts, gc, y, gpv["fz"], half_open, self.cam_off(Ry), tilt=tl)
                    turn = abs(wrap(y - y0))
                    pref = 0.0
                    if kind == "roll" and slot[1] > 0.02:
                        pref = 0.6 * abs(math.sin(y - e1a))     # near the hinge: closing along e1 at the place
                    tcp_c = np.array([gc[0], gc[1], gpv["fz"]]) - Ry[:, 2] * T.FINGERTIP
                    ev = self.ik_eval(tcp_c, Ry)
                    ikp = 0.0 if ev is None else (0.0 if ev[0] >= 0.25 else (5.0 if ev[0] > 0 else 1000.0))
                    if ikp < 1000.0:
                        # the approach pose too (run 017: hovers out of reach far from the robot)
                        hp = tcp_c - Ry[:, 2] * (0.14 if bt > 0 else 0.10)
                        ev2 = self.ik_eval(hp, Ry)
                        if ev2 is not None and ev2[0] <= 0:
                            ikp = 1000.0
                    sc = min(hc, 200.0) + 2.0 * turn + 2.0 * pref + ikp + 3.0 * gi + (6.0 * bt if bt else 0.0)
                    if gpv.get("mode") == "strand":
                        sc += 15.0
                    # a grasp like one that already failed (run 014: the same blocked grasp 4 times)
                    for (fn, fm, fy, ft) in self.failed:
                        if fn == name and fm == gpv.get("mode", "main") and abs(ft - tl) < 0.15 and \
                                abs(((y - fy) + math.pi / 2) % math.pi - math.pi / 2) < 0.3:
                            sc += 60.0
                    scored.append((sc, y, per, gi, ti, tl))
        scored.sort(key=lambda s: s[0])
        yaw_g = scored[0][1]
        bt, sg_ = tilts[scored[0][4]]
        gp = variants[scored[0][3]][0]
        gz = gp["fz"] + T.FINGERTIP
        gx, gy = gp["g"]
        strand = gp.get("mode") == "strand"
        self.log("yaw_choice", top=[(round(s[0], 2), round(s[1], 3), s[2], s[3], s[4]) for s in scored[:4]])
        # under the open lid's overhang: prop it first (run 008: the cable hooked under it, the box toppled)
        if kind == "cable" and scored[0][0] >= 40 and self.drags.get(name, 0) < 2 and pts is not None:
            # every grasp has a finger on something (run 028: the coil against the box wall, 4 blocked grasps):
            # closed fingers into the coil's empty middle, drag it away from what is around it
            self.drags[name] = self.drags.get(name, 0) + 1
            nl = self.drag_coil(ob, pts)
            if nl is not None:
                return "dragged", dict(ob, c=nl)
        if USE_PROP and kind == "cable" and not self.propped and self.under_lid(ob.get("xy")):
            # only the coil: it hooks under the lid (run 008); for the rest the prop did more harm (run 013)
            return "lid_in_way", ob
        self.last_try = (name, gp.get("mode", "main"), yaw_g, scored[0][5])

        def tiltR(y):
            return tilt_R(T.down(y), bt, sg_) if bt > 0 else T.down(y)

        Rg = tiltR(yaw_g)
        refine_it = kind in ("roll", "block", "screwdriver") or (kind == "cable" and not strand)
        tcp_g = np.array([gx, gy, gp["fz"]]) - Rg[:, 2] * T.FINGERTIP
        far = tcp_g - Rg[:, 2] * 0.14 if bt > 0 else None
        if refine_it:
            gp, ob, yaw_g, hover = self.refine(name, kind, gp, ob, yaw_g, Rg, gz, hover_p=far)
            Rg = tiltR(yaw_g)
            gx, gy = gp["g"]
        tcp_g = np.array([gx, gy, gp["fz"]]) - Rg[:, 2] * T.FINGERTIP
        hov = tcp_g - Rg[:, 2] * 0.05
        if not refine_it:
            self.travel(far if far is not None else hov, Rg, T.OPEN_W, "above", T=1.0)
        try:
            rb.move(hov, Rg, gp["open"], 0.5, "above")
            rb.move(tcp_g, Rg, gp["open"], 1.0, "descend")
            rb.flush()
        except T.RobotStop as e:
            self.log("descend_stop", err=repr(e))
            self.up()
            return "blocked", ob
        tcp = np.asarray(rb.tcp_now())
        self.log("at_grasp", tcp=_r(tcp), want=_r(tcp_g), tilt=round(bt * sg_, 3))
        if tcp[2] > tcp_g[2] + 0.008:
            self.up()
            return "blocked", ob
        rb.move(tcp_g, Rg, 0.0, 0.6, "close the gripper")
        rb.flush()
        fw = float(rb.finger_width())
        self.log("closed", fw=fw)
        if fw < 0.003:
            rb.move(tcp_g, Rg, T.OPEN_W, 0.4, "open")
            self.up()
            return "missed", ob
        if bt > 0:
            # back out along the fingers first
            # back out along the fingers first (out from under an overhang)
            rb.move(tcp_g - Rg[:, 2] * 0.12, Rg, 0.0, 1.0, "lift")
            q_ = tcp_g - Rg[:, 2] * 0.12
            rb.move(np.array([q_[0], q_[1], LIFT_Z]), Rg, 0.0, 1.0, "lift")
        else:
            rb.move(np.array([gx, gy, LIFT_Z]), Rg, 0.0, 1.0, "lift")
        rb.flush()
        fw2 = float(rb.finger_width())
        self.log("lifted", fw=fw2)
        if fw2 < 0.003:
            return "dropped", ob
        if kind == "cable" and not strand:
            return self.lay_cable(name, ob, slot, yaw_g, fw)
        # place: hand yaw at the slot
        off_w = np.asarray(gp.get("c_obj", ob["c"]), float) - np.array([gx, gy])
        if la is not None and gp.get("long") is not None:
            want_long = self.ang_world(la)
            # object long axis rotates with the hand
            d = wrap(want_long - gp["long"])
            cand = [yaw_g + d, yaw_g + d + math.pi]
            # (the long axis is symmetric: d and d + pi give the same placement for the object's extent)
        elif kind == "block":
            cand = [e1a + k * math.pi / 2 for k in range(4)]
        elif kind == "roll":
            cand = [yaw_g + k * math.pi / 12 for k in range(24)]
        else:
            cand = [yaw_g, yaw_g + math.pi]
        # each place yaw scored by the hand model against the lid (+ the walls), the wrist turn and IK
        # (run 007: the hand hit the propped lid at a slot near the hinge)
        try:
            wall = box_wall_pts(self.box)
            ppts = np.vstack([wall] + ([self.tall] if self.tall is not None and len(self.tall) else []))
        except Exception:
            ppts = None
        half = max(fw / 2.0, 0.005) + 0.006 + 0.012
        opts = []
        for yc in cand:
            dth = wrap(yc - yaw_g)
            cth, sth = math.cos(dth), math.sin(dth)
            off_p = np.array([cth * off_w[0] - sth * off_w[1], sth * off_w[0] + cth * off_w[1]])
            txy_c = sxy - off_p
            # release height: low inside the walls unless a finger would overlap a wall
            cl = np.array([math.cos(yc), math.sin(yc)])
            ac = np.array([-cl[1], cl[0]])
            hit = False
            for sgn in (1, -1):
                for sa in (1, -1):
                    pt = txy_c + sgn * half * cl + sa * 0.0115 * ac
                    dd = pt - self.box["c"]
                    if abs(dd @ self.box["e1"]) > INNER or abs(dd @ self.box["e2"]) > INNER:
                        hit = True
            fzc = BOX_H + 0.012 if hit else gp["fz"] + 0.006 + 0.012
            if strand:
                fzc = max(fzc, 0.05)       # the coil may hang ~3 cm below the strand
            if kind == "screwdriver":
                # its shaft can catch the wall going down and shove the box (run 021): drop from above the walls
                fzc = max(fzc, BOX_H + 0.015)
            # also a higher release (run 037: the only reachable low release had the hand in the lid)
            for fzv, pen in ((fzc, 0.0), (max(fzc, BOX_H + 0.012) + 0.05, 4.0)):
                hc, per = hand_cost(ppts, txy_c, yc, fzv, half - 0.012, self.cam_off(T.down(yc)))
                ev = self.ik_eval([txy_c[0], txy_c[1], fzv + T.FINGERTIP], T.down(yc))
                ikp = 0.0 if ev is None else (0.0 if ev[0] >= 0.25 else (5.0 if ev[0] > 0 else 1000.0))
                sc = min(hc, 200.0) + 2.0 * abs(dth) + ikp + pen
                opts.append((sc, yc, txy_c, fzv, hit, per, dth))
        opts.sort(key=lambda o_: o_[0])
        if opts and opts[0][0] < 50:
            opts = [o_ for o_ in opts if o_[0] < 50]
        # the best place orientations in turn (run 023: the carry pose out of reach, the roll then dropped
        # wherever the next look opened the gripper)
        for oi, best in enumerate(opts[:3]):
            _, yaw_p, txy, fz_rel, hit, per, dth = best
            Rp = T.down(yaw_p)
            self.log("place_plan", slot=_r(sxy), tcp=_r(txy), yaw=yaw_p, dth=dth, hit=hit, fz=fz_rel,
                     cost=best[0], per=per, opt=oi)
            try:
                self.travel([txy[0], txy[1], CARRY_Z], Rp, 0.0, "carry over the box", T=1.0, extra=0.03)
            except T.Unreachable as e:
                self.log("carry_unreachable", err=repr(e)[:200])
                rb.resync()
                continue
            try:
                rb.move(np.array([txy[0], txy[1], fz_rel + T.FINGERTIP]), Rp, 0.0, 1.0, "lower into the box")
                rb.flush()
            except T.RobotStop as e:
                self.log("lower_stop", err=repr(e))
                rb.resync()
            except T.Unreachable as e:
                self.log("lower_unreachable", err=repr(e)[:200])
                rb.resync()
            break
        fw3 = float(rb.finger_width())
        self.release(min(T.OPEN_W, fw3 / 2 + 0.012))
        self.log("placed", name=name, fw=fw3, held=fw3 > 0.003)
        return ("ok" if fw3 > 0.003 else "dropped"), ob

    def release(self, g_open):
        """Open where the hand is and go up; after a refused move (the arm lagging on a contact, runs 014/018:
        'release, up: refused ... starts 0.09 rad from the arm') resync and retry."""
        T, rb = self.T, self.rb
        for i in range(3):
            try:
                rb.resync()
                p = np.asarray(rb.tcp_now(), float)
                R = np.asarray(rb.R_cmd)
                rb.move(p, R, g_open, 0.5, "release")
                rb.move(np.array([p[0], p[1], max(CARRY_Z, p[2] + 0.03)]), R, g_open, 1.0, "up")
                rb.flush()
                return True
            except (T.RobotStop, T.Unreachable) as e:
                self.log("release_err", i=i, err=repr(e)[:200])
        return False

    def drag_coil(self, ob, pts):
        """Closed fingers down into the coil's middle (bare table), slide it 6 cm away from the nearby obstacles.
        -> the coil's new centre estimate, or None."""
        T, rb = self.T, self.rb
        c = np.asarray(ob["c"], float)          # the coil's middle (bare table inside the ring)
        near =pts[(np.linalg.norm(pts[:, :2] - c, axis=1) < 0.13) & (pts[:, 2] > 0.012)]
        walls = box_wall_pts(self.box)
        walls = walls[np.linalg.norm(walls[:, :2] - c, axis=1) < 0.13]
        allp = np.vstack([p for p in (near, walls) if len(p)]) if (len(near) or len(walls)) else np.zeros((0, 3))
        if len(allp) == 0:
            return None
        d = c - allp[:, :2].mean(0)
        nd = float(np.linalg.norm(d))
        if nd < 1e-6:
            return None
        d = d / nd
        yaw0 = math.atan2(d[1], d[0]) + math.pi / 2     # fingers side by side across the drag
        fz = 0.008
        tgt = c + 0.06 * d
        # away from the joint limits at both ends (run 038: a drag pushed joint 7 onto its limit, every later move
        # was refused)
        R, best = None, -1.0
        for y in (yaw0, yaw0 + math.pi):
            ms = [self.ik_eval(np.array([p[0], p[1], fz + T.FINGERTIP]), T.down(y)) for p in (c, tgt)]
            if any(m_ is None for m_ in ms):
                continue
            mm = min(m_[0] for m_ in ms)
            if mm > best:
                best, R = mm, T.down(y)
        if R is None or best < 0.3:
            self.log("drag_coil_ik", margin=best)
            return None
        self.log("drag_coil", c=_r(c), to=_r(tgt))
        try:
            self.travel([c[0], c[1], 0.12], R, 0.0, "above", T=1.0)
            rb.move(np.array([c[0], c[1], fz + T.FINGERTIP]), R, 0.0, 1.0, "descend")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("drag_err", err=repr(e)[:200])
            rb.resync()
            self.up()
            return None
        tcp = np.asarray(rb.tcp_now(), float)
        if tcp[2] > fz + T.FINGERTIP + 0.012:
            self.log("drag_blocked", tcp=_r(tcp))
            self.up()
            return None
        try:
            rb.move(np.array([tgt[0], tgt[1], fz + T.FINGERTIP]), R, 0.0, 1.2, "drag the coil")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("drag_err", err=repr(e)[:200])
            rb.resync()
        self.up()
        return tgt

    def needs_clearing(self, locs):
        """Objects lying just outside the hinge (under / beside the open lid: runs 026-030, hidden, the hand hits
        the lid, the box topples), or missing (maybe hidden under it)."""
        b = self.box
        out = []
        self.clear_dv = 0.12
        for n, l in locs.items():
            if l is None:
                out.append(n)
                self.clear_dv = min(self.clear_dv, 0.04)
                continue
            d = np.asarray(l, float) - b["c"]
            u, v = float(d @ b["e1"]), float(d @ b["e2"])
            if abs(u) < BOX_HALF + 0.05 and BOX_HALF - 0.01 < v < BOX_HALF + 0.11:
                out.append(n)
                self.clear_dv = min(self.clear_dv, v - BOX_HALF)
        return out

    def drag_box(self, dist=0.10):
        """Slide the (empty) box away from its hinge side: closed fingers inside it near the opposite wall, pulled
        that way. The open lid goes with it. -> True if done."""
        T, rb = self.T, self.rb
        b = self.box
        e1, e2, c = b["e1"], b["e2"], b["c"]
        p_in = c - (BOX_HALF - 0.03) * e2
        p_end = p_in - dist * e2
        fz = 0.035
        y1 = math.atan2(e1[1], e1[0])
        R = None
        for y in (y1, y1 + math.pi):
            Ry = T.down(y)
            ms = [self.ik_eval(np.array([p[0], p[1], fz + T.FINGERTIP]), Ry) for p in (p_in, p_end)]
            if all(m_ is not None and m_[0] > 0.3 for m_ in ms):
                R = Ry
                break
        self.log("drag_box", c=_r(c), to=_r(p_end), ok=R is not None)
        if R is None:
            return False
        try:
            self.travel([p_in[0], p_in[1], 0.16], R, 0.0, "above", T=1.0)
            rb.move(np.array([p_in[0], p_in[1], fz + T.FINGERTIP]), R, 0.0, 1.0, "descend")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("drag_box_err", err=repr(e)[:200])
            rb.resync()
            self.up(dz=0.1, zmin=0.2, phase="up")
            return False
        tcp = np.asarray(rb.tcp_now(), float)
        if tcp[2] > fz + T.FINGERTIP + 0.012:
            self.log("drag_box_blocked", tcp=_r(tcp))
            self.up(dz=0.1, zmin=0.2, phase="up")
            return False
        try:
            rb.move(np.array([p_end[0], p_end[1], fz + T.FINGERTIP]), R, 0.0, 2.5, "drag the box")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("drag_box_err", err=repr(e)[:200])
            rb.resync()
        self.up(dz=0.12, zmin=0.2, phase="up")
        return True

    def lay_cable(self, name, ob, slot, yaw_g, fw):
        """The coil, squeezed across its middle, hangs upright from the fingers (runs 004-006). Stand it on the
        box floor at one end of its slot, then tilt it down along the slot (the hand on an arc around its bottom
        point), so it lies flat inside the walls."""
        T, rb = self.T, self.rb
        # the coil is always the same object, ~15 cm long (survey run 001: 0.155); its masks are often partial
        # (run 009: 0.10 from a partial mask, the hand stopped too high and the coil stayed upright)
        L = 0.15
        u, v, la = slot
        sxy = self.to_world(u, v)
        la_w = self.ang_world(la if la is not None else 0.0)
        floor = 0.006
        ppts = np.vstack([box_wall_pts(self.box)] +
                         ([self.tall] if self.tall is not None and len(self.tall) else []))

        def plan(yaw_p):
            cl = np.array([math.cos(yaw_p), math.sin(yaw_p)])
            d = np.array([-cl[1], cl[0]])           # the tilt direction (across the closing axis)
            B = sxy - 0.9 * (L / 2) * d
            way = []
            for ph in (0, 25, 45, 62, 72):
                p_ = math.radians(ph)
                xy = B + (L / 2) * math.sin(p_) * d
                way.append(np.array([xy[0], xy[1], floor + (L / 2) * math.cos(p_) + 0.012 + T.FINGERTIP]))
            cost = 0.0
            for w in (way[0], way[-1]):
                cost += hand_cost(ppts, w[:2], yaw_p, w[2] - T.FINGERTIP, fw / 2 + 0.006,
                                  self.cam_off(T.down(yaw_p)))[0]
            ev = self.ik_eval(way[-1], T.down(yaw_p))
            cost += 0.0 if ev is None else (0.0 if ev[0] >= 0.25 else (5.0 if ev[0] > 0 else 1000.0))
            return cost + 2.0 * abs(wrap(yaw_p - yaw_g)), way, B

        opts = [(plan(y), y) for y in (la_w + math.pi / 2, la_w - math.pi / 2)]
        opts.sort(key=lambda o_: o_[0][0])
        (cost, way, B), yaw_p = opts[0]
        Rp = T.down(yaw_p)
        self.log("lay_plan", L=L, slot=_r(sxy), yaw=yaw_p, B=_r(B), cost=cost)
        try:
            self.travel([way[0][0], way[0][1], max(CARRY_Z + 0.03, way[0][2] + 0.06)], Rp, 0.0, "carry over the box",
                        T=1.0, extra=0.06)
            rb.move(way[0], Rp, 0.0, 1.2, "lower into the box")
            for w in way[1:]:
                rb.move(w, Rp, 0.0, 0.6, "lay it down")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("lay_err", err=repr(e))
            rb.resync()
        fw3 = float(rb.finger_width())
        self.release(min(T.OPEN_W, fw3 / 2 + 0.006))
        self.log("placed", name=name, fw=fw3, held=fw3 > 0.003)
        return ("ok" if fw3 > 0.003 else "dropped"), ob

    def recheck_box(self):
        """Before closing: the box and its lid again from the survey pose (run 019: the box had been shoved and
        the lid knocked open, the closing used the old hinge and angle)."""
        rb, T = self.rb, self.T
        if self.ctx.time_left() < 30:
            return
        try:
            self.travel(SURVEY, np.asarray(rb.R_cmd), T.OPEN_W, "look", T=1.0)
            o = rb.observe_frame("L_wrist")
            nb = find_box(self.ctx, o, (0.0, 0.0), log="box2")
        except Exception as e:
            self.log("recheck_err", err=repr(e)[:200])
            try:
                rb.resync()
            except Exception:
                pass
            return
        if nb is None or np.hypot(*(nb["c"] - self.box["c"])) > 0.15:
            self.log("recheck_nobox", found=nb is not None)
            return
        old = self.box
        moved = float(np.hypot(*(nb["c"] - old["c"])))
        # the hinge side: the new box's side nearest the old one (a lid flung flat has few tall points and
        # find_box may pick another side)
        ang = nb.get("ang", math.atan2(nb["e1"][1], nb["e1"][0]))
        sides = [ang + k * math.pi / 2 for k in range(4)]
        k = int(np.argmax([math.cos(s) * old["e2"][0] + math.sin(s) * old["e2"][1] for s in sides]))
        e2 = np.array([math.cos(sides[k]), math.sin(sides[k])])
        c = nb["c"] if moved >= 0.012 else old["c"]
        if moved < 0.012:
            e2 = old["e2"]
        box = dict(old, c=c, e2=e2, e1=np.array([-e2[1], e2[0]]))
        try:
            P, ok = self.T.world(o)
            th, n = lid_angle_strip(box, P, ok)
        except Exception as e:
            th, n = None, 0
            self.log("lid_angle_err", err=repr(e)[:200])
        if th is not None:
            box["theta"] = th
        self.box = box
        self.log("box_rechecked", c=_r(box["c"]), e2=_r(box["e2"], 3), theta=box.get("theta"), n_strip=n,
                 theta_find=nb.get("theta"), moved=round(moved, 4))
        try:
            self.flatten(o)
        except (T.RobotStop, T.Unreachable) as e:
            self.log("flatten_err", err=repr(e)[:200])
            rb.resync()
            self.up(dz=0.08, zmin=0.2, phase="up")

    def flatten(self, o):
        """Something in the box standing above its walls (run 027: the coil left upright, the lid rested on it at
        8.6 deg): sweep the closed fingers through it just below the walls' top, across its long axis."""
        T, rb = self.T, self.rb
        b = self.box
        P, ok = T.world(o)
        pts = P[ok]
        d = pts[:, :2] - b["c"]
        u, v = d @ b["e1"], d @ b["e2"]
        # well above the walls only (run 029: depth noise 1 cm above them triggered it; a coil standing up is
        # 6+ cm above them)
        m = (np.abs(u) < BOX_HALF - 0.02) & (np.abs(v) < BOX_HALF - 0.02) & (pts[:, 2] > BOX_H + 0.03) & \
            (pts[:, 2] < 0.25)
        n = int(m.sum())
        self.log("inbox_tall", n=n, zmax=float(pts[m, 2].max()) if n else None)
        if n < 80 or self.ctx.time_left() < 32:
            return
        xy = pts[m, :2]
        cxy = np.median(xy, 0)
        if len(xy) > 5:
            C = np.cov((xy - xy.mean(0)).T)
            w, V = np.linalg.eigh(C)
            ax = V[:, 1]
        else:
            ax = b["e1"]
        nrm = np.array([-ax[1], ax[0]])
        # stay inside the walls
        lim = BOX_HALF - 0.03

        def clamp(p):
            dd = p - b["c"]
            uu = float(np.clip(dd @ b["e1"], -lim, lim))
            vv = float(np.clip(dd @ b["e2"], -lim, lim))
            return b["c"] + uu * b["e1"] + vv * b["e2"]

        p0 = clamp(cxy - 0.07 * nrm)
        p1 = clamp(cxy + 0.07 * nrm)
        yaw = math.atan2(ax[1], ax[0])            # closing axis along the thing: the fingers side by side
        fz = BOX_H - 0.012
        R = None
        for y in (yaw, yaw + math.pi):
            ms = [self.ik_eval(np.array([p[0], p[1], fz + T.FINGERTIP]), T.down(y)) for p in (p0, p1)]
            if all(m_ is not None and m_[0] > 0.3 for m_ in ms):
                R = T.down(y)
                break
        if R is None:
            self.log("flatten_unreachable")
            return
        self.log("flatten", c=_r(cxy), p0=_r(p0), p1=_r(p1))
        self.travel([p0[0], p0[1], 0.17], R, 0.0, "above", T=1.0)
        rb.move(np.array([p0[0], p0[1], fz + T.FINGERTIP]), R, 0.0, 1.0, "descend")
        rb.move(np.array([p1[0], p1[1], fz + T.FINGERTIP]), R, 0.0, 1.2, "push it over")
        rb.flush()
        self.up(dz=0.1, zmin=0.2, phase="up")

    # ---------------------------------------------------------------- lid
    def under_lid(self, xy):
        """Is any of these points (the object's footprint) under the open lid's overhang?"""
        if xy is None or len(xy) == 0 or self.box is None:
            return False
        b = self.box
        th = b.get("theta") or 112.0
        lid_out = 0.19 * max(0.0, math.cos(math.radians(180 - th)))
        d = np.asarray(xy, float) - b["c"]
        u, v = d @ b["e1"], d @ b["e2"]
        m = (np.abs(u) < BOX_HALF + 0.01) & (v > BOX_HALF - 0.005) & (v < BOX_HALF + lid_out + 0.025)
        return int(m.sum()) >= 5

    def near_lid(self, xy):
        b = self.box
        d = np.asarray(xy, float) - b["c"]
        u, v = float(d @ b["e1"]), float(d @ b["e2"])
        return abs(u) < BOX_HALF + 0.08 and BOX_HALF - 0.01 < v < BOX_HALF + 0.16

    def lid_frame(self):
        T = self.T
        b = self.box
        e1, e2, c = b["e1"], b["e2"], b["c"]
        hinge = c + BOX_HALF * e2
        yaw = math.atan2(e1[1], e1[0])
        ys = [yaw, yaw + math.pi]
        offs = [float(self.cam_off(T.down(y))[:2] @ e2) for y in ys]
        yaw = ys[int(np.argmin(offs))]
        return hinge, T.down(yaw), yaw, offs

    def prop_lid(self, th_t=102.0, dzp=0.12):
        """Push the open lid from outside to near vertical (still open), so things under its overhang can be
        reached."""
        T, rb = self.T, self.rb
        b = self.box
        e1, e2, c = b["e1"], b["e2"], b["c"]
        hinge, R, yaw, _ = self.lid_frame()
        th = b.get("theta") or 112.0
        hz = BOX_H

        def P(off, dz):
            xy = hinge + off * e2
            return np.array([xy[0], xy[1], hz + dz + T.FINGERTIP])

        lid_out = 0.19 * max(0.0, math.cos(math.radians(180 - th)))
        s0 = max(0.14, lid_out + 0.07)
        off_t = dzp * math.tan(math.radians(th_t - 90.0)) + 0.0125
        self.log("prop_plan", theta=th, s0=s0, off_t=off_t)
        top = hz + 0.21 + T.FINGERTIP
        try:
            p = np.asarray(rb.tcp_now(), float)
            if p[2] < top:
                rb.move(np.array([p[0], p[1], top]), np.asarray(rb.R_cmd), 0.0, 1.0, "up")
            q = P(s0, 0.0)
            rb.move(np.array([q[0], q[1], top]), R, 0.0, 1.0, "transit")
            rb.move(P(s0, dzp), R, 0.0, 1.0, "descend")
            rb.move(P(off_t, dzp), R, 0.0, 1.5, "push the lid up")
            rb.move(P(s0, dzp), R, 0.0, 1.0, "back off")
            rb.move(np.array([q[0], q[1], top]), R, 0.0, 1.0, "up")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("prop_err", err=repr(e))
            rb.resync()
            self.up(dz=0.1, zmin=0.25, phase="up")
        self.propped = True
        b["theta"] = th_t
        # the tall points: the lid now at th_t (synthetic), the rest as surveyed
        keep = []
        if self.tall is not None and len(self.tall):
            d = self.tall[:, :2] - hinge
            near = (np.abs(d @ e1) < BOX_HALF + 0.03) & (d @ e2 > -0.03) & (d @ e2 < 0.22)
            keep = [self.tall[~near]]
        th_r = math.radians(th_t)
        ldir2, ldirz = -math.cos(th_r), math.sin(th_r)
        lid = []
        for s in np.linspace(0.02, 0.19, 18):
            for u in np.linspace(-BOX_HALF, BOX_HALF, 20):
                xy = hinge + u * e1 + s * ldir2 * e2
                lid.append([xy[0], xy[1], hz + s * ldirz])
        self.tall = np.vstack(keep + [np.array(lid)])

    def close_lid(self):
        T, rb = self.T, self.rb
        b = self.box
        e1, e2, c = b["e1"], b["e2"], b["c"]
        hinge = c + BOX_HALF * e2
        th = b.get("theta") or 112.0
        yaw = math.atan2(e1[1], e1[0])
        # the wrist camera on the box side, not the lid side (run 003: it hit the lid and pushed it open)
        ys = [yaw, yaw + math.pi]
        offs = [float(self.cam_off(T.down(y))[:2] @ e2) for y in ys]
        hz = BOX_H

        def P(off, dz):
            xy = hinge + off * e2
            return np.array([xy[0], xy[1], hz + dz + T.FINGERTIP])

        lid_out = 0.19 * max(0.0, math.cos(math.radians(180 - th)))   # top edge's outward offset
        s0 = max(0.14, lid_out + 0.07)
        rho = 0.105
        pts = [P(0.0, 0.10)]
        for a in (80, 65, 50, 35, 22, 12, 5):
            ar = math.radians(a)
            pts.append(P(-rho * math.cos(ar), rho * math.sin(ar)))
        # both hand orientations, the camera on the box side first; every waypoint must have an IK solution
        # (run 008: a box near the robot, the push out of reach)
        order = [ys[int(np.argmin(offs))], ys[int(np.argmax(offs))]]
        yaw = order[0]
        reach = []
        for y in order:
            Ry = T.down(y)
            ms = [self.ik_eval(q, Ry) for q in [P(s0, 0.07)] + pts]
            mm = min((m[0] for m in ms if m is not None), default=1.0)
            reach.append(round(mm, 3))
            if mm > 0:
                yaw = y
                break
        R = T.down(yaw)
        self.log("lid_plan", theta=th, s0=s0, hinge=_r(hinge), yaw=yaw, cam_e2=offs, reach=reach)
        if th >= 132 and self.lift_lid(hinge, th):
            self.press_lid(c, e2, hz, yaw)
            return
        if self.straddle_lid(hinge, th):
            self.press_lid(c, e2, hz, yaw)
            return
        try:
            # straight lines over the lid's top (a joint move swept through the lid and opened it, run 004)
            p = np.asarray(rb.tcp_now(), float)
            top = hz + 0.21 + T.FINGERTIP
            if p[2] < top:
                rb.move(np.array([p[0], p[1], top]), np.asarray(rb.R_cmd), 0.0, 1.0, "up")
            q = P(s0, 0.0)
            rb.move(np.array([q[0], q[1], top]), R, 0.0, 1.0, "transit")
            rb.move(P(s0, 0.07), R, 0.0, 1.0, "descend")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("lid_approach_err", err=repr(e))
            rb.resync()
        try:
            rb.move(pts[0], R, 0.0, 1.5, "push the lid")
            for q in pts[1:]:
                rb.move(q, R, 0.0, 0.5, "push the lid")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("lid_push_err", err=repr(e))
            rb.resync()
        self.log("lid_pushed", tcp=_r(rb.tcp_now()))
        self.press_lid(c, e2, hz, yaw)

    def straddle_lid(self, hinge, th):
        """Close the lid from above: the open fingers straddle its top edge (closing across the lid), then the
        fingertips follow an arc around the hinge past vertical, the outer finger pushing; the lid falls shut.
        Needs far less reach than pushing from outside (run 011: a hinge on the robot's side, out of reach).
        -> True when done."""
        T, rb = self.T, self.rb
        b = self.box
        e2 = b["e2"]
        hz = BOX_H
        rf = 0.16

        def A(al):
            ar = math.radians(al)
            xy = hinge + rf * (-math.cos(ar)) * e2
            return np.array([xy[0], xy[1], hz + rf * math.sin(ar) + T.FINGERTIP])

        th = float(np.clip(th, 95.0, 145.0))
        arc = [A(a) for a in (th - 8, 97, 88, 78, 68, 55, 40, 25, 15, 8)]
        # then slide off past the lid's edge before going up (run 012: the edge stayed between the fingers and
        # was lifted back to ~35 deg)
        q_ = hinge - 0.25 * e2
        arc.append(np.array([q_[0], q_[1], arc[-1][2]]))
        start = A(th)
        y2 = math.atan2(e2[1], e2[0])
        best = None
        for y in (y2, y2 + math.pi):
            Ry = T.down(y)
            ms = [self.ik_eval(q, Ry) for q in [start + np.array([0, 0, 0.08]), start] + arc]
            if any(m is None for m in ms):
                return False
            mm = min(m[0] for m in ms)
            tt = sum(m[1] for m in ms[:1])
            if mm > 0 and (best is None or tt < best[0]):
                best = (tt, y)
        self.log("straddle_plan", theta=th, start=_r(start), ok=best is not None)
        if best is None:
            return False
        R = T.down(best[1])
        try:
            p = np.asarray(rb.tcp_now(), float)
            top = max(start[2] + 0.08, hz + 0.21 + T.FINGERTIP)
            if p[2] < top:
                rb.move(np.array([p[0], p[1], top]), np.asarray(rb.R_cmd), T.OPEN_W, 1.0, "up")
            rb.move(np.array([start[0], start[1], top]), R, T.OPEN_W, 1.0, "transit")
            rb.move(start, R, T.OPEN_W, 1.0, "descend")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("straddle_err", err=repr(e)[:200])
            rb.resync()
            self.up(dz=0.08, zmin=0.25, phase="up")
            return False
        tcp = np.asarray(rb.tcp_now(), float)
        self.log("straddle_at", tcp=_r(tcp), want=_r(start))
        if tcp[2] > start[2] + 0.012:
            # a finger came down on the lid's edge: the lid is not where it was thought
            self.up(dz=0.08, zmin=0.25, phase="up")
            return False
        try:
            for i, q in enumerate(arc):
                rb.move(q, R, T.OPEN_W, 0.8 if i < 5 else 0.5, "push the lid")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("straddle_push_err", err=repr(e)[:200])
            rb.resync()
        self.log("lid_pushed", tcp=_r(rb.tcp_now()), how="straddle")
        return True

    def lift_lid(self, hinge, th):
        """A lid flung (nearly) flat open (runs 022/024: 142-165 deg; the straddle cannot reach its edge): the hand
        tilted 45 deg about a closing axis along the hinge, fingers closed and pointing in towards the hinge,
        slides its fingertips under the lid's free edge from outside, then lifts it along an arc around the hinge
        past vertical; it falls shut. -> True when done."""
        T, rb = self.T, self.rb
        e1, e2 = self.box["e1"], self.box["e2"]
        hz = BOX_H
        y1 = math.atan2(e1[1], e1[0])

        def F(r, z):
            xy = hinge + r * e2
            return np.array([xy[0], xy[1], z])

        def A(al, rho):
            ar = math.radians(al)
            xy = hinge + rho * (-math.cos(ar)) * e2
            return np.array([xy[0], xy[1], hz + rho * math.sin(ar)])

        best = None
        # several tilts / reaches (run 026: a hinge on the robot's side, the first plan out of reach)
        # contact near the free edge (long lever: the hinge is stiff, run 033 lifting at 15 cm tipped the box)
        for tdeg, ro, rho in ((45, 0.28, 0.175), (38, 0.27, 0.165), (32, 0.26, 0.155)):
            fts = [F(ro + 0.02, 0.20), F(ro, 0.045), F(rho, 0.045)] + \
                [A(a, rho) for a in (175, 160, 145, 130, 115, 100, 88, 78)]
            for y in (y1, y1 + math.pi):
                for sg in (1, -1):
                    R = tilt_R(T.down(y), math.radians(tdeg), sg)
                    if float(R[:, 2][:2] @ e2) > -0.45:
                        continue             # the fingers must point in towards the hinge
                    tcps = [f - R[:, 2] * T.FINGERTIP for f in fts]
                    ms = [self.ik_eval(q, R) for q in tcps]
                    if any(m is None for m in ms):
                        return False
                    mm = min(m[0] for m in ms)
                    self.log("lift_ik", tilt=tdeg, y=round(y, 2), sg=sg,
                             bad=[i for i, m in enumerate(ms) if m[0] <= 0])
                    if mm > 0 and (best is None or mm > best[0]):
                        best = (mm, R, tcps)
            if best is not None:
                break
        self.log("lift_plan", theta=th, ok=best is not None, tilt=tdeg)
        if best is None:
            return False
        _, R, tcps = best
        try:
            p = np.asarray(rb.tcp_now(), float)
            top = max(tcps[0][2], hz + 0.21 + T.FINGERTIP)
            if p[2] < top:
                rb.move(np.array([p[0], p[1], top]), np.asarray(rb.R_cmd), 0.0, 1.0, "up")
            rb.move(np.array([tcps[0][0], tcps[0][1], top]), R, 0.0, 1.0, "transit")
            rb.move(tcps[0], R, 0.0, 1.0, "transit")
            rb.move(tcps[1], R, 0.0, 1.0, "descend")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("lift_err", err=repr(e)[:200])
            rb.resync()
            self.up(dz=0.1, zmin=0.25, phase="up")
            return False
        tcp = np.asarray(rb.tcp_now(), float)
        if tcp[2] > tcps[1][2] + 0.015:
            self.log("lift_blocked", tcp=_r(tcp))
            self.up(dz=0.1, zmin=0.25, phase="up")
            return False
        try:
            rb.move(tcps[2], R, 0.0, 1.2, "under the lid")
            for q in tcps[3:]:
                rb.move(q, R, 0.0, 0.7, "lift the lid")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("lift_push_err", err=repr(e)[:200])
            rb.resync()
        self.log("lid_pushed", tcp=_r(rb.tcp_now()), how="lift")
        # back out along the fingers and up, the lid falling shut in front of them
        try:
            p = np.asarray(rb.tcp_now(), float)
            rb.move(p - R[:, 2] * 0.08, R, 0.0, 0.8, "retreat")
            rb.flush()
        except (T.RobotStop, T.Unreachable) as e:
            self.log("lift_back_err", err=repr(e)[:200])
            rb.resync()
        self.up(dz=0.08, zmin=0.25, phase="up")
        return True

    def press_lid(self, c, e2, hz, yaw):
        T, rb = self.T, self.rb
        # press the lid's free half down (run 005: it stayed at 8.2 deg on a cable)
        R = T.down(yaw_of(rb.R_cmd))        # straight down (the lift leaves the hand tilted)
        q1 = c - 0.06 * e2
        for Rq in (R, T.down(yaw_of(R) + math.pi)):
            try:
                rb.move(np.array([q1[0], q1[1], hz + 0.014 + T.FINGERTIP]), Rq, 0.0, 0.6, "over the lid")
                rb.move(np.array([q1[0], q1[1], hz + 0.005 + T.FINGERTIP]), Rq, 0.0, 0.6, "press the lid")
                rb.flush()
                break
            except (T.RobotStop, T.Unreachable) as e:
                self.log("lid_press_err", err=repr(e)[:200])
                rb.resync()
        self.log("lid_pressed", tcp=_r(rb.tcp_now()))
        self.up(dz=0.08, zmin=0.18, phase="up")

    # ---------------------------------------------------------------- main
    def run(self):
        rb = self.rb
        self.log("start", tcp=_r(rb.tcp_now()), task=self.ctx.task)
        o, locs = self.survey()
        if self.box is None:
            return dict(result="box not found")
        if USE_BOX_DRAG:
            nc = self.needs_clearing(locs)
            self.log("clearing", names=nc)
            if nc:
                try:
                    # far enough for >= 12 cm between the hinge and the nearest object (run 032: 7 cm was not,
                    # the lid was knocked flat); the fingers travel ~1.5 cm before meeting the wall
                    dist = float(np.clip(0.12 - getattr(self, "clear_dv", 0.0) + 0.02, 0.08, 0.16))
                    if self.drag_box(dist):
                        old_box, old_tall = self.box, self.tall
                        self.survey_obs = {}
                        o, locs = self.survey()
                        if self.box is None:
                            # not found from the survey pose (run 036: pulled nearer the robot): predict it
                            sh = -(dist - 0.015) * old_box["e2"]
                            self.box = dict(old_box, c=old_box["c"] + sh)
                            if old_tall is not None and len(old_tall):
                                self.tall = old_tall + np.array([sh[0], sh[1], 0.0])
                            self.log("box_predicted", c=_r(self.box["c"]))
                            for n in self.names:
                                if locs.get(n) is None and n in self.survey_obs:
                                    locs[n] = np.asarray(self.survey_obs[n]["c"], float)
                except (self.T.RobotStop, self.T.Unreachable) as e:
                    self.log("drag_box_err2", err=repr(e)[:200])
                    rb.resync()
        slots = self.layout()
        self.log("layout", slots={k: _r(v, 3) if v[2] is not None else _r(v[:2], 3) for k, v in slots.items()})
        done = []
        for n in self.names:
            loc = locs.get(n)
            for k in range(4):
                if self.ctx.time_left() < 45:
                    break
                if loc is None:
                    # re-survey for it
                    try:
                        self.travel(SURVEY, np.asarray(rb.R_cmd), self.T.OPEN_W, "look", T=1.0)
                    except (self.T.RobotStop, self.T.Unreachable) as e:
                        self.log("resurvey_err", err=repr(e)[:200])
                        rb.resync()
                    o2 = rb.observe_frame("L_wrist")
                    ob = find_object(self.ctx, o2, n, (0.0, 0.0), box=self.box, log="resurvey_obj", relaxed=True,
                                     exclude=[v for kk, v in locs.items() if kk in done and v is not None])
                    if ob is None:
                        break
                    loc = np.asarray(ob["c"], float)
                try:
                    res, ob = self.pick_and_place(n, loc, slots[n], first=(k == 0))
                except (self.T.RobotStop, self.T.Unreachable) as e:
                    self.log("pp_err", err=repr(e))
                    self.up()
                    res, ob = "error", None
                self.log("attempt", name=n, k=k, res=res)
                if res in ("blocked", "missed", "error", "dropped") and self.last_try is not None \
                        and self.last_try[0] == n:
                    self.failed.append(self.last_try)
                self.last_try = None
                if res == "lid_in_way":
                    try:
                        self.prop_lid()
                    except (self.T.RobotStop, self.T.Unreachable) as e:
                        self.log("prop_err2", err=repr(e))
                        self.up()
                if res == "ok":
                    done.append(n)
                    break
                if ob is not None:
                    loc = np.asarray(ob["c"], float)
                if res in ("lost", "dropped"):
                    loc = None
        # the box may have been shoved on the way (run 021: 10 cm, the lid closing missed)
        self.recheck_box()
        try:
            self.close_lid()
        except (self.T.RobotStop, self.T.Unreachable) as e:
            self.log("lid_err", err=repr(e))
        return dict(result="ok" if len(done) == len(self.names) else "partial", done=done)


def run(ctx):
    try:
        return Pack(ctx).run()
    except Exception as e:
        import traceback
        ctx.log("crash", err=traceback.format_exc()[-1500:])
        return dict(result="crash " + repr(e))
