"""The bimanual environments (docs/bimanual.md): the rig's two Pandas in the twin's scene, with the rig's parallel
grippers (sim_bimanual_grippers) or Sharpa Wave hands (sim_bimanual_dexterous).

    from agentic_robotics import bimanual
    S = bimanual.make("sim_bimanual_grippers", seed=0)          # sensors="d415" (default) | "ideal"
"""
from __future__ import annotations

ENVS = ("sim_bimanual_grippers", "sim_bimanual_dexterous")


def make(name, **kw):
    """-> GripperSim / DexterousSim. kw: seed, sensors ("d415" | "ideal"), layout, record, out."""
    if name == "sim_bimanual_grippers":
        from .grippers import GripperSim
        return GripperSim(**kw)
    if name == "sim_bimanual_dexterous":
        from .dexterous import DexterousSim
        return DexterousSim(**kw)
    raise KeyError(f"unknown environment {name!r}: {ENVS}")
