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
        X["suites"] = {k: filter_suite(v, fams) for k, v in X["suites"].items()}
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
    (ws / "practice").mkdir()
    if L.get("allow_custom_scenes"):
        (ws / "practice" / "scenes").mkdir()
    (ws / "notes.md").write_text("# Notes\n\n(What you tried, what you saw in practice, what you changed and why.)\n")
    (ws / "docs").mkdir()
    for f in ("skill_api.md", "robot.md", "perception.md", "task_clutter_pick.md") + (("scenes.md",) if L.get("allow_custom_scenes") else ()) \
            + (("tasks.md",) if X.get("tasks") else ()):
        if (paths.ROOT / "docs" / f).exists():
            txt = (paths.ROOT / "docs" / f).read_text()
            if not (X.get("vlm") or {}).get("enabled"):
                txt = strip_vlm(txt)                          # (runs without model calls: no word of ctx.vlm)
            (ws / "docs" / f).write_text(txt)
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
                  tasks=X.get("tasks"),
                  practice=dict(split=P["split"], levels=P.get("levels", [0]), idx_range=P.get("idx_range", [0, 300]),
                                seed_range=P["seed_range"], families=fam_ranges,
                                episodes_per_session=L["practice_episodes_per_session"],
                                episodes_total=L["practice_episodes_total"], max_per_call=L.get("practice_max_per_call", 12)),
                  agent=dict(model=L.get("model"), effort=L.get("effort"), usd_per_session=L["agent_usd_per_session"],
                             usd_total=L["agent_usd_total"], wall_s_per_session=L["agent_wall_s_per_session"]),
                  selection=L["selection"], dev_suite=L["dev_suite"],
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
    V = dict(V, tag=f"{run_id}:dev", cache_dir=str(run_dir(run_id) / "vlm_cache")) if V.get("enabled") else None
    S = run_suite(skill_dir, spec, task_config(X["task"]), out, workers=L.get("dev_workers", 16), sandbox=True,
                  entry=X.get("entry", "skills/pick.py"), log=None, vlm=V)
    ledger(run_id, kind="dev", name=name, episodes=S["n"], successes=S["successes"], robot_s=S["robot_time_total_s"],
           wall_s=round(time.time() - t0, 1), model_cost_usd=S["model_cost_usd"])
    return dict(successes=S["successes"], n=S["n"], t_success_mean=S["t_success_mean"], by_level=S["by_level"],
                by_family=S.get("by_family"),
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
    fam = per_family(inc_dev_dir, cand_dev_dir)
    if len(fam) > 1:
        info["by_task"] = fam
        worst = min(fam.values(), key=lambda d: d["gain"])
        if worst["gain"] < -rule.get("max_task_loss", 1):
            bad = [f for f, d in fam.items() if d["gain"] < -rule.get("max_task_loss", 1)]
            return dict(info, keep=False, why=f"{', '.join(bad)} lost {-worst['gain']} dev successes (> {rule.get('max_task_loss', 1)} per task)")
    if gain >= rule["min_gain"]:
        return dict(info, keep=True, why=f"+{gain} dev successes (>= {rule['min_gain']})")
    if 0 <= gain and c["dt_mean"] is not None and c["dt_mean"] <= -rule["min_dt_s"]:
        return dict(info, keep=True, why=f"successes {gain:+d}, {c['dt_mean']:+.2f} s on the {c['both']} both solve")
    return dict(info, keep=False, why=f"successes {gain:+d} (< {rule['min_gain']}), time {c['dt_mean']} s (needs <= -{rule['min_dt_s']})")


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
                              entry=X.get("entry", "skills/pick.py"), log=print, vlm=V)
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
skill_api.md). A second call in the same episode is refused. A short pointing answer costs about $0.006 (more if the
model writes more); this run's model budget is ${usd} for practice and development together (`status` shows what is
spent); when it is used up, calls are refused, so the program must still work without an answer. The model sees only
what you send; it can be wrong.

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
}


def tasks_section(B, practice):
    from .. import tasks as TK
    rows = []
    for f in B["tasks"]:
        td = TK.get(f)
        ex = td["instruction"].format(object="<object>", destination=td.get("destination", ""))
        rows.append(f"- **{f}** (`{td['entry']}`): told e.g. \"{ex}\" Success: {TASK_SUCCESS[f]}.")
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
    scenes_text = SCENES_TEXT.format(practice=practice, n=B["scenes"]["checks_per_session"],
                                     names="; ".join(f"{k} = \"{v}\"" for k, v in ph.items())) if B.get("scenes") else ""
    V = B.get("vlm") or {}
    vlm_text = VLM_TEXT.format(usd=V.get("run_usd")) if V.get("enabled") else ""
    tasks_text = tasks_section(B, practice) if B.get("tasks") else ""
    E = B.get("eval_access")
    eval_text = EVAL_TEXT.format(practice=practice, runs=E["runs_per_session"], n=len(_suite_eps(E["spec"]))) if E else ""
    timeout_note = ((f"A practice call takes about 30 s per {B.get('practice_workers', 8)} episodes (longer while the "
                     "machine is busy): give these shell commands a timeout of 600000 ms (10 min).\n\n")
                    if (X.get("learning") or {}).get("practice_timeout_note") else "")
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

{timeout_note}Practice uses training layouts (levels {P['levels']}: 0 = objects 6-10 cm apart ... 3 = boxed in, no free straight-down
grasp). Each run writes `practice/runs/NNN/`: `summary.md`, and per episode `result.json` (the verdict, robot time),
`events.jsonl` (what your program logged), `keyframes.png` (8 frames: overview | wrist camera; read it to see what
happened), `video.mp4`, `program/` (program.json, errors). Look at failures before changing code.

{tasks_text}{scenes_text}{vlm_text}{eval_text}**Budget.** {P['episodes_per_session']} practice episodes in this session (`status` shows what is left), and your own
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
    if len(d.get("by_family") or {}) > 1:
        lv = "tasks " + ", ".join(f"{k}: {v['successes']}/{v['n']}" for k, v in d["by_family"].items())
    else:
        lv = "levels " + ", ".join(f"{k}: {v['successes']}/{v['n']}" for k, v in (d.get("by_level") or {}).items())
    return (f"Session {it}. Read AGENTS.md, then docs/skill_api.md, then improve skills/pick.py by practising it. "
            f"The current version solves {d.get('successes')}/{d.get('n')} development episodes "
            f"({lv}; "
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
    ap.add_argument("--tasks", help="init, multi-task experiments: e.g. pick,place,clutter-pick,press,close-box,insert")
    ap.add_argument("--initial", help="init: scratch | ported (the experiment's initial: mapping)")
    ap.add_argument("--add-evaluation-set", "--add_evaluation_set", dest="add_eval_set", action="store_true",
                    help="init, ablation: the agent may run the evaluation set (learning.exposed_eval_suite) itself")
    o = ap.parse_args(a.rest)
    if a.action == "init":
        init(o.run_id, o.experiment, o.add_eval_set, o.tasks, o.initial)
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
