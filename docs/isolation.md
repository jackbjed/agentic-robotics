# Isolation: what is enforced, how, and its limits

## Skill programs (the agent's code, and every evaluated version)

They are enforced by the **kernel** (bubblewrap, `execution/sandbox.py`), whatever the code does:
- no network at all: there is no network interface but loopback;
- read-only: the system, the project's Python environment, the platform's `src/`, and the program's own code;
- writable: one output directory, plus the socket's directory; `/tmp` is a private tmpfs;
- not mounted: the home directory (no keys, no Claude or SSH configuration, no other projects), `configs/`, `data/`
  (layout banks), `runs/` (dev and held-out results), and other workspaces;
- no GPU device: a program renders its own robot model with OSMesa.

The true state lives only in the world-server process. The socket's requests (`execution/protocol.py`) return camera
frames, robot state and segmentation masks, never poses. The oracle segmenter answers only for frames the cameras
delivered.

Verified by `tests/test_isolation.py`:
- a program saw none of the probed paths;
- it could not open TCP 5590 (the real arm server's port);
- it got no answer to a privileged request.

The real arm server is never started by this repository. It listens on TCP 5590 on the host, which a sandbox cannot
reach.

## The coding agent (Claude Code)

It is enforced by **Claude Code's own permission system**: application level, verified locally on 2026-11-18 with
version 2.1.328.

| flag | effect |
|---|---|
| `--restricted` | the file tools only work inside the working directory, the workspace. A probe's reads and writes outside it were refused. User and project settings are ignored. |
| `--tools Read,Edit,Write,Glob,Grep,Bash` | the session's init event lists exactly these tools, with `mcp_servers: []` |
| `--allowedTools ... Bash(<repo>/bin/ar-practice *)` with `--permission-mode dontAsk --permission-prompts none` | every other shell command is denied without asking. A probe's `cat ../outside.txt` and `python3 ok.py; cat ../outside.txt` were denied, and so was the smoke session's `cd … && cat >> notes.md`. |
| `--strict-mcp-config` | no MCP servers |
| `--disable-slash-commands` | no skills or commands |
| `--no-session-persistence` | no transcript in `~/.claude`; it is kept in the run's own log |

`bin/ar-practice` is outside the workspace, so the agent cannot edit it. It:
- checks `AR_WORKSPACE`, set by the launcher, against the current directory;
- validates every argument against the run's permitted ranges;
- charges the ledger (outside the workspace) under a lock before running anything;
- runs the agent's code only in the program sandbox, from a frozen copy.

## Limits of this isolation (known)

- **The Claude Code process itself is not kernel-sandboxed.** It runs as the user, with network access for its own
  API, and its confinement to the workspace is Claude Code's permission logic.
  - `embodied-agents` ran the whole Claude process inside bubblewrap, with an allowlist network proxy and
    `--dangerously-skip-permissions` inside it. That is stronger, and it could be ported here if needed.
- **Permission-rule matching:** a Bash rule is a prefix match on the command line. `ar-practice` therefore validates
  all its arguments itself, and accepts no paths.
- **The agent shares the user's Claude login** (subscription). Its usage is the CLI's own accounting
  (`total_cost_usd`, an API-equivalent estimate; cumulative over a resumed conversation, so the platform records
  each session's own share), capped per session with `--max-budget-usd`. The subscription's own
  limits are not visible to the platform.
- **Practice records** inside the workspace (result.json: the verdict, robot time, disturbance in mm) are the agent's
  own practice episodes. They contain no object poses.
- **Wall-clock-dependent code** in the vendored RealSim (`settle_wait`'s 1.5 s wall timeout) can make trajectories
  load-dependent by a few frames. Parity on 300 episodes: verdicts identical 300/300, time-to-success identical
  292/300, the rest within 0.14 s.
