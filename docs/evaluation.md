# Evaluation

**Suites** (`configs/experiments/*.yaml` `suites:`) are fixed episode lists: (split, levels, layouts, seed). Each
episode's reset seed is `seed · 1000003 + level · 10007 + idx`, panda_pick_rgbd's formula, so `test`/seed 5 are the
very episodes of its RL and skill-baseline results.

| purpose | split | who runs it | who sees per-episode results |
|---|---|---|---|
| practice | train (2000 layouts per level) | the agent (`ar-practice`), within its budget | the agent |
| development: selection | val (`dev`: levels 1–3 × 30) | the loop, on frozen snapshots | the operator; the agent sees only the incumbent's aggregate |
| held-out | test (`heldout`: levels 0–3 × 100), `protected: true` | the operator (`ar learn evaluate`, `ar suite --allow-protected`) | the operator only (runs/, never mounted in a sandbox) |

**Frozen versions.** `run_suite` first copies the skill code into `<out>/skill_snapshot` with read-only permissions,
and runs every episode from that copy, sandboxed. Nothing can edit a version under evaluation.

**Records.** Each episode's result.json:
- the judge's verdict and category;
- `t_success`, and the robot time used, which is simulated time, separate from any model or API waiting;
- wall times: physics, cameras, perception, the program's own compute, and its waiting on the world;
- request counts, segmentation calls, model calls, cost and wait;
- the program's result or error;
- the sensor state: calibration error and depth scale;
- versions: the platform's git revision and dirty flag, the hashes of the skill code, the vendored snapshot and the
  asset manifest.

The suite's `episodes.jsonl` and `summary.json/.md` aggregate them. `ar compare A B` gives the paired comparison
(`evaluation/compare.py`): an exact McNemar test on the discordant episodes, and the mean time difference on the
episodes both solve, with a bootstrap 95 % CI.

**Validation of the judge** (`tests/test_judge.py`):
- a known success reproduces exactly (13.43 s);
- a known "blocked" failure, doing nothing, a crash, and the time limit are all failures;
- a skill told the other roll's name lifts it, and is judged `wrong_object`.

**Parity with the original implementation.** `ar suite --suite parity_test --allow-protected` runs the initial skill
with the 11-18 baseline's starting hint on test levels 0–2 × 100. It must reproduce panda_pick_rgbd
`runs/eval/test_skill` (0.950, 15.50 s per success).

2026-11-18 result (`runs/suites/parity_test_initial`):
- 285/300, 15.50 s per success;
- the same verdict on 300/300;
- identical time-to-success on 292/300;
- the other 8 within 0.14 s (the wall-clock settle timeout, docs/isolation.md).

**Not yet done:** confidence intervals across seeds of the learning loop itself (one run so far). Multiple learning
runs per condition are needed before any claim about improvement rates.
