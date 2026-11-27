"""The harness's side of the TWO-ARM tasks (rig "x5": RoboDojo's bimanual robot). Practice: MuJoCo (xsim/bi/mj.py,
project .venv). Target: ManiSkill (sweep_blocks/env.py, the robomme environment). Called by xsim/session.py,
xsim/target.py and xsim/launch.py where a task's rig is "x5"; the single-arm tasks do not come here.

  check_world      is an agent's world file acceptable (rules; no build)
  practice         a program in a practice world's scenes (MuJoCo)             -> one result per seed
  run_batch        a frozen program on target scene files (ManiSkill)          -> (agent-visible episodes, private results)
  prepare          a replicate's private target scenes, the final test splits, the shared look and documents
Every target scene of this rig is SHOWN SOLVABLE when it is made: sweep_blocks/make_panel.py keeps a drawn scene only
if the scripted sweeping reference solves it in the target (the reference's failures are recorded with the scenes)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE)) if str(HERE) not in sys.path else None
from m2m.envpaths import maniskill_python  # noqa: E402  (machine-local: M2M_MANISKILL_PYTHON or configs/local.yaml)
PANEL0 = HERE / "assets" / "robodojo_sweep" / "panel20"
INTERFACE_CHECK = '''def run(robot, task):
    # the harness's own check that a world supports the documented interface (not a skill, not the task)
    frames = robot.look()
    for name, f in frames.items():
        robot.show(name, f.rgb)
        print("camera", name, f.rgb.shape, "depth seen on", int((f.depth == f.depth).mean() * 100), "% of the pixels")
    for arm in ("left", "right"):
        s = robot.state(arm)
        print(arm, "state", s)
        p = s["tcp_mm"]
        print(arm, "move", robot.move(arm, (p[0], p[1], p[2] - 30), s["approach_dir"], s["closing_dir"]))
        print(arm, "gripper", robot.set_gripper(arm, 40), robot.set_gripper(arm, 88))
        print(arm, "home", robot.home(arm))
'''


def _json(r):
    for l in reversed(r.stdout.splitlines()):
        if l.startswith("XSIM_JSON "):
            return json.loads(l[10:])
    raise RuntimeError("the job gave no result:\n" + r.stderr[-1500:])


def _ms(args, timeout=1800):
    env = dict(os.environ, PYTHONNOUSERSITE="1", CUDA_VISIBLE_DEVICES=os.environ.get("M2M_GPU", "0"))
    return subprocess.run([maniskill_python(), "-I"] + [str(a) for a in args], capture_output=True, text=True, env=env, timeout=timeout)


def _mj(args, timeout=900):
    env = dict(os.environ, PYTHONNOUSERSITE="1", MUJOCO_GL="egl")
    return subprocess.run([sys.executable, "-I", str(HERE / "xsim" / "bi" / "practice_run.py")] + [str(a) for a in args], capture_output=True, text=True,
                          env=env, timeout=timeout)


def check_world(code):
    sys.path.insert(0, str(HERE))
    from xsim.bi.practice_run import check_world as chk
    return chk(code)


def practice(program, world, seeds, out_dirs, task, cfg, time_limit=None, validate=False):
    txt = Path(out_dirs[0]).parent / "task.txt"
    if not txt.exists():
        txt.write_text(task["text"])

    def one(sd):
        s, d = sd
        try:
            r = _mj(["--world", world, "--seed", s, "--program", program, "--out", d, "--task-text-file", txt, "--time-limit", time_limit or task["episode_s"]] + (["--validate"] if validate else []))
            if (Path(d) / "result.json").exists():
                return json.loads((Path(d) / "result.json").read_text())
            return dict(ok=False, ended="the practice run failed (an error of the harness or a crash of the simulation): " + r.stderr[-300:])
        except subprocess.TimeoutExpired:
            return dict(ok=False, ended="the practice run was stopped (wall-clock limit)")
        except Exception as e:
            return dict(ok=False, ended=f"runner error: {type(e).__name__}: {str(e)[:200]}")
    with ThreadPoolExecutor(max_workers=int(cfg["practice_jobs"])) as ex:
        return list(ex.map(one, list(zip(seeds, out_dirs))))


def run_batch(program, scene_files, task, private_dir, public_dir, cfg, jobs=None, log=print):
    program, private_dir = Path(program), Path(private_dir); private_dir.mkdir(parents=True, exist_ok=True)
    txt = private_dir / "task.txt"; txt.write_text(task["text"])

    def one(k_f):
        k, f = k_f
        d = private_dir / f"ep_{k:02d}"
        t = time.time()
        args = [HERE / "xsim" / "bi" / "target_run.py", "episode", "--layout", f, "--program", program, "--out", d, "--task-text-file", txt]
        if public_dir:
            args += ["--public", Path(public_dir) / f"ep_{k:02d}"]
        try:
            r = _ms(args, timeout=900)
            err = None if r.returncode == 0 else r.stderr[-800:]
        except subprocess.TimeoutExpired:
            err = "the episode's process was stopped (wall-clock limit)"
        if err:
            d.mkdir(parents=True, exist_ok=True); (d / "harness_error.txt").write_text(str(err))
        res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else dict(success=False, ended="process failed", check=None)
        res.update(episode=k, config=str(f), config_name=Path(f).stem, wall_total_s=round(time.time() - t, 1), harness_error=err)
        pub = None
        if public_dir:
            pf = Path(public_dir) / f"ep_{k:02d}" / "episode.json"
            if pf.exists():
                pub = json.loads(pf.read_text())
            else:
                pub = dict(success=False, ended="the episode's process failed (an error of the harness or the program)", robot_s=None, wall_s=None,
                           commands=None, segmentation_calls=None)
                pf.parent.mkdir(parents=True, exist_ok=True); pf.write_text(json.dumps(pub, indent=1))
        return res, pub
    with ThreadPoolExecutor(max_workers=int(jobs or cfg["target_jobs"])) as ex:
        out = list(ex.map(one, list(enumerate(scene_files))))
    return [p for _, p in out], [r for r, _ in out]


def panel(d, n=None):
    f = sorted(Path(d).glob("ep_*.json"))
    return f[:int(n)] if n else f


def make_scenes(out, n, seed, looks_seed, log=print, ood=False, jobs=12, mirror=False):
    """n scenes of the task, each solved by the sweeping reference in the target, with drawn looks -> their files.
    mirror: every scene mirrored left-right (the brush on the right, the mouth towards -x, the cubes on the left)."""
    out = Path(out)
    if not (out / "looks.json").exists():
        cand = int(n * 3) + 6
        for attempt in range(4):                                    # (the reference solves about half of what is drawn: draw more if short)
            if out.exists():
                subprocess.run(["chmod", "-R", "u+w", str(out)]); shutil.rmtree(out)
            r = _ms([HERE / "sweep_blocks" / "make_panel.py", "--n", n, "--seed", seed, "--out", out, "--jobs", jobs, "--candidates", cand] + (["--mirror"] if mirror else []),
                    timeout=6 * 3600)
            if r.returncode == 0 and len(panel(out)) >= n:
                break
            log(f"  {out.name}: {len(panel(out))} of {n} shown solvable among {cand} drawn; drawing more")
            cand = int(cand * 1.6)
        else:
            raise RuntimeError(f"{out.name}: only {len(panel(out))} of {n} scenes were shown solvable (of {cand} drawn): {r.stderr[-400:]}")
        r = _ms([HERE / "sweep_blocks" / "looks.py", "--panel", out, "--seed", looks_seed] + (["--ood"] if ood else []))
        if r.returncode != 0:
            raise RuntimeError("the looks failed: " + r.stderr[-400:])
        log(f"  {out.name}: {n} scenes shown solvable (of {cand} drawn)")
    return panel(out, n)


def freeze(d):
    """A set of scenes becomes read-only, with a manifest of its files' hashes (checked before every use)."""
    import hashlib
    d = Path(d)
    man = [dict(file=f.name, sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in panel(d)]
    (d / "panel_manifest.json").write_text(json.dumps(man, indent=1))
    for f in d.rglob("*"):
        if f.is_file():
            f.chmod(0o444)
    return man


def variant(files, out, change, log=print):
    """A final out-of-distribution split: each scene with ONE kind of change (the reference is run on the changed scenes
    and its outcome recorded; no scene is dropped for it)."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(files):
        L = json.loads(Path(f).read_text())
        V = dict(change)
        if V.get("cube_scale"):
            for o in L["Rigid"]["small_cube"]:
                o["default_pos"][2] += 0.00962 * (float(V["cube_scale"]) - 1.0)
        if V.get("cube_mass_scale"):
            for o in L["Rigid"]["small_cube"]:
                o["physics"]["mass"] = float(o["physics"].get("mass", 0.03)) * float(V["cube_mass_scale"])
        L["Variant"] = V
        (out / f"ep_{i:02d}.json").write_text(json.dumps(L))
    r = _ms([HERE / "sweep_blocks" / "run_panel.py", "--verify", "--panel", out, "--jobs", 10], timeout=3 * 3600)
    log(f"  {out.name}: the reference on the changed scenes: {(r.stdout.strip().splitlines() or ['?'])[-1]}")
    return panel(out)


def prepare(task_name, task, rep, cfg, R, T, log=print):
    """The private target scenes of a replicate and (once per task) the final test splits; the shared look and documents."""
    from xsim import prompts
    priv, sd = R / "private", cfg["seeds"]
    if not (T / "splits.json").exists():                            # the final test splits: made once, before any result exists
        log("the final test scenes (one set for all replicates)")
        splits = dict(test_id=[str(f) for f in make_scenes(T / "test_id", cfg["splits"]["test_id"], sd["test_id"], sd["test_id"] + 1, log)])
        for k, name in enumerate(n for n in cfg["splits"] if n.startswith("ood_") and n in task["ood"]):
            ch = task["ood"][name]
            if ch.get("looks") == "ood":
                files = make_scenes(T / name, cfg["splits"][name], sd["ood"] + 10 * k, sd["ood"] + 10 * k + 1, log, ood=True)
            else:
                base = make_scenes(T / (name + "_base"), cfg["splits"][name], sd["ood"] + 10 * k, sd["ood"] + 10 * k + 1, log)
                files = variant(base, T / name, ch, log)
            splits[name] = [str(f) for f in files]
        for name in splits:
            freeze(T / name)
        (T / "splits.json").write_text(json.dumps(dict(made=time.strftime("%Y-%m-%d %H:%M"), task=task_name, shown_solvable="every in-distribution scene, by the "
                                                       "sweeping reference; the changed (OOD) scenes: see each split's reverification.json", splits=splits), indent=1))
    else:                                                           # a split added to the task LATER (after results exist): made now, the others untouched
        sp = json.loads((T / "splits.json").read_text())
        for k, name in enumerate(n for n in cfg["splits"] if n.startswith("ood_") and n in task["ood"]):
            if name in sp["splits"]:
                continue
            ch = task["ood"][name]
            if not ch.get("mirror"):
                raise SystemExit(f"the split {name} is not in the frozen splits.json and is not one that can be added later")
            log(f"a further final test split: {name}")
            files = make_scenes(T / name, cfg["splits"][name], sd["ood"] + 10 * k, sd["ood"] + 10 * k + 1, log, mirror=True)
            freeze(T / name)
            sp["splits"][name] = [str(f) for f in files]
            sp.setdefault("added_later", {})[name] = dict(made=time.strftime("%Y-%m-%d %H:%M"), how="drawn like the in-distribution scenes, then mirrored "
                                                          "left-right; each shown solvable by the sweeping reference (its mirrored mode)")
            (T / "splits.json").write_text(json.dumps(sp, indent=1))
    if not (priv / "dev" / "panel_manifest.json").exists():
        log(f"replicate {rep}: the development panel, F's practice pool, the example scene")
        if rep == 0 and len(panel(PANEL0)) >= int(cfg["target_panel"]):   # (the panel made and shown on 2026-11-27)
            (priv / "dev").mkdir(parents=True, exist_ok=True)
            for f in list(panel(PANEL0, cfg["target_panel"])) + [PANEL0 / "verification.json", PANEL0 / "reverification.json", PANEL0 / "looks.json"]:
                shutil.copy(f, priv / "dev" / f.name)
        else:
            make_scenes(priv / "dev", cfg["target_panel"], sd["dev"] + 1000 * rep, sd["dev"] + 1000 * rep + 1, log)
        freeze(priv / "dev")
        make_scenes(priv / "f_pool", cfg["f_pool"], sd["dev"] + 1000 * rep + 300, sd["dev"] + 1000 * rep + 301, log); freeze(priv / "f_pool")
        make_scenes(priv / "example", 1, sd["example"] + 100 * rep, sd["example"] + 100 * rep + 1, log); freeze(priv / "example")
    sh = R / "shared"
    if not (sh / "look" / "state.txt").exists():
        r = _ms([HERE / "xsim" / "bi" / "target_run.py", "look", "--layout", panel(priv / "example")[0], "--out", sh / "look"])
        if r.returncode != 0:
            raise SystemExit("the look failed: " + r.stderr[-800:])
        (sh / "docs").mkdir(parents=True, exist_ok=True)
        (sh / "docs" / "interface.md").write_text(prompts.interface(task) + "\n\n" + prompts.RULES + "\n")
        (sh / "docs" / "task.md").write_text(task["text"] + "\n")
        shutil.copy(HERE / "xsim" / "bi" / "docs" / "robot.md", sh / "docs" / "robot.md")
    names = lambda d: {json.dumps(json.loads(f.read_text())["Rigid"], sort_keys=True) for f in panel(d)}
    test = set()
    for v in json.loads((T / "splits.json").read_text())["splits"].values():
        test |= {json.dumps(json.loads(Path(f).read_text())["Rigid"], sort_keys=True) for f in v}
    if (names(priv / "dev") | names(priv / "f_pool") | names(priv / "example")) & test or names(priv / "dev") & names(priv / "f_pool"):
        raise SystemExit("development and test scenes overlap")
    man = dict(task=task_name, replicate=rep, dev_scenes=[f.name for f in panel(priv / "dev")], observation=cfg["observation"],
               shared={str(f.relative_to(sh)): __import__("hashlib").sha256(f.read_bytes()).hexdigest()[:16] for f in sorted(sh.rglob("*")) if f.is_file()})
    (R / "prepared.json").write_text(json.dumps(man, indent=1))
    return R


def recheck(task, base, rep=0, jobs=12, log=print):
    """Every frozen scene set of the task, run again with the sweeping reference under the evaluator AS IT IS NOW (run this
    after any change of the judge). -> <base>/judge_recheck.json: per set, how many the reference solves; the
    evaluator's hash."""
    from xsim import target
    base = Path(base)
    sets = {"dev": base / f"rep{rep}" / "private" / "dev", "f_pool": base / f"rep{rep}" / "private" / "f_pool", "example": base / f"rep{rep}" / "private" / "example"}
    for d in sorted((base / "test_private").iterdir()):
        if d.is_dir() and not d.name.endswith("_base"):
            sets[d.name] = d

    def one(f):
        r = _ms([HERE / "sweep_blocks" / "reference_sweep.py", f, "--quiet"], timeout=900)
        l = next((x for x in r.stdout.splitlines() if x.startswith("REFERENCE ")), None)
        d = json.loads(l[10:]) if l else dict(solved=False, error=r.stderr[-200:])
        return dict(scene=Path(f).name, solved=bool(d.get("solved")), robot_s=d.get("robot_s"), cubes_in=d.get("cubes_in"))
    out = dict(evaluator_sha=target.evaluator_sha(task), made=time.strftime("%Y-%m-%d %H:%M"), sets={})
    for name, d in sets.items():
        with ThreadPoolExecutor(jobs) as ex:
            res = list(ex.map(one, panel(d)))
        out["sets"][name] = dict(scenes=len(res), solved=sum(r["solved"] for r in res), results=res)
        log(f"  {name}: the reference solves {out['sets'][name]['solved']} of {len(res)}")
    (base / "judge_recheck.json").write_text(json.dumps(out, indent=1))
    return out
