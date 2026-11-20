# Evaluation sets of solvable episodes, and the evaluation-set ablation (2026-11-19)

User: "an important ablation we should do is to create an evaluation set that the agent can train against, so that
after each skill writing trial, it can run an evaluation on the test sets, see the results, and iterate ... 25
randomized test episodes per task ... randomized positions and orientations but all be solvable."

## The sets (evaluation/evalsets.py, `ar evalset build|show`)

Four task families (tasks.py), the arm starting at its home joints (configs/tasks/tasks_B_rig_home.yaml):
`place-box` (the red, yellow and white tape rolls, the cable, the screwdriver into the white box), `close-box`,
`clutter-pick-any` (the same five objects out of clutter: 2–4 neighbours 1.5–6 cm from the target's outline, at least
one within 3.5 cm, a straight-down grasp still free) and `press` (the kettle's button). The blue cylinder is not in the
twin, so it is not in the sets.

For each family, two sets of 25 episodes: **`evalset`** (the one an agent may run in the ablation) and **`evalhidden`**
(the same kind, never shown to any agent: it measures whether gains on `evalset` carry over).

How a set is made:
1. **Candidates**: the family's generator (sim/task_layouts.py) with the split's own seeds: random positions and
   orientations; every layout checked as the banks' are (inside the work area, nothing overlapping or moving when
   settled, the target seen by the scene camera, the button reachable from above).
2. **Certification**: a reference program attempts every candidate through the full platform (world server, sandbox,
   judge) with exact segmentation (`perception: oracle`, configs/tasks/tasks_B_rig_home_oracle.yaml), the same
   cameras and arm start. The reference is the robot's own hand-written skills (initial_skills/tasks, with the skill
   library). The first attempt uses the set's own reset seed: an episode certified then is exactly the episode later
   evaluated. A candidate it fails gets another attempt with another sensor seed. **Certified = solved at least once.**
3. **The set** = the first 25 certified candidates in candidate order (never chosen by difficulty):
   `data/<banks>/<split>_L0.certified.json` (all certified indices, which attempt solved each, robot time, the suite
   spec). Its episodes are listed explicitly in the experiment (`idxs`).

What "solvable" means here, and its limit: every episode was solved once in this simulator by an existing program
that sees perfect masks. A set certified this way leans towards layouts that program can handle; layouts only a
different strategy could solve are not in it.

## The ablation (configs/experiments/evalset_B.yaml)

Two runs of the same experiment, everything equal except one thing:
- **control** (`ar learn init --run-id evalset_B_ctrl_r1 ...`): the agent sees only practice and, after each session,
  its development score (a separate val set);
- **evaluation set** (`... --run-id evalset_B_exp_r1 ... --add-evaluation-set`): the agent may also run the 100-episode
  `evalset` itself (`ar-practice eval`, up to `eval_runs_per_session` times per session) and read every episode's
  result, keyframes, events and program output. Its model calls there have their own budget (`vlm.eval_usd`) and a
  cache (an identical request is free).

Both start with no skills, both are kept or rejected by the same dev rule, and both are scored by the operator on
`evalset` AND `evalhidden` (`ar learn evaluate --suite evalset`, `--suite evalhidden`).
