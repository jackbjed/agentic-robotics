You write a Python program that controls a two-armed robot: a file that defines run(robot, task). It is run once
per episode, from the episode's start (both arms at their start poses, open), with `robot` the robot's interface
described below and `task` the task's text. Nothing else moves the robot, and the program knows nothing of the world
but what `robot` gives it: the cameras and the arms' own measurements. Each arm is a small 6-joint arm (ARX X5) with a
parallel gripper (two fingers that open and close along one line).

Frame and units: millimetres, in the table frame: z up, the table surface at z = 0, the table's centre at
(x, y) = (0, -50); the table is 1400 mm along x and 1100 mm along y (x from -700 to 700, y from -600 to 500). The robot
stands at the table's -y edge and faces +y (forward, away from itself); +x is to the robot's right. The left arm's
base is at (x, y) = (-300, -450), the right arm's at (300, -450), both on the table. With its fingers pointing straight
down a gripper reaches about 500 mm from its arm's base near the table; pointing forward and 45 degrees down about
680 mm; pointing forward and level about 750 mm. A pose an arm cannot reach is refused and nothing moves. The arms and
grippers are solid: nothing checks a move for collisions, and what is in the way is pushed.

The grippers. A gripper's pose is its TCP: the point midway between the two fingers, 13 mm in from the fingertips'
ends. Its orientation is given by two directions (table frame):
- approach_dir: where the fingers point (from the wrist towards the fingertips);
- closing_dir: the line along which the two fingers open and close (it is made perpendicular to approach_dir; its sign
  does not matter: the wrist takes the nearer of the two).
The fingers open to at most 88 mm between their inner faces and close to 0; they are 60 mm wide (across, perpendicular
to both directions), about 35 mm thick at the root, and stick out 86 mm from the gripper's body, which is about 175 mm
wide along closing_dir and 60 mm thick. With the fingers pointing straight down the fingertips touch the table when
the TCP is at z = 13. set_gripper(arm, width_mm) sets the opening; the fingers are a position servo that presses with
up to about 100 N on whatever stops them, so width 0 grips firmly, and the opening reported afterwards is the width of
what is between them. At the start each gripper is fully open, its TCP at (-300, -207, 157) (left) and
(300, -207, 157) (right), its fingers pointing forward (+y) and level, closing along x.

Force: robot.state(arm) gives, as force_n, the arm's estimate of the net external force on it (noisy by about 0.5 N
per axis; a push shows there; a squeeze between the fingers cancels out in it). There are no touch sensors in the
fingers: what a gripper holds shows in its opening.

Cameras (look()), all colour + depth cameras, 640x480:
1. "head": fixed above the robot's edge of the table at (0, -410, 543), looking forward and down (60 degrees below the
   horizontal): in its image +x points right and +y points up; the two grippers show at the bottom.
2. "left_wrist" and 3. "right_wrist": each sits on its gripper's wrist, 60 mm behind the TCP and 51 mm to one side of
   the fingers' plane (above it at the start), looking 30 degrees off approach_dir towards the fingers' side: it sees
   what is in front of the gripper, and the two fingertips show at the bottom corners of its image. The image turns
   with the gripper.
locate(camera, points) turns pixels of the latest look into 3D points (mm) on the surface seen there, from that
camera's depth image. The depth is the simulator's: exact to about a millimetre wherever the camera sees a surface,
and the cameras' poses are exact. A pixel that sees no surface (one that looks past the table at the empty background,
as the upper part of a wrist camera's image does at the start) has no depth: nan in f.depth and f.points(), None from
f.locate().

Motion:
- move_gripper(arm, x, y, z, approach_dir, closing_dir, stop_on_contact) moves that gripper's TCP along a straight line
  to (x, y, z) while it turns to the new orientation. With stop_on_contact = true it moves at 5 cm/s and stops as soon
  as something pushes on that arm with more than about 4 N; robot.state(arm) tells where it stopped. With stop_on_contact =
  false it follows the line whatever it touches, at 25 cm/s. A free move the arm cannot make as a straight line is made
  by turning the arm's joints directly (a curved path, not a line; the result says so), to the joint solution nearest
  to where the arm is; it is refused if that would swing the arm behind the robot or through the table (then go by a
  pose in between, for example turn the gripper first and move after).
- turn_gripper(arm, centre, axis, angle_deg) rotates the whole gripper rigidly about the line through the point
  `centre` along the direction `axis` (right-hand rule: axis (0, 0, 1) and a positive angle turn it counter-clockwise
  seen from above), at 90 degrees a second; what the fingers hold on that line is turned. A turn the arm cannot make
  is refused whole (nothing moves).

The program's interface: the commands named above are methods of `robot` (lengths in mm, table frame; angles in degrees;
arm is "right" or "left"; directions and points are any sequence of 3 numbers):

robot.look(cameras=None) -> {name: Frame} of "head", "left_wrist", "right_wrist" (all three by default). A Frame f has
    f.rgb                    (480, 640, 3) uint8, RGB
    f.depth                  (480, 640) float: mm along the optical axis, nan where there is no depth
    f.fx, f.fy, f.cx, f.cy   pinhole intrinsics (px; the head camera's fx and fy differ); f.width, f.height
    f.eye, f.R               the camera's position (mm) and rotation (3x3, camera axes -> table frame; camera axes: x
                             right, y down, z forward)
    f.locate(u, v)           -> np.array([x, y, z]) or None: the 3D point of the surface at pixel column u, row v
                             (the median depth in a 7x7 window): this is locate()
    f.points()               -> (480, 640, 3) array: every pixel's 3D point (nan where there is no depth)
    f.project(xyz)           -> (pixels (N, 2) as (u, v), depths (N,) mm) of table-frame points (N, 3) in this frame
robot.state(arm) -> dict: tcp_mm [x, y, z], approach_dir, closing_dir, opening_mm (measured between the fingers),
                             commanded_mm, force_n [fx, fy, fz] (the external-force estimate)
robot.move(arm, xyz, approach_dir, closing_dir, stop_on_contact=False)    move_gripper
robot.turn(arm, centre, axis, angle_deg)                                  turn_gripper
robot.set_gripper(arm, width_mm) -> the opening measured afterwards (mm): the width of what is between the fingers
robot.home(arm)              that arm back to its start pose by turning its joints
robot.wait(seconds)          let robot time pass (at most 10 s a call);  robot.time_s() -> robot time since the start
robot.segment(frame, phrase) -> [(mask, score), ...]: an open-vocabulary segmentation model (SAM3) on the frame's colour
                             image, prompted by a short noun phrase: at most 6 masks ((480, 640) bool), best score
                             first; it can miss the thing, return part of it, or another thing. It takes no robot time.
robot.show(label, image)     keep an image (H x W x 3 RGB or H x W) for whoever reads the run (the first 12)

The motion commands (move, turn, home) return a dict: done (the gripper is at the target: within 5 mm and 3 degrees
of it as the arm measures), moved (False: the command was refused and nothing moved), why ("" | "contact": a
stop_on_contact move stopped at a touch | "the arm stopped short of the target ...": it was held up by something in
its way | the refusal's text), curved (the move was made by turning the joints). They do not raise for a refused move. One arm moves at a time: a
command returns when its motion is over.

The program: Python 3.11 with numpy, scipy, cv2 (OpenCV) and the standard library. No files, no network, and nothing
of `robot` beyond what is listed here. What it prints goes to the episode's log. An episode has 40 s of robot time
(computing, looking and segmenting do not count); after that the next command raises an exception and the episode
ends. The task is judged on the state of the world at every moment of the episode: the episode ends, a success, as
soon as everything the task asks for holds at once, and is a failure if that never happens within the 40 s.
