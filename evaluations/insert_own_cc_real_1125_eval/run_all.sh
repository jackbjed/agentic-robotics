#!/bin/zsh
# The follow-up evaluations of the Claude Code insertion session WITH REAL EPISODES (v63 + w21; no model cost), from the
# own1125 checkout's simulator. Everything in evaluation mode: cameras only, no truth for the program.
M=<WORKSPACE>/agentic-robotics; D=<WORKSPACE>/agentic-robotics-own1125
R=$M/runs/bimanual/insert_own_cc_real_1125; R0=$M/runs/bimanual/insert_own_cc_xhigh_1125; E=$M/runs/bimanual/insert_own_cc_real_1125_eval
cd $D && unset VIRTUAL_ENV
py() { env EVAL_PRACTICE=0 EVAL_WORKERS=8 PYTHONPATH=$D/src PYTHONNOUSERSITE=1 MUJOCO_GL=egl $M/.venv/bin/python "$@" }
echo "=== held-out v63 $(date +%H:%M)"; py $E/eval_heldout.py $R/versions/v63.py $E/heldout 2000-2014,5000-5014,8100-8114 600
echo "=== worlds $(date +%H:%M)"
py $E/eval_cross.py $R/versions/v63.py $R/worlds/w21.py $E/worlds 2001-2016 600 v63_in_w21_real_table
py $E/eval_cross.py $R/versions/v38.py $R/worlds/w21.py $E/worlds 2001-2016 600 v38_in_w21_real_table
py $E/eval_cross.py $R/versions/v63.py $R0/worlds/w16.py $E/worlds 2001-2016 600 v63_in_w16_first_session
echo "=== held-out, versions of the feedback session $(date +%H:%M)"
for v in 41 46 51 56 60; do py $E/eval_heldout.py $R/versions/v$v.py $E/heldout_versions/v$v 2000-2014,5000-5014,8100-8114 600; done
echo "=== all done $(date +%H:%M)"
