# reference_pack_A_s02: a reference program for certifying evaluation sets (never a starting point)

The code of runs/learning/pack_A_r1/checkpoints/s02 (the pack_A agent's own skills, written without the skill
library, 2026-11-19), unchanged, plus `skills/place.py`, which gives the pack skill the one named object of a
place-box episode. Used by evaluation/evalsets.py (`--solver initial_skills/reference_pack_A_s02:scratch`):
- place-box: `skills/place.py` (pick it, put it into the box, close the lid);
- clutter-pick-any: `skills/pick.py` (its clutter pick, from fidelity2_A_r1 s02).
The robot's hand-written place skill never solved the cable and rarely the screwdriver; this one packs both.
