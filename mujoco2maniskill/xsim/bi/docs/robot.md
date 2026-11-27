THE ROBOT (the same in the practice simulator and in the target)

Arms: two ARX X5 arms, 6 revolute joints each, standing on the table: the left arm's base at (-300, -450, 0) mm, the
right arm's at (300, -450, 0), both facing +y. Start pose: every joint at 0: the tool points at (-300, -207, 157) and
(300, -207, 157), the fingers pointing forward (+y) and level, closing along x, fully open.
The arm joints are position servos (stiffness 4400 N m/rad, damping 40 N m s/rad, at most 100 N m); the arms' own
weight is compensated: they do not sag.

Grippers: two fingers, each travelling 0 to 44 mm (88 mm between the inner faces when fully open). The fingers are a
position servo (2300 N/m, damping 100 N s/m, at most 100 N a finger); "closed" is commanded 10 mm past touching, so a
closed gripper presses on what it holds. Finger surface friction coefficient 0.5.

How commands are carried out: every command is a stream of joint targets at 25 Hz (one action every 40 ms; the physics
steps every 4 ms; within an action the joints' targets move to the new values over the first 32 ms). A move is planned
whole before anything moves: inverse kinematics along a straight line of the tool point, the orientation turned evenly
(a joint-space path where a line is not possible); it is refused, nothing moved, if the arm cannot reach. Speeds:
25 cm/s (5 cm/s in a stop_on_contact move), turns 90 degrees a second; a gripper goes from fully open to closed in
0.6 s and is then given 0.3 s. A stop_on_contact move stops at about 4 N of new force against its direction. An
episode is 1000 actions: 40 s of robot time.

Cameras: three, 640 x 480, pinhole, colour and depth (see the interface for the depth), cx = 320, cy = 240.
- "head": fx = 288.1, fy = 336.5 px (its pixels are not square). Fixed at (0, -410, 543) mm, looking forward (+y) and
  60 degrees below the horizontal.
- "left_wrist", "right_wrist": fx = fy = 397.0 px, each on its arm's wrist (see the interface).
Every frame carries its camera's pose and intrinsics (f.eye, f.R, f.fx, ...).

Table: a flat top 1400 x 1100 mm, x from -700 to 700, y from -600 to 500, its surface at z = 0.
The head camera's stand is on the table at the robot's edge: a foot plate (x from -152 to 152, y from -518 to -423,
13 mm high) with a pole on it (about 56 mm wide, at y from -493 to -452). It is fixed.
