"""Stale results and forks (xsim/provenance.py, xsim/bi/backend.py run_batch, xsim/fork_root.py). No simulator, no model.

    PYTHONNOUSERSITE=1 ../.venv/bin/python -I tests/test_provenance.py

The regression asked for: a folder holds an OLD SUCCESSFUL result; the new execution's process FAILS; the old success
must never be reported as the new execution's result."""
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from xsim import provenance, target  # noqa: E402
from xsim.bi import backend  # noqa: E402
from xsim.tasks import TASKS  # noqa: E402

TASK = TASKS["sweep_blocks"]
CFG = dict(target_jobs=2, practice_jobs=2)
OLD = dict(success=True, ended="returned", robot_s=31.0, wall_s=50.0, commands=40, segment_calls=5, check=dict(success=True))


def files(d):
    d = Path(d); (d / "program.py").write_text("def run(robot, task):\n    pass\n"); (d / "scene.json").write_text('{"Rigid": {}}')
    return d / "program.py", d / "scene.json"


class Proc:
    def __init__(self, rc, err=""):
        self.returncode, self.stderr, self.stdout = rc, err, ""


def with_ms(fake, f):
    real = backend._ms; backend._ms = fake
    try:
        return f()
    finally:
        backend._ms = real


def test_1_an_existing_folder_is_never_run_into():
    with tempfile.TemporaryDirectory() as t:
        prog, scene = files(t); priv = Path(t) / "round_0"; (priv / "ep_00").mkdir(parents=True)
        (priv / "ep_00" / "result.json").write_text(json.dumps(OLD))                 # (an earlier run's successful result)
        called = []
        fake = lambda args, timeout=0: called.append(1) or Proc(1, "boom")           # (the new execution fails)
        try:
            with_ms(fake, lambda: backend.run_batch(prog, [scene], TASK, priv, None, CFG)); raise AssertionError("ran into an existing folder")
        except provenance.StaleExecutionFolder:
            pass
        assert not called and json.loads((priv / "ep_00" / "result.json").read_text()) == OLD   # (nothing ran; the old record is untouched)


def test_2_old_success_then_failed_execution_is_a_failure():
    """The same, with the folder check out of the way: the old result appears in the episode's folder (as if left there),
    the new process fails without writing. The batch must report a failure with the harness's error."""
    with tempfile.TemporaryDirectory() as t:
        prog, scene = files(t); priv = Path(t) / "round_0"; pub = Path(t) / "payload"

        def fake(args, timeout=0):
            a = [str(x) for x in args]; out = Path(a[a.index("--out") + 1]); out.mkdir(parents=True, exist_ok=True)
            (out / "result.json").write_text(json.dumps(OLD))                       # (an old successful result, no id of this execution)
            if "--public" in a:
                p = Path(a[a.index("--public") + 1]); p.mkdir(parents=True, exist_ok=True)
                (p / "episode.json").write_text(json.dumps(dict(success=True, ended="returned")))
            return Proc(1, "the simulator crashed")
        pubs, res = with_ms(fake, lambda: backend.run_batch(prog, [scene, scene], TASK, priv, pub, CFG))
        assert [r["success"] for r in res] == [False, False] and all("not written by this execution" in r["harness_error"] for r in res), res
        assert [p["success"] for p in pubs] == [False, False], pubs                  # (what would go back to an agent: failures too)
        assert (priv / "ep_00" / "result_not_of_this_execution.json").exists() and not (priv / "ep_00" / "result.json").exists()
        assert (priv / "ep_00" / "harness_error.txt").exists()


def test_3_a_result_is_taken_only_with_this_executions_id_program_and_scene():
    with tempfile.TemporaryDirectory() as t:
        prog, scene = files(t); d = Path(t) / "ep"; d.mkdir()
        rid = provenance.new_run_id()
        ok = lambda **kw: (d / "result.json").write_text(json.dumps(dict(OLD, provenance=dict(provenance.stamp(rid, prog, scene), **kw))))
        ok(); assert provenance.accept(d, rid, prog, scene)[0]["success"] is True
        assert provenance.accept(d, provenance.new_run_id(), prog, scene)[0] is None            # another execution's id
        ok(run_id=None); assert provenance.accept(d, rid, prog, scene)[0] is None
        ok(program_sha256="0" * 64); assert provenance.accept(d, rid, prog, scene)[0] is None    # another program
        ok(scene_sha256="0" * 64); assert provenance.accept(d, rid, prog, scene)[0] is None      # another scene
        (d / "result.json").write_text(json.dumps(OLD)); assert provenance.accept(d, rid, prog, scene)[0] is None   # no provenance at all (an old record)
        (d / "result.json").unlink(); assert provenance.accept(d, rid, prog, scene)[0] is None


def test_4_a_good_execution_is_taken():
    with tempfile.TemporaryDirectory() as t:
        prog, scene = files(t); priv = Path(t) / "round_0"; pub = Path(t) / "payload"

        def fake(args, timeout=0):
            a = [str(x) for x in args]; out = Path(a[a.index("--out") + 1]); out.mkdir(parents=True, exist_ok=True)
            rid = a[a.index("--run-id") + 1]
            (out / "result.json").write_text(json.dumps(dict(OLD, provenance=provenance.stamp(rid, a[a.index("--program") + 1], a[a.index("--layout") + 1]))))
            p = Path(a[a.index("--public") + 1]); p.mkdir(parents=True, exist_ok=True)
            (p / "episode.json").write_text(json.dumps(dict(success=True, ended="returned")))
            return Proc(0)
        pubs, res = with_ms(fake, lambda: backend.run_batch(prog, [scene], TASK, priv, pub, CFG))
        assert res[0]["success"] is True and res[0]["harness_error"] is None and pubs[0]["success"] is True


def _fake_root(t):
    """A small source root of the shape fork_root reads, with execution records that must NOT be forked."""
    R = Path(t) / "src" / "sweep_blocks" / "rep0"
    for name in ("dev", "f_pool", "example"):
        d = R / "private" / name; (d / "candidates").mkdir(parents=True)
        (d / "candidates" / "cand_000.json").write_text("{}")
        man = []
        for k in range(2):
            f = d / f"ep_{k:02d}.json"; f.write_text(json.dumps(dict(Rigid=dict(name=name, k=k))))
            man.append(dict(file=f.name, sha256=hashlib.sha256(f.read_bytes()).hexdigest()))
        (d / "panel_manifest.json").write_text(json.dumps(man)); (d / "verification.json").write_text("{}")
    for c in "BCD":
        e = R / "private" / "rounds" / c / "round_0" / "ep_00"; e.mkdir(parents=True); (e / "result.json").write_text(json.dumps(OLD))
    (R / "private" / "fpractice" / "F" / "job_0").mkdir(parents=True); (R / "private" / "fpractice" / "F" / "job_0" / "result.json").write_text(json.dumps(OLD))
    (R / "shared" / "docs").mkdir(parents=True); (R / "shared" / "docs" / "task.md").write_text("t")
    (R / "prepared.json").write_text("{}")
    S = R / "init" / "snapshot"; S.mkdir(parents=True)
    for n in ("program.py", "world_agent.py", "construction_notes.md"):
        (S / n).write_text(n); (R / "init" / n).write_text(n)
    (R / "init" / "snapshot.json").write_text(json.dumps(dict(files={n: target.sha(S / n) for n in ("program.py", "world_agent.py", "construction_notes.md")}, cost=dict(A=1.0, build=1.0))))
    for n in ("A_usage.json", "build_usage.json"):
        (R / "init" / n).write_text("{}")
    (R / "A" / "final").mkdir(parents=True); (R / "A" / "manifest.json").write_text("{}"); (R / "A" / "final" / "program.py").write_text("x")
    for c in "BCD":
        (R / c / "versions").mkdir(parents=True); (R / c / "versions" / "v9.py").write_text("an improved program")
    (R / "final_eval" / "D" / "test_id").mkdir(parents=True); (R / "final_eval" / "D" / "test_id" / "results.json").write_text("[]")
    T = Path(t) / "src" / "sweep_blocks" / "test_private"; T.mkdir(parents=True); (T / "splits.json").write_text(json.dumps(dict(splits={})))
    return Path(t) / "src", Path(t) / "dst"


def test_5_a_fork_takes_scenes_and_the_initialization_only():
    with tempfile.TemporaryDirectory(dir=HERE) as t:                # (fork_root takes paths relative to this folder)
        src, dst = _fake_root(t)
        r = subprocess.run([sys.executable, "-I", str(HERE / "xsim" / "fork_root.py"), "--task", "sweep_blocks", "--rep", "0",
                            "--src", str(src.relative_to(HERE)), "--dst", str(dst.relative_to(HERE))], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-800:]
        R1 = dst / "sweep_blocks" / "rep0"
        got = sorted(str(p.relative_to(R1)) for p in R1.rglob("*") if p.is_file())
        assert sorted(p.name for p in (R1 / "private").iterdir()) == ["dev", "example", "f_pool"], got
        assert not any("rounds" in g or "fpractice" in g or "candidates" in g or "final_eval" in g or "versions" in g or g.startswith(("B/", "C/", "D/")) for g in got), got
        assert not any(p.name == "result.json" for p in R1.rglob("*")), got
        assert (R1 / "private" / "dev" / "ep_01.json").read_bytes() == (src / "sweep_blocks" / "rep0" / "private" / "dev" / "ep_01.json").read_bytes()
        note = json.loads((R1 / "FORKED_FROM.json").read_text())
        assert any("rounds" in x and "fpractice" in x for x in note["not_copied"]), note


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in tests:
        try:
            f(); print("PASS ", n)
        except Exception as e:
            bad += 1; print("FAIL ", n, "::", type(e).__name__, str(e)[:900])
    print(f"{len(tests) - bad} of {len(tests)} passed")
    sys.exit(1 if bad else 0)
