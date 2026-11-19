"""The SAM3 segmentation backend (server side; the world server calls it for the program, which never has network).
Requests as the robot program makes them (vendored stack_bench.Perception.segment, clutter_bench
ClutterPerception.segment_points), to our own servers (configs/services.yaml sam3_text, sam3_points):

  segment(rgb, phrase)        the whole image enlarged to 1008 px on its long side (INTER_CUBIC; small far objects),
                              text-prompted SAM3 (detections over the server's --conf 0.1), masks resized back, at
                              most 6, best score first
  segment_points(rgb, ...)    click-prompted SAM3: its 3 candidate masks, best predicted IoU first
A real model: it can miss the object, return part of it, or another object (a similar colour, the wrong roll)."""
from __future__ import annotations

import io
import json
import urllib.request

import cv2
import numpy as np

from .. import services


def _post(name, path, **arrays):
    buf = io.BytesIO(); np.savez(buf, **arrays)
    req = urllib.request.Request(services.url(name, path), data=buf.getvalue())
    with urllib.request.urlopen(req, timeout=120) as r:
        return np.load(io.BytesIO(r.read()))


def segment(img, phrase):
    sc = max(1.0, 1008 / max(img.shape[:2]))
    big = cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC) if sc > 1 else img
    z = _post("sam3_text", "/segment", image=np.ascontiguousarray(big), phrases=json.dumps([str(phrase)]))
    out = []
    for i in range(min(6, len(z["scores_0"]))):
        mk = z["masks_0"][i]
        if sc > 1:
            mk = cv2.resize(mk.astype(np.uint8), (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA) > 0
        out.append((mk.astype(bool), float(z["scores_0"][i])))
    return out


def segment_points(img, pts, labels):
    z = _post("sam3_points", "/points", image=np.ascontiguousarray(img), points=np.asarray(pts, float),
              labels=np.asarray(labels, int))
    return [(z["masks"][j].astype(bool), float(z["scores"][j])) for j in np.argsort(-z["scores"])]
