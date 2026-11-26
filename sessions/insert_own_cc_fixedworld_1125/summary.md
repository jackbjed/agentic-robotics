SESSION SUMMARY - "put the cylinder on the stick" (session 2: after the real robot's first episodes)

RESULT: program v66 with world w16 (the earlier session's final world).

THE TASK AS BUILT (w16, unchanged in this session)
A ring-like cylinder (24 convex boxes; hole = stick + 1.5..5 mm, half the time < 2 mm; OD up to 68, height
12-55, density 500-8000, friction 0.2-1.2) and a stand (box or round base 60-140 mm, 6-25 thick, stick
5-24 mm thick, 20-90 mm longer than the ring, up to 160, flat or round top, off-centre 30%, density
300-12000), each on a platform 15-120 mm high 35% of the time, 3-8 distractors (boxes, spheres, cylinders,
lying capsules, tall thin boxes, solid ring-sized decoys, discs), table height -40..40 mm, ten colours and
patterns, random camera calibration errors (wrist 1-5 mm / 0.3-2 deg, scene 2-8 mm / 0.3-1.5 deg) and depth
noise. success(): the ring's axis within the clearance of the stick's at its bottom and top, its bottom
between the base and the stick's top, stand upright (<5 deg), the robot touching neither, the gripper open
(>70 mm) and back within 25 mm of its start pose. A program that does nothing fails.

WHAT THE REAL EPISODES SHOWED (R1-R4, all v38, all failed)
- Objects: a blue hexagonal ring (OD ~42, flats ~40, hole ~14, ~30 tall); a long narrow wooden board with
  three holes in a row and one 9.5 mm dowel (squeezed: 9.4-9.6 mm) in the end hole, its top at z ~190; a flat
  light-blue box. Black table at z = 0. The objects stood at x -260..-115 (further out than in w16).
- R1: the stick's top lay outside the survey image (stick at x ~ -137): its height was underestimated (145
  instead of 190), the close-up camera came within 275 mm of the top (no depth) -> "no stick found". The
  survey also proposed junk beyond the table's edge (rail, floor at z -700).
- R2, R3, R4: the descent round the stick was ONE move until contact from z 240 to ~46. On the real arm such a
  line runs at 5 mm/s and is cut every few mm without contact: 150-200 s of rig time, the tool 20-30 mm beside
  its line at times, and it answered "done" 11 mm SHORT of its goal. The operator intervened in R2.
- R3: the wrist camera's cable failed (rig fault).
- R4: after the stick was squeezed low (9.5 mm), the stick visibly leaned; the bias measured at its top was
  (0.6, 4.8) mm while first-look-minus-squeeze was (-7, -1): the stand/stick had moved or leaned. The ring's
  first close read 17.8 mm (a chord: the ring ~19 mm beside the fingers' line); v38 kept stepping along the
  push and the width fell to 0 (ring lost). The cause of that large offset is NOT understood (the close-up
  depth image of the ring is clean and centred; the camera's bias at the stick was small).
- Ordinary moves end 1-4 mm off their goal (measured), cost 2-4 s each; a 90-degree turn ~8 s.

WHAT CHANGED (v39-v63)
1. Survey from (-365, 60, 600) (sees ~120 mm further out; fallbacks if refused); points beyond the table's
   edges or far below it are ignored; a stick candidate at the image border is looked at again from 120 mm
   higher if the first close-up finds nothing.
2. No long moves until contact: every way down is an ordinary move to just above anything the fingers could
   meet (the survey's height grid under the finger regions), then a short guarded move (15-30 mm). A guarded
   move that answers done short of its goal without contact is sent again (guarded()).
3. A tall stick (top more than ~110 mm above the low grip) is centred low (a, b) and then near its top (a, b,
   22 mm below the top): the ring goes on where the top is, and the camera's bias is measured at the top. On
   a stick that stands straight this pushes nothing (sim: lean 0-1 mm).
4. Ring grasp: a first width below 0.6 x the seen OD is a chord: lift, look again where the camera saw it,
   retry; a second chord: try d to either side of the line. The fingers open fully (gently: w+2 first) before
   the 90-degree turn (half-open wide pads swept an off-line ring aside); if the two widths differ by >1.5 mm
   a third line (a again) is closed and the last two judged, with up to 16% difference allowed (hexagon:
   flats vs corners).
5. The insertion turns the fingers only if the current direction lacks clearance at the lowest point (a turn
   dropped a short ring); a drop during the turn is noticed. The insertion only counts if the ring got 8 mm
   below the stick's top. The xy loop over the stick accepts 0.5 mm after two tries (real moves are noisy).
6. After letting go: a close-up over the stick checks that the ring's top fills the pixels round the stick
   (height ratio 0.75-1.06 when on, ~0 when not); if not, ONE recovery: survey, find a ring of the measured
   size and height away from the stick, grasp, insert.
7. Travel height is kept 60 mm above the stick's top (a thin stick was missing from the survey's zmax: the
   fingers could have crossed the real stick's top); the measured stick is painted into the survey's height
   grid, so the finger directions chosen for the ring avoid it and no ordinary move goes down onto it; fewer
   moves (no duplicate moves; the fingers turn on the way).
8. A STATS line at the end of the log (top squeeze, lean, chords, searches, check ratio); images handed over
   through a guarded helper (at most 10, never fatal): survey, ring close-ups, re-looks, the final check.

TESTS (simulator)
- w16, normal: v41-v44 31/33 (the 2 failures were fixed: re-look at the ring where the camera saw it; next
  ring candidate after a failed insertion of a weakly-holed one); v47 8/8; v56 8/8; v58 8/8; v61 8/8;
  v62 16/16 (s85-s100); v63 24/24 (s1-s20, s101-s104); v64 8/8; v65 8/8; v66 16/16 (s29-s44). Since v56
  every normal w16 episode succeeded (96 of 96 episodes, ~80 distinct scenes). v38 had 40/41 on w16. Robot time
  ~100-180 s (mean ~125 s), up to ~280 s when an insertion is retried.
- Emulated real arm (RealLike wrapper inside the program, test versions v45, v57, v67: ordinary moves
  +-1-2.5 mm off, guarded moves up to 12 mm short and 3 mm beside): 24/24.
- Deliberate 18 mm error of every ring target (v49-v55): from 5/8 to 6/8 (the rest: OD 69 rings).
- Deliberate release beside the stick (v59, v60): the check saw it 8/8; the recovery succeeded 1/8 (the
  dropped ring merged with the stand's base or fell far) - it never made things worse.
- Reach and turns at x = -115..-135 (the real objects' places) work in the sim (probe v46).

WHAT TO EXPECT ON THE REAL ROBOT, AND WHAT TO WATCH
- Rig time should be much lower: guarded travel is now ~75 mm in total instead of ~190 mm in one line.
- The stick: low a-b squeeze (~35 mm above the board), then a top squeeze at z ~168. Watch whether the
  dowel stays where the top squeeze leaves it (if it falls back to its lean after opening, Txy is off by the
  lean; the log line "STICK top at ... lean ..." and the "closeup bias" tell).
- The ring grasp is the least understood step (R4). The log prints "ring: close-up minus grasp" (the camera's
  error at the ring) and shows the re-look images; please check those first.
- If the ring stops on the stick's top, a spiral search follows (each probe 2 ordinary moves + a short
  guarded one, ~5-8 s on the real arm).
- Measure first: the wrist camera's error at the ring (close-up minus grasp), the stick's lean after the top
  squeeze, and how often guarded moves end short.

UNCERTAIN
- Why R4's ring was ~19 mm off the fingers' line. The real stick's behaviour when pushed at its top. The sim
  cannot show the real arm's guarded-line behaviour, the stick's lean or the 30 N gripper.
