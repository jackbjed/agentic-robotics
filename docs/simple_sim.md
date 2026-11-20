# simple_sim: training in a simple, inaccurate simulator

2026-11-20, user: "we should train an agent in a much simpler environment, eg called simple_sim; it should reflect what
happens when we train in a simple and inaccurate sim; it should have the table 5 cm lower, no photorealism, table brown
instead of the real color, as objects just default red, green and blue cubes and red and yellow cylinders (basic ones,
unrelated to the real ones), and for box basic mujoco box unrelated to the real one."

The question: what do skills learned in a simple simulator that is wrong about the world give in the accurate twin
(and, later, on the real robot)? It is the far end of the fidelity conditions (docs/fidelity.md: A splat colour, B plain
colour, C other objects with the same affordances).

## What differs from the accurate twin

| | accurate twin (condition B, `tasks_B_rig_home.yaml`) | simple_sim (`tasks_simple.yaml`) |
|---|---|---|
| table | top at z = 0, dark grey | top at z = −0.05 (5 cm lower), brown |
| objects | the scanned real ones: tape rolls, cable, screwdriver, block, glue, pad | MuJoCo primitives in pure colours: red, green, blue cubes (40 mm); red, yellow cylinders (Ø40 × 50 mm); ~45 g |
| box | the white box, 191 × 193 × 58 mm, 3 mm walls, lid with a flap | a plain grey box, 220 × 170 × 70 mm, 4 mm walls, a flat lid (the same hinge) |
| clutter | the ruler on the table | no ruler |
| colour | plain MuJoCo rendering | plain MuJoCo rendering |
| depth | the D415 model fitted to real frames (noise, holes, edges, flying pixels) | exact (preset `ideal`) |
| camera poses | the real rig's calibrated poses + a per-episode calibration error | the same poses, no error |
| robot, cameras' mounts, lights, arm start (home), time limits, judges | | the same |

Built by `scripts/build_simple_sim.py` from the vendored `twin_6522_wedge.xml` →
`src/agentic_robotics/sim/models/simple_sim.xml`. The body names stay the twin's (the vendored world, task_box.BoxSim,
needs them); the names the program is told are `sim/scene.py` `PHRASES_SIMPLE`:

| body | simple_sim object |
|---|---|
| `tape_red` | red cylinder |
| `tape_yellow` | yellow cylinder |
| `tape_white` | red cube |
| `hole_block` | blue cube |
| `sticky_pad` | green cube |
| `box_white` | grey box |

The other bodies (glue, cable, screwdriver, brown box) are never placed. Practice records (`result.json`) give the
told names, not the body names.

## The robot's limits on the lower table

- **The world's arm** (TwinArm and the server's planner) keeps the fingertips ≥ 4 mm over simple_sim's table
  (`rig.json` safety `table_z` = −0.05 there, the TCP box's floor at −0.10; `sim/picktwin.py` `world_rig`).
- **The program's planner** (`ctx.robot`, RealSim in the sandbox) gets a floor at −0.10 instead (`hello` → `planner`;
  `robot/client.py`). A program can read that floor from `ctx.robot`, but it is not the table's height.
- **In the accurate twin** both are the rig's own (table at z = 0). A move that simple_sim allows below z = 0 is
  refused there as `Unreachable`, as on the real robot.
- The layout checks (the clutter's free-grasp count, "fell off the table") use simple_sim's table.

## What the agent is told

User (11-20): "it shouldn't know that the table is at z=-0.05; it should only be told that the sim is approximate and
that it may be tested on different table heights and different objects and different visuals."

- **The brief** (AGENTS.md) says the robot is "simulated in a simple MuJoCo simulation of the setup (approximate)".
  Its task paragraph, its evaluation paragraph and the session prompt all say: "The simulator is approximate: your
  skills may be tested with a different table height, different objects and different visuals."
- **Its docs** (`learning/loop.py` `simple_doc`):
  - `robot.md`: "x = y = 0 is the table's centre; the table top's height is not given" (not "the table top is z = 0");
    the cameras' poses are exact.
  - `perception.md`: ideal depth, no calibration error; examples name simple objects.
  - `tasks.md` = `docs/task_simple.md`: the three tasks with simple_sim's objects and box.
- **Never named to it:** the evaluation's objects, the table's height in either world.

## Tasks, layouts and the run

- **Families:** place-box, close-box, clutter-pick-any (press needs the kettle, which a simple simulator would not
  have). Their banks: `data/<banks>_simple/` (`ar layouts --task T --split S --object-set simple`; seeds +100000 vs
  the twin's): train 300 / 300 / 291, val 60 / 60 / 57, test 100 / 100 / 97.
- **The experiment** `configs/experiments/simple_sim.yaml`: as `evalset_B_ctrl_r1` except the world.
  - Skills from an empty start (`initial_skills/empty_three`), no skill library, no model calls.
  - No evaluation-set access; 3 resumed sessions of 300 practice episodes; the same dev rule (simple_sim's val
    layouts).
- **Scoring:**
  - `simple_test`: simple_sim's held-out layouts (test split, 25 per family; not certified);
  - `evalset`, `evalhidden`: the accurate twin's certified sets (docs/evalsets.md; 25 per family; each suite names
    its own task config, `tasks_B_rig_home.yaml`).
  - Compared with `evalset_B_ctrl_r1` (trained in the accurate twin) on the same three families.

## Checks (tests/test_simple_sim.py)

- **The world:** the table top at −0.05; everything rests on it.
- **The told names:** "Put the … into the grey box.", "Close the lid of the grey box."
- **The limits:**
  - the world's arm refuses fingertips under the table;
  - the program's floor is −0.10;
  - the twin is unchanged.
- **A scripted pick and place** (true poses, the robot's own motion path) succeeds on the lower table.
- **The lid judge** on the grey box.
- **No press** in simple_sim.
- **The docs** say nothing of the table's height, the tape rolls or the white box.

A probe (2026-11-20) sent a program's fingertips to z = −0.03:
- reached in simple_sim;
- `Unreachable` in the twin.

`evalset_B_ctrl_r1`'s skills (trained in the twin) scored 0/36 on simple_sim's dev layouts:
- its clutter pick rejected the objects it found, whose tops sat at the twin's table level;
- its close-box looked for a "white box".
