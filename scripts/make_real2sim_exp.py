"""Writes configs/experiments/real2sim.yaml (docs/real2sim.md): the agent starts without a simulator, reconstructs the
scene from the real world (the photorealistic twin), practises in its reconstruction; scored in the real world on the
certified evaluation sets (data/<banks>/<split>_L0.certified.json, the evalset ablation's, without press)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANKS = {"place-box": "layouts_place_box", "close-box": "layouts_close", "clutter-pick-any": "layouts_clutter_any"}


def fam_lines(split):
    out = []
    for f, b in BANKS.items():
        s = json.loads((ROOT / "data" / b / f"{split}_L0.certified.json").read_text())["suite"]
        assert len(s["idxs"]) == 25, (f, split, len(s["idxs"]))
        out.append(f"      {f + ':':17s} {{split: {s['split']}, seed: {s['seed']}, idxs: {json.dumps(s['idxs'])}}}")
    return "\n".join(out)


txt = f"""# real2sim (docs/real2sim.md; 2026-11-20, user: "this time, the agent start without even a simulator, and it's sent to
# the "real world" which in this case is the photorealistic simulator. The agent can look() and receive the rbgd output
# from the robot cameras (scene and wrist) with the realistic noisy depth. Based on those observations and interaction
# with the environment, the agent has to reconstruct the scene on its own. It should reconstruct it until it feels ready
# that the sim is good enough. It should save that reconstruction in the folder corresponding to that session, and then
# practice skills in its sim").
#   ar learn init --run-id real2sim_r1 --experiment configs/experiments/real2sim.yaml
# The real world: condition A (splat colour, the real objects, SAM3, the D415 depth model fitted to real frames, the
# real rig's calibrated camera poses with a per-episode calibration error), the arm starting at home, the program
# planning with the robot kit (no scene in its own model; the scene's model files hidden from its sandbox). The agent's
# simulator: its sim/ (docs/selfsim.md) on the robot kit. As evalset_B_ctrl_r1 otherwise (three of its four tasks: press
# needs the kettle, never on the real table): skills from an EMPTY start, no skill library, no model calls.
# Scored by the operator in the real world on the certified evalset / evalhidden (condition A), and on evalhidden in
# condition B (the accurate twin's plain colour) to compare with evalset_B_ctrl_r1.
name: real2sim
task: configs/tasks/tasks_A_rig_home_kit.yaml
skill: initial_skills/empty_three
entry: skills/pick.py
tasks: [place-box, close-box, clutter-pick-any]
real2sim:
  task: configs/tasks/tasks_A_rig_home_kit.yaml
  families:                       # the real world's training banks (index ranges = their sizes)
    place-box:        [0, 275]
    close-box:        [0, 300]
    clutter-pick-any: [0, 197]
  episodes_per_session: 40
  episodes_total: 100
  robot_time_s: 120               # per real-world episode (exploration programs)
  max_per_call: 4
  workers: 4
  py_wall_s: 600
suites:
  evalset:                        # the real world (condition A), certified episodes
    protected: true
    families:
{fam_lines('evalset')}
  evalhidden:
    protected: true
    families:
{fam_lines('evalhidden')}
  evalhidden_B:                   # the accurate twin's plain colour (condition B): evalset_B_ctrl_r1's condition
    protected: true
    task: configs/tasks/tasks_B_rig_home_kit.yaml
    families:
{fam_lines('evalhidden')}
  dev:                            # in the AGENT'S simulator: its layouts.py's samples (seed 11, 12 per task)
    families:
      place-box:        {{n: 12, seed: 11}}
      close-box:        {{n: 12, seed: 11}}
      clutter-pick-any: {{n: 12, seed: 11}}
practice:
  seed_range: [100, 999]
vlm:
  enabled: false                  # no model calls
learning:
  library: false
  tasks_doc: task_real2sim.md
  task_text: >-
    **The tasks.** Three skills, one per task, each with its own entry file (docs/tasks.md): put the named object into
    the open white box (place-box, `skills/place.py`), close the white box's lid (close-box, `skills/close_box.py`), pick
    the named object out of clutter and lift it (clutter-pick-any, `skills/pick.py`). The named objects are the red,
    yellow and white tape rolls, the coiled black cable and the screwdriver; other objects on the table may include the
    black block with holes, the glue bottle and the sticky note pad. Every episode starts with the arm at its home pose.
    The robot refuses to move after 60 s of robot time (place-box: 90 s).
  prompt_note: >-
    Your goal: a simulator of the real world you build from your own observations, and three skills written from
    scratch (place-box, close-box, clutter-pick-any) learned in it; they are evaluated in the real world.
  resume_session: true
  progress: {{every_output_tokens: 50000}}
  dev_suite: dev
  dev_workers: 12
  practice_workers: 10
  practice_episodes_per_session: 300
  practice_episodes_total: 900
  practice_max_per_call: 12
  model: claude-opus-5-5
  effort: medium
  agent_usd_per_session: 30.0     # the CLI's API-equivalent estimate (the sessions run on the subscription login)
  agent_usd_total: 90.0
  agent_wall_s_per_session: 18000
  selection: {{min_gain: 2, min_dt_s: 0.5, max_task_loss: 1}}
"""
out = ROOT / "configs/experiments/real2sim.yaml"
out.write_text(txt)
print(out)
