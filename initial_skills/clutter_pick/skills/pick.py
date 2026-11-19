"""Clutter pick, initial version (the no-practice baseline).

The robot's own pick skill, unchanged: panda_twin_tracking astra_skills_real.SkillEpisode.pick_direct (11-13), the
clutter pick without its own clearing:
  1. a wrist-camera look over the object's expected place (no move if the view is already there);
  2. the object's mask by name (ctx.segmenter), its 3D points from the wrist depth;
  3. "something lies on it" -> stop (blocked); else a grasp the fingers fit into (CB.plan_grasps on the depth points:
     every closing direction, the opening = width + margin, the height where fingers, hand and camera clear all
     depth points), preferring the one that turns the hand least;
  4. approach (over it, 10 cm above its top), a slow descent, close, a 2.5 cm check lift (did anything else come
     up?), then the lift; one more grasp direction if the descent was blocked or the fingers closed on nothing.
Only the starting point is new: on the robot Astra marked the object in the scene image; here the program finds it
by name in the scene camera (find_in_scene). The parity check gives it the 11-18 baseline's hint instead.
run(ctx) -> dict(result=..., detail=...)."""
import types

import numpy as np

from helpers.scene import find_in_scene


def setup_program(ctx):
    """The library's program object, set up as astra_skills_real.SkillEpisode does for a pick."""
    AS = ctx.library
    prog = AS.SkillProgram(ctx.robot, ctx.perception, confirm_plan=False)
    prog.targets, prog.avoid, prog.box, prog.sizes, prog.reserved = {}, {}, None, {}, []
    prog.steps = ctx.perception.steps = []
    per = ctx.perception
    per.seeds, per.templates, per.track_roi, per.still_mask = [], [], None, None
    prog.est, prog.PUSH_MAX = None, 0.08
    prog.COLOUR_RULES = {}           # (the robot: no colour check; the segmentation's name score chooses)
    prog.dbg = lambda *a, **k: None  # (no debug images: saves time)
    prog.pre_view = None             # (no wrist view taken before the skill)
    return prog


def run(ctx):
    AS = ctx.library
    phrase = ctx.task["object"]
    prog = setup_program(ctx)
    seed = ctx.task.get("hint")                      # (parity checks only; never in practice or evaluation)
    if seed is None:
        seed = find_in_scene(ctx, phrase)
        if seed is None:
            ctx.log("not found in the scene camera", object=phrase)
            return dict(result="not found", detail="the scene camera does not show it")
    ctx.log("seed", p=[round(float(v), 4) for v in seed["p"]])
    prog.steps.append(("pick", phrase))
    ctx.perception.seeds.append(dict(p=np.asarray(seed["p"], float), ray=seed.get("ray")))
    ep = types.SimpleNamespace(prog=prog, views={}, scene_px=lambda X: None, last_dbg=lambda name: None, aside_where=None)
    try:
        r, extra, _ = AS.SkillEpisode.pick_direct(ep, 0)
    except (AS.T.Unreachable, AS.F.Unseen) as e:
        r, extra = type(e).__name__, str(e)[:200]
    for e in prog.events:
        ctx.log(e["what"], t_program=round(e["t"], 2))
    return dict(result=r, detail=extra)
