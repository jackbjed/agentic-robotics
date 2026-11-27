"""Evaluate final programs again, under the code of THIS checkout, into a root of its own: the same frozen test scenes,
the original judge, hash-bound records (launch.final). Nothing of the source roots is written to; no session is given
anything; no model is called.

    ../.venv/bin/python -I xsim/reevaluate.py --task sweep_blocks --tests SRC_OUT --programs NAME=SRC_OUT[:A,B,C,D] ... --dst DST
        [--config FILE.json] [--splits test_id ...] [--sensitivity]

--tests      an experiment root whose <task>/test_private holds the frozen test sets (copied, each file checked against
             its manifest; the copies are frozen again)
--programs   NAME=ROOT[:conditions]: the final programs <ROOT>/<task>/rep0/<cond>/final/program.py, evaluated in
             DST/NAME/ (one root per experiment). Each program's hash is recorded with where it came from.
--sensitivity  afterwards, every success of the original judge is simulated again with its contacts recorded
             (sweep_blocks/judge_strict.py): the two named sensitivity analyses, reported NEXT to the judge's count.

Then, if the source root has earlier (unbound) results for the same program and scenes, DST/NAME/comparison.json says
episode by episode whether the new record agrees with them."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from xsim import config, launch, provenance, target  # noqa: E402
from xsim.tasks import TASKS  # noqa: E402
from m2m.envpaths import maniskill_python  # noqa: E402


def copy_tests(task_name, src, dst):
    """The frozen test sets of src, into dst/<task>/test_private (each scene checked against its manifest before and after)."""
    task = TASKS[task_name]
    S, D = Path(src) / task_name / "test_private", Path(dst) / task_name / "test_private"
    sp = json.loads((S / "splits.json").read_text())
    out = {}
    for name, listed in sp["splits"].items():
        files, sid = target.checked_panel(task, S / name)
        if [Path(f).name for f in listed] != [f.name for f in files]:
            raise SystemExit(f"{name}: splits.json and the folder disagree")
        (D / name).mkdir(parents=True)
        for f in files + [S / name / "panel_manifest.json"]:
            shutil.copy2(f, D / name / f.name)
        files2, sid2 = target.checked_panel(task, D / name)
        assert sid2 == sid
        out[name] = [str(f) for f in files2]
        for f in (D / name).iterdir():
            f.chmod(0o444)
    (D / "splits.json").write_text(json.dumps(dict(task=task_name, copied_from=str(S), made=time.strftime("%Y-%m-%d %H:%M"), splits=out), indent=1))
    return out


def sensitivity(root, task_name, jobs=6):
    """Every judged success of the bound records under root, simulated again with contacts. -> root/sensitivity.json"""
    task = TASKS[task_name]; R = Path(root) / task_name / "rep0"
    txt = Path(root) / "task.txt"; txt.write_text(task["text"])
    todo = []
    for ev in sorted(R.glob("final_eval/*/*/eval_*/evaluation.json")):
        e = json.loads(ev.read_text())
        if not e.get("complete"):
            continue
        for r in json.loads((ev.parent / "results.json").read_text()):
            if r.get("success"):
                todo.append((e["condition"], e["split"], r["episode"], r["config"], str(ev.parent / "program.py"), r))

    def one(j):
        cond, split, k, scene, prog, r = j
        out = Path(root) / "sensitivity" / cond / split / f"ep_{k:02d}"
        p = subprocess.run([maniskill_python(), "-I", str(HERE / "sweep_blocks" / "judge_strict.py"), "--layout", scene, "--program", prog, "--out", str(out),
                            "--task-text-file", str(txt)], capture_output=True, text=True, timeout=1500)
        line = next((x for x in p.stdout.splitlines() if x.startswith("STRICT_JSON ")), None)
        d = json.loads(line[12:]) if line else dict(error=p.stderr[-300:])
        same = "result" in d and (d["result"]["success"], d["result"]["robot_s"], d["result"]["commands"]) == (r["success"], r["robot_s"], r["commands"])
        return dict(condition=cond, split=split, episode=k, replay_reproduces_the_record=bool(same), **({k2: d[k2] for k2 in ("strict",)} if "strict" in d else dict(error=d.get("error"))))
    with ThreadPoolExecutor(jobs) as ex:
        rows = list(ex.map(one, todo))
    table = {}
    for x in rows:
        t = table.setdefault(x["condition"], {}).setdefault(x["split"], dict(original_judge=0, settled_and_resting=0, no_robot_contact=0, both=0, replays_that_reproduce=0))
        s = x.get("strict") or {}
        t["original_judge"] += 1; t["replays_that_reproduce"] += bool(x["replay_reproduces_the_record"])
        t["settled_and_resting"] += bool(s.get("settled_and_resting")); t["no_robot_contact"] += bool(s.get("no_robot_contact")); t["both"] += bool(s.get("strict"))
    out = dict(note="the original judge is the experiment's definition; the two analyses are reported next to it, never in place of it", table=table, episodes=rows)
    (Path(root) / "sensitivity.json").write_text(json.dumps(out, indent=1))
    return out


def compare(root, src, task_name):
    """The new bound records against the source root's earlier results for the same episodes (where there are any)."""
    R1, R0 = Path(root) / task_name / "rep0", Path(src) / task_name / "rep0"
    out = {}
    for ev in sorted(R1.glob("final_eval/*/*/eval_*/evaluation.json")):
        e = json.loads(ev.read_text()); old = R0 / "final_eval" / e["condition"] / e["split"] / "results.json"
        oldp = R0 / "final_eval" / e["condition"] / "program.py"
        if not (e.get("complete") and old.exists() and oldp.exists()):
            continue
        new, was = json.loads((ev.parent / "results.json").read_text()), json.loads(old.read_text())
        key = lambda r: (bool(r.get("success")), r.get("robot_s"), r.get("commands"), r.get("segment_calls"), r.get("ended"))
        diff = [k for k, (a, b) in enumerate(zip(new, was)) if key(a) != key(b)]
        out[f"{e['condition']}/{e['split']}"] = dict(same_program=provenance.file_sha(oldp) == e["program_sha256"], episodes=len(new), identical=len(new) - len(diff),
                                                     successes_new=sum(bool(r.get("success")) for r in new), successes_earlier=sum(bool(r.get("success")) for r in was),
                                                     different_episodes=[dict(episode=k, new=key(new[k]), earlier=key(was[k])) for k in diff])
    (Path(root) / "comparison_with_earlier_records.json").write_text(json.dumps(out, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True); ap.add_argument("--tests", required=True); ap.add_argument("--programs", nargs="+", required=True)
    ap.add_argument("--dst", required=True); ap.add_argument("--config"); ap.add_argument("--splits", nargs="*"); ap.add_argument("--sensitivity", action="store_true")
    a = ap.parse_args()
    cfg = config.load(a.config); dst = Path(a.dst).resolve()
    for spec in a.programs:
        name, rest = spec.split("=", 1)
        src, conds = (rest.rsplit(":", 1) + ["A,B,C,D"])[:2] if ":" in rest else (rest, "A,B,C,D")
        root = provenance.must_be_new(dst / name)                   # (a root of its own: never an existing one)
        copy_tests(a.task, a.tests, root)
        prov = {}
        for c in conds.split(","):
            p = Path(src) / a.task / "rep0" / c / "final" / "program.py"
            if not p.exists():
                raise SystemExit(f"{p} missing")
            d = root / a.task / "rep0" / c / "final"; d.mkdir(parents=True)
            shutil.copy2(p, d / "program.py"); (d / "program.py").chmod(0o444)
            prov[c] = dict(source=str(p), sha256=provenance.file_sha(p))
        (root / "PROGRAMS.json").write_text(json.dumps(dict(made=time.strftime("%Y-%m-%d %H:%M:%S"), source_root=str(src), programs=prov,
                                                           evaluator=target.evaluator_provenance(TASKS[a.task]), judge="the original judge (sweep_blocks/env.py info())"), indent=1))
        launch.final(a.task, 0, cfg, conds=conds.split(","), out=root, splits=a.splits)
        cmp_ = compare(root, src, a.task)
        print("REEVAL", name, json.dumps({k: (v["identical"], v["episodes"]) for k, v in cmp_.items()}))
        if a.sensitivity:
            s = sensitivity(root, a.task, jobs=int(cfg["target_jobs"]))
            print("SENSITIVITY", name, json.dumps(s["table"]))


if __name__ == "__main__":
    main()
