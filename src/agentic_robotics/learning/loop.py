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

SELECTION RULE (dev suite, the same episodes for candidate and incumbent, robot speed and limits unchanged):
  void   any platform error in the candidate's dev run -> rejected (re-run by the operator if the platform is at fault)
  keep   successes(candidate) - successes(incumbent) >= min_gain                                   (primary)
  keep   0 <= that gain < min_gain, and on the episodes both solve the candidate is faster by >= min_dt_s on average,
         with no more model calls                                                                  (secondary)
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
def init(run_id, exp_path):
    X = experiment(exp_path)
    L = X["learning"]
    ws, rd = paths.WORKSPACES / run_id, run_dir(run_id)
    if ws.exists() or rd.exists():
        raise SystemExit(f"{run_id}: exists already")
    src = paths.ROOT / X["skill"]
    rd.mkdir(parents=True)
    ws.mkdir(parents=True)
    for d in CODE_DIRS:
        if (src / d).exists():
            shutil.copytree(src / d, ws / d, ignore=shutil.ignore_patterns("__pycache__"))
    (ws / "practice").mkdir()
    (ws / "notes.md").write_text("# Notes\n\n(What you tried, what you saw in practice, what you changed and why.)\n")
    (ws / "docs").mkdir()
    for f in ("skill_api.md", "robot.md", "perception.md", "task_clutter_pick.md"):
        if (paths.ROOT / "docs" / f).exists():
            shutil.copy2(paths.ROOT / "docs" / f, ws / "docs" / f)
    ref = ws / "reference"
    shutil.copytree(paths.VENDOR, ref, ignore=shutil.ignore_patterns("__pycache__", "*.obj", "*.stl", "*.json", "__init__.py"))
    (ref / "README.md").write_text(REFERENCE_README)
    for p in ref.rglob("*"):
        p.chmod(0o555 if p.is_dir() else 0o444)
    P = X["practice"]
    budget = dict(task=X["task"], entry=X.get("entry", "skills/pick.py"), practice_workers=L.get("practice_workers", 8),
                  practice=dict(split=P["split"], levels=P["levels"], idx_range=P["idx_range"], seed_range=P["seed_range"],
                                episodes_per_session=L["practice_episodes_per_session"],
                                episodes_total=L["practice_episodes_total"], max_per_call=L.get("practice_max_per_call", 12)),
                  agent=dict(model=L.get("model"), effort=L.get("effort"), usd_per_session=L["agent_usd_per_session"],
                             usd_total=L["agent_usd_total"], wall_s_per_session=L["agent_wall_s_per_session"]),
                  selection=L["selection"], dev_suite=L["dev_suite"])
    (rd / "budget.json").write_text(json.dumps(budget, indent=1))
    (rd / "experiment.json").write_text(json.dumps(X, indent=1, default=str))
    (ws / ".ar_run.json").write_text(json.dumps(dict(run_id=run_id, session=None)))
    (ws / "AGENTS.md").write_text(brief(budget, X))
    (ws / "CLAUDE.md").write_text("See AGENTS.md (the brief for this workspace).\n")
    ck = rd / "checkpoints" / "000_initial"
    snapshot(ws, ck)
    ledger(run_id, kind="init", experiment=X["_path"], skill=X["skill"])
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
    S = run_suite(skill_dir, spec, task_config(X["task"]), out, workers=L.get("dev_workers", 16), sandbox=True,
                  entry=X.get("entry", "skills/pick.py"), log=None)
    ledger(run_id, kind="dev", name=name, episodes=S["n"], successes=S["successes"], robot_s=S["robot_time_total_s"],
           wall_s=round(time.time() - t0, 1), model_cost_usd=S["model_cost_usd"])
    return dict(successes=S["successes"], n=S["n"], t_success_mean=S["t_success_mean"], by_level=S["by_level"],
                categories=S["categories"], platform_errors=S["platform_errors"], model_calls=S["model_calls"])


# ---------------------------------------------------------------------------------------------------- one iteration
def iterate(run_id):
    rd, ws = run_dir(run_id), paths.WORKSPACES / run_id
    X = json.loads((rd / "experiment.json").read_text())
    B = json.loads((rd / "budget.json").read_text())
    st = state(run_id)
    it = st["iteration"] + 1
    spent = sum(r.get("cost_usd") or 0 for r in read_ledger(run_id) if r.get("kind") == "session")
    A = B["agent"]
    left = A["usd_total"] - spent
    if left < 0.5:
        raise SystemExit(f"agent budget spent: ${spent:.2f} of ${A['usd_total']:.2f}")
    sid = f"s{it:02d}"
    meta = json.loads((ws / ".ar_run.json").read_text()); meta["session"] = sid
    (ws / ".ar_run.json").write_text(json.dumps(meta))
    prompt = session_prompt(it, st, B, read_ledger(run_id))
    print(f"[{run_id}] iteration {it}: session {sid} (cap ${min(A['usd_per_session'], left):.2f}, {A['wall_s_per_session']} s)")
    res = agent.run_session(ws, prompt, (ws / "AGENTS.md").read_text(), rd / "sessions" / sid, model=A.get("model"),
                            max_budget_usd=min(A["usd_per_session"], left), wall_limit_s=A["wall_s_per_session"],
                            effort=A.get("effort"),
                            env_extra=dict(AR_WORKSPACE=str(ws.resolve()), AR_RUN=run_id))
    ledger(run_id, kind="session", session=sid, cost_usd=res.get("cost_usd"), usage=res.get("usage"), turns=res.get("turns"),
           wall_s=res.get("wall_s"), subtype=res.get("subtype"), denials=len(res.get("denials") or []),
           tool_calls=res.get("tool_calls"), model=(res.get("init") or {}).get("model"))
    print(f"  session done: ${res.get('cost_usd')}, {res.get('turns')} turns, {res.get('wall_s')} s, "
          f"{len(res.get('denials') or [])} denied tool calls, exit {res.get('exit')}")
    cand = rd / "candidates" / sid
    snapshot(ws, cand)
    shutil.copy2(ws / "notes.md", rd / "sessions" / sid / "notes.md")
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
    if not changed:
        decision = dict(keep=False, why="no code change")
        S = None
    else:
        S = dev_eval(run_id, sid, cand, X)
        decision = select(rd / "dev" / st["incumbent"], rd / "dev" / sid, S, B["selection"])
    ledger(run_id, kind="decision", session=sid, candidate=sid, incumbent=st["incumbent"], **decision)
    print(f"  dev: {None if S is None else (S['successes'], S['n'], S['t_success_mean'])} vs incumbent "
          f"{st['incumbent_dev']['successes']}/{st['incumbent_dev']['n']} ({st['incumbent_dev']['t_success_mean']} s) -> "
          f"{'KEEP' if decision['keep'] else 'reject'}: {decision['why']}")
    if decision["keep"]:
        shutil.copytree(cand, rd / "checkpoints" / sid)
        state(run_id, incumbent=sid, iteration=it, incumbent_dev=S, last=dict(session=sid, decision=decision, dev=S))
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


def select(inc_dev_dir, cand_dev_dir, S, rule):
    if S["platform_errors"]:
        return dict(keep=False, why=f"void: {S['platform_errors']} platform errors in the candidate's dev run")
    c = compare(inc_dev_dir, cand_dev_dir)
    gain = c["b_successes"] - c["a_successes"]
    info = dict(gain=gain, only_incumbent=c["only_a"], only_candidate=c["only_b"], mcnemar_p=c["mcnemar_p"],
                dt_mean=c["dt_mean"], dt_ci95=c["dt_ci95"])
    if gain >= rule["min_gain"]:
        return dict(info, keep=True, why=f"+{gain} dev successes (>= {rule['min_gain']})")
    if 0 <= gain and c["dt_mean"] is not None and c["dt_mean"] <= -rule["min_dt_s"]:
        return dict(info, keep=True, why=f"successes {gain:+d}, {c['dt_mean']:+.2f} s on the {c['both']} both solve")
    return dict(info, keep=False, why=f"successes {gain:+d} (< {rule['min_gain']}), time {c['dt_mean']} s (needs <= -{rule['min_dt_s']})")


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
        out[name] = run_suite(rd / "checkpoints" / name, spec, task_config(X["task"]), d, workers=workers, sandbox=True,
                              entry=X.get("entry", "skills/pick.py"), log=print)
    if st["incumbent"] != "000_initial":
        from ..evaluation.compare import compare_md
        c = compare(rd / "heldout" / f"{suite}_000_initial", rd / "heldout" / f"{suite}_{st['incumbent']}")
        md = compare_md(c, "initial", st["incumbent"])
        (rd / "heldout" / f"{suite}_comparison.md").write_text(md)
        print(md)
    return out


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


def brief(B, X):
    P = B["practice"]
    practice = str(paths.ROOT / "bin" / "ar-practice")
    from ..config import task_config
    from ..sim.scene import OBJECT_SETS
    t = task_config(B["task"])
    ph = OBJECT_SETS[t["object_set"]]["phrases"]
    objects = f'"{ph["tape_red"]}" or "{ph["tape_yellow"]}"'
    perception, perception_note, colour = t["perception"], PERCEPTION_NOTE[t["perception"]], COLOUR_NOTE[t["colour"]]
    return f"""# Your task in this workspace

You improve a robot skill program by practising it in simulation. The robot is a Franka Panda arm with a two-finger
gripper over a table, seen by two RealSense D415 cameras (one fixed across the table, one on the hand), simulated in a
MuJoCo twin of the real setup. The program you improve is `skills/pick.py` (entry point `run(ctx)`), with helpers in
`helpers/`.

**The task (clutter pick).** A target object (in this run's practice: {objects}) lies on the table among 2-4 other
objects, which may touch it. The program is told the object's name (ctx.task["object"]) and must pick it up and lift it. It
succeeds when the target is at least 5 cm above where it rested, held by both fingers, with nothing else lifted, for
1 second. An episode ends when the program returns; the robot refuses to move after 60 s of robot time.

**Goal, in order:** (1) more successes under that time limit, on layouts you have not seen; (2) less robot time per
success; (3) fewer model calls (none are used now). Robot speed, limits and the success check are fixed.

**Perception in this run: {perception}.** {perception_note} Depth is the realistic noisy D415 model and the
cameras have a per-episode calibration error (docs/perception.md). Colour: {colour}.

**What you can do.** Read and edit anything in this workspace: `skills/`, `helpers/`, `tests/`, `notes.md`. Change the
program's strategy, structure, feedback checks and recovery. The documented interfaces are in `docs/`; the robot code
behind `ctx.library` and `ctx.robot` is in `reference/` (read-only; to change a behaviour, subclass or wrap it in your
own code). Practise in simulation with exactly these commands (the only shell commands you can run):

    {practice} status
    {practice} run --levels 2,3 --n 6            # 1-{P['max_per_call']} episodes per call; --idx 5,17 picks layouts; --seed S
    {practice} run --levels 3 --n 4 --observations wrist    # also save the camera frames the program got
    {practice} test                               # your tests in tests/ (pytest), in the sandbox, no robot

Practice uses training layouts (levels {P['levels']}: 0 = objects 6-10 cm apart ... 3 = boxed in, no free straight-down
grasp). Each run writes `practice/runs/NNN/`: `summary.md`, and per episode `result.json` (the verdict, robot time),
`events.jsonl` (what your program logged), `keyframes.png` (8 frames: overview | wrist camera; read it to see what
happened), `video.mp4`, `program/` (program.json, errors). Look at failures before changing code.

**Budget.** {P['episodes_per_session']} practice episodes in this session (`status` shows what is left), and your own
session's spending cap. Failed and crashed episodes count too. Stop when the budget is used or you have a version you
believe is better.

**Rules.** Programs see the scene only through `ctx` (cameras, the robot's own state, the segmenter): never try to
read the simulator's true state or files outside this workspace. Do not try to change the success check, the time
limit or the robot's limits. Keep `run(ctx)` working: a crash is a failure.

**Before you finish:** leave your best version in `skills/` and `helpers/`, and write in `notes.md` what you changed,
why, and what practice showed (with the practice run numbers). After the session, your version is evaluated on
layouts you never see and kept only if it is better than the current version.
"""


def session_prompt(it, st, B, L):
    d = st.get("incumbent_dev") or {}
    last = st.get("last")
    prev = ""
    if last:
        dec = last["decision"]
        prev = (f"\nLast session ({last['session']}): your version was {'KEPT' if dec['keep'] else 'NOT kept'} ({dec['why']}). "
                + ("The workspace's code was reset to the current version; your notes.md and practice records are still here. "
                   if not dec["keep"] else ""))
    lv = ", ".join(f"{k}: {v['successes']}/{v['n']}" for k, v in (d.get("by_level") or {}).items())
    return (f"Session {it}. Read AGENTS.md, then docs/skill_api.md, then improve skills/pick.py by practising it. "
            f"The current version solves {d.get('successes')}/{d.get('n')} development episodes "
            f"(levels {lv}; "
            f"{d.get('t_success_mean')} s robot time per success).{prev} "
            f"You have {B['practice']['episodes_per_session']} practice episodes. End with your best version in place "
            f"and notes.md updated.")


# ---------------------------------------------------------------------------------------------------- cli
def cli(a):
    ap = argparse.ArgumentParser(prog=f"ar learn {a.action}")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--experiment", default="configs/experiments/m1_clutter_pick.yaml")
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--suite", default="heldout"); ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--baseline", help="reuse this suite run of the same initial skill (evaluate)")
    o = ap.parse_args(a.rest)
    if a.action == "init":
        init(o.run_id, o.experiment)
    elif a.action == "run":
        for _ in range(o.iterations):
            iterate(o.run_id)
    elif a.action == "status":
        st = state(o.run_id); L = read_ledger(o.run_id)
        cost = sum(r.get("cost_usd") or 0 for r in L if r.get("kind") == "session")
        prac = sum(r.get("episodes", 0) for r in L if r.get("kind") == "practice")
        dev = sum(r.get("episodes", 0) for r in L if r.get("kind") == "dev")
        print(json.dumps(dict(state=st, agent_usd=round(cost, 3), practice_episodes=prac, dev_episodes=dev,
                              decisions=[r for r in L if r.get("kind") == "decision"]), indent=1, default=str))
    elif a.action == "evaluate":
        evaluate(o.run_id, o.suite, o.workers, o.baseline)
