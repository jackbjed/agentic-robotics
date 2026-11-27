"""The experiments' settings, all in one place and all configurable (a JSON file given with --config overrides any of
them; the manifest of every session records the values it ran with). Defaults follow the specification; the two
marked PILOT are to be chosen with a small pilot and then frozen before the main comparison."""
import copy
import json
from pathlib import Path

DEFAULTS = dict(
    model="claude-opus-5-5", effort="xhigh",
    # $ of API-equivalent model usage: A (no execution), the shared world construction, and one cap per session B-F
    # (the user's numbers of 2026-11-27: A 20, the shared world's construction 20, B / C / D 70 each after initialization)
    #  updated by the user the same day: the world's construction 30, B / C / D 80)
    budget_usd=dict(A=20.0, build=30.0, B=80.0, C=80.0, D=80.0, E=80.0, F=80.0),
    max_hours=dict(A=3.0, build=3.0, B=10.0, C=10.0, D=10.0, E=10.0, F=10.0),
    # the simulator allowance of C, D (and E), identical: EVERY episode started in the practice simulator counts one, whatever
    # its outcome: a practice episode, and a scene checked by validate_world. The world's builder has its own, for checks only.
    practice_episode_cap=300,
    build_validation_cap=40,
    target_submissions=5,                # B, C, D: at most this many submissions (rounds) ...
    # how many of the frozen development scenes (the FIRST n of the panel, in its fixed order) one submission runs, by
    # condition. The user, 2026-11-27 ~03:45: five scenes a submission for B, C and D alike (5 submissions x 5 = at most 25
    # target episodes a session), in place of the 20 said before.
    submission_scenes=dict(B=5, C=5, D=5, F=5),
    target_panel=20,                     # the frozen development panel's size (made once per replicate;
    #                                      episodes; the user's word of 2026-11-27: 20 verified-solvable episodes, 5 rounds)
    # which sets of target configurations must be SHOWN SOLVABLE (the task's reference program solves each in the target)
    # besides passing the validation rules: any of dev, f_pool, test_id, ood_base. A configuration the reference does not
    # solve is set aside and recorded; no configuration is ever excluded because a tested method (A-F) failed on it.
    shown_solvable=["dev"],
    # F ("oracle-simulator practice with sparse external evaluation", the two-arm task): practice IN the target simulator, on
    # scenes from a generator (sweep_blocks/scene_gen.py), judged by the developer's own self-check; the same allowance
    # of practice episodes as C and D (practice_episode_cap, scene checks counted), and the same submissions. So F gets up
    # to practice_episode_cap + target_submissions x submission_scenes interactions with the target simulator: an oracle
    # reference, not a target-interaction-matched comparison.
    f_extra_episodes=100,                # (the one-arm tasks' older F only: practice episodes beyond practice_episode_cap)
    f_pool=40,                           # (a frozen set of development-like scenes, still made by `prepare`; the two-arm F does not use it)
    checkpoints_usd=[2, 5, 10, 20, 30, 40, 50, 60, 70, 80],
    practice_jobs=6, target_jobs=6,      # episodes run at the same time
    observation=dict(colour="640x480 RGB, scene and wrist cameras",
                     depth="ideal simulator depth: exact, no noise, no holes, no near limit; nan where no surface is seen",
                     calibration="exact camera poses"),
    # the final evaluation's splits (episodes); the development panel is target_panel
    splits=dict(test_id=30, ood_appearance=10, ood_dimensions=10, ood_heights=10, ood_physics=10, ood_mirrored=30),
    # seeds of the target configurations: per replicate for development; one set for the final tests (all replicates)
    seeds=dict(example=40001, dev=41000, test_id=45000, ood=46000),
    # the validation rules of a target configuration (frozen before any method is evaluated)
    validation=dict(settle_s=1.0, max_move_mm=5.0, max_tilt_deg=3.0, reach_x_mm=[-480, -150], reach_y_mm=[-200, 420]),
    # $ per million tokens of the API-equivalent accounting (bimanual/improve_cc.py PRICES: fitted on the CLI's own
    # totals, 2026-11-25): input / cache write 5 min / cache write 1 h / cache read / output
    pricing={"claude-opus-5-5": dict(input=4.0, write_5m=5.0, write_1h=8.0, read=0.2, output=20.0),
             "default": dict(input=4.0, write_5m=5.0, write_1h=8.0, read=0.2, output=20.0)},
    nudges=2,                            # times a session that stopped without finishing is asked to go on
)


def scenes(cfg, cond):
    """How many development scenes one target submission of a condition runs (the first n of the frozen panel)."""
    return int((cfg.get("submission_scenes") or {}).get(cond, cfg["target_panel"]))


def load(path=None, **over):
    c = copy.deepcopy(DEFAULTS)
    if path:
        for k, v in json.loads(Path(path).read_text()).items():
            if k.startswith("_"):                                   # (a description of the file: not a setting)
                continue
            if isinstance(v, dict) and isinstance(c.get(k), dict):
                c[k].update(v)
            else:
                c[k] = v
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c
