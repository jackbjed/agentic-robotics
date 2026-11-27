"""The texts the agents get. ONE shared template (the interface, the rules, the task, what is in the workspace, what the
program must be) and one block per condition that says what this session may do. The task's text, the interface and
the first look are identical in every condition."""
from pathlib import Path

from xsim import config

HERE = Path(__file__).resolve().parent
PAD = " " * 30
RULES = ("Imports: only math, numpy, scipy, cv2, itertools, functools, collections, dataclasses, typing, heapq, bisect, statistics, random, "
         "time, copy, enum, operator, numbers, abc (and their submodules). No eval, exec, open, getattr or file access (numpy's and "
         "OpenCV's file functions included), and no attribute whose name starts with an underscore except on self.")

SHARED = """{interface}

{rules}

THE TASK (the same text the program gets as `task`):

{task}

WHAT THE PROGRAM IS FOR. The program is a reusable skill: it will be run on many episodes of this task that you never
see, each starting fresh (nothing is remembered from one episode to the next), in the TARGET environment: a simulated
robot cell that this text calls the target. In every episode the program must find out the scene with the cameras and
the arm's own measurements: the things are the same from episode to episode, but they stand elsewhere and are turned
differently. The look you are given is one example of such a scene; do not write the program for that one scene.

Final evaluation uses separate, unseen target configurations. The {panel} target development configurations are identical
across B/C/D and across submissions; final evaluation feedback is never returned during development.

WHAT YOU ARE GIVEN, in your working directory:
- docs/interface.md, docs/task.md: this text's interface and task, to read again.
- docs/robot.md: the robot's model: its joints and their ranges, the gripper's shape, how its commands are carried
  out, the cameras' intrinsics and mounts, the table and the robot's place at it.
- look/: one look at a target scene, the arm at its start pose. For each camera: look_NAME.png the colour image,
  look_NAME_depth.png the depth as a picture (near red, far blue, the scale on top), look_NAME_depth.txt the depth as
  numbers (mm along the camera's axis on a grid over the image, with the camera's intrinsics and pose); state.txt the
  arm's state.
You are NOT given, and nothing here tells: the things' exact sizes, weights and surfaces, their true positions, or
how the target environment is made. What you need of them you estimate from what the cameras and the arm show.
{condition}"""

A = """
THIS SESSION. Write the program to the file program.py in your working directory (the whole program: a Python file
that defines run(robot, task)). You have your file tools and nothing else: there is nothing you can run and nothing to
try the program in. The program is used exactly as you leave it."""

TOOL = """
THIS SESSION. You work with your file tools, which reach only this directory, and ONE more tool: the session's
command (it is among your tools as mcp__xsim__run). It takes one argument, `command`: one sub-command line of those
below. There is no shell here: nothing else can be run.

    SUB-COMMAND ...

{subs}
note --text "..." [--round K] [--kind diagnosis|rationale|other]
                              put on record what you conclude from a result, or why you change something
status                        what has been saved and what is left of this session's allowances
finish [--version V]          end the session: the version named (the latest by default) is its result

Every answer of the command ends with what is left of the session's allowances. They are limits, not targets: end the
session with `finish` when the program is as good as you can make it here.
{start}"""

SUB = dict(
    save_program='save_program FILE --note "..."   save a file of yours as a new version of the program (v0, v1, ...). Every version is\n'
                 '                              kept. --note: why this version (what you changed and what made you change it).',
    save_world='save_world FILE --note "..." --aim mismatch|coverage\n'
               '                              save a file of yours as a new version of the practice world (w0, w1, ...): see\n'
               '                              docs/worlds.md. --aim: mismatch = to make the practice world agree better with what\n'
               '                              the target showed; coverage = to practise a wider range of cases.',
    save_world_build='save_world FILE --note "..."  save a file of yours as a version of the practice world (w0, w1, ...): see docs/worlds.md',
    validate_world='validate_world [--world K] [--seeds S ...]\n'
                   '                              build scenes of a practice world in the practice simulator (at most 4 a call) and run the\n'
                   '                              harness\'s own interface check in each (it looks with both cameras, reads the arm\'s state,\n'
                   '                              moves the gripper a little): you get whether the scene loads and renders, the cameras\'\n'
                   '                              images, and the TRUE positions of the world\'s bodies (it is your own world). Every scene\n'
                   '                              checked counts as one episode of the session\'s simulator allowance ({cap} episodes in all).',
    practice='practice --seeds S ... [--version V] [--world K]\n'
             '                              run a version of the program on scenes of a practice world, in the practice simulator (MuJoCo),\n'
             '                              at most 8 episodes a call. For each you get the verdict of the world\'s own success() (a\n'
             '                              self-check of the practice world, NOT the target\'s judgement), how the program ended, the\n'
             '                              robot time, and the episode\'s log with the TRUE positions of the practice world\'s bodies.\n'
             '                              The session\'s simulator allowance is {cap} episodes in all: every practice episode started\n'
             '                              counts one, whatever its outcome (a failed or crashed run too), and so does every\n'
             '                              scene checked by validate_world.',
    submit_target='submit_target [--version V] [--diagnosis "..."]\n'
                  '                              run a version of the program in the TARGET environment, one fresh episode a scene. Each\n'
                  '                              submission runs on the same {panel_w} fixed target development configurations, shared\n'
                  '                              across B/C/D. At most {subs_w} submissions are allowed, for a maximum of {total} target\n'
                  '                              episodes per session. The version cannot change while the batch runs. You get, for every\n'
                  '                              episode: success or failure by the target\'s own judge, how the program ended, robot and\n'
                  '                              wall-clock time, the program\'s log (what it printed and every robot command with its\n'
                  '                              result and the arm\'s measured tool position), frames of the episode\'s video, and the images\n'
                  '                              the program showed; and, for the submission, the success rate. You do not get the things\'\n'
                  '                              true positions or properties.',
    save_practice='save_practice FILE --note "..."  save a file of yours as a new version of your practice file (p0, p1, ...): see docs/practice.md.\n'
                  '                              It holds scene(scene, rng), the scenes you practise on, and success(state), your own check\n'
                  '                              of a practice episode. Every version is kept. --note: why this version.',
    validate_scene='validate_scene [--practice K] [--seeds S ...]\n'
                   '                              build scenes of a practice file in the target simulator (at most 4 a call) and run the\n'
                   '                              harness\'s own interface check in each (it looks with the three cameras, reads the arms\' states,\n'
                   '                              moves each arm and gripper a little): you get whether the scene is valid and rests as asked,\n'
                   '                              the scene as built, the cameras\' images, and the TRUE positions of the things. Every scene\n'
                   '                              asked for counts as one episode of the session\'s practice allowance ({cap} episodes in all).',
    practice_f='practice --seeds S ... [--version V] [--practice K]\n'
               '                              run a version of the program on scenes of a practice file, IN THE TARGET SIMULATOR (the\n'
               '                              target\'s own things, robot, sensors and physics), at most 8 episodes a call. An episode ends\n'
               '                              when the program returns, when YOUR success(state) says so, when the robot time is spent, or\n'
               '                              on an error. For each you get the verdict of your own success(), NOT the target judge\'s.\n'
               '                              The official verdict is not exposed to you and does not control practice termination.\n'
               '                              You also get how the program ended, the robot time, the episode\'s log with the TRUE\n'
               '                              positions of the things, the scene as built,\n'
               '                              and videos and frames of the same kinds as a target episode gives. The session\'s practice\n'
               '                              allowance is {cap} episodes in all: every practice episode asked for counts one, whatever its\n'
               '                              outcome (an invalid scene, a failed or crashed run too), and so does every scene checked by\n'
               '                              validate_scene.',
    target_practice='target_practice --configs I ... [--version V]\n'
                    '                              run a version of the program in the TARGET environment on practice scenes of your choice\n'
                    '                              (numbered 0 to {pool}; at most 8 episodes a call, {fcap} in the whole session), one fresh\n'
                    '                              episode a scene. You get for every episode what is described under FEEDBACK in docs/target.md:\n'
                    '                              success or failure by the target\'s own judge, how the program ended, the times, the\n'
                    '                              program\'s log, frames of the video, the images the program showed; not the things\' true\n'
                    '                              positions or properties.',
)

START = dict(
    build="\nYOUR JOB HERE is not the skill: it is the PRACTICE WORLD. From the look and the documents, write a world file (docs/worlds.md) "
          "that builds, in the practice simulator (MuJoCo), scenes like the target's: the same kinds of things, with the shapes, sizes, "
          "weights and surfaces you estimate from the look, standing where and turned how the task's text says they vary. Check with "
          "validate_world that it loads, settles, renders and answers the interface check. Do not write or try a skill program here. "
          "Write what you assumed and what you could not tell into notes.md: whoever practises in this world later gets these notes. "
          "End with `finish`.",
    B="\nprogram.py in your working directory is the starting program (it is saved as v0). It was written from the same look, "
      "without ever being run. There is no practice simulator in this session: the only place the program runs is the target, through "
      "submit_target.",
    C="\nprogram.py is the starting program (saved as v0; written from the same look, never run). world.py is the practice world "
      "(saved as w0): it was built from the same look by someone who never saw the target either, and construction_notes.md is what "
      "they noted. In this session the practice world is FIXED: you can practise in it on any scenes (seeds) you like, and read it, "
      "but not change it.",
    D="\nprogram.py is the starting program (saved as v0; written from the same look, never run). world.py is the practice world "
      "(saved as w0): it was built from the same look by someone who never saw the target either, and construction_notes.md is what "
      "they noted. You may revise the practice world as well as the program: its things' shapes and physical properties, its scenes, "
      "how they vary, and its self-check; the robot, its commands, its cameras and the table stay as they are.",
    E="\nprogram.py is the starting program (saved as v0; written from the same look, never run). world.py is the practice world "
      "(saved as w0): it was built from the same look by someone who never saw the target either, and construction_notes.md is what "
      "they noted. You may revise the practice world as well as the program. There are no runs in the target in this session: the "
      "final program is tried there only after the session has ended, and you are not told how it went.",
    F="\nprogram.py is the starting program (saved as v0; written from the same look, never run). In this session you practise IN THE "
      "TARGET SIMULATOR ITSELF: the target's own things, robot, sensors and physics. practice.py is your practice file (saved as p0; "
      "docs/practice.md): it says which scenes you practise on (fresh ones drawn by the harness, or scenes you ask for) and holds your own "
      "check of a practice episode, success(state), which you write and may revise. The official verdict is not exposed to you and does "
      "not control practice termination. A practice episode is judged and ended by your own check only, from the TRUE state of the "
      "practice scene, which your check and you may see and the program may not. The target's judge speaks only through submit_target.",
)
WHICH = dict(build=["save_world_build", "validate_world"], B=["save_program", "submit_target"],
             C=["save_program", "validate_world", "practice", "submit_target"],
             D=["save_program", "save_world", "validate_world", "practice", "submit_target"],
             E=["save_program", "save_world", "validate_world", "practice"], F=["save_program", "save_practice", "validate_scene", "practice_f", "submit_target"])

WORLDS_DOC = """A practice world is a Python file that makes the practice simulator's (MuJoCo's) scenes. It defines

    scene(scene, rng) -> scene     `scene` is a dict describing an empty table: fill scene["mjcf"] with a MuJoCo XML string
                                   '<mujoco><worldbody> ... </worldbody></mujoco>' whose bodies are the things on the table
                                   (lengths in METRES there; a body with <freejoint/> is loose; name the bodies w_...;
                                   boxes, cylinders, spheres, capsules, ellipsoids; rgba, density or mass, friction).
                                   `rng` is a numpy random generator seeded by the scene's seed: draw from it where things
                                   stand and how they are turned, so that every seed gives another scene. You may set
                                   scene["table"] = {"color": ..., "pattern": "plain"} for the table's look.
    success(state) -> dict         optional: the world's own check of an episode's end ({"ok": True/False, ...}), from the
                                   TRUE state of the practice world: state.pos(name) (mm), state.rot(name) (3x3 as rows),
                                   state.touching(name) ({"robot": ..., "others": [...]}), state.start (the positions at
                                   the start), state.speed(name). It is a self-check of the practice world: the target
                                   has its own judge, which you cannot see.

Fixed by the harness, whatever the file says: the robot and its commands, the cameras, the tabletop at z = 0, and the
task's text. A world file may not define robot() or goal(). The file is under the same rules as a program (imports,
no files)."""

TARGET_DOC = """THE TARGET ENVIRONMENT. The program is finally judged in the target: a simulated robot cell with the same robot, table and
cameras as documented, and the task's real things. It is not the practice simulator, and how it is made is not shown.

FEEDBACK from an episode in the target (in feedback/...):
  episode.json   success (the target's own judge, true or false), how the program ended, robot time and wall-clock time,
                 the number of robot commands, of segmentation calls and of safety stops
  output.txt     what the program printed and, on lines starting with "#", every robot command with its result and the
                 arm's MEASURED tool position and gripper opening
  frames/        frames of the episode's video (an overview camera that is not one of the robot's), evenly over the episode
  shown/         the images the program handed over with robot.show
Not given: the things' true positions, sizes, weights or surfaces; the judge's own measurements; the scenes' files."""


WORLDS_DOC_BI = """A practice world is a Python file that makes the practice simulator's (MuJoCo's) scenes. It defines

    scene(scene, rng) -> scene     `scene` is a dict describing an empty table: fill scene["mjcf"] with a MuJoCo XML string
                                   '<mujoco><worldbody> ... </worldbody></mujoco>' whose bodies are the things on the table,
                                   in the TABLE frame of the interface but in METRES (z = 0 is the tabletop). A body with
                                   <freejoint/> is loose; one without a joint is fixed; a body may hold further bodies on
                                   hinge or slide joints. Name every body w_... . Geoms: boxes, cylinders, spheres,
                                   capsules, ellipsoids (no meshes, no files); rgba; mass or density;
                                   friction="slide spin roll"; and, if you want, solref / solimp / condim / margin for how
                                   a contact gives. A geom with contype="0" conaffinity="0" is seen but touches nothing.
                                   <asset> may hold materials and built-in textures, <default> named classes.
                                   scene["table"] = {"color": [r, g, b], "friction": f} sets the table's colour (0..1)
                                   and its friction (1.0 if you do not). `rng` is a numpy random generator seeded by the
                                   scene's seed: draw from it where things lie and how they are turned (and their
                                   colours), so that every seed gives another scene.
    success(state) -> dict         optional: the world's own check ({"ok": True/False, ...}), from the TRUE state of the
                                   practice world: state.names; state.pos(name) (mm, table frame); state.rot(name) (3x3,
                                   rows; its columns are the body's own axes); state.start[name] and
                                   state.start_rot(name) (at the episode's start); state.speed(name) (mm/s);
                                   state.touching(name) -> {"robot": True/False, "others": [names, "table"]};
                                   state.held(name) -> "left" / "right" / None (squeezed between that gripper's two
                                   fingers now); state.arm("left" | "right") -> {"tcp_mm", "opening_mm", "from_start_mm",
                                   "from_start_deg"}. It is evaluated after every robot action and, as in the target, the
                                   episode ends, a success, as soon as it holds. It is a self-check of the practice world:
                                   the target has its own judge, which you cannot see.

Where two surfaces touch, MuJoCo takes the LARGER of their two friction coefficients (the robot's fingers have 0.5).

Fixed by the harness, whatever the file says: the two arms and how their commands are carried out, the cameras, the head
camera's stand, the tabletop at z = 0, the simulation's step, the episode's 40 s, and the task's text. A world file may
not define robot() or goal(), and its XML may hold nothing but the things on the table (no cameras, lights, actuators,
options, meshes or files). The file is under the same rules as a program (imports, no files)."""


VIDEOS_BI = ("  videos/        the episode's videos: overview_rgb.mp4 (a camera that is not one of the robot's), head_rgb.mp4 and head_depth.mp4\n"
             "                 (the robot's head camera: colour, and depth as a picture, near red, far blue, on a fixed scale of 300 to 1500 mm)\n"
             "  frames/        eight frames of each video, evenly over the episode (a file's name carries the robot time)")


F_TARGET = ("Final evaluation uses the same target simulator as your practice environment, on held-out configurations you cannot access. "
            "Practice uses your own success checks; official development and final evaluations use the independent target judge.")
F_NOT_EXPOSED = "The official verdict is not exposed to you and does not control practice termination."


def target_doc(task, cond=None):
    """What a session is told of the target. cond "F" (oracle-simulator practice): the paragraph that says the target is not
    the practice simulator would contradict what F is; it is replaced by F_TARGET (the user's wording, 2026-11-27)."""
    if task.get("rig") != "x5":
        return TARGET_DOC
    a = TARGET_DOC.index("  frames/        frames of the episode's video"); b = TARGET_DOC.index("  shown/")
    doc = (TARGET_DOC[:a] + VIDEOS_BI + "\n" + TARGET_DOC[b:]).replace(", of segmentation calls and of safety stops", " and of segmentation calls")
    if cond == "F":
        i, j = doc.index("THE TARGET ENVIRONMENT. ") + len("THE TARGET ENVIRONMENT. "), doc.index("\n\nFEEDBACK from an episode in the target")
        assert "It is not the practice simulator" in doc[i:j]
        doc = doc[:i] + F_TARGET + doc[j:]
    return doc


def worlds_doc(task):
    return WORLDS_DOC_BI if task.get("rig") == "x5" else WORLDS_DOC


PRACTICE_DOC = """A practice file is a Python file for practising in the TARGET SIMULATOR. It defines

    scene(scene, rng) -> scene     optional: the scenes you practise on. `scene` arrives as a FRESH scene the harness drew for
                                   this seed (of the kind the program will be tried on):
                                       {"dustpan": {"xy_mm": [x, y], "yaw_deg": a},      a: the direction its mouth faces
                                        "brush":   {"xy_mm": [x, y], "yaw_deg": a},      a: from its handle's end to its head
                                        "cubes":   [{"xy_mm": [x, y], "yaw_deg": a}, x3],
                                        "colours": {"dustpan": [r, g, b], "brush": [r, g, b], "cubes": [[r, g, b], x3]},
                                        "table":   {"name": NAME} or {"rgb": [r, g, b]}}
                                   (millimetres and degrees in the TABLE frame of the interface; a yaw of 0 is +x, 90 is +y;
                                   colours from 0 to 1; NAME one of {tables}). Return it as it is to practise on fresh scenes,
                                   or change it to ask for a scene of your own. `rng` is a numpy random generator seeded by the
                                   scene's seed: draw from it, so that every seed gives another scene.
                                   A scene is built only if it is VALID: exactly three cubes; every thing on the table, at
                                   least 20 mm from its edge and clear of the two arms' bases and of the camera stand's foot;
                                   no thing on or in another (at least 4 mm apart); colours from 0 to 1; a table that is one
                                   of the names or a plain colour. A scene that is not valid, or in which a thing does not
                                   stay where it was asked to be while the scene settles, is not run (it counts).
    success(state) -> dict         optional: YOUR check of a practice episode ({{"ok": True/False, ...}}), from the TRUE state
                                   of the practice scene: state.names (["dustpan", "brush", "cube_0", "cube_1", "cube_2"]),
                                   state.pos(name) (mm, table frame: the thing's own origin), state.rot(name) (3x3 as rows),
                                   state.start (the positions at the start), state.start_rot(name), state.touching(name)
                                   ({{"robot": ..., "others": [...]}}, "table" among the others), state.speed(name) (mm/s),
                                   state.held(name) ("left" / "right" / None: squeezed between the two fingers of one
                                   gripper), state.arm("left" | "right") ({{"tcp_mm", "opening_mm", "from_start_mm",
                                   "from_start_deg"}}). It is evaluated after every action: when it says ok the episode
                                   ends, a success by YOUR check. The target has its own judge.
                                   The official verdict is not exposed to you and does not control practice termination.

Fixed by the harness, whatever the file says: the things themselves (the target's dustpan, brush and three cubes: their
shapes, sizes, weights and surfaces), the robot and its commands, the cameras, the table, the physics, the task's text
and an episode's time. A practice file may not define robot(), goal() or run(). The file is under the same rules as a
program (imports, no files). The program you practise gets nothing of this: in practice as in the target it has the
robot's documented interface, and nothing else."""


def practice_doc(task=None):
    from sweep_blocks import scene_gen
    return PRACTICE_DOC.replace("{tables}", ", ".join(scene_gen.table_names())).replace("{{", "{").replace("}}", "}")


def interface(task):
    """The interface text of a task's robot (a task dict; or, as before, the single-arm tasks' episode length)."""
    if isinstance(task, dict) and task.get("rig") == "x5":
        return (HERE.parent / "sweep_blocks" / "zero_shot_interface.md").read_text().rstrip()
    episode_s = task["episode_s"] if isinstance(task, dict) else task
    return (HERE / "docs" / "interface.md").read_text().replace("{EPISODE_S}", f"{episode_s:.0f}").rstrip()


def system_text(cond, task, cfg, cmd=None, pool=None):
    if cond == "A":
        block = A
    else:
        words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
        n_p, n_s = config.scenes(cfg, cond), int(cfg["target_submissions"])
        subs = "\n".join(SUB[n] for n in WHICH[cond]).format(panel=n_p, subs_max=n_s, panel_w=words.get(n_p, n_p), subs_w=words.get(n_s, n_s), total=n_p * n_s,
                                                             pool=(pool or 1) - 1, fcap=cfg["practice_episode_cap"] + cfg["f_extra_episodes"],
                                                             cap=cfg["build_validation_cap"] if cond == "build" else cfg["practice_episode_cap"])
        block = TOOL.format(cmd=cmd, subs=subs, start=START[cond])
        if task.get("rig") == "x5":
            block = block.replace("(it looks with both cameras, reads the arm's state,\n", "(it looks with the three cameras, reads the arms' states,\n") \
                         .replace("moves the gripper a little)", "moves each arm and gripper a little)") \
                         .replace("result and the arm's measured tool position), frames of the episode's video, and the images\n" + PAD +
                                  "the program showed; and, for the submission, the success rate. You do not get the things'\n" + PAD + "true positions or properties.",
                                  "result and the arm's measured tool position), the episode's videos (an overview in colour;\n" + PAD +
                                  "the head camera in colour and depth) with frames sampled from them, and the images the\n" + PAD +
                                  "program showed; and, for the submission, the success rate. You do not get the things' true\n" + PAD +
                                  "positions or properties.") \
                         .replace("program's log, frames of the video, the images the program showed;", "program's log, the videos and frames of them, the images the program showed;") \
                         .replace("robot time, and the episode's log with the TRUE positions of the practice world's bodies.",
                                  "robot time, the episode's log with the TRUE positions of the practice world's bodies,\n"
                                  "                              and videos and frames of the same kinds as a target episode gives.")
        if cond in ("B", "C", "D", "F"):
            block += "\n\n" + NOTES
        if cond in ("B", "C", "D", "F"):
            block += "\n\n" + target_doc(task, cond)
    text = SHARED.format(interface=interface(task), rules=RULES, task=task["text"], condition=block,
                         panel=config.scenes(cfg, cond if cond in ("B", "C", "D") else "C"))
    if task.get("rig") == "x5":
        a = ("the cameras and\nthe arm's own measurements: the things are the same from episode to episode, but they stand elsewhere and are turned\n"
             "differently. The look you are given")
        assert a in text
        text = text.replace(a, "the cameras and\nthe arms' own measurements: the things have the same shapes and sizes from episode to episode, but they lie elsewhere,\n"
                               "are turned differently and have other colours, on a table of another colour or pattern. The look you are given") \
                   .replace("look/: one look at a target scene, the arm at its start pose.", "look/: one look at a target scene, the arms at their start poses.") \
                   .replace("depth.txt the depth as\n  numbers (mm along the camera's axis on a grid over the image, with the camera's intrinsics and pose); state.txt the\n  arm's state.",
                            "depth.txt the depth as\n  numbers (mm along the camera's axis on a grid over the image, with the camera's intrinsics and pose); state.txt the\n  arms' state.") \
                   .replace("What you need of them you estimate from what the cameras and the arm show.", "What you need of them you estimate from what the cameras and the arms show.")
    return text


FIRST = dict(
    A="Write the program for the task.",
    build="Build the practice world for the task.",
    B="Improve the program for the task, using the target submissions you have.",
    C="Improve the program for the task, using practice in the fixed practice world and the target submissions you have.",
    D="Improve the program for the task, using practice, revisions of the practice world, and the target submissions you have.",
    E="Improve the program for the task, using practice and revisions of the practice world.",
    F="Improve the program for the task, using practice in the target simulator with your own self-check, and the target submissions you have.",
)
# (the user's wording, 2026-11-27: identical in B, C and D; it asks for a record, not for a way of solving the task)
NOTES = ("Keep notes.md as a concise running log of changes, reasons, and observed outcomes, referencing program/world versions and feedback\n"
         "rounds. Before calling finish, write summary.md: what you tried, what the feedback showed, which program version you selected and\n"
         "why, how it works, and what remains unresolved. Distinguish observed results from hypotheses.")
CONTINUE = "You ended your turn but the session is not finished. Go on, or end it with the `finish` sub-command."
# (A: a writer that ended without the file, as one zero-shot writer did on 2026-11-27 after 512 000 tokens of thinking)
CONTINUE_A = "program.py has not been written yet. Write the program to the file program.py in your working directory now."
