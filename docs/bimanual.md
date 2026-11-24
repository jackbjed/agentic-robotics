# The bimanual environments

Two simulated environments for testing agents on two-armed work, with parallel grippers or with dexterous hands:

| environment | arms | tools |
|---|---|---|
| `sim_bimanual_grippers` | the rig's two Pandas | the rig's parallel grippers (fin-ray fingers, 80 mm) |
| `sim_bimanual_dexterous` | the rig's two Pandas | Sharpa Wave hands: a right hand on the right arm, a left hand on the left (22 joints each) |

They are for two kinds of test, on the same tasks and with the same judge:

1. **direct control**: a model (e.g. Astra) or a coding agent (e.g. Fable in Claude Code) looks, moves the arms and the
   fingers one tool call at a time, and is judged on the final scene;
2. **writing code**: an agent writes a program `run(robot, task)` that does the task through the robot API.

Code: `src/agentic_robotics/bimanual/`. Tests: `tests/test_bimanual.py`. They are plain MuJoCo simulations, separate
from the single-arm platform (`sim/`, `bin/ar`): no splat rendering, no world server (see "Not there yet").

## The scene

Both environments are the twin's own scene (`vendor/panda_twin/model/twin_6522_wedge.xml`, read only), rebuilt by
`scripts/build_bimanual.py` into `bimanual/models/sim_bimanual_{grippers,dexterous}.xml`:

- **Frame:** the rig's. z up, the table top at z = 0, its centre at the origin (1.22 x 1.22 m). The robot stands at
  the table's −x edge and faces +x; +y is its left.
- **The robot:** the rig's two Panda arms on its frame, at their calibrated bases: the right one (`R_`) at
  (−799, −391, −66) mm, the left one (`L_`) at (−788, 41, −46) mm. The single-arm twin parks the right arm; here both
  are driven. Start pose: each arm's flange 0.31 m in front of its base, the tool pointing straight down, open.
- **The scene camera:** the rig's D415 (`d415`) at its calibrated pose, (864, −8, 572) mm, across the table from the
  robot: the pose the platform's default camera config uses.
- **The wrist cameras:** a D415 on each wrist (`R_wrist`, `L_wrist`). Grippers: at the calibrated mount of the rig's
  left hand (the right hand's mount is assumed to be the same). Dexterous: 75 mm behind the back of the hand, looking
  along the fingers, turned 10° towards the palm (as in `panda_twin_dex`).
- **Objects:** the twin's scanned objects (boxes, tape rolls, glue, screwdriver, cable, pad, block; the ruler is
  fixed to the table) and a bottle with a screw cap (0.65 kg, 30 cm tall; a hinge and a slide tied by the thread's
  pitch, with friction; after one full turn the cap comes free), and a cylinder with its stick: a wooden cylinder
  (40 mm across, 40 mm tall, 0.02 kg) with a 20 mm hole along its axis, and a stand (a free board, 120 x 120 x 22 mm,
  with one vertical stick 18 mm across, its flat top 100 mm above the board). The hole is 24 flat faces, 10 mm from the
  axis: 1 mm of play all round. An episode's layout names the objects on the table; the others are parked out of sight.
- **Reach:** with the tool pointing down, the right arm covers about x −600..−50, y −700..+300 mm and the left arm
  y −200..+400 mm and beyond; both reach the strip y −200..+250, x −450..−50. The far half of the table (x > 0) is out
  of reach.

All cameras give 640x480 colour and depth. Colour is MuJoCo's own rendering. With `sensors="d415"` (the default) depth
goes through the model fitted to the real cameras (`d415_model.D415Real`: noise, scale, holes, flying pixels, the left
band, nothing nearer than 0.275 m), and each camera's true pose differs from the one reported by a per-episode
calibration error (5 mm and 1° for the scene camera, 3 mm and 1° for the wrist cameras): a point on the table located
from the scene camera can be 20 mm off. `sensors="ideal"` gives exact depth and poses.

The arms are stiff joint position servos (the twin's gains), with the steady-state error taken out after each move;
this is not the single-arm platform's compliant controller. Straight-line moves of the tool point, rigid turns about
an axis, and a collision-checked joint-space path for free moves with a big change of orientation. A move that would
make the two arms collide is refused. A guarded move stops at 4 N of new force; any move stops at 40 N. Time step
2 ms (grippers, about 30x real time) and 1 ms (dexterous, about 8x).

Rebuild the models (only after changing the builder; the Sharpa hands need the asset groups `sharpa_right` and
`sharpa_left` of `assets/manifest.yaml`, root `sharpa` in `configs/local.yaml`):

```bash
.venv/bin/python -I scripts/link_assets.py --only sharpa_right sharpa_left
.venv/bin/python -I scripts/build_bimanual.py
```

## Tasks

`bimanual/tasks.py`. The same tasks run in both environments. The seed sets the layout (and the sensor
errors). The judge reads the simulation's true state after the scene has rested for 1 s.

| task | arms | layout | success | limits |
|---|---|---|---|---|
| `open_bottle` | 2 | the bottle in front of the robot (x −330..−210, y −240..−120 mm; seed 0: the reference spot) | the cap is off the bottle (held or on the table) and the bottle stands upright on the table | 600 s, 80 calls |
| `move_across` | 2 | a roll of white tape on the right, where only the right arm reaches | the roll rests on the table beyond y = 400 mm, where only the left arm reaches, and the robot does not touch it | 400 s, 60 calls |
| `lift` | 1 | the roll in front of the robot, where both arms reach | the roll is held at least 50 mm above the table, touching nothing but the robot | 240 s, 40 calls |
| `insert_cylinder` | 1 | the stand on the right (x −300..−200, y −200..−100 mm, turned up to 45°), the cylinder standing on the left (x −360..−240, y 40..140 mm), both where both arms reach; seed 0: the reference spots | the cylinder is on the stick (its middle below the stick's top, within 4 mm of the stick's axis), the robot does not touch it, and the stand is upright on the table | 600 s, 80 calls |
| `insert_varied` | 1 | `scenes.py`: another cylinder and stand in every scene (sizes, colours, densities, places, the stand turned up to 45°) and up to 3 other objects; seeds from 5000 on: beyond those limits (the evaluation's out-of-distribution scenes); from 8000 on: the scene of seed - 3000 on another table (height, colour, pattern) | as `insert_cylinder` (the hole 2 mm wider than the stick) | 600 s, 80 calls |
| `pick_clutter_medium`, `_hard`, `_extra_hard` | 1–2 | `clutter.py`: a heap of 5–11 objects (boxes, cylinders, balls; seeds from 5000 on: other shapes, colours, sizes, counts, places); the task text names the target by colour and shape; medium: it touches others and can be seen; hard: it lies under others; extra hard: the heap in an open bin | the target is held with its centre at least 150 mm above the table, touching nothing but the robot; nothing fell off the table | 600 s, 80 calls (medium); 900 s, 120 calls |
| `biolab_open` | 2 | `biolab.py`, the bio-lab bench: two tube racks side by side, a 50 mL centrifuge tube with a screw cap in a hole of one of them, a pipette standing in another hole, a cup of water, sometimes vials or a block; another bench for every seed (rack sizes, heights, colours, places; seeds from 5000 on: beyond the training limits) | the tube stands in a hole of the other rack with its cap off (held by the robot or on the table), the robot does not touch the tube, the cup stands, nothing fell off the table | 600 s, 120 calls |
| `biolab_drops` | 2 | the same benches | the tube in the other rack, exactly three drops of the cup's water in it (put in with the pipette), the tube closed again, the robot touching nothing of it, the cup standing, nothing fallen off the table | 1200 s, 200 calls |

**The bio-lab bench** (2026-11-23). `biolab_open` is the short task (the first stages of `biolab_drops`: carry, hold,
unscrew); the pipette and the cup are on the bench and not used, so that a seed gives the same bench in both tasks.
A rack weighs 0.4–0.7 kg and is not fixed, a hole is 2 mm wider than the tube (training benches), and the cap is free
after three quarters of a turn against the thread's friction: one arm has to hold the tube while the other turns
the cap, and on a high rack or in a middle hole there is little room for the two hands. The reference solutions
(`biolab_ref.py`, true poses, grippers; `scripted.SOLUTIONS`) on seeds 2000–2049 and 5000–5049:

| task | ID | OOD | robot time (median, longest) |
|---|---|---|---|
| `biolab_open` | 49/50 | 50/50 | 99 s, 156 s |
| `biolab_drops` | 46/50 | 47/50 | 276 s, 389 s |

At reset, whatever does not stand at its height after the scene has come to rest is set back
(`biolab.Mech.settled`): while a rack settles on the table, the guard against false contacts (`core._false_contacts`)
can switch all the contacts of the rack's boxes off for some steps, and the pipette standing on its walls fell through
(3 of the 100 benches). The guard switches off whole geoms, not the false contact alone: a weakness to keep in mind
for other scenes with flat boxes on the table.

The roll is a ring 50 mm across and 27 mm high (0.06 kg). `insert_cylinder` is the precision task: the cylinder goes
on only when its axis is within about 1 mm of the stick's (the stick's top is flat, its edge square: nothing guides the
cylinder in), while a point located from the cameras can be 20 mm off. The task's text gives the sizes and the 1 mm,
not the positions. Limits are robot time and, for direct control, tool calls;
the time limit is checked before each action.

```python
from agentic_robotics.bimanual.tasks import Episode
ep = Episode("sim_bimanual_dexterous", "open_bottle", seed=3)   # sensors="d415" | "ideal", record=True for a video
ep.text        # the task as the controller gets it
ep.robot       # the robot API (below): all a controller gets
ep.finish()    # the verdict: dict(ok, ..., robot_s)
```

## The robot API (for programs)

`bimanual/api.py`, class `Robot`: millimetres and degrees, table frame. No true object state passes through it.

| call | |
|---|---|
| `look(cameras=None)` | `{camera: Frame}` for `"scene"`, `"right_wrist"`, `"left_wrist"`. A `Frame` has `rgb`, `depth` (mm, nan = no depth), `fx fy cx cy`, `eye`, `R`, and `locate(u, v)`, `points()`, `project(xyz)` |
| `state(arm)` | grippers: `tcp_mm`, `approach_dir`, `closing_dir`, `opening_mm`, `commanded_mm`, `force_n`. Dexterous: `palm_mm`, `fingers_dir`, `palm_dir`, `thumb_side`, `fingertips_mm`, `joints_deg`, `targets_deg`, `fingertip_force_n`, `force_n` |
| `move(arm, xyz, dir_a, dir_b, stop_on_contact=False)` | the tool point (the gripper's TCP / the palm's centre) along a straight line; the orientation by two directions: grippers `approach_dir`, `closing_dir`; dexterous `fingers_dir`, `palm_dir` |
| `turn(arm, centre, axis, angle_deg)` | the whole gripper / hand rigidly about a line |
| `home(arm)` | back to the start pose by the joints |
| `set_gripper(arm, width_mm)` | grippers: the opening, 0..80 mm; returns the opening measured afterwards |
| `set_joints(arm, joints, duration_s, stop_force_n)` | dexterous: finger joint targets in degrees |
| `fingertips(arm, targets, duration_s, stop_force_n)` | dexterous: fingertips to points (inverse kinematics of each finger) |
| `wait(seconds)`, `time_s()` | robot time |
| `segment(frame, phrase)`, `segment_points(frame, pixels, labels)` | SAM3, only in an episode run with `--segmenter sam3` (`bin/ar services start`) |

Motion calls return `dict(done, moved, why, curved)`: `moved` is False when the command was refused (out of reach,
the arms would collide, a wrist limit); `why` is `"contact"` when a `stop_on_contact` move stopped at a touch. When
the robot time is spent, the next call raises `TimeUp`.

A program is a file that defines `run(robot, task)`; `examples/bimanual_lift.py` is one (the roll from the scene
camera's depth, the table by touch):

```bash
python -m agentic_robotics.bimanual.run --env sim_bimanual_grippers --task lift --seed 0 \
       --program examples/bimanual_lift.py --video --out runs/bimanual/lift_example
```

## The tools (for direct control)

`bimanual/tools.py`, class `Toolbox`: the same calls as text and images, with instructions that describe the robot,
its geometry, the cameras and the limits (`Toolbox.instructions`).

| grippers | dexterous | |
|---|---|---|
| `look` | `look` | the three camera images and both arms' state |
| `locate` | `locate` | pixels of the latest look to 3D points, from that camera's depth |
| `move_gripper` | `move_hand` | |
| `turn_gripper` | `turn_hand` | |
| `set_gripper` | `set_joints`, `fingertips` | |
| `home` | `home` | |
| `done` | `done` | ends the episode |

Every result gives both arms' state as text. `--hints` (dexterous) adds three known grasps to the instructions: the
side wrap that holds the bottle, the three-finger pinch that turns the cap, and the tilted pinch that picks a low
object off the table. Without them, in the 11-17 study Astra did not find the side wrap. The default is no hints.
`openai_tools` / `openai_items` and `anthropic_tools` / `anthropic_content` convert the specs and results for the two
APIs.

## Running an episode

`python -m agentic_robotics.bimanual.run --env ENV --task TASK [--seed N] [--sensors d415|ideal] [--video] [--out DIR]`
with one of:

| | who controls the robot |
|---|---|
| `--scripted` | the reference solution (`scripted.py`). It reads the simulation's true state: it shows that the task can be done, it is not a baseline |
| `--program FILE` | a program, in this process |
| `--calls FILE` | tool calls replayed from a jsonl file (e.g. an earlier episode's `calls.jsonl`, to make its video) |
| `--model KEY` | a model of `configs/models.yaml` through the tools, on the platform's model client (`perception/vlm.py`: key file, prices, the budget ledger; tag `bimanual_TASK:ENV:SEED`). **This costs money**; `--max-usd` caps the episode (default 3); `--effort`, `--max-output-tokens` (per reply, reasoning included) |

`DIR` gets `result.json` (the verdict, how the episode ended, tool calls, model usage), `calls.jsonl`,
`messages.jsonl`, `final.jpg` and `video.mp4`.

**A coding agent as the controller** (`bimanual/drive.py`): one tool call per shell command, the camera images as PNG
files. The episode lives in a checkpoint between commands, so it is the episode one process would have run.

```bash
python -m agentic_robotics.bimanual.drive DIR start --env sim_bimanual_dexterous --task lift --seed 0   # the task, the first look
python -m agentic_robotics.bimanual.drive DIR call move_hand '{"hand": "left", "x": -350, "y": 70, "z": 230, "fingers_dir": [0, 0, -1], "palm_dir": [1, 0, 0], "stop_on_contact": false}'
python -m agentic_robotics.bimanual.drive DIR judge                                                     # the verdict
```

`DIR/instructions.md` holds the instructions, the tools and the task. `DIR/checkpoint.pkl` holds the true state: an
agent under test should be told to use the three commands only, and its session should be checked for that.

**A model writes the program** (`bimanual/write.py`, code as policy): one model call, no revision. The model gets the
robot's description (the text of direct control), the robot API, the task, how much the layout changes between
episodes, and the first look of one example episode (three colour images and the arms' states; pick a seed the
program will not be run on). It cannot run the program or see its output. **This costs money** (`--max-usd`, default 3).

```bash
python -m agentic_robotics.bimanual.write --env sim_bimanual_grippers --task insert_cylinder --model astra --effort high \
       --example-seed 3 --segmenter sam3 --out runs/bimanual/x            # program.py, prompt.md, reply.md, write.json
python -m agentic_robotics.bimanual.run --env sim_bimanual_grippers --task insert_cylinder --seed 0 \
       --program runs/bimanual/x/program.py --segmenter sam3 --video --out runs/bimanual/x/s0
```

**A model develops the program in the simulator** (`bimanual/improve.py`, on `bimanual/practice.py`): a session of
tool calls in which the model writes versions of the program, runs them on practice episodes, reads what happened,
and submits the version that is its result. **This costs money**: `--budget-usd` caps the session (the cost of each
turn is reserved at its worst case before the call; the session ends when a useful reply no longer fits).

```bash
python -m agentic_robotics.bimanual.improve --env sim_bimanual_grippers --task insert_cylinder --model astra --effort high \
       --start runs/bimanual/x/program.py --segmenter sam3 --budget-usd 20 --out runs/bimanual/y/session   # [--resume]
python -m agentic_robotics.bimanual.practice suite --env sim_bimanual_grippers --task insert_cylinder \
       --program runs/bimanual/y/session/program.py --seeds 0-2,4-10 --segmenter sam3 --video --out runs/bimanual/y/eval
# varied scenes: from nothing, the model sets up its practice scenes; --eval-set adds a fixed self-evaluation set;
# --tables asks for a skill that also works on other tables, and the model's scenes can have one (height within
# +-25 mm, 6 of the 10 colours, plain / checker / stripes / speckle: less than the evaluation's tables, seeds 8000+)
python -m agentic_robotics.bimanual.improve --env sim_bimanual_grippers --task insert_varied --model astra --effort high \
       --segmenter sam3 --budget-usd 15 --eval-episodes 30 [--eval-set] [--tables] --out runs/bimanual/z
python -m agentic_robotics.bimanual.practice suite --env sim_bimanual_grippers --task insert_varied \
       --program runs/bimanual/z/program.py --seeds 2000-2014,5000-5014 --jobs 7 --segmenter sam3 --video --out runs/bimanual/z_eval
```

- The model's tools: `write_program`, `edit_program` (exact replacements in a version), `run_episodes` (up to 6
  practice seeds at once, seeds 100-999; results are kept per version and seed), `read_output`, `view_images`,
  `read_program`, `submit`. `--start` gives it a starting program as v0 (here the one-call program); without it the
  model starts from nothing.
- What practice tells the model that the program never gets: the judge's verdict and its measurements, the objects'
  TRUE positions at the start and the end, and a trace of every robot command with the arm's true tool position and
  the objects that moved. A program can also hand images to the model with `robot.show(label, image)`. So practice is
  a simulator with full instrumentation, which a real robot would not give.
- Each episode runs in the platform's program sandbox (`execution/sandbox.py`: no network, no files but its own
  output directory; SAM3 is reached over a Unix socket), after a static check of the program (allowed imports, no
  file or introspection functions). The evaluation runs the submitted program the same way, without the model, on
  seeds the session never used.
- `DIR` gets `program.py` (the result), `versions/vNN.py`, `runs/vNN/sSEED/`, `transcript.md` (everything the model
  got and did, with each turn's cost), `result.json` (versions, their practice results, cost). A session that stops
  on an error continues with `--resume`.

## What is verified (2026-11-21)

Reference solutions, `sensors="ideal"`:

| | `lift` | `move_across` | `open_bottle` |
|---|---|---|---|
| grippers | 6/6 seeds, 15-18 s | 6/6, about 80 s | 16/16, about 72 s |
| dexterous | 6/6, 17-19 s | 6/6, about 85 s | 11/11, about 71 s |

`insert_cylinder` (grippers only: no reference solution with the Sharpa hands yet): 10/10 seeds, about 30 s. With
the cylinder's axis put a given distance off the stick's: on at up to 1.3 mm, stopped on the stick's top at 1.5 mm and
beyond (the contacts are soft and the free stand gives about 0.3 mm).

The dexterous solutions use the three grasps of `--hints`. The roll lift also passes when given as tool calls
(`sensors="d415"`, both environments), replayed, and driven from the shell, with the same result each way. The
example program lifts the roll with realistic sensors in 5 of 6 seeds. The model loop is tested with a stub model.

## First model results (2026-11-21)

Direct control (`--model`), `sim_bimanual_grippers`, `sensors="d415"`, effort high, no hints, seeds 0-2. Three
episodes per cell: a first look, not a benchmark.

| task | model | success | tool calls | robot time | cost |
|---|---|---|---|---|---|
| `open_bottle` | gpt-6-astra | 3/3 | 28-31 | 59-67 s | $1.67 |
| `open_bottle` | gpt-6.2-astra | 3/3 | 34-40 | 69-71 s | $2.53 |
| `insert_cylinder` | gpt-6-astra | 3/3 | 22-30 | 49-85 s | $1.83 |

Runs: `runs/bimanual/open_bottle_grippers_1121/`, `runs/bimanual/insert_cylinder_grippers_1121/` (not in git).

How gpt-6-astra got the cylinder within a millimetre of the stick, when one located point can be several millimetres
off (true offsets read by replaying each episode's calls):

- seed 1: it grasped the cylinder with the left arm and located the stick's top with the *same* arm's wrist camera,
  from above, while holding the cylinder: 1.4 mm off, on at the first try;
- seed 0: it used the other arm's wrist camera for the stick: 2.3 mm off, stopped on the stick's top (guarded move);
  two blind corrections missed; then it looked with the holding arm's camera: 0.7 mm off, on;
- seed 2: no camera in the last step. It closed the right gripper on the stick twice, a quarter turn apart (which
  slid the stand until the stick was centred on that tool point), put the cylinder down and grasped it again a
  quarter turn round (centred both ways), then moved the left tool point to the right one's coordinates: 0.5 mm off.

The third way leans on the simulated arms being exact relative to each other; on the rig the two bases' calibration
would add to it.

**A program instead of direct control** (`write.py`, same model, effort high, `sensors="d415"`, example seed 3; one
call: 17k output tokens, 6.8 min, $0.91; a 567-line program, run as written on seeds 0 and 1):

| task | model | success | robot time |
|---|---|---|---|
| `insert_cylinder` | gpt-6-astra, one program | 1/2 | 298 s (success), 532 s (failure) |

Run: `runs/bimanual/insert_cylinder_program_1121/astra/` (`program.py`, `prompt.md`, `s0/`, `s1/`; not in git).
What the program does: finds the cylinder by colour and the stick by colour and height in the scene camera's depth;
raises the free arm so its wrist camera looks at the stick from above and fits the stick's top (a silhouette fit);
grasps the cylinder after a silhouette fit from the holding arm's wrist camera; plans to align the held cylinder
over the stick in the free arm's camera; then probes downwards on a square spiral (0.85 mm steps), lifting before
each sideways step, until the tool point goes 9 mm below where the cylinder first touches the stick's top. It never
calls `robot.segment`.

What happened (true offsets from an instrumented re-run): in both episodes the first move to the stick was refused
(the raised free arm was in the way); the program's recovery is to send that arm home, which also drops its visual
alignment step. That left the first estimate of the stick's position, off by 5.0 mm (seed 0) and 10.7 mm (seed 1),
and the spiral: 104 probes found the hole in seed 0; in seed 1 the robot time ran out before the spiral got that far.
The grasps left the cylinder's axis 1.5 and 0.4 mm from the tool point. So the direct-control episodes (3/3, 49-85 s) corrected with a
new look where the unrevised program could only search by touch.

**The three ways compared** (`insert_cylinder`, `sim_bimanual_grippers`, gpt-6-astra, effort high, `sensors="d415"`;
2026-11-22). The program of the third row is the second row's after one development session (`improve.py`: 41 turns,
15 minutes, **$3.89** of the $20 allowed; 8 versions, 96 practice episodes on seeds 100-999; the model ended the
session itself). Both programs were then run, without the model, on the same 10 seeds (0-2, 4-10; seed 3 was the
one-call program's example):

| | success | robot time | model cost |
|---|---|---|---|
| direct control | 3/3 (seeds 0-2 only) | 49-85 s | $0.61 an episode, every episode |
| program, one call | 4/10 | 222-469 s (successes); 532 s (failures) | $0.91 once |
| program after the session | **10/10** | **37-44 s (mean 40.4)** | $0.91 + $3.89 once |

Runs: `runs/bimanual/insert_cylinder_program_1121/astra/eval10/`,
`runs/bimanual/insert_cylinder_improve_1121/{session,eval}/` (not in git). In the 10 evaluation episodes the final
program left the cylinder 0.1-0.9 mm off the stick's axis before the drop, and got on at the first downward probe in
7, the second in 2, the third in 1.

What the model changed, in its order (practice: successes, mean robot time):

| version | change | practice |
|---|---|---|
| v0 | the one-call program | 4/6, 184 s |
| v1 | the free arm watches from the side, wrist tilted, instead of from above the stick: the holding arm's move to the stick is no longer refused, so the visual alignment of the held cylinder over the stick (both seen in ONE camera, which cancels the calibration error) happens at all | 6/6, 181 s |
| v2, v3 | the stick's top found by colour (its bright end face) instead of the depth image, the held cylinder by its whole blue silhouette | v3: 24/24, 76 s |
| v4 | the watching pose aimed so the stick's top stands clear of the board in the image (three slow cases gone) | v4: 18/18, 58 s |
| v5-v7 | shorter descent and no retreat at the end; when the first probe lands on the stick's top, that contact gives the cylinder's true height in the hand and the alignment is refitted with it; alignment at one height | v7: 18/18, 40 s |
| v8 | dead code removed | 6/6, 40 s |

Of the 41 tool calls, 16 ran episodes, 8 edited the program (never a whole rewrite), 4 read a trace (the first two
found that the alignment step never ran) and 4 looked at `robot.show` images. It used little of the budget: most turns were a
tool call with almost no reasoning (replies of 30-1000 tokens), and the conversation stayed cached ($1 per Mtok).
The result is specific to these objects (colour thresholds, the stick's height from the task text): other shapes or
distractors are not tested.

**From nothing, on varied scenes** (`insert_varied`, 2026-11-22). The task text says only what to do; nothing about
the objects, and no starting program. Every scene (`scenes.py`) has another cylinder and stand (sizes, colours,
densities, places, the stand turned) and up to 3 other objects. Two sessions of gpt-6-astra (`improve.py`:
`SceneSession`, effort high, SAM3, `sensors="d415"`, $15 allowed each) ran at the same time. In both the model sets
up its own practice scenes within the training limits (`define_scenes`), and practice shows the simulator's truth
(the judge's verdict, true positions, the trace; `robot.truth()` and `robot.judge()` inside a practising program).
One session (`--eval-set`) also had a fixed self-evaluation set of 30 scenes (`run_eval_set`). The results were then
run, without the model and with the cameras only, on 30 scenes neither session saw: 15 drawn like the training scenes
(seeds 2000-2014) and 15 beyond the training limits (seeds 5000-5014: sizes outside the ranges, new colours, new
kinds of distractors (balls, pills, L and step shapes) and up to 6 of them, lighter and heavier objects, other places).

| | in distribution | out of distribution | robot time (successes) | session |
|---|---|---|---|---|
| own scenes only (v19, 321 lines) | 15/15 | 14/15 | 65.5 s | $12.11, 79 turns, 34 min, 46 own scenes, 213 practice episodes |
| with the self-evaluation set (v25, 402 lines) | 14/15 | 14/15 | 79.2 s | $12.32, 102 turns, 49 min, 35 own scenes, 560 practice episodes (448 on the set; 30/30 from v20 on) |
| the program of the fixed-object session above (v8) | 1/15 | 0/15 | - | |
| reference (reads the true state) | 15/15 | 15/15 | 32.3 s | |

Runs: `runs/bimanual/varied_{own,evalset}_1122/` (sessions), `runs/bimanual/varied_final_1122/` (evaluation, with
videos). The three failures are all perception, before any motion that matters: no cylinder or stick found in the
first overview picture (own, 5005: objects close to the robots' bases, the stick's top 183 mm above the table), no hole found in the close-up
(set, 2000: a grey cylinder, in distribution; 5014: a 62 mm wide cylinder with an 8 mm hole). No difference between
the two sessions shows at this size (29 against 28 of 30). Both ended by themselves, and neither program version ever
called `robot.truth()` or `robot.judge()`: the episodes' results and traces were enough. Both arrived at the same
plan: find the two objects in the overview (SAM3 prompts as proposals, checked by geometry: a ring with a hole, the
highest thin top), look down with the wrist camera at each, grasp, set the cylinder down and look again to calibrate
the camera against the grasp, carry, lower with contact and search by touch; a table hand-off when the two objects
need different arms. Assumed in all scenes: the cylinder stands hole-up, and the hole is 2 mm wider than the stick.

**Another table** (2026-11-22; no model call). The two programs above, as submitted, on 15 more scenes in which the
table is also new: seeds 8100-8114 are the out-of-distribution scenes 5100-5114 on another table (`scenes.py`: from
seed 8000 on, the scene of seed - 3000 with a `table`). The table top is 40 mm below to 40 mm above its usual height
in the robot's frame (the robots, their cameras and the frame stay; the table, the ruler and the objects move), in one
of 10 colours, plain or with a pattern (checker, stripes, speckle, wood, blotches). Both sessions were told that the
table top is z = 0 and never saw another table. Control: seeds 5100-5114, the same objects on the usual table.

| | usual table (control) | another table | height only | look only |
|---|---|---|---|---|
| own scenes only (v19) | 15/15 | 11/15 | 11/15 | 15/15 |
| with the self-evaluation set (v25) | 15/15 | 5/15 | 9/15 | 8/15 |
| reference (reads the true state) | 15/15 | 15/15 | | |

"Height only" is the usual dark table at the scene's height, "look only" the scene's colour and pattern at the usual
height (the same 15 scenes; `practice.run_sandboxed(..., scene=...)`). Runs: `runs/bimanual/varied_table_1122/`.
- Height. Both programs measure the cylinder's height above the table around it, then go to that height as an
  absolute z (`gz = max(18, h - 12)`): on a table 28 mm or more below, the gripper closes above the cylinder (3 scenes,
  both programs). At -12 mm both grasp the 54 mm cylinder and miss the stick. Tables above cost v19 nothing (8/8),
  and v25 one scene (+37.5 mm); v25 also fails at -24 mm.
- Look. v19 (SAM3 masks, checked by geometry) is not affected by any colour or pattern. v25 finds the hole in the close-up from edges (ellipses fitted
  to contours), and finds none ("no hole") on 7 of the 12 patterned tables: both checkers, both stripes, 2 of 3 wood
  grains, 1 of 3 blotches; plain and speckled tables are fine.

**A session asked for other tables** (2026-11-22; `improve --tables`, $13.35 of a $15 budget, 81 turns, 39 min, 23
versions, v21 submitted). The session of "own scenes only" again (from nothing, no self-evaluation set, the same
prompt), with one difference: the request also says "It should also be robust to variations in the height of the
table with respect to the robot, and to changes in the table's appearance (i.e., its color and pattern)", and the
model's scenes can have a table, within less than the evaluation's range: height within 25 mm of the usual one (the
evaluation: 40 mm), 6 of the 10 colours, plain / checker / stripes / speckle (not wood, not blotches). The prompt says
that 15 more evaluation episodes have another table, also beyond what can be set up. The model practised on 52 scenes
of its own: 37 on another table (32 at another height, to +-25 mm; 14 with a pattern), 15 on the usual one.

| cameras only, no model call | in distribution (2000-2014) | out of distribution (5000-5014) | another table (8100-8114) | usual table (5100-5114) | height only | look only |
|---|---|---|---|---|---|---|
| own scenes only (v19) | 15/15, 65 s | 14/15, 66 s | 11/15, 64 s | 15/15, 65 s | 11/15 | 15/15 |
| with the self-evaluation set (v25) | 14/15, 68 s | 14/15, 90 s | 5/15, 62 s | 15/15, 60 s | 9/15 | 8/15 |
| asked for other tables (v21) | 15/15, 92 s | 14/15, 105 s | 14/15, 98 s | 13/15, 76 s | 14/15 | 14/15 |

(Successes, and the mean robot time of the successes.) Runs: `runs/bimanual/varied_tables_1122/` (the session),
`runs/bimanual/varied_tables_eval_1122/` (the evaluation).
- The table. The program measures the table's height in each close-up (a plane fitted to the table's depth points, taken at the object)
  and works relative to it; the 6 tables beyond +-25 mm and the wood and blotch patterns it never saw cost it one
  scene, 8100 (-31 mm, cylinder 18 mm high). There one absolute height is left: the grasp height is
  `max(10.5, table + max(22, ...))`, a floor of 10.5 mm above the usual table, so that on this table the gripper
  closes 17 mm too high, above the cylinder (the taller cylinders at -35.5 and -28.5 mm are grasped). The same scene
  at the usual height on its wood-grain table fails another way (no circle found in the close-ups, the search over
  the stick finds nothing).
- The price. The program is slower (median 72-83 s against 63-66 s) and less reliable where the earlier ones were:
  2 of the 15 control scenes fail (5103, 5111: grasped, then 3 rounds of a 64-point search over the stick's top that
  all end in contact, 450-480 s), as does 5010; 4 successes take 270-290 s for the same reason (the search fails, the
  program puts the cylinder down, looks again and grasps again). 5103 succeeds on its other table (8103, 287 s) and
  5111 on its (8111, 64 s): what decides is the insertion, a few millimetres of the estimate of where the held
  cylinder's hole is, not the table.

## Picking from clutter: a session whose program asks the model (2026-11-23)

`improve --task pick_clutter --levels medium hard` (`ClutterSession`): one program for both levels, from nothing. The
model was given the environments and their randomization ranges, not the evaluation seeds; it sets up its own
heaps (`sample_scenes`, `define_scenes`, with shapes of its own), its practice shows the simulation's truth, and its
program may call the model inside an episode: `robot.ask(question, images, effort)` (`practice.AskBridge`; at most 4
calls an episode, their latency is robot time, their cost comes out of the session's budget).

```bash
python -m agentic_robotics.bimanual.improve --env sim_bimanual_grippers --task pick_clutter --levels medium hard \
    --model astra --segmenter sam3 --budget-usd 40 --eval-episodes 100 --max-hours 14 --out runs/bimanual/clutter_astra_1123
python scripts/eval_bimanual_ask.py --program runs/bimanual/clutter_astra_1123/program.py --model astra \
    --tasks pick_clutter_medium pick_clutter_hard --seeds 2000-2049,5000-5049 --run-usd 12 --out runs/bimanual/clutter_astra_1123_eval
```

The session (gpt-6-astra, effort high): 121 turns, 86 min, $35.71 of $40 ($21.11 of it the model calls of its 935
practice episodes), 20 versions; the submitted program (401 lines) asks the model which object is the target and
what to clear first (overhead and side views) and checks what it holds (three images), and solved 136 of 136 of
the session's own practice heaps (68 medium, 68 hard).

The evaluation (`scripts/eval_bimanual_ask.py`: cameras with realistic depth, SAM3 and `robot.ask`, no truth; 50
episodes per cell, $4.90 of model calls, no call refused). Reference: `scripted.pick_clutter` with the true poses.

| | medium ID | medium OOD | hard ID | hard OOD |
|---|---|---|---|---|
| reference (true poses) | 49/50 | 47/50 | 48/50 | 46/50 |
| the session's program | **48/50** | **47/50** | **47/50** | **38/50** |
| robot time of its successes: mean (median) | 102 s (45 s) | 93 s (36 s) | 240 s (193 s) | 257 s (208 s) |
| model calls an episode | 0.86 | 1.16 | 2.18 | 2.48 |
| model cost an episode | $0.012 | $0.017 | $0.031 | $0.037 |

180 of 200 (ID 95/100, OOD 85/100). The 20 failures:
- 7, all OOD: it lifted a look-alike (the orange cross for the orange steps, the red T for the red cross, the cyan
  box for the teal one, the maroon box for the pink one, ...) and its check of the held object accepted it: SAM3
  found the target's phrase on the lifted object (3), the model said yes (2), no model call was left (2);
- 7: the grasps closed on nothing or lost the target until the attempts or the time ran out;
- 4, all ID: the target was not found after the four model calls;
- 2: the target lifted with another object resting on it; an episode ended with nothing lifted.

The practice score (136/136) overstated the result on new heaps, most of all on out-of-distribution objects, where
colours and shapes close to the target's are the main cause.

## Picking from clutter with one arm (2026-11-23)

The real rig has one arm (the left Panda, the one with the green gripper), so the clutter tasks exist for that arm
alone: `pick_clutter_one_arm_medium` and `pick_clutter_one_arm_hard` (`bimanual/clutter_one_arm.py`; the levels, time
limits, model calls and judge of the two-armed tasks).

- The robot: the right arm stays parked at its start pose and is not in the interface (`arm` is always `"left"`, the
  cameras are `scene` and `left_wrist`); it is still a solid obstacle. The left arm keeps the real rig's safety limits
  (`core.rig_limits()`, from the vendored `panda_fk.py` and `rig.json`): the joints' ranges, the TCP box
  (x -720..100, y -420..600, z 0..800 mm), fingertips at most 4 mm below the table and every other link 50 mm above
  it (which limits how far the gripper can tilt near the table), and a keep-out box around the parked arm. A move
  that would break one is refused with the limit named.
- The heaps (`clutter.ONE_ARM`): practice seeds within x -360..-220, y -120..30 (two-armed: x -360..-230,
  y -200..30); the out-of-distribution places are near, far, to the side and beyond the ruler, all within that arm's
  reach. A scene carries `arm = "left"`.
- The reference with true poses (`scripted.pick_clutter`, one arm; free): medium ID 48/50, OOD 46/50; hard ID 48/50,
  OOD 47/50. No failure is a refused reach.

```bash
python -m agentic_robotics.bimanual.improve --env sim_bimanual_grippers --task pick_clutter_one_arm --levels medium hard \
    --model astra --segmenter sam3 --budget-usd 40 --eval-episodes 100 --max-hours 14 --out runs/bimanual/clutter_one_arm_astra_1123
python scripts/session_progress.py runs/bimanual/clutter_one_arm_astra_1123      # the versions: when saved, practice results
python scripts/eval_bimanual_ask.py --program runs/bimanual/clutter_one_arm_astra_1123/program.py --model astra \
    --tasks pick_clutter_one_arm_medium pick_clutter_one_arm_hard --seeds 2000-2049,5000-5049 --run-usd 12 \
    --out runs/bimanual/clutter_one_arm_astra_1123_eval
```

The session is the two-armed one's (same budget, tools, practice truth, `robot.ask`), with the instructions rewritten
for one arm (`write.one_arm`). Two changes apply to every clutter session from here on:
- `define_scenes` takes a field given as null, `""` or `[]` as left out. In the two-armed session all 9 calls with
  objects of the model's own were refused, because the model filled in every field of the schema (`"name": ""` on a
  ball, `"size_mm": []` on a custom object).
- Each version records when it was saved (`state.json`: `versions[i].at` = turn, dollars, tokens, minutes; the copies
  are `versions/vNN.py`), and `scripts/session_progress.py` prints the table of versions with their practice results.

**The session and its evaluation (2026-11-23).** The session ended "submitted" after 130 turns, 132 min and
**$37.34 of $40**: v24 (412 lines; 26 versions, their table in `runs/bimanual/clutter_one_arm_astra_1123/versions.md`,
made by `scripts/session_progress.py`). The evaluation (`scripts/eval_bimanual_ask.py`, cameras with realistic depth
and SAM3 only, no truth; 200 episodes, **$7.40**, on a fourth key; `runs/bimanual/clutter_one_arm_astra_1123_eval`):

| | medium ID | medium OOD | hard ID | hard OOD |
|---|---|---|---|---|
| one-arm reference (true poses) | 48/50 | 46/50 | 48/50 | 47/50 |
| the two-armed session's program on the two-armed tasks | 48/50 | 47/50 | 47/50 | 38/50 |
| **the one-arm session's program (v24)** | **48/50** | **47/50** | **45/50** | **40/50** |
| robot time of its successes: mean (median) | 141 s (124 s) | 90 s (37 s) | 259 s (247 s) | 224 s (196 s) |
| model calls an episode | 1.28 | 2.00 | 2.48 | 3.08 |
| model cost an episode | $0.015 | $0.031 | $0.043 | $0.059 |

**180 of 200** (ID 93/100, OOD 87/100), the two-armed program's total on its own tasks; with cameras only it
matches the true-pose reference on medium and is below it on hard (ID 45 against 48, OOD 40 against 47). No model
call failed. The 20 failures, from each episode's last lines and final state: 10 lifted another object and the
program's check of what it held accepted it (the target still on the table); 8 used up their attempts or the time
without a hold (grasps rejected as too shallow, a pick out of the arm's reach); 1 held the target at 243 mm at the
end and was still judged a failure (not looked into); 1 returned after 2.6 s without a pick. The session's own
last practice results (v23: 41/41 medium, 41/41 hard) overstated it, as the two-armed session's did.

**On the real robot (prepared 2026-11-23, 17:35; not yet run there).** The user judged 180/200 good enough.
`real/run program` now takes the one-arm clutter task (`real/run.py`: `--task clutter_one_arm`; the target is named by
the operator, the task's text is `clutter._text(level, object, "left")`, the limits are the task's):

```bash
P=runs/bimanual/clutter_one_arm_astra_1123/program.py
real/run program --program $P --task clutter_one_arm --object "red cylinder" --level medium --ask --tag NAME              # dry
real/run program --program $P --task clutter_one_arm --object "red cylinder" --level medium --ask --tag NAME --mode live
real/run program --program $P --task clutter_one_arm --level medium --ask --backend sim --seed 2003 --mode live          # rehearsal
```

`--ask` lets the program call the model (at most 4 calls an episode, about $0.02-0.06; ledger tag `real:program`;
`budget.total_usd` has $2 of headroom for it). Rehearsed with the simulation as the robot (seed 2003, medium):
success, 108 s, 2 model calls, $0.03; `tests/test_real_program.py` passes. **What to expect on the arm**: every pick
of v24 is an ordinary move to max(150, top + 85) mm and then a move until contact down to the grasp height, 85 to
125 mm of it. On this arm such a move stops falsely in its first millimetres (0.3b); v24 then finds the hand more
than 35 mm above the object, prints "REJECT shallow grasp", opens, lifts and tries another plan, until it gives up.
It was developed without any real episode. The way that worked for the insertion skill: real episodes into a
continued session (`improve.py --continue-from --real-feedback`, today for `insert_varied` only: the clutter
session does not take those options yet).

## On the real arm (2026-11-23)

`src/agentic_robotics/bimanual/real.py` answers the program API on the real rig, which moves the left Panda only:
`real/run program --program runs/bimanual/<session>/program.py` (real/README.md, "A bimanual session's program on the
real arm"). The insertion program (`insert_varied`) runs there; the clutter tasks need both arms and are refused.
`--backend sim` rehearses the whole path with this simulation as the robot: the insertion program v21 succeeds on 29
of 31 seeds, the two failures being scenes on the right arm's side.

First live run (2026-11-23, the insertion program, run by the user): it failed. The guarded descents (5 mm/s)
stopped on "contact" in free air (joint stiction; `--guard-dz 0.01` raises the threshold), and `set_gripper(0)`
returned widths read in the middle of the closing (21.4 and 37.8 mm), since the gripper's state comes only every
0.2-0.33 s. `RealRobot.set_gripper` now waits on the real arm until the opening has stood still for 0.8 s (at most
5 s). Not yet run again on the arm.

## Not there yet

- No coding-agent session yet (direct control through `drive`, or writing programs); model results are 2 to 10 episodes per cell, one task for programs, grippers only.
- `run --program` runs a program in the runner's process (`practice` and `improve` use the sandbox). The development
  session is one conversation with one model: no workspaces, no certified held-out suites as on the single-arm
  platform, and its practice shows the simulator's true state.
- `--model` speaks the OpenAI Responses API only. A Claude model is tested through `drive` (in Claude Code) or needs
  a loop on the Anthropic API (the converters are there, the loop is not).
- Colour is MuJoCo's rendering, not the splat; the right wrist camera's mount is assumed, not calibrated.
- `insert_cylinder`, `insert_varied` and the bio-lab tasks have no reference solution with the Sharpa hands. The layouts of `open_bottle` cover the area where both reference solutions work, not the whole table.
