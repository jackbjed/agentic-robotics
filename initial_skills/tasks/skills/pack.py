"""pack, initial (ported) version: the robot's own box program as a sequence, as real_box.py's plan runs it: the white box
located in the scene camera first (before the arm can hide it); for each named object, the robot's pick tool, then the
put-in (the box surveyed by the wrist camera, the held object dropped over its middle); then the lid skill
(task_box.BoxProgram.f_close). The hand-written baseline for the pack task (no practice)."""
from helpers.program import add_step, log_events, pick, setup_program
from helpers.scene import find_in_scene


def run(ctx):
    prog = setup_program(ctx)
    AS = ctx.library
    dest = ctx.task.get("destination", "white box")
    box = find_in_scene(ctx, dest, cap_z=None)
    if box is None:
        return dict(result="not found", detail=f"the scene camera does not show the {dest}")
    done = []
    for name in ctx.task.get("objects") or [ctx.task["object"]]:
        r, extra = pick(ctx, prog, name)
        if r != "ok":
            log_events(ctx, prog)
            return dict(result=f"{name}: {r}", detail=extra, packed=done)
        i = add_step(ctx, prog, "in", dest, box["p"])
        try:                                                  # (as place.py: the box surveyed before each drop)
            prog.box = None
            prog.survey(i)
            prog.box = prog.targets[i][0]
            r = prog.f_in(i)
        except (AS.T.Unreachable, AS.F.Unseen) as e:
            r = f"{type(e).__name__}: {str(e)[:150]}"
        if r != "ok":
            log_events(ctx, prog)
            return dict(result=f"{name} into the box: {r}", packed=done)
        done.append(name)
    i = add_step(ctx, prog, "close", dest, box["p"])
    try:
        prog.box = None
        r = prog.f_close(i)
    except (AS.T.Unreachable, AS.F.Unseen) as e:
        r = f"{type(e).__name__}: {str(e)[:150]}"
    log_events(ctx, prog)
    return dict(result=r, packed=done)
