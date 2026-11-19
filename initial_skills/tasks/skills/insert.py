"""insert, initial version: the stand's stick tips located in the scene camera (the stand's highest points), one
stick surveyed with the empty hand (task_fast.FastProgram.survey: the wrist camera over it, the tip fitted in the
depth), the robot's pick tool on the cylinder, then the robot's thread-onto-a-stick (FastProgram.f_peg: carried over
the tip, lowered 1 mm at a time; on a hit (> 2 N) back up and retry 1.5 / 2.5 mm to the sides; release, withdraw)."""
from helpers.program import add_step, log_events, pick, setup_program
from helpers.scene import find_in_scene


def run(ctx):
    prog = setup_program(ctx)
    AS = ctx.library
    dest = ctx.task.get("destination", "wooden stand with three vertical sticks")
    tip = ctx.task.get("hint") or find_in_scene(ctx, dest, cap_z=None, top_frac=0.015)   # (hint: diagnostics only)
    if tip is None:
        return dict(result="not found", detail="the scene camera does not show the stand")
    j = add_step(ctx, prog, "peg", dest, tip["p"])
    try:
        AS.F.FastProgram.survey(prog, j)          # (the stick's tip measured with the empty hand)
    except (AS.T.Unreachable, AS.F.Unseen) as e:
        log_events(ctx, prog)
        return dict(result=f"stick not surveyed: {type(e).__name__}: {str(e)[:120]}")
    r, extra = pick(ctx, prog, ctx.task["object"])
    if r != "ok":
        log_events(ctx, prog)
        return dict(result=r, detail=extra)
    try:
        r = AS.F.FastProgram.f_on(prog, j, mode="peg")   # (= FastProgram.f_peg; the real program's f_on is the box set-down)
    except (AS.T.Unreachable, AS.F.Unseen) as e:
        r = f"{type(e).__name__}: {str(e)[:150]}"
    log_events(ctx, prog)
    return dict(result=r)
