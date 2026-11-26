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

You develop this skill in a session with a simulator of the robot to practise in. The simulator has the robot, its
cameras and the table, and nothing else. There is no program yet. What is on the table, what an episode asks and how
it is judged are yours to make, as worlds (below); in practice the simulator's TRUE state is open to you, to test
and to measure with. You work with your file tools, which reach only this directory (your workspace), and ONE shell
command, which runs nothing but the sub-commands below (no other shell command works here):

    /var/tmp/ar-cc/insert_own_cc_1125/practice SUB-COMMAND ...

You write the program and the worlds as files in the workspace; the command saves them, runs them and reports.
Its sub-commands:

write_program FILE --note "..."   save a file of yours as a new version of the program (v0, v1, ...). Every version
                              is kept, and any version can be run, so a version can also be an experiment (a
                              program that only looks and reports). To change a version, edit your file and save
                              it again.
new_scenes COUNT              more scenes (s1, s2, ...). A scene here is an empty table with a seed: in a world of
                              yours it is one episode, which the world's scene(scene, rng) fills by its rng (the
                              same for the same scene and world). The seed also fixes the episode's sensor noise
                              and calibration error: a version on a scene in a world gives the same result every
                              time, and run_episodes keeps results.
write_world FILE --note "...", read_world [--world K]   your worlds (w0, w1, ...): see below
run_episodes --scenes S ... --world K [--version V]   run a version on scenes in a world, one episode a scene (at
                              most 8 new ones a call, run at the same time). You get, for each episode: the
                              verdict of the world's success() on the TRUE state at the end, how the program
                              ended, the robot time, what the program printed, and the TRUE positions of the
                              world's bodies at the start and at the end.
read_output --scene S [--version V] [--world K] [--start-line N] [--max-lines N] [--contains TEXT] [--no-trace]
                              more of an episode's output: what the program and the world printed and, on lines
                              starting with "#", a trace of every robot command: robot time, the command, its
                              result, the arm's TRUE tool position (and opening), and after NOW the TRUE position
                              of each body that moved. Compare what the program measured with the truth there.
view_images --scene S --names NAME ... [--version V] [--world K]
                              the overview camera's picture of the end of an episode (the name: final), and the
                              images the program handed over with robot.show (at most 6 a call)
list_scenes                   the scenes there are
read_program [--version V] [--start-line N] [--end-line N] [--file FILE]
                              a version's text, with line numbers; with --file: written to that file of yours
                              instead, to edit it (read_world takes the same)
submit [--version V] [--world K] [--final --summary FILE] [--note "..."]
                              name the version that is your result

A version is the newest one where --version is left out. Pictures (view_images, and those a result carries) are
written as files into pictures/ of the workspace, and the output names them: read them with your file tools.

Keep notes in notes.md in the workspace: what you found out about the task, the robot and the cameras; what worked
and what did not; what you plan next. The conversation is cut when it grows long, and after it you keep that file,
the versions with their notes, and all episode results (read_output, view_images); nothing else of the
conversation is sure to stay.

In a program, robot.show(label, image) hands an image to you (H x W x 3 uint8 RGB, or H x W; at most 12 an episode;
no robot time): for instance a camera frame with what the program detected drawn on it (cv2). In practice episodes a
program can also read the simulator: robot.truth() -> {name: [x, y, z]} (mm), the TRUE positions now of the world's
bodies (and its joints), and robot.judge() -> your world's success() on the state as it is now (a dict; "ok":
success). Both cost no robot time, and both return None when your result is evaluated: they are for measuring and
checking (how far is a measurement from the truth? did a step do what it should?), not for doing the task. 
Imports: only math, numpy, scipy, cv2, itertools, functools, collections, dataclasses, typing, heapq, bisect, statistics, random, time, copy, enum, operator, numbers, abc (and their submodules). No eval, exec, open, getattr or file access (numpy's and OpenCV's file functions included), and no attribute whose name starts with an underscore except on self.

Worlds. A WORLD is a Python file of yours that says what the simulator builds on the table, what an episode asks and
how it is judged, and, if you want, how the robot answers. write_world saves a file of yours as a new version of a world
(w0, w1, ...), as write_program does for the skill; read_world gives its text. Results are kept per
version, scene and world (the scene is then named s3@w1). Several worlds can stand for several kinds of test (a
plain one, a hard one, one for a single question). A world's file defines:

def scene(scene, rng):
    # -> the scene the simulator builds. `scene` is the empty table, a dict: leave what is in it and add
    #   scene["mjcf"] = "<mujoco> ... </mujoco>"
    #                                       everything that is on the table, in MuJoCo's own language (MJCF): whatever
    #                                       MJCF can describe. A geom with contype="0" conaffinity="0" is seen and
    #                                       touches nothing (a mark on the table). Mechanisms too: a drawer on a
    #                                       slide joint, a door or a lid on a hinge, a lever, a button on a spring,
    #                                       a latch, a box with a sliding lid, a rack, a mesh of your own. The text
    #                                       has a <worldbody> with your bodies and, as needed, <asset> (meshes by
    #                                       their vertex attribute, materials, built-in textures), <default>
    #                                       (classes of your own), <equality>, <tendon>, <contact> (pair, exclude).
    #                                       - bodies in bodies, each with geoms (box, sphere, capsule, cylinder,
    #                                         ellipsoid, mesh), joints (slide, hinge, ball, free; range, damping,
    #                                         frictionloss, stiffness, springref, armature), sites, lights; a body
    #                                         without a joint is fixed where it is;
    #                                       - MJCF's units: metres, kilograms, radians. Positions are in the
    #                                         robot's frame (the one of every position here), z = 0 at the table's
    #                                         top;
    #                                       - every name, and every reference to a name, starts with "w_" (w_drawer,
    #                                         w_slide): the simulator's own bodies cannot be named or changed;
    #                                       - soft and loose things are MuJoCo's <composite> (this is MuJoCo 2.3.7: no
    #                                         flex), each with a prefix of its own (prefix="w_soft"): type box,
    #                                         cylinder or ellipsoid (a soft body; inside a body that has a
    #                                         <freejoint/>), grid (a cloth; in the worldbody, placed by its
    #                                         offset), cable (a line that bends), particle (loose particles). Its
    #                                         count and spacing, the <geom> of its elements (size, mass, friction) and
    #                                         <joint kind="main" .../> or <tendon .../> set its size, weight and
    #                                         stiffness; <skin .../> gives it a surface in the images. truth() gives a
    #                                         composite as ONE entry under its prefix, the centre of its bodies
    #                                         (state.points(prefix) in success(): each body's place). Many loose things
    #                                         of a kind can also be bodies of their own (w_item_0, w_item_1, ...);
    #                                       - not possible: files (a mesh is its list of vertices), actuators, sensors,
    #                                         plugins, cameras, the model's settings; at most 450 bodies, 900 geoms and
    #                                         1400 degrees of freedom, the composites' included (they slow the
    #                                         simulation: a cloth of 13 x 13 nodes runs slower than real time);
    #                                       - a geom that says nothing of its contacts has MuJoCo's defaults
    #                                         (friction 1, condim 3); the fingers' pads have friction 1.5.
    #                                       MuJoCo compiles the text before the episode; if it does not, its error
    #                                       message is the episode's result. truth() then also has each named body
    #                                       of yours (its frame's origin, mm) and each named slide or hinge joint
    #                                       (mm, degrees), and the trace shows them when they move. The robot's
    #                                       moves take your bodies for obstacles like any other. A move stops at
    #                                       40 N on the arm (SAFETY STOP), a stop_on_contact move at 4 N: a
    #                                       mechanism the robot is to work must give way to less (a drawer: a
    #                                       frictionloss of a few N).
    #   scene["joints"] = {"w_slide": 40}   where slide and hinge joints of your MJCF start (mm, degrees; left out:
    #                                       at MJCF's 0)
    #   scene["cameras"] = {"depth": {"noise_px": 0.1}, "scene": {"depth": {"table_holes": None}},
    #                       "left_wrist": {"pose_error": {"shift_mm": 3, "turn_deg": 1}}}
    #                                       the cameras. "depth": the depth cameras' error model, for all of them,
    #                                       or under "scene", "left_wrist", "right_wrist" for that one. Its
    #                                       parameters, each of which can be left out (the simulator's own value):
    #                                         noise_px (0.18): the stereo matching noise in pixels of disparity;
    #                                           the depth's noise is noise_px * z^2 / (focal_px * baseline_mm)
    #                                         noise_smooth (0.9), noise_smooth_px (8): the part of the noise that
    #                                           is smooth over that many pixels (the rest is pixel to pixel)
    #                                         scale (0.005): a depth scale error per camera and episode, up to it
    #                                         fatten_px (4), fatten_step_mm (10): a nearer surface spreads that
    #                                           many pixels over a farther one where the depth steps by more
    #                                         flying (0.03), flying_step_mm (20): the part of the pixels at such a
    #                                           step that get a depth between its two sides
    #                                         dark_below (0.2), dark_holes (0.08), blob_px (4): objects darker than
    #                                           that (0..1) lose that part of their pixels, in blobs of that size
    #                                         table_holes ([0.2, 1100, 1.0, 0.6]) or None: the scene camera on the
    #                                           table: the part without depth up to a distance (mm), how much
    #                                           more per metre beyond it, the most
    #                                         dropout (0.002): single pixels without depth
    #                                         left_band_px (30) or None: the blind band at the image's left is
    #                                           focal_px * baseline_mm / z + this many pixels wide
    #                                         shadow (True): no depth where the second imager does not see
    #                                         min_mm (275): nothing nearer than this
    #                                         focal_px (625), baseline_mm (55): the depth sensor's own
    #                                       "ideal": True under a camera: its depth as the simulator renders it,
    #                                       without any of this (for a model of your own, in robot()'s look).
    #                                       "pose_error" under a camera: its TRUE pose differs from the one its
    #                                       frames give (eye, R) by {"shift_mm": 5, "turn_deg": 1} in a direction
    #                                       drawn per episode (the simulator's own: scene 5 mm and 1 deg, wrists
    #                                       3 mm and 1 deg), or with three numbers each by exactly that.
    #                                       frame.depth_report(), in a program, gives numbers that describe a
    #                                       frame's depth as a sensor's output: the part with depth, the blind
    #                                       band, the holes, the noise by distance.
    #   scene["table"] = {"height_mm": 15, "rgb": [205, 165, 115], "pattern": "plain"}
    #                                       another table: its top at z = height_mm in the robot's frame (-40..40; the
    #                                       usual one is at 0: the robot and its cameras stay where they are, the
    #                                       table and what is on it are that much higher or lower, and positions in
    #                                       your MJCF count from the table's top); its colour ("rgb", or "color":
    #                                       one of black, dark grey, light grey, white, light wood, dark wood, green, blue, beige, red brown); "pattern": plain, or
    #                                       checker, stripes, speckle, wood, blotches in "rgb2" / "color2", with "size_mm" (the
    #                                       checker's square, the stripes' width, ...) and "angle_deg". What is left
    #                                       out is the usual table's: plain dark grey at 0.
    # rng: a numpy Generator, the same for the same scene and world: draw from it everything that varies.
    return scene

def robot(robot, rng, kit):
    # -> the robot the PROGRAM gets in this world. `robot` is the simulator's own (the interface above). kit.Robot is
    # a class that passes every call on to it (as self.robot): subclass it, override what your world does
    # differently, return an instance. kit.frame(f, rgb=None, depth=None) is the Frame f with other pixels (depth in
    # mm, nan where there is none).
    calls = {}
    class Counting(kit.Robot):
        def move(self, arm, xyz, dir_a, dir_b, stop_on_contact=False):
            calls["move"] = calls.get("move", 0) + 1
            print("move", calls["move"], "to", xyz)
            return self.robot.move(arm, xyz, dir_a, dir_b, stop_on_contact)   # or another move, another answer
        def look(self, cameras=None):
            calls["look"] = calls.get("look", 0) + 1
            out = self.robot.look(cameras)
            for name, f in out.items():
                out[name] = kit.frame(f, depth=f.depth.copy())               # or other pixels
            return out
    return Counting(robot)

Everything a program calls can be overridden: look, state, move, turn, home, wait, time_s, set_gripper, segment,
show, truth, judge. What your world's robot returns is what the program sees; what the simulator really did is
what the "#" trace of output.txt shows and what the judge judges. A world keeps the program's rules (the same
imports, no files); what it prints goes to output.txt. If a world fails (an exception, a scene outside the bounds,
a scene the simulator cannot build) the program is not run and the episode's result says "world: ...".

A world that sets the task has these two functions together:

def goal(scene, rng):
    # -> the task's text: what the program gets as `task` in this world, as its task text. Say in it what
    # is asked and what a program needs to know of the scene, and nothing of how you built it: the program
    # has only this text and the robot.
    return "Task: open the drawer of the cabinet on the table by at least 80 mm, then let go of it. ..."

def success(state):
    # -> True or False, or a dict with "ok" and whatever else you measured (the result shows it): YOUR check of that
    # task, called on the TRUE state when the program has ended and the scene has been at rest for 1 s. state has:
    #   state.truth()                     the TRUE positions (mm) and your joints (mm, degrees), as robot.truth()
    #   state.start                       the same at the start of the episode
    #   state.pos(name), state.rot(name)  a body's position (mm) and its axes (3 x 3: the columns are its x, y, z)
    #   state.joint(name)                 a slide or hinge joint of your MJCF (mm, degrees)
    #   state.points(name)                each body's place (mm) of a composite (a soft body, loose particles)
    #   state.speed(name)                 a body's speed (mm/s)
    #   state.touching(name)              {"robot": {arm: [parts]}, "others": [bodies]}: what touches that body
    #   state.held(name)                  a hand touches it and nothing else does
    #   state.scene, state.ended, state.time_s   the scene, how the program ended, the robot time
    # Bodies are named as in truth().
    open_mm = state.joint("w_slide")
    return {"ok": open_mm >= 80 and not state.touching("w_drawer")["robot"], "open_mm": open_mm}

In such a world an episode's SUCCESS or FAILED is your success(): the result says so and gives what it returned, and robot.judge() in practice returns your check of the
state at that moment. Nothing checks your check but you, and the people who read the results rely on it. So make it
strict (the end state that the task's text asks for, measured on the true state, the robot's hands off unless the
task is to hold), and test it both ways: a program that does nothing must FAIL, and the final image of a success
must show the task done. Say in the world's note what its task is.

No real episode of this task exists yet: nobody has measured the real things for you. So a world here is your best
account of a real table: give the bodies the sizes, weights and frictions that such things have, vary with the rng
what you cannot know and what the task says can differ, and say in a world's note what it holds, what varies and
over which ranges.

Your result is the version you submitted last TOGETHER WITH THE WORLD that sets the task: the final submit names it
(--world K; it must have goal() and success()). After the session that version is run in that world, without you,
on scenes with new seeds, where the program has the cameras and nothing of the simulator's state. The episodes are
filmed, and the people who run the robot judge them by your success() first and then by what they see; every
version you saved can be run the same way, to see how the skill came about. After that the skill is to run on the
REAL robot, on a real table with real things. So:
- the world must cover what the task asks the skill to be robust to, widely, and not be easier than a real table;
- its check must be strict and honest (the end state the task's text asks for, measured on the true state): a task
  that the world makes easy is a poor result however many episodes succeed;
- the program must not lean on what only your world has: exact sizes, colours, the names of bodies, the truth.
So make the skill reliable over all of that first; then fast.

When you are done, your final submit (--final) must carry --summary FILE: a file of yours with your own summary of this session, for the
people who run the robot. Describe the task as you built it (the bodies, their sizes, weights and frictions, what
varies and over which ranges, what success() measures), what the skill does step by step, what you tested and how it
came out (by world and by kind of variation), where it still fails, what you expect on the real robot, what should
be measured there first, and what stays uncertain. Plain text, a page or two.

The session's budget is $50 of model usage (as the model's API would charge it): your replies (the thinking included) and the conversation so far, which
is read again at every turn, are what costs; simulation is free. Every output of the command ends with what is spent. So run
several scenes at once, read outputs selectively, and prefer editing your files to writing a whole file
again. Submit a version (with its world) as soon as it is better than the one submitted before: when the budget
runs out the session ends without warning and the last submitted version counts (nothing if none). When you are
satisfied, call submit with --final.
