# Your own test scenes (practice)

When a run allows it, the agent can write scenes and practise on them. A scene is a YAML file under
`practice/scenes/`:

```yaml
target: tape_red                  # the object the task names: tape_red or tape_yellow
objects:                          # object id: [x, y, yaw_deg]   (2 to 6 objects; the target among them)
  tape_red:    [-0.30, 0.020, 0]
  tape_yellow: [-0.30, 0.075, 0]
  hole_block:  [-0.22, 0.020, 45]
  glue:        [-0.35, -0.020, 10]
arm_start: [-0.30, 0.00, 0.22, 0] # optional: fingertips x, y, z (m) and hand yaw (deg); default: over the target, 22 cm up
```

**Frame:** the world frame of docs/robot.md, with the table top at z = 0.
- **x** grows away from the robot. The work area is x −0.49 … −0.12.
- **y** is lateral, with the ruler along y ≈ 0.19. The work area is y −0.18 … 0.21.
- **yaw** is the object's rotation about the vertical, in degrees.
- Positions are of the object's origin, which is the centre of its footprint.

**Object ids and approximate sizes** (footprint × height, mm). The names the task uses depend on the run's
condition; the brief lists them.

| id | object (real set) | size | condition C substitute |
|---|---|---|---|
| tape_red / tape_yellow / tape_white | tape rolls, the hole open | Ø47 / Ø52 / Ø50 × 19–27 | solid cans, same size |
| hole_block | black block with 4 holes | 71 × 71 × 22 | plain black box |
| cable | coiled black cable, a curved strip | 168 × 65 × 14 | black elliptical hoop |
| screwdriver | red handle + thin shaft | 190 × 22 × 22 | red-and-grey bar |
| glue | glue bottle, red base | 55 × 41 × 133 | red-and-white cylinder bottle |
| sticky_pad | sticky-note pad | 76 × 76 × 20 | purple disc Ø80 × 20 |

**Checks** (`ar-practice scene check practice/scenes/NAME.yaml`). These are the same rules as the training layouts'
generator:
- every outline inside the work area, and no two overlapping (gaps of 0.5 mm or more);
- the hand reachable at its start (12–30 cm up);
- nothing interpenetrating;
- after 1 s of settling, nothing moved more than 2 cm, tilted more than 20°, or fell. Moves over 5 mm are reported.

The report gives:
- the gaps between the target's outline and every other object's (mm);
- the settling moves;
- the target's visible pixels in the scene and wrist cameras (seen / unoccluded);
- the free straight-down grasp directions out of 18, at an 80 mm opening with a 4 mm margin (0 = boxed in).

It writes `NAME.check/preview.png`: the scene camera's frame as the program would get it, beside a top view without
the robot (x points DOWN the image, y to the right).

**Running** (`ar-practice run --scene practice/scenes/NAME.yaml --n 3 [--seed S]`): your skill on that scene, each
episode with different sensor noise and camera calibration error. Each episode counts against the practice budget.

Scenes are for practice only. The success check is the same, and the program sees nothing of the file: only the task
and the cameras. Development and held-out evaluation use layouts the agent never sees.
