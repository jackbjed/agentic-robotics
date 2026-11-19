# The robot interface

A skill program drives the robot through `ctx.robot`, the vendored **RealSim**
(`vendor/panda_twin/code/real/real_sim.py`, unchanged). It is the same class the real robot program uses. Here its two
backends are the world server's socket: an arm backend (`hello / state / execute / idle / wait_gripper / now`, the real
arm server's calls) and a camera backend (`get(cam)`). Every motion is planned and checked by RealSim, and checked
again by the server, before the arm moves.

## Frames and units

- **World frame:** the twin's frame, metres and radians. The table top is z = 0, and its centre is the origin. The robot
  (the left arm, `L_`) has its base at x ≈ −0.79 m (`rig.json` `base_world`, fitted). x grows away from the robot,
  across the table towards the scene camera; y is lateral; z points up. The work area of the clutter task is
  x ∈ [−0.46, −0.15], y ∈ [−0.15, 0.18].
- **TCP:** the hand's tool point (site `tcp`, 0.1808 m from the flange: `rig.json` `tool.tcp_z`). The **fingertip
  point** is 0.0157 m further along the hand's z axis (`T.FINGERTIP`, `rig.json` `tool.fingertip`). Motion targets are
  TCP positions. Fingertip = TCP + R[:, 2] · 0.0157.
- **Hand orientation:** a 3×3 rotation `R` (columns are the TCP axes in the world). `B.down(yaw)` points the hand
  straight down, with the fingers closing along the table direction `yaw`. `yaw` and `yaw + π` are the same grasp.
- **Gripper:** `grip` is the **per-finger** opening in metres. 0.04 (`B.OPEN_W`) is fully open (80 mm between the
  fingers); 0 closes with force (a grasp: 30 N, `rig.json` `gripper`). `finger_width()` returns the measured opening
  between the fingers (m).

## Calls (all in robot time; the twin's clock is its simulation time)

| call | what it does |
|---|---|
| `move(target_tcp, R, grip, T, phase)` | A straight TCP line to `target_tcp` with orientation `R`, IK every 1/30 s. A configuration flip becomes a joint-space move; otherwise it raises `Unreachable`. `T` is the requested duration (stretched to the limits). `phase` names the move; it decides the timing (below). Moves are queued, then sent as one trajectory when a later call needs the real state. |
| `move_joint(target, R, grip, T, phase)` | A smooth joint-space move to the IK solution nearest the present joints. |
| `hold(T)` | Waits `T` s; the arm holds. |
| `flush()` | Sends the queued moves and waits for them to finish. |
| `resync()` | Sets the command to where the arm is now (after a stop). |
| `observe_frame(cam)` | Once the arm is still: `dict(rgb, depth [m, inf = none], K = (W, H, fx, fy, cx, cy), R, eye, t, q)`. `R` and `eye` are the camera pose the program BELIEVES, its nominal mount: the true pose differs by the episode's calibration error. `cam` is `"d415"` (scene) or `"L_wrist"`. |
| `tcp_now()`, `finger_width()`, `force_now()` | Measured TCP position, finger opening, and the arm's own external-force estimate. In the twin this is the contact force on the hand; on the real arm it is the Franka estimate. |
| `ik(pos, R, q0)` | Inverse kinematics within the safety limits. A solution outside them reports a 1 m error. |
| `p_cmd, R_cmd, g_cmd, q_cmd` | The commanded TCP pose, grip and joints. |
| `q_home, lo, hi` | The home configuration and the joint limits in force. |

Errors: `Unreachable` (no such path within the limits), `RobotStop` (the server refused or stopped a trajectory:
lag, force, or the episode's time limit).

## Operating limits (constant across every comparison)

These come from the vendored `rig.json`, unchanged. Its `_doc` strings record where each value came from. They are
enforced twice, by RealSim before sending and by the server's `TwinArm` before executing:

| limit | value | provenance (rig.json) |
|---|---|---|
| joints | `safety.joint_lo/hi`, minus a 0.05 rad margin | the intersection of the Panda and FR3 specs. **Which arm it is (Panda vs FR3) is unconfirmed** |
| joint speed | 0.3 rad/s per joint | chosen after the 10-28 real runs (the arm trails fast goals) |
| TCP box | `safety.tcp_lo/hi` | chosen |
| keep-out | the parked right arm's box | chosen |
| table | fingertips ≥ −4 mm, links ≥ 5 cm over the table | chosen |
| force stop | 150 N in the twin (`force_abort_twin`); 35 N on the real server | chosen |
| tracking stop | joint lag over `control.track_abort` (+ the lag allowance) | fitted on the real arm (`lag_s`, 10-28) |

**Timing** (`real_sim` task timing, 10-30):
- **Free moves** (transits, lifts, looks; phases listed in `FREE_PHASES`) run at the twin's speed, or as a trapezoid
  at the joint speed limit with `a_max` = 0.6 rad/s².
- **Approaches and contacts** run at their planned duration.
- **Settling:** only the descents in `SETTLE_PHASES` settle.
- **Speed:** the twin runs RealSim at `speed = 1.0`. `rig.json`'s real-robot `speed` is 0.5, so real-robot times for
  the slow phases would be longer.

## What the program cannot do

It has no access to object poses, the target's location, the judge, or the simulator. It cannot change the limits:
they are re-checked in the server process, outside the program's sandbox. The real robot is never reachable from a
program. Its arm server listens on TCP 5590 on the host, and the program sandbox has no network.
