"""The robot skill library's program object, set up as the robot's pick tool does (astra_skills_real.SkillEpisode),
and the pieces the task skills share."""
import types

import numpy as np

from helpers.scene import find_in_scene


def setup_program(ctx):
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


def add_step(ctx, prog, kind, phrase, p, ray=None):
    prog.steps.append((kind, phrase))
    ctx.perception.seeds.append(dict(p=np.asarray(p, float), ray=ray))
    return len(prog.steps) - 1


def shim(prog):
    """The parts of an astra_skills_real.SkillEpisode its pick_direct uses (no Astra here)."""
    return types.SimpleNamespace(prog=prog, views={}, scene_px=lambda X: None, last_dbg=lambda name: None, aside_where=None)


def pick(ctx, prog, phrase, seed=None):
    """The robot's pick tool (pick_direct) on the named object; seed: its rough place (else the scene camera's).
    -> (result, detail)"""
    AS = ctx.library
    if seed is None:
        seed = find_in_scene(ctx, phrase)
        if seed is None:
            return "not found", "the scene camera does not show it"
    ctx.log("seed", object=phrase, p=[round(float(v), 4) for v in seed["p"]])
    i = add_step(ctx, prog, "pick", phrase, seed["p"], seed.get("ray"))
    try:
        r, extra, _ = AS.SkillEpisode.pick_direct(shim(prog), i)
    except (AS.T.Unreachable, AS.F.Unseen) as e:
        r, extra = type(e).__name__, str(e)[:200]
    return r, extra


def log_events(ctx, prog):
    """The library program's events (with their numbers) into the episode's events.jsonl."""
    for e in prog.events:
        extra = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in e.items()
                 if k not in ("what", "t") and isinstance(v, (int, float, str, bool, type(None)))}
        ctx.log(e["what"], t_program=round(e["t"], 2), **extra)
