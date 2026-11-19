"""Re-stamp the skill-code hash of existing records (2026-11-18 fix: versions.tree_hash excluded every file whose
ABSOLUTE path contained "runs/", so records of snapshots under runs/ carried the hash of nothing, e3b0c44298fc1c14).
For each suite directory with a skill_snapshot/: suite.json "skills", every episodes/*/result.json versions.skills,
episodes.jsonl "skills"; the old value is kept as skills_unfixed. Practice episodes (runs/learning/*/practice
snapshots) are re-stamped through their snapshot's name. Idempotent.

    .venv/bin/python -I scripts/restamp_skill_hashes.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic_robotics import paths  # noqa: E402
from agentic_robotics.execution.versions import tree_hash  # noqa: E402

EMPTY = "e3b0c44298fc1c14"


def fix_result(f, h):
    r = json.loads(f.read_text())
    v = r.get("versions") or {}
    if v.get("skills") == EMPTY:
        v["skills_unfixed"], v["skills"] = EMPTY, h
        r["versions"] = v
        f.chmod(0o644) if not f.stat().st_mode & 0o200 else None
        f.write_text(json.dumps(r, indent=1, default=str))
        return 1
    return 0


n = 0
for sj in paths.RUNS.rglob("suite.json"):
    d = sj.parent; snap = d / "skill_snapshot"
    if not snap.exists():
        continue
    h = tree_hash(snap)
    S = json.loads(sj.read_text())
    if S.get("skills") == EMPTY:
        S["skills_unfixed"], S["skills"] = EMPTY, h; sj.write_text(json.dumps(S, indent=1))
    for f in (d / "episodes").glob("*/result.json"):
        n += fix_result(f, h)
    ej = d / "episodes.jsonl"
    if ej.exists():
        rows = [json.loads(x) for x in open(ej)]
        for r in rows:
            if r.get("skills") == EMPTY:
                r["skills"] = h
        ej.write_text("".join(json.dumps(r) + "\n" for r in rows))
for ws in paths.WORKSPACES.iterdir() if paths.WORKSPACES.exists() else []:
    meta = ws / ".ar_run.json"
    if not meta.exists():
        continue
    rid = json.loads(meta.read_text())["run_id"]
    snaps = paths.RUNS / "learning" / rid / "practice_snapshots"
    for call in sorted((ws / "practice" / "runs").glob("*")) if (ws / "practice" / "runs").exists() else []:
        cand = [s for s in snaps.glob(f"*_{call.name}")] if snaps.exists() else []
        if len(cand) != 1:
            continue
        h = tree_hash(cand[0])
        for f in call.glob("*/result.json"):
            n += fix_result(f, h)
print(f"re-stamped {n} episode records")
