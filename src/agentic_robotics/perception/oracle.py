"""The ORACLE segmentation service (server side): masks from the world's own segmentation of the very frame the
program got. A perception stand-in for SAM3 (the robot's text- and click-prompted segmentation), not a perception
model: masks are exact over the object's visible pixels. It gives image masks only, never poses or positions.
Behaviour = the 11-18 skill baseline's stand-in (panda_pick_rgbd scripts/skill_baseline.py GTPerception):
  the target's name                -> the target's mask, score 0.92
  "small object" (SAM3's generic)  -> every visible object's mask, score 0.6 (largest first, <= 6)
  another object's name            -> that object's mask, score 0.92
Masks under 20 px are dropped (SAM3 returns nothing that small)."""
from __future__ import annotations

import numpy as np

MIN_PX = 20


def segment(lab, keys, target, phrase, phrases):
    if phrase == phrases.get(target):
        ids, sc = [keys.index(target)], 0.92
    elif phrase == "small object":
        ids, sc = [i for i in range(len(keys)) if (lab == i).any()], 0.6
    else:
        ids, sc = [i for i, k in enumerate(keys) if phrases.get(k) == phrase], 0.92
    out = [((lab == i), sc) for i in ids if (lab == i).sum() >= MIN_PX]
    return sorted(out, key=lambda x: -x[0].sum())[:6]


def segment_points(lab, pts, labels):
    H, W = lab.shape
    hit = [int(lab[int(np.clip(round(p[1]), 0, H - 1)), int(np.clip(round(p[0]), 0, W - 1))])
           for p, l_ in zip(pts, labels) if l_ > 0]
    hit = [h for h in hit if h >= 0]
    if not hit:
        return []
    i = max(set(hit), key=hit.count)
    return [((lab == i), 0.9)]
