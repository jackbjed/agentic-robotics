# Baselines

| baseline | where | status here |
|---|---|---|
| **No practice:** the initial skill programs | `initial_skills/clutter_pick` (= `checkpoints/000_initial` of every learning run) | running; never edited |
| **VLM plus skills:** Astra picks skills (pick/place/move/push/look/done) after every skill's report | `vendor/panda_twin_tracking/astra_skills_real.py` (`SkillEpisode`) | vendored, not wired to the world server yet |
| **Direct VLM control:** the model moves the arm (look / locate / move / gripper / done) | panda_twin `code/real/astra_e2e.py` (OpenAI), `gemini_e2e.py` (Gemini); embodied-agents `baselines/astra_direct` | preserved in their originals; to be ported as skill programs |

## Plan for the VLM baselines (not started: they cost money)

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
