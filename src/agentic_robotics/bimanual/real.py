"""A bimanual-environment program (run(robot, task), docs/bimanual.md) on the REAL rig: the same robot object as in
simulation (api.Robot's calls, show, ask; millimetres and degrees, table frame), over the vendored RealSim (the real
Panda's planning, limit checks and timing: real/README.md). Used by real/host_program.py inside the program sandbox.

What differs from the simulation, because the rig does:
  arms       only the LEFT Panda can be moved. The right one stands parked: a move of the right arm to a point in its
             parked zone (y < -280 mm; the programs' "park the other arm") is answered as done, nothing is sent;
             any other use of the right arm or of its wrist camera ends the episode (EpisodeEnd).
  moves      planned whole before anything is sent, as in simulation (refused = nothing moved). Timing: the
             simulation's speeds (core.V_SLOW / V_FREE / W_TURN), then RealSim's (rig.json: the joint speed limit, an
             unguarded way down to below 150 mm at half speed).
  settle     every move that is not a contact ends with the arm server's settle (rig.json control.settle: the
             remaining joint error iterated out, at most 2 s), a stop_on_contact move that touched nothing too: the
             real arm otherwise stops up to centimetres from the pose asked for (lag, stiction), where the
             simulation's arm is exact and the programs count on it. settle_all=False (real/run program
             --no-settle): RealSim's own (the unguarded ways down only).
  contact    a stop_on_contact move is RealSim's guarded line (the arm server stops when the hand is held up guard_dz,
             rig.json's 2 mm, or the vertical force changes by 4 N: descents only), at guard_speed: 5 mm/s, the
             speed that test was validated at on the arm (the simulation's is 40 mm/s). The safety stop is the arm
             server's (joint lag, 35 N): the move then answers "SAFETY STOP: ..." and after max_stops of them the
             episode ends.
  push       a plain move straight down from where a stop_on_contact move has just touched (the session's program
             does it to get past light contacts: in the simulation it ends at 40 N) is sent as a guarded line too,
             with the arm server's widest guard (PUSH: 10 mm held up, 15 N), and answers "SAFETY STOP: ..." there:
             a slow push with a limit, not a 40 mm/s one that ends at the arm server's 35 N abort. soft_push=False
             (real/run program --free-push) sends it as any other move.
  dry runs   (the server's mode "dry": nothing is sent) state() gives the commanded pose and opening, so that the
             program's logic goes on; frames are the real ones, from where the arm really is.
  time       robot.time_s() is the PROGRAM'S CLOCK: the server's robot clock (motion, waits, and the model's answers'
             wait) minus what the stop_on_contact moves took beyond the simulation's 40 mm/s (sim_clock: a program
             written in simulation times its own steps, "search until 510 s", against that speed; at 5 mm/s the
             rig's clock would cut them short for a reason that is the rig's, not the program's). The program's
             limit (program_limit_s: the task's) is on that clock; the server's limit and every reported robot
             time are on the rig's. sim_clock=False (real/run program --robot-clock): the rig's clock for both."""
from __future__ import annotations

import math
import re
import time
from pathlib import Path

import numpy as np

from ..robot.client import SocketArm
from ..sim import twin
from .api import Frame, Robot, TimeUp, _dir, _mm, _refused
from .core import ARMS, CAMS, LOW_Z, MIN_Z, V_FREE, V_SLOW, W_TURN, expm, rotvec
from .grippers import grip_R
from .practice import ASK, SHOWN, _fmt, _result, _rgb8

DT = twin.DT
RIG_CAMS = {"scene": "d415", "left_wrist": "L_wrist"}           # what the rig has of core.CAMS
PARK_Y = -280.0                                                 # mm: the parked right arm's side (rig.json keep-out)
DESCEND_Z = 0.15
WRIST_KEEP = 0.10                                                # (rad kept from the ends of joint 7's range in every plan: on the
#   arm, 2026-11-25, a move planned to 2.847 rad, the rig's limit, left the wrist 0.058 rad short of it, and the arm's server
#   refused the next trajectory: the episode ended)
ROT_SHORT = math.radians(1.5)                                    # (core.BiCore._follow: a pose is reached within this)
SETTLED = ("transit", "lift", "descend", "turn the hand", "settle")   # _go's phases that end with a settle
GRIP_STEADY = (0.8, 5.0)        # s: after a gripper command the real opening is read until it has not changed for the
                                # first (at most the second): the gripper's state comes every 0.2-0.33 s, so the arm
                                # server's own wait (0.25 s without a change) can return in the middle of the motion
# The real gripper does one thing at a time. Its grasp is not over when the fingers stand still: it goes on pressing
# for a moment, and a command that comes meanwhile is LOST (run 20261125_121707: the fingers stood at 17.9 mm 1.5 s
# after the close, the gripper reported the grasp done 1.5 s later; the program's open, 2 s after its close, did
# nothing, and the arm went on with the cylinder in its closed hand). So after a close that holds something the
# opening is not handed back before the gripper reports the grasp (or `done` s more have passed), and an OPEN after
# which the fingers stand more than `short` m inside the opening asked for is sent again, `again` times, `pause` s apart.
GRIP = dict(done=3.0, short=0.005, again=3, pause=1.0)
PUSH = dict(dz=0.010, df=15.0)                                  # (arm_server._guard's upper limits)
# The sense of state()'s force_n. The simulation gives the net force of everything else ON the arm: the table pushing
# the hand up is +z. This arm's server reports the opposite: at 42 of the 48 descents of 2026-11-23..25 that touched
# (guarded lines stopped with 3 N or more) its fz FELL, and with a sponge pressed on the table and the arm at rest it
# stood 4.5 N BELOW its value with the sponge held in the air (run 20261125_111328: the sweeping program, which waits
# for +1.4 N to know the sponge is down, pressed for 85 s and never saw it). So the program gets the estimate with
# its sign turned (all three axes: one convention; only z is checked against data). The server's own guards and
# CONFIRM use magnitudes and are not concerned; the programs that ran before used magnitudes too.
FORCE_SIGN = -1.0
# confirm_contact (real/run program --confirm-contact; off by default): the arm server ends a guarded line when the
# hand lags its model of a free arm (rig.json: 2 mm) OR the force rises by 4 N. On this arm the first fires in free
# air (joint friction: 2026-11-23, every guarded descent of every run, 0.1-0.8 N, the hand 2-6 mm behind). With it:
#   - the lines are sent with the widest lag the server allows (dz), so that the joints get time to start;
#   - a stop with less than `n` N of force change is not passed on as a contact: the line goes on from where the arm is;
#   - after `stuck` such lines that together brought the hand less than `step` m each nearer its goal (run 164318: thirty
#     lines cut after 1.5-2 s each, the hand going 1 mm down and 1 mm up in turn), the arm is taken `nudge` m on by an
#     ordinary, settled move, as a program's own recovery does on this arm. It IS a contact if such a step changes the
#     force by `push` N (or the force is `total` N from the move's start), or if the last `window` steps brought the
#     hand less than `held` of their way (one step says little: the arm ends its moves up to 4 mm off); at most
#     `nudges` steps and `tries` lines in one move.
#   - a line that ran to its end is NOT followed by the server's settle when the arm, `wait` s later, stands within
#     `near` m of the goal. (The settle works joint by joint. Run 185903: a guarded line of 8 mm ended with the tool at
#     its goal's height; the settle that followed took it 7 mm below and 6 mm beside the goal by the time the program
#     read it, the fingertips to the table, and the program refused to close.)
#   - (after the clutter runs of 2026-11-23, 201146: the fingers stood on the object; its stops came with 2.3 to 2.9 N,
#     under `n`, and the lines and steps pressed on it for 47 s while the hand slid 33 mm aside.) A REAL contact on this
#     arm can come with as little as 2 N at 10 to 14 mm of lag; the stops in free air that made no way came with under
#     1 N. So: `stuck` lines in a row without way that each came with `held_n` N or more ARE a contact; and so is an
#     ordinary step that brings under `nudged` of its way with `held_n` N or more; `window` is 2 steps.
#   - with the option, a plain move that goes DOWN from where a move until contact has just touched is sent as the
#     guarded push (PUSH) wherever within `push_xy` m it aims (without the option: within 3 mm): the hand may have
#     slid, and an ordinary move 28 mm into the object left the arm further from its goal than the server tolerates.
#   - (run 20261125_120552: a 181 mm descent onto a sponge at x -140 mm, far from the arm's base, was cut 15 times; after
#     each cut the next line started where the arm stood, and each time the shoulder lagged the elbow, so the hand went
#     OUT as much as down: 43 mm beside its line at z 150 mm, still 24 mm beside it when its fingers met the sponge's
#     top.) A cut line that leaves the tool more than `beside` m off the line the program asked for, with less than
#     `held_n` N, is followed by an ordinary, settled move back ONTO that line at the height reached; the ordinary
#     steps go along that line too. `beside` counts within `beside_near` m of the goal; further from it only a drift
#     of `beside_far` m is taken back (run 20261125_121707: 22 such moves in one 194 mm descent, 95 s of it).
CONFIRM = dict(n=3.9, dz=0.010, stuck=2, step=0.001, nudge=0.004, push=4.0, total=6.0, window=2, held=0.25, nudges=120, tries=300,
               wait=0.6, near=0.003, held_n=1.8, nudged=0.35, push_xy=0.06, beside=0.004, beside_near=0.06, beside_far=0.02)
# (push, total: 3.0 and 4.5 until run 170104, whose one ordinary step, in free air, changed the force estimate by 2.43 N.
#  n: 2.0 until run 171920, where a line stopped in free air at z 256 mm, the hand 13 mm behind, came with 2.1 N and was
#  passed on as a contact; the program, putting its cylinder back, let go of it there. n is now just under the server's
#  own force threshold (4 N): only a stop by the force counts, the lag never does. nudges, tries: 40 and 60 until then:
#  a long descent must not be reported as a contact because the tries ran out.)


class EpisodeEnd(Exception):
    """The episode cannot go on (the rig's limits, the operator, the arm server): no further command runs."""


class _Refused(Exception):
    """Nothing moved."""


class _Stopped(Exception):
    """The arm moved and stopped before the goal."""


class ReplyArm(SocketArm):
    """SocketArm that keeps the last execute reply (RealSim.flush raises RobotStop with its text only) and asks for
    the arm server's settle after the moves of settle_phases (RealSim: after its SETTLE_PHASES only)."""
    last = None
    settle_phases = ()

    def execute(self, t, Q, grip=None, settle=False, phase=None, hold_after=0.1, guard=None):
        if guard is None and grip is None and str(phase).split(", ")[-1] in self.settle_phases:
            settle = True
        self.last = super().execute(t, Q, grip=grip, settle=settle, phase=phase, hold_after=hold_after, guard=guard)
        return self.last


class RealRobot:
    CAMERAS = tuple(CAMS)
    ARMS = tuple(ARMS)
    GUARD_N, SAFETY_N, MIN_DEPTH_MM = Robot.GUARD_N, Robot.SAFETY_N, Robot.MIN_DEPTH_MM
    kind = "grippers"

    def __init__(self, S, link, seg, out, hello, ask=False, guard_speed=0.005, guard_dz=None, soft_push=True, max_stops=3,
                 max_refusals=6, tare_force=True, sim_clock=True, program_limit_s=None, settle_all=True, trace=None,
                 confirm_contact=False, guard_speed_high=None):
        """S: RealSim built on a ReplyArm(link); seg: perception.client.Segmenter; out: the program's directory
        (shown/); hello: the server's; ask: the server answers robot.ask; trace: a file for the commands' log."""
        self.S, self.link, self.seg, self.out, self.trace = S, link, seg, Path(out), trace
        S.lo, S.hi = np.array(S.lo, float), np.array(S.hi, float)   # (the planner's own copy: WRIST_KEEP)
        S.lo[6] += WRIST_KEEP; S.hi[6] -= WRIST_KEEP
        self.dry = hello.get("mode") == "dry"
        self.grip_steady = GRIP_STEADY if hello.get("backend") == "robot" and not self.dry else None
        self.settle_all = bool(settle_all) and isinstance(S.backend, ReplyArm)
        if self.settle_all:
            S.backend.settle_phases = SETTLED
        self.sim_clock, self.saved = bool(sim_clock), 0.0          # (saved: s the program's clock is behind the rig's)
        self.on_rig = hello.get("backend") == "robot"               # (the arm server's force estimate: FORCE_SIGN)
        self.limit = float(program_limit_s or hello["robot_time_limit_s"])
        self.asking, self.guard_speed = bool(ask), float(guard_speed)
        self.guard_speed_high = float(guard_speed_high or guard_speed)     # (a move until contact above DESCEND_Z)
        self.guard = dict(dz=float(guard_dz or S.rig["contact"]["dz"]), df=float(S.rig["contact"]["df"]))
        self.soft_push, self._touched = bool(soft_push), None       # (_touched: the command a guarded move stopped at)
        self.confirm = bool(confirm_contact)                        # (CONFIRM)
        self.max_stops, self.max_refusals = int(max_stops), int(max_refusals)
        self.ended = None
        self.n = dict(commands=0, shown=0, asks=0, ask_usd=0.0, ask_wait_s=0.0, stops=0, refusals=0, right_arm=0, unconfirmed=0)
        self._refusals = 0                                          # the server's, in a row
        self.frames = {}
        self.tare = None                                            # (the arm's force estimate with the hand free and
        if tare_force:                                              # empty, at the start: the real arm's is not 0 there,
            f = [(S.sync().get("f_ext") or [0.0] * 3)[:3] for _ in range(5)]    # the simulation's is)
            self.tare = np.mean(np.asarray(f, float), axis=0)

    # ------------------------------------------------------------------ the episode's end, the log
    def _end(self, why):
        self.ended = str(why)
        raise EpisodeEnd(self.ended)

    def _time(self):
        if self.ended:
            raise EpisodeEnd(self.ended)
        if self.time_s() >= self.limit:
            raise TimeUp(f"the episode's robot time ({self.limit:.0f} s) is spent")

    def _call(self, name, fn, a=(), k=None, count=True):
        """One command of the program: the rig's faults end the episode; logged with its result."""
        try:
            r = fn()
        except twin.RobotStop as e:
            if "robot-time limit" in str(e):
                raise TimeUp(f"the episode's robot time ({self.limit:.0f} s) is spent")
            self._log(name, a, k or {}, f"ROBOT STOP: {e}")
            self._end(f"robot stop: {str(e)[:300]}")
        except EpisodeEnd as e:
            self._log(name, a, k or {}, f"EPISODE ENDS: {e}")
            raise
        if count:
            self.n["commands"] += 1
            self._log(name, a, k or {}, r)
        return r

    def _log(self, name, a, k, r):
        if self.trace is None:
            return
        S = self.S
        args = ", ".join([_fmt(x) for x in a if not hasattr(x, "rgb")] + [f"{q}={_fmt(v)}" for q, v in k.items()])
        p = S.p_cmd if self.dry else S.d.site_xpos[S.tcp]         # (the mirror: the measured joints after each send)
        res = r if isinstance(r, str) else _result(r)
        try:
            t = f"{self.time_s():6.1f}"
        except Exception:
            t = "     ?"
        self.trace.write(f"# t={t} {name}({args}) -> {res} | left tool {_fmt(_mm(p))}"
                         f"{' (commanded: dry run)' if self.dry else ''}\n")

    def counts(self):
        return dict(self.n)

    # ------------------------------------------------------------------ time
    def time_s(self):
        """The program's clock since the episode's start (s): the module's text, time."""
        return float(self.link.call("now")[0]["t"]) - self.saved

    def wait(self, seconds):
        self._time()
        return self._call("wait", lambda: self.S.hold(float(np.clip(seconds, 0.0, 10.0))), (seconds,))

    # ------------------------------------------------------------------ sensing
    def look(self, cameras=None):
        """Frames of the cameras ("scene", "left_wrist" on this rig; both by default) -> {camera: Frame}."""
        self._time()
        cams = list(cameras or RIG_CAMS)

        def go():
            out = {}
            for c in cams:
                if c not in CAMS:
                    raise KeyError(f"camera must be one of {list(CAMS)}")
                if c not in RIG_CAMS:
                    self._end(f"the program asked for the {c} camera: this rig has only the left arm's (what the task "
                              "needs must lie on the left arm's side)")
                o = self.S.observe_frame(RIG_CAMS[c])
                z = np.asarray(o["depth"], np.float32)
                z = np.where(np.isfinite(z) & (z >= MIN_Z), z, np.nan).astype(np.float32)
                K = o["K"]
                out[c] = self.frames[c] = Frame(dict(camera=c, rgb=o["rgb"], t=float(o["t"]), depth=z,
                                                     K=(int(K[0]), int(K[1]), *[float(x) for x in K[2:]]),
                                                     eye=np.asarray(o["eye"], float), R=np.asarray(o["R"], float)))
            return out
        return self._call("look", go, (cams,))

    def segment(self, frame, phrase):
        """SAM3 on a frame's colour image, prompted by text -> [(mask, score)], best first, at most 6."""
        return self._call("segment", lambda: self.seg.segment(frame.rgb, str(phrase))[:6], (str(phrase),))

    def segment_points(self, frame, pixels, labels):
        return self._call("segment_points", lambda: self.seg.segment_points(frame.rgb, pixels, labels)[:6])

    def state(self, arm):
        """What the arm and gripper measure: tcp_mm, approach_dir, closing_dir, opening_mm, commanded_mm, force_n (the
        arm server's estimate of the external force, N, minus its reading at the episode's start and in the
        simulation's sense, FORCE_SIGN: tare_force; --raw-force: as the arm server reports it)."""
        self._arm(arm)
        if self.ended:
            raise EpisodeEnd(self.ended)
        if arm != "left":
            self._call("state", lambda: self._right("state"), (arm,))
        S = self.S

        def go():
            S.flush()
            if self.dry:
                p, R, w = S.p_cmd, S.R_cmd, S.g_cmd * 2
                st = S.sync()
            else:
                w = S.finger_width()                                # (waits for the gripper if it was commanded; syncs)
                st = S.last_state
                p, R = S.d.site_xpos[S.tcp].copy(), S.d.site_xmat[S.tcp].reshape(3, 3).copy()
            f = np.asarray((st.get("f_ext") or [0.0, 0.0, 0.0])[:3], float) - (0.0 if self.tare is None else self.tare)
            if self.tare is not None and self.on_rig:               # (FORCE_SIGN: the simulation's sense, what pushes the arm)
                f = FORCE_SIGN * f
            f = [round(float(v), 1) for v in f]
            return dict(tcp_mm=_mm(p), approach_dir=_dir(R[:, 2]), closing_dir=_dir(R[:, 1]),
                        opening_mm=round(max(float(w), 0.0) * 1000, 1), commanded_mm=round(S.g_cmd * 2000, 1), force_n=f)
        return self._call("state", go, count=False)

    # ------------------------------------------------------------------ the arm
    def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
        """api.Robot.move on the left arm (stop_on_contact: a guarded line at guard_speed, see the module's text)."""
        self._arm(arm)
        xyz = np.asarray(xyz, float).reshape(3)
        a = (arm, xyz, dir_a, dir_b) + ((True,) if stop_on_contact else ())
        if not np.all(np.isfinite(xyz)) or xyz[2] < 10.0:
            return _refused("the tool point may not go below 10 mm above the table")
        if arm != "left":
            self._time()
            return self._call("move", lambda: self._right("move", xyz), a)
        try:
            R = self._orient(dir_a, dir_b)
        except ValueError as e:
            return _refused(str(e))
        p, t = xyz / 1000.0, self._touched
        if stop_on_contact:
            fn = self._sim_time(lambda: self._guarded(p, R), R)
        elif self.soft_push and t is not None and p[2] < t[0][2] and float(np.linalg.norm((p - t[0])[:2])) < (CONFIRM["push_xy"] if self.confirm else 0.003) \
                and float(np.linalg.norm(rotvec(t[1].T @ R))) < math.radians(2.0):
            fn = self._sim_time(lambda: self._guarded(p, R, PUSH, "SAFETY STOP: the push down met more than "
                                                      f"{PUSH['df']:.0f} N (or {PUSH['dz'] * 1000:.0f} mm of give) on the left arm"), R)
        else:
            fn = lambda: self._free(p, R)
        return self._motion("move", fn, a)

    def _sim_time(self, fn, R1):
        """A move until contact on the PROGRAM'S clock (sim_clock): whatever the rig needed for it (5 mm/s, the lines
        its server cut short in free air and their repeats, ordinary steps, settles: CONFIRM), the program's clock
        goes on by what the simulation takes for the way made: the turn at W_TURN and the line at V_SLOW. (Until
        2026-11-25 only the line's slower speed was taken off: run 20261125_111328's first descent, 181 mm with 15
        cut lines, cost the program 247 of its 600 s.)"""
        if not self.sim_clock or self.dry:
            return fn

        def go():
            S = self.S
            t0, p0, R0, s0 = float(self.link.call("now")[0]["t"]), S.p_cmd.copy(), S.R_cmd.copy(), self.saved
            try:
                return fn()
            finally:
                took = float(self.link.call("now")[0]["t"]) - t0
                sim = float(np.linalg.norm(S.p_cmd - p0)) / V_SLOW + float(np.linalg.norm(rotvec(R0.T @ S.R_cmd))) / W_TURN
                self.saved = s0 + max(took - sim, 0.0)
        return go

    def turn(self, arm, centre, axis, angle_deg):
        self._arm(arm)
        axis = np.asarray(axis, float).reshape(3); centre = np.asarray(centre, float).reshape(3)
        if not np.linalg.norm(axis) > 1e-6 or not abs(float(angle_deg)) <= 360:
            return _refused("give a non-zero axis and an angle within +-360 degrees")
        if arm != "left":
            self._time()
            return self._call("turn", lambda: self._right("turn"), (arm,))
        return self._motion("turn", lambda: self._turn(centre / 1000.0, axis / np.linalg.norm(axis), math.radians(float(angle_deg))),
                            (arm, centre, axis, angle_deg))

    def home(self, arm):
        self._arm(arm)
        if arm != "left":
            self._time()
            return self._call("home", lambda: self._right("home"), (arm,))
        return self._motion("home", self._home, (arm,))

    def set_gripper(self, arm, width_mm):
        """The opening between the fingers, 0..80 mm (0 = close and grip: the real gripper's grasp, 30 N) -> the
        opening measured afterwards (mm)."""
        self._arm(arm); self._time()
        if arm != "left":
            self._call("set_gripper", lambda: self._right("set_gripper"), (arm,))
        S = self.S
        g = float(np.clip(width_mm, 0, 80)) / 2000.0

        def go():
            moved = abs(g - S.g_sent) > 1e-6
            if moved:
                S._set_phase("close" if g < S.g_sent else "release")
                S._queue([S.q_cmd.copy()] * 9, g, DT)               # (0.3 s in place, with the gripper's command)
                S.g_cmd = g; S.d.ctrl[S.grip] = g
                try:
                    self._send()
                except _Refused as e:
                    S.g_cmd = S.g_sent
                    self._end(f"the gripper command was refused: {e}")
                except _Stopped as e:
                    self._end(f"the gripper command was stopped: {e}")
            w = S.g_cmd * 2 if self.dry else self._width(moved)
            if moved and self.grip_steady:                          # (the real gripper: GRIP)
                if g < 1e-6 and w > 0.001:                          # a close that holds something: until its grasp is over
                    t0 = time.time()
                    while not S.sync().get("grasped") and time.time() - t0 < GRIP["done"]:
                        time.sleep(0.05)
                    self._note(f"the gripper's grasp: {'reported done' if S.last_state.get('grasped') else 'not reported'} "
                               f"{time.time() - t0:.1f} s after the fingers stood still")
                    w = float(S.sync().get("width") or w)
                for k in range(GRIP["again"]):                      # an open that did nothing: sent again
                    if not (g > 1e-6 and w < 2 * g - GRIP["short"]):
                        break
                    self._note(f"the gripper stands at {w * 1000:.1f} mm after the command to open to {g * 2000:.0f} mm: sent again")
                    time.sleep(GRIP["pause"])
                    S.g_sent = float(w) / 2.0                       # (where the fingers are: RealSim sends a command only
                    S._queue([S.q_cmd.copy()] * 9, g, DT)           #  for an opening other than the one it holds as sent)
                    S.g_cmd = g; S.d.ctrl[S.grip] = g
                    try:
                        self._send()
                    except (_Refused, _Stopped) as e:
                        self._end(f"the gripper command was not carried out: {e}")
                    w = self._width(True)
            return round(max(float(w), 0.0) * 1000, 1)
        return self._call("set_gripper", go, (arm, width_mm))

    def _note(self, text):
        """A line of the adapter's own in the episode's output (what the rig did that the program did not ask for)."""
        if self.trace is not None:
            self.trace.write("#          (" + text + ")\n")

    def _width(self, moved):
        """The measured opening (m); after a gripper command on the real arm, once it stands still (GRIP_STEADY)."""
        S = self.S
        w = float(S.finger_width())
        if not (moved and self.grip_steady):
            return w
        steady, most = self.grip_steady
        t0 = t = time.time()
        while time.time() - t < steady and time.time() - t0 < most:
            time.sleep(0.05)
            v = S.sync().get("width")
            v = 0.0 if v is None else float(v)
            if abs(v - w) > 2e-4:
                w, t = v, time.time()
        S.timeline.append((float(S.d.time), "event", f"gripper steady at {w * 1000:.1f} mm ({time.time() - t0:.1f} s)"))
        return w

    # ------------------------------------------------------------------ show, ask (practice.ProgramRobot's)
    def show(self, label, image):
        """An image for whoever reads the run (program/shown/; the first SHOWN of an episode)."""
        import cv2
        if self.n["shown"] >= SHOWN:
            return False
        im = _rgb8(image, "show(label, image)", 960)
        d = self.out / "shown"; d.mkdir(exist_ok=True)
        tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label))[:40]
        cv2.imwrite(str(d / f"{self.n['shown']:02d}_{tag}.png"), im[..., ::-1])
        self.n["shown"] += 1
        return True

    def ask(self, question, images=(), effort="low"):
        """A question to the vision-language model, with images -> dict(ok, text, wait_s, usd, calls_left), or
        dict(ok=False, why, calls_left). The robot stands still while the answer comes: the wait is robot time."""
        self._time()
        if effort not in ASK["efforts"]:
            raise ValueError(f"ask: effort is one of {', '.join(ASK['efforts'])}")
        if hasattr(images, "rgb") or isinstance(images, np.ndarray):
            images = [images]
        images = list(images)
        if len(images) > ASK["images"]:
            raise ValueError(f"ask: at most {ASK['images']} images a call")
        ims = [_rgb8(getattr(im, "rgb", im), "ask(question, images)", ASK["side"]) for im in images]
        if not self.asking:
            return dict(ok=False, why="no model calls in this run", calls_left=0)
        if self.n["asks"] >= ASK["calls"]:
            return dict(ok=False, why=f"at most {ASK['calls']} model calls an episode", calls_left=0)
        self.n["asks"] += 1

        def go():
            r = self.link.call("ask", arrays=ims, question=str(question)[:ASK["chars"]], effort=effort)[0]
            wait = float(np.clip(r.get("wait_s") or 0.0, 0.0, ASK["wait_s"]))
            self.n["ask_usd"] += float(r.get("usd") or 0.0); self.n["ask_wait_s"] += wait
            out = dict(ok=bool(r.get("ok")), calls_left=ASK["calls"] - self.n["asks"], wait_s=round(wait, 1),
                       usd=round(float(r.get("usd") or 0.0), 4))
            out.update(text=str(r.get("text") or "")) if out["ok"] else out.update(why=str(r.get("why") or "no answer"))
            return out
        return self._call("ask", go, (" ".join(str(question).split())[:60], f"{len(ims)} images"), dict(effort=effort))

    def truth(self):
        return None

    def judge(self):
        return None

    # ------------------------------------------------------------------ inside
    def _arm(self, arm):
        if arm not in ARMS:
            raise KeyError('arm must be "right" or "left"')

    def _right(self, name, xyz=None):
        self.n["right_arm"] += 1
        if name == "move" and xyz is not None and xyz[1] < PARK_Y:  # (the real right arm stands parked there already)
            return dict(done=True, moved=True, why="", curved=False)
        self._end(f"the program asked for the right arm ({name}): this rig moves only the left arm (what the task "
                  "needs must lie on the left arm's side)")

    def _orient(self, a, b):
        a = np.asarray(a, float).reshape(3); b = np.asarray(b, float).reshape(3)
        if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b)) and np.linalg.norm(a) > 1e-6):
            raise ValueError("give two non-zero directions")
        R = grip_R(a, b)                                            # (api.Robot._orient: the nearer wrist angle)
        if float(R[:, 1] @ self.S.R_cmd[:, 1]) < 0:
            R = grip_R(a, -b)
        return R

    def _motion(self, name, fn, a):
        self._time()

        def go():
            self._touched = None
            try:
                curved = bool(fn())
            except _Refused as e:
                self.n["refusals"] += 1
                return _refused(str(e))
            except _Stopped as e:
                return dict(done=False, moved=True, why=str(e), curved=False)
            return dict(done=True, moved=True, why="", curved=curved)
        return self._call(name, go, a)

    def _send(self, guard=None):
        """Send the queued trajectory -> the server's reply. _Refused: nothing moved; _Stopped: the arm server's
        safety stop; the episode's limits, the operator's no and the server's repeated refusals end the episode."""
        S, arm = self.S, self.S.backend
        arm.last = None
        try:
            r = S.flush(guard=guard)
        except twin.RobotStop as e:
            r = arm.last
            if r is None:                                           # RealSim's own check: nothing was sent
                raise _Refused(str(e)[:300])
            why = str(r.get("why"))
            if "robot-time limit" in why:
                raise TimeUp(f"the episode's robot time ({self.limit:.0f} s) is spent")
            if "trajectories" in why and "at most" in why:
                self._end(why)
            if r.get("declined"):
                self._end(f"the operator declined a trajectory ({why})")
            if r.get("aborted"):
                self.n["stops"] += 1
                S.resync()                                          # (the next move starts where the arm is)
                if self.n["stops"] > self.max_stops:
                    self._end(f"{self.n['stops']} safety stops of the arm server (the last: {why})")
                raise _Stopped(f"SAFETY STOP: {why}"[:300])
            self._refusals += 1
            if self._refusals > self.max_refusals or not r.get("refused"):
                self._end(f"the arm server refuses: {why}")
            raise _Refused(why[:300])
        self._refusals = 0
        return r

    def _go(self, Q, dt, phase, p1, R1, kind="move", guard=None):
        """Queue the waypoints Q (after the current one, dt apart) and send them; the command is then (p1, R1)."""
        S = self.S
        keep = S.p_cmd.copy(), S.R_cmd.copy(), S.q_cmd.copy()
        S._set_phase(phase)
        S._queue(Q, S.g_cmd, dt, kind)
        S.p_cmd, S.R_cmd, S.q_cmd = np.asarray(p1, float).copy(), np.asarray(R1, float).copy(), np.asarray(Q[-1], float).copy()
        try:
            return self._send(guard)
        except _Refused:
            S.p_cmd, S.R_cmd, S.q_cmd = keep
            raise

    def _fk(self, q):
        S = self.S
        S.k.qpos[:] = S.d.qpos; S.k.qpos[S.qadr] = q
        S.mj.mj_kinematics(S.m, S.k)
        return S.k.site_xpos[S.tcp].copy(), S.k.site_xmat[S.tcp].reshape(3, 3).copy()

    def _unsafe(self, Q):
        """Why the left arm may not be at some of these joints (the rig's limits, 3 cm from the right arm), or None."""
        S = self.S
        for q in list(Q[::5]) + [Q[-1]]:
            why = S.lim.check_q(q) or ("within 3 cm of the right arm" if S.arm_clash(q) else None)
            if why:
                return why
        return None

    def _joints(self, q1):
        """A joint-space path from the commanded joints to q1 (core.BiCore.joint_path's timing) -> Q or None."""
        S = self.S
        q0 = S.q_cmd.copy()
        T_ = max(float(np.abs(q1 - q0).max()) * 1.2 / W_TURN, 1.2)
        n = max(int(T_ / DT), 1)
        Q = [q0 + (q1 - q0) * (k / n) ** 2 * (3 - 2 * k / n) for k in range(1, n + 1)]
        return None if self._unsafe(Q) else Q

    def _rot_short(self, q, R1):
        """How far the hand at joints q is from the orientation R1 (rad). RealSim.ik reports the position's error only:
        with the wrist at the end of its range it gives the place with the hand turned short, and the move was then
        sent and answered done (2026-11-25: a program that turns the hand in steps and reads state() found it 14 to 34
        degrees short). core.BiCore._follow refuses such a pose (1.5 degrees): so does everything here."""
        S, k = self.S, self.S.k
        k.qpos[:] = S.d.qpos; k.qpos[S.qadr] = q
        S.mj.mj_kinematics(S.m, k)
        return float(np.linalg.norm(rotvec(R1 @ k.site_xmat[S.tcp].reshape(3, 3).T)))

    def _joint_path(self, p1, R1):
        S = self.S
        q1, err = S.ik(p1, R1, S.q_cmd)
        if err > 0.002 or self._rot_short(q1, R1) > ROT_SHORT:
            q1, err = S.ik(p1, R1, S.q_home)
        return None if err > 0.002 or self._rot_short(q1, R1) > ROT_SHORT else self._joints(q1)

    def _free(self, p1, R1):
        """core.BiCore.move, not guarded: a line (a turn of more than 40 degrees: by the joints first), else a joint
        path, planned whole. -> curved."""
        S = self.S
        p0, R0 = S.p_cmd.copy(), S.R_cmd.copy()
        dist, ang = float(np.linalg.norm(p1 - p0)), float(np.linalg.norm(rotvec(R0.T @ R1)))
        phase = "descend" if p1[2] < p0[2] - 0.005 and p1[2] < DESCEND_Z else "lift" if p1[2] > p0[2] + 0.005 and \
            float(np.linalg.norm(p1[:2] - p0[:2])) < 0.02 else "transit"
        Q = self._joint_path(p1, R1) if ang > math.radians(40) else None
        if Q is None:
            T_ = max(dist / (V_SLOW if min(p0[2], p1[2]) < LOW_Z else V_FREE), ang / W_TURN, 0.3)
            S.last_unsafe = None
            Ql, fail, T_, n = S.plan_line(p0, R0, S.q_cmd, p1, R1, T_)
            short = self._rot_short(Ql[-1], R1) if fail is None else 0.0
            if fail is None and short <= ROT_SHORT and not self._unsafe(Ql):
                self._go(Ql[1:], T_ / n, phase, p1, R1)
                return False
            Q = self._joint_path(p1, R1)
            if Q is None:
                raise _Refused("the arm cannot reach that pose" + (f" (the hand would stay {math.degrees(short):.0f} degrees short of that "
                               "orientation: the wrist is at the end of its range)" if short > ROT_SHORT else
                               f" ({S.last_unsafe})" if S.last_unsafe else ""))
        self._go(Q, DT, phase, p1, R1)
        return True

    def _posture(self, p, R, q):
        """The way from joints q, the hand staying at (p, R), to the posture Sim.ik drifts to there: each of its
        iterations takes 5 % of the null-space step to the home posture, so a line planned from another posture
        starts with joint steps of 0.01-0.02 rad whatever its speed. -> waypoints ([q] alone: already there)."""
        S, mj = self.S, self.S.mj
        Q = [np.asarray(q, float).copy()]
        for _ in range(400):
            k = S.k
            k.qpos[:] = S.d.qpos; k.qpos[S.qadr] = Q[-1]
            mj.mj_kinematics(S.m, k); mj.mj_comPos(S.m, k)
            jp, jr = np.zeros((3, S.m.nv)), np.zeros((3, S.m.nv))
            mj.mj_jacSite(S.m, k, jp, jr, S.tcp)
            J = np.vstack([jp, jr])[:, S.dadr]
            dq = 0.15 * (np.eye(7) - np.linalg.pinv(J) @ J) @ (S.q_home - Q[-1])
            if float(np.abs(dq).max()) < 1e-3:
                break
            q2, e = S.ik(p, R, np.clip(Q[-1] + dq, S.lo, S.hi))
            if e > 0.002:
                break
            Q.append(q2)
        return Q if float(np.abs(Q[-1] - Q[0]).max()) > 0.004 else Q[:1]

    def _guarded(self, p1, R1, guard=None, stop="contact"):
        """RealSim.guarded_line, planned whole before any of it is sent: first, in place, the turn (if the orientation
        changes) and the way to the posture the line's inverse kinematics drifts to (_posture: else the line's first
        steps carry that drift and the whole descent is slowed to the joint speed limit); then the line at
        guard_speed with the guard (default: the contact guard). Raises _Stopped(stop) at a touch."""
        S = self.S
        p0, R0, q0 = S.p_cmd.copy(), S.R_cmd.copy(), S.q_cmd.copy()
        ang = float(np.linalg.norm(rotvec(R0.T @ R1)))
        pre = []
        if ang > math.radians(1.0):
            Qt, fail, T_, n = S.plan_line(p0, R0, q0, p0, R1, max(ang / W_TURN, 0.3))
            if fail is not None or self._rot_short(Qt[-1], R1) > ROT_SHORT or self._unsafe(Qt):
                raise _Refused("the arm cannot turn to that orientation where it is")
            pre, q0 = [(Qt[1:], T_ / n)], Qt[-1]
        S.last_unsafe = None
        Qr = self._posture(p0, R1, q0)
        if len(Qr) > 1:
            if self._unsafe(Qr):
                raise _Refused("the arm cannot follow that line")
            pre.append((Qr[1:], DT)); q0 = Qr[-1]
        def line(pa, qa):
            # (the waypoints one DT apart: guard_speed apart in space, guard_speed_high where the tool is above
            #  DESCEND_Z, the height from which RealSim itself takes a way down for free)
            L, way, ws = float(np.linalg.norm(p1 - pa)), 0.0, []
            while way < L - 1e-9:
                z = float(pa[2] + (p1[2] - pa[2]) * way / max(L, 1e-9))
                way = min(way + (self.guard_speed_high if z > DESCEND_Z else self.guard_speed) * DT, L)
                ws.append(way / max(L, 1e-9))
            ws = ws or [1.0]
            Q = [qa]
            for w_ in ws:
                q, e = S.ik(pa + (p1 - pa) * w_, R1, Q[-1])
                if e > 0.002 or np.abs(q - Q[-1]).max() > 0.15 or (w_ == ws[-1] and self._rot_short(q, R1) > ROT_SHORT):
                    raise _Refused("the arm cannot follow that line" + (f" ({S.last_unsafe})" if S.last_unsafe else ""))
                Q.append(q)
            why = self._unsafe(Q)
            if why:
                raise _Refused(f"the arm cannot follow that line ({why})")
            return Q

        Q = line(p0, q0)
        for Qp, dt in pre:
            self._go(Qp, dt, "turn the hand", p0, R1)
        confirm = self.confirm and guard is None and not self.dry     # (the contact guard only, not the push's)
        g = dict(guard or self.guard)
        if confirm:
            g["dz"] = max(float(g["dz"]), CONFIRM["dz"])
        u = (p1 - p0) / max(float(np.linalg.norm(p1 - p0)), 1e-9)
        tries = nudges = 0
        at, f_start = [], None                                      # (where the ordinary steps started; the force before the first)
        ways, came = [], []                                         # (of the lines since the last ordinary step: way, force)

        def beside(p):                                              # (how far p stands off the line p0 -> p1 asked for)
            rel = np.asarray(p, float) - p0
            return rel - float(rel @ u) * u

        def here():                                                 # (RealSim.guarded_line: the command = where the arm is)
            S.q_cmd = S.q_sent.copy()
            S.p_cmd = S.d.site_xpos[S.tcp].copy()

        def force():
            return np.asarray((S.sync().get("f_ext") or [0.0, 0.0, 0.0])[:3], float)

        def note(t):
            if self.trace is not None:
                self.trace.write("#          (" + t + ")\n")

        while True:
            pa = S.p_cmd.copy()
            r = self._go(Q[1:], DT * S.speed, "down until contact", p1, R1, kind="guarded", guard=g)
            if self.settle_all and not self.dry and not r.get("contact"):
                near = False
                if confirm:                                         # (CONFIRM: no settle where the arm is there already)
                    S.hold(CONFIRM["wait"]); S.sync()
                    off = float(np.linalg.norm(S.d.site_xpos[S.tcp] - p1))
                    near = off <= CONFIRM["near"]
                    note(f"the line ran to its end: the tool {off * 1000:.1f} mm from its goal" + (", no settle" if near else ", the arm's settle follows"))
                if not near:
                    self._go([Q[-1]], 0.3, "settle", p1, R1)        # (its end reached free: there, exactly)
            if not self.dry:
                here()
            way = float((S.p_cmd - pa) @ u)                         # (towards the goal; a hand that rose: negative)
            if self.sim_clock and self.guard_speed < V_SLOW:
                self.saved += max(way, 0.0) * (1.0 / self.guard_speed - 1.0 / V_SLOW)
            c = r.get("contact")
            if not c:
                return False
            dfz = c.get("dfz_n")
            if confirm and dfz is not None and float(dfz) < CONFIRM["n"] and tries < CONFIRM["tries"]:   # (CONFIRM: is it a contact?)
                tries += 1
                ways.append(way); came.append(float(dfz))
                k = CONFIRM["stuck"]
                stuck = len(ways) >= k and sum(ways[-k:]) < k * CONFIRM["step"]
                self.n["unconfirmed"] += 1
                note(f"the arm's server stopped the line at z {S.p_cmd[2] * 1000:.1f} mm, {way * 1000:+.1f} mm on: the hand "
                     f"{c.get('held_mm', c.get('dz_mm'))} mm behind, force change {dfz} N: not taken as a contact")
                left = float((p1 - S.p_cmd) @ u)
                touched = bool(stuck and min(came[-k:]) >= CONFIRM["held_n"])   # no way, against a force each time: held up
                if touched:
                    note(f"{k} lines without way, each against {CONFIRM['held_n']:g} N or more: a contact")
                elif left > 0.0015 and stuck:                       # the lines do not get on: an ordinary step
                    step = min(CONFIRM["nudge"], left)
                    f0, pb = force(), S.p_cmd.copy()
                    f_start = f0 if f_start is None else f_start
                    at.append(float(pb @ u))
                    try:
                        self._free(pb - beside(pb) + u * step, R1)   # (along the line asked for, not beside it)
                    except _Refused:
                        touched = True
                    else:
                        here()
                        f1 = force()
                        got, df, dft = float((S.p_cmd - pb) @ u), float(np.linalg.norm(f1 - f0)), float(np.linalg.norm(f1 - f_start))
                        nudges += 1; ways, came = [], []
                        w = CONFIRM["window"]
                        held = len(at) >= w and float(S.p_cmd @ u) - at[-w] < CONFIRM["held"] * w * CONFIRM["nudge"]
                        short = got < CONFIRM["nudged"] * step and df >= CONFIRM["held_n"]
                        touched = df >= CONFIRM["push"] or dft >= CONFIRM["total"] or held or short or nudges >= CONFIRM["nudges"]
                        note(f"an ordinary step of {step * 1000:.1f} mm: {got * 1000:+.1f} mm on, to z {S.p_cmd[2] * 1000:.1f} mm, force change "
                             f"{df:.2f} N" + (": a contact" if touched else ""))
                    left = float((p1 - S.p_cmd) @ u)
                elif left > 0.0015 and float(dfz) < CONFIRM["held_n"]:
                    off = beside(S.p_cmd)                           # (the hand has drifted off its line: back onto it)
                    far = float(np.linalg.norm(off))                # (near the goal every few mm count; high above it only
                    if far > (CONFIRM["beside"] if left < CONFIRM["beside_near"] else CONFIRM["beside_far"]):   # a real drift)
                        try:
                            self._free(S.p_cmd - off, R1)
                        except _Refused:
                            pass
                        else:
                            here()
                            note(f"the tool stood {float(np.linalg.norm(off)) * 1000:.1f} mm beside its line: an ordinary move back onto it, "
                                 f"now {float(np.linalg.norm(beside(S.p_cmd))) * 1000:.1f} mm beside, z {S.p_cmd[2] * 1000:.1f} mm")
                        left = float((p1 - S.p_cmd) @ u)
                if not touched:
                    if left <= 0.0015:                              # there: the last of the way as an ordinary move
                        try:
                            self._free(p1, R1)
                            return False
                        except _Refused:
                            pass
                    else:
                        try:
                            # (the arm stands in another posture than the line's inverse kinematics drifts to: without
                            #  this the line's first steps carry that drift and RealSim slows the WHOLE line to the joint
                            #  speed limit: run 20261125_121707's lines after a cut were planned at 1.5 mm/s, not 5)
                            Qr = self._posture(S.p_cmd.copy(), R1, S.q_cmd.copy())
                            if len(Qr) > 1 and not self._unsafe(Qr):
                                self._go(Qr[1:], DT, "turn the hand", S.p_cmd.copy(), R1)
                                here()
                            Q = line(S.p_cmd.copy(), S.q_cmd.copy())
                            continue
                        except _Refused:
                            pass
            self._touched = (S.p_cmd.copy(), np.asarray(R1, float).copy())
            raise _Stopped(stop)

    def _turn(self, c, axis, th):
        """core.BiCore.turn: the hand rigidly about the line through c along axis, in 2-degree steps, 30 degrees/s."""
        S = self.S
        p0, R0 = S.p_cmd.copy(), S.R_cmd.copy()
        n = max(int(math.ceil(abs(th) / math.radians(2))), 1)
        Q, p, R = [S.q_cmd.copy()], p0, R0
        for i in range(1, n + 1):
            E = expm(axis * th * i / n)
            p, R = c + E @ (p0 - c), E @ R0
            q, e = S.ik(p, R, Q[-1])
            if e > 0.002 or np.abs(q - Q[-1]).max() > 0.15 or self._rot_short(q, R) > ROT_SHORT:
                raise _Refused("the arm cannot turn that far there")
            Q.append(q)
        why = self._unsafe(Q)
        if why:
            raise _Refused(f"the arm cannot turn that far there ({why})")
        self._go(Q[1:], max(abs(th) / math.radians(30), 0.3) / n, "turn the hand", p, R)
        return False

    def _home(self):
        S = self.S
        Q = self._joints(np.asarray(S.q_home, float))
        if Q is None:
            raise _Refused("the way home by the joints is not safe from here: move clear first")
        p, R = self._fk(S.q_home)
        self._go(Q, DT, "home", p, R)
        return True


def program_robot(R):
    """What the program gets of a RealRobot: the documented calls (practice.ProgramRobot's), nothing of the rig."""
    class ProgramRobot:
        CAMERAS, ARMS, kind = R.CAMERAS, R.ARMS, R.kind
        GUARD_N, SAFETY_N, MIN_DEPTH_MM = R.GUARD_N, R.SAFETY_N, R.MIN_DEPTH_MM

        def __setattr__(self, k, v):
            raise AttributeError("the robot object cannot be changed")
    for name in ("look", "state", "move", "turn", "home", "wait", "time_s", "set_gripper", "segment", "show", "ask",
                 "truth", "judge"):
        setattr(ProgramRobot, name, staticmethod(getattr(R, name)))
    return ProgramRobot()
