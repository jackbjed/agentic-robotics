"""Repository paths and the machine-local configuration (configs/local.yaml, gitignored: asset roots, render device,
secret FILE paths). Nothing machine-specific is committed; everything else reads its locations from here."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CONFIGS = ROOT / "configs"
VENDOR = Path(__file__).resolve().parent / "vendor"
DATA = ROOT / "data"
RUNS = ROOT / "runs"
WORKSPACES = ROOT / "workspaces"
INITIAL_SKILLS = ROOT / "initial_skills"


@lru_cache(None)
def local() -> dict:
    f = Path(os.environ.get("AR_LOCAL_CONFIG", CONFIGS / "local.yaml"))
    if not f.exists():
        raise FileNotFoundError(f"{f} missing: copy configs/local.example.yaml to configs/local.yaml and fill it in")
    return yaml.safe_load(f.read_text()) or {}


def root_of(name: str) -> Path:
    r = (local().get("roots") or {}).get(name)
    if not r:
        raise KeyError(f"asset root {name!r} is not set in configs/local.yaml (roots:)")
    return Path(r).expanduser()


def load_yaml(rel) -> dict:
    p = Path(rel)
    return yaml.safe_load((p if p.is_absolute() else ROOT / p).read_text())
