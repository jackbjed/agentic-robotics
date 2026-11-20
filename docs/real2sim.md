# real2sim: the agent builds its own simulator from the real world

2026-11-20, user: "this time, the agent start without even a simulator, and it's sent to the "real world" which in
this case is the photorealistic simulator. The agent can look() and receive the rbgd output from the robot cameras
(scene and wrist) with the realistic noisy depth. Based on those observations and interaction with the environment,
the agent has to reconstruct the scene on its own. It should reconstruct it until it feels ready that the sim is good
enough. It should save that reconstruction in the folder corresponding to that session, and then practice skills in
its sim (ie the sim of the "real photorealistic sim")."

The question: how well do skills learned in a simulator that the agent reconstructed itself, from the robot's own
observations, do in the real world? It sits between training in the accurate twin (`evalset_B_ctrl_r1`) and in a
simple, wrong one (docs/simple_sim.md).

## The two worlds

| | the real world | the agent's simulator |
|---|---|---|
| what | the photorealistic twin, condition A (`configs/tasks/tasks_A_rig_home_kit.yaml`) | the agent's `sim/` on the robot kit (`selfsim/`) |
| scene | the scanned table and objects, the white box, the ruler | what the agent built: `sim/scene.xml` (MJCF) |
| colour | the splat composite | MuJoCo's rendering of its materials and textures |
| depth, calibration | the D415 model fitted to real frames; a per-episode calibration error | its own sensor model (`sim.yaml` sensors) |
| layouts | the banks' train layouts (`--idx`) | its `sim/layouts.py` |
| robot, limits, timing, SAM3, arm at home, time limits | the same | the same |
| success checks | the tasks' (sim/scene.py) | the same checks on its declared names (selfsim/world.py) |
| what the agent gets | observations only (frames, the arm's state, its program's own output): no verdict | everything (it is its own) |

Tasks: place-box, close-box, clutter-pick-any (press needs the kettle, which the real table has not).

## What the agent is given

- **The robot kit** (`selfsim/models/robot_kit.xml`, `scripts/build_robot_kit.py`): the twin's model with the scene
  taken out: both arms, the gripper, the arm frame, the cameras at the rig's calibrated poses, the physics options.
  The vendored code needs the scene's bodies by name (task_box.BoxSim), so each is a placeholder: the same names, an
  invisible, non-colliding 4 mm sphere, no sizes, no hinge parameters.
- **Its docs** (`learning/real2sim.py` `r2s_doc`): robot.md, perception.md (the real D415s' faults in words, no
  "twin"), skill_api.md (from scratch), tasks.md = `docs/task_real2sim.md` (the objects' names, no sizes),
  selfsim.md (how to build `sim/`, the commands).
- **Not given:** any size, mass, colour or position of the scene; the judge's verdict in the real world.

## The pipeline (one session)

1. **Reconstruction phase.** The real world is open: `real look` (both cameras at home, the wrist camera from up to 8
   hand poses), `real run explore/X.py` (its own programs: look, touch, push, lift), within the real-world budget
   (40 episodes per session, 100 in the run, 120 s of robot time each). It analyses the frames with `py analysis/X.py`
   (sandboxed; writes `sim/` and `analysis/`), builds `sim/`, checks it (`sim check`) and compares it with real frames
   (`sim render --like <a real frame>`: the same arm pose and believed camera pose; real | yours | the depth
   difference).
2. **`sim ready`** (when it judges the simulator good enough): the simulator must build and settle; it is saved in the
   workspace's `sessions/sNN/sim/` and in `runs/learning/ID/sessions/sNN/reconstruction/`, and the session switches
   to its practice phase: the real world closes for the rest of the session.
3. **Practice phase.** `run --task T` in its simulator (300 episodes per session), the practice records as usual.
4. **After the session** (`learning/loop.py` iterate): the final `sim/` is archived
   (`sessions/sNN/reconstruction_final/`); development: the incumbent AND the candidate on 12 layouts per task sampled
   by the agent's `layouts.py` (seed 11), both in its simulator as it is then; the usual selection rule.
5. **Evaluation** (the operator, `ar learn evaluate`): in the real world on the certified `evalset` / `evalhidden`
   (condition A), and `evalhidden_B` (condition B, plain colour: the condition `evalset_B_ctrl_r1` was scored in).

Resumed sessions start in the reconstruction phase again with a new real-world budget.

## Isolation

- **Programs plan with the kit** in every episode of the experiment (task config `program_mirror: kit` → the server's
  `hello` → `robot/client.py`): RealSim's own model then holds no scene. Checked equivalent: evalset_B_ctrl_r1's s03 on
  6 episodes gave the same results with the full model and the kit (their action logs differ only as much as two runs
  of the same model do).
- **The scene's model files are hidden** from those programs' sandboxes (`selfsim.hidden_paths`: the twin's XML, the
  objects' meshes, every condition's model; a file reads empty, a folder is empty).
- **The agent's simulator runs no agent code in a privileged process**: its scene is data parsed by the platform
  (allowed sections only, files inside `sim/`, reserved names refused, the kit's physics options), its `layouts.py`
  runs in the program sandbox and its answer is checked.
- **The real world's records** in the workspace are the robot's: observations, the program's own output, the robot
  cameras' video and keyframes without the verdict (`record.verdict: false`). The judge's record stays in
  `runs/learning/ID/real_world/`.
- Limits as docs/isolation.md: the vendored source files (task_box.py's object table, ...) are still readable inside
  the sandbox; reading them is against the rules and flagged by guard.py only when a path is spelled out.

## Checks

- `tests/test_real2sim.py`: the kit holds no scene; a hand-made reconstruction (`tests/fixtures/selfsim_example`, crude
  sizes, never shown to an agent) composes, its refusals; layouts sampled in the sandbox and checked; a scripted place
  succeeds and a closed lid is judged closed in the agent's world; a program plans with the kit and sees the hidden
  files empty; the agent's docs give no sizes; the real world's export has no verdict.
- A manual trial (11-20, run `r2s_manual`): `real look` (12 s wall: four frames and the video), `real run` (an
  exploration program that segments the box and moves over it), `py`, `sim check`, `sim render`, `sim ready`, the real
  world refused after it, `run` in the agent's simulator, and one loop iteration with a stubbed agent (development:
  36 + 36 episodes in its simulator, the decision).
- `sim render`'s geometry is exact: on that trial's real frame, the agent's simulator rendered with the episode's own
  (hidden) calibration error lined up with the real image to 0.1 px; at the believed pose it was off by 11 px, which is
  that error.
