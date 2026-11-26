#!/bin/zsh
# The follow-up evaluations of the Claude Code insertion session (no model cost), from the own1125 checkout's simulator
# (the same simulator code as the session's worktree).
M=<WORKSPACE>/agentic-robotics; D=<WORKSPACE>/agentic-robotics-own1125
R=$M/runs/bimanual/insert_own_cc_xhigh_1125; E=$M/runs/bimanual/insert_own_cc_xhigh_1125_eval; A=$M/runs/bimanual/insert_own_1125
cd $D && unset VIRTUAL_ENV
py() { env EVAL_WORKERS=6 PYTHONPATH=$D/src PYTHONNOUSERSITE=1 MUJOCO_GL=egl $M/.venv/bin/python "$@" }
echo "=== v38 in w16, video $(date +%H:%M)"; py $E/eval_own.py $R $E/fresh 16 2001-2016 38 600 video
echo "=== versions in w16 $(date +%H:%M)"; py $E/eval_own.py $R $E/fresh 16 2001-2016 3,15,26,7,21,33,10 600
echo "=== held-out $(date +%H:%M)"; py $E/eval_heldout.py $R/versions/v38.py $E/heldout 2000-2014,5000-5014,8100-8114 600
echo "=== cross $(date +%H:%M)"; py $E/eval_cross.py $A/versions/v48.py $R/worlds/w16.py $E/cross 2001-2016 600 astra_v48_in_cc_w16
py $E/eval_cross.py $R/versions/v38.py $A/worlds/w10.py $E/cross 2001-2016 600 cc_v38_in_astra_w10
echo "=== all done $(date +%H:%M)"
