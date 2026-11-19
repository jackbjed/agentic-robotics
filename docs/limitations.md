# Known simulator and sensor limitations

From the inspection of panda_twin, panda_pick_rgbd and embodied-agents (2026-11-18). These hold for every result
here.

## Simulator

- **Collision models are not the scans.** Every collision model is a primitive or procedural mesh, sized from splat
  measurements and tape measures. The fin-ray fingers are modelled as 15 stacked boxes (the wedge model).
- **The arm is compliant:** the real kp/kd gains with 15 Hz goals, which is closer to the real arm than a stiff servo.
  But the twin has **no settle bias correction**: a move can end about 0.05 rad short, and the next trajectory can be
  refused. The twin's force stop is 150 N, against the real server's 35 N (the twin's contact forces spike).
- **Contact artifacts:**
  - the box–table contact is unstable, and the twin's box can launch itself (flagged as a TWIN ARTIFACT);
  - objects squeezed in clutter can jump;
  - the screwdriver can fall.
- **Timing** is the twin's: RealSim at speed 1.0, where rig.json's real-robot speed is 0.5. Robot time in this repo is
  therefore shorter than the same program would take on the real arm, especially in approaches and descents.
- **Arm identity:** Panda vs FR3 is unconfirmed. The joint limits are the intersection of both.

## Sensors

- **The depth model was fitted to real D415 frames** (10-27). The scene camera's lateral and yaw calibration errors
  were never checked against reality, and its 1–2 % scale error at 1–1.5 m is not modelled.
- **Colour** is a plain MuJoCo render (condition B). It is not photoreal; see docs/fidelity.md for what the splat
  composite (A) adds.
- **Segmentation.** Milestone 1 used an oracle (exact visible-pixel masks): its results are upper bounds on
  perception. Since milestone 2 the default is real SAM3 (our server, the robot's settings). It confuses objects
  (the orange octagon for red, overexposed yellow), returns partial masks, and depends on the words.
  - On a condition C wrist frame: "red can" → nothing; "red circle" → 0.95.
  - On a scene frame: "red can" → 0.17; "red object" → 0.93.
  - The initial skill prompts SAM3 with the task's name only, so it solves 0/90 condition C development episodes.
- **SAM3 time is not robot time.** In the twin, robot time pauses while SAM3 runs (0.3–0.5 s wall per call; recorded
  as perception wall time). On the robot the arm would wait.

## Platform

- **Load-dependent timing:** RealSim's `settle_wait` has a 1.5 s wall-clock timeout (vendored, unchanged), which can
  shift trajectories by a few frames under load.
- **Coding-agent isolation** is Claude Code's permission system, not a kernel sandbox (docs/isolation.md).
- **No physical validation.** Nothing here has run on the real arm.
