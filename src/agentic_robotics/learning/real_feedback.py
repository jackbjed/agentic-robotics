"""Real-robot feedback into a learning run (2026-11-19, user: "if we run it and get the real world feedback, will the
sessions ... use it for the next iteration?"). Episodes run on the real Panda with real/run.py (real/results/<run>/)
are copied into the agent's workspace, `real/NNN_<name>/`, and the next session's prompt announces them. The operator:

    ar learn add-real --run-id ID real/results/<run> [real/results/<run> ...]

Each import holds:
  summary.md     what ran (which version of this run's skill, dry or live, robot or twin), the verdict, robot time,
                 trajectories planned / sent / refused, the program's own result or error
  result.json    the run's record, trimmed (no machine paths)
  events.jsonl   the program's events, its SAM3 calls (phrase, scores), model requests and replies
  actions.md     every trajectory: phase, duration, planned (dry) or sent (live), the arm server's reply (no joint arrays)
  program/       the program's own output (program.json, host.log, program_error.txt, debug images)
  plan/          each planned move drawn over the scene camera (NNN.jpg, sheet.jpg)
  frames/        every camera frame the program got (colour, depth in mm, the joints then)
  keyframes.png  8 frames of episode.mp4 (scene | wrist, with the phase, gripper and force under each)
Recorded in the run's ledger (kind "real"); nothing of the operator's machine (paths, keys) is copied."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .. import paths

README = """# real/: your skill on the real robot

The operator ran versions of your skill on the real Franka Panda (real D415 images, the real arm at the robot's speed,
half the twin's move times) and copied each episode here, `NNN_<name>/`. Read `summary.md` first, then what you
need:
- `keyframes.png`: 8 frames, scene | wrist camera, with the phase, gripper and force;
- `frames/`: every camera frame your program got;
- `plan/`: each move it planned, drawn over the scene camera;
- `events.jsonl`: what it logged, and its SAM3 calls;
- `actions.md`: each trajectory and the arm's reply;
- `program/`: its own output and errors.

**Dry runs** sent no motion. The arm stayed where it was, so they show the perception and the first plans on real
images, not the outcome.

**Live runs** moved the arm. Their verdict is the operator's.

A change for the real robot must still pass the simulated development evaluation to be kept.
"""


def _versions(run_dir):
    from ..execution.versions import tree_hash
    out = {}
    for d in sorted((run_dir / "checkpoints").iterdir()):
        if d.is_dir():
            out[tree_hash(d)] = d.name
    for d in sorted((run_dir / "candidates").iterdir()) if (run_dir / "candidates").exists() else []:
        if d.is_dir():
            out.setdefault(tree_hash(d), f"{d.name} (candidate)")
    return out


def _keyframes(video, out, n=8):
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(video))
    N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if N <= 0:
        return False
    tiles = []
    for i in np.linspace(0, N - 1, n).round().astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, im = cap.read()
        if ok:
            tiles.append(cv2.resize(im, (640, int(im.shape[0] * 640 / im.shape[1]))))
    cap.release()
    if not tiles:
        return False
    while len(tiles) % 2:
        tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(str(out), np.concatenate([np.concatenate(tiles[k:k + 2], 1) for k in range(0, len(tiles), 2)], 0))
    return True


def _verdict_text(r):
    v = r.get("verdict") or {}
    if r.get("mode") == "dry":
        return "dry run: no motion was sent (perception and planned moves only)"
    if v.get("success") is None:
        return f"no verdict ({v.get('by')})"
    s = "SUCCESS" if v.get("success") else "FAIL"
    extra = [k for k in ("knocked_off", "intervened") if v.get(k)]
    held = v.get("held")
    return (f"{s} (by {v.get('by')})" + (f"; held up by both fingers: {'yes' if held else 'no'}" if held is not None else "")
            + (f"; {', '.join(x.replace('_', ' ') for x in extra)}" if extra else "") + (f"; note: \"{v['note']}\"" if v.get("note") else ""))


def summary_md(name, r, version):
    tr = r.get("trajectories") or {}
    pr = r.get("program_result")
    L = [f"# {name}", "",
         f"- **Version:** {version}",
         f"- **Where:** {'the real robot' if r.get('backend') == 'robot' else 'the twin standing in for it (a rehearsal)'}, "
         f"{'LIVE (the arm moved)' if r.get('mode') == 'live' else 'DRY (nothing was sent)'}",
         f"- **Task:** {(r.get('task') or {}).get('instruction')}",
         f"- **Outcome:** {_verdict_text(r)}",
         f"- **Robot time:** {r.get('robot_time_s')} s (limit {r.get('robot_time_limit_s')} s; motion and waits only)",
         f"- **Trajectories:** {tr.get('planned')} planned, {tr.get('sent')} sent, {tr.get('refused')} refused",
         f"- **Your program returned:** {json.dumps(pr, default=str)[:600] if pr is not None else None}",
         f"- **Program error:** {r.get('program_error')}",
         f"- **Model calls:** {(r.get('model') or {}).get('model_calls', 0)}"]
    if r.get("interrupted"):
        L.append("- **The operator stopped it** (Ctrl-C).")
    return "\n".join(L) + "\n"


def actions_md(src):
    f = src / "actions.jsonl"
    if not f.exists():
        return "(no trajectories)\n"
    L = ["| # | phase | duration s | sent | reply |", "|---|---|---|---|---|"]
    for line in open(f):
        a = json.loads(line)
        rep = a.get("result") or {}
        rep = {k: v for k, v in rep.items() if k not in ("hold", "q")}
        L.append(f"| {a.get('n')} | {str(a.get('phase'))[:70]} | {a.get('dur')} | {'no (dry)' if a.get('mode') == 'dry' else 'yes'} | "
                 f"{json.dumps(rep, default=str)[:160]} |")
    return "\n".join(L) + "\n"


def add_real(run_id, dirs):
    from .loop import ledger, run_dir, state
    rd, ws = run_dir(run_id), paths.WORKSPACES / run_id
    if not rd.exists():
        raise SystemExit(f"{run_id}: no such run")
    real = ws / "real"; real.mkdir(exist_ok=True)
    (real / "README.md").write_text(README)
    vers = _versions(rd)
    st = state(run_id)
    incumbent = st.get("incumbent")
    pending = list(st.get("real_pending") or [])
    n0 = len([d for d in real.iterdir() if d.is_dir()])
    for k, d in enumerate(dirs):
        src = Path(d).resolve()
        rf = src / "result.json"
        if not rf.exists():
            raise SystemExit(f"{src}: no result.json (a real/run episode directory?)")
        r = json.loads(rf.read_text())
        h = (r.get("spec") or {}).get("skills_hash")
        v = vers.get(h)
        version = (f"{v}" + (" (the current version)" if v == incumbent else "")) if v else "not a version of this run's skill"
        name = f"{n0 + k + 1:03d}_{src.name}"
        out = real / name; out.mkdir()
        (out / "summary.md").write_text(summary_md(name, r, version))
        keep = {k_: r.get(k_) for k_ in ("mode", "backend", "task", "verdict", "program_result", "program_error", "program",
                                          "robot_time_s", "robot_time_limit_s", "time_limit_hit", "trajectories", "counts",
                                          "interrupted", "judge")}
        keep["version"] = version
        (out / "result.json").write_text(json.dumps(keep, indent=1, default=str))
        (out / "actions.md").write_text(actions_md(src))
        for f in ("events.jsonl",):
            if (src / f).exists():
                shutil.copy2(src / f, out / f)
        for sub in ("program", "plan", "frames"):
            if (src / sub).exists():
                shutil.copytree(src / sub, out / sub, ignore=shutil.ignore_patterns("__pycache__"))
        vid = src / "episode.mp4" if (src / "episode.mp4").exists() else (src / "twin" / "video.mp4")
        if vid.exists():
            _keyframes(vid, out / "keyframes.png")
        for p in out.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
        ledger(run_id, kind="real", name=name, src=src.name, version=version, mode=r.get("mode"), backend=r.get("backend"),
               success=(r.get("verdict") or {}).get("success"), robot_s=r.get("robot_time_s"))
        pending.append(dict(name=name, mode=r.get("mode"), backend=r.get("backend"), version=version,
                            outcome=_verdict_text(r)))
        print(f"{name}: {version}; {_verdict_text(r)}")
    state(run_id, real_pending=pending)
    print(f"{len(dirs)} real run(s) in {real}; the next session's prompt announces {len(pending)}")


def prompt_note(pending):
    """The session prompt's paragraph on real runs since the last session."""
    if not pending:
        return ""
    live = [p for p in pending if p["mode"] == "live" and p["backend"] == "robot"]
    ok = sum(1 for p in live if p["outcome"].startswith("SUCCESS"))
    lines = "; ".join(f"{p['name']}: {p['mode']}{'' if p['backend'] == 'robot' else ' (twin rehearsal)'}, {p['version']}, "
                      f"{p['outcome'][:90]}" for p in pending)
    return (f" NEW REAL-ROBOT RUNS since your last session ({len(pending)}, {len(live)} live on the robot, {ok} of them "
            f"successes), in the workspace's real/ (read real/README.md, then each run's summary.md): {lines}. Use them "
            f"to make the skill work on the real robot as well as in simulation; a change must still pass the simulated "
            f"development evaluation to be kept.")
