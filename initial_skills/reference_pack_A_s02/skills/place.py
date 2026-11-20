"""place-box, REFERENCE program for certifying evaluation-set episodes (evaluation/evalsets.py; never an agent's
starting point): the agent's own pack skill (pack_A_r1 checkpoint s02, written from its own pick) given the one
named object: it picks it, puts it into the white box, then closes the lid (the in-box check has passed by then)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("pack_skill", str(Path(__file__).with_name("pack.py")))
_pack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pack)


def run(ctx):
    ctx.task = dict(ctx.task, objects=[ctx.task["object"]])
    return _pack.run(ctx)
