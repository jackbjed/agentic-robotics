"""Whose result is this? Every episode's result.json names the execution that wrote it (a random id given by the batch
runner on the command line), the program and the scene it ran (their hashes). A batch runner takes a result only if
it carries the id it gave out for THAT execution, and only writes into folders that did not exist: a result left in
a folder by an earlier run can never be read as the outcome of a new one, whatever happens to the new process."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path


class StaleExecutionFolder(RuntimeError):
    """An execution folder exists already: nothing is run into it."""


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def new_run_id():
    return uuid.uuid4().hex


def stamp(run_id, program, scene=None, **more):
    """(written by an episode's own process into its result.json, under "provenance")"""
    return dict(run_id=run_id, program_sha256=file_sha(program), scene_sha256=file_sha(scene) if scene else None, written=round(time.time(), 1), **more)


def must_be_new(d):
    """Make an execution folder that did not exist (an empty one is taken). Raises StaleExecutionFolder otherwise."""
    d = Path(d)
    if d.exists() and (not d.is_dir() or any(d.iterdir())):
        raise StaleExecutionFolder(f"{d} exists and is not empty: an execution never writes into the folder of an earlier one")
    d.mkdir(parents=True, exist_ok=True)
    return d


def accept(d, run_id, program, scene=None):
    """-> (the result written by the execution `run_id` in folder d, None) or (None, why it is not taken)."""
    f = Path(d) / "result.json"
    if not f.exists():
        return None, "the episode's process wrote no result"
    try:
        res = json.loads(f.read_text())
    except ValueError:
        return None, "the result file is not readable"
    p = res.get("provenance") if isinstance(res, dict) else None
    if not isinstance(p, dict) or p.get("run_id") != run_id:
        return None, "the result in the folder was not written by this execution (another run's id, or none)"
    if p.get("program_sha256") != file_sha(program):
        return None, "the result is of another program"
    if scene is not None and p.get("scene_sha256") != file_sha(scene):
        return None, "the result is of another scene"
    return res, None
