"""(development check) A task's target: a few configurations, the first look, a known program as a reference."""
import json, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(HERE))
from xsim import config, target, tasks
name, prog, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
cfg = config.load(); task = tasks.TASKS[name]
out = HERE / "out" / "xsim_devcheck" / name
t = time.time()
files = target.configs(task, n, 40500, out / "configs", cfg)
print("configs", [f.name for f in files], round(time.time() - t, 1), "s")
r = target._ms([HERE / "xsim" / "target_side.py", "look", "--scene", files[0], "--out", out / "look"]); print("look", r.returncode, r.stderr[-300:] if r.returncode else sorted(p.name for p in (out / "look").iterdir()))
if prog != "-":
    t = time.time()
    pub, priv = target.run_batch(prog, files, task, out / "private", out / "public", cfg)
    for p, q in zip(pub, priv):
        print("episode", q["episode"], "success", p["success"], "robot_s", p["robot_s"], "ended", str(p["ended"])[:60], "| private check", q.get("check"))
    print("batch", round(time.time() - t, 1), "s; public files:", sorted(str(x.relative_to(out / "public")) for x in (out / "public" / "ep_00").rglob("*") if x.is_file())[:12])
    print("--- public log tail ---"); print("\n".join((out / "public" / "ep_00" / "output.txt").read_text().splitlines()[-6:])[:900])
    txt = (out / "public" / "ep_00" / "output.txt").read_text()
    print("leaks:", {k: (k in txt) for k in ("TRUE", " NOW ", "w_cyl", "success()", "WORLD")})
