"""Episode recording, on the run's clock (RealSim.events_t0 = t_run 0, as timeline.json):

  Recorder   both cameras' colour as MP4 (H.264, rig.json record.fps, default 15) and optionally their depth (lossless
             FFV1 16-bit mm in .mkv, record.depth_fps), each frame's time in <stream>.csv; fed by the camera threads
             (RealSenseCams) or the twin (TwinArm, sim time). Encoded by ffmpeg in its own thread: a frame that cannot
             be queued is dropped and counted, the camera threads never wait.
  StateLog   the arm's state at a fixed rate (rows of float64, columns in <name>.json): the arm server writes one for
             its whole life (also between moves and read-only), the twin backend writes one into the run.
  compose    -> episode.mp4: scene | wrist side by side, with the run time, the phase, the gripper width, the force
             and the joint speed written under each frame.

    python recorder.py compose out/real/run_robot_<time>_<pid> [--server out/real/server_<time>]
    python recorder.py info    out/real/run_robot_<time>_<pid>

numpy only at import (the arm server's env imports StateLog); cv2 for compose.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

STATE_COLS = (["t"] + [f"q{i}" for i in range(1, 8)] + [f"dq{i}" for i in range(1, 8)] + [f"goal{i}" for i in range(1, 8)]
              + ["fx", "fy", "fz", "tx", "ty", "tz", "width", "grasped", "mode", "engaged", "moving", "traj"])


class Recorder:
    def __init__(self, out, t0, fps=15.0, depth_fps=0.0, crf=23, clock="unix"):
        self.dir = Path(out) / "video"; self.dir.mkdir(parents=True, exist_ok=True)
        self.t0, self.fps, self.depth_fps, self.crf, self.clock = float(t0), float(fps), float(depth_fps), int(crf), clock
        self.q = queue.Queue(maxsize=int(4 * fps) * 2)                  # (about 4 s of both cameras)
        self.next, self.enc, self.dropped, self.errors = {}, {}, 0, []
        self.open = True
        self.th = threading.Thread(target=self._loop, daemon=True); self.th.start()

    def _due(self, key, t, period):
        """One frame per period, by the frames' own times (a 30 fps camera -> every other frame at 15 fps)."""
        nx = self.next.get(key)
        if nx is not None and t < nx - 0.3 * period:                  # (0.3: the frames' jitter)
            return False
        self.next[key] = t + period if nx is None or t > nx + period else nx + period
        return True

    def wants(self, cam, t):
        """(For a source that must render a frame first: the twin.) Is a colour or depth frame of cam due at t?"""
        due = lambda key, period: self.next.get(key) is None or t >= self.next[key] - 0.3 * period
        return self.open and (due(cam + "_rgb", 1.0 / self.fps) or (self.depth_fps > 0 and due(cam + "_depth", 1.0 / self.depth_fps)))

    def offer(self, cam, t, rgb=None, depth=None):
        """A camera frame (rgb uint8 HxWx3; depth float m, inf = none) taken at t (the run's clock source)."""
        if not self.open:
            return
        items = []
        if rgb is not None and self._due(cam + "_rgb", t, 1.0 / self.fps):
            items.append((cam + "_rgb", t, rgb))
        if depth is not None and self.depth_fps > 0 and self._due(cam + "_depth", t, 1.0 / self.depth_fps):
            items.append((cam + "_depth", t, depth))
        for it in items:
            try:
                self.q.put_nowait(it)
            except queue.Full:
                self.dropped += 1

    def _start(self, key, a):
        H, W = a.shape[:2]
        if key.endswith("_rgb"):
            path = self.dir / f"{key[:-4]}.mp4"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                   "-framerate", f"{self.fps:g}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(self.crf),
                   "-pix_fmt", "yuv420p", str(path)]
        else:
            path = self.dir / f"{key}.mkv"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray16le", "-s", f"{W}x{H}",
                   "-framerate", f"{self.depth_fps:g}", "-i", "-", "-c:v", "ffv1", "-level", "3", str(path)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        csv = open(self.dir / f"{key}.csv", "w"); csv.write("i,t_clock,t_run\n")
        self.enc[key] = dict(p=p, csv=csv, n=0, path=path.name, size=(W, H), t_first=None, t_last=None)
        return self.enc[key]

    def _loop(self):
        while True:
            it = self.q.get()
            if it is None:
                return
            key, t, a = it
            try:
                if key.endswith("_depth"):
                    a = np.where(np.isfinite(a), np.clip(np.asarray(a, np.float32) * 1000.0, 0, 65535), 0).astype("<u2")
                e = self.enc.get(key) or self._start(key, a)
                if a.shape[1::-1] != e["size"]:
                    raise ValueError(f"{key}: frame size {a.shape[1::-1]} != {e['size']}")
                e["p"].stdin.write(np.ascontiguousarray(a).tobytes())
                e["csv"].write(f"{e['n']},{t:.4f},{t - self.t0:.4f}\n")
                e["n"] += 1; e["t_last"] = t
                if e["t_first"] is None:
                    e["t_first"] = t
            except Exception as ex:                                     # (recording never stops the run)
                if len(self.errors) < 20:
                    self.errors.append(f"{key}: {ex!r}")

    def close(self):
        """Finish the files (waits for the queue and ffmpeg). -> summary dict (also video/meta.json)."""
        if not self.open:
            return None
        self.open = False
        self.q.put(None); self.th.join(timeout=60)
        streams = {}
        for key, e in self.enc.items():
            try:
                e["p"].stdin.close(); err = e["p"].stderr.read().decode(errors="replace").strip(); e["p"].wait(timeout=60)
                if err:
                    self.errors.append(f"{key} ffmpeg: {err[-300:]}")
            except Exception as ex:
                self.errors.append(f"{key} close: {ex!r}")
            e["csv"].close()
            dur = (e["t_last"] - e["t_first"]) if e["n"] > 1 else 0.0
            streams[key] = dict(file=e["path"], frames=e["n"], size=e["size"], t_run_first=None if e["t_first"] is None else round(e["t_first"] - self.t0, 3),
                                seconds=round(dur, 2), fps_actual=round((e["n"] - 1) / dur, 2) if dur > 0 else None)
        meta = dict(clock=self.clock, t0_clock=self.t0, fps=self.fps, depth_fps=self.depth_fps, crf=self.crf, dropped=self.dropped,
                    errors=self.errors, streams=streams, depth_units="mm, uint16, 0 = no depth (FFV1, lossless)")
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=1))
        return meta


class StateLog:
    """Rows of float64 (STATE_COLS), appended and flushed one by one (nothing lost if the process dies)."""

    def __init__(self, path, clock="unix", hz=15.0, **info):
        self.path = Path(path)
        self.f = open(self.path.with_suffix(".f64"), "ab")
        self.path.with_suffix(".json").write_text(json.dumps(dict(cols=STATE_COLS, clock=clock, hz=hz, dtype="float64", **info), indent=1))
        self.lock = threading.Lock()

    def write(self, t, q, dq, goal=None, f=None, width=None, grasped=None, mode=None, engaged=False, moving=False, traj=0):
        nan = float("nan")
        row = np.r_[t, np.asarray(q, float), np.asarray(dq, float), np.full(7, nan) if goal is None else np.asarray(goal, float),
                    np.full(6, nan) if f is None else np.asarray(f, float)[:6],
                    nan if width is None else width, nan if grasped is None else float(grasped), nan if mode is None else mode,
                    float(engaged), float(moving), float(traj)].astype("<f8")
        with self.lock:
            self.f.write(row.tobytes()); self.f.flush()

    def close(self):
        self.f.close()


def read_state(path):
    """-> (dict col -> array, info) of a StateLog (path with or without the suffix)."""
    p = Path(path)
    info = json.loads(p.with_suffix(".json").read_text())
    a = np.fromfile(p.with_suffix(".f64"), "<f8")
    nc = len(info["cols"]); a = a[: len(a) // nc * nc].reshape(-1, nc)
    return {c: a[:, i] for i, c in enumerate(info["cols"])}, info


def read_times(run_dir, key):
    """-> t_run of each frame of a stream (key e.g. 'scene_rgb', 'wrist_depth')."""
    p = Path(run_dir) / "video" / f"{key}.csv"
    return np.loadtxt(p, delimiter=",", skiprows=1, ndmin=2)[:, 2] if p.exists() and p.stat().st_size > 20 else np.zeros(0)


def read_depth(run_dir, cam):
    """Iterate (t_run, depth in m with inf = none) over a recorded depth stream."""
    d = Path(run_dir) / "video"
    meta = json.loads((d / "meta.json").read_text())
    s = meta["streams"][f"{cam}_depth"]; W, H = s["size"]
    ts = read_times(run_dir, f"{cam}_depth")
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", str(d / s["file"]), "-f", "rawvideo", "-pix_fmt", "gray16le", "-"],
                         stdout=subprocess.PIPE)
    for t in ts:
        b = p.stdout.read(W * H * 2)
        if len(b) < W * H * 2:
            break
        z = np.frombuffer(b, "<u2").reshape(H, W).astype(np.float32) / 1000.0
        yield t, np.where(z > 0, z, np.inf)
    p.stdout.close(); p.wait()


# ---------------------------------------------------------------- the episode video
def _frames(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    while True:
        ok, im = cap.read()
        if not ok:
            return
        yield im


def compose(run_dir, server_dir=None, out=None, fps=None):
    """episode.mp4: scene | wrist (each frame the latest at that time), and under them: run time, the phase (timeline),
    gripper width, force change, fastest joint (the state log: the server's for the robot, the run's own for the twin)."""
    import cv2
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "video" / "meta.json").read_text())
    fps = fps or meta["fps"]
    cams = [c for c in ("scene", "wrist") if f"{c}_rgb" in meta["streams"]]
    if not cams:
        raise SystemExit("no colour stream recorded")
    run = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").exists() else {}
    tl = json.loads((run_dir / "timeline.json").read_text()) if (run_dir / "timeline.json").exists() else []
    phases = [(float(t), str(x)) for t, kind, x in tl if kind == "phase"]
    events = [(float(t), str(x)) for t, kind, x in tl if kind == "event"]
    st = None
    cand = [run_dir / "state"]
    if server_dir or run.get("server_log"):
        cand.append(Path(server_dir or run["server_log"]) / "state")
    for c in cand:
        if c.with_suffix(".f64").exists():
            st, _ = read_state(c); st["t_run"] = st["t"] - meta["t0_clock"]; break
    ts = {c: read_times(run_dir, f"{c}_rgb") for c in cams}
    gens = {c: _frames(run_dir / "video" / meta["streams"][f"{c}_rgb"]["file"]) for c in cams}
    W, H = meta["streams"][f"{cams[0]}_rgb"]["size"]
    bar = 64
    out = Path(out) if out else run_dir / "episode.mp4"
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W * len(cams)}x{H + bar}",
                          "-framerate", f"{fps:g}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                          "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    t_a = min(ts[c][0] for c in cams if len(ts[c])); t_b = max(ts[c][-1] for c in cams if len(ts[c]))
    cur = {c: np.zeros((H, W, 3), np.uint8) for c in cams}; k = {c: -1 for c in cams}
    f0 = None; n = 0
    for t in np.arange(t_a, t_b + 1e-6, 1.0 / fps):
        for c in cams:                                                  # (each camera: its latest frame at t)
            while k[c] + 1 < len(ts[c]) and ts[c][k[c] + 1] <= t + 1e-6:
                im = next(gens[c], None)
                if im is None:
                    break
                cur[c] = im; k[c] += 1
        img = np.zeros((H + bar, W * len(cams), 3), np.uint8)
        for j, c in enumerate(cams):
            img[:H, j * W:(j + 1) * W] = cur[c]
            cv2.putText(img, c, (j * W + 8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        ph = next((x for tt, x in reversed(phases) if tt <= t), "")
        ev = next((x for tt, x in reversed(events) if tt <= t and t - tt < 2.0), "")
        line1 = f"t {t:6.1f} s   {ph[:70]}" + (f"   [{ev[:40]}]" if ev else "")
        line2 = ""
        if st is not None and len(st["t_run"]):
            i = int(np.clip(np.searchsorted(st["t_run"], t), 0, len(st["t_run"]) - 1))
            fv = np.array([st["fx"][i], st["fy"][i], st["fz"][i]])
            if f0 is None and np.all(np.isfinite(fv)):
                f0 = fv
            dqm = max(abs(st[f"dq{m}"][i]) for m in range(1, 8))
            wd = st["width"][i]
            line2 = (f"gripper {'-' if not np.isfinite(wd) else '%.0f mm' % (wd * 1000)}   force {np.linalg.norm(fv - f0) if f0 is not None else float('nan'):5.1f} N"
                     f"   fastest joint {dqm:4.2f} rad/s" + ("   MOVING" if st["moving"][i] > 0 else ""))
        cv2.putText(img, line1, (8, H + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(img, line2, (8, H + 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 220, 255), 1, cv2.LINE_AA)
        p.stdin.write(img.tobytes()); n += 1
    p.stdin.close(); p.wait()
    return dict(file=str(out), frames=n, seconds=round(n / fps, 1), state=st is not None)


def info(run_dir):
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "video" / "meta.json").read_text())
    print(json.dumps({k: meta[k] for k in ("clock", "fps", "depth_fps", "dropped", "errors")}, indent=None))
    for k, s in meta["streams"].items():
        print(f"  {k}: {s['frames']} frames over {s['seconds']} s ({s['fps_actual']} fps), from t_run {s['t_run_first']} s, {s['file']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["compose", "info"])
    ap.add_argument("run_dir")
    ap.add_argument("--server", default=None, help="compose: the arm server's log dir (default: run.json server_log)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.cmd == "info":
        info(a.run_dir)
    else:
        print(compose(a.run_dir, a.server, a.out))


if __name__ == "__main__":
    main()
