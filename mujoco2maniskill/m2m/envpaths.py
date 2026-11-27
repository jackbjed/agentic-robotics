"""Machine-local paths of this folder, kept out of the code: the python of the environment that has ManiSkill.

    M2M_MANISKILL_PYTHON=/path/to/env/bin/python        (environment variable), or
    maniskill_python: /path/to/env/bin/python           (a line of configs/local.yaml, which is not committed)"""
import os
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def maniskill_python():
    v = os.environ.get("M2M_MANISKILL_PYTHON")
    if not v:
        f = Path(os.environ.get("AR_LOCAL_CONFIG", PROJECT / "configs" / "local.yaml"))
        if f.exists():
            m = re.search(r"^maniskill_python:\s*['\"]?([^'\"#\n]+?)['\"]?\s*(#.*)?$", f.read_text(), re.M)
            v = m.group(1).strip() if m else None
    if not v or not Path(v).exists():
        raise SystemExit("the ManiSkill environment's python is not known: set M2M_MANISKILL_PYTHON, or maniskill_python: in configs/local.yaml"
                         + (f" (now: {v}, which does not exist)" if v else ""))
    return str(v)
