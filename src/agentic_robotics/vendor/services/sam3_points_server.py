"""SAM3 with POINT prompts (the tracker head, SAM2-style: click on a thing -> its mask), for the clutter bench (11-01):
the object that blocks a grasp has no name; a click on its blocking points segments it. Runs in the `sam3` env next to
dexteleop-vla/twin/sam3_server.py (text prompts, :8131), which it does not touch.

    HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=1 ~/micromamba/envs/sam3/bin/python code/sam3_points_server.py --port 8134

POST /points, body = np.savez bytes: image (H,W,3) uint8, points (K,2) float (x, y) px, labels (K,) int (1 = on the
thing, 0 = not on it) -> np.savez: masks (3,H,W) bool (SAM's 3 candidate masks), scores (3,) (predicted IoU).
GET /ping -> "ok".
"""
import argparse
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch
from PIL import Image

LOCK = threading.Lock()


def build():
    from transformers import Sam3TrackerModel, Sam3TrackerProcessor
    global MODEL, PROC
    MODEL = Sam3TrackerModel.from_pretrained("facebook/sam3").to("cuda").eval()
    PROC = Sam3TrackerProcessor.from_pretrained("facebook/sam3")


def segment(img, pts, labels):
    pil = Image.fromarray(img)
    with LOCK, torch.inference_mode():
        inputs = PROC(images=pil, input_points=[[[[float(x), float(y)] for x, y in pts]]],
                      input_labels=[[[int(l) for l in labels]]], return_tensors="pt").to("cuda")
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            o = MODEL(**inputs, multimask_output=True)
        masks = PROC.post_process_masks(o.pred_masks.float().cpu(), inputs["original_sizes"])[0]   # (objects, 3, H, W)
    m = masks[0].numpy().astype(bool)
    sc = o.iou_scores[0, 0].float().cpu().numpy().reshape(-1)
    return dict(masks=m, scores=sc)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def do_POST(self):
        z = np.load(io.BytesIO(self.rfile.read(int(self.headers["Content-Length"]))), allow_pickle=False)
        res = segment(z["image"], z["points"], z["labels"])
        buf = io.BytesIO(); np.savez_compressed(buf, **res)
        self.send_response(200); self.send_header("Content-Length", str(buf.tell())); self.end_headers()
        self.wfile.write(buf.getvalue())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8134)
    a = ap.parse_args()
    build()
    print(f"sam3 points ready on :{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
