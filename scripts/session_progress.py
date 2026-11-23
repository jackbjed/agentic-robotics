"""How a self-improvement session's program got better: one line for each version it saved.

    python scripts/session_progress.py runs/bimanual/clutter_one_arm_astra_1123 [--md progress.md]

For each version (the copies are the run's versions/vNN.py): when it was saved (the session's turn, the dollars spent and
the minutes since the start, from session.jsonl), its length, what it was made from, the session's note, its practice
results by level (successes/episodes, mean robot time of the successes, mean model calls an episode; a scene run more
than once counts with its last result) and whether it was submitted. Reads the run's files only: free, and safe while
the session runs.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def load(run):
    run = Path(run)
    st = json.loads((run / "state.json").read_text())
    log = run / "session.jsonl"                                 # absent before the session's first turn
    rec = [json.loads(l) for l in log.read_text().splitlines() if l.strip()] if log.exists() else []
    turns = {r["turn"]: r for r in rec if r["kind"] == "turn"}
    t0 = min((r["t"] for r in turns.values()), default=0.0)
    saved, submitted = {}, []                                   # version -> the turn that saved it; (turn, version, final)
    for r in rec:
        if r["kind"] != "calls":
            continue
        for c in r["calls"]:
            m = re.match(r"saved as v(\d+) ", str(c.get("result", "")))
            if m:
                saved[int(m.group(1))] = r["turn"]
            m = re.match(r"v(\d+) is now your submitted result", str(c.get("result", "")))
            if m:
                submitted.append((r["turn"], int(m.group(1)), bool((c.get("args") or {}).get("final"))))
    return st, turns, t0, saved, submitted


def by_level(st, runs):
    """{level: (successes, episodes, mean robot time of the successes, mean model calls)}; without levels: {"": ...}."""
    out = {}
    for s, r in runs.items():
        out.setdefault((st.get("scenes") or {}).get(s, {}).get("level", ""), []).append(r)
    res = {}
    for l, rs in out.items():
        ok = [r[1] for r in rs if r[0]]
        calls = [r[2] for r in rs if len(r) > 2]
        res[l] = (len(ok), len(rs), sum(ok) / len(ok) if ok else None, sum(calls) / len(calls) if calls else None)
    return res


def rows(run):
    st, turns, t0, saved, submitted = load(run)
    out = []
    for v, m in enumerate(st["versions"]):
        at = dict(m.get("at") or {})
        t = turns.get(saved.get(v))                             # the turn whose calls saved it: spent and time at its reply
        if t:
            at.update(turn=t["turn"], usd=round(t.get("spent", at.get("usd", 0.0)), 2), min=round((t["t"] - t0) / 60, 1))
        out.append(dict(v=v, at=at, lines=m["lines"], base=m.get("base"), note=m["note"], levels=by_level(st, m["runs"]),
                        submitted=[(tn, f) for tn, sv, f in submitted if sv == v]))
    return st, turns, out


def _res(levels):
    parts = []
    for l in sorted(levels, key=lambda x: ("medium", "hard").index(x) if x in ("medium", "hard") else 9):
        ok, n, s, c = levels[l]
        extra = ", ".join(x for x in (f"{s:.0f} s" if s is not None else "", f"{c:.1f} calls" if c is not None else "") if x)
        parts.append(f"{l + ' ' if l else ''}{ok}/{n}" + (f" ({extra})" if extra else ""))
    return "; ".join(parts) or "not run"


def table(run):
    st, turns, R = rows(run)
    last = max(turns.values(), key=lambda r: r["turn"]) if turns else None
    head = (f"{Path(run).name}: {len(R)} versions, {len(turns)} turns"
            + (f", ${last.get('spent', 0.0):.2f} spent" if last else "")
            + f", submitted {'v' + str(st['submitted']) if st.get('submitted') is not None else 'nothing'}"
            + (f", ended ({st['ended']})" if st.get("ended") else ", running or stopped"))
    lines = [head, "", "| version | turn | $ spent | min | lines | from | practice (last result of each scene) | submitted | note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in R:
        a = r["at"]
        sub = ", ".join(f"turn {t}{' (final)' if f else ''}" for t, f in r["submitted"])
        lines.append(f"| v{r['v']} | {a.get('turn', '')} | {a.get('usd', '')} | {a.get('min', '')} | {r['lines']} | "
                     f"{'v' + str(r['base']) if r['base'] is not None else ''} | {_res(r['levels'])} | {sub} | "
                     f"{' '.join(r['note'].split()).replace('|', '/')[:110]} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--md", help="also write the table to this file")
    a = ap.parse_args()
    t = table(a.run)
    print(t)
    if a.md:
        Path(a.md).write_text(t + "\n")


if __name__ == "__main__":
    main()
