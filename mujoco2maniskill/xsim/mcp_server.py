"""The session's ONE tool, as an MCP server on stdin/stdout (started by the agent's Claude Code CLI from the launcher's
--mcp-config; the agent has no shell at all). One tool, `run`, with one argument, `command`: a sub-command line of
xsim/session.py ("practice --seeds 1 2", "save_program program.py --note ...", ...). The session is the launcher's
XSIM_SESSION. Answers are plain text; never a traceback or a path of the harness.

Why not a shell command: a Bash tool allowed for one command only still ran other read-only commands (`ls`) in the
first real session (2026-11-27), so the shell was removed. (xsim/bin/xsim-tool, the same command for a shell, is kept
for the harness's own tests.)"""
import fcntl
import json
import os
import shlex
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):        # (nothing this server starts may see the agent's credentials)
    os.environ.pop(k, None)
OUT = sys.stdout
sys.stdout = sys.stderr                                            # (stdout carries the protocol only)
TOOL = dict(name="run", description="The session's command: give one sub-command line, as described in your instructions "
            "(for example: status). Files are named relative to your working directory.",
            inputSchema=dict(type="object", properties=dict(command=dict(type="string", description="a sub-command line, e.g. 'practice --seeds 1 2 3'")),
                             required=["command"]))


def send(msg):
    OUT.write(json.dumps(msg) + "\n"); OUT.flush()


def run(command):
    d = Path(os.environ.get("XSIM_SESSION") or "/nonexistent")
    try:
        if not (d / "state.json").is_file():
            return "this tool works only inside a session"
        try:
            argv = shlex.split(str(command))
        except ValueError as e:
            return f"REFUSED: the command line could not be read ({e})"
        if argv and Path(argv[0]).name in ("xsim-tool", "run", "xsim"):
            argv = argv[1:]
        from xsim.session import Session
        with open(d / ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return Session(d).command(argv)
    except Exception:
        try:
            with open(d / "tool_errors.log", "a") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        return "the command failed (an error of the harness, not yours): try again, or go on otherwise"


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        mid, method, p = m.get("id"), m.get("method"), m.get("params") or {}
        if method == "initialize":
            send(dict(jsonrpc="2.0", id=mid, result=dict(protocolVersion=p.get("protocolVersion", "2024-11-05"), capabilities=dict(tools={}),
                                                         serverInfo=dict(name="xsim", version="1"))))
        elif method == "tools/list":
            send(dict(jsonrpc="2.0", id=mid, result=dict(tools=[TOOL])))
        elif method == "tools/call":
            if p.get("name") != "run":
                send(dict(jsonrpc="2.0", id=mid, error=dict(code=-32602, message="unknown tool"))); continue
            text = run((p.get("arguments") or {}).get("command", ""))
            send(dict(jsonrpc="2.0", id=mid, result=dict(content=[dict(type="text", text=text)], isError=False)))
        elif method == "ping":
            send(dict(jsonrpc="2.0", id=mid, result={}))
        elif mid is not None:
            send(dict(jsonrpc="2.0", id=mid, error=dict(code=-32601, message="method not found")))


if __name__ == "__main__":
    main()
