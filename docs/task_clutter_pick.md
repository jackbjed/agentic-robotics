# Task: clutter pick

**What the program is told:** "Pick up the {red|yellow} tape roll and lift it."

**The scene:**
- the target tape roll;
- the other-coloured roll;
- 1–3 more objects from: a white tape roll, a black block with holes, a coiled black cable, a screwdriver, a glue
  bottle, a sticky note pad.

The objects are settled on the table, and the arm starts over the target's area: within ±6 cm, fingertips 20–26 cm
up, at any hand angle.

**Levels.** The level sets the gap between the target's outline and every other object's:

| level | gap | |
|---|---|---|
| 0 | 6–10 cm | free |
| 1 | 3.5–6 cm | |
| 2 | 1.5–3.5 cm | tight |
| 3 | 2–12 mm all round | boxed in: the other roll plus 3 objects, and no free straight-down grasp at an 80 mm opening |

**Success.** It is judged on the true state every 1/30 s of robot time. The target's centre of mass must be at least
5 cm above its rest height, held by both fingers, with no other object lifted 3 cm or more while touching a finger.
All of this must hold for 1 s. Failures:
- `wrong_object`;
- `off_table` (something fell off the table);
- `dropped` (lifted at least 2 cm, then lost);
- `touched_not_lifted`;
- `not_touched`.

The episode ends when the program returns. Motion is refused after 60 s of robot time, and the process is killed
after 900 s of wall time.

**Layouts.** Banks of pre-generated, settled layouts, ported from panda_pick_rgbd:

| split | layouts per level | bank seed | used for |
|---|---|---|---|
| train | 2000 | 101 | practice |
| val | 100 | 202 | development |
| test | 200 | 303 | held-out |

Each episode also draws its sensor state (depth noise, the cameras' calibration error, lights) from its reset seed.
