SESSION SUMMARY (session 3): cylinder onto the stick, specialised to the real rig. Result: v80 with world w25.

WHAT THE REAL EPISODES SHOWED (R5-R10, all v63)
- R5 (success, 272 s rig): blue hex cylinder on the table. Wasted 7 cylinder close-ups: on the black table
  nothing is seen through the hole, so v63 did not call the first (right) candidate "holed". Wrist camera
  error at the close-ups measured twice (R5, R7): seen - true = (2.9, 4.3) and (3.3, 4.8) mm, consistent.
- R6 (FAILED, operator intervened): blue hex on a cardboard box (40 mm). First close came back 0 (missed);
  on the way down the tool was 7 mm beside its guarded line. R7 (FAILED): green ring (H 20) on a box
  (65 mm): its second grasp's 'b' close (after the 90 deg turn) came back 0.1; v63 gave up. R8 (FAILED):
  red disc OD 60 H 21 on a book: 'a' pushed the arm 2.8 then 5.6 mm, 'b' came back 0.1; gave up. All the
  missed closes were on platforms, fingertips ~5 mm above them; none on the table.
- R9 (FAILED): stand on two boxes (stick top 295): the stick close-up pose (TCP z 542) is out of reach and
  v63 then looked from the wrong place -> "no stick". The arm also sank 18 mm during a look.
  R10 (FAILED, knocked over): cylinder on a 2x2x2 tower of toy blocks (top 90): its support was measured
  10-20 mm away from it, i.e. on the table, so the fingers went down to z 20 through the tower.

THE SKILL (v80) STEP BY STEP: survey with the wrist camera from high up (table plane, height image,
segmentation) -> close-up of the best cylinder candidate (camera 380 mm above its top: top patch, hole,
outer diameter, its support right around it) and of the stick's top -> grasp (on the table: two
centring closes 90 deg apart; on a box/book/tower: one close, carried to a free table spot, put down,
centred there) -> let go, look at it again from the same distance: seen - true = the camera's error,
applied to the stick's top -> re-grasp with one close -> carry high, bring the measured TCP over the
corrected stick top, guarded descent; on the stick's top: re-seat, small spiral search -> release once
14 mm on (it slides down), open, lift, home. Failures: re-look and retry (misses, a moved stand, a
wrong camera-error measurement).

WHAT CHANGED (v64-v80)
1. Support right around the cylinder (od/2+1..10 mm): a tower just wider than it is recognised; a seen
   height > 58 mm means its support was missed (then the grasp is 25 mm below its top, guarded).
2. Any cylinder on something (box, book, tower) is picked with ONE close (fingertips 5-7 mm above the
   platform instead of 3), carried to a free spot on the table (searched in the survey's height grid;
   none free: put back where it was) and centred there (a/b closes), so all centring and the camera-error
   look happen on the table, as in the one real success. Centring on a narrow tower pushed it off (sim).
3. A close that comes back empty: open, lift, look again, try the next closing angle (3 tries), instead
   of giving up.
4. Close-ups: when the pose right over a thing is refused, lower (-40/-70), nearer the arm's base
   (-60/-120 in x), or away from the parked arm (+x, +y); never look from a wrong place. The arm must
   stand still within 5 mm of its goal before a look (re-sent otherwise).
5. The first cylinder candidate with a plausible hole (15-75% of its diameter, segmentation >= 0.5) is
   taken even when the table is not seen through the hole (R5's 6 wasted close-ups).
6. Centring re-closes only for an arm push >= 3 mm (real pushes are mostly noise); the final re-grasp
   after the camera-error look is one close, its offset across the close taken from the arm's
   measurements; insert correction loop at most 4 moves.
7. v74: a second survey pose further forward (-280, 60, 560) when the first misses the stick or the
   cylinder (R7's stick stood at x = -70, at the first survey image's edge).
8. v75: at the free spot the first centring close is across the pick's closing line (the one close of
   the pick left the cylinder off the TCP that way; closing along the pick's line first let the turn
   sweep a finger into it).
9. v76-v80: when the insertion fails, the stick is looked at again with the cylinder held (a light
   board was pushed 20 mm by the touches in the simulation): if it moved > 5 mm, once more at its new
   place; a hand pushed > 3.5 mm aside on the stick's top skips the search; then the cylinder is put
   back, centred again and the camera error measured again before the last try. The free spot prefers
   no tall thing (> 60 mm, the stick included) within 110 mm (the hand was driven into a tall box).

WORLD w25 (the task as built): the real stand (board 270-290 x 80-92 x 19-23 mm, ONE stick d 9.2-10.2,
165-175 mm above the board, lean 0-1.2 deg, the stick anywhere x -470..-60, y -190..230) on the table or
(45%) on a platform 15-110 mm; the cylinder
(round or hex, OD 24-66, H 12-50, hole = stick + 1.5..6 mm, density 300-8000, friction 0.3-1) on the
table 30%, a narrow block tower 30% (od+8..30 square, 25-95 high), a book 15% (15-26), a box 25%
(26-100); a black mat of the real table's friction; the flat book + 0-4 distractors; wrist camera pose
error 1-5 mm / 0.3-2 deg; plain-move arrival errors; soft-finger width readings; TCP z >= 10; and the
real fingers' missed closes (0/15/30% per close, at most 2 per episode, the fingers close above it).
success(): cylinder axis within the hole's clearance of the stick at its bottom and top, bottom on the
stick between the board and the stick's top, stand upright (< 5 deg), relative tilt < 8 deg, robot not
touching cylinder or stand, gripper open and back home. A do-nothing program fails.

TESTS: v80 in w25 24/24 incl. the hardest earlier failures (s36, s40, s48; mean ~190 s robot time,
rig estimate 180-650 s, long only when a search and the retry are needed), in w21 (previous world) 8/8.
v75 in w25 15/16 (the failure: a short heavy cylinder (H 17.5) tipped when gripped low at the free spot
and slid 6 mm when let go, so the camera-error look was 8 mm off; fixed by the retry's new look);
v74 in w25 15/16 (incl. sticks at x = -66..-88 on platforms, top up to 263; the failure
fixed in v75); v73 in w25 16/16,
in w24 24/24 (mean 152 s robot time, rig estimate 160-300 s), in w21 (the previous world) 8/8; v72 8/8;
v71 15/16 (a stick near the parked arm -> fixed in v72); v63 in w22 (= w24 without misses) 6/8 (both
failures: towers). A do-nothing program fails in w25. Misses were recovered in every episode where the
world injected them.

EXPECTED ON THE REAL ROBOT: table cylinders as R5 but faster (one close-up, one fewer turn+close);
platform cylinders now take one extra carry (~25-30 s) but every centring close happens on the table;
high stands are looked at from a reachable pose. Rig time ~200-300 s.
UNCERTAIN: why the real fingers closed on nothing on platforms (soft tips on the platform? the tool
drifting beside its guarded line?) - the program now retries and avoids centring there, but a single
pick close on a platform can still miss; tower support detection on real depth (edge holes) is untested
(fallback: grasp 25 mm below the top); a tilted stand on stacked boxes (R9) is not handled.
MEASURE FIRST: the camera error at the look (printed "bias"), the misses ("CYL missed"), and whether the
free spot chosen on the real table is really free.
