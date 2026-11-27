"""Read-only audit of a forked experiment root: for every target submission made so far, was each episode's accepted
result written by THAT submission (after it started), and what older files lie in the same folders?"""
import json, sys, time
from pathlib import Path
R = Path(sys.argv[1])
fork_t = json.loads((R / "FORKED_FROM.json").read_text())["made"]
fork_ts = time.mktime(time.strptime(fork_t, "%Y-%m-%d %H:%M"))
tot = dict(episodes=0, stale_result=0, harness_errors=0, stale_files=0, fresh_files=0)
for c in "BCD":
    sd = R / c
    if not (sd / "state.json").exists():
        continue
    st = json.loads((sd / "state.json").read_text()); t0 = st["t0"]
    for r in st["rounds"]:
        k = r["round"]; priv = R / "private" / "rounds" / c / f"round_{k}"
        rec = json.loads((sd / "rounds" / f"round_{k}" / "round.json").read_text()) if (sd / "rounds" / f"round_{k}" / "round.json").exists() else {}
        row = []
        for ep in sorted(priv.glob("ep_*")):
            f = ep / "result.json"; tot["episodes"] += 1
            res = json.loads(f.read_text()) if f.exists() else None
            stale = (not f.exists()) or f.stat().st_mtime < t0
            he = (ep / "harness_error.txt").exists() and (ep / "harness_error.txt").stat().st_mtime >= t0
            old = [p for p in ep.rglob("*") if p.is_file() and p.stat().st_mtime < t0]
            new = [p for p in ep.rglob("*") if p.is_file() and p.stat().st_mtime >= t0]
            tot["stale_result"] += stale; tot["harness_errors"] += he; tot["stale_files"] += len(old); tot["fresh_files"] += len(new)
            row.append(("S" if (res or {}).get("success") else "f") + ("!STALE" if stale else "") + ("!HERR" if he else "") + (f"(+{len(old)} old files)" if old else ""))
        print(c, f"round {k}: counted {r['successes']}/{r['episodes']} v{r['version']}", " ".join(row))
    # the rounds beyond what this session has made so far: copies of the source root's records
    left = sorted(p.name for p in (R / "private" / "rounds" / c).glob("round_*") if int(p.name.split("_")[1]) >= len(st["rounds"]))
    print(c, "  folders of rounds not (yet) made by this session:", left)
print("forked", fork_t, "|", tot)
