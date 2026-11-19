# Running

## Setup (once)

```bash
cd agentic-robotics
envs/setup_env.sh                          # .venv (python 3.11, mujoco 2.3.7; uv, everything inside the repo)
cp configs/local.example.yaml configs/local.yaml    # fill in: asset roots (deoxys, panda_twin, panda_pick_rgbd), EGL device
.venv/bin/python -I scripts/link_assets.py # meshes + layout banks from the manifest, sha256-checked
bin/ar check                               # assets, the program sandbox, the Claude CLI
```

## Services (our own GPU servers, for real perception and condition A)

```bash
bin/ar services start          # SAM3 text :8151, SAM3 clicks :8154, splat compositor :8152 (configs/services.yaml)
bin/ar services status
bin/ar services stop           # by PID from runs/services/*.pid
```

They use existing environments read-only (configs/local.yaml `services.python`) on `services.cuda_device`.
The user's own servers (:8131-:8137) are never touched.

## One episode

```bash
bin/ar run --split test --level 0 --idx 10            # the initial clutter-pick skill, sandboxed
bin/ar run --split test --level 0 --idx 10 --parity   # with the 11-18 baseline's starting hint (parity checks only)
bin/ar run --skills workspaces/<run>/ --split val --level 3 --idx 4   # any skill directory
bin/ar run --task configs/tasks/clutter_pick_A.yaml --split val --level 1 --idx 3   # a fidelity condition (A/B/C + SAM3)
bin/ar run --family press --task configs/tasks/clutter_pick_B.yaml --split val --idx 0 \
           --vlm configs/experiments/multitask_B.yaml       # another task (docs/tasks.md), its ported skill, one Astra call
```

`--family` is the task: clutter-pick (the default), pick, place, close-box, press or insert. Without `--skills`, the
other tasks run `initial_skills/tasks/` (the robot's existing skills, ported). `--vlm EXPERIMENT` allows the one model
call per episode under that experiment's limits; all manual runs share the ledger tag `manual` and its `--vlm-usd`
cap ($0.50 by default). Layout banks for the other tasks: `bin/ar layouts --task press --split val` (already built:
`data/layouts_{pick,place,close,press,insert}/`, 300 / 60 / 100 layouts for train / val / test).

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
