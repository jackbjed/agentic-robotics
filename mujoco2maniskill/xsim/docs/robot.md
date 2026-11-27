THE ROBOT (the same in every environment of these experiments)

Arm: a Franka Panda, 7 revolute joints, its base (link0) at (-788.2, 41.4, -45.9) mm in the table frame, facing +x.
Joint ranges used (rad): lower [-2.6937, -1.7128, -2.8473, -2.9921, -2.7565, 0.5945, -2.8473], upper [2.6937, 1.7128, 2.8473, -0.2018, 2.7565, 3.7025, 2.8473].
Start pose (rad): [-0.0253, -0.7839, 0.0163, -2.3925, 0.0115, 1.6087, 0.7708]: the tool point at (-478, 41, 350) mm, the fingers pointing straight down, closing along y.

Gripper: a Panda hand with long tapered fingers. The tool point (TCP) is 180.8 mm from the hand's frame along the
fingers; the fingertips' ends are 15.7 mm beyond it. Each finger: 138 mm long, 36 mm wide (across the closing line),
about 44 mm thick at its root, tapering to about 3 mm at its tip. Opening 0 to 80 mm between the inner faces (each
finger travels 40 mm). The fingers are a position servo of 2000 N/m, limited to about 70 N; finger surface friction 1.5.

How commands are carried out: a move is planned whole before anything moves (inverse kinematics along a straight line
of the tool point in 5 mm steps, the orientation turned evenly; a joint-space path where a line is not possible) and is
refused, nothing moved, if the arm cannot reach or would leave its limits. The joints are position servos that follow
the planned path; after a move the remaining error is taken out. Speeds: 150 mm/s above z = 300 mm, 40 mm/s below it
and in every stop_on_contact move; turns 30 to 45 degrees a second. A stop_on_contact move stops at about 4 N of new
force against its direction; any move stops at about 40 N ("SAFETY STOP"). The force estimate is noisy by about 0.5 N.

Cameras: two, 640 x 480, pinhole, fx = fy = 617.2 px, cx = 320, cy = 240, colour and depth (see the interface for the depth).
- "scene": fixed at (864, -8, 572) mm; its axes in the table frame (x right, y down, z forward):
  [-0.0349, 0.9993, -0.0166], [0.3478, -0.0034, -0.9376], [-0.9369, -0.0385, -0.3474].
- "left_wrist": on the hand; in the tool point's frame (x across the fingers, y along the closing line, z along the fingers) it sits at
  (51, 3, -133) mm and looks along the fingers.
Every frame carries its camera's pose and intrinsics (f.eye, f.R, f.fx, ...).

Table: a flat top 1221 x 1224 mm, centred at x = y = 0, its surface at z = 0; table friction coefficient 1.0.
The safety limits and the reachable area: docs/interface.md.
