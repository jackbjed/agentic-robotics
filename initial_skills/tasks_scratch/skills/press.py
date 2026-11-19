"""press, scratch version: no skill for this task yet (learning it is the agent's job). It only locates
the task's object in the scene camera and stops. The robot skill library (ctx.library; its source in reference/) has
routines that may be used, adapted or replaced."""
from helpers.scene import find_in_scene


def run(ctx):
    seen = find_in_scene(ctx, ctx.task["object"], cap_z=None)
    ctx.log("the task's object in the scene camera", p=None if seen is None else seen["p"])
    return dict(result="not implemented")
