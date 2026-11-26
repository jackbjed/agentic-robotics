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

    PRACTICE SUB-COMMAND ...

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

The real robot. Versions of the program have been run on the real robot, by an operator who watched each episode and
gave its verdict. Those episodes are yours to study:

list_real_episodes [NAME ...]  without names: the real robot (what it is, and how it answers a program's commands)
                              and one line per episode (the version that ran, the operator's verdict and note, the
                              setup, which setting of the moves until contact it ran with). With names (at most 4):
                              each one in full: its moves until contact as the program saw them and as the
                              arm's own server recorded its guarded lines, its looks, its model calls (robot.ask:
                              the question, the answer), and the names of its images
read_real_output --episode NAME [--start-line N] [--max-lines N] [--contains TEXT] [--no-trace]
                              a real episode's output: what the program printed and, on lines starting with "#", every
                              robot command with its result and the arm's MEASURED tool position after it (the real
                              robot has no truth)
view_real_images --episode NAME --names NAME ...
                              images of a real episode (written into pictures/, at most 6 a call): look_NN_<camera> (the colour image that camera gave the
                              program at its NN-th look), look_NN_<camera>_depth (that look's depth as a picture:
                              near red, far blue, the scale on top; black = NO depth), look_NN_<camera>_nodepth (the
                              colour image with the pixels that had no depth in magenta), shown_... (what the
                              program handed over with robot.show), asked_NN_k (the k-th image of the program's
                              NN-th robot.ask), and video_scene_<t>s / video_wrist_<t>s (frames of the two cameras'
                              videos, t the rig's time)
read_real_depth --episode NAME --look LOOK [--u0 N] [--v0 N] [--u1 N] [--v1 N]
                              the depth a real camera gave at a look, as numbers: the values (mm along the camera's
                              axis, as a Frame's depth; 0 = no depth) on a grid over a window of pixels, and the
                              window's statistics; with the camera's intrinsics

You cannot run anything on the real robot in this session: the practice is in the simulator, as before. The simulator
is the one this skill was developed in: where the real robot behaves differently, the simulator does not show it.

Your result is the version you submitted last. The operator will run it on the real robot again, on scenes like
those of the real episodes (the same kinds of objects, set out anew), and it is judged there first. It is also run
on the simulated final evaluation described above, where it should do no worse than v63 did.

When you are done, your final submit (--final) must carry --summary FILE: a file of yours with your own summary of this session, for the
people who run the robot. Say what you learned from the real episodes (name them), what you changed in the skill
and why, what you tested and how it came out, what you expect on the real robot, and what stays uncertain. Plain
text, a page or so.

A world here is your best
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

The session's budget is $25 of model usage (as the model's API would charge it): your replies (the thinking included) and the conversation so far, which
is read again at every turn, are what costs; simulation is free. Every output of the command ends with what is spent. So run
several scenes at once, read outputs selectively, and prefer editing your files to writing a whole file
again. Submit a version (with its world) as soon as it is better than the one submitted before: when the budget
runs out the session ends without warning and the last submitted version counts (nothing if none). When you are
satisfied, call submit with --final.


===== THE FIRST MESSAGE =====

THE SKILL TO MAKE: put the cylinder on the stick.

On the table there are a cylinder with a hole through it and a stand with one vertical stick. Pick up the
cylinder and thread it onto the stick, so that the stick passes through its hole, and let go of it: at the end
the cylinder must be on the stick, the robot not touching it, and the stand upright. Then open the gripper and
return it to its initial pose. The hole can be as little as 1.5 mm wider than the stick.

The program should be robust to:
- different cylinders and stands, with different physical properties: visual appearances, shapes, sizes,
  colours, frictions, densities and other physical parameters;
- different spatial locations and configurations, and different spatial arrangements of the objects;
- the height at which the cylinder and the stand are;
- the presence of distractors;
- different table heights.

You receive no external test cases and no assets: no cylinders and no stands, only the robot, its cameras and
the table. With worlds of your own you create your own test cases and assets to make the skill.

Versions of this skill have now been run on the real robot; the episodes are yours to study. Specialize the skill to this real table and to this stand and its stick, the ones in the episodes: it no longer has to handle other stands or sticks. It must still handle different cylinders, and different heights of the cylinder and of the stand. Make it reliable on the real robot first, then as fast as you can in the rig's time.

Nothing is given for it but the robot, its cameras and the table: no objects, no assets, no test cases, no task in
the simulator. You make all of that yourself, as WORLDS (described below): what is on the table (bodies, soft
bodies, loose things, marks), the task's text, and the check that says whether an episode did the task. Then you
write the skill: one program run(robot, task) that does the task in your worlds' episodes from the cameras and the
arm's own measurements only, as every program here. The robot is the left arm alone; the other arm is not to be
used, and the simulator refuses it.

This session continues an earlier one, which ended with v63 as its result. You keep its versions (read_program),
its scenes and all its episode results (read_output, view_images), and the notes you wrote in it.

The versions:
v0 (42 lines): probe: segmentation phrases, truth | practice: scene s1@w0 FAIL 0.0 s, scene s2@w0 FAIL 0.0 s, scene s3@w0 FAIL 0.0 s, scene s4@w0 FAIL 0.0 s, scene s1@w1 FAIL 0.0 s, scene s2@w1 FAIL 0.0 s, scene s3@w1 FAIL 0.0 s, scene s4@w1 FAIL 0.0 s, scene s1@w2 FAIL 0.0 s, scene s2@w2 FAIL 0.0 s, scene s3@w2 FAIL 0.0 s, scene s4@w2 FAIL 0.0 s, scene s1@w3 FAIL 2.1 s, scene s2@w3 FAIL 2.1 s, scene s3@w3 FAIL 2.1 s, scene s4@w3 FAIL 2.1 s
v1 (43 lines): probe2: wrist survey from z=560, segmentation phrases | practice: scene s1@w3 FAIL 4.8 s, scene s2@w3 FAIL 4.8 s, scene s3@w3 FAIL 4.8 s, scene s4@w3 FAIL 4.8 s
v2 (115 lines): mech dev: truth-based positions; squeeze stick and cylinder, touch-down, insert | practice: scene s1@w3 ok 67.2 s, scene s3@w3 ok 69.3 s
v3 (432 lines): v3: full skill: wrist survey (height residual + SAM), close-ups, squeeze stick/cyl, insert w/ search | practice: scene s1@w3 FAIL 7.0 s, scene s2@w3 FAIL 52.0 s, scene s3@w3 FAIL 65.7 s, scene s4@w3 FAIL 70.0 s, scene s5@w3 FAIL 0.0 s, scene s6@w3 FAIL 54.7 s, scene s7@w3 ok 70.8 s, scene s8@w3 ok 82.2 s
v4 (500 lines): v4: ring verified by close-up hole, stick by local rise; artifact masking | practice: scene s1@w4 FAIL 13.3 s, scene s2@w4 FAIL 182.0 s, scene s3@w4 FAIL 13.0 s, scene s4@w4 FAIL 13.5 s, scene s5@w4 ok 79.6 s, scene s6@w4 FAIL 13.9 s, scene s7@w4 ok 74.8 s, scene s8@w4 FAIL 14.7 s
v5 (513 lines): v5: debug stick detection | practice: scene s1@w4 FAIL 13.3 s, scene s3@w4 FAIL 13.0 s
v6 (513 lines): v6: stick opening disc 44mm, annulus 18-32 p60; ring gripped low | practice: scene s1@w4 ok 68.8 s, scene s2@w4 FAIL 183.0 s, scene s3@w4 ok 70.1 s, scene s4@w4 FAIL 13.5 s, scene s5@w4 ok 108.8 s, scene s6@w4 ok 95.9 s, scene s7@w4 ok 74.8 s, scene s8@w4 ok 101.3 s
v7 (714 lines): v7: survey from (-420,60,600); stick squeezed low a-b-a; put-back & retry when the search fails | practice: scene s1@w4 ok 76.9 s, scene s2@w4 ok 86.4 s, scene s3@w4 ok 84.9 s, scene s4@w4 ok 80.9 s, scene s5@w4 ok 90.9 s, scene s6@w4 ok 98.6 s, scene s7@w4 ok 82.7 s, scene s8@w4 ok 92.8 s, scene s9@w4 ok 149.9 s, scene s10@w4 ok 80.2 s, scene s11@w4 FAIL 22.5 s, scene s12@w4 ok 117.9 s, scene s13@w4 ok 336.2 s, scene s14@w4 FAIL 85.4 s, scene s15@w4 ok 89.4 s, scene s16@w4 ok 87.0 s
v8 (23 lines): v8: squeeze test on fixed / heavy sticks | practice: scene s1@w5 FAIL 57.8 s
v9 (774 lines): v9: iterative centring squeeze (arm compliance), grasp consistency check + re-look, ring hole by centre depth | practice: scene s9@w4 ok 97.8 s, scene s11@w4 FAIL 22.5 s, scene s12@w4 ok 81.0 s, scene s13@w4 ok 100.3 s, scene s14@w4 ok 112.7 s, scene s17@w4 ok 80.5 s, scene s18@w4 ok 104.0 s, scene s19@w4 ok 81.4 s
v10 (865 lines): v10: finger clearance (height grid) chooses closing dirs; better hole test + weak fallback | practice: scene s1@w6 ok 83.6 s, scene s2@w6 ok 93.1 s, scene s3@w6 FAIL 41.7 s, scene s4@w6 ok 81.4 s, scene s5@w6 FAIL 519.7 s, scene s6@w6 ok 102.3 s, scene s7@w6 ok 85.5 s, scene s8@w6 ok 95.0 s
v11 (897 lines): v11: stick candidates verified by pedestal + round widths; orientation fallbacks; robust clearance; faster search | practice: scene s3@w6 FAIL 50.2 s, scene s5@w6 ok 145.4 s, scene s9@w6 ok 83.8 s, scene s10@w6 ok 82.9 s, scene s11@w6 ok 132.4 s, scene s12@w6 ok 80.2 s, scene s13@w6 FAIL 61.4 s, scene s14@w6 ok 108.5 s
v12 (925 lines): v12: squeeze_at tries other closing angles when a move/turn is refused | practice: scene s3@w6 ok 82.5 s, scene s13@w6 FAIL 66.8 s, scene s15@w6 ok 102.9 s, scene s16@w6 ok 107.9 s, scene s17@w6 FAIL 77.0 s, scene s18@w6 ok 104.4 s, scene s19@w6 ok 90.7 s, scene s20@w6 ok 94.8 s
v13 (926 lines): v13: judge squeeze width only once centred | practice: scene s13@w6 ok 109.8 s, scene s17@w6 FAIL 77.0 s
v14 (929 lines): v14: blocked descent -> next closing angle | practice: scene s17@w6 ok 84.0 s, scene s1@w6 ok 89.9 s, scene s2@w6 ok 114.2 s, scene s4@w6 ok 95.6 s, scene s5@w6 ok 141.8 s, scene s6@w6 ok 113.0 s, scene s7@w6 ok 85.5 s, scene s8@w6 ok 103.1 s, scene s9@w6 ok 83.8 s, scene s10@w6 ok 205.7 s, scene s11@w6 ok 132.4 s, scene s12@w6 ok 80.2 s, scene s14@w6 ok 108.5 s, scene s15@w6 ok 102.9 s, scene s16@w6 ok 107.9 s, scene s18@w6 ok 104.4 s, scene s19@w6 ok 90.7 s, scene s21@w6 ok 93.9 s, scene s22@w6 ok 93.4 s, scene s23@w6 ok 91.0 s, scene s24@w6 FAIL 102.2 s, scene s25@w6 ok 100.6 s, scene s26@w6 ok 158.5 s, scene s27@w6 ok 131.0 s, scene s28@w6 ok 91.0 s
v15 (939 lines): v15: correct the loaded arm's sag over the stick (closed loop on measured TCP) | practice: scene s24@w6 ok 94.9 s, scene s29@w6 ok 115.9 s, scene s30@w6 ok 92.9 s, scene s31@w6 ok 88.0 s, scene s32@w6 ok 103.7 s, scene s26@w6 ok 158.6 s, scene s27@w6 ok 134.5 s, scene s10@w6 ok 83.4 s, scene s33@w6 ok 110.9 s, scene s34@w6 ok 102.2 s, scene s35@w6 ok 88.9 s, scene s36@w6 ok 85.6 s, scene s37@w6 ok 103.6 s, scene s38@w6 ok 105.4 s, scene s39@w6 ok 85.2 s, scene s40@w6 ok 109.8 s, scene s1@w7 ok 83.5 s, scene s2@w7 ok 123.4 s, scene s3@w7 ok 101.2 s, scene s4@w7 FAIL 15.3 s, scene s5@w7 ok 88.9 s, scene s6@w7 ok 138.7 s, scene s7@w7 ok 83.0 s, scene s8@w7 ok 112.6 s
v16 (944 lines): v16: stick candidates: drop ring-wall ones and duplicates before taking the top 7 | practice: scene s4@w7 ok 83.8 s, scene s9@w7 ok 93.9 s, scene s10@w7 ok 86.6 s, scene s11@w7 ok 93.3 s, scene s12@w7 ok 80.0 s, scene s13@w7 ok 100.4 s, scene s14@w7 ok 104.0 s, scene s15@w7 ok 104.5 s, scene s16@w7 ok 93.1 s, scene s17@w7 ok 82.1 s, scene s18@w7 ok 104.9 s, scene s19@w7 ok 95.1 s, scene s20@w7 ok 92.2 s, scene s21@w7 ok 121.5 s, scene s22@w7 ok 81.4 s, scene s23@w7 ok 113.8 s, scene s24@w7 ok 84.8 s, scene s25@w7 ok 123.2 s, scene s26@w7 ok 95.0 s, scene s27@w7 FAIL 323.1 s, scene s28@w7 FAIL 328.2 s, scene s29@w7 ok 100.6 s, scene s30@w7 ok 99.6 s, scene s31@w7 ok 79.7 s
v17 (954 lines): v17: stricter hole test; stick candidates ranked by plate fraction; stick must be round (1.5) and fit the seen hole | practice: scene s27@w7 ok 97.9 s, scene s28@w7 ok 116.3 s, scene s32@w7 ok 104.7 s, scene s33@w7 ok 127.7 s, scene s34@w7 ok 93.9 s, scene s35@w7 FAIL 116.2 s, scene s36@w7 ok 101.1 s, scene s37@w7 ok 89.5 s
v18 (967 lines): v18: finish(): lift clear, go to the start pose by a straight move, then home | practice: scene s35@w7 ok 131.3 s, scene s38@w7 ok 123.5 s, scene s39@w7 ok 108.6 s, scene s40@w7 ok 123.4 s, scene s41@w7 ok 115.6 s, scene s42@w7 ok 110.5 s, scene s43@w7 ok 86.7 s, scene s44@w7 ok 107.1 s, scene s1@w7 ok 91.3 s, scene s2@w7 FAIL 112.1 s, scene s3@w7 ok 111.9 s, scene s4@w7 ok 90.0 s, scene s5@w7 ok 95.0 s, scene s6@w7 ok 108.8 s, scene s7@w7 ok 89.0 s, scene s8@w7 ok 120.4 s
v19 (972 lines): v19: camera bias from a second look at the squeezed stick (the stand may have been dragged) | practice: scene s2@w7 ok 139.5 s, scene s9@w7 ok 112.1 s, scene s10@w7 ok 104.2 s, scene s11@w7 ok 125.8 s, scene s12@w7 ok 102.9 s, scene s13@w7 ok 115.0 s, scene s14@w7 ok 107.3 s, scene s15@w7 ok 116.7 s, scene s16@w7 ok 106.5 s, scene s17@w7 ok 131.7 s, scene s18@w7 FAIL 99.5 s, scene s19@w7 ok 113.1 s, scene s20@w7 ok 99.4 s, scene s21@w7 ok 124.0 s, scene s22@w7 ok 94.4 s, scene s23@w7 ok 126.0 s
v20 (989 lines): v20: centring steps by the width excess on later passes (stiff arm / fixed stand) | practice: scene s18@w7 ok 105.9 s, scene s24@w7 ok 104.2 s, scene s25@w7 ok 108.2 s, scene s26@w7 ok 143.8 s, scene s27@w7 ok 98.8 s, scene s28@w7 ok 130.4 s, scene s29@w7 FAIL 128.3 s, scene s30@w7 ok 107.6 s
v21 (996 lines): v21: close-ups always with the camera on the survey side (bias consistency); width-step only for the stick | practice: scene s29@w7 ok 119.7 s, scene s31@w7 ok 92.8 s, scene s32@w7 ok 116.9 s, scene s33@w7 ok 121.1 s, scene s34@w7 ok 106.5 s, scene s35@w7 ok 136.3 s, scene s36@w7 ok 105.3 s, scene s37@w7 ok 104.1 s, scene s1@w7 ok 93.5 s, scene s2@w7 ok 139.5 s, scene s3@w7 ok 92.5 s, scene s4@w7 ok 94.1 s, scene s5@w7 ok 102.3 s, scene s6@w7 ok 113.8 s, scene s7@w7 ok 93.5 s, scene s8@w7 ok 118.3 s, scene s9@w7 ok 112.1 s, scene s10@w7 ok 104.2 s, scene s11@w7 ok 126.0 s, scene s12@w7 ok 102.9 s, scene s13@w7 ok 110.5 s, scene s14@w7 ok 107.3 s, scene s15@w7 ok 116.7 s, scene s16@w7 ok 106.5 s, scene s17@w7 ok 131.7 s, scene s18@w7 ok 105.9 s, scene s19@w7 ok 113.1 s, scene s20@w7 ok 99.4 s, scene s21@w7 ok 122.7 s, scene s22@w7 ok 94.4 s, scene s23@w7 ok 126.0 s, scene s24@w7 ok 104.2 s
v22 (997 lines): v22: ring grasp height adapts to short rings | practice: scene s1@w8 ok 93.3 s, scene s2@w8 ok 108.7 s, scene s3@w8 ok 98.3 s, scene s4@w8 FAIL 76.8 s, scene s5@w8 ok 105.6 s, scene s6@w8 ok 127.8 s, scene s7@w8 ok 108.5 s, scene s8@w8 ok 118.4 s
v23 (1000 lines): v23: close-up poses go higher when refused | practice: scene s4@w9 FAIL 133.4 s, scene s9@w9 ok 113.4 s, scene s10@w9 ok 106.9 s, scene s11@w9 ok 124.7 s, scene s12@w9 ok 96.1 s, scene s13@w9 ok 127.5 s, scene s14@w9 ok 105.2 s, scene s15@w9 ok 104.1 s, scene s1@w11 ok 93.3 s, scene s2@w11 ok 108.7 s, scene s3@w11 ok 98.4 s, scene s4@w11 ok 97.4 s, scene s5@w11 ok 105.6 s, scene s6@w11 ok 161.6 s, scene s7@w11 ok 108.5 s, scene s8@w11 ok 118.4 s, scene s16@w11 ok 109.8 s, scene s17@w11 ok 125.6 s, scene s18@w11 ok 101.4 s, scene s19@w11 ok 109.8 s, scene s20@w11 ok 98.5 s, scene s21@w11 ok 128.1 s, scene s22@w11 ok 95.3 s, scene s23@w11 ok 125.9 s, scene s24@w11 ok 125.3 s, scene s25@w11 ok 107.9 s, scene s26@w11 ok 143.5 s, scene s27@w11 ok 97.4 s, scene s28@w11 ok 126.0 s, scene s29@w11 ok 113.0 s, scene s30@w11 ok 140.9 s, scene s31@w11 ok 97.7 s, scene s32@w11 ok 111.2 s, scene s33@w11 ok 127.3 s, scene s34@w11 ok 109.7 s, scene s35@w11 ok 131.7 s, scene s36@w11 ok 98.7 s, scene s37@w11 ok 102.9 s, scene s38@w11 ok 129.7 s, scene s39@w11 ok 113.8 s, scene s1@w12 ok 93.3 s, scene s2@w12 ok 107.8 s, scene s3@w12 FAIL 48.9 s, scene s4@w12 FAIL 356.6 s, scene s5@w12 ok 109.1 s, scene s6@w12 ok 112.3 s, scene s7@w12 ok 116.9 s, scene s8@w12 ok 121.7 s
v24 (6 lines): idle: does nothing, prints truth (checks scene stability and that success() fails) | practice: scene s4@w9 FAIL 2.0 s, scene s1@w9 FAIL 2.0 s, scene s2@w9 FAIL 2.0 s, scene s3@w9 FAIL 2.0 s, scene s4@w10 FAIL 2.0 s, scene s5@w10 FAIL 2.0 s, scene s6@w10 FAIL 2.0 s, scene s7@w10 FAIL 2.0 s, scene s8@w10 FAIL 2.0 s, scene s9@w10 FAIL 2.0 s, scene s10@w10 FAIL 2.0 s, scene s11@w10 FAIL 2.0 s, scene s4@w11 FAIL 2.0 s, scene s4@w13 FAIL 2.0 s, scene s9@w13 FAIL 2.0 s, scene s10@w13 FAIL 2.0 s, scene s11@w13 FAIL 2.0 s, scene s12@w13 FAIL 2.0 s, scene s13@w13 FAIL 2.0 s, scene s14@w13 FAIL 2.0 s, scene s15@w13 FAIL 2.0 s, scene s4@w14 FAIL 2.0 s, scene s11@w14 FAIL 2.0 s, scene s1@w14 FAIL 2.0 s, scene s3@w14 FAIL 2.0 s, scene s5@w14 FAIL 2.0 s, scene s9@w14 FAIL 2.0 s, scene s12@w14 FAIL 2.0 s, scene s13@w14 FAIL 2.0 s, scene s1@w15 FAIL 2.0 s, scene s2@w15 FAIL 2.0 s, scene s3@w15 FAIL 2.0 s, scene s4@w15 FAIL 2.0 s, scene s1@w21 FAIL 2.0 s, scene s2@w21 FAIL 2.0 s
v25 (1001 lines): v25: stick candidate score: taller counts more, tiny patches penalised, no SAM-ring penalty | practice: scene s1@w13 ok 93.3 s, scene s2@w13 ok 107.8 s, scene s3@w13 ok 121.2 s, scene s4@w13 ok 94.0 s, scene s5@w13 ok 109.1 s, scene s6@w13 ok 116.7 s, scene s7@w13 ok 116.9 s, scene s8@w13 ok 110.2 s, scene s9@w13 ok 123.9 s, scene s10@w13 ok 106.9 s, scene s11@w13 FAIL 249.2 s, scene s12@w13 ok 124.6 s, scene s13@w13 ok 136.3 s, scene s14@w13 ok 115.2 s, scene s15@w13 ok 104.1 s, scene s16@w13 ok 113.5 s
v26 (1010 lines): v26: ring candidates ranked (hole evidence + survey score); a failed grasp moves on to the next candidate | practice: scene s11@w13 ok 392.8 s, scene s17@w13 ok 153.6 s, scene s18@w13 ok 104.7 s, scene s19@w13 ok 151.0 s, scene s20@w13 ok 112.8 s, scene s21@w13 ok 113.9 s, scene s22@w13 ok 91.6 s, scene s23@w13 ok 114.6 s
v27 (1010 lines): v27: at most 2 grasp attempts per ring candidate | practice: scene s11@w13 ok 255.0 s, scene s24@w13 FAIL 107.2 s, scene s25@w13 ok 111.2 s, scene s26@w13 ok 131.0 s, scene s27@w13 ok 93.6 s, scene s28@w13 ok 129.6 s, scene s29@w13 ok 113.7 s, scene s30@w13 ok 110.8 s
v28 (22 lines): probe: fingertip touch on the table with stop_on_contact | practice: scene s1@w13 FAIL 26.1 s, scene s2@w13 FAIL 26.1 s
v29 (22 lines): probe: fingertip touch on the table (target z 12.5) | practice: scene s1@w13 FAIL 28.8 s, scene s3@w13 FAIL 28.8 s
v30 (1011 lines): v30: larger height margins for the grasp and the release (wrist camera heights ~5 mm off) | practice: scene s24@w13 FAIL 121.4 s, scene s1@w13 ok 94.6 s, scene s2@w13 ok 107.7 s, scene s3@w13 ok 121.0 s, scene s31@w13 ok 97.1 s, scene s32@w13 ok 111.0 s, scene s33@w13 ok 127.2 s, scene s34@w13 ok 109.4 s
v31 (1014 lines): v31: release the ring once 15 mm onto the stick (slides down); keep the sag-corrected xy after a search | practice: scene s24@w13 FAIL 119.9 s, scene s35@w13 ok 129.7 s, scene s36@w13 ok 105.9 s, scene s37@w13 ok 96.7 s, scene s38@w13 ok 144.5 s, scene s39@w13 ok 105.2 s, scene s40@w13 ok 255.3 s, scene s41@w13 ok 121.1 s
v32 (1018 lines): v32: gentle release (loosen, wait, open) | practice: scene s24@w13 FAIL 122.9 s, scene s40@w13 ok 258.3 s, scene s42@w13 ok 108.2 s, scene s43@w13 ok 89.3 s, scene s44@w13 ok 107.2 s, scene s5@w13 ok 107.3 s, scene s6@w13 ok 113.4 s, scene s7@w13 ok 96.5 s, scene s9@w13 ok 127.0 s, scene s10@w13 ok 107.3 s, scene s11@w13 FAIL 489.7 s, scene s12@w13 ok 106.3 s, scene s13@w13 ok 135.7 s, scene s14@w13 ok 126.7 s, scene s15@w13 ok 102.3 s, scene s16@w13 ok 112.5 s
v33 (1036 lines): v33: travel moves (up, across, down) between places | practice: scene s11@w13 FAIL 151.0 s, scene s17@w13 ok 153.3 s, scene s18@w13 ok 115.4 s, scene s19@w13 ok 171.4 s, scene s20@w13 ok 105.9 s, scene s21@w13 ok 126.0 s, scene s22@w13 ok 110.0 s, scene s23@w13 ok 135.5 s, scene s11@w14 ok 235.9 s, scene s24@w14 ok 147.8 s, scene s1@w14 FAIL 110.1 s, scene s2@w14 ok 119.6 s, scene s3@w14 FAIL 67.6 s, scene s4@w14 ok 103.7 s, scene s5@w14 ok 114.1 s, scene s6@w14 ok 134.1 s, scene s1@w15 ok 110.1 s, scene s3@w15 FAIL 67.6 s, scene s11@w15 ok 232.6 s, scene s24@w15 FAIL 146.8 s, scene s4@w15 ok 103.7 s, scene s7@w15 ok 108.5 s, scene s8@w15 ok 113.3 s, scene s9@w15 ok 135.9 s
v34 (1036 lines): v34: stick close-up opening 40 mm (thick sticks) | practice: scene s3@w15 ok 111.2 s, scene s24@w15 ok 143.5 s, scene s2@w15 ok 119.6 s, scene s5@w15 ok 114.2 s, scene s6@w15 ok 134.1 s, scene s10@w15 ok 119.4 s, scene s12@w15 ok 118.0 s, scene s13@w15 ok 150.1 s, scene s14@w15 ok 120.8 s, scene s15@w15 ok 121.4 s, scene s16@w15 ok 135.7 s, scene s17@w15 ok 145.6 s, scene s18@w15 ok 115.4 s, scene s19@w15 ok 150.1 s, scene s20@w15 ok 105.9 s, scene s21@w15 ok 119.4 s, scene s22@w15 ok 110.5 s, scene s23@w15 ok 156.6 s, scene s25@w15 ok 114.6 s, scene s26@w15 ok 140.4 s, scene s27@w15 ok 89.0 s, scene s28@w15 ok 128.0 s, scene s29@w15 ok 120.7 s, scene s30@w15 ok 117.4 s, scene s31@w15 ok 106.3 s, scene s32@w15 ok 125.1 s, scene s33@w15 ok 133.5 s, scene s34@w15 ok 116.5 s, scene s35@w15 ok 156.1 s, scene s36@w15 ok 122.2 s, scene s37@w15 ok 109.8 s, scene s38@w15 ok 169.5 s, scene s45@w15 ok 95.9 s, scene s46@w15 ok 144.8 s, scene s47@w15 ok 116.2 s, scene s48@w15 ok 106.0 s, scene s49@w15 ok 124.2 s, scene s50@w15 ok 201.4 s, scene s51@w15 ok 113.8 s, scene s52@w15 ok 130.6 s, scene s53@w15 ok 134.6 s, scene s54@w15 ok 108.0 s, scene s55@w15 ok 112.2 s, scene s56@w15 ok 97.5 s, scene s39@w15 ok 118.7 s, scene s40@w15 ok 127.5 s, scene s41@w15 ok 156.2 s, scene s42@w15 ok 109.7 s
v35 (1054 lines): v35: = v34 tidied (header doc, unused_* names for old code); no behaviour change | practice: scene s43@w15 ok 104.5 s, scene s44@w15 ok 119.9 s, scene s1@w15 ok 110.1 s, scene s4@w15 ok 107.1 s, scene s7@w15 ok 108.5 s, scene s8@w15 ok 113.4 s, scene s9@w15 ok 135.9 s, scene s11@w15 ok 235.9 s
v36 (1065 lines): v36: = v35 + on any exception: open, lift clear, go home | practice: scene s45@w15 ok 95.9 s, scene s46@w15 ok 144.8 s, scene s47@w15 ok 116.2 s, scene s48@w15 ok 106.0 s, scene s2@w15 ok 119.6 s, scene s3@w15 ok 111.2 s, scene s5@w15 ok 114.2 s, scene s6@w15 ok 134.1 s, scene s49@w16 ok 124.2 s, scene s50@w16 ok 201.4 s, scene s51@w16 ok 113.8 s, scene s52@w16 ok 130.6 s, scene s53@w16 ok 134.6 s, scene s54@w16 ok 107.1 s, scene s55@w16 ok 112.2 s, scene s56@w16 ok 97.5 s, scene s57@w16 ok 120.8 s, scene s58@w16 ok 127.5 s, scene s59@w16 ok 131.4 s, scene s60@w16 ok 118.3 s, scene s61@w16 ok 116.2 s, scene s62@w16 ok 144.1 s, scene s63@w16 ok 145.5 s, scene s64@w16 ok 125.1 s, scene s65@w16 ok 118.9 s, scene s66@w16 FAIL 62.6 s, scene s67@w16 ok 105.4 s, scene s68@w16 ok 119.7 s, scene s10@w16 ok 119.4 s, scene s12@w16 ok 118.0 s, scene s13@w16 ok 150.1 s, scene s14@w16 ok 120.8 s
v37 (1065 lines): v37: accept thick sticks' close-up patch up to 34 mm (24 mm stick + depth blur) | practice: scene s66@w16 FAIL 62.6 s, scene s15@w16 ok 121.4 s, scene s16@w16 ok 135.7 s, scene s17@w16 ok 145.6 s, scene s18@w16 ok 115.4 s, scene s19@w16 ok 150.1 s, scene s20@w16 ok 105.9 s, scene s21@w16 ok 119.4 s
v38 (1065 lines): v38: accept thick sticks' close-up patch up to 34 mm (24 mm stick + depth blur) [v37 had not changed] | practice: scene s66@w16 ok 108.9 s, scene s22@w16 ok 110.5 s, scene s23@w16 ok 156.6 s, scene s25@w16 ok 114.6 s, scene s26@w16 ok 140.4 s, scene s27@w16 ok 89.0 s, scene s28@w16 ok 128.0 s, scene s29@w16 ok 120.7 s, scene s69@w16 ok 122.5 s, scene s70@w16 ok 150.0 s, scene s71@w16 ok 136.3 s, scene s72@w16 ok 168.9 s, scene s73@w16 ok 129.6 s, scene s74@w16 ok 107.1 s, scene s75@w16 ok 126.9 s, scene s76@w16 ok 104.9 s, scene s77@w16 ok 125.8 s, scene s78@w16 ok 140.2 s, scene s79@w16 ok 118.9 s, scene s80@w16 ok 121.0 s, scene s30@w16 ok 117.4 s, scene s31@w16 ok 106.3 s, scene s32@w16 ok 125.1 s, scene s33@w16 ok 133.5 s, scene s81@w16 ok 108.0 s, scene s82@w16 ok 116.2 s, scene s83@w16 ok 161.2 s, scene s84@w16 ok 226.7 s, scene s85@w16 ok 118.4 s, scene s86@w16 ok 120.1 s, scene s87@w16 ok 123.5 s, scene s88@w16 ok 124.5 s, scene s89@w16 ok 165.2 s, scene s90@w16 ok 171.6 s, scene s91@w16 ok 115.4 s, scene s92@w16 ok 147.3 s, scene s34@w16 ok 116.5 s, scene s35@w16 ok 156.1 s, scene s36@w16 ok 122.2 s, scene s37@w16 ok 109.8 s
v39 (28 lines): probe: survey poses reach/coverage in w17 | practice: scene s1@w17 FAIL 0.9 s, scene s2@w17 FAIL 0.9 s, scene s3@w17 FAIL 0.9 s
v40 (29 lines): probe: survey poses reach/coverage in w17 (lower) | practice: scene s1@w17 FAIL 17.3 s
v41 (820 lines): v41: real-rig skill: survey (-350,60,620); close-ups 380 mm above tops; no stick squeeze; ring centred a/b with open width from vision; put down + re-look = camera bias applied to the stick top; re-grasp; short guarded insert + search | practice: scene s1@w17 ok 223.3 s, scene s2@w17 ok 135.4 s, scene s3@w17 ok 130.6 s, scene s4@w17 ok 199.6 s, scene s5@w17 ok 201.0 s, scene s6@w17 ok 374.0 s, scene s7@w17 ok 131.8 s, scene s8@w17 ok 163.9 s
v42 (828 lines): v42: = v41 + diagnostics (true stick top, ring truth at the bias look); insert loop keeps the best | practice: scene s1@w18 ok 157.5 s, scene s4@w18 ok 198.3 s, scene s5@w18 FAIL 167.8 s, scene s6@w18 ok 145.7 s, scene s7@w18 ok 118.2 s, scene s9@w18 ok 134.2 s, scene s10@w18 ok 143.3 s, scene s11@w18 FAIL 181.1 s
v43 (860 lines): v43: domes are not holes (high fraction); gentle two-step opening (let_go); insert tries other hand turns when refused near the parked arm; correction loop stops on refusal | practice: scene s5@w18 FAIL 166.3 s, scene s6@w18 ok 144.8 s, scene s11@w18 ok 156.7 s, scene s12@w18 ok 151.3 s, scene s13@w18 ok 141.4 s, scene s14@w18 ok 135.0 s, scene s15@w18 ok 144.5 s, scene s16@w18 ok 124.7 s
v44 (865 lines): v44: a hole must show the support through it (lowfin >= 0.1): dark domes are not rings | practice: scene s5@w18 ok 180.0 s, scene s17@w18 ok 186.2 s, scene s18@w18 ok 119.2 s, scene s19@w18 ok 177.0 s, scene s20@w18 ok 178.6 s, scene s21@w18 ok 138.3 s, scene s22@w18 ok 231.1 s, scene s23@w18 ok 142.5 s
v45 (885 lines): v45: faster: no redundant travel moves, hand turned during travel, short guarded segments (grasp: only past the ring top; insert: only the first 10 mm), 2 SAM phrases | practice: scene s6@w18 FAIL 242.9 s, scene s15@w18 ok 114.2 s, scene s22@w18 ok 187.8 s, scene s24@w18 ok 277.3 s, scene s25@w18 ok 149.2 s, scene s26@w18 ok 122.7 s, scene s27@w18 ok 124.7 s, scene s28@w18 ok 178.3 s
v46 (883 lines): v46: = v45 with the whole insertion descent guarded again (a plain part hit a 40 N safety stop when the top read high) | practice: scene s6@w19 ok 124.2 s, scene s22@w19 ok 113.0 s, scene s24@w19 ok 225.3 s, scene s29@w19 ok 136.7 s, scene s30@w19 ok 141.3 s, scene s31@w19 ok 137.8 s, scene s32@w19 FAIL 298.9 s, scene s33@w19 ok 117.5 s, scene s6@w20 ok 121.7 s, scene s24@w20 ok 142.2 s, scene s30@w20 ok 123.5 s, scene s32@w20 ok 120.0 s, scene s34@w20 ok 110.3 s, scene s35@w20 FAIL 160.7 s, scene s36@w20 ok 132.9 s, scene s37@w20 ok 123.4 s
v47 (920 lines): v47: when the camera cannot look at the cylinder from right above (near the parked arm), carry it to a free spot and look there | practice: scene s35@w20 FAIL 160.7 s, scene s11@w20 ok 142.7 s, scene s38@w20 ok 122.5 s, scene s39@w20 ok 153.5 s, scene s40@w20 ok 122.9 s, scene s41@w20 ok 130.9 s, scene s42@w20 ok 107.0 s, scene s43@w20 ok 139.2 s
v48 (923 lines): v48: free spot test tolerant of depth noise; re-grasp first tries the closing angle that worked | practice: scene s35@w20 ok 250.1 s, scene s44@w20 ok 118.0 s, scene s45@w20 ok 115.2 s, scene s46@w20 ok 128.2 s, scene s47@w20 ok 123.5 s, scene s48@w20 ok 132.3 s, scene s49@w20 ok 136.3 s, scene s50@w20 ok 121.3 s
v49 (933 lines): v49: relocation: pick with one close (turns refused near the parked arm), put down at the free spot, centre it there, then look | practice: scene s35@w20 ok 232.9 s, scene s51@w20 ok 112.0 s, scene s52@w20 FAIL 412.7 s, scene s53@w20 ok 117.6 s, scene s54@w20 ok 114.1 s, scene s55@w20 FAIL 127.5 s, scene s56@w20 ok 130.1 s, scene s57@w20 ok 133.7 s
v50 (941 lines): v50: holed candidates always rank first; on platforms grip 3 mm above the seen support, guarded to the end; hold check after the final grasp | practice: scene s52@w20 ok 127.5 s, scene s55@w20 FAIL 265.5 s, scene s58@w20 ok 134.5 s, scene s59@w20 ok 137.4 s, scene s60@w20 FAIL 114.5 s, scene s61@w20 FAIL 335.9 s, scene s62@w20 ok 139.3 s, scene s63@w20 ok 122.7 s
v51 (945 lines): v51: ring candidates: flat/huge ones skipped, stop at the first holed; stuck >5 mm onto the stick = let go there | practice: scene s55@w20 FAIL 131.0 s, scene s60@w20 FAIL 114.5 s, scene s61@w20 FAIL 469.6 s, scene s64@w20 ok 140.0 s, scene s65@w20 ok 132.9 s, scene s66@w20 ok 134.2 s, scene s67@w20 ok 128.7 s, scene s68@w20 ok 133.7 s, scene s55@w21 ok 140.3 s, scene s60@w21 ok 114.3 s, scene s61@w21 ok 126.4 s, scene s6@w21 ok 120.2 s, scene s32@w21 ok 137.1 s, scene s69@w21 ok 233.0 s, scene s70@w21 ok 121.7 s, scene s71@w21 ok 120.8 s, scene s72@w21 ok 114.4 s, scene s73@w21 ok 117.0 s, scene s74@w21 ok 167.9 s, scene s75@w21 ok 131.6 s, scene s76@w21 FAIL 164.9 s, scene s77@w21 ok 102.2 s, scene s78@w21 ok 182.0 s, scene s79@w21 ok 144.0 s
v52 (948 lines): v52: cylinder candidates out of the arm's reach are not looked at | practice: scene s76@w21 ok 161.2 s, scene s80@w21 ok 139.7 s, scene s81@w21 ok 133.6 s, scene s82@w21 ok 123.2 s, scene s83@w21 ok 120.5 s, scene s84@w21 ok 135.6 s, scene s85@w21 ok 114.2 s, scene s86@w21 FAIL 326.4 s
v53 (953 lines): v53: cylinder candidates also from the local rise (on platforms); not at the stick; up to 9 close-ups | practice: scene s86@w21 FAIL 353.3 s, scene s87@w21 ok 147.6 s, scene s88@w21 ok 126.6 s, scene s89@w21 FAIL 172.5 s, scene s90@w21 ok 129.2 s, scene s91@w21 ok 126.9 s, scene s92@w21 ok 135.6 s, scene s93@w21 ok 122.7 s
v54 (955 lines): v54: cylinder candidates must be mostly above their surroundings (median) | practice: scene s86@w21 FAIL 353.3 s, scene s89@w21 FAIL 172.5 s, scene s94@w21 ok 133.0 s, scene s95@w21 ok 136.9 s, scene s96@w21 ok 132.1 s, scene s97@w21 ok 139.0 s, scene s98@w21 ok 127.5 s, scene s99@w21 ok 125.9 s
v55 (968 lines): v55: a look with the gripper turned the other way is not a bias look (relocate); platform candidates only round solid rises > 11 mm; survey hole evidence raises a candidate | practice: scene s86@w21 FAIL 330.4 s, scene s89@w21 FAIL 170.5 s, scene s100@w21 ok 135.5 s, scene s101@w21 ok 116.6 s, scene s102@w21 ok 121.4 s, scene s103@w21 ok 126.4 s, scene s104@w21 ok 122.1 s, scene s93@w21 ok 122.7 s
v56 (979 lines): v56: when every closing angle is refused, home the arm and try them all again; platform candidates by a 75 mm opening | practice: scene s86@w21 FAIL 330.4 s, scene s89@w21 ok 275.4 s, scene s35@w21 ok 244.0 s, scene s52@w21 ok 134.3 s, scene s60@w21 ok 120.1 s, scene s61@w21 ok 236.3 s, scene s55@w21 ok 140.3 s, scene s76@w21 ok 161.2 s
v57 (985 lines): v57: debug: candidates near the true cylinder | practice: scene s86@w21 FAIL 330.4 s
v58 (988 lines): v58: debug: show hf and res2 | practice: scene s86@w21 FAIL 330.4 s
v59 (992 lines): v59: platform candidates: the tallest part of each local-rise blob (a platform rim hung on to it) | practice: scene s86@w21 FAIL 338.1 s, scene s1@w21 ok 137.3 s, scene s2@w21 ok 138.3 s, scene s3@w21 ok 122.1 s, scene s4@w21 ok 127.9 s, scene s5@w21 ok 132.8 s, scene s7@w21 ok 114.0 s, scene s8@w21 FAIL 130.2 s
v60 (1001 lines): v60: no 'partly on' release; search re-seats at the same place first; stick top height corrected by the table seen in the bias look; cylinder rise plausible up to 160 (on platforms) | practice: scene s86@w21 FAIL 319.9 s, scene s8@w21 ok 135.2 s, scene s55@w21 ok 140.3 s, scene s9@w21 ok 127.9 s, scene s10@w21 ok 182.2 s, scene s12@w21 ok 119.0 s, scene s13@w21 ok 135.3 s, scene s14@w21 ok 169.7 s, scene s15@w21 ok 108.0 s, scene s16@w21 ok 122.9 s, scene s17@w21 ok 130.0 s, scene s18@w21 ok 108.9 s, scene s19@w21 ok 138.7 s, scene s20@w21 ok 208.9 s, scene s21@w21 ok 130.7 s, scene s23@w21 ok 139.6 s
v61 (992 lines): v61: = v60 without the debug output (candidate dump, height images) | practice: scene s24@w21 FAIL 31.3 s, scene s25@w21 ok 147.1 s, scene s26@w21 ok 96.0 s, scene s27@w21 ok 135.1 s, scene s28@w21 ok 126.8 s, scene s29@w21 ok 131.0 s, scene s30@w21 ok 190.8 s, scene s31@w21 ok 181.1 s
v62 (1006 lines): v62: another survey (other poses) when no cylinder or no stick is found | practice: scene s24@w21 ok 140.8 s, scene s32@w21 ok 139.5 s, scene s33@w21 ok 118.0 s, scene s34@w21 ok 111.8 s, scene s36@w21 ok 133.7 s, scene s37@w21 ok 121.3 s, scene s38@w21 ok 124.5 s, scene s39@w21 ok 137.6 s
v63 (1006 lines): v63: centring re-closes only for a push >= 1.5 mm, at most twice (the real arm reads 0.3-2.3 mm of push as noise) | practice: scene s40@w21 ok 123.0 s, scene s41@w21 ok 137.3 s, scene s42@w21 ok 120.2 s, scene s43@w21 ok 137.8 s, scene s44@w21 ok 125.1 s, scene s45@w21 ok 113.8 s, scene s46@w21 ok 131.9 s, scene s47@w21 ok 126.0 s, scene s6@w21 ok 120.2 s, scene s32@w21 ok 139.5 s, scene s35@w21 ok 244.0 s, scene s52@w21 ok 134.3 s, scene s60@w21 ok 120.1 s, scene s61@w21 ok 215.0 s, scene s89@w21 ok 275.4 s, scene s69@w21 ok 158.6 s, scene s48@w21 ok 134.7 s, scene s49@w21 ok 150.6 s, scene s50@w21 ok 125.0 s, scene s51@w21 ok 119.8 s, scene s53@w21 ok 119.2 s, scene s54@w21 ok 123.8 s, scene s56@w21 ok 131.8 s, scene s57@w21 ok 128.6 s

Practice so far (successes/episodes, mean robot time of the successes): v0 in w0 0/4, in w1 0/4, in w2 0/4, in w3 0/4; v1 in w3 0/4; v2 in w3 2/2 (68 s); v3 in w3 2/8 (76 s); v4 in w4 2/8 (77 s); v5 in w4 0/2; v6 in w4 6/8 (87 s); v7 in w4 14/16 (111 s); v8 in w5 0/1; v9 in w4 7/8 (94 s); v10 in w6 6/8 (90 s); v11 in w6 6/8 (106 s); v12 in w6 6/8 (97 s); v13 in w6 1/2 (110 s); v14 in w6 24/25 (108 s); v15 in w6 16/16 (104 s), in w7 7/8 (104 s); v16 in w7 22/24 (96 s); v17 in w7 7/8 (104 s); v18 in w7 15/16 (108 s); v19 in w7 15/16 (115 s); v20 in w7 7/8 (114 s); v21 in w7 32/32 (110 s); v22 in w8 7/8 (109 s); v23 in w9 7/8 (111 s), in w11 32/32 (114 s), in w12 6/8 (110 s); v24 in w9 0/4, in w10 0/8, in w11 0/1, in w13 0/8, in w14 0/8, in w15 0/4, in w21 0/2; v25 in w13 15/16 (113 s); v26 in w13 8/8 (154 s); v27 in w13 7/8 (135 s); v28 in w13 0/2; v29 in w13 0/2; v30 in w13 7/8 (110 s); v31 in w13 7/8 (137 s); v32 in w13 14/16 (121 s); v33 in w13 7/8 (131 s), in w14 6/8 (143 s), in w15 6/8 (134 s); v34 in w15 48/48 (126 s); v35 in w15 8/8 (129 s); v36 in w15 8/8 (118 s), in w16 23/24 (126 s); v37 in w16 7/8 (128 s); v38 in w16 40/40 (130 s); v39 in w17 0/3; v40 in w17 0/1; v41 in w17 8/8 (195 s); v42 in w18 6/8 (150 s); v43 in w18 7/8 (143 s); v44 in w18 8/8 (169 s); v45 in w18 7/8 (165 s); v46 in w19 7/8 (142 s), in w20 7/8 (125 s); v47 in w20 7/8 (131 s); v48 in w20 8/8 (141 s); v49 in w20 6/8 (140 s); v50 in w20 5/8 (132 s); v51 in w20 5/8 (134 s), in w21 15/16 (138 s); v52 in w21 7/8 (133 s); v53 in w21 6/8 (131 s); v54 in w21 6/8 (132 s); v55 in w21 6/8 (124 s); v56 in w21 7/8 (187 s); v57 in w21 0/1; v58 in w21 0/1; v59 in w21 6/8 (129 s); v60 in w21 15/16 (140 s); v61 in w21 7/8 (144 s); v62 in w21 8/8 (128 s); v63 in w21 24/24 (144 s). Submitted: v63.

Your notes from the earlier session:
# Notes: cylinder-on-stick skill

## SESSION 2 (real robot episodes R1-R4 of v38, all FAILED)
- Real objects: ring = blue hexagonal, OD ~41.5 (filled-patch eqd), H ~30.5, hole ~14. Stand = light wood board
  ~280 x 87 x 21 mm, 3 holes in a row (50/140/230 mm from one end), ONE wooden stick ~9.5 mm (squeeze 9.4-9.6)
  in an end hole, stick top at z ~191 (=170 above board). Flat light-blue book (distractor), black table z=0.
- R1: survey (-420,60,600) did not see the stick (stand at x~-150: top out of image) -> "no stick".
- R2-R4: stick squeeze low (TCP 46): the PALM lands on the stick top at TCP ~56 (palm ~TCP+135 real).
  Guarded descents on the real arm: 5 mm/s, cut & re-sent many times (~1.5 s/mm rig time!). Tool drifts
  "beside its line" 5-30 mm. => avoid long guarded moves; never go low around the tall stick.
- R4 ring grasp: FIN RAY soft fingers: closing on the 41 mm ring read 17.8 mm (!). v38 re-opened to w+20=37.8
  (< OD) and its centring loop dragged the ring -> knocked off. => re-open by vision OD, never by reading;
  widths are not object sizes on the real gripper. Real arm: measured TCP differs from commanded by 1-6 mm.
- Real closeup heights good to ~1 mm (ring sup -0.5..0.5 on the table at 0). D415 min depth ~300: R1
  closeup at 273 mm from the stick top had no depth there.
- Real gripper: 30 N grasp; fingertips 14 mm below TCP; TCP z >= 10.
- DONE: skill2.py = working file (v41+). w17.py = world source (saved as w17, then w18 with w_sticktop body +
  arrival-error model). SUBMITTED v44 + w18 (8/8 + 7/8 + 8/8 runs). Survey (-350,60,620) reachable; (-350,60,700) not.
  Bias = ring re-look after centring grasp works: stick err after bias mostly < 0.7 mm (was up to 13 mm).
  Small light rings tilt in the b-close (sim table friction = 1) -> bias off 2-5 mm -> search handles it.
  Dark spheres: NaN centre looked like a hole -> lowfin (support seen through the hole) required (v44).
  Near the parked arm: insert tries other hand turns (v43).
- SUBMITTED v51 + w21 (8/8). w19: ring feet at outer edge; w20: ring friction priority (bad: ring pivoted out
  of the pads); w21: thin black mat (friction 0.35-0.6) over the sim table (friction 1 made light rings tip).
  v45 speed (travel w/o redundant moves, short guarded parts); v46 insertion fully guarded again (plain part
  hit 40 N); v47-49 relocation to a free spot when the bias-look pose is refused (near parked arm); v50 holed
  candidates rank first, platform grasp guarded to the end, hold check; v51 skip flat/huge candidates, stop at
  first holed, "stuck > 5 mm onto the stick" = release.
- SUBMITTED v62 + w21. v52 reach filter for candidates; v53-59 platform candidates (local rise, 75 mm opening,
  tallest part of the blob), median check, survey hole evidence; v56 home+retry when all angles refused;
  v60 removed "partly on" release (stick top z can be 5 mm off) -> search re-seats first; stick top z corrected
  by the table seen in the bias look; v62 re-survey from other poses when no stick/ring (fingertip mask hid it).
  Known fail: s86 (tall 50 mm ring on a platform tilts in the grip). w21 recent: v60/61/62 31/32.
- v63 (= v62 + centring re-close only for push >= 1.5, max 2): 16/16 in w21. SUBMITTED v63 + w21.
  summary2.txt = final summary for this session. Final submit: v63 --world 21 --final --summary summary2.txt
- PLAN v39 (done in v41): survey farther forward/higher; closeups at camera 380 mm above the top; bias = ring vision -
  ring grasp centre (same camera distance & yaw) applied to the stick-top vision; no stick squeeze; short
  guarded moves only; ring grasp a/b closes with open width from vision OD.

## Status (keep updated)
- FINAL CANDIDATE: v38 with world w16 (= w15 + LAST cleared per episode). v38: 24/24 in w16.
  summary.txt written (session summary for final submit).
- SUBMITTED: v34 with world w15 (stress: gaps 5/8, 3-8 distractors, friction 0.2-1.2, stand density
  300-12000, + w11 features). v34 in w15: 24/24 so far (s1-s21 mix). w15 ring = 24 wall boxes 0.5 mm short of
  the bottom + 8 full-height "feet" boxes inside the wall (96 coplanar contacts made the ring creep/jump;
  thin feet tunnelled), free-joint damping 0.003.
- v25-v34: stick score (taller counts, tiny patches penalised); ring candidates ranked + next candidate on
  failed grasp; max 2 grasp attempts; grasp/release height margins (wrist heights ~5 mm off); release ring
  once 15 mm on the stick (slides down) and keep sag-corrected xy after search; gentle release; travel()
  up-across-down between places (straight moves swept fingers through things); stick close-up opening 40 mm.
- Fingertip touch on the table with stop_on_contact is NOT reliable (70 N, no stop) -> never touch rigid.
- TCP may not go below z=10 (refused).
- SUBMITTED: v23 with world w11 (hard+). v23: 40/40 in w11 (s1-s8, s16-s39). w11 = w7 + sticks 5-24,
  rings 12-55 tall, OD<=68, ring/stand never black (sim depth loses black things entirely), ring free joint
  damping 0.0002 (s4 ring flew away at t~0.7s spontaneously without it), ring density 500-8000.
- v22: grasp height adapts to short rings; v23: close-up poses go higher if refused (keep-out of right arm).
- (older) SUBMITTED: v21 with world w7 (hard). v21: 32/32 in w7 (s1-s24, s29-s37).
- skill.py = working file of the program (has dead code run_old / squeeze_center_old: remove before final).
- world.py = w7 source (DARK, PLAT, CAMS flags). w6 = cluttered w/o camera errors; w4 = plain; w5 = squeeze test.
- v15 sag fix, v16 stick cand. dedupe, v17 plate fraction + strict hole, v18 finish() homing,
  v19 bias from re-look at squeezed stick, v20 width-excess step (fixed stand), v21 close-ups same camera side.

## Tool facts
- practice: one sub-command per Bash call (no &&, no cat/heredoc), --world/--version take ints.
- run_episodes output shows only first 12 + last 25 printed lines; use read_output for the middle.
- MJCF: no comments, no <custom>; hidden sites (rgba 0, pos*1e-4) carry world params to success().
- Table pattern size_mm ranges: wood 15..45, stripes 8..50, speckle 1.5..6 (others: omit).
- World module globals ARE shared between robot() wrapper and success() (LAST state -> home check).

## World (w6)
- Ring = 24 boxes, hole dia = ds + c (c 1.5..5, half the time <2), OD <= 66, H 15..55, density 300-8000.
- Stand: box/round base 60..140 x 6..25 thick, stick ds 5..20, L = H+20..H+90 (<=160), flat/round top,
  stick off-centre 30%. Platforms 35% each (15..70 mm). Table h -40..40, colours/patterns.
- Distractors 1-6: box, sphere, solid cyl, lying capsule, tall thin box, ring-sized solid decoy, disc.
- success: ring axis within clearance of stick axis at bottom+top, bottom between base and L-H, stand tilt<5,
  rel tilt<8, robot not touching ring/stand, final TCP within 25mm of start & opening>70 (via LAST).

## Skill (v14) pipeline
1. Survey: wrist cam from TCP (-420,60,600) looking down. Table plane fit; height image; opening (44mm disc)
   residual -> stick candidates (local rise vs annulus 18-32mm p60); SAM ring/cylinder-with-hole/washer +
   compact objects -> ring candidates. Height grid (3mm) for finger clearance.
2. Ring: close-up (camera 318 mm above top) -> top patch, filled -> centre/OD/hole (deep centre or hole blob
   low) -> accept first holed+plausible; else best weak.
3. Stick: close-ups -> thin+high patch; pedestal (base above table plane >=3) first; squeeze verifies round.
4. Stick squeeze a-b-a low on the stick (base+25) with iterative centring (center_along: if the arm gets
   pushed (>0.35mm) step 1.8x push and re-close) -> exact stick xy. Bias closeup-vs-squeeze corrects ring xy.
5. Ring grasp: squeeze a-b at sup+22, consistency check (w1~w2~OD) else re-closeup & retry.
6. Insert: travel high, turn fingers to free direction, descend stop_on_contact to base+2; if stuck on top ->
   spiral search (<=3.3mm); if fails put ring back, re-squeeze stick, retry once. Release, lift, home.
- squeeze_at: tries other closing angles when a move/turn is refused (wrist flip / reach) or blocked.

## Findings
- Arm is compliant: closing on a fixed/heavy thing off-centre pushes the arm ~2-3 mm and reads wider (fixed
  16 mm stick at 6 mm offset read 22.5). => iterate; judge widths only once centred.
- Gripping a tall ring high tips it -> grip low (fingertips ~6 mm above support).
- Wrist close-up xy error up to ~7 mm (camera calibration), same for ring & stick -> bias transfer works.
- stop_on_contact with held ring onto rigid support gave SAFETY STOP 51 N once (v2 test) - avoid hard touches.
- Times ~80-140 s; search can be long (time guard at 470 s).

- Loaded arm sags ~1.5 mm in xy (heavy ring): close the loop on measured TCP before descending (v15).
- Camera bias depends on gripper yaw (camera side flips if closing sign flips) -> always same side (v21).
- Squeezing a free stand drags it to the TCP -> first close-up vs squeeze says nothing about camera bias;
  re-look at the stick after squeezing gives the true bias (up to 13 mm in w7).
- robot.home() can be refused near tall things -> finish(): lift, straight move to start pose, then home.

## Ideas / TODO
- Run w4 regression; more scenes (s21+); harder variants (low-contrast colours, light tall stands).
- Clean dead code; final summary file.


Your worlds (read_world):
w0 (243 lines): w0: ring (24 boxes) + stand with stick, platforms, distractors, table height/colour vary
w1 (250 lines): w0b: params in MJCF comment
w2 (249 lines): w2: params in <custom numeric>, table pattern size fixed
w3 (255 lines): w3: params in hidden sites
w4 (258 lines): w4: = w3 with table pattern sizes in range
w5 (22 lines): w5: test world: fixed stick + heavy stand, for squeeze tests
w6 (271 lines): w6 (cluttered): as w4 but gaps 10/15 mm, 1-6 distractors incl. tall thin boxes, solid ring-sized decoys, discs; ds 5-20, OD<=66
w7 (282 lines): w7 (hard): w6 + platforms 15-120 mm, 12% dark colours, random camera pose errors (wrist 1-5 mm/0.3-2 deg, scene 2-8 mm/0.3-1.5 deg) and depth noise 0.12-0.3 px
w8 (283 lines): w8 (hard+): w7 + stick 5-24 mm, ring height 12-55, OD up to 68
w9 (285 lines): w9: = w8 but ring and stand never black (max channel >= 0.3); distractors may be dark
w10 (285 lines): w10: = w9 with ring density 500-8000 (stability check)
w11 (285 lines): w11: = w10 with a tiny damping on the ring's free joint (start-up instability in s4)
w12 (286 lines): w12 (stress): w11 + gaps 5/8 mm, 3-8 distractors, frictions 0.2-1.2, stand density 300-12000
w13 (286 lines): w13: = w12 (stress) with ring free-joint damping 0.003
w14 (296 lines): w14 (stress): = w13 with the ring standing on 8 thin feet (its 24 wall boxes stop 0.5 mm short of the bottom): no creeping
w15 (297 lines): w15 (stress): ring feet are full-height boxes inside the wall (w14's thin feet tunnelled through the base)
w16 (298 lines): w16 (final): = w15 (stress world) + the recorded last robot state is cleared per episode. Task: put the ring-like cylinder (hole = stick + 1.5..5 mm) on the stand's stick, let go, open, go home.
w17 (322 lines): w17 (real table): black table z=0+-1; the real stand (board 270-290 x 80-92 x 19-23, 3 holes, ONE stick d 9.2-10.2 x 165-175 above the board, lean 0-1.2 deg, in an end hole); cylinders round/hex OD 24-66 H 12-50 hole = stick+1.5..6, density 300-8000, friction 0.25-1; platforms 30% each 15-100 mm; book 85% + 0-4 distractors; camera pose errors; robot wrapper: plain-move arrival error ~1.3 mm, soft-finger width reading (narrower by up to 60% of w-12), TCP z>=10, rig-time estimate
w18 (325 lines): w18 (real table): = w17 + body w_sticktop (truth of the stick's top) + arrival error of plain moves: episode offset ~0.5 mm + random sigma 0.25 + 0.006*length (<=1.4) mm per axis. Real stand: board 270-290 x 80-92 x 19-23, ONE stick d 9.2-10.2, 165-175 above the board, lean 0-1.2 deg; cylinders round/hex OD 24-66 H 12-50 hole = stick + 1.5..6; platforms 30% each 15-100; book + 0-4 distractors; soft-finger width readings; TCP z >= 10
w19 (325 lines): w19 (real table, final candidate): = w18 with the cylinder's 8 feet at its outer edge (w18's feet near the hole made small cylinders tip far too easily). Real stand: board 270-290 x 80-92 x 19-23 (density 450-800), 3 holes, ONE stick d 9.2-10.2 x 165-175 above the board, lean 0-1.2 deg; cylinders round/hex OD 24-66 H 12-50 hole = stick+1.5..6 (40% <= 2.5), density 300-8000; platforms 30% each 15-100 mm; book 85% + 0-4 distractors; black table z=0+-1; wrist pose error 1-5 mm/0.3-2 deg; arrival errors of plain moves; soft-finger width readings; TCP z>=10. success: cylinder axis within the cleara
w20 (327 lines): w20 (real table, final candidate): = w19 + the cylinder's own friction (0.3-1.0) holds in all its contacts (priority 1; the sim table's friction 1 otherwise wins). Real stand: board 270-290 x 80-92 x 19-23, 3 holes, ONE stick d 9.2-10.2 x 165-175 above the board, lean 0-1.2 deg; cylinders round/hex OD 24-66 H 12-50 hole = stick+1.5..6, density 300-8000, feet at the outer edge; platforms 30% each 15-100 mm; book 85% + 0-4 distractors; black table z=0+-1; wrist pose error 1-5 mm/0.3-2 deg; plain-move arrival errors; soft-finger width readings; TCP z>=10. success: on the stick within clearance, s
w21 (328 lines): w21 (real table, final candidate): = w19 + a thin black mat on the table (friction 0.35-0.6, the real table's) instead of the sim table's friction 1 (cylinders gripped it like rubber and tipped when pushed); cylinder friction 0.3-1 (pads 1.5). Real stand: board 270-290 x 80-92 x 19-23, 3 holes, ONE stick d 9.2-10.2 x 165-175 above the board, lean 0-1.2 deg; cylinders round/hex OD 24-66 H 12-50 hole = stick+1.5..6, density 300-8000, feet at the outer edge; platforms 30% each 15-100 mm; book 85% + 0-4 distractors; table z=0+-1; wrist pose error 1-5 mm/0.3-2 deg; plain-move arrival errors; soft-f

The real robot's episodes so far: 6 episodes, R5 (v63: success), R6 (v63: FAILED), R7 (v63: FAILED), R8 (v63: FAILED), R9 (v63: FAILED), R10 (v63: FAILED). Start with list_real_episodes.

v63, the version that ran there:

```python
"""Put the cylinder (with a hole) on the stick of the stand. Entry point: run(robot, task).

Made for the real rig: a black table at z = 0, a stand that is a long wooden board with ONE tall thin stick
(~10 mm, ~170 mm above the board) in one of its holes, cylinders of many kinds, things possibly on platforms.

1. survey: the wrist camera looks down on the work area from high up (TCP (-350, 60, 620)); table plane fit;
   stick candidates (thin things much higher than their close surroundings) and cylinder candidates (SAM masks
   and compact objects).
2. close-ups, always with the camera 380 mm above the thing's top, right over it, the gripper turned the same
   way: the cylinder (a round top with a deep hole), the stick (a thin top far above a board).
3. the cylinder is centred between the fingers (closing along two lines 90 degrees apart, the fingers opened
   by what the camera saw, never by what they read: soft fingers read narrower than what they hold): its
   position is then the TCP's. It is put down there, the camera looks at it again from the same distance:
   seen minus true is the camera's calibration error, the same for the stick's top seen the same way.
4. grasped again, carried high, the measured TCP brought over the stick's corrected top, lowered with
   stop-on-contact (short); if it stops on the stick's top: a small spiral search; released once on the stick.
5. open, lift clear, back to the start pose.
"""
import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
C0 = (0.0, -1.0, 0.0)
SURVEYS = [(-350.0, 60.0, 620.0), (-420.0, 60.0, 600.0), (-300.0, -150.0, 560.0)]
HOME = (-478.0, 41.0, 350.0)
LOOK_D = 380.0           # the camera's distance above a thing's top in the close-ups (D415: no depth < ~300)
TIP = 16.0               # the fingertips below the TCP (14 on the real gripper: 2 mm more margin there)
CAM = {"up": 133.0}
TRAVEL = {"z": 300.0}
T_LIMIT = 560.0


# ------------------------------------------------------------------ robot helpers
def st(robot):
    return robot.state(ARM)


def tcp(robot):
    return np.array(st(robot)["tcp_mm"], float)


def cdir(robot):
    return tuple(float(x) for x in st(robot)["closing_dir"])


def mv(robot, xyz, stop=False, closing=None):
    c = closing if closing is not None else cdir(robot)
    r = robot.move(ARM, [float(v) for v in xyz], DOWN, c, stop)
    if not r.get("moved", True) or (not r.get("done") and r.get("why") != "contact"):
        print("MOVE", [round(float(v), 1) for v in xyz], "stop" if stop else "", r)
    return r


def travel(robot, xyz, closing=None):
    """up, across, down (a straight line between places could sweep the fingers through things)"""
    p = tcp(robot)
    zt = max(TRAVEL["z"], float(xyz[2]))
    if math.hypot(xyz[0] - p[0], xyz[1] - p[1]) > 15:
        if p[2] < zt - 1:
            mv(robot, (p[0], p[1], zt))
        r = mv(robot, (xyz[0], xyz[1], zt), closing=closing)
        if abs(zt - float(xyz[2])) < 0.5 or not r.get("done"):
            return r
    return mv(robot, xyz, closing=closing)


def open_wide(robot):
    if float(st(robot)["opening_mm"]) < 78.0:
        robot.set_gripper(ARM, 80)


def turn(robot, ang):
    p = tcp(robot)
    r = robot.turn(ARM, p.tolist(), (0, 0, 1), ang)
    if r.get("moved"):
        return ang
    r = robot.turn(ARM, p.tolist(), (0, 0, 1), -ang)
    if r.get("moved"):
        return -ang
    print("TURN refused", r.get("why"))
    return 0


def truth(robot):
    try:
        return robot.truth()
    except Exception:
        return None


def fmt(d):
    return {k: (np.round(v, 1).tolist() if isinstance(v, np.ndarray) else (round(v, 2) if isinstance(v, float) else v))
            for k, v in d.items() if k not in ("mask", "img")}


# ------------------------------------------------------------------ image helpers
def disc(px):
    px = max(3, int(round(px))) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (px, px))


def fill_nan(a, iters=8, default=0.0):
    w = np.where(np.isfinite(a), a, -1e4).astype(np.float32)
    k = np.ones((3, 3), np.uint8)
    for _ in range(iters):
        m = w < -1e3
        if not m.any():
            break
        d = cv2.dilate(w, k)
        w[m] = d[m]
    w[w < -1e3] = default
    return w


def table_plane(P):
    z = P[..., 2]
    v = np.isfinite(z)
    zs = z[v]
    hist, edges = np.histogram(zs, bins=np.arange(-100, 400, 2.0))
    z0 = edges[int(np.argmax(hist))] + 1.0
    X = P[v & (np.abs(z - z0) < 8)]
    if len(X) > 40000:
        X = X[:: len(X) // 40000 + 1]
    coef = np.array([0.0, 0.0, z0])
    for _ in range(3):
        A = np.c_[X[:, 0], X[:, 1], np.ones(len(X))]
        coef = np.linalg.lstsq(A, X[:, 2], rcond=None)[0]
        r = X[:, 2] - A @ coef
        X = X[np.abs(r) < 4]
    return coef


def ray_point(f, u, v, z):
    d = f.R @ np.array([(u - f.cx) / f.fx, (v - f.cy) / f.fy, 1.0])
    t = (z - f.eye[2]) / d[2]
    return np.asarray(f.eye, float) + t * d


def mask_iou(a, b):
    i = np.logical_and(a, b).sum()
    u = np.logical_or(a, b).sum()
    return i / max(u, 1)


def annulus(shape, u, v, r0, r1):
    H, W = shape
    R = int(math.ceil(r1)) + 1
    v0, v1, u0, u1 = max(0, int(v) - R), min(H, int(v) + R + 1), max(0, int(u) - R), min(W, int(u) + R + 1)
    yy, xx = np.mgrid[v0:v1, u0:u1]
    rr = np.hypot(xx - u, yy - v)
    return (slice(v0, v1), slice(u0, u1)), (rr >= r0) & (rr < r1)


# ------------------------------------------------------------------ survey
def survey(robot, pose):
    r = mv(robot, pose, closing=C0)
    if not r.get("done"):
        return None
    f = robot.look(["left_wrist"])["left_wrist"]
    P = f.points().copy()
    dep = f.depth
    Hh, Ww = dep.shape
    bad = ~np.isfinite(dep) | (dep < 330)
    bad[int(Hh * 0.78):, :int(Ww * 0.3)] = True      # the fingertips
    bad[int(Hh * 0.78):, int(Ww * 0.7):] = True
    P[bad] = np.nan
    coef = table_plane(P)
    zp = coef[0] * P[..., 0] + coef[1] * P[..., 1] + coef[2]
    h = P[..., 2] - zp
    h[(h > 400) | (h < -25)] = np.nan                 # (beyond the table: the floor, the robot's rail)
    P[~np.isfinite(h)] = np.nan
    valid = np.isfinite(h)
    hf = fill_nan(h)
    rng_mm = float(np.nanmedian(dep[valid]))
    pxt = f.fx / rng_mm
    op = cv2.morphologyEx(hf, cv2.MORPH_OPEN, disc(44 * pxt))
    res = hf - op
    sam = {}
    sam["stick"], sam["washer"] = [], []
    for ph in ("ring", "cylinder with a hole"):
        try:
            sam[ph] = [(m.astype(bool), float(s)) for m, s in robot.segment(f, ph)]
        except Exception as e:
            print("segment fail", ph, e)
            sam[ph] = []
    # ---- the stick: thin, far higher (~170 mm) than the board around it
    sticks = []
    n, lab, stats, _ = cv2.connectedComponentsWithStats((res > 40).astype(np.uint8), 8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 3:
            continue
        x0, y0, w0, h0 = stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3]
        sl = (slice(y0, y0 + h0), slice(x0, x0 + w0))
        comp = lab[sl] == i
        hv = np.where(comp, hf[sl], -1e9)
        vv, uu = np.unravel_index(int(np.argmax(hv)), hv.shape)
        v, u = vv + y0, uu + x0
        top = float(hf[v, u])
        pxmm = f.fx / max(150.0, rng_mm - top)
        win, ann = annulus(hf.shape, u, v, 18 * pxmm, 32 * pxmm)
        hv_ann = h[win][ann]
        hv_ann = hv_ann[np.isfinite(hv_ann)]
        if len(hv_ann) < 10:
            continue
        a60 = float(np.percentile(hv_ann, 60))
        ped = float(np.median(hv_ann))
        lrise = top - a60
        patch = comp & (hf[sl] > top - 8)
        eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
        if lrise < 90 or eqd > 40:
            continue
        p = ray_point(f, u, v, top + coef[2])
        samv = max([s for m, s in sam["stick"] if m[max(0, v - 4):v + 5, max(0, u - 4):u + 5].any()] + [0.0])
        score = min(4.0, lrise / 40.0) + (0.8 if 5 <= eqd < 25 else 0.0) + (0.5 if ped > 8 else 0.0) + samv
        sticks.append(dict(uv=(int(u), int(v)), xyz=p, top=float(p[2]), lrise=lrise, eqd=eqd, ped=ped + coef[2],
                           score=score))
    sticks.sort(key=lambda s: -s["score"])
    # ---- cylinder candidates: SAM masks and compact objects
    cands = []
    for ph in ("ring", "cylinder with a hole", "washer"):
        for m, s in sam[ph][:4]:
            if m.sum() < 20:
                continue
            for r in cands:
                if mask_iou(r["mask"], m) > 0.5:
                    r["sam"] += s
                    break
            else:
                cands.append(dict(mask=m, sam=s))
    # compact things above the table, and compact things above what is around them (on a platform)
    res2 = hf - cv2.morphologyEx(hf, cv2.MORPH_OPEN, disc(75 * pxt))
    for k, objm in enumerate((((hf > 8) & valid).astype(np.uint8), ((res2 > 11) & valid).astype(np.uint8))):
        n, lab, stats, _ = cv2.connectedComponentsWithStats(objm, 8)
        for i in range(1, n):
            a = stats[i, cv2.CC_STAT_AREA]
            if k == 1 and a >= 20:           # its tallest part only (a platform's rim may hang on to it)
                m = lab == i
                m = m & (res2 > max(11.0, 0.6 * float(np.percentile(res2[m], 95))))
                a = int(m.sum())
            eqd = 2 * math.sqrt(a / math.pi) / pxt
            if 15 < eqd < 85:
                m = (lab == i) if k == 0 else m
                if k == 1:                   # (depth noise makes small bumps: only round, solid ones)
                    cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                    per = max(cv2.arcLength(max(cnts, key=len), True), 1.0)
                    if 4 * math.pi * a / per ** 2 < 0.6 or float(np.median(res2[m])) < 11:
                        continue
                if not any(mask_iou(r["mask"], m) > 0.3 for r in cands):
                    cands.append(dict(mask=m, sam=0.0))
    rings = []
    for r in cands:
        m = r["mask"]
        hm = h[m]
        hm = hm[np.isfinite(hm)]
        if len(hm) < 10:
            continue
        top = float(np.percentile(hm, 80))
        m8 = m.astype(np.uint8)
        out = cv2.dilate(m8, disc(50 * pxt)).astype(bool) & ~cv2.dilate(m8, disc(12 * pxt)).astype(bool) & valid
        sup = float(np.percentile(h[out], 25)) if out.sum() > 10 else 0.0
        vs, us = np.nonzero(m)
        u, v = float(us.mean()), float(vs.mean())
        pxmm = f.fx / max(150.0, rng_mm - top)
        p = ray_point(f, u, v, top + coef[2])
        eqd = 2 * math.sqrt(m.sum() / math.pi) / pxmm
        cnts, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        per = max(cv2.arcLength(max(cnts, key=len), True), 1.0)
        circ = 4 * math.pi * m.sum() / per ** 2
        rise = top - sup
        if float(np.percentile(hm, 50)) - sup < 5:      # mostly flat table: not a thing
            continue
        plaus = (8 < rise < 160) and (16 < eqd < 85)      # (rise: from the table, it may be on something)
        # a hole already seen from up here: its middle lower than its top (or no depth there)
        rh = max(1.5, 0.12 * eqd * pxmm)
        win, cdisc = annulus(h.shape, u, v, 0.0, rh)
        hc = h[win][cdisc]
        holey = float(np.mean(~np.isfinite(hc) | (np.nan_to_num(hc, nan=-1e4) < top - 6))) if hc.size else 0.0
        r.update(uv=(int(u), int(v)), xyz=p, top=float(p[2]), rise=rise, eqd=eqd, circ=circ, holey=holey,
                 score=r["sam"] + (0.8 if plaus else -1.5) + (0.4 if circ > 0.7 else 0.0)
                 + (1.0 if holey > 0.5 and plaus else 0.0))
        rings.append(r)
    rings.sort(key=lambda r: -r["score"])
    zmax = float(np.nanpercentile(P[..., 2], 99.8))
    HG = height_grid(P, float(coef[2] + coef[0] * -300 + coef[1] * 50))
    return dict(f=f, coef=coef, sticks=sticks, rings=rings, zmax=zmax, HG=HG)


def cam_offset(f, robot):
    t = tcp(robot)
    return np.asarray(f.eye[:2], float) - t[:2], float(f.eye[2] - t[2])


# ------------------------------------------------------------------ close-ups
def closeup(robot, xy, top_z, camoff):
    """the camera LOOK_D above top_z, right over xy, the gripper turned as in the survey"""
    z = min(780.0, max(top_z + LOOK_D - CAM["up"], 200.0))
    tx, ty = xy[0] - camoff[0], xy[1] - camoff[1]
    tx = min(90.0, max(-700.0, tx))
    CAM["reached"] = False
    for dz in (0.0, 60.0, 120.0):
        r = travel(robot, (tx, ty, min(780.0, z + dz)), closing=C0)
        if r.get("done"):
            CAM["reached"] = dz == 0.0
            break
    if float(np.dot(st(robot)["closing_dir"], C0)) < 0:
        print("closeup: gripper flipped, turning it back")
        if turn(robot, 180) == 0:
            turn(robot, 90)
            turn(robot, 90)
    if float(np.dot(st(robot)["closing_dir"], C0)) < 0.9:
        CAM["reached"] = False             # (the camera is not on its usual side: its error is another)
    return robot.look(["left_wrist"])["left_wrist"]


def free_spot(S, od, avoid):
    """a free place on the table in the middle of the work area (for the cylinder, to look at it there)"""
    HG, c = S["HG"], S["coef"]
    best = None
    for x in np.arange(-420.0, -159.0, 30.0):
        for y in np.arange(-120.0, 181.0, 30.0):
            if any(math.hypot(x - a[0], y - a[1]) < 90 for a in avoid):
                continue
            zt = c[0] * x + c[1] * y + c[2]
            h = region_max(HG, np.array([x, y]), (1.0, 0.0), 0.0, od / 2 + 45, od / 2 + 45, pct=98) - zt
            if h > 10:
                continue
            d = math.hypot(x + 300.0, y - 30.0)
            if best is None or d < best[0]:
                best = (d, x, y)
    return None if best is None else np.array([best[1], best[2]])


def stick_closeup(f, guess_xy, guess_top, coef=None):
    P = f.points()
    z = P[..., 2]
    if not np.isfinite(z).any():
        return None
    zf = fill_nan(z, default=float(np.nanmin(z)))
    (uv,), _ = f.project(np.array([[guess_xy[0], guess_xy[1], guess_top]]))
    u0, v0 = float(uv[0]), float(uv[1])
    pxmm = f.fx / max(150.0, float(f.eye[2] - guess_top))
    op = cv2.morphologyEx(zf, cv2.MORPH_OPEN, disc(40 * pxmm))
    res = zf - op
    n, lab, stats, cents = cv2.connectedComponentsWithStats((res > 25).astype(np.uint8), 8)
    best = None
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 4:
            continue
        comp = lab == i
        zv = np.where(comp, zf, -1e9)
        v, u = np.unravel_index(int(np.argmax(zv)), zv.shape)
        dpx = math.hypot(u - u0, v - v0)
        if dpx > 45 * pxmm:
            continue
        sc = zv[v, u] - 0.5 * dpx / pxmm
        if best is None or sc > best[0]:
            best = (sc, i)
    if best is None:
        return None
    comp = lab == best[1]
    zc = np.where(comp & np.isfinite(z), z, np.nan)
    if not np.isfinite(zc).any():
        return None
    top = float(np.nanpercentile(zc, 97))
    patch = comp & np.isfinite(z) & (z > top - 4)
    if patch.sum() < 3:
        patch = comp & np.isfinite(z) & (z > top - 8)
    vs, us = np.nonzero(patch)
    uc, vc = float(us.mean()), float(vs.mean())
    pxmm = f.fx / max(150.0, float(f.eye[2] - top))
    xy = ray_point(f, uc, vc, top)[:2]
    eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
    rp = eqd / 2
    win2, ann2 = annulus(z.shape, uc, vc, (rp + 5) * pxmm, (rp + 16) * pxmm)
    zz2 = z[win2][ann2]
    zz2 = zz2[np.isfinite(zz2)]
    lrise = top - float(np.percentile(zz2, 75)) if len(zz2) > 10 else 0.0
    base = float(np.percentile(zz2, 70)) if len(zz2) > 10 else None
    plate = 0.0
    if coef is not None:
        zt = coef[0] * xy[0] + coef[1] * xy[1] + coef[2]
        win3, ann3 = annulus(z.shape, uc, vc, (rp + 5) * pxmm, (rp + 20) * pxmm)
        zz3 = z[win3][ann3]
        zz3 = zz3[np.isfinite(zz3)]
        if len(zz3) > 20:
            plate = float(np.mean((zz3 > zt + 3.5) & (zz3 < top - 10)))
    cen = math.hypot(uc - f.cx, vc - f.cy) / pxmm          # how far from the image's centre (mm at the top)
    return dict(xy=xy, top=top, eqd=eqd, base=base, lrise=lrise, plate=plate, uv=(uc, vc), cen=cen)


def ring_closeup(f, guess_xy, guess_top, guess_d):
    P = f.points()
    z = P[..., 2]
    (uv,), _ = f.project(np.array([[guess_xy[0], guess_xy[1], guess_top]]))
    u0, v0 = float(uv[0]), float(uv[1])
    pxmm = f.fx / max(150.0, float(f.eye[2] - guess_top))
    gd = min(max(guess_d, 20.0), 75.0)
    yy, xx = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    rr = np.hypot(xx - u0, yy - v0) / pxmm
    win = (rr < gd / 2 + 12) & np.isfinite(z)
    if win.sum() < 30:
        return None
    ztop = float(np.percentile(z[win], 92))
    patch = (np.abs(np.nan_to_num(z, nan=-1e4) - ztop) < 4) & (rr < gd / 2 + 35)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(patch.astype(np.uint8), 8)
    if n < 2:
        return None
    best = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA] - 3 * pxmm * math.hypot(cents[i][0] - u0,
                                                                                             cents[i][1] - v0))
    pm = (lab == best).astype(np.uint8)
    cnts, _ = cv2.findContours(pm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(pm)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    fm = filled.astype(bool)
    hole = fm & ~pm.astype(bool)
    vs, us = np.nonzero(fm)
    uc, vc = float(us.mean()), float(vs.mean())
    c3 = ray_point(f, uc, vc, ztop)
    od = 2 * math.sqrt(fm.sum() / math.pi) / pxmm
    hole_d = 2 * math.sqrt(hole.sum() / math.pi) / pxmm
    zh = z[hole]
    low = float(np.mean(~np.isfinite(zh) | (np.nan_to_num(zh, nan=-1e4) < ztop - 6))) if hole.any() else 0.0
    zhf = zh[np.isfinite(zh)]
    high = float(np.mean(zhf > ztop + 5)) if len(zhf) > 3 else 0.0     # a dome (a sphere), not a hole
    rc = np.hypot(xx - uc, yy - vc) / pxmm
    ann = (rc > od / 2 + 6) & (rc < od / 2 + 22) & np.isfinite(z) & (z < ztop - 6)
    sup = float(np.percentile(z[ann], 30)) if ann.sum() > 20 else None
    cen = rc < 2.5
    zc = z[cen]
    deep = float(np.mean(~np.isfinite(zc) | (np.nan_to_num(zc, nan=-1e4) < ztop - 6))) if cen.any() else 0.0
    hd2, lowc, lowfin = 0.0, 0.0, 0.0
    nh, hl, hst, hce = cv2.connectedComponentsWithStats(hole.astype(np.uint8), 8)
    for i in range(1, nh):
        if math.hypot(hce[i][0] - uc, hce[i][1] - vc) / pxmm > 4:
            continue
        d_ = 2 * math.sqrt(hst[i, cv2.CC_STAT_AREA] / math.pi) / pxmm
        if d_ > hd2:
            zz_ = z[hl == i]
            hd2 = d_
            lowc = float(np.mean(~np.isfinite(zz_) | (np.nan_to_num(zz_, nan=-1e4) < ztop - 6)))
            lowfin = float(np.mean(np.nan_to_num(zz_, nan=1e4) < ztop - 6))   # seen through: the support
    img = f.rgb.copy()
    cv2.drawContours(img, cnts, -1, (255, 0, 0), 1)
    cv2.circle(img, (int(round(uc)), int(round(vc))), 3, (0, 255, 0), -1)
    return dict(xy=c3[:2], top=ztop, od=od, hole_d=hole_d, low=low, high=high, deep=deep, hd2=hd2, lowc=lowc,
                lowfin=lowfin,
                sup=sup, uv=(uc, vc), img=img, cen=math.hypot(uc - f.cx, vc - f.cy) / pxmm)


# ------------------------------------------------------------------ free directions for the fingers and the hand
def height_grid(P, z_table, x0=-680.0, x1=60.0, y0=-420.0, y1=560.0, cell=3.0):
    nx, ny = int((x1 - x0) / cell), int((y1 - y0) / cell)
    G = np.full((nx, ny), -1e4, np.float32)
    X = P[np.isfinite(P[..., 2])]
    ix = ((X[:, 0] - x0) / cell).astype(int)
    iy = ((X[:, 1] - y0) / cell).astype(int)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    np.maximum.at(G, (ix[ok], iy[ok]), X[ok, 2].astype(np.float32))
    G = fill_nan(np.where(G < -1e3, np.nan, G), iters=4, default=z_table)
    return dict(G=G, x0=x0, y0=y0, cell=cell)


def region_max(HG, centre, cdir_xy, along0, along1, across, pct=95):
    c = np.asarray(cdir_xy, float)
    c = c / np.linalg.norm(c)
    n = np.array([-c[1], c[0]])
    a = np.arange(along0, along1 + 0.1, 2.5)
    b = np.arange(-across, across + 0.1, 2.5)
    A, B = np.meshgrid(a, b)
    pts = []
    for s in (1, -1):
        pts.append(centre[None, :] + (s * A.ravel())[:, None] * c[None, :] + B.ravel()[:, None] * n[None, :])
    pts = np.concatenate(pts)
    ix = ((pts[:, 0] - HG["x0"]) / HG["cell"]).astype(int)
    iy = ((pts[:, 1] - HG["y0"]) / HG["cell"]).astype(int)
    G = HG["G"]
    ok = (ix >= 0) & (ix < G.shape[0]) & (iy >= 0) & (iy < G.shape[1])
    if not ok.any():
        return -1e4
    return float(np.percentile(G[ix[ok], iy[ok]], pct))


def best_closing(HG, centre, inner, z_tip, z_body=None, angles=(0, 30, 60, 90, 120, 150), extra=(45, 90)):
    """the closing direction (deg from +x) whose finger regions (and those turned by `extra`) are clearest
    below the fingertips (z_tip), and whose hand (+-105 mm along, +-60 across) clears z_body"""
    best = None
    for th in angles:
        hs, hb = [], []
        for e in (0,) + tuple(extra):
            t = math.radians(th + e)
            cd = (math.cos(t), math.sin(t))
            hs.append(region_max(HG, np.asarray(centre, float), cd, inner, 58.0, 21.0))
            if z_body is not None:
                hb.append(region_max(HG, np.asarray(centre, float), cd, 0.0, 105.0, 60.0, pct=100))
        clr = z_tip - max(hs)
        if hb:
            clr = min(clr, z_body - max(hb))
        if best is None or clr > best[1] + 0.5:
            best = (th, clr)
    return best


def move_closing(robot, xyz, angles):
    for th in angles:
        c = (math.cos(math.radians(th)), math.sin(math.radians(th)), 0.0)
        r = robot.move(ARM, [float(v) for v in xyz], DOWN, c, False)
        if r.get("done"):
            return th
        print("move with closing", round(th), "refused:", r.get("why", "")[:80])
        if r.get("moved"):
            break
    mv(robot, xyz)
    return None


# ------------------------------------------------------------------ centring a thing between the fingers
def center_along(robot, label, min_w, max_w, open_w, iters=2, push_min=1.5, max_step=5.0):
    """close on the thing along the current closing line. A free thing is pushed to the middle; if the arm is
    pushed instead (a heavy thing), step toward it and close again. Between closes the fingers open to open_w
    (from what the camera saw: soft fingers read narrower than what they hold). Leaves it closed; returns w."""
    w = 0.0
    for it in range(iters):
        p0 = tcp(robot)
        w = robot.set_gripper(ARM, 0)
        s1 = st(robot)
        p1 = np.array(s1["tcp_mm"], float)
        d = p1 - p0
        d[2] = 0.0
        nd = float(np.linalg.norm(d))
        print(label, "w %.2f push %.2f" % (w, nd))
        if w < min_w or w > max_w or nd < push_min or it == iters - 1:
            return w
        let_go(robot, w, open_w)
        t = p0 + min(1.5 * nd, max_step) * d / nd
        mv(robot, (t[0], t[1], p0[2]))
    return w


def let_go(robot, w, open_w):
    """open gently: first just loose (a sudden wide opening can fling a light thing), then wide"""
    w1 = min(open_w, max(w + 4.0, open_w - 22.0))
    if w1 < open_w - 1:
        robot.set_gripper(ARM, w1)
        robot.wait(0.2)
    robot.set_gripper(ARM, open_w)


def squeeze_center(robot, label, min_w, max_w, open_w, single=False):
    """centre it along the closing line, then (turned 90 degrees) along the other. Returns the widths, None
    (nothing there) or "TURNFAIL"."""
    w1 = center_along(robot, label + "-a", min_w, max_w, open_w)
    if w1 < min_w:                      # missed it: catch it along the other line first
        robot.set_gripper(ARM, open_w)
        a = turn(robot, 90)
        if a == 0:
            return "TURNFAIL"
        w = center_along(robot, label + "-rot", min_w, max_w, open_w)
        robot.set_gripper(ARM, open_w)
        if not (min_w <= w <= max_w):
            return None
        if turn(robot, -a) == 0:
            return "TURNFAIL"
        w1 = center_along(robot, label + "-a2", min_w, max_w, open_w)
    if not (min_w <= w1 <= max_w):
        robot.set_gripper(ARM, open_w)
        return None
    if single:
        return [w1]
    let_go(robot, w1, open_w)
    if turn(robot, 90) == 0:
        return "TURNFAIL"
    w2 = center_along(robot, label + "-b", min_w, max_w, open_w)
    if not (min_w <= w2 <= max_w):
        robot.set_gripper(ARM, open_w)
        return None
    print(label, "widths", round(w1, 2), round(w2, 2), "tcp", np.round(tcp(robot), 2))
    return [w1, w2]


def squeeze_at(robot, label, xy, z_safe, z_grip, angles, min_w, max_w, open_w, z_pre=None, z_g1=None,
               single=False):
    """go down at xy to z_grip with one of the closing angles (plain to z_pre, until contact to z_g1, plain
    on) and centre the thing there; when a move or a turn is refused, come back up and try the next angle."""
    tried = []
    reset = False
    angles = list(angles)
    k = 0
    while k < len(angles):
        th = angles[k]
        k += 1
        if any(abs(((th - t) + 90) % 180 - 90) < 10 for t in tried):
            continue
        tried.append(th)
        cd = (math.cos(math.radians(th)), math.sin(math.radians(th)), 0.0)
        r = travel(robot, (xy[0], xy[1], z_safe), closing=cd)
        if not r.get("done"):
            print(label, "closing angle", round(th), "refused:", (r.get("why") or "")[:70])
            if k == len(angles) and not reset:      # the arm may be wound up: back to its start, all again
                reset = True
                p = tcp(robot)
                mv(robot, (p[0], p[1], max(p[2], TRAVEL["z"])))
                robot.home(ARM)
                tried, k = [], 0
            continue
        open_wide(robot)
        if z_pre is not None:
            if z_pre < z_safe - 2:
                mv(robot, (xy[0], xy[1], max(z_pre, z_grip)))
            zg1 = max(z_grip, z_g1 if z_g1 is not None else z_grip)
            r = mv(robot, (xy[0], xy[1], zg1), stop=True)
            if not r.get("why") and tcp(robot)[2] < zg1 + 3 and zg1 > z_grip + 0.5:
                r = mv(robot, (xy[0], xy[1], z_grip))      # the fingers are clear of its top: on, plain
        else:
            r = mv(robot, (xy[0], xy[1], z_grip))
        p = tcp(robot)
        if not r.get("moved", True) or (r.get("why") and r.get("why") != "contact"):
            print(label, "descent refused/stopped at angle", round(th), (r.get("why") or "")[:70])
            mv(robot, (p[0], p[1], z_safe))
            continue
        if r.get("why") or p[2] > z_grip + 3:
            print(label, "descent held up at", np.round(p, 1), "angle", round(th))
            if p[2] > z_grip + 8:            # something under the fingers: try another direction
                mv(robot, (p[0], p[1], z_safe))
                continue
            mv(robot, (p[0], p[1], p[2] + 2))
        ws = squeeze_center(robot, label, min_w, max_w, open_w, single)
        if isinstance(ws, str):
            print(label, "turn refused at angle", round(th))
            robot.set_gripper(ARM, 80)
            p = tcp(robot)
            mv(robot, (p[0], p[1], z_safe))
            continue
        CAM["angle"] = th
        return ws
    return None


# ------------------------------------------------------------------ the steps
def finish(robot, z_clear=0.0):
    """open, lift straight up clear of everything, go to the start pose (straight, then by the joints)"""
    robot.set_gripper(ARM, 80)
    p = tcp(robot)
    zc = min(700.0, max(z_clear + 70.0, p[2] + 10, 300.0))
    mv(robot, (p[0], p[1], zc))
    for z in (max(zc, HOME[2]), 500.0):
        mv(robot, (HOME[0], HOME[1], z), closing=C0)
        r = robot.home(ARM)
        if r.get("moved") or r.get("done"):
            return
        print("home refused:", r.get("why"))
    mv(robot, HOME, closing=C0)


def ring_support(ring, coef):
    sup = ring["sup"] if ring["sup"] is not None else ring["top"] - 30
    if abs(sup) < 9:                     # on the table (the real table's top is at z = 0)
        return 0.0, True
    return sup, False


def find_ring(robot, S, camoff):
    found = []
    def reachable(p):
        return -620 < p[0] < -95 and -400 < p[1] < 600 and not (p[1] > 250 and p[0] > -150)
    # not flat things, not big ones, not out of the arm's reach
    sk0 = S["sticks"][0]["xyz"][:2] if S["sticks"] else np.array([1e4, 1e4])
    cands = [rc for rc in S["rings"] if rc["rise"] >= 6 and rc["eqd"] <= 90 and reachable(rc["xyz"])
             and np.linalg.norm(rc["xyz"][:2] - sk0) > 25]
    print("cylinder candidates to look at:", len(cands))
    for rc in cands[:9]:
        if robot.time_s() > 250:
            break
        fc = closeup(robot, rc["xyz"][:2], rc["top"], camoff)
        cy = ring_closeup(fc, rc["xyz"][:2], rc["top"], rc["eqd"])
        print("RING closeup", None if cy is None else fmt(cy))
        if cy is None:
            continue
        if len(found) < 2:
            robot.show("ring_closeup", cy["img"])
        plaus = 16 <= cy["od"] <= 72 and (cy["sup"] is None or cy["top"] - cy["sup"] >= 8)
        if not plaus:
            continue
        # a hole: deeper than the top, and the support seen through it (a dark thing's top only loses depth)
        holed = (cy["deep"] >= 0.5 or (cy["hd2"] >= 2.5 and cy["lowc"] >= 0.8)) and cy["high"] < 0.15 \
            and cy["lowfin"] >= 0.1
        hole_ev = 2.0 if holed else min(1.0, cy["deep"] + cy["hd2"] / 5 + cy["lowc"] * 0.3) - 2 * cy["high"] \
            - (1.0 if cy["lowfin"] < 0.1 else 0.0)
        cy["rank"] = (10.0 if holed else 0.0) + hole_ev + min(rc["score"], 3.5)   # a seen hole comes first
        found.append(cy)
        if holed:
            break
    found.sort(key=lambda c: -c["rank"])
    print("ring candidates", [(np.round(c["xy"], 0).tolist(), round(c["rank"], 2)) for c in found])
    return found


def find_stick(robot, S, camoff, ring):
    """the stick's top seen from LOOK_D above it, nearly at the image's centre"""
    cands = []
    for sc in S["sticks"]:
        if np.linalg.norm(sc["xyz"][:2] - ring["xy"]) < ring["od"] / 2 + 10:
            continue
        if any(np.linalg.norm(sc["xyz"][:2] - o["xyz"][:2]) < 25 for o in cands):
            continue
        cands.append(sc)
    for sc in cands[:4]:
        xy, top = sc["xyz"][:2], sc["top"]
        sk = None
        for k in range(3):
            fs = closeup(robot, xy, top, camoff)
            sk = stick_closeup(fs, xy, top, S["coef"])
            print("STICK closeup", None if sk is None else fmt(sk))
            if sk is None or not (sk["eqd"] <= 24 and sk["lrise"] >= 90):
                sk = None
                break
            if sk["cen"] < 12 and abs(sk["top"] - top) < 8:
                break
            xy, top = sk["xy"], sk["top"]        # look again from right above it
        if sk is not None:
            robot.show("stick_closeup", fs.rgb)
            return sk
    return None


def grasp_ring(robot, cxy, ring, S, known=False, single=False):
    """centre the cylinder between the fingers low on it; returns dict(C=TCP, db=TCP z - its bottom, w) or
    None. known: its position is known from a grasp (plain moves all the way down)"""
    sup, on_table = ring_support(ring, S["coef"])
    Hc = ring["top"] - sup
    clr = 4.0 if on_table else 3.0         # (on something: the last part until contact, it may be a little higher)
    zc = max(sup + TIP + min(clr, max(2.0, Hc / 2)), 12.0)
    od_c = ring["od"] * 1.1                          # (a hexagon's corners)
    open_w = min(80.0, od_c + 20.0)
    z_safe = ring["top"] + TIP + 8.0
    th, clr_ = best_closing(S["HG"], np.asarray(cxy, float), max(ring["od"] / 2 + 8, 30.0), zc - TIP,
                            z_body=zc + 112.0)
    print("ring closing angle", th, "clearance %.1f" % clr_, "zc %.1f" % zc, "sup %.1f" % sup)
    margin = (80.0 - od_c) / 2 - 3.0
    # until contact only while the fingertips pass its top (they might land on it), plain before and after
    z_pre = None if (known or margin > 16) else ring["top"] + TIP + 3.0
    z_g1 = ring["top"] + TIP - 10.0
    if not on_table:                       # its support's height is seen only: until contact to the end
        z_pre, z_g1 = (z_pre if z_pre is not None else ring["top"] + TIP + 3.0), zc
    angles = [th, th + 90, th + 45, th - 45, th + 22, th - 22]
    if known and CAM.get("angle") is not None:
        angles = [CAM["angle"]] + angles          # the one that worked here before
    sq = squeeze_at(robot, "CYL", cxy, z_safe, zc, angles, 3.0, 80.0, open_w, z_pre=z_pre, z_g1=z_g1,
                    single=single)
    if sq is None:
        robot.set_gripper(ARM, 80)
        p = tcp(robot)
        mv(robot, (p[0], p[1], z_safe + 20))
        return None
    C = tcp(robot)
    t = truth(robot)
    if t:
        print("CYL truth err", np.round(np.array(t["w_cyl"][:2]) - C[:2], 2), "db true", round(C[2] - t["w_cyl"][2], 1),
              "db est", round(C[2] - sup, 1))
    return dict(C=C, db=C[2] - sup, w=sq, open_w=open_w, sup=sup)


def insert(robot, Sxy, stop, db, zt, open_w):
    """the held cylinder's axis (the TCP) over the stick's top, down onto it; a small search if it sits on
    the top. Returns True when it is on the stick."""
    C = tcp(robot)
    mv(robot, (C[0], C[1], zt))
    z1 = stop + 6 + db                     # its bottom 6 mm above the stick's top
    c = np.array(cdir(robot)[:2])
    a0 = math.degrees(math.atan2(c[1], c[0]))
    reached = False
    for dz in (0.0, 60.0):                 # near the parked arm some turns of the hand are refused
        for d in (0, 45, -45, 90, 135, -90):
            t = math.radians(a0 + d)
            cd = (math.cos(t), math.sin(t), 0.0)
            r = robot.move(ARM, [float(Sxy[0]), float(Sxy[1]), zt + dz], DOWN, cd, False)
            if not r.get("done"):
                print("insert: approach refused at", d, (r.get("why") or "")[:60])
                continue
            r = robot.move(ARM, [float(Sxy[0]), float(Sxy[1]), z1], DOWN, cd, False)
            if r.get("done"):
                reached = True
                break
            print("insert: descent refused at", d, (r.get("why") or "")[:60])
            mv(robot, (Sxy[0], Sxy[1], zt + dz))
        if reached:
            break
    if not reached:
        print("insert: the stick cannot be reached")
        return False
    cmd = np.array([Sxy[0], Sxy[1], z1], float)
    best = None
    for _ in range(6):                     # the arm ends a little off (and sags under a load): close the loop
        err = np.asarray(Sxy, float) - tcp(robot)[:2]
        if best is None or np.linalg.norm(err) < best[0]:
            best = (float(np.linalg.norm(err)), cmd.copy())
        if np.linalg.norm(err) < 0.5 or np.linalg.norm(err) > 15:
            break
        cmd[:2] += err
        if not mv(robot, cmd).get("done"):
            break
    err = np.asarray(Sxy, float) - tcp(robot)[:2]
    if np.linalg.norm(err) > best[0] + 0.3:
        cmd = best[1]
        mv(robot, cmd)
    corr = cmd[:2] - np.asarray(Sxy, float)
    print("insert: xy correction", np.round(corr, 2), "residual", np.round(np.asarray(Sxy) - tcp(robot)[:2], 2))
    zeng = stop - 14 + db                  # 14 mm onto the stick, then let go (it slides down)
    r = mv(robot, (cmd[0], cmd[1], zeng), stop=True)   # (the top's height is known to a few mm only)
    zn = tcp(robot)[2]
    print("insert", r.get("why"), "z", round(zn, 1), "zeng", round(zeng, 1), "bottom", round(zn - db, 1),
          "stick_top", round(stop, 1))
    if zn - db < stop - 8:
        return True
    zs = zn
    # first the same place again, from higher up (it may have caught askew), then around it
    mv(robot, (cmd[0], cmd[1], zs + 6.0))
    mv(robot, (cmd[0], cmd[1], zs - 7), stop=True)
    if tcp(robot)[2] < zs - 4:
        print("search: found it on a second try at the same place")
        mv(robot, (cmd[0], cmd[1], zeng), stop=True)
        return tcp(robot)[2] - db < stop - 8 or tcp(robot)[2] < zs - 8
    zs = min(zs, tcp(robot)[2])
    for rad in (0.8, 1.6, 2.4, 3.2):
        nk = 6 if rad < 1 else (8 if rad < 2 else 12)
        for k in range(nk):
            if robot.time_s() > T_LIMIT - 60:
                print("search: out of time")
                return False
            a = 2 * math.pi * k / nk + rad
            x, y = Sxy[0] + corr[0] + rad * math.cos(a), Sxy[1] + corr[1] + rad * math.sin(a)
            mv(robot, (x, y, zs + 2.0))
            mv(robot, (x, y, zs - 7), stop=True)
            if tcp(robot)[2] < zs - 4:
                print("search found at offset", round(rad, 1), round(math.degrees(a) % 360))
                mv(robot, (x, y, zeng), stop=True)
                return tcp(robot)[2] - db < stop - 8
    print("search failed")
    return False


def run(robot, task):
    try:
        main(robot, task)
    except Exception as e:                  # whatever went wrong: let go and go home
        print("ERROR", repr(e))
        try:
            finish(robot, TRAVEL["z"])
        except Exception as e2:
            print("ERROR in finish", repr(e2))


def main(robot, task):
    T0 = truth(robot)
    if T0:
        print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in T0.items()})
    robot.set_gripper(ARM, 80)
    S = None
    for pose in SURVEYS:
        S = survey(robot, pose)
        if S is not None and S["sticks"] and S["rings"]:
            break
        print("survey at", pose, "found", None if S is None else (len(S["sticks"]), len(S["rings"])))
    if S is None:
        finish(robot)
        return
    f = S["f"]
    camoff, CAM["up"] = cam_offset(f, robot)
    TRAVEL["z"] = min(600.0, max(200.0, S["zmax"] + 60.0))
    print("plane", np.round(S["coef"], 4), "zmax", round(S["zmax"], 1), "camoff", np.round(camoff, 1),
          "cam up", round(CAM["up"], 1))
    for s in S["sticks"][:3]:
        print("STICKC", fmt(s))
    for r in S["rings"][:3]:
        print("RINGC", fmt(r))
    ann = f.rgb.copy()
    for s in S["sticks"][:4]:
        cv2.circle(ann, s["uv"], 6, (255, 0, 0), 1)
    for r in S["rings"][:4]:
        cv2.circle(ann, r["uv"], 10, (0, 255, 0), 1)
    robot.show("survey", ann)
    rings = find_ring(robot, S, camoff)
    for pose in SURVEYS[1:]:                # (things under the fingertips' corners of the image: look again)
        if rings:
            break
        S2 = survey(robot, pose)
        if S2 is not None:
            rings = find_ring(robot, S2, camoff)
    if not rings:
        print("no ring found")
        finish(robot, S["zmax"])
        return
    ring = rings[0]
    stick = find_stick(robot, S, camoff, ring)
    for pose in SURVEYS[1:]:
        if stick is not None:
            break
        print("no stick seen: another survey from", pose)
        S2 = survey(robot, pose)
        if S2 is not None:
            stick = find_stick(robot, S2, camoff, ring)
            S["zmax"] = max(S["zmax"], S2["zmax"])
    if stick is None:
        print("no stick found")
        finish(robot, S["zmax"])
        return
    T = truth(robot)
    if T:
        tk = np.array(T.get("w_sticktop", T["w_stick"]), float)
        print("STICK vision err (no bias)", np.round(stick["xy"] - tk[:2], 2), "top", round(stick["top"], 1))
    # ---- grasp the cylinder: its true place
    g = grasp_ring(robot, ring["xy"], ring, S)
    while g is None and len(rings) > 1 and robot.time_s() < 300:
        rings = rings[1:]
        ring = rings[0]
        print("cylinder grasp failed: trying the next candidate at", np.round(ring["xy"], 1))
        g = grasp_ring(robot, ring["xy"], ring, S)
    if g is None:
        print("cylinder grasp failed")
        finish(robot, max(stick["top"], S["zmax"]))
        return
    # ---- put it down where it is, look at it as at the stick: the camera's error
    C = g["C"]
    let_go(robot, g["w"][-1], g["open_w"])
    mv(robot, (C[0], C[1], ring["top"] + TIP + 8))
    fc = closeup(robot, C[:2], ring["top"], camoff)
    r2 = ring_closeup(fc, C[:2], ring["top"], ring["od"]) if CAM["reached"] else None
    if r2 is None or abs(r2["od"] - ring["od"]) > 0.3 * ring["od"]:
        # the camera cannot look at it from right above (near the parked arm): put it somewhere it can
        spot = free_spot(S, ring["od"], [stick["xy"], C[:2]])
        print("cannot look at the cylinder there; a free spot:", spot)
        if spot is not None and robot.time_s() < 330:
            g2 = grasp_ring(robot, C[:2], ring, S, known=True, single=True)   # (turns may be refused here)
            if g2 is not None:
                ring = dict(ring)
                ring["top"], ring["sup"] = ring["top"] - g2["sup"], 0.0
                zput = g2["db"] + 1.0
                travel(robot, (spot[0], spot[1], zput + 10.0))
                mv(robot, (spot[0], spot[1], zput), stop=True)
                C = tcp(robot)
                let_go(robot, g2["w"][-1], g2["open_w"])
                mv(robot, (C[0], C[1], ring["top"] + TIP + 8))
                CAM["angle"] = None
                g3 = grasp_ring(robot, C[:2], ring, S, known=True)          # centre it here
                if g3 is not None:
                    C = g3["C"]
                    let_go(robot, g3["w"][-1], g3["open_w"])
                    mv(robot, (C[0], C[1], ring["top"] + TIP + 8))
                    fc = closeup(robot, C[:2], ring["top"], camoff)
                    r2 = ring_closeup(fc, C[:2], ring["top"], ring["od"])
    print("RING bias look", None if r2 is None else fmt(r2))
    T2 = truth(robot)
    if T2 and r2 is not None:
        print("RING truth at the bias look: C - true", np.round(C[:2] - np.array(T2["w_cyl"][:2]), 2),
              "seen - true", np.round(r2["xy"] - np.array(T2["w_cyl"][:2]), 2))
    bias = np.zeros(2)
    if r2 is not None and abs(r2["od"] - ring["od"]) < 0.3 * ring["od"]:
        bias = np.asarray(r2["xy"], float) - C[:2]
        if np.linalg.norm(bias) > 20:
            print("bias too large, not used", np.round(bias, 2))
            bias = np.zeros(2)
    Sxy = np.asarray(stick["xy"], float) - bias
    if r2 is not None and r2["sup"] is not None and abs(r2["sup"]) < 9 and ring_support(ring, None)[1]:
        # the table (at z = 0) seen around it from about as far: the camera's height error
        stick = dict(stick)
        stick["top"] -= float(np.clip(r2["sup"], -6.0, 6.0))
    print("bias", np.round(bias, 2), "stick at", np.round(Sxy, 2), "top", round(stick["top"], 1))
    if T:
        print("STICK err after bias", np.round(Sxy - tk[:2], 2))
    ok = False
    for attempt in range(2):
        g = grasp_ring(robot, C[:2], ring, S, known=True)
        if g is not None:                  # does it hold? lift a little and see
            mv(robot, (g["C"][0], g["C"][1], g["C"][2] + 15))
            if float(st(robot)["opening_mm"]) < 1.5:
                print("cylinder lost when lifted: grasp it again")
                robot.set_gripper(ARM, 80)
                g = grasp_ring(robot, C[:2], ring, S, known=True)
        if g is None:
            print("cylinder re-grasp failed")
            break
        C = g["C"]
        db = g["db"]
        zt = max(stick["top"], S["zmax"], ring["top"]) + db + 35
        if insert(robot, Sxy, stick["top"], db, zt, g["open_w"]):
            ok = True
            robot.set_gripper(ARM, min(80.0, g["open_w"] - 10))
            robot.wait(0.3)
            break
        if robot.time_s() > T_LIMIT - 120:
            break
        # put it back where it was and try once more
        mv(robot, (tcp(robot)[0], tcp(robot)[1], zt))
        mv(robot, (C[0], C[1], zt))
        mv(robot, (C[0], C[1], C[2] + 10.0))
        mv(robot, (C[0], C[1], C[2] + 1.0), stop=True)
        robot.set_gripper(ARM, g["open_w"])
        mv(robot, (C[0], C[1], ring["top"] + TIP + 8))
    finish(robot, max(stick["top"], S["zmax"]))
    j = None
    try:
        j = robot.judge()
    except Exception:
        pass
    print("RESULT", ok, "JUDGE", j)
```

In your workspace: program.py is v63; worlds/ holds your worlds (w0.py, ...) as files to go on from; notes.md holds those notes.

