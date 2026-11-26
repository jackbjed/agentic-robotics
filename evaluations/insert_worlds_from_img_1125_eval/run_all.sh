#!/bin/zsh
# The follow-up evaluations of the worlds_from_img insertion session (v23 + w13; no model cost), from the own1125
# checkout's simulator. Everything in evaluation mode: cameras only, no truth for the program.
M=<WORKSPACE>/agentic-robotics; D=<WORKSPACE>/agentic-robotics-own1125
R=$M/runs/bimanual/insert_worlds_from_img_1125; E=$M/runs/bimanual/insert_worlds_from_img_1125_eval
R0=$M/runs/bimanual/insert_own_cc_xhigh_1125; R1=$M/runs/bimanual/insert_own_cc_real_1125
cd $D && unset VIRTUAL_ENV
py() { env EVAL_PRACTICE=0 EVAL_WORKERS=8 PYTHONPATH=$D/src PYTHONNOUSERSITE=1 MUJOCO_GL=egl $M/.venv/bin/python "$@" }
echo "=== v23 in w13, video $(date +%H:%M)"; py $E/eval_own.py $R $E/fresh 13 2001-2016 23 600 video
echo "=== held-out v23 $(date +%H:%M)"; py $E/eval_heldout.py $R/versions/v23.py $E/heldout 2000-2014,5000-5014,8100-8114 600
echo "=== worlds $(date +%H:%M)"
py $E/eval_cross.py $R/versions/v23.py $R0/worlds/w16.py $E/worlds 2001-2016 600 img_v23_in_w16_first_session
py $E/eval_cross.py $R/versions/v23.py $R1/worlds/w21.py $E/worlds 2001-2016 600 img_v23_in_w21_real_table
py $E/eval_cross.py $R0/versions/v38.py $R/worlds/w13.py $E/worlds 2001-2016 600 v38_in_w13_from_img
py $E/eval_cross.py $R1/versions/v63.py $R/worlds/w13.py $E/worlds 2001-2016 600 v63_in_w13_from_img
echo "=== versions in w13 $(date +%H:%M)"; py $E/eval_own.py $R $E/fresh 13 2001-2016 18,3,10,5,13,20 600
echo "=== held-out, versions $(date +%H:%M)"
for v in 05 13 18; do py $E/eval_heldout.py $R/versions/v$v.py $E/heldout_versions/v$v 2000-2014,5000-5014,8100-8114 600; done
echo "=== all done $(date +%H:%M)"
