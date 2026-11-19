"""The world server: ONE episode of the clutter-pick scene in its own process (PRIVILEGED: the only process holding the
true state). It serves the robot (the arm server's calls over the twin's compliant arm: the same limit checks), the
two cameras (D415 model), the perception service, and records everything: actions, observations, events, a video,
the judge's verdict (run every 1/30 s of robot time) and the timing. Skill programs reach it only through the Unix
socket (protocol.py); nothing in a reply carries object poses or the target's location.

    python -I -m agentic_robotics.sim.server --spec <episode.json> --sock <dir>/world.sock --out <episode dir>

The spec (written by execution.episode): split, level, idx, seed, scene options (preset, colour_noise), perception
backend ("oracle"), robot_time_limit_s, record options (video, observations), parity (true: the task carries the
11-18 baseline's truth hint; never set by learning or evaluation)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from ..execution import protocol
from ..perception import oracle


class Recorder:
    """TwinArm's recorder slot (called every physics frame): the judge at JUDGE_HZ of robot time, video frames at fps."""
    JUDGE_HZ = 30.0

    def __init__(self, scene, fps=10.0, video=True, W=480, H=360):
        self.scene, self.video, self.fps_v, self.W, self.H = scene, video, float(fps), W, H
        self.fps, self.depth_fps = self.JUDGE_HZ, 0
        self.next_j = self.next_v = scene.P.d.time
        self.frames = []           # (t, overview, wrist)
        self._wrist = self._over = None
        self.n_judge = 0
        self.render_wall = 0.0
        self.phase = ""

    def wants(self, name, t):
        if name == "scene" and t >= self.next_j - 1e-9:
            self.next_j = t + 1.0 / self.JUDGE_HZ
            self.scene.jd = self.scene.judge(); self.n_judge += 1
        if self.video and t >= self.next_v - 1e-9:
            return name in ("scene", "wrist")
        return False

    def offer(self, name, t, rgb, dep):
        import cv2
        t0 = time.time()
        if name == "wrist":
            self._wrist = cv2.resize(rgb, (self.W, self.H), interpolation=cv2.INTER_AREA)
            self.frames.append((t - self.scene.t0, self._over, self._wrist, self.phase))
            self.next_v = t + 1.0 / self.fps_v
        else:
            self._over = cv2.resize(rgb, (self.W, self.H), interpolation=cv2.INTER_AREA)
        self.render_wall += time.time() - t0


class World:
    def __init__(self, spec, out):
        from .scene import ClutterPickScene
        self.spec, self.out = spec, Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        sc = spec.get("scene", {})
        self.scene = ClutterPickScene(preset=sc.get("preset", "d415"), colour_noise=sc.get("colour_noise", False),
                                      colour=sc.get("colour", "mujoco"), object_set=sc.get("object_set", "real"))
        if spec.get("layout") is not None:                          # (a scene the agent wrote: sim/custom.py, practice)
            self.task = self.scene.reset_layout(spec["layout"], spec["seed"])
        else:
            self.task = self.scene.reset(spec["split"], spec["level"], spec["idx"], spec["seed"])
        self.vlm = None
        if (spec.get("vlm") or {}).get("enabled"):                 # (Astra: perception/vlm.py, budgets there)
            from ..perception.vlm import VLM
            v = spec["vlm"]
            self.vlm = VLM(v, tag=v.get("tag", "untagged"), cache_dir=v.get("cache_dir"))
        if spec.get("parity"):
            self.task = dict(self.task, hint=self.scene.truth_hint())
        if spec.get("test_name_other_roll"):                       # (judge validation only: the program is told the
            other = "tape_yellow" if self.scene.target == "tape_red" else "tape_red"   # OTHER roll's name; the judge
            ph = self.scene.phrases[other]                          # still judges the true target: wrong_object)
            self.task = dict(self.task, object=ph, instruction=f"Pick up the {ph} and lift it.")
        P = self.scene.P
        self.arm = P.arm
        rc = spec.get("record", {})
        self.rec = Recorder(self.scene, fps=rc.get("video_fps", 10.0), video=rc.get("video", True))
        self.arm.renderer = None
        if self.rec.video:
            self.arm.attach_recorder(self.rec)                     # (creates the arm's 640x480 renderer)
            c = P.mj.MjvCamera(); c.type = P.mj.mjtCamera.mjCAMERA_FREE
            tp = self.scene.xy0[self.scene.target]
            c.lookat[:] = np.r_[tp, 0.10]; c.distance = 0.95; c.azimuth = 205.0; c.elevation = -32.0
            self.arm.rec_cam = c
        else:
            self.arm.rec, self.arm.state_log = self.rec, None
        self.rec.next_j = self.rec.next_v = float(P.d.time)
        self.limit = float(spec.get("robot_time_limit_s", 60.0))
        self.save_obs = rc.get("observations", "all")               # all | none
        self.perc = spec.get("perception", "oracle")
        self.n_obs = 0
        self.wall = dict(physics=0.0, camera=0.0, perception=0.0, idle_wait=0.0)
        self.counts = dict(execute=0, idle=0, camera=0, segment=0, refused=0)
        self.f_actions = open(self.out / "actions.jsonl", "w")
        self.f_events = open(self.out / "events.jsonl", "w")
        if self.save_obs != "none":
            (self.out / "observations").mkdir(exist_ok=True)
        self.t_wall0 = time.time()
        self.finished = None
        self.platform_errors = []                                   # (a server-side failure voids the episode)

    # ------------------------------------------------------------------ the robot's calls
    def t_rel(self):
        return float(self.scene.P.d.time) - self.scene.t0

    def over_time(self):
        return self.t_rel() >= self.limit

    def handle(self, h, arrays):
        cmd = h.get("cmd")
        if cmd == "hello":
            r = self.arm.hello(); return dict(r, ok=True, robot_time_limit_s=self.limit, t0=self.scene.t0), []
        if cmd == "task":
            return dict(ok=True, task=self.task), []
        if cmd == "now":
            return dict(ok=True, t=self.arm.now()), []
        if cmd == "state":
            return dict(self.arm.state(), ok=True), []
        if cmd == "execute":
            if self.over_time():
                self.counts["refused"] += 1
                return dict(ok=False, refused=True, why=f"episode robot-time limit ({self.limit:.0f} s) reached"), []
            t0w, ts = time.time(), self.t_rel()
            self.rec.phase = str(h.get("phase") or "")
            r = self.arm.execute(h["t"], h["q"], grip=h.get("grip"), settle=h.get("settle", False), phase=h.get("phase"),
                                 hold_after=h.get("hold_after", 0.1), guard=h.get("guard"))
            self.wall["physics"] += time.time() - t0w; self.counts["execute"] += 1
            self.f_actions.write(protocol.jdump(dict(kind="execute", t_start=round(ts, 4), t_end=round(self.t_rel(), 4),
                                                     phase=h.get("phase"), grip=h.get("grip"), settle=h.get("settle"),
                                                     guard=h.get("guard"), t=h["t"], q=h["q"], result=r)) + "\n")
            return dict(r), []
        if cmd == "idle":
            if self.over_time():
                self.counts["refused"] += 1
                return dict(ok=False, refused=True, why=f"episode robot-time limit ({self.limit:.0f} s) reached"), []
            t0w, ts = time.time(), self.t_rel()
            self.arm.idle(float(h["T"]))
            self.wall["physics"] += time.time() - t0w; self.counts["idle"] += 1
            self.f_actions.write(protocol.jdump(dict(kind="idle", t_start=round(ts, 4), t_end=round(self.t_rel(), 4),
                                                     T=h["T"])) + "\n")
            return dict(ok=True), []
        if cmd == "wait_gripper":
            t0w = time.time()
            r = self.arm.wait_gripper(timeout=h.get("timeout", 4.0))
            self.wall["physics"] += time.time() - t0w
            return dict(r if isinstance(r, dict) else {}, ok=True), []
        if cmd == "camera":
            t0w = time.time()
            rgb, depth, K = self.scene.camera(h["cam"])
            self.wall["camera"] += time.time() - t0w; self.counts["camera"] += 1
            self.save_frame(h["cam"], rgb, depth, K)
            return dict(ok=True, K=list(K), t=float(self.scene.P.d.time)), [rgb, depth]
        if cmd in ("segment", "segment_points"):
            t0w = time.time(); self.counts["segment"] += 1
            rgb = arrays[0] if arrays else None
            lab = self.scene.labels_for(rgb) if rgb is not None else None
            if lab is None:                                     # (only frames this world's cameras delivered)
                return dict(ok=True, scores=[], why="not a frame this world delivered"), []
            if self.perc == "oracle":
                res = (oracle.segment(lab, self.scene.P.obj_keys, self.scene.target, h["phrase"], self.scene.phrases) if cmd == "segment"
                       else oracle.segment_points(lab, h["pts"], h["labels"]))
            elif self.perc == "sam3":
                from ..perception import sam3
                res = sam3.segment(rgb, h["phrase"]) if cmd == "segment" else sam3.segment_points(rgb, h["pts"], h["labels"])
            else:
                return dict(ok=False, why=f"perception backend {self.perc!r} unknown"), []
            self.wall["perception"] += time.time() - t0w
            return dict(ok=True, scores=[s for _, s in res]), [m.astype(np.uint8) for m, _ in res]
        if cmd == "vlm":
            if self.vlm is None:
                return dict(ok=False, why="no model calls in this experiment"), []
            ims = []
            for a_ in arrays:
                if self.scene.labels_for(a_) is None:
                    return dict(ok=False, why="images must be frames this episode's cameras delivered"), []
                ims.append(a_)
            if not 1 <= len(ims) <= 4:
                return dict(ok=False, why="1 to 4 images per call"), []
            self.f_events.write(protocol.jdump(dict(t=round(self.t_rel(), 3), what="vlm request", data=dict(prompt=str(h.get("prompt"))[:4000], n_images=len(ims)))) + "\n")
            r = self.vlm.ask(h.get("prompt", ""), ims, detail=h.get("detail", "high"))
            self.f_events.write(protocol.jdump(dict(t=round(self.t_rel(), 3), what="vlm reply", data={k: v for k, v in r.items()})) + "\n")
            return dict(r), []
        if cmd == "event":
            self.f_events.write(protocol.jdump(dict(t=round(self.t_rel(), 3), what=h.get("what"), data=h.get("data"))) + "\n")
            return dict(ok=True), []
        if cmd == "done":
            self.finish(h)
            return dict(ok=True), []
        return dict(ok=False, why=f"unknown request {cmd!r}"), []

    def save_frame(self, cam, rgb, depth, K):
        if self.save_obs == "none":
            return
        import cv2
        n = self.n_obs; self.n_obs += 1
        base = self.out / "observations" / f"{n:03d}_{cam}"
        cv2.imwrite(str(base) + "_rgb.png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        cv2.imwrite(str(base) + "_depth.png", np.where(np.isfinite(depth), np.clip(depth * 1000, 0, 65535), 0).astype(np.uint16))
        st = self.arm.state()
        json.dump(dict(cam=cam, K=list(K), t=round(self.t_rel(), 4), q=st["q"], width=st["width"]), open(str(base) + ".json", "w"))

    # ------------------------------------------------------------------ the end
    def finish(self, h):
        """After the program's last word: up to 3 s more (the arm holding) while the target is held, for the judge's
        1 s hold; then the record."""
        sc = self.scene
        t_prog_end = self.t_rel()
        for _ in range(30):
            if sc.t_success is not None or not sc.jd["held"]:
                break
            self.arm.idle(0.1)
        sc.jd = sc.judge()
        S = sc.summary()
        self.f_actions.close(); self.f_events.close()
        if self.rec.video and self.rec.frames:
            write_video(self.rec.frames, self.out / "video.mp4", self.rec.fps_v, S)
            if self.spec.get("record", {}).get("keyframes", True):
                write_keyframes(self.rec.frames, self.out / "keyframes.png", S)
        if self.platform_errors:                                    # (not the program's failure: void, re-run)
            S = dict(S, judge_category=S["category"], category="platform_error", success=False,
                     platform_errors=self.platform_errors[:5])
        res = dict(S, program_result=h.get("result"), program_error=h.get("error"),
                   robot_time_s=round(t_prog_end, 3), robot_time_total_s=round(self.t_rel(), 3),
                   robot_time_limit_s=self.limit, time_limit_hit=t_prog_end >= self.limit,
                   wall=dict({k: round(v, 3) for k, v in self.wall.items()}, video_render=round(self.rec.render_wall, 3),
                             server_total=round(time.time() - self.t_wall0, 3)),
                   program=dict(h.get("usage") or {}, **(self.vlm.usage() if self.vlm is not None else {})),
                   counts=self.counts, judge_calls=self.rec.n_judge,
                   task=self.task, episode={k: self.spec.get(k) for k in ("split", "level", "idx", "seed")},
                   custom_scene=(self.spec.get("layout") or {}).get("custom") if self.spec.get("layout") else None)
        json.dump(res, open(self.out / "result.json", "w"), indent=1, default=protocol._default)
        self.finished = res


def write_video(frames, path, fps, S):
    import subprocess
    import cv2
    out = []
    for t, over, wrist, phase in frames:
        if over is None:
            over = np.zeros_like(wrist)
        im = np.concatenate([over, wrist], 1)
        bar = np.full((30, im.shape[1], 3), 25, np.uint8)
        txt = f"t {t:5.1f} s  {phase[:40]}"
        cv2.putText(bar, txt, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        out.append(np.concatenate([bar, im], 0))
    last = out[-1].copy()
    verdict = f"{S['category']}" + (f"  {S['t_success']:.1f} s" if S["t_success"] is not None else "")
    cv2.putText(last, verdict, (8, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (120, 255, 120) if S["success"] else (255, 120, 120), 2, cv2.LINE_AA)
    out += [last] * int(fps)
    h, w = out[0].shape[:2]; h -= h % 2; w -= w % 2
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                          "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", str(path)],
                         stdin=subprocess.PIPE)
    for f in out:
        p.stdin.write(np.ascontiguousarray(f[:h, :w]).tobytes())
    p.stdin.close(); p.wait()


def write_keyframes(frames, path, S, n=8):
    """A contact sheet: n frames evenly spread over the episode (overview | wrist each, half size), robot time on each."""
    import cv2
    idx = np.linspace(0, len(frames) - 1, n).round().astype(int)
    tiles = []
    for i in idx:
        t, over, wrist, phase = frames[i]
        over = np.zeros_like(wrist) if over is None else over
        im = cv2.resize(np.concatenate([over, wrist], 1), (480, 180), interpolation=cv2.INTER_AREA)
        cv2.putText(im, f"t {t:.1f} s {phase[:28]}", (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1, cv2.LINE_AA)
        tiles.append(im)
    rows = [np.concatenate(tiles[k:k + 2], 1) for k in range(0, len(tiles), 2)]
    sheet = np.concatenate(rows, 0)
    bar = np.full((26, sheet.shape[1], 3), 25, np.uint8)
    v = f"{S['category']}" + (f" at {S['t_success']:.1f} s" if S["t_success"] is not None else "") + f", max lift {S['max_lift_mm']} mm"
    cv2.putText(bar, v, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), cv2.cvtColor(np.concatenate([bar, sheet], 0), cv2.COLOR_RGB2BGR))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--sock", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import zmq
    spec = json.load(open(a.spec))
    try:
        world = World(spec, a.out)
    except Exception:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "server_error.txt").write_text(traceback.format_exc())
        raise
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REP)
    sock.bind(f"ipc://{a.sock}")
    os.chmod(a.sock, 0o600)
    Path(str(a.sock) + ".ready").write_text("ready\n")
    idle_timeout = float(spec.get("idle_timeout_s", 600))
    poller = zmq.Poller(); poller.register(sock, zmq.POLLIN)
    while world.finished is None:
        t0 = time.time()
        if not poller.poll(int(idle_timeout * 1000)):
            world.finish(dict(result=None, error=f"no request for {idle_timeout:.0f} s (program hung or died)"))
            break
        world.wall["idle_wait"] += time.time() - t0
        h, arrays = protocol.recv(sock)
        try:
            r, out = world.handle(h, arrays)
        except Exception as e:
            r, out = dict(ok=False, why=f"server error: {type(e).__name__}: {e}"[:400]), []
            world.platform_errors.append(f"{h.get('cmd')}: {type(e).__name__}: {e}"[:300])
            (Path(a.out) / "server_error.txt").write_text(traceback.format_exc())
        protocol.send(sock, r, out)
    sock.close(linger=0); ctx.term()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0)                                                 # (skip the EGL context's noisy destructor at exit)


if __name__ == "__main__":
    main()
