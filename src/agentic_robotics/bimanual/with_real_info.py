"""with_real_info: a from-nothing session with worlds (improve_cc.CodeSession, the Claude Code agent) that is given,
at its start, a TEXT about the real robot: what it is and how it answers a program's commands (the text a session
continued with real episodes gets with them). Nothing else differs from a from-nothing session: the same brief, the
same task text, the same starting point (no program, no world, the simulated empty table), the same sub-commands. No
real episode, no real image and nothing about the real objects is given.

    bin/ar-with-real-info brief --ask "..." --info FILE [--diff]
    bin/ar-with-real-info run   --ask "..." --info FILE --out runs/bimanual/NAME
                                [--budget-usd 50] [--max-hours 6] [--episode-s 600] [--model opus] [--effort xhigh] [--resume]

The standard sessions (improve_cc.py run) are not changed by this module.
"""
import argparse
import difflib
import json
import sys
from pathlib import Path

from .improve_cc import CodeSession, EFFORT, MODEL, WORKSPACES, run_session

KIND = "with_real_info"

# The brief's one addition (everything else is improve_cc's brief of a from-nothing session, word for word).
PARA = """
The real robot. The skill is to run on the real robot, where an operator watches each episode and gives its verdict.
What that robot is, and how it answers a program's commands:

{info}

You cannot run anything on the real robot in this session: the practice is in the simulator. Where the real robot
behaves differently from the simulator, the simulator does not show it.
"""


class InfoSession(CodeSession):
    """improve_cc.CodeSession of a from-nothing task, given the text about the real robot (the file `info`)."""

    def __init__(self, out, ask, budget_usd, info=None, **kw):
        sf = Path(out).resolve() / "state.json"
        st0 = json.loads(sf.read_text()) if sf.exists() else {}
        self.info_text = st0.get("real_info_text") or Path(info).read_text().strip()   # (kept in the state: the practice command reads it there)
        if not self.info_text:
            raise ValueError("--info: an empty text")
        if kw.get("real") or kw.get("continue_from"):
            raise ValueError("a with_real_info session starts from nothing: no --continue-from, no --real-feedback")
        super().__init__(out, ask, budget_usd, **kw)
        for key, now in (("kind", KIND), ("real_info_text", self.info_text)):
            if self.state.setdefault(key, now) != now:
                raise ValueError(f"this session was started with another {key}")
        self._keep()

    def _instructions(self, kind):
        t = super()._instructions(kind)
        b = "\nNo real episode of this task exists yet"
        if t.count(b) != 1:
            raise ValueError("the brief has changed: with_real_info's place is not found")
        return t.replace(b, PARA.format(info=self.info_text) + b)

    def base_instructions(self):
        return CodeSession._instructions(self, "grippers")

    def finish(self, agent=None):
        how = super().finish(agent)
        f = self.dir / "result.json"
        r = json.loads(f.read_text())
        r["kind"] = KIND
        f.write_text(json.dumps(r, indent=1) + "\n")
        return how


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "brief"):
        p = sub.add_parser(name)
        p.add_argument("--ask", help="the skill to make: the task's text")
        p.add_argument("--info", help="the text about the real robot (a file)")
        p.add_argument("--out", required=name == "run", help="the session's record directory (runs/bimanual/NAME)")
        p.add_argument("--budget-usd", type=float, default=50.0); p.add_argument("--episode-s", type=float)
        p.add_argument("--max-hours", type=float, default=6.0)
        p.add_argument("--model", default=MODEL); p.add_argument("--effort", default=EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
        p.add_argument("--no-segmenter", action="store_true")
        p.add_argument("--root", help=f"the session's directory outside the checkouts (default {WORKSPACES}/NAME)")
        p.add_argument("--resume", action="store_true"); p.add_argument("--agent-cmd", help="tests: a stand-in for the claude CLI")
        p.add_argument("--account"); p.add_argument("--diff", action="store_true", help="brief: the diff against a from-nothing session's brief")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    log = lambda *x: print(*x, flush=True)
    if a.cmd == "brief":
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            s = InfoSession(Path(t) / "out" / (Path(a.out).name if a.out else "NAME"), a.ask, a.budget_usd, info=a.info, root=Path(t) / "root" / "NAME",
                            episode_s=a.episode_s, segmenter=not a.no_segmenter, log=lambda *x: None)
            mine, theirs = s.instr.replace(str(s.cmd), "PRACTICE"), s.base_instructions().replace(str(s.cmd), "PRACTICE")
            if a.diff:
                sys.stdout.writelines(difflib.unified_diff(theirs.splitlines(True), mine.splitlines(True), "from_nothing/instructions.md", f"{KIND}/instructions.md"))
            else:
                print(mine + "\n\n===== THE FIRST MESSAGE =====\n\n" + s.first_message())
        return 0
    out = Path(a.out)
    if (out / "state.json").exists() != a.resume:
        ap.error("--out holds a session: --resume goes on with it" if not a.resume else "--resume: no session in --out")
    if a.resume:
        st0 = json.loads((out / "state.json").read_text())
        s = InfoSession(out, st0.get("ask"), st0.get("budget_usd") or a.budget_usd, episode_s=st0.get("episode_s"), segmenter=bool(st0.get("segmenter", True)),
                        max_hours=a.max_hours, log=log)
    else:
        if not a.ask or not a.info:
            ap.error("--ask and --info")
        s = InfoSession(out, a.ask, a.budget_usd, info=a.info, root=a.root, episode_s=a.episode_s, segmenter=not a.no_segmenter, max_hours=a.max_hours,
                        model=a.model, effort=a.effort, log=log)
        s.state.update(budget_usd=float(a.budget_usd), segmenter=not a.no_segmenter, account=a.account)
        s._keep()
    env = None
    if a.agent_cmd:
        import os
        env = dict(PATH=os.environ.get("PATH", ""), HOME=str(s.dir / "claude" / "home"), AR_CC_SESSION=str(s.dir), AR_CC_PRACTICE=s.cmd,
                   **{k: v for k, v in os.environ.items() if k.startswith("FAKE_")})
    run_session(s, s.state["model"], s.effort, resume=a.resume, claude=a.agent_cmd, env=env, log=log)
    print(json.dumps(dict(ended=s.state["ended"], stopped=None if s.state["ended"] else "unfinished", submitted=s.state["submitted"],
                          turns=s.state["turn"], spent_usd=round(s.spent(), 4))))
    return 0 if s.state["ended"] else 1


if __name__ == "__main__":
    sys.exit(main())
