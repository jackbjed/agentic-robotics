#!/bin/zsh
# The follow-up evaluations of the EPISODES-ONLY resume of the insertion session (v61, judged in its w18; the four real
# episodes of v38, no rig text; no model cost), from the own1125 checkout's simulator. Everything in evaluation mode:
# cameras only, no truth for the program. The same seeds as the fixed-world evaluation (2001-2016; the 45 held-out).
M=<WORKSPACE>/agentic-robotics; D=<WORKSPACE>/agentic-robotics-own1125
R=$M/runs/bimanual/insert_own_cc_episodes_1126; E=$M/runs/bimanual/insert_own_cc_episodes_1126_eval
R0=$M/runs/bimanual/insert_own_cc_xhigh_1125; R1=$M/runs/bimanual/insert_own_cc_real_1125; R2=$M/runs/bimanual/insert_own_cc_real2_1125
RF=$M/runs/bimanual/insert_own_cc_fixedworld_1125
cd $D && unset VIRTUAL_ENV
py() { env EVAL_PRACTICE=0 EVAL_WORKERS=8 PYTHONPATH=$D/src PYTHONNOUSERSITE=1 MUJOCO_GL=egl $M/.venv/bin/python "$@" }
echo "=== v61 in the worlds $(date +%H:%M)"
py $E/eval_cross.py $R/versions/v61.py $R/worlds/w18.py $E/worlds 2001-2016 600 episodes_v61_in_w18_own
py $E/eval_cross.py $R/versions/v61.py $R0/worlds/w16.py $E/worlds 2001-2016 600 episodes_v61_in_w16_first_session
py $E/eval_cross.py $R/versions/v61.py $R1/worlds/w21.py $E/worlds 2001-2016 600 episodes_v61_in_w21_real_table
py $E/eval_cross.py $R/versions/v61.py $R2/worlds/w25.py $E/worlds 2001-2016 600 episodes_v61_in_w25_real_table_2
echo "=== held-out v61 $(date +%H:%M)"; py $E/eval_heldout.py $R/versions/v61.py $E/heldout 2000-2014,5000-5014,8100-8114 600
echo "=== the others in w18 $(date +%H:%M)"
py $E/eval_cross.py $R0/versions/v38.py $R/worlds/w18.py $E/worlds 2001-2016 600 v38_in_w18
py $E/eval_cross.py $R1/versions/v63.py $R/worlds/w18.py $E/worlds 2001-2016 600 v63_in_w18
py $E/eval_cross.py $RF/versions/v66.py $R/worlds/w18.py $E/worlds 2001-2016 600 fixed_v66_in_w18
py $E/eval_cross.py $R2/versions/v80.py $R/worlds/w18.py $E/worlds 2001-2016 600 v80_in_w18
echo "=== all done $(date +%H:%M)"
