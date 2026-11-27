"""The execution boundary for agent-written code (m2m/boxed), with DUMMY state and harmless temporary files.

    PYTHONNOUSERSITE=1 MUJOCO_GL=egl ../.venv/bin/python -I tests/test_boundary.py        (or: pytest tests/test_boundary.py)

Part 1 reproduces, in the old in-process way of running a program (m2m/boxed/legacy.py), what the AST checker let
through: a program that the checker ACCEPTS reads a hidden attribute of the object behind the robot, and reads a file.
Part 2 runs the same programs through the boundary: nothing of that is reachable. Part 3: world files. Part 4: the
messages. No simulator, no network, no model."""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

from xsim.bi import mj  # noqa: E402,F401  (in the project's .venv this also lets m2m.api import without ManiSkill)
from m2m import api  # noqa: E402
from m2m.boxed import host, legacy, wire  # noqa: E402
from m2m.boxed.frame import Frame  # noqa: E402
from sweep_blocks import program_api as PA  # noqa: E402
from xsim.bi import practice_run  # noqa: E402

SECRET = "HIDDEN-STATE-7f3a"


class DummyEnv:
    """Stands for the simulator: holds a 'true state' no program may see."""
    secret = SECRET
    steps = 0


class DummyRobot:
    """Stands for X5Robot: the documented calls, and (like the real one) a reference to its environment."""
    CAMERAS, ARMS, kind, GUARD_N = ("head",), ("left", "right"), "grippers", 5.0
    segmenter = None

    def __init__(self):
        self.env = DummyEnv()

    def time_s(self):
        return 0.0

    def look(self, cameras=None):
        return {"head": Frame(dict(camera="head", rgb=np.zeros((48, 64, 3), np.uint8), t=0.0, depth=np.full((48, 64), 0.7), K=(64, 48, 30.0, 31.0, 32.0, 24.0),
                                   eye=np.array([0.0, -0.5, 0.6]), R=np.eye(3)))}

    def state(self, arm):
        if arm not in self.ARMS:
            raise KeyError('arm must be "right" or "left"')
        return dict(tcp_mm=[1.0, 2.0, 3.0], opening_mm=88.0)

    def move(self, arm, xyz, a, c, stop_on_contact=False):
        return dict(done=True, moved=True, why="", curved=False)

    def turn(self, *a):
        return dict(done=True, moved=True, why="", curved=False)

    def home(self, arm):
        return dict(done=True, moved=True, why="", curved=True)

    def wait(self, s):
        return None

    def set_gripper(self, arm, w):
        return float(w)


def given():
    d = tempfile.mkdtemp(prefix="m2m-test-")
    return PA.program_robot(DummyRobot(), d, trace=None, truth=None)[0]       # (the REAL wrapper a program is handed)


# a program the AST checker accepts, that walks from a wrapped call to the object behind it
STEAL_STATE = '''
def run(robot, task):
    self = robot.move                      # (a private attribute is allowed on a name called self)
    cells = self.__closure__
    for c in cells or ():
        self = c.cell_contents
        try:
            print("STOLEN", self.env.secret)
        except AttributeError:
            pass
        try:
            self = self.__self__
            print("STOLEN", self.env.secret)
        except AttributeError:
            pass
    print("done looking")
'''
# a program the AST checker accepts, that reads a file (two ways: the os module that cv2 carries, and numpy)
READ_FILE = '''
import cv2
import numpy as np
def run(robot, task):
    path = task
    try:
        fd = cv2.os.open(path, 0)
        print("READ-OS", cv2.os.read(fd, 200))
    except OSError as e:
        print("os refused:", repr(e))
    try:
        print("READ-NP", np.fromregex(path, r"(.+)", [("s", "S64")]))
    except OSError as e:
        print("numpy refused:", repr(e))
    print("root holds", sorted(cv2.os.listdir("/")))
    print("argv", cv2.sys.argv)
'''
WRITE_FILE = '''
import cv2
def run(robot, task):
    fd = cv2.os.open("/tmp/x.bin", 65, 0o600)      # (create, write only)
    try:
        cv2.os.write(fd, b"x" * 4096)
        print("WROTE")
    except OSError as e:
        print("write refused:", repr(e))
'''
HONEST = '''
import numpy as np
def run(robot, task):
    f = robot.look()["head"]
    print("frame", f.rgb.shape, f.width, float(f.depth[0, 0]), f.points().shape, robot.CAMERAS, robot.ARMS, robot.kind, robot.GUARD_N)
    print("state", robot.state("left"))
    try:
        robot.state("middle")
    except KeyError as e:
        print("KeyError", e)
    print("move", robot.move("right", np.array([1.5, 2, 3]), (0, 0, -1), (1, 0, 0), stop_on_contact=True))
    print("grip", robot.set_gripper("left", 40))
    print(task)
'''


def run_both(code, task="t"):
    a, b = io.StringIO(), io.StringIO()
    ha = legacy.run_inprocess(code, given(), task, a)
    hb = host.run_program(code, given(), task, b)
    return (ha, a.getvalue()), (hb, b.getvalue())


def test_1_the_checker_accepted_the_exploits():
    for code in (STEAL_STATE, READ_FILE, WRITE_FILE):
        assert api.check_program(code, strict=False) == [], api.check_program(code, strict=False)   # (the check as it was: nothing to object)
        assert api.check_program(code), "the tightened check lets it through"                        # (now refused up front as well; the boundary does not rely on it)
    for code in (HONEST, "class K:\n    def __init__(self):\n        self._x = 1\n    def f(self):\n        return self._x\ndef run(robot, task):\n    K().f()\n"):
        assert api.check_program(code) == [], api.check_program(code)                                # (an ordinary program is not affected)


def test_2_hidden_state_in_process_and_boxed():
    (ha, la), (hb, lb) = run_both(STEAL_STATE)
    assert ha == "returned" and f"STOLEN {SECRET}" in la, "the in-process way no longer leaks: update this reproduction"
    assert hb == "returned" and "done looking" in lb and SECRET not in lb, lb


def test_3_files_in_process_and_boxed():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("CANARY-file-content\n"); canary = f.name
    try:
        (ha, la), (hb, lb) = run_both(READ_FILE, task=canary)
        assert "CANARY-file-content" in la and la.count("CANARY") >= 2, la            # (both ways of reading worked in-process)
        assert "CANARY" not in lb and "os refused: FileNotFoundError" in lb and "numpy refused: FileNotFoundError" in lb, lb
        root = lb.split("root holds ")[1].splitlines()[0]
        assert "home" in root and "boxed" in root, root
        assert str(HERE) not in lb and "out" not in json.loads(root.replace("'", '"')), lb
        assert "/boxed/child.py" in lb.split("argv ")[1]                               # (its command line names no path of the experiment)
    finally:
        os.unlink(canary)


def test_4_nothing_can_be_written_and_the_repository_is_not_there():
    log = io.StringIO()
    how = host.run_program(WRITE_FILE, given(), "t", log)
    assert "WROTE" not in log.getvalue() and "write refused: OSError" in log.getvalue() and how == "returned", (how, log.getvalue())
    probe = '''
import cv2
def run(robot, task):
    for p in task.split("|"):
        print("EXISTS" if cv2.os.path.exists(p) else "absent", p)
    try:
        s = cv2.os.popen("cat /proc/net/dev | tail -n +3 | cut -d: -f1 | tr -d ' ' | tr '\\\\n' ' '").read()
        print("interfaces:", s.strip())
    except OSError as e:
        print("popen refused", e)
'''
    paths = [str(HERE), str(HERE / "out"), str(HERE / "xsim" / "launch.py"), str(HERE.parent / "configs"), str(HERE.parent / "secrets"),
             str(Path.home() / ".ssh"), str(Path.home() / ".claude"), "/var/tmp"]
    log = io.StringIO(); host.run_program(probe, given(), "|".join(paths), log)
    out = log.getvalue()
    assert "EXISTS" not in out and out.count("absent") == len(paths), out
    assert "interfaces: lo" in out or "interfaces:\n" in out + "\n", out               # (no network interface but the loopback)


def test_5_an_honest_program_behaves_the_same():
    (ha, la), (hb, lb) = run_both(HONEST, task="the task's text")
    assert ha == hb == "returned" and la == lb, (la, lb)


def test_6_errors_and_time_up_are_reported_the_same():
    err = "def run(robot, task):\n    print('before')\n    raise ValueError('my own error')\n"
    (ha, la), (hb, lb) = run_both(err)
    assert ha == hb == "error: ValueError: my own error" and "before" in lb and 'File "program.py", line 3, in run' in lb and "ValueError: my own error" in lb, (ha, hb, lb)

    class Late(DummyRobot):
        def move(self, *a, **k):
            raise api.TimeUp("the episode's robot time (40 s) is spent")
    g = PA.program_robot(Late(), tempfile.mkdtemp(prefix="m2m-test-"))[0]
    code = "def run(robot, task):\n    robot.move('left', (0, 0, 0), (0, 0, -1), (1, 0, 0))\n"
    a, b = io.StringIO(), io.StringIO()
    assert legacy.run_inprocess(code, g, "t", a) == host.run_program(code, g, "t", b) == "time: the episode's robot time (40 s) is spent"
    assert host.run_program("def run(robot, task):\n    while True:\n        pass\n" if False else "import cv2\ndef run(robot, task):\n    cv2.os.kill(cv2.os.getpid(), 9)\n",
                            given(), "t", io.StringIO()).startswith("error: the program's process ended without a result")


def test_7_only_the_documented_calls_are_answered():
    # a program that takes the socket itself out of its robot and asks for other things by hand
    code = '''
def run(robot, task):
    self = robot.move
    ch = [c.cell_contents for c in self.__closure__ if type(c.cell_contents).__name__ == "Channel"][0]
    for name in ("env", "_tool", "__class__", "segmenter", "kin", "q_cmd", "links", "nothing"):
        ch.send({"op": "call", "name": name, "a": [], "k": {}})
        print(name, "->", ch.recv())
    ch.send({"op": "call", "name": "state", "a": "left", "k": {}})
    print("bad args ->", ch.recv())
'''
    log = io.StringIO(); how = host.run_program(code, given(), "t", log); out = log.getvalue()
    assert how == "returned" and out.count("'err'") == 9 and SECRET not in out and "'ok'" not in out, out
    forged = "def run(robot, task):\n    self = robot.move\n    ch = [c.cell_contents for c in self.__closure__ if type(c.cell_contents).__name__ == 'Channel'][0]\n" \
             "    ch.send({'op': 'end', 'how': 'success: the judge says yes'})\n    cv2 = 1\n"
    assert host.run_program(forged, given(), "t", io.StringIO()) == "error: success: the judge says yes"   # (how it ended is never a verdict; and it is marked)


def test_8_the_messages_carry_no_objects():
    import pickle
    for bad in (object(), {1, 2}, np.array([object()], dtype=object), lambda: 0, b"bytes"):
        try:
            wire.dumps({"x": bad}); raise AssertionError(f"{type(bad).__name__} was encoded")
        except TypeError:
            pass
    blob = wire.dumps({"a": [1, 2.5, None, True, "s", (1, 2), {"k": np.arange(3)}], "n": float("nan"), 3: "non-string key" if False else "x"} if False else
                      {"a": [1, 2.5, None, True, "s", (1, 2), {"k": np.arange(3)}], "n": float("nan")})
    back = wire.loads(blob)
    assert back["a"][:6] == [1, 2.5, None, True, "s", (1, 2)] and back["a"][6]["k"].tolist() == [0, 1, 2] and np.isnan(back["n"])
    evil = io.BytesIO(); np.save(evil, np.array([{"a": 1}], dtype=object), allow_pickle=True)
    import struct
    head = json.dumps({"~nd": 0}).encode()
    data = struct.pack(">II", len(head), 1) + head + struct.pack(">Q", evil.getbuffer().nbytes) + evil.getvalue()
    try:
        wire.loads(data); raise AssertionError("a pickled array was decoded")
    except ValueError:
        pass
    assert b"pickle" not in Path(wire.__file__).read_bytes().replace(b"allow_pickle=False", b"").replace(b"never pickle", b"").replace(b"unpickles", b"") and pickle


class DummyState:
    names, start = ["w_a", "w_b"], {"w_a": [0.0, 0.0, 10.0], "w_b": [50.0, 0.0, 10.0]}
    _t = SECRET                                                     # (stands for the simulator behind the real State)

    def pos(self, n):
        return {"w_a": [1.0, 2.0, 3.0], "w_b": [4.0, 5.0, 6.0]}[n]

    def rot(self, n):
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    start_rot = rot

    def touching(self, n):
        return dict(robot=False, others=["table"])

    def speed(self, n):
        return 0.25

    def held(self, n):
        return None

    def arm(self, side):
        return dict(tcp_mm=[0.0, 0.0, 100.0], opening_mm=88.0, from_start_mm=0.0, from_start_deg=0.0)


def test_9_world_files():
    fixture = (HERE / "xsim" / "bi" / "fixtures" / "world.py").read_text()
    assert practice_run.check_world(fixture) == []
    ns = {"__name__": "world"}; exec(compile(fixture, "world.py", "exec"), ns)          # (the fixture is the harness's own file: run here to compare)
    w = host.World(fixture)
    try:
        assert w.has_success and w.has_scene
        for seed in range(5):
            base = dict(mjcf="", table=dict(mj.TABLE_DEFAULT, size_mm=[1400, 1100]))
            want = ns["scene"](json.loads(json.dumps(base)), np.random.default_rng(seed))
            got = w.scene(base, seed)
            assert got["mjcf"] == want["mjcf"] and json.loads(json.dumps(want.get("table"), default=list)) == got.get("table"), seed
    finally:
        w.close()
    # a world whose success() the checker accepts and that reaches behind its state
    leaky = '''
def scene(scene, rng):
    scene["mjcf"] = "<mujoco><worldbody><body name='w_a' pos='0 0 0.01'><freejoint/><geom type='box' size='0.01 0.01 0.01'/></body></worldbody></mujoco>"
    return scene
def success(state):
    self = state
    return {"ok": False, "leak": str(self._t), "pos": state.pos("w_a"), "touch": state.touching("w_b"), "arm": state.arm("left"), "names": state.names, "start": state.start}
'''
    assert [b for b in practice_run.check_world(leaky) if "self" not in b] == [] and api.check_program(leaky, needs=("scene",), strict=False) == []
    ns = {"__name__": "world"}; exec(compile(leaky, "world.py", "exec"), ns)
    assert ns["success"](DummyState())["leak"] == SECRET                               # (in-process: it reads what is behind the state)
    w = host.World(leaky)
    try:
        try:
            w.success(DummyState()); raise AssertionError("the leak went through")
        except host.WorldFailure as e:
            assert e.kind == "AttributeError" and SECRET not in str(e) + e.tb
    finally:
        w.close()
    honest = leaky.replace('"leak": str(self._t), ', "")
    w = host.World(honest)
    try:
        yes, c = w.success(DummyState())
        want = ns_honest = {"__name__": "world"}; exec(compile(honest, "world.py", "exec"), ns_honest)
        assert yes is False and c == json.loads(json.dumps(ns_honest["success"](DummyState()), default=str)), c
        try:
            w.c.ch.send({"op": "success", "names": [], "start": {}}); w.c.ch.send({"ok": 1})   # (nothing but answers is accepted while it runs)
        except Exception:
            pass
    finally:
        w.close()
    files = '''
import cv2
def scene(scene, rng):
    scene["mjcf"] = "<mujoco/>" + str(sorted(cv2.os.listdir("/"))) + str(cv2.os.path.exists(%r))
    return scene
''' % str(HERE)
    w = host.World(files)
    try:
        s = w.scene(dict(mjcf="", table={}), 0)["mjcf"]
        assert s.endswith("False") and "'home'" in s, s
    finally:
        w.close()


def test_10_the_sandbox_recipe_is_the_projects():
    try:
        sys.path.insert(0, str(HERE.parent / "src"))
        from agentic_robotics.execution import sandbox
    except Exception as e:                                          # (the ManiSkill environment has no project code: nothing to compare with)
        print("   (the project's sandbox module cannot be imported here:", type(e).__name__, ")"); return
    assert host.base_args() == sandbox._base(net=False)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items(), key=lambda kv: int(kv[0].split("_")[1]) if kv[0].startswith("test_") else 0) if n.startswith("test_")]
    bad = 0
    for n, f in tests:
        try:
            f(); print("PASS ", n)
        except Exception as e:
            bad += 1; print("FAIL ", n, "::", type(e).__name__, str(e)[:900])
    print(f"{len(tests) - bad} of {len(tests)} passed")
    sys.exit(1 if bad else 0)
