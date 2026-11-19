"""Perception tools as a skill program sees them (program side, over the world socket):

  Segmenter.segment(rgb, phrase)                -> [(mask bool HxW, score)], best first: the phrase's object(s) in that
                                                   frame (rgb must be a frame this episode's cameras delivered)
  Segmenter.segment_points(rgb, pts, labels)    -> [(mask, score)] at clicked pixels (labels 1 = on it, 0 = not)

The backend is chosen by the experiment config, not by the program: "oracle" (exact masks of the visible pixels; a
stand-in, see perception/oracle.py) now; SAM3 (the robot's text/click segmentation) when a server is configured.
library_perception() wraps them in the vendored skill library's perception class (track_run.TrackPerception: the
robot's cameras via RealSim.observe_frame, the robot's own pixels masked in scene looks), as the robot program uses it."""
from __future__ import annotations

import numpy as np


class Segmenter:
    def __init__(self, link):
        self.link, self.calls = link, 0

    def segment(self, rgb, phrase, box=None):
        self.calls += 1
        h, arr = self.link.call("segment", arrays=[np.ascontiguousarray(rgb)], phrase=str(phrase))
        if not h.get("ok"):
            raise RuntimeError(f"segment: {h.get('why')}")
        return [(m.astype(bool), float(s)) for m, s in zip(arr, h["scores"])]

    def segment_points(self, rgb, pts, labels):
        self.calls += 1
        h, arr = self.link.call("segment_points", arrays=[np.ascontiguousarray(rgb)],
                                pts=np.asarray(pts, float).tolist(), labels=[int(x) for x in labels])
        if not h.get("ok"):
            raise RuntimeError(f"segment_points: {h.get('why')}")
        return [(m.astype(bool), float(s)) for m, s in zip(arr, h["scores"])]


def library_perception(robot, seg, out_dir, library):
    """The vendored skill library's perception object (TrackPerception) with this segmenter behind it."""
    TR = library.TR

    class LinkedPerception(TR.TrackPerception):
        def segment(self, img, phrase, box=None):
            return seg.segment(img, phrase, box)

        def segment_points(self, img, pts, labels):
            return seg.segment_points(img, pts, labels)

    return LinkedPerception(robot, np.random.default_rng(0), out_dir, "skill")


class VLMClient:
    """ctx.vlm: the experiment's vision-language model (Astra, gpt-6-astra), called by the world server for the
    program. AT MOST ONE call per episode (the server refuses more), with frames this episode's cameras delivered.

      ctx.vlm.ask(prompt, images, detail="high") -> dict(ok, text, usd, wait_s, tokens) or dict(ok=False, why)
    images: 1-4 frames (observe_frame(...) dicts or their "rgb" arrays). The reply is text; ask for JSON in the prompt
    if you want to parse it (e.g. pixel coordinates of objects, which you can turn into 3D points with the frame's
    depth). Robot time does not advance while the model answers (its wall time is recorded separately)."""

    def __init__(self, link):
        self.link, self.calls = link, 0

    def ask(self, prompt, images, detail="high"):
        ims = [np.ascontiguousarray(i["rgb"] if isinstance(i, dict) else i) for i in images]
        self.calls += 1
        h, _ = self.link.call("vlm", arrays=ims, prompt=str(prompt), detail=str(detail))
        return h
