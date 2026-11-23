# real/: evaluating skill programs on the real Panda

This folder runs a skill program (an initial skill, a learning run's checkpoint, or a baseline) on the real Franka
Panda and records what happened. It reuses what already ran on the robot for Astra + skills (11-12/13): the arm server,
`real_sim.RemoteArm` and `RealSenseCams`, `recorder.Recorder` and `compose`, and the calibrated rig (`calib.json`,
`rig.json`), from the vendored panda_twin copy.

**The program runs exactly as in simulation.** It runs in the same sandbox (no network, read-only code) and gets the
same `ctx`: the robot interface (RealSim), the cameras, SAM3, and the skill library or `ctx.tools`. The only
differences are that its RealSim gets the real rig's calibration (`calib.json`: the cameras' poses and the 7.1 mm flange
adapter) and the real robot's speed (`rig.json` speed 0.5: half the twin's move times), as `real_box.make_rig` builds
it. The program talks to `real/server.py` through a Unix socket; only that server touches the robot.

| file | |
|---|---|
| `run` / `run.py` | the operator's commands: `check`, `episode`, `program`, `verdict`, `summary` |
| `server.py` | the real-robot episode server: the arm server and both D415s (backend `robot`), or the simulator standing in for them (backend `twin`; `sim`: the bimanual simulation, for `program`); modes `dry` and `live`; recording |
| `host.py` | the program's host in its sandbox: the platform's (`src/agentic_robotics/execution/host.py`) with the real calibration and speed |
| `host_program.py` | the host of a bimanual session's program (`run program`): `bimanual/real.py`'s robot over RealSim |
| `ghost.py` | each planned trajectory drawn over the scene camera's frame: the arm at its end, the fingertip path |
| `results/` | one directory per run (not in git) |

## Safety

- **Dry run is the default: nothing is sent.** Every trajectory the program plans goes through RealSim's checks, then
  is recorded and drawn (`plan/`), and the arm stays where it is. Start the arm server read-only (`srv`, without
  `--allow-motion`) for dry runs: then it cannot move even by mistake.
- **A live run on the real arm needs all of these:**
  - `--mode live`;
  - the operator at this terminal, typing `LIVE` after the checklist;
  - the arm server started with `--allow-motion`.

  Leave out the server's `--no-confirm`: it then asks for every trajectory in its own terminal (Enter = go, `n` =
  refuse). Keep the E-stop in your hand.
- **Limits per episode:** 150 s of robot time (motion and waits only), 40 trajectories. On top of these come the arm
  server's own limits (joints, speed, TCP box, keep-out around the right arm, table, force) and RealSim's, the same as
  in simulation.
- **Stopping:** Ctrl-C stops the program and no further trajectory is sent. The arm holds where it is; the arm server
  and the E-stop do the rest.
- **No network for the program.** It reaches the arm only through `server.py`, never the arm server's TCP port.

## Bring-up (panda_twin `code/real/REAL_ROBOT.md` §0–2 first)

```
# terminal A: the arm server (read-only for a dry run; --allow-motion for a live one)
~/miniforge3/envs/mobile_platform_arm310/bin/python -I ~/tdieudonne/panda_twin/code/real/arm_server.py [--allow-motion]
# SAM3: ours (the same model and settings as the robot's), or --sam3 robot for the robot's own :8131/:8134
bin/ar services start sam3_text sam3_points
# the arm at the twin's home pose (a motion: confirmed in terminal A)
env MUJOCO_GL=egl ~/miniforge3/envs/teleavatar_client/bin/python -I ~/tdieudonne/panda_twin/code/real/real_box.py home
```

## Commands

```
real/run check --object "red tape roll"            # no motion: the arm's state, a frame from each camera, SAM3's masks
real/run episode --skills SKILL_DIR --object "red tape roll" --tag NAME              # dry run (default)
real/run episode --skills SKILL_DIR --object "red tape roll" --tag NAME --mode live  # live, then the operator's verdict
real/run verdict real/results/<run> success|fail [--knocked-off] [--intervened] [--note "..."]
real/run summary                                   # results/summary.md: every episode
```

- **Skills:**
  - from a learning run, a checkpoint, e.g. `runs/learning/opusA_evalset_r1/checkpoints/s01`;
  - from scratch: add `--library off`;
  - **Astra is on by default** (2026-11-19): at most one call per episode, with the vlm block of the skill's own
    learning run (`runs/learning/<run>/experiment.json`), else `configs/experiments/fidelity2_A.yaml`'s, or
    `--vlm EXPERIMENT`. `--vlm-usd X` caps all real runs' calls together (ledger tag `real`, default $2). `--no-vlm`
    turns it off.
  - **Put it in the box:** `--task place` tells the program "Put the <object> into the white box." (entry
    `skills/place.py`). The default, `--task pick`, tells it "Pick up the <object> and lift it."
- **Rehearsal without the robot:** `--backend twin [--split val --level 1 --idx 0 --seed 13]` runs the same path on the
  simulator at the calibrated cameras, with its judge.
- **Robot backend without the robot:** start a copy of the arm server with `--fake` on another port, then pass
  `--port P --replay-frames <a real run's frames/>`. This is how it was tested (11-19).

## A bimanual session's program on the real arm: `real/run program` (2026-11-23)

The bimanual sessions (docs/bimanual.md) write one `program.py` against the `robot` API of
`src/agentic_robotics/bimanual/api.py`. `real/run program` runs such a program on the real left Panda through
`src/agentic_robotics/bimanual/real.py`: the same commands, in mm and degrees in the table's frame, answered by the
real rig (RealSim, the arm server, both D415s, SAM3). The program is the session's file, unchanged, in the same sandbox.

**One arm only.** The rig moves the left arm; the right arm stays parked. A move of the right arm into its parked zone
(y < -280 mm) is answered "done"; any other use of it ends the episode. So only tasks that one arm can do run here: for now
**`insert_varied`** (put the cylinder on the stick). **The clutter tasks need both arms and are refused on the robot.**

```
P=runs/bimanual/varied_tables_1122/program.py     # the insertion session's program (v21)
real/run program --program $P --backend sim --mode live --seed 2001   # free rehearsal: the simulation is the robot
real/run check --object "cylinder with a hole"                        # no motion: arm, cameras, SAM3
real/run program --program $P --tag NAME                              # dry run on the robot: nothing is sent
real/run program --program $P --tag NAME --mode live                  # live: type LIVE, then the verdict
real/run verdict real/results/<run> success|fail [--note "..."]
```

**Where to put the cylinder and the stand** (table frame: origin at the table's centre, +x toward the scene camera,
+y from the right arm toward the left; the left arm's base is at (-790, 40) mm):

- both within x -380..-180 mm and y -150..+130 mm, their centres at least 150 mm apart, nothing else near them;
- at least one of them at y > -60 mm: the program picks the arm from where it sees them, and takes the right arm
  when both are on the right arm's side (the episode then ends at its first move);
- not near x < -370, y < -190 mm: the left arm would enter the keep-out box around the parked right arm.

Run the dry run first: it prints the program's own first lines (where it sees both objects, the arm it picks).

**What differs from the simulation, and the options:**

| | |
|---|---|
| moves until contact | `move(..., stop_on_contact=True)` is the arm server's guarded line (descents only), at `--guard-speed` (default 0.005 m/s, the speed it was validated at on the arm; the simulation's is 0.04), stopped when the hand is held up `--guard-dz` (rig.json: 2 mm) or the vertical force changes by 4 N. The arm server's safety stop (joint lag, 35 N) answers the move with "SAFETY STOP: ..." |
| the program's clock | `robot.time_s()` does not count the extra time of those slow moves, so the program's own time checks (written against the simulation's speeds) still mean what they meant. The server's limit (`--time-limit`, default 2.5 x the task's = 1500 s) and every reported time are the rig's. `--robot-clock`: the program sees the rig's clock |
| settle | every free move ends with the arm server's settle (at most 2 s), a move until contact that touched nothing too: the real arm otherwise stops some mm to cm from the pose asked for. `--no-settle`: RealSim's own settles only |
| force | `state()`'s force is the arm's estimate minus its value at the start (hand free and empty). `--raw-force`: as estimated |
| pushing down | a plain move straight down from where a move until contact has just touched (the program does it to get past light contacts) is sent as a guarded line with the arm server's widest guard (10 mm held up, 15 N). `--free-push`: as any other move, ended by the 35 N abort |
| limits | `--max-trajectories` (40 dry, 400 live), `--max-stops` (3 safety stops of the arm server end the episode) |
| `robot.ask` | off unless `--ask` (ledger tag `real:program`, `--ask-usd`); the insertion program never asks |

**Rehearsals (2026-11-23, no robot, no model call).** `--backend sim` puts the bimanual simulation's scene of the task
(left arm, its two cameras with the realistic depth, the task's judge) behind the same server and the same adapter:
`sim/video.mp4` and `sim/result.json` in the run's directory. The insertion program (v21) there: 29 of 31 seeds
(2010, 2013-2042); both failures are scenes on the right arm's side (the placement rules above). About 170-190 s of
robot time when the first try goes in (the two 280 mm descents until contact take 56 s each at 5 mm/s), 630-670 s
when it has to search and grasp again. The same program with both arms in the simulation: 13 of 13, 94 s. With the
settles: 8 of 8. The robot backend was run against the arm server's `--fake --fake-real` arm on another port with
replayed frames (`--port P --replay-frames DIR`): 23 trajectories sent, none refused; the settles brought the free
moves' end error from up to 12 mm down to 4 mm.

**Not known before a real run:** whether the guarded descents false-trigger on this arm (joint stiction: rig.json's
note) and how the program reacts; the force estimate's drift with the pose (the tare is taken once, at the start);
the real objects (the wooden cylinder and the 3-stick stand differ from the simulation's; which stick SAM3's
"vertical stick" returns is not controlled). A dry run cannot show more than the first look: the arm does not move,
so every later frame is the same.

## What a run writes (`results/<time>_<backend>_<mode>_<tag>/`, its README.md lists them)

| file | |
|---|---|
| `spec.json` | what ran: the skill (path, hash, a copy in `skill/`), mode, backend, limits, the platform's git revision |
| `result.json` | the verdict, robot time, trajectories planned / sent / refused, the program's result and usage |
| `actions.jsonl` | every trajectory: its joints and times, phase, planned or sent, the arm server's reply |
| `events.jsonl` | the program's events, SAM3 calls (phrase, scores), model requests and replies |
| `frames/` | every camera frame the program got (colour, depth in mm, the joints then) |
| `plan/` | each planned move drawn over the scene camera (`NNN.jpg`, `sheet.jpg`) |
| `video/`, `episode.mp4` | both cameras at 15 fps, and side by side with the phase, gripper, force and joint speed (the arm server's state log) |
| `program/` | the program's own output (`program.json` with RealSim's trajectory record, `host.log`, debug images) |

**Verdict:**
- live on the real arm: the operator answers after the episode (held up by both fingers, nothing else lifted? anything
  knocked off? any intervention?), or later with `real/run verdict`;
- twin: the simulator's judge;
- dry: none.

## Feeding real runs back into a learning run

```
bin/ar learn add-real --run-id fidelity2_A_r1 real/results/<run> [...]   # into the agent's workspace, real/
bin/ar learn extend   --run-id fidelity2_A_r1 --practice 100 --agent-usd 15   # if its budgets are used up
bin/ar learn run      --run-id fidelity2_A_r1 --resume-session             # the next iteration sees them
```

The next session's prompt announces the new runs. docs/learning.md, "Real-robot feedback and continuous sessions", has
the details.

## What a dry run shows, and what it does not

The program gets live frames and the arm's real state, so its first perception and its first plan are real: where it
finds the target, which grasp it picks, and where the first move goes. Since the arm does not move, every later look
comes from the same pose. From the second move on, the plan is what the program would do if its moves had happened
without the view changing. Read `plan/sheet.jpg` for the first moves. A live run with the arm server confirming each
trajectory is the step after.
