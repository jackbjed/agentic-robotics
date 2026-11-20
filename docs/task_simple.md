# Tasks: three skills

This simulator is **approximate**: your skills may be tested with a different table height, different objects and
different visuals than you practise with.

Every episode starts with **the arm at its home pose**, the two D415 cameras at their mounts, plain colour, SAM3
segmentation. The program is told `ctx.task`: `kind`, `instruction`, `object` (the name), and for place-box
`destination`. Success is judged on the true state every 1/30 s of robot time and means the condition held for 1 s.
Each task has its own entry file.

| task | entry | told (example) | success |
|---|---|---|---|
| place-box | `skills/place.py` | "Put the blue cube into the grey box." (kind "place"; the box open, its lid at 100–120°; 1–3 other objects) | the object inside the box's walls, released (no finger touching it), the box upright, nothing else put in, nothing off the table; 90 s of robot time |
| close-box | `skills/close_box.py` | "Close the lid of the grey box." (kind "close_box"; the lid open 70–120°; 0–2 other objects) | the lid < 8°, the box upright and not shoved more than 10 cm |
| clutter-pick-any | `skills/pick.py` | "Pick up the red cylinder and lift it." (kind "clutter_pick"; 2–4 other objects 1.5–6 cm from it) | the object ≥ 5 cm above where it rested, held by both fingers, nothing else lifted |

Objects in practice:
- **place-box and clutter-pick-any name one of:** the red cube, the green cube, the blue cube (40 mm), the red
  cylinder, the yellow cylinder (Ø40 mm, 50 mm tall). The other objects on the table are the same kinds.
- **the grey box:** 220 × 170 × 70 mm, its lid hinged on one side.

Layouts: banks of settled layouts with random positions and orientations: train (practice) and val (development).
