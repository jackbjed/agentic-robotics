"""The session's command must work with the environment a REAL session gives it (launch.tool_env), which inherits nothing.
(Found 2026-11-27: the ManiSkill python, once a path in the code, became machine-local, and the tool's environment did not
carry it: every target command of a real session would have failed.) Also: a fork from a root of another checkout.

    PYTHONNOUSERSITE=1 M2M_MANISKILL_PYTHON=... ../.venv/bin/python -I tests/test_session_tool_env.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from xsim import launch  # noqa: E402


def test_1_the_tools_environment_resolves_the_simulators_python():
    env = launch.tool_env("/tmp/some-session")
    assert set(env) <= {"XSIM_SESSION", "PYTHONNOUSERSITE", "MUJOCO_GL", "PATH", "M2M_MANISKILL_PYTHON", "M2M_GPU"} and Path(env["M2M_MANISKILL_PYTHON"]).exists()
    assert not any(k in env for k in ("HOME", "CLAUDE_CODE_OAUTH_TOKEN", "AR_LOCAL_CONFIG"))        # (no credential, no home: the tool needs neither)
    code = ("import sys; sys.path.insert(0, %r)\nfrom xsim.bi import backend\nfrom m2m.envpaths import maniskill_python\n"
            "r = backend._ms(['-c', 'import mani_skill, sapien; print(\"SIM\", mani_skill.__version__)'])\nprint(maniskill_python()); print(r.stdout.strip()[-40:], r.returncode)") % str(HERE)
    r = subprocess.run([str(HERE.parent / ".venv" / "bin" / "python"), "-I", "-c", code], env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "SIM 3." in r.stdout, (r.stdout[-300:], r.stderr[-500:])
    bare = dict(env); bare.pop("M2M_MANISKILL_PYTHON")
    cfg = HERE.parent / "configs" / "local.yaml"
    if not cfg.exists():                                             # (without it, in a checkout that has no local config: the failure this test is about)
        r2 = subprocess.run([str(HERE.parent / ".venv" / "bin" / "python"), "-I", "-c", code], env=bare, capture_output=True, text=True, timeout=300)
        assert r2.returncode != 0 and "ManiSkill environment's python is not known" in r2.stderr + r2.stdout


def test_2_a_fork_from_another_checkouts_root():
    sys.path.insert(0, str(HERE / "tests"))
    import test_provenance as tp
    with tempfile.TemporaryDirectory() as t:                         # (a source root that is NOT under this folder)
        src, dst_ = tp._fake_root(t)
        with tempfile.TemporaryDirectory(dir=HERE) as d:
            dst = Path(d) / "dst"
            r = subprocess.run([sys.executable, "-I", str(HERE / "xsim" / "fork_root.py"), "--task", "sweep_blocks", "--rep", "0", "--src", str(src), "--dst", str(dst.relative_to(HERE))],
                               capture_output=True, text=True, env=dict(os.environ))
            assert r.returncode == 0, r.stderr[-600:]
            note = json.loads((dst / "sweep_blocks" / "rep0" / "FORKED_FROM.json").read_text())
            assert note["source"] == str((src / "sweep_blocks" / "rep0").resolve()) and not (dst / "sweep_blocks" / "rep0" / "private" / "rounds").exists()


if __name__ == "__main__":
    bad = 0
    for n, f in sorted((n, f) for n, f in globals().items() if n.startswith("test_")):
        try:
            f(); print("PASS ", n)
        except Exception as e:
            bad += 1; print("FAIL ", n, "::", type(e).__name__, str(e)[:700])
    print(f"{2 - bad} of 2 passed"); sys.exit(1 if bad else 0)
