# Provenance: which code and which settings every run was made with

Two implementations are to be told apart:

- the **historical learning implementation**: the workstation's `mujoco2maniskill/` folder as it stood when the
  sessions ran. It was not under version control then; it is identified below by the pushed snapshot commit and by
  which files changed when (file times), and its evaluator by the hash each submission recorded;
- the **integrated evaluation implementation**: this branch (`mujoco2maniskill-1127`), which holds A to F together:
  the snapshot, the fixes of the audit of 2026-11-27 (`docs/AUDIT_2026-11-27.md`) and condition F
  (`docs/F_ORACLE.md`). Every session and every evaluation made with it records its commit (`manifest.json` "code",
  `evaluation.json` "evaluator.code").

No held-out outcome is written in this file or anywhere in the repository.

## Runs

| Run | When (2026-11-27) | Code | Settings | Evaluator |
|---|---|---|---|---|
| A (zero-shot program) and the shared practice world (initialization) | before 04:45 (snapshot made 04:45:23) | historical, state 1 | `xsim/configs/sweep_blocks_5x5_usd80_sim300.json` (A $20, world $30) | none (no target run) |
| B, C, D of the 5 x 5 experiment (learning sessions) | 04:46:28 to 06:50:55 | historical, state 1 | the same file ($80; 300 practice episodes for C, D; 5 submissions x 5 scenes) | version 1, hash `1383044c34394a50` at every submission |
| Held-out evaluation of the 5 x 5 final programs, four sets (historical record) | 04:47 to 07:02 | historical, state 1 | as above | version 1, the same files |
| Held-out evaluation of the same programs on the mirrored set (historical record) | 11:11 to 11:25 | historical, state 2 | as above, with the mirrored set added | version 1, the same files |
| B, C, D of the 10 x 10 experiment (learning sessions) | 11:33:13 to 13:44:50 | historical, state 2 = commit `69f4e4f` | `xsim/configs/sweep_blocks_10x10_usd120_sim600.json` (run with `out/xsim_10x10/config.json`, the same values) | version 1, hash `1383044c34394a50` at every submission |
| Re-evaluation of every compared final program (A, B, C, D of 5 x 5; B, C, D of 10 x 10) | after the sessions ended | integrated: see "Re-evaluation" below | the experiments' files; evaluation settings only | version 2: see below |
| F (oracle-simulator practice with sparse external evaluation) | NOT RUN | would be: integrated | `xsim/configs/sweep_blocks_F_oracle_*.json` | version 2 |

**Historical, state 2** is the content of commit `69f4e4f` (pushed as `mujoco2maniskill-1127` at 11:50): no code or
document file of the workstation folder changed after 11:27:44, the 10 x 10 sessions started at 11:33:13, and the
folder was not touched again while they ran. The commit differs from the folder only in the home directory of the
ManiSkill python, written `<HOME>` in five files.

**Historical, state 1** is that folder before ten files were changed between the end of the 5 x 5 sessions (06:50:55)
and 11:27:44; no file changed while the 5 x 5 sessions ran. The ten, with the time of their last change:
`sweep_blocks/mirror_scenes.py` (new, 10:56), `sweep_blocks/reference_sweep.py` (its mirrored mode, 11:03),
`sweep_blocks/make_panel.py`, `xsim/bi/backend.py`, `xsim/config.py`, `xsim/tasks.py` (the mirrored test set, 11:06),
`xsim/bi/LIMITATIONS.md` (11:12), `xsim/launch.py` (the live spending figure; the summary of some splits, 11:26),
`xsim/fork_root.py` (new, 11:27), `xsim/check_bi.py` (11:27). None of them is one of the evaluator's files: those were
last changed at 03:06, before any session, which is why state 1 and state 2 give the same evaluator hash. The
earlier contents of the ten files are not kept anywhere (the folder was not versioned).

**The evaluator.** Version 1: the program ran in the simulator's process; the hash covered seven files. Version 2
(this branch): the program runs in a sandbox (`m2m/boxed`); the hash covers every local file the episode runner
imports, the robot's and objects' assets, the simulator's package versions and the segmentation service's
configuration. The JUDGE (`sweep_blocks/env.py` `info()`), the scenes and the task text are the same in both.
Everything a learning session was told about its submissions was computed by version 1.

## Re-evaluation

Not run yet at this commit: this section is completed by the commit that follows the re-evaluation.

## What was established, and how far

- **No exploitation found in the inspected artifacts** (every saved program and world version of both experiments,
  the sessions' file-tool calls, every submission's records). This is not a guarantee that exploitation was
  impossible: the holes described in the audit were open while the historical sessions ran.
- **Runner equivalence** of version 1 and version 2 is established on the scenes that were run with both, not in
  general: see "Re-evaluation" for how many episodes that is and what differed.
- **Rendering nondeterminism in practice** (`docs/PRACTICE_NONDETERMINISM.md`): the diagnosis (the MuJoCo render of
  the head camera, not the segmentation service) is supported by the tested configurations: one practice world, one
  seed, one machine. The rendering is unchanged. Before small practice-score differences are interpreted in a
  future run, a deterministic rendering configuration should be tested or repeated executions evaluated.
- **The one-arm practice path has no state boundary.** The one-arm tasks practise through the main project's
  `bimanual/practice.py` (outside this folder), which runs simulator and program in one sandboxed process: files and
  network are closed, the true state is not. It needs its own fix before those tasks are run with agents.
- **Development scores are not generalization results**: they are scores on the same development scenes, observed
  again at every submission. Conditions are compared by their frozen final programs on the held-out scenes.
- **F is an oracle reference**: its extra practice in the target simulator makes it not a
  target-interaction-matched comparison.
