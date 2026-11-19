"""Skills written from scratch (library: false): no ctx.library, ctx.tools instead, the robot code not importable by
the program, and the static check that rejects a version reaching for it."""
import json

from agentic_robotics.config import episode_base, task_config
from agentic_robotics.evaluation.suites import episode_seed
from agentic_robotics.execution.episode import run_episode
from agentic_robotics.execution.guard import violations

PROG = '''
def run(ctx):
    out = dict(library=ctx.library is None, perception=ctx.perception is None, tools=ctx.tools is not None)
    try:
        import clutter_bench  # noqa: F401
        out["import"] = "allowed"
    except ImportError as e:
        out["import"] = "refused" if "not available" in str(e) else str(e)
    try:
        from helpers import h
        out["helper"] = h.X
    except Exception as e:
        out["helper"] = repr(e)
    obs = ctx.robot.observe_frame("L_wrist")
    P, ok = ctx.tools.world(obs)
    uv, z = ctx.tools.project(obs, P[240, 320])
    out["roundtrip_px"] = float(abs(uv[0] - 320) + abs(uv[1] - 240)) if ok[240, 320] else None
    tcp = ctx.robot.tcp_now()
    ctx.robot.move(tcp + [0, 0, 0.02], ctx.tools.down(0.3), ctx.tools.OPEN_W, 1.0, phase="up")
    ctx.robot.flush()
    out["moved"] = True
    return dict(result="ok", **out)
'''


def _skill(tmp_path, prog):
    (tmp_path / "skills").mkdir(); (tmp_path / "helpers").mkdir()
    (tmp_path / "skills" / "pick.py").write_text(prog)
    (tmp_path / "helpers" / "__init__.py").write_text("")
    (tmp_path / "helpers" / "h.py").write_text("X = 7\n")
    return tmp_path


def test_scratch_episode(tmp_path):
    (tmp_path / "sk").mkdir()
    sk = _skill(tmp_path / "sk", PROG)
    t = task_config("configs/tasks/clutter_pick_B_rig.yaml")
    ep = dict(episode_base(t, record=dict(video=False, observations="none"), library=False),
              split="val", level=1, idx=3, seed=episode_seed(13, 1, 3))
    r = run_episode(sk, ep, tmp_path / "out", sandbox=True)
    pr = r.get("program_result") or {}
    assert pr.get("result") == "ok", (r.get("program_error"), (tmp_path / "out" / "program" / "host.log").read_text()[-800:])
    assert pr["library"] and pr["perception"] and pr["tools"]
    assert pr["import"] == "refused" and pr["helper"] == 7 and pr["moved"]
    assert pr["roundtrip_px"] is not None and pr["roundtrip_px"] < 1e-3


def test_static_check(tmp_path):
    sk = _skill(tmp_path, "import numpy as np\nimport sys\nfrom helpers.h import X\n"
                          "def run(ctx):\n    m = sys.modules['task_fast']\n    return dict(result='ok')\n")
    v = violations(sk)
    assert any("sys.modules" in x for x in v), v
    (sk / "skills" / "pick.py").write_text("import numpy as np\nfrom helpers.h import X\ndef run(ctx):\n"
                                           "    '''see helpers/h.py'''\n    return dict(result='ok')\n")
    assert violations(sk) == []
    (sk / "helpers" / "g.py").write_text("from task_fast import world\n")
    assert any("task_fast" in x for x in violations(sk))
