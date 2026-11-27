"""How a program was run BEFORE the boundary: in the simulator's own process. Kept only for tests/test_boundary.py, which
shows with dummy state what that allowed and compares the two ways' results. No runner imports this."""
import contextlib
import linecache
import traceback


def run_inprocess(code, given, task_text, log):
    linecache.cache["program.py"] = (len(code), None, code.splitlines(True), "program.py")
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            ns = {"__name__": "program"}
            exec(compile(code, "program.py", "exec"), ns)
            ns["run"](given, task_text)
            return "returned"
        except Exception as e:
            if type(e).__name__ == "TimeUp":
                return f"time: {e}"
            traceback.print_exc(limit=-6)
            return f"error: {type(e).__name__}: {str(e)[:200]}"
        except SystemExit as e:
            traceback.print_exc(limit=-6)
            return f"error: {type(e).__name__}: {str(e)[:200]}"
