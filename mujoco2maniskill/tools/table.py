"""The table: every skill on the same 16 scenes of each world, in MuJoCo (the project's recorded evaluations, read
only) and in ManiSkill (out/eval/). -> out/eval/table.md and table.json.

    python3 tools/table.py
"world robot": the world's robot() wraps the robot, as in the MuJoCo evaluations; "scene only": the rig's robot as it
is (--plain-robot). "same": the scenes (of 16) on which MuJoCo and ManiSkill (world robot) give the same verdict."""
import glob, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
EVAL = HERE / "out" / (sys.argv[1] if len(sys.argv) > 1 else "eval")          # (python3 tools/table.py [eval folder under out/])
RUNS = HERE.parent / "runs" / "bimanual"
MJ = {("v38", "w16"): "cc_v38_in_cc_w16", ("v38", "w21"): "v38_in_w21_real_table", ("v38", "w25"): "v38_in_w25_real_table_2",
      ("v63", "w16"): "v63_in_w16_first_session", ("v63", "w21"): "v63_in_w21_real_table", ("v63", "w25"): "v63_in_w25_real_table_2",
      ("v80", "w16"): "v80_in_w16_first_session", ("v80", "w21"): "v80_in_w21_real_table", ("v80", "w25"): "v80_in_w25_real_table_2",
      ("v66fixed", "w16"): "fixed_v66_in_w16_first_session", ("v66fixed", "w21"): "fixed_v66_in_w21_real_table",
      ("v66fixed", "w25"): "fixed_v66_in_w25_real_table_2",
      ("v23img", "w16"): "img_v23_in_w16_first_session", ("v23img", "w21"): "img_v23_in_w21_real_table",
      ("v61episodes", "w16"): "episodes_v61_in_w16_first_session", ("v61episodes", "w21"): "episodes_v61_in_w21_real_table",
      ("v61episodes", "w25"): "episodes_v61_in_w25_real_table_2"}
NAMES = {"v38": "v38 (simulation only)", "v66fixed": "v66 (fixed world)", "v63": "v63 (round one)", "v80": "v80 (round two)",
         "v23img": "v23 (from image)", "v61episodes": "v61 (episodes only)"}
# files in which a label occurs more than once: the evaluation without truth is the one to compare with
PREFER = ("cross_no_truth",)


def mujoco():
    out = {}
    files = sorted(glob.glob(str(RUNS / "insert_*_eval" / "worlds" / "episodes.jsonl")) + glob.glob(str(RUNS / "insert_*_eval" / "cross_no_truth" / "episodes.jsonl")))
    for f in files:
        for l in open(f):
            r = json.loads(l)
            if r.get("label"):
                out.setdefault(r["label"], {})[int(r["seed"])] = bool(r.get("ok"))
    return out


def maniskill(name):
    f = EVAL / name / "episodes.jsonl"
    if not f.exists():
        return None
    return {int(r["seed"]): bool(r["success"]) for r in map(json.loads, open(f))}, \
        {int(r["seed"]): r for r in map(json.loads, open(f))}


def main():
    mj = mujoco()
    rows, md = [], ["| Skill | World | MuJoCo | ManiSkill, world robot | same verdict | ManiSkill, scene only |", "|---|---|---|---|---|---|"]
    for w in ("w16", "w21", "w25"):
        for s in ("v38", "v66fixed", "v63", "v80", "v23img", "v61episodes"):
            a = mj.get(MJ.get((s, w), ""))
            b = maniskill(f"{s}_{w}"); c = maniskill(f"{s}_{w}_plain")
            f = lambda d: "not run" if d is None else f"{sum(d.values())}/{len(d)}"
            same = None
            if a and b:
                seeds = sorted(set(a) & set(b[0])); same = sum(a[k] == b[0][k] for k in seeds)
            t = lambda x: None if x is None else round(sum(r["robot_s"] for r in x[1].values() if r["success"]) / max(sum(x[0].values()), 1), 1)
            rows.append(dict(skill=s, world=w, mujoco=None if a is None else sum(a.values()), maniskill=None if b is None else sum(b[0].values()),
                             same=same, maniskill_plain=None if c is None else sum(c[0].values()), n=16,
                             mujoco_fail=sorted(k for k, v in (a or {}).items() if not v), maniskill_fail=sorted(k for k, v in (b[0] if b else {}).items() if not v),
                             plain_fail=sorted(k for k, v in (c[0] if c else {}).items() if not v), robot_s=t(b), robot_s_plain=t(c)))
            md.append(f"| {NAMES[s]} | {w} | {f(a)} | {f(b and b[0])} | {'' if same is None else f'{same}/16'} | {'same robot' if w == 'w16' else f(c and c[0])} |")
    (EVAL / "table.md").write_text("\n".join(md) + "\n")
    (EVAL / "table.json").write_text(json.dumps(rows, indent=1))
    print("\n".join(md))


if __name__ == "__main__":
    main()
