"""The skill-improvement loop (docs/learning.md). Outer loop, run by the operator:

  ar learn init --run-id ID [--experiment configs/experiments/m1_clutter_pick.yaml]
      workspaces/ID/ from the experiment's initial skill (+ the brief, API docs, a read-only reference copy of the
      vendored skill library); runs/learning/ID/: budget.json, ledger.jsonl, checkpoints/000_initial, and the initial
      skill's development-suite result (the first incumbent)
  ar learn run --run-id ID [--iterations 1]
      per iteration: one bounded coding-agent session in the workspace -> snapshot the candidate (code + config) ->
      the development suite on the FROZEN snapshot -> keep or reject (SELECTION RULE below) -> checkpoint; every
      attempt is kept (candidates/), accepted or not, with its diff, the session's transcript and costs
  ar learn status --run-id ID
  ar learn evaluate --run-id ID [--suite heldout] [--workers 16]
      OPERATOR ONLY, after learning: the initial skill and the final incumbent on the held-out suite (results under
      runs/learning/ID/heldout/, outside every workspace), paired comparison
  ar learn add-real --run-id ID real/results/<run> [...]
      OPERATOR: real-robot episodes (real/run.py) into the workspace's real/, announced in the next session's prompt
      (learning/real_feedback.py)
  ar learn extend --run-id ID [--practice N] [--agent-usd X]
      OPERATOR: more practice episodes / agent budget for further iterations (e.g. after real-robot feedback)
  ar learn run ... --resume-session | --no-resume-session
      continue the previous session's conversation (its whole context) instead of a fresh one; default: the
      experiment's learning.resume_session (off)
  ar learn run ... --note "..."
      the operator's message, added to this session's prompt (recorded in the ledger)
  ar learn progress --run-id ID [--suite progress] [--workers 16]
      OPERATOR ONLY, after learning: the progress curve. With learning.progress (or run --progress-every N), the
      runner copies the workspace's code each time the run's output tokens (the agent's thinking, text and tool calls,
      over all sessions) pass a multiple of N (runs/learning/ID/progress/); this scores every copy on a protected suite
      the agent never sees (identical code is scored once) -> progress/curve.md, curve.json, curve.svg

SELECTION RULE (dev suite, the same episodes for candidate and incumbent, robot speed and limits unchanged):
  void   any platform error in the candidate's dev run -> rejected (re-run by the operator if the platform is at fault)
  keep   successes(candidate) - successes(incumbent) >= min_gain                                   (primary)
  keep   0 <= that gain < min_gain, and on the episodes both solve the candidate is faster by >= min_dt_s on average,
         with no more model calls                                                                  (secondary)
  real   a session given new real-robot runs (ar learn add-real): kept if it loses at most selection.real.max_loss (1)
         dev successes and is at most selection.real.max_dt_s (1 s) slower per success: the simulated development
         suite cannot score the real-robot fixes, it guards against breaking what works in simulation
  else   rejected: the workspace's code goes back to the incumbent (its notes and practice records stay)"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from .. import paths
from ..evaluation.compare import compare
from ..evaluation.runner import run_suite, snapshot
from . import agent

CODE_DIRS = ("skills", "helpers", "tests")
PHRASES_REAL = {"tape_red": "red tape roll", "tape_yellow": "yellow tape roll"}


def strip_vlm(txt):
    """A doc without its ctx.vlm parts: the table row and the '## The model call' section."""
    lines = [l for l in txt.splitlines(keepends=True) if not l.startswith("| `ctx.vlm` |")]
    out, skip = [], False
    for l in lines:
        if l.startswith("## The model call"):
            skip = True; continue
        if skip and l.startswith("## "):
            skip = False
        if not skip:
            out.append(l)
    return "".join(out)


def scratch_doc(name, txt):
    """A doc for runs without the skill library: the library's helpers named by their ctx.tools equivalents; the robot
    doc gets the motion phase names (they set the timing: real_sim FREE_PHASES / SETTLE_PHASES)."""
    for a, b in (("`T.FINGERTIP`", "`ctx.tools.FINGERTIP`"), ("`B.down(yaw)`", "`ctx.tools.down(yaw)`"),
                 ("(`B.OPEN_W`)", "(`ctx.tools.OPEN_W`)"), ("`F.world(obs)` (`ctx.library.F`)", "`ctx.tools.world(obs)`")):
        txt = txt.replace(a, b)
    if name == "robot.md":
        from ..sim import twin  # noqa: F401  (puts the vendored code on the path)
        import real_sim as RS
        free = sorted(str(x) for x in RS.FREE_PHASES if x)
        txt += ("\n## Phase names (`phase=` of `move` / `move_joint`)\n\nThe phase decides how a move is timed:\n"
                f"- **free moves** (the faster timing): {', '.join(repr(x) for x in free)}, and any phase starting with "
                f"{', '.join(repr(x) for x in RS.FREE_PREFIXES)};\n"
                f"- **settled at the end** (the arm waits until it is still): {', '.join(repr(x) for x in sorted(RS.SETTLE_PHASES))};\n"
                "- any other name: an approach / contact move, run at its requested duration `T`.\n"
                "Name a descent towards an object `\"descend\"` (settled), a grasp closing `\"close the gripper\"`, a lift "
                "`\"lift\"`.\n")
    return txt


SUBSTITUTE_SCENE = """**What the program is told:** "Pick up the {red|yellow} can and lift it." (this run's practice objects)

**The scene:**
- the target can;
- the other-coloured can;
- 1–3 more objects from: a white can, a black box, a black hoop, a red and grey bar, a red and white bottle, a purple
  disc.

"""


def substitute_doc(name, txt, ph):
    """Condition C's docs: its own objects' names (the evaluation's objects are not named to it)."""
    if name == "task_clutter_pick.md":
        a, b = txt.index("**What the program is told:**"), txt.index("The objects are settled")
        txt = txt[:a] + SUBSTITUTE_SCENE + txt[b:]
    for k in ("tape_red", "tape_yellow"):
        txt = txt.replace(PHRASES_REAL[k], ph[k])
    return txt.replace("the other roll", "the other can")


SIMPLE_DEPTH = """**Depth in this simulator: ideal.** Programs get exact depth (only the D415's 0.275 m minimum range), and
`observe_frame` returns the camera's true pose (`R`, `eye`): 3D points computed from a frame are exact.
`F.world(obs)` (`ctx.library.F`) gives per-pixel 3D points in the world frame, plus a validity mask.

**Colour:** plain MuJoCo rendering.

"""
SIMPLE_EDITS = {                                               # doc -> [(the twin's text, simple_sim's)]
    "robot.md": [("- **World frame:** the twin's frame, metres and radians. The table top is z = 0, and its centre is the "
                  "origin. The robot", "- **World frame:** metres and radians. x = y = 0 is the table's centre; the table "
                  "top's height is not given. The robot"),
                 ("`R` and `eye` are the camera pose the program BELIEVES, its nominal mount: the true pose differs by the "
                  "episode's calibration error.", "`R` and `eye` are the camera's pose (exact in this simulator).")],
    "perception.md": [('a name ("red tape roll")', 'a name ("blue cube")'),
                      ("  - return **another object**: a similar colour, the other roll, a red part of a different object "
                       "such as a red\n    handle or a red bottle base.", "  - return **another object**: one of a similar "
                       "colour or shape.")],
    "skill_api.md": [("red tape roll", "red cylinder"), ("  holds (noisy depth: use a small neighbourhood).", "  holds.")],
}


def simple_doc(name, txt):
    """simple_sim's docs (docs/simple_sim.md): its sensors (ideal) and objects; nothing about the table's height and
    nothing about the objects the evaluation uses (the brief says the sim is approximate: other table heights, objects
    and visuals may be tested)."""
    if name == "perception.md":
        a, b = txt.index("**Depth: realistic and noisy, in every condition.**"), txt.index("## Segmentation")
        txt = txt[:a] + SIMPLE_DEPTH + txt[b:]
    for old, new in SIMPLE_EDITS.get(name, []):
        if old not in txt:
            raise ValueError(f"simple_doc: {name} no longer has {old[:60]!r} (docs changed: update SIMPLE_EDITS)")
        txt = txt.replace(old, new)
    for a, b in (("In the twin", "In the simulator"), ("in the twin", "in the simulator"), ("the twin's", "the simulator's"),
                 ("the twin runs", "the simulator runs")):        # (not "the twin": nothing says it is accurate)
        txt = txt.replace(a, b)
    return txt


def run_dir(run_id):
    return paths.RUNS / "learning" / run_id


def ledger(run_id, **rec):
    with open(run_dir(run_id) / "ledger.jsonl", "a") as f:
        f.write(json.dumps(dict(rec, t=time.time()), default=str) + "\n")


def read_ledger(run_id):
    f = run_dir(run_id) / "ledger.jsonl"
    return [json.loads(x) for x in open(f)] if f.exists() else []


def state(run_id, **upd):
    f = run_dir(run_id) / "state.json"
    s = json.loads(f.read_text()) if f.exists() else {}
    if upd:
        s.update(upd); f.write_text(json.dumps(s, indent=1))
    return s


def experiment(path):
    X = paths.load_yaml(path)
    X["_path"] = str(path)
    return X


# ---------------------------------------------------------------------------------------------------- init
def filter_suite(spec, fams):
    if "families" not in spec or not fams:
        return spec
    return dict(spec, families={f: v for f, v in spec["families"].items() if f in fams})


def init(run_id, exp_path, add_eval_set=False, tasks=None, initial=None):
    """add_eval_set (ablation, `--add-evaluation-set`): the agent may run the evaluation set itself (learning.
    exposed_eval_suite) during its sessions and see its per-episode results."""
    X = experiment(exp_path)
    if X.get("real2sim"):                                      # (no simulator at the start: learning/real2sim.py)
        from . import real2sim
        return real2sim.init(run_id, X)
    L = X["learning"]
    ws, rd = paths.WORKSPACES / run_id, run_dir(run_id)
    if ws.exists() or rd.exists():
        raise SystemExit(f"{run_id}: exists already")
    if X.get("tasks"):                                         # (a multi-task experiment: --tasks picks a subset)
        from .. import tasks as TK
        fams = TK.parse(tasks) if tasks else list(X["tasks"])
        bad = [f for f in fams if f not in X["tasks"]]
        if bad:
            raise SystemExit(f"tasks {bad} are not in the experiment ({X['tasks']})")
        X["tasks"] = fams
        test_only = TK.parse(",".join(X.get("test_tasks") or []))   # (held-out-only families: in no practice / dev)
        X["suites"] = {k: filter_suite(v, fams + test_only) for k, v in X["suites"].items()}
    elif tasks:
        raise SystemExit("--tasks needs a multi-task experiment (configs/experiments/multitask_*.yaml)")
    if initial:
        X["skill"] = X["initial"][initial]; X["initial_choice"] = initial
    src = paths.ROOT / X["skill"]
    rd.mkdir(parents=True)
    ws.mkdir(parents=True)
    for d in CODE_DIRS:
        if (src / d).exists():
            shutil.copytree(src / d, ws / d, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            (ws / d).mkdir()                                   # (the brief names all three)
    (ws / "practice").mkdir()
    if L.get("allow_custom_scenes"):
        (ws / "practice" / "scenes").mkdir()
    (ws / "notes.md").write_text("# Notes\n\n(What you tried, what you saw in practice, what you changed and why.)\n")
    (ws / "docs").mkdir()
    lib = L.get("library", True)                               # (False: skills from scratch, no skill library)
    from ..config import task_config
    from ..sim.scene import OBJECT_SETS
    t_ = task_config(X["task"])
    clutter = not X.get("tasks") or "clutter-pick" in X["tasks"]
    for f in ("skill_api.md", "robot.md", "perception.md") + (("task_clutter_pick.md",) if clutter else ()) \
            + (("scenes.md",) if L.get("allow_custom_scenes") else ()) + (("tasks.md",) if X.get("tasks") else ()):
        src_f = "skill_api_scratch.md" if (f == "skill_api.md" and not lib) else f
        if f == "tasks.md" and L.get("tasks_doc"):            # (the experiment's own task doc: only its tasks)
            src_f = L["tasks_doc"]
        if (paths.ROOT / "docs" / src_f).exists():
            txt = (paths.ROOT / "docs" / src_f).read_text()
            if t_["object_set"] == "simple":                 # (first: the texts it edits are the docs' own)
                txt = simple_doc(f, txt)
            if not (X.get("vlm") or {}).get("enabled"):
                txt = strip_vlm(txt)                          # (runs without model calls: no word of ctx.vlm)
            if not lib:
                txt = scratch_doc(f, txt)
            if t_["object_set"] == "substitute":
                txt = substitute_doc(f, txt, OBJECT_SETS["substitute"]["phrases"])
            (ws / "docs" / f).write_text(txt)
    if lib:                                                    # (from scratch: the robot code is not shown either)
        ref = ws / "reference"
        shutil.copytree(paths.VENDOR, ref, ignore=shutil.ignore_patterns("__pycache__", "*.obj", "*.stl", "*.json", "__init__.py"))
        (ref / "README.md").write_text(REFERENCE_README)
        for p in ref.rglob("*"):
            p.chmod(0o555 if p.is_dir() else 0o444)
    P = X["practice"]
    fam_ranges = None
    if X.get("tasks"):
        fam_ranges = {f: dict(P["families"][f]) for f in X["tasks"]}
    budget = dict(task=X["task"], entry=X.get("entry", "skills/pick.py"), practice_workers=L.get("practice_workers", 8),
                  tasks=X.get("tasks"), library=bool(lib),
                  practice=dict(split=P["split"], levels=P.get("levels", [0]), idx_range=P.get("idx_range", [0, 300]),
                                seed_range=P["seed_range"], families=fam_ranges,
                                episodes_per_session=L["practice_episodes_per_session"],
                                episodes_total=L["practice_episodes_total"], max_per_call=L.get("practice_max_per_call", 12)),
                  agent=dict(model=L.get("model"), effort=L.get("effort"), usd_per_session=L["agent_usd_per_session"],
                             usd_total=L["agent_usd_total"], wall_s_per_session=L["agent_wall_s_per_session"],
                             resume=bool(L.get("resume_session", False))),
                  selection=L["selection"], dev_suite=L["dev_suite"], prompt_note=L.get("prompt_note"),
                  progress=L.get("progress"),
                  vlm=X.get("vlm") if (X.get("vlm") or {}).get("enabled") else None,
                  scenes=dict(allowed=True, checks_per_session=L.get("scene_checks_per_session", 40))
                  if L.get("allow_custom_scenes") else None,
                  eval_access=dict(suite=L.get("exposed_eval_suite", "heldout_target_200"),
                                   spec=X["suites"][L.get("exposed_eval_suite", "heldout_target_200")],
                                   runs_per_session=L.get("eval_runs_per_session", 3), workers=L.get("dev_workers", 16))
                  if add_eval_set else None)
    (rd / "budget.json").write_text(json.dumps(budget, indent=1))
    (rd / "experiment.json").write_text(json.dumps(X, indent=1, default=str))
    (ws / ".ar_run.json").write_text(json.dumps(dict(run_id=run_id, session=None)))
    (ws / "AGENTS.md").write_text(brief(budget, X))
    (ws / "CLAUDE.md").write_text("See AGENTS.md (the brief for this workspace).\n")
    ck = rd / "checkpoints" / "000_initial"
    snapshot(ws, ck)
    ledger(run_id, kind="init", experiment=X["_path"], skill=X["skill"], add_eval_set=bool(add_eval_set))
    print(f"workspace {ws}\nrun {rd}\nthe initial skill on the dev suite ...")
    S = dev_eval(run_id, "000_initial", ck, X)
    state(run_id, incumbent="000_initial", iteration=0, incumbent_dev=S)
    print(json.dumps(S, indent=1)[:800])


def dev_eval(run_id, name, skill_dir, X):
    from ..config import task_config
    L = X["learning"]
    spec = X["suites"][L["dev_suite"]]
    out = run_dir(run_id) / "dev" / name
    t0 = time.time()
    V = X.get("vlm") or {}
    if V.get("enabled"):                                       # (dev_usd: development's own model budget, apart from
        V = dict(V, tag=f"{run_id}-dev:dev" if "dev_usd" in V else f"{run_id}:dev",   # practice's run_usd)
                 run_usd=V.get("dev_usd", V.get("run_usd")), cache_dir=str(run_dir(run_id) / "vlm_cache"))
    else:
        V = None
    S = run_suite(skill_dir, spec, task_config(X["task"]), out, workers=L.get("dev_workers", 16), sandbox=True,
                  entry=X.get("entry", "skills/pick.py"), log=None, vlm=V, library=L.get("library", True))
    ledger(run_id, kind="dev", name=name, episodes=S["n"], successes=S["successes"], robot_s=S["robot_time_total_s"],
           wall_s=round(time.time() - t0, 1), model_cost_usd=S["model_cost_usd"])
    return dict(successes=S["successes"], n=S["n"], t_success_mean=S["t_success_mean"], by_level=S["by_level"],
                by_family=S.get("by_family"),
                categories=S["categories"], platform_errors=S["platform_errors"], model_calls=S["model_calls"])


# ---------------------------------------------------------------------------------------------------- one iteration
def last_session_id(L):
    """The id of the last session if it was saved (resumable), else None."""
    for r in reversed(L):
        if r.get("kind") == "session":
            return r.get("session_id") if r.get("persisted") else None
    return None


def session_costs(L):
    """[(session record, its own agent cost in API-equivalent USD)]. Claude Code's total_cost_usd is cumulative over a
    resumed conversation (agent.py): records written since 2026-11-20 hold the session's own cost_usd and the CLI's
    figure as cost_usd_total; older ones hold the CLI's figure in cost_usd, so a resumed one's own cost is that minus
    the total after the previous invocation of the same saved session."""
    out, total = [], {}
    for r in L:
        if r.get("kind") != "session":
            continue
        if "cost_usd_total" in r:
            own, tot = float(r.get("cost_usd") or 0.0), r.get("cost_usd_total")
        else:
            tot = r.get("cost_usd")
            prev = total.get(r.get("resumed_from")) if r.get("resumed_from") else None
            own = float(tot or 0.0) - prev if prev is not None and (tot or 0.0) >= prev else float(tot or 0.0)
        if r.get("session_id") and tot is not None:
            total[r["session_id"]] = float(tot)
        out.append((r, round(own, 6)))
    return out


def agent_spent(L):
    """The run's agent cost so far (API-equivalent USD): the sessions' own costs (session_costs)."""
    return sum(c for _, c in session_costs(L))


def cli_cost_total(L, session_id):
    """The CLI's cumulative total_cost_usd after the last invocation of this saved session that reported one (0.0)."""
    for r in reversed(L):
        if r.get("kind") == "session" and r.get("session_id") == session_id:
            tot = r["cost_usd_total"] if "cost_usd_total" in r else r.get("cost_usd")
            if tot is not None:
                return float(tot)
    return 0.0


def run_output_tokens(L):
    """The run's agent output tokens so far (thinking, text and tool calls; every session)."""
    return sum(int((r.get("progress") or r.get("usage") or {}).get("output_tokens") or 0) for r in L if r.get("kind") == "session")


def progress_snapshotter(run_id, ws, sid):
    """The workspace's code at a token count -> runs/learning/ID/progress/tNNNNNNN_<session>/ (+ .json beside it)."""
    d0 = run_dir(run_id) / "progress"

    def snap(at, info):
        d0.mkdir(exist_ok=True)
        d = d0 / f"t{at:08d}_{sid}"
        tmp = d0 / f".tmp_{d.name}"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        for c in CODE_DIRS:
            if (ws / c).exists():
                shutil.copytree(ws / c, tmp / c, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if d.exists():
            shutil.rmtree(d)
        tmp.rename(d)
        L = read_ledger(run_id)
        prac = sum(r.get("episodes", 0) for r in L if r.get("kind") == "practice")
        (d0 / f"{d.name}.json").write_text(json.dumps(dict(at_output_tokens=at, session=sid, practice_episodes=prac,
                                                           t=time.time(), **info), indent=1))
    return snap


def iterate(run_id, resume=None, note=None, progress_every=None):
    """resume: None = the run's setting (budget agent.resume), True / False = this iteration. note: the operator's
    message for this session's prompt. progress_every: snapshot the code every N output tokens (default: the
    experiment's learning.progress.every_output_tokens; none if neither)."""
    from .real_feedback import prompt_note
    rd, ws = run_dir(run_id), paths.WORKSPACES / run_id
    X = json.loads((rd / "experiment.json").read_text())
    B = json.loads((rd / "budget.json").read_text())
    st = state(run_id)
    it = st["iteration"] + 1
    spent = agent_spent(read_ledger(run_id))
    A = B["agent"]
    left = A["usd_total"] - spent
    if left < 0.5:
        raise SystemExit(f"agent budget spent: ${spent:.2f} of ${A['usd_total']:.2f}")
    sid = f"s{it:02d}"
    meta = json.loads((ws / ".ar_run.json").read_text()); meta["session"] = sid
    (ws / ".ar_run.json").write_text(json.dumps(meta))
    resume_on = bool(A.get("resume")) if resume is None else bool(resume)
    prev_sid = last_session_id(read_ledger(run_id)) if resume_on else None
    real_new = list(st.get("real_pending") or [])
    if B.get("real2sim"):
        from . import real2sim
        prompt = real2sim.session_prompt(it, st, B, read_ledger(run_id), resumed=bool(prev_sid))
    else:
        prompt = session_prompt(it, st, B, read_ledger(run_id), real_note=prompt_note(real_new), resumed=bool(prev_sid))
    if note:
        prompt += f" A message from the operator: {note.strip()}"
    every = progress_every or (B.get("progress") or {}).get("every_output_tokens")
    prog = None
    if every:
        off = run_output_tokens(read_ledger(run_id))
        prog = dict(every=int(every), offset=off, snapshot=progress_snapshotter(run_id, ws, sid))
    print(f"[{run_id}] iteration {it}: session {sid} (cap ${min(A['usd_per_session'], left):.2f}, {A['wall_s_per_session']} s"
          f"{', resuming ' + prev_sid if prev_sid else (', saved for resuming' if resume_on else '')}"
          f"{f', {len(real_new)} new real runs' if real_new else ''})")
    res = agent.run_session(ws, prompt, (ws / "AGENTS.md").read_text(), rd / "sessions" / sid, model=A.get("model"),
                            max_budget_usd=min(A["usd_per_session"], left), wall_limit_s=A["wall_s_per_session"],
                            effort=A.get("effort"),
                            env_extra=dict(AR_WORKSPACE=str(ws.resolve()), AR_RUN=run_id), persist=resume_on, resume=prev_sid,
                            progress=prog, cost_before=cli_cost_total(read_ledger(run_id), prev_sid) if prev_sid else 0.0)
    ledger(run_id, kind="session", session=sid, cost_usd=res.get("cost_usd"), cost_usd_total=res.get("cost_usd_total"),
           usage=res.get("usage"), turns=res.get("turns"),
           wall_s=res.get("wall_s"), subtype=res.get("subtype"), denials=len(res.get("denials") or []),
           tool_calls=res.get("tool_calls"), model=(res.get("init") or {}).get("model"),
           session_id=(res.get("init") or {}).get("session_id"), persisted=res.get("persisted"), resumed_from=prev_sid,
           real_announced=[r["name"] for r in real_new], operator_note=note, progress=res.get("progress"))
    if real_new:
        state(run_id, real_pending=[], real_announced=(st.get("real_announced") or []) + [r["name"] for r in real_new])
    if prog:                                                  # (the session's last code, at its last token count)
        try:
            prog["snapshot"](prog["offset"] + int((res.get("progress") or {}).get("output_tokens") or 0),
                             dict(end_of_session=True, **{k: v for k, v in (res.get("progress") or {}).items() if k != "next"}))
        except Exception as ex:
            print(f"  progress snapshot at the session's end failed: {ex}")
    so_far = f" (the conversation so far ${res['cost_usd_total']})" if res.get("cost_before_usd") else ""
    print(f"  session done: ${res.get('cost_usd')}{so_far}, {res.get('turns')} turns, {res.get('wall_s')} s, "
          f"{len(res.get('denials') or [])} denied tool calls, exit {res.get('exit')}")
    cand = rd / "candidates" / sid
    snapshot(ws, cand)
    shutil.copy2(ws / "notes.md", rd / "sessions" / sid / "notes.md")
    if B.get("real2sim"):                                     # (its simulator at the session's end, kept with the session)
        real2sim.archive_sim(run_id, sid)
    inc_dir = rd / "checkpoints" / st["incumbent"]
    diff, full = "", ""
    for d in CODE_DIRS:                                       # (the code only: not the brief, notes, run metadata)
        a_, b_ = inc_dir / d, cand / d
        if not a_.exists() and not b_.exists():
            continue
        a_ = a_ if a_.exists() else Path("/dev/null"); b_ = b_ if b_.exists() else Path("/dev/null")
        diff += subprocess.run(["git", "diff", "--no-index", "--stat", "--", str(a_), str(b_)], capture_output=True, text=True).stdout
        full += subprocess.run(["git", "diff", "--no-index", "--", str(a_), str(b_)], capture_output=True, text=True).stdout
    (rd / "candidates" / f"{sid}.diff").write_text(full)
    changed = bool(full.strip())
    print("  changes:\n" + (diff or "  (none)"))
    viol = []
    if changed and B.get("library", True) is False:
        from ..execution.guard import violations
        viol = violations(cand)
    if not changed:
        decision = dict(keep=False, why="no code change")
        S = None
    elif viol:
        decision = dict(keep=False, why="uses the robot's own code (skills from scratch): " + "; ".join(viol[:5]))
        S = None
    elif B.get("real2sim"):                                   # (both versions in its simulator as it is now)
        S, S_inc, inc_dev, cand_dev = real2sim.dev_eval(run_id, sid, cand, st, X)
        decision = select(inc_dev, cand_dev, S, B["selection"])
        st = dict(st, incumbent_dev=S_inc)
    else:
        S = dev_eval(run_id, sid, cand, X)
        decision = select(rd / "dev" / st["incumbent"], rd / "dev" / sid, S, B["selection"], real=bool(real_new))
    ledger(run_id, kind="decision", session=sid, candidate=sid, incumbent=st["incumbent"], **decision)
    I = st.get("incumbent_dev") or {}
    print(f"  dev: {None if S is None else (S['successes'], S['n'], S['t_success_mean'])} vs incumbent "
          f"{I.get('successes')}/{I.get('n')} ({I.get('t_success_mean')} s) -> "
          f"{'KEEP' if decision['keep'] else 'reject'}: {decision['why']}")
    if decision["keep"]:
        shutil.copytree(cand, rd / "checkpoints" / sid)
        state(run_id, incumbent=sid, iteration=it, incumbent_dev=S, last=dict(session=sid, decision=decision, dev=S))
    elif B.get("real2sim"):
        state(run_id, incumbent_dev=st.get("incumbent_dev"))
        for d in CODE_DIRS:                                   # (back to the incumbent's code; notes, sim/, data stay)
            if (ws / d).exists():
                shutil.rmtree(ws / d)
            if (inc_dir / d).exists():
                shutil.copytree(inc_dir / d, ws / d)
                (ws / d).chmod(0o755)
                for p in (ws / d).rglob("*"):
                    p.chmod(0o755 if p.is_dir() else 0o644)
        state(run_id, iteration=it, last=dict(session=sid, decision=decision, dev=S))
    else:
        for d in CODE_DIRS:                                   # (back to the incumbent's code; notes, practice stay)
            if (ws / d).exists():
                shutil.rmtree(ws / d)
            if (inc_dir / d).exists():
                shutil.copytree(inc_dir / d, ws / d)
                (ws / d).chmod(0o755)
                for p in (ws / d).rglob("*"):
                    p.chmod(0o755 if p.is_dir() else 0o644)
        state(run_id, iteration=it, last=dict(session=sid, decision=decision, dev=S))
    return decision


def select(inc_dev_dir, cand_dev_dir, S, rule, real=False):
    """real: the session had new real-robot runs: kept unless the simulated dev suite gets worse (selection.real)."""
    if S["platform_errors"]:
        return dict(keep=False, why=f"void: {S['platform_errors']} platform errors in the candidate's dev run")
    c = compare(inc_dev_dir, cand_dev_dir)
    gain = c["b_successes"] - c["a_successes"]
    info = dict(gain=gain, only_incumbent=c["only_a"], only_candidate=c["only_b"], mcnemar_p=c["mcnemar_p"],
                dt_mean=c["dt_mean"], dt_ci95=c["dt_ci95"])
    fam = per_family(inc_dev_dir, cand_dev_dir)
    if len(fam) > 1:
        info["by_task"] = fam
        worst = min(fam.values(), key=lambda d: d["gain"])
        if worst["gain"] < -rule.get("max_task_loss", 1):
            bad = [f for f, d in fam.items() if d["gain"] < -rule.get("max_task_loss", 1)]
            return dict(info, keep=False, why=f"{', '.join(bad)} lost {-worst['gain']} dev successes (> {rule.get('max_task_loss', 1)} per task)")
    if gain >= rule["min_gain"]:
        return dict(info, keep=True, why=f"+{gain} dev successes (>= {rule['min_gain']})")
    if real:
        R = dict(REAL_RULE, **(rule.get("real") or {}))
        if gain >= -R["max_loss"] and (c["dt_mean"] is None or c["dt_mean"] <= R["max_dt_s"]):
            return dict(info, keep=True, why=f"real-robot feedback: successes {gain:+d} (>= -{R['max_loss']}), time "
                                             f"{c['dt_mean']} s (<= +{R['max_dt_s']})")
        return dict(info, keep=False, why=f"real-robot feedback, but the simulated dev suite got worse: successes {gain:+d} "
                                          f"(needs >= -{R['max_loss']}), time {c['dt_mean']} s (needs <= +{R['max_dt_s']})")
    if 0 <= gain and c["dt_mean"] is not None and c["dt_mean"] <= -rule["min_dt_s"]:
        return dict(info, keep=True, why=f"successes {gain:+d}, {c['dt_mean']:+.2f} s on the {c['both']} both solve")
    return dict(info, keep=False, why=f"successes {gain:+d} (< {rule['min_gain']}), time {c['dt_mean']} s (needs <= -{rule['min_dt_s']})")


REAL_RULE = dict(max_loss=1, max_dt_s=1.0)


def per_family(a_dir, b_dir):
    from ..evaluation.compare import load
    A, B_ = load(a_dir), load(b_dir)
    out = {}
    for k in set(A) & set(B_):
        f = A[k].get("family", "clutter-pick")
        d = out.setdefault(f, dict(incumbent=0, candidate=0, n=0))
        d["incumbent"] += int(A[k]["success"]); d["candidate"] += int(B_[k]["success"]); d["n"] += 1
    for d in out.values():
        d["gain"] = d["candidate"] - d["incumbent"]
    return out


# ---------------------------------------------------------------------------------------------------- held-out evaluation
def evaluate(run_id, suite="heldout", workers=16, baseline=None):
    """(the suite's own task config, if it names one, decides the condition: run_suite). baseline: an existing run of
    the SAME initial skill on the SAME suite (checked by the skill hash), reused instead of re-running it."""
    from ..config import task_config
    from ..execution.versions import tree_hash
    rd = run_dir(run_id)
    X = json.loads((rd / "experiment.json").read_text())
    st = state(run_id)
    spec = X["suites"][suite]
    out = {}
    names = ["000_initial", st["incumbent"]]
    if baseline:
        b = json.loads((Path(baseline) / "suite.json").read_text())
        if b["skills"] != tree_hash(rd / "checkpoints" / "000_initial") or b["spec"] != spec:
            raise SystemExit(f"{baseline}: not the same initial skill / suite")
        d = rd / "heldout" / f"{suite}_000_initial"
        d.parent.mkdir(parents=True, exist_ok=True)
        if not d.exists():
            d.symlink_to(Path(baseline).resolve())
        names = names[1:]
    for name in names:
        d = rd / "heldout" / f"{suite}_{name}"
        print(f"[{run_id}] {suite}: {name}")
        V = X.get("vlm") or {}
        V = dict(V, tag=f"{run_id}-heldout:{suite}", run_usd=V.get("heldout_usd", 0.0)) if V.get("enabled") else None
        out[name] = run_suite(rd / "checkpoints" / name, spec, task_config(X["task"]), d, workers=workers, sandbox=True,
                              entry=X.get("entry", "skills/pick.py"), log=print, vlm=V,
                              library=(X.get("learning") or {}).get("library", True))
    if st["incumbent"] != "000_initial":
        from ..evaluation.compare import compare_md
        c = compare(rd / "heldout" / f"{suite}_000_initial", rd / "heldout" / f"{suite}_{st['incumbent']}")
        md = compare_md(c, "initial", st["incumbent"])
        (rd / "heldout" / f"{suite}_comparison.md").write_text(md)
        print(md)
    return out


def progress_eval(run_id, suite="progress", workers=16):
    """The progress curve: the initial skill (0 tokens) and every progress snapshot (runs/learning/ID/progress/) on a
    protected suite (the experiment's `suite`, else heldout). Identical code (tree hash) is scored once. Model calls:
    tag ID-progress, budget vlm.progress_usd. -> progress/curve.json, curve.md, curve.svg"""
    from ..config import task_config
    from ..execution.versions import tree_hash
    rd = run_dir(run_id)
    X = json.loads((rd / "experiment.json").read_text())
    if suite not in X["suites"]:
        suite = "heldout"
    spec = X["suites"][suite]
    pd = rd / "progress"
    snaps = [("000_initial", rd / "checkpoints" / "000_initial", dict(at_output_tokens=0, session=None, practice_episodes=0))]
    for d in sorted(x for x in pd.iterdir() if x.is_dir() and x.name.startswith("t")) if pd.exists() else []:
        meta = json.loads((pd / f"{d.name}.json").read_text()) if (pd / f"{d.name}.json").exists() else {}
        snaps.append((d.name, d, meta))
    snaps.sort(key=lambda x: (x[2].get("at_output_tokens", 0), x[0]))
    V = X.get("vlm") or {}
    V = dict(V, tag=f"{run_id}-progress:{suite}", run_usd=V.get("progress_usd", 0.0),
             cache_dir=str(rd / "vlm_cache_progress")) if V.get("enabled") else None
    lib = (X.get("learning") or {}).get("library", True)
    out_root = pd / "eval" / suite
    done, rows = {}, []
    for name, d, meta in snaps:
        h = tree_hash(d)
        if h not in done:
            print(f"[{run_id}] progress {suite}: {name} ({meta.get('at_output_tokens')} output tokens)", flush=True)
            S = run_suite(d, spec, task_config(X["task"]), out_root / h, workers=workers, sandbox=True,
                          entry=X.get("entry", "skills/pick.py"), log=None, vlm=V, library=lib)
            done[h] = dict(successes=S["successes"], n=S["n"], t_success_mean=S["t_success_mean"],
                           by_family={k: dict(successes=v["successes"], n=v["n"]) for k, v in (S.get("by_family") or {}).items()},
                           platform_errors=S["platform_errors"], model_calls=S["model_calls"], model_cost_usd=S["model_cost_usd"])
        rows.append(dict(name=name, hash=h, **{k: meta.get(k) for k in ("at_output_tokens", "session", "practice_episodes",
                                                                        "session_thinking_tokens", "wall_s", "end_of_session")},
                         **done[h]))
    (pd / "curve.json").write_text(json.dumps(dict(run=run_id, suite=suite, spec=spec, rows=rows), indent=1))
    fams = sorted({f for r in rows for f in (r.get("by_family") or {})})
    md = [f"# {run_id}: progress on `{suite}` ({len(rows)} snapshots, {len(done)} distinct versions)", "",
          "| output tokens | session | practice episodes | successes | " + " | ".join(fams) + (" | " if fams else "") +
          "s per success | model calls | |", "|" + "---|" * (7 + len(fams))]
    for r in rows:
        bf = r.get("by_family") or {}
        md.append(f"| {r['at_output_tokens']:,} | {r['session'] or '-'} | {r['practice_episodes']} | {r['successes']}/{r['n']} | "
                  + "".join(f"{bf[f]['successes']}/{bf[f]['n']} | " if f in bf else "- | " for f in fams)
                  + f"{r['t_success_mean']} | {r['model_calls']} | {'end of session' if r.get('end_of_session') else ''} |")
    (pd / "curve.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    (pd / "curve.svg").write_text(curve_svg(rows, fams, f"{run_id}: {suite} success % vs agent output tokens"))
    return rows


def curve_svg(rows, fams, title, W=720, H=360):
    """Success % against output tokens (all, and per task family); grey lines = session ends."""
    ml, mr, mt, mb = 56, 120, 34, 48
    xmax = max([r["at_output_tokens"] or 0 for r in rows] + [1])
    X_ = lambda v: ml + (W - ml - mr) * v / xmax
    Y_ = lambda pct: mt + (H - mt - mb) * (1 - pct / 100)
    cols = ["#1f6feb", "#d97706", "#059669", "#9333ea", "#dc2626"]
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="12">',
         f'<rect width="{W}" height="{H}" fill="white"/>', f'<text x="{ml}" y="20" font-size="14">{title}</text>']
    for pct in range(0, 101, 20):
        o.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y_(pct):.1f}" y2="{Y_(pct):.1f}" stroke="#eee"/>'
                 f'<text x="{ml - 8}" y="{Y_(pct) + 4:.1f}" text-anchor="end">{pct}</text>')
    for k in range(6):
        v = xmax * k / 5
        o.append(f'<text x="{X_(v):.1f}" y="{H - mb + 18}" text-anchor="middle">{v / 1000:.0f}k</text>')
    o.append(f'<text x="{(ml + W - mr) / 2}" y="{H - 10}" text-anchor="middle">agent output tokens (thinking, text, tool calls)</text>')
    for r in rows:
        if r.get("end_of_session"):
            o.append(f'<line x1="{X_(r["at_output_tokens"]):.1f}" x2="{X_(r["at_output_tokens"]):.1f}" y1="{mt}" y2="{H - mb}" stroke="#bbb" stroke-dasharray="4 3"/>')
    series = [("all", [(r["at_output_tokens"], 100 * r["successes"] / max(1, r["n"])) for r in rows])]
    for f in fams:
        series.append((f, [(r["at_output_tokens"], 100 * r["by_family"][f]["successes"] / max(1, r["by_family"][f]["n"]))
                           for r in rows if f in (r.get("by_family") or {})]))
    for i, (name, pts) in enumerate(series):
        c = cols[i % len(cols)]
        o.append(f'<polyline fill="none" stroke="{c}" stroke-width="{2.5 if i == 0 else 1.5}" points="' +
                 " ".join(f"{X_(x):.1f},{Y_(y):.1f}" for x, y in pts) + '"/>')
        o += [f'<circle cx="{X_(x):.1f}" cy="{Y_(y):.1f}" r="3" fill="{c}"/>' for x, y in pts]
        o.append(f'<text x="{W - mr + 10}" y="{mt + 16 * i + 4}" fill="{c}">{name}</text>')
    return "\n".join(o + ["</svg>"]) + "\n"


def extend(run_id, practice=0, agent_usd=0.0):
    """More practice episodes and agent budget for further iterations (recorded in the ledger)."""
    f = run_dir(run_id) / "budget.json"
    B = json.loads(f.read_text())
    B["practice"]["episodes_total"] += int(practice)
    B["agent"]["usd_total"] = round(B["agent"]["usd_total"] + float(agent_usd), 2)
    f.write_text(json.dumps(B, indent=1))
    ledger(run_id, kind="budget", practice_added=int(practice), agent_usd_added=float(agent_usd),
           episodes_total=B["practice"]["episodes_total"], agent_usd_total=B["agent"]["usd_total"])
    print(f"{run_id}: practice episodes total {B['practice']['episodes_total']}, agent budget ${B['agent']['usd_total']}")


# ---------------------------------------------------------------------------------------------------- texts
REFERENCE_README = """# reference/ (read-only)

A copy of the vendored robot code the platform runs: `panda_twin/code` (the twin, the robot path `real/real_sim.py`
RealSim, the pick skill library `clutter_bench.py`, `task_box.py`, `task_fast.py`, `real/real_box.py`) and
`panda_twin_tracking` (`track_run.py`, `astra_skills_real.py`: SkillProgram, SkillEpisode.pick_direct). Read it to
understand what `ctx.library` and `ctx.robot` do. Editing these files changes nothing (the platform imports its own
copy): to change a behaviour, subclass or wrap it in `helpers/` or `skills/`. Only what the program can reach through
`ctx` exists at run time: the true scene state is not available to programs.
"""


PERCEPTION_NOTE = {
    "sam3": ("ctx.segmenter is SAM3, a real segmentation model (the real robot's): free-text prompts, scored masks; it "
             "can miss the object, return part of it or another object; phrases matter, and one view may need other "
             "words than another (docs/perception.md, 'Using it well')."),
    "oracle": "ctx.segmenter is an oracle stand-in (exact masks for the objects' exact names; docs/perception.md).",
}
COLOUR_NOTE = {"mujoco": "plain MuJoCo rendering", "splat": "the splat composite (scanned table and objects, MuJoCo's arm)"}


SCENES_TEXT = """**Your own test scenes.** Besides the training layouts, you can write scenes: a YAML file under `practice/scenes/`
placing the objects where you want them (to reproduce a failure, or to test one situation: a tight gap on one side, a
blocker the fingers would hit, the other roll touching the target ...). Format and checks: docs/scenes.md. In short:

    target: tape_red                          # the object the task names: tape_red or tape_yellow
    objects:                                  # id: [x, y, yaw_deg], world frame (m, deg)
      tape_red:    [-0.30, 0.020, 0]
      tape_yellow: [-0.30, 0.075, 0]
      hole_block:  [-0.22, 0.020, 45]
    arm_start: [-0.30, 0.00, 0.22, 0]         # optional: fingertips x, y, z, hand yaw

Object ids (what the task calls them in this run): {names}. Work area x -0.49..-0.12 (away from the robot), y
-0.18..0.21. `{practice} scene check practice/scenes/NAME.yaml` builds and checks it ({n} checks per session): gaps
to the target, its visibility, the free straight-down grasp directions, and a preview image (NAME.check/preview.png).
`{practice} run --scene practice/scenes/NAME.yaml --n 3` runs your skill there (each episode counts). Your scenes are
for practice: the evaluation uses layouts you never see.

"""

VLM_TEXT = """**One model call per episode (Astra, `ctx.vlm`).** Your program may ask a vision-language model (gpt-6-astra) ONE
question per episode, typically at the start with the scene camera's (and/or the wrist camera's) first frame: to point
at the target or at the objects in its way (ask for pixel coordinates as JSON and turn them into 3D points with the
frame's depth), or to suggest a plan. `r = ctx.vlm.ask(prompt, [frame, ...])` -> `r["ok"]`, `r["text"]` (docs/
skill_api.md). A second call in the same episode is refused. A short pointing answer costs about $0.006-0.011 (more if
the model writes more); this run's model budget is {budget} (`status` shows what is spent); when it is used up, calls
are refused, so the program must still work without an answer. The model sees only what you send; it can be wrong.

"""


EVAL_TEXT = """**The evaluation set (you may use it in this run).** After the session your version is scored on a fixed set of
{n} layouts. You can run exactly that set on your current code: `{practice} eval` ({runs} times per session; it takes
a while: give it a timeout of 600000 ms, or run it in the background and check its summary). It writes
`practice/eval/NNN/`: `summary.md` and per episode `result.json`, `keyframes.png`, `events.jsonl`, `program/`. Its
episodes do not count against the practice budget; its model calls count against the model budget.

"""


TASK_SUCCESS = {
    "clutter-pick": "the target >= 5 cm above where it rested, held by both fingers, nothing else lifted, for 1 s",
    "pick": "the same lift check as clutter-pick (one named object, others >= 6 cm away)",
    "place": "the object inside the white box's walls, released (no finger touching it), the box upright, nothing else in "
             "the box, nothing off the table, for 1 s",
    "close-box": "the white box's lid (open 70-120 deg) closed to < 8 deg, the box upright and not shoved > 10 cm, for 1 s",
    "press": "the kettle's lid open > 30 deg: the lid-release button on top of its handle (a 16 mm cap) pressed 2.5 mm "
             "releases the latch and the lid's spring opens it; the kettle upright, for 1 s",
    "insert": "the wooden cylinder (40 x 40 mm, a 22 mm hole along its axis) on one of the stand's three 18 mm wooden "
              "sticks (~17 cm tall): the stick through the hole, the cylinder below the stick's tip, released, for 1 s",
    "pack": "every named object (2-3 of: the red, yellow and white tape rolls, the black block with holes, the "
            "screwdriver, the coiled cable) inside the white box's walls and released, then the box's lid closed (< 8 deg), "
            "the box upright, nothing else in it, nothing off the table, for 1 s; this task has 180 s of robot time "
            "(ctx.task: \"objects\" = their names, \"destination\" = the box)",
    "place-box": "the named object inside the white box's walls, released (no finger touching it), the box upright, "
                 "nothing else put in the box, nothing off the table, for 1 s; 90 s of robot time (ctx.task: kind "
                 "\"place\", \"object\" = its name, \"destination\" = the box)",
}
TASK_SUCCESS["place-box-new"] = TASK_SUCCESS["place-box"]
TASK_SUCCESS_SIMPLE = {                                       # (simple_sim: its own box and objects; docs/simple_sim.md)
    "place-box": TASK_SUCCESS["place-box"].replace("the white box's walls", "the grey box's walls"),
    "close-box": TASK_SUCCESS["close-box"].replace("the white box's lid", "the grey box's lid"),
    "clutter-pick-any": ("the named object (one of the red, green and blue cubes, the red and yellow cylinders; 2-4 other "
                         "objects 1.5-6 cm around it) >= 5 cm above where it rested, held by both fingers, nothing else "
                         "lifted, for 1 s"),
}
TASK_SUCCESS["clutter-pick-any"] = ("the named object (one of the red, yellow and white tape rolls, the coiled cable, the "
                                    "screwdriver; 2-4 other objects 1.5-6 cm around it) >= 5 cm above where it rested, held "
                                    "by both fingers, nothing else lifted, for 1 s")


def tasks_section(B, practice):
    from .. import tasks as TK
    from ..config import task_config
    from ..sim.scene import OBJECT_SETS
    os_ = task_config(B["task"])["object_set"]
    S, succ = OBJECT_SETS[os_], (TASK_SUCCESS_SIMPLE if os_ == "simple" else {})
    rows = []
    for f in B["tasks"]:
        td = TK.get(f)
        ex = td["instruction"].format(object="<object>", objects="the <object>, the <object> and the <object>",
                                      destination=S.get("destination") or td.get("destination", ""))
        rows.append(f"- **{f}** (`{td['entry']}`): told e.g. \"{ex}\" Success: {succ.get(f, TASK_SUCCESS[f])}.")
    if len(rows) == 1:
        return (f"**The task's entry and check.**\n{rows[0]}\nPractise it with `{practice} run --n 6`.\n\n")
    return ("**Tasks in this run.** The program is one skill per task, each with its own entry file (`run(ctx)`); "
            "`ctx.task[\"kind\"]` says which task an episode is. Each is evaluated on its own layouts.\n" + "\n".join(rows) +
            f"\nPractise a task with `{practice} run --task <name> --n 6` (and `scene check ... --task <name>`). Changes to "
            "one task's skill must not break the others: a version is kept only if it gains overall and loses no more "
            "than one success on any task.\n\n")


def _suite_eps(spec):
    from ..evaluation.suites import episodes
    return episodes(spec)


def brief(B, X):
    P = B["practice"]
    practice = str(paths.ROOT / "bin" / "ar-practice")
    from ..config import task_config
    from ..sim.scene import OBJECT_SETS
    t = task_config(B["task"])
    ph = OBJECT_SETS[t["object_set"]]["phrases"]
    objects = f'"{ph["tape_red"]}" or "{ph["tape_yellow"]}"'
    perception, perception_note, colour = t["perception"], PERCEPTION_NOTE[t["perception"]], COLOUR_NOTE[t["colour"]]
    depth_text = ("Depth is exact and the cameras' poses are the ones the program believes (docs/perception.md)."
                  if t.get("preset") == "ideal" else
                  "Depth is the realistic noisy D415 model and the\ncameras have a per-episode calibration error "
                  "(docs/perception.md).")
    scenes_text = SCENES_TEXT.format(practice=practice, n=B["scenes"]["checks_per_session"],
                                     names="; ".join(f"{k} = \"{v}\"" for k, v in ph.items())) if B.get("scenes") else ""
    V = B.get("vlm") or {}
    if V.get("dev_usd") is not None:
        vb = (f"${V.get('run_usd')} for your practice; the development evaluations have their own ${V['dev_usd']}, and "
              "in the final evaluation every episode can make its call")
    else:
        vb = f"${V.get('run_usd')} for practice and development together"
    vlm_text = VLM_TEXT.format(budget=vb) if V.get("enabled") else ""
    tasks_text = tasks_section(B, practice) if B.get("tasks") else ""
    E = B.get("eval_access")
    eval_text = EVAL_TEXT.format(practice=practice, runs=E["runs_per_session"], n=len(_suite_eps(E["spec"]))) if E else ""
    en = (X.get("learning") or {}).get("eval_note")
    if E and en:                                              # (the evalset ablation: when to use it; no model words
        if not V.get("enabled"):                              # in a run without model calls)
            eval_text = eval_text.replace("; its model calls count against the model budget.", ".")
        eval_text = eval_text.rstrip("\n") + f" {en.strip()}\n\n"
    if E and V.get("eval_usd") is not None:                   # (its own model budget, cached: the evalset ablation)
        eval_text = eval_text.replace("its model calls count against the model budget.",
                                      f"its model calls have their own budget (${V['eval_usd']}), and a request identical "
                                      "to an earlier one (the same images and words) is answered from a cache, free.")
    timeout_note = ((f"A practice call takes about 30 s per {B.get('practice_workers', 8)} episodes (longer while the "
                     "machine is busy): give these shell commands a timeout of 600000 ms (10 min).\n\n")
                    if (X.get("learning") or {}).get("practice_timeout_note") else "")
    lib = B.get("library", True)
    robot_text = ("a Franka Panda arm with a two-finger\ngripper over a table, seen by two RealSense D415 cameras (one "
                  "fixed across the table, one on the hand), simulated in a\n"
                  + ((X.get("learning") or {}).get("sim_text") or "MuJoCo twin of the real setup"))
    if lib:
        intro = (f"You improve a robot skill program by practising it in simulation. The robot is {robot_text}. The "
                 "program you improve is `skills/pick.py` (entry point `run(ctx)`), with helpers in\n`helpers/`.")
        can_do = ("Change the\nprogram's strategy, structure, feedback checks and recovery. The documented interfaces are in "
                  "`docs/`; the robot code\nbehind `ctx.library` and `ctx.robot` is in `reference/` (read-only; to change a "
                  "behaviour, subclass or wrap it in your\nown code).")
        rule_lib, goal3 = "", "(3) fewer model calls (none are used now)"
    else:
        vlm_bit = (" and, once per episode, the vision-language model Astra, for example to point at the object "
                   "(`ctx.vlm`)") if V.get("enabled") else ""
        sn = (X.get("learning") or {}).get("start_note")
        multi = len(B.get("tasks") or []) > 1
        prog_text = ("The program is one skill per task (its entry file, `run(ctx)`, in **Tasks in this run** below), with "
                     "helpers in `helpers/`." if multi else
                     f"The program is `{B.get('entry', 'skills/pick.py')}` (entry point `run(ctx)`), with helpers in `helpers/`.")
        intro = (f"You write a robot skill program and improve it by practising it in simulation. The robot is {robot_text}. "
                 f"{prog_text}\n\n"
                 f"**{sn or 'It is empty: no skill is written yet.'}** Write the skill{'s' if multi else ''} as code, using what the robot gives you: its "
                 "motion interface and cameras (`ctx.robot`, docs/robot.md), the depth and the camera geometry "
                 f"(`ctx.tools`), the segmentation model (`ctx.segmenter`, docs/perception.md){vlm_bit}. There is no skill "
                 "library: the robot's own skill code is neither available to your program nor shown to you "
                 "(docs/skill_api.md).")
        can_do = ("Write the\nprogram's strategy, structure, perception, grasp choice, feedback checks and recovery. The "
                  "documented interfaces are in `docs/`.")
        rule_lib = (" Write the skill yourself: a version that imports the robot's own code, or reaches\nfor it another "
                    "way (`sys.modules`, `importlib`, `__globals__`, ...), is rejected.")
        goal3 = "(3) fewer model calls" if V.get("enabled") else ""
    tn = (X.get("learning") or {}).get("test_note")
    test_text = f"**How your version is evaluated.** {tn}\n\n" if tn else ""
    tt = (X.get("learning") or {}).get("task_text")
    if tt:                                                    # (the experiment's own task paragraph: not clutter pick)
        task_text = tt.strip()
        examples = (f"    {practice} run --n 6                       # 1-{P['max_per_call']} episodes per call; --idx 5,17 picks layouts; --seed S\n"
                    f"    {practice} run --n 4 --observations wrist   # also save the camera frames the program got\n")
        layouts_text = "Practice uses training layouts (the evaluations use others)."
    else:
        task_text = (f"**The task (clutter pick).** A target object (in this run's practice: {objects}) lies on the table among 2-4 other\n"
                     "objects, which may touch it. The program is told the object's name (ctx.task[\"object\"]) and must pick it up and lift it. It\n"
                     "succeeds when the target is at least 5 cm above where it rested, held by both fingers, with nothing else lifted, for\n"
                     "1 second. An episode ends when the program returns; the robot refuses to move after 60 s of robot time.")
        examples = (f"    {practice} run --levels 2,3 --n 6            # 1-{P['max_per_call']} episodes per call; --idx 5,17 picks layouts; --seed S\n"
                    f"    {practice} run --levels 3 --n 4 --observations wrist    # also save the camera frames the program got\n")
        layouts_text = (f"Practice uses training layouts (levels {P['levels']}: 0 = objects 6-10 cm apart ... 3 = boxed in, no free straight-down\n"
                        "grasp).")
    return f"""# Your task in this workspace

{intro}

{task_text}

{test_text}**Goal, in order:** (1) more successes under that time limit, on layouts you have not seen; (2) less robot time per
success{'; ' + goal3 if goal3 else ''}. Robot speed, limits and the success check are fixed.

**Perception in this run: {perception}.** {perception_note} {depth_text} Colour: {colour}.

**What you can do.** Read and edit anything in this workspace: `skills/`, `helpers/`, `tests/`, `notes.md`. {can_do}
Practise in simulation with exactly these commands (the only shell commands you can run):

    {practice} status
{examples}    {practice} test                               # your tests in tests/ (pytest), in the sandbox, no robot

{timeout_note}{layouts_text} Each run writes `practice/runs/NNN/`: `summary.md`, and per episode `result.json` (the verdict, robot time),
`events.jsonl` (what your program logged), `keyframes.png` (8 frames: overview | wrist camera; read it to see what
happened), `video.mp4`, `program/` (program.json, errors). Look at failures before changing code.

{tasks_text}{scenes_text}{vlm_text}{eval_text}**Real-robot runs.** Between sessions the operator may run a version of your skill on the real Panda.
Those runs appear in `real/` (`real/README.md`, then each run's `summary.md`), and the session's prompt says so.

**Budget.** {P['episodes_per_session']} practice episodes in this session (`status` shows what is left), and your own
session's spending cap. Failed and crashed episodes count too. Stop when the budget is used or you have a version you
believe is better.

**Rules.** Programs see the scene only through `ctx` (cameras, the robot's own state, the segmenter): never try to
read the simulator's true state or files outside this workspace. Do not try to change the success check, the time
limit or the robot's limits. Keep `run(ctx)` working: a crash is a failure.{rule_lib}

**Before you finish:** leave your best version in `skills/` and `helpers/`, and write in `notes.md` what you changed,
why, and what practice showed (with the practice run numbers). After the session, your version is evaluated on
layouts you never see and kept only if it is better than the current version.
"""


def session_prompt(it, st, B, L, real_note="", resumed=False):
    pn = B.get("prompt_note")
    if pn:                                                    # (the experiment's goal, in every session's prompt)
        real_note = f" {pn.strip()}{real_note}"
    d = st.get("incumbent_dev") or {}
    last = st.get("last")
    prev = ""
    if last:
        dec = last["decision"]
        prev = (f"\nLast session ({last['session']}): your version was {'KEPT' if dec['keep'] else 'NOT kept'} ({dec['why']}). "
                + ("The workspace's code was reset to the current version; your notes.md and practice records are still here. "
                   if not dec["keep"] else ""))
    if len(d.get("by_family") or {}) > 1:
        lv = "tasks " + ", ".join(f"{k}: {v['successes']}/{v['n']}" for k, v in d["by_family"].items())
    else:
        lv = "levels " + ", ".join(f"{k}: {v['successes']}/{v['n']}" for k, v in (d.get("by_level") or {}).items())
    eps = practice_left(B, L)
    if resumed:                                               # (the conversation goes on: what changed since)
        return (f"Session {it} (you are continuing your previous session; your context from it is above). "
                f"The current version solves {d.get('successes')}/{d.get('n')} development episodes ({lv}; "
                f"{d.get('t_success_mean')} s robot time per success).{prev}{real_note} "
                f"You have {eps} practice episodes in this session. End with your best version in place and notes.md "
                f"updated.")
    entry = B.get("entry", "skills/pick.py")
    if len(B.get("tasks") or []) > 1:                          # (several tasks: each its own entry file)
        return (f"Session {it}. Read AGENTS.md (the tasks, their entry files and success checks), then docs/skill_api.md "
                f"and docs/tasks.md, then write and improve the skills by practising them. The current version solves "
                f"{d.get('successes')}/{d.get('n')} development episodes ({lv}; {d.get('t_success_mean')} s robot time per "
                f"success).{prev}{real_note} You have {eps} practice episodes. End with your best version in place and "
                f"notes.md updated.")
    if B.get("library", True) is False and st.get("incumbent") == "000_initial" and not d.get("successes"):
        docs = "docs/skill_api.md, docs/robot.md and docs/tasks.md" if B.get("tasks") else "docs/skill_api.md and docs/robot.md"
        return (f"Session {it}. Read AGENTS.md, then {docs}, then write {entry}: "
                f"it is empty, no skill is written yet (it solves 0/{d.get('n')} development episodes).{prev}{real_note} "
                f"Write the skill as code and improve it by practising it. You have {eps} "
                f"practice episodes. End with your best version in place and notes.md updated.")
    return (f"Session {it}. Read AGENTS.md, then docs/skill_api.md, then improve {entry} by practising it. "
            f"The current version solves {d.get('successes')}/{d.get('n')} development episodes "
            f"({lv}; "
            f"{d.get('t_success_mean')} s robot time per success).{prev}{real_note} "
            f"You have {eps} practice episodes. End with your best version in place "
            f"and notes.md updated.")


def practice_left(B, L):
    """This session's practice episodes: the per-session allowance, within what is left of the run's total."""
    P = B["practice"]
    used = sum(r.get("episodes", 0) for r in L if r.get("kind") == "practice")
    return max(0, min(P["episodes_per_session"], P["episodes_total"] - used))


# ---------------------------------------------------------------------------------------------------- cli
def cli(a):
    ap = argparse.ArgumentParser(prog=f"ar learn {a.action}")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--experiment", default="configs/experiments/m1_clutter_pick.yaml")
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--suite", default="heldout"); ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--baseline", help="reuse this suite run of the same initial skill (evaluate)")
    ap.add_argument("--tasks", help="init, multi-task experiments: e.g. pick,place,clutter-pick,press,close-box,insert")
    ap.add_argument("--initial", help="init: scratch | ported (the experiment's initial: mapping)")
    ap.add_argument("--add-evaluation-set", "--add_evaluation_set", dest="add_eval_set", action="store_true",
                    help="init, ablation: the agent may run the evaluation set (learning.exposed_eval_suite) itself")
    ap.add_argument("--note", help="run: the operator's message, added to this session's prompt")
    ap.add_argument("--progress-every", type=int, help="run: snapshot the code every N agent output tokens (progress)")
    ap.add_argument("--resume-session", dest="resume", action="store_true", default=None,
                    help="run: continue the previous session's conversation (saved for resuming from now on)")
    ap.add_argument("--no-resume-session", dest="resume", action="store_false", help="run: a fresh session")
    ap.add_argument("dirs", nargs="*", help="add-real: real/results/<run> directories")
    ap.add_argument("--practice", type=int, default=0, help="extend: more practice episodes for the run")
    ap.add_argument("--agent-usd", type=float, default=0.0, help="extend: more agent budget for the run")
    o = ap.parse_args(a.rest)
    if a.action == "init":
        init(o.run_id, o.experiment, o.add_eval_set, o.tasks, o.initial)
    elif a.action == "run":
        for _ in range(o.iterations):
            iterate(o.run_id, resume=o.resume, note=o.note, progress_every=o.progress_every)
    elif a.action == "add-real":
        from .real_feedback import add_real
        if not o.dirs:
            raise SystemExit("add-real: give real/results/<run> directories")
        add_real(o.run_id, o.dirs)
    elif a.action == "extend":
        extend(o.run_id, o.practice, o.agent_usd)
    elif a.action == "status":
        st = state(o.run_id); L = read_ledger(o.run_id)
        cost = agent_spent(L)
        prac = sum(r.get("episodes", 0) for r in L if r.get("kind") == "practice")
        dev = sum(r.get("episodes", 0) for r in L if r.get("kind") == "dev")
        print(json.dumps(dict(state=st, agent_usd=round(cost, 3),
                              agent_usd_by_session={r.get("session"): round(c, 3) for r, c in session_costs(L)},
                              practice_episodes=prac, dev_episodes=dev,
                              decisions=[r for r in L if r.get("kind") == "decision"]), indent=1, default=str))
    elif a.action == "evaluate":
        evaluate(o.run_id, o.suite, o.workers, o.baseline)
    elif a.action == "progress":
        progress_eval(o.run_id, o.suite if o.suite != "heldout" else "progress", o.workers)
