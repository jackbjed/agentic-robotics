# Tasks

Every task runs on the same robot, cameras (the realistic D415 depth), perception tools and time limit (60 s of robot
time). The program is told `ctx.task`: `kind`, `instruction`, `object` (its name), and for some tasks `destination` or
`part`. Success is judged on the true state every 1/30 s of robot time, and means the condition held for 1 s.

| task | told (example) | success |
|---|---|---|
| clutter-pick | "Pick up the red tape roll and lift it." (2–4 objects around it, gaps by level 0–3) | the target ≥ 5 cm above its rest height, held by both fingers, nothing else lifted |
| pick | "Pick up the glue bottle and lift it." (0–2 objects ≥ 6 cm away) | as clutter-pick |
| place | "Put the yellow tape roll into the white box." (the box open, the lid at 100–120°) | inside the box's walls, released (no finger touching it), the box upright, nothing else in the box, nothing off the table |
| close-box | "Close the lid of the white box." (the lid open 70–120°) | the lid < 8°, the box upright and not shoved more than 10 cm |
| press | "Press the button on top of the kettle's handle (it releases the kettle's lid)." | the kettle's lid > 30° open (the 16 mm button cap pressed 2.5 mm releases the latch; a spring opens the lid), the kettle upright |
| insert | "Put the short wooden cylinder onto one of the vertical wooden sticks of the wooden stand with three vertical sticks: ..." | the cylinder (40 × 40 mm, 22 mm hole) on one of the 18 mm sticks (~17 cm tall), below its tip, released |

Objects:
- **pick, place:** tape rolls (red Ø47, yellow Ø52, white Ø50 mm), the black block with holes (71 × 71 × 22 mm: only
  ~4 mm per side in the 80 mm gripper), the glue bottle (55 × 41 × 133 mm), the screwdriver (190 mm), the coiled
  cable.
- **the white box:** 191 × 193 × 58 mm, its lid hinged on one side.
- **the kettle:** 1.36 kg, Ø14 cm, 18 cm tall, its button on top of the handle.
- **the stand:** three 18 mm sticks, ~17 cm tall.
- **the cylinder:** 28.6 g.
