# Instructions

You write a Python program that controls a robot arm: a file that defines run(robot, task). It is run once
per episode, from the episode's start (the arm at its start pose, the gripper open), with `robot` the robot's interface
described below and `task` the task's text. Nothing else moves the robot, and the program knows nothing of the world
but what `robot` gives it: the cameras and the arm's own measurements. The arm is a Franka Panda with a parallel gripper (two fingers that open and close along one line).

Frame and units: millimetres, in the table frame: z up, the table surface at z = 0, the table's centre at x = y = 0
(the table is 1.2 m square). The robot stands at the table's -x edge and faces +x (forward, away from itself); +y is
to the robot's left. The robot works with one arm, its left one, whose base is at (x, y) = (-790, 40), just
below table height. Its other arm (the right one, base at (-800, -390)) stays parked at its start pose for the whole
episode, its gripper at about (-490, -390, 350): it is not in the interface, and it is solid. The arm keeps the
safety limits of the real robot it stands for; a move that would break one is refused, with the limit named (there in
m and rad), and nothing moves:
- the TCP stays within x -720..100, y -420..600, z 0..800;
- the fingertips' ends go at most 4 mm below z = 0, and every other part of the gripper, the wrist camera and the arm
  stays at least 50 mm above the table. So near the table the gripper points down or nearly so: it can lean from the
  vertical by about 30 degrees with the TCP at z = 20, 40 at z = 40, 50 at z = 70 and 65 at z = 110 (less in some
  directions and places), and lie horizontal only from about z = 160 up, and not in every direction;
- no part of the gripper or the arm enters the space around the parked arm: x < -420 together with y < -280 (up to
  z = 800);
- the joints' ranges are a little narrower than a Franka Panda's.
Pointing straight down with the TCP at z = 20, the gripper reaches x from about -480 to -90 for y between -210 and
120 (as far as y = -390 where x > -330), and x from about -600 to -150 for y between 270 and 600 (the far half of the
table is out of reach); nothing farther than about 800 mm from the arm's base. A move the arm cannot make is refused
and nothing moves.

The gripper. Its pose is its TCP: the point midway between the two fingers' gripping pads, 16 mm in from the
fingertips' ends. Its orientation is given by two directions (table frame):
- approach_dir: where the fingers point (from the wrist towards the fingertips);
- closing_dir: the line along which the two fingers open and close (it is made perpendicular to approach_dir; its sign
  does not matter: the wrist takes the nearer of the two).
The fingers open to at most 80 mm between the pads and close to 0; they are 36 mm wide (across, perpendicular to both
directions), about 45 mm thick at the root, and stick out 130 mm from the gripper's body, which is about 200 mm wide
along closing_dir and 65 mm thick; the wrist and forearm behind it are about 12 cm thick. With the fingers pointing
straight down the fingertips touch the table when the TCP is at z = 16. set_gripper(arm, width_mm) sets the opening;
the fingers are a position servo that presses with up to about 70 N on whatever stops them, so width 0 grips firmly,
and the opening reported afterwards is the width of what is between them.

Force: robot.state(arm) gives, as force_n, the arm's estimate of the net external force on it (noisy by about 0.5 N
per axis; a held weight or a push shows there; a squeeze between the fingers cancels out in it). There are no touch
sensors in the fingers: what a gripper holds shows in its opening.

Cameras (look()), two RealSense D415 colour + depth cameras, 640x480:
1. the scene camera, fixed across the table at (860, -10, 570), facing the robot and looking down at the table: in its
   image +y points right and +x towards the camera; the parked right arm is on the left of that image.
2. the arm's wrist camera ("left_wrist"): it sits on the gripper's body beside the fingers, 50 mm
   to one side and 133 mm back from the TCP, looking along approach_dir: it sees what is in front of the fingertips,
   and the two fingertips show at the bottom corners of its image. At the start (the gripper pointing straight down) its
   image's up is +x and its right is -y; the image turns with the gripper. Surfaces nearer than about 140 mm beyond
   the TCP are too near for its depth.
locate(camera, points) turns pixels of the latest look into 3D points (mm) on the surface seen there, from that
camera's depth image. This is real stereo depth: noisy (1-2 mm at 0.4 m, 5-8 mm at 1 m), with holes (null) at object
edges, on parts of the table, in the left ~70-130 px of every image, and for everything nearer than 275 mm to the
camera. The cameras' calibration is off by a few mm and up to a degree, so points are wrong by up to about 25 mm from
the scene camera (it is 1.2 m from the work area; e.g. the table top can read 20 mm too high or too low) and by less
from a wrist camera close to something, in a direction that stays the same during the episode: heights relative to
the table as the same camera sees it are better than absolute ones, and contact (stop_on_contact, the gripper's opening after closing) tells
where things really are.

Motion:
- move_gripper(arm, x, y, z, approach_dir, closing_dir, stop_on_contact) moves that gripper's TCP along a straight line
  to (x, y, z) while it turns to the new orientation. With stop_on_contact = true it moves at 4 cm/s and stops as soon
  as something pushes on that arm with more than about 4 N; robot.state(arm) tells where it stopped. With stop_on_contact =
  false it follows the line whatever it touches (4 cm/s below z = 300, 15 cm/s above). A free move with a big change
  of orientation is made by turning the arm's joints directly (a curved path, not a line; the result says so); it is
  refused if the arm would hit something on the way.
- turn_gripper(arm, centre, axis, angle_deg) rotates the whole gripper rigidly about the line through the point
  `centre` along the direction `axis` (right-hand rule: axis (0, 0, 1) and a positive angle turn it counter-clockwise
  seen from above), at 30 degrees a second; what the fingers hold on that line is turned. The arm's last joint turns
  about approach_dir through about 330 degrees in all; a turn that would pass a joint's limit is refused whole
  (nothing moves): turn less, or open the gripper, turn back and grip again. Where in that range the wrist is after a
  move is not known in advance.

The program's interface: the commands named above are methods of `robot` (lengths in mm, table frame; angles in degrees;
arm is always "left"; directions and points are any sequence of 3 numbers):

robot.look(cameras=None) -> {name: Frame} of "scene", "left_wrist" (both by default). A Frame f has
    f.rgb                    (480, 640, 3) uint8, RGB
    f.depth                  (480, 640) float: mm along the optical axis, nan where there is no depth
    f.fx, f.fy, f.cx, f.cy   pinhole intrinsics (px); f.width, f.height
    f.eye, f.R               the camera's position (mm) and rotation (3x3, camera axes -> table frame; camera axes: x
                             right, y down, z forward) as the robot believes them (the calibration error above)
    f.locate(u, v)           -> np.array([x, y, z]) or None: the 3D point of the surface at pixel column u, row v
                             (the median depth in a 7x7 window): this is locate()
    f.points()               -> (480, 640, 3) array: every pixel's 3D point (nan where there is no depth)
    f.project(xyz)           -> (pixels (N, 2) as (u, v), depths (N,) mm) of table-frame points (N, 3) in this frame
robot.state(arm) -> dict: tcp_mm [x, y, z], approach_dir, closing_dir, opening_mm (measured between the pads),
                             commanded_mm, force_n [fx, fy, fz] (the external-force estimate)
robot.move(arm, xyz, approach_dir, closing_dir, stop_on_contact=False)    move_gripper
robot.turn(arm, centre, axis, angle_deg)                                  turn_gripper
robot.set_gripper(arm, width_mm) -> the opening measured afterwards (mm): the width of what is between the fingers
robot.home(arm)              that arm back to its start pose by turning its joints (refused if it would hit something)
robot.wait(seconds)          let robot time pass (at most 10 s a call);  robot.time_s() -> robot time since the start
robot.segment(frame, phrase) -> [(mask, score), ...]: an open-vocabulary segmentation model (SAM3) on the frame's colour
                             image, prompted by a short noun phrase: at most 6 masks ((480, 640) bool), best score
                             first; it can miss the thing, return part of it, or another thing. About 1 s a call.

The motion commands (move, turn, home) return a dict: done (the target was reached), moved (False: the command was
refused and nothing moved), why ("" | "contact": a stop_on_contact move stopped at a touch | the refusal's or the
safety stop's text), curved (the move was made by turning the joints). They do not raise for a refused move.

The program: Python 3.11 with numpy, scipy, cv2 (OpenCV) and the standard library. No files, no network, and nothing
of `robot` beyond what is listed here. What it prints goes to the episode's log. An episode has 600 s of robot
time (computing does not count); after that the next command raises an exception and the episode ends. The task is
judged on the state of the world when run() returns or raises. The arm's safety stops a move if the net force on that
arm exceeds about 40 N; anything in the way of an arm is pushed or knocked over.

Imports: only math, numpy, scipy, cv2, itertools, functools, collections, dataclasses, typing, heapq, bisect, statistics, random, time, copy, enum, operator, numbers, abc (and their submodules). No eval, exec, open, getattr or file access (numpy's and OpenCV's file functions included), and no attribute whose name starts with an underscore except on self.

The program is run exactly as you write it, on the real robot. You cannot test it: there is nothing you can run and nothing
to try it in. If the operator judges the episode a failure, you are shown what it recorded and write the program again:
at most 2 more times. Write it so that it finds things with the cameras, checks what it measured, and recovers where it can.

# Message

Task: put the cylinder on the stick. On the table there are a cylinder with a hole through it (standing on one of its flat ends, the hole vertical) and a stand with one vertical stick. Pick up the cylinder and thread it onto the stick, so that the stick passes through its hole, and let go of it: at the end the cylinder must be on the stick, the robot not touching it, and the stand upright. Then open the gripper and return it to its initial pose. The hole can be as little as 1.5 mm wider than the stick. The cylinder and the stand may stand on the table or on something on it; other objects may be on the table.

Write the program for this task. It is written for the scene as it is now, shown below, and run on it once; the
objects may have been nudged since the look, so the program finds them with the cameras itself.

Write the program to the file program.py in your working directory (the whole program: a Python file that defines run(robot, task)). The images and files named here are in that directory: read them with your file tools.

The look of the scene as it is now (the arm at its start pose; the scene camera's and the wrist camera's colour images):
The arm's state: tool point, approach direction, closing direction, opening (mm): [-491.5, 83.5, 357.2] [0.042, 0.197, -0.979] [0.093, -0.977, -0.193] 80.0
scene: look_scene.png
left_wrist: look_left_wrist.png

# Given

look_left_wrist.png
look_scene.png
