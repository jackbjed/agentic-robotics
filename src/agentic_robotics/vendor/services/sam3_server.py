"""SAM3 mask service for the twin's perception (runs in the `sam3` env; one model shared by every bench worker).

    HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=1 ~/micromamba/envs/sam3/bin/python dexteleop-vla/twin/sam3_server.py --port 8131

POST /segment, body = np.savez bytes with image (H,W,3) uint8 and phrases (json list of text prompts)
-> np.savez bytes: for each phrase i, masks_i (K,H,W) bool (all detections above --conf, best first) and scores_i (K,).
GET /ping -> "ok". Text-prompted SAM3 (transformers' Sam3Model, facebook/sam3 from the local Hugging Face cache, bf16 autocast).
"""
import argparse
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch
from PIL import Image

LOCK = threading.Lock()
PROC = None


def build(conf):
    from transformers import Sam3Model, Sam3Processor
    global MODEL, CONF
    MODEL, CONF = Sam3Model.from_pretrained("facebook/sam3").to("cuda").eval(), conf
    return Sam3Processor.from_pretrained("facebook/sam3")


def segment(img, phrases):
    """All phrases in one batch (the image repeated): per phrase, masks above CONF, best first."""
    out = {}
    pil = Image.fromarray(img)
    with LOCK, torch.inference_mode():
        inputs = PROC(images=[pil] * len(phrases), text=list(phrases), return_tensors="pt").to("cuda")
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            o = MODEL(**inputs)
        res = PROC.post_process_instance_segmentation(o, threshold=CONF, mask_threshold=0.5,
                                                      target_sizes=inputs.get("original_sizes").tolist())
    for i, r in enumerate(res):
        m = r["masks"].detach().cpu().numpy().astype(bool).reshape((-1,) + img.shape[:2])
        sc = r["scores"].detach().float().cpu().numpy().reshape(-1)
        k = np.argsort(-sc)
        out[f"masks_{i}"], out[f"scores_{i}"] = m[k], sc[k]
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def do_POST(self):
        z = np.load(io.BytesIO(self.rfile.read(int(self.headers["Content-Length"]))), allow_pickle=False)
        res = segment(z["image"], json.loads(str(z["phrases"])))
        buf = io.BytesIO(); np.savez_compressed(buf, **res)
        self.send_response(200); self.send_header("Content-Length", str(buf.tell())); self.end_headers()
        self.wfile.write(buf.getvalue())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8131)
    ap.add_argument("--conf", type=float, default=0.3)
    a = ap.parse_args()
    PROC = build(a.conf)
    print(f"sam3 ready on :{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
