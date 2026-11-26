PUT THE CYLINDER ON THE STICK - session summary (result: program v39 with world w13)

TASK AS BUILT (world w13 = world_main.py with LEVEL 2, HARD, SLOPPY)
- Cylinder ("ring"): a tube standing on one flat end, built from 14-40 box segments (MuJoCo collides meshes as
  convex hulls, so a hole needs segments). It rests on 4 small hidden spheres 0.5 mm under its bottom face: the
  ~100 box contacts made rings creep and jump in MuJoCo while the arm moved elsewhere (a solver artefact).
  Stick diameter 5-22 mm; hole = stick + 1.5..4 mm (30% of episodes 1.5..2.2 mm); wall 5-24 mm; outer diameter
  <= 64 mm (the gripper opens 80); height 15-70 mm and <= 2 x outer diameter; mass 15-400 g (density 350-8000);
  friction 0.3-1.0 (MuJoCo takes the max with the table's 1.0, so ring/table friction is effectively 1).
- Stand: box or disk base 90-150 mm, 8-25 mm thick, mass 0.12-1.5 kg, one vertical stick (flat-topped cylinder or
  rounded capsule tip), length = ring height + 15..90 mm (>= 50 mm). Random colours and yaw.
- Ring and stand centres anywhere in x -490..-170, y -200..340 mm (up to the reach limits and next to the parked
  arm's keep-out box); each on the table or (45%) on a fixed box platform 20-120 mm high; table height -30..+40 mm
  (ring top kept >= 28 mm in the robot frame since the tool point may not go below z = 10); table colour random,
  25% patterned. 2-6 distractors (boxes, balls, solid cylinders), as close as ~45 mm from the ring's edge.
- Cameras: wrist pose error 3-6 mm / 1.0-1.8 deg (simulator default 3 mm / 1 deg), depth noise 0.15-0.3 px.
- The robot answers like the rig is said to: ordinary moves end off the commanded point (per-episode bias
  +-1.5 mm, a position-dependent part, 0.2 mm noise; state() reports where the tool is). Guarded moves exact.
- success(): on the TRUE state after the program ends: the stick's axis passes within (hole radius + 1 mm) of the
  ring's axis at the ring's bottom and top planes, the stick's top is at least half the ring height above the
  ring's bottom (threaded), stand tilt < 12 deg, no robot part touching the ring, ring at rest. A do-nothing
  program fails (checked); final images of successes show the ring on the stick, gripper open, arm home.

WHAT THE SKILL DOES (prog.py, v39)
1 Survey: wrist camera from TCP (-400,120,600) -> top-down heightmap (2 mm cells). Stick = narrow tall top-hat
  peak; ring candidates = round raised tops (ranked by roundness x height). If the stick or ring is not found:
  a closer two-view survey (TCP z 430).
2 Stick: camera centred 330 mm above its top; confirmed if radius < 15.5 mm, > 30 mm tall, no hole. If that look
  pose is refused (keep-out box/reach), the episode's reference hand orientation is turned 180 deg.
3 Stick closes near its base (fingertips 3 mm above the base): along A (reference closing direction) and, after a
  collision-checked 45-deg reorientation above the stick, along the 45-deg axis. Stall-aware: if the opening
  exceeds the diameter (one finger stalled on a heavy stand), shift by half the excess and close again; equal
  openings from two positions are accepted as the diameter. The stick's displacement between two camera looks
  (camera error cancels) gives the pushes -> stick rest position -> estimate of the wrist camera's 2-D error.
4 Ring: candidates looked at from 330 mm, chosen by a visible hole. Grasp yaw (0, +-30, +-60, 90 deg) chosen so the
  finger zones are free in the heightmap; next yaw if the approach is refused. Guarded descent beside it,
  fingertips 5 mm above the support, close (centres it along the closing axis), open, wait 1 s, look again from
  the reference orientation -> camera error along the closing axis (mean with the stick estimate if they differ
  by 1-4 mm, the stick's if more; the stick's if that look is impossible). Grasp it there.
5 Target: along the closing axis from the calibrated camera; across it from the difference stick-vs-ring as seen
  by the camera from the same relative pose (the camera error cancels; it turns with the hand, so a close can
  never calibrate that component). Hand orientation is checked from the camera's offset before every precise
  look; 180-deg wrist flips are undone in <= 60-deg steps.
6 Insert: closed-loop xy positioning above the stick (re-command with the measured error; the learned correction
  is reused), guarded descent; on contact a spiral search (0.6..2.5 mm, ~60 points, stops at 470 s). Then a
  guarded push toward the base, open, lift, home.

RESULTS (simulation; robot time of successes 105-230 s, mostly 110-160 s)
- w13 (everything above): v39 48/48 (s1-s36 subsets); v37 32/32 + 8/8.
- w12 (same, placement x -460..-200, y -170..300): v32 24/24; forced closer-survey fallback 4/4.
- w11 (no move error): v26 32/32. Normal world w6 (smaller errors/ranges, 1-4 distractors, no ring feet): v12 24/24.
- Insertion target error typically 0.0-0.6 mm; ~25% of episodes need 1-13 search steps, rarely ~50 (very short
  rings tilt a little in the grasp).
- Failures found and fixed along the way: rings tipping when pushed high (no 90-deg pushes on the ring), fingers
  stalling on heavy stands, camera on the other side after wrist flips, the arm's elbow sweeping objects during
  90-deg turns (turn() is not obstacle-checked; free moves are), fingers landing on neighbours, short rings lifted
  by the fingertips, rings dragged by a finger on release, look poses refused near the keep-out box.

WHERE IT CAN STILL FAIL
- Perception relies on depth: a stick without depth even at 330 mm, or a ring whose hole is not visible (then the
  best-looking candidate is taken; a solid cylinder of the same size could be picked).
- Very short (<20 mm) or very slender rings, pushed by the closes, tilt or shift; the search then takes long.
- Heavy stands pushed by the stick closes tilt and spring back (handled approximately; the ring calibration and
  the mean/stick rules cover most of it).
- The search covers +-2.5 mm only; a worse calibration ends in "could not thread" (ring released, arm home).

ON THE REAL ROBOT - EXPECTATIONS AND WHAT TO MEASURE FIRST
- Time: ~50-70 ordinary moves (+2-4 s settle each) -> estimate 250-450 s; long searches are cut at 470 s.
- Check first in output.txt: (1) depth on the actual stick ("stick cand", "stick it0 ... r=") at 330 mm and in the
  survey; (2) that the fingers close symmetrically about the reported TCP (the method relies on it); (3) with the
  real 30 N grasp, the stick close lines ("close along ... w= ok="): the stand should slide or the stall logic
  shift; (4) the TCP error after ordinary moves (closed-loop corrections); (5) "camera error along A: ring x,
  stick-based y" - the two should agree within ~1 mm.
- Only vertical guarded moves are used (the rig detects vertical force changes).
- Uncertain: real fingertip geometry (FINGER = 16 mm assumed; the rig says 14), how a real stand reacts to the
  closes, stereo edge bias on thin sticks vs wide rings, objects outside the survey's view, and how often the
  real arm flips its wrist.
