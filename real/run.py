"""Real-robot evaluation of skill programs (real/README.md). The operator's command line:

  real/run check [--backend robot|twin] [--object "red tape roll"]
        no motion: the arm server's answer and the arm's state, a frame from each camera, SAM3 on the scene frame for
        the object (masks drawn): results/check_<time>/
  real/run episode --skills DIR --object "red tape roll" [--task pick|place] [--mode dry|live] [--tag NAME]
        [--library on|off] [--time-limit S] [--max-trajectories N] [--speed S] [--sam3 ours|robot]
        [--vlm EXPERIMENT | --no-vlm] [--vlm-usd X]
        [--backend twin --split val --level 1 --idx 0 --seed 13 --task-config configs/tasks/clutter_pick_A_rig.yaml]
        ONE episode: the skill program in its sandbox, real/server.py for the robot (or the twin). dry (the default)
        sends no motion; live sends it (robot: only with the operator at the E-stop, typed confirmation, the arm server
        started with --allow-motion and, by default, confirming every trajectory). After a live robot episode the
        operator gives the verdict. Writes results/<time>_<backend>_<mode>_<tag>/ (README.md: what is in it).
        Astra is ON by default (the skill's one call per episode): the vlm block of the skill's learning run
        (runs/learning/<run>/experiment.json), else configs/experiments/fidelity2_A.yaml's; --vlm-usd caps ALL real
        runs' model spending together (ledger tag 'real', default $2); --no-vlm turns it off.
        --task place: "Put the <object> into the white box." (skills/place.py), else "Pick up the <object> and lift it."
  real/run program --program runs/bimanual/<session>/program.py [--task insert_varied] [--mode dry|live] [--tag NAME]
        [--time-limit S] [--robot-clock] [--no-settle] [--max-trajectories N] [--speed S] [--guard-speed M_PER_S] [--guard-dz M]
        [--free-push] [--raw-force] [--backend sim --seed N [--scene FILE.json]]
        ONE episode of a program written for the bimanual environments' robot API (run(robot, task): docs/bimanual.md),
        unchanged, on the rig's LEFT arm (src/agentic_robotics/bimanual/real.py: what differs from the simulation).
        --task insert_varied (the default): "put the cylinder on the stick", one arm's work: the cylinder and the stand
        on the left arm's side. --backend sim: the rehearsal, the bimanual simulation's scene `seed` of the task (or
        --scene) standing in for the robot and judged by the task's judge. The clutter tasks (--task clutter --object
        ... --level ...) need both arms: for now not on the real robot (--backend twin only: the path's own test).
        --task clutter_one_arm --object "red cylinder" [--level medium|hard] --ask: a one-armed clutter session's program
        (bimanual/clutter_one_arm.py): the left arm takes the named object out of a heap; --backend sim rehearses it.
        No model call unless --ask (robot.ask: at most 4 calls an episode, ledger tag 'real:program').
  real/run verdict RESULTS_DIR success|fail [--knocked-off] [--intervened] [--note ...]
        the operator's verdict on a live robot episode, after it (or to correct it)
  real/run summary [RESULTS_DIR ...]
        a table of the episodes (mode, skill, verdict, robot time, trajectories, model calls): results/summary.md

Run with the project's environment (real/run does: .venv, -I); the robot backend's server runs with the robot client's
Python (configs/local.yaml real.python: pyrealsense2)."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "real"
RESULTS = REAL / "results"
sys.path.insert(0, str(ROOT / "src"))

from agentic_robotics import paths  # noqa: E402
from agentic_robotics.execution import sandbox as sbx  # noqa: E402
from agentic_robotics.execution.episode import base_env  # noqa: E402
from agentic_robotics.execution.versions import tree_hash, versions  # noqa: E402

VENV_PY = str(ROOT / ".venv" / "bin" / "python")
CHECKLIST = """LIVE RUN ON THE REAL ROBOT. Before you type LIVE:
  - you are at the robot with the E-stop in your hand;
  - the arm server runs with --allow-motion (and WITHOUT --no-confirm: it asks for every trajectory in its terminal);
  - the arm is at the twin's home pose (panda_twin: py code/real/real_box.py home), the right arm parked;
  - the scene is set: the target and the clutter where you want them, nothing else in the arm's way;
  - both D415s plugged in; SAM3 up (real/run check)."""


def robot_python():
    try:
        p = (paths.local().get("real") or {}).get("python")
    except FileNotFoundError:
        p = None
    if not p:
        raise SystemExit("configs/local.yaml real.python is not set (the robot client's Python, with pyrealsense2)")
    return str(Path(p).expanduser())


def git_rev():
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    d = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "real"], capture_output=True, text=True)
    return r.stdout.strip() + ("+dirty" if d.stdout.strip() else "")


def services_ok(sam3):
    from agentic_robotics import services
    if sam3 == "robot":
        import urllib.request
        ok = True
        for port in (8131, 8134):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=5).read()
            except Exception:
                ok = False
        return ok
    return services.alive("sam3_text") and services.alive("sam3_points")


# ---------------------------------------------------------------------------------------------------- one episode
def arm_hello(port=None, timeout_s=5.0):
    """The arm server's hello (read-only), or None if it does not answer."""
    import zmq
    from panda_fk import load_rig  # noqa: F401  (vendored: on the path after agentic_robotics.sim.twin)
    rig = load_rig()
    c = zmq.Context(); s = c.socket(zmq.REQ)
    s.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000)); s.setsockopt(zmq.LINGER, 0)
    s.connect(f"tcp://{rig['server']['host']}:{port or rig['server']['port']}")
    try:
        s.send(json.dumps(dict(cmd="hello")).encode())
        return json.loads(s.recv())
    except zmq.Again:
        return None
    finally:
        s.close(); c.term()


def vlm_block(a, skills):
    """The skill's one model call per episode (on by default): the vlm block of the learning run the skill comes from,
    else of --vlm EXPERIMENT (default fidelity2_A). None with --no-vlm or a block without a model."""
    if a.no_vlm:
        return None
    V = None
    if a.vlm:
        V = paths.load_yaml(a.vlm).get("vlm")
    else:
        for d in [skills] + list(skills.parents):              # (runs/learning/<run>/checkpoints/sNN or candidates/sNN)
            if (d / "experiment.json").exists() and (d / "budget.json").exists():
                V = json.loads((d / "experiment.json").read_text()).get("vlm")
                break
        if V is None:
            V = paths.load_yaml(ROOT / "configs" / "experiments" / "fidelity2_A.yaml").get("vlm")
    if not (V or {}).get("enabled"):
        return None
    V = dict(V, tag="real", run_usd=a.vlm_usd)
    V["max_calls_per_episode"] = min(int(V.get("max_calls_per_episode", 1)), 1)
    return V


def vlm_spent(tag):
    from agentic_robotics.perception.vlm import spent
    return spent(tag)


def run_episode(a):
    a.entry = a.entry or ("skills/place.py" if a.task == "place" else "skills/pick.py")
    hi = preflight(a)
    skills = Path(a.skills).resolve()
    if not (skills / a.entry).exists():
        raise SystemExit(f"{skills / a.entry}: no such entry")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    tag = a.tag or skills.name
    out = Path(a.out_root or RESULTS) / f"{stamp}_{a.backend}_{a.mode}_{tag}"
    prog_out = out / "program"; prog_out.mkdir(parents=True)
    task = None
    if a.backend == "robot":
        if not a.object:
            raise SystemExit("--object is needed on the robot (the target's name, e.g. 'red tape roll')")
        task = (dict(kind="place", instruction=f"Put the {a.object} into the white box.", object=a.object,
                     destination="white box") if a.task == "place" else
                dict(kind="clutter_pick", instruction=f"Pick up the {a.object} and lift it.", object=a.object))
    elif a.object:
        raise SystemExit("--object: the twin's layout names its own target")
    V = vlm_block(a, skills)
    if V is not None:
        print(f"Astra on: at most one call per episode; all real runs together capped at ${a.vlm_usd} (spent so far "
              f"${vlm_spent('real'):.3f}); --no-vlm turns it off", flush=True)
    spec = dict(mode=a.mode, backend=a.backend, task=task, robot_time_limit_s=a.time_limit, max_trajectories=a.max_trajectories,
                sam3=a.sam3, vlm=V, record=not a.no_record, depth_fps=a.depth_fps, port=a.port, replay_frames=a.replay_frames,
                twin=dict(task_config=a.task_config, split=a.split, level=a.level, idx=a.idx, seed=a.seed) if a.backend == "twin" else None,
                skills=str(skills), skills_hash=tree_hash(skills), entry=a.entry, library=a.library == "on", speed=a.speed,
                platform=git_rev(), started=stamp)
    (out / "spec.json").write_text(json.dumps(spec, indent=1))
    shutil.copytree(skills, out / "skill", ignore=shutil.ignore_patterns("__pycache__", "practice", "*.mp4"))
    host_args = ["--skills", str(skills), "--entry", a.entry] + (["--no-library"] if a.library == "off" else []) + \
        (["--speed", str(a.speed)] if a.speed is not None else [])
    return launch(a, out, prog_out, spec, skills, "host.py", host_args, hi)


def preflight(a):
    """The arm server's answer, the operator's LIVE confirmation, SAM3 -> the arm server's hello ({} on the twin)."""
    hi = {}
    if a.backend == "robot":
        from agentic_robotics.sim import twin  # noqa: F401
        hi = arm_hello(a.port)
        if hi is None:
            raise SystemExit("the arm server does not answer (panda_twin: srv, or srv --allow-motion for a live run)")
        print(f"arm server: {'FAKE arm' if hi.get('fake') else 'the REAL arm'}, motion {'allowed' if hi.get('allow_motion') else 'not allowed'}"
              f"{', each trajectory confirmed there' if hi.get('confirm') else ''}", flush=True)
    if a.mode == "live" and a.backend == "robot" and not hi.get("fake"):
        if not sys.stdin.isatty():
            raise SystemExit("a live robot run needs the operator at this terminal")
        print(CHECKLIST + getattr(a, "checklist_more", ""))
        if input("Type LIVE to send motion to the real arm: ").strip() != "LIVE":
            raise SystemExit("not confirmed: nothing was run")
    if not services_ok(a.sam3):
        raise SystemExit(f"SAM3 ({a.sam3}) is not answering: bin/ar services start sam3_text sam3_points")
    return hi


PROGRAM_MORE = {"insert_varied": """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION, WHERE THE ROBOT HAS TWO ARMS; only the LEFT arm moves here:
      the cylinder (standing, its hole up) and the stand (stick up) both on the left arm's side of the table
      (table frame: x -380..-180 mm, y -150..+130 mm), at least one of them at y > -60 mm (else the program picks
      the right arm and the episode ends), their centres at least 150 mm apart, nothing else near them; the right
      arm parked. A dry run first: its output gives where the program sees them and the arm it picks;
  - it lowers the hand until contact (5 mm/s), closes on the cylinder, carries it over the stick and searches for the
      hole with small moves until contact: the stand may be pushed; be ready on the E-stop;
  - about 3 minutes when the first try goes in; up to 11 minutes when it has to search and grasp again.""",
                "clutter": "",
                "clutter_one_arm": """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION (the one-armed robot's heaps, bimanual/clutter_one_arm.py); only the LEFT
      arm moves:
      the heap within the arm's reach (table frame: about x -360..-220 mm, y -120..+30 mm is where it practised), the
      target named by --object among it; free table around the heap: the program puts the objects it clears away at
      about (-400, 350), (-270, 410), (-480, 380), (-200, 300), (-400, -170), (-260, -250) mm: nothing may stand there;
  - it lowers the open gripper onto an object until contact (5 mm/s), closes, lifts, and may push or drop objects: be
      ready on the E-stop; light things only in the heap;
  - it asks the model (robot.ask, --ask: at most 4 calls an episode) which object is the target and what it holds;
  - several minutes: every pick is a slow descent."""}
HELD = {"clutter_one_arm": "does the robot hold the named object up, at least 150 mm above the table, touching nothing else, and has "
                           "nothing fallen off the table (the task's success)?",
        "insert_varied": "is the cylinder on the stick (the stick through its hole), let go by the robot, the stand upright on "
                         "the table (the task's success)?"}


def run_program(a):
    from agentic_robotics.bimanual import clutter
    from agentic_robotics.bimanual.practice import ASK, check_program
    from agentic_robotics.bimanual.tasks import TASKS
    program = Path(a.program).resolve()
    if not program.is_file():
        raise SystemExit(f"{program}: no such program")
    bad = check_program(program.read_text())
    if bad:
        raise SystemExit("the program is refused: " + "; ".join(bad))
    if a.task == "clutter":
        if a.backend != "twin":
            raise SystemExit("the clutter tasks need both arms: for now they are not run on the real robot (the rig moves one arm). "
                             "--backend twin runs the path's own test on the single-arm twin's pile")
        if a.level not in clutter._HOW:
            raise SystemExit(f"--level is one of {', '.join(clutter._HOW)}")
        limit = 900.0 if a.level == "hard" else 600.0                         # (the simulation's limits)
    elif a.task == "clutter_one_arm":                                         # (bimanual/clutter_one_arm.py: the left arm alone)
        from agentic_robotics.bimanual import clutter_one_arm
        if a.backend == "twin":
            raise SystemExit("--task clutter_one_arm: on the robot, or --backend sim (the task's simulated heaps: a rehearsal)")
        if a.level not in clutter_one_arm.LEVELS:
            raise SystemExit(f"--level is one of {', '.join(clutter_one_arm.LEVELS)}")
        if a.backend == "robot" and not (a.object or "").strip():
            raise SystemExit('--object is needed on the robot: the target as the task names it, colour and shape ("red cylinder")')
        limit = float(TASKS[clutter_one_arm.task(a.level)].time_limit_s)
    elif a.backend == "twin":
        raise SystemExit(f"--task {a.task}: the twin has no cylinder and no stand; its rehearsal is --backend sim")
    else:
        limit = float(TASKS[a.task].time_limit_s)
    # the program's limit is the simulation's, on the program's clock (bimanual/real.py, time); the rig's own is longer,
    a.time_limit = a.time_limit or (limit if a.robot_clock else 2.5 * limit)   # as its stop_on_contact moves are slower
    a.max_trajectories = a.max_trajectories or (40 if a.mode == "dry" else 400)
    a.checklist_more = PROGRAM_MORE[a.task]
    a.held_question = HELD.get(a.task)
    hi = preflight(a)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    tag = a.tag or program.parent.name
    out = Path(a.out_root or RESULTS) / f"{stamp}_{a.backend}_{a.mode}_{tag}"
    prog_out = out / "program"; prog_out.mkdir(parents=True)
    task = sim = None
    one_arm = a.task == "clutter_one_arm"
    if a.backend == "robot" and one_arm:                                      # (the task's text names the target: clutter.text)
        name = re.sub(r"^the\s+", "", a.object.strip())
        task = dict(kind=a.task, instruction=clutter._text(a.level, name, "left"), object=name, level=a.level)
    elif a.backend == "robot":
        task = dict(kind=a.task, instruction=TASKS[a.task].text)
    elif a.backend == "sim":                                                  # (the scene of the seed names its own target)
        sim = dict(task=f"pick_clutter_one_arm_{a.level}" if one_arm else a.task, seed=a.seed, sensors=a.sensors,
                   scene=json.loads(Path(a.scene).read_text()) if a.scene else None)
    A = None
    if a.ask:
        from agentic_robotics.perception.vlm import models_config
        M = models_config()
        if a.model not in M["models"]:
            raise SystemExit(f"--model is one of {', '.join(M['models'])}")
        total, cap = vlm_spent(None), float(M["budget"]["total_usd"])
        A = dict(enabled=True, model=a.model, tag="real:program", run_usd=a.ask_usd, usd_per_episode=ASK["calls"] * ASK["reserve_usd"],
                 **{k: (list(v) if isinstance(v, tuple) else v) for k, v in ASK.items() if k != "reserve_usd"})
        print(f"robot.ask on ({a.model}): at most {ASK['calls']} calls an episode; all real runs together capped at ${a.ask_usd} "
              f"(spent so far ${vlm_spent('real'):.3f})", flush=True)
        if total >= cap:
            print(f"   NOTE: the repository's model budget is spent (${total:.2f} of {cap:.2f}, configs/models.yaml budget.total_usd): "
                  "every robot.ask will be refused until the cap is raised", flush=True)
    spec = dict(mode=a.mode, backend=a.backend, task=task, robot_time_limit_s=a.time_limit, max_trajectories=a.max_trajectories,
                sam3=a.sam3, vlm=None, ask=A, record=not a.no_record, depth_fps=a.depth_fps, port=a.port, replay_frames=a.replay_frames,
                twin=dict(task_config=a.task_config, split=a.split, level=a.twin_level, idx=a.idx, seed=a.seed) if a.backend == "twin" else None,
                sim=sim, skills=str(program), kind="program", program_time_limit_s=limit, robot_clock=a.robot_clock, program_task=a.task, level=a.level if a.task == "clutter" else None,
                speed=a.speed, guard_speed=a.guard_speed, guard_dz=a.guard_dz, free_push=a.free_push, raw_force=a.raw_force,
                confirm_contact=a.confirm_contact,
                max_stops=a.max_stops, settle_all=not a.no_settle, platform=git_rev(), started=stamp)
    skill = out / "skill"; skill.mkdir()
    shutil.copy2(program, skill / "program.py")                               # (the sandbox sees this copy only)
    spec["skills_hash"] = tree_hash(skill)
    (out / "spec.json").write_text(json.dumps(spec, indent=1))
    host_args = ["--program", str(skill / "program.py"), "--guard-speed", str(a.guard_speed), "--max-stops", str(a.max_stops),
                 "--program-limit", str(limit)] + (["--robot-clock"] if a.robot_clock else []) + (["--no-settle"] if a.no_settle else []) + \
        (["--clutter-level", a.level] if a.task == "clutter" else []) + (["--speed", str(a.speed)] if a.speed is not None else []) + \
        (["--guard-dz", str(a.guard_dz)] if a.guard_dz is not None else []) + (["--free-push"] if a.free_push else []) + \
        (["--confirm-contact"] if a.confirm_contact else []) + \
        (["--raw-force"] if a.raw_force else [])
    launch(a, out, prog_out, spec, skill, "host_program.py", host_args, hi)
    txt = prog_out / "output.txt"                                             # the program's own prints (not the commands' log)
    own = [l for l in txt.read_text().splitlines() if l.strip() and not l.startswith("#")] if txt.exists() else []
    if own:
        print(f"the program's own output ({txt}; {len(own)} lines, the first 8):")
        for l in own[:8]:
            print("  " + l[:200])
    return out


def launch(a, out, prog_out, spec, skills, host, host_args, hi):
    """The episode: real/server.py for the robot (or the twin), the host (a file of real/) with the program in its
    sandbox (skills: the directory bound read-only into it), then the record and the verdict -> out."""
    sock_dir = Path(tempfile.mkdtemp(prefix="arr-"))
    sock = sock_dir / "real.sock"
    py = robot_python() if a.backend == "robot" else VENV_PY
    env = base_env()
    t0 = time.time()
    srv = subprocess.Popen([py, "-I", str(REAL / "server.py"), "--spec", str(out / "spec.json"), "--sock", str(sock), "--out", str(out)],
                           env=env, cwd=str(ROOT), stdout=open(out / "server.log", "w"), stderr=subprocess.STDOUT)
    proc = None
    interrupted = False
    try:
        while not Path(str(sock) + ".ready").exists():
            if srv.poll() is not None:
                raise SystemExit(f"the real-robot server exited ({srv.returncode}):\n{(out / 'server.log').read_text()[-2000:]}")
            if time.time() - t0 > 180:
                raise SystemExit("the real-robot server did not start in 180 s")
            time.sleep(0.1)
        cmd = [VENV_PY, "-I", str(REAL / host), "--sock", str(sock), "--out", str(prog_out)] + list(host_args)
        cmd = sbx.program_cmd(cmd, skills, prog_out, sock_dir)
        cmd[1:1] = ["--ro-bind", str(REAL / host), str(REAL / host)]             # (only this file of real/: no results)
        print(f"{a.mode} run on the {a.backend}: {out}", flush=True)
        proc = subprocess.Popen(cmd, cwd=str(prog_out), stdout=open(prog_out / "host.log", "w"), stderr=subprocess.STDOUT)
        try:
            proc.wait(timeout=a.wall_limit)
        except subprocess.TimeoutExpired:
            print("wall limit: the program is stopped", flush=True)
            proc.kill(); proc.wait()
        except KeyboardInterrupt:
            interrupted = True
            print("\nCtrl-C: the program is stopped (no more trajectories are sent; the arm holds where it is)", flush=True)
            proc.kill(); proc.wait()
        try:
            srv.wait(timeout=60)
        except subprocess.TimeoutExpired:
            if not (out / "server.json").exists():
                from agentic_robotics.execution.episode import _send_done
                _send_done(sock, "interrupted by the operator" if interrupted else f"program ended without reporting (exit {proc.returncode})")
            try:
                srv.wait(timeout=180)
            except subprocess.TimeoutExpired:
                srv.kill(); srv.wait()
    finally:
        for p in (proc, srv):
            if p is not None and p.poll() is None:
                p.kill()
        shutil.rmtree(sock_dir, ignore_errors=True)
    S = json.loads((out / "server.json").read_text()) if (out / "server.json").exists() else \
        dict(error="no server record", server_log=(out / "server.log").read_text()[-2000:])
    res = dict(S, spec=spec, versions=versions(skills), interrupted=interrupted, wall_total_s=round(time.time() - t0, 1))
    res["verdict"] = verdict(a, S, interrupted, hi)
    (out / "result.json").write_text(json.dumps(res, indent=1, default=str))
    write_readme(out, res)
    print(one_line(out, res))
    return out


def verdict(a, S, interrupted, hi):
    if a.backend in ("twin", "sim"):
        j = S.get("judge") or {}
        if a.mode == "dry" and a.backend == "sim":
            return dict(by=f"none (dry run: no motion; the sim's judge on the untouched scene: {j.get('category')})", success=None)
        return dict(by=f"{a.backend} judge", success=bool(j.get("success")), category=j.get("category"), t_success=j.get("t_success"))
    if a.mode == "dry":
        return dict(by="none (dry run: no motion)", success=None)
    if hi.get("fake"):
        return dict(by="none (the arm server's fake arm: no physical outcome)", success=None)
    if a.verdict:
        return dict(by="operator (--verdict)", success=a.verdict == "success", note=a.note)
    if not sys.stdin.isatty():
        return dict(by="pending (no terminal: real/run verdict <dir> ...)", success=None)

    def yn(q):
        while True:
            s = input(f"   {q} [y/n] ").strip().lower()
            if s in ("y", "yes", "n", "no"):
                return s.startswith("y")
    print("Verdict (the operator):")
    held = yn(getattr(a, "held_question", None) or "is the target held up by both fingers, nothing else lifted (the task's success)?")
    knocked = yn("did anything fall off the table or get knocked over?")
    stopped = yn("did you intervene (E-stop, refused a trajectory, by hand)?")
    note = input("   a note (Enter for none): ").strip()
    return dict(by="operator", success=bool(held and not knocked and not stopped and not interrupted), held=held,
                knocked_off=knocked, intervened=stopped or interrupted, note=note)


def one_line(out, r):
    v = r.get("verdict") or {}
    tr = r.get("trajectories") or {}
    return (f"{out.name}: {v.get('category') or ('SUCCESS' if v.get('success') else ('-' if v.get('success') is None else 'fail'))}"
            f" | robot {r.get('robot_time_s')} s | trajectories planned {tr.get('planned')} sent {tr.get('sent')} refused {tr.get('refused')}"
            f" | program: {str(r.get('program_result') or r.get('program_error'))[:120]}")


def write_readme(out, r):
    files = [("spec.json", "what was run: the skill (path, hash, a copy in skill/), mode, backend, limits, platform rev"),
             ("result.json", "the outcome: verdict, robot time, trajectories, the program's result and usage, versions"),
             ("actions.jsonl", "every trajectory the program planned (dry) or sent (live): joints, times, phase, the reply"),
             ("events.jsonl", "the program's events, SAM3 calls (phrases, scores), model requests/replies"),
             ("frames/", "every camera frame the program got: colour, depth (16-bit mm), joints"),
             ("plan/", "each planned move drawn over the scene camera (NNN.jpg; sheet.jpg: all of them)"),
             ("video/, episode.mp4", "robot: both cameras recorded at 15 fps, and side by side with the phase, gripper, force"),
             ("twin/", "twin backend: the simulator's own record (video.mp4, result.json with the judge)"),
             ("sim/", "sim backend: the bimanual simulation's record (video.mp4, result.json: the judge, the scene)"),
             ("program/", "the program's own output: program.json, host.log, debug images, program_error.txt"),
             ("server.log", "the real-robot server's log")]
    L = [f"# {out.name}", "", one_line(out, r), "", "| file | |", "|---|---|"]
    L += [f"| `{f}` | {d} |" for f, d in files if (out / f.split(",")[0].strip()).exists()]
    (out / "README.md").write_text("\n".join(L) + "\n")


# ---------------------------------------------------------------------------------------------------- check, summary
def check(a):
    """No motion. The arm server's hello and the arm's state, a frame from each camera, SAM3 on the scene frame."""
    if a.object and not services_ok(a.sam3):
        raise SystemExit(f"SAM3 ({a.sam3}) is not answering: bin/ar services start sam3_text sam3_points (or --object '' to skip it)")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = RESULTS / f"check_{stamp}_{a.backend}"; out.mkdir(parents=True)
    code = r'''
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/src"); sys.path.insert(0, sys.argv[1] + "/real")
out, backend, phrase, sam3_which = Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
port, replay = (int(sys.argv[6]) if sys.argv[6] else None), (sys.argv[7] or None)
import numpy as np, cv2
from server import RobotBackend, TwinBackend, RealEpisode
spec = dict(mode="dry", backend=backend, sam3=sam3_which, robot_time_limit_s=1, record=False, port=port, replay_frames=replay,
            task=dict(kind="clutter_pick", instruction=f"Pick up the {phrase} and lift it.", object=phrase) if backend == "robot" else None,
            twin=dict(task_config="configs/tasks/clutter_pick_A_rig.yaml", split="val", level=1, idx=0, seed=13))
ep = RealEpisode(spec, out)
rep = dict(arm=ep.be.hello_info, state={k: v for k, v in ep.be.state().items() if k in ("q", "width", "f_ext", "mode", "errors")})
from agentic_robotics.perception import sam3
for cam in ("d415", "L_wrist"):
    rgb, depth, K = ep.be.frame(cam)
    cv2.imwrite(str(out / f"{cam}_rgb.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    rep[cam] = dict(K=K, depth_valid=round(float(np.isfinite(depth).mean()), 3))
    if phrase:
        res = sam3.segment(rgb, phrase)
        im = rgb.copy()
        for mk, s in res[:3]:
            cnts, _ = cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(im, cnts, -1, (0, 255, 0), 2)
            ys, xs = np.nonzero(mk)
            if len(xs):
                cv2.putText(im, f"{s:.2f}", (int(xs.mean()), int(ys.mean())), 0, 0.6, (255, 255, 0), 2)
        cv2.imwrite(str(out / f"{cam}_sam3.png"), cv2.cvtColor(im, cv2.COLOR_RGB2BGR))
        rep[cam]["sam3"] = [round(s, 3) for _, s in res]
ep.be.finish(dict(result="check"), out)
json.dump(rep, open(out / "check.json", "w"), indent=1, default=str)
print(json.dumps(rep, default=str)[:3000])
import os; sys.stdout.flush(); os._exit(0)
'''
    py = robot_python() if a.backend == "robot" else VENV_PY
    r = subprocess.run([py, "-I", "-c", code, str(ROOT), str(out), a.backend, a.object or "", a.sam3, str(a.port or ""),
                        a.replay_frames or ""], env=base_env(), cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    print(r.stdout[-3000:] or r.stderr[-3000:])
    if r.returncode != 0 or not (out / "check.json").exists():
        print(f"CHECK FAILED (exit {r.returncode}): {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'see above'}")
        print(f"(partial output in {out})")
        sys.exit(1)
    print(f"check written to {out}: {', '.join(sorted(p.name for p in out.glob('*.png')))}")


def set_verdict(a):
    """The operator's verdict on a live robot episode, after the run (or to correct it)."""
    d = Path(a.dir)
    r = json.loads((d / "result.json").read_text())
    r["verdict"] = dict(by="operator (real/run verdict)", success=a.result == "success", knocked_off=a.knocked_off,
                        intervened=a.intervened, note=a.note, at=time.strftime("%Y-%m-%d %H:%M:%S"))
    (d / "result.json").write_text(json.dumps(r, indent=1, default=str))
    write_readme(d, r)
    print(one_line(d, r))


def summary(a):
    dirs = [Path(d) for d in a.dirs] if a.dirs else sorted(p for p in RESULTS.iterdir() if (p / "result.json").exists())
    L = ["| episode | mode | backend | skill | target | verdict | robot s | trajectories planned / sent | model calls |", "|---|---|---|---|---|---|---|---|---|"]
    for d in dirs:
        r = json.loads((d / "result.json").read_text())
        v = r.get("verdict") or {}
        s = r.get("spec") or {}
        tr = r.get("trajectories") or {}
        vv = v.get("category") or ("success" if v.get("success") else ("-" if v.get("success") is None else "fail"))
        L.append(f"| {d.name} | {r.get('mode')} | {r.get('backend')} | {Path(s.get('skills', '')).name} | "
                 f"{(r.get('task') or {}).get('object')} | {vv} | {r.get('robot_time_s')} | {tr.get('planned')} / {tr.get('sent')} | "
                 f"{(r.get('model') or {}).get('model_calls', 0)} |")
    (RESULTS / "summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


def main():
    ap = argparse.ArgumentParser(prog="real/run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check"); s.add_argument("--backend", default="robot", choices=("robot", "twin"))
    s.add_argument("--object", default="red tape roll"); s.add_argument("--sam3", default="ours", choices=("ours", "robot"))
    s.add_argument("--port", type=int, default=None, help="the arm server's port (default rig.json's)")
    s.add_argument("--replay-frames", help="tests: a real run's frames/ dir instead of the cameras")
    s = sub.add_parser("episode")
    s.add_argument("--skills", required=True)
    s.add_argument("--entry", default=None, help="default skills/pick.py (--task place: skills/place.py)")
    s.add_argument("--object", help="robot: the target's name, as the task names it ('red tape roll')")
    s.add_argument("--mode", default="dry", choices=("dry", "live"))
    s.add_argument("--backend", default="robot", choices=("robot", "twin"))
    s.add_argument("--tag"); s.add_argument("--library", default="on", choices=("on", "off"))
    s.add_argument("--time-limit", type=float, default=150.0, help="robot seconds (motion + waits); sim's 60 s is at speed 1")
    s.add_argument("--max-trajectories", type=int, default=40)
    s.add_argument("--speed", type=float, default=None, help="RealSim time scale (default rig.json's: 0.5)")
    s.add_argument("--wall-limit", type=float, default=1800.0)
    s.add_argument("--sam3", default="ours", choices=("ours", "robot"), help="ours: :8151/:8154 (bin/ar services); robot: :8131/:8134")
    s.add_argument("--task", default="pick", choices=("pick", "place"), help="robot: pick = lift it; place = into the white box")
    s.add_argument("--vlm", metavar="EXPERIMENT", help="the vlm block of this experiment (default: the skill's run's, else fidelity2_A)")
    s.add_argument("--no-vlm", action="store_true", help="no model call (Astra is on by default)")
    s.add_argument("--vlm-usd", type=float, default=2.0, help="the cap for ALL real runs' model calls together (ledger tag 'real')")
    s.add_argument("--no-record", action="store_true"); s.add_argument("--depth-fps", type=float, default=0.0)
    s.add_argument("--port", type=int, default=None, help="the arm server's port (default rig.json's)")
    s.add_argument("--replay-frames", help="robot backend, tests: a real run's frames/ dir instead of the cameras")
    s.add_argument("--verdict", choices=("success", "fail"), help="live robot: the operator's verdict without the questions")
    s.add_argument("--note", default="")
    s.add_argument("--out-root", help="where the run's directory goes (default real/results)")
    s.add_argument("--task-config", default="configs/tasks/clutter_pick_A_rig.yaml", help="twin: the condition")
    s.add_argument("--split", default="val"); s.add_argument("--level", type=int, default=1)
    s.add_argument("--idx", type=int, default=0); s.add_argument("--seed", type=int, default=13)
    s = sub.add_parser("program")
    s.add_argument("--program", required=True, help="a bimanual-environment session's program.py (run(robot, task))")
    s.add_argument("--task", default="insert_varied", choices=("insert_varied", "clutter_one_arm", "clutter"),
                   help="insert_varied: put the cylinder on the stick (one arm's work); clutter_one_arm: take --object out of a "
                        "heap with the left arm alone (--level, --ask); clutter: twin backend only")
    s.add_argument("--mode", default="dry", choices=("dry", "live"))
    s.add_argument("--backend", default="robot", choices=("robot", "sim", "twin"),
                   help="sim: the bimanual simulation's scene of the task (a rehearsal); twin: --task clutter's test")
    s.add_argument("--tag")
    s.add_argument("--time-limit", type=float, default=None,
                   help="the rig's robot seconds (default: 2.5 x the task's 600 s; the program's own limit stays the task's, on its clock)")
    s.add_argument("--no-settle", action="store_true",
                   help="the arm server's settle after the unguarded ways down only (default: after every move that is not a contact)")
    s.add_argument("--robot-clock", action="store_true",
                   help="robot.time_s() = the rig's clock (default: the stop_on_contact moves counted at the simulation's 40 mm/s)")
    s.add_argument("--max-trajectories", type=int, default=None, help="default 40 dry, 400 live (one per command of the program)")
    s.add_argument("--speed", type=float, default=None, help="RealSim time scale (default rig.json's: 0.5)")
    s.add_argument("--guard-speed", type=float, default=0.005, help="m/s of the stop_on_contact moves (0.005: validated on the arm; simulation: 0.04)")
    s.add_argument("--guard-dz", type=float, default=None, help="m the hand is held up at a contact (default rig.json's 0.002; the arm server allows 0.001-0.01)")
    s.add_argument("--confirm-contact", action="store_true",
                   help="a move until contact that the arm stops WITHOUT force (under 2 N: joint friction, on this arm) is not "
                        "reported as a contact: it goes on from where it stopped (bimanual/real.py CONFIRM; default off)")
    s.add_argument("--free-push", action="store_true",
                   help="send a push down after a contact as a plain 40 mm/s move (default: a 5 mm/s guarded one: 15 N / 10 mm)")
    s.add_argument("--raw-force", action="store_true", help="state()'s force as the arm reports it (default: minus its reading at the start)")
    s.add_argument("--max-stops", type=int, default=3, help="safety stops of the arm server before the episode ends")
    s.add_argument("--wall-limit", type=float, default=3600.0)
    s.add_argument("--sam3", default="ours", choices=("ours", "robot"), help="ours: :8151/:8154 (bin/ar services); robot: :8131/:8134")
    s.add_argument("--ask", action="store_true", help="robot.ask answers (a model call; off: it answers ok=False)")
    s.add_argument("--model", default="astra", help="robot.ask's model (configs/models.yaml)")
    s.add_argument("--ask-usd", type=float, default=2.0, help="the cap for ALL real runs' model calls together (ledger tag 'real')")
    s.add_argument("--no-record", action="store_true"); s.add_argument("--depth-fps", type=float, default=0.0)
    s.add_argument("--port", type=int, default=None, help="the arm server's port (default rig.json's)")
    s.add_argument("--replay-frames", help="robot backend, tests: a real run's frames/ dir instead of the cameras")
    s.add_argument("--verdict", choices=("success", "fail"), help="live robot: the operator's verdict without the questions")
    s.add_argument("--note", default="")
    s.add_argument("--out-root", help="where the run's directory goes (default real/results)")
    s.add_argument("--seed", type=int, default=2001, help="sim: the task's scene (twin: the layout's seed)")
    s.add_argument("--scene", help="sim: a scene file (bimanual/scenes.py's dict) instead of the seed's")
    s.add_argument("--sensors", default="d415", choices=("d415", "ideal"), help="sim: the cameras' depth and calibration errors, or none")
    s.add_argument("--object", help='clutter_one_arm on the robot: the target, by colour and shape ("red cylinder"); clutter: unused')
    s.add_argument("--level", default="medium", help="clutter, clutter_one_arm: the task's text, medium (the target can be seen) | hard (it lies under others)")
    s.add_argument("--task-config", default="configs/tasks/clutter_pick_A_rig.yaml", help="twin: the condition")
    s.add_argument("--split", default="val"); s.add_argument("--twin-level", type=int, default=1)
    s.add_argument("--idx", type=int, default=0)
    s = sub.add_parser("verdict"); s.add_argument("dir"); s.add_argument("result", choices=("success", "fail"))
    s.add_argument("--knocked-off", action="store_true"); s.add_argument("--intervened", action="store_true")
    s.add_argument("--note", default="")
    s = sub.add_parser("summary"); s.add_argument("dirs", nargs="*")
    a = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    {"check": check, "episode": run_episode, "program": run_program, "verdict": set_verdict, "summary": summary}[a.cmd](a)


if __name__ == "__main__":
    main()
