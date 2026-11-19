#!/usr/bin/env bash
# Creates the project-local environment with uv. Everything lands inside this repository:
#   .uv/python  standalone CPython   .uv/cache  uv's download cache   .venv  core (python 3.11)
# Nothing global is touched: no conda env, no ~/.local, no shell startup file, no sudo.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
unset VIRTUAL_ENV VIRTUAL_ENV_PROMPT CONDA_PREFIX || true      # (an inherited venv would receive the packages)
export UV_PYTHON_INSTALL_DIR="$ROOT/.uv/python" UV_CACHE_DIR="$ROOT/.uv/cache" PYTHONNOUSERSITE=1
UV="${UV:-uv}"
"$UV" venv --python-preference only-managed --python 3.11 "$ROOT/.venv"
"$UV" pip install --python "$ROOT/.venv/bin/python" -r "$ROOT/envs/requirements-core.txt"
"$UV" pip install --python "$ROOT/.venv/bin/python" --no-deps -e "$ROOT"
"$ROOT/.venv/bin/python" -I -c "import mujoco, numpy, cv2; print('core ok: mujoco', mujoco.__version__, 'numpy', numpy.__version__)"
