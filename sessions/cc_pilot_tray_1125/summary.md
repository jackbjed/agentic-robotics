PUT THE BLOCK IN THE TRAY - session summary (result: program v9 with world w8)

THE TASK AS BUILT (world w8, file world_tray.py)
- Block: a rectangular box, sides a = 20-60 mm and b = a..a+35 (at most 95) mm, height 20-70 mm, 20-500 g,
  sliding friction 0.3-1.0, free on the table, any yaw. Its top always stands at least 22 mm above robot z = 0
  (the fingertips may not go below z = -4 in the robot frame, so on a low table only tall blocks are graspable).
- Tray: loose (free joint) rectangular tray, inner floor 130-260 x 110-220 mm (always >= the block's long side
  + 80 mm), floor 3-8 mm thick, walls 3-10 mm thick and 12-40 mm high above the floor; half of the trays have
  walls flared outwards by 5-25 deg. 150-800 g, friction 0.3-0.9, any yaw.
- Places: tray centre x -440..-150, y -220..340; block x -450..-150, y -210..290, at least 30 mm (plus its
  half-diagonal) from the tray's rim, and not at x < -390 with y < -160 (next to the parked right arm's keep-out
  box, where no top grasp is possible).
- Table: top at -40..+40 mm, random colour, plain or checker/stripes/speckle/wood/blotches. 25 % of blocks and
  20 % of trays are coloured like the table (low contrast); colours otherwise random, 15 % very dark.
- Cameras: depth noise 0.12-0.30 px, calibration error scene camera 3-8 mm / 0.5-1.5 deg, wrist 2-5 mm / 0.5-1.5 deg.
- success() (true state, after 1 s at rest): the block's whole footprint inside the tray's inner walls, its lowest
  point within 4 mm of the floor's top, the block at rest, the tray upright and moved less than 30 mm, and the
  robot touching neither the block nor the tray. A program that does nothing fails it (probe v0).
  Gripper open + arm home are done by the program but cannot be read by success(); the final images show them.

WHAT THE SKILL DOES (skill.py, v9)
1. Scene camera: fit the table plane (RANSAC) and mark everything 8-140 mm above it. The tray is SAM's "tray"
   mask (checked for size), or else the largest object found by depth.
2. Wrist camera from ~550 mm above the tray: the rim (walls above the local table plane) gives centre, axes and
   rim height; the flat floor gives floor height and the floor's real extent (matters for flared walls).
3. Block candidates: SAM "block" masks, then depth objects outside the tray (split in a top-down XY grid). Each is
   confirmed by a wrist look from ~400 mm: a flat-topped blob 10-85 x <=125 mm, not inside the tray; its top face
   gives centre, yaw, sides, top height and the local table height.
4. Grasp: closing across the shorter side (or the other side if <= 72 mm), open to width + 26 mm, pre-grasp at
   two heights, contact-guarded vertical descent (in steps if the direct move is refused) to
   max(top - min(h/2, 22), table + 19, 13) mm; close; check the opening against the measured width; up to 3 tries
   with a new look.
5. Carry at z >= 305 (fast zone), closing direction along the tray's long axis. Drop point = tray centre, or the
   nearest reachable point inside the floor (for trays near the keep-out box), two orientations, two heights.
   Lower with a contact guard to 28 mm above the floor, measure the arm's sag under the load, move so the
   block's bottom is ~4 mm above the floor, open (block falls ~4 mm), retreat up, open fully, home.
   If no point in the tray can be reached the block is set back where it was (never dropped from height).
Robot time: 40-56 s, mean 47 s.

TESTS AND RESULTS (all in the simulator)
- w4 plain world: v1 8/8.  w5 hard (low contrast, noise, calibration, 20 mm and 500 g blocks): v2 16/16.
- w6 = w5 with SAM returning nothing (depth-only path): v2 7/8, v3 6/8 -> v4 8/8, v7 8/8.
- Failures found and fixed: block and tray merged in the scene camera's image (fixed: XY-grid split, wrist
  confirmation of each candidate); tray centre unreachable near the parked arm (fixed: offset drop points);
  heavy blocks pressed into the floor with up to 41 N by a late contact stop (fixed: sag-compensated release);
  a "wrist flip" refusal of the grasp descent near the keep-out box (fixed: stepwise descent, 2 pre-grasp heights).
- Final world w8: v6 16/16, v7 16/16, v9 16/16 (16 distinct scenes covering table -38..+39 mm, blocks 23-60 mm
  wide, 25-493 g, straight and flared trays, trays near the keep-out box, low-contrast colours).

WHERE IT STILL FAILS / LIMITS
- Blocks right next to the parked arm (x < -390, y < -160) are excluded from the world: a top grasp is refused
  there. A tilted approach would be needed.
- Only box-shaped blocks with one side <= 72 mm; no clutter or distractor objects; the tray must be the largest
  object on the table and its walls must stand >= ~10 mm above the table to be seen in depth.
- The place relies on the wrist depth of the tray floor (+-2-3 mm); a 4 mm drop is assumed harmless.

ON THE REAL ROBOT - MEASURE FIRST
- Wrist camera extrinsics (the program assumes the camera ~51 mm to the side and 133 mm behind the TCP for its
  first look and then uses frame.eye), and depth quality on the real block/tray materials (black, shiny or
  transparent objects lose depth; the block must give a flat top patch).
- That the TCP lower limit (13.5 mm used) and the fingertip clearance (grasp >= table + 19 mm) match the real pads.
- The arm's sag with a held block and the contact-stop latency: the release height depends on them.
- SAM on real images (the depth path works without it, but it ranks candidates).
Expected: similar behaviour if the depth and calibration are as specified. Uncertain: real tray shapes with lips
or rounded walls, reflective trays, and blocks lighter than 20 g (could shift when the fingers open).
