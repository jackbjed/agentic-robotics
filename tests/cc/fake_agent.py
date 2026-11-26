#!/usr/bin/env python3
"""A scripted stand-in for the claude CLI, for the tests of bimanual/improve_cc.py (no model call, no token): it takes
the CLI's arguments, works in its working directory (the workspace) with plain file writes and the practice command,
and prints the CLI's stream (stream-json: init, assistant messages with their token usage, tool results, a result
event with the total cost, cumulative over a resumed conversation). FAKE_SCENARIO picks what it does."""
import json, os, subprocess, sys, uuid
from pathlib import Path

A = sys.argv[1:]
arg = lambda name, d=None: A[A.index(name) + 1] if name in A else d
prompt, budget, resume = arg("-p", ""), float(arg("--max-budget-usd", "1e9")), arg("--resume")
scenario = os.environ.get("FAKE_SCENARIO", "full")
home = Path(os.environ["HOME"]); home.mkdir(parents=True, exist_ok=True)
book = home / "fake_sessions.json"
B = json.loads(book.read_text()) if book.exists() else {}
sid = resume if resume in B else str(uuid.uuid4())
total = float(B.get(sid, {}).get("total", 0.0)); runs = int(B.get(sid, {}).get("runs", 0))
practice = os.environ["AR_CC_PRACTICE"]
PRICE = dict(input=4.0, write=5.0, read=0.2, output=20.0)           # (improve_cc.PRICES["default"], the 5-minute write)
spent, n = 0.0, 0

def emit(e):
    print(json.dumps(e), flush=True)

def say(text, tool=None, usage=None):
    """One reply of the 'model': its usage is what the launcher prices."""
    global spent, n
    n += 1
    u = usage or dict(input_tokens=200, cache_creation_input_tokens=2000, cache_read_input_tokens=20000, output_tokens=400)
    spent += (u["input_tokens"] * PRICE["input"] + u["cache_creation_input_tokens"] * PRICE["write"] + u["cache_read_input_tokens"] * PRICE["read"]
              + u["output_tokens"] * PRICE["output"]) / 1e6
    content = [dict(type="text", text=text)] + ([dict(type="tool_use", id=f"t{n}", name=tool[0], input=tool[1])] if tool else [])
    mid, start = f"msg_{sid[:6]}_{runs}_{n}", dict(u, output_tokens=1)      # (as the CLI: the output count comes with the message's end)
    emit(dict(type="stream_event", event=dict(type="message_start", message=dict(id=mid, model="fake-model", usage=start))))
    emit(dict(type="stream_event", event=dict(type="content_block_delta", delta=dict(type="text_delta", text=text))))
    emit(dict(type="assistant", message=dict(id=mid, model="fake-model", usage=start, content=content)))
    emit(dict(type="stream_event", event=dict(type="message_delta", usage=dict(output_tokens=u["output_tokens"]))))
    if spent > budget:
        end("error_max_budget_usd", True, "the budget is spent")

def bash(*words):
    say("I run the practice command.", ("Bash", dict(command=" ".join([practice, *words]))))
    r = subprocess.run([practice, *words], capture_output=True, text=True)
    emit(dict(type="user", message=dict(content=[dict(type="tool_result", tool_use_id=f"t{n}", content=r.stdout + r.stderr)])))
    return r.stdout

def write(name, body):
    say(f"I write {name}.", ("Write", dict(file_path=name, content=body)))
    Path(name).parent.mkdir(parents=True, exist_ok=True); Path(name).write_text(body)

def end(subtype="success", is_error=False, text="done"):
    B[sid] = dict(total=total + spent, runs=runs + 1); book.write_text(json.dumps(B))
    emit(dict(type="result", subtype=subtype, is_error=is_error, result=text, total_cost_usd=round(total + spent, 6), num_turns=n,
              usage=dict(output_tokens=400 * n), modelUsage={"fake-model": dict(costUSD=round(total + spent, 6))}, permission_denials=[]))
    sys.exit(0 if not is_error else 1)

WORLD = '''import numpy as np

def scene(scene, rng):
    x = -0.30 + 0.04 * float(rng.uniform(-1, 1))
    scene["mjcf"] = ('<mujoco><worldbody><body name="w_block" pos="%.3f 0.16 0.021"><freejoint name="w_block_joint"/>'
                     '<geom type="box" size=".02 .02 .02" mass=".05" rgba=".8 .2 .2 1"/></body></worldbody></mujoco>' % x)
    return scene

def goal(scene, rng):
    return "Push the red block at least 30 mm away from where it starts, and take the hand off it."

def success(state):
    d = float(np.linalg.norm(np.array(state.pos("w_block"))[:2] - np.array(state.start["w_block"])[:2]))
    return {"ok": d > 30 and not state.touching("w_block")["robot"], "moved_mm": d}
'''
PROGRAM = '''def run(robot, task):
    robot.show("start", robot.look(["scene"])["scene"].rgb)
    for y in (40, 290):
        r = robot.move("left", (-300, y, 28), (0, 0, -1), (0, 1, 0))
        print("move", y, r["done"], r["why"])
    robot.move("left", (-300, 290, 200), (0, 0, -1), (0, 1, 0))
'''
NOTHING = "def run(robot, task):\n    print('nothing')\n"

emit(dict(type="system", subtype="init", session_id=sid, model="fake-model", tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"], mcp_servers=[],
          permissionMode="dontAsk", cwd=os.getcwd(), claude_code_version="fake", slash_commands=[], agents=[]))
if scenario == "probe":                                            # what the stand-in can see and do from the workspace
    out = dict(cwd=os.getcwd(), files=sorted(p.name for p in Path(".").iterdir()), env=sorted(os.environ), resumed=bool(resume))
    Path("probe.json").write_text(json.dumps(out)); say("probe"); end()
if scenario == "stall":                                            # ends its turn without a final submit, every time
    say("I think about it." + (" (again)" if runs else "")); end()
if scenario == "overspend":                                        # replies that cost a lot: the launcher's cap must stop it
    while True:
        say("more", usage=dict(input_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=20000))
        import time; time.sleep(0.3)
if scenario == "limit" and runs == 0:                              # the subscription's usage limit, in the middle
    write("world.py", WORLD); bash("write_world", "world.py", "--note", "a block to push")
    end("error_during_execution", True, "Claude usage limit reached. Your limit will reset at 3pm.")
if scenario == "bad":                                              # calls that must be refused
    Path("../outside.py").write_text("x = 1\n") if os.environ.get("FAKE_WRITE_OUTSIDE") else None
    for words in (["write_program", "../outside.py", "--note", "x"], ["write_program", "/etc/passwd", "--note", "x"], ["nonsense"],
                  ["run_episodes", "--scenes", "s1", "--world", "0"], ["submit", "--final", "--world", "0"]):
        bash(*words)
    write("evil.py", "import os\ndef run(robot, task):\n    os.system('id')\n"); bash("write_program", "evil.py", "--note", "not allowed")
    write("eworld.py", "import subprocess\ndef scene(scene, rng):\n    return scene\n"); bash("write_world", "eworld.py", "--note", "not allowed")
    end()
# full (and limit, resumed; and feedback): a world, a program, scenes, episodes, pictures, the final submit with its summary
if "feedback" in scenario:
    bash("list_real_episodes"); eps = [l.split(":")[0] for l in bash("list_real_episodes").splitlines() if l.startswith("R")]
    if eps:
        bash("list_real_episodes", eps[0]); bash("read_real_output", "--episode", eps[0], "--max-lines", "5")
        bash("view_real_images", "--episode", eps[0], "--names", "look_00_scene")
if not Path("world.py").exists():
    write("world.py", WORLD)
if "w0" not in bash("read_world"):
    bash("write_world", "world.py", "--note", "a block to push: its x varies +-40 mm; success: moved more than 30 mm, hand off")
write("nothing.py", NOTHING); bash("write_program", "nothing.py", "--note", "does nothing (the check must fail)")
write("program.py", PROGRAM); bash("write_program", "program.py", "--note", "pushes along y through the block")
bash("new_scenes", "2"); bash("list_scenes")
bash("run_episodes", "--scenes", "s1", "--world", "0", "--version", "0")
out = bash("run_episodes", "--scenes", "s1", "s2", "--world", "0")
bash("read_output", "--scene", "s1", "--world", "0", "--max-lines", "8"); bash("view_images", "--scene", "s1@w0", "--names", "final", "start")
bash("read_program", "--version", "1", "--file", "copy_of_v1.py")
Path("notes.md").write_text("The block is pushed by the fingertip when the closing direction is along y.\n")
bash("submit", "--world", "0")
bash("submit", "--final", "--world", "0")                           # refused: no summary
Path("summary.md").write_text("SUMMARY. " + "The task as built: one red block to push; what the skill does; what was tested. " * 12 + "\n")
bash("submit", "--final", "--world", "0", "--summary", "summary.md", "--note", "the pushing program")
say("After the final submit I try once more."); bash("list_scenes")
if os.environ.get("FAKE_HANG"):                                     # (an agent that does not end its turn: the launcher stops it)
    import time; time.sleep(600)
end()
