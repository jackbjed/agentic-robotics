"""A second experiment root that starts from an existing replicate's INITIALIZATION, for running conditions again under
other allowances while the first root's runs stay as they are.

    ../.venv/bin/python -I xsim/fork_root.py --task sweep_blocks --rep 0 --src out/xsim --dst out/xsim_10x10

Copied (and nothing else): the replicate's frozen private scene sets (private/dev, private/f_pool, private/example:
their ep_NN.json and manifests, by an allow-list; NOT private/rounds or private/fpractice, the hidden side of the
source's own target runs, which the first version of this tool copied along: see docs/AUDIT_2026-11-27.md), the shared look and documents, prepared.json, the initialization's snapshot (the
initial program, the shared practice world, its construction notes) with its costs, condition A's record (the initial
program as a result), and the task's test splits list (the test scenes themselves stay where they are, frozen).
NOT copied: the first root's sessions of B to F (their programs, worlds, notes, transcripts, feedback) and every
held-out result. A session started in the new root is given what a session of the first root was given at its start."""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from xsim import target  # noqa: E402
from xsim.tasks import TASKS  # noqa: E402

SCENE_SETS = ("dev", "f_pool", "example")                           # what a fork takes of private/: the frozen scene sets
SCENE_FILES = ("panel_manifest.json", "verification.json", "reverification.json", "looks.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True); ap.add_argument("--rep", type=int, default=0); ap.add_argument("--src", required=True); ap.add_argument("--dst", required=True)
    a = ap.parse_args()
    src, dst = (HERE / a.src).resolve(), (HERE / a.dst).resolve()
    R0, R1 = src / a.task / f"rep{a.rep}", dst / a.task / f"rep{a.rep}"
    if R1.exists():
        raise SystemExit(f"{R1} exists already")
    R1.mkdir(parents=True)
    for name in SCENE_SETS:                                         # (ONLY the frozen scene sets: never the source's execution records)
        s = R0 / "private" / name
        if not (s / "panel_manifest.json").exists():
            raise SystemExit(f"{s} is not a frozen scene set")
        (R1 / "private" / name).mkdir(parents=True)
        for f in sorted(s.iterdir()):
            if f.is_file() and (f.name in SCENE_FILES or (f.name.startswith("ep_") and f.suffix == ".json")):
                shutil.copy2(f, R1 / "private" / name / f.name)
    left = sorted(p.name for p in (R0 / "private").iterdir() if p.name not in SCENE_SETS)
    shutil.copytree(R0 / "shared", R1 / "shared"); shutil.copy(R0 / "prepared.json", R1 / "prepared.json")
    (R1 / "init").mkdir()
    for n in ("snapshot.json", "A_usage.json", "build_usage.json", "program.py", "world_agent.py", "construction_notes.md"):
        shutil.copy(R0 / "init" / n, R1 / "init" / n)
    shutil.copytree(R0 / "init" / "snapshot", R1 / "init" / "snapshot")
    (R1 / "A").mkdir(); shutil.copy(R0 / "A" / "manifest.json", R1 / "A" / "manifest.json"); shutil.copytree(R0 / "A" / "final", R1 / "A" / "final")
    (dst / a.task / "test_private").mkdir(parents=True, exist_ok=True)
    shutil.copy(src / a.task / "test_private" / "splits.json", dst / a.task / "test_private" / "splits.json")
    task = TASKS[a.task]
    snap = json.loads((R1 / "init" / "snapshot.json").read_text())
    for n, h in snap["files"].items():
        if target.sha(R1 / "init" / "snapshot" / n) != h:
            raise SystemExit(f"the copied snapshot's {n} does not match its recorded hash")
    dev, dev_id = target.checked_panel(task, R1 / "private" / "dev", None)   # (the copied panel against its manifest of hashes)
    (R1 / "FORKED_FROM.json").write_text(json.dumps(dict(
        made=time.strftime("%Y-%m-%d %H:%M"), source=str(R0.relative_to(HERE)), snapshot=snap["files"], evaluator_sha=target.evaluator_sha(task),
        development_panel=[f.name for f in dev], development_panel_id=dev_id, copied=["private/dev, private/f_pool, private/example (scenes and manifests only)", "shared", "prepared.json", "init: snapshot and its costs", "A", "test_private/splits.json"],
        not_copied=["the sessions of B to F of the source root", "every held-out result (final_eval)", "the transcripts of the initialization",
                    "the source's execution records under private/: " + ", ".join(left)]), indent=1))
    for bad in ("rounds", "fpractice"):
        if (R1 / "private" / bad).exists():
            raise SystemExit(f"the fork holds private/{bad}: it must not")
    print(json.dumps(json.loads((R1 / "FORKED_FROM.json").read_text()), indent=1))


if __name__ == "__main__":
    main()
