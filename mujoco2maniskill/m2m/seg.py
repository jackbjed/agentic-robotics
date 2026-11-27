"""SAM3 as the project calls it (perception/sam3.py segment(), copied): the project's own text-prompted service
(configs/services.yaml sam3_text). The image goes to that local service only."""
from __future__ import annotations

import io
import json
import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parents[2]


def _ports(name="sam3_text"):
    import yaml
    c = yaml.safe_load((PROJECT / "configs" / "services.yaml").read_text())[name]
    return [int(c["port"]) + 10 * i for i in range(int(c.get("instances", 1)))]


class Segmenter:
    def __init__(self):
        P = _ports()
        self.url = f"http://127.0.0.1:{P[os.getpid() % len(P)]}"
        self.calls = 0

    def alive(self):
        try:
            with urllib.request.urlopen(self.url + "/ping", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def segment(self, img, phrase):
        self.calls += 1
        sc = max(1.0, 1008 / max(img.shape[:2]))
        big = cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC) if sc > 1 else img
        buf = io.BytesIO(); np.savez(buf, image=np.ascontiguousarray(big), phrases=json.dumps([str(phrase)]))
        req = urllib.request.Request(self.url + "/segment", data=buf.getvalue())
        with urllib.request.urlopen(req, timeout=120) as r:
            z = np.load(io.BytesIO(r.read()))
        out = []
        for i in range(min(6, len(z["scores_0"]))):
            mk = z["masks_0"][i]
            if sc > 1:
                mk = cv2.resize(mk.astype(np.uint8), (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA) > 0
            out.append((mk.astype(bool), float(z["scores_0"][i])))
        return out
