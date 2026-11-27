"""A contact sheet of a panel's starting scenes (the head camera's view of each)."""
import sys
from pathlib import Path
import cv2
import numpy as np
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from sweep_blocks.looks import SweepBlocks
panel = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "assets" / "robodojo_sweep" / "panel20"
cam = sys.argv[2] if len(sys.argv) > 2 else "head"
tiles = []
for f in sorted(panel.glob("ep_*.json")):
    env = SweepBlocks(layout=f, cameras=()); im = np.ascontiguousarray(env.image(cam)[..., ::-1])
    im = cv2.resize(im, (400, int(400 * im.shape[0] / im.shape[1])))
    lab_ = f.stem + ((" " + env.layout["Appearance"]["table"]["name"]) if env.layout.get("Appearance") else "")
    cv2.rectangle(im, (0, 0), (190, 26), (255, 255, 255), -1); cv2.putText(im, lab_, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    tiles.append(im); env.close()
rows = [np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]
out = panel / f"sheet_{cam}.jpg"; cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88]); print(out)
