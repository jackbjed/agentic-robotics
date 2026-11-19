"""pick / clutter-pick, initial version: the robot's pick tool (astra_skills_real.SkillEpisode.pick_direct: wrist look,
the object's mask by name, a grasp planned on the wrist depth, approach, descent, close, check lift, lift), started
from the object's place in the scene camera. Identical in behaviour to initial_skills/clutter_pick."""
from helpers.program import log_events, pick, setup_program


def run(ctx):
    prog = setup_program(ctx)
    r, extra = pick(ctx, prog, ctx.task["object"], ctx.task.get("hint"))
    log_events(ctx, prog)
    return dict(result=r, detail=extra)
