"""A look at the rebuilt task: one layout, the cameras' pictures, a second of holding still, the success check.

    <robomme python> -I sweep_blocks/demo.py [layout]      -> out/robodojo_sweep/layout_<k>.jpg"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from sweep_blocks.env import SweepBlocks  # noqa: E402

k = int(sys.argv[1]) if len(sys.argv) > 1 else 4
rand = len(sys.argv) > 2 and sys.argv[2] == "random"
env = SweepBlocks(layout=k, random=rand, cameras=("head", "left_wrist", "right_wrist", "overview"))
obs = env.reset()
print("instruction:", obs["instruction"])
print("objects:", {n: np.round(env.pose(n)[0], 3).tolist() for n in env.U.objects})
print("moved while settling (mm):", {n: round(float(np.linalg.norm(env.pose(n)[0] - np.asarray(env.U.start[n].p)) * 1000), 1) for n in env.U.objects})
hold = np.r_[np.zeros(6), 1.0, np.zeros(6), 1.0]
for _ in range(25):
    obs, done, info = env.step(hold)
print("after 25 actions of holding still:", json.dumps(info))
print("joint drift (mrad):", round(float(np.abs(np.r_[obs["left_arm_joints"], obs["right_arm_joints"]]).max() * 1000), 3), "grippers", round(obs["left_gripper"], 3), round(obs["right_gripper"], 3))
out = HERE / "out" / "robodojo_sweep"; out.mkdir(parents=True, exist_ok=True)
ims = []
for c in ("head", "overview", "left_wrist", "right_wrist"):
    im = obs["images"][c][..., ::-1].copy()
    cv2.putText(im, c, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4); cv2.putText(im, c, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
    ims.append(im)
tag = f"{'random_' if rand else ''}{k}"
cv2.imwrite(str(out / f"layout_{tag}.jpg"), np.vstack([np.hstack(ims[:2]), np.hstack(ims[2:])]), [cv2.IMWRITE_JPEG_QUALITY, 90])
cv2.imwrite(str(out / f"head_{tag}.jpg"), ims[0])
print(out / f"layout_{tag}.jpg")
env.close()
