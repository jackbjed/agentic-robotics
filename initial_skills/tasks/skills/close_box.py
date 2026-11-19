"""close-box, initial version: the box located in the scene camera, then the robot's lid skill (task_box.BoxProgram
.f_close): the gripper closed; the box surveyed by the wrist camera from 42 cm (its rim, the open lid's angle, length
and hinge axis); A: the side of a finger put behind the lid 2 cm below its top edge and the lid tipped over the hinge
in 4-degree steps to 75 deg; B: the fingertip pressed on the lid's outer face (60 % of its length) along the arc in
4-degree steps until shut; up, and a second survey: "ok" if no open lid is seen."""
from helpers.program import add_step, log_events, setup_program
from helpers.scene import find_in_scene


def run(ctx):
    prog = setup_program(ctx)
    box = find_in_scene(ctx, ctx.task["object"], cap_z=None)
    if box is None:
        return dict(result="not found", detail="the scene camera does not show the box")
    i = add_step(ctx, prog, "close", ctx.task["object"], box["p"])
    AS = ctx.library
    try:
        prog.box = None
        r = prog.f_close(i)
    except (AS.T.Unreachable, AS.F.Unseen) as e:
        r = f"{type(e).__name__}: {str(e)[:150]}"
    log_events(ctx, prog)
    return dict(result=r)
