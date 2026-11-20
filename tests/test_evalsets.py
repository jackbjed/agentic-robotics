"""Evaluation sets (evaluation/evalsets.py): candidates certified by reference attempts, the set = the first N
certified in candidate order, retries only on the unsolved, platform errors retried; suites with an explicit episode
list; the arm-start option (every episode from the home joints); the clutter-pick-any family's layouts and task."""
import json

import numpy as np

from agentic_robotics import paths
from agentic_robotics.evaluation import evalsets as ES
from agentic_robotics.evaluation.suites import episode_seed, episodes


def test_suite_idxs():
    eps = episodes(dict(family="press", split="evalset", idxs=[4, 1, 9], seed=31))
    assert [e["idx"] for e in eps] == [4, 1, 9] and eps[0]["seed"] == episode_seed(31, 0, 4) and eps[0]["family"] == "press"


def test_build_certifies_first_n(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data"); monkeypatch.setattr(paths, "RUNS", tmp_path / "runs")
    targets = ["tape_red", "cable", "screwdriver", "tape_white", "tape_red", "tape_yellow"]

    def gen(task, split, n, workers, log):
        f = ES.bank_file(task, split); f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(dict(target=t)) + "\n" for t in targets))
        f.with_suffix(".meta.json").write_text(json.dumps(dict(n=n)))
        return dict(ok=len(targets))
    calls = []
    outcome = {31: {0: True, 1: False, 2: True, 3: "error", 4: False, 5: False}, 1031: {1: True, 3: True, 4: False, 5: False}}

    def fake_suite(root, spec, t, out, **k):
        calls.append((spec["seed"], list(spec["idxs"])))
        out.mkdir(parents=True, exist_ok=True)
        recs = []
        for i in spec["idxs"]:
            o = outcome[spec["seed"]][i]
            recs.append(dict(idx=i, success=o is True, category="platform_error" if o == "error" else ("success" if o is True else "not_done"),
                             t_success=12.0 if o is True else None, robot_time_s=20.0))
        (out / "episodes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        return dict(n=len(recs), successes=sum(r["success"] for r in recs))
    rec = ES.build("place-box", "evalset", n=3, candidates=6, task_cfg="configs/tasks/tasks_B_rig_home_oracle.yaml",
                   attempts=2, workers=1, log=lambda *a: None, run_suite=fake_suite, generate=gen)
    assert calls == [(31, [0, 1, 2, 3, 4, 5]), (1031, [1, 3, 4, 5])]          # (retries: only the unsolved / voided)
    assert rec["certified"] == [0, 1, 2, 3] and rec["set"] == [0, 1, 2] and rec["short"] == 0
    assert rec["per_idx"]["0"]["attempt"] == 0 and rec["per_idx"]["1"]["seed"] == 1031
    assert rec["suite"] == dict(split="evalset", idxs=[0, 1, 2], seed=31)
    assert rec["targets_in_set"] == {"cable": 1, "screwdriver": 1, "tape_red": 1}
    assert json.loads(ES.cert_file("place-box", "evalset").read_text())["set"] == [0, 1, 2]


def test_home_start_and_close_box_task():
    from agentic_robotics.sim.scene import ClutterPickScene
    s = ClutterPickScene(colour="mujoco", mounts="calibrated", task="close-box", arm_start="home")
    t = s.reset("val", 0, 0, episode_seed(11, 0, 0))
    assert t["kind"] == "close_box" and t["instruction"] == "Close the lid of the white box."
    assert np.abs(s.P.d.qpos[s.P.world.qadr] - s.P.S.q_home).max() < 1e-3
    s2 = ClutterPickScene(colour="mujoco", mounts="calibrated", task="close-box")
    s2.reset("val", 0, 0, episode_seed(11, 0, 0))
    assert np.abs(s2.P.d.qpos[s2.P.world.qadr] - s2.P.S.q_home).max() > 0.05     # (the layout's own start)


def test_clutter_pick_any_layout():
    from agentic_robotics.sim import task_layouts as T
    from agentic_robotics.sim.picktwin import PickTwin
    from agentic_robotics.sim.scene import use_object_set, world_class
    use_object_set("real")
    P = PickTwin(render=True, world_cls=world_class("real"))
    got = 0
    for idx in range(4):
        L, why = T.sample(P, "clutter-pick-any", T.SEEDS["clutter-pick-any"] + 10, idx)
        if L is None:
            continue
        got += 1
        g = sorted(L["gaps_mm"].values())
        assert L["target"] in ("tape_red", "tape_yellow", "tape_white", "cable", "screwdriver")
        assert 2 <= len(L["distractors"]) <= 4 and 15 <= g[0] <= 35 and g[-1] <= 60 and L["free_yaws"]
    assert got >= 2
