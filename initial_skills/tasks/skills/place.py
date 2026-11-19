"""place, initial version: the white box located in the scene camera first (before the arm can hide it), then the robot's
pick tool on the object, then the robot's put-in: the box surveyed with the wrist camera (task_box.BoxProgram.survey:
rim, size, the open lid), the held object carried over the box's middle and dropped in (f_in), the hand withdrawn."""
from helpers.program import add_step, log_events, pick, setup_program
from helpers.scene import find_in_scene


def run(ctx):
    prog = setup_program(ctx)
    dest = ctx.task.get("destination", "white box")
    box = find_in_scene(ctx, dest, cap_z=None)
    if box is None:
        return dict(result="not found", detail=f"the scene camera does not show the {dest}")
    r, extra = pick(ctx, prog, ctx.task["object"])
    if r != "ok":
        log_events(ctx, prog)
        return dict(result=r, detail=extra)
    i = add_step(ctx, prog, "in", dest, box["p"])
    AS = ctx.library
    try:
        prog.box = None
        prog.survey(i)
        prog.box = prog.targets[i][0]
        r = prog.f_in(i)
    except (AS.T.Unreachable, AS.F.Unseen) as e:
        r = f"{type(e).__name__}: {str(e)[:150]}"
    log_events(ctx, prog)
    return dict(result=r)
