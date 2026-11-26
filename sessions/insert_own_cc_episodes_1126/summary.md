SUMMARY - "put the cylinder on the stick", session 2 (result: program v61 with world w18)

WHAT THE REAL EPISODES SHOWED (R1-R4, all v38, all failed)
- The real stand: a light-wood board ~280 x 85 x 20 mm with three holes on its centre line (~87 mm apart),
  a ~10 mm dowel in an end hole, its top ~190 mm above the table (~170 above the board). The table is black
  with its top at z ~ 0. The cylinder was a blue octagonal nut (~41 mm across, ~30 mm tall, ~14 mm hole).
  Distractors: a blue book, a green sponge.
- R1: from the old survey pose (-420, 60, 600) the stick was at the very top edge of the image. Its top was
  read as 145 instead of 190. The close-up was placed from that value and put the stick top at the depth
  camera's minimum range (273 mm), so no stick was found.
- R2/R3/R4: the long stop-on-contact descents (~190 mm, to squeeze the stick) took minutes of wall time on
  the rig (about 1.5 s per mm) and drifted up to 30 mm sideways. In R2 the operator stopped the episode.
  The program's own clock does not show this.
- R3: the wrist camera server failed ("no new frame"). That is a rig error; the program cannot recover.
- R4: closing on the 41 mm ring read 17.8 mm. The real fingers are soft fin-ray fingers: when gripping with
  the tips they read wide objects much narrower. v38's width-consistency check failed, and its "open to
  w+20" never let go, so the ring was dragged around until it escaped.
- Also seen: real moves end 1-6 mm from the command (the measured TCP is reliable), and looks taken right
  after a move may be stale.

THE WORLD (w18, the final one): the real table and stand, with what varies
- Table: black or dark grey, plain or speckled, top at z = 0. Wrist camera pose error 2-6 mm / 0.4-1.5 deg.
  Depth noise 0.12-0.25 px.
- Stand: wood board 270-290 x 78-90 x 18-24 mm, density 400-750, friction 0.3-0.6. Three holes are marked.
  Dowel 9.5-12 mm, standing 160-176 mm above the board, in an end hole (80%) or the middle hole, leaning up
  to 0.8 deg. In 35% of episodes the stand is on a box platform 10-80 mm high. Random yaw. The stick is at
  x -380..-140, y -170..170.
- Cylinder: round, OD up to 60 mm, height 15-50 mm. Hole = stick + 1.5..6 mm (half the time below +2.5).
  Density 300-3000, friction 0.3-0.9, any colour except black. On a 10-80 mm platform in 35% of episodes.
  15-60 mm from the board or its platform. Ring and board use their own friction against the table (geom
  priority; otherwise MuJoCo takes the table's ~1).
- 2-6 distractors: books, sponges, mugs, boxes, tall thin boxes, spheres, solid ring-sized cylinders, discs,
  pens.
- The robot as the real one behaves:
  - every move ends off target by a smooth 0.5-3.5 mm field plus 0.2-0.7 mm of noise;
  - stop-on-contact moves cost 0.15 s per mm and drift sideways;
  - opening readings of wide things are 0-26 mm narrower (soft fingers);
  - a look taken under 0.3 s after a move sees the scene shifted 1-5 mm.
- success(): the cylinder's hole axis lies within the clearance of the stick's axis at both its ends, its
  bottom is between the board and the top, the stand is tilted under 5 deg, the cylinder under 8 deg to the
  stick, the robot touches neither, and the arm ends at its start pose with the gripper open. An idle
  program fails (0/4). The final images of successes show the cylinder down on the dowel.

THE SKILL (v61), STEP BY STEP
1. Survey from (-340, 45, 615), further forward than before, so a tall stick anywhere in reach is fully in
   view. Every look first waits 0.5 s. From the survey: a table-plane fit, stick candidates (thin, much
   higher than around them, on a plate) and ring candidates (SAM "ring" / "cylinder with a hole" plus compact
   objects).
2. Close-ups with the camera 330 mm above the top of the object (clear of the 275 mm minimum range). If the
   arm refuses the pose, shifted or higher poses are tried. The ring is checked as a round top with a deeper
   hole, the stick as a thin, tall patch on a plate.
3. The ring is centred between the fingers along two perpendicular lines (a, b):
   - the descents are ordinary moves (no long contact moves);
   - if the compliant arm was pushed while holding, it is first commanded to where it is (so it does not
     spring back and flick the ring), then the fingers close again;
   - the open fingers are lifted over anything they would sweep before the 90-degree turn;
   - the ring is never gripped lower than this table allows.
   If no two finger lines are free (the ring stands close to something), it is picked up along one free line
   and set down in the clearest place 65-85 mm away first.
4. Calibration: the ring is released and looked at again, with the camera ahead of the gripper. Seen
   position minus the measured TCP is the wrist camera's error. The stick is then looked at from exactly the
   same camera-to-target offset (same ray, so the same error) and corrected by it. The stick is never
   squeezed: the light board would slide or tip. In simulation the corrected stick position is typically
   0.5-2 mm from the truth.
5. The ring is taken again with the free closing line. A lift checks that it is held (an opening reading
   only means "something is held").
6. Insertion:
   - the ring is carried high and brought over the stick in closed loop on the measured TCP;
   - an ordinary move takes it to 5 mm above the stick top;
   - one short stop-on-contact move (~17 mm) follows;
   - if it lands on the top, a 0.9 mm hexagonal probe search (radius 3.3 mm, nearest first) runs. A probe
     counts only if it drops 4.5 mm, and the ring is let go only when its bottom is 6 mm below the stick top.
   If the search fails, the ring is put back and one more attempt is made if time allows.
7. Open, lift clear, go back to the start pose.

TESTS (simulation)
- v38, the version that ran on the real robot, fails 0/8 in the real-like world. The failures match the
  real ones: no stick found, and the grasp check fails with soft fingers.
- The last lines of development, in w18 (from 16 to 54 scenes each):
  - v47: 24/24;
  - v56/v57: 23/24 each;
  - v60: 30/32;
  - v61 (final): 14/16, including scenes that earlier versions failed.
- Robot time per success is 110-200 s, mean ~145 s; a search adds 30-150 s.
- The robustness checks covered stand and cylinder on platforms, small, large and tall rings, holes 1.5 mm
  wider than the stick, the ring next to the stand or a distractor, close-up poses refused near the parked
  arm or at the edge of reach, a flipped wrist (camera on the other side), and soft-finger readings.

WHERE IT STILL FAILS (simulation)
- Clutter: a small ring with a solid object ~30 mm away, so no finger line is free even after relocating
  (s16).
- Tight holes (1.5-2 mm play): the search probes inherit the arm's ~0.5 mm move noise and can miss. With
  over 300 s used, no second attempt is made (s39).
- A cylinder that misses the stick by more than its radius (only after a gross stick error) would be
  released beside it. Nothing checks for this.

EXPECTED ON THE REAL ROBOT, AND WHAT TO MEASURE FIRST
- The R1 failure (stick out of view) and the R4 failure (narrow readings, drag) are addressed. Only one short
  contact move is made, plus probes if needed, so episodes should take about 3-5 minutes of rig time rather
  than being stopped.
- The blue nut has ~4 mm of play on the dowel, so it should usually go on without a search.
- To check first, in this order:
  - the survey pose (-340, 45, 615) is reachable on the real arm (a fallback to the old pose is built in);
  - the stick is found in the survey image;
  - the printed "camera bias (ring seen - centred)" is consistent (a few mm);
  - the ring is not moved when the fingers open for the calibration look;
  - the stop-on-contact insertion lands within ~2 mm (look at "insert contact" and how many probes were
    needed);
  - wall time per probe.

UNCERTAIN
- How the real fin-ray fingers centre and release the ring.
- How much the real arm scatters on small moves.
- Whether the real camera's error is the same at the ring and at the stick.
- How real stop-on-contact moves behave when the ring lands on the dowel top.
- Rings that are dark, transparent or very short were not modelled.
