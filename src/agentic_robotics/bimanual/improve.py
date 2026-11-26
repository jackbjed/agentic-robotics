"""A development session: a model improves a program run(robot, task) for a bimanual task (docs/bimanual.md) by
practising in the simulator, until it submits a version or its budget is spent. The submitted program is then
evaluated without the model (practice.py suite).

    python -m agentic_robotics.bimanual.improve --env sim_bimanual_grippers --task insert_cylinder --model astra \\
           --start runs/bimanual/x/program.py --segmenter sam3 --budget-usd 20 --out runs/bimanual/y           (PAID)
    python -m agentic_robotics.bimanual.improve ... --out runs/bimanual/y --resume          (a stopped session: go on)
    python -m agentic_robotics.bimanual.practice suite --env sim_bimanual_grippers --task insert_cylinder \\
           --program runs/bimanual/y/program.py --seeds 0-2,4-10 --segmenter sam3 --video --out runs/bimanual/y/eval

The session is one conversation with tool calls (TOOLS): the model saves versions of the program (write_program,
edit_program), runs a version on practice episodes (run_episodes: seeds 100 to 999, each run in the program sandbox,
practice.py), reads what happened (the judge's verdict, the program's output, a trace of its commands with the
objects' TRUE positions, images the program hands over with robot.show, the overview camera's last frame) and
submits a version (submit). The program itself gets nothing more than in evaluation.

The budget is hard: before each model call its worst case (the whole conversation as uncached input, a full reply)
is reserved against the session's cap in the spending ledger (perception/vlm.py), and the reply's length is cut to
what is left; when a useful reply no longer fits, the session ends and the last submitted version is the result.

A task with varied scenes (insert_varied: SceneSession) is practised on scenes of the model's own (define_scenes,
within the training limits), with the simulation's truth and judge open to the practising program (robot.truth,
robot.judge: not in an evaluation), with or without a self-evaluation set.

Picking from clutter (--task pick_clutter --levels medium hard: ClutterSession) is one program for several levels:
the model draws scenes from the levels' environments (sample_scenes: the seeds below CLUTTER_SEEDS, the evaluation's
are not among them) or makes its own (define_scenes: heaps of given and drawn objects, assets made of parts, objects
placed by hand; clutter.build), runs a version on up to SET_N of them at once (run_set), and its program may ask
the model itself (robot.ask, practice.ASK: at most four calls an episode, their latency is robot time, their cost is
part of the session's budget).

    python -m agentic_robotics.bimanual.improve --env sim_bimanual_grippers --task pick_clutter --levels medium hard \\
           --model astra --segmenter sam3 --budget-usd 40 --eval-episodes 100 --out runs/bimanual/z            (PAID)

--out DIR gets versions/vNN.py, runs/vNN/sSEED/ (each practice episode: practice.py's files), transcript.md (what
the model said, called and got), session.jsonl (the same, as data, with every turn's cost), state.json + pending.pkl
(to resume), result.json and program.py (the submitted version).
"""
from __future__ import annotations

import argparse
import ast
import json
import pickle
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import clutter, clutter_one_arm, practice, scenes
from .tasks import TASKS, Episode
from .tools import Toolbox, openai_items, openai_tools, text
from .write import VARIES, instructions

SEEDS = (100, 999)                                              # the practice episodes
AT_ONCE = 6                                                     # episodes of one run_episodes call
PRICES = {"in": 12.5, "cached": 1.0, "out": 50.0}               # $ per million tokens, if the model client has none
MIN_REPLY = 4000                                                # tokens: the session ends when a reply this long no longer fits
IMAGE_TOKENS = 800                                              # an image's input tokens (an upper estimate)

_SESSION = """
You develop this program in a session with a simulator of the robot to practise in, through these tools:

write_program, edit_program   save a new version of the program (v1, v2, ...{v0}). Any version can be run, so a
                              version can also be an experiment (a program that only looks and reports).
run_episodes                  run a version on practice episodes: seeds {lo} to {hi}, each seed one layout. You get, for
                              each episode: the judge's verdict, how the program ended, the robot time, what the
                              program printed, and the objects' TRUE positions at the start and at the end (the
                              simulator's state, which the program itself never gets){points}.
read_output                   more of an episode's output: what the program printed and, on lines starting with "#",
                              a trace of every robot command: robot time, the command, its result, that arm's TRUE
                              tool position (and opening), and after NOW the TRUE position of each object that
                              moved. Compare what the program measured with the truth there.
view_images                   the overview camera's picture of the end of an episode, and the images the program
                              handed over with robot.show
read_program                  a version's text, with line numbers
submit                        name the version that is your result

In a program, robot.show(label, image) hands an image to you (H x W x 3 uint8 RGB, or H x W; at most {shown} an episode;
no robot time): for instance a camera frame with what the program detected drawn on it (cv2). An episode's
randomness (sensor noise, calibration error) is fixed by its seed: the same version on the same seed gives the
same result, and run_episodes keeps results. {rules}

Your result is the version you submitted last. After the session it is run, without you, on {n_eval} episodes with new
layouts of the same kind (not practice seeds), and judged by how many succeed and then by the robot time it takes. So make the
program reliable over many layouts first, then fast.

The session's budget is ${budget:.0f} of model usage: your replies (the thinking included) and the conversation so far, which
is read again at every turn, are what costs; simulation is free. Every tool result ends with what is spent. So run
several seeds at once, read outputs selectively, and prefer edit_program to writing the whole program again. Submit
a version as soon as it is better than the one submitted before: when the budget runs out the session ends without
warning and the last submitted version counts{fallback}. When you are satisfied, call submit with final = true.
"""

_FIRST = """
{start}
The first look of practice episode {seed} (the arms at their start poses; the three cameras' colour images):"""

_V0 = """
v0, the starting program, was written earlier for this task in one attempt, without any test:

```python
{code}```
"""
# ---- a session on a task with varied scenes (scenes.py): from nothing, the scenes set up by the model
ASK_VARIED = ("You should write a skill that solves this task (i.e., picking the cylinder and threading it onto the stick so that "
              "the stick passes through its hole).\n\nThe skill should be robust to many variations, such as positions and poses of "
              "the stick and cylinder, size and dimensions of the cylinder and stick, colors of the objects, their densities, and "
              "the presence of distractors.")
ASK_TABLES = (" It should also be robust to variations in the height of the table with respect to the robot, and to changes in "
              "the table's appearance (i.e., its color and pattern).")

_SCENES = """
You develop this program (the skill) in a session with a simulator of the robot to practise in. There is no program
yet, and nothing is told about the objects: the program has the cameras. The practice is yours to set up: you
choose the scenes, and in practice the simulator's TRUE state is open to you, to test and to measure with. The tools:

write_program, edit_program   save a new version of the program (v0, v1, ...). Any version can be run, so a
                              version can also be an experiment (a program that only looks and reports).
define_scenes                 set up practice scenes (see below) -> their names (s1, s2, ...) and what is in each
list_scenes                   what is in scenes: the objects' sizes, colours, densities and places
run_episodes                  run a version on scenes, one episode a scene. You get, for each episode: the judge's
                              verdict (the simulator's success check, on the TRUE state at the end), how the program
                              ended, the robot time, what the program printed, and the objects' TRUE positions at the
                              start and at the end{points}.
{eval_tool}read_output                   more of an episode's output: what the program printed and, on lines starting with "#",
                              a trace of every robot command: robot time, the command, its result, that arm's TRUE
                              tool position (and opening), and after NOW the TRUE position of each object that
                              moved. Compare what the program measured with the truth there.
view_images                   the overview camera's picture of the end of an episode, and the images the program
                              handed over with robot.show
read_program                  a version's text, with line numbers
submit                        name the version that is your result

The scenes. A scene has one cylinder (standing on the table, its hole upwards), one stand with its stick, and up to
{dis_n} other objects (distractors). define_scenes takes scenes as JSON objects in which every field can be left out; what
is left out is drawn at random within its range, by the scene's "seed" (the same object gives the same scene):

  {{"seed": 3,
   "cylinder": {{"diameter_mm": {cyl_d}, "height_mm": {cyl_h}, "color": "red", "density": {density}, "xy_mm": [x, y]}},
   "stand": {{"stick_diameter_mm": {stick_d}, "stick_height_mm": {stick_h}, "board_mm": [side, side, thickness],
             "color": "wood", "stick_color": "wood", "density": {density}, "xy_mm": [x, y], "yaw_deg": {yaw}}},
   "hole_clearance_mm": {clearance},{table_spec}
   "distractors": 2}}        (a number, or a list of {{"shape": "box" | "cylinder", "size_mm": [side, side, height] |
                            [diameter, height], "color": ..., "density": ..., "xy_mm": [x, y], "yaw_deg": ...}})

Lengths in mm, densities in kg/m3 (the weight follows from the size). The hole's diameter is the stick's plus
hole_clearance_mm ({clear0:g} if left out, and {clear0:g} in the final evaluation); the cylinder's wall is at least {wall:g} mm thick.
stick_height_mm is the stick's height above the board; the board's sides are {board}, its thickness {board_t}. A
distractor's sides or diameter are {dis_size}, its height {dis_h}. Colours: {colours}. xy_mm is where the
object's centre stands on the table: the cylinder and the stand within x {x}, y {y}, with at least {gap} mm
of free table between them; a distractor within x {dis_x}, y {dis_y}, at least {dis_gap} mm from the cylinder and
from the stand and {dis_dis} mm from another distractor (between the circles around their outlines); nothing stands on the
ruler that is fixed to the table (y {ruler}). A scene outside these limits is refused, with the reason. The
seed also fixes the episode's sensor noise and calibration error: a version on a scene gives the same result every
time, and run_episodes keeps results.
{table}
In a program, robot.show(label, image) hands an image to you (H x W x 3 uint8 RGB, or H x W; at most {shown} an episode;
no robot time): for instance a camera frame with what the program detected drawn on it (cv2). In practice episodes a
program can also read the simulator: robot.truth() -> {{name: [x, y, z]}} (mm), the objects' TRUE positions now (the
names and points of run_episodes' results), and robot.judge() -> the judge's verdict on the state as it is now (a dict;
"ok": success). Both cost no robot time, and both return None in the final evaluation: they are for measuring and
checking (how far is a measurement from the truth? did a step do what it should?), not for doing the task. {rules}

Your result is the version you submitted last. After the session it is run, without you, on the final evaluation:
{n_eval} episodes with new scenes, in which the program has the cameras and nothing of the simulator's state, judged by how
many succeed and then by the robot time it takes. Half of those scenes are drawn at random within the limits above{like}.
The other half go beyond these limits, in ways that cannot be set up here: sizes outside the ranges above, other
colours, other kinds of distractors and more of them, lighter and heavier objects, and other placements (objects
closer together, or elsewhere on the table within the arms' reach).{table_eval} So make the program reliable over many different
scenes first, without leaning on what only happens to hold in the scenes here; then fast.

The session's budget is ${budget:.0f} of model usage: your replies (the thinking included) and the conversation so far, which
is read again at every turn, are what costs; simulation is free. Every tool result ends with what is spent. So run
several scenes at once, read outputs selectively, and prefer edit_program to writing the whole program again. Submit
a version as soon as it is better than the one submitted before: when the budget runs out the session ends without
warning and the last submitted version counts (nothing if none). When you are satisfied, call submit with final = true.
"""

_TABLE_SPEC = """
   "table": {{"height_mm": {h}, "color": "blue", "pattern": "checker", "color2": "white", "size_mm": 40, "angle_deg": 30}},"""

_TABLE = """
The table. The frame is fixed to the robots, and "the table surface at z = 0" holds for the usual table: plain dark
grey, its top at z = 0. A scene without "table" has the usual table. A scene with "table" (every field can be left
out, and is then drawn at random; "table": {{}} is a table drawn at random) has another table: its top is at
z = height_mm ({h}; negative: below the usual height), and the ruler and every object are that much higher or
lower, while the robots and the cameras on them stay where they are. The table's colour is one of {colours};
its pattern one of {patterns}, in the colour color2: size_mm is the checker's square
({checker}), the stripes' width ({stripes}) or the speckles' radius ({speckle}), and angle_deg ({angle}) turns the pattern on
the table.
"""

_TABLE_EVAL = """ In those {n_eval} the table is the
usual one. In {n} more episodes, with objects beyond the limits as well, the table is another one: its top higher or
lower with respect to the robots (also further than can be set up here), of another colour and pattern (also colours
and patterns that cannot be set up here)."""

_EVAL_TOOL = """run_eval_set                  run a version on this session's self-evaluation set: the scenes E0 to E{last}, drawn at
                              random within the limits below and fixed (their names also work in list_scenes,
                              run_episodes, read_output and view_images). One line per episode.
"""

# ---- real episodes back into a session (--continue-from, --real-feedback; --real-scenes: scenes like the real rig)
ASK_REAL = ("\n\nVersions of this skill have now been run on the REAL robot. Improve the skill so that it does the task on the real "
            "robot, without losing what it does in simulation.")

_REAL = """
The real robot. Versions of the program have been run on the real robot, by an operator who watched each episode and
gave its verdict. Those episodes are yours to study:

list_real_episodes            without `episodes`: the real robot (what it is, and how it answers a program's commands)
                              and one line per episode (the version that ran, the operator's verdict and note, the
                              setup, which setting of the moves until contact it ran with). With `episodes` (at most 4
                              names): each one in full: its moves until contact as the program saw them and as the
                              arm's own server recorded its guarded lines, its looks, its model calls (robot.ask:
                              the question, the answer), and the names of its images
read_real_output              a real episode's output: what the program printed and, on lines starting with "#", every
                              robot command with its result and the arm's MEASURED tool position after it (the real
                              robot has no truth)
view_real_images              images of a real episode: look_NN_<camera> (the colour image that camera gave the
                              program at its NN-th look), look_NN_<camera>_depth (that look's depth as a picture:
                              near red, far blue, the scale on top; black = NO depth), look_NN_<camera>_nodepth (the
                              colour image with the pixels that had no depth in magenta), shown_... (what the
                              program handed over with robot.show), asked_NN_k (the k-th image of the program's
                              NN-th robot.ask), and video_scene_<t>s / video_wrist_<t>s (frames of the two cameras'
                              videos, t the rig's time)
read_real_depth               the depth a real camera gave at a look, as numbers: the values (mm along the camera's
                              axis, as a Frame's depth; 0 = no depth) on a grid over a window of pixels, and the
                              window's statistics; with the camera's intrinsics

You cannot run anything on the real robot in this session: the practice is in the simulator, as before.{sim}

Your result is the version you submitted last. The operator will run it on the real robot again, on scenes like
those of the real episodes (the same kinds of objects, set out anew), and it is judged there first. It is also run
on the simulated final evaluation described above, where it should do no worse than v{sub} did.
"""

_WORLDS = """
Worlds: the simulator is yours to change. A WORLD is a Python file of yours that changes what the simulator builds
and how its robot answers, in the episodes you run in it: your own model of what the real robot's episodes show and
the plain simulator does not have (other objects, other sensors, another arm). Tools:

write_world, edit_world       save a new version of a world (w0, w1, ...), as write_program and edit_program do
                              for the skill; a world can also be an experiment
read_world                    a world's text, with line numbers
run_episodes                  with "world": k the episodes run in world wk; without it (or null, or -1), in the plain simulator. A
                              version's results in a world are kept apart (the scene is then named s3@w1)
read_output, view_images      take "world" too

A world's file defines one or both of these functions:

def scene(scene, rng):
    # -> the scene the simulator builds. `scene` is the scene as define_scenes made it, a dict (list_scenes names its
    # fields; colours are "rgb": [r, g, b], and the cylinder has "hole_mm"). Change it and return it. It is NOT held
    # to the limits of define_scenes, only to what the simulation can hold (lengths in mm): the cylinder {cyl_d}
    # across, {cyl_h} tall, its hole {hole}; a stick {stick_d} across, {stick_h} above its board; the board's sides
    # {board}, its thickness {board_t}; any other size {size}; densities {density} kg/m3; places x {x}, y {y}.
    # It can also have what define_scenes has not:
    #   scene["cylinder"]["sides"] = 6      its outline and its hole are polygons of that many sides (absent: round)
    #   scene["cylinder"]["z_mm"] = 20      its bottom stands that high above the table: on something, which you
    #                                       put there as an extra (the same for the stand and for a distractor)
    #   scene["stand"]["sticks"] = 3; scene["stand"]["stick_spacing_mm"] = 55
    #                                       several sticks in a row along the board's first side; the cylinder on
    #                                       any one of them is a success; truth() has stick_top, stick_top_1, ...
    #   scene["distractors"]                up to {distractors}, of the shapes box, cylinder, ball, pill, L, steps
    #   scene["extras"] = [{{"name": "pad", "fixed": True, "xy_mm": [x, y], "z_mm": 0, "yaw_deg": 0, "density": 600,
    #                       "parts": [{{"type": "box", "size_mm": [75, 75, 20], "pos_mm": [0, 0, 10],
    #                                  "euler_deg": [0, 0, 0], "rgb": [235, 235, 230]}}]}}]
    #                                       bodies of your own (up to {extras}), each made of up to {parts} parts: box
    #                                       (three sides), cylinder and capsule (diameter, length along its own
    #                                       z), ball (diameter). pos_mm is the part's centre from the extra's
    #                                       reference point, which stands at xy_mm, z_mm above the table.
    #                                       "fixed": True: part of the table, it never moves; False: a free object
    #                                       (truth() names it extra_k)
    #   o["friction"] = 0.3                 on the cylinder, the stand, a distractor or an extra: its sliding friction ({friction}; the
    #                                       simulator's own: 1.0, and 1.5 for the fingers' pads)
    #   o["contact"] = {{"time_ms": 5, "damping": 1, "impedance": [0.95, 0.99, 0.5]}}
    #                                       on the same: how hard its contacts are, in MuJoCo's terms: solref's time
    #                                       constant ({contact_ms} ms) and damping ratio, and solimp's d0, d1 and
    #                                       width (mm); each can be left out; the simulator's own: 20, 1,
    #                                       [0.9, 0.95, 1]. A shorter time and a narrower width: a harder contact,
    #                                       which gives way less. An object that says friction or contact takes
    #                                       precedence in its contacts: with the table, the fingers and objects that
    #                                       say neither, the contact has ITS friction and hardness (the simulator
    #                                       otherwise takes the larger friction of the two); between two objects
    #                                       that both say them: the larger friction, a mix of the hardness.
    #   a part {{"type": "hull", "points_mm": [[x, y, z], ...], "pos_mm": [0, 0, 0], "euler_deg": [0, 0, 0], "rgb": [r, g, b]}}
    #                                       among an extra's parts: the convex hull of {hull} points
    #                                       given in the part's own frame (a convex mesh): a wedge, a prism, a
    #                                       tapered or rounded body. A shape that is not convex is several hulls.
    #                                       Its mass is its density times the hull's volume.
    #   scene["arm"] = "left"               the left arm alone, within the real arm's safety limits; the right arm
    #                                       stands parked: a move of it to its parking place (y below -280) is
    #                                       answered as done, any other use of it is an error
    #   scene["mjcf"] = "<mujoco> ... </mujoco>"
    #                                       bodies of your own in MuJoCo's own language (MJCF), for what the fields
    #                                       above cannot describe: anything rigid that MJCF can, e.g. a drawer on a
    #                                       slide joint, a door or a lid on a hinge, a lever, a button on a spring,
    #                                       a latch, a box with a sliding lid, a rack, a mesh of your own. The text
    #                                       has a <worldbody> with your bodies and, as needed, <asset> (meshes by
    #                                       their vertex attribute, materials, built-in textures), <default>
    #                                       (classes of your own), <equality>, <tendon>, <contact> (pair, exclude).
    #                                       - bodies in bodies, each with geoms (box, sphere, capsule, cylinder,
    #                                         ellipsoid, mesh), joints (slide, hinge, ball, free; range, damping,
    #                                         frictionloss, stiffness, springref, armature), sites, lights; a body
    #                                         without a joint is fixed where it is;
    #                                       - MJCF's units: metres, kilograms, radians. Positions are in the
    #                                         robot's frame (the one of every position here), z = 0 at the table's
    #                                         top;
    #                                       - every name, and every reference to a name, starts with "w_" (w_drawer,
    #                                         w_slide): the simulator's own bodies cannot be named or changed;
    #                                       - soft and loose things are MuJoCo's <composite> (this is MuJoCo 2.3.7: no
    #                                         flex), each with a prefix of its own (prefix="w_sponge"): type box,
    #                                         cylinder or ellipsoid (a soft body: a sponge, a cushion; inside a body
    #                                         that has a <freejoint/>), grid (a cloth: a towel; in the worldbody, placed
    #                                         by its offset), cable (a rope, a wire), particle (loose grains). Its
    #                                         count and spacing, the <geom> of its elements (size, mass, friction) and
    #                                         <joint kind="main" .../> or <tendon .../> set its size, weight and
    #                                         stiffness; <skin .../> gives it a surface in the images. truth() gives a
    #                                         composite as ONE entry under its prefix, the centre of its bodies
    #                                         (state.points(prefix) in success(): each body's place). Many loose things
    #                                         of a kind (beans, crumbs) can also be bodies of their own (w_bean_0, ...);
    #                                       - not possible: files (a mesh is its list of vertices), actuators, sensors,
    #                                         plugins, cameras, the model's settings; at most 450 bodies, 900 geoms and
    #                                         1400 degrees of freedom, the composites' included (they slow the
    #                                         simulation: a cloth of 13 x 13 nodes runs slower than real time);
    #                                       - a geom that says nothing of its contacts has MuJoCo's defaults
    #                                         (friction 1, condim 3); the fingers' pads have friction 1.5.
    #                                       MuJoCo compiles the text before the episode; if it does not, its error
    #                                       message is the episode's result. truth() then also has each named body
    #                                       of yours (its frame's origin, mm) and each named slide or hinge joint
    #                                       (mm, degrees), and the trace shows them when they move. The robot's
    #                                       moves take your bodies for obstacles like any other. A move stops at
    #                                       40 N on the arm (SAFETY STOP), a stop_on_contact move at 4 N: a
    #                                       mechanism the robot is to work must give way to less (a drawer: a
    #                                       frictionloss of a few N).
    #   scene["joints"] = {{"w_slide": 40}}   where slide and hinge joints of your MJCF start (mm, degrees; left out:
    #                                       at MJCF's 0)
    #   scene["cameras"] = {{"depth": {{"noise_px": 0.1}}, "scene": {{"depth": {{"table_holes": None}}}},
    #                       "left_wrist": {{"pose_error": {{"shift_mm": 3, "turn_deg": 1}}}}}}
    #                                       the cameras. "depth": the depth cameras' error model, for all of them,
    #                                       or under "scene", "left_wrist", "right_wrist" for that one. Its
    #                                       parameters, each of which can be left out (the simulator's own value):
    #                                         noise_px (0.18): the stereo matching noise in pixels of disparity;
    #                                           the depth's noise is noise_px * z^2 / (focal_px * baseline_mm)
    #                                         noise_smooth (0.9), noise_smooth_px (8): the part of the noise that
    #                                           is smooth over that many pixels (the rest is pixel to pixel)
    #                                         scale (0.005): a depth scale error per camera and episode, up to it
    #                                         fatten_px (4), fatten_step_mm (10): a nearer surface spreads that
    #                                           many pixels over a farther one where the depth steps by more
    #                                         flying (0.03), flying_step_mm (20): the part of the pixels at such a
    #                                           step that get a depth between its two sides
    #                                         dark_below (0.2), dark_holes (0.08), blob_px (4): objects darker than
    #                                           that (0..1) lose that part of their pixels, in blobs of that size
    #                                         table_holes ([0.2, 1100, 1.0, 0.6]) or None: the scene camera on the
    #                                           table: the part without depth up to a distance (mm), how much
    #                                           more per metre beyond it, the most
    #                                         dropout (0.002): single pixels without depth
    #                                         left_band_px (30) or None: the blind band at the image's left is
    #                                           focal_px * baseline_mm / z + this many pixels wide
    #                                         shadow (True): no depth where the second imager does not see
    #                                         min_mm (275): nothing nearer than this
    #                                         focal_px (625), baseline_mm (55): the depth sensor's own
    #                                       "ideal": True under a camera: its depth as the simulator renders it,
    #                                       without any of this (for a model of your own, in robot()'s look).
    #                                       "pose_error" under a camera: its TRUE pose differs from the one its
    #                                       frames give (eye, R) by {{"shift_mm": 5, "turn_deg": 1}} in a direction
    #                                       drawn per episode (the simulator's own: scene 5 mm and 1 deg, wrists
    #                                       3 mm and 1 deg), or with three numbers each by exactly that.
    #                                       frame.depth_report(), in a program, gives numbers that describe a
    #                                       frame's depth as a sensor's output: the part with depth, the blind
    #                                       band, the holes, the noise by distance. read_real_depth gives the
    #                                       same numbers for a real look: with both, a world's depth model can be
    #                                       fitted to the real cameras and checked against looks it was not
    #                                       fitted to.
    #   scene["without"] = ["cylinder", "stand", "distractors"]
    #                                       what is NOT on the table in this scene (any of the three): for a task of
    #                                       your own (goal and success, below)
    # rng: a numpy Generator, the same for the same scene.
    return scene

def robot(robot, rng, kit):
    # -> the robot the PROGRAM gets in this world. `robot` is the simulator's own (the interface above). kit.Robot is
    # a class that passes every call on to it (as self.robot): subclass it, override what your world does
    # differently, return an instance. kit.frame(f, rgb=None, depth=None) is the Frame f with other pixels (depth in
    # mm, nan where there is none).
    class Real(kit.Robot):
        def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
            r = self.robot.move(arm, xyz, dir_a, dir_b, stop_on_contact)   # or another move, another answer
            self.robot.wait(2.0)                                           # e.g. what a real move costs
            return r
        def look(self, cameras=None):
            out = self.robot.look(cameras)
            for name, f in out.items():
                d = f.depth.copy(); d[d < 280] = float("nan")             # e.g. what a real camera does not see
                out[name] = kit.frame(f, depth=d)
            return out
    return Real(robot)

Everything a program calls can be overridden: look, state, move, turn, home, wait, time_s, set_gripper, segment,
show, truth, judge. What your world's robot returns is what the program sees; what the simulator really did is
what the "#" trace of output.txt shows and what the judge judges. A world keeps the program's rules (the same
imports, no files); what it prints goes to output.txt. If a world fails (an exception, a scene outside the bounds,
a scene the simulator cannot build) the program is not run and the episode's result says "world: ...".

A world can also set a TASK OF ITS OWN, with these two functions together:

def goal(scene, rng):
    # -> the task's text: what the program gets as `task` in this world, in place of the simulator's. Say in it what
    # is asked and what a program needs to know of the scene, as the task text above does for its task: the program
    # has only this text and the robot.
    return "Task: open the drawer of the cabinet on the table by at least 80 mm, then let go of it. ..."

def success(state):
    # -> True or False, or a dict with "ok" and whatever else you measured (the result shows it): YOUR check of that
    # task, called on the TRUE state when the program has ended and the scene has been at rest for 1 s. state has:
    #   state.truth()                     the TRUE positions (mm) and your joints (mm, degrees), as robot.truth()
    #   state.start                       the same at the start of the episode
    #   state.pos(name), state.rot(name)  a body's position (mm) and its axes (3 x 3: the columns are its x, y, z)
    #   state.joint(name)                 a slide or hinge joint of your MJCF (mm, degrees)
    #   state.points(name)                each body's place (mm) of a composite (a soft body, loose particles)
    #   state.speed(name)                 a body's speed (mm/s)
    #   state.touching(name)              {{"robot": {{arm: [parts]}}, "others": [bodies]}}: what touches that body
    #   state.held(name)                  a hand touches it and nothing else does
    #   state.simulator()                 the simulator's own judge of its own task (a dict with "ok")
    #   state.scene, state.ended, state.time_s   the scene, how the program ended, the robot time
    # Bodies are named as in truth().
    open_mm = state.joint("w_slide")
    return {{"ok": open_mm >= 80 and not state.touching("w_drawer")["robot"], "open_mm": open_mm}}

In such a world an episode's SUCCESS or FAILED is your success(): the result says so and gives what it returned (the
simulator's own verdict stays beside it, as simulator_ok), and robot.judge() in practice returns your check of the
state at that moment. Nothing checks your check but you, and the people who read the results rely on it. So make it
strict (the end state that the task's text asks for, measured on the true state, the robot's hands off unless the
task is to hold), and test it both ways: a program that does nothing must FAIL, and the final image of a success
must show the task done. Say in the world's note what its task is.

A world is YOUR claim about the real robot, and nothing checks it but you. A world is good when the version that
ran on the real robot does in it what it did there: the same failures at the same steps, about the same times,
the same kind of numbers in its output. A world that only makes practice harder teaches nothing. So build it from
the real episodes (their outputs, images, depth, the arm's records), check it against them, and say in a world's
note what it models and what it leaves out. The skill is judged on the real robot and in the plain simulator, never
in a world of yours: it must not come to rely on anything that only your world has. (A task of your own exists only in
your world: its results are reported as that world's.)
"""

_SUMMARY = """
When you are done, your final submit (final = true) must carry `summary`: your own summary of this session, for the
people who run the robot. Say what you learned from the real episodes (name them), what you changed in the skill
and why, what you tested and how it came out, what you expect on the real robot, and what stays uncertain. Plain
text, a page or so.
"""

_REAL_SIM_SAME = """ The simulator
is the one this skill was developed in: where the real robot behaves differently, the simulator does not show it."""

_REAL_SIM_SCENES = """ A scene can
now be set up "like the real robot": add "real": true to it in define_scenes. In such a scene
- only the left arm moves, within the real arm's safety limits (the right arm stands parked: a move of it to its
  parking place, y below -280 mm, is answered as done; the cameras are "scene" and "left_wrist");
- the cylinder and the stand are within x {x}, y {y};
- the stand can carry several sticks in a row along its board's first side: "stand": {{"sticks": {sticks},
  "stick_spacing_mm": {spacing}, "board_mm": [{board_long}, {board}, thickness]}} (left out: drawn at random). The
  cylinder on any one of the sticks is a success; truth() names the other sticks' tops stick_top_1, stick_top_2;
- three effects that were measured on the real robot are on: false_stops (a move until contact can stop and answer
  "contact" with nothing touched, most often within its first millimetres), scatter (an ordinary move ends a few
  millimetres off the pose asked for, as the arm's measured position shows) and thin_depth (a wrist camera can give
  no depth on an object that is thin in its image and stands well in front of what is behind it).
  "real": {{"false_stops": false}} (and likewise scatter, thin_depth) switches one off, to see what it does.
These effects are models made by hand from few real episodes: they are not the real robot."""

# ---- a session on a task of the model's own making (--own-task): the task is a world's goal() and success()
ASK_OWN = """THE SKILL TO MAKE: {ask}

Nothing is given for it but the robot, its cameras and the table: no objects, no assets, no test cases, no task in
the simulator. You make all of that yourself, as WORLDS (described below): what is on the table (bodies, soft
bodies, loose things, marks), the task's text, and the check that says whether an episode did the task. Then you
write the skill: one program run(robot, task) that does the task in your worlds' episodes from the cameras and the
arm's own measurements only, as every program here. The robot is the left arm alone; the other arm is not to be
used, and the simulator refuses it."""

_OWN_SCENES = """
You develop this skill in a session with a simulator of the robot to practise in. The simulator has the robot, its
cameras and the table, and nothing else. There is no program yet. What is on the table, what an episode asks and how
it is judged are yours to make, as worlds (below); in practice the simulator's TRUE state is open to you, to test
and to measure with. The tools:

write_program, edit_program   save a new version of the program (v0, v1, ...). Every version is kept, and any
                              version can be run, so a version can also be an experiment (a program that only
                              looks and reports).
new_scenes                    more scenes (s1, s2, ...). A scene here is an empty table with a seed: in a world of
                              yours it is one episode, which the world's scene(scene, rng) fills by its rng (the
                              same for the same scene and world). The seed also fixes the episode's sensor noise
                              and calibration error: a version on a scene in a world gives the same result every
                              time, and run_episodes keeps results.
write_world, edit_world, read_world   your worlds (w0, w1, ...): see below
run_episodes                  run a version on scenes in a world ("world": k), one episode a scene (at most {at_once} new
                              ones a call, run at the same time). You get, for each episode: the verdict of the
                              world's success() on the TRUE state at the end, how the program ended, the robot
                              time, what the program printed, and the TRUE positions of the world's bodies at the
                              start and at the end.
read_output                   more of an episode's output: what the program and the world printed and, on lines
                              starting with "#", a trace of every robot command: robot time, the command, its
                              result, the arm's TRUE tool position (and opening), and after NOW the TRUE position
                              of each body that moved. Compare what the program measured with the truth there.
view_images                   the overview camera's picture of the end of an episode, and the images the program
                              handed over with robot.show
list_scenes                   the scenes there are
read_program                  a version's text, with line numbers
submit                        name the version that is your result

In a program, robot.show(label, image) hands an image to you (H x W x 3 uint8 RGB, or H x W; at most {shown} an episode;
no robot time): for instance a camera frame with what the program detected drawn on it (cv2). In practice episodes a
program can also read the simulator: robot.truth() -> {{name: [x, y, z]}} (mm), the TRUE positions now of the world's
bodies (and its joints), and robot.judge() -> your world's success() on the state as it is now (a dict; "ok":
success). Both cost no robot time, and both return None when your result is evaluated: they are for measuring and
checking (how far is a measurement from the truth? did a step do what it should?), not for doing the task. {rules}
"""

_OWN_WORLDS_HEAD = """
Worlds. A WORLD is a Python file of yours that says what the simulator builds on the table, what an episode asks and
how it is judged, and, if you want, how the robot answers. write_world and edit_world save a new version of a world
(w0, w1, ...), as write_program and edit_program do for the skill; read_world gives its text. Results are kept per
version, scene and world (the scene is then named s3@w1). Several worlds can stand for several kinds of test (a
plain one, a hard one, one for a single question). A world's file defines:

def scene(scene, rng):
    # -> the scene the simulator builds. `scene` is the empty table, a dict: leave what is in it and add
"""

_OWN_WORLDS_TABLE = """    #   scene["table"] = {{"height_mm": 15, "rgb": [205, 165, 115], "pattern": "plain"}}
    #                                       another table: its top at z = height_mm in the robot's frame ({h}; the
    #                                       usual one is at 0: the robot and its cameras stay where they are, the
    #                                       table and what is on it are that much higher or lower, and positions in
    #                                       your MJCF count from the table's top); its colour ("rgb", or "color":
    #                                       one of {colours}); "pattern": plain, or
    #                                       {patterns} in "rgb2" / "color2", with "size_mm" (the
    #                                       checker's square, the stripes' width, ...) and "angle_deg". What is left
    #                                       out is the usual table's: plain dark grey at 0.
    # rng: a numpy Generator, the same for the same scene and world: draw from it everything that varies.
    return scene

"""

_OWN_RESULT = """
No real episode of this task exists yet: nobody has measured the real things for you. So a world here is your best
account of a real table: give the bodies the sizes, weights and frictions that such things have, vary with the rng
what you cannot know and what the task says can differ, and say in a world's note what it holds, what varies and
over which ranges.

Your result is the version you submitted last TOGETHER WITH THE WORLD that sets the task: the final submit names it
("world": k; it must have goal() and success()). After the session that version is run in that world, without you,
on scenes with new seeds, where the program has the cameras and nothing of the simulator's state. The episodes are
filmed, and the people who run the robot judge them by your success() first and then by what they see; every
version you saved can be run the same way, to see how the skill came about. After that the skill is to run on the
REAL robot, on a real table with real things. So:
- the world must cover what the task asks the skill to be robust to, widely, and not be easier than a real table;
- its check must be strict and honest (the end state the task's text asks for, measured on the true state): a task
  that the world makes easy is a poor result however many episodes succeed;
- the program must not lean on what only your world has: exact sizes, colours, the names of bodies, the truth.
So make the skill reliable over all of that first; then fast.

When you are done, your final submit (final = true) must carry `summary`: your own summary of this session, for the
people who run the robot. Describe the task as you built it (the bodies, their sizes, weights and frictions, what
varies and over which ranges, what success() measures), what the skill does step by step, what you tested and how it
came out (by world and by kind of variation), where it still fails, what you expect on the real robot, what should
be measured there first, and what stays uncertain. Plain text, a page or two.

The session's budget is ${budget:.0f} of model usage: your replies (the thinking included) and the conversation so far, which
is read again at every turn, are what costs; simulation is free. Every tool result ends with what is spent. So run
several scenes at once, read outputs selectively, and prefer edit_program and edit_world to writing a whole file
again. Submit a version (with its world) as soon as it is better than the one submitted before: when the budget
runs out the session ends without warning and the last submitted version counts (nothing if none). When you are
satisfied, call submit with final = true.
"""

# (a from-nothing task that is continued with real episodes: the sentence that none exists is left out)
_OWN_NO_REAL = ("No real episode of this task exists yet: nobody has measured the real things for you. So a world here is your best\n",
                "A world here is your best\n")

_FIRST_OWN = """

There is no program and no world yet.

The first look at the empty table (the arm at its start pose; the cameras' colour images):"""

_NO_PROGRAM = ("There is no program\nyet, and nothing", "This session continues an earlier one, whose versions you have,\nand nothing")
_NO_PROGRAM_CLUTTER = ("There is no program\nyet. The practice", "This session continues an earlier one, whose versions you have.\nThe practice")

# ---- worlds for the heaps (a ClutterSession with --worlds): _WORLDS with the heap's scene(scene, rng)
_CLUTTER_WORLD_SCENE = """    # -> the scene the simulator builds. `scene` is the scene as sample_scenes or define_scenes made it, a dict:
    #   scene["objects"]    the heap's objects, each {{"shape", "size_mm", "color", "rgb": [r, g, b], "density",
    #                       "pos_mm": [x, y, z] (its centre), "quat": [w, x, y, z]}}; item_k is objects[k]
    #   scene["target"]     the index of the target in it; the task text names it by its "color" and its shape
    #   scene["level"]      (it stays what it is)
    # Change it and return it. It is NOT held to the limits of define_scenes, only to what the simulation can hold
    # (lengths in mm): up to {n} objects; any size {size}; densities {density} kg/m3; places x {x}, y {y}, z {z}.
    # It can also have what define_scenes has not:
    #   other shapes        "ring": [outer diameter, height, hole diameter], a cylinder with a hole along its axis
    #                       ("sides": 6 makes its outline and its hole polygons of that many sides); "pill":
    #                       [diameter, whole length]; "hexagon": [across the flats, height]; "L" and "cross": [arm
    #                       length, arm width, height]; "steps": [length, width, height]; "T": [bar, stem, width,
    #                       height]; "custom" with "parts" as in define_scenes (up to {parts} parts)
    #   o["name"] = "cylinder"          what the task text calls the object, instead of its shape's own word: on
    #                       the real robot the operator names the target (a ring can be "the blue cylinder" there)
    #   o["euler_deg"] = [rx, ry, rz]   in place of o["quat"] (delete "quat")
    #   scene["target"] = k             another object as the target
    #   scene["extras"] = [{{"name": "book", "fixed": True, "xy_mm": [x, y], "z_mm": 0, "yaw_deg": 0,
    #                       "parts": [{{"type": "box", "size_mm": [200, 150, 25], "pos_mm": [0, 0, 12.5],
    #                                  "euler_deg": [0, 0, 0], "rgb": [60, 90, 60]}}]}}]
    #                       bodies of your own that are part of the table and never move (up to {extras}, each of up
    #                       to {eparts} parts: box (three sides), cylinder and capsule (diameter, length along its own
    #                       z), ball (diameter)). pos_mm is the part's centre from the extra's reference point,
    #                       which stands at xy_mm, z_mm above the table. A thing that can move is an object.
    #   o["friction"] = 0.3                 on an object or an extra: its sliding friction ({friction}; the
    #                                       simulator's own: 0.7 for a heap's objects, 1.0 for the table and the extras, and 1.5 for the fingers' pads)
    #   o["contact"] = {{"time_ms": 5, "damping": 1, "impedance": [0.95, 0.99, 0.5]}}
    #                                       on the same: how hard its contacts are, in MuJoCo's terms: solref's time
    #                                       constant ({contact_ms} ms) and damping ratio, and solimp's d0, d1 and
    #                                       width (mm); each can be left out; the simulator's own: 20, 1,
    #                                       [0.9, 0.95, 1]. A shorter time and a narrower width: a harder contact,
    #                                       which gives way less. An object that says friction or contact takes
    #                                       precedence in its contacts: with the table, the fingers and objects that
    #                                       say neither, the contact has ITS friction and hardness (the simulator
    #                                       otherwise takes the larger friction of the two); between two objects
    #                                       that both say them: the larger friction, a mix of the hardness.
    #   a part {{"type": "hull", "points_mm": [[x, y, z], ...], "pos_mm": [0, 0, 0], "euler_deg": [0, 0, 0]}}
    #                                       among the parts of a "custom" object or of an extra (there with "rgb"): the convex hull of {hull} points
    #                                       given in the part's own frame (a convex mesh): a wedge, a prism, a
    #                                       tapered or rounded body. A shape that is not convex is several hulls.
    #                                       Its mass is its density times the hull's volume.
    #   scene["settle"]     if you changed the objects or the extras, the heap is brought to rest again before the
    #                       episode: "together" (the default: every object set at its pose at once: for other
    #                       sizes and shapes in a heap as it is; what overlaps is pushed apart), "in_order" (set
    #                       one after another in the list's order, each left to fall for 0.5 s: for a heap that
    #                       you build from the bottom up, each pose a little above where the object should lie)
    #                       or "none" (taken as given). The episode's output then starts with "# the world's
    #                       scene: ..." (what it became), and the result's TRUE positions at the start are its.
    #   scene["mjcf"] = "<mujoco> ... </mujoco>"
    #                       bodies of your own in MuJoCo's own language (MJCF), for what the fields
    #                       above cannot describe: anything rigid that MJCF can, e.g. a drawer on a
    #                       slide joint, a door or a lid on a hinge, a lever, a button on a spring,
    #                       a latch, a box with a sliding lid, a rack, a mesh of your own. The text
    #                       has a <worldbody> with your bodies and, as needed, <asset> (meshes by
    #                       their vertex attribute, materials, built-in textures), <default>
    #                       (classes of your own), <equality>, <tendon>, <contact> (pair, exclude).
    #                       - bodies in bodies, each with geoms (box, sphere, capsule, cylinder,
    #                         ellipsoid, mesh), joints (slide, hinge, ball, free; range, damping,
    #                         frictionloss, stiffness, springref, armature), sites, lights; a body
    #                         without a joint is fixed where it is;
    #                       - MJCF's units: metres, kilograms, radians. Positions are in the
    #                         robot's frame (the one of every position here), z = 0 at the table's
    #                         top;
    #                       - every name, and every reference to a name, starts with "w_" (w_drawer,
    #                         w_slide): the simulator's own bodies cannot be named or changed;
    #                       - soft and loose things are MuJoCo's <composite> (this is MuJoCo 2.3.7: no
    #                         flex), each with a prefix of its own (prefix="w_sponge"): type box,
    #                         cylinder or ellipsoid (a soft body: a sponge, a cushion; inside a body
    #                         that has a <freejoint/>), grid (a cloth: a towel; in the worldbody, placed
    #                         by its offset), cable (a rope, a wire), particle (loose grains). Its
    #                         count and spacing, the <geom> of its elements (size, mass, friction) and
    #                         <joint kind="main" .../> or <tendon .../> set its size, weight and
    #                         stiffness; <skin .../> gives it a surface in the images. truth() gives a
    #                         composite as ONE entry under its prefix, the centre of its bodies
    #                         (state.points(prefix) in success(): each body's place). Many loose things
    #                         of a kind (beans, crumbs) can also be bodies of their own (w_bean_0, ...);
    #                       - not possible: files (a mesh is its list of vertices), actuators, sensors,
    #                         plugins, cameras, the model's settings; at most 450 bodies, 900 geoms and
    #                         1400 degrees of freedom, the composites' included (they slow the
    #                         simulation: a cloth of 13 x 13 nodes runs slower than real time);
    #                       - a geom that says nothing of its contacts has MuJoCo's defaults
    #                         (friction 1, condim 3); the fingers' pads have friction 1.5.
    #                       MuJoCo compiles the text before the episode; if it does not, its error
    #                       message is the episode's result. truth() then also has each named body
    #                       of yours (its frame's origin, mm) and each named slide or hinge joint
    #                       (mm, degrees), and the trace shows them when they move. The robot's
    #                       moves take your bodies for obstacles like any other. A move stops at
    #                       40 N on the arm (SAFETY STOP), a stop_on_contact move at 4 N: a
    #                       mechanism the robot is to work must give way to less (a drawer: a
    #                       frictionloss of a few N).
    #   scene["joints"] = {{"w_slide": 40}}
    #                       where slide and hinge joints of your MJCF start (mm, degrees; left out:
    #                       at MJCF's 0)
    #                       In a heap's scene your bodies are there while the heap is brought to rest; they do not
    #                       count as its objects (the target is one of scene["objects"]).
    #   scene["cameras"] = {{"depth": {{"noise_px": 0.1}}, "scene": {{"depth": {{"table_holes": None}}}},
    #       "left_wrist": {{"pose_error": {{"shift_mm": 3, "turn_deg": 1}}}}}}
    #                       the cameras. "depth": the depth cameras' error model, for all of them,
    #                       or under "scene", "left_wrist", "right_wrist" for that one. Its
    #                       parameters, each of which can be left out (the simulator's own value):
    #                         noise_px (0.18): the stereo matching noise in pixels of disparity;
    #                           the depth's noise is noise_px * z^2 / (focal_px * baseline_mm)
    #                         noise_smooth (0.9), noise_smooth_px (8): the part of the noise that
    #                           is smooth over that many pixels (the rest is pixel to pixel)
    #                         scale (0.005): a depth scale error per camera and episode, up to it
    #                         fatten_px (4), fatten_step_mm (10): a nearer surface spreads that
    #                           many pixels over a farther one where the depth steps by more
    #                         flying (0.03), flying_step_mm (20): the part of the pixels at such a
    #                           step that get a depth between its two sides
    #                         dark_below (0.2), dark_holes (0.08), blob_px (4): objects darker than
    #                           that (0..1) lose that part of their pixels, in blobs of that size
    #                         table_holes ([0.2, 1100, 1.0, 0.6]) or None: the scene camera on the
    #                           table: the part without depth up to a distance (mm), how much
    #                           more per metre beyond it, the most
    #                         dropout (0.002): single pixels without depth
    #                         left_band_px (30) or None: the blind band at the image's left is
    #                           focal_px * baseline_mm / z + this many pixels wide
    #                         shadow (True): no depth where the second imager does not see
    #                         min_mm (275): nothing nearer than this
    #                         focal_px (625), baseline_mm (55): the depth sensor's own
    #                       "ideal": True under a camera: its depth as the simulator renders it,
    #                       without any of this (for a model of your own, in robot()'s look).
    #                       "pose_error" under a camera: its TRUE pose differs from the one its
    #                       frames give (eye, R) by {{"shift_mm": 5, "turn_deg": 1}} in a direction
    #                       drawn per episode (the simulator's own: scene 5 mm and 1 deg, wrists
    #                       3 mm and 1 deg), or with three numbers each by exactly that.
    #                       frame.depth_report(), in a program, gives numbers that describe a
    #                       frame's depth as a sensor's output: the part with depth, the blind
    #                       band, the holes, the noise by distance. read_real_depth gives the
    #                       same numbers for a real look: with both, a world's depth model can be
    #                       fitted to the real cameras and checked against looks it was not
    #                       fitted to.
    # rng: a numpy Generator, the same for the same scene.
    return scene

"""
_W_HEAD, _W_REST = _WORLDS.split("def scene(scene, rng):\n", 1)
_WORLDS_CLUTTER = (_W_HEAD.replace('run_episodes                  with "world": k', 'run_episodes, run_set         with "world": k')
                   + "def scene(scene, rng):\n" + _CLUTTER_WORLD_SCENE + "def robot(robot, rng, kit):\n"
                   + _W_REST.split("def robot(robot, rng, kit):\n", 1)[1].replace("show, truth, judge.", "show, truth, judge, ask."))
assert "run_set         with" in _WORLDS_CLUTTER and "judge, ask." in _WORLDS_CLUTTER

# ---- worlds for a task of the model's own (--own-task): the empty table's scene(scene, rng), a task's goal and success
_W_OWN_MJCF = _WORLDS[_WORLDS.index('    #   scene["mjcf"] = '):_WORLDS.index('    #   scene["without"] = ')]
_W_OWN_OLD = ("""bodies of your own in MuJoCo's own language (MJCF), for what the fields
    #                                       above cannot describe: anything rigid that MJCF can, e.g. a drawer on a""",
              """everything that is on the table, in MuJoCo's own language (MJCF): whatever
    #                                       MJCF can describe. A geom with contype="0" conaffinity="0" is seen and
    #                                       touches nothing (a mark on the table). Mechanisms too: a drawer on a""")
assert _W_OWN_MJCF.count(_W_OWN_OLD[0]) == 1
_W_OWN_ROBOT = _W_REST.split("def robot(robot, rng, kit):\n", 1)[1]
_W_OWN_ROBOT = _W_OWN_ROBOT[:_W_OWN_ROBOT.index("\nA world is YOUR claim about the real robot")]
_W_OWN_TASK = ("A world can also set a TASK OF ITS OWN, with these two functions together:", "A world that sets the task has these two functions together:")
assert _W_OWN_ROBOT.count(_W_OWN_TASK[0]) == 1
_W_OWN_CUTS = ((""" read_real_depth gives the
    #                                       same numbers for a real look: with both, a world's depth model can be
    #                                       fitted to the real cameras and checked against looks it was not
    #                                       fitted to.
""", "\n"),
               ("    #   state.simulator()                 the simulator's own judge of its own task (a dict with \"ok\")\n", ""),
               (""" (the
simulator's own verdict stays beside it, as simulator_ok), and robot.judge()""", ", and robot.judge()"),
               ("in place of the simulator's. Say", "as its task text. Say"),
               ("as the task text above does for its task: the program", "and nothing of how you built it: the program"),
               # examples that describe no rig and no earlier task (what a world can do is said as before)
               ("""    class Real(kit.Robot):
        def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
            r = self.robot.move(arm, xyz, dir_a, dir_b, stop_on_contact)   # or another move, another answer
            self.robot.wait(2.0)                                           # e.g. what a real move costs
            return r
        def look(self, cameras=None):
            out = self.robot.look(cameras)
            for name, f in out.items():
                d = f.depth.copy(); d[d < 280] = float("nan")             # e.g. what a real camera does not see
                out[name] = kit.frame(f, depth=d)
            return out
    return Real(robot)
""", """    calls = {{}}
    class Counting(kit.Robot):
        def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
            calls["move"] = calls.get("move", 0) + 1
            print("move", calls["move"], "to", xyz)
            return self.robot.move(arm, xyz, dir_a, dir_b, stop_on_contact)   # or another move, another answer
        def look(self, cameras=None):
            calls["look"] = calls.get("look", 0) + 1
            out = self.robot.look(cameras)
            for name, f in out.items():
                out[name] = kit.frame(f, depth=f.depth.copy())               # or other pixels
            return out
    return Counting(robot)
"""),
               ("""flex), each with a prefix of its own (prefix="w_sponge"): type box,
    #                                         cylinder or ellipsoid (a soft body: a sponge, a cushion; inside a body
    #                                         that has a <freejoint/>), grid (a cloth: a towel; in the worldbody, placed
    #                                         by its offset), cable (a rope, a wire), particle (loose grains). Its""",
                """flex), each with a prefix of its own (prefix="w_soft"): type box,
    #                                         cylinder or ellipsoid (a soft body; inside a body that has a
    #                                         <freejoint/>), grid (a cloth; in the worldbody, placed by its
    #                                         offset), cable (a line that bends), particle (loose particles). Its"""),
               ("""(state.points(prefix) in success(): each body's place). Many loose things
    #                                         of a kind (beans, crumbs) can also be bodies of their own (w_bean_0, ...);""",
                """(state.points(prefix) in success(): each body's place). Many loose things
    #                                         of a kind can also be bodies of their own (w_item_0, w_item_1, ...);"""))
_WORLDS_OWN = (_OWN_WORLDS_HEAD + _W_OWN_MJCF.replace(*_W_OWN_OLD) + _OWN_WORLDS_TABLE + "def robot(robot, rng, kit):\n"
               + _W_OWN_ROBOT.replace(*_W_OWN_TASK))
for _old, _new in _W_OWN_CUTS:
    assert _WORLDS_OWN.count(_old) == 1, _old
    _WORLDS_OWN = _WORLDS_OWN.replace(_old, _new)

_CONTINUED = """

This session continues an earlier one, which ended with v{sub} as its result. You keep its versions (read_program),
its scenes and all its episode results (read_output, view_images), and the notes you wrote in it.

The versions:
{versions}

{results}

Your notes from the earlier session:
{notes}

The real robot's episodes so far: {real}. Start with list_real_episodes.

v{sub}, the version that ran there:

```python
{code}```
"""

_FIRST_SCENE = """

There is no program yet.

The first look of the practice scene "example" (drawn at random; the arms at their start poses; the three cameras'
colour images):"""

ASK_CLUTTER = ("You should write one skill (one program, for both levels) that extracts the named object from the clutter. Maximize "
               "the success rate at both levels; then make it as fast as possible (robot time); and use as few model calls "
               "inside the episodes (robot.ask) as possible.")

_CLUTTER = """
You develop this program (the skill) in a session with a simulator of the robot to practise in. There is no program
yet. The practice is yours to set up: you draw scenes from the two environments, build scenes and objects of your
own, put together your own evaluation sets, and in practice the simulator's TRUE state is open to you, to test and
to measure with. The tools:

write_program, edit_program   save a new version of the program (v0, v1, ...). Any version can be run, so a
                              version can also be an experiment (a program that only looks and reports).
sample_scenes                 draw scenes at random from a level's environment -> their names (s1, s2, ...), a line each
define_scenes                 set up scenes of your own (see below) -> their names and what is in each
list_scenes                   what is in scenes: every object's shape, sizes, colour, density and place
run_episodes                  run a version on up to {at_once} scenes, one episode a scene. You get, for each episode: the
                              judge's verdict (the simulator's success check, on the TRUE state at the end), how the
                              program ended, the robot time, its model calls (how many, their cost, the robot time
                              spent waiting for them), what the program printed, and the objects' TRUE positions at
                              the start and at the end{points}.
run_set                       run a version on many scenes (up to {set_n}: for instance an evaluation set of your own):
                              one line per episode, then the successes, the mean robot time and the model calls by
                              level. A large set of hard scenes takes up to half an hour.
read_output                   more of an episode's output: what the program printed and, on lines starting with "#",
                              a trace of every robot command: robot time, the command, its result, that arm's TRUE
                              tool position (and opening), and after NOW the TRUE position of each object that
                              moved; every robot.ask with its answer. Compare what the program measured with the
                              truth there.
view_images                   the overview camera's picture of the end of an episode, and the images the program
                              handed over with robot.show
read_program                  a version's text, with line numbers
submit                        name the version that is your result

Where a tool takes scenes, "s10..s49" stands for the scenes s10 to s49.

The environments. In every episode a heap of objects lies on the table, and the episode's task text (the program's
`task`) names one of them, the target, by its colour and its shape: "the red cylinder", "the wood block", "the grey
ball". Which object it is, is random. No other object of the heap has both the target's colour and its shape (others
can have its colour, or its shape). The task text also tells the episode's level, in the words of the two task
texts of the first message (medium: "it can be seen"; hard: "It lies under other objects").

  medium   {n_medium} objects. The target rests on the table and touches at least two neighbours. At the start at least
           {m_scene:.0%} of it is seen from the scene camera and at least {m_top:.0%} from straight above. {t_medium:.0f} s of robot time.
  hard     {n_hard} objects. One or two flat blocks ({cover}) lie on the target, and
           more objects on and around them. At the start at most {h_scene:.0%} of the target is seen from the scene camera and
           at most {h_top:.0%} from straight above: often nothing of it. {t_hard:.0f} s of robot time.

A heap is made like this: the target is put on the table, on any of its sides, at a random place within x {x},
y {y}; 3 to 5 other objects are set around it and pulled against it; at the hard level the flat blocks are dropped
onto it; the remaining objects are dropped onto the heap (medium: next to the target, not on it); then everything
comes to rest (heaps that do not meet the level's conditions above are drawn again). Every object ends within 220 mm
of the target, and nothing lies on the ruler that is fixed to the table (y {ruler}). The objects are blocks,
cylinders and balls, of {density} kg/m3 (the weight follows from the size), in the colours {colours}
(the shade differs a little from object to object). Sizes in mm: the target is a block with sides of {tb} each, a
cylinder of diameter {tcd} and height {tch}, or a ball of diameter {tball}; the others are blocks of {ob}, cylinders
of diameter {ocd} and height {och}, balls of diameter {oball}.

sample_scenes draws such scenes: level, the first seed and how many (a seed is 0..{seed_hi}; a level's seed is always the
same scene). The seed also fixes the episode's sensor noise and calibration error.

Scenes of your own. define_scenes takes scenes as JSON objects. {{"level": "hard", "seed": 12}} alone is the scene
sample_scenes draws. With more fields it is a heap of your own, made in the way described above:

  {{"level": "hard", "seed": 3, "n_objects": 9, "centre_mm": [x, y],
   "target": {{"shape": "cylinder", "size_mm": [40, 50], "color": "red", "density": 600}},
   "objects": [{{"shape": "box", "size_mm": [110, 60, 15], "color": "blue", "role": "cover"}}, {{"shape": "ball"}}, ...]}}

Every field but level can be left out (a field given as null, "" or [] counts as left out); what is left out is drawn
at random within the environment's ranges, by the seed (n_objects too, and the objects beyond those given, up to
n_objects). size_mm is [a, b, c] for a box, [diameter, height] for a
cylinder, [diameter] for a ball, each {size} mm; density {dens} kg/m3; n_objects {n_own} (the target included);
centre_mm within x {own_x}, y {own_y} (the arms do not reach all of that). color is one of the colours above, or any
colour's name together with "rgb": [r, g, b] (0..255): the task text names the target by that name. role says how an
object of "objects" goes into the heap: "neighbour" (set against the target), "cover" (dropped flat onto it) or
"other" (dropped onto the heap). An object can also be an asset of your own, made of up to {parts} parts fixed together:

  {{"shape": "custom", "name": "U-shaped block", "color": "purple", "rgb": [130, 60, 170], "density": 500,
   "parts": [{{"type": "box", "size_mm": [60, 20, 20], "pos_mm": [0, 0, 0], "euler_deg": [0, 0, 0]}}, ...]}}

A part's type is box, cylinder, ball or capsule ([diameter, whole length]; a cylinder's and a capsule's axis is the
part's z), pos_mm and euler_deg place it in the object's frame (rotations about x, then y, then z). As a target such
an object is named by its colour and name ("the purple U-shaped block"). A heap of your own must meet its level's
conditions (how much of the target is seen, what it touches): one that does not after {tries} draws is refused, with the
reasons. Alternatively, give the target and every object "pos_mm": [x, y, z] (its centre) and, if wanted,
"euler_deg": the objects are then set at these poses one after another, each left to fall for 0.5 s, and no
condition is checked (the level then only sets the task text and the time limit): that is how to build one
particular arrangement. A scene that cannot be made is refused, with the reason.

In a program, robot.show(label, image) hands an image to you (H x W x 3 uint8 RGB, or H x W; at most {shown} an episode;
no robot time): for instance a camera frame with what the program detected drawn on it (cv2). In practice episodes a
program can also read the simulator: robot.truth() -> the TRUE state now (the names and points of run_episodes'
results, lengths in mm), and robot.judge() -> the judge's verdict on the state as it is now (a dict; "ok": success).
Both cost no robot time, and both return None in the final evaluation: they are for measuring and checking (how far
is a measurement from the truth? did a step do what it should?), not for doing the task. {rules}

robot.ask's model is the model you are. It is there because the task can need visual reasoning (where is the named
object? is it visible at all? what lies on it?), but each call takes robot time and costs money, and the fewer calls
a program needs for the same success, the better it is. A program's model calls in practice episodes are paid from
this session's budget, and each episode's result reports them. Their answers are not exactly repeatable, so a version
that asks the model can end differently on the same scene from one run to the next (run_episodes keeps the first
result; without model calls a version on a scene gives the same result every time).

Your result is the version you submitted last. After the session it is run, without you, on the final evaluation:
{n_eval} episodes at each of the two levels with new scenes, in which the program has the cameras and robot.ask and
nothing of the simulator's state. It is judged by how many episodes succeed at each level, then by the robot time it
takes, then by how few model calls it makes. Half of those scenes are drawn from the two environments as sample_scenes
draws them (none of the seeds open to you). The other half go beyond them: objects of other shapes (the task text
then names the target by that shape's name) and of other colours, smaller and bigger objects, more objects in a heap,
lighter and heavier ones, and heaps elsewhere on the table within the arms' reach. With scenes and objects of your
own you can practise on such things. So make the program reliable over many different scenes first, without leaning
on what only happens to hold in the scenes you tried; then fast; then sparing with model calls.

The session's budget is ${budget:.0f} of model usage. What costs: your replies (the thinking included), the conversation so
far, which is read again at every turn, and the model calls that programs make in practice episodes; simulation is
free. Every tool result ends with what is spent. So run several scenes at once, read outputs selectively, and prefer
edit_program to writing the whole program again. Submit a version as soon as it is better than the one submitted
before: when the budget runs out the session ends without warning and the last submitted version counts (nothing if
none); near the end of the budget, programs' model calls are refused (ok False). When you are satisfied, call submit
with final = true.
"""

_FIRST_CLUTTER = """

There is no program yet.

The first looks of two practice scenes drawn at random, "example_medium" and "example_hard" (the arms at their start
poses; the three cameras' colour images):"""

# the two-armed session's texts -> the one-armed robot's (clutter_one_arm.py)
_ONE_ARM = (("(the arms do not reach all of that)", "(the arm does not reach all of that)"),
            ("heaps elsewhere on the table within the arms' reach", "heaps elsewhere on the table within the arm's reach"),
            ("(the arms at their start\nposes; the three cameras' colour images)", "(the arm at its start\npose; the two cameras' colour images)"))


def _one_arm(text):
    for old, new in _ONE_ARM:
        text = text.replace(old, new)
    if "arms" in text or "three cameras" in text:
        raise ValueError("the one-armed session's text still speaks of two arms")
    return text

CLUTTER_SEEDS = 2000                                            # sample_scenes: the seeds below this (the evaluation's are others)
CLUTTER_EXAMPLE = 900                                           # the two scenes of the first look: clutter.sample of this seed
SET_N = 60                                                      # scenes of one run_set call
ASK_KEEP = 1.0                                                  # $ of the budget that programs' model calls leave to the session's own turns

EVAL_SET = (1000, 30)                                           # a session's self-evaluation set: scenes.sample of these seeds
EXAMPLE = 900                                                   # the scene of the first look: scenes.sample of this seed
AT_ONCE_SCENES = 8                                              # new episodes of one run_episodes call (the set: all at once)

NUDGE = "Go on with the tools; call submit with final = true when you are satisfied with a version."
NOTES = ("The conversation has grown long and will now be cut to save budget. Reply with notes of everything you want to keep "
         "(what you found out about the task, the robot and the cameras; what worked and what did not; what you plan next), "
         "without a tool call. After it you keep these notes, the versions with their notes, and all episode results "
         "(read_output, view_images); nothing else of the conversation.")


def _spec(name, description, properties, required=()):
    return dict(name=name, description=description,
                parameters={"type": "object", "properties": properties, "required": list(required), "additionalProperties": False})


_VER = {"type": "integer", "description": "the version's number (default: the newest)"}
TOOLS = [
    _spec("write_program", "Save a new version: the whole program.",
          {"code": {"type": "string", "description": "the whole Python file; it defines run(robot, task)"},
           "note": {"type": "string", "description": "one or two lines: what this version is"}}, ["code", "note"]),
    _spec("edit_program", "Save a new version: a version with pieces of its text replaced.",
          {"edits": {"type": "array", "description": "applied in order",
                     "items": {"type": "object", "additionalProperties": False, "required": ["old", "new"],
                               "properties": {"old": {"type": "string", "description": "text that occurs exactly once in the program (whitespace included)"},
                                              "new": {"type": "string", "description": "what replaces it"}}}},
           "note": {"type": "string", "description": "one or two lines: what this version changes"},
           "base": {"type": "integer", "description": "the version to edit (default: the newest)"}}, ["edits", "note"]),
    _spec("run_episodes", f"Run a version on practice episodes (at most {AT_ONCE} new ones a call, run at the same time; a few minutes).",
          {"seeds": {"type": "array", "items": {"type": "integer"}, "description": f"{SEEDS[0]} to {SEEDS[1]}"}, "version": _VER}, ["seeds"]),
    _spec("read_output", "Lines of an episode's output (the program's prints, its traceback, and the '#' trace of its robot commands).",
          {"version": _VER, "seed": {"type": "integer"},
           "start_line": {"type": "integer", "description": "default 1"},
           "max_lines": {"type": "integer", "description": "default 60, at most 200"},
           "contains": {"type": "string", "description": "only the lines that contain this text"},
           "trace": {"type": "boolean", "description": "false: without the '#' lines (default true)"}}, ["seed"]),
    _spec("view_images", "Images of an episode: 'final' (the overview camera at the end) and the names robot.show gave (at most 6 a call).",
          {"version": _VER, "seed": {"type": "integer"}, "names": {"type": "array", "items": {"type": "string"}}}, ["seed", "names"]),
    _spec("read_program", "A version's text with line numbers ('  12| ' is not part of the text).",
          {"version": _VER, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}),
    _spec("submit", "Name the version that is your result (it replaces an earlier submission). final = true ends the session.",
          {"version": _VER, "final": {"type": "boolean"}, "note": {"type": "string"},
           "summary": {"type": "string", "description": "with final = true, where the instructions ask for it: the summary of the session"},
           "world": {"type": "integer", "description": "with final = true, in a session on a task of your own: the world that sets the task"}},
          ["final"]),
]


class Session:
    """vlm: perception.vlm.VLM (or anything with its respond()); spent: () -> the session's spending so far in $ (the
    ledger's; default: the sum of what the replies report)."""

    def __init__(self, out, env, task, vlm, budget_usd=20.0, start=None, sensors="d415", segmenter=False, effort="high",
                 max_output_tokens=32000, n_eval=10, example_seed=100, spent=None, max_hours=8.0, compact_tokens=150000, log=print):
        self.dir = Path(out).resolve()
        (self.dir / "versions").mkdir(parents=True, exist_ok=True)
        self.env, self.task, self.vlm, self.budget, self.sensors = env, TASKS[task], vlm, float(budget_usd), sensors
        self.segmenter, self.effort, self.max_out, self.log = bool(segmenter), effort, int(max_output_tokens), log
        self.max_hours, self.compact_tokens, self.example_seed = float(max_hours), int(compact_tokens), int(example_seed)
        self.P = (getattr(vlm, "m", None) or {}).get("price_per_mtok") or PRICES
        self._spent = spent
        self.bridge = self.asker = None
        kind = "grippers" if env == "sim_bimanual_grippers" else "dexterous"
        self.n_eval = int(n_eval)
        self.specs = self._specs()
        self.tools = openai_tools(self.specs)
        sf = self.dir / "state.json"
        if sf.exists():
            self.state = json.loads(sf.read_text())
            self.inp, self.images = pickle.loads((self.dir / "pending.pkl").read_bytes())
        else:
            self.state = dict(prev=None, turn=0, nudges=0, usd=0.0, at_risk=0.0, submitted=None, ended=None, versions=[], last=None,
                              calls=[], turn_usd=0.0, cache_fields=True, compactions=0, notes="", wall_s=0.0, start=bool(start))
            if start:
                code = Path(start).read_text()
                bad = practice.check_program(code)
                if bad:
                    raise ValueError(f"the starting program is not allowed: {'; '.join(bad)}")
                self._save(code, "the starting program")
            self.inp, self.images = self._first()
            self._keep()
            self._md("## The first message\n\n" + self.inp[0]["content"][0]["text"] + f"\n\n[{len(self.images)} images, with the arms' states]")
        self.instr = self._instructions(kind)
        (self.dir / "instructions.md").write_text(self.instr)

    # ------------------------------------------------------------------ what a kind of session sets (SceneSession)
    def _specs(self):
        return TOOLS

    def _instructions(self, kind):
        return instructions(kind, self.task.time_limit_s, self.segmenter, closing=self._closing())

    def _closing(self):
        return _SESSION.format(
            lo=SEEDS[0], hi=SEEDS[1], shown=practice.SHOWN, points=practice.POINTS.get(self.task.name, ""), rules="\n" + practice.RULES,
            n_eval=self.n_eval, budget=self.budget, v0="; v0 is the starting program" if self.state["start"] else "",
            fallback=" (v0 if none)" if self.state["start"] else " (nothing if none)")

    def _intro(self):
        """The task, as every conversation of the session starts."""
        return self.task.text + "\n\nIn each episode the layout is new: " + VARIES.get(self.task.name, "the objects are elsewhere") + "."

    _EP = "seed"                                                # what an episode is named by, in texts
    _WALL = 900.0                                               # an episode's process is stopped after this (s)

    # ------------------------------------------------------------------ state
    def _keep(self):
        (self.dir / "pending.pkl").write_bytes(pickle.dumps((self.inp, self.images)))
        (self.dir / "state.json").write_text(json.dumps(self.state, indent=1) + "\n")

    def _md(self, t):
        with open(self.dir / "transcript.md", "a") as f:
            f.write(t.rstrip() + "\n\n")

    def _jsonl(self, **rec):
        with open(self.dir / "session.jsonl", "a") as f:
            f.write(json.dumps(dict(rec, t=round(time.time(), 1))) + "\n")

    def spent(self):
        return float(self._spent()) if self._spent else float(self.state["usd"])

    def left(self):
        return self.budget - self.spent() - float(self.state["at_risk"])

    def _path(self, v):
        return self.dir / "versions" / f"v{int(v):02d}.py"

    def _save(self, code, note, base=None):
        v = len(self.state["versions"])
        self._path(v).write_text(code if code.endswith("\n") else code + "\n")
        st = self.state                                             # at: when it was saved (scripts/session_progress.py)
        tk = st.get("tokens") or {}
        st["versions"].append(dict(note=str(note)[:400], lines=len(code.splitlines()), base=base, runs={},
                                   at=dict(turn=st.get("turn", 0), usd=round(self.spent(), 2), tokens=tk.get("input", 0) + tk.get("output", 0),
                                           min=round((st.get("wall_s", 0.0) + (time.time() - self._t0 if getattr(self, '_t0', None) else 0.0)) / 60, 1))))
        return v

    def _version(self, a, key="version"):
        n = len(self.state["versions"])
        if not n:
            raise ValueError("there is no version yet: write_program first")
        v = a.get(key)
        v = n - 1 if v is None else int(v)
        if not 0 <= v < n:
            raise ValueError(f"there is no v{v}: the versions are v0 to v{n - 1}")
        return v

    def _first(self):
        ep = Episode(self.env, self.task, seed=self.example_seed, sensors=self.sensors)
        start = _V0.format(code=self._path(0).read_text()) if self.state["start"] else "\nThere is no program yet.\n"
        t = self._intro() + _FIRST.format(start=start, seed=self.example_seed)
        images = []
        return [{"role": "user", "content": [{"type": "input_text", "text": t}]
                 + openai_items(Toolbox(ep.robot).call("look", {}, free=True), images)}], images

    def results_line(self):
        out = []
        for v, m in enumerate(self.state["versions"]):
            if m["runs"]:
                ok = [r[1] for r in m["runs"].values() if r[0]]
                out.append(f"v{v} {len(ok)}/{len(m['runs'])}" + (f" ({sum(ok) / len(ok):.0f} s)" if ok else ""))
        sub = self.state["submitted"]
        return ("Practice so far (successes/episodes, mean robot time of the successes): " + ("; ".join(out) or "nothing run")
                + f". Submitted: {'v' + str(sub) if sub is not None else 'nothing'}.")

    # ------------------------------------------------------------------ the tools
    def tool(self, name, a):
        """One tool call -> the result's items. Never raises for a bad call."""
        try:
            if not isinstance(a, dict):
                raise ValueError("the arguments are not a JSON object (was the reply cut at its length limit? Write less in one call)")
            fn = {s["name"]: getattr(self, "_t_" + s["name"]) for s in self.specs}.get(name)
            if fn is None:
                raise ValueError(f"unknown tool {name}: there are {', '.join(s['name'] for s in self.specs)}")
            items = fn(a)
        except (KeyError, ValueError, TypeError, IndexError) as e:
            items = [text(f"bad call: {str(e) or type(e).__name__}")]
        except Exception as e:                                  # a fault of the harness must not lose the session
            self.log(f"TOOL FAILED: {name}: {type(e).__name__}: {e}")
            items = [text(f"the tool failed ({type(e).__name__}: {str(e)[:300]}): try again, or otherwise")]
        for i in items:
            if i["type"] == "text" and len(i["text"]) > 30000:
                i["text"] = i["text"][:30000] + "\n... (cut: ask for less at once)"
        return items

    def _new(self, code, note, base=None):
        bad = practice.check_program(code)
        if bad:
            return [text("NOT saved, the program is not allowed:\n  " + "\n  ".join(bad) + "\n" + practice.RULES)]
        v = self._save(code, note, base)
        return [text(f"saved as v{v} ({self.state['versions'][v]['lines']} lines)")]

    def _t_write_program(self, a):
        return self._new(str(a["code"]), a.get("note", ""))

    def _t_edit_program(self, a):
        base = self._version(a, "base")
        code, bad = self._path(base).read_text(), []
        for k, e in enumerate(a["edits"]):
            n = code.count(e["old"]) if e["old"] else 0
            if n != 1:
                bad.append(f"edit {k + 1}: its `old` text occurs {n} times in the program (after the edits before it), not once: {e['old'][:80]!r}")
            else:
                code = code.replace(e["old"], e["new"])
        if bad:
            return [text(f"NOT saved (v{base} is unchanged; no edit was applied):\n  " + "\n  ".join(bad))]
        return self._new(code, a.get("note", ""), base)

    def _run_dir(self, v, seed):
        return self.dir / "runs" / f"v{v:02d}" / f"s{seed}"

    def _results(self, v, keys, brief=False):
        """The tool result for the episodes `keys` of version v, as run (their directories)."""
        items, runs, failed = [], self.state["versions"][v]["runs"], []
        for s in keys:
            d = self._run_dir(v, s)
            if not (d / "result.json").exists():                # the process gave no result: not kept, it can be run again
                items.append(text(f"v{v} {self._EP} {s}: NO RESULT: the episode's process was stopped ({self._WALL / 60:.0f} min of computing: a loop "
                                  "without robot commands?) or crashed" + ("" if brief else _printed(d))))
                continue
            r = json.loads((d / "result.json").read_text())
            runs[str(s)] = [bool(r["ok"]), r["robot_s"]] + ([r["model_calls"], r["model_usd"]] if "model_calls" in r else [])
            failed += [] if r["ok"] else [(s, d)]
            items.append(text(_episode_line(v, s, r, d, self._EP) if brief else _episode_text(v, s, r, d, self._EP)))
        if brief:
            items = [text("\n".join(i["text"] for i in items))]
        for s, d in ([] if brief else failed[:3]):
            items += [text(f"v{v} {self._EP} {s}, the overview camera at the end:"), _image(d / "final.jpg")]
        n = [runs[str(s)][0] for s in keys if str(s) in runs]
        items.append(text(f"v{v}: {sum(n)} of these {len(n)} episodes succeeded. " + self.results_line()))
        return items

    def _t_run_episodes(self, a):
        v = self._version(a)
        seeds = list(dict.fromkeys(int(s) for s in a["seeds"]))
        if not seeds or any(not SEEDS[0] <= s <= SEEDS[1] for s in seeds):
            raise ValueError(f"seeds: practice episodes are the seeds {SEEDS[0]} to {SEEDS[1]}")
        new = [s for s in seeds if not (self._run_dir(v, s) / "result.json").exists()]
        if len(new) > AT_ONCE:
            raise ValueError(f"at most {AT_ONCE} new episodes a call")
        if new:
            if self.segmenter and self.bridge is None:
                self.bridge = practice.SegmenterBridge()
            with ThreadPoolExecutor(len(new)) as ex:
                list(ex.map(lambda s: practice.run_sandboxed(self.env, self.task.name, s, self._path(v), self._run_dir(v, s), sensors=self.sensors,
                                                              trace=True, bridge=self.bridge, wall_s=900.0), new))
        return self._results(v, seeds)

    def _episode(self, a):
        v, s = self._version(a), int(a["seed"])
        d = self._run_dir(v, s)
        if not (d / "output.txt").exists():
            raise ValueError(f"v{v} was not run on seed {s}")
        return v, s, d

    def _t_read_output(self, a):
        v, s, d = self._episode(a)
        lines = (d / "output.txt").read_text(errors="replace").splitlines()
        k0, n = max(1, int(a.get("start_line") or 1)), min(200, max(1, int(a.get("max_lines") or 60)))
        has, tr = a.get("contains"), a.get("trace", True)
        sel = [(k, l) for k, l in enumerate(lines, 1)
               if k >= k0 and (tr or not l.startswith("#")) and (not has or has.lower() in l.lower())]
        out = [f"{k:5d}| {l[:300]}" for k, l in sel[:n]]
        more = f"; the next one is line {sel[n][0]}" if len(sel) > n else ""
        return [text(f"v{v} {self._EP} {s}: output.txt has {len(lines)} lines; {len(sel)} from line {k0} on match; {len(out)} shown{more}\n" + "\n".join(out))]

    def _t_view_images(self, a):
        v, s, d = self._episode(a)
        shown = sorted(p.name for p in (d / "shown").glob("*.png")) if (d / "shown").exists() else []
        items = []
        for name in list(a["names"])[:6]:
            name = str(name)
            hit = [n for n in shown if name in (n, n[:-4], n[3:-4], n[:2])]
            if name == "final" and (d / "final.jpg").exists():
                items += [text(f"v{v} {self._EP} {s}, the overview camera at the end:"), _image(d / "final.jpg")]
            elif hit:
                items += [text(f"v{v} {self._EP} {s}, {hit[0]}:"), _image(d / "shown" / hit[0])]
            else:
                items.append(text(f"no image {name!r}: this episode has final" + "".join(", " + n for n in shown)))
        return items or [text("names: which images?")]

    def _t_read_program(self, a):
        v = self._version(a)
        lines = self._path(v).read_text().splitlines()
        k0 = max(1, int(a.get("start_line") or 1)); k1 = min(len(lines), int(a.get("end_line") or len(lines)))
        m = self.state["versions"][v]
        return [text(f"v{v} ({len(lines)} lines; {m['note']}), lines {k0} to {k1}:\n" + "\n".join(f"{k:4d}| {lines[k - 1]}" for k in range(k0, k1 + 1)))]

    def _t_submit(self, a):
        v = self._version(a)
        self.state["submitted"] = v
        runs = self.state["versions"][v]["runs"]
        t = f"v{v} is now your submitted result" + ("" if runs else " (it was not run on any practice episode)") + ". "
        if a.get("final"):
            if self.state.get("summary"):                           # (--summary: the session's own account, kept as summary.md)
                said = str(a.get("summary") or "").strip()
                if len(said) < 600:
                    return [text(t + "The session has NOT ended: final = true needs `summary`, the summary of this session that the "
                                     "instructions ask for (what you learned from the real episodes, what you changed and why, what you "
                                     "tested and how it came out, what you expect on the real robot and what stays uncertain).")]
                (self.dir / "summary.md").write_text(said + "\n")
            self.state["ended"] = "submitted"
            return [text(t + "The session has ended.")]
        return [text(t + self.results_line())]

    # ------------------------------------------------------------------ the conversation
    def _plan(self, inp, images):
        """-> (the reply's token limit that the budget still allows, the call's worst-case cost)."""
        last = self.state["last"] or {}
        ctx = (last.get("input", 0) + last.get("output", 0)) if self.state["prev"] else 0
        new = (len(json.dumps(inp)) + len(self.instr) + len(json.dumps(self.tools))) / 2.5 + IMAGE_TOKENS * len(images)
        cin = ((ctx + new) * 1.2 + 2000) * self.P["in"] / 1e6
        out = int(min(self.max_out, (self.left() - cin) / self.P["out"] * 1e6))
        return out, cin + max(out, 0) * self.P["out"] / 1e6

    def _model(self, inp, images, max_out, worst):
        """One reply, with retries -> the reply, or dict(ok=False, why, stop=how the session ends or None)."""
        for wait in (60, 300, 900, None):
            if hasattr(self.vlm, "cfg"):
                self.vlm.cfg["max_usd_per_call"] = worst
            extra = dict(prompt_cache_key=self.dir.name[:60], prompt_cache_retention="24h") if self.state["cache_fields"] else None
            r = self.vlm.respond(inp, images, instructions=self.instr, tools=self.tools, previous=self.state["prev"], effort=self.effort,
                                 max_output_tokens=max_out, extra=extra, timeout_s=1800)
            if r.get("ok"):
                return r
            why = str(r.get("why"))
            self.log(f"model call failed: {why[:300]}")
            self._jsonl(kind="failed_call", why=why[:600])
            if "prompt_cache" in why and self.state["cache_fields"]:
                self.state["cache_fields"] = False              # this model does not take the fields: without them
                continue
            if "model budget" in why:
                return dict(r, stop="budget")
            if "context" in why.lower() and any(w in why.lower() for w in ("length", "window", "exceed")):
                return dict(r, stop=None, overflow=True)
            if any(c in why for c in ("failed: 400", "failed: 401", "failed: 403", "failed: 404")):
                return dict(r, stop=f"model: {why[:300]}")
            if "Timeout" in why or "timed out" in why:           # the provider may still bill a reply that never arrived
                self.state["at_risk"] += worst; self._keep()
            if wait is None or self.left() < worst:
                break
            time.sleep(wait)
        return dict(ok=False, why=why, stop=f"model: {why[:300]}")

    def _compact(self, notes, pending):
        """A new conversation: the task, the model's notes, the versions and the newest program, the pending results."""
        st = self.state
        st["notes"] = notes or st["notes"]
        st["prev"], st["last"] = None, None
        st["compactions"] += 1
        vs = "\n".join(f"v{v} ({m['lines']} lines{', from v' + str(m['base']) if m['base'] is not None else ''}): {m['note']} | practice: "
                       + (self._practice(m["runs"]) or "not run") for v, m in enumerate(st["versions"]))
        newest = len(st["versions"]) - 1
        t = (self._intro() + "\n\n"
             "This session started earlier; the conversation so far was cut to save budget. You keep your notes, the versions "
             "(read_program) and all episode results (read_output, view_images).\n\nThe versions:\n" + vs + "\n\n" + self.results_line()
             + "\n\nYour notes:\n" + (st["notes"] or "(none)") + (self._worlds_text() if hasattr(self, "_worlds_text") else "")
             + (f"\n\nv{newest}, the newest version:\n\n```python\n{self._path(newest).read_text()}```\n" if newest >= 0 else "")
             + ("\n\nThe result of your last tool call:\n" + pending[:8000] if pending else ""))
        self.inp, self.images = [{"role": "user", "content": [{"type": "input_text", "text": t}]}], []
        self._md(f"### (the conversation was cut; it goes on from the model's notes)\n\n{st['notes']}")

    def run(self):
        """The session, to its end -> how it ended. (A session stopped by a failing model call, or killed, can be
        resumed: the state is kept after every step.)"""
        st, t0 = self.state, time.time()
        self._t0 = t0
        try:
            while not st["ended"]:
                if st["wall_s"] + time.time() - t0 > self.max_hours * 3600:
                    st["ended"] = f"wall time: {self.max_hours:g} h"; break
                if not st["calls"]:
                    stop = self._turn()
                    if stop:
                        return stop                             # not ended: --resume goes on from here
                    continue
                results, self.inp, self.images = [], [], []
                for c in st["calls"]:                           # the reply's tool calls (again, if the session was killed in them)
                    try:
                        args = json.loads(c.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = None
                    items = self.tool(c.get("name"), args)
                    res = _texts_of(items)
                    results.append(dict(name=c.get("name"), args=_short(args), result=res))
                    self.log(f"> {c.get('name')} {json.dumps(_short(args))[:300]}\n  {res[:600]}")
                    self._md(f"**{c.get('name')}** `{json.dumps(_short(args))[:1500]}`\n\n```\n{res[:6000]}\n```")
                    self.inp.append({"type": "function_call_output", "call_id": c.get("call_id"), "output": items})
                self._jsonl(kind="calls", turn=st["turn"], calls=results)
                if st["ended"]:
                    break
                self.inp[-1]["output"].append(text(self._budget_note()))
                for i in self.inp:                              # the items as the model client takes them
                    i["output"] = openai_items(i["output"], self.images)
                st["calls"] = []
                tk = st["last"] or {}
                cached = tk.get("cached", 0) >= 0.5 * max(tk.get("input", 0), 1)
                if tk.get("input", 0) + tk.get("output", 0) > self.compact_tokens * (1.0 if cached else 0.35):
                    self._ask_notes()
                self._keep()
        finally:
            st["wall_s"], self._t0 = round(st["wall_s"] + time.time() - t0, 1), None
            if self.asker is not None:
                self.asker.close(); self.asker = None
            if self.bridge is not None:
                self.bridge.close(); self.bridge = None
            self._keep()
        return self._finish()

    def _practice(self, runs):
        """A version's practice results, for the text of a new conversation."""
        return ", ".join(f"{self._EP} {s} {'ok' if r[0] else 'FAIL'} {r[1]} s" for s, r in runs.items())

    def _budget_note(self):
        left, st = self.left(), self.state
        note = f"[budget: ${self.budget - left:.2f} of ${self.budget:.0f} spent; that turn cost ${st['turn_usd']:.2f}]"
        if left < 0.2 * self.budget:
            note += " Little is left: make sure your best version is submitted" + ("." if st["submitted"] is not None else " (nothing is submitted yet).")
        # the session ends, without a further turn, when a reply's worst case no longer fits: with a long conversation that
        # is a few dollars before the cap (the session of 2026-11-23 that owed a summary ended at $13.14 of $15 without it)
        need = self._plan([], [])[1] if st.get("prev") else 0.0
        if st.get("summary") and left < max(2.0 * need, 0.12 * self.budget):
            note += (f" THE SESSION IS ABOUT TO END: a turn now reserves up to ${need:.2f} and ${left:.2f} is left. Make your final submit NOW "
                     "(final = true, with the summary): after one or two more turns it ends without it.")
        return note

    def _turn(self):
        """One reply of the model to the pending input -> None, or why the session stops here (unfinished). The reply's
        tool calls become the state's pending calls; without one, the pending input is a nudge."""
        st = self.state
        max_out, worst = self._plan(self.inp, self.images)
        if max_out < MIN_REPLY:
            st["ended"] = "budget"; return None
        r = self._model(self.inp, self.images, max_out, worst)
        if not r.get("ok"):
            if r.get("overflow"):
                self._compact("", _texts(self.inp)); self._keep(); return None
            if r.get("stop") == "budget":
                st["ended"] = "budget"; return None
            self._keep()
            return r.get("stop") or "model"
        st["turn"] += 1
        st["turn_usd"] = float(r.get("usd") or 0.0)
        st["usd"] = round(st["usd"] + st["turn_usd"], 5)
        st["prev"], st["last"] = r["id"], r.get("tokens") or {}
        tk = st["last"]
        st["tokens"] = {k: (st.get("tokens") or {}).get(k, 0) + int(tk.get(k) or 0) for k in ("input", "cached", "output")}
        said = _said(r)
        st["calls"] = [dict(call_id=o.get("call_id"), name=o.get("name"), arguments=o.get("arguments"))
                       for o in r["output"] if o.get("type") == "function_call"]
        head = (f"turn {st['turn']}: ${st['turn_usd']:.2f} (in {tk.get('input', 0)}, cached {tk.get('cached', 0)}, out {tk.get('output', 0)}, "
                f"reasoning {tk.get('reasoning', 0)}; {r.get('wait_s', 0):.0f} s); spent ${self.spent():.2f} of ${self.budget:.2f}")
        self.log(head + (f"\n  model: {said[:500]}" if said else ""))
        self._md(f"## {head}" + (f"\n\n{said}" if said else ""))
        self._jsonl(kind="turn", turn=st["turn"], usd=r.get("usd"), tokens=tk, wait_s=r.get("wait_s"), status=r.get("status"), said=said,
                    calls=[dict(c, arguments=str(c["arguments"])[:20000]) for c in st["calls"]], spent=round(self.spent(), 4))
        self.inp, self.images = [], []
        if not st["calls"]:
            st["nudges"] += 1
            if st["nudges"] > 3:
                st["ended"] = "the model stopped calling tools"; return None
            cut = (f"Your reply was cut at its length limit ({max_out} tokens, thinking included) before a tool call: think less at "
                   "once and make smaller steps. ") if r.get("status") == "incomplete" else ""
            self.inp = [{"role": "user", "content": [{"type": "input_text", "text": cut + NUDGE + " " + self._budget_note()}]}]
        else:
            st["nudges"] = 0
        self._keep()
        return None

    def _ask_notes(self):
        """The conversation is long: one more turn for the model's notes, then a new conversation from them."""
        pending = _texts(self.inp)
        inp = self.inp + [{"role": "user", "content": [{"type": "input_text", "text": NOTES}]}]
        max_out, worst = self._plan(inp, self.images)
        notes = ""
        if max_out >= MIN_REPLY:
            r = self._model(inp, self.images, min(max_out, 12000), worst)
            if r.get("ok"):
                self.state["usd"] = round(self.state["usd"] + float(r.get("usd") or 0.0), 5)
                notes = _said(r)
                self._jsonl(kind="notes", usd=r.get("usd"), tokens=r.get("tokens"), said=notes)
        self._compact(notes, pending)

    def _finish(self):
        st = self.state
        sub = st["submitted"] if st["submitted"] is not None else (0 if st["start"] else None)
        if sub is not None:
            shutil.copyfile(self._path(sub), self.dir / "program.py")
        res = dict(ended=st["ended"], submitted=st["submitted"], result_version=sub, turns=st["turn"], versions=st["versions"],
                   task=self.task.name, env=self.env, sensors=self.sensors, budget_usd=self.budget, spent_usd=round(self.spent(), 4),
                   compactions=st["compactions"], wall_s=st["wall_s"], effort=self.effort)
        if st.get("task_world") is not None:                        # (--own-task: the world that sets the session's task)
            res["task_world"] = st["task_world"]
        (self.dir / "result.json").write_text(json.dumps(res, indent=1) + "\n")
        self._md(f"## The session ended: {st['ended']}. Result: {'v' + str(sub) if sub is not None else 'none'}; ${self.spent():.2f} spent.")
        return st["ended"]


_NAME = {"type": "string", "description": "a scene's name"}
_N, _XY = {"type": "number"}, {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
_COL = {"type": "string", "enum": list(scenes.COLOURS)}


def _obj(description=None, **props):
    return dict({"type": "object", "properties": props, "additionalProperties": False}, **({"description": description} if description else {}))


_SCENE_SPEC = _obj(
    "a scene (the instructions: The scenes); every field can be left out",
    seed={"type": "integer"},
    cylinder=_obj(diameter_mm=_N, height_mm=_N, color=_COL, density=_N, xy_mm=_XY),
    stand=_obj(stick_diameter_mm=_N, stick_height_mm=_N, board_mm={"type": "array", "items": _N, "minItems": 3, "maxItems": 3},
               color=_COL, stick_color=_COL, density=_N, xy_mm=_XY, yaw_deg=_N),
    hole_clearance_mm=_N,
    table=_obj("another table (the instructions: The table)", height_mm=_N, color={"type": "string"}, pattern={"type": "string"},
               color2={"type": "string"}, size_mm=_N, angle_deg=_N),
    distractors={"anyOf": [{"type": "integer", "description": "how many"},
                           {"type": "array", "items": _obj(shape={"type": "string", "enum": list(scenes.SHAPES)},
                                                           size_mm={"type": "array", "items": _N}, color=_COL, density=_N, xy_mm=_XY, yaw_deg=_N)}]})


_EPISODE = {"type": "string", "description": "a real episode's name (list_real_episodes)"}
REAL_TOOLS = [
    _spec("list_real_episodes", "Without episodes: the real robot, and a line per episode (version, verdict, setup, contact setting). With "
          "episodes: those in full (moves until contact, the server's guarded lines, looks, the names of the images).",
          {"episodes": {"type": "array", "items": _EPISODE, "description": "at most 4; left out: the overview of all"}}),
    _spec("read_real_output", "Lines of a real episode's output (the program's prints and the '#' lines of its robot commands, with the arm's "
          "measured tool position).",
          {"episode": _EPISODE, "start_line": {"type": "integer", "description": "default 1"},
           "max_lines": {"type": "integer", "description": "default 60, at most 200"},
           "contains": {"type": "string", "description": "only the lines that contain this text"},
           "trace": {"type": "boolean", "description": "false: without the '#' lines (default true)"}}, ["episode"]),
    _spec("view_real_images", "Images of a real episode, by the names list_real_episodes gives (at most 6 a call).",
          {"episode": _EPISODE, "names": {"type": "array", "items": {"type": "string"}}}, ["episode", "names"]),
    _spec("read_real_depth", "The depth of a real look as numbers (mm, 0 = no depth): a grid of at most 24 x 18 values over a window of pixels "
          "(each the pixel at the grid point), the window's statistics, and the whole look's depth report (the part with depth, the blind "
          "band, the holes, the noise by distance: the numbers frame.depth_report() gives for a simulated frame).",
          {"episode": _EPISODE, "look": {"type": "string", "description": "a look's name, such as look_03_left_wrist"},
           "u0": {"type": "integer", "description": "the window's left column (default 0)"}, "v0": {"type": "integer", "description": "its top row (default 0)"},
           "u1": {"type": "integer", "description": "its right column (default 639)"}, "v1": {"type": "integer", "description": "its bottom row (default 479)"}},
          ["episode", "look"]),
]
_REAL_SPEC = {"anyOf": [{"type": "boolean"}, _obj(false_stops={"type": "boolean"}, scatter={"type": "boolean"}, thin_depth={"type": "boolean"})],
              "description": "a scene like the real robot (the instructions: The real robot)"}


_WORLD = {"type": ["integer", "null"], "description": "a world's number (left out, null or -1: the plain simulator)"}
WORLD_TOOLS = [
    _spec("write_world", "Save a new version of a world: the whole file (scene(scene, rng) and/or robot(robot, rng, kit)).",
          {"code": {"type": "string", "description": "the whole Python file"},
           "note": {"type": "string", "description": "what this world models, from which real episodes, and what it leaves out"}}, ["code", "note"]),
    _spec("edit_world", "Save a new version of a world: a world with pieces of its text replaced.",
          {"edits": {"type": "array", "description": "applied in order",
                     "items": {"type": "object", "additionalProperties": False, "required": ["old", "new"],
                               "properties": {"old": {"type": "string", "description": "text that occurs exactly once in the world's file"},
                                              "new": {"type": "string", "description": "what replaces it"}}}},
           "note": {"type": "string"}, "base": {"type": "integer", "description": "the world to edit (default: the newest)"}}, ["edits", "note"]),
    _spec("read_world", "A world's text with line numbers.",
          {"world": {"type": "integer", "description": "default: the newest"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}),
]


def _scene_tools(eval_set, tables=False, real=False, real_scenes=False, worlds=False):
    keep = {s["name"]: s for s in TOOLS}
    scene = _SCENE_SPEC if tables else dict(_SCENE_SPEC, properties={k: v for k, v in _SCENE_SPEC["properties"].items() if k != "table"})
    if real_scenes:
        stand = scene["properties"]["stand"]
        stand = dict(stand, properties=dict(stand["properties"], sticks={"type": "integer"}, stick_spacing_mm=_N))
        scene = dict(scene, properties=dict(scene["properties"], stand=stand, real=_REAL_SPEC))
    out = [keep["write_program"], keep["edit_program"],
           _spec("define_scenes", "Set up practice scenes -> their names and what is in each.",
                 {"scenes": {"type": "array", "items": scene}}, ["scenes"]),
           _spec("list_scenes", "What is in scenes (sizes, colours, densities, places); without names: all of them.",
                 {"names": {"type": "array", "items": _NAME}}),
           _spec("run_episodes", f"Run a version on scenes, one episode each (at most {AT_ONCE_SCENES} new ones a call, run at the same time; a few minutes).",
                 dict({"scenes": {"type": "array", "items": _NAME}, "version": _VER}, **({"world": _WORLD} if worlds else {})), ["scenes"])]
    if eval_set:
        out.append(_spec("run_eval_set", "Run a version on the whole self-evaluation set (E0, E1, ...): one line per episode.", {"version": _VER}))
    for name in ("read_output", "view_images"):
        p = keep[name]["parameters"]
        props = {("scene" if k == "seed" else k): (_NAME if k == "seed" else x) for k, x in p["properties"].items()}
        if worlds:
            props["world"] = _WORLD
        out.append(_spec(name, keep[name]["description"], props, ["scene" if k == "seed" else k for k in p["required"]]))
    return out + (WORLD_TOOLS if worlds else []) + (REAL_TOOLS if real else []) + [keep["read_program"], keep["submit"]]


class SceneSession(Session):
    """A session on a task with varied scenes (scenes.py): the model starts from nothing, sets up its own practice
    scenes within the training limits (define_scenes), and its practice episodes run with the simulation's truth
    (practice.py: practice). eval_set: it also gets a fixed self-evaluation set (EVAL_SET) to run versions on.
    tables: it is asked for a skill that also works on other tables (height, colour, pattern), and its scenes can
    have one (scenes.build_table). Episodes are named by their scene."""
    _EP = "scene"

    def __init__(self, *a, eval_set=False, tables=False, real=None, real_scenes=False, continue_from=None, ask=None, summary=False,
                 worlds=False, own_task=False, episode_s=None, **k):
        """episode_s: the robot time of an episode in this session (None: the task's own). real: a pack of real-robot episodes (scripts/real_feedback_pack.py) that the model can study; real_scenes:
        its scenes can be "like the real rig" (scenes.py REAL: hand-written); continue_from: an ended session's
        directory: this one starts with its versions, scenes, episode results and notes (that one is not changed)."""
        self.eval_set, self.tables = bool(eval_set), bool(tables)
        self.real, self.real_scenes = (Path(real).resolve() if real else None), bool(real_scenes)
        self.source = Path(continue_from).resolve() if continue_from else None
        self.more, self.want_summary = (str(ask).strip() if ask else ""), bool(summary)   # (--ask: more that is asked for; --summary)
        self.worlds = bool(worlds)                                  # (--worlds: the model writes worlds, practice.py: world)
        self.own_task = bool(own_task)                              # (--own-task: the task is a world's goal() and success())
        self.episode_s = float(episode_s) if episode_s else None    # (--episode-s: the system text states it, the episodes have it)
        if self.own_task and not (self.worlds and self.more and self.want_summary):
            raise ValueError("a task of the model's own needs worlds, what is asked (ask) and the summary")
        self.episodes = {}
        if self.real is not None:
            names = json.loads((self.real / "pack.json").read_text())["episodes"]
            self.episodes = {n: json.loads((self.real / n / "episode.json").read_text()) for n in names}
        super().__init__(*a, **k)
        (self.dir / "worlds").mkdir(exist_ok=True)
        self.state.setdefault("worlds", [])
        for key, now in (("real", str(self.real) if self.real else None), ("real_scenes", self.real_scenes), ("ask", self.more),
                         ("summary", self.want_summary), ("worlds_on", self.worlds), ("own_task", self.own_task), ("episode_s", self.episode_s)):
            if self.state.setdefault(key, now) != now:
                raise ValueError(f"this session was started with {key} = {self.state[key]!r}")
        if self.state.get("tables", self.tables) != self.tables:
            raise ValueError("this session was started " + ("with" if self.state["tables"] else "without") + " other tables")
        if self.state.get("eval_set", self.eval_set) != self.eval_set:
            raise ValueError("this session was started " + ("with" if self.state["eval_set"] else "without") + " the self-evaluation set")

    def _specs(self):
        tools = _scene_tools(self.eval_set, self.tables, real=self.real is not None, real_scenes=self.real_scenes, worlds=self.worlds)
        if self.own_task:                                           # (the scenes are empty tables: nothing to set up in them)
            new = _spec("new_scenes", "More scenes (empty tables with a seed each: one episode each in a world of yours) -> their names.",
                        {"count": {"type": "integer", "description": "how many (1 to 12)"}}, ["count"])
            lst = _spec("list_scenes", "The scenes there are.", {})
            tools = [new if t["name"] == "define_scenes" else lst if t["name"] == "list_scenes" else t for t in tools]
        return tools

    def _instructions(self, kind):
        if self.own_task:                                           # (the left arm alone, as the real robot)
            return instructions(kind, self.episode_s or self.task.time_limit_s, self.segmenter, closing=self._closing(), arm="left")
        if self.episode_s:
            return instructions(kind, self.episode_s, self.segmenter, closing=self._closing())
        return super()._instructions(kind)

    def _t_new_scenes(self, a):
        st, n = self.state, a.get("count")
        if not self.own_task:
            raise ValueError("new_scenes: in a session on a task of your own")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 12:
            raise ValueError("count: 1 to 12")
        names = []
        for _ in range(n):
            st["n_scenes"] += 1
            st["scenes"][f"s{st['n_scenes']}"] = scenes.empty(st["n_scenes"])
            names.append(f"s{st['n_scenes']}")
        return [text(f"{names[0]} to {names[-1]}: empty tables with the seeds {st['n_scenes'] - n + 1} to {st['n_scenes']}" if n > 1
                     else f"{names[0]}: an empty table with the seed {st['n_scenes']}")]

    def _worlds_closing(self):
        if not self.worlds:
            return ""
        W, rr = scenes.WORLD, lambda v: f"{v[0]:g}..{v[1]:g}"
        return _WORLDS.format(**{k: rr(W[k]) for k in ("cyl_d", "cyl_h", "hole", "stick_d", "stick_h", "board", "board_t", "size", "density", "x", "y")},
                              distractors=W["distractors"], extras=W["extras"], parts=W["parts"], friction=rr(W["friction"]),
                              contact_ms=rr(W["contact_ms"]), hull=rr(W["hull"]))

    def _wpath(self, k):
        return self.dir / "worlds" / f"w{int(k):02d}.py"

    def _world(self, a, key="world", default_newest=False):
        """The world a call names -> its number, or None (the plain simulator)."""
        k, n = a.get(key), len(self.state["worlds"])
        if key == "world" and isinstance(k, int) and not isinstance(k, bool) and k < 0:
            k = None                                                # (-1: the plain simulator, as left out)
        if k is None:
            if not default_newest:
                return None
            if not n:
                raise ValueError("there is no world yet: write_world first")
            return n - 1
        if not self.worlds or isinstance(k, bool) or not isinstance(k, int) or not 0 <= k < n:
            raise ValueError(f"there is no world w{k}: " + (f"the worlds are w0 to w{n - 1}" if n else "write_world makes the first"))
        return k

    def _new_world(self, code, note, base=None):
        bad = practice.check_world(code)
        if bad:
            return [text("NOT saved, the world is not allowed:\n  " + "\n  ".join(bad) + "\n" + practice.RULES)]
        st = self.state
        k = len(st["worlds"])
        self._wpath(k).write_text(code if code.endswith("\n") else code + "\n")
        st["worlds"].append(dict(note=str(note)[:600], lines=len(code.splitlines()), base=base, at=dict(turn=st.get("turn", 0), usd=round(self.spent(), 2))))
        return [text(f"saved as w{k} ({st['worlds'][k]['lines']} lines)")]

    def _t_write_world(self, a):
        return self._new_world(a["code"], a.get("note", ""))

    def _t_edit_world(self, a):
        base = self._world(a, "base", default_newest=True)
        code, bad = self._wpath(base).read_text(), []
        for k, e in enumerate(a["edits"]):
            n = code.count(e["old"]) if e["old"] else 0
            if n != 1:
                bad.append(f"edit {k + 1}: its `old` text occurs {n} times in the world (after the edits before it), not once: {e['old'][:80]!r}")
            else:
                code = code.replace(e["old"], e["new"])
        if bad:
            return [text(f"NOT saved (w{base} is unchanged; no edit was applied):\n  " + "\n  ".join(bad))]
        return self._new_world(code, a.get("note", ""), base)

    def _t_read_world(self, a):
        k = self._world(a, default_newest=True)
        lines = self._wpath(k).read_text().splitlines()
        k0 = max(1, int(a.get("start_line") or 1)); k1 = min(len(lines), int(a.get("end_line") or len(lines)))
        m = self.state["worlds"][k]
        return [text(f"w{k} ({len(lines)} lines; {m['note']}), lines {k0} to {k1}:\n" + "\n".join(f"{i:4d}| {lines[i - 1]}" for i in range(k0, k1 + 1)))]

    def _worlds_text(self):
        """The worlds, for the text of a new conversation."""
        ws = self.state.get("worlds") or []
        if not self.worlds:
            return ""
        return ("\n\nYour worlds (read_world):\n" + ("\n".join(f"w{k} ({m['lines']} lines{', from w' + str(m['base']) if m.get('base') is not None else ''}): {m['note']}"
                                                                 for k, m in enumerate(ws)) or "none yet"))

    def _eval_names(self):
        return [f"E{k}" for k in range(EVAL_SET[1])] if self.eval_set else []

    def _real_closing(self):
        if self.real is None:
            return ""
        R, rr = scenes.REAL, lambda v: f"{v[0]:g}..{v[1]:g}"
        sim = _REAL_SIM_SCENES.format(x=rr(R["x"]), y=rr(R["y"]), sticks=rr(R["sticks"]), spacing=rr(R["spacing"]),
                                      board_long=rr(R["board_long"]), board=rr(R["board"])) if self.real_scenes else _REAL_SIM_SAME
        return (_REAL.format(sim=sim, sub=(self.state.get("continued") or {}).get("submitted", self.state["submitted"]))
                + (_SUMMARY if self.want_summary else ""))

    def _closing(self):
        if self.own_task:
            H = scenes.TABLE_H
            return (_OWN_SCENES.format(at_once=AT_ONCE_SCENES, shown=practice.SHOWN, rules="\n" + practice.RULES)
                    + (_WORLDS_OWN.format(h=f"{H[0]:g}..{H[1]:g}", colours=", ".join(scenes.TABLE_COLOURS), patterns=", ".join(p for p in scenes.PATTERNS if p != "plain")))
                    + self._real_closing() + (_OWN_RESULT if self.real is None else _OWN_RESULT.replace(*_OWN_NO_REAL)).format(budget=self.budget))
        T = scenes.TRAIN
        r = lambda key: f"{T[key][0]:g}..{T[key][1]:g}"
        B, P = scenes.TRAIN_TABLE, scenes.PATTERN_SIZE
        rr = lambda v: f"{v[0]:g}..{v[1]:g}"
        tab = dict(table_spec="", table="", table_eval="")
        if self.tables:
            tab = dict(table_spec=_TABLE_SPEC.format(h=rr(B["height"])),
                       table=_TABLE.format(h=rr(B["height"]), colours=", ".join(B["colours"]), patterns=", ".join(B["patterns"]),
                                           checker=rr(P["checker"][0]), stripes=rr(P["stripes"][0]), speckle=rr(P["speckle"][0]), angle="0..180"),
                       table_eval=_TABLE_EVAL.format(n_eval=self.n_eval, n=self.n_eval // 2))
        return _SCENES.format(
            **tab,
            points=practice.POINTS.get(self.task.name, ""), shown=practice.SHOWN, rules="\n" + practice.RULES, n_eval=self.n_eval, budget=self.budget,
            eval_tool=_EVAL_TOOL.format(last=EVAL_SET[1] - 1) if self.eval_set else "",
            like=f" (as this session's self-evaluation set is; its {EVAL_SET[1]} scenes are not among them)" if self.eval_set else "",
            dis_n=T["dis_n"][1], clear0=scenes.CLEARANCE, wall=scenes.WALL, colours=", ".join(scenes.COLOURS), gap=T["gap"], dis_gap=T["dis_gap"],
            dis_dis=T["dis_dis"], ruler=f"{scenes.RULER[0]:g}..{scenes.RULER[1]:g}",
            **{key: r(key) for key in ("cyl_d", "cyl_h", "density", "stick_d", "stick_h", "yaw", "clearance", "board", "board_t", "dis_size",
                                       "dis_h", "x", "y", "dis_x", "dis_y")}).replace(
            *(_NO_PROGRAM if self.state.get("continued") else ("", ""))) + self._worlds_closing() + self._real_closing() + (
            _SUMMARY if self.want_summary and self.real is None else "")

    def _intro(self):
        if self.own_task:
            return ASK_OWN.format(ask=self.more)
        return (self.task.text + " " + ASK_VARIED + (ASK_TABLES if self.tables else "") + (ASK_REAL if self.real is not None else "")
                + ("\n\n" + self.more if self.more else ""))

    def _t_submit(self, a):
        if self.own_task and a.get("final"):                        # (the world that sets the task goes with the result)
            if a.get("world") is None:
                return [text('The session has NOT ended: in this session final = true needs "world": the world that sets your task.')]
            k = self._world(a)
            tree = ast.parse(self._wpath(k).read_text())
            if not {"goal", "success"} <= {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}:
                return [text(f"The session has NOT ended: w{k} has no goal() and success(): it does not set a task.")]
            self.state["task_world"] = k
        return super()._t_submit(a)

    def _same_kind(self, src):
        return ("scenes" in src and "levels" not in src and bool(src.get("tables")) == self.tables and bool(src.get("eval_set")) == self.eval_set
                and bool(src.get("own_task")) == self.own_task)

    def _kind_state(self):
        return dict(eval_set=self.eval_set, tables=self.tables)

    def _continue(self):
        """The first message of a session that continues an ended one (self.source): its versions, scenes, episode
        results and notes are copied here; nothing of it is changed."""
        st, src = self.state, json.loads((self.source / "state.json").read_text())
        if not src.get("ended") or src.get("submitted") is None:
            raise ValueError(f"{self.source}: not an ended session with a submitted version (a stopped one goes on with --resume)")
        if not self._same_kind(src):
            raise ValueError(f"{self.source}: not a session of this kind (scenes, --tables, --eval-set; or the levels and the arm)")
        for f in sorted((self.source / "versions").glob("v*.py")):
            shutil.copy(f, self.dir / "versions" / f.name)
        if (self.source / "runs").exists() and not (self.dir / "runs").exists():
            shutil.copytree(self.source / "runs", self.dir / "runs", ignore=shutil.ignore_patterns("*.mp4"))
        sub_ = int(src["submitted"])
        if (self.source / "worlds").exists():
            (self.dir / "worlds").mkdir(parents=True, exist_ok=True)   # (a continued session takes the worlds over as well)
            for f in sorted((self.source / "worlds").glob("w*.py")):
                shutil.copy(f, self.dir / "worlds" / f.name)
            st["worlds"] = list(src.get("worlds") or [])
        st.update(versions=src["versions"], scenes=src["scenes"], n_scenes=src["n_scenes"], notes=src.get("notes", ""), submitted=sub_,
                  **self._kind_state(),
                  continued=dict(source=str(self.source), submitted=sub_, versions=len(src["versions"]), usd=src.get("usd")))
        vs = "\n".join(f"v{v} ({m['lines']} lines{', from v' + str(m['base']) if m['base'] is not None else ''}): {m['note']} | practice: "
                       + (self._practice(m["runs"]) or "not run") for v, m in enumerate(st["versions"]))
        if self.real is not None:
            ran = [f"{n} (v{e['version']}: {e['verdict']})" if e.get("version") is not None else f"{n} ({e['verdict']})" for n, e in self.episodes.items()]
            real = f"{len(ran)} episode{'s' if len(ran) != 1 else ''}, " + ", ".join(ran)
        else:
            real = "no episode is given"
        t = self._intro() + _CONTINUED.format(sub=sub_, versions=vs, results=self.results_line(), notes=(st["notes"] or "(none)") + self._worlds_text(),
                                              real=real, code=self._path(sub_).read_text())
        return [{"role": "user", "content": [{"type": "input_text", "text": t}]}], []

    def _first(self):
        if self.source is not None:
            return self._continue()
        st = self.state
        st["eval_set"] = self.eval_set
        st["tables"] = self.tables
        st["scenes"] = {"example": scenes.empty(0) if self.own_task else scenes.sample(EXAMPLE)}
        st["scenes"].update({n: scenes.sample(EVAL_SET[0] + k) for k, n in enumerate(self._eval_names())})
        st["n_scenes"] = 0
        sc = st["scenes"]["example"]
        ep = Episode(self.env, self.task, seed=sc["seed"], sensors=self.sensors, scene=sc)
        images = []
        return [{"role": "user", "content": [{"type": "input_text", "text": self._intro() + (_FIRST_OWN if self.own_task else _FIRST_SCENE)}]
                 + openai_items(Toolbox(ep.robot).call("look", {}, free=True), images)}], images

    def results_line(self):
        out, ev = [], set(self._eval_names())
        for v, m in enumerate(self.state["versions"]):
            parts = []
            ws = sorted({s.split("@")[1] for s in m["runs"] if "@" in s}, key=lambda w: int(w[1:]))
            for what, runs in ([("", [r for s, r in m["runs"].items() if s not in ev and "@" not in s]),
                                ("self-evaluation set ", [r for s, r in m["runs"].items() if s in ev])]
                               + [(f"in {w} ", [r for s, r in m["runs"].items() if s.endswith("@" + w)]) for w in ws]):
                if runs:
                    ok = [r[1] for r in runs if r[0]]
                    parts.append(f"{what}{len(ok)}/{len(runs)}" + (f" ({sum(ok) / len(ok):.0f} s)" if ok else ""))
            if parts:
                out.append(f"v{v} " + ", ".join(parts))
        sub = self.state["submitted"]
        return ("Practice so far (successes/episodes, mean robot time of the successes): " + ("; ".join(out) or "nothing run")
                + f". Submitted: {'v' + str(sub) if sub is not None else 'nothing'}.")

    # ------------------------------------------------------------------ the tools
    def _t_define_scenes(self, a):
        st, out = self.state, []
        specs = a["scenes"]
        if not isinstance(specs, list) or not specs or len(specs) > 12:
            raise ValueError("scenes: a list of 1 to 12 scene objects")
        for spec in specs:
            n = st["n_scenes"] + 1
            try:
                if not isinstance(spec, dict):
                    raise ValueError("a scene is a JSON object")
                seed = spec.get("seed", n)
                if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 1 << 30:
                    raise ValueError("seed: a whole number from 0 on")
                if "table" in spec and not self.tables:
                    raise ValueError("scene: unknown field(s) table")
                sc = scenes.build(spec, seed=seed, real=self.real_scenes)
            except (ValueError, TypeError, KeyError) as e:
                out.append(f"REFUSED ({json.dumps(spec)[:200]}): {str(e) or type(e).__name__}")
                continue
            st["n_scenes"] = n
            st["scenes"][f"s{n}"] = sc
            out.append(f"s{n} (seed {sc['seed']}): {scenes.describe(sc)}")
        return [text("\n".join(out))]

    def _names(self, names):
        names = list(dict.fromkeys(str(n) for n in names))
        bad = [n for n in names if n not in self.state["scenes"]]
        if bad:
            raise ValueError(f"no scene {', '.join(bad)}: there are {self._scene_names()}")
        return names

    def _scene_names(self):
        st, ev = self.state, self._eval_names()
        return ("example" + (f", {ev[0]} to {ev[-1]}" if ev else "")
                + (f", s1 to s{st['n_scenes']}" if st["n_scenes"] > 1 else ", s1" if st["n_scenes"] else "") + (" (new_scenes makes more)" if self.own_task else " (define_scenes makes more)"))

    def _t_list_scenes(self, a):
        names = self._names(a.get("names") or list(self.state["scenes"]))
        return [text("\n".join(f"{n} (seed {self.state['scenes'][n]['seed']}): {scenes.describe(self.state['scenes'][n])}" for n in names))]

    def _real_episode(self, a):
        n = str(a.get("episode"))
        if n not in self.episodes:
            raise ValueError(f"no real episode {n!r}: there are {', '.join(self.episodes)}")
        return n, self.real / n

    def _t_list_real_episodes(self, a):
        want = [str(n) for n in (a.get("episodes") or [])]
        bad = [n for n in want if n not in self.episodes]
        if bad or len(want) > 4:
            raise ValueError(f"episodes: at most 4 of {', '.join(self.episodes)}")
        head = lambda n, e: (f"{n}" + (f": v{e['version']}" if e.get("version") is not None else "") + f", {e['when']}. The operator's verdict: {e['verdict']}"
                             + (f' ("{e["operator_note"]}")' if e["operator_note"] else "") + ("; the operator intervened" if e["operator_intervened"] else "")
                             + ("; something was knocked off" if e.get("knocked_off") else "") + ".")
        secs = lambda e: f"{e['robot_time_s']:.0f} s" if e.get("robot_time_s") is not None else "an unknown time"
        if not want:
            out = ["THE REAL ROBOT\n\n" + (self.real / "rig.md").read_text().strip(), "\nTHE EPISODES (list_real_episodes with `episodes` gives one in full)"]
            for n, e in self.episodes.items():
                out.append(f"\n{head(n, e)}\n  Setup: {e['setup']}\n  Moves until contact: {e.get('contact') or 'as the rig text says'}\n"
                           f"  The program {e['program_ended'] or 'ended'} after {secs(e)} of the rig's time; {len(e['moves_until_contact'])} moves until contact; "
                           + (f"{len(e['asks'])} model calls; " if e.get("asks") else "") + f"output.txt has {e['output_lines']} lines.")
            return [text("\n".join(out))]
        out = []
        for n in want:
            e = self.episodes[n]
            imgs = [l["image"] for l in e["looks"]]
            looks = "; ".join(f"{l['image']} (t {l['robot_time_s']:g} s; {100 * l['pixels_without_depth']:.0f}% of its pixels without depth)" for l in e["looks"])
            imgs += [x + "_depth" for x in imgs] + [x + "_nodepth" for x in imgs] + e["shown"] + [x for v in e["video_frames"].values() for x in v]
            moves = "\n".join(
                f"    t {m['t']:g} s: to z {m['goal_mm'][2]:g} mm -> {m['result']}; the tool then at z {m['tool_after_mm'][2]:g} mm"
                + (f" ({m['server_stops_not_passed_on']} stops of the arm's server not passed on, {m['ordinary_steps']} ordinary steps on the way)"
                   if m.get("server_stops_not_passed_on") or m.get("ordinary_steps") else "") for m in e["moves_until_contact"]) or "    none"
            L = e.get("server_guarded_lines") or []
            st = [x for x in L if x["ended"] != "ran to its end"]
            rng = lambda k: (f"{min(x[k] for x in st if x.get(k) is not None):g} to {max(x[k] for x in st if x.get(k) is not None):g}"
                             if any(x.get(k) is not None for x in st) else "?")
            server = (f"{len(L)} guarded lines: {len(L) - len(st)} ran to their end, {len(st)} were stopped by the server"
                      + (f" (the hand {rng('hand_behind_mm')} mm behind, force change {rng('force_change_n')} N, after {rng('came_down_mm')} mm of way down)" if st else "")
                      + ("; lag allowed " + ", ".join(sorted({format(x["lag_allowed_mm"], "g") for x in L})) + " mm" if L else ""))
            asks, seen_q = "", []
            for k, q in enumerate(e.get("asks") or []):
                new = q["question"] not in seen_q
                seen_q += [q["question"]] if new else []
                asks += (f"\n    ask {q['n']} at t {q['t']:g} s, {len(q['images'])} images ({', '.join(q['images'])}), effort {q.get('effort')}: "
                         + (f"question {seen_q.index(q['question']) + 1}" + (f": {q['question'][:1500]!r}" if new else " again"))
                         + f" -> {q['answer']!r}" + (f" (${q['usd']:.3f}, {q['wait_s']:g} s)" if q.get("usd") is not None else ""))
                imgs += q["images"]
            out.append(f"{head(n, e)}\n  Setup: {e['setup']}\n  Moves until contact: {e.get('contact') or 'as the rig text says'}\n"
                       f"  The program {e['program_ended'] or 'ended'} after {secs(e)} of the rig's time; output.txt has {e['output_lines']} lines.\n"
                       f"  Its moves until contact, as the program saw them:\n{moves}\n  The arm's server: {server}\n  Its looks: {looks}\n"
                       + (f"  Its model calls (robot.ask):{asks}\n" if asks else "") + f"  Images: {', '.join(imgs)}")
        return [text("\n\n".join(out))]

    def _t_read_real_output(self, a):
        n, d = self._real_episode(a)
        lines = (d / "output.txt").read_text(errors="replace").splitlines()
        k0, m = max(1, int(a.get("start_line") or 1)), min(200, max(1, int(a.get("max_lines") or 60)))
        has, tr = a.get("contains"), a.get("trace", True)
        sel = [(k, l) for k, l in enumerate(lines, 1)
               if k >= k0 and (tr or not l.startswith("#")) and (not has or has.lower() in l.lower())]
        out = [f"{k:5d}| {l[:400]}" for k, l in sel[:m]]
        more = f"; the next one is line {sel[m][0]}" if len(sel) > m else ""
        return [text(f"real episode {n}: output.txt has {len(lines)} lines; {len(sel)} from line {k0} on match; {len(out)} shown{more}\n" + "\n".join(out))]

    def _t_read_real_depth(self, a):
        import cv2
        n, d = self._real_episode(a)
        name = str(a.get("look", "")).replace("_depth", "").replace("_nodepth", "")
        look = next((l for l in self.episodes[n]["looks"] if l["image"] == name), None)
        if look is None:
            raise ValueError(f"no look {name!r} in real episode {n}: there are {', '.join(l['image'] for l in self.episodes[n]['looks'])}")
        dep = cv2.imread(str(d / "depth" / f"{name}.png"), cv2.IMREAD_UNCHANGED)
        H, W = dep.shape
        u0, v0 = max(0, int(a.get("u0") or 0)), max(0, int(a.get("v0") or 0))
        u1 = min(W - 1, int(a["u1"]) if a.get("u1") is not None else W - 1); v1 = min(H - 1, int(a["v1"]) if a.get("v1") is not None else H - 1)
        if u1 < u0 or v1 < v0:
            raise ValueError("the window: u0 <= u1 and v0 <= v1")
        win = dep[v0:v1 + 1, u0:u1 + 1]; ok = win[win > 0]
        us = sorted({int(round(x)) for x in np.linspace(u0, u1, min(24, u1 - u0 + 1))}); vs = sorted({int(round(x)) for x in np.linspace(v0, v1, min(18, v1 - v0 + 1))})
        K = look.get("K") or []
        head = (f"real episode {n}, {name}: depth in mm along the camera's axis (0 = no depth); the image is {W} x {H}"
                + (f", fx {K[2]:.1f}, fy {K[3]:.1f}, cx {K[4]:.1f}, cy {K[5]:.1f}" if len(K) == 6 else "") + f".\nWindow u {u0}..{u1}, v {v0}..{v1}: "
                + (f"{100 * (1 - len(ok) / win.size):.0f}% of its pixels without depth; of the others: nearest {int(ok.min())}, 5% {int(np.percentile(ok, 5))}, "
                   f"median {int(np.median(ok))}, 95% {int(np.percentile(ok, 95))}, farthest {int(ok.max())} mm." if len(ok) else "no pixel has depth."))
        grid = "\n".join([" v \\ u " + "".join(f"{u:5d}" for u in us)] + [f"{v:6d} " + "".join(f"{int(dep[v, u]):5d}" for u in us) for v in vs])
        from .api import depth_report
        rep = depth_report(np.where(dep > 0, dep.astype(float), np.nan))
        return [text(head + "\n" + grid + "\nThe whole look's depth report (what frame.depth_report() gives for a simulated frame): " + json.dumps(rep))]

    def _t_view_real_images(self, a):
        n, d = self._real_episode(a)
        have = {p.stem: p for p in sorted((d / "images").iterdir())}
        items = []
        for name in list(a["names"])[:6]:
            name = str(name)
            hit = have.get(name) or have.get(name.rsplit(".", 1)[0])
            if hit is None:
                items.append(text(f"no image {name!r} in real episode {n}: list_real_episodes gives the names"))
            else:
                items += [text(f"real episode {n}, {hit.stem}:"), _image(hit)]
        return items or [text("names: which images?")]

    def _run_dir(self, v, name):
        return self.dir / "runs" / f"v{v:02d}" / str(name)

    def _run(self, v, names, world=None):
        """Run version v on the scenes `names` (those not yet run), in the plain simulator or in world w<world>: the
        episode is then kept as <scene>@w<world>."""
        key = (lambda n: n) if world is None else (lambda n: f"{n}@w{world}")
        new = [n for n in names if not (self._run_dir(v, key(n)) / "result.json").exists()]
        if new:
            if self.segmenter and self.bridge is None:
                self.bridge = practice.SegmenterBridge()
            sc = self.state["scenes"]
            wf = None if world is None else self._wpath(world)
            with ThreadPoolExecutor(len(new)) as ex:                # (practice.SLOTS bounds how many run at the same time)
                wall = 900.0 * max(1.0, (self.episode_s or 0.0) / self.task.time_limit_s)     # (the process's own limit grows with the episode's)
                list(ex.map(lambda n: practice.run_sandboxed(self.env, self.task.name, sc[n]["seed"], self._path(v), self._run_dir(v, key(n)),
                                                              sensors=self.sensors, trace=True, bridge=self.bridge, wall_s=wall,
                                                              time_limit_s=self.episode_s, scene=sc[n], practice=True, world=wf), new))
        return [key(n) for n in names]

    def _t_run_episodes(self, a):
        v, names, world = self._version(a), self._names(a["scenes"]), self._world(a)
        if not names:
            raise ValueError(f"scenes: which? There are {self._scene_names()}")
        if self.own_task and world is None:
            raise ValueError('world: which? In this session an episode runs in a world of yours ("world": k): the table is empty without one')
        key = (lambda n: n) if world is None else (lambda n: f"{n}@w{world}")
        if sum(not (self._run_dir(v, key(n)) / "result.json").exists() for n in names) > AT_ONCE_SCENES:
            raise ValueError(f"at most {AT_ONCE_SCENES} new episodes a call" + (" (run_eval_set runs the whole self-evaluation set)" if self.eval_set else ""))
        return self._results(v, self._run(v, names, world))

    def _t_run_eval_set(self, a):
        v, names = self._version(a), self._eval_names()
        self._run(v, names)
        return self._results(v, names, brief=True)

    def _episode(self, a):
        v, n, world = self._version(a), str(a["scene"]).split("@")[0], self._world(a)
        if world is None and "@w" in str(a["scene"]) and str(a["scene"]).split("@w")[1].isdigit():   # ("s3@w1" as the results name it)
            world = self._world(dict(world=int(str(a["scene"]).split("@w")[1])))
        key = n if world is None else f"{n}@w{world}"
        d = self._run_dir(v, key)
        if n not in self.state["scenes"] or not (d / "output.txt").exists():
            raise ValueError(f"v{v} was not run on a scene {n}" + ("" if world is None else f" in world w{world}"))
        return v, key, d


def _opt(spec):
    """A field that can also be null (= left out)."""
    spec = dict(spec, type=[spec["type"], "null"])
    if "enum" in spec:
        spec["enum"] = list(spec["enum"]) + [None]
    return spec


def _given(v):
    """A scene's spec without the fields given as null, "" or [] (= left out; `objects` keeps its place in the list)."""
    if isinstance(v, dict):
        return {k: _given(x) for k, x in v.items() if x is not None and x != "" and x != []}
    return [_given(x) for x in v] if isinstance(v, list) else v


_PART = _obj(type={"type": "string", "enum": list(clutter.PARTS)}, size_mm={"type": "array", "items": _N},
             pos_mm=_opt({"type": "array", "items": _N}), euler_deg=_opt({"type": "array", "items": _N}))
_CLUTTER_OBJECT = _obj(
    "an object (the instructions: Scenes of your own); every field can be left out, or be null",
    shape=_opt({"type": "string", "enum": list(clutter.SHAPES) + ["custom"]}), size_mm=_opt({"type": "array", "items": _N}),
    color=_opt({"type": "string"}), rgb=_opt({"type": "array", "items": {"type": "integer"}}), density=_opt(_N),
    role=_opt({"type": "string", "enum": list(clutter.ROLES), "description": "not for the target"}),
    name=_opt({"type": "string", "description": "a custom object's name"}), parts=_opt({"type": "array", "items": _PART}),
    pos_mm=_opt({"type": "array", "items": _N}), euler_deg=_opt({"type": "array", "items": _N}))
_NAMES = {"type": "array", "items": {"type": "string", "description": "a scene's name, or a range such as s10..s49"}}


def _clutter_tools(levels, real=False, worlds=False):
    keep = {s["name"]: s for s in TOOLS}
    w = {"world": _WORLD} if worlds else {}
    lv = {"type": "string", "enum": list(levels)}
    scene = _obj("a scene (the instructions: Scenes of your own); every field but level can be left out, or be null",
                 level=lv, seed=_opt({"type": "integer"}), n_objects=_opt({"type": "integer"}),
                 centre_mm=_opt(_XY), target=_opt(_CLUTTER_OBJECT), objects=_opt({"type": "array", "items": _CLUTTER_OBJECT}))
    scene["required"] = ["level"]
    out = [keep["write_program"], keep["edit_program"],
           _spec("sample_scenes", "Draw scenes at random from a level's environment -> their names, one line each.",
                 {"level": lv, "first_seed": {"type": "integer", "description": f"0 to {CLUTTER_SEEDS - 1}"},
                  "count": {"type": "integer", "description": "how many (the seeds first_seed, first_seed + 1, ...): at most 60"}},
                 ["level", "first_seed", "count"]),
           _spec("define_scenes", "Set up scenes of your own -> their names and what is in each.",
                 {"scenes": {"type": "array", "items": scene}}, ["scenes"]),
           _spec("list_scenes", "What is in scenes: every object's shape, sizes, colour, density and place (without names, or for more "
                                "than 12 scenes: one line a scene).", {"names": _NAMES}),
           _spec("run_episodes", f"Run a version on scenes, one episode each, with the whole result of each (at most {AT_ONCE_SCENES} new "
                                 "ones a call, run at the same time; some minutes).", {"scenes": _NAMES, "version": _VER, **w}, ["scenes"]),
           _spec("run_set", f"Run a version on many scenes (at most {SET_N} a call): one line per episode, and the totals by level.",
                 {"scenes": _NAMES, "version": _VER, **w}, ["scenes"])]
    for name in ("read_output", "view_images"):
        p = keep[name]["parameters"]
        props = {("scene" if k == "seed" else k): (_NAME if k == "seed" else x) for k, x in p["properties"].items()}
        props.update(w)
        out.append(_spec(name, keep[name]["description"], props, ["scene" if k == "seed" else k for k in p["required"]]))
    return out + (WORLD_TOOLS if worlds else []) + (REAL_TOOLS if real else []) + [keep["read_program"], keep["submit"]]


class ClutterSession(SceneSession):
    """A session on picking from clutter (clutter.py), several levels at once: one program for all of them. The model
    starts from nothing; it draws scenes from the levels' environments (sample_scenes: the seeds below CLUTTER_SEEDS,
    which the evaluation does not use), builds heaps and objects of its own (define_scenes: clutter.build), and runs
    versions on any of them, also on many at once (run_set: its own evaluation sets). Practice episodes run with
    the simulation's truth, and programs have robot.ask (practice.AskBridge): ask(label) -> the model client of one
    episode, whose calls are booked on the session's budget."""

    _WALL = practice.WALL_S

    def __init__(self, out, env, levels, vlm, ask=None, arm=None, more=None, **k):
        """arm="left": the one-armed robot's environments (clutter_one_arm.py). ask: label -> the model client of one
        episode's robot.ask. more: more that is asked of the skill (--ask). k: also SceneSession's real (a pack of
        real episodes), continue_from (an ended clutter session), summary and worlds."""
        self.levels, self.ask, self.arm = tuple(levels), ask, arm
        if not self.levels or any(l not in ("medium", "hard") for l in self.levels):
            raise ValueError("levels: medium, hard")
        if arm not in clutter.ARMS or (arm and env != "sim_bimanual_grippers"):
            raise ValueError('arm: "left" (the one-armed robot, in sim_bimanual_grippers) or none')
        super().__init__(out, env, self._task(self.levels[0]), vlm, ask=more, **k)
        if tuple(self.state.get("levels", self.levels)) != self.levels:
            raise ValueError(f"this session was started with the levels {', '.join(self.state['levels'])}")
        if self.state.get("arm", arm) != arm:
            raise ValueError(f"this session was started {'with the ' + self.state['arm'] + ' arm alone' if self.state.get('arm') else 'with both arms'}")

    def _task(self, level):
        return clutter_one_arm.task(level) if self.arm else f"pick_clutter_{level}"

    def _specs(self):
        return _clutter_tools(self.levels, real=self.real is not None, worlds=self.worlds)

    def _same_kind(self, src):
        return "scenes" in src and tuple(src.get("levels") or ()) == self.levels and src.get("arm") == self.arm

    def _kind_state(self):
        return dict(eval_set=False, tables=False, levels=list(self.levels), arm=self.arm)

    def _worlds_closing(self):
        if not self.worlds:
            return ""
        W, rr = clutter.WORLD, lambda v: f"{v[0]:g}..{v[1]:g}"
        return _WORLDS_CLUTTER.format(n=W["n"][1], size=rr(W["size"]), density=rr(W["density"]), x=rr(W["x"]), y=rr(W["y"]), z=rr(W["z"]),
                                      parts=W["parts"], extras=scenes.WORLD["extras"], eparts=scenes.WORLD["parts"],
                                      friction=rr(scenes.WORLD["friction"]), contact_ms=rr(scenes.WORLD["contact_ms"]), hull=rr(scenes.WORLD["hull"]))

    def _eval_names(self):
        return []

    def _limit(self, level):
        return TASKS[self._task(level)].time_limit_s

    def _instructions(self, kind):
        model = (getattr(self.vlm, "m", None) or {}).get("model", "the model you are")
        return instructions(kind, " or ".join(f"{self._limit(l):.0f} s ({l})" for l in self.levels), self.segmenter,
                            closing=self._closing(), asker=model if self.ask is not None else None, arm=self.arm)

    def _closing(self):
        T, S, L = clutter.TRAIN, clutter.SEEN, clutter.LIMITS
        P, O = (clutter.ONE_ARM, clutter.ONE_ARM["limits"]) if self.arm else (T, L)       # where the heaps are; a scene of one's own
        r = lambda v: f"{v[0]:g}..{v[1]:g}"
        return (_one_arm(_CLUTTER) if self.arm else _CLUTTER).format(
            at_once=AT_ONCE_SCENES, set_n=SET_N, points=practice.POINTS[self._task("medium")], shown=practice.SHOWN,
            rules="\n" + practice.RULES, n_eval=self.n_eval, budget=self.budget, seed_hi=CLUTTER_SEEDS - 1,
            n_medium=f"{T['n']['medium'][0]} to {T['n']['medium'][1]}", n_hard=f"{T['n']['hard'][0]} to {T['n']['hard'][1]}",
            m_scene=S["medium"]["scene"][0], m_top=S["medium"]["top"][0], h_scene=S["hard"]["scene"][1], h_top=S["hard"]["top"][1],
            t_medium=self._limit("medium"), t_hard=self._limit("hard"), cover="90..130 x 55..75 x 10..25 mm",
            x=r(P["x"]), y=r(P["y"]), ruler=r(clutter.RULER), density=r(T["density"]), colours=", ".join(clutter.COLOURS),
            tb="28..58", tcd="28..58", tch="28..65", tball="32..58", ob="25..80 x 25..80 x 20..80", ocd="25..75", och="20..90", oball="28..65",
            size=r(L["size"]), dens=r(L["density"]), n_own=r(L["n"]), own_x=r(O["x"]), own_y=r(O["y"]), parts=L["parts"],
            tries=clutter.BUILD_TRIES).replace(*(_NO_PROGRAM_CLUTTER if self.state.get("continued") else ("", ""))) + (
            self._worlds_closing() + self._real_closing() + (_SUMMARY if self.want_summary and self.real is None else ""))

    def _intro(self):
        T = clutter_one_arm.TEXT if self.arm else clutter.TEXT
        t = "\n\n".join(f"The {l} level. {T[l]}" for l in self.levels)
        return (f"Picking from clutter{' with one arm' if self.arm else ''}, at two levels.\n\n" + t + "\n\n" + ASK_CLUTTER
                + (ASK_REAL if self.real is not None else "") + ("\n\n" + self.more if self.more else ""))

    def _first(self):
        if self.source is not None:
            return self._continue()
        st = self.state
        st["eval_set"], st["tables"], st["levels"], st["arm"] = False, False, list(self.levels), self.arm
        st["scenes"] = {f"example_{l}": clutter.sample(l, CLUTTER_EXAMPLE, self.arm) for l in self.levels}
        st["n_scenes"] = 0
        images, content = [], [{"type": "input_text", "text": self._intro() + (_one_arm(_FIRST_CLUTTER) if self.arm else _FIRST_CLUTTER)}]
        for n, sc in st["scenes"].items():
            ep = Episode(self.env, self._task(sc["level"]), seed=sc["seed"], sensors=self.sensors, scene=sc)
            content.append({"type": "input_text", "text": f"\n{n}. Its task text: {ep.text}"})
            content += openai_items(Toolbox(ep.robot).call("look", {}, free=True), images)
        return [{"role": "user", "content": content}], images

    def _level(self, name):
        return self.state["scenes"][str(name).split("@")[0]]["level"]

    def _by_level(self, runs):
        """runs {scene: [ok, robot_s, model calls, $]} -> text: by level, successes/episodes (mean robot time of the
        successes, mean model calls an episode); the episodes in a world (scene@wK) apart, after the plain ones."""
        parts, sc = [], self.state["scenes"]
        worlds = sorted({s.split("@")[1] for s in runs if "@" in s}, key=lambda w: int(w[1:]) if w[1:].isdigit() else 0)
        for w in [None] + worlds:
            for l in self.levels:
                rs = [r for s, r in runs.items() if s.split("@")[0] in sc and self._level(s) == l
                      and (s.split("@")[1] if "@" in s else None) == w]
                if rs:
                    ok = [r[1] for r in rs if r[0]]
                    calls = [r[2] for r in rs if len(r) > 2]
                    extra = ([f"{sum(ok) / len(ok):.0f} s"] if ok else []) + ([f"{sum(calls) / len(calls):.1f} calls"] if calls else [])
                    parts.append((f"in {w} " if w else "") + f"{l} {len(ok)}/{len(rs)}" + (f" ({', '.join(extra)})" if extra else ""))
        return ", ".join(parts)

    def results_line(self):
        out = [f"v{v} " + self._by_level(m["runs"]) for v, m in enumerate(self.state["versions"]) if m["runs"]]
        sub = self.state["submitted"]
        return ("Practice so far, by level (successes/episodes; mean robot time of the successes, mean model calls an episode): "
                + ("; ".join(out) or "nothing run") + f". Submitted: {'v' + str(sub) if sub is not None else 'nothing'}.")

    def _practice(self, runs):
        if len(runs) <= 12:
            return super()._practice(runs)
        bad = [s for s, r in runs.items() if not r[0]]
        return self._by_level(runs) + (f"; failed on {', '.join(bad[:40])}{' ...' if len(bad) > 40 else ''}" if bad else "")

    # ------------------------------------------------------------------ the tools
    def _add(self, spec, out, full):
        st = self.state
        n = st["n_scenes"] + 1
        try:
            if not isinstance(spec, dict):
                raise ValueError("a scene is a JSON object")
            spec = _given(spec)
            seed = spec.get("seed", n % CLUTTER_SEEDS)
            drawn = not set(spec) - {"level", "seed"}
            if drawn and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < CLUTTER_SEEDS):
                raise ValueError(f"seed: a scene drawn from an environment has a seed from 0 to {CLUTTER_SEEDS - 1}")
            sc = clutter.build(spec, seed=seed, levels=self.levels, arm=self.arm)
        except (ValueError, TypeError, KeyError, RuntimeError) as e:
            out.append(f"REFUSED ({json.dumps(spec)[:200]}): {str(e) or type(e).__name__}")
            return
        st["n_scenes"] = n
        st["scenes"][f"s{n}"] = sc
        out.append(f"s{n} (seed {sc['seed']}): " + (clutter.describe(sc) if full else clutter.brief(sc)))

    def _t_sample_scenes(self, a):
        level, first, count = a["level"], int(a["first_seed"]), int(a["count"])
        if level not in self.levels:
            raise ValueError(f"level: one of {', '.join(self.levels)}")
        if not 1 <= count <= 60 or not 0 <= first or first + count > CLUTTER_SEEDS:
            raise ValueError(f"count: 1 to 60; the seeds first_seed to first_seed + count - 1 must be within 0 to {CLUTTER_SEEDS - 1}")
        out = []
        with ThreadPoolExecutor(16) as ex:                          # (clutter.sample keeps them)
            list(ex.map(lambda seed: clutter.sample(level, seed, self.arm), range(first, first + count)))
        for seed in range(first, first + count):
            self._add({"level": level, "seed": seed}, out, False)
        return [text("\n".join(out))]

    def _t_define_scenes(self, a):
        specs, out = a["scenes"], []
        if not isinstance(specs, list) or not specs or len(specs) > 12:
            raise ValueError("scenes: a list of 1 to 12 scene objects")
        for spec in specs:
            self._add(spec, out, isinstance(spec, dict) and bool(set(_given(spec)) - {"level", "seed"}))
        return [text("\n".join(out))]

    def _names(self, names):
        out = []
        for n in names:
            n = str(n).strip()
            if ".." in n:                                       # s10..s49
                a, b = (x.strip() for x in n.split("..", 1))
                if not (a[:1] == "s" and b[:1] == "s" and a[1:].isdigit() and b[1:].isdigit() and int(a[1:]) <= int(b[1:])):
                    raise ValueError(f"{n}: a range is sA..sB with A <= B")
                out += [f"s{k}" for k in range(int(a[1:]), int(b[1:]) + 1)]
            else:
                out.append(n)
        return super()._names(out)

    def _scene_names(self):
        k = self.state["n_scenes"]
        return (", ".join(f"example_{l}" for l in self.levels) + (f", s1 to s{k}" if k > 1 else ", s1" if k else "")
                + " (sample_scenes and define_scenes make more)")

    def _t_list_scenes(self, a):
        sc = self.state["scenes"]
        names = self._names(a.get("names") or list(sc))
        full = bool(a.get("names")) and len(names) <= 12
        return [text("\n".join(f"{n} (seed {sc[n]['seed']}): " + (clutter.describe(sc[n]) if full else clutter.brief(sc[n])) for n in names))]

    def _run(self, v, names, world=None):
        """Run version v on the scenes `names` (those not yet run), in the plain simulator or in world w<world> (the
        episode is then kept as <scene>@w<world>) -> the episodes' names."""
        key = (lambda n: n) if world is None else (lambda n: f"{n}@w{world}")
        new = [n for n in names if not (self._run_dir(v, key(n)) / "result.json").exists()]
        if new:
            if self.segmenter and self.bridge is None:
                self.bridge = practice.SegmenterBridge()
            if self.ask is not None and self.asker is None:
                self.asker = practice.AskBridge(self.ask, dir=self.bridge.dir if self.bridge is not None else None)
            sc = self.state["scenes"]
            wf = None if world is None else self._wpath(world)
            with ThreadPoolExecutor(len(new)) as ex:                # (practice.SLOTS bounds how many run at the same time)
                list(ex.map(lambda n: practice.run_sandboxed(
                    self.env, self._task(sc[n]["level"]), sc[n]["seed"], self._path(v), self._run_dir(v, key(n)), sensors=self.sensors,
                    trace=True, bridge=self.bridge, wall_s=self._WALL, scene=sc[n], practice=True, asker=self.asker,
                    ask_label=f"v{v}_{key(n).replace('@', '_')}", world=wf), new))
        return [key(n) for n in names]

    def _totals(self, v, names):
        runs = self.state["versions"][v]["runs"]
        usd = sum(r[3] for n in names if len(r := runs.get(n, [])) > 3)
        return text(f"v{v} on these {len(names)} scenes, by level: " + (self._by_level({n: runs[n] for n in names if n in runs}) or "no result")
                    + f"; their model calls cost ${usd:.3f}.")

    def _t_run_episodes(self, a):
        v, names, world = self._version(a), self._names(a["scenes"]), self._world(a)
        if not names:
            raise ValueError(f"scenes: which? There are {self._scene_names()}")
        key = (lambda n: n) if world is None else (lambda n: f"{n}@w{world}")
        if sum(not (self._run_dir(v, key(n)) / "result.json").exists() for n in names) > AT_ONCE_SCENES:
            raise ValueError(f"at most {AT_ONCE_SCENES} new episodes a call (run_set runs many, with one line per episode)")
        return self._results(v, self._run(v, names, world))

    def _t_run_set(self, a):
        v, names, world = self._version(a), self._names(a["scenes"]), self._world(a)
        if not 1 <= len(names) <= SET_N:
            raise ValueError(f"scenes: 1 to {SET_N} scenes a call")
        keys = self._run(v, names, world)
        items = self._results(v, keys, brief=True)
        return items[:-1] + [self._totals(v, keys), items[-1]]


def _said(r):
    return "\n".join(c.get("text", "") for o in r["output"] if o.get("type") == "message"
                     for c in o.get("content") or [] if c.get("type") == "output_text").strip()


def _image(path):
    import cv2
    return {"type": "image", "rgb": cv2.imread(str(path))[..., ::-1].copy(), "name": Path(path).stem}


def _texts_of(items):
    return "\n".join(i["text"] if i["type"] == "text" else f"[image: {i.get('name', '')}]" for i in items)


def _texts(inp):
    """The text of pending input items (already in the model client's form)."""
    out = []
    for i in inp:
        for c in i.get("output") or i.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "input_text":
                out.append(c["text"])
    return "\n".join(out)


def _short(args):
    if not isinstance(args, dict):
        return args
    return {k: (f"<{len(v.splitlines())} lines>" if k == "code" and isinstance(v, str) else v) for k, v in args.items()}


def _printed(d, head=12, tail=25):
    f = Path(d) / "output.txt"
    if not f.exists():
        return ""
    pr = [l[:200] for l in f.read_text(errors="replace").splitlines() if not l.startswith("#")]
    if not pr:
        return "\n  it printed nothing"
    if len(pr) <= head + tail:
        return f"\n  printed ({len(pr)} lines):\n" + "\n".join("    " + l for l in pr)
    return (f"\n  printed (the first {head} and the last {tail} of {len(pr)} lines):\n" + "\n".join("    " + l for l in pr[:head])
            + "\n    ...\n" + "\n".join("    " + l for l in pr[-tail:]))


_BOOK = ("ok", "task", "env", "seed", "robot_s", "mode", "ended", "sensors", "commands", "shown", "true_start_mm", "true_end_mm", "wall_s",
         "scene", "model_calls", "model_usd", "model_wait_s", "task_text", "judged_by")


def _by_world(r):
    """" (the world's success())" for an episode judged by its world's own check."""
    return " (the world's success())" if r.get("judged_by") == "world" else ""


def _asked(r, brief=False):
    """The episode's model calls (robot.ask), if it could make any."""
    if "model_calls" not in r:
        return ""
    if brief:
        return f", {r['model_calls']} model call{'' if r['model_calls'] == 1 else 's'}"
    return (f", {r['model_calls']} model call{'' if r['model_calls'] == 1 else 's'}"
            + (f" (${r['model_usd']:.3f}; {r['model_wait_s']:.0f} s of the robot time waiting for them)" if r["model_calls"] else ""))


def _level_of(r):
    """" (medium)" for a clutter episode's result, "" for another task's."""
    t = str(r.get("task", ""))
    return f" ({t[len('pick_clutter_'):].replace('one_arm_', '')})" if t.startswith("pick_clutter_") else ""


def _episode_line(v, s, r, d, ep="seed"):
    """An episode's result in one line (a whole evaluation set at once)."""
    j = {k: x for k, x in r.items() if k in ("on_stick", "released", "stand_upright", "off_axis_mm", "stick_through_mm",
                                             "held", "height_mm", "held_by", "touches", "fallen")}
    level = _level_of(r)
    if r.get("judged_by") == "world":
        j = r.get("world_check")
    elif set((r.get("scene") or {}).get("without") or ()) >= {"cylinder", "stand"}:
        j = "not judged: no world's success() ran"
    return (f"v{v} {ep} {s}{level}: {'SUCCESS' if r['ok'] else 'FAILED'}{_by_world(r)}, {r['robot_s']} s{_asked(r, True)}; the program "
            f"{'returned' if r['ended'] == 'returned' else 'ended by ' + str(r['ended'])[:160]}" + ("" if r["ok"] else f"; judge: {json.dumps(j)}"))


def _episode_text(v, s, r, d, ep="seed"):
    def pos(t):                                                     # (a world's many bodies: the first of them)
        mine = [n for n in t if n.startswith("w_")]
        more = f"; and {len(mine) - 10} more of the world's bodies (robot.truth() and state.truth() have them all)" if len(mine) > 10 else ""
        return "; ".join(f"{n} {tuple(p) if isinstance(p, list) else p}" for n, p in t.items() if n not in mine[10:]) + more
    bare = set((r.get("scene") or {}).get("without") or ()) >= {"cylinder", "stand"}     # (an empty table: no task of the simulator's)
    shown = sorted(p.name for p in (Path(d) / "shown").glob("*.png")) if (Path(d) / "shown").exists() else []
    n_lines = len((Path(d) / "output.txt").read_text(errors="replace").splitlines())
    level = _level_of(r)
    return (f"v{v} {ep} {s}{level}: {'SUCCESS' if r['ok'] else 'FAILED'}{_by_world(r)}, {r['robot_s']} s of robot time, {r['commands']} robot commands"
            f"{_asked(r)}; the program "
            f"{'returned' if r['ended'] == 'returned' else 'ended by ' + str(r['ended'])}\n"
            + (f"  the world's success(): {json.dumps(r.get('world_check'))}"
               + ("" if bare else f"; the simulator's own task: {'done' if r.get('simulator_ok') else 'not done'}") + "\n"
               if r.get("judged_by") == "world" else "  not judged: no world's success() ran\n" if bare
               else f"  judge: {json.dumps({k: x for k, x in r.items() if k not in _BOOK})}\n") +
            f"  TRUE positions (mm) at the start: {pos(r['true_start_mm'])}\n  TRUE positions at the end: {pos(r['true_end_mm'])}"
            + (f"\n  shown: {', '.join(shown)}" if shown else "") + f"\n  output.txt: {n_lines} lines with the trace" + _printed(d))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", required=True, choices=["sim_bimanual_grippers", "sim_bimanual_dexterous"])
    ap.add_argument("--task", required=True, choices=list(TASKS) + ["pick_clutter", "pick_clutter_one_arm"],
                    help="pick_clutter: one program for several levels of picking from clutter (--levels), from nothing, with "
                         "scenes of the model's own and model calls inside the episodes (robot.ask); pick_clutter_one_arm: the "
                         "same for the one-armed robot (the left arm alone, within the real rig's limits)")
    ap.add_argument("--levels", nargs="+", default=["medium", "hard"], choices=["medium", "hard"], help="pick_clutter: its levels")
    ap.add_argument("--model", required=True, help="a model of configs/models.yaml")
    ap.add_argument("--start", help="the starting program (v0); none: the model starts from nothing")
    ap.add_argument("--eval-set", action="store_true",
                    help="a task with varied scenes (insert_varied): the model also gets a fixed self-evaluation set")
    ap.add_argument("--tables", action="store_true",
                    help="a task with varied scenes: the skill is asked to work on other tables too (height, colour, pattern), "
                         "and the model's scenes can have one")
    ap.add_argument("--budget-usd", type=float, required=True, help="the session's hard cap on model spending")
    ap.add_argument("--sensors", default="d415", choices=["d415", "ideal"])
    ap.add_argument("--segmenter", choices=["sam3"], help="robot.segment() through the SAM3 service")
    ap.add_argument("--effort", default="high", help="reasoning effort")
    ap.add_argument("--max-output-tokens", type=int, default=32000, help="per reply, reasoning included")
    ap.add_argument("--max-hours", type=float, default=8.0)
    ap.add_argument("--eval-episodes", type=int, default=10, help="how many episodes the model is told its result is judged on")
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume", action="store_true", help="go on with the session in --out")
    ap.add_argument("--continue-from", help="a session on varied scenes, or on picking from clutter, that has ended: this one starts with "
                                             "its versions, scenes, results and notes (--eval-set and --tables are taken from it)")
    ap.add_argument("--real-feedback", help="a pack of real-robot episodes (scripts/real_feedback_pack.py) for the model to study")
    ap.add_argument("--ask", help="more that is asked of the skill, added to the task's text (a session on varied scenes)")
    ap.add_argument("--summary", action="store_true", help="the final submit must carry the model's own summary of the session (kept as summary.md)")
    ap.add_argument("--worlds", action="store_true",
                    help="the model writes its own changes to the simulator (worlds: scenes, objects, sensors, the robot's answers)")
    ap.add_argument("--own-task", action="store_true",
                    help="insert_varied: the session is about a NEW task, given in --ask: the model builds it as a world (its "
                         "objects as MJCF, its text goal(), its check success()) and writes the skill for it (implies --worlds "
                         "and --summary; the result is the submitted version with that world)")
    ap.add_argument("--episode-s", type=float, help="a session on varied scenes: the robot time of an episode (default: the task's own); "
                    "the system text states it and every episode of the session has it")
    ap.add_argument("--real-scenes", action="store_true",
                    help="with --real-feedback: scenes can be set up like the real rig (scenes.py REAL: hand-written effects)")
    a = ap.parse_args(argv)

    out = Path(a.out)
    if (out / "state.json").exists() != a.resume:
        ap.error("--out holds a session: --resume goes on with it" if not a.resume else "--resume: no session in --out")
    from ..perception import vlm as V
    tag = f"bimanual_improve_{out.resolve().name}"
    vlm = V.VLM(dict(model=a.model, converse=True, max_calls_per_episode=100000, max_usd_per_episode=a.budget_usd, run_usd=a.budget_usd,
                     max_usd_per_call=a.budget_usd), tag=f"{tag}:{a.env}:{a.task}")
    kw = dict(budget_usd=a.budget_usd, sensors=a.sensors, segmenter=bool(a.segmenter), effort=a.effort, max_output_tokens=a.max_output_tokens,
              n_eval=a.eval_episodes, max_hours=a.max_hours, spent=lambda: V.spent(tag), log=lambda *x: print(*x, flush=True))
    if a.task in ("pick_clutter", "pick_clutter_one_arm"):
        if a.start or a.eval_set or a.tables or a.real_scenes or a.own_task:
            ap.error("--start, --eval-set, --tables, --real-scenes, --own-task: not for pick_clutter")
        if a.resume:                                                # (what the session was started with)
            st0 = json.loads((out / "state.json").read_text())
            a.real_feedback, a.continue_from = st0.get("real"), None
            a.ask, a.summary, a.worlds = st0.get("ask") or None, bool(st0.get("summary")), bool(st0.get("worlds_on"))
        ask = lambda label: V.VLM(dict(model=a.model, max_calls_per_episode=practice.ASK["calls"], max_usd_per_episode=practice.ASK["reserve_usd"],
                                       run_usd=a.budget_usd - ASK_KEEP), tag=f"{tag}:ask:{label}")
        s = ClutterSession(out, a.env, a.levels, vlm, ask=ask, arm=clutter_one_arm.ARM if a.task == "pick_clutter_one_arm" else None,
                           more=a.ask, real=a.real_feedback, continue_from=a.continue_from, summary=a.summary, worlds=a.worlds, **kw)
    elif TASKS[a.task].scene:
        if a.start:
            ap.error("--start: a session on a task with varied scenes starts from nothing (--continue-from: from an ended session)")
        if a.real_scenes and not a.real_feedback:
            ap.error("--real-scenes: only with --real-feedback")
        if a.resume:                                                # (what the session was started with)
            st0 = json.loads((out / "state.json").read_text())
            a.eval_set, a.tables = st0.get("eval_set", a.eval_set), st0.get("tables", a.tables)
            a.real_feedback, a.real_scenes = st0.get("real"), st0.get("real_scenes", False)
            a.ask, a.summary, a.worlds = st0.get("ask") or None, bool(st0.get("summary")), bool(st0.get("worlds_on"))
            a.own_task = bool(st0.get("own_task"))
            a.episode_s = st0.get("episode_s")
            a.continue_from = None                                  # (its versions are here already)
        elif a.continue_from:
            src = json.loads((Path(a.continue_from) / "state.json").read_text())
            a.eval_set, a.tables = bool(src.get("eval_set")), bool(src.get("tables"))
        if a.own_task:
            if a.task != "insert_varied" or not a.ask or a.eval_set or a.tables:
                ap.error("--own-task: with --task insert_varied (whose scenes the world rebuilds) and --ask (the new task), "
                         "without --eval-set and --tables")
            a.worlds = a.summary = True
        s = SceneSession(out, a.env, a.task, vlm, eval_set=a.eval_set, tables=a.tables, real=a.real_feedback, real_scenes=a.real_scenes,
                         continue_from=a.continue_from, ask=a.ask, summary=a.summary, worlds=a.worlds, own_task=a.own_task,
                         episode_s=a.episode_s, **kw)
    elif a.eval_set or a.tables or a.continue_from or a.real_feedback or a.real_scenes or a.worlds or a.own_task:
        ap.error("--eval-set, --tables, --continue-from, --real-feedback, --real-scenes: only for a task with varied scenes")
    else:
        s = Session(out, a.env, a.task, vlm, start=a.start, **kw)
    how = s.run()
    print(json.dumps(dict(ended=s.state["ended"], stopped=None if s.state["ended"] else how, submitted=s.state["submitted"],
                          turns=s.state["turn"], spent_usd=round(s.spent(), 4))))
    return 0 if s.state["ended"] else 1


if __name__ == "__main__":
    sys.exit(main())
