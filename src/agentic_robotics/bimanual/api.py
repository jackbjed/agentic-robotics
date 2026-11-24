"""What a controller may use of a bimanual environment (docs/bimanual.md): the robot's own sensing and commands, in
MILLIMETRES and DEGREES, table frame. A program written for these environments gets a Robot and nothing else; the
model tools (tools.py) are the same calls as text. No true object state passes through here.

    from agentic_robotics import bimanual
    from agentic_robotics.bimanual.api import Robot
    robot = Robot(bimanual.make("sim_bimanual_grippers", seed=0))
    f = robot.look()["scene"]; f.rgb; f.depth; f.locate(320, 240)
    robot.move("right", (-250, -180, 300), (0, 0, -1), (0, 1, 0)); robot.set_gripper("right", 0); robot.state("right")

Arms: "right" | "left". Every motion command returns dict(done, moved, why, curved):
    done   the target was reached
    moved  False when the command was refused: nothing moved (why says why)
    why    "" | "contact" (a stop_on_contact move stopped at a touch) | the safety stop's or the refusal's text
    curved the move was made by turning the arm's joints (a curved path, not a line)
"""
from __future__ import annotations

import math

import numpy as np

from .core import ARMS, CAMS, GUARD_N, MIN_Z, SAFETY_N, SIDE, Stop

TIP_NOISE = 0.1          # N: the Sharpa fingertips' tactile reading


class TimeUp(Exception):
    """The episode's robot time is spent: no further command runs."""


DEPTH_BINS = (0, 400, 700, 1000, 1500, 100000)                    # mm: the distances depth_report gives the noise for


def _plane_rms(d, ok, k, step):
    """The RMS (mm) of the depth about its local plane in k x k windows (every `step` px) that have depth everywhere
    and no depth step -> (the windows' centre depths, their RMS)."""
    H, W = d.shape; h = k // 2
    v, u = np.mgrid[-h:h + 1, -h:h + 1]
    A = np.c_[u.ravel(), v.ravel(), np.ones(k * k)]; P = np.linalg.pinv(A)
    zs, rs = [], []
    for y in range(h, H - h, step):
        for x in range(h, W - h, step):
            if not ok[y - h:y + h + 1, x - h:x + h + 1].all():
                continue
            w = d[y - h:y + h + 1, x - h:x + h + 1].ravel()
            z = float(w.mean())
            r = w - A @ (P @ w)
            if float(np.abs(r).max()) > 0.04 * z:                   # (a depth step or an edge: not noise)
                continue
            zs.append(z); rs.append(float(np.sqrt((r ** 2).mean())))
    return np.array(zs), np.array(rs)


def depth_report(depth):
    """Numbers that describe a depth image (H, W; mm; nan or 0 = no depth) as a sensor's output, the same for a real
    camera's and the simulator's (a program: frame.depth_report(); a session: read_real_depth):
      valid                 the part of the pixels that have depth
      left_band_px          the columns at the image's left without depth (a stereo camera's blind band)
      valid_right_of_band   the part with depth, right of that band
      holes, hole_px        the regions without depth right of the band (of 20 px and more): how many, their median size
      speckle               the part of the pixels right of the band that are holes of under 5 px
      nearest_mm            the nearest depth (the 0.5 % quantile)
      noise_mm              by distance (mm): [windows, fine, coarse]: the median RMS of the depth about its local plane
                            in 7 x 7 px windows (the pixel-to-pixel noise) and in 31 x 31 px windows (with the noise
                            that is smooth over a few pixels), over flat surfaces; None where under 5 windows."""
    import cv2
    d = np.asarray(depth, float)
    ok = np.isfinite(d) & (d > 0)
    H, W = d.shape
    col = ok.mean(0)
    band = int(np.argmax(col > 0.5)) if (col > 0.5).any() else W
    right = ok[:, band:]
    out = dict(valid=round(float(ok.mean()), 3), left_band_px=band, valid_right_of_band=round(float(right.mean()), 3) if right.size else 0.0)
    if right.size:
        _, _, st, _ = cv2.connectedComponentsWithStats((~right).astype(np.uint8), connectivity=8)
        areas = st[1:, cv2.CC_STAT_AREA]
        big = areas[areas >= 20]
        out.update(holes=int(len(big)), hole_px=int(np.median(big)) if len(big) else 0, speckle=round(float(areas[areas < 5].sum()) / right.size, 4))
    out["nearest_mm"] = round(float(np.quantile(d[ok], 0.005)), 0) if ok.any() else None
    dz = np.where(ok, d, 0.0)
    fine, coarse = _plane_rms(dz, ok, 7, 6), _plane_rms(dz, ok, 31, 12)
    noise = {}
    for lo, hi in zip(DEPTH_BINS[:-1], DEPTH_BINS[1:]):
        a, b = (fine[0] >= lo) & (fine[0] < hi), (coarse[0] >= lo) & (coarse[0] < hi)
        key = f"{lo}-{hi}" if hi < 100000 else f"{lo}+"
        noise[key] = [int(a.sum()), round(float(np.median(fine[1][a])), 2) if a.sum() >= 5 else None,
                      round(float(np.median(coarse[1][b])), 2) if b.sum() >= 5 else None]
    out["noise_mm"] = noise
    return out


class Frame:
    """One camera frame. rgb (H, W, 3 uint8); depth (H, W) mm along the optical axis, nan = none; fx, fy, cx, cy;
    eye (mm) and R (3x3, camera to table frame; camera axes x right, y down, z forward): the camera's pose as the robot
    believes it (the calibration is off by a few mm and up to a degree, the same all episode)."""

    def __init__(self, o):
        self._o = o
        self.camera, self.rgb, self.t = o["camera"], o["rgb"], o["t"]
        self.depth = o["depth"] * 1000.0
        self.width, self.height, self.fx, self.fy, self.cx, self.cy = o["K"]
        self.eye, self.R = o["eye"] * 1000.0, o["R"]

    def locate(self, u, v):
        """The 3D point (mm, table frame) of the surface seen at pixel (u, v): the median depth in a 7x7 window; None
        where there is no depth."""
        from .core import BiCore
        X = BiCore.unproject(self._o, float(u), float(v))
        return None if X is None else X * 1000.0

    def points(self):
        """(H, W, 3) mm, table frame: every pixel's 3D point (nan where there is no depth)."""
        v, u = np.mgrid[0:self.height, 0:self.width]
        z = self.depth
        C = np.dstack([(u - self.cx) / self.fx * z, (v - self.cy) / self.fy * z, z])
        return self.eye + C @ self.R.T

    def project(self, xyz):
        """Pixels (N, 2) and depths (N, mm) of table-frame points (mm) in this frame."""
        c = (np.atleast_2d(np.asarray(xyz, float)) - self.eye) @ self.R; z = c[:, 2]
        return np.c_[self.fx * c[:, 0] / z + self.cx, self.fy * c[:, 1] / z + self.cy], z

    def depth_report(self):
        """Numbers that describe this frame's depth image as a sensor's output (valid part, blind band, holes,
        noise by distance): api.depth_report; the same numbers a real look's read_real_depth gives."""
        return depth_report(self.depth)

    def with_pixels(self, rgb=None, depth=None):
        """This frame with other pixels (a world's code: practice.py): rgb (H, W, 3 uint8) and/or depth (H, W; mm,
        nan = none), of this frame's size; the camera, its pose and the time stay."""
        o = dict(self._o)
        if rgb is not None:
            rgb = np.ascontiguousarray(np.asarray(rgb, np.uint8))
            if rgb.shape != self.rgb.shape:
                raise ValueError(f"rgb: {self.rgb.shape} uint8")
            o["rgb"] = rgb
        if depth is not None:
            depth = np.asarray(depth, float)
            if depth.shape != self.depth.shape:
                raise ValueError(f"depth: {self.depth.shape}, mm, nan where there is none")
            o["depth"] = np.where(np.isfinite(depth) & (depth > 0), depth, np.nan) / 1000.0
        return Frame(o)


class WorldRobot:
    """The robot a program gets in a WORLD (practice.py): every call is passed on to the simulation's robot
    (self.robot). A world's code subclasses it and overrides what its world does differently (a move that stops
    short, a frame without depth, a wait after every move, ...), calling self.robot or super() for the rest."""

    def __init__(self, robot):
        self.robot = robot

    def look(self, cameras=None):
        return self.robot.look(cameras)

    def state(self, arm):
        return self.robot.state(arm)

    def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
        return self.robot.move(arm, xyz, dir_a, dir_b, stop_on_contact)

    def turn(self, arm, centre, axis, angle_deg):
        return self.robot.turn(arm, centre, axis, angle_deg)

    def home(self, arm):
        return self.robot.home(arm)

    def wait(self, seconds):
        return self.robot.wait(seconds)

    def time_s(self):
        return self.robot.time_s()

    def set_gripper(self, arm, width_mm):
        return self.robot.set_gripper(arm, width_mm)

    def segment(self, frame, phrase):
        return self.robot.segment(frame, phrase)

    def show(self, label, image):
        return self.robot.show(label, image)

    def truth(self):
        return self.robot.truth()

    def judge(self):
        return self.robot.judge()

    def ask(self, question, images=(), effort="low"):
        return self.robot.ask(question, images, effort)


PARK_Y = -280.0                                                 # mm: beyond it the real rig's right arm stands parked (real.py)


class Robot:
    CAMERAS = tuple(CAMS)
    ARMS = tuple(ARMS)
    GUARD_N, SAFETY_N, MIN_DEPTH_MM = GUARD_N, SAFETY_N, MIN_Z * 1000

    def __init__(self, S, time_limit_s=None, segmenter=None):
        self.S, self.segmenter = S, segmenter                       # segmenter: perception.sam3 (or None: no service)
        self.kind = S.HANDS                                         # "grippers" | "dexterous"
        self.t0, self.limit = float(S.d.time), time_limit_s
        if getattr(S, "only", None):                                # a one-armed scene: that arm and its wrist camera
            self.ARMS = (SIDE[S.only],)
            self.CAMERAS = ("scene", f"{SIDE[S.only]}_wrist")
        self.frames = {}                                            # the latest frame of each camera

    # ------------------------------------------------------------------ time
    def time_s(self):
        """Robot time since the episode's start (s)."""
        return float(self.S.d.time) - self.t0

    def _time(self):
        if self.limit is not None and self.time_s() >= self.limit:
            raise TimeUp(f"the episode's robot time ({self.limit:.0f} s) is spent")

    def wait(self, seconds):
        self._time()
        self.S.run(float(np.clip(seconds, 0.0, 10.0)))

    # ------------------------------------------------------------------ sensing
    def look(self, cameras=None):
        """Frames of the cameras ("scene", "right_wrist", "left_wrist"; all by default) -> {camera: Frame}."""
        self._time()
        out = {}
        for c in (cameras or self.CAMERAS):
            if c not in self.CAMERAS:
                raise KeyError(f"camera must be one of {list(self.CAMERAS)}")
            out[c] = self.frames[c] = Frame(self.S.observe(c))
        return out

    def segment(self, frame, phrase):
        """SAM3 on a frame's colour image, prompted by text (docs/perception.md) -> [(mask, score)], best first, at
        most 6. Only in episodes run with a segmenter."""
        return self._seg().segment(frame.rgb, str(phrase))

    def segment_points(self, frame, pixels, labels):
        """SAM3 prompted by clicks at pixels [[u, v], ...] (labels 1 = on the thing, 0 = not) -> [(mask, score)]."""
        return self._seg().segment_points(frame.rgb, pixels, labels)

    def _seg(self):
        if self.segmenter is None:
            raise RuntimeError("no segmentation service in this episode")
        return self.segmenter

    def state(self, arm):
        """What that arm and hand measure. Both kinds: force_n, the arm's estimate of the net external force on it
        (table frame, noisy by about 0.5 N per axis).
        grippers:  tcp_mm, approach_dir, closing_dir, opening_mm (measured), commanded_mm
        dexterous: palm_mm, fingers_dir, palm_dir, thumb_side, fingertips_mm {finger: xyz}, joints_deg, targets_deg
                   {joint: deg}, fingertip_force_n {finger: N} (the fingertips' tactile pads)."""
        S, P = self.S, self._arm(arm)
        p, R = S.tool_pose(P)
        f = [round(float(v), 1) for v in S.force_estimate(P)]
        if self.kind == "grippers":
            return dict(tcp_mm=_mm(p), approach_dir=_dir(R[:, 2]), closing_dir=_dir(R[:, 1]),
                        opening_mm=round(max(S.width(P), 0.0) * 1000, 1), commanded_mm=round(S.arms[P].g_cmd * 2000, 1), force_n=f)
        from .dexterous import FINGERS, JOINTS
        parts = S.contacts()[P][0]
        tip = {fg: round(max(parts.get(fg + ":tip", 0.0) + float(S.frng.normal(0, TIP_NOISE)), 0.0), 1) for fg in FINGERS}
        hq, hc = S.hand_q(P), S.arms[P].h_cmd
        return dict(palm_mm=_mm(p), fingers_dir=_dir(R[:, 2]), palm_dir=_dir(R[:, 0]), thumb_side=_dir(R[:, 1] if P == "R" else -R[:, 1]),
                    fingertips_mm={fg: _mm(X) for fg, X in S.tip_pos(P).items()},
                    joints_deg={n: round(math.degrees(hq[i]), 1) for i, n in enumerate(JOINTS)},
                    targets_deg={n: round(math.degrees(hc[i]), 1) for i, n in enumerate(JOINTS)},
                    fingertip_force_n=tip, force_n=f)

    # ------------------------------------------------------------------ the arms
    def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
        """The tool point of that arm along a straight line to xyz (mm) while the tool turns to the orientation given
        by two directions: grippers: approach_dir (where the fingers point), closing_dir (the line they close along);
        dexterous: fingers_dir (where the straight fingers point), palm_dir (where the palm faces). The tool point is
        the gripper's TCP / the palm's centre. closing_dir's sign does not matter (the nearer of the two wrist angles
        is taken). stop_on_contact: 4 cm/s, stops at about 4 N of new force on the arm."""
        xyz = np.asarray(xyz, float).reshape(3)
        if arm == "right" and "right" not in self.ARMS and np.all(np.isfinite(xyz)) and xyz[1] < PARK_Y:
            self._time()                                            # a one-armed scene, as the real rig
            return dict(done=True, moved=True, why="", curved=False)   # answers it (real.py): the right arm stands parked there
        P = self._arm(arm)
        lo = 10.0 if self.kind == "grippers" else 5.0
        if not np.all(np.isfinite(xyz)) or xyz[2] < lo:
            return _refused(f"the tool point may not go below {lo:.0f} mm above the table")
        try:
            R = self._orient(dir_a, dir_b, self.S.tool_pose(P)[1])
        except ValueError as e:
            return _refused(str(e))
        return self._motion(lambda: self.S.move(P, xyz / 1000.0, R, guarded=bool(stop_on_contact)))

    def turn(self, arm, centre, axis, angle_deg):
        """Rotate that arm's whole gripper / hand rigidly by angle_deg about the line through centre (mm) along axis
        (right-hand rule: axis (0, 0, 1) and a positive angle = counter-clockwise seen from above)."""
        P = self._arm(arm)
        axis = np.asarray(axis, float).reshape(3); centre = np.asarray(centre, float).reshape(3)
        if not np.linalg.norm(axis) > 1e-6 or not abs(float(angle_deg)) <= 360:
            return _refused("give a non-zero axis and an angle within +-360 degrees")
        return self._motion(lambda: self.S.turn(P, centre / 1000.0, axis, float(angle_deg)))

    def home(self, arm):
        """That arm back to its start pose by the joints (refused if it would hit something on the way)."""
        P = self._arm(arm)
        return self._motion(lambda: self.S.go_home(P))

    # ------------------------------------------------------------------ grippers
    def set_gripper(self, arm, width_mm):
        """The opening between the two fingers' pads, 0..80 mm (0 = close and grip, up to about 70 N).
        -> the opening measured afterwards (mm): the width of what is between the fingers."""
        self._kind("grippers"); self._time()
        return round(max(self.S.set_gripper(self._arm(arm), float(np.clip(width_mm, 0, 80)) / 1000.0), 0.0) * 1000, 1)

    # ------------------------------------------------------------------ Sharpa hands
    def set_joints(self, arm, joints, duration_s=0.8, stop_force_n=None):
        """Joint targets of one hand, degrees ({"index_MCP_FE": 45, ...}; the others keep their targets), reached over
        duration_s. stop_force_n: each finger stops where it is once the contact force on it exceeds that.
        -> dict(stopped=[fingers stopped by a contact], clipped={joint: "lo..hi"}, unknown=[names])."""
        self._kind("dexterous"); self._time()
        from .dexterous import JOINTS
        S, P = self.S, self._arm(arm)
        tg, clipped, unknown = {}, {}, []
        for k, v in (joints or {}).items():
            k2 = str(k).removeprefix("right_").removeprefix("left_")
            if k2 not in JOINTS or not np.isfinite(float(v)):
                unknown.append(str(k)); continue
            lo, hi = S.hand_range(P, k2); r = math.radians(float(v))
            if not lo - 1e-6 <= r <= hi + 1e-6:
                clipped[k2] = f"{math.degrees(lo):.0f}..{math.degrees(hi):.0f}"
            tg[k2] = r
        st = S.set_hand(P, tg, _dur(duration_s), guard_n=_guard(stop_force_n))
        return dict(stopped=list(st), clipped=clipped, unknown=unknown)

    def fingertips(self, arm, targets, duration_s=0.8, stop_force_n=None):
        """Put fingertips of one hand ({"thumb" | "index" | "middle" | "ring" | "pinky": xyz mm}; the centre of the
        fingertip's pad) at points, the arm staying where it is: the finger's joints by inverse kinematics.
        -> dict(stopped=[fingers], miss_mm={finger: distance between the target and the nearest point it can reach})."""
        self._kind("dexterous"); self._time()
        from .dexterous import FINGERS
        S, P = self.S, self._arm(arm)
        tg, miss, unknown = {}, {}, []
        h0 = S.arms[P].h_cmd.copy()
        for f, X in (targets or {}).items():
            if f not in FINGERS:
                unknown.append(str(f)); continue
            t, err = S.finger_ik(P, f, np.asarray(X, float).reshape(3) / 1000.0, h0=h0)
            tg.update(t); miss[f] = round(err * 1000, 1)
        st = S.set_hand(P, tg, _dur(duration_s), guard_n=_guard(stop_force_n))
        return dict(stopped=list(st), miss_mm=miss, unknown=unknown)

    # ------------------------------------------------------------------
    def _arm(self, arm):
        if arm not in self.ARMS:
            raise KeyError('arm must be "right" or "left"' if len(self.ARMS) > 1 else
                           f'arm must be "{self.ARMS[0]}": this robot uses only its {self.ARMS[0]} arm')
        return ARMS[arm]

    def _kind(self, kind):
        if self.kind != kind:
            raise AttributeError(f"this robot has {self.kind}: that command is for {kind}")

    def _orient(self, a, b, cur=None):
        a = np.asarray(a, float).reshape(3); b = np.asarray(b, float).reshape(3)
        if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b)) and np.linalg.norm(a) > 1e-6):
            raise ValueError("give two non-zero directions")
        if self.kind == "grippers":
            from .grippers import grip_R
            R = grip_R(a, b)                                        # a closing line has no sign: take the half turn
            if cur is not None and float(R[:, 1] @ cur[:, 1]) < 0:  # of the wrist nearer to where it is
                R = grip_R(a, -np.asarray(b, float))
            return R
        from .dexterous import hand_R
        return hand_R(a, b)

    def _motion(self, fn):
        self._time()
        self.S.last_path = "line"
        try:
            r = fn()
        except Stop as e:
            return _refused(str(e))
        return dict(done=r is None, moved=True, why=r or "", curved=self.S.last_path == "joint")


def _refused(why):
    return dict(done=False, moved=False, why=why, curved=False)


def _mm(v):
    return [round(float(c) * 1000, 1) for c in v]


def _dir(v):
    return [round(float(c), 3) for c in v]


def _dur(s):
    return float(np.clip(0.8 if s is None else s, 0.2, 5.0))


def _guard(n):
    return None if not n else float(np.clip(n, 0.3, 20.0))
