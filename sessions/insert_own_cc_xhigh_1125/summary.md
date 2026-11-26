PUT THE CYLINDER ON THE STICK - session summary (program v38, world w16)

THE TASK AS BUILT (world w16, the final one; earlier worlds were easier steps toward it)
- Table: top at -40..+40 mm (30% exactly 0), ten colours, plain or wood/stripes/speckle/checker/blotches.
- The cylinder ("ring"): a thick tube standing on one end, built of 24 boxes (a 24-gon; MuJoCo collides only
  convex shapes), plus 8 full-height boxes inside the wall that carry it (a sim fix: 96 coplanar contacts made
  the ring creep and jump). Hole = stick diameter + 1.5..5 mm (half of the episodes 1.5..2 mm, i.e. 0.75..1 mm
  per side). Outer diameter 22..68 mm, height 12..55 mm, density 500..8000 kg/m3, friction 0.2..1.2.
- The stand: square or round base 60..140 mm wide, 6..25 mm thick; one vertical stick 5..24 mm thick,
  20..90 mm longer than the ring (at most 160 mm), flat (60%) or rounded top, off the base's centre in 30%
  (up to 22% of the base). Base and stick densities 300..12000 kg/m3 (very light to immovable), friction
  0.2..1.2.
- Heights: the ring and the stand each stand on a fixed block 15..120 mm high in 35% of episodes (forced when
  the table is low, so that everything stays reachable).
- Clutter: 3..8 distractors - boxes, spheres, solid cylinders, lying rods, tall thin boxes (15..26 mm square,
  50..120 mm tall: stick look-alikes), solid ring-sized decoys (no hole), flat discs; 12% of all colours dark.
  Footprints only 5 mm (ring/stand) and 8 mm (distractors) apart. Ring and stand are never black (the
  simulated depth camera loses black things entirely - see limits).
- Cameras: random per episode - depth noise 0.12..0.3 px, scene camera pose error 2..8 mm / 0.3..1.5 deg,
  wrist camera 1..5 mm / 0.3..2 deg (up to ~12 mm of error at the close-up distance).
- Task text given to the program: put the cylinder (standing on one end) on the stick, let go, stand upright,
  open the gripper, return to the initial pose; hole as little as 1.5 mm wider than the stick; things may
  stand on something; other objects on the table.
- success() on the TRUE state: the ring's axis (at its bottom and top) within the hole clearance (+0.5 mm) of
  the stick's axis, its bottom between the base top and (stick length - ring height + 1 mm) along the stick
  (i.e. the stick passes through the whole hole), ring tilt relative to the stick < 8 deg, stand tilt < 5 deg,
  no robot part touching ring or stand, and the last measured TCP within 25 mm of the start pose, pointing
  down, gripper open > 70 mm. A program that does nothing fails (checked); final images of successes show the
  ring down on the stick.

WHAT THE SKILL DOES (skill.py v38, entry run(); the functions named unused_* are dead earlier code)
1. Survey: wrist camera looks down from TCP (-420, 60, 600). Table plane fit on the depth; height image;
   morphological opening (44 mm disc) residual -> stick candidates (much higher than a ring 18..32 mm around
   them, top patch 7..36 mm); SAM ("ring", "cylinder with a hole", "washer") masks and compact objects ->
   ring candidates; a 3 mm height grid of everything (for finger clearance).
2. Ring: close-up from ~320 mm (always with the camera on the same side - its calibration error turns with
   the gripper). Top patch -> centre, diameter, support height; hole = depth deeper than the top at the centre.
   Candidates ranked by hole evidence + survey score.
3. Stick: close-ups of candidates; prefer thin, high patches standing on a plate. Then the stick is squeezed
   low (base + 25 mm) three times, a/b/a 90 deg apart: a free stand slides to the middle of the fingers; an
   immovable one pushes the compliant arm aside and reads too wide, so the arm steps by the push (or by half
   the width excess) and closes again until centred. The TCP is then the stick's position (~0.3 mm). It must
   read round (widths within 1.5 mm) and fit the hole seen, else the next candidate.
4. A second close-up of the squeezed stick gives the camera's calibration error (up to 13 mm seen); the
   ring's position is corrected by it. Ring grasp: fingers 6..8 mm above its support, closing direction chosen
   free of neighbours, centred along a then b; widths must agree with each other and the seen diameter, else
   re-look / next candidate.
5. Insert: carry above everything, turn the fingers to a free direction, bring the MEASURED TCP over the stick
   (a heavy ring makes the arm sag ~1.5 mm), lower with stop-on-contact; if it stops on the stick top, a small
   spiral search (<= 2.8 mm); hold until 15 mm on the stick, loosen gently, let it slide down. If the search
   fails: put the ring back, squeeze the stick again, retry once.
6. Open, lift clear, straight to the start pose, home(). Any exception: open, lift, home.
   Moves between places go up-across-down. Refused poses (wrist flips, the parked arm's keep-out box) are
   retried with other closing angles or higher close-up poses. Typical robot time 90..170 s (max seen ~250 s).

RESULTS (practice episodes, success() on the true state)
- w16/w15 (final stress world): v34..v38 together 118 of 120; both failures are the same scene (a 24 mm
  stick rejected as too wide in close-up, v36 and v37), fixed in v38. v38 alone: 40/40 (24 of them on seeds
  never used during development); robot time 89..227 s, typically ~125 s.
- Earlier worlds: w7 (camera errors, platforms to 120 mm) v21 32/32; w11 (thick sticks, short rings) v23 32/32;
  w6 (clutter, no camera errors) v14/v15 40/41; plain w4 v7 14/16.
- By variation, everything above passed: thin 5 mm and thick 24 mm sticks, 1.5 mm clearance, rings 12..55 mm
  tall, rings and stands on 15..120 mm blocks, table at -40..+40, immovable and very light stands, frictions
  0.2..1.2, look-alike decoys (tall thin boxes, solid cylinders of the ring's size), 5 mm gaps, wrist camera
  errors to ~12 mm, off-centre sticks, rounded tops.

WHERE IT STILL FAILS / LIMITS
- Black (or IR-absorbing) ring or stand: the simulated D415 returns no depth on them and the skill finds
  nothing (no RGB-only fallback). Excluded from the world on purpose; check the real objects' depth first.
- Cylinder lying on its side: not handled (the gripper can only be near-vertical near the table; standing it
  up would need a regrasp strategy). The task text says it stands on an end.
- Outer diameter above ~70 mm (80 mm gripper) or rings shorter than ~12 mm: not tested.
- Very cluttered spots where no closing direction is free; objects in the parked arm's keep-out zone
  (x < -420 with y < -280) cannot be looked at closely.
- Simulation artefacts seen: a jammed ring + light stand can "explode" on release (rare; v31+ releases
  earlier). Fingertip touches on rigid surfaces did not stop at 4 N (reached 70 N), so the skill never
  touches the table on purpose.

ON THE REAL ROBOT: WHAT TO MEASURE FIRST
1. Squeeze centring on a fixed peg at known offsets (0, 3, 6 mm): TCP shift and width reading vs. offset -
   the sim arm yields ~2-3 mm at ~20 N; the real Franka's impedance decides the iteration (step 1.8 x push).
   Make sure these squeezes do not trigger reflex errors.
2. The wrist camera's calibration error (close-up vs. squeezed stick), and how depth looks on the real ring,
   stick (thin, maybe shiny) and table: the hole test needs depth inside the hole or at least a visible step.
3. Sag of the arm with the ring held (TCP measured vs. commanded) - corrected in closed loop, but check it.
4. Contact detection when the ring lands on the stick top (force spike at 4 cm/s), and that a 0.75 mm
   clearance ring slides down the real stick when released 15 mm onto it (friction, chamfers).
5. SAM's behaviour on real images (it is only a hint; depth geometry decides).
Uncertain: real stereo depth on dark/shiny parts; real grip of short rings (pads contact only the lower
6..30 mm); whether real stands tip when squeezed (squeezed low, steps of a few mm).
