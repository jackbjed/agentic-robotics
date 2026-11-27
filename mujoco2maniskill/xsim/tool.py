"""The one shell command of a development session (xsim/bin/xsim-tool SUB ...). The session is the launcher's
XSIM_SESSION; the caller must stand in that session's workspace. Prints the answer; never a traceback or a path of the
harness. What the session may do: xsim/session.py."""
import fcntl
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv):
    d = Path(os.environ.get("XSIM_SESSION") or "/nonexistent")
    try:
        if not (d / "state.json").is_file():
            print("this command works only inside a session"); return 2
        st = json.loads((d / "state.json").read_text())
        if Path.cwd().resolve() != (Path(st["root"]) / "workspace").resolve():
            print("this command works only from the session's workspace"); return 2
        from xsim.session import Session
        with open(d / ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            print(Session(d).command(list(argv)))
        return 0
    except Exception:
        try:
            with open(d / "tool_errors.log", "a") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        print("the command failed (an error of the harness, not yours): try again, or go on otherwise")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
