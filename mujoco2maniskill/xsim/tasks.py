"""The experiments' tasks. A task = its text (the same for every condition, and what the evaluator tests), its hidden
TARGET world (tasks/<name>_target.py: fixed objects, only their places vary), its episode length, and how its final
out-of-distribution test scenes are made from in-distribution ones (tools/vary_scene.py's options).

The text names every final-state condition the hidden evaluator tests, with its tolerance, and nothing about the
objects' sizes, masses or frictions."""
from pathlib import Path

HERE = Path(__file__).resolve().parent

INSERT = """Put the cylinder on the stick.

On the table there are a cylinder with a hole through it (standing on one of its flat ends, the hole vertical), a
stand with one vertical stick, and other things. Pick up the cylinder and thread it onto the stick, so that the stick
passes through its hole, and let go of it. Then open the gripper fully and bring it back to its start pose.

The episode succeeds when, at its end, all of this holds: the cylinder is on the stick (the stick through its hole,
the cylinder resting down on the stand); the stand is upright (within 5 degrees); the robot touches neither the
cylinder nor the stand; the gripper is open (more than 70 mm) and back at its start pose (its tool point within 25 mm
of where it started, the fingers pointing straight down).

What varies between episodes: where the cylinder, the stand and the other things stand on the table and how they are
turned. The things themselves (their shapes, sizes, weights, surfaces) are the same in every episode."""

SWEEP = """Sweep the small round things into the marked area with the sponge.

On the table there are a sponge, a number of small loose round things lying together, a rectangular area marked on
the table with tape, and one other thing. Take the sponge and use it to sweep the small things into the marked area.
Leave the other thing where it is. Then let go of the sponge, open the gripper fully and bring it back to its start
pose.

The episode succeeds when, at its end, all of this holds: at least 90 percent of the small things lie on the table
inside the marked area (their centres within the tape's outer half); the other thing has moved by no more than 30 mm
and has not tipped over; the robot does not touch the sponge; the gripper is open (more than 70 mm) and back at its
start pose (its tool point within 25 mm of where it started, the fingers pointing straight down).

What varies between episodes: where the sponge, the small things, the marked area and the other thing are on the
table and how they are turned. The things themselves (their shapes, sizes, weights, surfaces, number) are the same in
every episode."""

# RoboDojo's public text of sweep_blocks (task/RoboDojo/tasks/sweep_blocks.py: its detailed instruction and its scoring), with the user's
# edits of 2026-11-27: "brush" throughout (RoboDojo also says broom); its first line ("Pick up the broom, hand it over to the right hand,
# then use the dustpan to sweep the blocks.") replaced, because it imposes a hand-over that the judge does not test;
# followed by the no-gripping rule, the judge's conditions in plain words with their tolerances, and what varies
SWEEP_BLOCKS = """Use the brush and dustpan to sweep all three blocks into the dustpan.

Use the brush and dustpan to sweep all target objects from the tabletop into the dustpan. Avoid pushing objects outside the pan or toward the table edge, and check for objects left in their original positions, scattered nearby, or stopped at the pan entrance. Continue until every target object is completely inside and remains contained. At completion, the dustpan must be more than 10 cm to the left of the brush along the world x-axis, measured between their object origins. Finish with the dustpan level on the table and positioned to the left of the brush, then fully open both grippers and return both arms to their initial poses.

Scoring:
- 0: The blocks are not fully swept into the dustpan.
- 100: The dustpan is positioned left of the brush, stably placed upright on the table, all blocks are inside the dustpan, and the robot returns to origin.

The blocks are to be SWEPT into the dustpan: a block must not be picked up. An episode in which a block is gripped at any time (held between the two fingers of a gripper; a push is not a grip) is a failure.

The episode succeeds as soon as all of this holds at once: every block is inside the dustpan (seen from above, its centre is over the dustpan's floor, behind the front lip); the dustpan is level (tilted by less than 5 degrees) and on the table (not more than 10 mm above where it started); the brush's origin (the middle of its length) is more than 100 mm further along +x than the dustpan's origin (the middle of the dustpan); both arms are back at their start poses (each hand within 150 mm of where it started along every axis, and turned by less than 20 degrees from how it started); both grippers are open (more than 70 mm between the fingers); and no block has been gripped so far in the episode.

What varies between episodes: where the dustpan, the brush and the blocks lie on the table and how they are turned, their colours, and the table's colour or pattern. The things themselves (their shapes, sizes, weights, surfaces) and the number of blocks (3) are the same in every episode."""

TASKS = {
    "sweep_blocks": dict(rig="x5", text=SWEEP_BLOCKS, episode_s=40.0, reference=HERE.parent / "sweep_blocks" / "reference_sweep.py",
                         ood=dict(ood_appearance=dict(looks="ood"), ood_dimensions=dict(cube_scale=1.3),
                                  ood_physics=dict(cube_mass_scale=3.0, cube_friction_scale=0.5, table_friction=0.4),
                                  # (added 2026-11-27, after the first A-D run: every development and test scene so far has the brush on the
                                  #  robot's left and the dustpan's mouth towards +x; this split has them the other way round. Evaluation only)
                                  ood_mirrored=dict(mirror=True))),
    "insert": dict(text=INSERT, target_world=HERE / "tasks" / "insert_target.py", episode_s=600.0,
                   required=["w_cyl", "w_stand"], reference=HERE / "reference" / "insert_ref.py",
                   ood=dict(ood_appearance=dict(recolor=True), ood_dimensions=dict(size_scale=1.15, bodies="w_cyl"),
                            ood_heights=dict(platform_mm=30.0, bodies="w_cyl"), ood_physics=dict(friction=0.25, mass_scale=3.0, bodies="w_cyl"))),
    "sweep": dict(text=SWEEP, target_world=HERE / "tasks" / "sweep_target.py", episode_s=600.0,
                  required=["w_sponge", "w_tape", "w_obj_0"], reference=HERE / "reference" / "sweep_ref.py",
                  ood=dict(ood_appearance=dict(recolor=True), ood_dimensions=dict(size_scale=1.5, bodies="w_obj_"),
                           ood_heights=dict(platform_mm=20.0, bodies="w_sponge"), ood_physics=dict(table_friction=0.3, friction=0.3, mass_scale=5.0, bodies="w_obj_"))),
}
