# Task: put the named object into the white box

**What the program is told:** "Put the <object> into the white box." `ctx.task`: `kind` = "place", `object` = the
object's name, `destination` = "white box", `instruction`.

**Practice objects (and development evaluation):** the red tape roll (Ø47 mm), the yellow tape roll (Ø52 mm), the
white tape roll (Ø50 mm), the coiled black cable, the screwdriver (190 mm long). **The skill is also tested on other
objects, which are not in this list and never in practice** (in simulation and on the real robot): it must find and
put away the object it is told by name.

**The scene:**
- the white box, open: 191 × 193 × 58 mm, its lid hinged on one side and open at 100–120°;
- the target object, 16–32 cm from the box's middle;
- 1–3 other objects (from the twin's: tape rolls, a black block with holes, the cable, the screwdriver, a glue bottle,
  a sticky note pad), at least 9 cm apart.

The objects are settled on the table. **The arm starts at its home pose** (the same joints as the real robot's start),
not over the target.

**Success.** Judged on the true state every 1/30 s of robot time: the named object inside the box's walls, released (no
finger touching it), the box upright (< 10°), nothing else put in the box, nothing off the table, for 1 s.

The episode ends when the program returns. Motion is refused after 90 s of robot time, and the process is killed after
900 s of wall time.

**Layouts.** Banks of pre-generated, settled layouts: train (practice), val (development), test (held-out). Each
episode also draws its sensor state (depth noise, the cameras' calibration error, lights) from its reset seed.
