"""The coding agent: headless Claude Code (`claude -p`), flags as documented by the installed CLI (`claude --help`,
version recorded per session). One session = one process, its working directory = the workspace. By default a fresh
context (no conversation, no session persistence). With the experiment's `learning.resume_session` (or `ar learn run
--resume-session`), the conversation is saved (Claude Code's own session store, ~/.claude/projects/<workspace>) and
the next iteration resumes it (`--resume <session id>`): the agent keeps its whole context, compacted by Claude Code
when the window fills; the session id stays the same. Costs: the CLI's `total_cost_usd` (and `modelUsage`) is
CUMULATIVE over the resumed conversation (checked 2026-11-20: a resumed invocation's modelUsage holds the earlier
invocations' tokens too), so the runner records the invocation's own cost (`cost_usd` = that total minus the total
after the previous invocation, `cost_before`) next to the CLI's figure (`cost_usd_total`); each result event's
`usage` and `num_turns` are the invocation's own. `--max-budget-usd` was not applied to the cumulative total (11-19:
resumed invocations already past their cap in total ran normally). These restrictions apply to every invocation,
resumed or not:

  --restricted                     file tools confined to the working directory; user/project/local settings ignored
  --tools Read,Edit,Write,Glob,Grep,Bash   the built-in tools it has (no web, no agents/tasks, no notebooks)
  --allowedTools ... Bash(<ar-practice> *)  the only shell command it may run: the practice command (outside the
                                   workspace; it runs the agent's code only in the program sandbox)
  --permission-mode dontAsk --permission-prompts none   anything not pre-allowed is denied, nobody is asked
  --strict-mcp-config (no --mcp-config)    no MCP servers
  --disable-slash-commands         no skills or commands
  --no-session-persistence         (unless resuming) the transcript is kept only in this run's log (stream-json)
  --max-budget-usd B               the session's spending cap (the CLI's own accounting, API-equivalent dollars)
  --include-partial-messages       (only with progress snapshots) each API message's final token usage as it ends;
                                   the runner keeps those events (message_delta) and drops the text deltas
Verified 2026-11-18 (docs/isolation.md): reads/writes outside the workspace and any other shell command, also chained
after the allowed one, were denied. Limits of this isolation: docs/isolation.md."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
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


def command(prompt, system_append, model, max_budget_usd, effort=None, persist=False, resume=None, partial=False):
    """persist: the conversation is saved (it can be resumed); resume: a saved session's id to continue; partial: the
    stream carries each message's final usage (progress snapshots)."""
    practice = str(paths.ROOT / "bin" / "ar-practice")
    cmd = [claude_bin(), "-p", prompt, "--output-format", "stream-json", "--verbose", "--restricted",
           "--tools", TOOLS, "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep", f"Bash({practice} *)", f"Bash({practice})",
           "--permission-mode", "dontAsk", "--permission-prompts", "none", "--strict-mcp-config",
           "--disable-slash-commands", "--max-budget-usd", f"{max_budget_usd:.2f}",
           "--append-system-prompt", system_append]
    if not (persist or resume):
        cmd.insert(cmd.index("--max-budget-usd"), "--no-session-persistence")
    if resume:
        cmd += ["--resume", str(resume)]
    if partial:
        cmd.insert(cmd.index("--verbose") + 1, "--include-partial-messages")
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def run_session(workspace, prompt, system_append, log_dir, model=None, max_budget_usd=2.0, wall_limit_s=1800,
                effort=None, env_extra=None, persist=False, resume=None, progress=None, cost_before=0.0):
    """Run one session to its end (or the wall limit). -> dict(cost, usage, turns, duration, denials, init info, exit).
    cost_before: with resume, the CLI's cumulative total_cost_usd after the previous invocation (-> cost_usd = this
    invocation's own cost; cost_usd_total = the CLI's figure).
    progress: dict(every=N, offset=the run's output tokens before this session, snapshot=f(cum_tokens, info)): f is
    called each time the run's output tokens (thinking included) pass a multiple of N (the stream's per-message usage)."""
    log_dir = Path(log_dir); log_dir.mkdir(parents=True, exist_ok=True)
    cmd = command(prompt, system_append, model, max_budget_usd, effort, persist=persist, resume=resume,
                  partial=progress is not None)
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH", "CONDA_PREFIX")}
    env.update(env_extra or {})
    tok = ((paths.local().get("secrets") or {}).get("claude_oauth_token_file"))
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = Path(tok).expanduser().read_text().strip()
    json.dump(dict(cmd=[c if c not in (prompt, system_append) else f"<{len(c)} chars>" for c in cmd], cli=cli_info(),
                   model=model, max_budget_usd=max_budget_usd, wall_limit_s=wall_limit_s, persist=bool(persist or resume),
                   resume=resume, t=time.time()),
              open(log_dir / "session_cmd.json", "w"), indent=1)
    (log_dir / "prompt.md").write_text(prompt); (log_dir / "system_append.md").write_text(system_append)
    t0 = time.time()
    out = open(log_dir / "claude.jsonl", "w"); err = open(log_dir / "claude.stderr", "w")
    acct = dict(output_tokens=0, thinking_tokens=0, messages=0, snapshots=0)
    if progress is None:
        p = subprocess.Popen(cmd, cwd=str(workspace), env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL)
    else:
        p = subprocess.Popen(cmd, cwd=str(workspace), env=env, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                             text=True, bufsize=1)
        pump = threading.Thread(target=_pump, args=(p.stdout, out, acct, progress, t0), daemon=True)
        pump.start()
    killed = False
    try:
        p.wait(timeout=wall_limit_s)
    except subprocess.TimeoutExpired:
        p.terminate(); killed = True
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait()
    if progress is not None:
        pump.join(timeout=120)
    out.close(); err.close()
    res = parse_stream(log_dir / "claude.jsonl")
    tot = res.get("cost_usd")
    res["cost_usd_total"] = tot
    if tot is not None and resume and cost_before:
        res["cost_before_usd"] = float(cost_before)
        res["cost_usd"] = round(tot - float(cost_before), 6) if tot >= float(cost_before) else tot   # (else: not resumed)
    if progress is not None:
        res["progress"] = dict(acct, every=progress["every"], offset=progress.get("offset", 0))
    res.update(exit=p.returncode, killed_at_wall_limit=killed, wall_s=round(time.time() - t0, 1),
               persisted=bool(persist or resume), resumed_from=resume)
    json.dump(res, open(log_dir / "session.json", "w"), indent=1)
    return res


def _pump(src, out, acct, progress, t0):
    """The session's stream -> claude.jsonl, without the text deltas; each message's final usage (message_delta) is
    counted and logged as a compact "usage_delta" line, and the snapshot callback runs at every multiple of N."""
    every, off = int(progress["every"]), int(progress.get("offset", 0))
    acct["next"] = (off // every + 1) * every
    for line in src:
        try:
            _pump_line(line, out, acct, progress, t0, off)
        except Exception as ex:                              # (never stop draining the pipe: the CLI would block)
            out.write(line)
            out.write(json.dumps(dict(type="pump_error", error=f"{type(ex).__name__}: {ex}"[:300])) + "\n")


def _pump_line(line, out, acct, progress, t0, off):
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        out.write(line); return
    if e.get("type") != "stream_event":
        out.write(line); out.flush(); return
    ev = e.get("event") or {}
    if ev.get("type") != "message_delta":                  # (text deltas, block starts / stops: dropped)
        return
    u = ev.get("usage") or {}
    acct["output_tokens"] += int(u.get("output_tokens") or 0)
    acct["thinking_tokens"] += int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
    acct["messages"] += 1
    cum = off + acct["output_tokens"]
    out.write(json.dumps(dict(type="usage_delta", t=round(time.time() - t0, 1), usage=u, session_output_tokens=acct["output_tokens"],
                              run_output_tokens=cum)) + "\n")
    out.flush()
    while cum >= acct["next"]:
        nxt = acct["next"]
        try:
            progress["snapshot"](nxt, dict(run_output_tokens=cum, session_output_tokens=acct["output_tokens"],
                                           session_thinking_tokens=acct["thinking_tokens"], messages=acct["messages"],
                                           wall_s=round(time.time() - t0, 1)))
            acct["snapshots"] += 1
        except Exception as ex:                              # (a failed copy must not stall the session's stream)
            out.write(json.dumps(dict(type="snapshot_error", at=nxt, error=f"{type(ex).__name__}: {ex}"[:300])) + "\n")
        acct["next"] = nxt + int(progress["every"])


def parse_stream(path):
    init, results, n_tool = None, [], {}
    for line in open(path):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            init = dict(model=e.get("model"), tools=e.get("tools"), mcp_servers=e.get("mcp_servers"),
                        permission_mode=e.get("permissionMode"), cwd=e.get("cwd"), version=e.get("claude_code_version"),
                        session_id=e.get("session_id"))
        elif e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    n_tool[c.get("name")] = n_tool.get(c.get("name"), 0) + 1
        elif e.get("type") == "result":
            results.append(e)
    # (19-11: a session can end with more than one result event, e.g. one more turn after a background notice. Each
    # event's num_turns, duration and usage are its own; total_cost_usd and modelUsage are cumulative, over the
    # resumed conversation's earlier invocations too: run_session subtracts those.)
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
