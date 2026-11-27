"""The target environment as the experiments use it (ManiSkill; run by the harness in the project's environment, which
starts the ManiSkill jobs as sub-processes). Everything here is the EVALUATOR's side: no agent can call it or read
what it writes, except the agent-visible payload of a development round, which the session command copies.

  configs      a task's target configurations: its hidden world -> scene files (tools/export_scene.py: exported with
               ideal sensors, so the cameras' true and reported poses agree), each admitted by the frozen rules
  ood          a final out-of-distribution split from admitted in-distribution configurations
  run_batch    one frozen program on a list of configurations -> the private records and the agent-visible payload"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parent
MS_PY = "<HOME>/micromamba/envs/robomme/bin/python"
SRC = PROJECT.parent / "agentic-robotics-ownreal" / "src"          # (the checkout with the worlds' code; read only)
FRAMES = 6                                                      # frames of an episode's video given back


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _ms(args, timeout=1800):
    env = dict(os.environ, PYTHONNOUSERSITE="1", CUDA_VISIBLE_DEVICES=os.environ.get("M2M_GPU", "0"))
    return subprocess.run([MS_PY, "-I"] + [str(a) for a in args], capture_output=True, text=True, env=env, timeout=timeout)


def _json(r):
    for l in reversed(r.stdout.splitlines()):
        if l.startswith("XSIM_JSON "):
            return json.loads(l[10:])
    raise RuntimeError("the target job gave no result:\n" + r.stderr[-1500:])


def configs(task, n, base_seed, out, cfg, log=print, reference=None):
    """n admitted configurations of the task's target world, seeds from base_seed on -> their scene files.
    Admission: the frozen rules (the scene builds, settles, its things are in the working area, the task is not done
    at the start) and, with `reference` (a program file), ALSO that the reference program solves the configuration in
    the target, by the target's own judge: such a set is SHOWN SOLVABLE. A configuration the reference does not solve
    is set aside (unsolved_<seed>.json), not called unsolvable."""
    sys.path.insert(0, str(SRC)); sys.path.insert(0, str(HERE / "tools"))
    os.environ.setdefault("MUJOCO_GL", "egl")
    import cv2
    import export_scene
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    rules = out / "rules.json"; rules.write_text(json.dumps(cfg["validation"]))
    files, book, refs, seed, last, batch = [], [], [], int(base_seed), int(base_seed) + 20 * n + 50, 0

    def draw(k):                                                    # the next k configurations admitted by the rules
        nonlocal seed
        got = []
        while len(got) < k and seed < last:
            try:
                spec, tex = export_scene.export(Path(task["target_world"]).resolve(), seed, str(SRC), "ideal")
            except Exception as e:                                  # (the world found no layout for this seed)
                book.append(dict(seed=seed, admitted=False, why=[f"no scene: {type(e).__name__}: {str(e)[:120]}"])); seed += 1
                continue
            f = out / f"{seed}.json"
            if tex is not None:
                cv2.imwrite(str(out / f"{seed}_table.png"), tex["rgb"][..., ::-1])
                spec["table_texture"] = dict(file=f"{seed}_table.png", repeat=tex["repeat"], uniform=tex["uniform"])
            f.write_text(json.dumps(spec))
            v = _json(_ms([HERE / "xsim" / "target_side.py", "validate", "--scene", f, "--rules", rules, "--required", *task["required"]]))
            book.append(v)
            if v["admitted"]:
                got.append(f)
            else:
                f.rename(out / f"rejected_{seed}.json")
            log(f"  config {seed}: {'admitted' if v['admitted'] else 'REJECTED: ' + '; '.join(v['why'])[:120]}")
            seed += 1
        return got
    while len(files) < n and seed < last:
        need = n - len(files)
        got = draw(need if reference is None else need + max(2, need // 3))
        if reference is None:
            files += got
            continue
        _, priv = run_batch(reference, got, task, out / "reference_runs" / f"batch_{batch}", None, cfg, log=log); batch += 1
        for f, r in zip(got, priv):
            ok = bool(r.get("success")) and not r.get("harness_error")
            refs.append(dict(seed=int(f.stem), solved=ok, robot_s=r.get("robot_s"), ended=str(r.get("ended"))[:80], check=r.get("check"),
                             kept=ok and len(files) < n))
            log(f"  config {f.stem}: the reference {'SOLVED it' if ok else 'did not solve it'} ({r.get('robot_s')} s of robot time)")
            if ok and len(files) < n:
                files.append(f)
            else:
                f.rename(out / (("spare_" if ok else "unsolved_") + f.name))
    label = "feasibility-screened (not shown solvable)" if reference is None else \
        "shown solvable: each configuration was solved in the target by the reference program " + sha(reference)
    (out / "validation.json").write_text(json.dumps(dict(rules=cfg["validation"], label=label, reference=str(reference) if reference else None,
                                                         reference_runs=refs, book=book), indent=1))
    if len(files) < n:
        raise RuntimeError(f"only {len(files)} of {n} configurations were admitted")
    return files


def ood(task, split, id_files, out, seed=0):
    """A final out-of-distribution split: each in-distribution configuration with ONE kind of change (the task's)."""
    import numpy as np
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    v = task["ood"][split]; rng = np.random.default_rng(seed); files = []
    for f in id_files:
        s = json.loads(Path(f).read_text()); pre = v.get("bodies", "w_")
        mine = [b for b in s["bodies"] if b["name"].startswith(pre)]
        if v.get("recolor"):                                        # appearance: every thing's and the table's colour
            for b in s["bodies"]:
                if b["name"].startswith("w_"):
                    c = rng.uniform(0.1, 0.95, 3).tolist()
                    for g in b["geoms"]:
                        g["rgba"] = c + [1.0]
            for g in s["world_geoms"]:
                if g.get("name") == "table_top":
                    g["rgba"] = rng.uniform(0.2, 0.9, 3).tolist() + [1.0]
        if v.get("size_scale"):
            S, tz = float(v["size_scale"]), float(s.get("table_z", 0.0))
            for b in mine:
                for g in b["geoms"]:
                    g["size"] = [x * S for x in g["size"]]; g["pos"] = [x * S for x in g["pos"]]; g["friction"][2] *= S
                b["pos"][2] = tz + (b["pos"][2] - tz) * S
                b["mass"] *= S ** 3; b["inertia"] = [x * S ** 5 for x in b["inertia"]]; b["ipos"] = [x * S for x in b["ipos"]]
        if v.get("platform_mm"):                                    # heights: the thing stands on a fixed block
            h = float(v["platform_mm"]) / 1000.0
            for b in mine:
                r = max(max(abs(g["pos"][0]) + g["size"][0], abs(g["pos"][1]) + (g["size"][1] if g["type"] == "box" else g["size"][0])) for g in b["geoms"]) + 0.03
                s["bodies"].append(dict(name=b["name"] + "_platform", free=False, pos=[b["pos"][0], b["pos"][1], float(s.get("table_z", 0.0)) + h / 2],
                                        quat=[1, 0, 0, 0], mass=1.0, ipos=[0, 0, 0], iquat=[1, 0, 0, 0], inertia=[1e-3] * 3, members=[], damping=0.0,
                                        geoms=[dict(type="box", size=[r, r, h / 2], pos=[0, 0, 0], quat=[1, 0, 0, 0], rgba=[0.5, 0.5, 0.52, 1.0], col=True,
                                                    friction=[0.8, 0.005, 0.0001], name="", group=0)]))
                b["pos"][2] += h
        if v.get("friction") is not None:
            for b in mine:
                for g in b["geoms"]:
                    g["friction"][0] = float(v["friction"])
        if v.get("table_friction") is not None:
            for g in s["world_geoms"]:
                if g.get("name") == "table_top":
                    g["friction"][0] = float(v["table_friction"])
        if v.get("mass_scale"):
            for b in mine:
                b["mass"] *= float(v["mass_scale"]); b["inertia"] = [x * float(v["mass_scale"]) for x in b["inertia"]]
        s["ood"] = dict(split=split, change=v)
        g = out / Path(f).name; g.write_text(json.dumps(s)); files.append(g)
        t = s.get("table_texture")
        if t and not (out / t["file"]).exists():
            shutil.copy(Path(f).parent / t["file"], out / t["file"])
    return files


def public_payload(private, public, task_text):
    """An episode's private record -> what a development agent may see of it."""
    sys.path.insert(0, str(HERE))
    import cv2
    from run_episode import public_log
    private, public = Path(private), Path(public); public.mkdir(parents=True, exist_ok=True)
    r = json.loads((private / "result.json").read_text()) if (private / "result.json").exists() else None
    ep = dict(success=bool(r and r["success"]), ended=(r["ended"] if r else "the episode's process failed (an error of the harness or the program)"),
              robot_s=r and r["robot_s"], wall_s=r and r["wall_s"], commands=r and r["commands"], segmentation_calls=r and r["segment_calls"],
              safety_stops=r and r["safety_stops"])
    (public / "episode.json").write_text(json.dumps(ep, indent=1))
    if (private / "output.txt").exists():
        (public / "output.txt").write_text(public_log((private / "output.txt").read_text(errors="replace"), " ".join(task_text.split())))
    if (private / "shown").is_dir():
        shutil.copytree(private / "shown", public / "shown", dirs_exist_ok=True)
    v = private / "video.mp4"
    if v.exists():
        cap = cv2.VideoCapture(str(v)); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        (public / "frames").mkdir(exist_ok=True)
        for k in range(FRAMES):
            i = min(int(round(k * (n - 1) / max(FRAMES - 1, 1))), max(n - 1, 0))
            cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, im = cap.read()
            if ok:
                cv2.imwrite(str(public / "frames" / f"frame_{k}_t{i / fps:05.1f}s.jpg"), im, [cv2.IMWRITE_JPEG_QUALITY, 85])
        cap.release()
    return ep


def evaluator_files(task):
    """The files that ARE the target's judge and the target's physics, for a task (hashed: see evaluator_sha)."""
    if task.get("rig") == "x5":
        A = HERE / "assets" / "robodojo_sweep"
        return [HERE / "sweep_blocks" / "env.py", HERE / "sweep_blocks" / "program_api.py", HERE / "sweep_blocks" / "looks.py",
                HERE / "xsim" / "bi" / "target_run.py", A / "broom_shovel.json", A / "small_cube.json", HERE / "x5" / "spec.py"]
    return [Path(task["target_world"]), HERE / "run_episode.py", HERE / "m2m" / "env.py", HERE / "m2m" / "rig.py", HERE / "m2m" / "api.py"]


def evaluator_sha(task):
    h = hashlib.sha256()
    for f in evaluator_files(task):
        h.update(f.name.encode()); h.update(Path(f).read_bytes())
    return h.hexdigest()[:16]


def panel_files(task, d, n=None):
    """A set of target scenes in its fixed order."""
    f = sorted(Path(d).glob("ep_*.json" if task.get("rig") == "x5" else "[0-9]*.json"))
    return f[:int(n)] if n else f


def freeze(task, d):
    """A set of target scenes becomes read-only, with a manifest of its files' hashes."""
    d = Path(d)
    man = [dict(file=f.name, sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in panel_files(task, d)]
    if (d / "panel_manifest.json").exists():
        (d / "panel_manifest.json").chmod(0o644)
    (d / "panel_manifest.json").write_text(json.dumps(man, indent=1))
    for f in d.rglob("*"):
        if f.is_file():
            f.chmod(0o444)
    return man


def checked_panel(task, d, n=None):
    """The scenes of a frozen set, each checked against the set's manifest -> (files, the panel's identity)."""
    d = Path(d); files = panel_files(task, d, n)
    want = {m["file"]: m["sha256"] for m in json.loads((d / "panel_manifest.json").read_text())}
    h = hashlib.sha256()
    for f in files:
        got = hashlib.sha256(f.read_bytes()).hexdigest()
        if got != want.get(f.name):
            raise RuntimeError(f"the target scene {f.name} does not match its panel's manifest")
        h.update(f.name.encode()); h.update(got.encode())
    return files, h.hexdigest()[:16]


def run_batch(program, scene_files, task, private_dir, public_dir, cfg, jobs=None, log=print):
    """One frozen program on the configurations (fresh state each) -> (the agent-visible episodes, the private results)."""
    if task.get("rig") == "x5":
        from xsim.bi import backend
        return backend.run_batch(program, scene_files, task, private_dir, public_dir, cfg, jobs=jobs, log=log)
    program, private_dir = Path(program), Path(private_dir); private_dir.mkdir(parents=True, exist_ok=True)
    txt = private_dir / "task.txt"; txt.write_text(task["text"])

    def one(k_f):
        k, f = k_f
        d = private_dir / f"ep_{k:02d}"
        t = time.time()
        try:
            r = _ms([HERE / "run_episode.py", "--scene", f, "--program", program, "--out", d, "--plain-robot", "--min-depth", "0", "--video",
                     "--time-limit", task["episode_s"], "--task-text-file", txt], timeout=int(4 * task["episode_s"]) + 600)
            err = None if r.returncode == 0 else r.stderr[-800:]
        except subprocess.TimeoutExpired:
            err = "the episode's process was stopped (wall-clock limit)"
        if err:
            (d).mkdir(parents=True, exist_ok=True); (d / "harness_error.txt").write_text(str(err))
        res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else dict(success=False, ended="process failed", check=None)
        res.update(episode=k, config=str(f), config_seed=int(Path(f).stem), wall_total_s=round(time.time() - t, 1), harness_error=err)
        pub = public_payload(d, Path(public_dir) / f"ep_{k:02d}", task["text"]) if public_dir else None
        return res, pub
    with ThreadPoolExecutor(max_workers=int(jobs or cfg["target_jobs"])) as ex:
        out = list(ex.map(one, list(enumerate(scene_files))))
    return [p for _, p in out], [r for r, _ in out]
