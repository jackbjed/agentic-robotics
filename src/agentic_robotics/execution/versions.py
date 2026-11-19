"""Version stamps for every record: the platform's git revision (+ dirty), the skill code's content hash, the vendored
snapshot's and the asset manifest's hashes."""
from __future__ import annotations

import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path

from .. import paths


def tree_hash(root, globs=("**/*.py", "**/*.yaml", "**/*.json", "**/*.md")):
    """sha256 over the relative paths and contents of the code files under root (caches, outputs excluded)."""
    root = Path(root)
    h = hashlib.sha256()
    files = sorted({p for g in globs for p in root.glob(g) if p.is_file() and "__pycache__" not in p.parts
                    and not any(x in p.parts for x in ("runs", "practice_runs", ".git"))})
    for p in files:
        h.update(str(p.relative_to(root)).encode()); h.update(b"\0"); h.update(p.read_bytes()); h.update(b"\0")
    return h.hexdigest()[:16]


@lru_cache(None)
def platform():
    def git(*a):
        try:
            return subprocess.run(["git", "-C", str(paths.ROOT), *a], capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    rev = git("rev-parse", "--short", "HEAD") or "uncommitted"
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", "--", "src", "configs", "assets"))
    return dict(platform_rev=rev, platform_dirty=dirty, vendor=tree_hash(paths.VENDOR),
                manifest=hashlib.sha256((paths.ROOT / "assets/manifest.yaml").read_bytes()).hexdigest()[:16])


def versions(skill_root):
    return dict(platform(), skills=tree_hash(skill_root))
