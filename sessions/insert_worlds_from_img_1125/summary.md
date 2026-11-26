PUT THE CYLINDER ON THE STICK - session summary (result: program v23, world w13)

THE TASK AS BUILT (world w13)
The stand is modelled on the real one in the two images (measured with the real depth image). It is a light wood
board, 265-295 x 80-95 x 18-22 mm, density 450-750 kg/m3, friction 0.3-0.6 to the table, free on the table (it can be
pushed or knocked over). It has a wooden stick 11.5-13 mm in diameter, standing 160-180 mm above the board, 38-50 mm
from one end, tilted 0-1.5 deg. Two dark hole marks are on the board.
The cylinder stands on one flat end. It is a ring of boxes (12 collision boxes, 24 visual boxes), round (60%),
hexagonal or octagonal outside, with a round or polygonal hole. The hole is 1.5-3 mm or 3-10 mm wider than the stick
(in "hard" scenes always 1.5-2 mm). Outer radius is up to 33 mm, wall 5-22 mm, height 12-70 mm, density 300-8000 kg/m3
(mass at least 25 g), friction 0.25-1.1, and one of 12 colours.
Heights: the table is at -40..+40 mm. The stand and the cylinder each sit on a box support 15-150 mm high with
probability 35% (60% in hard scenes). Low tables get a support so that the cylinder stays graspable.
Distractors: 0-6 of them (4-8 in the work area in hard scenes): solid cylinders, boxes, mugs, 150-240 mm bottles,
and yellow tape squares. The table is one of 12 colours or patterns, black weighted, like the real table.
Cameras: the wrist camera's pose error is 2-6 mm / 0.5-2 deg (hard: 4-8 mm / 1.5-2.5 deg), the scene camera's 3-8 mm
/ 0.5-1.5 deg, and the depth noise is 0.12-0.3 px. Each scene is "hard" with probability 40%.
success() reads the TRUE state. It checks that both end centres of the hole lie within (hole radius - stick radius
+ 1.5 mm) of the stick's (tilted) axis, and that the whole hole lies between the board and the stick tip, so the
stick passes fully through. It also checks that the stand tilts less than 10 deg and that the robot touches neither
the cylinder nor the stand. A program that does nothing fails. The final images show the ring down on the board.
success() cannot see the arm's own pose, so "gripper open, arm home" is done by the program but not checked.

THE SKILL (prog v23). It uses only wrist-camera depth: no colours, no SAM, and it ignores the task text.
1 Survey: two high wrist views. Each view gets its own height map, with the table tilt (camera error) fitted and
  removed. Views are not merged for detection, because each view's calibration error shifts objects differently.
  A component tree over height levels finds the stick (a thin blob standing >140 mm over the table) and cylinder
  candidates (round blobs 16-82 mm). If the survey finds nothing, more views are taken; looser candidates come last.
2 Stick close-up: the camera is put straight above the tip at 320 mm (300 or 285 mm if out of reach) and the
  tip-disc centre is measured. The yaw is chosen where the arm can reach.
3 Each candidate is looked at from the same distance. The cylinder is the one with a hole near its centre, the hole
  under 40 mm and the outline under 82 mm (this excludes mugs).
4 Two-yaw view: the same close-up with the gripper turned 90 deg. The camera error turns with the gripper, so
  measured = position + Rz(yaw)*bias gives both, to about 0.1 mm in sim. Near the parked right arm's keep-out box,
  where the gripper cannot turn, the cylinder is first moved to a flat free spot.
5 Grip, open, turn 90 deg, grip again: the parallel fingers centre the cylinder on the TCP in both axes. Release and
  look again from the same distance. This gives a second bias estimate, which is averaged with step 4. If the
  cylinder moved, it is centred again.
6 The stick tip is corrected by the bias (same camera geometry as step 2). The skill grips, carries the cylinder
  over the tip (paths are raised over anything tall in the height map, other yaws are tried), and lowers it with
  stop_on_contact. On contact it searches rings 0.8 mm apart, up to 4.8 mm. A stop below the felt tip level
  (tilted stick) counts as threaded. It lets go, and the ring slides down the stick.
7 It opens the gripper and goes home. If threading fails, it puts the cylinder back and retries (3 attempts).
Typical time is 85-130 s; the longest seen is 250 s (the limit is 600 s).

TESTS (simulated; s = scene seeds)
- Development worlds w5-w11: early versions 6/8 and 5/8, then 16/16, 8/8, 8/8, 9/9.
- Hard world w12: 8/8 (v18).
- Final mixed world w13: v18-v23 42/42 (v23 8/8 on new scenes). This covers tight holes, bias up to 18 mm,
  supports up to 150 mm, tables at +-40 mm, tilted sticks, hex/oct shapes, tall tubes, heavy and light rings,
  slippery and grippy rings, clutter, and both stand and cylinder next to the keep-out box.
- Sensor stress w14 (depth noise 0.35-0.5 px, 6 px edge fattening, more holes): v22 5/8 -> v23 7/8.
Failures fixed during the session:
- flying pixels at the fingertips taken for the stick
- the centring turn sweeping an off-centre ring away (this led to the two-yaw method)
- the keep-out box
- double images from merging views
- table tilt in the height map
- a stick too high for the 320 mm close-up
- the fingertip z limit when setting a ring down
- MuJoCo instability of thin slender rings (a world bug, fixed with chunkier collision boxes)

WHERE IT STILL FAILS / LIMITS
- Under heavy depth noise, a small, low ring right next to the parked arm can be missed by the survey (w14, s5).
- The cylinder must stand on a flat end. A ring lying on its side is not handled.
- An object with a centred hole (another ring) is taken for the cylinder if it is the first such candidate.
- If the gripper cannot turn around the ring and no free flat spot exists, the bias is only partly calibrated. The
  contact search (+-4.8 mm) then has to make up for it.

ON THE REAL ROBOT: EXPECTATIONS AND WHAT TO MEASURE FIRST
The method does not depend on absolute camera calibration. It does depend on four things:
(a) depth on the 12 mm stick tip from 285-320 mm away. The real wrist image had good tip depth at 565 mm; check it
    at 320 mm.
(b) symmetric fingers. The Franka hand is coupled, so this should hold.
(c) the camera error being rigid in the gripper frame. Arm kinematic errors would add about 1 mm, which the search
    covers.
(d) the ring sliding down the dowel. Real wood and plastic friction or a burr could jam it; the check needs it fully
    threaded.
Measure first:
- the stick's real diameter, height and straightness
- the hole sizes of the real cylinders
- the wrist camera's depth noise on the tip
- how far the robot's reported TCP is from the true one after a 90 deg turn
- whether a 4 N contact stop is gentle enough for the real stand (it may slide; it is light)
Uncertain: real depth artefacts on shiny or dark rings, the board's real mass and friction, and lighting.
