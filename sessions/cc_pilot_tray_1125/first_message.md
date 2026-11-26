THE SKILL TO MAKE: put the block in the tray.

On the table there are a small block and a shallow tray. Pick up the block and put it in the tray, and let go of it: at the end
the block must lie in the tray, the robot not touching it. Then open the gripper and return it to its initial pose.

The program should be robust to:
- different blocks and trays: sizes, colours, weights, frictions;
- different places of the block and the tray on the table;
- different table heights.

You receive no external test cases and no assets: no block and no tray, only the robot, its cameras and the table. With worlds of
your own you create your own test cases and assets to make the skill.

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