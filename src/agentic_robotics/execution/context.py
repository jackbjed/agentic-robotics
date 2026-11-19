"""What a skill program receives: run(ctx) -> dict(result=..., ...). See docs/skill_api.md.

  ctx.task         dict: kind, instruction, object (the object's name). Nothing about where it is.
  ctx.robot        the robot (robot/api.py): RealSim over the world socket; motion, cameras, joint/gripper/force state
  ctx.segmenter    perception (perception/client.py): segment(rgb, phrase), segment_points(rgb, pts, labels)
  ctx.library      the vendored robot skill library (astra_skills_real module: SkillProgram, pick_direct, ...)
  ctx.perception   the library's perception object over ctx.robot + ctx.segmenter (library_perception)
  ctx.log(what, **data)       an event in the episode's log (robot time stamped by the server)
  ctx.save_image(name, rgb)   a debug image in the episode's program/ directory
  ctx.out          the episode's program/ directory (writable)
  ctx.time_left()  robot seconds left before the server refuses motion (the episode limit)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SkillContext:
    task: dict
    robot: object
    segmenter: object
    library: object
    perception: object
    out: Path
    link: object
    t0: float
    limit_s: float
    events: list = field(default_factory=list)

    def log(self, what, **data):
        self.events.append(dict(what=what, **data))
        try:
            self.link.call("event", what=str(what), data=data)
        except Exception:
            pass

    def save_image(self, name, rgb):
        import cv2
        p = Path(self.out) / f"{name}.png"
        cv2.imwrite(str(p), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        return p

    def time_left(self):
        return self.limit_s - (self.robot.backend.now() - self.t0)
