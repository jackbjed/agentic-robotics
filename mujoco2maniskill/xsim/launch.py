"""The cross-simulator transfer experiments (practice simulator: MuJoCo; target environment: ManiSkill): conditions
A to F of a task, per independent replicate. These are transfers BETWEEN SIMULATORS, not to a physical robot.

    PY=../.venv/bin/python   (run from mujoco2maniskill/, with -I)
    $PY -I xsim/launch.py prepare --task insert --rep 0        no model: the private target configurations (development panel,
                                                              F's practice pool, the final test splits), the shared look and documents
    $PY -I xsim/launch.py init    --task insert --rep 0        PAID: the shared initial program (condition A's result) and the shared
                                                              practice world; then the immutable snapshot C, D and E start from
    $PY -I xsim/launch.py run     --task insert --rep 0 --cond D     PAID: one condition's isolated development session
    $PY -I xsim/launch.py final   --task insert --rep 0 [--conds A B ...]   no model: the frozen final programs on the final test splits
    $PY -I xsim/launch.py report  --task insert               no model: the manifest of every session, the learning curves, the final table
    [--config FILE.json]  overrides of xsim/config.py (budgets, caps, splits, ...)

Where things are (out/xsim/<task>/):
  test_private/              the final test configurations (ID and OOD): one set for all replicates; never given to a session
  rep<r>/private/            the evaluator's side of a replicate: the development panel, F's pool, the private record of every round
  rep<r>/shared/             what every condition is given: the look, the documents
  rep<r>/init/               the shared initialization: program (A), world builder's session, snapshot/ (+ snapshot.json: hashes)
  rep<r>/<cond>/             a condition's session record (xsim/session.py); manifest.json
  rep<r>/final_eval/         the final evaluation's results (never shown to a session)
The agents' workspaces are under /var/tmp/ar-cc (outside every checkout: no project instruction file is above them)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
from xsim import config, prompts, target  # noqa: E402
from xsim.session import Session, sha  # noqa: E402
from xsim.tasks import TASKS  # noqa: E402

OUT = HERE / "out" / "xsim"
WORK = Path("/var/tmp/ar-cc")
TOOL = HERE / "xsim" / "bin" / "xsim-tool"
SESSION_CONDS = ("B", "C", "D", "E", "F")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rep_dir(task, rep, out=OUT):
    return Path(out) / task / f"rep{rep}"


# ------------------------------------------------------------------------------------------------ prepare (no model)
def prepare(task_name, rep, cfg, out=OUT):
    task, R = TASKS[task_name], rep_dir(task_name, rep, out)
    priv = R / "private"
    sd = cfg["seeds"]
    T = Path(out) / task_name / "test_private"
    if task.get("rig") == "x5":                                     # (the two-arm tasks have their own scenes: xsim/bi/backend.py)
        from xsim.bi import backend
        return backend.prepare(task_name, task, rep, cfg, R, T, log=log)
    if not (T / "splits.json").exists():                            # the final test splits: made once, before any result exists
        log("the final test configurations (one set for all replicates)")
        ref = lambda name: task["reference"] if name in cfg["shown_solvable"] else None
        ids = target.configs(task, cfg["splits"]["test_id"], sd["test_id"], T / "test_id", cfg, log=log, reference=ref("test_id"))
        splits = dict(test_id=[str(f) for f in ids])
        for name in [k for k in cfg["splits"] if k.startswith("ood_") and k in task["ood"]]:
            base = target.configs(task, cfg["splits"][name], sd["ood"] + 500 * len(splits), T / (name + "_base"), cfg, log=log, reference=ref("ood_base"))
            splits[name] = [str(f) for f in target.ood(task, name, base, T / name)]
        (T / "splits.json").write_text(json.dumps(dict(made=time.strftime("%Y-%m-%d %H:%M"), task=task_name, validation=cfg["validation"],
                                                       shown_solvable=[k for k in cfg["shown_solvable"] if k != "dev" and k != "f_pool"], reference=str(task["reference"]), splits=splits), indent=1))
    if not (priv / "dev").exists():
        log(f"replicate {rep}: the development panel and F's practice pool")
        ref = lambda name: task["reference"] if name in cfg["shown_solvable"] else None
        target.configs(task, cfg["target_panel"], sd["dev"] + 1000 * rep, priv / "dev", cfg, log=log, reference=ref("dev"))
        target.configs(task, cfg["f_pool"], sd["dev"] + 1000 * rep + 300, priv / "f_pool", cfg, log=log, reference=ref("f_pool"))
        target.freeze(task, priv / "dev"); target.freeze(task, priv / "f_pool")
    sh = R / "shared"
    if not (sh / "look" / "state.txt").exists():
        ex = target.configs(task, 1, sd["example"] + 100 * rep, priv / "example", cfg, log=log)[0]
        r = target._ms([HERE / "xsim" / "target_side.py", "look", "--scene", ex, "--out", sh / "look"])
        if r.returncode != 0:
            raise SystemExit("the look failed: " + r.stderr[-800:])
        (sh / "docs").mkdir(parents=True, exist_ok=True)
        (sh / "docs" / "interface.md").write_text(prompts.interface(task) + "\n\n" + prompts.RULES + "\n")
        (sh / "docs" / "task.md").write_text(task["text"] + "\n")
        shutil.copy(HERE / "xsim" / "docs" / "robot.md", sh / "docs" / "robot.md")
    used = {int(Path(f).stem) for f in (priv / "dev").glob("[0-9]*.json")} | {int(Path(f).stem) for f in (priv / "f_pool").glob("[0-9]*.json")}
    test = {int(Path(f).stem) for k, v in json.loads((T / "splits.json").read_text())["splits"].items() for f in v}
    if used & test:
        raise SystemExit("development and test configurations overlap")
    man = dict(task=task_name, replicate=rep, dev_seeds=sorted(int(Path(f).stem) for f in (priv / "dev").glob("[0-9]*.json")),
               observation=cfg["observation"], shared={str(f.relative_to(sh)): target.sha(f) for f in sorted(sh.rglob("*")) if f.is_file()})
    (R / "prepared.json").write_text(json.dumps(man, indent=1))
    return R


# ------------------------------------------------------------------------------------------------ the agent (isolated, headless)
def token_file():
    sys.path.insert(0, str(PROJECT / "src"))
    from agentic_robotics import paths
    t = (paths.local().get("secrets") or {}).get("claude_oauth_token_file")
    return Path(t).expanduser() if t else None


def usd_of(u, model, cfg):
    P = cfg["pricing"].get(str(model), cfg["pricing"]["default"])
    cc = u.get("cache_creation") or {}
    w1h = int(cc.get("ephemeral_1h_input_tokens") or 0); w5m = int(u.get("cache_creation_input_tokens") or 0) - w1h
    return (int(u.get("input_tokens") or 0) * P["input"] + w5m * P["write_5m"] + w1h * P["write_1h"]
            + int(u.get("cache_read_input_tokens") or 0) * P["read"] + int(u.get("output_tokens") or 0) * P["output"]) / 1e6


OUT_TOKENS_PER_CHAR = 0.53


def out_usd(extra, model, cfg):
    """The OUTPUT tokens of an invocation so far, in $. The stream's per-message usage does not have them (it is the
    count at the message's start: 8 or 16), and they were a quarter to a half of a session's cost. Estimated from what
    the stream does give: the CLI's running thinking-token events, plus OUT_TOKENS_PER_CHAR tokens per character of the
    visible text and tool inputs. On the five sessions of 2026-11-27 this lands within 1.1 % of the CLI's own totals
    (which replace it as soon as an invocation ends)."""
    P = cfg["pricing"].get(str(model), cfg["pricing"]["default"])
    return (extra["thinking"] + OUT_TOKENS_PER_CHAR * extra["chars"]) * P["output"] / 1e6


def run_agent(record, root, system, prompt, cfg, cap_usd, hours, shell=True, finished=lambda: False, claude=None, again=None):
    """The isolated Claude Code session(s) of one development stage. record: where cost.json and claude/ are kept.
    -> dict(usd, turns, tokens, wall_s, invocations, model). Stops at the cap, the wall-clock limit, or when the agent
    ends its turn with the stage finished (it is asked to go on, cfg['nudges'] times, if it ends without)."""
    record, root = Path(record), Path(root); ws = root / "workspace"
    for p in (ws, *ws.parents):
        if any((p / n).exists() for n in ("CLAUDE.md", "AGENTS.md", ".claude", ".mcp.json")) and p != Path.home():
            raise SystemExit(f"{p} holds a CLAUDE.md / AGENTS.md / .claude: the workspace must have none above it")
    for n in ("home", "config"):
        (root / n).mkdir(parents=True, exist_ok=True)
    (record / "claude").mkdir(exist_ok=True)
    tf = token_file()
    if claude is None and not (tf and tf.is_file()):
        raise SystemExit("configs/local.yaml secrets.claude_oauth_token_file names no token file")
    env = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), LANG=os.environ.get("LANG", "C.UTF-8"), HOME=str(root / "home"),
               CLAUDE_CONFIG_DIR=str(root / "config"), TERM="dumb", DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
               XSIM_SESSION=str(record), BASH_DEFAULT_TIMEOUT_MS="3600000", BASH_MAX_TIMEOUT_MS="3600000")
    if claude is None:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = tf.read_text().strip()
    # No shell for any agent. The session's command is an MCP tool (xsim/mcp_server.py), the only server there is. (`shell`:
    # the session HAS that tool.) --restricted confines the file tools to the workspace; --safe-mode would switch MCP off,
    # so it is used only where there is no tool (A).
    tools = "Read,Edit,Write,Glob,Grep"
    allowed = ["Read", "Edit", "Write", "Glob", "Grep"] + (["mcp__xsim__run"] if shell else [])
    mcp = root / "mcp.json"
    mcp.write_text(json.dumps(dict(mcpServers=dict(xsim=dict(command=str(PROJECT / ".venv" / "bin" / "python"), args=["-I", str(HERE / "xsim" / "mcp_server.py")],
                                                             env=dict(XSIM_SESSION=str(record), PYTHONNOUSERSITE="1", MUJOCO_GL="egl",
                                                                      PATH=os.environ.get("PATH", "/usr/bin:/bin"))))) if shell else dict(mcpServers={})))
    env.update(MCP_TOOL_TIMEOUT="3600000", MCP_TIMEOUT="60000")
    t0, tot, k, resume, msgs_all, model = time.time(), 0.0, 0, None, {}, None
    cost = dict(usd=0.0, turns=0, tokens={}, t0=t0, t=t0)
    text = prompt
    while True:
        left = cap_usd - tot
        wall_left = hours * 3600 - (time.time() - t0)
        if left <= 0.01 or wall_left <= 5:
            break
        cmd = [claude or shutil.which("claude") or "claude", "-p", text, "--output-format", "stream-json", "--verbose", "--restricted"] + \
              ([] if shell else ["--safe-mode"]) + \
              ["--setting-sources", "", "--tools", tools, "--allowedTools", *allowed, "--permission-mode", "dontAsk", "--permission-prompts", "none",
               "--mcp-config", str(mcp), "--strict-mcp-config", "--disable-slash-commands", "--max-budget-usd", f"{left:.2f}", "--append-system-prompt", system,
               "--model", cfg["model"], "--effort", cfg["effort"]] + (["--resume", resume] if resume else [])
        msgs, cli_total, extra = {}, None, dict(thinking=0, chars=0)
        inv = dict(invocation=k, started=time.strftime("%Y-%m-%d %H:%M:%S"), t_start=round(time.time(), 1), prompt=text, system_prompt_appended=system,
                   resumed_session=resume, model=cfg["model"], effort=cfg["effort"], tools=tools, allowed_tools=allowed, budget_left_usd=round(left, 4),
                   wall_left_s=round(wall_left, 1), workspace=str(ws), command=[c if c not in (text, system) else "<see prompt / system_prompt_appended>" for c in cmd],
                   stream=f"claude_{k}.jsonl (the CLI's stream-json, written line by line as it arrives: complete up to the moment it stops)")
        (record / "claude" / f"invocation_{k}.json").write_text(json.dumps(inv, indent=1))
        with open(record / "claude" / f"claude_{k}.jsonl", "w") as out, open(record / "claude" / f"claude_{k}.stderr", "w") as err:
            p = subprocess.Popen(cmd, cwd=str(ws), env=env, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL, text=True)
            try:
                for line in p.stdout:
                    out.write(line); out.flush()
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") == "system" and d.get("subtype") == "init":
                        resume, model = d.get("session_id") or resume, d.get("model") or model
                    m = d.get("message") if d.get("type") == "assistant" else None
                    if m and m.get("usage"):
                        msgs[m.get("id")] = dict(usage=m["usage"], model=m.get("model"))
                    if d.get("type") == "system" and d.get("subtype") == "thinking_tokens":
                        extra["thinking"] += int(d.get("estimated_tokens_delta") or 0)
                    for b in (m or {}).get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "text":
                            extra["chars"] += len(b.get("text") or "")
                        elif isinstance(b, dict) and b.get("type") == "tool_use":
                            extra["chars"] += len(json.dumps(b.get("input")))
                    if d.get("type") == "result" and d.get("total_cost_usd") is not None:
                        cli_total = float(d["total_cost_usd"])
                    est = sum(usd_of(x["usage"], x["model"], cfg) for x in msgs.values()) + out_usd(extra, model, cfg)
                    cost = dict(usd=round(tot + (cli_total if cli_total is not None else est), 5), turns=len(msgs_all) + len(msgs), t0=t0, t=round(time.time(), 1),
                                estimated=cli_total is None, invocation=k, model=model)
                    tmp = record / "cost.tmp"; tmp.write_text(json.dumps(cost)); os.replace(tmp, record / "cost.json")
                    if cost["usd"] >= cap_usd or time.time() - t0 > hours * 3600:
                        p.terminate(); break
                p.wait(timeout=120)
            except Exception:
                p.kill(); p.wait()
        est = sum(usd_of(x["usage"], x["model"], cfg) for x in msgs.values()) + out_usd(extra, model, cfg)
        tot += cli_total if cli_total is not None else est
        msgs_all.update({f"{k}:{i}": v for i, v in msgs.items()})
        why = "the budget was reached" if tot >= cap_usd - 0.01 else "the time limit was reached" if time.time() - t0 > hours * 3600 else "the CLI ended"
        inv.update(ended=time.strftime("%Y-%m-%d %H:%M:%S"), t_end=round(time.time(), 1), returncode=p.returncode, stopped_because=why,
                   usd_this_invocation=round(cli_total if cli_total is not None else est, 5), usd_total=round(tot, 5), session_id=resume, assistant_messages=len(msgs))
        (record / "claude" / f"invocation_{k}.json").write_text(json.dumps(inv, indent=1))
        try:
            archive(record, root)                                   # (after every invocation: a session that dies later has this much kept)
        except Exception as e:
            (record / "claude" / "archive_error.txt").write_text(str(e))
        k += 1
        if finished() or k > int(cfg["nudges"]) or (not shell and again is None):
            break
        text = again or prompts.CONTINUE                            # (the same session is asked to go on: counted in `invocations`)
    tok = dict(input=0, cache_read=0, cache_write=0, output=0)
    for v in msgs_all.values():
        u = v["usage"]
        tok["input"] += int(u.get("input_tokens") or 0); tok["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        tok["cache_write"] += int(u.get("cache_creation_input_tokens") or 0); tok["output"] += int(u.get("output_tokens") or 0)
    res = dict(usd=round(tot, 5), turns=len(msgs_all), tokens=tok, wall_s=round(time.time() - t0, 1), invocations=k, model=model,
               pricing=cfg["pricing"].get(str(model), cfg["pricing"]["default"]), cost_basis="API-equivalent (the CLI's own total where it gave one)")
    (record / "cost.json").write_text(json.dumps(dict(res, t0=t0, t=round(time.time(), 1))))
    return res


def workspace(tag):
    root = WORK / f"xsim_{tag}_{time.strftime('%m%d_%H%M%S')}"
    (root / "workspace").mkdir(parents=True)
    return root


def give_shared(R, ws, cond, task=None):
    shutil.copytree(R / "shared" / "docs", ws / "docs"); shutil.copytree(R / "shared" / "look", ws / "look")
    if cond in ("build", "C", "D", "E"):
        (ws / "docs" / "worlds.md").write_text(prompts.worlds_doc(task or {}) + "\n")
    if cond in ("B", "C", "D", "F"):
        (ws / "docs" / "target.md").write_text(prompts.target_doc(task or {}, cond) + "\n")
    if cond == "F" and (task or {}).get("rig") == "x5":
        (ws / "docs" / "practice.md").write_text(prompts.practice_doc(task) + "\n")
    (ws / "notes.md").write_text("")


# ------------------------------------------------------------------------------------------------ shared initialization
def transcript(record):
    """The session's transcript in reading order -> claude/transcript.md: every prompt given (invocation_k.json), then
    the CLI's stream of that invocation: the assistant's messages (what the CLI exposes of its thinking, its text, its
    tool calls) and every tool result. Complete text; an image in a result is named, not embedded (it is in the stream)."""
    record = Path(record); C = record / "claude"; out = ["# Session transcript: " + record.name, ""]
    pm = record / "prompt.md"
    if pm.exists():
        out += ["## Instructions and first message (prompt.md)", "", "````", pm.read_text(errors="replace").rstrip(), "````", ""]
    for f in sorted(C.glob("claude_*.jsonl"), key=lambda f: int(f.stem.split("_")[1])):
        k = f.stem.split("_")[1]
        iv = json.loads((C / f"invocation_{k}.json").read_text()) if (C / f"invocation_{k}.json").exists() else {}
        out += [f"## Invocation {k}" + (f" ({iv.get('started')} to {iv.get('ended', 'not ended')}; {iv.get('stopped_because', '')})" if iv else ""), ""]
        if iv.get("prompt"):
            out += ["**Prompt given:**", "", "````", str(iv["prompt"]), "````", ""]
        for line in f.read_text(errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t, m = d.get("type"), d.get("message")
            if t == "system" and d.get("subtype") == "init":
                out += [f"*CLI start: model {d.get('model')}, tools {d.get('tools')}, MCP {[s.get('name') for s in d.get('mcp_servers') or []]}, session {d.get('session_id')}*", ""]
            elif t == "result":
                out += [f"*CLI result: {d.get('subtype')}, turns {d.get('num_turns')}, cost ${d.get('total_cost_usd')}, stop {d.get('stop_reason')}*", ""]
            elif isinstance(m, dict) and isinstance(m.get("content"), list):
                for c in m["content"]:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "thinking":
                        th = c.get("thinking") or ""
                        out += ["**Assistant (thinking):** " + (th if th else "*(not exposed by the CLI: an empty block with a signature)*"), ""]
                    elif c.get("type") == "text":
                        out += [("**Assistant:** " if t == "assistant" else "**User:** ") + str(c.get("text")), ""]
                    elif c.get("type") == "tool_use":
                        out += [f"**Tool call** `{c.get('name')}`:", "", "````json", json.dumps(c.get("input"), indent=1), "````", ""]
                    elif c.get("type") == "tool_result":
                        r = c.get("content")
                        parts = [r] if isinstance(r, str) else [(x.get("text") if x.get("type") == "text" else f"[{x.get('type')}: in the stream file]") if isinstance(x, dict) else str(x)
                                                               for x in (r or [])]
                        out += ["**Tool result**" + (" (error)" if c.get("is_error") else "") + ":", "", "````", "\n".join(str(x) for x in parts), "````", ""]
            elif isinstance(m, dict) and isinstance(m.get("content"), str):
                out += [("**Assistant:** " if t == "assistant" else "**User:** ") + m["content"], ""]
    (C / "transcript.md").write_text("\n".join(out))
    return C / "transcript.md"


def archive(record, root):
    """Everything of a session's conversation, kept in its record (safe to call at any time, also for a session that
    failed or was stopped): claude/transcript.md; claude/cli_files/ (the CLI's own session file and the log of the session
    tool's server; not its account or settings files); workspace_final/ (the workspace as it stands, without the folders that are copies of
    records: feedback, practice, validation)."""
    record, root = Path(record), Path(root)
    transcript(record)
    dst = record / "claude" / "cli_files"
    for base in ("config", "home"):
        for f in (root / base).rglob("*"):
            keep = ("projects" in f.parts and f.suffix == ".jsonl") or any(x.startswith("mcp-logs") for x in f.parts)     # (its session file; the tool server's log.
            if f.is_file() and keep and f.stat().st_size < 200_000_000:                                                  #  not its account or settings files)
                g = dst / base / f.relative_to(root / base); g.parent.mkdir(parents=True, exist_ok=True); shutil.copy(f, g)
    ws, wf = root / "workspace", record / "workspace_final"
    if ws.is_dir():
        if wf.exists():
            shutil.rmtree(wf)
        shutil.copytree(ws, wf, ignore=lambda d, names: [n for n in names if Path(d) == ws and n in ("feedback", "practice", "validation")])
    return dict(transcript=str(record / "claude" / "transcript.md"), workspace=str(wf))


def audit_paths(record, ws):
    """Did the agent's tools touch anything outside its workspace? -> the paths outside it that its tool calls named
    (from the CLI's own stream: every Read / Edit / Write / Glob / Grep call and every shell command)."""
    ws, bad, calls = str(Path(ws).resolve()), [], 0
    for f in sorted((Path(record) / "claude").glob("claude_*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") if d.get("type") == "assistant" else None
            for c in (m or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    calls += 1; i = c.get("input") or {}
                    for k in ("file_path", "path", "notebook_path"):
                        v = i.get(k)
                        if isinstance(v, str) and v:
                            q = str((Path(ws) / v).resolve()) if not v.startswith("/") else str(Path(v).resolve())
                            if not (q == ws or q.startswith(ws + "/")):
                                bad.append(dict(tool=c.get("name"), path=v))
                    if c.get("name") not in ("Read", "Edit", "Write", "Glob", "Grep", "mcp__xsim__run"):
                        bad.append(dict(tool=c.get("name"), input=str(i)[:200]))
    return dict(tool_calls=calls, outside_workspace=bad)


def init(task_name, rep, cfg, out=OUT, fake=None, step=None):
    """The shared initial program and the shared practice world of a replicate, then the snapshot. fake: dict(program=FILE,
    world=FILE, notes=TEXT) in place of the two paid sessions (tests). step: "A" = only the initial program (condition A's
    session); "world" = only the shared practice world's construction (needs A's program for nothing, but comes second);
    None = all three (A, the world, the snapshot)."""
    task, R = TASKS[task_name], rep_dir(task_name, rep, out)
    I = R / "init"; I.mkdir(exist_ok=True)
    if not (I / "program.py").exists() and step in (None, "A"):     # 1. the initial program, without any execution (condition A)
        root = workspace(f"{task_name}_rep{rep}_A"); ws = root / "workspace"
        give_shared(R, ws, "A", task)
        rec = I / "A_session"; rec.mkdir(exist_ok=True)
        system = prompts.system_text("A", task, cfg)
        (rec / "prompt.md").write_text("# system\n\n" + system + "\n\n# message\n\n" + prompts.FIRST["A"] + "\n")
        if fake:
            shutil.copy(fake["program"], ws / "program.py"); use = dict(usd=0.0, turns=0, tokens={}, wall_s=0.0, fake=True)
        else:
            use = run_agent(rec, root, system, prompts.FIRST["A"], cfg, cfg["budget_usd"]["A"], cfg["max_hours"]["A"], shell=False,
                            finished=lambda: (ws / "program.py").is_file(), again=prompts.CONTINUE_A)
        aud = audit_paths(rec, ws) if not fake else None
        (I / "A_usage.json").write_text(json.dumps(dict(use, workspace=str(ws), audit=aud, wrote_program=(ws / "program.py").is_file()), indent=1))
        if not (ws / "program.py").is_file():
            raise SystemExit("the initial program was not written")
        shutil.copy(ws / "program.py", I / "program.py"); (I / "program.py").chmod(0o444)
        log(f"initial program: ${use['usd']}, {use['wall_s']} s" + (f"; tool calls {aud['tool_calls']}, outside the workspace: {len(aud['outside_workspace'])}" if aud else ""))
    if step == "A":
        return dict(step="A", program=str(I / "program.py"), usage=json.loads((I / "A_usage.json").read_text()))
    if not (I / "world_agent.py").exists() and step != "snapshot":                         # 2. the shared practice world (no target feedback, no skill)
        root = workspace(f"{task_name}_rep{rep}_build"); ws = root / "workspace"
        give_shared(R, ws, "build", task)
        rec = I / "build_session"
        s = Session.create(rec, "build", task_name, R, root, cfg)
        system = prompts.system_text("build", task, cfg, cmd=str(TOOL))
        (rec / "prompt.md").write_text("# system\n\n" + system + "\n\n# message\n\n" + prompts.FIRST["build"] + "\n")
        if fake:
            s.add_world(Path(fake["world"]).read_text(), "a stand-in world (test)", "construction", source="test")
            (ws / "notes.md").write_text(fake.get("notes", "stand-in notes\n")); use = dict(usd=0.0, turns=0, tokens={}, wall_s=0.0, fake=True)
            s._t_finish(argparse.Namespace(version=None))
        else:
            use = run_agent(rec, root, system, prompts.FIRST["build"], cfg, cfg["budget_usd"]["build"], cfg["max_hours"]["build"],
                            finished=lambda: Session(rec).st["finished"])
        s = Session(rec)
        if not s.st["worlds"]:
            raise SystemExit("the practice world was not built")
        w = s.st["worlds"][-1]
        shutil.copy(rec / "worlds" / f"w{w['k']}_agent.py", I / "world_agent.py")
        shutil.copy(ws / "notes.md", I / "construction_notes.md")
        (I / "build_usage.json").write_text(json.dumps(dict(use, workspace=str(ws), validation_jobs=s.st["validation_jobs"], world_versions=len(s.st["worlds"]),
                                                            audit=audit_paths(rec, ws) if not fake else None), indent=1))
        log(f"practice world: ${use['usd']}, {use['wall_s']} s, {s.st['validation_jobs']} validation jobs")
    S = I / "snapshot"
    if step == "world" and not (I / "program.py").exists():         # (the world was built while A's writer is still at work: no snapshot yet)
        return dict(step="world", world=str(I / "world_agent.py"), usage=json.loads((I / "build_usage.json").read_text()), snapshot="not yet: A's program is not written")
    for n in ("program.py", "world_agent.py", "construction_notes.md"):
        if not (I / n).exists():
            raise SystemExit(f"no snapshot yet: {n} is missing")
    if not (I / "snapshot.json").exists():                          # 3. the immutable snapshot
        S.mkdir()
        for n in ("program.py", "world_agent.py", "construction_notes.md"):
            shutil.copy(I / n, S / n)
        files = {n: target.sha(S / n) for n in ("program.py", "world_agent.py", "construction_notes.md")}
        for f in S.iterdir():
            f.chmod(0o444)
        S.chmod(0o555)
        (I / "snapshot.json").write_text(json.dumps(dict(made=time.strftime("%Y-%m-%d %H:%M:%S"), files=files,
                                                         cost=dict(A=json.loads((I / "A_usage.json").read_text()).get("usd"),
                                                                   build=json.loads((I / "build_usage.json").read_text()).get("usd"))), indent=1))
    return json.loads((I / "snapshot.json").read_text())


# ------------------------------------------------------------------------------------------------ a condition's session
def start(task_name, rep, cond, cfg, out=OUT):
    """A condition's record and workspace, forked from the snapshot (checked by hash). -> (Session, root)."""
    task, R = TASKS[task_name], rep_dir(task_name, rep, out)
    snap = json.loads((R / "init" / "snapshot.json").read_text()); S = R / "init" / "snapshot"
    for n, h in snap["files"].items():
        if target.sha(S / n) != h:
            raise SystemExit(f"the snapshot's {n} does not match its recorded hash")
    root = workspace(f"{task_name}_rep{rep}_{cond}"); ws = root / "workspace"
    give_shared(R, ws, cond, task)
    s = Session.create(R / cond, cond, task_name, R, root, cfg, snapshot=snap["files"])
    shutil.copy(S / "program.py", ws / "program.py")
    s.add_program((S / "program.py").read_text(), "the shared initial program (written without any execution)", source="shared initialization")
    if cond in ("C", "D", "E"):
        shutil.copy(S / "world_agent.py", ws / "world.py"); shutil.copy(S / "construction_notes.md", ws / "construction_notes.md")
        s.add_world((S / "world_agent.py").read_text(), "the shared practice world (built from the look, without target feedback)", "construction",
                    source="shared initialization")
    if cond == "F" and task.get("rig") == "x5":                     # (oracle-simulator practice: the harness's starting practice file, the same for every F)
        from xsim.bi import backend as bi_backend
        shutil.copy(bi_backend.PRACTICE_START, ws / "practice.py")
        s.add_world(bi_backend.PRACTICE_START.read_text(), "the starting practice file: fresh scenes, no self-check yet", "practice file", source="shared initialization")
    for f in ws.rglob("*"):
        if f.is_file():
            f.chmod(0o644)
    start_hash = dict(program=s.st["versions"][0]["sha"], world_agent=s.st["worlds"][0]["agent_sha"] if s.st["worlds"] else None,
                      world=s.st["worlds"][0]["sha"] if s.st["worlds"] else None)
    s.st["start"] = start_hash; s.save()
    return Session(R / cond), root


def run(task_name, rep, cond, cfg, out=OUT, agent=None):
    """One condition's development session. agent: a callable(session_dir, workspace, tool) in place of the paid agent (tests)."""
    task, R = TASKS[task_name], rep_dir(task_name, rep, out)
    snap = json.loads((R / "init" / "snapshot.json").read_text())
    if cond == "A":                                                 # A: the shared initial program, frozen as it is
        d = R / "A"; (d / "final").mkdir(parents=True, exist_ok=True)
        shutil.copy(R / "init" / "snapshot" / "program.py", d / "final" / "program.py")
        use = json.loads((R / "init" / "A_usage.json").read_text())
        man = dict(condition="A", task=task_name, replicate=rep, model=cfg["model"], effort=cfg["effort"], snapshot=snap["files"],
                   cost=dict(initial_program_usd=use.get("usd"), world_construction_usd=None, adaptation_usd=0.0, total_usd=use.get("usd")),
                   usage=use, practice_episodes=0, target_interactions=0, wall_s=use.get("wall_s"),
                   final=dict(program_sha=target.sha(d / "final" / "program.py")), budgets=dict(usd=cfg["budget_usd"]["A"]))
        (d / "manifest.json").write_text(json.dumps(man, indent=1))
        return man
    s, root = start(task_name, rep, cond, cfg, out)
    pool = len(target.panel_files(task, R / "private" / "f_pool"))
    system = prompts.system_text(cond, task, cfg, cmd=str(TOOL), pool=pool)
    (s.dir / "prompt.md").write_text("# system\n\n" + system + "\n\n# message\n\n" + prompts.FIRST[cond] + "\n")
    t0 = time.time()
    if agent is not None:
        agent(s.dir, root / "workspace", TOOL); use = dict(usd=Session(s.dir).usd(), turns=0, tokens={}, wall_s=round(time.time() - t0, 1), fake=True)
    else:
        use = run_agent(s.dir, root, system, prompts.FIRST[cond], cfg, cfg["budget_usd"][cond], cfg["max_hours"][cond],
                        finished=lambda: Session(s.dir).st["finished"])
    s = Session(s.dir)
    if not s.st["finished"]:                                        # (stopped by a limit: its latest complete program is its result)
        s._t_finish(argparse.Namespace(version=None)); s = Session(s.dir)
        s.st["final"]["ended_by"] = "a limit (budget or time), not the agent"; s.save()
    s.checkpoints()
    kept = s.keep_notes(s.dir / "final")                            # (again at the very end: what the agent wrote after `finish`, or without it)
    st = s.st
    init_cost = snap.get("cost") or {}
    world_cost = init_cost.get("build") if cond in ("C", "D", "E") else None
    man = dict(condition=cond, task=task_name, replicate=rep, model=cfg["model"], effort=cfg["effort"], snapshot=snap["files"], start=st.get("start"),
               budgets=dict(usd=cfg["budget_usd"][cond], practice_episodes=cfg["practice_episode_cap"] if cond in "CDEF" else 0,
                            target_submissions=cfg["target_submissions"] if cond in "BCD" else 0, target_panel=config.scenes(cfg, cond) if cond in "BCD" else 0,
                            practice_simulator="the target simulator (ManiSkill)" if cond == "F" else "MuJoCo" if cond in "CDE" else None, max_hours=cfg["max_hours"][cond]),
               cost=dict(initial_program_usd=init_cost.get("A"), world_construction_usd=world_cost, adaptation_usd=use.get("usd"),
                         total_usd=round(float(init_cost.get("A") or 0) + float(world_cost or 0) + float(use.get("usd") or 0), 4)),
               usage=use, practice_episodes=st["practice"]["episodes"], validation_jobs=st["validation_jobs"],
               target_interactions=st["target"]["episodes"], target_rounds=st["rounds"],
               # every episode run IN the target simulator: the judged submissions, and (F only) practice episodes and scene checks
               target_simulator_interactions=st["target"]["episodes"] + ((st["practice"]["episodes"] + st["validation_jobs"]) if cond == "F" else 0),
               wall_s=use.get("wall_s"), versions=len(st["versions"]), worlds=len(st["worlds"]), checkpoints=st["checkpoints"], final=st["final"],
               workspace=str(root / "workspace"), observation=cfg["observation"])
    if cond == "F":
        man["label"] = "oracle-simulator practice with sparse external evaluation"
        man["oracle_reference"] = ("F practises in the target simulator itself: up to " + str(cfg["practice_episode_cap"]) + " practice episodes and scene checks, plus "
                                   + str(int(cfg["target_submissions"]) * config.scenes(cfg, cond)) + " judged episodes in its submissions = up to "
                                   + str(int(cfg["practice_episode_cap"]) + int(cfg["target_submissions"]) * config.scenes(cfg, cond))
                                   + " target-simulator interactions. Its extra target-simulator practice makes it an oracle reference, not a "
                                   "target-interaction-matched comparison with B, C or D.")
    from xsim import provenance as _prov
    man["code"] = _prov.code_version()                              # (the commit this session's harness ran from)
    man["evaluator"] = dict(sha=st.get("evaluator_sha"), version=getattr(target, "EVALUATOR_VERSION", None))
    man["agent_notes"] = dict(kept=kept, summary_written="summary.md" in kept, where="final/notes.md, final/summary.md; rounds/round_k/agent_notes/; checkpoints/")
    if agent is None:
        man["audit"] = audit_paths(s.dir, root / "workspace")
    (s.dir / "manifest.json").write_text(json.dumps(man, indent=1))
    return man


# ------------------------------------------------------------------------------------------------ final evaluation, report
def ending(r):
    """How an episode ended, in classes that do not mix a success with a timeout: the program returned | the judge ended
    the episode (a success: the next command was refused because the episode was over) | the robot time ran out | the
    program raised | the program was refused | the harness failed (no result of this execution, or a wall-clock stop)."""
    e = str(r.get("ended") or "")
    if r.get("harness_error") or e.startswith(("process failed", "the episode's process")):
        return "harness_failure"
    if e == "returned":
        return "returned"
    if e.startswith("time: the episode is over"):
        return "ended_by_success"
    if e.startswith("time"):
        return "robot_time_up"
    if e.startswith("error"):
        return "program_error"
    if e.startswith("refused"):
        return "refused"
    return "other"


def split_summary(priv):
    ok = [r for r in priv if r.get("success")]
    ends = {k: 0 for k in ("returned", "ended_by_success", "robot_time_up", "program_error", "refused", "harness_failure", "other")}
    for r in priv:
        ends[ending(r)] += 1
    return dict(episodes=len(priv), successes=len(ok), endings=ends,
                errors=ends["program_error"] + ends["refused"] + ends["harness_failure"],          # (as before: every kind of failure to run)
                timeouts=ends["robot_time_up"],                                                     # (NOT the episodes the judge ended on a success)
                robot_s_of_successes=round(sum(r["robot_s"] for r in ok) / len(ok), 1) if ok else None,
                robot_s_of_all=round(sum(float(r.get("robot_s") or 0) for r in priv) / max(len(priv), 1), 1),
                wall_s_mean=round(sum(float(r.get("wall_s") or 0) for r in priv) / max(len(priv), 1), 1),
                segmentation_calls=sum(int(r.get("segment_calls") or 0) for r in priv), per_episode=[bool(r.get("success")) for r in priv])


def final(task_name, rep, cfg, conds=None, out=OUT, splits=None):
    """The final programs on the final test splits (fresh state each episode; nothing goes back to a session).

    Every evaluation is BOUND to what it is of: the program's hash, the evaluator's (target.evaluator_provenance: code,
    assets, package versions), and the scene set's (its frozen manifest, checked before anything runs). Its record is
    final_eval/<condition>/<split>/eval_<key>/ (evaluation.json, program.py, results.json, episodes/), the key being the
    hash of the three. A record is reused only if its three hashes are the ones asked for and it is complete; a change
    of any of them makes ANOTHER record, and nothing is ever written into an existing one. Records without
    evaluation.json (those of 2026-11-27, made before this) are left as they are and never reused; their summary.json is
    not touched (this writes summary_bound.json), and docs/AUDIT_2026-11-27.md holds the audit of those records."""
    from xsim import provenance
    task, R = TASKS[task_name], rep_dir(task_name, rep, out)
    Td = Path(out) / task_name / "test_private"
    T = json.loads((Td / "splits.json").read_text())["splits"]
    ev = target.evaluator_provenance(task)
    res = {}
    for cond in conds or ("A",) + SESSION_CONDS:
        prog = R / cond / "final" / "program.py"
        if not prog.exists():
            continue
        psha = provenance.file_sha(prog)
        res[cond] = dict(program_sha256=psha, evaluator_sha=ev["sha"], evaluator_version=ev["version"], splits={})
        for name, listed in T.items():
            if splits and name not in splits:
                continue
            files, scenes_id = target.checked_panel(task, Td / name)        # (every scene against the split's frozen manifest)
            if [Path(f).name for f in listed] != [f.name for f in files]:
                raise RuntimeError(f"the split {name}: splits.json and the frozen folder do not list the same scenes")
            key = hashlib.sha256(f"{psha}|{ev['sha']}|{scenes_id}".encode()).hexdigest()[:16]
            base = R / "final_eval" / cond / name
            d, n, reused = base / f"eval_{key}", 0, False
            while d.exists():                                               # (an earlier record of the same three hashes)
                e = json.loads((d / "evaluation.json").read_text()) if (d / "evaluation.json").exists() else {}
                if (e.get("complete") and e.get("program_sha256") == psha and e.get("evaluator", {}).get("sha") == ev["sha"] and e.get("scenes", {}).get("id") == scenes_id
                        and (d / "results.json").exists() and provenance.file_sha(d / "program.py") == psha):
                    reused = True
                    break
                n += 1; d = base / f"eval_{key}_again{n}"                   # (incomplete or inconsistent: left as it is; a new record beside it)
            if not reused:
                d.mkdir(parents=True)
                shutil.copy(prog, d / "program.py"); (d / "program.py").chmod(0o444)
                e = dict(task=task_name, replicate=rep, condition=cond, split=name, key=key, program_sha256=psha, program_source=str(prog.relative_to(R)),
                         evaluator=ev, scenes=dict(id=scenes_id, folder=str((Td / name).relative_to(Path(out))), files=[f.name for f in files],
                                                   manifest_sha256=provenance.file_sha(Td / name / "panel_manifest.json")),
                         started=time.strftime("%Y-%m-%d %H:%M:%S"), complete=False)
                (d / "evaluation.json").write_text(json.dumps(e, indent=1))
                _, priv = target.run_batch(d / "program.py", files, task, d / "episodes", None, cfg)
                (d / "results.json").write_text(json.dumps(priv, indent=1, default=str))
                e.update(complete=True, finished=time.strftime("%Y-%m-%d %H:%M:%S"))
                (d / "evaluation.json").write_text(json.dumps(e, indent=1))
            priv = json.loads((d / "results.json").read_text())
            res[cond]["splits"][name] = dict(split_summary(priv), evaluation=d.name, scenes_id=scenes_id, reused=reused,
                                             unbound_record_beside_it=(base / "results.json").exists())
            log(f"final {cond} {name}: {res[cond]['splits'][name]['successes']}/{len(priv)} ({'reused ' if reused else ''}{d.name})")
    sf = R / "final_eval" / "summary_bound.json"                    # (the earlier summary.json, of the unbound records, is not touched)
    allres = json.loads(sf.read_text()) if sf.exists() else {}
    for cond, r in res.items():
        old = allres.get(cond) or {}
        same = old.get("program_sha256") == r["program_sha256"] and old.get("evaluator_sha") == r["evaluator_sha"]
        allres[cond] = dict(r, splits=dict((old.get("splits") or {}) if same else {}, **r["splits"]))
    sf.parent.mkdir(parents=True, exist_ok=True); sf.write_text(json.dumps(allres, indent=1))
    return res


def report(task_name, out=OUT):
    """Every replicate's manifests, development learning curves and final results -> out/xsim/<task>/report.json, report.md."""
    base = Path(out) / task_name
    rows, curves = [], []
    for R in sorted(base.glob("rep*")):
        sb, sl = R / "final_eval" / "summary_bound.json", R / "final_eval" / "summary.json"
        fin = json.loads(sb.read_text()) if sb.exists() else json.loads(sl.read_text()) if sl.exists() else {}   # (bound records where there are any)
        for cond in ("A",) + SESSION_CONDS:
            mf = R / cond / "manifest.json"
            if not mf.exists():
                continue
            m = json.loads(mf.read_text())
            rows.append(dict(replicate=R.name, condition=cond, cost=m["cost"], practice_episodes=m.get("practice_episodes"),
                             target_interactions=m.get("target_interactions"), wall_s=m.get("wall_s"), final=fin.get(cond, {}).get("splits")))
            ep = 0
            for r in m.get("target_rounds") or []:
                ep += r["episodes"]
                curves.append(dict(replicate=R.name, condition=cond, submission=r["round"] + 1, cumulative_target_episodes=ep, cumulative_usd=r["usd"],
                                   development_successes=r["successes"], development_episodes=r["episodes"]))
    (base / "report.json").write_text(json.dumps(dict(rows=rows, development_curves=curves, note="development results (curves) and final "
                                                      "held-out results are separate; cross-simulator transfer, not transfer to a physical robot"), indent=1))
    md = ["# " + task_name + ": cross-simulator transfer (practice: MuJoCo; target: ManiSkill)", "",
          "| replicate | condition | initial program $ | world $ | adaptation $ | total $ | practice episodes | target interactions | final ID |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        c = r["cost"]; f = (r["final"] or {}).get("test_id")
        md.append(f"| {r['replicate']} | {r['condition']} | {c.get('initial_program_usd')} | {c.get('world_construction_usd')} | {c.get('adaptation_usd')} | "
                  f"{c.get('total_usd')} | {r['practice_episodes']} | {r['target_interactions']} | {f and str(f['successes']) + '/' + str(f['episodes'])} |")
    (base / "report.md").write_text("\n".join(md) + "\n")
    return rows, curves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "init", "run", "final", "report"])
    ap.add_argument("--task", required=True); ap.add_argument("--rep", type=int, default=0); ap.add_argument("--cond")
    ap.add_argument("--conds", nargs="*"); ap.add_argument("--config"); ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--step", choices=["A", "world", "snapshot"], help="init: only this step (A: the zero-shot program; world: the shared practice "
                    "world, then the snapshot if A's program exists; snapshot: only the snapshot)")
    a = ap.parse_args()
    cfg = config.load(a.config)
    if a.cmd == "prepare":
        print(prepare(a.task, a.rep, cfg, a.out))
    elif a.cmd == "init":
        print(json.dumps(init(a.task, a.rep, cfg, a.out, step=a.step), indent=1, default=str)[:3000])
    elif a.cmd == "run":
        print(json.dumps(run(a.task, a.rep, a.cond, cfg, a.out), indent=1)[:3000])
    elif a.cmd == "final":
        print(json.dumps(final(a.task, a.rep, cfg, a.conds, a.out), indent=1)[:3000])
    else:
        rows, curves = report(a.task, a.out); print(len(rows), "sessions,", len(curves), "development rounds")


if __name__ == "__main__":
    main()
