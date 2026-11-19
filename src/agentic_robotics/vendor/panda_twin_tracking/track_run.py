"""Tracking mode for panda_twin's clutter pick (2026-11-11, user: "a subskill or acting mode 'tracking' which tracks
objects even if they move, e.g. track the tape even if I move it").

panda_twin's `real_box.py run --pick seg` (RealClutterProgram: SAM3 finds each thing by its name in the wrist camera's
colour image) plans once, then looks for each thing where Astra marked it: 13 cm around that point at most, and then
it starts taking other things away, taking the thing to be hidden under them. Tracking mode keeps each thing's place up
to date from the fixed scene camera at every decision point, with no new Astra call:

- before each look for the thing to pick: the table must be still (something moving there, a hand: it waits, up to
  30 s); then the scene camera finds the thing again, by its name (SAM3 on the scene image) and by how it looked at the
  plan (panda_twin's track.py template). Found more than 3 cm from where it was last seen: the wrist camera looks there.
- not where the wrist camera looked: the scene camera looks for it again, before any search under other things (that
  original search runs only if the scene camera sees it nowhere else).
- a pick that came back empty (missed, dropped, not found: it was moved meanwhile): the thing is found again and the
  pick tried again, up to TRACK_TRIES (3) more times.
- the box: found again before each pick and before each put_in (real_box.track_box); moved while the hand carries a
  thing: the box's survey moves with it.
Also fixed here, in both modes: the thing's last seen place (ClutterProgram.est) was kept from one pick to the next, so
a second thing was first looked for where the first one had been.

Not tracked: a move during a trajectory. The arm runs each confirmed trajectory to its end; a thing moved during the
final descent is missed, then found again (the retry above).

panda_twin is not changed: this file imports its real_box.py and replaces the two clutter classes before its main().

    cd ~/tdieudonne/panda_twin            (real_box.py's paths are relative to it)
    py ~/tdieudonne/panda_twin_tracking/track_run.py run --pick seg --text "Put the red tape roll into the white box."
    (any real_box.py arguments; --no-track: the original behaviour, with only the est fix)

Twin tests (the twin backend only): TRACK_TEST="<when>:<object>:<dx>:<dy>[,...]" moves an object (m), or
"<when>:<object>:<dist>" that far in the first direction with free table there (other things' centres >= 9 cm away,
the box's >= 15 cm; for the box: >= 15 cm), at
after_plan (before the first step), look<k> (right after the k-th wrist look: the grasp is planned on the old place),
in<k> (as the k-th put_in starts: the hand holds the thing).
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

import numpy as np

PT = Path.home() / "tdieudonne" / "panda_twin" / "code"
sys.path.insert(0, str(PT)); sys.path.insert(0, str(PT / "real"))
TRACK = "--no-track" not in sys.argv
if not TRACK:
    sys.argv.remove("--no-track")

import real_box as RB  # noqa: E402  (its import reads sys.argv: --task / --text / --clutter)

F, T, TRK, CB = RB.F, RB.T, RB.TRK, RB.CB

TRIES = int(os.environ.get("TRACK_TRIES", "3"))   # picks again after an empty one
MOVED = 0.03            # m: found this far from where it was last seen = moved
STILL_DIFF = 30         # colour change (0-255, after a 5 px blur) that counts a pixel as changed
STILL_PX = 300          # changed pixels on the table between two scene frames 0.4 s apart: something is moving
STILL_WAIT = 30.0       # s: waits at most this long for the table to be still
RETRY_ON = ("not found", "missed", "dropped")     # (other failures - no clear grasp, another thing lifted - are not
                                                  # about where the thing is: no retry)


def parse_test(s):
    out = []
    for part in (s or "").split(","):
        if part.strip():
            f = part.strip().split(":")
            if len(f) == 3:
                out.append(dict(when=f[0], obj=f[1], dist=float(f[2]), done=False))
            else:
                out.append(dict(when=f[0], obj=f[1], dx=float(f[2]), dy=float(f[3]), done=False))
    return out


class TrackPerception(RB.RealClutterPerception):
    """RealClutterPerception + each step's thing cut out of the plan's scene image (track.py templates; the clutter
    plan made none)."""

    def plan(self, feedback=None):
        steps = super().plan(feedback)
        self.templates = self.step_templates() if steps else None
        return steps

    def step_templates(self):
        sl = getattr(self, "scene_last", None)
        obs = sl[0] if (sl is not None and not self.gt) else self.observe("d415")
        rgb = obs.get("rgb")
        out = []
        for (sk, _), sd in zip(self.steps, self.seeds):
            tpl = None
            if rgb is not None and sd is not None and sk in ("pick", "in"):
                try:
                    tpl = TRK.make_template(rgb, F.project(obs, sd["p"]), 40 if sk == "pick" else 101)
                except Exception:
                    tpl = None
            out.append(tpl)
        try:
            self.track_roi = self.roi(obs, T.TASK_BOX)
        except Exception:
            self.track_roi = None
        self.still_mask = self.footprint(obs)
        return out

    def footprint(self, obs):
        """The table's working area (TASK_BOX at z 0) in the scene image: where wait_still looks for movement. (11-12:
        track_roi, TASK_BOX's box up to 26 cm high, is 45 % lab behind the table on the real camera: robots, floor.)"""
        import cv2
        try:
            foot = T.TASK_BOX[T.TASK_BOX[:, 2] == 0]
            uv = np.array([F.project(obs, q) for q in foot], np.float32)
            W, Hh = int(obs["K"][0]), int(obs["K"][1])
            mk = np.zeros((Hh, W), np.uint8)
            cv2.fillConvexPoly(mk, cv2.convexHull(uv).astype(np.int32), 1)
            return mk > 0 if mk.sum() > 1000 else None
        except Exception:
            return None

    def arm_pixels(self, o, grow=6):
        """RealClutterPerception.arm_pixels with only the robot drawn (the arms' geoms; everything else hidden).
        (11-12, real run 13:43: drawn from the calibrated eye, 6 cm behind the model's camera, the camera looked through
        its own housing box (184k of 307k px; the arm: 13 px): the robot was not masked, its lifted hand read as
        "something else hangs at the hand" and the well-grasped tape was put back. The twin draws from the model's own
        camera: never seen there. Also hidden: the twin's table things, not on the real table (they could cover the
        arm in the drawing; the twin ruler's edges drew a 16 px band of "arm" across the table).)"""
        m = self.S.m
        if getattr(self, "_not_arm", None) is None:
            self._not_arm = np.array([g for g in range(m.ngeom)
                                      if not m.body(int(m.geom_bodyid[g])).name.startswith(("L_", "R_"))], int)
        ids = self._not_arm
        keep = m.geom_group[ids].copy()
        m.geom_group[ids] = 5                                         # (a group the drawing's MjvOption hides. Moving
        try:                                                          # the housing does not work: a body's only geom
            return super().arm_pixels(o, grow)                        # sits at its inertial frame, geom_pos unused)
        finally:
            m.geom_group[ids] = keep

    HAND_ABOVE = 0.08       # m over the fingertips ...
    HAND_R = 0.15           # ... within this of the hand's axis: robot

    def scene_depth(self):
        """RealClutterPerception.scene_depth; near the hand, everything more than HAND_ABOVE over the fingertips counts
        as the robot's too. (11-12, real run 15:04: the wrist camera's USB cable, not in the model, loops up beside
        the hand: 127 px of "something else hangs at the hand" 27 cm over the fingertips, and the well-grasped tape
        was put back; 4 px a minute later, the loop swung elsewhere. A thing that came up with the target hangs at or
        under the fingers.)"""
        o = super().scene_depth()
        try:
            o["arm"] = o["arm"] | self.hand_space(o)
        except Exception:
            pass
        return o

    def hand_space(self, o):
        P, ok = F.world(o)
        tcp = self.S.d.site_xpos[self.S.tcp]
        return ok & (P[..., 2] > tcp[2] - T.FINGERTIP + self.HAND_ABOVE) & (np.linalg.norm(P[..., :2] - tcp[:2], axis=-1) < self.HAND_R)


class TrackProgram(RB.RealClutterProgram):
    """RealClutterProgram with tracking (see the module's doc)."""

    def __init__(self, S, per, confirm_plan=False):
        super().__init__(S, per, confirm_plan=confirm_plan)
        self.tests = parse_test(os.environ.get("TRACK_TEST")) if RB.TwinArm is not None and isinstance(S.backend, RB.TwinArm) else []
        self.n_look, self.n_in = 0, 0

    # ------------------------------------------------------------------ the run
    def run(self, stop_after=None):
        """RealClutterProgram.run + tracking: the box found again before each pick and put_in, each pick tracked."""
        self.targets, self.avoid, self.box = {}, {}, None
        steps = self.per.plan()
        if not steps:
            return "plan: no usable steps"
        self.steps = steps
        self.event("plan", steps=[f"{s}:{o}" for s, o in steps], tracking=TRACK)
        print("plan:", "; ".join(f"{i} {s} {o}" for i, (s, o) in enumerate(steps)), "| tracking", "on" if TRACK else "off", flush=True)
        if self.confirm_plan:
            print(f"   (Astra's marks: {self.per.dir / (self.per.tag + '_plan.jpg')})", flush=True)
            if input("   go ahead with this plan? [Enter = yes, n = no] ").strip().lower() not in ("", "y", "yes"):
                return "plan declined"
        self.test_moves("after_plan")
        self.sizes = {}
        self.reserved = [self.per.seeds[j]["p"][:2] for j, (sk, _) in enumerate(steps) if sk == "down"]
        n_steps = len(steps) if stop_after is None else min(stop_after, len(steps))
        for i, (sk, obj) in enumerate(steps[:n_steps]):
            print(f"-- step {i}: {sk} {obj}", flush=True)
            try:
                if sk == "pick":
                    self.est = None                                   # (a new thing: not where the last one was seen)
                    if i + 1 < len(steps) and steps[i + 1][0] in ("on", "in"):
                        if TRACK:
                            self.track_box()
                        self.survey(i + 1)
                    r = self.pick_tracked(i)
                else:
                    if sk == "in" and self.held is not None and not self.still_holding():
                        self.event("it fell out of the hand before the put_in: pick it again")
                        print(f"   the {steps[i - 1][1]} fell out of the hand: picking it again", flush=True)
                        self.S.move(self.S.p_cmd, self.S.R_cmd, CB.B.OPEN_W, 0.4, "open (nothing held)")
                        self.held = None
                        if i == 0 or steps[i - 1][0] != "pick":
                            return f"step {i} {sk}: it fell out of the hand"
                        r = self.pick_tracked(i - 1)
                        if r != "ok":
                            return f"step {i - 1} pick (again, it fell out): {r}"
                    if sk == "in":
                        self.n_in += 1
                        self.test_moves(f"in{self.n_in}")
                        if TRACK:
                            dxy = self.track_box()
                            if dxy is not None:
                                self.shift_survey(i, dxy) if self.held is not None else self.survey(i)
                    r = getattr(self, "f_" + sk)(i)
                    if sk == "in" and r == "ok" and TRACK:
                        self.S.flush()
                        self.box_look_now()
            except T.Unreachable as e:
                return f"step {i} {sk}: unreachable ({e})"
            except F.Unseen as e:
                return f"step {i} {sk}: {e}"
            self.S.flush()
            if r != "ok":
                return f"step {i} {sk}: {r}"
        if self.held is not None:
            self.put_back()
        return "done" if n_steps == len(steps) else f"stopped after {n_steps} steps"

    def pick_tracked(self, i):
        """f_pick; came back empty because the thing was not where it was looked for (not found, missed, dropped): it is
        found again (locate) and picked again, up to TRIES times."""
        name = self.steps[i][1]
        self.clear_mem = dict(failed=[], rounds=0)                    # (remove_blocker: what could not be moved)
        for k in range(1 + (TRIES if TRACK else 0)):
            boxed = False
            try:
                r = self.f_pick(i)
            except F.Unseen as e:
                if not TRACK or k == TRIES:
                    raise
                boxed = "blocking thing" in str(e)                    # (11-12: the target seen, not one of the things
                r = f"{'could not clear around it' if boxed else 'not found'} ({e})"   # around it movable)
            if r == "ok" or self.held is not None or not TRACK or not (boxed or any(w in r for w in RETRY_ON)):
                return r
            if k < TRIES:
                if boxed:
                    print(f"   the {name} is seen but still boxed in ({r}): again, without the things that would not move, "
                          f"looking wider around it ({k + 1}/{TRIES})", flush=True)   # (not e: deleted after its except)
                else:
                    print(f"   tracking: the {name} was not picked ({r}): looking for it again ({k + 1}/{TRIES})", flush=True)
                self.event("tracking: pick again", why=r, attempt=k + 1)
        return r

    # ------------------------------------------------------------------ clearing around the target
    CLEAR_MEMORY = os.environ.get("CLEAR_MEMORY", "0") == "1"      # (11-12 twin, 24 robot-path piles: 19/24 on and
    # off; on lost pile 10 (a blocker that failed once worked on a later try) and won pile 1 (an unrelated reach
    # failure): no gain, so off by default)

    def remove_blocker(self, obs, Pn, is_t, cands, mask):
        """ClutterProgram.remove_blocker, with a memory: a thing that could not be taken away, pushed or dragged is
        skipped by the next attempts of this pick, and each attempt looks wider for something else around the target
        (12 cm, then 15, 18, 21; 4 things, then 6, 8, 10). (11-12, real run 15:04: attempts 1 and 2 tried the same two
        blockers and the same 3 cm thing the same way, 27 s each; the third found the apple, 62 mm, and moved it.)"""
        if not self.CLEAR_MEMORY:
            return super().remove_blocker(obs, Pn, is_t, cands, mask)
        mem = getattr(self, "clear_mem", None) or dict(failed=[], rounds=0)
        self.clear_mem = mem
        k = mem["rounds"]

        def known(X):
            return any(np.linalg.norm(np.asarray(X[:2]) - f) < 0.025 for f in mem["failed"])

        def moved():
            mem["failed"].clear(); mem["rounds"] = 0                  # (something moved: a new scene)

        P, ok = F.world(obs)
        tried, seen = 0, []
        for g in cands:
            if tried >= 3:
                break
            if any(abs((g["close_a"] - a + math.pi / 2) % math.pi - math.pi / 2) < math.radians(25) for a in seen):
                continue
            seen.append(g["close_a"])
            hit = CB.blocking(Pn, ~is_t, g["c"], g["close_a"], g["q"], g["z_want"], g["fm"])
            if hit.sum() < 10:
                tried += 1
                continue
            hp = np.zeros(ok.shape, bool); hp.flat[np.flatnonzero(ok)[hit]] = True
            c = self.segment_at(obs, hp, mask)
            if c is None:
                tried += 1
                self.event("blocking thing not segmented", a_deg=round(math.degrees(g["close_a"])))
                continue
            X = np.r_[np.median(c["Q"][:, :2], 0), float(np.percentile(c["Q"][:, 2], 97))]
            if known(X):
                self.event("blocked: skip it, it would not move before", at_mm=np.round(X * 1000).tolist())
                continue
            tried += 1
            self.event("blocked: put the blocking thing aside first", a_deg=round(math.degrees(g["close_a"])), at_mm=np.round(X * 1000).tolist(),
                       points=int(hit.sum()), in_mask=c["n"])
            if self.take_away(obs, c["mask"], c["Q"], "it blocked the target"):
                return moved()
            sup = self.support_under(obs, c["mask"], c["Q"])
            tc = np.r_[np.median(P[mask & ok][:, :2], 0), 0.0]
            if CB.SIDE_PUSH:
                awy = np.median(c["Q"][:, :2], 0) - tc[:2]
                if np.linalg.norm(awy) > 1e-3:
                    Ld, _ = self.side_push_any(obs, c["mask"], c["Q"], sup, 0.05, awy / np.linalg.norm(awy),
                                               "blocked: push the blocking thing away from the side", max_in_way=150, cone=math.radians(60))
                    if Ld > 0.01:
                        return moved()
            if self.drag(obs, c["mask"], c["Q"], sup, 0.05, away=tc, what="blocked: drag the blocking thing away"):
                return moved()
            mem["failed"].append(X[:2].copy())
        tc = np.median(P[mask & ok][:, :2], 0)
        r_near, n_near = 0.12 + 0.03 * k, 4 + 2 * k
        near = [c for c in self.others(obs, exclude=mask) if np.linalg.norm(np.median(c["Q"][:, :2], 0) - tc) < r_near]
        near.sort(key=lambda c: -float(np.percentile(c["Q"][:, 2], 90)))
        n = 0
        for c in near:
            if n >= n_near:
                break
            X = np.r_[np.median(c["Q"][:, :2], 0), 0.0]
            if known(X):
                continue
            n += 1
            self.event("blocked: take something around it away", top_mm=round(float(np.percentile(c["Q"][:, 2], 90)) * 1000),
                       score=round(c["score"], 2), within_cm=round(r_near * 100), attempt=k + 1)
            if self.take_away(obs, c["mask"], c["Q"], "it crowded the target"):
                return moved()
            mem["failed"].append(X[:2].copy())
        mem["rounds"] = k + 1
        raise F.Unseen("could not take a blocking thing away")

    # ------------------------------------------------------------------ is it still in the hand?
    def still_holding(self):
        """Fingers apart: yes. Closed flat (a soft thing squeezed, or it slid out): the wrist camera between the
        fingertips (the hand first raised to 12 cm over the table if it is lower, and back after)."""
        S = self.S
        if S.finger_width() > 0.0025:
            return True
        tcp = S.tcp_now()
        low = tcp[2] - T.FINGERTIP < 0.12
        if low:
            S.move(np.r_[tcp[:2], 0.12 + T.FINGERTIP], S.R_cmd, 0.0, 0.8, "up: still in the hand?"); S.hold(0.2)
        try:
            frac = self.between_fingers()
        except Exception as e:
            self.event("still in the hand? the look failed", err=repr(e)[:200])
            return True
        held = frac >= self.HELD_NEAR
        self.event("still in the hand?", held=held, near=round(frac, 2), width_mm=round(S.finger_width() * 1000, 1))
        if low and held:
            S.move(tcp, S.R_cmd, 0.0, 0.8, "back down")
        return held

    def put_down(self, X, what, transit=True):
        """RealClutterProgram.put_down, unless the thing fell out of the hand on the way. (11-12, real run 15:04: the
        apple rolled out of the fingers in the lift, 41.8 -> 0.0 mm; the empty hand was carried 30 cm and 'set it
        down'.)"""
        if self.held is not None and not self.still_holding():
            S = self.S
            self.event("it fell out of the hand: nothing to put down", during=what)
            print("   it fell out of the hand on the way: nothing to set down (looking again)", flush=True)
            S.move(S.p_cmd, S.R_cmd, CB.B.OPEN_W, 0.4, "open (nothing held)")
            self.held = None
            return
        return super().put_down(X, what, transit)

    # ------------------------------------------------------------------ a soft thing held flat
    HELD_NEAR = 0.25        # share of the space between the fingertips (wrist image) nearer than the tips + 5 cm or
                            # without depth: held. (11-12, recorded real frames after lifts: held 0.32-0.50 (two
                            # cables called "dropped", a tape, 11-10's small cable), empty hands 0.06-0.12, 0.18 with
                            # a kettle under the hand)

    def grasp(self, g, Q, geo, support, strict=True):
        """ClutterProgram.grasp; "dropped" with the fingers closed flat while the wrist camera sees something between
        the fingertips: held (soft). (11-12, real run 14:10: the coiled cable, squeezed, read 0.0 mm; called dropped,
        the hand opened 30 cm up and the cable fell out of reach. Same on 11-03 16:20. task_fast's box pick has had
        this rule since 10-29; the clutter grasp did not.)"""
        r = super().grasp(g, Q, geo, support, strict)
        S = self.S
        if r != "dropped" or S.finger_width() > 0.0025:
            return r
        try:
            frac = self.between_fingers()
        except Exception as e:
            self.event("closed flat: the look between the fingers failed", err=repr(e)[:200])
            return r
        if frac < self.HELD_NEAR:
            self.event("closed flat, nothing between the fingers: dropped", near=round(frac, 2))
            return r
        R = CB.B.down(g["a"]); c = g["c"]
        h_obj = max(geo["top"] - support, 0.01)
        tcp = S.tcp_now()
        off, top = np.zeros(3), geo["top"] + tcp[2] - g["z"]
        bottom_off = top - h_obj - tcp[2]
        w = min(max(0.03, g["w"]), 0.068)                             # (let go of it wide enough: as planned)
        self.held = dict(k=None, R=R, h=h_obj, off=off, bottom_off=bottom_off, width=w)
        aa, ww = F.widths(Q[:, :2])
        self.last_g = dict(Q=Q, c=c, top=geo["top"], table=support, width=g["w"], dirs=aa[ww < geo["w_min"] + 0.002].tolist())
        self.event("the fingers closed flat but it is between them: held (soft)", near=round(frac, 2),
                   width_mm=round(S.finger_width() * 1000, 1))
        print(f"   the fingers closed flat on it, but it is between them: held (soft; {frac:.2f} of the space)", flush=True)
        return "ok"

    def between_fingers(self):
        """The wrist camera now: the space between the fingertips (TCP frame: 8 cm along, 6 cm across, from 3 cm above
        the TCP to 3.5 cm below it) in its image, the green fingers left out; the share of it nearer than the tips
        + 5 cm or without depth (a held thing is nearer than the D415's shortest range), thin bits opened away."""
        import cv2
        S = self.S
        o = self.per.wrist()
        tcp = S.tcp_now(); R = S.d.site_xmat[S.tcp].reshape(3, 3).copy()
        corners = np.array([[x, y, z] for x in (-0.04, 0.04) for y in (-0.03, 0.03) for z in (-0.03, 0.035)])
        uv = np.array([F.project(o, tcp + R @ cn) for cn in corners], np.float32)
        W, Hh = int(o["K"][0]), int(o["K"][1])
        reg = np.zeros((Hh, W), np.uint8)
        cv2.fillConvexPoly(reg, cv2.convexHull(uv).astype(np.int32), 1)
        hsv = cv2.cvtColor(np.ascontiguousarray(o["rgb"]), cv2.COLOR_RGB2HSV)
        green = (hsv[..., 0] > 35) & (hsv[..., 0] < 90) & (hsv[..., 1] > 70) & (hsv[..., 2] > 40)
        reg = (reg > 0) & ~(cv2.dilate(green.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0)
        tip = tcp + R @ np.r_[0.0, 0.0, T.FINGERTIP]
        d_tip = float((np.asarray(o["R"]).T @ (tip - np.asarray(o["eye"])))[2])
        z = o["depth"]
        near = reg & (~np.isfinite(z) | (z < d_tip + 0.05))
        near = cv2.morphologyEx(near.astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)) > 0
        return float(near.sum()) / max(int(reg.sum()), 1)

    # ------------------------------------------------------------------ tracking a thing
    def locate(self, i, phrase, digs=3):
        """ClutterProgram.locate with tracking: the scene camera's view first (moved: look there); not where the wrist
        camera looked: the scene camera again; only if it is nowhere in sight, the original search (and dig)."""
        if not TRACK:
            return super().locate(i, phrase, digs)
        self.wait_still()
        self.track_thing(i, phrase)
        try:
            return self.counted(super().locate(i, phrase, digs=0))
        except F.Unseen:
            pass
        self.wait_still()
        if self.track_thing(i, phrase, lost=True):
            try:
                return self.counted(super().locate(i, phrase, digs=0))
            except F.Unseen:
                pass
        self.event("tracking: not seen elsewhere: the original search")
        return self.counted(super().locate(i, phrase, digs))

    def counted(self, r):
        self.n_look += 1
        self.test_moves(f"look{self.n_look}")
        return r

    def in_box(self, p):
        b = self.box
        if b is None:
            return False
        rel = np.asarray(p[:2], float) - np.asarray(b["c"], float)
        return bool(abs(rel @ b["u"]) < b["hu"] + 0.02 and abs(rel @ b["v"]) < b["hv"] + 0.02)

    def scene_cands(self, i, phrase):
        """Where step i's thing may be in a fresh scene image: SAM3's masks for its name and the template's best match.
        -> (candidates [dict(uv, p (top centre), sam, tpl, how)], obs)"""
        per = self.per
        obs = per.observe("d415")
        rgb = obs.get("rgb")
        if rgb is None:
            return [], obs
        tpls = getattr(per, "templates", None) or []
        tpl = tpls[i] if i < len(tpls) else None
        H, W = rgb.shape[:2]
        cands = []
        roi = getattr(per, "track_roi", None)                      # (the table's part of the image: SAM3 sees it
        try:                                                          # larger; the real C01 image: 0.88 -> 0.91)
            segs = per.segment(rgb, phrase, box=tuple(int(v) for v in roi) if roi is not None else None)
        except Exception as e:
            segs = []
            self.event("tracking: SAM3 on the scene image failed", err=repr(e)[:200])
        for mk, sc in segs:
            n = int(mk.sum())
            if sc < 0.3 or n < 12 or n > 0.05 * H * W:
                continue
            ys, xs = np.nonzero(mk)
            cands.append(dict(uv=np.array([xs.mean(), ys.mean()]), sam=float(sc), how="name", mask=mk))
        if tpl is not None:
            uv, sc, _ = TRK.match(rgb, tpl, getattr(per, "track_roi", None))
            cands.append(dict(uv=np.asarray(uv, float), sam=0.0, tpl=float(sc), how="look"))
        out = []
        for c in cands:
            if tpl is not None and "tpl" not in c:
                c["tpl"] = float(TRK.score_at(rgb, tpl, c["uv"]))
            c.setdefault("tpl", 0.0)
            zt = per.top_z(obs, c["uv"], 0.012)
            if zt is None or zt < 0.006:                              # (nothing stands there)
                continue
            p = per.on_plane(obs, c["uv"], min(zt, 0.06))
            if p is None or self.in_box(p):                           # (one in the box is already packed)
                continue
            p = np.asarray(p, float)
            if not (-0.75 < p[0] < 0.0 and -0.35 < p[1] < 0.60):      # (off the table's working area)
                continue
            c["p"] = p
            out.append(c)
        return out, obs

    def track_thing(self, i, phrase, lost=False):
        """Is step i's thing where it was last seen (self.est, else Astra's point)? Found clearly elsewhere by the scene
        camera: self.est moves there (the wrist camera's next look). lost: the wrist camera did not find it there.
        -> True if it moved"""
        import cv2
        last = np.asarray(self.est if self.est is not None else self.per.seeds[i]["p"], float)
        cands, obs = self.scene_cands(i, phrase)
        good = [c for c in cands if c["sam"] >= 0.5 or (c["sam"] >= 0.3 and c["tpl"] >= 0.65) or (c["how"] == "look" and c["tpl"] >= 0.75)]
        # (11-11, the real C01 image: the tape 0.91 and the cable 0.85 by name; weak "cable" masks 0.3-0.4 on the cables
        # hanging at the table's edge: a weak name match counts only if it also looks like the thing did)
        ref = np.asarray(F.project(obs, last), float)                 # (where it was last seen, in this image. Compared
        tpls = getattr(self.per, "templates", None) or []             # in the image: Astra's mark is a point on the
        tpl = tpls[i] if i < len(tpls) else None                      # thing's visible side, a candidate's point its top
        x, y = int(round(ref[0])), int(round(ref[1]))                 # centre: 4 cm apart along the slanted ray, 11-11)
        for c in good:
            lc = self.per.on_plane(obs, ref, float(c["p"][2]))       # (the last place at this candidate's height)
            lc = last if lc is None else np.asarray(lc, float)
            c["d"] = float(np.linalg.norm(c["p"][:2] - lc[:2]))
            c["px"] = float(np.linalg.norm(c["uv"] - ref))
            if c["how"] == "name":
                mk = cv2.dilate(c["mask"].astype(np.uint8), np.ones((17, 17), np.uint8)) > 0
                c["near"] = bool(0 <= y < mk.shape[0] and 0 <= x < mk.shape[1] and mk[y, x])
            else:
                c["near"] = c["px"] < 12 or (tpl is not None and float(np.linalg.norm(c["uv"] - np.asarray(tpl["uv"], float))) < 12)
        near = [c for c in good if c["near"]]
        far = [c for c in good if not c["near"] and c["px"] > 15 and c["d"] >= MOVED]
        info = dict(lost=lost, candidates=len(cands), good=len(good),
                    seen=[dict(how=c["how"], sam=round(c["sam"], 2), tpl=round(c["tpl"], 2), d_mm=round(c["d"] * 1000), px=round(c["px"]),
                              near=c["near"]) for c in good][:6])
        if near or not far:                                           # (still there, or seen nowhere else)
            self.event("tracking: " + ("still where it was seen" if near else "not seen elsewhere"), **info)
            return False
        best = max(far, key=lambda c: c["sam"] + 0.5 * c["tpl"] - 0.5 * c["d"])
        self.est = np.asarray(best["p"], float)
        tpls = getattr(self.per, "templates", None) or []
        if i < len(tpls) and tpls[i] is not None:
            tpls[i]["uv"] = best["uv"]
        print(f"   tracking: the {phrase} has moved (about {best['d'] * 100:.0f} cm; scene camera, {best['how']}): "
              "the wrist camera looks there", flush=True)                # (about: before its first close look, where it
        # was is Astra's mark on its visible side, not its middle: a few cm off)
        self.event("tracking: moved, found again by the scene camera", by_mm=round(best["d"] * 1000), how=best["how"],
                   sam=round(best["sam"], 2), tpl=round(best["tpl"], 2), at_mm=np.round(best["p"] * 1000).tolist(), **info)
        return True

    def scene_rgb(self):
        S = self.S
        try:
            return S.cams.get("d415", after=S.backend.now() + 0.04)["rgb"]
        except Exception:
            return self.per.observe("d415").get("rgb")

    def wait_still(self):
        """Wait (up to STILL_WAIT) until nothing moves on the table: two scene frames 0.4 s apart (blurred) differ in
        fewer than STILL_PX pixels on the table's working area (people behind the table do not count)."""
        import cv2
        S = self.S
        S.flush()
        roi = getattr(self.per, "track_roi", None)
        foot = getattr(self.per, "still_mask", None)
        prev = self.scene_rgb()
        if prev is None:
            return
        t0, said, n = time.time(), False, 0
        while True:
            time.sleep(0.4)
            cur = self.scene_rgb()
            a, b = (cv2.GaussianBlur(x, (5, 5), 0).astype(np.int16) for x in (prev, cur))
            ch = np.abs(a - b).max(2) > STILL_DIFF
            if foot is not None and foot.shape == ch.shape:
                ch = ch & foot
            elif roi is not None:
                ch = ch[roi[1]:roi[3], roi[0]:roi[2]]
            n = int(ch.sum())
            if n < STILL_PX:
                break
            if not said:
                print("   tracking: something is moving on the table: waiting until it is still", flush=True)
                said = True
            if time.time() - t0 > STILL_WAIT:
                print("   tracking: still moving after 30 s: going on", flush=True)
                break
            prev = cur
        if said:
            self.event("tracking: waited for the table to be still", s=round(time.time() - t0, 1), changed_px=n)

    # ------------------------------------------------------------------ twin tests
    def test_moves(self, when):
        for m in self.tests:
            if m["done"] or m["when"] != when:
                continue
            w = self.S.backend.w
            import mujoco
            if "dist" in m:
                m["dx"], m["dy"] = self.free_dir(w, m["obj"], m["dist"])
            a = w.oq[m["obj"]]
            w.d.qpos[a:a + 2] += [m["dx"], m["dy"]]
            dof = w.m.jnt_dofadr[w.m.body_jntadr[w.ob[m["obj"]]]]
            w.d.qvel[dof:dof + 6] = 0
            mujoco.mj_forward(w.m, w.d)
            m["done"] = True
            print(f"   [twin test] moved {m['obj']} by ({m['dx'] * 100:+.0f}, {m['dy'] * 100:+.0f}) cm at {when}", flush=True)
            self.event("twin test: moved", obj=m["obj"], dx=m["dx"], dy=m["dy"], when=when)


    @staticmethod
    def free_dir(w, obj, dist):
        """A direction to move obj by dist with free table there (twin tests)."""
        p0 = w.pose(obj)[0][:2]
        others = {k: w.pose(k)[0][:2] for k in w.ob if k != obj and w.pose(k)[0][2] > -0.05}
        for a in np.radians(np.arange(0, 360, 15)):
            q = p0 + dist * np.array([np.cos(a), np.sin(a)])
            if not (-0.60 < q[0] < -0.28 and -0.02 < q[1] < 0.42):
                continue
            ok = all(np.linalg.norm(q - o) >= (0.15 if ("box" in k or "box" in obj) else 0.09) for k, o in others.items())
            if ok:
                return float(q[0] - p0[0]), float(q[1] - p0[1])
        raise RuntimeError(f"twin test: no free spot {dist} m from {obj}")


RB.RealClutterPerception = TrackPerception
RB.RealClutterProgram = TrackProgram

if __name__ == "__main__":
    RB.main()
