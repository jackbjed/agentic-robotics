"""The task families (`--tasks clutter-pick,pick,place,close-box,press,insert`). Each: the object set its scenes use, its
layout banks, what the program is told, which program entry runs it, and how the world judges it (sim/scene.py
judge_*; every 1/30 s of robot time, success = the condition held for 1 s):

  clutter-pick  the red or yellow tape roll out of clutter (2-4 objects, gaps by level 0-3); lifted >= 5 cm, held by both
                fingers, nothing else lifted                                                    [the milestone-1 task]
  pick          one named object (tape rolls, the block with holes, the glue bottle, the screwdriver, the cable) with
                0-2 others >= 6 cm away; the same lift check
  place         the named object into the open white box; inside its walls, released (no finger touching it), the box
                upright (< 10 deg), nothing else put in, nothing off the table
  close-box     the white box's lid (open 70-120 deg) closed: < 8 deg, the box upright, not shoved > 10 cm
  press         the kettle's lid-release button (on top of its handle; 16 mm cap, released at 2.5 mm of travel): the
                lid open > 30 deg (the latch gave way, its spring opened it), the kettle upright
  insert        the wooden cylinder (40 x 40 mm, 22 mm bore) onto one of the stand's three wooden sticks (18 mm,
                ~17 cm tall): the cylinder's axis within 2 mm + 3 mm of a stick's, below its tip, released
  pack          2-3 named objects into the open white box, then its lid closed: every one inside its walls, released,
                the lid < 8 deg, the box upright, nothing else in it, nothing off the table (the real robot's box task,
                2026-11-19); 180 s of robot time
  place-box     the named object into the open white box (place's check), among 1-3 other objects: the red, yellow and
                white tape rolls, the cable, the screwdriver; 90 s of robot time (2026-11-19, user: "learn pick and place
                of the red tape, yellow tape, white tape, cable, screwdriver ... in the box ... tested on other objects")
  place-box-new the same task with objects place-box never practises: the black block with holes, the glue bottle
                (held-out only: in no practice or development suite). Both tell the program kind "place".
press and insert use the multi-task model (scripts/build_tasks_model.py); their scenes have plain MuJoCo colour only (no
scans of the kettle, stand or cylinder in the splat compositor)."""
from __future__ import annotations

TASKS = {
    "clutter-pick": dict(object_set="real", banks="layouts", judge="lift", entry="skills/pick.py", levels=[0, 1, 2, 3],
                         instruction="Pick up the {object} and lift it."),
    "pick": dict(object_set="real", banks="layouts_pick", judge="lift", entry="skills/pick.py", levels=[0],
                 targets=["tape_red", "tape_yellow", "tape_white", "hole_block", "glue", "screwdriver", "cable"],
                 instruction="Pick up the {object} and lift it."),
    "place": dict(object_set="real", banks="layouts_place", judge="in_box", entry="skills/place.py", levels=[0],
                  targets=["tape_red", "tape_yellow", "tape_white", "hole_block", "glue"], fixed=["box_white"],
                  instruction="Put the {object} into the white box.", destination="white box"),
    "close-box": dict(object_set="real", banks="layouts_close", judge="lid_closed", entry="skills/close_box.py", levels=[0],
                      targets=["box_white"], instruction="Close the lid of the {object}."),
    "pack": dict(object_set="real", banks="layouts_pack", judge="packed", entry="skills/pack.py", levels=[0],
                 targets=["tape_red", "tape_yellow", "tape_white", "hole_block", "screwdriver", "cable"], fixed=["box_white"],
                 instruction="Put {objects} into the {destination}, then close its lid.", destination="white box",
                 time_limit_s=180.0),
    "place-box": dict(object_set="real", banks="layouts_place_box", judge="in_box", entry="skills/place.py", levels=[0],
                      targets=["tape_red", "tape_yellow", "tape_white", "cable", "screwdriver"], fixed=["box_white"],
                      kind="place", instruction="Put the {object} into the {destination}.", destination="white box",
                      time_limit_s=90.0),
    "place-box-new": dict(object_set="real", banks="layouts_place_box_new", judge="in_box", entry="skills/place.py",
                          levels=[0], targets=["hole_block", "glue"], fixed=["box_white"], kind="place",
                          instruction="Put the {object} into the {destination}.", destination="white box", time_limit_s=90.0),
    "press": dict(object_set="tasks", banks="layouts_press", judge="kettle_open", entry="skills/press.py", levels=[0],
                  targets=["kettle"], colour_only="mujoco",
                  instruction="Press the button on top of the {object}'s handle (it releases the kettle's lid).",
                  part="the round lid-release button on top of the handle"),
    "insert": dict(object_set="tasks", banks="layouts_insert", judge="on_stick", entry="skills/insert.py", levels=[0],
                   targets=["wood_cyl"], fixed=["peg_stand"], colour_only="mujoco",
                   instruction="Put the {object} onto one of the vertical wooden sticks of the {destination}: the stick "
                               "has to go through the hole along the cylinder's axis.",
                   destination="wooden stand with three vertical sticks"),
}
DEFAULT = "clutter-pick"


def get(name):
    if name not in TASKS:
        raise KeyError(f"unknown task {name!r}: {', '.join(TASKS)}")
    return TASKS[name]


def parse(arg):
    names = [x.strip() for x in str(arg).split(",") if x.strip()]
    for n in names:
        get(n)
    return names
