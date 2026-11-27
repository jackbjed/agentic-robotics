#!/bin/bash
# Every insertion skill on the exported scenes of the three worlds, in the ManiSkill rig (no model cost).
#   tools/run_matrix.sh [jobs]        -> out/eval/<skill>_<world>[_plain]/, out/eval/matrix.log
# "_plain": the program gets the rig's robot as it is (the world gives the scene only);
# without: the world's robot() wraps it, as in the project's own evaluations of these worlds.
cd "$(dirname "$0")/.." || exit 1
P=<HOME>/micromamba/envs/robomme/bin/python
R=../runs/bimanual
J=${1:-12}
declare -A PROG=(
  [v38]=$R/insert_own_cc_xhigh_1125/program.py
  [v63]=$R/insert_own_cc_real_1125/program.py
  [v80]=$R/insert_own_cc_real2_1125/program.py
  [v66fixed]=$R/insert_own_cc_fixedworld_1125/program.py
  [v23img]=$R/insert_worlds_from_img_1125/program.py
  [v61episodes]=$R/insert_own_cc_episodes_1126/program.py
)
for w in w16 w21 w25; do
  for s in v38 v63 v80 v66fixed v23img v61episodes; do
    for mode in "" plain; do
      [ "$w" = w16 ] && [ "$mode" = plain ] && continue
      out=out/eval/${s}_${w}${mode:+_plain}
      [ -f "$out/summary.json" ] && { echo "have $out"; continue; }
      echo "== $s $w ${mode:-world-robot} $(date +%H:%M:%S)"
      PYTHONNOUSERSITE=1 $P -I eval.py --scenes out/scenes/$w --program "${PROG[$s]}" --out "$out" --jobs "$J" \
        --label "$s in $w scenes${mode:+, plain robot}, ManiSkill" ${mode:+--plain-robot} 2>&1 | grep -v -i "warn\|pynvml" | tail -1
    done
  done
done
echo "== done $(date +%H:%M:%S)"
