# Limitations of the sweep_blocks experiment (to be stated with its results)

- **Primitive-only practice worlds.** A practice world is built from boxes, cylinders, spheres, capsules and
  ellipsoids only (any number per object, freely placed and turned; joints allowed). Meshes, height fields and files
  are refused (`xsim/bi/mj.py` `BANNED_TAGS`), although MuJoCo would take a mesh given as a vertex list. The target's
  dustpan and brush are meshes (RoboDojo's, in convex pieces), so a practice world can only approximate their forms:
  the dustpan's lip and ridge, its curved walls, the brush's bristles and curved handle. Decided by the user on
  2026-11-27 for this experiment, for the builder, C and D alike: D may revise the primitive geometry, the physical
  properties and the practice distribution; C keeps the shared initial world fixed. Inline meshes are to be
  considered later if the results show that this representation prevents a useful approximation of the task.
- **Cross-simulator transfer, not transfer to a physical robot.** Practice: MuJoCo. Target: ManiSkill (PhysX).
- **One session per condition** (no repeated seeds): differences between conditions are single observations.
- **The development and test scenes are a narrow family**: the dustpan near the middle of the table with its mouth
  within about 20 degrees of +x (turned by up to 15 from a base orientation that is itself a few degrees off), three cubes within 35 mm of the mouth's axis, the brush where the left arm reaches it; each
  kept only if a scripted sweeping reference solves it. RoboDojo's own 55 layouts are not used (that reference solves
  none of them).
- **One side only in development; the mirrored test split was added after the first results.** In every development
  scene, the example scene and the first four test splits the brush lies on the robot's left, the dustpan's mouth
  faces the robot's right and the cubes are to the right of the dustpan. The split `ood_mirrored` (30 scenes: drawn
  like the in-distribution ones, then mirrored left-right; each shown solvable by the reference's mirrored mode) was
  made on 2026-11-27 AFTER conditions A to D of replicate 0 had run and been evaluated, at the user's request, for
  evaluation only: no session has seen a mirrored scene, and development scenes stay unmirrored. The task is not
  mirror-symmetric: the judge wants the brush to the dustpan's RIGHT at the end in either case, so in a mirrored
  scene the arm that can sweep (the left one) must also bring the brush back across. The reference solved 59 of the
  96 mirrored candidates drawn, and needs about 2 to 3 s more of the 40 s than in an unmirrored scene.
- **The judge is RoboDojo's test plus two added conditions** (no block ever gripped; both grippers open), stated in
  the task text. A block pushed in with a closed gripper, without the brush, still counts; a block counts as inside
  by its position seen from above alone (no height, no contact); and success is declared at the first instant all
  conditions hold (no settling interval). `sweep_blocks/judge_strict.py` states this exactly and holds a stricter
  reading ("strict-1": held for 1 s, resting in the pan, not pushed by the robot itself) for re-scoring; the audit of
  2026-11-27 replayed every judged success of the first run under it (`docs/AUDIT_2026-11-27.md`).
- **Practice episodes that use the segmentation service are not reproducible run to run**: the same program on the
  same practice scene gave different segmentation scores and different outcomes in three runs (the same episodes
  without segmentation, and target episodes with it, were identical run to run). A practice success rate is noisier
  than its number of episodes suggests.
- **The model's thinking is mostly not exposed by the CLI**, so the transcripts hold the calls, results and visible
  text, and little of the reasoning.
- **Costs are API-equivalent figures** (the CLI's own totals), on a subscription.
