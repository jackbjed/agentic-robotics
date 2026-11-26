SESSION SUMMARY - put the cylinder on the stick (real-rig specialisation). Result: v63 with world w21.

WHAT THE REAL EPISODES SHOWED (R1-R4, all v38, all FAILED)
- R1: the survey pose (-420,60,600) did not see the stick (stand at x ~ -150: its top was outside the wrist
  image) -> "no stick found".
- R2, R3, R4: the stand is a ~280 x 87 x 21 mm wooden board with 3 holes and ONE ~9.5 mm stick whose top is at
  z ~191 (170 mm above the board). v38 squeezed the stick low (TCP ~46): the hand's palm lands on the stick top
  at TCP ~56. Those descents were long guarded lines, which the arm's server cuts and re-sends (~1-1.5 s of
  rig time per mm, the tool drifting 5-30 mm beside its line). In R2 the operator stopped the arm. R3 ended
  when the wrist camera's cable failed.
- R4: the fingers are soft Fin Ray fingers. Closing on the 41 mm ring read 17.8 mm. v38 re-opened to
  "reading + 20" = 37.8 mm (narrower than the ring), so its centring loop dragged the ring until it was
  knocked off the table. The real arm also ends plain moves 0.5-3 mm off the commanded pose; the measured
  TCP is what counts.
- From the real depth: ring OD ~41.5 (area-equivalent), height ~30.5, hole ~14. Close-up heights matched
  the table (z = 0) to about 1 mm. The D415 gave no depth on the stick top from 273 mm away.

WHAT CHANGED IN THE SKILL (v41 -> v63; skill2.py in the workspace)
1. Survey from TCP (-350,60,620): it covers the stick top up to x ~ -80. If nothing usable is found, the
   program surveys again from (-420,60,600) and (-300,-150,560).
2. Stick: found as a thin top far (>=90 mm) above its surroundings. It is never touched or squeezed now.
   Its top is measured by a close-up with the camera 380 mm above the top, straight over it, the hand turned
   the same way every time (the camera's error turns with it).
3. Cylinder: candidates come from SAM ("ring", "cylinder with a hole"), compact objects, and local rises
   (cylinders on platforms); out-of-reach, flat and stick-like candidates are skipped. A close-up from 380 mm
   confirms a hole: the hole must be deeper than the top AND show the support through it (a dark sphere's
   missing depth is not a hole). Candidates with a seen hole always rank first.
4. Camera calibration is measured on the cylinder itself. It is grasped low and centred: one close, a gentle
   two-step open, a 90-degree turn, a second close. Opening widths come from the vision OD, never from the
   finger reading. Its true position is then the TCP. It is let go there and looked at again from the same
   380 mm: seen minus true is the camera's xy error, applied to the stick top. The table seen around the
   cylinder corrects the stick top's height. If that look is impossible (next to the parked arm), the
   cylinder is carried to a free spot first.
5. Re-grasp (plain moves, its position is known), hold check after a 15 mm lift, carry high over the stick,
   close the loop on the measured TCP (<= 0.5 mm, up to 6 moves), then ONE short guarded descent of about
   20 mm, ending 14 mm onto the stick. If it sits on the top: re-seat at the same place, then a small spiral
   search (0.8-3.2 mm). If that fails: put it back and retry once. Release, lift, back to the start pose.
6. Guarded (stop-on-contact) moves are short everywhere: about 13 mm while the fingers pass the cylinder's
   top, 20 mm for the insertion, 9 mm per search probe. All other moves are plain.
7. Near the parked arm: other hand turns, and if all are refused, home and try again.

THE WORLD (w21, "real table")
- Black table at z = 0 +-1 mm, covered by a thin black mat with friction 0.35-0.6. The sim table's friction
  is 1, and MuJoCo uses the larger of two frictions, so light cylinders gripped it like rubber and tipped.
- The real stand: board 270-290 x 80-92 x 19-23 mm (density 450-800), two empty hole marks, ONE stick
  9.2-10.2 mm thick, 165-175 mm above the board, leaning 0-1.2 deg, in the hole 50 mm from one end.
- Cylinders: round or hexagonal, OD 24-66, height 12-50, hole = stick + 1.5..6 mm (40% <= 2.5), any
  non-black colour, density 300-8000, friction 0.3-1.0. They stand on 8 feet at their outer edge.
- Platforms (30% each, 15-100 mm) under the cylinder and/or the stand. A flat book (85%) plus 0-4
  distractors: boxes, spheres, solid cylinders the size of the ring, discs, lying capsules, tall boxes.
- Cameras: wrist pose error 1-5 mm / 0.3-2 deg, scene 2-8 mm / 0.3-1.5 deg, depth noise 0.12-0.3 px.
- The robot as the real one answers: plain moves end ~0.5-1.4 mm off (an episode offset plus random); the
  gripper reads narrower than what it holds (Fin Ray-like, up to 60% of w-12); TCP z >= 10 is refused; a
  rig-time estimate is reported.
- success(): the cylinder's hole axis (bottom and top) within the clearance of the true stick axis, its
  bottom on the stick, stand tilt < 5 deg, relative tilt < 8 deg, the robot touching neither, the robot back
  at its start pose with the gripper open. An idle program FAILS; the final images show the cylinder on
  the stick.

RESULTS (simulation)
- v63 in w21: 24/24 (16 new scenes plus 8 that had failed in earlier versions: tiny light cylinders,
  cylinders on platforms, next to the parked arm, among dark distractors). v60-v63 together: 55/56 in w21. The one
  failure is s86 (a 50 mm tall cylinder on a platform that tilts in the grip). Robot time ~110-160 s;
  rig-time estimate ~150-200 s, up to ~400 s when a relocation or a search is needed.
- After the bias correction the stick top is usually within 0.5 mm of the truth. Without it the error was
  up to 15 mm.

WHAT I EXPECT ON THE REAL ROBOT, AND WHAT TO WATCH
- The real ring (41 mm hex, 14 mm hole on a ~9.5 mm stick: ~4 mm clearance) is the easy case for this
  skill, and the palm never goes near the stick top any more. Expect roughly 25 guarded mm, so far less
  rig time than R2-R4.
- Check first: (a) the survey pose (-350,60,620) is accepted by the real arm (there is a fallback);
  (b) the printed "bias" (the camera's xy error) is a few mm and the "STICK closeup" eqd is ~12-16;
  (c) the Fin Ray fingers centre the ring: after "CYL widths" the ring should be under the TCP; the
  re-opening widths are now from the vision OD (~66 mm for the real ring); (d) the insertion: "insert"
  ends without contact, or a short search finds it.
- Uncertain: the soft fingers' centring and grip low on the ring (fingertips ~6 mm above the table); the
  guarded line's sideways drift on the arm during the 20 mm insertion; the real camera's error at 380 mm
  (assumed rigid in the gripper frame, so the same for the cylinder and the stick top); the stick's lean
  (the top is measured, so a lean is tolerated).
