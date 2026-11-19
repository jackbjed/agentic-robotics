# For coding agents working ON this repository (not the learning sessions: their brief is the workspace's AGENTS.md)

- **Never command the physical robot.** Nothing in this repository starts or talks to the real arm server. Do not
  add code that does without the user present and the E-stop ready.
- **Never edit `src/agentic_robotics/vendor/`.** It holds byte-identical copies (SNAPSHOT.md). Change behaviour in
  `sim/`, `robot/` or the skills, and re-run the parity suite (`bin/ar suite --suite parity_test --allow-protected`:
  verdicts must match 300/300).
- **The original projects are read-only.** That covers panda_twin, panda_twin_tracking, panda_pick_rgbd and
  embodied-agents. Port by copying.
- **Held-out suites stay out of learning.**
  - `protected: true` suites are run only by the operator.
  - Their results live under `runs/`, never in a workspace.
  - Never give a learning session paths, results or seeds of held-out suites.
- **Do not change platform code while a learning run or a suite is running.** Episodes import it fresh.
- **Budgets:**
  - The OpenAI/Gemini credit is about $50 in total. No paid sweeps without the user's go-ahead.
  - Coding-agent sessions are capped (`--max-budget-usd`) and recorded in the run's ledger.
- **No secrets or machine paths in committed files.** Machine settings go in `configs/local.yaml` (gitignored), keys
  as file paths there.
- **Environment:** only the project's `.venv` (`envs/setup_env.sh`). No conda env, system package, CUDA or driver is
  touched. Run Python with `-I` and `PYTHONNOUSERSITE=1` (bin/ar does).
- **Report outcomes faithfully.** Name the perception stand-in (oracle) and the fidelity condition with every result.
  Simulation results are simulation generalization, not sim-to-real transfer.
