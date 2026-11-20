"""real2sim (docs/real2sim.md): an agent session that starts WITHOUT a simulator. The agent is sent to the "real world"
(here the photorealistic twin, condition A: the scanned table and objects, the D415 depth model fitted to real
frames, the cameras' calibration error), which it reaches only through the robot: its cameras, its arm, the
segmenter. From what it sees and does there it reconstructs the scene as its own simulator (the workspace's sim/:
docs/selfsim.md), until it judges it good enough; `sim ready` saves that reconstruction in the session's folder
(workspace sessions/sNN/sim, runs/learning/ID/sessions/sNN/reconstruction) and switches the session to practice:
from then on it practises its skills in ITS simulator only. Its skills are evaluated in the real world.

The practice command's real2sim subcommands (bin/ar-practice; the agent's only command):
  status                                  phase, budgets (real-world episodes, practice episodes)
  real look --task T [--idx I] [--wrist "x,y,z[,yaw];..."]     both cameras at home (+ the wrist camera from hand poses)
  real run explore/NAME.py --task T [--idx I] [--n K]          your exploration program (run(ctx)) in the real world
  py analysis/NAME.py [args]              your analysis script in the sandbox (reads the workspace; writes sim/, analysis/)
  sim check [--n 3]                       build your simulator, sample and settle layouts, previews
  sim render --layout FILE.yaml --like real_world/NNN/EE/observations/MMM_<cam>.json   your simulator vs a real frame
  sim ready                               save the reconstruction in this session's folder; practice from now on
  run --task T [--n 6] [--seed S]         practice episodes in YOUR simulator (after sim ready)
  test                                    your tests (pytest), in the sandbox
Real-world episodes give the robot's observations only: no verdict, no true state."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from .. import paths

FAMILIES = ("place-box", "close-box", "clutter-pick-any")
WS_DIRS = ("explore", "analysis", "sim", "real_world", "sessions")
REAL_RECORD = dict(video=True, video_view="cameras", video_colour="condition", video_fps=2.0, observations="all",
                   keyframes=True, verdict=False)
CODE = ("skills", "helpers", "tests")


def fail(msg):
    print(f"ar-practice: {msg}", file=sys.stderr)
    sys.exit(2)


def ledger_add(run_dir, **rec):
    from .practice import ledger_lock
    with ledger_lock(run_dir):
        with open(run_dir / "ledger.jsonl", "a") as f:
            f.write(json.dumps(dict(rec, t=time.time()), default=str) + "\n")


def records(run_dir, kind=None, session=None):
    f = run_dir / "ledger.jsonl"
    if not f.exists():
        return []
    R = [json.loads(x) for x in open(f)]
    return [r for r in R if (kind is None or r.get("kind") == kind) and (session is None or r.get("session") == session)]


def real_used(run_dir, session=None):
    return sum(int(r.get("episodes", 0)) for r in records(run_dir, "real", session))


def is_ready(run_dir, session):
    return bool(records(run_dir, "sim_ready", session))


def code_snapshot(ws, dst, extra=()):
    """The program's code only (skills/, helpers/, tests/ and `extra` dirs), read-only."""
    from ..selfsim.suite import rmtree_ro
    dst = Path(dst)
    rmtree_ro(dst)
    dst.mkdir(parents=True)
    for d in tuple(CODE) + tuple(extra):
        if (ws / d).exists():
            shutil.copytree(ws / d, dst / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for p in dst.rglob("*"):
        p.chmod(0o555 if p.is_dir() else 0o444)
    return dst


def next_dir(root):
    root.mkdir(parents=True, exist_ok=True)
    n = len([d for d in root.iterdir() if d.is_dir() and d.name.isdigit()]) + 1
    d = root / f"{n:03d}"; d.mkdir()
    return d, n


# ---------------------------------------------------------------------------------------------------- status
def cmd_status(ws, meta, run_dir, budget):
    s = meta.get("session"); R = budget["real2sim"]
    from .practice import usage
    u, U = usage(run_dir, s), usage(run_dir)
    P = budget["practice"]
    print(json.dumps(dict(
        session=s, phase="practice (in your simulator)" if is_ready(run_dir, s) else "reconstruction (the real world is open)",
        real_world=dict(this_session=real_used(run_dir, s), session_limit=R["episodes_per_session"],
                        run_total=real_used(run_dir), run_limit=R["episodes_total"], robot_time_limit_s=R["robot_time_s"],
                        tasks={f: dict(idx_range=v) for f, v in R["families"].items()}),
        practice_in_your_sim=dict(this_session=u["episodes"], session_limit=P["episodes_per_session"],
                                  run_total=U["episodes"], run_limit=P["episodes_total"], max_per_call=P["max_per_call"]),
        reconstructions_saved=sorted(p.name for p in (ws / "sessions").iterdir() if (p / "sim").exists())
        if (ws / "sessions").exists() else []), indent=1))


# ---------------------------------------------------------------------------------------------------- the real world
def export_real(priv, dst, r, task):
    """A real-world episode, as the robot recorded it: observations (+ a depth picture each), the program's own
    output and log, the robot cameras' video and keyframes (no verdict). Not the judge's record."""
    import cv2
    from ..selfsim.check import depth_vis
    dst.mkdir(parents=True, exist_ok=True)
    if (priv / "observations").exists():
        shutil.copytree(priv / "observations", dst / "observations")
        for f in sorted((dst / "observations").glob("*_depth.png")):
            z = cv2.imread(str(f), cv2.IMREAD_UNCHANGED).astype(np.float32) / 1000.0
            z = np.where(z > 0, z, np.inf)
            cv2.imwrite(str(f).replace("_depth.png", "_depth_vis.png"), cv2.cvtColor(depth_vis(z), cv2.COLOR_RGB2BGR))
    if (priv / "program").exists():
        shutil.copytree(priv / "program", dst / "program", ignore=shutil.ignore_patterns("program.json"))
        pj = priv / "program" / "program.json"
        if pj.exists():
            P = json.loads(pj.read_text())
            json.dump(dict(result=P.get("result"), error=P.get("error"), events=P.get("events")),
                      open(dst / "program" / "program.json", "w"), indent=1, default=str)
    for f in ("events.jsonl", "keyframes.png", "video.mp4"):
        if (priv / f).exists():
            shutil.copy2(priv / f, dst / f)
    n_obs = len(list((dst / "observations").glob("*_rgb.png"))) if (dst / "observations").exists() else 0
    info = dict(task=task, robot_time_s=r.get("robot_time_s"), time_limit_hit=r.get("time_limit_hit"),
                program_result=r.get("program_result"), program_error=r.get("program_error"), frames=n_obs,
                platform_error=r.get("category") == "platform_error")
    json.dump(info, open(dst / "episode.json", "w"), indent=1, default=str)
    return info


def real_episodes(ws, run_dir, budget, snap, entry, fam, idxs, label):
    from ..config import episode_base, task_config
    from ..evaluation.suites import episode_seed
    from ..execution.episode import run_episode
    from concurrent.futures import ThreadPoolExecutor
    R, s = budget["real2sim"], None
    t = task_config(R["task"])
    root = ws / "real_world"
    out, n = next_dir(root)
    priv_root = run_dir / "real_world" / out.name
    rng = np.random.default_rng()
    rows = []

    def one(k_i):
        k, i = k_i
        seed = int(rng.integers(100, 1_000_000))
        ep = dict(episode_base(t, record=REAL_RECORD, library=False), family=fam, split="train", level=0, idx=int(i),
                  seed=episode_seed(seed, 0, i), robot_time_limit_s=float(R["robot_time_s"]))
        pd = priv_root / f"{k:02d}"
        try:
            r = run_episode(snap, ep, pd, entry=entry, sandbox=True, wall_limit_s=R.get("wall_s", 900))
        except Exception as e:
            r = dict(category="platform_error", error=f"{type(e).__name__}: {e}"[:300])
        task = (json.loads((pd / "result.json").read_text()).get("task") if (pd / "result.json").exists() else None)
        return k, i, r, export_real(pd, out / f"{k:02d}", r, task)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(len(idxs), int(R.get("workers", 4)))) as ex:
        for row in ex.map(one, enumerate(idxs)):
            rows.append(row)
    voids = sum(1 for *_, info in rows if info["platform_error"])
    L = [f"real-world run {n:03d} ({label}): task {fam}, {len(rows)} episodes, {time.time() - t0:.0f} s wall", "",
         "| # | layout | told | frames | robot s | program result | error |", "|---|---|---|---|---|---|---|"]
    for k, i, r, info in rows:
        pr = info["program_result"]
        L.append(f"| {k:02d} | {i} | {(info['task'] or {}).get('instruction')} | {info['frames']} | {info['robot_time_s']} | "
                 f"{pr.get('result') if isinstance(pr, dict) else pr} | {(info['program_error'] or '')[:80]} |")
    L += ["", "Per episode: observations/ (MMM_<cam>_rgb.png, _depth.png = uint16 mm with 0 = no depth, _depth_vis.png, "
          ".json = K, R, eye (the pose the program believes), q, width, t), program/, events.jsonl, keyframes.png and "
          "video.mp4 (the two robot cameras, 2 frames per second). No verdict: the real world does not tell you."]
    if voids:
        L += ["", f"{voids} episode(s) failed on the platform's side (not your program; refunded)."]
    (out / "summary.md").write_text("\n".join(L) + "\n")
    return out, n, len(rows) - voids, voids, L


def cmd_real(a, ws, meta, run_dir, budget):
    s = meta.get("session"); R = budget["real2sim"]
    if is_ready(run_dir, s):
        fail("this session is in its practice phase (you ran `sim ready`): the real world is closed until the next session")
    fam = a.task
    if fam not in R["families"]:
        fail(f"--task: one of {', '.join(R['families'])}")
    lo, hi = R["families"][fam]
    n = 1 if a.action == "look" else int(a.n)
    if a.idx is not None:
        idxs = [int(x) for x in str(a.idx).split(",")]
        if a.action == "run" and len(idxs) == 1:
            idxs = idxs * n
    else:
        idxs = [int(x) for x in np.random.default_rng().integers(lo, hi, n)]
    if any(not lo <= i < hi for i in idxs):
        fail(f"layout indices for {fam}: {lo} to {hi - 1}")
    if not 1 <= len(idxs) <= int(R.get("max_per_call", 4)):
        fail(f"1 to {R.get('max_per_call', 4)} real-world episodes per call")
    from .practice import ledger_lock
    with ledger_lock(run_dir):
        u, U = real_used(run_dir, s), real_used(run_dir)
        if u + len(idxs) > R["episodes_per_session"] or U + len(idxs) > R["episodes_total"]:
            fail(f"budget: {u}/{R['episodes_per_session']} real-world episodes used this session, {U}/{R['episodes_total']} "
                 f"in the run; asked for {len(idxs)}")
        with open(run_dir / "ledger.jsonl", "a") as f:
            f.write(json.dumps(dict(kind="real", session=s, t=time.time(), episodes=len(idxs), status="started", task=fam,
                                    action=a.action)) + "\n")
    tmp = run_dir / "real_snapshots" / f"{s}_{int(time.time() * 1000)}"
    if a.action == "look":
        wrist = []
        for p in (a.wrist or "").split(";"):
            if p.strip():
                v = [float(x) for x in p.split(",")]
                if len(v) not in (3, 4):
                    fail("--wrist: x,y,z[,yaw] (the TCP, metres; yaw rad) separated by ';'")
                wrist.append(v + [0.0] * (4 - len(v)))
        if len(wrist) > 8:
            fail("--wrist: at most 8 hand poses per look")
        tmp.mkdir(parents=True)
        shutil.copy2(Path(__file__).resolve().parents[1] / "selfsim" / "programs" / "look.py", tmp / "look.py")
        (tmp / "look_args.json").write_text(json.dumps(dict(wrist=wrist)))
        snap, entry, label = tmp, "look.py", "look" + (f", {len(wrist)} wrist poses" if wrist else "")
    else:
        prog = Path(a.program)
        if prog.is_absolute() or not (ws / prog).resolve().is_relative_to((ws / "explore").resolve()) or prog.suffix != ".py":
            fail("real run: a program under explore/ (explore/NAME.py, with run(ctx))")
        if not (ws / prog).exists():
            fail(f"{prog}: no such file")
        snap, entry, label = code_snapshot(ws, tmp, extra=("explore",)), str(prog), f"program {prog}"
    out, n, ok_eps, voids, L = real_episodes(ws, run_dir, budget, snap, entry, fam, idxs, label)
    ledger_add(run_dir, kind="real", session=s, episodes=-voids, status="done", call=n, task=fam, action=a.action,
               platform_errors=voids, dir=str(out.relative_to(ws)))
    print("\n".join(L))
    print(f"\n-> {out.relative_to(ws)}/ ; real-world budget: {real_used(run_dir, s)}/{R['episodes_per_session']} this session")


# ---------------------------------------------------------------------------------------------------- analysis scripts
def cmd_py(a, ws, meta, run_dir, budget):
    from ..execution import sandbox as sbx
    from ..execution.episode import base_env
    from ..selfsim import hidden_paths
    f = Path(a.script)
    if f.is_absolute() or f.suffix != ".py" or not (ws / f).exists():
        fail("py: a .py file in this workspace (e.g. analysis/fit_table.py)")
    for d in ("sim", "analysis"):
        (ws / d).mkdir(exist_ok=True)
    tmp = run_dir / "py_tmp" / f"{int(time.time() * 1000)}"
    tmp.mkdir(parents=True)
    py = str(paths.ROOT / ".venv" / "bin" / "python")
    cmd = sbx.program_cmd([py, "-I", str(ws / f)] + list(a.args or []), ws, tmp, tmp, hide=hidden_paths(),
                          rw=[ws / "sim", ws / "analysis"])
    i = cmd.index("--chdir"); cmd[i + 1] = str(ws)
    t0 = time.time(); lim = int(budget["real2sim"].get("py_wall_s", 600))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=lim, env=base_env())
        out, err, code = r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        o = e.stdout or ""
        out, err, code = (o.decode(errors="replace") if isinstance(o, bytes) else o), f"killed after {lim} s", 124
    shutil.rmtree(tmp, ignore_errors=True)
    print(out[-12000:])
    if err.strip():
        print(err[-4000:], file=sys.stderr)
    print(f"[exit {code}, {time.time() - t0:.1f} s]")
    sys.exit(code)


# ---------------------------------------------------------------------------------------------------- your simulator
def privileged(module, prep, job, out, timeout=900):
    """A check / render of the agent's simulator in a privileged process (GPU rendering; no agent code runs)."""
    from ..execution.episode import base_env, wait_for_gpu
    out.mkdir(parents=True, exist_ok=True)
    (out / ".prep.json").write_text(json.dumps(prep)); (out / ".job.json").write_text(json.dumps(job))
    wait_for_gpu()
    r = subprocess.run([str(paths.ROOT / ".venv/bin/python"), "-I", "-m", module, "--prep", str(out / ".prep.json"),
                        "--job", str(out / ".job.json"), "--out", str(out)], env=base_env(), cwd=str(paths.ROOT),
                       capture_output=True, text=True, timeout=timeout)
    for f in (".prep.json", ".job.json"):
        (out / f).unlink(missing_ok=True)
    return r


def sim_check(ws, run_dir, out, n=3, fams=FAMILIES, seed0=None):
    """prepare + sample + settle + previews -> (report, prep)."""
    from ..selfsim.sample import check_layout, sample_layouts
    from ..selfsim.suite import prepare_sim
    from ..selfsim.model import SimError
    work = run_dir / "sim_work" / f"{int(time.time() * 1000)}"
    prep = prepare_sim(ws / "sim", work)
    if prep.get("error"):
        return dict(ok=False, why=prep["error"]), prep
    seed0 = int(np.random.default_rng().integers(1, 10**6)) if seed0 is None else seed0
    lays = {}
    for fam in fams:
        if fam in ("place-box", "close-box") and not prep["decl"].get("box"):
            lays[fam] = [dict(error=f"{fam} needs the box (sim.yaml box)")]
            continue
        raw = sample_layouts(prep["dir"], fam, [seed0 + k for k in range(n)], work / f"sample_{fam}")
        L = []
        for x in raw:
            try:
                L.append(check_layout(x, prep["decl"], fam))
            except SimError as e:
                L.append(dict(error=str(e)))
        lays[fam] = L
    r = privileged("agentic_robotics.selfsim.check", prep, dict(kind="check", layouts=lays), out)
    rep = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else \
        dict(ok=False, why="the check crashed (platform): " + (r.stderr or "")[-800:], platform_error=True)
    rep["model"] = prep.get("info")
    return rep, prep


def cmd_sim(a, ws, meta, run_dir, budget):
    s = meta.get("session")
    if a.action == "check":
        out, n = next_dir(ws / "practice" / "checks")
        rep, prep = sim_check(ws, run_dir, out, n=max(1, min(int(a.n or 3), 6)))
        json.dump(rep, open(out / "report.json", "w"), indent=1, default=str)
        print(json.dumps(rep, indent=1, default=str)[:12000])
        print(f"\n-> {out.relative_to(ws)}/ (previews: <task>_<k>.png: your scene camera and wrist camera at home, "
              f"colour and depth)")
        return
    if a.action == "render":
        if not a.layout or not a.like:
            fail("sim render --layout FILE.yaml --like real_world/NNN/EE/observations/MMM_<cam>.json")
        import yaml
        from ..selfsim.sample import check_layout
        from ..selfsim.suite import prepare_sim
        from ..selfsim.model import SimError
        lf, rf = (ws / a.layout).resolve(), (ws / a.like).resolve()
        if not lf.is_relative_to(ws) or not lf.exists():
            fail(f"{a.layout}: no such file in the workspace")
        if not rf.is_relative_to((ws / "real_world").resolve()) or rf.suffix != ".json" or not rf.exists():
            fail("--like: a real frame's .json under real_world/")
        work = run_dir / "sim_work" / f"{int(time.time() * 1000)}"
        prep = prepare_sim(ws / "sim", work)
        if prep.get("error"):
            fail(f"your simulator does not build: {prep['error']}")
        try:
            L = yaml.safe_load(lf.read_text()) or {}
            L.setdefault("target", next(iter(L.get("place") or {"?": 0})))
            fam = "close-box" if (prep["decl"].get("box") and L["target"] in (prep["decl"]["box"]["name"], prep["decl"]["box"]["body"])) \
                else "clutter-pick-any"
            L = check_layout(L, prep["decl"], fam)
        except (SimError, yaml.YAMLError) as e:
            fail(f"{a.layout}: {e}")
        out, n = next_dir(ws / "practice" / "renders")
        privileged("agentic_robotics.selfsim.check", prep, dict(kind="render", layout=L, real=str(rf)), out)
        st = json.loads((out / "stats.json").read_text()) if (out / "stats.json").exists() else dict(ok=False, why="render crashed")
        print(json.dumps(st, indent=1))
        print(f"\n-> {out.relative_to(ws)}/compare.png (top: real | yours | overlay; bottom: real depth | your depth | "
              f"the difference)")
        return
    if a.action == "ready":
        out, n = next_dir(ws / "practice" / "checks")
        rep, prep = sim_check(ws, run_dir, out, n=1)
        json.dump(rep, open(out / "report.json", "w"), indent=1, default=str)
        if prep.get("error") or not any(r.get("ok") for rows in rep.get("layouts", {}).values() for r in rows):
            print(json.dumps(rep, indent=1, default=str)[:6000])
            fail("not ready: your simulator must build and settle at least one layout (see the report above)")
        bad = [f for f, rows in rep.get("layouts", {}).items() if not all(r.get("ok") for r in rows)]
        dst = ws / "sessions" / s / "sim"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(ws / "sim", dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        arch = run_dir / "sessions" / s / "reconstruction"
        if arch.exists():
            shutil.rmtree(arch)
        shutil.copytree(ws / "sim", arch, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (run_dir / "sessions" / s / "reconstruction.json").write_text(json.dumps(dict(hash=prep.get("hash"), info=prep.get("info"),
                                                                                        check=rep, t=time.time()), indent=1, default=str))
        first = not is_ready(run_dir, s)
        ledger_add(run_dir, kind="sim_ready", session=s, hash=prep.get("hash"), tasks_failing_check=bad)
        print(json.dumps(dict(saved=str(dst.relative_to(ws)), hash=prep.get("hash"), model=prep.get("info"),
                              tasks_with_failed_check=bad), indent=1, default=str))
        print(("\nYour reconstruction is saved in " + str(dst.relative_to(ws)) + ". " +
               ("This session is now in its practice phase: the real world is closed until the next session; practise "
                "with `run --task T`." if first else "(saved again; still the practice phase)")))
        return
    fail("sim check | sim render | sim ready")


def cmd_run(a, ws, meta, run_dir, budget):
    """Practice episodes in the agent's own simulator."""
    from ..selfsim.suite import prepare_sim, run_items
    from .practice import ledger_lock, usage
    s = meta.get("session"); P = budget["practice"]
    if not is_ready(run_dir, s):
        fail("practice runs in your simulator: build it first and declare it ready (`sim ready`)")
    fam = a.task or FAMILIES[0]
    if fam not in FAMILIES:
        fail(f"--task: one of {', '.join(FAMILIES)}")
    if not 1 <= int(a.n) <= P["max_per_call"]:
        fail(f"1 to {P['max_per_call']} episodes per call")
    seed = a.seed if a.seed is not None else int(np.random.default_rng().integers(*P["seed_range"]))
    with ledger_lock(run_dir):
        u, U = usage(run_dir, s), usage(run_dir)
        if u["episodes"] + a.n > P["episodes_per_session"] or U["episodes"] + a.n > P["episodes_total"]:
            fail(f"budget: {u['episodes']}/{P['episodes_per_session']} practice episodes used this session, "
                 f"{U['episodes']}/{P['episodes_total']} in the run; asked for {a.n}")
        with open(run_dir / "ledger.jsonl", "a") as f:
            f.write(json.dumps(dict(kind="practice", session=s, t=time.time(), episodes=int(a.n), robot_s=0.0, wall_s=0.0,
                                    status="started")) + "\n")
    out, n = next_dir(ws / "practice" / "runs")
    work = run_dir / "practice_sims" / f"{s}_{n:03d}"
    prep = prepare_sim(ws / "sim", work)
    snap = code_snapshot(ws, run_dir / "practice_snapshots" / f"{s}_{n:03d}")
    from ..evaluation.suites import episode_seed
    items = [(fam, k, episode_seed(seed, 0, k)) for k in range(int(a.n))]
    obs = "all" if a.observations in ("wrist", "all") else "none"
    t0 = time.time()
    if prep.get("error"):
        from ..selfsim.suite import sim_error_result
        rows = [(it, sim_error_result(f"your simulator does not build: {prep['error']}", *it)) for it in items]
    else:
        rows = run_items(snap, prep, items, out, workers=budget.get("practice_workers", 8),
                         record=dict(video=True, observations=obs, keyframes=True), name=lambda it: f"{it[1]:02d}")
    wall = time.time() - t0
    n_void = sum(r.get("category") == "platform_error" for _, r in rows)
    robot = sum(float(r.get("robot_time_s") or 0) for _, r in rows)
    ledger_add(run_dir, kind="practice", session=s, episodes=-n_void, robot_s=round(robot, 2), wall_s=round(wall, 1),
               status="done", call=n, seed=seed, platform_errors=n_void, task=fam, sim_hash=prep.get("hash"),
               successes=sum(bool(r.get("success")) for _, r in rows))
    L = [f"practice run {n:03d} in YOUR simulator (sim {prep.get('hash')}): task {fam}, seed {seed}, {len(rows)} episodes, "
         f"{wall:.0f} s wall", "", "| # | told | result | robot s to success | program result | error |", "|---|---|---|---|---|---|"]
    for (f_, k, sd), r in rows:
        pr = r.get("program_result")
        L.append(f"| {k:02d} | {(r.get('task') or {}).get('instruction')} | {r.get('category')} | {r.get('t_success')} | "
                 f"{pr.get('result') if isinstance(pr, dict) else pr} | {(r.get('program_error') or r.get('sim_error') or '')[:90]} |")
    ok = sum(bool(r.get("success")) for _, r in rows)
    L += ["", f"{ok}/{len(rows)} successes (judged in your simulator, by the real world's success checks on your declared "
          "names). Per episode: result.json (verdict, task_state, layout), keyframes.png, video.mp4, events.jsonl, program/"]
    from ..execution.guard import violations
    v = violations(snap)
    if v:
        L += ["", "WARNING: this code reaches for the robot's own code; a version like this is rejected:"] + [f"- {x}" for x in v[:10]]
    (out / "summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    if n_void:
        print(f"\n{n_void} episode(s) voided by a platform error (not your program; refunded)")
    print(f"\nbudget: {usage(run_dir, s)['episodes']}/{P['episodes_per_session']} practice episodes used this session")


def dispatch(a):
    from .practice import context
    ws, meta, run_dir, budget = context()
    if not budget.get("real2sim"):
        fail(f"`{a.cmd}` is not part of this run")
    if a.cmd == "status":
        return cmd_status(ws, meta, run_dir, budget)
    if a.cmd == "real":
        return cmd_real(a, ws, meta, run_dir, budget)
    if a.cmd == "sim":
        return cmd_sim(a, ws, meta, run_dir, budget)
    if a.cmd == "py":
        return cmd_py(a, ws, meta, run_dir, budget)
    if a.cmd == "run":
        return cmd_run(a, ws, meta, run_dir, budget)
    fail(f"{a.cmd}: not part of this run")


# ---------------------------------------------------------------------------------------------------- the learning loop
R2S_EDITS = {                                               # doc -> [(the twin's text, real2sim's)]
    "robot.md": [("A skill program drives the robot through `ctx.robot`, the vendored **RealSim**\n(`vendor/panda_twin/code/real/real_sim.py`, unchanged). It is the same class the real robot program uses.",
                  "A skill program drives the robot through `ctx.robot`, **RealSim**, the same class the real robot program\nuses."),
                 ("enforced twice, by RealSim before sending and by the server's `TwinArm` before executing:",
                  "enforced twice, by RealSim before sending and by the arm's server before executing:"),
                 ("- **World frame:** the twin's frame, metres and radians.", "- **World frame:** the rig's frame, metres and radians."),
                 ("## Calls (all in robot time; the twin's clock is its simulation time)", "## Calls (all in robot time)"),
                 (" In the twin this is the contact force on the hand; on the real arm it is the Franka estimate.",
                  " It is the arm's own estimate of the force on the hand."),
                 ("| force stop | 150 N in the twin (`force_abort_twin`); 35 N on the real server | chosen |",
                  "| force stop | 35 N | chosen |"),
                 ("- **Speed:** the twin runs RealSim at `speed = 1.0`. `rig.json`'s real-robot `speed` is 0.5, so real-robot times for\n  the slow phases would be longer.\n",
                  "")],
}
PERCEPTION_R2S = """**Depth: the real D415s'.** In the real world, depth has the real sensor's faults:
- stereo noise, growing with distance;
- holes on dark surfaces;
- edge fattening and stereo shadows;
- flying pixels at edges;
- a blind band along the image's left edge;
- nothing nearer than 0.275 m;
- a small depth scale error.

The cameras' calibration is not exact either: the true poses differ from the ones `observe_frame` returns (about 5 mm /
1° for the scene camera, 3 mm / 1° for the wrist camera), differently in each real-world episode. `ctx.tools.world(obs)`
gives per-pixel 3D points in the world frame from the frame's depth and the believed pose, plus a validity mask.

**Colour:** the real world's is photorealistic (a rendering of the scanned real scene). In your own simulator, the colour
is MuJoCo's rendering of your materials and textures, and the depth comes from your sensor model (docs/selfsim.md).

"""


def r2s_doc(name, txt):
    from .loop import strip_vlm, scratch_doc
    if name == "perception.md":
        a, b = txt.index("**Depth: realistic and noisy, in every condition.**"), txt.index("## Segmentation")
        txt = txt[:a] + PERCEPTION_R2S + txt[b:]
    for old, new in R2S_EDITS.get(name, []):
        if old not in txt:
            raise ValueError(f"r2s_doc: {name} no longer has {old[:60]!r} (docs changed: update R2S_EDITS)")
        txt = txt.replace(old, new)
    txt = scratch_doc(name, strip_vlm(txt))
    for a_, b_ in (("In the twin", "In the real world"), ("in the twin", "in the real world"), ("the twin's", "the real world's"),
                   ("The twin", "The real world")):
        txt = txt.replace(a_, b_)
    return txt


def init(run_id, X):
    """The workspace for a real2sim run (loop.init's job for these runs): no simulator, no dev result yet."""
    from . import loop
    from ..evaluation.runner import snapshot
    L, R = X["learning"], X["real2sim"]
    ws, rd = paths.WORKSPACES / run_id, loop.run_dir(run_id)
    if ws.exists() or rd.exists():
        raise SystemExit(f"{run_id}: exists already")
    src = paths.ROOT / X["skill"]
    rd.mkdir(parents=True); ws.mkdir(parents=True)
    for d in CODE:
        if (src / d).exists():
            shutil.copytree(src / d, ws / d, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            (ws / d).mkdir()
    for d in WS_DIRS + ("practice",):
        (ws / d).mkdir()
    (ws / "explore" / "README.md").write_text("Your exploration programs for the real world (run(ctx), as the skills): "
                                              "`ar-practice real run explore/NAME.py --task T`.\n")
    (ws / "analysis" / "README.md").write_text("Your analysis scripts: `ar-practice py analysis/NAME.py` (sandbox: reads the "
                                               "workspace, writes sim/ and analysis/).\n")
    (ws / "sim" / "README.md").write_text("Your simulator goes here: sim.yaml, scene.xml, layouts.py, assets/ (docs/selfsim.md).\n")
    (ws / "notes.md").write_text("# Notes\n\n(What you observed in the real world, how you reconstructed it, what you "
                                 "practised, what you changed and why.)\n")
    (ws / "docs").mkdir()
    for f, src_f in (("skill_api.md", "skill_api_scratch.md"), ("robot.md", "robot.md"), ("perception.md", "perception.md"),
                     ("tasks.md", L.get("tasks_doc", "task_real2sim.md")), ("selfsim.md", "selfsim.md")):
        txt = (paths.ROOT / "docs" / src_f).read_text()
        (ws / "docs" / f).write_text(r2s_doc(f, txt) if f in ("skill_api.md", "robot.md", "perception.md") else txt)
    P = X["practice"]
    budget = dict(task=X["task"], entry=X.get("entry", "skills/pick.py"), practice_workers=L.get("practice_workers", 8),
                  tasks=X.get("tasks"), library=False,
                  practice=dict(split="selfsim", levels=[0], idx_range=[0, 0], seed_range=P["seed_range"], families=None,
                                episodes_per_session=L["practice_episodes_per_session"],
                                episodes_total=L["practice_episodes_total"], max_per_call=L.get("practice_max_per_call", 12)),
                  agent=dict(model=L.get("model"), effort=L.get("effort"), usd_per_session=L["agent_usd_per_session"],
                             usd_total=L["agent_usd_total"], wall_s_per_session=L["agent_wall_s_per_session"],
                             resume=bool(L.get("resume_session", False))),
                  selection=L["selection"], dev_suite=L["dev_suite"], prompt_note=L.get("prompt_note"),
                  progress=L.get("progress"), vlm=None, scenes=None, eval_access=None,
                  real2sim=dict(task=R["task"], families={f: list(v) for f, v in R["families"].items()},
                                episodes_per_session=R["episodes_per_session"], episodes_total=R["episodes_total"],
                                robot_time_s=R.get("robot_time_s", 120), max_per_call=R.get("max_per_call", 4),
                                workers=R.get("workers", 4), py_wall_s=R.get("py_wall_s", 600)))
    (rd / "budget.json").write_text(json.dumps(budget, indent=1))
    (rd / "experiment.json").write_text(json.dumps(X, indent=1, default=str))
    (ws / ".ar_run.json").write_text(json.dumps(dict(run_id=run_id, session=None)))
    (ws / "AGENTS.md").write_text(brief(budget, X))
    (ws / "CLAUDE.md").write_text("See AGENTS.md (the brief for this workspace).\n")
    snapshot(ws, rd / "checkpoints" / "000_initial")
    loop.ledger(run_id, kind="init", experiment=X["_path"], skill=X["skill"], add_eval_set=False, real2sim=True)
    loop.state(run_id, incumbent="000_initial", iteration=0, incumbent_dev=None)
    print(f"workspace {ws}\nrun {rd}\n(real2sim: no simulator yet, so no development result for the initial skill)")


def dev_eval(run_id, sid, cand, st, X):
    """Development in the agent's simulator at the end of the session (its sim/ then): the incumbent AND the candidate,
    the same episodes -> (candidate summary, incumbent summary, incumbent dir, candidate dir)."""
    from . import loop
    from ..selfsim.suite import prepare_sim, run_selfsim_suite
    rd, ws = loop.run_dir(run_id), paths.WORKSPACES / run_id
    L = X["learning"]
    spec = X["suites"][L["dev_suite"]]
    prep = prepare_sim(ws / "sim", rd / "dev_sims" / sid)
    out = {}
    for name, d in (("incumbent", rd / "checkpoints" / st["incumbent"]), ("candidate", cand)):
        o = rd / "dev" / (sid if name == "candidate" else f"{sid}_incumbent_{st['incumbent']}")
        t0 = time.time()
        S = run_selfsim_suite(d, prep, spec, o, workers=L.get("dev_workers", 12))
        loop.ledger(run_id, kind="dev", name=o.name, episodes=S["n"], successes=S["successes"], robot_s=S["robot_time_total_s"],
                    wall_s=round(time.time() - t0, 1), model_cost_usd=0.0, sim_hash=prep.get("hash"), sim_error=prep.get("error"))
        out[name] = (o, dict(successes=S["successes"], n=S["n"], t_success_mean=S["t_success_mean"], by_level=S["by_level"],
                             by_family=S.get("by_family"), categories=S["categories"], platform_errors=S["platform_errors"],
                             model_calls=0, sim_hash=prep.get("hash"), sim_error=prep.get("error")))
    return out["candidate"][1], out["incumbent"][1], out["incumbent"][0], out["candidate"][0]


def archive_sim(run_id, sid):
    """The workspace's sim/ at the end of the session -> runs/learning/ID/sessions/sNN/reconstruction_final/."""
    from . import loop
    ws, d = paths.WORKSPACES / run_id, loop.run_dir(run_id) / "sessions" / sid / "reconstruction_final"
    if (ws / "sim").exists():
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(ws / "sim", d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def brief(B, X):
    P, R = B["practice"], B["real2sim"]
    pr = str(paths.ROOT / "bin" / "ar-practice")
    L = X.get("learning") or {}
    fams = ", ".join(f"{f} (layouts {a}-{b - 1})" for f, (a, b) in R["families"].items())
    return f"""# Your task in this workspace

You write robot skill programs and teach them to yourself in **a simulator you build yourself**. The robot is a Franka
Panda arm with a two-finger gripper over a table, seen by two RealSense D415 cameras (one fixed across the table, one on
the hand). **You start without any simulator of its world.** You are sent to the **real world**, which you can only
observe and touch through the robot: its two cameras (colour and depth), its arm and gripper, its own force estimate,
and the segmentation model. In this run the real world is a photorealistic stand-in for the real lab (a rendering of
the scanned real scene, with the real cameras' depth faults and calibration error); treat it as the real robot.

**Based on what you see there, reconstruct the scene as your own simulator** (`sim/`, docs/selfsim.md): the table, the
objects (their shapes, sizes, colours, masses), the box and its lid, the lights and what the cameras see, how the
depth sensor behaves, and how the tasks' scenes vary (`sim/layouts.py`). The robot itself is given: your simulator
runs the same robot model, motion interface, limits and timing, and the same success checks as the real world (on the
names you declare). **Reconstruct until you judge your simulator good enough** to learn the skills in, then run
`{pr} sim ready`: it saves your reconstruction in this session's folder (`sessions/<session>/sim/`) and switches the
session to practice: from then on the real world is closed for the session and **you practise your skills in your
simulator**.

{L.get('task_text', '').strip()}

**How your skills are evaluated.** After the session your skills are run **in the real world**, on layouts you have
never seen. Whether a version is kept is decided in YOUR simulator: at the end of the session your current version and
the previous one are both run on the same layouts sampled by your `sim/layouts.py` (12 per task), and yours is kept if
it solves more (+2, losing at most one on any task). So an honest simulator is in your interest: the real world is what
counts.

**Goal, in order:** (1) more successes in the real world under the time limits; (2) less robot time per success. Robot
speed, limits and the success checks are fixed.

**Perception:** SAM3 (a real segmentation model: free-text prompts, scored masks; it can miss or confuse things;
docs/perception.md). Depth and calibration as on the real robot (docs/perception.md).

**What you can do.** Read and edit anything in this workspace: `skills/`, `helpers/`, `tests/`, `explore/`,
`analysis/`, `sim/`, `notes.md`. You may look at every image in it (Read them). The documented interfaces are in
`docs/` (skill_api.md, robot.md, perception.md, tasks.md, selfsim.md). The only shell commands you can run are these:

    {pr} status
    {pr} real look --task place-box [--idx 17] [--wrist "-0.30,0.05,0.45;-0.25,-0.05,0.40,1.57"]
        # a real-world scene of that task (layout idx, or a random one): both cameras at home, then the wrist
        # camera from each hand pose (the hand pointing down, its TCP at x,y,z m, the fingers along yaw rad)
    {pr} real run explore/NAME.py --task close-box [--idx 17] [--n 2]
        # your own exploration program (run(ctx), the skills' API) in the real world: move, look, touch, push, lift...
    {pr} py analysis/NAME.py [args]       # your analysis script (numpy, scipy, cv2, PIL, mujoco, yaml), in the sandbox
    {pr} sim check [--n 3]                # build your simulator; sample, place and settle layouts; previews
    {pr} sim render --layout sim/views/X.yaml --like real_world/NNN/EE/observations/MMM_d415.json
        # your simulator with that layout, the arm and camera as in that real frame: real | yours, colour and depth
    {pr} sim ready                        # save your reconstruction (sessions/<session>/sim/); practice from now on
    {pr} run --task place-box --n 6 [--seed S]   # practice episodes in YOUR simulator (1-{P['max_per_call']} per call)
    {pr} test                             # your tests in tests/ (pytest), in the sandbox, no robot

Real-world tasks and their layouts: {fams}. Each real-world episode writes `real_world/NNN/EE/`: the frames the
program got (`observations/`: colour PNG, depth as 16-bit PNG in mm (0 = no depth) and a coloured depth picture, and per
frame its intrinsics, the camera pose the program believed, the arm's joints and the gripper opening), your program's
own output (`program/`, e.g. files it saved to `ctx.out`) and log (`events.jsonl`), and the two robot cameras' video
and keyframes. **The real world gives no verdict**: nothing tells you whether a task succeeded there, and nothing
gives you the true positions or sizes of things: you measure them. Practice runs in your simulator write
`practice/runs/NNN/` (summary.md; per episode result.json with the verdict, keyframes.png, video.mp4, events.jsonl,
program/). A real-world call takes a while (photorealistic rendering): give these shell commands a timeout of
600000 ms (10 min).

**Budget.** Real world: {R['episodes_per_session']} episodes per session ({R['episodes_total']} in the run), at most
{R['robot_time_s']:.0f} s of robot time each, 1-{R['max_per_call']} per call; `look` counts as one. Practice in your
simulator: {P['episodes_per_session']} episodes per session ({P['episodes_total']} in the run). `status` shows what is
left. Episodes that crash count too.

**Rules.** Programs see the world only through `ctx` (cameras, the robot's own state, the segmenter): never try to
read the simulators' true state or files outside this workspace. Your simulator must come from what you observe in the
real world, not from guesses about how it was made. Do not try to change the success checks, the time limits or the
robot's limits. Keep each `run(ctx)` working: a crash is a failure. Write the skills yourself: a version that imports
the robot's own code, or reaches for it another way (`sys.modules`, `importlib`, `__globals__`, ...), is rejected.

**Before you finish:** leave your best version in `skills/` and `helpers/`, your simulator in `sim/`, and write in
`notes.md` what you observed, how you reconstructed it, what you practised, what you changed and why.
"""


def session_prompt(it, st, B, Lg, resumed=False):
    from .loop import practice_left
    s_ = f"s{it:02d}"
    R = B["real2sim"]
    pn = (B.get("prompt_note") or "").strip()
    eps = practice_left(B, Lg)
    real_left = max(0, min(R["episodes_per_session"], R["episodes_total"] - sum(int(r.get("episodes", 0)) for r in Lg if r.get("kind") == "real")))
    last, d = st.get("last"), st.get("incumbent_dev") or {}
    prev = ""
    if last:
        dec = last["decision"]
        prev = (f" Last session ({last['session']}): your version was {'KEPT' if dec['keep'] else 'NOT kept'} ({dec['why']}; "
                f"judged in your simulator as it was at the end of that session). "
                + ("The workspace's code was reset to the current version; your notes, sim/ and records are still here. "
                   if not dec["keep"] else ""))
        if d.get("n"):
            fam = ", ".join(f"{k}: {v['successes']}/{v['n']}" for k, v in (d.get("by_family") or {}).items())
            prev += f"The current version solves {d['successes']}/{d['n']} development episodes in your simulator ({fam})."
    budget = (f"You have {real_left} real-world episodes and {eps} practice episodes (in your simulator) in this session.")
    if resumed:
        return (f"Session {it} (you are continuing your previous session; your context from it is above). The real world is "
                f"open again until you run `sim ready` (which saves this session's reconstruction in sessions/{s_}/sim/): "
                f"you may refine your simulator from new observations first, or declare it ready as it is, then practise "
                f"in it.{prev} {pn} {budget} End with your best version in place, your simulator in sim/ and notes.md "
                f"updated.").replace("  ", " ")
    return (f"Session {it}. Read AGENTS.md, then docs/selfsim.md, docs/robot.md, docs/perception.md, docs/skill_api.md "
            f"and docs/tasks.md. You start without a simulator: based on what the robot's cameras show you in the real "
            f"world (`real look`, and your own exploration programs with `real run`), reconstruct the scene in sim/ as your "
            f"own simulator, which you will use to teach yourself the skills. Compare it with the real frames (`sim "
            f"render`) and refine it until you judge it good enough; then run `sim ready` (it saves your reconstruction in "
            f"sessions/{s_}/sim/) and write and improve the three skills by practising them in your simulator. The skills "
            f"are empty now.{prev} {pn} {budget} End with your best version in place, your simulator in sim/ and notes.md "
            f"updated.").replace("  ", " ")
