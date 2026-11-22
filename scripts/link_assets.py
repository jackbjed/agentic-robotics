"""Put the assets of assets/manifest.yaml in place (symlinks or copies) and verify every sha256.

    .venv/bin/python -I scripts/link_assets.py            # link / copy, then verify
    .venv/bin/python -I scripts/link_assets.py --check    # verify only (exit 1 on any missing or changed file)
    .venv/bin/python -I scripts/link_assets.py --only sharpa_right sharpa_left   # these groups only
Optional groups (optional: true) are skipped when their root is not configured."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic_robotics import paths  # noqa: E402


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only", nargs="+", default=None, help="asset groups to place / verify (default: all)")
    a = ap.parse_args()
    man = paths.load_yaml("assets/manifest.yaml")
    bad = 0
    for name, g in man["assets"].items():
        if a.only and name not in a.only:
            continue
        try:
            src_root = paths.root_of(g["root"]) / g["src_dir"]
        except KeyError as e:
            if g.get("optional"):
                print(f"{name}: skipped ({e})"); continue
            raise
        dst = paths.ROOT / g["dst_dir"]
        n_ok = 0
        for rel, want in g["files"].items():
            s, d = src_root / rel, dst / rel
            if not a.check:
                if not s.exists():
                    if g.get("optional"):
                        continue
                    print(f"{name}: MISSING source {s}"); bad += 1; continue
                d.parent.mkdir(parents=True, exist_ok=True)
                if d.is_symlink() or d.exists():
                    d.unlink()
                if g["kind"] == "symlink":
                    d.symlink_to(s.resolve())
                else:
                    shutil.copy2(s, d)
            if not d.exists():
                if not g.get("optional"):
                    print(f"{name}: missing {d}"); bad += 1
                continue
            if want is not None and sha256(d) != want:
                print(f"{name}: CHANGED {rel} (sha256 differs from the manifest)"); bad += 1; continue
            n_ok += 1
        print(f"{name}: {n_ok}/{len(g['files'])} ok ({g['kind']})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
