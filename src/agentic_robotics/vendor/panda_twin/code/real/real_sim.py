"""RealSim: the box program's simulator interface (task_box.BoxSim: move, hold, ik, tcp_now, finger_width, the
cameras' poses ...) over a real arm, so that task_box.BoxProgram runs unchanged on the robot. Runs in the twin env
(teleavatar_client). The twin model stays as a kinematic mirror of the arm (its joints set to the measured ones;
its objects parked away: no ground truth) for the program's inverse kinematics and collision checks.

Motion: each S.move is planned whole first (inverse kinematics every 1/30 s along the straight line, as
TaskSim.move; a configuration flip -> BoxSim's joint-space move; else the part it can do, then Unreachable), checked
against the rig's limits (panda_fk.Limits, the same as the server's), slowed to the joint speed limit, and queued;
consecutive moves are sent as ONE trajectory (one confirmation) when something needs the arm's real state (a look,
the gripper's width, a hold, ...) or the gripper target changes. A free move ends with "settle" (the server takes
out the remaining joint error); the contact phases (set down, the lid, the push ...) do not. With task_timing (real_box
run, 10-30): each move timed on its own, the free ones (FREE_PHASES) at the twin's speed or a trapezoid at the joint
speed limit (task_times); a settle only after the descents whose end must be exact (SETTLE_PHASES).
Backends (the same calls): RemoteArm = arm_server.py over zmq (the robot, or its --fake arm); TwinArm = the twin's
physics as the "real" world (a dry run of this whole path: trajectories resampled at 15 Hz as the NUC does, the
gripper at its real speed, the cameras rendered from the world with the realistic D415 depth).
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import stack_bench as B  # noqa: E402
import task_bench as T  # noqa: E402
import task_box as TB  # noqa: E402
from panda_fk import Kin, Limits, load_calib, load_rig, resample, time_scale_for  # noqa: E402

DT = B.DT
CONTACT_PHASES = {"close", "close again", "set down", "unload", "release", "touch the lid", "tip the lid over", "off the lid",
                  "onto the lid", "press the lid down", "press it shut", "touch the box", "push it away", "push on",
                  "back off", "back off, turn the hand", "close the gripper",
                  "press the button", "off the button", "fingers apart",     # (11-02: press_skill.py)
                  "press", "drag", "back off what was pushed", "shake", "set it down", "set it back down", "back down",
                  "let go", "let go of the box",                             # (11-03: the clutter pick, clutter_bench.py)
                  "push"}                                                    # (11-04: clutter_bench.f_push)
# the task timing (RealSim.task_timing, real_box run; 10-30): moves in the open at the twin's speed x speed_free (a
# trapezoid at v_max where a joint would pass it), all the others (approaches, contacts) as before at speed; a settle
# only after the moves whose end must be exact (the server's catch-up after the others)
FREE_PHASES = {None, "up", "lift", "retreat", "above", "look into the box", "survey the target", "re-centre", "look",
               "carry over the box", "turn the hand", "transit", "retreat up after the stop", "over the point", "home",
               "above the button", "look closer"}
FREE_PREFIXES = ("inventory: look at", "look at ", "search for ")
SETTLE_PHASES = {"descend", "lower into the box", "down to it", "put it back down", "over the button", "onto the button",
                 "down beside it"}                                          # (11-04: f_push, the fingertips beside it)


def free_phase(ph):
    return ph in FREE_PHASES or str(ph).startswith(FREE_PREFIXES)


def trapezoid_dt(dq, v_lim, a_max):
    """Segment durations for the polyline with joint steps dq [n, 7]: along it at the joint speed limit v_lim, speeding
    up from rest and slowing down to rest at <= a_max rad/s2 per joint (the path parameter s = the time each step takes
    at v_lim; s' <= 1, s'' <= a_max / max(v_lim))."""
    ds = (np.abs(dq) / v_lim).max(1)
    s = np.r_[0.0, np.cumsum(ds)]; L = s[-1]
    a = a_max / float(np.max(v_lim))
    if L <= 0:
        return np.zeros(len(ds))
    if L >= 1.0 / a:                                                  # (cruises at v_lim)
        sr, T = 0.5 / a, L + 1.0 / a
        t = np.where(s <= sr, np.sqrt(2 * s / a), np.where(s >= L - sr, T - np.sqrt(np.maximum(2 * (L - s) / a, 0.0)), 1.0 / a + (s - sr)))
    else:                                                             # (too short: speeds up, slows down)
        T = 2 * math.sqrt(L / a)
        t = np.where(s <= L / 2, np.sqrt(2 * s / a), T - np.sqrt(np.maximum(2 * (L - s) / a, 0.0)))
    return np.diff(t)


def peak_accel(dq, dt):
    """The largest joint acceleration (rad/s2) of a move from rest to rest along joint steps dq [n, 7] of durations dt."""
    if len(dq) == 0:
        return 0.0
    v = np.asarray(dq) / np.maximum(np.asarray(dt), 1e-9)[:, None]
    dv = np.diff(np.vstack([np.zeros(v.shape[1]), v, np.zeros(v.shape[1])]), axis=0)
    span = np.r_[dt[:1] / 2, (np.asarray(dt[1:]) + np.asarray(dt[:-1])) / 2, dt[-1:] / 2]
    return float((np.abs(dv) / np.maximum(span, 1e-9)[:, None]).max())


class RobotStop(Exception):
    """The arm server refused or stopped a motion (limits, operator, blocked, force, reflex): the run ends here."""


# ---------------------------------------------------------------- backends
class RemoteArm:
    """arm_server.py over zmq REQ/REP (JSON)."""

    def __init__(self, host="127.0.0.1", port=5590, timeout_s=600):
        import zmq
        self.zmq = zmq
        self.ctx = zmq.Context()
        self.addr = f"tcp://{host}:{port}"
        self.timeout = int(timeout_s * 1000)
        self._open()

    def _open(self):
        self.sock = self.ctx.socket(self.zmq.REQ)
        self.sock.setsockopt(self.zmq.RCVTIMEO, self.timeout); self.sock.setsockopt(self.zmq.LINGER, 0)
        self.sock.connect(self.addr)

    def call(self, **req):
        self.sock.send(json.dumps(req).encode())
        try:
            return json.loads(self.sock.recv())
        except self.zmq.Again:
            self.sock.close(); self._open()
            raise RobotStop(f"no answer from the arm server at {self.addr} ({req.get('cmd')})")

    def hello(self):
        return self.call(cmd="hello")

    def state(self):
        r = self.call(cmd="state")
        if not r.get("ok"):
            raise RobotStop("arm state: " + str(r.get("why")))
        return r

    def execute(self, t, Q, grip=None, settle=False, phase=None, hold_after=0.1, guard=None):
        return self.call(cmd="execute", t=np.asarray(t, float).tolist(), q=np.asarray(Q, float).tolist(), grip=grip,
                         settle=bool(settle), phase=phase, hold_after=hold_after, guard=guard)

    def idle(self, T_):
        time.sleep(max(0.0, T_))

    def wait_gripper(self, timeout=4.0):
        return self.call(cmd="wait_gripper", timeout=timeout)

    def now(self):
        return time.time()


class TwinArm:
    """The twin's physics (a BoxSim with a layout) standing in for the real arm and the world: the same calls as
    RemoteArm, the same checks and the same 15 Hz resampled goals (linearly interpolated over each period, as the
    NUC's LINEAR_JOINT_POSITION with time_fraction 1)."""

    def __init__(self, world, rig, kin):
        self.w, self.rig = world, rig
        self.lim = Limits(rig, kin)
        c = rig["control"]
        self.rate = c["rate_hz"]; self.track_abort, self.force_abort = c["track_abort"], c.get("force_abort_twin", c["force_abort"])
        self.compliant = os.environ.get("TWIN_COMPLIANT", "0") == "1"   # (11-03) the arm as the real one in deoxys'
        if self.compliant:                                               # joint impedance: its kp / kd (the twin's own
            m_ = world.m                                                 # servo is 20-45x stiffer: a finger on a thing
            for i, a in enumerate(world.act):                            # read 100-180 N), the server's force abort, its
                m_.actuator_gainprm[a, 0] = c["kp"][i]                   # lag allowance (track_abort + lag_factor x
                m_.actuator_biasprm[a, 1] = -c["kp"][i]                  # lag_s x goal speed) and its catch-up
                m_.actuator_biasprm[a, 2] = -c["kd"][i]
            self.force_abort = c["force_abort"]
            self.lag_allow = np.asarray(c["lag_s"], float) * float(c.get("lag_factor", 1.2))
        self.hold_q = world.d.qpos[world.qadr].copy()
        self.g_target = float(world.d.ctrl[world.grip])
        self.g_speed = rig["gripper"]["move_speed"] / 2                  # per finger
        self.t_grip = -1.0
        arm = {b for b in range(world.m.nbody) if world.m.body(b).name.startswith("L_")}
        self.arm = arm
        m = world.m                                                     # (the twin's box-table contact can launch the
        self.box_b = {b for b in range(m.nbody) if m.body(b).name.startswith("obj_box_white")}   # box by itself: flagged)
        self.box_dof = m.jnt_dofadr[m.body_jntadr[m.body("obj_box_white").id]]
        self.artifact = None
        self.rec = self.state_log = None; self.moving = False; self.n_traj = 0

    def attach_recorder(self, rec, state_log=None):
        """Record the world's cameras (plain MuJoCo colour, not the splat composite; depth: the ideal one) and the arm's
        state at the recorder's rate in sim time, as the robot's cameras and arm server do in wall time."""
        w = self.w; m = w.m
        m.vis.global_.offwidth = max(m.vis.global_.offwidth, 640); m.vis.global_.offheight = max(m.vis.global_.offheight, 480)
        self.renderer = w.mj.Renderer(m, 480, 640)
        self.rec, self.state_log = rec, state_log
        self.rec_cam = None                                             # (11-05, TWIN_REC_VIEW=close: the "scene" stream
        la = getattr(self, "rec_lookat", None)                          # from a free camera 0.7-0.9 m from the pile
        if os.environ.get("TWIN_REC_VIEW") == "close" and la is not None:   # (rec_lookat, real_box.make_rig), not the
            c = w.mj.MjvCamera(); c.type = w.mj.mjtCamera.mjCAMERA_FREE    # D415 across the table: videos only)
            c.lookat[:] = la[0]; c.distance = la[1]; c.azimuth = 205.0; c.elevation = -40.0
            self.rec_cam = c
        self.rec_next = float(w.d.time)

    def _record(self):
        w = self.w; t = float(w.d.time); d = w.d
        if self.state_log is not None and t >= self.rec_next - 1e-9:
            p = 1.0 / self.rec.fps
            self.rec_next = self.rec_next + p if t - self.rec_next < p else t + p
            self.state_log.write(t, d.qpos[w.qadr], d.qvel[w.dadr], goal=d.ctrl[w.act], f=np.r_[self._force(), 0, 0, 0],
                                 width=float(w.finger_width()), mode=2, engaged=True, moving=self.moving, traj=self.n_traj)
        if self.rec is None:
            return
        mj = w.mj; rr = self.renderer
        for name, cam in (("scene", "d415"), ("wrist", "L_wrist")):
            if self.rec.wants(name, t):
                opt = mj.MjvOption(); opt.sitegroup[:] = 0
                rr.update_scene(d, camera=self.rec_cam if (name == "scene" and self.rec_cam is not None) else cam, scene_option=opt)
                rgb = rr.render().copy()
                dep = None
                if self.rec.depth_fps > 0:
                    rr.enable_depth_rendering(); rr.update_scene(d, camera=cam, scene_option=opt); dep = rr.render().copy()
                    rr.disable_depth_rendering(); dep = np.where(np.isfinite(dep) & (dep < 10), dep, np.inf).astype(np.float32)
                self.rec.offer(name, t, rgb, dep)

    def hello(self):
        return dict(ok=True, fake=True, twin=True, allow_motion=True, confirm=False)

    def _force(self):
        """The force on the hand (fingers + what they hold) from everything else, world frame (N, on the robot). A thing
        the fingers touch that rests on the table or in the box is not held: only its contact with the fingers counts
        (11-04: pressing on a thing in a pile counted its table support and its neighbours' pushes as on the hand: a
        drag's unload read 36-40 N, the force stop, 6 of 72 dry runs; the real arm's estimate is what the hand feels)."""
        w = self.w; m, d = w.m, w.d
        if not hasattr(self, "_table_g"):
            self._table_g = {g for g in range(m.ngeom) if m.geom(g).name == "table_top"}
        rests = set()
        for i in range(d.ncon):
            c = d.contact[i]
            for g_, o_ in ((c.geom1, c.geom2), (c.geom2, c.geom1)):
                if g_ in self._table_g or m.geom_bodyid[g_] in self.box_b:
                    b_ = m.geom_bodyid[o_]
                    if b_ not in self.box_b and b_ not in self.arm:
                        rests |= w.subtree_of(b_)
        held = set()
        for i in range(d.ncon):
            c = d.contact[i]; b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            if b1 in w.fingers and b2 not in self.arm and b2 not in rests:
                held |= w.subtree_of(b2)
            if b2 in w.fingers and b1 not in self.arm and b1 not in rests:
                held |= w.subtree_of(b1)
        A = w.fingers | held
        F, f6 = np.zeros(3), np.zeros(6)
        for i in range(d.ncon):
            c = d.contact[i]; b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            if (b1 in A) == (b2 in A) or (b1 in self.arm and b1 not in w.fingers) or (b2 in self.arm and b2 not in w.fingers):
                continue
            w.mj.mj_contactForce(m, d, i, f6)
            fw = c.frame.reshape(3, 3).T @ f6[:3]                         # on geom2, world frame
            F += fw if b2 in A else -fw
        return F

    def state(self):
        w = self.w
        return dict(ok=True, q=w.d.qpos[w.qadr].tolist(), dq=w.d.qvel[w.dadr].tolist(), width=float(w.finger_width()),
                    grasped=None, f_ext=np.r_[self._force(), 0, 0, 0].tolist(), mode=2, errors=[], t=float(w.d.time),
                    bias=[0.0] * 7, fault=None)

    def _frame(self, q):
        w = self.w
        w.d.ctrl[w.act] = q
        g = float(w.d.ctrl[w.grip])
        w.d.ctrl[w.grip] = g + float(np.clip(self.g_target - g, -self.g_speed * DT, self.g_speed * DT))
        m, d = w.m, w.d
        touch = any(((m.geom_bodyid[c.geom1] in self.box_b) != (m.geom_bodyid[c.geom2] in self.box_b)) and
                    (m.body(m.geom_bodyid[c.geom1]).name + m.body(m.geom_bodyid[c.geom2]).name).count("L_") > 0 for c in d.contact[:d.ncon])
        w.physics()
        if self.rec is not None or self.state_log is not None:
            self._record()
        if self.artifact is None and not touch and np.abs(d.qvel[self.box_dof:self.box_dof + 3]).max() > 1.0:
            self.artifact = f"t {d.time:.1f} s: the box jumped by itself (twin box-table contact instability, {np.abs(d.qvel[self.box_dof:self.box_dof + 3]).max():.1f} m/s, no arm contact)"
            print("TWIN ARTIFACT:", self.artifact, flush=True)

    def _grip(self, grip):
        if grip is None:
            return
        self.g_target = 0.0 if grip["kind"] == "grasp" else float(grip["width"]) / 2
        self.t_grip = float(self.w.d.time)

    def execute(self, t, Q, grip=None, settle=False, phase=None, hold_after=0.1, guard=None):
        w = self.w
        q_now = w.d.qpos[w.qadr].copy()
        why = self.lim.check_traj(t, Q, q_now)
        if why:
            return dict(ok=False, refused=True, why="refused by the server: " + why)
        self._grip(grip)
        self.n_traj += 1; self.moving = True
        try:
            return self._execute(t, Q, settle, hold_after, guard)
        finally:
            self.moving = False

    def _execute(self, t, Q, settle, hold_after, guard):
        w = self.w
        goals = resample(t, Q, self.rate)
        prev = np.asarray(Q[0], float); track = fmax = 0.0
        f0 = self._force(); n_sub = max(1, int(round(1 / self.rate / DT)))
        fh = [f0] * 3                                                   # (the force as the arm's estimate reports it:
                                                                        # filtered; the twin's contacts spike for a step)
        f_first = []                                                    # (11-04: the baseline too: the median of the
        contact = None                                                  # first 3 steps, not one step: a spike there read
        for g in goals:                                                 # as 35-40 N when the press was let go, 6 of 72 runs)
            for k in range(1, n_sub + 1):
                self._frame(prev + (g - prev) * k / n_sub)
            q = w.d.qpos[w.qadr]
            lag = float(np.abs(q - g).max()); track = max(track, lag)
            fh = fh[1:] + [self._force()]; f = np.median(fh, axis=0)
            if len(f_first) < 3:
                f_first.append(fh[-1])
                if len(f_first) == 3:
                    f0 = np.median([f0] + f_first, axis=0)
            fm = float(np.linalg.norm(f - f0)) if len(f_first) >= 3 else 0.0; fmax = max(fmax, fm)
            lag_bad = (np.abs(q - g) > self.track_abort + self.lag_allow * np.abs(g - prev) * self.rate).any() if self.compliant \
                else lag > self.track_abort
            if lag_bad or fm > self.force_abort:
                self.hold_q = q.copy()
                return dict(ok=False, aborted=True, why=f"stopped: lag {lag:.3f} rad, force {fm:.1f} N", q_end=q.tolist(), hold=q.tolist())
            if guard is not None:
                dz = float(w.d.site_xpos[w.tcp][2] - self._tcp_z(g))
                dfz = abs(float(f[2] - f0[2]))
                if dz > guard["dz"] or dfz > guard["df"]:
                    contact = dict(dz_mm=round(dz * 1000, 2), dfz_n=round(dfz, 2))
                    self.hold_q = q.copy()
                    for _ in range(int(0.1 / DT)):
                        self._frame(self.hold_q)
                    return dict(ok=True, guarded=True, contact=contact, q_end=w.d.qpos[w.qadr].tolist(), hold=self.hold_q.tolist(),
                                track_max=track, f_max=fmax)
            prev = g
        self.hold_q = np.asarray(Q[-1], float).copy()
        for _ in range(max(1, int(round(hold_after / DT)))):
            self._frame(self.hold_q)
        if self.compliant:                                              # (the server's catch-up: until within tol of
            cu = self.rig["control"].get("catch_up", {})                # the last goal or still, at most max_s)
            for _ in range(int(float(cu.get("max_s", 2.0)) / DT)):
                if np.abs(w.d.qpos[w.qadr] - self.hold_q).max() < float(cu.get("tol", 0.02)) or np.abs(w.d.qvel[w.dadr]).max() < 0.01:
                    break
                self._frame(self.hold_q)
        if settle:
            for _ in range(int(1.5 / DT)):
                if np.abs(w.d.qvel[w.dadr]).max() < 0.02:
                    break
                self._frame(self.hold_q)
        return dict(ok=True, q_end=w.d.qpos[w.qadr].tolist(), hold=self.hold_q.tolist(), track_max=track, f_max=fmax,
                    guarded=guard is not None, contact=None)

    def _tcp_z(self, q):
        k = self.w.k
        k.qpos[:] = self.w.d.qpos; k.qpos[self.w.qadr] = q
        self.w.mj.mj_kinematics(self.w.m, k)
        return float(k.site_xpos[self.w.tcp][2])

    def idle(self, T_):
        for _ in range(max(0, int(round(T_ / DT)))):
            self._frame(self.hold_q)

    def wait_gripper(self, timeout=4.0):
        w = self.w; hist = []; t0 = float(w.d.time)
        while True:
            self._frame(self.hold_q)
            now = float(w.d.time); hist.append((now, w.finger_width()))
            hist = [h for h in hist if now - h[0] <= 0.25]
            if now - hist[0][0] >= 0.2 and max(h[1] for h in hist) - min(h[1] for h in hist) < 0.0002 and now - self.t_grip > 0.3:
                return dict(ok=True, width=float(w.finger_width()), waited=round(now - t0, 2))
            if now - t0 > timeout:
                return dict(ok=True, width=float(w.finger_width()), timeout=True)

    def now(self):
        return float(self.w.d.time)


# ---------------------------------------------------------------- cameras
class RealSenseCams:
    """Both D415s (rig.json cameras: serials), colour 640x480 + depth aligned to it, 30 fps, each in its own thread
    keeping the newest frame (host time of arrival). get(cam, after): the first frame that arrived after `after`."""

    NAMES = {"d415": "scene", "L_wrist": "wrist"}

    def __init__(self, rig):
        import pyrealsense2 as rs
        self.rs = rs
        c = rig["cameras"]
        self.cams, self.latest, self.threads = {}, {}, []
        self.cond = threading.Condition()
        self.rec = None                                                 # (recorder.Recorder: every frame is offered)
        self.running = True
        for cam, key in self.NAMES.items():
            cfg = rs.config(); cfg.enable_device(c[key])
            cfg.enable_stream(rs.stream.color, c["width"], c["height"], rs.format.rgb8, c["fps"])
            cfg.enable_stream(rs.stream.depth, c["width"], c["height"], rs.format.z16, c["fps"])
            pipe = rs.pipeline(); prof = pipe.start(cfg)
            ci = prof.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
            scale = prof.get_device().first_depth_sensor().get_depth_scale()
            self.cams[cam] = dict(pipe=pipe, align=rs.align(rs.stream.color), scale=scale, serial=c[key],
                                  K=(ci.width, ci.height, ci.fx, ci.fy, ci.ppx, ci.ppy), coeffs=list(ci.coeffs), model=str(ci.model))
            th = threading.Thread(target=self._loop, args=(cam,), daemon=True); th.start(); self.threads.append(th)
        t0 = time.time()
        while len(self.latest) < len(self.cams) and time.time() - t0 < 10:
            time.sleep(0.1)
        if len(self.latest) < len(self.cams):
            raise RuntimeError(f"no frames from {set(self.cams) - set(self.latest)} after 10 s")
        time.sleep(1.0)                                                 # (auto exposure)

    def _loop(self, cam):
        c = self.cams[cam]
        while self.running:
            try:
                fs = c["pipe"].wait_for_frames(2000)
            except RuntimeError:
                continue
            al = c["align"].process(fs)
            col, dep = al.get_color_frame(), al.get_depth_frame()
            if not col or not dep:
                continue
            rgb = np.asanyarray(col.get_data()).copy()
            z = np.asanyarray(dep.get_data()).astype(np.float32) * c["scale"]
            z[z <= 0] = np.inf
            t = time.time()
            with self.cond:
                self.latest[cam] = dict(t=t, rgb=rgb, depth=z)
                self.cond.notify_all()
            rec = self.rec
            if rec is not None:
                rec.offer(self.NAMES[cam], t, rgb, z)

    def get(self, cam, after=0.0, timeout=2.0):
        t0 = time.time()
        with self.cond:
            while self.latest.get(cam) is None or self.latest[cam]["t"] <= after:
                if not self.cond.wait(timeout=max(0.01, timeout - (time.time() - t0))):
                    raise RuntimeError(f"no new frame from {cam} in {timeout} s")
            f = self.latest[cam]
        return dict(rgb=f["rgb"], depth=f["depth"], K=self.cams[cam]["K"], t=f["t"])

    def attach_recorder(self, rec, state_log=None):
        self.rec = rec

    def info(self):
        return {cam: dict(serial=c["serial"], K=c["K"], coeffs=c["coeffs"], model=c["model"], depth_scale=c["scale"]) for cam, c in self.cams.items()}

    def close(self):
        self.running = False
        for c in self.cams.values():
            try:
                c["pipe"].stop()
            except Exception:
                pass


class TwinCams:
    """The world's cameras (the twin): task_box.BoxPerception's observe on the world sim (D415_DEPTH=real: the fitted
    D415 depth and the cameras' calibration error), its image and depth only (the pose: RealSim's, as on the robot)."""

    def __init__(self, wper):
        self.wper = wper

    def get(self, cam, after=0.0, timeout=2.0):
        o = self.wper.observe(cam)
        return dict(rgb=o["rgb"], depth=o["depth"], K=tuple(o["K"]), t=float(self.wper.S.d.time))

    def info(self):
        return {}

    def close(self):
        pass


# ---------------------------------------------------------------- the simulator interface over a real arm
class RealSim(TB.BoxSim):
    def __init__(self, backend, cams, rig=None, calib=None, speed=None, v_max=None, log_dir=None):
        TB.BoxSim.__init__(self)
        m, d, mj = self.m, self.d, self.mj
        self.rig = load_rig() if rig is None else rig
        self.calib = load_calib() if calib is None else calib
        self.kin = Kin(self.rig, self.calib)
        self.lim = Limits(self.rig, self.kin)
        self.backend, self.cams = backend, cams
        self.speed = float(self.rig.get("speed", 0.5) if speed is None else speed)
        self.speed_free, self.a_max = float(self.rig.get("speed_free", 1.0)), float(self.rig.get("a_max", 0.6))
        self.task_timing = False                                        # (real_box run: True; see FREE_PHASES)
        self.v_lim = np.asarray(self.lim.v_max if v_max is None else np.minimum(self.lim.v_max, v_max), float) * 0.95
        self.log_dir = None if log_dir is None else Path(log_dir)
        if self.log_dir is not None:
            (self.log_dir / "frames").mkdir(parents=True, exist_ok=True)
        # the mirror: the adapter between flange and hand, the joint limits the server enforces, no objects
        m.body_pos[m.body("L_hand").id][2] += self.kin.adapter_dz
        jid = [m.joint(f"L_joint{i}").id for i in range(1, 8)]
        m.jnt_range[jid] = np.stack([self.lim.lo, self.lim.hi], 1)
        self.lo, self.hi = self.lim.lo.copy(), self.lim.hi.copy()
        names = [m.body(b).name for b in range(m.nbody)]                # the two arms' geoms 1.5 cm fatter each (in the
        self.armL = {b for b in range(m.nbody) if names[b].startswith("L_")}        # mirror only): closer than 3 cm =
        self.armR = {b for b in range(m.nbody) if names[b].startswith("R_")}        # a contact (arm_clash)
        for g in range(m.ngeom):
            if m.geom_bodyid[g] in self.armL or m.geom_bodyid[g] in self.armR:
                m.geom_margin[g] += 0.015
        for j, b in enumerate(b for b in range(m.nbody) if m.body(b).name.startswith("obj_") and m.body_jntadr[b] >= 0
                                and m.jnt_type[m.body_jntadr[b]] == 0):
            a = m.jnt_qposadr[m.body_jntadr[b]]
            d.qpos[a:a + 3] = [20.0 + 0.5 * j, 20.0, 0.0]               # (far away: no object in the checks)
        self.n_frame, self.events_t0 = 0, backend.now()
        self._pend = None
        self._phase_now = None
        self._grip_wait = False
        st = self.sync()
        self.q_sent = np.asarray(st["q"], float).copy()
        self.q_cmd = self.q_sent.copy()
        self.p_cmd = self.d.site_xpos[self.tcp].copy(); self.R_cmd = self.d.site_xmat[self.tcp].reshape(3, 3).copy()
        w = st.get("width")
        self.g_cmd = self.g_sent = B.OPEN_W if w is None else min(B.OPEN_W, float(w) / 2)
        d.ctrl[self.grip] = self.g_cmd
        self.rec, self.timeline = [], []
        self.straight = False
        self.last_state = st

    # -- recording (recorder.py): the cameras as video, the arm's state (the twin's; the robot's: the arm server's log)
    def start_recording(self, fps=15.0, depth_fps=0.0, crf=23):
        from recorder import Recorder, StateLog
        if self.log_dir is None:
            return None
        twin = isinstance(self.backend, TwinArm)
        clock = "twin sim time" if twin else "unix"
        self.recorder = Recorder(self.log_dir, self.events_t0, fps, depth_fps, crf, clock)
        self.state_log = StateLog(self.log_dir / "state", clock=clock, hz=fps, source="the twin (TwinArm)") if twin else None
        for src in (self.cams, self.backend):
            if hasattr(src, "attach_recorder"):
                src.attach_recorder(self.recorder, self.state_log)
        return self.recorder

    def stop_recording(self):
        """Finish the video files (also on a crash or Ctrl-C: real_box.main calls it). -> video/meta.json's dict."""
        rec = getattr(self, "recorder", None)
        if rec is None:
            return None
        for src in (self.cams, self.backend):
            if getattr(src, "rec", None) is rec:
                src.rec = None
        if isinstance(self.backend, TwinArm):
            self.backend.state_log = None
        meta = rec.close()
        if self.state_log is not None:
            self.state_log.close()
        self.recorder = None
        return meta

    # -- state
    def sync(self):
        """Read the arm; the mirror's joints (and fingers) = the measured ones."""
        st = self.backend.state()
        d = self.d
        d.qpos[self.qadr] = st["q"]
        if st.get("width") is not None:
            d.qpos[self.fing] = float(st["width"]) / 2
        d.time = float(self.backend.now() - self.events_t0)
        self.mj.mj_forward(self.m, d)
        self.last_state = st
        return st

    def resync(self):
        """The command = where the arm is (after a move it could not finish, blocked: the next trajectory starts at
        the measured joints, not at the goal it could not reach: the server would refuse that start, > start_tol)."""
        self.flush()
        st = self.sync()
        self.q_sent = np.asarray(st["q"], float).copy(); self.q_cmd = self.q_sent.copy()
        self.p_cmd = self.d.site_xpos[self.tcp].copy(); self.R_cmd = self.d.site_xmat[self.tcp].reshape(3, 3).copy()

    def tcp_now(self):
        self.flush(); self.sync()
        return self.d.site_xpos[self.tcp].copy()

    def finger_width(self):
        self.flush()
        if self._grip_wait:
            r = self.backend.wait_gripper(5.0)
            self._grip_wait = False
            self.timeline.append((float(self.d.time), "event", f"gripper {r.get('width', 0) * 1000:.1f} mm"))
        st = self.sync()
        return float(st["width"]) if st.get("width") is not None else 0.0

    def pose(self, k):
        raise RuntimeError("no ground truth on the real robot (S.pose)")

    def settle_wait(self, tol=0.01, timeout=1.5):
        """Until the arm is still (joint speeds < tol rad/s)."""
        t0 = time.time()
        while True:
            st = self.sync()
            if np.abs(st["dq"]).max() < tol or time.time() - t0 > timeout:
                return st
            self.backend.idle(0.03)

    # -- cameras
    def nominal_camera(self, cam):
        """The camera's pose the program uses: the calibrated mount (calib.json) if there is one, else the twin's."""
        if cam == "L_wrist" and "wrist_cam" in self.calib:
            hb = self.m.body("L_hand").id
            Hm = np.eye(4); Hm[:3, :3] = self.d.xmat[hb].reshape(3, 3); Hm[:3, 3] = self.d.xpos[hb]
            Tc = Hm @ np.asarray(self.calib["wrist_cam"]["T_hand_cam"], float)
            return Tc[:3, :3].copy(), Tc[:3, 3].copy()
        if cam == "d415" and "scene_cam" in self.calib:
            Tc = np.asarray(self.calib["scene_cam"]["T_world_cam"], float)
            return Tc[:3, :3].copy(), Tc[:3, 3].copy()
        return super().nominal_camera(cam)

    def observe_frame(self, cam):
        """A frame from cam once the arm is still: depth (m, inf = none), colour, intrinsics, and the camera's pose
        from the measured joints. Saved under log_dir/frames."""
        self.flush()
        self.settle_wait()
        f = self.cams.get(cam, after=self.backend.now() + 0.04)
        st = self.sync()
        R, eye = self.nominal_camera(cam)
        obs = dict(cam=cam, rgb=f["rgb"], depth=f["depth"].astype(np.float32), K=tuple(float(x) for x in f["K"]), R=R, eye=eye,
                   t=float(self.d.time), q=list(st["q"]))
        if self.log_dir is not None:
            import cv2
            n = self.n_frame; self.n_frame += 1
            base = self.log_dir / "frames" / f"{n:03d}_{cam}"
            if f["rgb"] is not None:
                cv2.imwrite(str(base) + "_rgb.png", cv2.cvtColor(f["rgb"], cv2.COLOR_RGB2BGR))
            z = f["depth"]; cv2.imwrite(str(base) + "_depth.png", np.where(np.isfinite(z), np.clip(z * 1000, 0, 65535), 0).astype(np.uint16))
            json.dump(dict(K=obs["K"], R=R.tolist(), eye=eye.tolist(), q=obs["q"], t=obs["t"], phase=self._phase_now),
                      open(str(base) + ".json", "w"))
        return obs

    def ik(self, pos, R, q0, iters=100):
        """Sim.ik; a solution outside the rig's safety limits (joints, the TCP's box, the table, the keep-out boxes)
        reports a 1 m error: the program's own searches (look poses, hand angles, drop spots, transits) then skip it,
        as they skip what the arm cannot reach."""
        q, e = super().ik(pos, R, q0, iters)
        if e < 0.01:
            why = self.lim.check_q(q) or ("within 3 cm of the right arm" if self.arm_clash(q) else None)
            if why is not None:
                self.last_unsafe = why
                if os.environ.get("REAL_DEBUG_IK"):
                    print("unsafe IK:", np.round(pos, 3).tolist(), why, flush=True)
                return q, 1.0
        return q, e

    def arm_clash(self, q):
        """Would the left arm at joints q come within 3 cm of the right arm (parked as in the twin)? (In the twin the
        transit behind an open lid twisted the left elbow into the right arm's link 5: the program's straight moves do
        not check it, the twin's stiff arms just pushed each other.)"""
        m, k = self.m, self.k
        k.qpos[:] = self.d.qpos; k.qpos[self.qadr] = q
        self.mj.mj_kinematics(m, k); self.mj.mj_collision(m, k)
        for c in k.contact[:k.ncon]:
            b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            if (b1 in self.armL and b2 in self.armR) or (b2 in self.armL and b1 in self.armR):
                return True
        return False

    # -- motion
    def _set_phase(self, phase):
        if phase:
            self._phase_now = phase
            self.timeline.append((float(self.d.time), "phase", phase))

    def plan_line(self, p0, R0, q0, target, R, T_):
        """move's straight line from (p0, R0) at joints q0 to (target, R): inverse kinematics every DT along it, each step
        seeded from the last (the safety limits and the right arm's 3 cm included: RealSim.ik). -> (Q, fail = None or
        (IK error, joint step) where it stopped, T_ as stretched, n steps). (Also real_box's arm_ok: the way down to a
        spot checked exactly as it will be moved.)"""
        target = np.asarray(target, float)
        rv = T.rotvec(R0.T @ R)
        T_ = max(T_, float(np.linalg.norm(rv)) * 1.0, float(np.linalg.norm(target - p0)) * 3.0)
        n = max(int(round(T_ / DT)), 1)
        Q, fail = [np.asarray(q0, float).copy()], None
        for i in range(1, n + 1):
            s = i / n; s = s * s * (3 - 2 * s)
            q, e = self.ik(p0 + (target - p0) * s, R0 @ B.small_rot(rv * s), Q[-1])
            step = float(np.abs(q - Q[-1]).max())
            if e > 0.005 or step > 0.15:
                fail = (e, step); break
            Q.append(q)
        return Q, fail, T_, n

    def move(self, target, R, grip, T_, phase=None):
        """task_bench.TaskSim.move + task_box.BoxSim.move's joint-space fallback, planned whole before anything is sent."""
        self._set_phase(phase)
        target = np.asarray(target, float)
        p0, R0 = self.p_cmd.copy(), self.R_cmd.copy()
        Q, fail, T_, n = self.plan_line(p0, R0, self.q_cmd, target, R, T_)
        if fail is None and any(self.arm_clash(q) for q in Q[5::5] + [Q[-1]]):
            raise T.Unreachable(f"{phase or 'move'} to {np.round(target, 3).tolist()}: the left arm would come within 3 cm of the right arm")
        if fail is None:
            self._queue(Q[1:], grip, T_ / n)
            self.p_cmd, self.R_cmd, self.g_cmd = target.copy(), R, grip
            self.q_cmd = Q[-1].copy(); self.d.ctrl[self.grip] = grip
            return 0.0
        e, step = fail
        why = f"{phase or 'move'} to {np.round(target, 3).tolist()} (IK error {e * 1000:.0f} mm, joint step {step:.2f} rad)"
        if not self.straight and step > 0.15 and float(np.linalg.norm(target - self.p_cmd)) <= 0.6:   # (BoxSim.move)
            q1, err = self.ik(target, R, self.q_cmd)
            if err > 0.002:
                q1, err = self.ik(target, R, self.q_home)
            if err <= 0.002:
                self.timeline.append((float(self.d.time), "phase", (phase or "move") + " (joint space)"))
                q0 = self.q_cmd.copy()
                nj = max(int(max(T_, 1.2) / DT), 1)
                Qj = [q0 + (q1 - q0) * (k / nj) ** 2 * (3 - 2 * k / nj) for k in range(1, nj + 1)]
                if any(self.arm_clash(q) for q in Qj[::5] + [Qj[-1]]):
                    raise T.Unreachable(why + "; in joint space the left arm would come within 3 cm of the right arm")
                self._queue(Qj, grip, DT)
                self.p_cmd, self.R_cmd, self.g_cmd = target.copy(), R, grip
                self.q_cmd = q1.copy(); self.d.ctrl[self.grip] = grip
                return 0.0
        if len(Q) > 1:                                                  # (TaskSim.move: the part it can do, then stop)
            self._queue(Q[1:], self.g_cmd + (grip - self.g_cmd) * min(1.0, 2 * (len(Q) - 1) / n), T_ / n)
            self.q_cmd = Q[-1].copy()
        self.flush()
        self.p_cmd = self.tcp_now()
        self.hold(0.3)
        raise T.Unreachable(why)

    def move_joint(self, target, R, grip, T_, phase=None, q_goal=None):
        """To (target, R) by a smooth joint-space interpolation from the current joints to the IK solution nearest them
        (or q_goal): no straight line in space, so no joint wanders out and back on the way (the calibration views)."""
        self._set_phase(phase)
        q1, err = (np.asarray(q_goal, float), 0.0) if q_goal is not None else self.ik(np.asarray(target, float), R, self.q_cmd)
        if err > 0.002:
            raise T.Unreachable(f"{phase or 'move'} to {np.round(target, 3).tolist()} (IK error {err * 1000:.0f} mm)")
        q0 = self.q_cmd.copy()
        nj = max(int(max(T_, 1.2) / DT), 1)
        Qj = [q0 + (q1 - q0) * (k / nj) ** 2 * (3 - 2 * k / nj) for k in range(1, nj + 1)]
        if any(self.arm_clash(q) for q in Qj[::5] + [Qj[-1]]):
            raise T.Unreachable(f"{phase or 'move'}: in joint space the left arm would come within 3 cm of the right arm")
        self._queue(Qj, grip, DT)
        self.p_cmd, self.R_cmd, self.g_cmd = np.asarray(target, float).copy(), R, grip
        self.q_cmd = q1.copy(); self.d.ctrl[self.grip] = grip
        return 0.0

    def physics(self, n_frames=1):
        """(BoxProgram.joint_move sets ctrl and steps the physics frame by frame: each frame = one waypoint.)"""
        for _ in range(n_frames):
            self._queue([self.d.ctrl[self.act].copy()], float(self.d.ctrl[self.grip]), DT, kind="frame")

    def hold(self, T_):
        self.flush()
        if T_ > 0:
            self.backend.idle(T_)
        self.d.time = float(self.backend.now() - self.events_t0)

    def _queue(self, Qs, grip, dt, kind="move"):
        """Add waypoints (each dt after the last) to the pending trajectory; a new gripper target starts a new one.
        kind: "move" (one S.move: a move of its own for the task timing), "frame" (BoxProgram.joint_move's frames, one
        by one: consecutive ones = one transit, high over everything), "guarded" (its own speed)."""
        if self._pend is not None and abs(grip - self._pend["grip_end"]) > 1e-6:
            self.flush()
        if self._pend is None:
            gcmd = None
            if abs(grip - self.g_sent) > 1e-6:
                gcmd = dict(kind="grasp") if grip < 0.0005 else dict(kind="move", width=min(2 * grip, self.rig["gripper"]["max_width"]))
            self._pend = dict(t=[0.0], tp=[0.0], Q=[self.q_sent.copy()], grip=gcmd, grip_end=grip, phases=[], phase_end=None,
                              moves=[], last_kind=None)
        P = self._pend
        if kind != "frame" or P["last_kind"] != "frame":               # (the moves: first waypoint index - 1, class)
            P["moves"].append((len(P["Q"]) - 1, "free" if kind == "frame" or free_phase(self._phase_now) else kind if kind == "guarded" else "slow"))
        P["last_kind"] = kind
        for q in Qs:
            P["t"].append(P["t"][-1] + dt / self.speed); P["tp"].append(P["tp"][-1] + dt); P["Q"].append(np.asarray(q, float).copy())
        if self._phase_now and (not P["phases"] or P["phases"][-1] != self._phase_now):
            P["phases"].append(self._phase_now)
        P["phase_end"] = self._phase_now

    def flush(self, guard=None):
        """Send the pending trajectory (slowed to the joint speed limit) and wait for it."""
        P, self._pend = self._pend, None
        if P is None:
            return None
        t, Q = np.asarray(P["t"]), np.asarray(P["Q"])
        moved = len(Q) > 1 and float(np.abs(Q - Q[0]).max()) > 1e-5
        task = self.task_timing and guard is None
        t = self.task_times(P) if task else t * time_scale_for(t, Q, self.v_lim)
        why = self.lim.check_traj(t, Q, None, every=1)
        if why is None:                                               # the right arm: no waypoint within 3 cm, unless
            idx = list(range(0, len(Q), 5)) + [len(Q) - 1]            # the move starts there and only leaves
            cl = [self.arm_clash(Q[i]) for i in idx]
            if cl[-1] or any(cl[j] and not cl[j - 1] for j in range(1, len(cl))):
                why = "the left arm would come within 3 cm of the right arm"
        if why:
            raise RobotStop(f"unsafe trajectory planned ({', '.join(P['phases']) or 'move'}): {why}")
        phase = ", ".join(P["phases"][:3]) + (f" (+{len(P['phases']) - 3})" if len(P["phases"]) > 3 else "") or None
        settle = moved and guard is None and (P["phase_end"] in SETTLE_PHASES if task else P["phase_end"] not in CONTACT_PHASES)
        r = self.backend.execute(t, Q, grip=P["grip"], settle=settle, phase=phase, hold_after=0.1, guard=guard)
        if not r.get("ok"):
            self.rec.append(dict(t=float(self.d.time), phase=phase, n=len(Q), dur=float(t[-1]), stopped=r.get("why")))
            raise RobotStop(f"{phase}: {r.get('why')}")
        if P["grip"] is not None:
            self._grip_wait = True
        self.g_sent = P["grip_end"]
        self.q_sent = np.asarray(r.get("hold", Q[-1]), float)
        self.rec.append(dict(t=float(self.d.time), phase=phase, n=len(Q), dur=float(t[-1]), settle=r.get("settle"),
                             track_max=r.get("track_max"), f_max=r.get("f_max"), guarded=r.get("guarded"), contact=r.get("contact")))
        self.sync()
        return r

    def task_times(self, P):
        """The task timing of a pending trajectory: each move (P["moves"]) timed on its own (the 10-29 timing stretched
        the whole trajectory to its fastest part, e.g. the lid's 4-degree steps as slow as the transit before them): at
        its planned times / speed (a slow one: approaches, contacts) or / speed_free (a free one), or where a joint
        would pass v_lim, the trapezoid (trapezoid_dt), no step faster than those planned times; then, if a joint would
        speed up or slow down faster than a_max anywhere in it (its path curved in joint space; the twin's short moves
        at full speed), the whole move slower, to a_max."""
        Q, tp = np.asarray(P["Q"], float), np.asarray(P["tp"], float)
        t = np.zeros(len(Q))
        ends = [i for i, _ in P["moves"][1:]] + [len(Q) - 1]
        for (i0, cls), i1 in zip(P["moves"], ends):
            if i1 <= i0:
                continue
            dq = np.diff(Q[i0:i1 + 1], axis=0)
            dt = np.diff(tp[i0:i1 + 1]) / (self.speed_free if cls == "free" else self.speed)
            dt = np.maximum(trapezoid_dt(dq, self.v_lim, self.a_max), dt)   # (never faster than its planned times /
            a = peak_accel(dq, dt)                                    # speed, e.g. its last cm down; the twin's
            if a > self.a_max:                                        # short moves at full speed, a curved joint
                dt = dt * math.sqrt(a / self.a_max)                   # path: slower, to a_max)
            t[i0 + 1:i1 + 1] = t[i0] + np.cumsum(dt)
        return t

    def guarded_line(self, target, R, speed_mps, phase):
        """A straight line to target at speed_mps that stops at the first contact (server: the hand held up > dz or the
        force estimate up > df). -> dict(contact=..., tcp=where it stopped)."""
        self.flush()
        self._set_phase(phase)
        target = np.asarray(target, float); p0 = self.p_cmd.copy()
        n = max(int(round(np.linalg.norm(target - p0) / speed_mps / DT)), 1)
        Q = [self.q_cmd.copy()]
        for i in range(1, n + 1):
            q, e = self.ik(p0 + (target - p0) * i / n, R, Q[-1])
            if e > 0.002 or np.abs(q - Q[-1]).max() > 0.15:
                raise T.Unreachable(f"{phase}: guarded line not reachable")
            Q.append(q)
        self._queue(Q[1:], self.g_cmd, DT * self.speed, kind="guarded")  # (its own speed: not slowed further)
        g = self.rig["contact"]
        r = self.flush(guard=dict(dz=g["dz"], df=g["df"]))
        self.q_cmd = self.q_sent.copy()
        self.p_cmd = self.d.site_xpos[self.tcp].copy(); self.R_cmd = R
        return dict(contact=r.get("contact"), tcp=self.p_cmd.copy())

    def force_now(self):
        return np.asarray(self.sync()["f_ext"][:3], float)
