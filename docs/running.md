# Running

## Setup (once)

```bash
cd agentic-robotics
envs/setup_env.sh                          # .venv (python 3.11, mujoco 2.3.7; uv, everything inside the repo)
cp configs/local.example.yaml configs/local.yaml    # fill in: asset roots (deoxys, panda_twin, panda_pick_rgbd), EGL device
.venv/bin/python -I scripts/link_assets.py # meshes + layout banks from the manifest, sha256-checked
bin/ar check                               # assets, the program sandbox, the Claude CLI
```

## One episode

```bash
bin/ar run --split test --level 0 --idx 10            # the initial clutter-pick skill, sandboxed
bin/ar run --split test --level 0 --idx 10 --parity   # with the 11-18 baseline's starting hint (parity checks only)
bin/ar run --skills workspaces/<run>/ --split val --level 3 --idx 4   # any skill directory
```

Each writes `runs/manual/<time>_<episode>/`:

| file | contents |
|---|---|
| `result.json` | verdict and category, `t_success` (robot s), robot time, wall times, usage, versions |
| `video.mp4` | real time: the overview camera with the wrist camera beside it |
| `keyframes.png` | 8 frames |
| `observations/` | every frame the program got: rgb, depth in mm, K, robot time, joints |
| `actions.jsonl` | every trajectory sent: times, joints, grip, phase, and the server's answer |
| `events.jsonl` | the program's log |
| `program/` | its own output, `program.json`, errors |

Expected for `--parity`: success at 13.43 s (robot time 15.03 s), about 12 s wall.

## A suite, a comparison

```bash
bin/ar suite --suite dev --workers 16                   # a frozen copy of the experiment's skill on val L1-3 x 30
bin/ar suite --suite parity_test --allow-protected      # held-out suites need --allow-protected (the operator only)
bin/ar compare runs/suites/A runs/suites/B --names initial revised
```

## The learning loop

See docs/learning.md: `bin/ar learn init|run|status|evaluate --run-id ID`.

## Tests

`.venv/bin/python -I -m pytest -q -p no:cacheprovider tests` runs:
- the judge's known successes and failures;
- the program sandbox's isolation;
- the wire format's exactness.

It takes about 80 s and uses EGL on the configured device.
