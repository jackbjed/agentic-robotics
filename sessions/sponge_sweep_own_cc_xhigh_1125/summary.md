SPONGE SWEEP SKILL - session summary (final: program v31, world w21)

THE TASK AS BUILT (world w21)
Task text (3 phrasings), e.g. "Pick up the yellow sponge with the green scrubbing side (it lies on a small block)
and use it to sweep the lentils on the table into the target area, the white sheet of paper ... Leave everything
else where it is." It names the sponge's colour(s), the kind of small objects, and the marker (paper or tape, colour).
Table: top at -24..+35 mm in the robot frame; 9 colours, 30% with a wood/speckle/blotch pattern.
Sponge: rigid box with soft contact, 85-130 x 45-64 x 25-45 mm (thickness also >= 21 mm - table height, see
limits below), 8 colours, half with a 3-7 mm scrub layer of another colour on top or bottom, 5-60 g
(25-120 kg/m3), friction 0.5-1.1. 45% on the table, 55% on a stand (block 15-80 mm tall, 10-40 mm larger, 0.3-0.8 kg).
Small objects: one kind per episode, 8-28 pieces, +-8% size scatter: lentils (lens 5-7 mm, 2.2-2.8 thick), beans
(12-16 x 7-9 x 6-7), chickpeas (8-10 mm spheres), dried peas (6-8), coffee beans, cereal pieces (flat lenses 10-14 mm),
pasta (capsules), beads (multicoloured spheres), crumbs (small boxes); densities 0.4-1.3 g/cm3, >= 0.1 g each;
friction 0.3-0.8, rolling resistance for round ones. Laid out as one cluster, a spread, or 2-3 small clusters,
50-160 mm outside the target, within the arm's reach. Colours chosen to contrast with table and marker.
Target: 50% a loose sheet of paper (0.5 mm, 100x100 .. 236x148 mm, 80-160 g/m2, friction 0.4-0.7, 9 colours; it
can be pushed), 50% a rectangle of tape on the table (inner side 130-230 mm, tape 15-25 mm wide, 7 colours).
Distractors: 0-3 of mug, box, bottle, fruit, pen, anywhere not in the way between objects and target.
success(): >= 90% of the objects have their centre inside the target (sheet: its final pose; tape: up to the
tape's middle line) and lie on the table; every distractor moved <= 30 mm (mug/box/bottle not tipped > 25 deg);
the sponge was taken (moved > 30 mm, off its stand, or turned > 20 deg); the robot has let go of the sponge.
A program that does nothing fails (checked: 0/4).

WHAT THE SKILL DOES
1. Reads colours/kinds from the task text (sponge colour + scrub colour, paper/tape + colour, object name).
2. Looks from above with the wrist camera (TCP z 520, turns the wrist back if it is the other way round) and
   builds a 1 mm/px top-down map of the table: colour (image projected on the table plane), heights from the
   wrist depth (scene camera depth, plane-fitted, only where the wrist sees no depth), a smooth model of the
   table colour (lighting varies).
3. Finds: tall things (sponge, stand, distractors = obstacles); the target (large flat region: filled = sheet,
   frame = tape, matched to the named kind/colour); the sponge (tall, elongated, size and named colour; extra
   looks nearer the robot if doubtful); small objects (blobs that differ from the local background, not table
   colour, not shadows, not seen on tall things; clumps split into 15 mm points; long thin blobs = obstacles).
4. Close look above the sponge: exact pose, top height, height of what it lies on. Grasp across its width,
   pads 18 mm below its top (fingertips >= 2.5 mm above its support); on a low table shallower so it sticks out.
5. Touches the table with the sponge (stop on contact) at two free places; the lower is the sweep height.
   If the fingertips would be < 5 mm above the table, grips again higher; if the sponge cannot reach the table
   from the arm's lowest pose, puts it down, measures its thickness and grips it higher.
6. Plans straight strokes: each object (farthest first) is pushed towards the target (centre, or other target
   points / off-centre on the sponge face to get round obstacles), starting 40 (or 22) mm behind it, ending
   ~50 mm inside a tape outline (never past its centre) or 45 mm past a sheet's edge (32 mm in later rounds);
   objects in the same
   corridor go together. Skips strokes that leave the reach or hit obstacles. The push face is the sponge's long
   side; over a sheet the sponge is raised 5.5 mm so it does not drag the sheet.
7. After each round: puts the sponge down on a free spot, looks again (tracks a moved sheet by fitting its
   rectangle), regrips it at the same pose, sweeps what is still outside. Ignores a detection where a stroke
   already went (a mark, not an object). Ends when nothing is outside or near 560 s; puts the sponge down.

TESTS AND RESULTS
w21 (final world), v31: 35/40 on scenes s1-s40 (used for tuning), 6/8 on fresh scenes s41-s48 (so expect
roughly 75-85% on new seeds); a variant (v33: stricter "inside" margin on sheets) did not help. Successes take
~100-450 s (mean ~196 s). Tape targets almost always succeed. Low tables (-24 mm), sponges on 15-80 mm stands,
scrub layers, lentils/beans/peas/beads/pasta/crumbs, patterned tables were all solved in several scenes.
Failures (v31): loose paper targets with many objects (the sheet gets dragged; objects end at its edge: 1/20,
5/24 in two scenes), one object left in 8-object scenes (7/8 < 90%), a pen near the robot knocked off by the
arm's body, a pen at the far edge, beans hidden/missed in one scene.

LIMITS FOUND (they shape the world)
- The arm's safety limits are fixed in the robot frame (TCP z >= 10 mm, fingertips >= -4 mm at z = 0) even if
  the table is lower: a thin sponge cannot reach a table lower than about (thickness - 21) mm below z = 0.
- The 80 mm opening and the pads 16 mm above the fingertips: sponges wider than ~65 mm or thinner than ~25 mm
  cannot be held from above (world limited to 45-64 x 25-45 mm).
- In simulation, thin cylinders (lentils) exploded when swept: lentils/cereal are lens-shaped ellipsoids.

ON THE REAL ROBOT: EXPECTATIONS, WHAT TO MEASURE FIRST
- Measure first: the wrist camera's pose error (absolute map error here 10-15 mm; strokes keep 22-40 mm margins);
  the table height under the limits; the force at which stop_on_contact stops on a real sponge (sweep height =
  that contact height - 0.5 mm; over paper +5.5 mm assumes the sponge sinks ~2.5 mm at 4 N - re-tune for a real
  foam); how much a real sheet slides (tape it down if it slides more than a few cm).
- Perception is classical colour/depth (no learned model): real shadows, glare, wood grain, black objects on a
  dark table or white paper on a white table will produce more false/missed objects than in simulation; the
  named colours in the task text help find sponge and marker.
- Uncertain: real lentils/rice under a real compliant sponge (they may pass under or stick), rolling peas,
  real sponge friction/drag (safety stops at 40 N were seen when the finger ploughed lentils), objects hidden
  under the parked sponge, distractors close to the robot's base (the arm's body is not checked for collisions
  with low objects).
