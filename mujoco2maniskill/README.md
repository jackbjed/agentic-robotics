# mujoco2maniskill

The project's skill programs, unchanged, on a second simulator: the same robot, gripper, cameras, scenes and task
checks as the project's MuJoCo practice environment, simulated by ManiSkill 3 (SAPIEN, PhysX) and drawn by SAPIEN's
renderer.

Purpose: to see whether a second physics engine can stand in for the real robot, so that the "practise in your own
simulator, then learn from failures elsewhere" experiments get many episodes with an automatic judge.

Everything here lives in this folder. Nothing outside it is changed: the project's code is imported read-only or
copied (each copied piece names its source).

## What is the same, and what differs

| | MuJoCo (the project) | ManiSkill (here) |
|---|---|---|
| Arm | left Panda, base pose of the rig | ManiSkill's Panda at the same base pose |
| Gripper | the project's long tapered fingers | the same collision boxes, masses and tool point (`m2m/build_urdf.py`) |
| Cameras | scene and wrist D415, 640 x 480, the mounts of the rig | the same mounts and intrinsics, the same per-episode calibration error |
| Planning | straight lines by IK, joint path for big turns, the rig's safety limits | the same code and limits (`m2m/kin.py`, `m2m/rig.py`) |
| Speeds, thresholds | 40 mm/s low and guarded, 150 mm/s free, 4 N contact, 40 N safety stop | the same |
| Scenes | a world file's `scene()` | the same scenes, exported body by body (`tools/export_scene.py`) |
| Judge | the world file's `success()` | the same function, on ManiSkill's true state |
| **Contact solver** | MuJoCo's soft constraints | PhysX |
| **Contact softness** | every contact gives a little (a damped spring of 20 ms) | rigid, except: the fingers (a spring contact, `FINGER_STIFFNESS`) and shapes the world gave its own softness, e.g. a sponge (`m2m/env.py _phys_material`) |
| **Rolling friction** | yes (condim 6) | none: emulated by damping the spin of round bodies |
| **Friction of a pair** | the larger of the two coefficients | the average of the two |
| **Joint drives** | position actuators with gravity, error taken out after a move | PhysX drives, gravity of the arm switched off |
| **Depth** | a model fitted to the real D415 (noise, holes, blind band, scale) | the renderer's exact depth, cut below 275 mm |
| **Colour image** | MuJoCo's renderer | SAPIEN's renderer and lights |
| **Second arm** | parked on the right, visible | absent (its keep-out box is still enforced) |

Not carried over: collision checks of planned joint-space paths against the objects (the real rig has none either), a
world's sliding or hinged parts (reported in the scene file as `skipped`), and contacts on a part fixed to another
body are counted as contacts on that body.

## How to run

Two Python environments are used, neither is modified:

- `PROJ=../.venv/bin/python` (the project's: MuJoCo), for exporting scenes;
- `MS=<HOME>/micromamba/envs/robomme/bin/python` (ManiSkill 3.0.0b21, SAPIEN 3.0.3), for everything else.

```bash
cd mujoco2maniskill
# 1. a world file -> one scene file per seed
PYTHONNOUSERSITE=1 MUJOCO_GL=egl $PROJ -I tools/export_scene.py \
    --world ../runs/bimanual/insert_own_cc_xhigh_1125/worlds/w16.py --seeds 2001-2016 --out out/scenes/w16
# 2. one episode (with a video)
CUDA_VISIBLE_DEVICES=0 PYTHONNOUSERSITE=1 $MS -I run_episode.py --scene out/scenes/w16/2001.json \
    --program ../runs/bimanual/insert_own_cc_xhigh_1125/program.py --out out/test/v38_2001 --video
# 3. a skill on all scenes of a world, in parallel
PYTHONNOUSERSITE=1 $MS -I eval.py --scenes out/scenes/w16 --program <program.py> --out out/eval/<name> --jobs 12
# 4. every insertion skill on the three worlds, then the table against the MuJoCo records
tools/run_matrix.sh 12 && python3 tools/table.py
```

`run_episode.py` options:

- `--plain-robot`: the program gets the rig's robot as it is. Without it the world's `robot()` wraps the robot, as in
  the project's evaluations (in the real-feedback worlds that adds arrival errors and the soft fingers' reading).
- `--depth-noise-px X`: stereo-like depth noise (off by default).
- `--no-segmenter`: no `robot.segment()`. By default the project's own SAM3 text service is used (port in
  `configs/services.yaml`); images go to that local service only.

An episode takes 25 to 80 s of computer time for 100 to 250 s of robot time. Sixteen in parallel finish in about
two and a half minutes. No model is called.

Other physical properties of a scene (a copy of the scene file; nothing else changes):

```bash
python3 tools/vary_scene.py --scene out/scenes/sweep_w21/2013.json --out out/scenes/sweep_vary/2013_slippery.json \
    --table-friction 0.15 --friction 0.15      # also: --mass-scale S, --size-scale S, --no-rolling, --bodies PREFIX
```

Not built yet: ellipsoids (the sweeping world's lentils and beans).

## Files

```
m2m/build_urdf.py   the Panda with the project's gripper -> assets/generated/panda_umi.urdf
m2m/env.py          the ManiSkill environment: robot, table, cameras, the scene's bodies
m2m/kin.py          kinematics, IK and the rig's limits (the project's panda_fk.py, imported by file)
m2m/rig.py          moves, guarded moves, gripper, cameras, force estimate; the true state for the judge
m2m/api.py          what a program gets: the project's robot API (copied), the program rules
m2m/seg.py          the SAM3 client (copied)
run_episode.py      one episode: program, log with true positions, the world's check, video
eval.py             many episodes in parallel
tools/export_scene.py   a world file -> scene files (project environment)
tools/dump_rig.py, dump_hand.py   how assets/*.json were measured from the project's model
tools/compare_views.py  the same scene in both simulators, side by side
tools/run_matrix.sh, table.py     all skills x all worlds, and the table
assets/             the measured gripper and rig (inputs); out/ everything generated
```

## Results

See `out/eval/table.md` (made by `tools/table.py`; earlier versions of the rig in `out/eval_v1_colours_as_linear/` and
`out/eval_v2_rigid_fingers/`), and `out/test/` for the single episodes (heavy cylinder, sweeping with other friction,
mass and size). Every number there is a
simulation result: it says how a skill does in ManiSkill on these scenes, not how it does on the real robot.

## The bimanual X5 robot of RoboDojo, in both simulators (`x5/`)

The robot most RoboDojo tasks use (two ARX X5 arms on a table), built from RoboDojo's own files (copied to
`assets/x5/`): the arm's URDF, the two base poses, the drive gains (4400 / 40 for the arm joints, 2300 / 100 for the
fingers, 100 N limits, armature 0.01), gravity of the arms off, the 4 ms step, the table.

```bash
PYTHONNOUSERSITE=1 $PROJ -I x5/build.py        # one URDF for both; MuJoCo's model compiled from the same file
PYTHONNOUSERSITE=1 $PROJ -I x5/traj.py         # one test motion as joint targets (left arm free, right arm picks a cube)
PYTHONNOUSERSITE=1 $PROJ -I x5/run.py mujoco
CUDA_VISIBLE_DEVICES=0 PYTHONNOUSERSITE=1 $MS -I x5/run.py maniskill
python3 x5/compare.py                          # out/x5/compare.json, out/x5/side_by_side.mp4
```

`x5/sim_mujoco.py` and `x5/sim_maniskill.py` have the same interface (`step(16 joint targets)`, `q()`, `tcp(side)`,
`cube()`, `render(cam)`). Two settings were needed on the MuJoCo side to match PhysX at a grasp (`x5/spec.py`):
stiffer contacts (`MJ_SOLREF`) and torsional friction (`MJ_CONDIM`). The links of the robot do not collide with each
other in either simulator. Not there yet: RoboDojo's own objects (USD files), its cameras' exact models, a skill API.

## RoboDojo's `sweep_blocks` task in ManiSkill (`sweep_blocks/`)

RoboDojo's sweeping task rebuilt from its own files (read only; nothing in RoboDojo is changed): the two X5 arms, the
table with its wood picture, the head camera's stand, the dustpan, the brush and the cubes with RoboDojo's meshes and
masses, its 55 saved evaluation layouts, its action stepping (250 Hz physics, 25 Hz actions, 1000 actions), its three
cameras, its instruction and its success test. `sweep_blocks/env.py`'s header lists every item with its source.

```bash
# once: the objects (USD) -> meshes and convex collision pieces in assets/robodojo_sweep/   (this folder's own venv:
#       python -m venv .venv_usd && .venv_usd/bin/pip install usd-core trimesh coacd pillow fast-simplification scipy rtree)
PYTHONNOUSERSITE=1 .venv_usd/bin/python -I sweep_blocks/extract_assets.py
CUDA_VISIBLE_DEVICES=0 PYTHONNOUSERSITE=1 $MS -I sweep_blocks/demo.py 4          # layout 4: the cameras' pictures
CUDA_VISIBLE_DEVICES=0 PYTHONNOUSERSITE=1 $MS -I sweep_blocks/test_check.py 4    # the success test, objects placed by hand
```
```python
from sweep_blocks.env import SweepBlocks
env = SweepBlocks(layout=4); obs = env.reset()
obs, done, info = env.step(action14)    # left arm 6 joints, left gripper 0..1, right arm 6, right gripper 0..1
```

Not the same as RoboDojo: the convex pieces of the collision shapes (CoACD here; PhysX's own decomposition there), the
room and lights, arm self-collision (off here), the `sweep_blocks_random` layouts (other objects, not converted).
Open question in RoboDojo's files: the cubes' meshes are 19 mm, their metadata says 35 mm; the meshes are used here.
No policy or scripted solution has been run on it; RoboDojo itself does not start on this machine, so there is no
picture-by-picture comparison.

## Cross-simulator transfer experiments, conditions A to F (`xsim/`)

Practice simulator: MuJoCo. Target environment: ManiSkill. These are transfers between simulators, not to a physical
robot. Conditions: A zero-shot program; B target feedback only; C fixed inferred practice world + target feedback;
D adaptive practice world + target feedback; E practice world only; F practice directly in the target.

```bash
PY=../.venv/bin/python
$PY -I xsim/launch.py prepare --task insert --rep 0     # no model: private target configurations, the shared look and documents
$PY -I xsim/launch.py init    --task insert --rep 0     # PAID: the shared initial program (= A) and the shared practice world; the snapshot
$PY -I xsim/launch.py run     --task insert --rep 0 --cond D      # PAID: one condition's isolated session
$PY -I xsim/launch.py final   --task insert --rep 0     # no model: frozen final programs on the ID and OOD test splits
$PY -I xsim/launch.py report  --task insert             # manifests, development curves, the final table
PYTHONNOUSERSITE=1 MUJOCO_GL=egl $PY -I xsim/smoke_test.py   # no model: 56 checks of permissions, initialization, accounting, records
```

- `xsim/config.py`: every budget, cap, split, seed, validation rule and the pricing table (overridable with `--config`).
- `xsim/session.py`: a session's record and the ONE command its agent has; what each condition may do is decided there.
- `xsim/target.py`, `xsim/target_side.py`: the evaluator's side (configurations and their admission, batch runs, the
  agent-visible payload without true positions or evaluator data).
- `xsim/prompts.py`: the shared template and the A-F blocks. `xsim/tasks.py`, `xsim/tasks/*_target.py`: the tasks and
  their hidden target worlds (fixed objects; only where they stand varies).
- Records: `out/xsim/<task>/rep<r>/<cond>/` (versions, worlds, practice, rounds with the exact payload, revisions.jsonl,
  checkpoints, manifest.json); the evaluator's private records in `rep<r>/private/`; final results in `rep<r>/final_eval/`.

Development rounds (B, C, D): `target_submissions` = 5 rounds, each on the same `target_panel` = 20 development
configurations. `shown_solvable` (default: the development panel) names the sets whose every configuration must be
solved in the target by the task's reference program (`xsim/reference/`: copies of earlier sessions' programs, the
harness's own, never given to an agent) before it is admitted; the runs are in `private/dev/reference_runs/` and
`validation.json`. A configuration the reference does not solve is set aside, not called unsolvable.

What it runs on today: the single-arm Panda rig (tasks `insert` and `sweep`), where the MuJoCo practice simulator, the
program interface and agent-written worlds already exist. It does NOT yet run on RoboDojo's sweep_blocks (the two X5
arms): that needs an X5 practice simulator in MuJoCo with the same program interface and agent-written objects.
Not implemented: retrospective evaluation of checkpoints; re-validation of the OOD variants; a real-agent pilot
(the launcher's call of the Claude Code CLI is written after the existing launcher but has not been run).

### sweep_blocks: is it solvable by sweeping, and the fixed panel of 20 episodes

```bash
MS=<HOME>/micromamba/envs/robomme/bin/python
$MS -I sweep_blocks/reference_sweep.py 4 --video    # the scripted SWEEPING reference (TRUE positions; the harness's check, not a skill)
$MS -I sweep_blocks/reference_sweep_all.py          # ... on RoboDojo's 55 saved layouts -> out/robodojo_sweep/reference_sweep.json
$MS -I sweep_blocks/make_panel.py --n 20 --seed 5 --candidates 36 --out DIR     # candidates -> the first 20 the reference solves
$MS -I sweep_blocks/run_panel.py --verify           # the reference again on the panel's 20 episodes
$MS -I sweep_blocks/run_panel.py --program P.py --out DIR [--video]     # one ROUND: a program on the same 20 episodes
$MS -I sweep_blocks/reach_map.py --map              # where each arm reaches with its fingers down; the dustpan's handle and mouth per layout
```

`reference_sweep.py`: the left arm takes the brush by its handle and lays it in front of the robot; the left arm then
holds the dustpan by its handle; the right arm takes the brush by its HEAD, turns it across the mouth's direction and
sweeps the cubes over the lip in short steps (heading corrected, raised over the lip and ridge), lifts it straight up,
puts it down to the right of the dustpan; both arms go home. No cube is picked up. What it took:
- held by the handle the brush swings round in the grip (the fingers are convex hulls, no torsional friction): the head is held;
- a cube pushed low jams against the dustpan's lip (a 5 mm step in the collision shape, 3 mm in RoboDojo's mesh): the
  brush is raised as it reaches the lip, so the cube is pushed at its top and tips over it;
- the brush see-saws about the grip: cubes far from the grip are passed over at the lip, so later strokes are centred on one cube.
`assets/robodojo_sweep/panel20/`: `ep_00..19.json` (RoboDojo's layout format: the dustpan near the middle, its mouth within
15 degrees of +x; 3 cubes 25 to 150 mm in front of the lip and within 35 mm of the mouth's axis; the brush on the left where
the left arm can take it), `verification.json` (all 36 candidates and their outcomes: 20 solved), `reverification.json`,
`sheet_head.jpg`. `reference.py` and `panel20_superseded_pick_and_place/` are the earlier check that PICKED the cubes up and
put them in: not a sweep, kept for the record only.
On RoboDojo's own 55 saved layouts the sweeping reference solves 0 (4 of 216 cubes in; `out/robodojo_sweep/reference_sweep.json`):
their cubes lie wider than the mouth, their dustpans are often out of the arms' fingers-down reach (handle and mouth
both reachable in 22 of 55), and they hold up to 5 cubes. That is the limit of this reference, not a proof that they
cannot be solved. Layout 4, which the zero-shot runs a-d were given, is among the unsolved.

Looks (`sweep_blocks/looks.py`): each panel file carries an "Appearance" entry: the colours of the dustpan, the brush
and each cube, and one of 10 tables (RoboDojo's red wood, a pale wood, a grey wood, a finely speckled stone; plain white,
grey, slate, sand, green, blue), each table used twice over the 20 scenes. Every object's colour is at least 50 (CIE Lab)
from the table's colour, every cube at least 30 from the dustpan and 25 from the other cubes (`panel20/looks.json`).
Only the looks change: the sweeping reference solves all 20 with the same robot times as before.
`$MS -I sweep_blocks/looks.py --panel DIR --seed 11` draws them; `looks.SweepBlocks` shows them. `run_panel.py`,
`panel_sheet.py` and `reference_sweep.py` use it; `env.py`, `run_program.py --look` and `zero_shot.py` do not yet (they
were left untouched while two zero-shot sessions were running): a layout is shown there in RoboDojo's own colours.

## A to F on the two-arm task: sweep_blocks (`xsim/bi/`, task `sweep_blocks`)

Practice: RoboDojo's two X5 arms in MuJoCo (`xsim/bi/mj.py`) with the SAME program interface as the target
(`sweep_blocks/program_api.py` X5Robot, unchanged: same commands, kinematics, speeds, 40 s; the same three cameras),
the things on the table taken from an agent's practice world (MuJoCo XML of primitives, table frame).
Target: `sweep_blocks/env.py` in ManiSkill, with each scene's looks, judged by RoboDojo's test plus one added rule:
no block may be GRIPPED (squeezed between a gripper's two fingers for three actions running): sweeping, not picking.

```bash
PY=../.venv/bin/python
$PY -I xsim/launch.py prepare --task sweep_blocks --rep 0    # no model: all target scene sets (each shown solvable by the sweeping reference)
PYTHONNOUSERSITE=1 MUJOCO_GL=egl $PY -I xsim/check_bi.py     # no model: conditions A-F played by scripts at the real settings; the C/D allowance
$PY -I xsim/bi/practice_run.py --world W.py --seed 3 --program P.py --out DIR --task-text-file T     # one practice episode (MuJoCo)
```

- `xsim/bi/backend.py`: practice batches, target batches, the scene sets (development panel, F's pool, the example,
  test_id, three OOD splits: other tables, cubes 1.3 times larger, cubes 3 times heavier and half as rough on a
  slippery table), frozen read-only with a manifest of hashes.
- `xsim/bi/target_run.py`: the evaluator's episode and the agent-visible payload (no true positions, nothing of the judge).
- Every round's record names the panel's identity and the evaluator's hash (`rounds/round_k/round.json`); a session
  stops if either differs from what it started with.
- `out/xsim/sweep_blocks_texts/texts.md`: every text the sessions get (for review before anything paid).

### What is kept of every agent session (`<record>/claude/`)
- `invocation_<k>.json`: written before each CLI invocation starts (the prompt given, the instructions appended to the
  system prompt, model, effort, tools, the budget and time left, the command line) and completed when it ends (why it
  stopped, its cost).
- `claude_<k>.jsonl`, `claude_<k>.stderr`: the CLI's stream, written line by line as it arrives: assistant messages,
  tool calls, tool results. A session that fails or reaches a limit leaves everything up to that moment.
- `transcript.md`: the same in reading order (`launch.transcript(record)` makes it at any time, also for a running session).
- `cli_files/`: the CLI's own session file and the session tool's server log. `../workspace_final/`: the workspace as left.
What the CLI does not expose is not there: most thinking blocks arrive empty (a signature only).

Limitations of the sweep_blocks experiment, to be stated with its results: `xsim/bi/LIMITATIONS.md` (among them:
practice worlds are made of primitive shapes only; meshes are refused, by the user's decision of 2026-11-27 for the
builder, C and D alike).
