# agentic-robotics

A research system in which a coding agent develops reusable robot skill programs through simulated practice. The
robot is the user's Franka Panda table, as a MuJoCo twin built from a phone scan (panda_twin): the compliant arm on
the real robot's path, two RealSense D415s with a depth model fitted to real frames, and the real gripper's outline.

Questions it is built for:
1. How do skills improve with additional practice?
2. How does simulator fidelity affect skill development and generalization (docs/fidelity.md)?
3. What are the success, execution time and model-call costs of direct VLM control vs VLM planning with executable
   skills (docs/baselines.md)?

**Status (2026-11-18): milestone 1 done (docs/results_m1.md); milestone 2 in progress: SAM3 perception, fidelity conditions A/B/C.**

**Milestone 1.** One reproducible skill-improvement loop on the clutter pick.
- **Reproduction:** the robot's own pick skill runs here unchanged. On 300 test episodes it reproduces the original
  results: identical verdicts on 300/300, 0.950 at 15.5 s per success.
- **The loop:**
  - a headless Claude Code session revises the skill in an isolated workspace, practising within an explicit budget;
  - its candidate is snapshotted and evaluated on a development suite;
  - it is kept or rejected by a fixed rule;
  - the initial and final versions are compared on held-out layouts by the operator.

```bash
envs/setup_env.sh && cp configs/local.example.yaml configs/local.yaml   # then fill in configs/local.yaml
.venv/bin/python -I scripts/link_assets.py && bin/ar check
bin/ar run --split test --level 0 --idx 10              # one episode: video, observations, actions, metrics
bin/ar learn init --run-id r1 && bin/ar learn run --run-id r1 --iterations 1
bin/ar learn evaluate --run-id r1                       # held-out: initial vs final (the operator)
```

| doc | |
|---|---|
| docs/running.md | setup, one episode, suites, tests |
| docs/robot.md | the robot API: frames, gripper, calls, operating limits (and where each comes from) |
| docs/perception.md | cameras, the realistic D415 depth, segmentation: SAM3 (our server) or the milestone-1 oracle |
| docs/skill_api.md | skill programs: `run(ctx)`, the context, the robot skill library |
| docs/task_clutter_pick.md | the task, levels, success check, splits |
| docs/learning.md | workspaces, sessions, practice, budgets, checkpoints, the selection rule |
| docs/isolation.md | what is enforced, how, and its limits |
| docs/evaluation.md | suites, held-out protection, records, parity |
| docs/fidelity.md | conditions A / B / C (all implemented): what changes, the common held-out target |
| docs/baselines.md | no-practice, VLM+skills, direct VLM; the $50 total budget |
| docs/bimanual.md | the two bimanual environments (grippers, Sharpa hands): scene, tasks, robot API, tools, runners |
| docs/limitations.md | the simulator and sensors |
| docs/migration.md | where every part came from; what works and what is incomplete |

## Layout

```
configs/      robot, cameras, scenes, tasks, experiments (+ local.yaml: machine paths, gitignored)
src/agentic_robotics/
  sim/        world server (privileged), scene (reset, judge), twin import shim, sensor presets
  robot/      the program side: RealSim over the world socket
  perception/ segmentation client, oracle service
  execution/  protocol, sandbox (bubblewrap), skill host, episode runner, versions
  learning/   practice command, Claude Code adapter, the loop
  evaluation/ suites, runner (frozen snapshots), paired comparison
  vendor/     byte-identical copies of panda_twin and panda_twin_tracking (SNAPSHOT.md)
initial_skills/clutter_pick/   the no-practice baseline
assets/manifest.yaml           referenced assets with sha256
bin/ar, bin/ar-practice        the command line; the learning agent's only command
workspaces/ runs/ data/        generated, gitignored
```

Nothing here commands the physical robot. The original projects (panda_twin, panda_twin_tracking, panda_pick_rgbd,
embodied-agents) are unchanged.
