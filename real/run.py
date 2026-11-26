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
        --task sweep | insert_own | hang_mugs: the program of a from-nothing session (improve.py --own-task; OWN):
          sweep       "pick the sponge and sweep the small objects into the target area"
                      (runs/bimanual/sponge_sweep_own_1124/program.py); the operator counts the small objects: how
                      many there were, how many ended inside the target (the verdict's fraction);
          insert_own  "put the cylinder on the stick" as its own session learned it from nothing
                      (runs/bimanual/insert_own_1125/program.py): NOT insert_varied's program, another text, other
                      objects (a free stand with one stick, the cylinder also on its side), the gripper back home;
          insert_own_cc  the same task as its Claude Code session learned it from nothing
                      (runs/bimanual/insert_own_cc_xhigh_1125/program.py): its own text, a cylinder standing on an end;
          hang_mugs   "hang both mugs by their handles on the rack" (runs/bimanual/hang_mugs_own_1125/program.py);
                      the operator counts the mugs that hang;
          hang_mugs_cc  the same task as its Claude Code session learned it from nothing
                      (runs/bimanual/hang_mugs_own_cc_xhigh_1125/program.py): its own text, a mug TREE (a post with
                      branches), the mugs pinched by the rim.
        The task's text is the one the session's task world gave the program, word for word (--instruction
        TEXT|FILE: another). --backend sim --world FILE [--seed N]: the rehearsal in a session's world (default:
        the task world beside the program, worlds/wNN.py), which gives the scene, the text, the robot the program
        gets and the check; it needs a checkout whose simulation has worlds (the sim-dev line), and the world's code
        runs in the program sandbox only (real/world_tool.py, real/host_program.py).
        No model call unless --ask (robot.ask: at most 4 calls an episode, ledger tag 'real:program').
  real/run verdict RESULTS_DIR success|fail [--inside N --total M | --hung N] [--knocked-off] [--intervened] [--note ...]
        the operator's verdict on a live robot episode, after it (or to correct it); --inside/--total: a sweep
        episode's count of the small objects (inside the target area, all of them); --hung: the mugs that hang (of 2)
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

VENV_PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
#         (a worktree or a copy without an environment of its own runs on the one it was started with: the main checkout's)
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
# --task sweep: what the session's task world (runs/bimanual/sponge_sweep_own_1124/worlds/w23.py, goal()) gave the
# program as its task, word for word. The program was written against this text and nothing else.
SWEEP = ("Pick up the rectangular kitchen sponge and use it to sweep all the scattered small loose pieces (beans, grains of "
         "rice and/or paper scraps) into the single marked rectangular target area. The target is a coloured sticker or paper "
         "sheet, or a rectangle outlined with coloured tape; pieces must be fully inside its boundary. Sweep on the continuous "
         "work surface on which the pieces lie. The sponge may initially rest on a small raised stand; pick it up, do not sweep "
         "the stand. Leave the pieces at rest, release the sponge outside the target, and withdraw the hand. Do not move the "
         "larger distractor objects. Sponge size, colour, orientation and position, debris type and distribution, target "
         "position/orientation/marking, surface height and table appearance vary. The target sheet, if present, lies flat and "
         "is affixed to the surface; unrelated larger objects are distractors.")
# --task insert_own: runs/bimanual/insert_own_1125/worlds/w10.py, goal(), word for word.
INSERT_OWN = ("Put the hollow cylinder onto the single vertical stick of the stand, with the stick passing completely through "
              "its hole, and release it. Keep the stand upright. Then open the gripper fully and return the left arm to its "
              "initial pose. The cylinder can start on one flat end, on a mildly inclined support, or resting on its curved "
              "side; either object may be raised above the table. There can be unrelated solid objects nearby. Objects vary in "
              "material, texture, colour, exterior profile, dimensions and position. The stick can have a round or square "
              "cross-section and can be off-centre on the base. The hole may be only 1.5 mm wider than the stick. The table "
              "height can vary. Use only cameras and arm feedback.")
# --task insert_own_cc: the Claude Code session's (bimanual/improve_cc.py), runs/bimanual/insert_own_cc_xhigh_1125/worlds/w16.py,
# goal(), word for word.
INSERT_WITH_REAL_INFO = ("Put the cylinder on the stick. On the table there is a cylinder with a hole through it along its axis, "   # (w13's goal(), word for word)
                         "standing on one of its flat ends, and a stand with one vertical stick. Pick up the cylinder and thread "
                         "it onto the stick, so that the stick passes through its hole, and let go of it: at the end the cylinder "
                         "must be on the stick, the robot not touching it, and the stand upright. Then open the gripper and "
                         "return the arm to its initial pose. The hole can be as little as 1.5 mm wider than the stick. The "
                         "cylinder and the stand may stand on the table or on raised supports, and other objects may be on the "
                         "table.")
INSERT_OWN_CC = ('Task: put the cylinder on the stick. On the table there are a cylinder with a hole through it (standing on '
                 'one of its flat ends, the hole vertical) and a stand with one vertical stick. Pick up the cylinder and '
                 'thread it onto the stick, so that the stick passes through its hole, and let go of it: at the end the '
                 'cylinder must be on the stick, the robot not touching it, and the stand upright. Then open the gripper and '
                 'return it to its initial pose. The hole can be as little as 1.5 mm wider than the stick. The cylinder and '
                 'the stand may stand on the table or on something on it; other objects may be on the table.')
# --task hang_mugs: runs/bimanual/hang_mugs_own_1125/worlds/w11.py, goal(), word for word.
HANG_MUGS = ("Hang both upright mugs by their handles on the freestanding mug rack, one mug on each branch. The branch must "
             "pass through the handle and bear the mug\u2019s weight after release. Do not dislodge an already hung mug. Leave "
             "both mugs hanging, then return the open gripper to its initial pose. Mugs, rack, clutter, surface height, and "
             "small supports may differ. Use only the cameras and arm measurements.")
# --task hang_mugs_cc: the Claude Code session's (bimanual/improve_cc.py), runs/bimanual/hang_mugs_own_cc_xhigh_1125/worlds/w08.py,
# goal(), word for word.
HANG_MUGS_CC = ("Hang all the mugs on the mug rack. On the table there are two mugs, standing upright, and one mug rack (a mug "
                "tree: a post on a base, with branches sticking out of the post). Pick up each mug and hang it on the rack by its "
                "handle: a branch of the rack must pass through the handle so that the rack bears the mug's weight once the gripper "
                "lets go of it. Each mug must stay hanging after it is released, and hanging one mug must not knock the other off. "
                "Other things on the table are not to be used. At the end all mugs hang on the rack, the gripper is open and the arm "
                "is back at its start pose.")
# --task insert_from_img: the worlds_from_img session's (bimanual/worlds_from_img.py: from nothing, given two real images),
# runs/bimanual/insert_worlds_from_img_1125/worlds/w13.py, goal(), word for word.
INSERT_FROM_IMG = 'Put the cylinder on the stick. On the table there are a cylinder with a hole through it along its axis (standing on one of its flat ends) and a stand: a wooden board with one vertical wooden stick. Pick up the cylinder and thread it onto the stick, so that the stick passes through its hole, and let go of it: at the end the cylinder must be on the stick, the robot not touching it, and the stand upright. Then open the gripper and return the arm to its initial pose. The hole can be as little as 1.5 mm wider than the stick. Other objects may be on the table; the stand and the cylinder may stand on raised supports.'
PROGRAM_MORE["sweep"] = """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION ONLY, in worlds its session wrote itself (no real sponge, no real pieces);
      only the LEFT arm moves. Where it practised (table frame, mm; the left arm's base is at (-790, 40)):
      . ONE continuous flat work surface under everything (in its worlds a board of 490 x 720 mm, 8 to 35 mm thick, on
        the table, its top 0 to 60 mm above the table's zero): the pieces, the target and the sponge all on it;
      . the target: ONE rectangle with sides of 160 to 235 mm, its centre about x -350..-245, y +100..+320, turned by up
        to 35 degrees: a coloured sticker or sheet of paper FIXED FLAT to the surface, or an outline of coloured tape
        (blue, orange, green, magenta or pale yellow in its worlds); it must stand out from the surface;
      . 12 to 25 small dry pieces (dried beans, rice grains, card scraps of 8-18 mm) OUTSIDE the target, within about
        150 mm of its edge, inside x -460..-165, y -15..+480; no deep piles, no thin paper that curls;
      . the sponge: DRY, a plain rectangular block of 65-115 x 32-65 x 28-55 mm (the gripper opens 80 mm and takes it
        across its width), 12 to 60 g, anywhere in x -480..-190, y -105..+480, at least 75 mm from the target's edge and
        95 mm from the pieces; it may lie on a low stand (12 or 25 mm);
      . 0 to 3 distractors (blocks or jars 36-56 mm across, 40-75 mm high), not in the target, 60 mm from the pieces;
  - it looks from several wrist poses, grips the sponge from the side (a descent until contact, 5 mm/s), lowers it onto
      the surface until contact and wipes toward the target in strokes of 18 mm steps, holding about 1 N of downward
      force READ FROM THE ARM'S FORCE ESTIMATE: on this arm that estimate moves by 1 N and more without any contact,
      so expect strokes in the air or pressed too hard; be ready on the E-stop; nothing fragile or heavy on the table;
  - it puts the sponge back near where it took it, opens the gripper and withdraws;
  - 5 to 9 minutes in simulation (its limit is 600 s on its own clock); longer on the rig (slow contact moves, SAM3)."""
PROGRAM_MORE["insert_own"] = """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION ONLY, in worlds its session wrote itself (no real cylinder, no real stand).
      It is NOT the earlier insertion program (--task insert_varied). Only the LEFT arm moves. Where it practised
      (table frame, mm; the left arm's base is at (-790, 40)):
      . the cylinder: 32 to 72 mm across, 20 to 70 mm long, walls of 7 mm or more, a hole 1.5 to 7 mm wider than the
        stick; standing on an end, on a slightly inclined block, or LYING ON ITS SIDE (it then tips it upright first);
      . the stand: FREE on the table (not fixed), ONE vertical stick 5 to 22 mm thick, round or square, 65 to 180 mm
        long and at least the cylinder's length + 25 mm; a base 76 to 140 mm across, 7 to 25 mm thick, 0.25 to 1.4 kg;
      . both inside x -450..-220, y -180..+480, 160 to 430 mm apart; either may stand on a block (25 to 100 mm high,
        140 mm square); 0 to 5 other objects of 36 to 70 mm near them, none touching;
  - it looks from wrist poses, centres the cylinder by closing the gripper on it twice, lifts it, carries it over the
      stick and feels for the hole: small descents until contact (5 mm/s) and sideways steps of 1 to 2 mm, up to 13 mm
      around; the stand may be pushed; then it lowers, lets go, opens the gripper and goes back to its start pose;
  - if it cannot insert it tries to put the cylinder back where it took it; if that is blocked it KEEPS HOLDING it;
  - 2 to 5 minutes in simulation when it goes in, up to 9 with a long search (its limit is 600 s on its own clock)."""
PROGRAM_MORE["insert_own_cc"] = """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION ONLY, by a Claude Code session, in worlds it wrote itself (no real cylinder,
      no real stand). It is neither the earlier insertion program (--task insert_varied) nor the Astra from-nothing one
      (--task insert_own). Only the LEFT arm moves. Where it practised (table frame, mm):
      . the cylinder: STANDING ON AN END (lying on its side is not handled), 22 to 68 mm across, 12 to 55 mm high, a
        hole 1.5 to 5 mm wider than the stick; NOT black (it finds things by depth);
      . the stand: ONE vertical stick 5 to 24 mm thick, 20 to 90 mm longer than the cylinder is high (at most 160 mm),
        round, on a base 60 to 140 mm wide; free or heavy; NOT black;
      . both centred inside x -440..-200, y -170..+230; either may stand on a block of 15 to 120 mm; other objects
        may stand around, 5 mm or more away;
  - it first looks down from high above (-420, 60, 600), then from about 320 mm above each candidate;
  - IT CLOSES THE GRIPPER ON THE STICK, low (25 mm above the base), three times, to find its centre: a light stand
      slides, a heavy one pushes the arm aside; then on the cylinder (6 to 8 mm above what it stands on), twice;
  - it carries the cylinder over the stick, lowers until contact (5 mm/s), searches at most 2.8 mm around, lets go
      once 15 mm onto the stick; then opens, lifts and goes back to its start pose;
  - on any failure it opens the gripper, lifts and goes home;
  - 1.5 to 3 minutes in simulation, at most about 4 (its limit is 600 s on its own clock)."""
PROGRAM_MORE["insert_own_cc_real"] = """
  - THIS PROGRAM IS THE CLAUDE CODE INSERTION SKILL AFTER ITS SESSION WITH FOUR REAL EPISODES (runs/bimanual/
      insert_own_cc_real_1125, v63): specialised to THIS rig's stand (the wooden board with ONE stick of about 9.5 mm,
      170 mm above the board) and its soft fingers. It has not yet succeeded on the real arm. Only the LEFT arm moves.
      . the cylinder: STANDING ON AN END, 24 to 66 mm across, 12 to 50 mm high, a hole 1.5 to 6 mm wider than the
        stick; NOT black; on the table or on a platform of 15 to 100 mm;
      . the stand: on the left arm's side, AWAY FROM THE PARKED RIGHT ARM (a close-up above a stand near it is refused:
        "no stick found"); on the table or on a platform; other objects may stand around;
  - it first looks down from (-350, 60, 620), and from two other poses if it finds no cylinder or no stick;
  - IT NEVER TOUCHES THE STICK: it looks at the stick's top from 380 mm above it;
  - it closes on the cylinder low (fingertips about 6 mm above the table), opens gently, turns 90 degrees, closes again,
      lets go and looks at it once more from above (this measures the camera's error); next to the parked arm it first
      carries the cylinder to a free spot;
  - it grips again, lifts 15 mm and checks the hold, carries the cylinder high over the stick, and lowers until
      contact for about 20 mm only (5 mm/s); if it sits on the top it searches 0.8 to 3.2 mm around; then lets go, lifts
      and goes back to its start pose;
  - if that fails it puts the cylinder back and tries once more;
  - 2 to 2.5 minutes in simulation; its own estimate for the rig is 150 to 200 s, up to 400 s with a search."""
PROGRAM_MORE["insert_from_img"] = """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION ONLY, by a Claude Code session that was given TWO REAL IMAGES of this rig's
      stand and a blue ring (runs/bimanual/insert_worlds_from_img_1125, v23) and nothing else of the real things. It
      has never run on the real arm and was told nothing of the gripper's fingers. Only the LEFT arm moves.
      . the stand: THIS rig's wooden board with ONE stick (in its worlds 11.5 to 13 mm thick, 160 to 180 mm above the
        board); on the table or on a support of 15 to 150 mm;
      . the cylinder: STANDING ON A FLAT END (lying on its side is not handled), up to 66 mm across, 12 to 70 mm high,
        a hole 1.5 to 10 mm wider than the stick; on the table or on a support of 15 to 150 mm;
      . other objects may stand around; ANOTHER RING ON THE TABLE CAN BE TAKEN FOR THE CYLINDER;
  - it uses only the wrist camera's depth: two views from high above, then a close-up from 320 mm above the stick's
      tip (300 or 285 mm if that is out of reach) and from the same distance above each cylinder candidate;
  - IT NEVER TOUCHES THE STICK before the insertion;
  - it looks at the cylinder twice, the hand turned by 90 degrees between (next to the parked arm it first carries
      the cylinder to a free flat spot); then it closes on the cylinder, opens, turns 90 degrees, closes again, lets
      go and looks once more;
  - it grips, carries the cylinder over the stick's tip, lowers until contact (5 mm/s), and on contact searches in
      rings 0.8 mm apart, up to 4.8 mm around; then it lets go and the ring should slide down the stick;
  - if that fails it puts the cylinder back and tries again, 3 attempts in all; then opens and goes to its start pose;
  - 1.5 to 2 minutes in simulation, at most about 4 (its limit is 600 s on its own clock)."""
PROGRAM_MORE["insert_own_cc_real2"] = """
  - THIS PROGRAM IS THE CLAUDE CODE INSERTION SKILL AFTER ITS SECOND SESSION WITH REAL EPISODES (runs/bimanual/
      insert_own_cc_real2_1125, v80; it studied six real runs of v63). Specialised to THIS rig's stand. Only the LEFT
      arm moves. Not yet run on the real arm.
      . the cylinder: STANDING ON AN END, 24 to 66 mm across, 12 to 50 mm high, NOT black; on the table, or on a box, a
        book or a narrow pile of blocks up to about 100 mm high;
      . the stand: on the table or on a platform of up to about 110 mm (the stick's top up to about 265 mm: higher,
        the look from above is out of the arm's reach); anywhere x -470..-60; AWAY FROM THE PARKED RIGHT ARM;
  - it first looks down from (-350, 60, 620), then from further forward if it misses the stick or the cylinder;
  - IT NEVER TOUCHES THE STICK before the insertion; it looks at the stick's top from about 380 mm above it;
  - A CYLINDER ON SOMETHING IS PICKED WITH ONE CLOSE AND CARRIED TO A FREE SPOT ON THE TABLE, put down there and
      centred (close, open, turn 90 degrees, close); a cylinder on the table is centred where it stands. KEEP A FREE
      PATCH OF TABLE with nothing taller than 60 mm around it;
  - a close that comes back empty: it opens, lifts, looks again and tries another closing angle (3 tries);
  - it lets go, looks at the cylinder again (the camera's error), grips with one close, carries it high over the
      stick and lowers until contact for about 20 mm (5 mm/s); on the stick's top it searches 0.8 to 3.2 mm around;
  - if the insertion fails it looks at the stick again (the stand may have been pushed), puts the cylinder back,
      centres and measures again, and tries once more; then lets go, lifts and goes back to its start pose;
  - about 2.5 to 3 minutes in simulation; its own estimate for the rig is 180 to 650 s (long with a search and retry)."""
PROGRAM_MORE["insert_own_cc_fixed"] = """
  - THIS PROGRAM IS THE CLAUDE CODE INSERTION SKILL AFTER A SESSION WITH FOUR REAL EPISODES IN WHICH IT COULD NOT
      REWRITE ITS WORLD (the fixed-world ablation: runs/bimanual/insert_own_cc_fixedworld_1125, v66; it practised only
      in the first session's world of varied stands). Not yet run on the real arm. Only the LEFT arm moves.
      . the cylinder: STANDING ON AN END, 22 to 68 mm across, 12 to 55 mm high, NOT black; on the table or on a
        block of 15 to 120 mm;
      . the stand: ONE vertical stick 5 to 24 mm thick, up to 160 mm long in its world (this rig's is longer); other
        objects may stand around;
  - it first looks down from (-365, 60, 600), and from three other poses if that one is refused; then from close
      above the stick and above the cylinder;
  - IT CLOSES THE GRIPPER ON THE STICK: low (about 25 mm above the board), in two or three directions, and on a tall stick
      AGAIN ABOUT 22 mm BELOW ITS TOP: a light stand slides, a stick that is not rigid leans. WATCH THE STICK;
  - no long move until contact any more: it goes down by an ordinary move to just above what the fingers could
      meet, then by a short move until contact (15 to 30 mm, 5 mm/s), sent again if it ends short;
  - it closes on the cylinder low, opens fully, turns 90 degrees, closes again (a third time if the two widths
      differ); a width much below what the camera saw: it lifts, looks again and retries;
  - it carries the cylinder 60 mm above the stick's top, lowers until contact, searches around if it sits on the
      top, lets go, and LOOKS FROM ABOVE THE STICK whether the cylinder is on it; if not, ONE recovery (it looks for
      the cylinder, grips it and inserts again); then it lifts and goes back to its start pose;
  - 100 to 180 s in simulation, up to about 280 s with a retry (its limit is 600 s on its own clock)."""
# --task sweep_cc: the Claude Code session's (bimanual/improve_cc.py), runs/bimanual/sponge_sweep_own_cc_xhigh_1125/worlds/w21.py.
# Its world writes a text PER SCENE (goal(): the sponge's colour, the small objects' name, the marker and its colour), and
# the program reads the colours and the marker's kind from it. On the robot the text is w21's first wording, word for
# word, with the operator's words for this table (--sponge, --scrub, --sponge-on, --pieces, --target), or --instruction.
SWEEP_CC = ("Pick up {sponge} and use it to sweep the {pieces} on the table into the target area, {target}. "
            "Every one of them should end up inside that area. Leave everything else on the table where it is.")
SWEEP_CC_COLOURS = ("light blue", "dark blue", "light green", "dark green", "light grey", "dark grey", "yellow", "green", "blue",
                    "pink", "orange", "purple", "grey", "gray", "white", "black", "red", "brown", "beige")   # (the program's)


def sweep_cc_text(a):
    """w21's text for this table from the operator's words; None if they are not given."""
    if not (a.sponge or a.pieces or a.target):
        return None
    if not (a.sponge and a.pieces and a.target):
        raise SystemExit("--task sweep_cc on the robot: give --sponge COLOUR --pieces NAME --target 'COLOUR paper' | 'COLOUR tape' "
                         "(and --scrub COLOUR, --sponge-on small|tall if they apply), or --instruction with the whole text")
    def colour(c, what):
        c = " ".join(c.lower().split())
        if c not in SWEEP_CC_COLOURS:
            raise SystemExit(f"{what}: {c!r} is not a colour this program knows; it knows: " + ", ".join(SWEEP_CC_COLOURS))
        return c
    t = a.target.lower().split()
    if len(t) < 2 or t[-1] not in ("paper", "tape"):
        raise SystemExit("--target: 'COLOUR paper' (a sheet of paper) or 'COLOUR tape' (an outline of tape), e.g. 'white paper'")
    tc = colour(" ".join(t[:-1]), "--target")
    sponge = f"the {colour(a.sponge, '--sponge')} sponge" + (f" with the {colour(a.scrub, '--scrub')} scrubbing side" if a.scrub else "")
    if a.sponge_on:
        sponge += f" (it lies on a {a.sponge_on} block)"
    target = f"the {tc} sheet of paper" if t[-1] == "paper" else f"the area outlined with {tc} tape"
    return SWEEP_CC.format(sponge=sponge, pieces=" ".join(a.pieces.lower().split()), target=target)


PROGRAM_MORE["sweep_cc"] = """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION ONLY, by a Claude Code session, in worlds it wrote itself (no real sponge,
      no real pieces; runs/bimanual/sponge_sweep_own_cc_xhigh_1125, v31). It is not the Astra sweeping program
      (--task sweep). Only the LEFT arm moves. Where it practised (table frame, mm):
      . the sponge: a block of 85-130 x 45-64 x 25-45 mm (the gripper takes it ACROSS ITS WIDTH: not wider than about
        65 mm, not thinner than 25 mm), 5 to 60 g, DRY; on the table or on a block 15 to 80 mm high; one colour, or
        with a scrubbing layer of another colour;
      . 8 to 28 small objects of ONE kind (lentils, beans, chickpeas, dried peas, coffee beans, cereal, pasta, beads,
        crumbs), 50 to 160 mm outside the target, in one or a few groups; of a colour unlike the table and the marker;
      . the target: a LOOSE sheet of paper (100 x 100 to 236 x 148 mm) or a rectangle outlined with tape (inner side
        130 to 230 mm, the tape 15 to 25 mm wide), of a colour unlike the table;
      . 0 to 3 other things (mug, box, bottle, fruit, pen), not between the small objects and the target;
      . everything inside x -440..-150, y -180..+400 (it looks and works in x -485..-105, y -235..+455);
  - THE TEXT TELLS IT THE COLOURS: the sponge's (and its scrubbing side's), the marker (paper or tape) and its colour.
      Check the line "task text" below against the table;
  - it finds everything BY COLOUR AND DEPTH, no segmentation model (a black table was one of its tables): dark
      small objects on this black table, shadows and glare can give missed or false objects. It first looks down
      from (-375, 110, 520), then from close above the sponge. A dry run first (--mode dry) shows what it finds;
  - it grips the sponge from above across its width (pads 18 mm below its top), then LOWERS THE SPONGE ONTO THE TABLE
      UNTIL CONTACT at two free places (5 mm/s) to find the sweeping height; the hand comes down to about 12 mm above
      the table: nothing hard under the sponge. A descent that ends early without a contact gives it a sweeping
      height above the table (strokes in the air): --confirm-contact;
  - IT READS THE GRIPPER'S WIDTH: under 12 mm after closing on the sponge it gives up ("could not pick the
      sponge"), under 10 mm after the lift or 8 mm after a stroke it takes the sponge for lost. In its worlds the
      sponge is a rigid block: use a FIRM sponge that the gripper does not crush flat (its fingers are soft too);
  - a hand angle at the end of the wrist's range is refused here (not in its worlds): in 1 of 16 rehearsals every
      stroke was refused and nothing was swept; lines "REFUSED ... wrist" in the output tell;
  - it pushes the small objects toward the target in straight strokes with the sponge's long side, the farthest
      first; over a sheet of paper it lifts the sponge 5.5 mm. A LOOSE SHEET CAN BE DRAGGED (its commonest failure
      in simulation): tape its corners down if it slides;
  - after each round it PUTS THE SPONGE DOWN on a free spot, looks again, takes it again and sweeps what is still
      outside; it stops when nothing is outside or near 560 s on its clock, and puts the sponge down;
  - its arm's body is not checked against low things near the robot's base: keep that side free;
  - 100 to 450 s in simulation (about 200 on average; its limit is 600 s on its own clock); longer on the rig.
      Rehearsed through this path on 16 simulated scenes: 9 solved (11 in the plain simulation), 150 to 520 s."""
PROGRAM_MORE["insert_own_cc_episodes"] = """
  - THIS PROGRAM IS THE CLAUDE CODE INSERTION SKILL AFTER A SESSION WITH THE FOUR REAL EPISODES OF v38 AND NO TEXT ABOUT
      THE RIG (the episodes-only ablation: runs/bimanual/insert_own_cc_episodes_1126, v61). Specialised to THIS rig's
      stand. Not yet run on the real arm. Only the LEFT arm moves.
      . the cylinder: STANDING ON AN END, up to 60 mm across, 15 to 50 mm high, NOT black; on the table or on a
        platform of 10 to 80 mm;
      . the stand: the board with ONE stick of about 10 mm, 160 to 176 mm above the board, in an end hole or the
        middle one; on the table or on a platform of 10 to 80 mm; the stick inside x -380..-140, y -170..+170;
      . 15 to 60 mm or more between the cylinder and the board; other objects may stand around;
  - it first looks down from (-340, 45, 615) (the old pose if that one is refused), waiting 0.5 s before each look;
      then from about 330 mm above the cylinder and above the stick's top;
  - IT NEVER SQUEEZES THE STICK;
  - it closes on the cylinder along two lines 90 degrees apart to centre it (ordinary moves down, no long move
      until contact), lets go and looks at it again (the camera's error), then looks at the stick the same way;
      a cylinder with no two free finger lines is first carried 65 to 85 mm to a clearer place;
  - it grips again, lifts to check the hold, carries the cylinder high over the stick, goes down to 5 mm above the
      stick's top by an ordinary move, then ONE short move until contact (about 17 mm, 5 mm/s); if it sits on the
      top it probes around (0.9 mm steps, up to 3.3 mm); it lets go once the cylinder's bottom is 6 mm below the top;
  - if the search fails it puts the cylinder back and tries once more if time allows; then it opens, lifts and
      goes back to its start pose. NOTHING CHECKS THAT THE CYLINDER IS ON THE STICK after letting go;
  - 110 to 200 s in simulation (a search adds 30 to 150 s); its own estimate for the rig is 3 to 5 minutes."""
PROGRAM_MORE["insert_with_real_info"] = """
  - THIS PROGRAM IS THE CLAUDE CODE INSERTION SKILL OF A FROM-NOTHING SESSION THAT WAS GIVEN THE TEXT ABOUT THE RIG AT
      ITS START AND NO REAL EPISODE (runs/bimanual/insert_with_real_info_1126, v39). Simulation only; not yet run on
      the real arm. Only the LEFT arm moves.
      . the cylinder: STANDING ON AN END, up to 64 mm across, 15 to 70 mm high and at most twice as high as wide; on
        the table or on a platform of 20 to 120 mm;
      . the stand: a base with ONE vertical stick 5 to 22 mm thick, at least 50 mm long and 15 to 90 mm longer than
        the cylinder is high; on the table or on a platform of 20 to 120 mm;
      . practised with both anywhere in x -490..-170, y -200..+340; other objects may stand around, 45 mm or more
        from the cylinder;
  - it first looks down from (-400, 120, 600) (a closer survey from 430 if it finds no stick or no cylinder), then
      from 330 mm above the stick's top and above each cylinder candidate (it takes the one with a visible hole);
  - IT CLOSES THE FINGERS ON THE STICK, TWICE, 3 mm above the stick's base (along its first closing direction, then
      turned 45 degrees), to find the stick's place exactly; the stand may slide when it does;
  - it goes down beside the cylinder by a move until contact, closes on it once 5 mm above its support, lets go,
      waits 1 s and looks at it again (the camera's error); then it grips it there;
  - it carries the cylinder over the stick, corrects its place from where the arm says it is, goes down until
      contact; if it sits on the stick's top it searches a spiral (0.6 to 2.5 mm, about 60 points; the search stops
      at 470 s); then it pushes down toward the base, opens, lifts and goes back to its start pose. If it cannot
      thread, it lets go of the cylinder and goes home. NOTHING CHECKS THAT THE CYLINDER IS ON THE STICK after;
  - 105 to 230 s in simulation; its own estimate for the rig is 250 to 450 s."""
PROGRAM_MORE["hang_mugs"] = """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION ONLY, in worlds its session wrote itself (no real mug, no real rack).
      USE UNBREAKABLE MUGS: the gripper closes with up to 70 N and the arm pushes up to its 35 N stop; its own
      summary says the same. Only the LEFT arm moves. Where it practised (table frame, mm):
      . the rack: FREE-STANDING and heavy (base 210 to 290 mm across, 2.5 to 4.3 kg in all), TWO pins for the two mugs,
        175 to 260 mm apart, each 95 to 145 mm long and 10 to 18 mm thick, 260 to 370 mm above the surface, pointing
        roughly toward the mugs' side (+x to -x: "front-facing"), level or rising slightly, with an upturned tip of
        8 to 20 mm; the rack's foot at about x -275..-205, y +120..+235, turned by up to 37 degrees; it may stand on a
        pedestal of 25 or 50 mm;
      . TWO mugs, UPRIGHT, 54 to 88 mm across (the gripper opens 80 mm: wider ones it pinches by the wall), 70 to
        115 mm high, 0.16 to 0.50 kg, each with ONE closed handle whose opening is about 42-58 mm wide and 46-68 mm
        high; inside x -490..-350, y -170..+510, on the surface or on a block of 25 or 55 mm;
      . 2 to 5 other objects of about 56 x 60 x 50 mm around;
  - it surveys the rack from wrist poses, takes a mug by its body, lifts it, turns the handle to a pin, threads it
      with moves until contact (5 mm/s), lowers it until the pin carries it, opens and backs away; then the second
      mug; then its start pose, the gripper open, and a wait of 30 s;
  - it does not look again after letting go: a mug that falls or a rack that moves is not noticed;
  - 3 to 5 minutes in simulation for both mugs (its limit is 900 s on its own clock)."""
PROGRAM_MORE["hang_mugs_cc"] = """
  - THIS PROGRAM WAS WRITTEN IN SIMULATION ONLY, by a Claude Code session, in worlds it wrote itself (no real mug, no
      real rack). It is not the Astra from-nothing one (--task hang_mugs): another text, another rack, another grasp.
      USE UNBREAKABLE MUGS: IT PINCHES A MUG BY ITS RIM (one finger inside, one outside the wall), and the arm pushes
      up to its 35 N stop. Only the LEFT arm moves. Where it practised (table frame, mm):
      . the rack: a MUG TREE, free-standing: a round or square base 170 to 230 mm across (1.0 to 2.0 kg), a central
        post 16 to 30 mm thick, and STRAIGHT branches RISING OUTWARD at 15 to 40 degrees: one level of 3 or 4, or two
        levels of 2 or 3 (55 to 85 mm apart); the branches 8 to 15 mm thick and 90 to 140 mm long, starting 150 to
        210 mm above the base, half of them with a small knob at the tip; the post ends 30 to 90 mm above the top
        level. Centred at x -400..-240, y -100..+300, turned any way; it may stand on a platform of 15 to 50 mm;
      . TWO mugs, UPRIGHT, 72 to 92 mm across, 80 to 115 mm high, the wall 3 to 6 mm thick, 0.22 to 0.42 kg, each with
        ONE closed handle whose opening is 20 to 32 mm wide and at least 36 mm high (the branch and its knob must
        pass through it); inside x -500..-150, y -220..+460, on the table or on a platform of 15 to 50 mm;
      . 0 to 3 other objects around (a box, a can, a ball, a cup without a handle);
  - it first looks down twice from high above (the tool at z 600), then from above each mug and above where it will
      hang; the rack is the TALLEST thing it sees, the mugs are found by their round rims and handles (by depth);
  - it pinches a mug by the rim, 90 degrees round from the handle, 48 mm above the mug's bottom, lifts, weighs
      it by the arm's force estimate, turns, carries it beside a branch tip, slides the handle along the branch in
      4 mm steps (a jump of the force estimate of 3 N is a contact: it backs off and tries 8 offsets), lowers in
      1.5 mm steps until the rack carries more than 55% of the mug's weight, opens and rises; then the other mug;
      then the gripper open and its start pose;
  - on a failure it puts the mug back and tries the other one first; an error ends with the gripper opened and home;
  - 2.5 to 9 minutes in simulation for both mugs (its limit is 900 s on its own clock; it starts nothing new after
      620 s)."""
HELD = {"sweep": "are ALL the small objects inside the target area, at rest, the sponge let go outside the target, the hand "
                 "withdrawn, no distractor moved (the task's success)?",
        "insert_own": "is the cylinder on the stick (the stick right through its hole), let go by the robot, the stand upright, "
                      "the gripper open and back at its start pose (the task's success)?",
        "insert_own_cc": "is the cylinder on the stick (the stick right through its hole), let go by the robot, the stand upright, "
                         "the gripper open and back at its start pose (the task's success)?",
        "insert_own_cc_real": "is the cylinder on the stick (the stick right through its hole), let go by the robot, the stand upright, "
                              "the gripper open and back at its start pose (the task's success)?",
        "insert_from_img": "is the cylinder on the stick (the stick right through its hole), let go by the robot, the stand upright, "
                           "the gripper open and back at its start pose (the task's success)?",
        "insert_own_cc_real2": "is the cylinder on the stick (the stick right through its hole), let go by the robot, the stand upright, "
                               "the gripper open and back at its start pose (the task's success)?",
        "insert_own_cc_fixed": "is the cylinder on the stick (the stick right through its hole), let go by the robot, the stand upright, "
                               "the gripper open and back at its start pose (the task's success)?",
        "sweep_cc": "are at least 9 in 10 of the small objects inside the target area (on the sheet, or inside the tape's outline), "
                    "the sponge let go, nothing else moved by more than about 3 cm or tipped over (the task's success)?",
        "insert_own_cc_episodes": "is the cylinder on the stick (the stick right through its hole), let go by the robot, the stand upright, "
                                  "the gripper open and back at its start pose (the task's success)?",
        "insert_with_real_info": "is the cylinder on the stick (the stick right through its hole), let go by the robot, the stand upright, "
                                 "the gripper open and back at its start pose (the task's success)?",
        "hang_mugs": "do BOTH mugs hang from the rack by their handles, each on its own branch, let go by the robot, the gripper "
                     "open and back at its start pose (the task's success)?",
        "hang_mugs_cc": "do BOTH mugs hang from the rack by their handles (a branch through each handle), let go by the robot, "
                        "the rack upright, the gripper open and back at its start pose (the task's success)?",
        "clutter_one_arm": "does the robot hold the named object up, at least 150 mm above the table, touching nothing else, and has "
                           "nothing fallen off the table (the task's success)?",
        "insert_varied": "is the cylinder on the stick (the stick through its hole), let go by the robot, the stand upright on "
                         "the table (the task's success)?"}


# The tasks of from-nothing sessions (improve.py --own-task): the program's task is its session's task world's own.
#   text    what that world's goal() gave the program;  limit   the session's episodes, s on the program's clock;
#   rig     the rig's own limits per live episode: its robot time as a multiple of `limit`, its trajectories (a sweep
#           wipes in many short moves until contact, each at 5 mm/s here: its rehearsals took 2.1 to 2.8 times the
#           program's clock, and 400 trajectories ended two of nine with the sponge still in the hand);
#   count   what the operator counts after a live episode (key: its name in the verdict; total: fixed, or asked) and
#           where a world's check reports the same (check: its keys for the count and the total, or the list to count)
OWN = {"sweep": dict(text=SWEEP, limit=600.0, rig=dict(time=3.0, trajectories=800),
                     count=dict(key="inside", what="the small objects", ended="ended inside the target area", label="in the target",
                                total=None, check=("inside", "total"))),
       "insert_own": dict(text=INSERT_OWN, limit=600.0, rig=dict(time=2.5, trajectories=400), count=None),
       "insert_own_cc": dict(text=INSERT_OWN_CC, limit=600.0, rig=dict(time=2.5, trajectories=400), count=None),
       "insert_own_cc_real": dict(text=INSERT_OWN_CC, limit=600.0, rig=dict(time=2.5, trajectories=400), count=None),   # (w21's text = w16's)
       "insert_from_img": dict(text=INSERT_FROM_IMG, limit=600.0, rig=dict(time=2.5, trajectories=400), count=None),
       "insert_own_cc_real2": dict(text=INSERT_OWN_CC, limit=600.0, rig=dict(time=2.5, trajectories=400), count=None),  # (w25's text = w16's)
       "insert_own_cc_fixed": dict(text=INSERT_OWN_CC, limit=600.0, rig=dict(time=2.5, trajectories=400), count=None),  # (its world is w16 itself)
       "sweep_cc": dict(text=None, limit=600.0, rig=dict(time=3.0, trajectories=800),      # (text: sweep_cc_text or --instruction)
                        count=dict(key="inside", what="the small objects", ended="ended inside the target area", label="in the target",
                                   total=None, check=("in", "n"))),
       "insert_own_cc_episodes": dict(text=INSERT_OWN_CC, limit=600.0, rig=dict(time=2.5, trajectories=400), count=None),  # (w18's text = w16's)
       "insert_with_real_info": dict(text=INSERT_WITH_REAL_INFO, limit=600.0, rig=dict(time=2.5, trajectories=400), count=None),  # (w13's own text)
       "hang_mugs": dict(text=HANG_MUGS, limit=900.0, rig=dict(time=2.5, trajectories=400),
                         count=dict(key="hung", what="the mugs", ended="hang from the rack by their handles", label="hung",
                                    total=2, check=("hanging_count", "mugs"))),
       "hang_mugs_cc": dict(text=HANG_MUGS_CC, limit=900.0, rig=dict(time=2.5, trajectories=800),     # (4 mm and 1.5 mm steps)
                            count=dict(key="hung", what="the mugs", ended="hang from the rack by their handles", label="hung",
                                       total=2, check=("hanging", "mugs")))}
COUNT_KEYS = {v["count"]["key"]: v["count"]["label"] for v in OWN.values() if v["count"]}


def counted(v):
    """A verdict's count -> (its key, n, total), or None."""
    for k in COUNT_KEYS:
        if v.get(k) is not None and v.get("total"):
            return k, v[k], v["total"]
    return None


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
    elif a.task in OWN:                                                       # (a from-nothing session's own task)
        if a.backend == "twin":
            raise SystemExit(f"--task {a.task}: on the robot, or --backend sim --world FILE (a session's world: a rehearsal)")
        limit = OWN[a.task]["limit"]
    elif a.backend == "twin":
        raise SystemExit(f"--task {a.task}: the twin has no cylinder and no stand; its rehearsal is --backend sim")
    else:
        limit = float(TASKS[a.task].time_limit_s)
    # the program's limit is the simulation's, on the program's clock (bimanual/real.py, time); the rig's own is longer,
    more = OWN[a.task]["rig"] if a.task in OWN else dict(time=2.5, trajectories=400)
    a.time_limit = a.time_limit or (limit if a.robot_clock else more["time"] * limit)   # as its stop_on_contact moves are slower
    a.max_trajectories = a.max_trajectories or (40 if a.mode == "dry" else more["trajectories"])
    a.checklist_more = PROGRAM_MORE[a.task]
    a.held_question = HELD.get(a.task)
    a.count = OWN[a.task]["count"] if a.task in OWN else None
    text = None
    if a.task in OWN:
        text = OWN[a.task]["text"]
        if a.task == "sweep_cc" and not a.instruction and a.backend == "robot":   # (its text names what is on this table)
            text = sweep_cc_text(a)
            if text is None:
                raise SystemExit("--task sweep_cc on the robot: its text names the sponge, the small objects and the marker. Give "
                                 "--sponge COLOUR --pieces NAME --target 'COLOUR paper' | 'COLOUR tape' (and --scrub COLOUR, "
                                 "--sponge-on small|tall if they apply), or --instruction with the whole text")
        if a.instruction:                                                     # (another text: given, or a file's)
            text = Path(a.instruction).read_text().strip() if Path(a.instruction).is_file() else a.instruction.strip()
    if a.task == "sweep_cc" and a.backend == "robot":                         # (the operator checks it against the table)
        print("task text:", text, flush=True)
        a.checklist_more += f"\n  task text: {text}"
    world = own_world(a, program) if a.task in OWN and a.backend == "sim" else None
    a.world_judged = world is not None
    hi = preflight(a)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    tag = a.tag or program.parent.name
    out = Path(a.out_root or RESULTS) / f"{stamp}_{a.backend}_{a.mode}_{tag}"
    prog_out = out / "program"; prog_out.mkdir(parents=True)
    task = sim = None
    one_arm = a.task == "clutter_one_arm"
    if a.task in OWN and a.backend == "robot":
        task = dict(kind=a.task, instruction=text)
    elif a.task in OWN:                                                       # (sim: the world's scene of the seed and its own text)
        made = world_scene(world, a.seed, out / "sim")
        sim = dict(task="insert_varied", kind=a.task, seed=a.seed, sensors=a.sensors, scene=made["scene"], world=str(world),
                   grip_force_n=sim_grip_force(a), force_abort_n=a.sim_force_abort,
                   text=(text if a.instruction else made["text"] or text))
    elif a.backend == "robot" and one_arm:                                      # (the task's text names the target: clutter.text)
        name = re.sub(r"^the\s+", "", a.object.strip())
        task = dict(kind=a.task, instruction=clutter._text(a.level, name, "left"), object=name, level=a.level)
    elif a.backend == "robot":
        task = dict(kind=a.task, instruction=TASKS[a.task].text)
    elif a.backend == "sim":                                                  # (the scene of the seed names its own target)
        sim = dict(task=f"pick_clutter_one_arm_{a.level}" if one_arm else a.task, seed=a.seed, sensors=a.sensors,
                   scene=json.loads(Path(a.scene).read_text()) if a.scene else None, grip_force_n=sim_grip_force(a), force_abort_n=a.sim_force_abort)
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
                speed=a.speed, guard_speed=a.guard_speed, guard_speed_high=a.guard_speed_high or a.guard_speed, guard_dz=a.guard_dz,
                free_push=a.free_push, raw_force=a.raw_force,
                confirm_contact=a.confirm_contact,
                max_stops=a.max_stops, settle_all=not a.no_settle, platform=git_rev(), started=stamp)
    skill = out / "skill"; skill.mkdir()
    shutil.copy2(program, skill / "program.py")                               # (the sandbox sees this copy only)
    if world is not None:                                                     # (and the world's, for the robot it gives the
        shutil.copy2(world, skill / "world.py")                               #  program and for its check: host_program.py)
    spec["skills_hash"] = tree_hash(skill)
    (out / "spec.json").write_text(json.dumps(spec, indent=1))
    host_args = ["--program", str(skill / "program.py"), "--guard-speed", str(a.guard_speed), "--max-stops", str(a.max_stops),
                 "--guard-speed-high", str(a.guard_speed_high or a.guard_speed),
                 "--program-limit", str(limit)] + (["--robot-clock"] if a.robot_clock else []) + (["--no-settle"] if a.no_settle else []) + \
        (["--clutter-level", a.level] if a.task == "clutter" else []) + (["--speed", str(a.speed)] if a.speed is not None else []) + \
        (["--guard-dz", str(a.guard_dz)] if a.guard_dz is not None else []) + (["--free-push"] if a.free_push else []) + \
        (["--confirm-contact"] if a.confirm_contact else []) + \
        (["--raw-force"] if a.raw_force else []) + \
        (["--world", str(skill / "world.py"), "--world-seed", str(int(a.seed))] if world is not None else [])
    launch(a, out, prog_out, spec, skill, "host_program.py", host_args, hi)
    if world is not None:
        world_verdict(a, out, world)
    txt = prog_out / "output.txt"                                             # the program's own prints (not the commands' log)
    own = [l for l in txt.read_text().splitlines() if l.strip() and not l.startswith("#")] if txt.exists() else []
    if own:
        print(f"the program's own output ({txt}; {len(own)} lines, the first 8):")
        for l in own[:8]:
            print("  " + l[:200])
    return out


def sim_grip_force(a):
    """--backend sim: the force the simulated gripper closes with (N), or None for the simulation's own (70 N).
    --sim-grip-force N | rig | sim; default: the rig's grasp force (rig.json: 30 N) for the from-nothing tasks, whose
    programs squeeze supported and tapered objects, the simulation's for the others (as their rehearsals were run)."""
    f = a.sim_grip_force or ("rig" if a.task in OWN else "sim")
    if f == "sim":
        return None
    if f == "rig":
        from agentic_robotics.sim import twin  # noqa: F401  (the vendored code on the path)
        from panda_fk import load_rig
        return float(load_rig()["gripper"]["grasp_force"])
    return float(f)


def own_world(a, program):
    """--world, or the task world of the session the program is in (its result.json's task_world: worlds/wNN.py)."""
    if a.world:
        w = Path(a.world).resolve()
    else:
        r = program.parent / "result.json"
        k = json.loads(r.read_text()).get("task_world") if r.exists() else None
        if k is None:
            raise SystemExit("--world FILE is needed: the program's directory names no task world (result.json task_world)")
        w = program.parent / "worlds" / f"w{int(k):02d}.py"
    if not w.is_file():
        raise SystemExit(f"{w}: no such world")
    return w


def _world_tool(args, world, out, what):
    """real/world_tool.py in the program sandbox (a session's world code never runs outside it) -> its stdout."""
    out.mkdir(parents=True, exist_ok=True)
    tool = REAL / "world_tool.py"
    sock_dir = Path(tempfile.mkdtemp(prefix="arw-"))                          # (the sandbox's second writable directory: unused)
    try:
        cmd = sbx.program_cmd([VENV_PY, "-I", str(tool)] + args + ["--world", str(world), "--out", str(out)], world.parent, out, sock_dir)
        cmd[1:1] = ["--ro-bind", str(tool), str(tool)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)
    if r.returncode != 0:
        raise SystemExit(f"{what}: {(r.stderr.strip().splitlines() or ['failed'])[-1]}")
    return r.stdout


def world_scene(world, seed, out):
    """The empty table of the seed as the world fills it, and the world's own task text (None without a goal())."""
    _world_tool(["scene", "--seed", str(int(seed))], world, out, "the world's scene")
    return dict(scene=json.loads((out / "scene.json").read_text()),
                text=(out / "task.txt").read_text() if (out / "task.txt").exists() else None)


def world_verdict(a, out, world):
    """After a rehearsal in a world: the world's own check, as the program's host ran it in the sandbox on the
    simulation's end state (host_program.py: the same process as the world's robot, whose record the check may
    read) -> the verdict of result.json, with the task's count as the check reports it."""
    r = json.loads((out / "result.json").read_text())
    if a.mode == "dry":
        return
    c = (r.get("program_result") or {}).get("world_check") if isinstance(r.get("program_result"), dict) else None
    if not isinstance(c, dict):
        c = dict(ok=False, error="the world's check did not run (program/output.txt, program/host.log)")
    v = dict(by=f"the world's success() ({world.name})", success=bool(c.get("ok")), category="success" if c.get("ok") else "fail", world_check=c)
    cnt = getattr(a, "count", None)
    if cnt:
        n, total = c.get(cnt["check"][0]), c.get(cnt["check"][1])
        total = len(total) if isinstance(total, list) else total
        if isinstance(n, int) and isinstance(total, int) and total > 0:
            v.update({cnt["key"]: n, "total": total, "fraction": round(n / total, 3)})
    r["verdict"] = v
    (out / "result.json").write_text(json.dumps(r, indent=1, default=str))
    write_readme(out, r)
    print("judged by the world: " + one_line(out, r))


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
        cmd[1:1] = ["--ro-bind", str(REAL / host), str(REAL / host)]             # (only this file of real/: no results;
        cmd[1:1] = ["--ro-bind", str(REAL / "world_tool.py"), str(REAL / "world_tool.py")]   # and the world's reader)
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
        if getattr(a, "world_judged", False) and a.mode != "dry":             # (a world's own task: world_verdict, after this)
            return dict(by="pending: the world's own check", success=None)
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
    def count(q):
        while True:
            s = input(f"   {q} ").strip()
            if s.isdigit():
                return int(s)
    held = yn(getattr(a, "held_question", None) or "is the target held up by both fingers, nothing else lifted (the task's success)?")
    counted = {}
    cnt = getattr(a, "count", None)
    if cnt:                                                                   # (a sweep: the fraction in the target; the mugs that hang)
        total = cnt["total"] or count(f"how many of {cnt['what']} were on the table?")
        n = min(count(f"how many of {cnt['what']} {cnt['ended']}" + (f" (of {total})?" if cnt["total"] else "?")), total)
        counted = {cnt["key"]: n, "total": total, "fraction": round(n / total, 3) if total else None}
    knocked = yn("did anything fall off the table or get knocked over?")
    stopped = yn("did you intervene (E-stop, refused a trajectory, by hand)?")
    note = input("   a note (Enter for none): ").strip()
    return dict(by="operator", success=bool(held and not knocked and not stopped and not interrupted), held=held,
                knocked_off=knocked, intervened=stopped or interrupted, note=note, **counted)


def one_line(out, r):
    v = r.get("verdict") or {}
    tr = r.get("trajectories") or {}
    c = counted(v)
    part = f" ({c[1]}/{c[2]} {COUNT_KEYS[c[0]]})" if c else ""
    return (f"{out.name}: {v.get('category') or ('SUCCESS' if v.get('success') else ('-' if v.get('success') is None else 'fail'))}{part}"
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
n_masks = int(sys.argv[8])
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
        for mk, s in res[:n_masks]:
            cnts, _ = cv2.findContours(mk.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(im, cnts, -1, (0, 255, 0), 2 if n_masks <= 3 else 1)
            ys, xs = np.nonzero(mk)
            if len(xs) and n_masks <= 3:                       # (many small masks: the outlines only, the scores in check.json)
                cv2.putText(im, f"{s:.2f}", (int(xs.mean()), int(ys.mean())), 0, 0.6, (255, 255, 0), 2)
        cv2.imwrite(str(out / f"{cam}_sam3.png"), cv2.cvtColor(im, cv2.COLOR_RGB2BGR))
        rep[cam]["sam3"] = [round(s, 3) for _, s in res]
        rep[cam]["sam3_pixels"] = [int(mk.sum()) for mk, _ in res]
ep.be.finish(dict(result="check"), out)
json.dump(rep, open(out / "check.json", "w"), indent=1, default=str)
print(json.dumps(rep, default=str)[:3000])
import os; sys.stdout.flush(); os._exit(0)
'''
    py = robot_python() if a.backend == "robot" else VENV_PY
    r = subprocess.run([py, "-I", "-c", code, str(ROOT), str(out), a.backend, a.object or "", a.sam3, str(a.port or ""),
                        a.replay_frames or "", str(a.masks)], env=base_env(), cwd=str(ROOT), capture_output=True, text=True, timeout=300)
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
    if (a.inside is None) != (a.total is None) and a.hung is None:
        raise SystemExit("--inside and --total come together")
    if a.inside is not None and a.hung is not None:
        raise SystemExit("--inside (a sweep) or --hung (the mugs), not both")
    if a.inside is not None:                                                  # (a sweep: the small objects, counted)
        if not 0 <= a.inside <= a.total or a.total == 0:
            raise SystemExit("--inside N --total M: 0 <= N <= M, M > 0")
        r["verdict"].update(inside=a.inside, total=a.total, fraction=round(a.inside / a.total, 3))
    if a.hung is not None:                                                    # (the mugs that hang, of --total: 2)
        total = a.total or 2
        if not 0 <= a.hung <= total:
            raise SystemExit(f"--hung N: 0 <= N <= {total}")
        r["verdict"].update(hung=a.hung, total=total, fraction=round(a.hung / total, 3))
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
        vv += f" ({counted(v)[1]}/{counted(v)[2]} {COUNT_KEYS[counted(v)[0]]})" if counted(v) else ""
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
    s.add_argument("--masks", type=int, default=3, help="how many of SAM3's masks are drawn, best first (many small objects: 60)")
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
    s.add_argument("--task", default="insert_varied", choices=("insert_varied", "clutter_one_arm", "sweep", "insert_own", "insert_own_cc", "insert_own_cc_real", "insert_own_cc_real2", "insert_own_cc_fixed", "insert_own_cc_episodes", "insert_with_real_info", "insert_from_img", "sweep_cc", "hang_mugs", "hang_mugs_cc", "clutter"),
                   help="insert_varied: put the cylinder on the stick (one arm's work); clutter_one_arm: take --object out of a "
                        "heap with the left arm alone (--level, --ask); the programs of from-nothing sessions: sweep (the sponge and "
                        "the small objects into the marked target area), insert_own (the cylinder on the stick, as learned from "
                        "nothing: not insert_varied's program), insert_own_cc (the same from a Claude Code session), hang_mugs (both mugs on "
                        "the rack), hang_mugs_cc (the same from a Claude Code session); clutter: twin backend only")
    s.add_argument("--instruction", help="sweep, insert_own, hang_mugs: the task's text given to the program, or a file with it "
                                         "(default: the text of the session's task world, OWN; sim: the world's own)")
    s.add_argument("--sponge", help="sweep_cc on the robot: the sponge's colour, for the task's text ('yellow')")
    s.add_argument("--scrub", help="sweep_cc: the colour of the sponge's scrubbing side, if it has one ('green')")
    s.add_argument("--sponge-on", choices=("small", "tall"), help="sweep_cc: the sponge lies on a block (small: under 40 mm)")
    s.add_argument("--pieces", help="sweep_cc: the small objects' name, as the text says it ('lentils', 'beans')")
    s.add_argument("--target", help="sweep_cc: the marker, 'COLOUR paper' (a sheet) or 'COLOUR tape' (an outline): 'white paper'")
    s.add_argument("--world", help="sweep, insert_own, hang_mugs, --backend sim: a session's world file (its scene, text, robot and check); default: the task "
                                   "world beside the program (worlds/wNN.py of its result.json)")
    s.add_argument("--mode", default="dry", choices=("dry", "live"))
    s.add_argument("--backend", default="robot", choices=("robot", "sim", "twin"),
                   help="sim: the bimanual simulation's scene of the task (a rehearsal); twin: --task clutter's test")
    s.add_argument("--tag")
    s.add_argument("--time-limit", type=float, default=None,
                   help="the rig's robot seconds (default: 2.5 x the task's 600 s; sweep: 3 x; hang_mugs: 2.5 x 900 s; the program's own limit stays the task's, on its clock)")
    s.add_argument("--no-settle", action="store_true",
                   help="the arm server's settle after the unguarded ways down only (default: after every move that is not a contact)")
    s.add_argument("--robot-clock", action="store_true",
                   help="robot.time_s() = the rig's clock (default: the stop_on_contact moves counted at the simulation's 40 mm/s)")
    s.add_argument("--max-trajectories", type=int, default=None, help="default 40 dry, 400 live (sweep: 800 live; one per command of the program)")
    s.add_argument("--speed", type=float, default=None, help="RealSim time scale (default rig.json's: 0.5)")
    s.add_argument("--guard-speed", type=float, default=0.005, help="m/s of the stop_on_contact moves (0.005: validated on the arm; simulation: 0.04)")
    s.add_argument("--guard-speed-high", type=float, default=None,
                   help="m/s of the part of a stop_on_contact move above 150 mm (default: --guard-speed): a long way down from high "
                        "up goes faster there, guarded all the same (the simulation's is 0.04 everywhere)")
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
    s.add_argument("--sim-force-abort", type=float, default=None,
                   help="sim: the force (N) at which the simulated arm server stops a trajectory, in place of rig.json's 35 N: to "
                        "see the rest of an episode that the rig's limit would end (not the rig's behaviour)")
    s.add_argument("--sim-grip-force", help="sim: the force the simulated gripper closes with: N, 'rig' (rig.json's grasp force, 30 N: "
                                            "the default for sweep, insert_own, hang_mugs) or 'sim' (the simulation's 70 N: the others' default)")
    s.add_argument("--object", help='clutter_one_arm on the robot: the target, by colour and shape ("red cylinder"); clutter: unused')
    s.add_argument("--level", default="medium", help="clutter, clutter_one_arm: the task's text, medium (the target can be seen) | hard (it lies under others)")
    s.add_argument("--task-config", default="configs/tasks/clutter_pick_A_rig.yaml", help="twin: the condition")
    s.add_argument("--split", default="val"); s.add_argument("--twin-level", type=int, default=1)
    s.add_argument("--idx", type=int, default=0)
    s = sub.add_parser("verdict"); s.add_argument("dir"); s.add_argument("result", choices=("success", "fail"))
    s.add_argument("--knocked-off", action="store_true"); s.add_argument("--intervened", action="store_true")
    s.add_argument("--inside", type=int, help="a sweep: how many of the small objects ended inside the target area")
    s.add_argument("--total", type=int, help="a sweep: how many small objects there were (the mugs: 2)")
    s.add_argument("--hung", type=int, help="hang_mugs: how many of the mugs hang from the rack by their handles")
    s.add_argument("--note", default="")
    s = sub.add_parser("summary"); s.add_argument("dirs", nargs="*")
    a = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    {"check": check, "episode": run_episode, "program": run_program, "verdict": set_verdict, "summary": summary}[a.cmd](a)


if __name__ == "__main__":
    main()
