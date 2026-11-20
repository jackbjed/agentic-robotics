# The skill-improvement loop

```
ar learn init --run-id ID [--experiment configs/experiments/m1_clutter_pick.yaml]
ar learn run  --run-id ID [--iterations N]
ar learn status --run-id ID
ar learn evaluate --run-id ID [--suite heldout]      # the operator, after learning
```

**Several tasks in one run** (`configs/experiments/multitask_B.yaml`; docs/tasks.md):

```
ar learn init --run-id ID --experiment configs/experiments/multitask_B.yaml \
              --tasks pick,place,clutter-pick,press,close-box,insert [--initial scratch|ported]
```

- `--tasks` picks any subset of the experiment's `tasks:`; the development and held-out suites keep only those
  tasks' parts, and the brief describes each task, its entry file and its success check.
- `--initial scratch` (the default): pick and clutter-pick start from the robot's pick tool; place, close-box, press
  and insert start from a stub that only finds the task's object and stops, so the agent has to write those skills.
  `--initial ported`: every task starts from the robot's existing skill (the box program's set-down and lid closing,
  the press skill with one Astra pointing call, the stick threading), for comparison.
- The program has one entry file per task (`skills/<task>.py`); every episode carries `family`, and suites, practice
  (`ar-practice run --task <name>`), custom scenes (`scene check ... --task <name>`) and the reports are per task.

## What `init` makes

- `workspaces/ID/`: the agent's workspace (gitignored).
  - `skills/`, `helpers/`, `tests/`: a copy of the experiment's initial skill.
  - `AGENTS.md`: the brief, with the budget and the exact practice commands. `CLAUDE.md` points to it.
  - `docs/`: the robot, perception, skill-API and task docs.
  - `reference/`: a read-only copy of the vendored robot code.
  - `notes.md`, `practice/`.
- `runs/learning/ID/`: outside the workspace, never readable by the agent.
  - `budget.json`, `experiment.json`.
  - `ledger.jsonl`: every practice call, session, development evaluation and decision, with costs.
  - `state.json`: the incumbent.
  - `checkpoints/000_initial/`, `dev/000_initial/`: the initial skill on the development suite, the first incumbent.

## One iteration (`run`)

1. **Session.** One headless Claude Code process in the workspace (`learning/agent.py`).
   - It starts with a fresh context: no conversation and no persistence. It gets the workspace's AGENTS.md as an
     appended system prompt, plus a short prompt with the incumbent's development result (aggregate only) and the
     last decision.
   - It is bounded by `--max-budget-usd` (per session and in total) and a wall-clock limit. At the limit it is
     terminated and its workspace taken as it is.
2. **Snapshot.** The workspace's code (`skills/`, `helpers/`, `tests/`) goes to `candidates/sNN/` with its diff against
   the incumbent (`candidates/sNN.diff`). The notes and the session transcript go to `sessions/sNN/`. Every attempt is
   kept, accepted or not.
3. **Development evaluation.** The frozen snapshot runs on the experiment's dev suite (val layouts), the same
   episodes as the incumbent's.
4. **Selection** (`learning/loop.py`, constant `selection:` of the experiment):
   - **void:** any platform error in the candidate's dev run means rejection.
   - **keep:** successes(candidate) − successes(incumbent) ≥ `min_gain` (2). Success comes first, under the fixed
     60 s robot-time limit.
   - **keep:** a gain of 0 ≤ g < `min_gain`, with the candidate faster by ≥ `min_dt_s` (0.5 s) on average on the
     episodes both solve, and with no more model calls.
   - **multi-task runs:** also rejected if any one task loses more than `max_task_loss` (1) dev successes, whatever
     the total.
   - **otherwise rejected:** the workspace's code goes back to the incumbent. Notes and practice records stay, and
     the next session is told why.
5. **Checkpoint.** A kept candidate becomes `checkpoints/sNN/` and the new incumbent.

**Costs counted:**
- every practice episode, including failed and crashed ones (`kind: practice`);
- every development episode (`kind: dev`);
- every session: the CLI's `total_cost_usd`, tokens, turns, wall time and denied tool calls (`kind: session`);
- every decision.

Rejected revisions count like accepted ones. `ar learn status` sums them.

## What the agent can and cannot do

| can | cannot |
|---|---|
| read, write and edit files in its workspace (`skills/`, `helpers/`, `tests/`, `notes.md`) | read or write anything outside it: the file tools are confined (`--restricted`) |
| change strategy, structure, feedback checks, recovery; wrap or subclass the library | change the platform, the judge, the time limit, the robot's limits, the budget accounting (all outside the workspace, enforced in other processes) |
| run practice episodes on **train** layouts, levels and seeds within the permitted ranges (`bin/ar-practice run`) | run any other shell command (the only allowed Bash command is `bin/ar-practice`; anything else, even chained after it, is denied) |
| look at its practice results: verdicts, robot time, keyframe sheets (PNG), events, camera frames, videos | see development or held-out episodes: only the aggregate dev result of the incumbent, in the prompt |
| run its tests in the sandbox (`bin/ar-practice test`) | use the network, web tools, MCP servers, sub-agents, or command the physical robot |

## Held-out evaluation (`evaluate`, the operator)

The initial skill (`checkpoints/000_initial`) and the final incumbent run on the protected `heldout` suite: test
layouts, the same episodes for both, through the same frozen-snapshot path. The results are written to
`runs/learning/ID/heldout/`, where no session ever looks, along with the paired comparison.

## Real-robot feedback and continuous sessions (2026-11-19)

**Real runs into the loop.** After running a version of the skill on the robot with `real/run episode ...`, the operator
copies the episodes into the run's workspace:

```
ar learn add-real --run-id ID real/results/<run> [real/results/<run> ...]
ar learn extend   --run-id ID --practice 100 --agent-usd 15     # if the run's practice / agent budget is used up
ar learn run      --run-id ID [--resume-session]
```

- **What `add-real` copies.** Each episode goes to `workspaces/ID/real/NNN_<name>/` (learning/real_feedback.py):
  - `summary.md`: which version of this run's skill it was (matched by its code hash), dry or live, the operator's
    verdict and note, robot time, trajectories planned / sent / refused, the program's result;
  - `keyframes.png` (8 frames of `episode.mp4`), `frames/`, `plan/`, `events.jsonl`, `actions.md` (no joint arrays),
    `program/`.

  No machine paths or keys are copied.
- **The next session's prompt** names the new runs (live / dry, version, outcome) and points to `real/`. The brief of
  runs made from now on says that `real/` may appear. `ar learn run --note "..."` adds the operator's own message to
  the prompt (kept in the ledger).
- **Selection after real feedback.** The simulated development suite cannot score a fix for the real robot, and it
  may already be saturated (fidelity2_A_r1: 90/90). So a session given new real runs is kept if the dev suite does not
  get worse: at most `selection.real.max_loss` (1) success fewer and at most `selection.real.max_dt_s` (1 s) slower
  per success. A clear sim gain (`min_gain`) is kept as usual. The prompt and `real/README.md` state the rule.
- **A run the operator stopped** (Ctrl-C) has no robot record; its mode, backend and task come from its spec, and its
  keyframes from `video/scene.mp4`.
- **The ledger** records every import (kind `real`), each budget extension (kind `budget`) and, per session, which
  real runs it was told about.

**Continuous sessions.** By default each iteration is a fresh Claude Code process, with no conversation; continuity
comes from the workspace (code, `notes.md`, practice records, `real/`) and the session prompt.

With `learning.resume_session: true` in the experiment, or `ar learn run --resume-session`, the conversation is saved
and the next iteration resumes it (`claude -p --resume <session id>`).
- The agent keeps its whole context; Claude Code compacts it when the window fills.
- The prompt says what changed since: the dev result, the keep / reject decision (a reset of the code if rejected), and
  new real runs.
- The restrictions are the same on every invocation. The session id stays the same, and each invocation's cost is its
  own (checked 2026-11-19): the per-session and total caps hold as before.
- Saved conversations live in Claude Code's own store, `~/.claude/projects/<workspace path>/`, outside the workspace.
  The first `--resume-session` iteration of a run whose earlier sessions were not saved starts fresh and is saved from
  then on.

Use the workspace record for comparisons across coding agents: another agent cannot resume a Claude session, but any
agent reads the same `notes.md` and `real/`.

## Progress curves: skill quality against the agent's work (2026-11-19)

User: "shouldn't there be a way to measure how skills improve as the agent works? e.g. by evaluating them in a held
out evaluation set throughout the agent's work, e.g. by measuring after N, 2N, 3N, 4N tokens".

- **Snapshots during the sessions.** With `learning.progress: {every_output_tokens: N}` in the experiment (or
  `ar learn run --progress-every N`), the session runs with `--include-partial-messages`. The runner reads each API
  message's final usage as it ends (`message_delta`; the text deltas are dropped, not logged) and counts the run's
  output tokens: thinking, text and tool calls, over every session of the run. Each time that count passes a multiple
  of N, it copies the workspace's code (`skills/`, `helpers/`, `tests/`) to `runs/learning/ID/progress/tNNNNNNNN_sNN/`,
  with a `.json` beside it: the token count, the session's thinking tokens, the practice episodes used so far, the
  wall time. It also copies the code at each session's end. The copy is what the agent would hand over if stopped
  there, half-finished edits included. The agent does not know about it.
- **Scoring them (`ar learn progress --run-id ID [--suite progress]`, the operator, after learning).** Every snapshot,
  and the initial skill at 0 tokens, runs on a protected suite that no session sees (the experiment's `progress`
  suite, else `heldout`). Identical code (tree hash) is scored once. Model calls use the tag `ID-progress` and the
  budget `vlm.progress_usd`. Output: `progress/curve.md` (table), `curve.json`, `curve.svg` (success % against
  thousands of output tokens, per task family, with the session ends marked).
- **Why output tokens.** They measure the agent's own work, its reasoning included, and are comparable across
  sessions and agents. Input tokens mostly measure how often the context is re-read. The table also gives practice
  episodes and wall time, for curves against those.
- **Cost.** One suite run per distinct version: with N = 50k and about 350k output tokens per run, roughly 8 versions ×
  the suite's episodes. The model calls are capped by `progress_usd`.

## Task docs given to the agent

`docs/tasks.md` goes to multi-task runs. An experiment can give its own instead (`learning.tasks_doc`, copied as the
workspace's `docs/tasks.md`): `task_place_box.md` (place_box_A: tells the agent it is tested on other objects, not
which: `place-box-new` = the black block with holes and the glue bottle), `task_four.md` (evalset_B, docs/evalsets.md).
