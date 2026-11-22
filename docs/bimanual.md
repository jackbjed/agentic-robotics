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
from the single-arm platform (`sim/`, `bin/ar`): no splat rendering, no world server, no program sandbox (see
"Not there yet").

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
  pitch, with friction; after one full turn the cap comes free). An episode's layout names the objects on the table;
  the others are parked out of sight.
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

`bimanual/tasks.py`. The same three tasks run in both environments. The seed sets the layout (and the sensor
errors). The judge reads the simulation's true state after the scene has rested for 1 s.

| task | arms | layout | success | limits |
|---|---|---|---|---|
| `open_bottle` | 2 | the bottle in front of the robot (x −330..−210, y −240..−120 mm; seed 0: the reference spot) | the cap is off the bottle (held or on the table) and the bottle stands upright on the table | 600 s, 80 calls |
| `move_across` | 2 | a roll of white tape on the right, where only the right arm reaches | the roll rests on the table beyond y = 400 mm, where only the left arm reaches, and the robot does not touch it | 400 s, 60 calls |
| `lift` | 1 | the roll in front of the robot, where both arms reach | the roll is held at least 50 mm above the table, touching nothing but the robot | 240 s, 40 calls |

The roll is a ring 50 mm across and 27 mm high (0.06 kg). Limits are robot time and, for direct control, tool calls;
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
| `--model KEY` | a model of `configs/models.yaml` through the tools, on the platform's model client (`perception/vlm.py`: key file, prices, the budget ledger; tag `bimanual_TASK:ENV:SEED`). **This costs money**; `--max-usd` caps the episode (default 3) |

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

## What is verified (2026-11-21)

Reference solutions, `sensors="ideal"`:

| | `lift` | `move_across` | `open_bottle` |
|---|---|---|---|
| grippers | 6/6 seeds, 15-18 s | 6/6, about 80 s | 16/16, about 72 s |
| dexterous | 6/6, 17-19 s | 6/6, about 85 s | 11/11, about 71 s |

The dexterous solutions use the three grasps of `--hints`. The roll lift also passes when given as tool calls
(`sensors="d415"`, both environments), replayed, and driven from the shell, with the same result each way. The
example program lifts the roll with realistic sensors in 5 of 6 seeds. The model loop is tested with a stub model
only: **no model or agent has been evaluated here yet.**

## Not there yet

- No agent results: no Astra episode, no coding-agent session.
- Programs run in the runner's process, not in the platform's sandbox, and there is no learning loop
  (workspaces, practice, held-out suites) for these environments.
- `--model` speaks the OpenAI Responses API only. A Claude model is tested through `drive` (in Claude Code) or needs
  a loop on the Anthropic API (the converters are there, the loop is not).
- Colour is MuJoCo's rendering, not the splat; the right wrist camera's mount is assumed, not calibrated.
- Three tasks. The layouts of `open_bottle` cover the area where both reference solutions work, not the whole table.
