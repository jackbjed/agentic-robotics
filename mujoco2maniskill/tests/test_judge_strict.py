"""The stricter reading of a success (sweep_blocks/judge_strict.py verdict), on made-up traces: the cases judge 1 lets
through must fail it, an honest sweep must pass. No simulator.

    ../.venv/bin/python -I tests/test_judge_strict.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from sweep_blocks import judge_strict as J  # noqa: E402

N = int(round(J.SETTLE_S / J.ACTION_S))


def cube(bottom=5.0, speed=0.0, robot=0.0, brush=0.0):
    return dict(bottom_mm=bottom, speed_mm_s=speed, robot_n=robot, brush_n=brush)


def trace(before, after, n_before=50):
    """n_before actions without success (records made by before(i)), then the success and the settling interval (after(j))."""
    return [dict(v1=False, cubes=before(i)) for i in range(n_before)] + [dict(v1=a.pop("v1", True), cubes=a) for a in (after(j) for j in range(N + 1))]


def three(**kw):
    return {f"cube_{i}": cube(**kw) for i in range(3)}


def test_1_an_honest_sweep_passes():
    t = trace(lambda i: three(bottom=0.0, speed=60.0, brush=2.0), lambda j: three(bottom=5.0, speed=0.5))
    v = J.verdict(t)
    assert v["v1"] and v["strict"] and v["held"] and v["rests"] and v["swept"] and v["why"] == [] and v["v1_at_action"] == 51, v


def test_2_a_cube_above_the_pan_but_not_in_it_fails():
    def after(j):
        c = three(bottom=5.0); c["cube_1"] = cube(bottom=42.0)      # lying on the brush's head, over the pan
        return c
    v = J.verdict(trace(lambda i: three(brush=2.0), after))
    assert v["v1"] and not v["strict"] and not v["rests"] and "cube_1 does not rest in the pan" in " ".join(v["why"]), v


def test_3_a_cube_passing_over_the_pan_fails():
    def after(j):                                                   # inside by XY for 5 actions, then out the other side
        c = three(bottom=5.0, speed=300.0 if j < 6 else 0.0); c["v1"] = j < 5
        return c
    v = J.verdict(trace(lambda i: three(brush=2.0), after))
    assert v["v1"] and not v["strict"] and not v["held"] and "0.20 s after the success" in v["why"][0], v


def test_4_a_cube_still_rolling_at_the_end_of_the_interval_fails():
    v = J.verdict(trace(lambda i: three(brush=2.0), lambda j: three(bottom=5.0, speed=35.0)))
    assert v["v1"] and v["held"] and not v["rests"] and not v["strict"], v


def test_5_cubes_pushed_in_by_the_gripper_fail():
    def before(i):
        c = three(bottom=0.0, speed=40.0)
        if 10 <= i < 30:
            c["cube_2"] = cube(bottom=0.0, speed=40.0, robot=3.0)   # a closed gripper pushes it for 0.8 s; the brush touches nothing
        return c
    v = J.verdict(trace(before, lambda j: three(bottom=5.0)))
    assert v["v1"] and v["held"] and v["rests"] and not v["swept"] and not v["strict"] and v["robot_push_actions"]["cube_2"] == 20, v
    graze = lambda i: three(bottom=0.0, robot=3.0 if i in (10, 11, 20) else 0.0, brush=2.0)       # touched twice in passing: not a push
    assert J.verdict(trace(graze, lambda j: three(bottom=5.0)))["strict"]


def test_6_no_success_stays_no_success():
    t = [dict(v1=False, cubes=three()) for _ in range(100)]
    v = J.verdict(t)
    assert not v["v1"] and not v["strict"], v
    cut = trace(lambda i: three(brush=2.0), lambda j: three(bottom=5.0))[:55]      # the interval was not simulated: never a strict success
    assert not J.verdict(cut)["strict"]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in tests:
        try:
            f(); print("PASS ", n)
        except Exception as e:
            bad += 1; print("FAIL ", n, "::", type(e).__name__, str(e)[:700])
    print(f"{len(tests) - bad} of {len(tests)} passed")
    sys.exit(1 if bad else 0)
