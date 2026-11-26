#!/bin/zsh
# The earlier versions of the Claude Code insertion session on the simulator's 45 held-out insertion scenes (as v38 in
# heldout/): one-armed, cameras only, the simulator's own judge. No model cost. From the own1125 checkout's simulator.
M=<WORKSPACE>/agentic-robotics; D=<WORKSPACE>/agentic-robotics-own1125
R=$M/runs/bimanual/insert_own_cc_xhigh_1125; E=$M/runs/bimanual/insert_own_cc_xhigh_1125_eval
cd $D && unset VIRTUAL_ENV
py() { env EVAL_WORKERS=8 PYTHONPATH=$D/src PYTHONNOUSERSITE=1 MUJOCO_GL=egl $M/.venv/bin/python "$@" }
for v in 33 3 15 7 26 10 21; do
  echo "=== v$v held-out $(date +%H:%M)"; py $E/eval_heldout.py $R/versions/v$(printf %02d $v).py $E/heldout_versions/v$(printf %02d $v) 2000-2014,5000-5014,8100-8114 600
done
echo "=== all done $(date +%H:%M)"
