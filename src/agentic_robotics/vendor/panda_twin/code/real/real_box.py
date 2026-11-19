"""The box task (task_box.py) on the real Panda, and its bring-up steps. Twin env:

    PY="env MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 OMP_NUM_THREADS=1 ~/miniforge3/envs/teleavatar_client/bin/python -I"
    $PY code/real/real_box.py check                    NO motion: arm state, both cameras, frames and kinematics agree?
    $PY code/real/real_box.py home                     the arm to the twin's home pose, gripper open (motion)
    $PY code/real/real_box.py jog                      small test moves around home: tracking / settle errors (motion)
    $PY code/real/real_box.py touch --at X Y           the closed fingertips onto the bare table (guarded, 5 mm/s), at 3
                                                       points: the adapter's length (motion)
    $PY code/real/real_box.py look --at X Y [--box]    the wrist camera over a table point: what it sees there (motion)
    $PY code/real/real_box.py run [--stop-after N]     the task: one Astra call, then the skills (motion)
        [--task three|tape|loose|close|full] or [--text "any instruction"]   (e.g. --text "Press the button on top of
                                                       the coffee machine.": Astra's plan, its press point, press_skill.py)
  with --backend twin [--layout L] [--plan gt]: the same code with the twin as the robot and the world (dry run).
The robot backend needs code/real/arm_server.py running (deoxys env; --allow-motion for motion; it asks before each
trajectory). Every run logs to out/real/<command>_<time>/ (frames, trajectories, events, Astra's plan), and every
command but check records both cameras (video/: MP4 + each frame's time; --record-depth N: depth too; --no-record: off);
run also writes episode.mp4 (both cameras, the phase, gripper, force: recorder.py compose with the arm server's state log).

Changes to the box program for the robot (RealBoxProgram): putting the red tape on the block, the hand goes down
until the arm feels the contact (a guarded move on the server: the hand held up 2 mm or the force estimate up 4 N)
instead of 1 mm steps with the twin's contact forces; no true-state bookkeeping; the plan is shown and must be
accepted before the arm moves. Pressing a button (11-02, press_skill.py): by position too (press_down), the arm's
compliance limiting the force.
"""
from __future__ import annotations

import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import argparse
import json
import math
import sys
import time
import traceback
import warnings
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import stack_bench as B  # noqa: E402
import task_bench as T  # noqa: E402
for _i, _a in enumerate(sys.argv[:-1]):                              # (--task: read before task_box is imported)
    if _a == "--task":
        os.environ["BOX_TASK"] = sys.argv[_i + 1]
if "--text" in sys.argv and "--task" not in sys.argv:                # (a free instruction, 10-30: as the loose task,
    os.environ["BOX_TASK"] = "loose"                                  # things may overlap in the box)
if "--clutter" in sys.argv:                                          # (11-03: a clutter layout in the twin: the bench's own
    os.environ.setdefault("BOX_REAL", "0")                           # model and box, clutter_bench.sample_layout)
os.environ.setdefault("BOX_TASK", "three")     # 10-29 (user): the block, the red tape on it, the yellow tape, then the lid
os.environ.setdefault("BOX_REAL", "1")         # (no cable, screwdriver, push); the twin's box at the real size (BOX_TASK=full: all)
import task_box as TB  # noqa: E402
import task_fast as F  # noqa: E402
import clutter_bench as CB  # noqa: E402
from panda_fk import load_calib, load_rig  # noqa: E402
from real_sim import RealSenseCams, RealSim, RemoteArm, RobotStop, TwinArm, TwinCams  # noqa: E402
import track as TRK  # noqa: E402

OUT = HERE.parents[1] / "out/real"
ACTIVE = []                                                             # (the RealSims: their recordings are closed at exit)


def jdump(o):
    return json.dumps(o, default=lambda x: x.tolist() if hasattr(x, "tolist") else (float(x) if isinstance(x, np.floating) else str(x)))


# ---------------------------------------------------------------- perception and program on the robot
class RealPerception(TB.BoxPerception):
    """BoxPerception with the robot's cameras (RealSim.observe_frame); no renderers, no simulated depth."""

    def __init__(self, S, rng, out, tag, plan_gt=None):
        B.Perception.__init__(self, S, "gt", rng)                     # (kind "gt": no renderers)
        self.contacts, self.cache, self.calls, self.cost = {}, {}, 0, 0.0
        self.dir = Path(out) / "astra"; self.dir.mkdir(parents=True, exist_ok=True)
        self.tag, self.astra_log = tag, []
        self.task_text = f"Task: {TB.TEXT}"; self.place_z = {}
        self.steps, self.seeds = [], []
        self.gt, self.depth_model, self._plan_gt = plan_gt is not None, None, plan_gt

    def observe(self, cam):
        return self.S.observe_frame(cam)

    def frame(self, cam):
        r = super().frame(cam)
        if cam == "d415":
            self.scene_last = r                                       # (obs, image, crop offset, what)
        return r

    def plan(self, feedback=None):
        steps = super().plan(feedback)
        self.templates = self.make_templates()
        return steps

    def make_templates(self):
        """(10-30, track.py) Each step's thing cut out of the scene image the plan was made on: a pick's around Astra's
        two grasp points, the box around its put_in point. (The twin's reference plan: around the true points.)"""
        if self.gt:
            obs = self.observe("d415"); marks = []
            for (sk, _), sd in zip(self.steps, self.seeds):
                uv = None if sd is None else F.project(obs, sd["p"])
                marks.append((sk, uv, 40 if sk == "pick" else 101))
        else:
            sl = getattr(self, "scene_last", None)
            if sl is None:
                return [None] * len(self.steps)
            obs, _, o, _ = sl
            final = (self.astra_log[-1].get("final") or {}).get("steps") or []
            marks = []
            for st in final:
                sk = TB.SKILLS.get(str(st.get("skill", "")).strip())
                if sk is None:
                    continue
                if sk == "pick" and st.get("grasp") and len(st["grasp"]) == 2:
                    g = [np.asarray(q, float) + o for q in st["grasp"]]
                    marks.append((sk, (g[0] + g[1]) / 2, TRK.pick_size(*g)))
                elif sk == "in" and st.get("point") is not None:
                    marks.append((sk, np.asarray(st["point"], float) + o, 101))
                else:
                    marks.append((sk, None, 0))
        rgb = obs.get("rgb")
        out = []
        for sk, uv, n in marks:
            tpl = None
            if rgb is not None and uv is not None and sk in ("pick", "in"):
                try:
                    tpl = TRK.make_template(rgb, uv, n)
                except Exception:
                    tpl = None
            out.append(tpl)
        self.track_roi = self.roi(obs, T.TASK_BOX)
        return out + [None] * (len(self.steps) - len(out))

    def plan_gt(self):
        self.steps, self.seeds = self._plan_gt()
        self.astra_log.append(dict(t=float(self.S.d.time), keys=["plan (true state, twin)"], rounds=[dict(reply="", s=0.0)], final=None, img=None))
        self.templates = self.make_templates()
        return self.steps

    def close(self):
        pass


class RealBoxProgram(TB.BoxProgram):
    WIDE_YAWS = True        # (10-30: fewer wrist turns: task_fast.look_yaws, a round thing grasped at any angle)
    LOOK_FLOOR = 0.03       # (10-30: task_fast.f_pick's look never nearer a thin thing than the D415 sees)

    def __init__(self, S, per, confirm_plan=False, truth=None):
        super().__init__(S, per)
        self.confirm_plan, self.truth_inside = confirm_plan, truth

    def run(self, stop_after=None):
        """BoxProgram.run: the plan (one Astra call; again if it fails the checks), shown for approval, the inventory,
        the steps. stop_after: only the first N steps (bring-up)."""
        self.targets, self.avoid, self.box = {}, {}, None
        steps = self.per.plan()
        bad = self.check_plan(steps)
        if bad:
            self.event("plan check failed: asking again", why=bad)
            steps = self.per.plan(feedback=bad + " Mark the right objects (the task's objects are within the arm's reach) "
                                  "and respond with the whole plan again, same JSON format.")
            bad = self.check_plan(steps)
            if bad:
                return f"plan: {bad}"
        self.steps = steps
        self.event("plan", steps=[f"{s}:{o}" for s, o in steps])
        print("plan:", "; ".join(f"{i} {s} {o}" for i, (s, o) in enumerate(steps)), flush=True)
        if self.confirm_plan:
            print(f"   (Astra's marks: {self.per.dir / (self.per.tag + '_plan.jpg')})", flush=True)
            if input("   go ahead with this plan? [Enter = yes, n = no] ").strip().lower() not in ("", "y", "yes"):
                return "plan declined"
        n_steps = len(steps) if stop_after is None else min(stop_after, len(steps))
        needs_sizes = any(s == "in" and self.is_box(j) for j, (s, _) in enumerate(steps[:n_steps]))
        self.sizes = self.estimate_sizes() if needs_sizes else {}
        for i, (sk, obj) in enumerate(steps[:n_steps]):
            if self.per.seeds[i] is None:
                return f"step {i} {sk} {obj}: no point from Astra"
            print(f"-- step {i}: {sk} {obj}", flush=True)
            try:
                if sk == "pick" and i + 1 < len(steps) and steps[i + 1][0] in ("on", "in") and self.per.seeds[i + 1] is not None:
                    self.track_box()                                  # (10-30: moved since the plan? track.py)
                    self.survey(i + 1)
                if sk == "pick":
                    self.track_pick(i)
                if sk == "in":                                        # moved since the survey (during the pick):
                    dxy = self.track_box()                            # its measurement moved with it (the held
                    if dxy is not None:                               # thing blocks the wrist camera's view)
                        self.shift_survey(i, dxy) if self.held is not None else self.survey(i)
                if sk == "close":
                    self.track_box()
                r = getattr(self, "f_" + sk)(i)
            except T.Unreachable as e:
                return f"step {i} {sk}: unreachable ({e})"
            except F.Unseen as e:
                return f"step {i} {sk}: {e}"
            self.S.flush()                                            # (the step's last moves, queued: sent now)
            if r != "ok":
                return f"step {i} {sk}: {r}"
            if sk in ("in", "on") and self.truth_inside is not None:
                self.inside_log.append((i, self.truth_inside()))
        if self.held is not None:                                     # (stopped after a pick: put it back down)
            self.put_back()
        return "done" if n_steps == len(steps) else f"stopped after {n_steps} steps"

    def put_back(self):
        """Lower the held object to where it was picked (its bottom 5 mm over the table) and let go."""
        S, H = self.S, self.held
        tcp = S.p_cmd.copy()
        S.move(np.r_[tcp[:2], 0.005 - H["bottom_off"]], H["R"], 0.0, 1.5, "put it back down")
        S.move(S.p_cmd, H["R"], min(B.OPEN_W, H["width"] / 2 + 0.01), 0.5, "release")
        S.hold(0.3)
        S.move(S.p_cmd + [0, 0, 0.10], H["R"], B.OPEN_W, 0.8, "retreat")
        S.hold(0.2)
        S.flush()
        self.held = None

    CLEARANCE = 0.27        # m: the open lid's top edge is ~0.24 m over the table (the twin; refined by the box survey)

    # -- a round container (11-03: press the kettle's button, then the coffee pod into the kettle): task_fast's put_in
    def is_box(self, i):
        """Is step i's container the box (task_box's put_in: walls, lid, packing) or a round one (task_fast's: rim)?"""
        return "box" in str(self.steps[i][1]).lower()

    def survey(self, i):
        if self.steps[i][0] == "in" and not self.is_box(i):
            return self.survey_round(i)
        return super().survey(i)

    def f_in(self, i):
        if not self.is_box(i):
            return self.f_in_round(i)
        return super().f_in(i)

    def f_in_round(self, i, clear_min=0.006):
        """Into a round container (task_fast.f_in's way: over at >= 30 cm, straight down until the held thing's bottom
        is 15 mm over the rim, open), its centre over the spot of the opening farthest from what stands above the rim
        (the open lid), not the rim's centre: 11-03 real, the kettle's lid opens only to ~70 deg and leans over the
        opening's handle side; dropped at the centre, the cup came down on the lid's edge, pushed it shut and fell off
        the closed lid. The spot: within (rim radius - the thing's radius - 4 mm) of the centre, the thing's footprint
        as far as it can be from the lid's points (warned under clear_min)."""
        S, H = self.S, self.held
        X = np.asarray(self.targets[i], float)
        rim = getattr(self, "rims", {}).get(i)
        ob = getattr(self, "rim_obst", {}).get(i)
        O, Oz = ob if ob is not None else (None, None)
        c = X[:2].copy()
        bottom_clear = 0.015                                         # (its bottom over the rim when let go)
        if rim is not None and O is not None and len(O):
            r_obj = max(float(H.get("hx", 0.026)), float(H.get("hy", 0.026)))
            room = rim["r"] - r_obj - 0.006                          # (6 mm inside the rim: the fit may be its outer edge)
            best = (float(np.min(np.linalg.norm(O, axis=1))) - r_obj, np.zeros(2))
            for rr in np.arange(0.003, max(room, 0.0) + 1e-9, 0.003):   # the smallest shift that clears it by clear_min
                if best[0] >= clear_min:                             # (11-03 twin: the most clearance put the cup at the
                    break                                            # rim's edge, 43 mm off: it bounced off the table)
                for a in np.radians(np.arange(0, 360, 10.0)):
                    v = rr * np.array([math.cos(a), math.sin(a)])
                    cl = float(np.min(np.linalg.norm(O - v, axis=1))) - r_obj
                    if cl > best[0] + 0.001:
                        best = (cl, v)
            c = rim["c"] + best[1]
            self.event("drop spot off the lid", off_centre_mm=round(float(np.linalg.norm(best[1])) * 1000, 1),
                       clear_mm=round(best[0] * 1000, 1), thing_r_mm=round(r_obj * 1000), room_mm=round(room * 1000))
            under = np.linalg.norm(O - best[1], axis=1) < r_obj + 0.004
            if best[0] < clear_min and under.any():                  # (it would still touch the lid's edge on the way
                bottom_clear = max(bottom_clear, float(Oz[under].max()) - X[2] + 0.012)   # down: let go just over it, not
                self.event("let go over the lid's edge", clear_mm=round(best[0] * 1000, 1),   # pushing it shut: 11-03, the
                           bottom_over_rim_mm=round(bottom_clear * 1000))                  # cup falls past the edge
        if i in self.avoid:                                          # the fingers' axis where the lid is not
            a = self.avoid[i]
            off_local = H["R"].T @ np.r_[H["off"][:2], 0.0]
            Rn = B.down(self.best_yaw(np.r_[c, X[2] + 0.2], (a, a + math.pi)))
            H["R"], H["off"] = Rn, np.r_[(Rn @ off_local)[:2], 0.0]
        Xd = np.r_[c, X[2]]
        hi = self._over(Xd, 0.14); hi[2] = max(hi[2], 0.30 - H["bottom_off"])
        S.move(hi, H["R"], 0.0, 1.5, "carry over")
        low = self._over(Xd, bottom_clear); low[2] = max(low[2], X[2] + T.FINGERTIP + 0.015)
        S.move(low, H["R"], 0.0, 0.8, "lower to the rim")
        S.move(S.p_cmd, H["R"], B.OPEN_W, 0.5, "release")
        S.move(S.p_cmd + [0, 0, 0.10], H["R"], B.OPEN_W, 0.8, "retreat"); S.hold(0.8)
        self.held = None
        return "ok"

    def survey_round(self, i, r_est=0.05):
        """A round container's rim for put_in, with the empty hand before the pick (task_fast.survey's): task_box's
        put_in mark is a point inside it, not on its rim, so the rim's height comes from the wrist depth: from the top
        down, the first height where the points around the mark make a rim circle (task_fast.refine_rim) that goes at
        least 200 deg round (an open lid's straight edge does not). The hand will be turned off what stands above the
        rim (the open lid)."""
        S, per = self.S, self.per
        s = np.asarray(per.seeds[i]["p"], float)
        so = per.observe("d415")                                      # (the look's height: over the container's top as
        Ps, oks = F.world(so)                                         # the scene camera sees it, the open lid included,
        near_s = oks & (np.hypot(Ps[..., 0] - s[0], Ps[..., 1] - s[1]) < 0.08) & (Ps[..., 2] > 0.02) & (Ps[..., 2] < 0.45)
        z_top = max(float(s[2]), float(np.percentile(Ps[near_s][:, 2], 75)) if near_s.sum() > 30 else 0.0)   # not the mark's
        z_top = min(z_top, float(s[2]) + 0.25)                        # depth inside it: the rim would be too near; 75 %:
        h = self.look_height(0.07)                                    # not the open lid's top, 11-03 twin: a look from
        y0 = self.default_yaw(s[:2])                                  # 62 cm, out of reach)
        R = B.down(self.best_yaw(np.r_[s[:2], z_top + h], (y0, y0 + math.pi)))
        tcp = self.look_tcp(np.r_[s[:2], z_top], R, z_top + h)
        z_min = tcp[2] - (h - (B.MIN_Z + 0.03))                       # (the rim still beyond the D415's minimum range)
        while tcp[2] > z_min and S.ik(tcp, R, S.q_cmd)[1] > 0.002:   # the highest look the arm reaches
            tcp = tcp - [0, 0, 0.02]
        S.move(tcp, R, B.OPEN_W, 1.5, "survey the target")
        S.hold(0.2)
        obs = per.wrist()
        P, ok = F.world(obs)
        d = np.hypot(P[..., 0] - s[0], P[..., 1] - s[1])
        near = ok & (d < 0.12) & (P[..., 2] > 0.04)
        if near.sum() < 50:
            raise F.Unseen("no container at the put_in point")
        rim = None
        for z_c in np.arange(float(np.percentile(P[near][:, 2], 99)), 0.04, -0.01):
            try:
                r_ = F.refine_rim(obs, np.r_[s[:2], z_c], r_est)
            except F.Unseen:
                continue
            Q = P[ok & (np.abs(P[..., 2] - r_["z"]) < 0.01)]
            rel = Q[:, :2] - r_["c"]
            on = np.abs(np.hypot(*rel.T) - r_["r"]) < 0.004
            sectors = np.unique(((np.degrees(np.arctan2(rel[on, 1], rel[on, 0])) + 180.0) // 15).astype(int))
            if len(sectors) * 15 >= 200:
                rim = r_
                break
        if rim is None:
            raise F.Unseen("no rim circle at the container")
        X = np.r_[rim["c"], rim["z"]]
        rel = P[..., :2] - rim["c"]                                   # anything standing above the rim next to the
        m_ = ok & (P[..., 2] > rim["z"] + 0.015) & (P[..., 2] < rim["z"] + 0.15) & (np.hypot(*np.moveaxis(rel, -1, 0)) < rim["r"] + 0.04)
        if not hasattr(self, "rims"):
            self.rims, self.rim_obst = {}, {}
        self.rims[i] = rim
        self.rim_obst[i] = (rel[m_][::4], P[m_][::4, 2]) if m_.sum() > 100 else None   # (f_in_round's drop spot: off these)
        if m_.sum() > 100:                                            # opening (the open lid): the fingers' footprint
            O = rel[m_][::4]                                          # (+-55 mm along, +-20 across) on the fewest
            best = min(np.radians(np.arange(0, 180, 5.0)), key=lambda a: int(
                ((np.abs(O @ [math.cos(a), math.sin(a)]) < 0.055) & (np.abs(O @ [-math.sin(a), math.cos(a)]) < 0.020)).sum()))
            self.avoid[i] = best
            self.event("obstacle above the rim: fingers turned", finger_axis_deg=math.degrees(best), points=int(m_.sum()))
        self.event("container rim surveyed", c_mm=np.round(rim["c"] * 1000).tolist(), r_mm=round(rim["r"] * 1000), z_mm=round(rim["z"] * 1000),
                   look_from_mm=round((z_top + h) * 1000))
        self.targets[i] = X

    def clearance(self):
        lid = (self.box or {}).get("lid") if self.box is not None else None
        return max(self.CLEARANCE, (lid["top"] + 0.03) if lid is not None else 0.0)

    def path_clear(self, q0, q1, n=21):
        """A joint-space path q0 -> q1 (the arm's transits) keeps the hand, the fingertips and what they hold over the
        clearance height wherever it is over the table. (In the twin the program's own path check sees the objects; the
        robot's mirror has none: without this, a transit that turns the hand swept it through the open lid.)"""
        S, kin = self.S, self.S.kin
        drop = 0.0 if self.held is None else max(0.0, -float(self.held.get("bottom_off", 0.0)))   # (its bottom under the TCP)
        for f in np.linspace(0, 1, n):
            q = q0 + (q1 - q0) * f
            T_ = kin.tcp_world(q)
            pts = list(kin.check_points_world(q)[-6:])                # the hand's bottom corners, the camera
            for dy in (-0.045, 0.045):                                # the fingertips (open)
                pts.append((T_ @ np.r_[0, dy, kin.fingertip, 1.0])[:3])
            pts.append(T_[:3, 3] + T_[:3, 2] * drop)                  # what it holds
            for P in pts:
                if -0.65 < P[0] < 0.65 and -0.62 < P[1] < 0.62 and P[2] < self.clearance():
                    return False, P
        return True, None

    def joint_move(self, X, R, T_=2.5, check=False, q1=None):
        """BoxProgram.joint_move, only along a path over the clearance height (path_clear); else a checked one returns
        False (the program then goes in a straight line, high) and a forced one stops the step."""
        S = self.S
        qq, e = (q1, 0.0) if q1 is not None else S.ik(np.asarray(X, float), R, S.q_home)
        if e <= 0.002:
            ok, P = self.path_clear(S.q_cmd.copy(), np.asarray(qq, float))
            if ok and any(S.arm_clash(S.q_cmd + (np.asarray(qq) - S.q_cmd) * f) for f in np.linspace(0, 1, 16)):
                self.event("joint-space transit would come within 3 cm of the right arm")
                if check:
                    return False
                raise T.Unreachable("joint-space transit: the left arm would come within 3 cm of the right arm")
            if not ok:
                self.event("joint-space transit would sweep low", at_mm=np.round(P * 1000).tolist(), clearance_mm=round(self.clearance() * 1000))
                if check:
                    return False
                raise T.Unreachable(f"joint-space transit would take the hand to {np.round(P, 3).tolist()} (under {self.clearance():.2f} m)")
        return super().joint_move(X, R, T_, check, q1)

    TRACK = True            # (10-30: a thing moved since the plan is found again in the scene camera: track.py)

    def _scene(self):
        obs = self.per.observe("d415")
        return obs, obs.get("rgb")

    def track_pick(self, i):
        """Is the thing step i picks still where Astra marked it? If it is clearly gone from there and clearly
        somewhere else in the scene camera, its point moves there (not into the box: a thing already put there)."""
        tpls = getattr(self.per, "templates", None)
        tpl = tpls[i] if self.TRACK and tpls and i < len(tpls) else None
        if tpl is None:
            return False
        obs, rgb = self._scene()
        if rgb is None:
            return False
        s_old = TRK.score_at(rgb, tpl, tpl["uv"])
        uv, sc, ang = TRK.match(rgb, tpl, getattr(self.per, "track_roi", None))
        moved = (((s_old < 0.6 and sc >= 0.62 and sc - s_old >= 0.15) or (sc >= 0.8 and sc - s_old >= 0.18))   # (a dark
                 and float(np.linalg.norm(uv - tpl["uv"])) > 10)    # thing's empty place looks like it: 0.67)
        p = None
        if moved:
            per = self.per
            zt = per.top_z(obs, uv, 0.012)
            if zt is None or zt < 0.006:                              # (nothing stands there: a look-alike patch)
                moved = False
            else:
                p = per.on_plane(obs, uv, min(zt, 0.06))
            if p is None:
                moved = False
            elif self.box is not None:
                rel = np.asarray(p[:2]) - self.box["c"]
                if abs(rel @ self.box["u"]) < self.box["hu"] + 0.02 and abs(rel @ self.box["v"]) < self.box["hv"] + 0.02:
                    moved = False                                     # (that one is in the box already)
        self.event("tracked", step=i, thing=self.steps[i][1], score_where_it_was=round(s_old, 2), best=round(sc, 2),
                   best_px=uv.astype(int).tolist(), moved=bool(moved))
        if not moved:
            return False
        sd = self.per.seeds[i]
        d = float(np.linalg.norm(np.asarray(p[:2]) - sd["p"][:2]))
        sd["p"] = np.asarray(p, float); sd["ray"] = self.per.ray_of(obs, uv)
        tpl["uv"] = uv
        print(f"   the {self.steps[i][1]} was moved: found again {d * 100:.0f} cm away (scene camera, no new Astra call)", flush=True)
        self.event("moved: found again by the scene camera", thing=self.steps[i][1], by_mm=round(d * 1000))
        return True

    def track_box(self):
        """Is the box still where it was? Clearly gone and clearly elsewhere: every box point (put_in, close, push),
        the measured box, what was put in it, all shifted by its move (the next look into it measures it again). Not
        moved: its template cut again from this image (what was put in it changes its look)."""
        tpls = getattr(self.per, "templates", None) or []
        k = next((j for j, (sk, _) in enumerate(self.steps) if sk == "in" and self.is_box(j) and j < len(tpls) and tpls[j] is not None), None)
        if not self.TRACK or k is None:
            return None
        tpl = tpls[k]
        obs, rgb = self._scene()
        if rgb is None:
            return None
        s_old = TRK.score_at(rgb, tpl, tpl["uv"])
        roi = getattr(self.per, "track_roi", None) or (0, 0, rgb.shape[1], rgb.shape[0])
        x, y = tpl["uv"]                                              # (within ~30 cm of where it was: a slid box)
        roi = (max(roi[0], x - 150), max(roi[1], y - 150), min(roi[2], x + 150), min(roi[3], y + 150))
        uv, sc, _ = TRK.match(rgb, tpl, roi)
        far = float(np.linalg.norm(uv - tpl["uv"]))
        moved = s_old < 0.6 and sc >= 0.7 and sc - s_old >= 0.2 and far > 10   # (real 10-30: 0.45 -> 0.86)
        if moved:
            zt = self.per.top_z(obs, uv, 0.11)                        # (its walls: up to ~9.5 cm from the middle;
            if zt is None or zt < 0.035:                              # none box-high there: a look-alike)
                moved = False
        self.event("tracked the box", score_where_it_was=round(s_old, 2), best=round(sc, 2), best_px=uv.astype(int).tolist(), moved=bool(moved))
        if not moved:
            if s_old >= 0.6 or (far <= 8 and sc >= 0.45):             # (its look now: what is in it changes it)
                new = TRK.make_template(rgb, tpl["uv"], tpl["n"])
                if new is not None:
                    tpls[k] = new
            return None
        ray_new = self.per.ray_of(obs, uv)
        if ray_new[2] is None:
            return None
        p_old = self.per.seeds[k]["p"]
        dxy = np.r_[np.asarray(ray_new[2])[:2] - np.asarray(p_old)[:2], 0.0]
        if np.linalg.norm(dxy) > 0.25:                                # (not a slide: a look-alike)
            self.event("tracked the box: a look-alike far away ignored", by_mm=round(float(np.linalg.norm(dxy)) * 1000))
            return None
        for j, (sk, _) in enumerate(self.steps):
            if sk in ("in", "close", "push") and self.per.seeds[j] is not None:
                self.per.seeds[j]["p"] = np.asarray(self.per.seeds[j]["p"], float) + dxy
        if self.box is not None:
            self.box = dict(self.box, c=np.asarray(self.box["c"], float) + dxy[:2])
        if getattr(self, "last_lid", None) is not None:
            self.last_lid = dict(self.last_lid, mid=np.asarray(self.last_lid["mid"], float) + dxy, box_c=np.asarray(self.last_lid["box_c"], float) + dxy[:2])
        for nm in list(self.where):
            self.where[nm] = np.asarray(self.where[nm], float) + dxy
        new = TRK.make_template(rgb, uv, tpl["n"])
        tpls[k] = new if new is not None else dict(tpl, uv=uv)
        print(f"   the box was moved: found again {np.linalg.norm(dxy) * 100:.0f} cm away (scene camera, no new Astra call)", flush=True)
        self.event("moved: the box found again by the scene camera", by_mm=round(float(np.linalg.norm(dxy)) * 1000))
        return dxy

    def box_look_now(self):
        """After a drop (the box is where it was, with one more thing in it): its template cut again from a fresh
        scene image, so that what is in it does not make it look moved."""
        tpls = getattr(self.per, "templates", None) or []
        k = next((j for j, (sk, _) in enumerate(self.steps) if sk == "in" and self.is_box(j) and j < len(tpls) and tpls[j] is not None), None)
        if not self.TRACK or k is None:
            return
        _, rgb = self._scene()
        if rgb is not None:
            new = TRK.make_template(rgb, tpls[k]["uv"], tpls[k]["n"])
            if new is not None:
                tpls[k] = new

    def shift_survey(self, i, dxy):
        """The box moved after its survey, the hand holding the next thing (it hangs in front of the wrist camera: no
        look into the box): the survey moved with it, the box, its points, its lid and the camera of that look shifted
        by dxy, with 12 mm less room a side (the move is known to a few mm, a small turn not at all)."""
        if i not in self.targets:
            return
        box, obs = self.targets[i]
        d3 = np.r_[np.asarray(dxy, float)[:2], 0.0]
        b = dict(box, c=np.asarray(box["c"], float) + d3[:2], inside=np.asarray(box["inside"], float) + d3,
                 hu=max(box["hu"] - 0.012, 0.03), hv=max(box["hv"] - 0.012, 0.03))
        if box.get("lid") is not None:
            b["lid"] = dict(box["lid"], mid=np.asarray(box["lid"]["mid"], float) + d3)
        self.targets[i] = (b, dict(obs, eye=np.asarray(obs["eye"], float) + d3))
        self.box = b
        self.event("the box's survey moved with it (the hand is full)", by_mm=round(float(np.linalg.norm(d3)) * 1000))

    PICK_TRIES = 2

    def f_pick(self, i):
        """BoxProgram.f_pick, and if it missed or dropped the thing (still on the table: task_fast still_there), once
        more: the wrist camera over where it is now (last_left), new looks, the grasp across another direction if it
        has one (avoid_dir). (10-30: a miss or a drop ended the run.)"""
        try:
            r = super().f_pick(i)
        except F.Unseen:                                              # (moved after the scene check, on the way to it:
            if not self.track_pick(i):                                # once more where the scene camera sees it now)
                raise
            r = super().f_pick(i)
        for k in range(1, self.PICK_TRIES):
            if not r.startswith(("missed", "dropped")):
                return r
            seed = self.per.seeds[i]; keep = np.asarray(seed["p"], float).copy()
            left = getattr(self, "last_left", None)
            if left is not None:
                seed["p"] = np.r_[left, keep[2]]
            self.event("pick again", why=r, at_mm=np.round(seed["p"][:2] * 1000).tolist(),
                       not_across_deg=None if getattr(self, "last_grasp_a", None) is None else round(math.degrees(self.last_grasp_a) % 180))
            print(f"   {r}: looking again, try {k + 1} of {self.PICK_TRIES}", flush=True)
            self.avoid_dir, self.last_left = getattr(self, "last_grasp_a", None), None
            try:
                r = super().f_pick(i)
            finally:
                seed["p"] = keep; self.avoid_dir = None
        return r

    def arm_ok(self, c_, H, spot=None):
        """BoxProgram.arm_ok (the way down checked in 6 steps), then the way down exactly as f_in will move it (RealSim
        .plan_line from the carry's configuration, every DT): 10-30 twin, loose, twice, the screwdriver's diagonal spot
        passed the 6 steps, but the move's own steps swung the elbow within 3 cm of the right arm (each seeded from the
        last: another elbow than the coarse steps found), which the safety layer refuses: the run stopped there."""
        if not super().arm_ok(c_, H, spot):
            return False
        R = B.down(c_["a"])
        tcp = c_["oc"] - H["cx"] * R[:2, 0] - H["cy"] * R[:2, 1]
        _, fail, _, _ = self.S.plan_line(np.r_[tcp, self.safe_z()], R, c_["q1"], np.r_[tcp, c_["z"]], R, 1.2)   # (f_in:
        self.n_down_refused = getattr(self, "n_down_refused", 0) + (fail is not None)                          # "lower
        return fail is None                                                                                    # into the box")

    PRESS = dict(depth=0.020, depth_twin=0.010, speed=0.02, hold=0.6, min_force=1.5)   # (rig.json "press" overrides)

    def press_down(self, tcp, R, c):
        """press_skill's press on the robot, by position: the fingertip from 1.5 cm over the button's top (c, from the
        wrist depth) in one slow line (speed: twin time, m/s; the task timing runs contacts at rig speed 0.5: 10 mm/s)
        to `depth` under it, held `hold` s, then back to 2 mm over it. The arm in joint impedance gives ~0.3 N per mm
        at the hand (10-28): 20 mm under the top ~ 4-6 N once a button's travel is taken; the server's force abort
        (35 N) still applies. Not a guarded move: on this arm joints 2 and 4 stick for 1-2 s at the start of a slow
        descent, which the guard takes for a touch (f_on's note), and the guard's hold-up limit (10 mm) would cap the
        force at ~3 N. The twin backend's arm is a stiff servo: depth_twin there (10 mm: its realistic D415 model reads
        the button's top up to ~4 mm high; 6 mm left the kettle's button 1.7 mm in). -> what happened (the "pressed"
        event): the commanded depth, the peak force over the line (the server's f_max) and while held, where the tip
        got to (the measured arm)."""
        S = self.S
        pr = dict(self.PRESS, **S.rig.get("press", {}))
        twin = isinstance(S.backend, TwinArm)
        depth = min(pr["depth_twin" if twin else "depth"], c[2] + 0.003)   # (the fingertips never under the table - 3 mm)
        z0, z1 = c[2] + T.FINGERTIP + 0.015, c[2] - depth + T.FINGERTIP
        S.move(np.r_[tcp, z0], R, 0.0, 0.8, "onto the button")
        S.hold(0.3)
        f0 = S.force_now()
        S.move(np.r_[tcp, z1], R, 0.0, (z0 - z1) / pr["speed"], "press the button")
        r = S.flush() or {}
        S.hold(pr["hold"])
        f1 = S.force_now(); tip = float(S.tcp_now()[2]) - T.FINGERTIP
        out = dict(depth_mm=round(depth * 1000, 1), f_max=None if r.get("f_max") is None else round(float(r["f_max"]), 1),
                   f_hold=round(float(np.linalg.norm(f1 - f0)), 1), tip_in_mm=round((c[2] - tip) * 1000, 1))
        if max(out["f_hold"], out["f_max"] or 0.0) < pr["min_force"]:
            out["note"] = "nothing felt under the fingertip"
            print(f"   press: nothing felt under the fingertip (peak {out['f_max']} N): missed the button?", flush=True)
        S.move(np.r_[tcp, c[2] + T.FINGERTIP + 0.002], R, 0.0, 1.0, "off the button")
        return out

    def hand_force(self):
        """(Not used by the box task on the robot: put_on uses a guarded move.) The force estimate's size, N."""
        return float(np.linalg.norm(self.S.force_now()))

    def f_on(self, i):
        """BoxProgram.f_on, the set-down by position: the held thing's bottom to rig.json contact.set_down_clear (3 mm)
        over the top the wrist camera measured, then let go. (Not a guarded move: on the real arm joints 2 and 4 stick
        for 1-2 s at the start of a slow descent, which a contact guard cannot tell from a touch: 10-28, touch stopped
        8-15 cm over the table. A few mm of error is taken by the arm's softness, ~0.3 N per mm.)"""
        S, H = self.S, self.held
        X, obs, zmax = self.targets[i]
        if H.get("round"):
            sp = TB.Spot(dict(c=X[:2], u=np.r_[1.0, 0], v=np.r_[0, 1.0], hu=0.2, hv=0.2, table=0.0), obs, zmax)
            z_rel = X[2] + 0.001 + H["grip"]
            q = H["width"] / 2 + 0.006
            best = min(np.radians(np.arange(0, 180, 15.0)), key=lambda a: sp.fingers_hit(X[:2], a, z_rel, q))
            self.event("hand turned for the fingers", a_deg=math.degrees(best), hits=sp.fingers_hit(X[:2], best, z_rel, q))
            S.move(np.r_[S.p_cmd[:2], max(self.safe_z(), S.p_cmd[2])], S.R_cmd, 0.0, 0.5, "up")
            Rn = self._turn_hand(best)
            self.turn_to(math.atan2(Rn[1, 1], Rn[0, 1]), 0.0)
        self.transit(self._over(X, 0.06)[:2], H["R"])
        S.move(self._over(X, 0.06), H["R"], 0.0, 1.5, "carry over")
        S.move(self._over(X, 0.012), H["R"], 0.0, 0.8, "down to it")         # (settled: the sag taken out, kept after)
        clear = float(S.rig.get("contact", {}).get("set_down_clear", 0.003))
        S.move(self._over(X, clear), H["R"], 0.0, 1.2, "set down")
        self.event("set down by position", clear_mm=round(clear * 1000, 1), tcp_mm=np.round(S.p_cmd * 1000, 1).tolist())
        rel = S.p_cmd + [0, 0, 0.001]
        S.move(rel, H["R"], 0.0, 0.15, "unload")
        g_rel = min(B.OPEN_W, H["width"] / 2 + 0.006)
        S.move(rel, H["R"], g_rel, 0.5, "release"); S.hold(0.3)
        S.move(rel + [0, 0, 0.07], H["R"], g_rel, 0.6, "retreat")
        S.move(rel + [0, 0, 0.10], H["R"], B.OPEN_W, 0.4); S.hold(0.3)
        self.where[H["name"]] = np.r_[X[:2], X[2] + H["h"]]
        self.held = None
        return "ok"


# ---------------------------------------------------------------- the clutter pick (11-03, clutter_bench.py; run --pick seg)
class RealClutterPerception(RealPerception):
    """RealPerception for the clutter pick: Astra's plan with clutter_bench's prompt (pick / put_in / put_down; the
    target is found again by its name with SAM3 in the wrist camera's colour image, the mark choosing among the
    matches), the wrist camera's colour looks, SAM3 clicks (sam3_points_server.py :8134), and the scene camera's depth
    with the robot's own pixels marked and the force estimate at that moment (the checks of what came up with the
    hand: ClutterProgram.risen / hanging / hand_load)."""
    REAL_DEPTH = True                         # (a real D415's edge spread: clutter_bench.UNSPREAD takes it back)
    plan_astra = CB.ClutterPerception.plan_astra
    segment_points = CB.ClutterPerception.segment_points

    def __init__(self, S, rng, out, tag, plan_gt=None):
        super().__init__(S, rng, out, tag, plan_gt)
        self._rr, self._arm, self.forces = None, None, []

    def plan(self, feedback=None):
        if self.gt:
            self.steps, self.seeds = self._plan_gt()
            self.astra_log.append(dict(t=float(self.S.d.time), keys=["plan (true state, twin)"], rounds=[dict(reply="", s=0.0)], final=None, img=None))
            return self.steps
        return self.plan_astra()

    def wrist_rgbd(self):
        return self.observe("L_wrist")

    SCENE_FRAMES = 5         # (the checks compare two scene looks: one frame each, the D415's noise on the black table
                             # at a glancing angle (5-8 mm, 10-27) made "risen" blobs all over it (11-03 dry runs: 4 of
                             # 14 "something stuck on the fingers"); the median of 5 frames, ~0.2 s)

    def scene_depth(self):
        o = self.observe("d415")
        if self.SCENE_FRAMES > 1:
            zs = [o["depth"]] + [self.observe("d415")["depth"] for _ in range(self.SCENE_FRAMES - 1)]
            Z = np.stack([np.where(np.isfinite(z), z, np.nan) for z in zs])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)       # (all-NaN pixels: no depth in any frame)
                med = np.nanmedian(Z, axis=0)
            o = dict(o, depth=np.where(np.isfinite(Z).sum(0) * 2 > len(zs), med, np.inf).astype(np.float32))
        o["arm"] = self.arm_pixels(o)
        self.forces.append((np.asarray(self.S.force_now(), float), bool(self.S.g_cmd > 0.005)))   # (+ the hand open?)
        return o

    def arm_pixels(self, o, grow=6):
        """The robot's own pixels in a scene-camera look: the mirror's arms at the measured joints, drawn from the
        camera's pose the program uses (as a real system renders its URDF), grown by `grow` px (MuJoCo's camera: one
        focal length, a centred principal point: shifted to the real one)."""
        S, m, mj = self.S, self.S.m, self.S.mj
        W, H, fx, fy, cx, cy = o["K"]; W, H = int(W), int(H)
        if self._rr is None:
            m.vis.global_.offwidth = max(m.vis.global_.offwidth, W); m.vis.global_.offheight = max(m.vis.global_.offheight, H)
            self._rr = mj.Renderer(m, H, W)
            names = [m.body(b).name for b in range(m.nbody)]
            self._arm = np.array([names[b].startswith(("L_", "R_")) for b in m.geom_bodyid])
        cid = m.camera("d415").id
        keep = (m.cam_pos[cid].copy(), m.cam_quat[cid].copy(), float(m.cam_fovy[cid]))
        b = m.cam_bodyid[cid]
        Rb, pb = S.d.xmat[b].reshape(3, 3), S.d.xpos[b]
        q = np.zeros(4); mj.mju_mat2Quat(q, (Rb.T @ np.asarray(o["R"]) @ np.diag([1.0, -1.0, -1.0])).flatten())
        m.cam_pos[cid] = Rb.T @ (np.asarray(o["eye"], float) - pb); m.cam_quat[cid] = q
        m.cam_fovy[cid] = math.degrees(2 * math.atan(H / 2 / fy))
        try:
            mj.mj_forward(m, S.d)
            opt = mj.MjvOption(); opt.sitegroup[:] = 0
            rr = self._rr
            rr.enable_segmentation_rendering(); rr.update_scene(S.d, camera=cid, scene_option=opt); seg = rr.render().copy()
            rr.disable_segmentation_rendering()
            rr.enable_depth_rendering(); rr.update_scene(S.d, camera=cid, scene_option=opt); dep = rr.render().copy()
            rr.disable_depth_rendering()
        finally:
            m.cam_pos[cid], m.cam_quat[cid], m.cam_fovy[cid] = keep
            mj.mj_forward(m, S.d)
        gid = np.clip(seg[..., 0], 0, m.ngeom - 1)
        arm = (seg[..., 1] == int(mj.mjtObj.mjOBJ_GEOM)) & (seg[..., 0] >= 0) & self._arm[gid]
        sy, sx = int(round(cy - H / 2)), int(round(cx - W / 2))
        dy, dx, fit = self.register_arm(arm, dep, o["depth"], sy, sx)
        o["arm_shift_px"], o["arm_fit"] = (dy, dx), fit
        arm = np.roll(arm, (sy + dy, sx + dx), (0, 1))
        return cv2.dilate(arm.astype(np.uint8), np.ones((2 * grow + 1, 2 * grow + 1), np.uint8)) > 0

    @staticmethod
    def register_arm(arm, dep, z, sy, sx, r=16, n=2500):
        """The image shift (dy, dx) within +-r px that best lays the drawn arm (its depth dep) on the measured depth z:
        the fraction of its pixels within 15 mm. The camera's pose is off by its calibration error (twin: 5 mm / 1 deg;
        the real scene camera: 1-2 cm, 10-29): unshifted, the arm's edges read as something that came up (11-03 dry
        runs: "something stuck on the fingers", 4 of 14). -> (dy, dx, fraction) ((0, 0) if under 0.3)"""
        ys, xs = np.nonzero(arm)
        if len(ys) < 50:
            return 0, 0, None
        k = np.random.default_rng(0).choice(len(ys), min(n, len(ys)), replace=False)
        ys, xs = ys[k], xs[k]; zr = dep[ys, xs]
        H, W = z.shape

        def score(dy, dx):
            yy, xx = ys + sy + dy, xs + sx + dx
            ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            zm = z[yy[ok], xx[ok]]
            return float((np.abs(zm - zr[ok]) < 0.015).sum()) / len(ys)
        best = max(((score(a, b), a, b) for a in range(-r, r + 1, 2) for b in range(-r, r + 1, 2)))
        best = max(((score(a, b), a, b) for a in range(best[1] - 1, best[1] + 2) for b in range(best[2] - 1, best[2] + 2)))
        if best[0] < 0.3:
            return 0, 0, round(best[0], 2)
        return best[1], best[2], round(best[0], 2)


class RealClutterProgram(CB.ClutterProgram, RealBoxProgram):
    """clutter_bench.ClutterProgram (the SAM3 pick: drags things out, sets blockers aside, checks what came up) on the
    robot, with RealBoxProgram's safety and timing (the right-arm clearance, joint transits, flushes). Its contacts by
    POSITION, as f_on and the press (the real arm's force estimate cannot tell a touch from stiction, 10-28): a drag
    presses the closed fingertips DRAG depth under the thing's measured top, a put-down sets the thing's bottom
    contact.set_down_clear over the table. The load check (something heavy came up: the box) compares the force
    estimate before and after the 2.5 cm lift, LOAD_EXTRA N less sensitive than the twin's 2 N."""
    DRAG = dict(depth=0.008, depth_twin=0.004, hold=0.3)   # (rig.json "drag" overrides) the robot: 8 mm under the top
    # at ~0.3 N/mm (10-28) ~ 2-3 N on the thing, enough to slide a tape or the block (the realistic D415 reads tops
    # 2-4 mm high, press_skill 11-02); the twin backend's arm is a stiff servo: 4 mm (the sim's own drag: 2 N + 3 mm)
    LOAD_EXTRA = 3.0               # (11-05: 1.0 -> 3.0: the load check fires at 2 + this, the real estimate's drift)
    RISE_DZ = 0.015                # (11-05, real run 17:47: two good grasps of the yellow tape set back down and one
    RISE_OPEN = 5                  # drag ended "something stuck on the fingers": the real scene camera's depth noise
    ARM_GROW = 8                   # on the black table. Replayed on its frames: 15 mm + a 5 px opening: 0 false pixels)
    COLOUR_RULES = {}              # (11-03: no colour check on the robot. The wrist D415 overexposes the yellow tape
    # (V 255): its saturation read 68-92 at 15:58 and 22-44 at 17:38, same tape, same pile; the twin's rule (S >= 120)
    # and then S >= 50 both rejected it ("not found", the block taken away instead). SAM3's score for the name (0.92-0.95)
    # and Astra's mark choose the target; a colour check needs the camera's exposure fixed first)
    LOCAL_ASIDE = True             # (11-03 user: things in the way moved a few cm, low: ClutterProgram.take_away_near)
    DRAG_TURN_W = 20.0             # (a drag's direction: + 20 per rad of wrist turn)
    BLOCKED = 0.006                # (a descent that ended this far over its goal: a finger on something, 11-03 dry runs
                                   # 8 of 16 fails: the arm 2-3 cm short, the next move refused; the robot's block grasp)
    MAX_TURN = math.radians(100)   # (11-03: a cable grasp turned the wrist 112 deg, joint 7 to its limit, the elbow
    J7_MARGIN = 0.3                # twisted (joints 1 / 3 / 5 by ~0.9 rad); the same grasp turned the other way round
    MAX_TWIST = 0.7                # needed almost none: configurations past these are refused, the next one is taken)
    MIN_ROOM = 0.005               # (each side between the opened finger and the thing: the 71 mm block in the 80 mm
    # gripper (4.5 mm) missed twice on the robot, 10-29 and 11-03: hand-eye error ~3-4 mm; it is dragged instead)
    if CB.TIGHT:                   # (11-05, CLUTTER_TIGHT=1, user: "about 0.8 cm gap should be enough": its 3 mm level;
        MIN_ROOM = 0.0025          # a finger landing on the thing's edge stops the descent: descent_blocked, another)

    def __init__(self, S, per, confirm_plan=False):
        CB.ClutterProgram.__init__(self, S, per, "seg", None)
        self.confirm_plan = confirm_plan
        if isinstance(S.backend, TwinArm):                             # (the twin's cameras: its renders' colours)
            self.COLOUR_RULES = CB.COLOUR_RULES

    def run(self, stop_after=None):
        """ClutterProgram.run on the robot: the plan (one Astra call), shown for approval, then the steps (pick: the
        clutter pick; in: over the box's middle; down: a free spot on the table). stop_after: only the first N steps
        (a held thing is put back where it was picked)."""
        self.targets, self.avoid, self.box = {}, {}, None
        steps = self.per.plan()
        if not steps:
            return "plan: no usable steps"
        self.steps = steps
        self.event("plan", steps=[f"{s}:{o}" for s, o in steps])
        print("plan:", "; ".join(f"{i} {s} {o}" for i, (s, o) in enumerate(steps)), flush=True)
        if self.confirm_plan:
            print(f"   (Astra's marks: {self.per.dir / (self.per.tag + '_plan.jpg')})", flush=True)
            if input("   go ahead with this plan? [Enter = yes, n = no] ").strip().lower() not in ("", "y", "yes"):
                return "plan declined"
        self.sizes = {}
        self.reserved = [self.per.seeds[j]["p"][:2] for j, (sk, _) in enumerate(steps) if sk == "down"]
        n_steps = len(steps) if stop_after is None else min(stop_after, len(steps))
        for i, (sk, obj) in enumerate(steps[:n_steps]):
            print(f"-- step {i}: {sk} {obj}", flush=True)
            try:
                if sk == "pick" and i + 1 < len(steps) and steps[i + 1][0] in ("on", "in"):
                    self.survey(i + 1)
                r = getattr(self, "f_" + sk)(i)
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

    def config_ok(self, q):
        """A configuration without a big wrist turn (joint 7 by <= MAX_TURN, >= J7_MARGIN from its limits) or an elbow
        twist (joints 1-6 by <= MAX_TWIST) from the present one."""
        S = self.S
        dq = np.abs(np.asarray(q) - S.q_cmd)
        return bool(dq[6] <= self.MAX_TURN and S.lo[6] + self.J7_MARGIN <= q[6] <= S.hi[6] - self.J7_MARGIN and dq[:6].max() <= self.MAX_TWIST)

    def descent_blocked(self, z_goal, R, q):
        """The descent ended > BLOCKED over its goal (the arm measured): a finger on something. The command is put where
        the arm is (RealSim.resync: the next move starts there, not at the goal the arm could not reach), the hand
        goes 3 cm up, and the caller takes another grasp (pick_seg: not across the same direction)."""
        S = self.S
        short = float(S.tcp_now()[2] - z_goal)
        if short <= self.BLOCKED:
            return False
        S.resync()
        self.event("blocked on the way down: back up, another grasp", short_mm=round(short * 1000, 1))
        S.move(S.p_cmd + [0, 0, 0.03], R, q, 0.6, "back up")
        return True

    def grasp_allowed(self, c):
        """A grasp the safety layer will take: the fingertips at least 2 mm over the table (z 0; the wrist depth can
        read the table a few mm low, and the safety layer refuses fingertips under -4 mm: 11-03 dry runs, 2 of 14),
        then its configuration within the rig's limits (joints, the hand and links >= 5 cm over the table, the
        right arm's keep-out)."""
        S = self.S
        if c["q"] - c["w"] / 2 < self.MIN_ROOM:
            return False
        tip_min = T.FINGERTIP + 0.002
        if c["z"] < tip_min:
            self.event("grasp raised over the table", z_mm=round(c["z"] * 1000, 1), to_mm=round(tip_min * 1000, 1))
            c["depth"] -= tip_min - c["z"]; c["z"] = tip_min
            if c["depth"] < min(0.006, 0.5 * c["lh"]):
                return False
        R = B.down(c["a"])                                             # the way down exactly as grasp() moves it (11-03:
        z_hi = max(S.p_cmd[2], c["top"] + 0.10)                        # a descent the straight line could not do went
        while z_hi > c["top"] + 0.06 and S.ik(np.r_[c["c"], z_hi], R, S.q_cmd)[1] > 0.002:   # in joint space to an IK
            z_hi -= 0.02                                               # seeded at home, another elbow: the fingertips
        q_hi, e = S.ik(np.r_[c["c"], z_hi], R, S.q_cmd)                # swept under the table, refused)
        why = "unreachable" if e > 0.002 else (None if self.config_ok(q_hi) else "a big wrist turn / elbow twist")
        if why is None:
            Q, fail, _, _ = S.plan_line(np.r_[c["c"], z_hi], R, q_hi, np.r_[c["c"], c["z"]], R, 1.2)
            why = "no straight way down" if fail is not None else S.lim.check_q(Q[-1])
        if why:
            self.event("grasp skipped: the safety layer would refuse it", why=why)
        return why is None

    def put_back(self):
        """Lower the held thing to where it was picked (its bottom 5 mm over what it stood on: in a box, the box's
        floor) and let go."""
        S, H = self.S, self.held
        sup = float(getattr(self, "last_g", {}).get("table", 0.0))
        S.move(np.r_[S.p_cmd[:2], sup + 0.005 - H["bottom_off"]], H["R"], 0.0, 1.5, "put it back down")
        S.move(S.p_cmd, H["R"], min(B.OPEN_W, H["width"] / 2 + 0.01), 0.5, "release")
        S.hold(0.3)
        S.move(S.p_cmd + [0, 0, 0.10], H["R"], B.OPEN_W, 0.8, "retreat")
        S.hold(0.2)
        S.flush()
        self.held = None

    def press_depth(self):
        """The drag's press under the thing's top: DRAG depth (rig.json "drag" overrides; the stiff twin: depth_twin)."""
        dr = dict(self.DRAG, **self.S.rig.get("drag", {}))
        stiff = isinstance(self.S.backend, TwinArm) and not self.S.backend.compliant   # (TWIN_COMPLIANT=1: as the robot)
        return dr["depth_twin" if stiff else "depth"]

    def press_closed(self, cmd, R, top):
        """ClutterProgram.press_closed by position: the closed fingertips DRAG depth under the thing's top (never under
        the table - 3 mm), held, in one move."""
        S = self.S
        dr = dict(self.DRAG, **S.rig.get("drag", {}))
        depth = self.press_depth()
        cmd = np.asarray(cmd, float).copy()
        cmd[2] = max(top + T.FINGERTIP - depth, T.FINGERTIP - 0.003)
        S.move(cmd, R, 0.0, 0.8, "press"); S.hold(dr["hold"])
        self.event("press by position", depth_mm=round(depth * 1000, 1), top_mm=round(top * 1000, 1))
        return cmd

    def press_blocked(self, cmd, R, top):
        """(11-04) The drag's press: the hand measured > BLOCKED over the fingertips' touch of the thing's top: the
        closed fingers (or the hand) on something else (the twin with the real arm's softness: on a fallen glue bottle
        beside the tape, the arm held 5-7 cm up; the drag then pushed the glue 6 cm and the next move was refused, 4 of
        24 piles). The command is put where the arm is, the hand 3 cm up: no drag."""
        S = self.S
        short = float(S.tcp_now()[2] - (top + T.FINGERTIP))
        if short <= self.BLOCKED:
            return False
        S.resync()
        self.event("drag: the press is held up (on something else): no drag", short_mm=round(short * 1000, 1))
        S.move(S.p_cmd + [0, 0, 0.03], R, 0.0, 0.6, "back up")
        return True

    SLIDE_LAG = 0.020              # (11-04) a slide's step: the arm > 20 mm behind along the way = what it pushes is stuck
                                   # (~15-20 N at the arm's gains; 10 mm stopped drags that were moving, the 35 N stop ~40 mm)

    def slide(self, cmd, d, D, R, phase):
        """(11-04) A drag's / push's slide in 1 cm steps, each measured: where the arm stays > SLIDE_LAG behind along
        the way (what it pushes is stuck), it stops there, the command put where the arm is (11-04, the twin with the
        real arm's softness: a drag held back built 35-37 N, the server's force stop ended the run, 4 of 16 piles).
        -> the distance slid"""
        S = self.S
        p0 = np.asarray(cmd, float).copy()
        S.resync()                                                     # (11-04: after the press the soft arm's elbow
        # drifts ~0.05 rad (joint 2) with the hand nearly still: the server refused the slide's start, > start_tol;
        # from the measured joints, the steps' goals keep the press height)
        n = max(1, int(math.ceil(D / 0.01 - 1e-9)))
        for k in range(1, n + 1):
            S.move(p0 + np.r_[D * k / n * d, 0.0], R, 0.0, max(0.3, 2.0 * D / 0.06 / n), phase)
            behind = float((S.p_cmd[:2] - S.tcp_now()[:2]) @ d)
            if behind > self.SLIDE_LAG:
                S.resync()
                done = max(0.0, float((S.p_cmd[:2] - p0[:2]) @ d))
                self.event(f"{phase}: held back, stopped there", after_mm=round(done * 1000), behind_mm=round(behind * 1000, 1))
                return done
            if float(np.abs(np.asarray(S.last_state["q"], float) - S.q_sent).max()) > 0.03:
                S.resync()                                             # (the next step from the measured joints: 11-04,
                # the 4th step refused, joint 2 0.058 rad behind with the hand < 20 mm behind)
        return D

    def after_drag(self):
        """(11-04) After a drag or a push: the arm measured; if it stayed behind its goal (> 5 mm: what it pushed held
        it), the command is put where it is (the next move starts there: the server refuses a start > start_tol from
        the arm, 11-04 twin dry runs)."""
        S = self.S
        lag = float(np.linalg.norm(S.tcp_now() - S.p_cmd))
        if lag > 0.005:
            self.event("drag: the arm stayed behind", lag_mm=round(lag * 1000, 1))
            S.resync()
        for _ in range(int(round((self.press_depth() + 0.002) / 0.002))):   # unload in 2 mm steps, each its own move
            S.move(S.p_cmd + [0, 0, 0.002], S.R_cmd, S.g_cmd, 0.15, "unload")   # (the server's force stop compares
            S.flush()                                          # with each move's start: 11-04, a drag's load let go in
                                                               # one move read 35-40 N, 5 of 72 twin runs stopped)

    def put_down(self, X, what, transit=True):
        """ClutterProgram.put_down by position (f_on's way): the held thing's bottom contact.set_down_clear (3 mm) over
        the table point X, then let go (transit=False: from where the hand is, carried there low)."""
        S, H = self.S, self.held
        gh = H.get("hold", 0.0)
        g_rel, g_after = (0.0, 0.0) if H.get("internal") else (min(B.OPEN_W, H["width"] / 2 + 0.008), B.OPEN_W)
        if transit:
            self.transit(self._over(X, 0.06)[:2], H["R"])
            S.move(self._over(X, 0.06), H["R"], gh, 1.2, what)
        S.move(self._over(X, 0.012), H["R"], gh, 0.6, "down to it")
        clear = float(S.rig.get("contact", {}).get("set_down_clear", 0.003))
        S.move(self._over(X, clear), H["R"], gh, 1.0, "set down")
        self.event("set down by position", clear_mm=round(clear * 1000, 1), tcp_mm=np.round(S.p_cmd * 1000, 1).tolist())
        rel = S.p_cmd + [0, 0, 0.001]
        S.move(rel, H["R"], gh, 0.15, "unload")
        S.move(rel, H["R"], g_rel, 0.4, "release"); S.hold(0.2)
        S.move(rel + [0, 0, 0.10], H["R"], g_after, 0.8, "retreat")
        self.held = None

    def hand_load(self):
        """The weight at the hand: the force estimate's vertical part at the last scene look (lifted 2.5 cm) against
        the last one with the hand open (the grasp's first look, in the air: 11-03, against the look just before the
        lift, the closed fingers' push on the table read as 4.4 N of load), less LOAD_EXTRA (the estimate's noise
        between two poses). 0 without an open-hand look."""
        f = getattr(self.per, "forces", [])
        base = next((x for x, is_open in reversed(f[:-1]) if is_open), None)
        if not f or base is None:
            return 0.0
        return max(0.0, abs(float(f[-1][0][2] - base[2])) - self.LOAD_EXTRA)


class TwinCamsRGB(TwinCams):
    """TwinCams with the wrist camera's colour image too (the clutter pick segments it): the world's photoreal render."""

    def get(self, cam, after=0.0, timeout=2.0):
        o = B.Perception.observe(self.wper, cam) if cam == "L_wrist" else self.wper.observe(cam)
        return dict(rgb=o["rgb"], depth=o["depth"], K=tuple(o["K"]), t=float(self.wper.S.d.time))


# ---------------------------------------------------------------- setups
def make_rig(a, out, tag):
    """-> (S, per, world, wper, layout): RealSim over the robot (arm server + RealSense) or over the twin."""
    rig = load_rig(); calib = load_calib() if a.backend == "robot" else {}   # (the twin has no adapter, its own mounts)
    if a.backend == "robot":
        arm = RemoteArm(rig["server"]["host"], a.port or rig["server"]["port"])
        hi = arm.hello()
        if not hi.get("ok"):
            raise SystemExit(f"arm server: {hi}")
        print(f"arm server: {'FAKE arm' if hi.get('fake') else 'robot'}, motion {'allowed' if hi.get('allow_motion') else 'NOT allowed (read-only)'}"
              f"{', each confirmed there' if hi.get('confirm') else ''}; its log {hi.get('log')}", flush=True)
        cams = RealSenseCams(rig) if not a.no_cameras else None
        S = RealSim(arm, cams, rig, calib, speed=a.speed, v_max=a.v_max, log_dir=out)
        S.server_log = hi.get("log")
        start_recording(a, S, tag, rig)
        return S, arm, None, None, None
    os.environ.setdefault("D415_DEPTH", "real")                       # (the world's cameras: the fitted D415 depth)
    if getattr(a, "clutter", None):                                   # (11-03: clutter_bench's pile / bin layouts)
        L = CB.sample_layout(a.clutter, a.seed, a.layout)
        if L is None:
            raise SystemExit(f"clutter layout {a.layout} rejected")
        world = TB.BoxSim(); CB.apply_layout(world, L)
        wper = CB.ClutterPerception(world, np.random.default_rng([a.seed, a.layout, 5]), out, tag + "_world", L)
        cams = TwinCamsRGB(wper)
    else:
        L = TB.sample_layout(a.seed, a.layout)
        if L is None:
            raise SystemExit(f"layout {a.layout} rejected")
        world = TB.BoxSim(); TB.apply_layout(world, L)
        wper = TB.BoxPerception(world, np.random.default_rng([a.seed, a.layout, 5]), out, tag + "_world")
        cams = TwinCamsRGB(wper) if getattr(a, "pick", "box") == "seg" else TwinCams(wper)
    arm = TwinArm(world, rig, None)
    if getattr(a, "clutter", None):                                   # (TWIN_REC_VIEW=close: the video's view, between
        pt = world.d.xpos[world.m.body("obj_" + L["target"]).id]       # the target and the white box)
        pb = world.d.xpos[world.m.body("obj_box_white").id]
        arm.rec_lookat = (np.r_[0.65 * pt[:2] + 0.35 * pb[:2], 0.04], 0.5 + 0.7 * float(np.linalg.norm(pt[:2] - pb[:2])))
    S = RealSim(arm, cams, rig, calib, speed=a.speed if a.speed is not None else 1.0, v_max=a.v_max, log_dir=out)
    S.server_log = None
    start_recording(a, S, tag, rig)
    return S, arm, world, wper, L


def start_recording(a, S, tag, rig):
    ACTIVE.append(S)
    if tag in ("check", "scene") or getattr(a, "no_record", False):    # (no motion: nothing to record)
        return
    r = rig.get("record", {})
    rd = getattr(a, "record_depth", None)
    fps, dfps = float(r.get("fps", 15)), float(rd if rd is not None else r.get("depth_fps", 0))
    S.start_recording(fps, dfps, int(r.get("crf", 23)))
    print(f"recording both cameras at {fps:g} fps{f' (+ depth at {dfps:g} fps)' if dfps > 0 else ''}: {S.log_dir / 'video'}", flush=True)


def out_dir(a, cmd):
    d = OUT / f"{cmd}_{a.backend}{'_L%d' % a.layout if a.backend == 'twin' else ''}_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- check (no motion)
def plane_fit(P, tol=0.004, iters=300, rng=None):
    """Dominant plane of points P [n, 3] (RANSAC, then least squares on its inliers): unit normal n, offset c (n.x = c)."""
    rng = np.random.default_rng(0) if rng is None else rng
    best = (0, None)
    for _ in range(iters):
        a, b, c = P[rng.choice(len(P), 3, replace=False)]
        n = np.cross(b - a, c - a); nn = np.linalg.norm(n)
        if nn < 1e-9:
            continue
        n /= nn
        k = int((np.abs(P @ n - n @ a) < tol).sum())
        if k > best[0]:
            best = (k, (n, float(n @ a)))
    n, c = best[1]
    inl = np.abs(P @ n - c) < tol
    Q = P[inl]; mu = Q.mean(0)
    w, V = np.linalg.eigh(np.cov((Q - mu).T))
    n = V[:, 0] * (1 if V[2, 0] >= 0 else -1)
    return n, float(n @ mu), inl


def cam_points(obs, step=4, zmax=2.5):
    """Camera-frame points of a depth image (every step-th pixel)."""
    W, Hh, fx, fy, cx, cy = obs["K"]
    v, u = np.mgrid[0:int(Hh):step, 0:int(W):step]
    z = obs["depth"][::step, ::step]
    ok = np.isfinite(z) & (z > 0.1) & (z < zmax)
    return np.stack([(u[ok] - cx) / fx * z[ok], (v[ok] - cy) / fy * z[ok], z[ok]], 1)


def cmd_check(a):
    """No motion. Arm: joints, modes, libfranka's own end-effector pose vs our kinematics. Wrist camera: the table's
    plane from its depth vs where the kinematics + mount put it (-> the adapter length). Scene camera: the table's
    plane in the world (-> its pose), the hand drawn where the kinematics say. Both cameras: an object under the wrist
    camera, located by each."""
    out = out_dir(a, "check")
    S, arm, world, wper, L = make_rig(a, out, "check")
    rep = {}
    st = S.sync()
    q = np.asarray(st["q"])
    print(f"\n== arm\n q = {np.round(q, 4).tolist()}\n mode {st.get('mode')} errors {st.get('errors') or 'none'}; gripper "
          f"{'?' if st.get('width') is None else '%.1f mm' % (st['width'] * 1000)}")
    why = S.lim.check_q(q)
    print(f" inside the safety limits: {'yes' if why is None else 'NO: ' + why}")
    dh = np.abs(q - S.q_home)
    print(f" from the twin's home: max {dh.max():.3f} rad (joint {int(np.argmax(dh)) + 1})")
    tcp = S.kin.tcp_world(q)
    print(f" TCP (world, our kinematics): {np.round(tcp[:3, 3] * 1000, 1).tolist()} mm, hand axis {np.round(tcp[:3, 2], 3).tolist()}")
    rep["arm"] = dict(q=q.tolist(), mode=st.get("mode"), errors=st.get("errors"), width=st.get("width"), limits=why,
                      tcp=tcp[:3, 3].tolist())
    if st.get("O_T_EE") is not None:
        O = np.asarray(st["O_T_EE"]).reshape(4, 4).T                   # (column-major)
        Fb = S.kin.flange_base(q)
        F_T_EE = np.asarray(st.get("F_T_EE", np.eye(4).flatten())).reshape(4, 4).T
        EEf = np.linalg.inv(Fb) @ O                                    # libfranka's EE in our flange frame
        print(f" libfranka's O_T_EE vs our flange: offset {np.round(EEf[:3, 3] * 1000, 1).tolist()} mm, rotation "
              f"{math.degrees(math.acos(np.clip((np.trace(EEf[:3, :3]) - 1) / 2, -1, 1))):.1f} deg "
              f"(= the end effector set in Desk: F_T_EE offset {np.round(F_T_EE[:3, 3] * 1000, 1).tolist()} mm; mass m_ee {st.get('m_ee')} kg)")
        rep["arm"].update(O_T_EE=O.tolist(), flange_vs_O_T_EE=EEf.tolist(), F_T_EE=F_T_EE.tolist(), m_ee=st.get("m_ee"))
    if S.cams is None:
        json.dump(rep, open(out / "check.json", "w"), default=float, indent=1); return
    # wrist camera: the table plane
    ow = S.observe_frame("L_wrist")
    Pc = cam_points(ow)
    n_c, c_c, inl = plane_fit(Pc)
    h_meas = abs(c_c)                                                  # camera to the table plane, along its normal
    tilt_meas = math.degrees(math.acos(min(1.0, abs(n_c[2]))))
    R, eye = ow["R"], ow["eye"]
    h_fk = eye[2] - S.rig["safety"]["table_z"]
    tilt_fk = math.degrees(math.acos(min(1.0, abs(R[2, 2]))))
    Pw = Pc @ R.T + eye                                                # its points in the world (kinematics + mount)
    zt = Pw[inl, 2]
    print(f"\n== wrist camera ({len(Pc)} points, {inl.mean() * 100:.0f} % on the table plane)")
    print(f" height over the table: depth {h_meas * 1000:.1f} mm, kinematics + mount {h_fk * 1000:.1f} mm -> "
          f"the wrist camera is {abs(h_fk - h_meas) * 1000:.1f} mm {'LOWER' if h_fk > h_meas else 'HIGHER'} than the model has it (hand or camera mount)"
          f"{' (an adapter? put ~%.0f mm in calib.json tool.adapter_dz, then check again)' % ((h_fk - h_meas) * 1000) if h_fk - h_meas > 0.005 else ''}")
    print(f" optical axis to the table's normal: depth {tilt_meas:.1f} deg, kinematics {tilt_fk:.1f} deg")
    print(f" the table seen through the kinematics: z {np.median(zt) * 1000:+.1f} mm (should be ~0), spread {np.ptp(np.percentile(zt, [5, 95])) * 1000:.1f} mm")
    rep["wrist"] = dict(h_depth=h_meas, h_kin=h_fk, adapter_hint=h_fk - h_meas, tilt_depth=tilt_meas, tilt_kin=tilt_fk,
                        table_z=float(np.median(zt)), K=ow["K"])
    # scene camera: the table plane in the world; the hand drawn into its image
    osc = S.observe_frame("d415")
    Ps = cam_points(osc, step=4, zmax=2.0) @ osc["R"].T + osc["eye"]
    near = np.abs(Ps[:, 2]) < 0.05
    n_s, c_s, inl_s = plane_fit(Ps[near])
    Pt = Ps[near][inl_s]
    print(f"\n== scene camera ({near.sum()} points near the table height, {inl_s.mean() * 100:.0f} % on the plane)")
    print(f" the table in the world: z {np.median(Pt[:, 2]) * 1000:+.1f} mm (should be ~0), tilt {math.degrees(math.acos(min(1.0, abs(n_s[2])))):.2f} deg")
    rep["scene"] = dict(table_z=float(np.median(Pt[:, 2])), tilt_deg=math.degrees(math.acos(min(1.0, abs(n_s[2])))), K=osc["K"])
    img = osc["rgb"].copy() if osc["rgb"] is not None else np.zeros((480, 640, 3), np.uint8)
    H = S.kin.hand_world(q)
    pts = {"TCP": S.kin.tcp_world(q)[:3, 3], "hand": H[:3, 3], "base": S.kin.T_wb[:3, 3],
           "wrist cam": eye}
    for sy in (-0.106, 0.106):
        for sx in (-0.032, 0.032):
            pts[f"hand {sx:+.2f},{sy:+.2f}"] = (H @ np.r_[sx, sy, S.kin.tcp_z - 0.11, 1.0])[:3]
    for k, X in pts.items():
        u, v = F.project(osc, X)
        if 0 <= u < img.shape[1] and 0 <= v < img.shape[0]:
            col = (255, 0, 0) if k == "TCP" else (0, 255, 255) if k.startswith("hand") else (255, 0, 255)
            cv2.drawMarker(img, (int(u), int(v)), col, cv2.MARKER_CROSS if k != "TCP" else cv2.MARKER_TILTED_CROSS, 14, 2)
            if not k.startswith("hand "):
                cv2.putText(img, k, (int(u) + 6, int(v) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
    cv2.imwrite(str(out / "check_scene.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    print(f" the hand as the kinematics place it, drawn on the scene image: {out / 'check_scene.png'} (red X = TCP, cyan = the hand's corners)")
    # both cameras: whatever stands under the wrist camera
    Pw_all = Pc @ R.T + eye
    up = Pw_all[:, 2] > 0.012
    if up.sum() > 50:
        Pu = Pw_all[up]
        c_w = np.median(Pu[:, :2], 0); top_w = np.percentile(Pu[:, 2], 95)
        Ps_all = cam_points(osc, step=2, zmax=2.0) @ osc["R"].T + osc["eye"]
        sel = (np.hypot(*(Ps_all[:, :2] - c_w).T) < 0.06) & (Ps_all[:, 2] > 0.012) & (Ps_all[:, 2] < top_w + 0.02)   # (not the arm over it)
        if sel.sum() > 20:
            c_s2 = np.median(Ps_all[sel, :2], 0); top_s = np.percentile(Ps_all[sel, 2], 95)
            print(f"\n== an object under the wrist camera: wrist {np.round(c_w * 1000).tolist()} mm, top {top_w * 1000:.0f} mm; "
                  f"scene camera {np.round(c_s2 * 1000).tolist()} mm, top {top_s * 1000:.0f} mm -> "
                  f"{np.linalg.norm(c_w - c_s2) * 1000:.0f} mm apart (a few mm: cameras and kinematics agree)")
            rep["cross"] = dict(wrist=c_w.tolist(), scene=c_s2.tolist(), dist=float(np.linalg.norm(c_w - c_s2)), top_w=top_w, top_s=top_s)
        else:
            print("\n== an object under the wrist camera: the scene camera sees nothing there (> 12 mm) within 6 cm")
    else:
        print("\n== nothing under the wrist camera (put an object there, e.g. a tape roll, for the two-camera check)")
    if world is not None:
        R_t, e_t = world.d.cam_xmat[world.m.camera("L_wrist").id].reshape(3, 3) @ np.diag([1, -1, -1]), world.d.cam_xpos[world.m.camera("L_wrist").id]
        print(f"\n(twin: the world's wrist camera is {np.linalg.norm(e_t - eye) * 1000:.1f} mm / "
              f"{math.degrees(math.acos(np.clip((np.trace(R_t.T @ R) - 1) / 2, -1, 1))):.2f} deg off the nominal mount: its calibration error)")
    rep["cameras"] = S.cams.info()
    json.dump(rep, open(out / "check.json", "w"), default=lambda x: x.tolist() if hasattr(x, "tolist") else float(x), indent=1)
    print(f"\nreport: {out / 'check.json'}")


# ---------------------------------------------------------------- motion commands
def joint_path(S, q1, T_):
    q0 = S.q_cmd.copy()
    n = max(int(T_ / B.DT), 1)
    return [q0 + (q1 - q0) * (k / n) ** 2 * (3 - 2 * k / n) for k in range(1, n + 1)]


def cmd_home(a):
    out = out_dir(a, "home")
    S, arm, *_ = make_rig(a, out, "home")
    q1 = np.asarray(a.q if a.q else S.q_home, float)
    why = S.lim.check_q(q1)
    if why:
        raise SystemExit(f"home pose outside the limits: {why}")
    dq = np.abs(q1 - S.q_cmd).max()
    print(f"to {'the twin home' if not a.q else 'q'} {np.round(q1, 4).tolist()} (max joint change {dq:.2f} rad), gripper open", flush=True)
    S._set_phase("home")
    S._queue(joint_path(S, q1, max(4.0, dq / 0.25)), B.OPEN_W, B.DT)
    S.flush(); S.q_cmd = q1.copy()
    w = S.finger_width()
    st = S.sync()
    print(f"done: max joint error {np.abs(np.asarray(st['q']) - q1).max() * 1000:.1f} mrad, gripper {w * 1000:.1f} mm")


def cmd_jog(a):
    """Small moves around the current pose (best: home): up/down 5 cm, sideways 3 cm, a 20 deg turn of the hand; after
    each, the TCP where the arm is (measured joints) vs where it was sent."""
    out = out_dir(a, "jog")
    S, arm, *_ = make_rig(a, out, "jog")
    p0, R0 = S.p_cmd.copy(), S.R_cmd.copy()
    yaw0 = math.atan2(R0[1, 1], R0[0, 1])
    Rd = B.down(yaw0)
    seq = [("hand straight down", p0, Rd), ("up 5 cm", p0 + [0, 0, 0.05], Rd), ("back", p0, Rd),
           ("down 5 cm", p0 - [0, 0, 0.05], Rd), ("back", p0, Rd), ("x +3 cm", p0 + [0.03, 0, 0], Rd), ("y +3 cm", p0 + [0.03, 0.03, 0], Rd),
           ("back", p0, Rd), ("turn +20 deg", p0, B.down(yaw0 + math.radians(20))), ("back", p0, Rd)]
    rows = []
    for name, X, R in seq:
        S.move(X, R, S.g_cmd, 1.0, name)
        r = S.flush()
        st = S.settle_wait()
        tcp = S.d.site_xpos[S.tcp].copy(); Rm = S.d.site_xmat[S.tcp].reshape(3, 3)
        e = np.linalg.norm(tcp - X) * 1000; ea = math.degrees(math.acos(np.clip((np.trace(Rm.T @ R) - 1) / 2, -1, 1)))
        rec = S.rec[-1] if S.rec else {}
        rows.append(dict(move=name, err_mm=round(e, 2), err_deg=round(ea, 2), settle=rec.get("settle"), track_max=rec.get("track_max")))
        print(f" {name:18s} TCP off by {e:5.2f} mm, {ea:4.2f} deg | settle {rec.get('settle')} | max lag {rec.get('track_max')} rad", flush=True)
    json.dump(rows, open(out / "jog.json", "w"), indent=1, default=str)


def cmd_touch(a):
    """The closed fingertips down onto the bare table at 3 points near (X, Y), slowly (5 mm/s), stopped by the first
    contact (guarded): where the kinematics put the fingertips at the contact = how much lower the real hand hangs than
    the model (the adapter between flange and hand), plus the table's height error. Clear the table there first."""
    out = out_dir(a, "touch")
    S, arm, *_ = make_rig(a, out, "touch")
    x0, y0 = a.at
    zs = []
    yaw = math.atan2(*(np.r_[x0, y0] - S.base[:2])[::-1]) + math.pi / 2
    yaw_now = math.atan2(S.R_cmd[1, 1], S.R_cmd[0, 1])                 # (closed fingers: yaw and yaw + 180 deg touch alike:
    yaw += math.pi * round((yaw_now - yaw) / math.pi)                  # the nearer one, no half turn of the wrist)
    R = B.down(yaw)
    up = 0.08 + S.kin.fingertip                                        # TCP with the fingertips 8 cm over the table
    S.move(S.p_cmd, S.R_cmd, 0.0, 0.8, "close the gripper"); S.finger_width()
    for dx, dy in ((0.0, 0.0), (0.06, 0.0), (0.0, 0.06)):
        X = np.r_[x0 + dx, y0 + dy]
        S.move(np.r_[S.p_cmd[:2], max(S.p_cmd[2], up + 0.1)], S.R_cmd, 0.0, 1.0, "up")
        S.move(np.r_[X, up + 0.1], R, 0.0, 2.0, "over the point")
        S.move(np.r_[X, up], R, 0.0, 1.0, "fingertips 8 cm over the table")
        floor = S.lim.table_z + S.lim.min_fingertip + 0.0005           # (the deepest the server allows)
        r = S.guarded_line(np.r_[X, floor + S.kin.fingertip], R, 0.005, "touch the table")
        tip = S.p_cmd[2] - S.kin.fingertip
        if r["contact"] and tip > 0.015:
            print(f" at ({X[0]:.3f}, {X[1]:.3f}): contact {tip * 1000:+.1f} mm over the table: something lies there (clear the "
                  f"table around the point): not counted", flush=True)
        elif r["contact"]:
            zs.append(tip)
            print(f" at ({X[0]:.3f}, {X[1]:.3f}): contact with the fingertips (by the kinematics) at {tip * 1000:+.1f} mm "
                  f"({r['contact']})", flush=True)
        else:
            print(f" at ({X[0]:.3f}, {X[1]:.3f}): NO contact down to {tip * 1000:+.1f} mm: the real hand is higher than the model "
                  f"(or the table lower) by more than that", flush=True)
        S.move(S.p_cmd + [0, 0, 0.08], R, 0.0, 1.0, "up")
    S.flush()
    if zs:
        z = float(np.median(zs))
        dz = z + 0.0015                                                # (~1.5 mm the soft fingertips give before the contact is felt)
        print(f"\nfingertips at contact: {np.round(np.array(zs) * 1000, 1).tolist()} mm by the kinematics -> the real hand is "
              f"~{abs(dz) * 1000:.1f} mm {'lower' if dz > 0 else 'higher'} than the model with adapter_dz {S.kin.adapter_dz * 1000:+.1f} mm."
              f"\n -> put \"tool\": {{\"adapter_dz\": {S.kin.adapter_dz + dz:.4f}}} in code/real/calib.json (negative = the hand nearer "
              f"the flange than the twin's), restart the arm server, then touch again (~0)")
    json.dump(dict(contacts_m=zs, adapter_dz_now=S.kin.adapter_dz), open(out / "touch.json", "w"), indent=1)


def cmd_hover(a):
    """The closed fingertips settled HEIGHT (--height, 10 mm) over the table at 3 points near (X, Y), with no contact:
    measure each gap (the table to the lowest point of the fingertips) with a ruler and type it; the gap minus the
    kinematics' height (from the measured joints) = how much higher the real hand is than the model. (10-28: the
    guarded touch cannot tell a contact from joints 2 and 4 sticking for up to 2 s at the start of a slow descent.)"""
    out = out_dir(a, "hover")
    S, arm, *_ = make_rig(a, out, "hover")
    x0, y0 = a.at
    h = a.height
    yaw = math.atan2(*(np.r_[x0, y0] - S.base[:2])[::-1]) + math.pi / 2
    yaw_now = math.atan2(S.R_cmd[1, 1], S.R_cmd[0, 1])                 # (closed fingers: yaw and yaw + 180 deg alike)
    yaw += math.pi * round((yaw_now - yaw) / math.pi)
    R = B.down(yaw)
    tip = S.kin.fingertip
    ask = a.backend == "robot" and sys.stdin.isatty()
    S.move(S.p_cmd, S.R_cmd, 0.0, 0.8, "close the gripper"); S.finger_width()
    rows = []
    for dx, dy in ((0.0, 0.0), (0.06, 0.0), (0.0, 0.06)):
        X = np.r_[x0 + dx, y0 + dy]
        S.move(np.r_[S.p_cmd[:2], max(S.p_cmd[2], 0.18 + tip)], S.R_cmd, 0.0, 1.0, "up")
        S.move(np.r_[X, 0.18 + tip], R, 0.0, 2.0, "over the point")
        S.move(np.r_[X, 0.03 + tip], R, 0.0, 1.5, "fingertips 3 cm over the table")
        S.tcp_now()                                                    # (sent and settled: the last bit on its own)
        S.move(np.r_[X, h + tip], R, 0.0, 2.0, f"fingertips {h * 1000:.0f} mm over the table")
        S.tcp_now()
        T = S.kin.tcp_world(np.asarray(S.last_state["q"]))
        z = float((T[:3, 3] + T[:3, 2] * tip)[2])                      # (the fingertips from the measured joints)
        print(f"\n at ({X[0]:.3f}, {X[1]:.3f}): the fingertips are {z * 1000:.1f} mm over the table by the kinematics "
              f"(adapter_dz {S.kin.adapter_dz * 1000:+.1f} mm)", flush=True)
        gap = None
        if ask:
            while True:
                txt = input("   measure the gap, table to the lowest point of the fingertips, in mm (Enter = skip): ").strip()
                if not txt:
                    break
                try:
                    gap = float(txt) / 1000; break
                except ValueError:
                    print("   a number in mm, e.g. 11.5")
        rows.append(dict(at=X.tolist(), model_mm=round(z * 1000, 2), ruler_mm=None if gap is None else round(gap * 1000, 2)))
        if gap is not None:
            print(f"   -> the real hand is {(gap - z) * 1000:+.1f} mm higher than the model here", flush=True)
        S.move(S.p_cmd + [0, 0, 0.08], R, 0.0, 1.0, "up")
    S.flush()
    d = [r["ruler_mm"] - r["model_mm"] for r in rows if r["ruler_mm"] is not None]
    if d:
        dm = float(np.median(d)) / 1000
        print(f"\nruler - kinematics: {np.round(d, 1).tolist()} mm -> the real hand is ~{abs(dm) * 1000:.1f} mm "
              f"{'higher' if dm > 0 else 'lower'} than the model with adapter_dz {S.kin.adapter_dz * 1000:+.1f} mm"
              f"\n -> \"tool\": {{\"adapter_dz\": {S.kin.adapter_dz - dm:.4f}}} in code/real/calib.json (negative = the hand nearer "
              f"the flange than the twin's); restart the arm server", flush=True)
    json.dump(dict(rows=rows, adapter_dz_now=S.kin.adapter_dz, height=h), open(out / "hover.json", "w"), indent=1)


def cmd_look(a):
    """The wrist camera over table point (X, Y) from the look height; the object there (or with --box the box) found
    in its depth."""
    out = out_dir(a, "look")
    S, arm, world, wper, L = make_rig(a, out, "look")
    per = RealPerception(S, np.random.default_rng(0), out, "look")
    prog = TB.BoxProgram(S, per)
    prog.box, prog.targets, prog.avoid = None, {}, {}
    X = np.r_[a.at, 0.03]
    R = B.down(prog.default_yaw(X[:2]))
    if a.box:
        box, obs = prog.look_box(np.r_[X[:2], 0.0])
        print(f"box: centre {np.round(box['c'] * 1000).tolist()} mm, size {box['hu'] * 2000:.0f} x {box['hv'] * 2000:.0f} mm, rim "
              f"{box['rim'] * 1000:.1f} mm, floor {box['floor'] * 1000:.1f} mm, lid {None if box['lid'] is None else round(math.degrees(box['lid']['th']))} deg")
    else:
        h = prog.look_height(0.05)
        prog.transit(prog.look_tcp(X, R, X[2] + h)[:2], R)
        S.move(prog.look_tcp(X, R, X[2] + h), R, S.g_cmd, 1.5, "look")
        obs = per.wrist()
        try:
            g = F.refine_object(obs, X, zmax=0.15)
            print(f"object: centre {np.round(g['c'] * 1000).tolist()} mm, top {g['top'] * 1000:.1f} mm (table {g['table'] * 1000:.1f}), "
                  f"narrowest {g['width'] * 1000:.1f} mm")
        except F.Unseen as e:
            print("nothing found:", e)


def cmd_run(a):
    out = out_dir(a, "run")
    S, arm, world, wper, L = make_rig(a, out, "run")
    S.task_timing = not a.old_timing                                    # (10-30: real_sim FREE_PHASES, rig.json _speed_doc)
    if a.text:                                                          # (10-30: any instruction; Astra plans it from the
        TB.TEXT = a.text.strip()                                        # scene image with the same skills)
    print(f"task: {TB.TEXT}", flush=True)
    if a.plan == "astra" and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set (Astra)")
    plan_gt = None
    if a.plan == "gt":
        if world is None:
            raise SystemExit("--plan gt needs the twin backend (the true state)")
        def plan_gt():
            steps = (CB.ClutterPerception.plan_gt if a.clutter else TB.BoxPerception.plan_gt)(wper)
            return steps, wper.seeds
    seg = a.pick == "seg"
    per = (RealClutterPerception if seg else RealPerception)(S, np.random.default_rng([a.seed, a.layout, 5]), out, f"run_{a.backend}", plan_gt=plan_gt)
    truth = None if world is None else (lambda: sorted(k for k in TB.ITEMS if TB.inside_box(world, k)))
    if a.backend == "robot":
        st = S.sync()
        dh = np.abs(np.asarray(st["q"]) - S.q_home).max()
        if dh > 0.05:
            raise SystemExit(f"the arm is {dh:.2f} rad from the home pose: run `real_box.py home` first")
    confirm = a.backend == "robot" and not a.no_confirm_plan
    prog = RealClutterProgram(S, per, confirm_plan=confirm) if seg else RealBoxProgram(S, per, confirm_plan=confirm, truth=truth)
    if a.clutter:
        tg = L["target"]
        k0 = {k: world.pose(k)[0].copy() for k in L["objs"]}
    box0 = None if world is None else world.pose("box_white")[0].copy()
    t0 = time.time()
    try:
        r = prog.run(stop_after=a.stop_after)
    except RobotStop as e:
        r = f"robot stop: {e}"
    except KeyboardInterrupt:                                         # (11-05: Ctrl-C lost the run's log: saved now;
        r = "interrupted (Ctrl-C)"                                    # the arm is not moved after it)
        print("\ninterrupted: the arm is left where it is", flush=True)
    except Exception as e:
        traceback.print_exc(); r = f"crash: {e!r}"[:300]
    if r not in ("done",) and not r.startswith(("robot stop", "plan", "stopped after", "interrupted")):   # a step failed: up and away
        try:
            S.flush()
            S.move(S.p_cmd + [0, 0, 0.10], S.R_cmd, S.g_cmd, 1.5, "retreat up after the stop"); S.flush()
            print("(the arm went 10 cm up after the stop)", flush=True)
        except Exception as e:
            print(f"(could not retreat up: {e}: move the arm away with `real_box.py home` or by hand)", flush=True)
    print(f"\nresult: {r} ({time.time() - t0:.0f} s, Astra ${per.cost:.3f})", flush=True)
    row = dict(program=r, wall_s=round(time.time() - t0, 1), astra_cost=per.cost, events=prog.events, trajectories=S.rec,
               astra=per.astra_log, layout=L, server_log=S.server_log, t0_clock=S.events_t0,
               clock="twin sim time" if world is not None else "unix")
    if world is not None and a.clutter:                               # (the twin's true state: the target where the
        p1 = world.pose(tg)[0]                                         # plan put it, nothing else lifted out)
        inside = TB.inside_box(world, tg)
        ok = (inside if L["scene"] == "pile" else (not TB.inside_box(world, tg, 0.02) and p1[2] < 0.05)) if a.stop_after is None \
            else bool(np.linalg.norm(p1[:2] - k0[tg][:2]) < 0.05)
        moved = {k: round(float(np.linalg.norm(world.pose(k)[0][:2] - k0[k][:2])) * 1000) for k in L["objs"] if k != tg}
        off = [k for k in L["objs"] if world.pose(k)[0][2] < -0.01]
        res = dict(ok=bool(ok and not off), scene=L["scene"], target=tg, target_in_box=inside, others_moved_mm=moved, off_table=off)
        print(f"judged on the twin's true state: {'SUCCESS' if res['ok'] else 'fail'} {res}")
        row["judge"] = res
    elif world is not None and a.stop_after is None:
        ev = next((e for e in prog.events if e["what"] == "pushed"), None)
        res = TB.judge(world, prog, np.r_[ev["from_xy"], 0] if ev else box0, ev["dir"] if ev else None)
        print(f"judged on the twin's true state: {'SUCCESS' if res['ok'] else 'fail'} {res}")
        row["judge"] = res
    if world is not None and arm.artifact:
        print(f"(twin physics artifact during the run: {arm.artifact})")
        row["twin_artifact"] = arm.artifact
    (out / "run.json").write_text(jdump(row))
    (out / "timeline.json").write_text(jdump(S.timeline))
    finish_recording(S, compose_episode=True)
    print(f"log: {out}")
    return row


def finish_recording(S, compose_episode=False):
    meta = S.stop_recording()
    if meta is None:
        return
    import recorder
    print("recorded:", ", ".join(f"{k} {v['frames']} frames ({v['seconds']} s)" for k, v in meta["streams"].items()) +
          (f"; {meta['dropped']} frames dropped" if meta["dropped"] else "") + (f"; ERRORS {meta['errors']}" if meta["errors"] else ""), flush=True)
    if compose_episode and meta["streams"]:
        try:
            print("episode video:", recorder.compose(S.log_dir), flush=True)
        except Exception as e:
            print(f"(episode.mp4 not written: {e!r}; the camera videos are in {S.log_dir / 'video'})", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "home", "jog", "touch", "hover", "look", "run"])
    ap.add_argument("--backend", choices=["robot", "twin"], default="robot")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--layout", type=int, default=0, help="twin: the layout (task_box.sample_layout)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--plan", choices=["astra", "gt"], default="astra")
    ap.add_argument("--stop-after", type=int, default=None, help="run: only the first N steps (a held object is put back)")
    ap.add_argument("--speed", type=float, default=None, help="time scale of the moves (robot: rig.json speed, 0.5 = half the twin's)")
    ap.add_argument("--v-max", type=float, default=None, help="joint speed limit, rad/s (<= rig.json safety.v_max)")
    ap.add_argument("--old-timing", action="store_true", help="run: the 10-29 timing (every free move settled, each "
                    "trajectory stretched as a whole at rig.json speed) instead of the task timing (real_sim.task_times)")
    ap.add_argument("--no-confirm-plan", action="store_true")
    ap.add_argument("--no-cameras", action="store_true", help="check: the arm only")
    ap.add_argument("--q", type=float, nargs=7, help="home: this pose instead of the twin's home")
    ap.add_argument("--at", type=float, nargs=2, help="look / touch / hover: table point x y (world, m)")
    ap.add_argument("--height", type=float, default=0.010, help="hover: the fingertips this high over the table (m)")
    ap.add_argument("--box", action="store_true", help="look: find the box there")
    ap.add_argument("--task", choices=["three", "tape", "loose", "close", "full"], default=None, help="run: three = the block, the red tape on it, the "
                    "yellow tape, the lid (default); tape = the yellow tape into the box, the lid; loose = the cable, the yellow tape, the "
                    "screwdriver into the box, overlapping allowed, then the lid; close = only close the lid; full = the original five "
                    "things + push")
    ap.add_argument("--text", default=None, help='run: any instruction instead of --task\'s, e.g. "Put everything on the table into the '
                    'box, then close it." (Astra plans it with the same skills: pick, put_in, put_on, close, push; things may overlap in '
                    'the box; with --plan astra)')
    ap.add_argument("--pick", choices=["box", "seg"], default="box", help="run: seg = the clutter pick (clutter_bench.py, "
                    "11-03): Astra's clutter plan (pick / put_in / put_down), the target found by name with SAM3 in the wrist "
                    "camera's colour image (servers :8131 and :8134), drags things out, sets blockers aside; with --text")
    ap.add_argument("--clutter", choices=["pile", "bin"], default=None, help="twin backend: a clutter_bench layout (pile: pick the "
                    "target out of a pile into the box; bin: take it out of the full box onto the table); implies --pick seg")
    ap.add_argument("--no-record", action="store_true", help="no camera recording")
    ap.add_argument("--record-depth", type=float, default=None, metavar="FPS", help="record the depth too, at FPS (rig.json record.depth_fps)")
    a = ap.parse_args()
    if a.clutter:
        if a.backend != "twin":
            raise SystemExit("--clutter is a twin layout: --backend twin")
        a.pick = "seg"
    code = 0
    try:
        {"check": cmd_check, "home": cmd_home, "jog": cmd_jog, "touch": cmd_touch, "hover": cmd_hover, "look": cmd_look,
         "run": cmd_run}[a.cmd](a)
    except SystemExit as e:                                           # (os._exit below skips Python's own printing of
        if e.code not in (None, 0):                                   # these: 10-29, a missing OPENAI_API_KEY ended a run
            print(f"stopped: {e.code}", flush=True); code = 1         # with no message at all)
    except BaseException:
        traceback.print_exc(); code = 1
    finally:
        for S in ACTIVE:                                              # (also after a crash or Ctrl-C: the files are finished)
            try:
                finish_recording(S)
            except BaseException as e:
                print(f"(recording not closed cleanly: {e!r})")
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(code)                                                # (camera / zmq threads)


if __name__ == "__main__":
    main()
