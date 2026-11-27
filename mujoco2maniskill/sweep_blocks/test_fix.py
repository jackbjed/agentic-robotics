"""The two faults seen in the zero-shot runs, replayed: (1) the right arm sent from its start pose to the hand-over
point with its fingers down (it swung behind the robot and answered done); (2) an arm sent into something solid."""
import sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from sweep_blocks.env import SweepBlocks
from sweep_blocks.program_api import X5Robot
env = SweepBlocks(layout=4, cameras=()); r = X5Robot(env)
res = r.move("right", (7.7, -200.49, 254.9), (0, 0, -1), (-0.47, -0.88, 0))
print("1. to the hand-over point:", res, "| tool", r.state("right")["tcp_mm"])
res = r.move("right", (7.7, -200.49, 174.9), (0, 0, -1), (-0.47, -0.88, 0)); print("   down 80:", res["done"], r.state("right")["tcp_mm"])
print("   home:", r.home("right")["done"])
res = r.move("left", (-300, -100, 60), (0, 0, -1), (1, 0, 0)); print("2. left arm above the table:", res["done"], r.state("left")["tcp_mm"])
res = r.move("left", (-300, -100, -40), (0, 0, -1), (1, 0, 0)); print("   sent 40 mm INTO the table:", res, "| tool", r.state("left")["tcp_mm"])
env.close()
