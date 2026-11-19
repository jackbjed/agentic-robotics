"""The coding agent: headless Claude Code (`claude -p`), flags as documented by the installed CLI (`claude --help`,
version recorded per session). One session = one process, its working directory = the workspace, a fresh context
(no conversation, no session persistence), and these restrictions:

  --restricted                     file tools confined to the working directory; user/project/local settings ignored
  --tools Read,Edit,Write,Glob,Grep,Bash   the built-in tools it has (no web, no agents/tasks, no notebooks)
  --allowedTools ... Bash(<ar-practice> *)  the only shell command it may run: the practice command (outside the
                                   workspace; it runs the agent's code only in the program sandbox)
  --permission-mode dontAsk --permission-prompts none   anything not pre-allowed is denied, nobody is asked
  --strict-mcp-config (no --mcp-config)    no MCP servers
  --disable-slash-commands         no skills or commands
  --no-session-persistence         the transcript is kept only in this run's log (stream-json), not in ~/.claude
  --max-budget-usd B               the session's spending cap (the CLI's own accounting, API-equivalent dollars)
Verified 2026-11-18 (docs/isolation.md): reads/writes outside the workspace and any other shell command, also chained
after the allowed one, were denied. Limits of this isolation: docs/isolation.md."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import paths

TOOLS = "Read,Edit,Write,Glob,Grep,Bash"


def claude_bin():
    b = shutil.which("claude")
    if not b:
        raise RuntimeError("the claude CLI is not on PATH")
    return b


def cli_info():
    try:
        v = subprocess.run([claude_bin(), "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        st = json.loads(subprocess.run([claude_bin(), "auth", "status"], capture_output=True, text=True, timeout=30).stdout)
        return dict(version=v, auth=st.get("authMethod"), subscription=st.get("subscriptionType"), logged_in=st.get("loggedIn"))
    except Exception as e:
        return dict(error=f"{type(e).__name__}: {e}"[:200])


def command(prompt, system_append, model, max_budget_usd, effort=None):
    practice = str(paths.ROOT / "bin" / "ar-practice")
    cmd = [claude_bin(), "-p", prompt, "--output-format", "stream-json", "--verbose", "--restricted",
           "--tools", TOOLS, "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep", f"Bash({practice} *)", f"Bash({practice})",
           "--permission-mode", "dontAsk", "--permission-prompts", "none", "--strict-mcp-config",
           "--disable-slash-commands", "--no-session-persistence", "--max-budget-usd", f"{max_budget_usd:.2f}",
           "--append-system-prompt", system_append]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def run_session(workspace, prompt, system_append, log_dir, model=None, max_budget_usd=2.0, wall_limit_s=1800,
                effort=None, env_extra=None):
    """Run one session to its end (or the wall limit). -> dict(cost, usage, turns, duration, denials, init info, exit)."""
    log_dir = Path(log_dir); log_dir.mkdir(parents=True, exist_ok=True)
    cmd = command(prompt, system_append, model, max_budget_usd, effort)
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH", "CONDA_PREFIX")}
    env.update(env_extra or {})
    tok = ((paths.local().get("secrets") or {}).get("claude_oauth_token_file"))
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = Path(tok).expanduser().read_text().strip()
    json.dump(dict(cmd=[c if c not in (prompt, system_append) else f"<{len(c)} chars>" for c in cmd], cli=cli_info(),
                   model=model, max_budget_usd=max_budget_usd, wall_limit_s=wall_limit_s, t=time.time()),
              open(log_dir / "session_cmd.json", "w"), indent=1)
    (log_dir / "prompt.md").write_text(prompt); (log_dir / "system_append.md").write_text(system_append)
    t0 = time.time()
    out = open(log_dir / "claude.jsonl", "w"); err = open(log_dir / "claude.stderr", "w")
    p = subprocess.Popen(cmd, cwd=str(workspace), env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL)
    killed = False
    try:
        p.wait(timeout=wall_limit_s)
    except subprocess.TimeoutExpired:
        p.terminate(); killed = True
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait()
    out.close(); err.close()
    res = parse_stream(log_dir / "claude.jsonl")
    res.update(exit=p.returncode, killed_at_wall_limit=killed, wall_s=round(time.time() - t0, 1))
    json.dump(res, open(log_dir / "session.json", "w"), indent=1)
    return res


def parse_stream(path):
    init, results, n_tool = None, [], {}
    for line in open(path):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            init = dict(model=e.get("model"), tools=e.get("tools"), mcp_servers=e.get("mcp_servers"),
                        permission_mode=e.get("permissionMode"), cwd=e.get("cwd"), version=e.get("claude_code_version"))
        elif e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    n_tool[c.get("name")] = n_tool.get(c.get("name"), 0) + 1
        elif e.get("type") == "result":
            results.append(e)
    # (19-11: a session can end with more than one result event, e.g. one more turn after a background notice. Each
    # event's num_turns, duration and usage are its own; total_cost_usd and modelUsage are cumulative.)
    f = results[-1] if results else {}
    usage = {}
    for r in results:
        for k, v in (r.get("usage") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                usage[k] = usage.get(k, 0) + v
    first = results[0] if results else {}
    return dict(init=init, cost_usd=f.get("total_cost_usd"), usage=usage or None, model_usage=f.get("modelUsage"),
                turns=sum(int(r.get("num_turns") or 0) for r in results) if results else None,
                duration_ms=sum(int(r.get("duration_ms") or 0) for r in results) if results else None,
                n_results=len(results), subtype=f.get("subtype"), is_error=any(bool(r.get("is_error")) for r in results),
                result_text=(first.get("result") or "")[:4000],
                denials=[dict(tool=d.get("tool_name"), input=str(d.get("tool_input"))[:200])
                         for r in results for d in r.get("permission_denials") or []],
                tool_calls=n_tool)
