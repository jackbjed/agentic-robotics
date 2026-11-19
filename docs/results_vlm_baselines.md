# VLM baselines: results

Setup: docs/baselines.md. Episodes are judged like every skill program's (the same judge, robot path, cameras and
60 s robot-time limit; model time does not count as robot time).

## Smoke test (2026-11-19): one episode each

Validation layout L1 #0 (seed 13), condition A at the calibrated cameras (`configs/tasks/clutter_pick_A_rig.yaml`):
the real objects, splat colour, SAM3. Target: the red tape roll.

| baseline | result | robot s to success | Astra calls | tool / skill calls | cost | model wait |
|---|---|---|---|---|---|---|
| direct control (`baselines/direct_vlm`) | success | 11.9 | 7 | 7 tools: locate, move over, move down (stop on contact), close, lift, look, done | $0.080 | 32 s |
| VLM + skills (`baselines/vlm_skills`) | success | 18.2 | 2 | 2: pick, done | $0.055 | |

Records: `runs/manual/vlmbase_smoke_{direct_vlm,vlm_skills}` (exchange.jsonl, astra/ images, video.mp4).

One easy layout says only that both baselines work end to end in this platform. A comparison needs a suite (the
user's go-ahead: it spends real money).
