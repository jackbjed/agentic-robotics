# Migration map (2026-11-18)

Everything was ported by copying; no original project was modified. Sources, and what came here:

| source | status there | here |
|---|---|---|
| **panda_twin** (not under git; the reference implementation) | Reconstruction pipeline: phone video → COLMAP → Gaussian splats (gsplat) → metric frame (`find_frame.py`) → robot registration (`fit_robots.py`) → object measurement and splats (`object_splats.py`, `align_objsplats.py`). MuJoCo models (`model/twin_*.xml`). Benches (stack, task, task_fast, task_box, clutter, press, precision). Robot path `code/real/` (RealSim, TwinArm, arm_server on deoxys, calibration, real_box). D415 depth model. | **Vendored** (vendor/panda_twin, byte-identical, SNAPSHOT.md): the twin and the robot path (stack_bench, task_bench, task_box, task_fast, clutter_bench, press_skill, d415_model, real/real_sim, panda_fk, recorder, real_box, track, rig.json, calib.json, rl/clutter_env, oracle), and the wedge model. **Referenced, not copied** (assets/manifest.yaml, sha256): the Panda meshes, the finger mesh, the splats. **Documented only:** the reconstruction scripts (they stay in panda_twin; this repo consumes their outputs), the real-arm server and bring-up (`REAL_ROBOT.md`). |
| **panda_twin_tracking** | `track_run.py` (tracking, the robot-mask fix, soft hold). `astra_skills_real.py`: Astra calls pick/place/move/push/look/done over the clutter skills, run on the robot 11-12/13. | **Vendored** (vendor/panda_twin_tracking). Its `SkillEpisode.pick_direct` is the **initial clutter-pick skill**. Its Astra loop is the VLM-plus-skills baseline, preserved and not yet wired (docs/baselines.md). |
| **panda_pick_rgbd** (git, uncommitted edits in its working tree) | Clutter-pick RL env: layout banks L0–L3 with seeded train/val/test splits; PickTwin reset; judge; sensor presets. BC/PPO policies. Skill baseline 11-18 (0.950 at 15.5 s, test L0–2). | **Ported:** `sim/picktwin.py` (its sim.py, imports changed), `sim/sensors.py` (obs.py's presets and Corruptor), `sim/scene.py` (env.reset without the RL parts, `_judge`, the summary). **Referenced:** the layout banks (manifest, copied into data/). **Not ported:** BC and RL (not in milestone 1). |
| **embodied-agents** (git, own skills repo) | A platform with world server, sandboxed programs, Astra proxy, protected suites, and a Claude improver with gateway, on the pack tasks. Held-out 54 → 72/84. | **Ported:** the wire protocol (`execution/protocol.py`), the program sandbox (`execution/sandbox.py`), the patterns (fresh world process per episode, frozen snapshots, ledger, paired acceptance). **Not ported:** its world (pack tasks, fin-ray model), Astra proxy, gateway, its improver's bwrap jail around Claude Code (docs/isolation.md). They can come later as a second task family and a stronger agent sandbox. |
| **astra_e2e / gemini_e2e** (panda_twin/code/real) | Direct VLM control in the twin (OpenAI Responses `gpt-6-astra`, Gemini REST). Recorded runs. | Preserved in their originals; to be ported behind a provider-neutral client and one budget ledger (docs/baselines.md). |
| embodied-rl, franka-agent, industrial-agent, dexteleop-vla, panda_twin_dex, robomme_*, dreamzero, RoboDojo | other robots, tasks and studies | not part of this repository |

## Working vs incomplete (as inspected)

- **Working, with evidence:**
  - the twin with the compliant arm, D415 model and judge; parity of this port: 300/300 verdicts;
  - the clutter skills at levels 0–2 (0.95);
  - the embodied-agents improvement loop on the pack tasks;
  - the splat compositor (running on the user's ports);
  - the Astra/Gemini end-to-end scripts (twin results in `panda_twin/out/real/`).
- **Incomplete or experimental:**
  - SAM3 inside a sandboxed platform;
  - clutter stages 4–5 in embodied-agents;
  - real-arm episodes (a handful);
  - boxed-in clutter (level 3: the initial skill 6/30 on dev);
  - condition C objects;
  - the TeleAvatar twin (other robot, not here).
