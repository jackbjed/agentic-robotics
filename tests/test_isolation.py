"""The program sandbox is closed, and a program cannot reach privileged state through the socket."""
import textwrap

from agentic_robotics import paths
from agentic_robotics.evaluation.suites import episode_seed
from agentic_robotics.execution import sandbox
from agentic_robotics.execution.episode import run_episode


def test_sandbox_selftest():
    s = sandbox.selftest()
    assert s["ok"], s


def test_program_cannot_read_outside(tmp_path):
    d = tmp_path / "prog"; (d / "skills").mkdir(parents=True)
    probes = [str(paths.ROOT / "data" / "layouts" / "test_L0.jsonl"), str(paths.ROOT / "configs" / "local.yaml"),
              str(paths.RUNS), str(paths.ROOT.parent / "embodied-agents" / "secrets")]
    (d / "skills" / "pick.py").write_text(textwrap.dedent(f"""
        import os, socket
        def run(ctx):
            seen = [p for p in {probes!r} if os.path.exists(p)]
            try:
                socket.create_connection(("127.0.0.1", 5590), timeout=1); net = True
            except OSError:
                net = False
            h, _ = ctx.link.call("oracle_plan")
            return dict(result="probe", seen=seen, net=net, privileged=h)
    """))
    ep = dict(split="test", level=0, idx=10, seed=episode_seed(5, 0, 10), scene=dict(preset="d415"), perception="oracle",
              robot_time_limit_s=60, record=dict(video=False, observations="none"))
    r = run_episode(d, ep, tmp_path / "e")
    pr = r["program_result"]
    assert pr["seen"] == [] and pr["net"] is False and pr["privileged"]["ok"] is False, pr
