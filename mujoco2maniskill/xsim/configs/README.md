# Named settings of the experiments that were run

Give one with `--config`: every value an experiment depended on is in the file (the code's defaults may move on).

| File | Experiment | Root |
|---|---|---|
| `sweep_blocks_5x5_usd80_sim300.json` | A, B, C, D of sweep_blocks, 2026-11-27: 5 submissions x 5 scenes, $80, 300 simulator episodes; A $20, shared world $30 | `out/xsim/sweep_blocks/rep0` |
| `sweep_blocks_5x5_usd80_sim300.json`, condition E | E (practice world only: program, world, practice distribution and self-check may be revised as in D; NO target-development evaluation: the submission command is not among its commands) under the original allowances: $80, 300 MuJoCo practice interactions (checks counted) | a root of its own, forked from the first run's snapshot |
| `sweep_blocks_F_oracle_5x5_usd80_sim300.json` | condition F (oracle-simulator practice with sparse external evaluation) under the original allowances: $80, 300 practice interactions in the target simulator (scene checks counted), 5 submissions x 5 scenes. Up to 325 target-simulator interactions: an oracle reference. NOT RUN | a root of its own, forked from the first run's snapshot |
| `sweep_blocks_F_oracle_10x10_usd120_sim600.json` | condition F under the expanded allowances: $120, 600 practice interactions, 10 submissions x 10 scenes. Up to 700 target-simulator interactions: an oracle reference. NOT RUN | a root of its own, forked from the same snapshot |
| `sweep_blocks_10x10_usd120_sim600.json` | B, C, D again, 2026-11-27: 10 submissions x 10 scenes, $120, 600 simulator episodes; forked from the first run's snapshot | `out/xsim_10x10/sweep_blocks/rep0` |

```bash
PY=../.venv/bin/python; C=xsim/configs/sweep_blocks_5x5_usd80_sim300.json
$PY -I xsim/launch.py prepare --task sweep_blocks --rep 0 --config $C      # no model
$PY -I xsim/launch.py init    --task sweep_blocks --rep 0 --config $C      # PAID: A and the shared practice world
$PY -I xsim/launch.py run     --task sweep_blocks --rep 0 --cond D --config $C      # PAID
$PY -I xsim/launch.py final   --task sweep_blocks --rep 0 --config $C      # no model

# the 10x10 experiment starts from the first one's snapshot, in a root of its own:
$PY -I xsim/fork_root.py --task sweep_blocks --rep 0 --src out/xsim --dst out/xsim_10x10
C=xsim/configs/sweep_blocks_10x10_usd120_sim600.json
$PY -I xsim/launch.py run --task sweep_blocks --rep 0 --cond D --out $PWD/out/xsim_10x10 --config $C      # PAID (--out: an absolute path)
```

`_about` in each file is a description; the loader ignores keys that start with an underscore. The run of 10x10 was
started with `out/xsim_10x10/config.json`, which holds the same values for B, C and D (tests/test_configs.py compares
the named files with what the sessions' manifests recorded).
