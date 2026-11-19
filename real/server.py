"""The real-robot episode server (privileged): ONE episode of a skill program on the real Panda, or on the twin standing
in for it. The program runs in the same sandbox as in simulation (real/host.py) and talks to this server over the
platform's socket protocol (src/agentic_robotics/execution/protocol.py): hello / task / state / execute / idle /
wait_gripper / now / camera / segment / segment_points / vlm / event / done. It sees the same interface as in sim.

Backends (spec "backend"):
  robot   the arm server (panda_twin code/real/arm_server.py, zmq, rig.json server.port; it checks every trajectory
          against the safety limits and, unless started with --no-confirm, asks for each one in its own terminal) and
          both D415s (pyrealsense2): the code that ran the Astra + skills episodes on the robot (real_sim.RemoteArm,
          RealSenseCams, recorder.Recorder). Run this file with the robot client's Python (configs/local.yaml
          real.python); real/run.py does.
  twin    the simulator's own world server (sim/server.py World: a layout, physics, the judge) at the calibrated cameras:
          a rehearsal of the whole real path, and its test.
Modes (spec "mode"):
  dry     NOTHING is sent. Each trajectory the program plans (and RealSim checked) is recorded and drawn over the scene
          camera's frame (plan/NNN.jpg, real/ghost.py); the arm stays where it is and the program gets its real state
          and real frames. Robot time is the planned motion's. After max_trajectories the next one is refused.
  live    trajectories go to the arm (robot: the arm server's checks and confirmation), until the robot-time limit
          (motion and waits only, as in sim; perception and model time do not count) or max_trajectories.

    <python> -I real/server.py --spec <out>/spec.json --sock <dir>/real.sock --out <out>
Writes into <out>: actions.jsonl (every trajectory: planned or sent, and the reply), events.jsonl, timeline.json, frames/
(every frame the program got), plan/ (the planned moves drawn), video/ + episode.mp4 (robot: both cameras, the phase,
gripper, force), twin/ (the twin backend's own record), server.json (the result; real/run.py adds the verdict)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:                           # (the robot client's Python has no install of ours)
    sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from agentic_robotics.execution import protocol  # noqa: E402


def frame_key(rgb):
    return hashlib.md5(np.ascontiguousarray(rgb[::4, ::4]).tobytes()).hexdigest()


# ---------------------------------------------------------------------------------------------------- backends
class RobotBackend:
    """The real arm (arm server) and cameras, as real_box.make_rig(backend robot) builds them."""
    clock = "unix"

    def __init__(self, spec, out):
        from agentic_robotics.sim import twin  # noqa: F401  (the vendored code on the path)
        from real_sim import RealSenseCams, RemoteArm
        from panda_fk import load_rig
        rig = load_rig()
        self.arm = RemoteArm(rig["server"]["host"], spec.get("port") or rig["server"]["port"])
        hi = self.arm.hello()
        if not hi.get("ok"):
            raise SystemExit(f"arm server: {hi}")
        if spec["mode"] == "live" and not hi.get("allow_motion"):
            raise SystemExit("the arm server is read-only: start it with --allow-motion for a live run")
        self.hello_info = {k: hi.get(k) for k in ("fake", "allow_motion", "confirm", "log")}
        if spec.get("replay_frames"):                          # (tests: frames recorded on the robot, not the cameras)
            self.cams = ReplayCams(spec["replay_frames"])
            self.hello_info["replay_frames"] = spec["replay_frames"]
        else:
            self.cams = RealSenseCams(rig)
        self.rig = rig
        self.rec = None
        r = rig.get("record", {})
        if spec.get("record", True):
            from recorder import Recorder
            self.rec = Recorder(out, time.time(), float(r.get("fps", 15)), float(spec.get("depth_fps", 0)),
                                int(r.get("crf", 23)), "unix")
            self.cams.attach_recorder(self.rec)

    def wall(self):
        return time.time()

    def state(self):
        return self.arm.state()

    def execute(self, h):
        return self.arm.execute(h["t"], h["q"], grip=h.get("grip"), settle=h.get("settle", False), phase=h.get("phase"),
                                hold_after=h.get("hold_after", 0.1), guard=h.get("guard"))

    def idle(self, T_):
        time.sleep(max(0.0, float(T_)))

    def wait_gripper(self, timeout):
        return self.arm.wait_gripper(timeout)

    def frame(self, cam):
        f = self.cams.get(cam, after=time.time() + 0.04)
        return f["rgb"], f["depth"].astype(np.float32), tuple(float(x) for x in f["K"])

    def latest_scene(self):
        f = self.cams.latest.get("d415")
        return (f["rgb"], tuple(float(x) for x in self.cams.cams["d415"]["K"])) if f else (None, None)

    def finish(self, h, out):
        meta = None
        if self.rec is not None:
            self.cams.rec = None
            meta = self.rec.close()
        self.cams.close()
        return dict(judge=None, video=meta, arm_server=self.hello_info)


class ReplayCams:
    """RealSenseCams' interface over frames a real run recorded (RealSim log_dir frames/NNN_<cam>_rgb.png, _depth.png,
    .json): each camera's first frame, every time (tests of the robot backend without the cameras)."""

    def __init__(self, d):
        import cv2
        d = Path(d)
        self.cams, self.latest, self.rec = {}, {}, None
        for cam in ("d415", "L_wrist"):
            j = sorted(d.glob(f"*_{cam}.json"))[0]
            meta = json.loads(j.read_text())
            rgb = cv2.cvtColor(cv2.imread(str(j.with_name(j.stem + "_rgb.png"))), cv2.COLOR_BGR2RGB)
            z = cv2.imread(str(j.with_name(j.stem + "_depth.png")), cv2.IMREAD_UNCHANGED).astype(np.float32) / 1000.0
            z[z <= 0] = np.inf
            self.cams[cam] = dict(K=tuple(meta["K"]))
            self.latest[cam] = dict(t=time.time(), rgb=rgb, depth=z)

    def get(self, cam, after=0.0, timeout=2.0):
        f = dict(self.latest[cam], t=time.time())
        if self.rec is not None:
            self.rec.offer(RealSenseCamsNames[cam], f["t"], f["rgb"], f["depth"])
        return dict(f, K=self.cams[cam]["K"])

    def attach_recorder(self, rec, state_log=None):
        self.rec = rec

    def close(self):
        pass


RealSenseCamsNames = {"d415": "scene", "L_wrist": "wrist"}


class TwinBackend:
    """The simulator's world server (sim/server.py World) standing in for the robot and the scene."""
    clock = "twin sim time"

    def __init__(self, spec, out):
        from agentic_robotics.config import episode_base, task_config
        from agentic_robotics.evaluation.suites import episode_seed
        from agentic_robotics.sim.server import World
        t = task_config(spec["twin"]["task_config"])
        e = spec["twin"]
        ws = dict(episode_base(t, record=dict(video=True, observations="none", video_view="cameras")),
                  split=e["split"], level=int(e["level"]), idx=int(e["idx"]), seed=episode_seed(int(e["seed"]), int(e["level"]), int(e["idx"])),
                  robot_time_limit_s=float(spec["robot_time_limit_s"]) + 30.0)    # (our limit is ours; the World's above it)
        ws["scene"]["mounts"] = "calibrated"
        self.world = World(ws, out / "twin")
        self.hello_info = dict(twin=True, layout=dict(split=e["split"], level=e["level"], idx=e["idx"], seed=e["seed"]),
                               target=self.world.scene.target, object=self.world.task["object"])
        self.task = self.world.task

    def call(self, **h):
        r, arr = self.world.handle(h, [])
        return r, arr

    def wall(self):
        return float(self.world.arm.now())

    def state(self):
        return self.call(cmd="state")[0]

    def execute(self, h):
        return self.world.handle(dict(h), [])[0]

    def idle(self, T_):
        self.call(cmd="idle", T=float(T_))

    def wait_gripper(self, timeout):
        return self.call(cmd="wait_gripper", timeout=timeout)[0]

    def frame(self, cam):
        r, arr = self.call(cmd="camera", cam=cam)
        return arr[0], arr[1], tuple(float(x) for x in r["K"])

    def latest_scene(self):
        sc = self.world.scene
        rgb = sc.colour_frame("d415")
        m = sc.P.m; cid = m.camera("d415").id
        import math
        f = 240 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        return rgb, (640, 480, f, f, 320.0, 240.0)

    def finish(self, h, out):
        self.world.finish(dict(h))
        res = self.world.finished
        keys = ("category", "success", "t_success", "max_lift_mm", "wrong_object", "off_table", "target")
        return dict(judge={k: res.get(k) for k in keys}, video=None, twin_record=str(out / "twin"))


# ---------------------------------------------------------------------------------------------------- the episode
class RealEpisode:
    def __init__(self, spec, out):
        self.spec, self.out = spec, Path(out)
        self.mode = spec["mode"]
        assert self.mode in ("dry", "live"), self.mode
        for d in ("frames", "plan"):
            (self.out / d).mkdir(parents=True, exist_ok=True)
        self.be = RobotBackend(spec, self.out) if spec["backend"] == "robot" else TwinBackend(spec, self.out)
        self.task = dict(spec["task"]) if spec.get("task") else dict(getattr(self.be, "task", None) or {})
        self.limit = float(spec.get("robot_time_limit_s", 150.0))
        self.max_traj = int(spec.get("max_trajectories", 40))
        self.t_wall0 = time.time()
        self.w0 = self.be.wall()                                   # (the backend's clock at the start)
        self.robot_s = 0.0                                         # motion + waits (dry: as planned)
        self.n_traj = self.n_sent = self.n_refused = 0
        self.frames = {}
        self.n_frame = 0
        self.timeline = []
        self.f_actions = open(self.out / "actions.jsonl", "w")
        self.f_events = open(self.out / "events.jsonl", "w")
        self.counts = dict(camera=0, segment=0, vlm=0)
        self.ghost = None
        self.plans = []
        self.finished = None
        self.vlm = None
        if (spec.get("vlm") or {}).get("enabled"):
            from agentic_robotics.perception.vlm import VLM
            v = spec["vlm"]
            self.vlm = VLM(v, tag=v.get("tag", "real"), cache_dir=None)
        if spec.get("sam3") == "robot":                            # (the robot's own SAM3 servers, :8131 / :8134)
            from agentic_robotics import services
            ports = {"sam3_text": 8131, "sam3_points": 8134}
            orig = services.url
            services.url = lambda name, path="": f"http://127.0.0.1:{ports[name]}{path}" if name in ports else orig(name, path)

    # ---- clocks
    def t_run(self):
        """Seconds since the start on the video's clock (robot: wall; twin: sim time)."""
        return float(self.be.wall() - self.w0)

    def now(self):
        """The program's robot clock: motion and waits only (sim's semantics), from 0 at the start."""
        return self.robot_s

    def log(self, what, **data):
        self.f_events.write(protocol.jdump(dict(t=round(self.now(), 3), t_run=round(self.t_run(), 3), what=what, data=data)) + "\n")
        self.f_events.flush()

    # ---- planned moves drawn
    def draw_plan(self, h, n):
        try:
            if self.ghost is None:
                sys.path.insert(0, str(ROOT / "real"))
                from ghost import PlanView
                self.ghost = PlanView()
            rgb, K = self.be.latest_scene()
            if rgb is None:
                return
            Q = np.asarray(h["q"], float)
            dur = float(np.asarray(h["t"], float)[-1]) if len(h["t"]) else 0.0
            lab = f"#{n:02d} {'PLANNED (not sent)' if self.mode == 'dry' else 'sent'}: {h.get('phase') or 'move'}, {dur:.1f} s, {len(Q)} points"
            grip = h.get("grip")
            g_end = grip[-1] if isinstance(grip, (list, tuple)) and grip else grip
            im = self.ghost.draw(rgb, K, Q, g_end if isinstance(g_end, (int, float)) else None, lab)
            import cv2
            p = self.out / "plan" / f"{n:03d}.jpg"
            cv2.imwrite(str(p), cv2.cvtColor(im, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
            self.plans.append(im)
        except Exception as e:                                      # (a drawing never stops an episode)
            self.log("plan drawing failed", error=f"{type(e).__name__}: {e}"[:300])

    # ---- requests
    def refuse(self, why):
        self.n_refused += 1
        return dict(ok=False, refused=True, why=why)

    def handle(self, h, arrays):
        cmd = h.get("cmd")
        if cmd == "hello":
            return dict(ok=True, real=self.spec["backend"] == "robot", backend=self.spec["backend"], mode=self.mode,
                        twin=False, fake=False, allow_motion=self.mode == "live", camera_mounts="real",
                        robot_time_limit_s=self.limit, t0=0.0, **{f"arm_{k}": v for k, v in self.be.hello_info.items()}), []
        if cmd == "task":
            return dict(ok=True, task=self.task), []
        if cmd == "now":
            return dict(ok=True, t=self.now()), []
        if cmd == "state":
            st = self.be.state()
            return dict(st, ok=True), []
        if cmd == "execute":
            if self.robot_s >= self.limit:
                return self.refuse(f"episode robot-time limit ({self.limit:.0f} s) reached"), []
            if self.n_traj >= self.max_traj:
                return self.refuse(f"{'dry run' if self.mode == 'dry' else 'episode'}: at most {self.max_traj} trajectories"), []
            n = self.n_traj; self.n_traj += 1
            dur = float(np.asarray(h["t"], float)[-1]) if len(h["t"]) else 0.0
            self.timeline.append((round(self.t_run(), 3), "phase", str(h.get("phase") or "move")))
            self.draw_plan(h, n)
            ts, tw = self.now(), self.t_run()
            if self.mode == "dry":
                r = dict(ok=True, dry=True, hold=list(np.asarray(h["q"], float)[-1]), settle=None, track_max=0.0, f_max=0.0)
                self.robot_s += dur + float(h.get("hold_after", 0.1))
            else:
                w0 = time.time()
                r = self.be.execute(h)
                self.n_sent += 1
                spent = (time.time() - w0) if self.spec["backend"] == "robot" else None
                self.robot_s += dur + float(h.get("hold_after", 0.1)) if spent is None else min(spent, dur + 5.0)
            self.f_actions.write(protocol.jdump(dict(kind="execute", n=n, mode=self.mode, t_start=round(ts, 3), t_run=round(tw, 3),
                                                     dur=round(dur, 3), phase=h.get("phase"), grip=h.get("grip"),
                                                     settle=h.get("settle"), guard=h.get("guard"), t=h["t"], q=h["q"],
                                                     result=r)) + "\n")
            self.f_actions.flush()
            return dict(r), []
        if cmd == "idle":
            T_ = float(h["T"])
            if self.robot_s >= self.limit:
                return self.refuse(f"episode robot-time limit ({self.limit:.0f} s) reached"), []
            if self.mode == "live":
                self.be.idle(T_)
            self.robot_s += T_
            return dict(ok=True), []
        if cmd == "wait_gripper":
            if self.mode == "dry":
                st = self.be.state()
                return dict(ok=True, width=st.get("width"), dry=True), []
            r = self.be.wait_gripper(h.get("timeout", 4.0))
            return dict(r if isinstance(r, dict) else {}, ok=True), []
        if cmd == "camera":
            rgb, depth, K = self.be.frame(h["cam"])
            self.counts["camera"] += 1
            self.frames[frame_key(rgb)] = True
            self.save_frame(h["cam"], rgb, depth, K)
            return dict(ok=True, K=list(K), t=self.now()), [rgb, depth]
        if cmd in ("segment", "segment_points"):
            from agentic_robotics.perception import sam3
            rgb = arrays[0] if arrays else None
            if rgb is None or frame_key(rgb) not in self.frames:
                return dict(ok=True, scores=[], why="not a frame this episode's cameras delivered"), []
            self.counts["segment"] += 1
            t0 = time.time()
            res = sam3.segment(rgb, h["phrase"]) if cmd == "segment" else sam3.segment_points(rgb, h["pts"], h["labels"])
            self.log(cmd, phrase=h.get("phrase"), pts=h.get("pts"), scores=[round(s, 3) for _, s in res], wall_s=round(time.time() - t0, 2))
            return dict(ok=True, scores=[s for _, s in res]), [m.astype(np.uint8) for m, _ in res]
        if cmd == "vlm":
            if self.vlm is None:
                return dict(ok=False, why="no model calls in this run"), []
            if any(frame_key(a_) not in self.frames for a_ in arrays) or not 1 <= len(arrays) <= 4:
                return dict(ok=False, why="1-4 frames this episode's cameras delivered"), []
            self.counts["vlm"] += 1
            self.log("vlm request", prompt=str(h.get("prompt"))[:4000], n_images=len(arrays))
            r = self.vlm.ask(h.get("prompt", ""), list(arrays), detail=h.get("detail", "high"))
            self.log("vlm reply", **r)
            return dict(r), []
        if cmd == "vlm_converse":
            return dict(ok=False, why="no multi-turn model calls on the real robot runner"), []
        if cmd == "event":
            self.log(h.get("what"), **(h.get("data") or {}))
            self.timeline.append((round(self.t_run(), 3), "event", str(h.get("what"))[:80]))
            return dict(ok=True), []
        if cmd == "done":
            self.finish(h)
            return dict(ok=True), []
        return dict(ok=False, why=f"unknown request {cmd!r}"), []

    def save_frame(self, cam, rgb, depth, K):
        import cv2
        n = self.n_frame; self.n_frame += 1
        base = self.out / "frames" / f"{n:03d}_{cam}"
        cv2.imwrite(str(base) + "_rgb.png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        cv2.imwrite(str(base) + "_depth.png", np.where(np.isfinite(depth), np.clip(depth * 1000, 0, 65535), 0).astype(np.uint16))
        st = self.be.state()
        json.dump(dict(cam=cam, K=list(K), t=round(self.now(), 3), t_run=round(self.t_run(), 3), q=st.get("q"),
                       width=st.get("width")), open(str(base) + ".json", "w"))

    def finish(self, h):
        t_end = self.now()
        self.f_actions.close(); self.f_events.close()
        info = self.be.finish(h, self.out)
        (self.out / "timeline.json").write_text(json.dumps(self.timeline))
        if self.plans:
            sys.path.insert(0, str(ROOT / "real"))
            import cv2
            from ghost import sheet
            sh = sheet(self.plans[:24])
            cv2.imwrite(str(self.out / "plan" / "sheet.jpg"), cv2.cvtColor(sh, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
        video = info.get("video")
        if video and video.get("streams"):                         # (robot: episode.mp4 with the phases, as real_box)
            (self.out / "run.json").write_text(json.dumps(dict(server_log=(self.be.hello_info or {}).get("log"))))
            try:
                from recorder import compose
                info["episode_video"] = compose(self.out)
            except Exception as e:
                info["episode_video"] = f"not written: {e!r}"
        res = dict(mode=self.mode, backend=self.spec["backend"], task=self.task, program_result=h.get("result"),
                   program_error=h.get("error"), program=h.get("usage") or {}, model=self.vlm.usage() if self.vlm else None,
                   robot_time_s=round(t_end, 3), robot_time_limit_s=self.limit, time_limit_hit=t_end >= self.limit,
                   trajectories=dict(planned=self.n_traj, sent=self.n_sent, refused=self.n_refused, max=self.max_traj),
                   counts=self.counts, wall_s=round(time.time() - self.t_wall0, 1), **info)
        json.dump(res, open(self.out / "server.json", "w"), indent=1, default=protocol._default)
        self.finished = res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True); ap.add_argument("--sock", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import zmq
    spec = json.load(open(a.spec))
    out = Path(a.out)
    try:
        ep = RealEpisode(spec, out)
    except BaseException:
        out.mkdir(parents=True, exist_ok=True)
        (out / "server_error.txt").write_text(traceback.format_exc())
        raise
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REP)
    sock.bind(f"ipc://{a.sock}")
    os.chmod(a.sock, 0o600)
    Path(str(a.sock) + ".ready").write_text("ready\n")
    idle_timeout = float(spec.get("idle_timeout_s", 900))
    poller = zmq.Poller(); poller.register(sock, zmq.POLLIN)
    while ep.finished is None:
        if not poller.poll(int(idle_timeout * 1000)):
            ep.finish(dict(result=None, error=f"no request for {idle_timeout:.0f} s (the program hung or died)"))
            break
        h, arrays = protocol.recv(sock)
        try:
            r, arr = ep.handle(h, arrays)
        except Exception as e:
            r, arr = dict(ok=False, why=f"server error: {type(e).__name__}: {e}"[:400]), []
            (out / "server_error.txt").write_text(traceback.format_exc())
        protocol.send(sock, r, arr)
    sock.close(linger=0); ctx.term()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
