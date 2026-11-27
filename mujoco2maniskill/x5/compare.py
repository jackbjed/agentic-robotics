"""The same motion in both simulators: how far apart they are, and a side-by-side video.

    python3 x5/compare.py [mujoco-run-folder]      -> out/x5/compare.json, out/x5/side_by_side.mp4"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
O = HERE / "out" / "x5"
mj = sys.argv[1] if len(sys.argv) > 1 else "mujoco"
a, b = np.load(O / mj / "log.npz"), np.load(O / "maniskill" / "log.npz")
arm = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13]
dq = np.abs(a["q"] - b["q"])
tilt = lambda z: np.degrees(np.arccos(np.clip(z["cubeR"][:, 2, 2], -1, 1)))
rep = dict(mujoco_run=mj, seconds=float(a["t"][-1]), steps=int(len(a["t"])),
           arm_joint_difference_mrad=dict(mean=round(float(dq[:, arm].mean() * 1000), 3), max=round(float(dq[:, arm].max() * 1000), 2)),
           tracking_error_mrad=dict(mujoco_max=round(float(np.abs(a["q"] - a["cmd"])[:, arm].max() * 1000), 2),
                                    maniskill_max=round(float(np.abs(b["q"] - b["cmd"])[:, arm].max() * 1000), 2)),
           tool_point_difference_mm={s: dict(mean=round(float(np.linalg.norm(a["tcp" + s] - b["tcp" + s], axis=1).mean() * 1000), 3),
                                             max=round(float(np.linalg.norm(a["tcp" + s] - b["tcp" + s], axis=1).max() * 1000), 2)) for s in "LR"},
           finger_gap_on_cube_mm=dict(mujoco=round(float(a["q"][320, 14:16].sum() * 1000 + 0.8), 1), maniskill=round(float(b["q"][320, 14:16].sum() * 1000 + 0.8), 1), cube=40.0),
           cube=dict(highest_z_mm=dict(mujoco=round(float(a["cube"][:, 2].max() * 1000), 1), maniskill=round(float(b["cube"][:, 2].max() * 1000), 1)),
                     end_mm=dict(mujoco=np.round(a["cube"][-1] * 1000, 1).tolist(), maniskill=np.round(b["cube"][-1] * 1000, 1).tolist()),
                     largest_tilt_deg=dict(mujoco=round(float(tilt(a).max()), 1), maniskill=round(float(tilt(b).max()), 1)),
                     path_difference_mm=dict(mean=round(float(np.linalg.norm(a["cube"] - b["cube"], axis=1).mean() * 1000), 2),
                                             max=round(float(np.linalg.norm(a["cube"] - b["cube"], axis=1).max() * 1000), 2))))
(O / f"compare{'' if mj == 'mujoco' else '_' + mj}.json").write_text(json.dumps(rep, indent=1))
print(json.dumps(rep, indent=1))
out = O / f"side_by_side{'' if mj == 'mujoco' else '_' + mj}.mp4"
lab = "drawtext=text='%s':x=12:y=12:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=6"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(O / mj / "video.mp4"), "-i", str(O / "maniskill" / "video.mp4"), "-filter_complex",
                f"[0:v]{lab % ('MuJoCo' + ('' if mj == 'mujoco' else ' (' + mj.replace('mujoco_', '').replace('_', ' ') + ')'))}[a];[1:v]{lab % 'ManiSkill'}[b];[a][b]hstack",
                "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p", str(out)], check=True)
print(out)
