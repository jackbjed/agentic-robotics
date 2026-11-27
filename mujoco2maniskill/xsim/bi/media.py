"""An episode's videos and frames, the same in the practice simulator and in the target: an overview camera (colour;
not one of the robot's) and the robot's head camera (colour and depth). numpy and OpenCV only."""
from pathlib import Path

import cv2
import numpy as np

LO, HI = 300.0, 1500.0                                             # the depth pictures' fixed scale (mm)
FRAMES = 8
OVER_EVERY, HEAD_EVERY, ACTION_S = 2, 5, 0.04                      # an overview frame every 2 actions, a head frame every 5


def depth_picture(dep_mm):
    """Depth (mm; nan = none) as a picture: near red, far blue on a fixed scale (shown on top); no depth = black."""
    d = np.nan_to_num(np.asarray(dep_mm, np.float32), nan=0.0)
    x = np.clip((d - LO) / (HI - LO), 0, 1)
    out = cv2.applyColorMap((255 * (1 - x)).astype(np.uint8), cv2.COLORMAP_TURBO)
    out[d <= 0] = 0
    out[:14] = cv2.applyColorMap(np.tile(np.linspace(255, 0, out.shape[1]).astype(np.uint8), (14, 1)), cv2.COLORMAP_TURBO)
    for i in range(5):
        cv2.putText(out, f"{LO + (HI - LO) * i / 4:.0f} mm", (min(int(i / 4 * (out.shape[1] - 1)), out.shape[1] - 62), 28), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return out


class Recorder:
    def __init__(self):
        self.over, self.head = [], []                               # (t, rgb) ; (t, rgb, depth_mm)

    def add_overview(self, t, rgb):
        self.over.append((float(t), rgb))

    def add_head(self, t, rgb, depth_mm):
        self.head.append((float(t), rgb, np.asarray(depth_mm, np.float32)))

    def _video(self, f, images, fps):
        h, w = images[0].shape[:2]
        vw = cv2.VideoWriter(str(f), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for im in images:
            vw.write(im)
        vw.release()

    def save(self, *dirs):
        """-> in each directory: videos/ (overview_rgb.mp4, head_rgb.mp4, head_depth.mp4) and frames/ (eight of each)."""
        over = [(t, im[..., ::-1]) for t, im in self.over]
        hrgb = [(t, im[..., ::-1]) for t, im, _ in self.head]
        hdep = [(t, depth_picture(d)) for t, _, d in self.head]
        for d in dirs:
            d = Path(d); (d / "videos").mkdir(parents=True, exist_ok=True); (d / "frames").mkdir(exist_ok=True)
            for name, seq, fps in (("overview_rgb", over, 1 / (OVER_EVERY * ACTION_S)), ("head_rgb", hrgb, 1 / (HEAD_EVERY * ACTION_S)),
                                   ("head_depth", hdep, 1 / (HEAD_EVERY * ACTION_S))):
                if not seq:
                    continue
                self._video(d / "videos" / f"{name}.mp4", [im for _, im in seq], fps)
                for k in range(FRAMES):
                    t, im = seq[int(round(k * (len(seq) - 1) / max(FRAMES - 1, 1)))]
                    cv2.imwrite(str(d / "frames" / f"{name}_{k}_t{t:05.1f}s.jpg"), im, [cv2.IMWRITE_JPEG_QUALITY, 85])
