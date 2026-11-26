THE SKILL TO MAKE: pick the sponge and sweep the small objects into the target area.

On the table there are a sponge, small objects and a marked target area. The small objects can be, for example, lentils, beans, or other small objects. The program should be robust to:
- different small objects and different sponges, both with different physical properties: visual appearances, colours, frictions, densities and other physical parameters;
- different spatial locations and configurations, and different spatial arrangements of the small objects;
- the height at which the sponge is;
- different markers designating the target area: for example a piece of paper that the objects have to land on, or an area delimited by some coloured tape;
- the presence of distractors;
- different table heights.

You receive no external test cases and no assets: no sponge and no objects, only the robot, its cameras and the table. With worlds of your own you create your own test cases and assets (the sponges, the small objects such as lentils or beans, the markers, the distractors) to make the skill.

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