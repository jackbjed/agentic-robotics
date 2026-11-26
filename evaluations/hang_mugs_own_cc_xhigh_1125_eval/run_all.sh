#!/bin/zsh
# The follow-up evaluations of the Claude Code hang-mugs session (no model cost), from the own1125 checkout's simulator.
# Everything in evaluation mode (EVAL_PRACTICE=0: the program has the cameras only, no truth; the world's check judges).
M=<WORKSPACE>/agentic-robotics; D=<WORKSPACE>/agentic-robotics-own1125
R=$M/runs/bimanual/hang_mugs_own_cc_xhigh_1125; E=$M/runs/bimanual/hang_mugs_own_cc_xhigh_1125_eval; A=$M/runs/bimanual/hang_mugs_own_1125
cd $D && unset VIRTUAL_ENV
py() { env EVAL_PRACTICE=0 EVAL_WORKERS=6 PYTHONPATH=$D/src PYTHONNOUSERSITE=1 MUJOCO_GL=egl $M/.venv/bin/python "$@" }
echo "=== v38 in w8, video $(date +%H:%M)"; py $E/eval_own.py $R $E/fresh 8 2001-2016 38 900 video
echo "=== cross $(date +%H:%M)"; py $E/eval_cross.py $R/versions/v38.py $A/worlds/w11.py $E/cross 2001-2016 900 cc_v38_in_astra_w11
py $E/eval_cross.py $A/versions/v50.py $R/worlds/w08.py $E/cross 2001-2016 900 astra_v50_in_cc_w8
py $E/eval_cross.py $A/versions/v50.py $A/worlds/w11.py $E/cross 2001-2016 900 astra_v50_in_astra_w11
echo "=== versions in w8 $(date +%H:%M)"; py $E/eval_own.py $R $E/fresh 8 2001-2016 36,4,19,8,27,13,33 900
echo "=== all done $(date +%H:%M)"
