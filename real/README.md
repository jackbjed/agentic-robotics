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
(y < -280 mm) is answered "done"; any other use of it ends the episode. So only tasks that one arm can do run here:
**`insert_varied`** (put the cylinder on the stick), **`clutter_one_arm`** (take the named object out of a heap) and
the from-nothing skills **`sweep`**, **`insert_own`** and **`hang_mugs`** (below). **The two-armed clutter tasks are
refused on the robot.**

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
| the program's clock | `robot.time_s()` counts a move until contact as the simulation would (its way at 40 mm/s), whatever the rig needed for it (5 mm/s, lines cut short and repeated, settles: since 2026-11-25), so the program's own time checks (written against the simulation's speeds) still mean what they meant. The server's limit (`--time-limit`, default 2.5 x the task's = 1500 s) and every reported time are the rig's. `--robot-clock`: the program sees the rig's clock |
| settle | every free move ends with the arm server's settle (at most 2 s), a move until contact that touched nothing too: the real arm otherwise stops some mm to cm from the pose asked for. `--no-settle`: RealSim's own settles only |
| force | `state()`'s force is the arm's estimate minus its value at the start (hand free and empty), with its sign turned to the simulation's: what pushes the hand up is +z (this arm's server reports the opposite: `bimanual/real.py` `FORCE_SIGN`, 2026-11-25). `--raw-force`: as estimated |
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

## The from-nothing skills on the real arm: `real/run program --task sweep | insert_own | insert_own_cc | hang_mugs | hang_mugs_cc` (2026-11-25)

Three programs come from from-nothing sessions (`improve.py --own-task`): each was written in simulation only, in
worlds its session wrote itself, with no real object, no asset and no test case given. They run here as the insertion
and clutter programs do: the file unchanged, in the sandbox, the left arm alone.

| `--task` | the program | its session's task world | limit on its clock |
|---|---|---|---|
| `sweep` | `runs/bimanual/sponge_sweep_own_1124/program.py` (v41) | `worlds/w23.py` | 600 s |
| `sweep_cc` | `runs/bimanual/sponge_sweep_own_cc_xhigh_1125/program.py` (v31, a Claude Code session; its text names the sponge, the small objects and the marker: `--sponge`, `--scrub`, `--sponge-on`, `--pieces`, `--target`, or `--instruction`) | `worlds/w21.py` | 600 s |
| `insert_own` | `runs/bimanual/insert_own_1125/program.py` (v48) | `worlds/w10.py` | 600 s |
| `insert_own_cc` | `runs/bimanual/insert_own_cc_xhigh_1125/program.py` (v38, a Claude Code session: `bimanual/improve_cc.py`) | `worlds/w16.py` | 600 s |
| `insert_own_cc_real` | `runs/bimanual/insert_own_cc_real_1125/program.py` (v63: the Claude Code skill after its session with four real episodes, specialised to this rig's stand) | `worlds/w21.py` | 600 s |
| `insert_own_cc_real2` | `runs/bimanual/insert_own_cc_real2_1125/program.py` (v80: the same skill after a second session, with six real runs of v63) | `worlds/w25.py` | 600 s |
| `insert_own_cc_fixed` | `runs/bimanual/insert_own_cc_fixedworld_1125/program.py` (v66: the fixed-world ablation, the same four real episodes as v63 but no rewriting of its world) | `worlds/w16.py` (the first session's) | 600 s |
| `insert_own_cc_episodes` | `runs/bimanual/insert_own_cc_episodes_1126/program.py` (v61: the episodes-only ablation, the four real episodes of v38 and no text about the rig) | `worlds/w18.py` | 600 s |
| `insert_with_real_info` | `runs/bimanual/insert_with_real_info_1126/program.py` (v39: a from-nothing session given the text about the rig at its start, no real episode; it closes the fingers on the stick twice near its base) | `worlds/w13.py` | 600 s |
| `insert_from_img` | `runs/bimanual/insert_worlds_from_img_1125/program.py` (v23: a `worlds_from_img` session, from nothing with two real images of this rig's stand; `bimanual/worlds_from_img.py`) | `worlds/w13.py` | 600 s |
| `hang_mugs` | `runs/bimanual/hang_mugs_own_1125/program.py` (v50) | `worlds/w11.py` | 900 s |
| `hang_mugs_cc` | `runs/bimanual/hang_mugs_own_cc_xhigh_1125/program.py` (v38, a Claude Code session) | `worlds/w08.py` | 900 s |

**`insert_own` is not `insert_varied`.** `insert_varied` runs the earlier insertion programs (v21 to v66), written
for the simulator's built-in cylinder and stand. `insert_own`'s program learned the task from nothing: another task
text, a stand that is free on the table with one stick, a cylinder that may lie on its side, and at the end the
gripper open and back at its start pose. Give each program its own `--task`: the text it is told and the operator's
question differ.

```
P=runs/bimanual/insert_own_1125/program.py            # or the sweep's, or hang_mugs_own_1125's
real/run check --object "hollow cylinder"             # no motion: arm, cameras, SAM3 ("kitchen sponge", "mug")
real/run program --task insert_own --program $P --tag NAME                               # dry run: nothing is sent
real/run program --task insert_own --program $P --tag NAME --mode live --confirm-contact # live: type LIVE, then the verdict
real/run verdict real/results/<run> success|fail [--inside N --total M | --hung N] [--note "..."]
```

- **The task's text** given to the program is the one its session's task world gave it, word for word (`run.py`
  `SWEEP`, `INSERT_OWN`, `HANG_MUGS`). `--instruction TEXT|FILE` gives another.
- **The verdict.** After a live episode the operator answers the task's question (`run.py` `HELD`) and, for two of
  the tasks, counts: `sweep`, how many small objects there were and how many ended inside the target area
  (`inside`, `total`, `fraction` in `result.json`'s verdict); `hang_mugs` and `hang_mugs_cc`, how many of the two mugs hang (`hung`).
  `real/run summary` shows the counts.
- **The scene.** The LIVE checklist prints where each program practised (`run.py` `PROGRAM_MORE`): sizes, places,
  what it does and how long it takes. In short:
  - `sweep`: one continuous flat work surface; one rectangular target of 160-235 mm (a coloured sticker or sheet fixed
    flat, or a tape outline) centred about x -350..-245, y +100..+320 mm; 12 to 25 small dry pieces outside it; a dry
    rectangular sponge of 65-115 x 32-65 x 28-55 mm at least 75 mm from the target; up to 3 distractors.
  - `insert_own`: a cylinder 32-72 mm across and 20-70 mm long with a hole 1.5-7 mm wider than the stick; a FREE stand
    with ONE vertical stick 5-22 mm thick and 65-180 mm long; both inside x -450..-220, y -180..+480 mm, 160 to 430 mm
    apart.
  - `insert_own_cc`: a cylinder STANDING ON AN END, 22-68 mm across and 12-55 mm high, not black; a stand with ONE round stick
    5-24 mm thick; both centred inside x -440..-200, y -170..+230 mm. The program closes the gripper on the stick itself, low,
    to find its centre.
  - `hang_mugs`: a heavy free-standing rack with TWO pins 260-370 mm above the surface, its foot about x -275..-205,
    y +120..+235 mm; two upright mugs 54-88 mm across with closed handles, inside x -490..-350, y -170..+510 mm.
    Unbreakable mugs: the gripper closes with up to 70 N.
  - `hang_mugs_cc`: a free-standing MUG TREE (a base 170-230 mm across, a central post, straight branches rising outward at
    15-40 degrees, 90-140 mm long, starting 150-210 mm above the base), centred about x -400..-240, y -100..+300 mm; two upright
    mugs 72-92 mm across with closed handles (opening 20-32 mm wide, at least 36 mm high). Unbreakable mugs: the program
    pinches a mug by its RIM. The rack must be the tallest thing on the table.
- **Limits.** The program's own limit is its session's (the table), on the program's clock. The rig's defaults per
  live episode (`run.py` `OWN`): `sweep` 1800 s and 800 trajectories (it wipes in many short moves until contact, each
  at 5 mm/s here); `insert_own` 1500 s and 400; `hang_mugs` and `hang_mugs_cc` 2250 s and 400. `--time-limit`, `--max-trajectories`.

**Rehearsal (no robot, no model call):** `--backend sim --mode live --seed N [--world FILE]` puts the simulation behind
the same server and adapter, in a session's world (default: the task world beside the program). The world gives the
scene of the seed, the task's text, the robot the program gets (a world may wrap it to keep a record for its check)
and the check. The world's code runs only in the program sandbox: `real/world_tool.py` builds its scene and text before
the episode; `real/host_program.py` wraps the robot and runs the check on the end state the server hands over
(`sim/state.json` keeps it). The simulation of the main line has no worlds, and each world needs the simulation its
session ran on, so the rehearsals run from two copies that have this folder's files, with this checkout's environment:

```
C=../agentic-robotics-sweepreal      # sweep: the sim-dev simulation (w23 was written on it)
C=../agentic-robotics-ownreal        # insert_own, insert_own_cc, hang_mugs, hang_mugs_cc: the own-1125 simulation (no ruler, the table-height fix)
.venv/bin/python -I $C/real/run.py program --task insert_own --program $PWD/$P --backend sim --mode live --seed 2001 \
    --out-root $PWD/real/results --tag NAME
```

After a change to `real/*.py` or `src/agentic_robotics/bimanual/real.py` here, copy the file to both.

**Not known before a real run:**
- `sweep`: the program holds its wiping force at about 1 N from the arm's force estimate and finds the surface by a move
  until contact; on this arm the estimate moves by 1 N and more in free air and the guarded descents stop without
  contact (`--confirm-contact`). Its sponge, pieces and target sheet are simulated ones: a rigid block on six springs,
  rigid grains and card scraps, a sheet that cannot move.
- `insert_own`: it finds the hole by descents of under a millimetre until contact and by the force estimate (4 N), and
  corrects the tool's position under load from where the arm says it is; the real arm ends its moves some mm off and
  its guarded lines stop in free air. The real stand has three sticks: its worlds had one.
- `hang_mugs`: no real rack was described to it; its racks have two straight pins with upturned tips at 260-370 mm. It
  tells a held mug's weight from a contact by the force estimate. It never looks again after letting go.

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

## Written on the spot, rewritten after a failure: `real/direct-skill` (2026-11-25)

The ablation of the sessions with worlds: no simulator. The agent writes the program from one look at the real scene;
`real/run program` runs it; on a failed verdict the agent is shown what the episode recorded (the program, its output
with the trace of commands, frames of both cameras' videos, the shown images) and a new look, and writes the whole
program again; at most `--rewrites` times (default 2: three runs). `real/direct_skill.py` has the options.

```
real/direct-skill --tag NAME --task insert_own_cc --mode live -- --confirm-contact --depth-fps 1
```

- `--writer claude` (default): one isolated headless Claude Code session per attempt (Opus, xhigh, at most $5; file tools
  in a workspace of its own under `/var/tmp/ar-cc/`, NO shell, no memory, no settings); `--writer astra`: one paid call
  of the OpenAI model per attempt; `--writer file:DIR`: programs from files (tests).
- Records: `runs/bimanual/real_direct/<time>_<tag>/a0, a1, a2` (`prompt.md`, `given/`, `program.py`, `write.json`,
  `run.txt`) and `summary.json`.
- Tested without a model and without the robot (the simulation standing in, a stand-in for the CLI): a failing program,
  the rewrite's material and prompt, a succeeding program, the loop's end.
