# Session records (Claude Code sessions, 25-26 November 2026)

The small files of the sessions run with the isolated Claude Code agent, copied out of `runs/bimanual` (which is not
in the repository). No images, videos, episode folders or raw agent logs are here.

- `sessions/NAME/`: what the session was given (`instructions.md`, `first_message.md`), what it made (`versions/`,
  `worlds/`, the final `program.py`), its own `summary.md`, `result.json`, `cost.json` and `transcript.md`.
- `session_texts/`: the task texts (`*_ask.txt`), the frozen briefs, the texts about the rig and the diffs between
  briefs.
- `real_feedback/PACK/`: the packs handed back to continued sessions: the text about the rig (`rig.md`) or the guide
  to reading episodes (`guide.md`), `pack.json`, and for each real episode its `episode.json`, `output.txt` and the
  program that ran. The episodes' images and depth are not here.
- `evaluations/NAME_eval/`: the evaluation scripts and the per-episode result lines (`episodes.jsonl`). Simulation,
  cameras only, no truth given to the program. These include the held-out suites' seeds and results: never give this
  folder to a learning session.
- `real_direct/`: the records of the direct-skill ablation (no simulator: programs written on the spot, times, costs).

Machine paths were replaced in these copies: `<WORKSPACE>` is the folder holding the checkouts, `<HOME>` the home
folder. Everything else is as the sessions wrote or received it.

The sessions:

| Folder | What it is | Result |
|---|---|---|
| `insert_own_cc_xhigh_1125` | insertion from nothing, simulation only | v38, world w16 |
| `insert_own_cc_real_1125` | continued from v38 with the rig text and four real episodes | v63, w21 |
| `insert_own_cc_real2_1125` | continued from v63 with more real episodes | v80, w25 |
| `insert_own_cc_fixedworld_1125` | as `real_1125` but the world could not be changed | v66 |
| `insert_own_cc_episodes_1126` | continued from v38 with the four real episodes and no rig text | v61, w18 |
| `insert_with_real_info_1126` | insertion from nothing with the rig text given at the start, no real episode | v39, w13 |
| `insert_worlds_from_img_1125` | insertion from nothing given two real images | v23, w13 |
| `sponge_sweep_own_cc_xhigh_1125` | sweeping with a sponge from nothing, simulation only | v31, w21 |
| `hang_mugs_own_cc_xhigh_1125` | hanging mugs from nothing, simulation only | v38, w8 |
| `hang_mugs_own_cc_real_1125` | its continued session with real episodes: not finished, no result |  |
| `cc_pilot_tray_1125` | the pilot session | v9, w8 |
