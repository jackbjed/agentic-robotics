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

Nothing is given for it but the robot, its cameras and the table: no objects, no assets, no test cases, no task in
the simulator. You make all of that yourself, as WORLDS (described below): what is on the table (bodies, soft
bodies, loose things, marks), the task's text, and the check that says whether an episode did the task. Then you
write the skill: one program run(robot, task) that does the task in your worlds' episodes from the cameras and the
arm's own measurements only, as every program here. The robot is the left arm alone; the other arm is not to be
used, and the simulator refuses it.

There is no program and no world yet.

The first look at the empty table (the arm at its start pose; the cameras' colour images):

LEFT arm: TCP (-478, 41, 350) mm; approach_dir (0.00, 0.00, -1.00), closing_dir (-0.00, -1.00, -0.00); opening 80 mm (commanded 80); external force estimate (0.7, 0.4, -0.1) N.
scene camera (640x480 px):
[picture: pictures/first_look/1_scene.png]
left wrist camera (640x480 px):
[picture: pictures/first_look/2_left_wrist.png]