# Tasks: three skills

Every episode, in the real world and in your simulator, starts with **the arm at its home pose** (the real robot's
start). The program is told `ctx.task`: `kind`, `instruction`, `object` (the name), and for place-box `destination`.
Success is judged on the true state every 1/30 s of robot time and means the condition held for 1 s. Each task has its
own entry file.

| task | entry | told (example) | success |
|---|---|---|---|
| place-box | `skills/place.py` | "Put the screwdriver into the white box." (kind "place"; the box open; 1–3 other objects on the table) | the object inside the box's walls, released (no finger touching it), the box upright (< 10°), nothing else put in, nothing knocked off the table; 90 s of robot time |
| close-box | `skills/close_box.py` | "Close the lid of the white box." (kind "close_box"; the lid open; 0–2 other objects) | the lid closed to < 8°, the box upright and not shoved more than 10 cm; 60 s |
| clutter-pick-any | `skills/pick.py` | "Pick up the white tape roll and lift it." (kind "clutter_pick"; 2–4 other objects close around it) | the object ≥ 5 cm above where it rested, held by both fingers, nothing else lifted; 60 s |

**The objects, by the names the tasks use:**
- **place-box and clutter-pick-any name one of:** the red tape roll, the yellow tape roll, the white tape roll, the
  coiled black cable, the screwdriver.
- **Other objects that may be on the table:** those, the black block with holes, the glue bottle, the sticky note pad.
- **the white box**, with a lid hinged on one side.

Their sizes, shapes, weights and how they lie are for you to find out in the real world: nothing here gives them.

**In your simulator** the same checks run on the names you declare in `sim/sim.yaml` (docs/selfsim.md): declare the
objects under these names, and the box as "white box", so that your skills are told the same words in both worlds.

Real-world layouts: banks of settled layouts with random positions and orientations (`real look --task T --idx I` shows
layout I). The evaluation uses other layouts of the same kinds.
