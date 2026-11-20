# Tasks: four skills

Every episode starts with **the arm at its home pose** (the real robot's start), the two D415s at the real rig's
calibrated poses, plain colour, SAM3 segmentation. The program is told `ctx.task`: `kind`, `instruction`, `object` (the
name), and for some tasks `destination`. Success is judged on the true state every 1/30 s of robot time and means the
condition held for 1 s. Each task has its own entry file.

| task | entry | told (example) | success |
|---|---|---|---|
| place-box | `skills/place.py` | "Put the screwdriver into the white box." (kind "place"; the box open, its lid at 100–120°; 1–3 other objects) | the object inside the box's walls, released (no finger touching it), the box upright, nothing else put in, nothing off the table; 90 s of robot time |
| close-box | `skills/close_box.py` | "Close the lid of the white box." (kind "close_box"; the lid open 70–120°; 0–2 other objects) | the lid < 8°, the box upright and not shoved more than 10 cm |
| clutter-pick-any | `skills/pick.py` | "Pick up the white tape roll and lift it." (kind "clutter_pick"; 2–4 other objects 1.5–6 cm from it) | the object ≥ 5 cm above where it rested, held by both fingers, nothing else lifted |
| press | `skills/press.py` | "Press the button on top of the kettle's handle (it releases the kettle's lid)." (kind "press") | the kettle's lid > 30° open (the 16 mm button cap pressed 2.5 mm releases the latch; a spring opens the lid), the kettle upright |

Objects:
- **place-box and clutter-pick-any name one of:** the red tape roll (Ø47 mm), the yellow tape roll (Ø52 mm), the white
  tape roll (Ø50 mm), the coiled black cable, the screwdriver (190 mm). Other objects on the table: those, the black
  block with holes, the glue bottle, the sticky note pad.
- **the white box:** 191 × 193 × 58 mm, its lid hinged on one side.
- **the kettle:** 1.36 kg, Ø14 cm, 18 cm tall, its lid-release button on top of the handle.

Layouts: banks of settled layouts with random positions and orientations: train (practice), val (development), and
the evaluation sets. Each episode also draws its sensor state (depth noise, the cameras' calibration error, lights)
from its reset seed.
