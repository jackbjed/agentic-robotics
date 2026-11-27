"""A skill program's robot for RoboDojo's sweep_blocks in ManiSkill (sweep_blocks/env.py): the project's program
interface (run(robot, task); millimetres and degrees; look / state / move / turn / home / set_gripper / wait / time_s /
segment / show: docs/bimanual.md, bimanual/api.py) on the two X5 arms.

Underneath, every command is a stream of RoboDojo's own actions (joint targets at 25 Hz, grippers 0..1), so a program
moves the robot exactly as a policy would, and RoboDojo's limit of 1000 actions (40 s) is the episode's time.

Frame: the TABLE frame: RoboDojo's world frame with the table's top at z = 0 (world z - 765 mm); x and y as RoboDojo's
(the arms stand at the table's -y edge and face +y; +x is to the robot's right).
The tool point (TCP) is 145 mm along the hand from its wrist frame (RoboDojo's gripper_bias), between the fingers."""
from __future__ import annotations

import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from m2m.api import Frame, TimeUp, _fmt, _result, _rgb8  # noqa: E402
from x5 import spec as S  # noqa: E402

TABLE_Z = 0.765
ARMS = {"left": "L", "right": "R"}
CAMERAS = ("head", "left_wrist", "right_wrist")
ACTION_S = 0.04
V_FREE, V_GUARD, W_TURN = 0.25, 0.05, math.radians(90)       # m/s, m/s, rad/s
GUARD_N, FORCE_NOISE = 4.0, 0.5
MAX_OPEN = 0.0888                                             # m between the fingers' inner faces, fully open
TIME_LIMIT_S = 40.0
ARRIVE_MM, ARRIVE_DEG = 5.0, 3.0                              # a move is "done" only within this of its target
BACK_Y = -0.42                                                # m: a joint-space path's tool point stays in front of this
SHOWN = 12
CV = np.array([[0.0, -1.0, 0.0], [0.0, 0.0, -1.0], [1.0, 0.0, 0.0]])   # (camera axes: R_opencv = R_sapien @ CV.T)


def _rpy(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr], [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def _axis_rot(axis, a):
    k = np.asarray(axis, float); k = k / np.linalg.norm(k)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K


def _rotvec(R):
    c = float(np.clip((np.trace(R) - 1) / 2, -1, 1)); a = math.acos(c)
    if a < 1e-9:
        return np.zeros(3)
    if a > math.pi - 1e-4:
        M = (R + np.eye(3)) / 2
        k = int(np.argmax(np.diag(M))); v = M[:, k] / math.sqrt(max(M[k, k], 1e-12))
        return v * a
    return np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(a)) * a


class ArmKin:
    """One X5 arm's kinematics from RoboDojo's URDF: the tool point's pose (table frame, metres) and its inverse."""

    def __init__(self, side):
        root = ET.parse(S.ASSETS / "X5A.urdf").getroot()
        J = {j.get("name"): j for j in root.findall("joint")}
        self.chain = []
        for n in S.ARM_JOINTS:
            o = J[n].find("origin")
            T = np.eye(4); T[:3, :3] = _rpy(*[float(v) for v in o.get("rpy").split()]); T[:3, 3] = [float(v) for v in o.get("xyz").split()]
            self.chain.append((T, np.array([float(v) for v in J[n].find("axis").get("xyz").split()])))
        self.T0 = np.eye(4); self.T0[:3, :3] = _axis_rot([0, 0, 1], S.BASE_YAW)
        self.T0[:3, 3] = np.array(S.BASE[side]) - [0, 0, TABLE_Z]
        self.lo, self.hi = np.full(6, -math.pi), np.full(6, math.pi)

    def frames(self, q):
        T, out = self.T0.copy(), []
        for (To, ax), a in zip(self.chain, q):
            T = T @ To
            out.append((T[:3, 3].copy(), T[:3, :3] @ ax))
            R = np.eye(4); R[:3, :3] = _axis_rot(ax, a)
            T = T @ R
        return T, out

    def fk(self, q):
        T, _ = self.frames(q)
        return T[:3, 3] + T[:3, 0] * S.TCP_X, T[:3, :3].copy()

    def ik(self, p, R, q0, iters=200):
        q = np.asarray(q0, float).copy()
        for _ in range(iters):
            T, fr = self.frames(q)
            x = T[:3, 3] + T[:3, 0] * S.TCP_X
            ep, er = p - x, _rotvec(R @ T[:3, :3].T)
            if np.linalg.norm(ep) < 1e-4 and np.linalg.norm(er) < 1e-3:
                break
            J = np.zeros((6, 6))
            for i, (o, z) in enumerate(fr):
                J[:3, i] = np.cross(z, x - o); J[3:, i] = z
            Jw = np.vstack([J[:3], 0.2 * J[3:]]); e = np.r_[ep, 0.2 * er]
            dq = Jw.T @ np.linalg.solve(Jw @ Jw.T + 1e-4 * np.eye(6), e)
            s = np.abs(dq).max()
            q = np.clip(q + (dq if s < 0.2 else dq * 0.2 / s), self.lo, self.hi)
        x, Rc = self.fk(q)
        return q, float(np.linalg.norm(p - x)), float(np.linalg.norm(_rotvec(R @ Rc.T)))


def tool_R(approach, closing):
    """The hand's orientation from where the fingers point and the line they close along: columns x (approach),
    y (closing), z."""
    x = np.asarray(approach, float); x = x / np.linalg.norm(x)
    y = np.asarray(closing, float); y = y - x * float(y @ x)
    if np.linalg.norm(y) < 1e-6:
        raise ValueError("the closing direction is parallel to the approach direction")
    y = y / np.linalg.norm(y)
    return np.column_stack([x, y, np.cross(x, y)])


class X5Robot:
    """The robot a program is given. Every command steps the task (SweepBlocks) by RoboDojo's actions."""
    CAMERAS, ARMS, kind = CAMERAS, tuple(ARMS), "grippers"
    GUARD_N = GUARD_N

    def __init__(self, env, segmenter=None, on_step=None):
        self.env, self.segmenter, self.on_step = env, segmenter, on_step
        self.kin = {a: ArmKin(s) for a, s in ARMS.items()}
        self.rng = np.random.default_rng(0); self.frng = np.random.default_rng(1)
        self.q_cmd = {a: np.zeros(6) for a in ARMS}
        self.g_cmd = {a: 1.0 for a in ARMS}                         # (RoboDojo's gripper value: 1 = open)
        self.last_curved = False
        self.links = {a: [l for l in env.robot.get_links() if l.name.startswith(s + "_link")] for a, s in ARMS.items()}

    # ------------------------------------------------------------------ time, stepping
    def time_s(self):
        return self.env.steps * ACTION_S

    def _time(self):
        if self.env.done or self.env.steps >= self.env_limit():
            raise TimeUp(f"the episode's robot time ({TIME_LIMIT_S:.0f} s) is spent" if not self.env.success else "the episode is over")

    @staticmethod
    def env_limit():
        return int(round(TIME_LIMIT_S / ACTION_S))

    def _act(self):
        self._time()
        a = np.r_[self.q_cmd["left"], self.g_cmd["left"], self.q_cmd["right"], self.g_cmd["right"]]
        self.env.step(a)
        if self.on_step:
            self.on_step()

    def wait(self, seconds):
        for _ in range(int(round(float(np.clip(seconds, 0.0, 10.0)) / ACTION_S))):
            self._act()

    # ------------------------------------------------------------------ sensing
    def look(self, cameras=None):
        self._time()
        out = {}
        U = self.env.U
        U.scene.update_render(update_sensors=True, update_human_render_cameras=False)
        for c in (cameras or CAMERAS):
            if c not in CAMERAS:
                raise KeyError(f"camera must be one of {list(CAMERAS)}")
            s = U._sensors[c]; s.capture()
            col = s.camera.get_picture("Color"); pos = s.camera.get_picture("Position")
            col = (col[0] if isinstance(col, (list, tuple)) else col).cpu().numpy(); pos = (pos[0] if isinstance(pos, (list, tuple)) else pos).cpu().numpy()
            col = col[0] if col.ndim == 4 else col; pos = pos[0] if pos.ndim == 4 else pos
            z = (-pos[..., 2]).astype(np.float64)
            K = s.camera.get_intrinsic_matrix()[0].cpu().numpy()
            T = s.camera.get_global_pose().to_transformation_matrix()[0].cpu().numpy().astype(float)
            out[c] = Frame(dict(camera=c, rgb=np.ascontiguousarray((np.clip(col[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)),
                                depth=np.where((z > 0.005) & (z < 9.0), z, np.nan), t=self.time_s(),
                                K=(col.shape[1], col.shape[0], float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2])),
                                eye=T[:3, 3] - [0, 0, TABLE_Z], R=T[:3, :3] @ CV.T))
        return out

    def segment(self, frame, phrase):
        if self.segmenter is None:
            raise RuntimeError("no segmentation service in this episode")
        return self.segmenter.segment(frame.rgb, str(phrase))

    def _force(self, arm):
        names = [l.name for l in self.links[arm]]
        return self.env.robot.get_net_contact_forces(names)[0].cpu().numpy().astype(float).sum(0)

    def _tool(self, arm):
        p, R = self.env.endpose(ARMS[arm])
        return p + R[:, 0] * S.TCP_X - [0, 0, TABLE_Z], R

    def _opening(self, arm):
        q = self.env.q()[6 if arm == "left" else 14]
        return float(np.clip(2 * q + 0.0008, 0.0, MAX_OPEN))

    def state(self, arm):
        self._arm(arm)
        p, R = self._tool(arm)
        f = self._force(arm) + self.frng.normal(0, FORCE_NOISE, 3)
        g = self.g_cmd[arm] * 0.054 - 0.01
        return dict(tcp_mm=[round(float(v) * 1000, 1) for v in p], approach_dir=[round(float(v), 3) for v in R[:, 0]],
                    closing_dir=[round(float(v), 3) for v in R[:, 1]], opening_mm=round(self._opening(arm) * 1000, 1),
                    commanded_mm=round(float(np.clip(2 * g + 0.0008, 0, MAX_OPEN)) * 1000, 1), force_n=[round(float(v), 1) for v in f])

    # ------------------------------------------------------------------ motion
    def _arm(self, arm):
        if arm not in ARMS:
            raise KeyError('arm must be "right" or "left"')

    def _solve(self, arm, p, R, q0):
        k = self.kin[arm]
        q, ep, er = k.ik(p, R, q0)
        if ep < 0.002 and er < math.radians(1.5):
            return q
        return None

    def _play(self, arm, Q, guarded=False, u=None):
        """Joint waypoints, one per action. -> "" or "contact"."""
        f0 = self._force(arm)
        for q in Q:
            self.q_cmd[arm] = np.asarray(q, float)
            self._act()
            if guarded:
                dF = self._force(arm) - f0
                if (float(dF @ -u) if u is not None else float(np.linalg.norm(dF))) > GUARD_N:
                    self.q_cmd[arm] = self.env.q()[0:6] if arm == "left" else self.env.q()[8:14]
                    self._act()
                    return "contact"
        self._act(); self._act()                                    # (two more actions: the arm arrives)
        return ""

    def _arrived(self, arm, p1, R1):
        """Did the arm get where it was sent? -> "" or why not. (A command that ran to its end is not thereby done:
        the arm may have been held up by something in its way.)"""
        p, R = self._tool(arm)
        d = float(np.linalg.norm(p - p1)) * 1000; a = math.degrees(float(np.linalg.norm(_rotvec(R1 @ R.T))))
        if d <= ARRIVE_MM and a <= ARRIVE_DEG:
            return ""
        return f"the arm stopped short of the target ({d:.0f} mm and {a:.0f} degrees from it): something is in its way"

    def _path_ok(self, arm, Q):
        """Is a joint-space path sane: the tool point and every joint stay over the table, in front of the robot's
        edge of it? (A far-fetched joint solution swings the arm behind the robot or through the table.)"""
        k = self.kin[arm]
        for q in Q[::3] + [Q[-1]]:
            T, fr = k.frames(q)
            tcp = T[:3, 3] + T[:3, 0] * S.TCP_X
            if tcp[2] < 0.005 or tcp[1] < BACK_Y or any(o[2] < 0.02 or o[1] < BACK_Y - 0.10 for o, _ in fr[2:]):
                return False
        return True

    def move(self, arm, xyz, approach_dir, closing_dir, stop_on_contact=False):
        self._arm(arm); self._time()
        try:
            p1 = np.asarray(xyz, float).reshape(3) / 1000.0
            a, c = np.asarray(approach_dir, float).reshape(3), np.asarray(closing_dir, float).reshape(3)
            if not (np.all(np.isfinite(p1)) and np.all(np.isfinite(a)) and np.all(np.isfinite(c)) and np.linalg.norm(a) > 1e-6):
                raise ValueError("give a finite point and two non-zero directions")
            R1 = tool_R(a, c)
        except ValueError as e:
            return dict(done=False, moved=False, why=str(e), curved=False)
        k = self.kin[arm]
        p0, R0 = k.fk(self.q_cmd[arm])
        if float(R1[:, 1] @ R0[:, 1]) < 0:                          # (a closing line has no sign: the nearer of the two)
            R1 = tool_R(a, -c)
        w = _rotvec(R0.T @ R1); ang = float(np.linalg.norm(w)); dist = float(np.linalg.norm(p1 - p0))
        v = V_GUARD if stop_on_contact else V_FREE
        n = max(int(math.ceil(max(dist / v, ang / W_TURN) / ACTION_S)), 1)
        Q, q, curved = [], self.q_cmd[arm].copy(), False
        for i in range(1, n + 1):
            s = i / n
            qi = self._solve(arm, p0 + (p1 - p0) * s, R0 @ _axis_rot(w / ang, ang * s) if ang > 1e-9 else R0, q)
            if qi is None or np.abs(qi - q).max() > 0.5:
                Q = None
                break
            Q.append(qi); q = qi
        if Q is None:                                               # (not as a straight line: by the joints, to a solution)
            if stop_on_contact:
                return dict(done=False, moved=False, why="out of the arm's reach on a straight line", curved=False)
            # the joint solution NEAREST to where the arm is whose joint-space path is sane (seeds near the arm first)
            q0, best, any_solution = self.q_cmd[arm], None, False
            seeds = [q0] + [q0 + self.rng.normal(0, 0.4, 6) for _ in range(24)] + [self.rng.uniform(-1.6, 1.6, 6) for _ in range(24)]
            for seed in seeds:
                qe = self._solve(arm, p1, R1, seed)
                if qe is None:
                    continue
                any_solution = True
                m = max(int(math.ceil(np.abs(qe - q0).max() / (1.5 * ACTION_S))), 1)
                path = [q0 + (qe - q0) * i / m for i in range(1, m + 1)]
                if self._path_ok(arm, path) and (best is None or np.abs(qe - q0).max() < np.abs(best[0] - q0).max()):
                    best = (qe, path)
            if best is None:
                return dict(done=False, moved=False, curved=False,
                            why="out of the arm's reach (no joint solution for that pose)" if not any_solution else
                            "the arm cannot get there in one move without swinging behind the robot or through the table: go by a pose in between")
            Q, curved = best[1], True
        u = (p1 - p0) / dist if dist > 1e-3 else None
        why = self._play(arm, Q, guarded=bool(stop_on_contact), u=u)
        why = why or self._arrived(arm, p1, R1)
        return dict(done=why == "", moved=True, why=why, curved=curved)

    def turn(self, arm, centre, axis, angle_deg):
        self._arm(arm); self._time()
        axis = np.asarray(axis, float).reshape(3); c = np.asarray(centre, float).reshape(3) / 1000.0
        if not np.linalg.norm(axis) > 1e-6 or not abs(float(angle_deg)) <= 360:
            return dict(done=False, moved=False, why="give a non-zero axis and an angle within +-360 degrees", curved=False)
        k = self.kin[arm]
        p0, R0 = k.fk(self.q_cmd[arm])
        th = math.radians(float(angle_deg)); n = max(int(math.ceil(abs(th) / W_TURN / ACTION_S)), 1)
        Q, q = [], self.q_cmd[arm].copy()
        for i in range(1, n + 1):
            E = _axis_rot(axis, th * i / n)
            qi = self._solve(arm, c + E @ (p0 - c), E @ R0, q)
            if qi is None or np.abs(qi - q).max() > 0.5:
                return dict(done=False, moved=False, why="the arm cannot make that turn (out of reach or a joint's limit on the way)", curved=False)
            Q.append(qi); q = qi
        E = _axis_rot(axis, th)
        why = self._play(arm, Q) or self._arrived(arm, c + E @ (p0 - c), E @ R0)
        return dict(done=why == "", moved=True, why=why, curved=False)

    def home(self, arm):
        self._arm(arm); self._time()
        q0 = self.q_cmd[arm]
        m = max(int(math.ceil(np.abs(q0).max() / (1.5 * ACTION_S))), 1)
        why = self._play(arm, [q0 * (1 - i / m) for i in range(1, m + 1)])
        if not why:
            p_home, R_home = self.kin[arm].fk(np.zeros(6))
            why = self._arrived(arm, p_home, R_home)
        return dict(done=why == "", moved=True, why=why, curved=True)

    def set_gripper(self, arm, width_mm):
        self._arm(arm); self._time()
        w = float(np.clip(width_mm, 0.0, MAX_OPEN * 1000)) / 1000.0
        g1 = float(np.clip(((w - 0.0008) / 2 + 0.01) / 0.054, 0.0, 1.0)); g0 = self.g_cmd[arm]
        if w <= 0.0:
            g1 = 0.0
        n = max(int(math.ceil(abs(g1 - g0) * 0.6 / ACTION_S)), 1)   # (fully open to closed in 0.6 s)
        for i in range(1, n + 1):
            self.g_cmd[arm] = g0 + (g1 - g0) * i / n
            self._act()
        for _ in range(8):
            self._act()
        return round(self._opening(arm) * 1000, 1)


def program_robot(robot, out, trace=None, truth=None):
    """What the program gets: the documented calls, logged (m2m/api.program_robot's way)."""
    counts = dict(commands=0, shown=0)
    last = {}

    def moved():
        if truth is None:
            return ""
        now = {n: v for n, v in truth().items() if n not in last or np.abs(np.asarray(v) - np.asarray(last[n])).max() > 1.0}
        last.update(now)
        return " ".join(f"{n} {_fmt(v)}" for n, v in now.items())
    if trace is not None:
        trace.write(f"# TRUE positions at the start (mm, table frame): {moved()}\n")

    def wrap(name):
        fn = getattr(robot, name)

        def call(*a, **k):
            r = fn(*a, **k)
            if name not in ("state", "time_s"):
                counts["commands"] += 1
                if trace is not None:
                    args = ", ".join([_fmt(x) for x in a if not hasattr(x, "rgb")] + [f"{q}={_fmt(v)}" for q, v in k.items()])
                    arm = a[0] if a and isinstance(a[0], str) and a[0] in ARMS else None
                    tool = f" | {arm} tool {_fmt(robot.state(arm)['tcp_mm'])} open {robot.state(arm)['opening_mm']:.1f}" if arm else ""
                    tr = moved()
                    trace.write(f"# t={robot.time_s():5.1f} {name}({args}) -> {_result(r)}{tool}{' | NOW ' + tr if tr else ''}\n")
            return r
        call.__name__ = name
        return call

    def show(label, image):
        import cv2
        if counts["shown"] >= SHOWN:
            return False
        im = _rgb8(image, "show(label, image)", 960)
        d = Path(out) / "shown"; d.mkdir(exist_ok=True)
        cv2.imwrite(str(d / f"{counts['shown']:02d}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', str(label))[:40]}.png"), im[..., ::-1])
        counts["shown"] += 1
        return True

    class ProgramRobot:
        CAMERAS, ARMS, kind, GUARD_N = robot.CAMERAS, robot.ARMS, robot.kind, robot.GUARD_N

        def __setattr__(self, k, v):
            raise AttributeError("the robot object cannot be changed")
    for name in ["look", "state", "move", "turn", "home", "wait", "time_s", "set_gripper"] + (["segment"] if robot.segmenter is not None else []):
        setattr(ProgramRobot, name, staticmethod(wrap(name)))
    ProgramRobot.show = staticmethod(show)
    return ProgramRobot(), counts
