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
| `run` / `run.py` | the operator's commands: `check`, `episode`, `verdict`, `summary` |
| `server.py` | the real-robot episode server: the arm server and both D415s (backend `robot`), or the simulator standing in for them (backend `twin`); modes `dry` and `live`; recording |
| `host.py` | the program's host in its sandbox: the platform's (`src/agentic_robotics/execution/host.py`) with the real calibration and speed |
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
  - with a model call: add `--vlm configs/experiments/<exp>.yaml --vlm-usd X` (at most one call per episode;
    ledger tag `real`).
- **Rehearsal without the robot:** `--backend twin [--split val --level 1 --idx 0 --seed 13]` runs the same path on the
  simulator at the calibrated cameras, with its judge.
- **Robot backend without the robot:** start a copy of the arm server with `--fake` on another port, then pass
  `--port P --replay-frames <a real run's frames/>`. This is how it was tested (11-19).

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

## What a dry run shows, and what it does not

The program gets live frames and the arm's real state, so its first perception and its first plan are real: where it
finds the target, which grasp it picks, and where the first move goes. Since the arm does not move, every later look
comes from the same pose. From the second move on, the plan is what the program would do if its moves had happened
without the view changing. Read `plan/sheet.jpg` for the first moves. A live run with the arm server confirming each
trajectory is the step after.
