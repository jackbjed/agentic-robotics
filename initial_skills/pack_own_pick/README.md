# pack_own_pick

The start of the pack experiment (configs/experiments/pack_A.yaml): the agent's OWN skills, no robot skill library.
- `skills/pick.py`, `helpers/`: the clutter pick Opus 5.5 wrote from an empty file in the fidelity study 2, run
  fidelity2_A_r1, checkpoint s02, copied unchanged. In simulation it lifted 198/200 never-seen test layouts. On the
  real robot it lifted the red tape roll on 2026-11-19.
- `skills/pack.py`: a stub; the agent writes the packing and the lid closing.
