"""real2sim (docs/real2sim.md): an agent that starts without a simulator observes the "real world" (the photorealistic
twin, condition A) through the robot alone, reconstructs the scene as its own simulator (sim/ in its workspace: an MJCF
scene on the robot kit, a layout sampler, a sensor model), and practises its skills there.

  KIT            the robot kit (scripts/build_robot_kit.py): the twin's robot without the scene (placeholders by name)
  hidden_paths() the scene's model files, hidden from every program sandbox of the experiment (with the kit as the
                 program's own planning model, nothing in the sandbox describes the scene)"""
from __future__ import annotations

from pathlib import Path

from .. import paths

KIT = Path(__file__).resolve().parent / "models" / "robot_kit.xml"


def hidden_paths():
    """The files that describe the scene: the twin's model, its objects' meshes, every condition's model."""
    M = paths.VENDOR / "panda_twin" / "model"
    return [M / "twin_6522_wedge.xml", M / "assets" / "twin_6522", paths.SRC / "agentic_robotics" / "sim" / "models"]
