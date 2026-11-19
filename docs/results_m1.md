# Milestone 1 results (2026-11-18)

**The conditions** apply to every number below:
- fidelity condition B: plain MuJoCo colour, the D415 depth model with per-episode calibration error;
- the **oracle** segmenter (exact masks; a SAM3 stand-in);
- the twin's compliant arm at RealSim speed 1.0;
- a 60 s robot-time limit per episode.

These are simulation results: none has run on the real arm.

## 1. The existing skill through the new infrastructure: parity

`bin/ar suite --suite parity_test --allow-protected` (`runs/suites/parity_test_initial`) gave **285/300** (0.950),
15.50 s per success. Against panda_pick_rgbd `runs/eval/test_skill` (the same 300 test episodes):
- the same verdict on **300/300**;
- identical time-to-success on 292/300;
- 8 within 0.14 s.

## 2. One improvement loop: run `m1_r1`

| | dev (val L1–3 × 30) | per level L1 / L2 / L3 | s per success | decision |
|---|---|---|---|---|
| initial (`000_initial`) | 64/90 | 29 / 29 / 6 | 16.0 | the first incumbent |
| session s01 | 76/90 | 29 / 29 / 18 | 20.3 | **kept** (+12 ≥ 2) |
| session s02 | 77/90 | 29 / 29 / 19 | 21.1 | rejected (+1 < 2, not faster) |

- **The kept change** (`candidates/s01.diff`, 8 lines): when the robot's direct pick fails (blocked, or no grasp
  fits), and more than 20 s of robot time is left, fall back to the library's clutter pick with its own clearing
  (`pick_seg`: drags, taking blockers away).
- **Session s02** tried a looser "blocked descent" threshold plus capped clearing. It was rejected.
- **Costs:**
  - coding agent: claude-sonnet-5-5, $0.13 + $0.38 = **$0.51**, the CLI's API-equivalent estimate on the
    subscription login; 16 + 36 turns, 2 + 3 denied tool calls (all shell commands other than practice);
  - practice: **58 episodes**;
  - development: 270 episodes (3 × 90);
  - no model calls by the skills.

## 3. Held-out: test levels 0–3 × 100, the operator (`ar learn evaluate`)

| | successes / 400 | L0 | L1 | L2 | L3 | s per success | time-limit hits |
|---|---|---|---|---|---|---|---|
| initial | **310** (0.775) | 93 | 95 | 97 | 25 | 16.4 | 0 |
| s01 | **356** (0.890) | 99 | 98 | 100 | 59 | 19.9 | 46 |

- **Paired:** only s01 46, only initial 0 (exact McNemar p ≈ 3·10⁻¹⁴).
- **Time:** on the 310 episodes both solve, the times are identical, because the fallback runs only after a failure.
  The higher time per success comes from the newly solved boxed-in episodes (L3: 34 s).
- **Still failing:** 46 episodes end at the 60 s limit mid-clearing. Faster clearing is the obvious next target for
  practice.

**Caveats.**
- This is one learning run with one agent model; claims about improvement rates need repeated runs.
- The oracle segmenter hides perception errors that SAM3 would make.
- Held-out level 3 is harder than levels 0–2 (no free straight-down grasp).
