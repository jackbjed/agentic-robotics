"""The final evaluation's records (xsim/launch.py final): bound to program, evaluator and scenes; reused only on a
match; never written over. And the endings in its summary. No simulator, no model.

    PYTHONNOUSERSITE=1 ../.venv/bin/python -I tests/test_final_provenance.py"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from xsim import config, launch, target  # noqa: E402

CFG = config.load()


def root(t):
    out = Path(t); R = out / "sweep_blocks" / "rep0"; T = out / "sweep_blocks" / "test_private"
    (R / "D" / "final").mkdir(parents=True); (R / "D" / "final" / "program.py").write_text("def run(robot, task):\n    pass\n")
    splits = {}
    for name, n in (("test_id", 3), ("ood_mirrored", 2)):
        d = T / name; d.mkdir(parents=True); man = []
        for k in range(n):
            f = d / f"ep_{k:02d}.json"; f.write_text(json.dumps(dict(Rigid=dict(split=name, k=k))))
            man.append(dict(file=f.name, sha256=hashlib.sha256(f.read_bytes()).hexdigest()))
        (d / "panel_manifest.json").write_text(json.dumps(man)); splits[name] = [str(f) for f in sorted(d.glob("ep_*.json"))]
    (T / "splits.json").write_text(json.dumps(dict(splits=splits)))
    return out, R, T


class Fakes:
    """target.run_batch and the evaluator's provenance replaced: how often was something RUN, and with what?"""

    def __init__(self):
        self.runs, self.ev = [], "e" * 16

    def __enter__(self):
        self.real = target.run_batch, target.evaluator_provenance

        def run_batch(program, files, task, private_dir, public_dir, cfg, jobs=None, log=print):
            from xsim import provenance
            provenance.must_be_new(private_dir)
            self.runs.append((Path(program).read_text(), [Path(f).name for f in files], str(private_dir)))
            return None, [dict(success=(k == 0), ended="returned", robot_s=30.0, wall_s=1.0, segment_calls=2, harness_error=None, config=str(f)) for k, f in enumerate(files)]
        target.run_batch = run_batch
        target.evaluator_provenance = lambda task: dict(version="2", sha=self.ev, files={}, dependencies={}, segmentation_service={}, machine={})
        return self

    def __exit__(self, *a):
        target.run_batch, target.evaluator_provenance = self.real


def records(R, cond="D", split="test_id"):
    return sorted(p.name for p in (R / "final_eval" / cond / split).iterdir() if p.is_dir())


def test_1_bound_reused_only_on_a_match_and_never_written_over():
    with tempfile.TemporaryDirectory() as t, Fakes() as F:
        out, R, T = root(t)
        r1 = launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out)
        assert len(F.runs) == 2 and r1["D"]["splits"]["test_id"]["successes"] == 1 and not r1["D"]["splits"]["test_id"]["reused"]
        rec = R / "final_eval" / "D" / "test_id" / records(R)[0]; e = json.loads((rec / "evaluation.json").read_text())
        assert e["complete"] and e["program_sha256"] == hashlib.sha256((R / "D" / "final" / "program.py").read_bytes()).hexdigest() and e["evaluator"]["sha"] == F.ev
        assert e["scenes"]["files"] == ["ep_00.json", "ep_01.json", "ep_02.json"] and (rec / "program.py").read_text() == (R / "D" / "final" / "program.py").read_text()
        before = {p: p.read_bytes() for p in rec.rglob("*") if p.is_file()}
        # the same again: nothing runs
        r2 = launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out)
        assert len(F.runs) == 2 and r2["D"]["splits"]["test_id"]["reused"] and r2["D"]["splits"]["test_id"]["evaluation"] == rec.name
        # the session's final program changes: another record, the first one untouched
        (R / "D" / "final" / "program.py").write_text("def run(robot, task):\n    robot.wait(1)\n")
        r3 = launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out, splits=["test_id"])
        assert len(F.runs) == 3 and "robot.wait" in F.runs[-1][0] and len(records(R)) == 2 and r3["D"]["splits"]["test_id"]["evaluation"] != rec.name
        # the evaluator changes: another record again
        F.ev = "f" * 16
        launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out, splits=["test_id"])
        assert len(F.runs) == 4 and len(records(R)) == 3
        assert all(p.read_bytes() == b for p, b in before.items())                   # (the first record: byte for byte as it was)
        s = json.loads((R / "final_eval" / "summary_bound.json").read_text())
        assert s["D"]["evaluator_sha"] == "f" * 16 and list(s["D"]["splits"]) == ["test_id"]   # (splits of another evaluator are not carried along)


def test_2_changed_scenes_are_refused_before_anything_runs():
    with tempfile.TemporaryDirectory() as t, Fakes() as F:
        out, R, T = root(t)
        f = T / "test_id" / "ep_01.json"; f.write_text(json.dumps(dict(Rigid=dict(split="test_id", k=1, moved=True))))
        try:
            launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out, splits=["test_id"]); raise AssertionError("a changed test scene was evaluated")
        except RuntimeError as e:
            assert "manifest" in str(e)
        assert not F.runs and not (R / "final_eval" / "D" / "test_id").exists()
        # splits.json naming other scenes than the frozen folder holds
        out, R, T = root(Path(t) / "b")
        sp = json.loads((T / "splits.json").read_text()); sp["splits"]["test_id"] = sp["splits"]["test_id"][:2]; (T / "splits.json").write_text(json.dumps(sp))
        try:
            launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out, splits=["test_id"]); raise AssertionError("a split that disagrees with its folder was evaluated")
        except RuntimeError:
            pass
        assert not F.runs


def test_3_an_unbound_or_broken_record_is_not_reused():
    with tempfile.TemporaryDirectory() as t, Fakes() as F:
        out, R, T = root(t)
        legacy = R / "final_eval" / "D" / "test_id"; legacy.mkdir(parents=True)
        old = [dict(success=True, ended="returned", robot_s=1.0)] * 3
        (legacy / "results.json").write_text(json.dumps(old)); (R / "final_eval" / "D" / "program.py").write_text("an OLD frozen program")
        r = launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out, splits=["test_id"])
        assert len(F.runs) == 1 and r["D"]["splits"]["test_id"]["successes"] == 1 and r["D"]["splits"]["test_id"]["unbound_record_beside_it"]
        assert json.loads((legacy / "results.json").read_text()) == old             # (the old record: not reused, not touched)
        rec = legacy / records(R)[0]
        (rec / "program.py").chmod(0o644); (rec / "program.py").write_text("tampered")   # a record whose program is not the one it names
        launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out, splits=["test_id"])
        assert len(F.runs) == 2 and len(records(R)) == 2 and any(n.endswith("_again1") for n in records(R))
        e = json.loads((legacy / records(R)[1] / "evaluation.json").read_text()); e["complete"] = False
        (legacy / records(R)[1] / "evaluation.json").write_text(json.dumps(e))       # an evaluation that did not finish
        launch.final("sweep_blocks", 0, CFG, conds=["D"], out=out, splits=["test_id"])
        assert len(F.runs) == 3 and len(records(R)) == 3


def test_4_a_success_that_ends_the_episode_is_not_a_timeout():
    priv = [dict(success=True, ended="time: the episode is over", robot_s=30.0), dict(success=True, ended="returned", robot_s=31.0),
            dict(success=False, ended="time: the episode's robot time (40 s) is spent", robot_s=40.0), dict(success=False, ended="returned", robot_s=20.0),
            dict(success=False, ended="error: ValueError: x", robot_s=3.0), dict(success=False, ended="refused: line 1: import of os", robot_s=0.0),
            dict(success=False, ended="process failed", harness_error="boom"), dict(success=False, ended="returned", harness_error="the result is of another program")]
    s = launch.split_summary(priv)
    assert s["successes"] == 2 and s["timeouts"] == 1 and s["errors"] == 4, s
    assert s["endings"] == dict(returned=2, ended_by_success=1, robot_time_up=1, program_error=1, refused=1, harness_failure=2, other=0), s["endings"]
    old_timeouts = sum(1 for r in priv if str(r.get("ended", "")).startswith("time"))   # (how it was counted before)
    assert old_timeouts == 2


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in tests:
        try:
            f(); print("PASS ", n)
        except Exception as e:
            import traceback
            bad += 1; print("FAIL ", n, "::", type(e).__name__, str(e)[:600], traceback.format_exc()[-500:])
    print(f"{len(tests) - bad} of {len(tests)} passed")
    sys.exit(1 if bad else 0)
