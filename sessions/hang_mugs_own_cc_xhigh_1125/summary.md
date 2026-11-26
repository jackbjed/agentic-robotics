HANG ALL THE MUGS ON THE MUG RACK - session summary (final: program v38, world w8)

THE TASK AS BUILT (world w8, file world.py, LEVEL 1)
- Table: height -40..+40 mm, 10 colours, 35% patterned (checker/stripes/speckle/wood/blotches).
- Mug tree (free body): disc or square base, radius 85-115 mm, 12-25 mm thick, 1.0-2.0 kg; pole radius 8-15 mm;
  1 level of 3-4 branches or 2 levels of 2-3 (levels 55-85 mm apart, offset in azimuth), branch roots
  150-210 mm above the base, length 90-140 mm, elevation 15-40 deg, radius 4-7.5 mm, 50% with a tip knob
  (smaller than the mugs' handle holes); pole 30-90 mm above the top level; random yaw; friction 0.3-0.8;
  30% stand on a 15-50 mm platform. Placed x -400..-240, y -100..300 (robot frame, mm).
- Two mugs, upright, random yaw: outer radius 36-46 mm, height 80-115, wall 3-6 mm, mass 220-420 g;
  handle = tube radius 4-6.5 mm on a superellipse loop (exponent 2-3.5), hole 20-32 mm wide and >= 36 mm tall,
  top 12-25 mm below the rim. Mug-table friction 0.35-0.75 (bottom geom priority); walls/handle use MuJoCo's
  max rule (rubber pads 1.5). Free-joint damping 1e-3 so a hanging mug settles in ~1-2 s. 25% on a 15-50 mm
  platform. 0-3 distractors (box, can, ball, a handle-less cup), 11 mug colours, 7 rack colours.
- success(): for each mug, on the TRUE state: some rack branch segment (root->tip) passes through the handle
  loop (polygon of the handle centreline closed at the wall, in the mug frame); the mug touches nothing but the
  rack or the other mug, is not touched by the robot, speed < 30 mm/s; rack tilt < 15 deg. ok = all mugs hang.
  Reports "hanging" (count) and per-mug details. A program that does nothing fails (checked).
  Not checked by success(): the arm's final pose (the robot's state is not in the judged state); the program
  always opens the gripper and calls home() at the end (with a move-clear fallback), as the film shows.

WHAT THE SKILL DOES (skill.py)
1 Overview: two top-down wrist views (TCP z 600) -> table height, height map, blobs. Rack = tallest blob.
  Mugs: rim circles (RANSAC, several per blob), support level, hollow test, handle = radially contiguous point
  cluster in the upper half of the mug, beyond the wall's depth band. Unsure hollow round things are inspected
  from closer. Rack: axis from the intersection of branch lines, branches by azimuth histogram + RANSAC,
  modelled as xy line + top-surface height line; near-duplicates merged.
2 Plan: for each (branch, side of branch, position along branch) a hang pose that puts the handle hole on the
  branch line, the grasp on the rim 90 deg from the handle (far side from the pole). Gripper + mug samples are
  collision-checked against rack capsules and a keep-out around an already hung mug; reach heuristics; cost.
3 Per mug (round robin, best plan first): hang hover (wrist camera above where the mug will hang, with the hang
  wrist angle) -> refine the branch; grasp hover (camera above the mug axis, grasp wrist angle) -> refine the mug.
  The two views have the same camera pose relative to the gripper, so the wrist camera's calibration error
  (up to ~15 mm at overview range) cancels between "where the handle is" and "where the branch is".
4 Rim pinch 48 mm above the mug bottom (the mug slides rather than tips when centred), lift (lower + inward
  fallback, obstacle aware), weigh, turn, transport (collision-checked, detours), descend beside the tip.
  All moves while holding are closed-loop on the measured TCP (the arm sags ~10 mm under the load).
5 Thread: slide along the branch in 4 mm free steps, a step-to-step force jump = contact (stop_on_contact counts
  the load); on contact retreat and retry with lateral/vertical offsets (8 tries).
6 Lower in 1.5 mm steps until the rack carries > 55% of the mug's weight (not too early: that would be the
  handle's lower bar or something under the mug), only then open, rise, next mug. Finally open, home.

RESULTS (simulation, robot time per success 140-540 s, mean ~260 s)
- Final world w8, v36/v38: 12/16 on unseen seeds (s45-s60). Earlier full world w7 (same, lighter rack):
  v27 7/8 unseen; typical 6/8. Plain world (w4): ~6/8 at v8 (older version).
- Per mug, when the mug is found and the plan reachable, threading + hanging works in >90% of attempts.
FAILURE MODES SEEN
- Mug next to a distractor or a platform edge: handle not recognised (mug skipped) or confused (~1 in 10).
- Tall mug on a platform near the far corners: hover / transport poses out of reach.
- After a hard contact a mug can turn in the grip; retries then fail; rarely a mug is dropped or knocked off
  the table while being put back. Several retries can use most of the 900 s (guard at 620 s).
EXPECTATIONS FOR THE REAL ROBOT / WHAT TO MEASURE FIRST
- Wrist camera: depth noise and blind band as simulated? Hand-eye error: check that the matched-pose trick works
  (hover over a known point at two wrist angles). Minimum depth 275 mm sets the hover heights.
- Force estimate with a held mug: noise and drift with pose (the contact and support tests use 3 N jumps and
  >55% of the measured weight); the payload sag the closed-loop moves correct.
- Pinch: real Franka grasp force on a 3-6 mm ceramic wall, slip and tilt in the grip; real pad friction.
- Real handles: shape (D / ear), hole size vs branch + knob size; glaze reflections may lose depth on rims.
- Real racks: wooden branches may have curved / upturned tips (not modelled), lighter racks may tip.
UNCERTAIN
- Friction ceramic-wood (sliding of the hung mug toward the pole), how fast a hung mug settles, dark/glossy mugs
  (depth holes), and racks other than a central-post tree (bar racks, wall hooks) which were not modelled.
