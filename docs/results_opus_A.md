# Opus 5.5 sessions, condition A (2026-11-18/19)

Experiment: `configs/experiments/opus_A.yaml`.

**Agent:** Claude Opus 5.5 at medium effort, one session per run, with $40 and 4 h available.

**What the agent could use:**
- 400 practice episodes.
- 60 scene checks for its own test scenes.
- One Astra call per episode, under a $5.5 cap.

**The task:** clutter pick, condition A (splat colour, SAM3, the fitted D415 depth with a per-episode calibration
error).

**Two runs:**
- `opusA_r1`: the normal brief.
- `opusA_evalset_r1`: the same, plus `--add-evaluation-set`, which allows up to 3 runs per session of
  `ar-practice eval` on `heldout_target_200` (test L0–3, idx 0–49).

`heldout_fresh_200` (test L0–3, idx 100–149) was never shown to either run. Both runs were evaluated by the operator
after the session.

## Results

| | opusA_r1: initial → s01 | opusA_evalset_r1: initial → s01 |
|---|---|---|
| dev (val L1–3 ×30) | 65 → **90**/90 (16.7 → 15.7 s) | 65 → **90**/90 (16.7 → 12.8 s) |
| heldout_target_200 (exposed to the evaluation-set run) | 157 → **196**/200 (16.8 → 13.7 s) | 158 → **199**/200 (16.8 → 12.7 s) |
| heldout_fresh_200 (never seen) | 159 → **196**/200 (16.5 → 14.1 s) | 159 → **200**/200 (16.5 → 12.7 s) |
| level 3 on the fresh set | 13 → 46/50 (22.5 s) | 13 → 50/50 (12.9 s) |
| levels 0–2 on the fresh set | 146 → 150/150 (≈11.5 s) | 146 → 150/150 (≈12.6 s) |
| failures on the fresh set | 2 touched, not lifted; 1 dropped; 1 not touched | none |

On the fresh set, the improved versions lost no episode that the initial skill solved: only s01 solved 39 (opusA_r1)
and 41 (opusA_evalset_r1), only the initial solved 2 and 0 (McNemar p < 1e-6). On the episodes both solve, the
improved versions are 4.1 s and 3.8 s faster.

**Run-to-run noise.** The two runs evaluated the same initial skill on heldout_target_200. They agree on 199 of the
200 verdicts (157 vs 158), and on 154 of the 157 shared successes the time is identical. The residue most likely
comes from GPU inference: SAM3 and the splat compositor. Differences of 1–3 episodes are therefore within noise.

**Evaluation-set ablation.** The agent used 2 runs: 140/200 (60 episodes voided by a platform error; see below),
then 200/200. Its final version scores 199/200 on the exposed set and 200/200 on the fresh one. So there is no sign
of overfitting to the exposed set. On success counts, the two arms differ by 3–4 episodes, at the ceiling and close
to noise. With one run per arm, this is not evidence that access to the evaluation set helps or hurts; more seeds
would be needed. The arm with evaluation access is faster at level 3 (12.9 s vs 22.5 s) because of a different
grasp (below), not because of the evaluation runs.

## What the agents changed (from their reports and code)

**Both runs found the same main fix independently.** The wrist camera's per-episode calibration error (3 mm / 1° in
the hand's frame) shifts the target by 5–11 mm on the table, while level-3 grasps have 3–5 mm of margin. The error
is fixed in the hand's frame, so two looks at a still target with the hand turned give two estimates. Their
difference solves for the error. Every grasp is then corrected (`helpers/calib.py` in both workspaces). This uses
only camera frames, and it applies to the real arm, whose hand-eye error is fixed but unknown.

**opusA_r1** (kept diff: +719 / −28 lines in 6 files; 480-line `pick.py`, `calib.py`, `grasp.py`, 2 tests):
- its own pick loop: look → plan → calibrate when the grasp is tight → grasp;
- the library's one-finger "wall pinch" inside the roll's hole as a fallback, then a clearing step;
- grasp directions that failed are remembered;
- grasps reaching less than 7 mm below the roll's top are rejected;
- less robot time: looks keep the hand's angle, the start frame is used as the first look, approaches are fast
  moves, and an empty grasp is detected at once.

**opusA_evalset_r1** (kept diff: +799 / −33 lines in 6 files; 313-line `pick.py`, `calib.py`, `ring.py`, `choose.py`):
- an inside grasp as the main level-3 grasp: the closed fingertips go into the roll's ~37 mm hole and open against
  its wall, so no room beside the roll is needed;
- the calibration, which took it from 5/8 to 8/8 on its level-3 test layouts;
- a mask check on the requested colour and height (SAM3 gave the white roll 0.90 for "yellow tape roll");
- a higher first look near tall objects;
- recovery after hitting something;
- six custom test scenes.

## Cost and usage

| | opusA_r1 | opusA_evalset_r1 |
|---|---|---|
| agent (API-equivalent, subscription login) | $11.95, 201 turns, 59 min | $13.38, 244 turns, 87 min |
| practice episodes / robot time | 394 / 2.7 h | 400 / 2.0 h |
| scene checks | 2 (both failed: platform) | 10 |
| evaluation-set runs | — | 2 (400 episodes) |
| Astra calls | 0 ($0) | 0 ($0) |

Neither agent used the model call. Both kept the skill model-free.

## Platform problems during these runs (all fixed)

Most were caused by platform changes made while the runs were going (the multi-task work):
- **World-server crash**, `'BoxSim' object has no attribute 'latch'`. This came from a scene change that briefly
  assumed every world has the kettle's latch. It crashed one practice call in opusA_r1. It also voided 60 of 200
  episodes of the evaluation-set run's first `eval`, which the agent then saw as 140/200.
- **Scene checks failed with `EGL_NOT_INITIALIZED`** in opusA_r1. They ran while the rendering GPU was nearly full,
  and scene checks did not wait for free memory the way world servers do. The agent stopped using custom scenes.
- **Accounting:**
  - the evaluation-set limit counted each run twice, so the agent got 2 of its 3 runs;
  - platform-voided practice episodes and failed checks were charged to the agent;
  - one session's turn count was recorded as 1 (it was 244).

The fixes are in commits 87c73a8 and 3bed6df, and the tests in `tests/test_practice_budget.py`. The held-out
numbers above are unaffected: they were run afterwards, on the fixed platform, for both versions. The sessions
themselves had somewhat less usable practice and feedback than their budgets said.
