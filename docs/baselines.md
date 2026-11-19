# Baselines

| baseline | where | status here |
|---|---|---|
| **No practice:** the initial skill programs | `initial_skills/clutter_pick` (= `checkpoints/000_initial` of every learning run) | running; never edited |
| **VLM plus skills:** Astra picks skills (look / pick / place / move / push / done) after every skill's report | `baselines/vlm_skills` (the vendored `astra_skills_real.SkillEpisode`, its run loop, tools, skills and instructions unchanged) | built 11-19; smoke-tested |
| **Direct VLM control:** Astra moves the arm itself (look / locate / move / gripper / done) | `baselines/direct_vlm` (panda_twin `code/real/astra_e2e.py`'s tools and prompt, verbatim in `helpers/original.py`) | built 11-19; smoke-tested |

## The VLM baselines (built 2026-11-19)

Both are ordinary skill programs in the same sandbox, on the same episodes, judge, robot path, cameras and limits as
the skill programs: `ar run` / `ar suite --experiment configs/experiments/vlm_baselines.yaml --skills baselines/<name>`.
Their numbers pair episode by episode with any skill program run on the same suite.

- **Astra is called by the world server** (`ctx.vlm.converse`, perception/vlm.py `respond`): the Responses API's
  conversation (instructions, function tools, `previous_response_id`), as in the originals. Keys never enter the
  sandbox. Multi-turn calls exist only where the experiment's vlm block says `converse: true` (the fidelity runs'
  programs get one call per episode and no conversation).
- **Budget.** One ledger (`runs/budget/openai.jsonl`) for all OpenAI spending: every turn is checked against the
  per-episode cap (`max_usd_per_episode` $1), the run's cap (`--vlm-usd`) and the repository total
  (`configs/models.yaml`), with a reservation per call (`max_usd_per_call`). Nothing paid runs automatically.
- **What changed from the originals**, and only this: the task sentence (pick and lift, its success check, the 60 s
  robot-time limit), the wrist camera's description at its calibrated mount (direct control: the fingertips show in its
  view), the episode ends when robot time is up, the images go to the world server as arrays (the same images).
  Model and effort as originally: gpt-6-astra, medium (direct control), high (skills).
- **Limits per episode:** 70 tool calls (direct), 25 skill calls (skills), as the originals' defaults; robot time 60 s
  (the task's; model time does not count). The originals ran without a robot-time limit and up to $6 per episode.
- **Prices** come from configs/models.yaml (an upper bound: uncached input billed at the cache-write price).

Single-episode smoke costs and outcomes are in docs/results_vlm_baselines.md once run; a suite needs the user's
go-ahead and a cap.

## Earlier plan (kept for the record)


- **One provider-neutral model client** behind the program socket: a `model` request served by the world server's
  side, never by the sandboxed program, so keys never enter a sandbox. Providers would be OpenAI Responses and
  Gemini REST, as in the originals (raw HTTP, no SDK); the model ids come from configuration.
- **One budget ledger** for all provider spending. The available OpenAI/Gemini credit is about **$50 in total**.
  - Hard caps per episode, per suite and in total, checked before every call.
  - Price tables in configuration, with the source date. The originals' tables differ: $10 or $12.5 per M input
    tokens for gpt-6-astra.
  - Model waiting time recorded separately from robot time.
- **Smoke first:** one episode per baseline with a $0.50 cap, then a small paired suite only on the user's go-ahead.
  Recorded single-episode costs are $0.17–0.82 per direct-control episode, so a 30-episode suite is about $10–25.
