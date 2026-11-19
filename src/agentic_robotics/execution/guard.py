"""Skills written from scratch (runs without the robot skill library): the program's own code may not import the
platform's or the vendored robot code's modules (the skill library: clutter_bench, task_box, task_fast, track_run,
astra_skills_real, ...). The robot (ctx.robot) still runs on that code; only the program's own imports are refused.

  install(skills_root)      at run time, in the skill host: an import from a module under skills_root of one of
                            those names raises ImportError
  violations(code_dir)      statically, on a candidate's code (the learning loop, practice summaries): those imports,
                            and reaching around them (sys.modules, importlib, __import__, __globals__, inspect, gc, the
                            platform's file paths) -> [str]"""
from __future__ import annotations

import ast
import builtins
import importlib
import sys
from pathlib import Path

from .. import paths


def blocked_names():
    names = {"agentic_robotics"}
    for p in paths.VENDOR.rglob("*.py"):
        if p.stem != "__init__":
            names.add(p.stem)
    names.discard("conftest")
    return names


def _from_program(g, root):
    f = (g or {}).get("__file__") or ""
    try:
        return Path(f).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def install(skills_root):
    root = Path(skills_root).resolve()
    names = blocked_names()
    orig_import, orig_im = builtins.__import__, importlib.import_module

    def refuse(name):
        raise ImportError(f"'{name}' is not available to programs in this run: the robot skill library is withheld "
                          f"(skills from scratch, docs/skill_api.md); use ctx.robot, ctx.segmenter, ctx.vlm, ctx.tools")

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name.split(".")[0] in names and _from_program(globals, root):
            refuse(name)
        return orig_import(name, globals, locals, fromlist, level)

    def guarded_import_module(name, package=None):
        if not name.startswith(".") and name.split(".")[0] in names and _from_program(sys._getframe(1).f_globals, root):
            refuse(name)
        return orig_im(name, package)

    builtins.__import__ = guarded_import
    importlib.import_module = guarded_import_module


SUSPECT_ATTRS = {"__globals__": "__globals__", "__builtins__": "__builtins__", "__subclasses__": "__subclasses__",
                 "f_globals": "frame globals", "f_back": "frame globals"}
SUSPECT_MODULES = {"importlib", "inspect", "gc", "ctypes", "runpy", "pkgutil", "zipimport", "marshal"}
SUSPECT_TEXT = ("vendor", "panda_twin", "agentic_robotics", "/src/")


def violations(code_dir):
    names = blocked_names()
    out = []
    for p in sorted(Path(code_dir).rglob("*.py")):
        rel = p.relative_to(code_dir)
        try:
            tree = ast.parse(p.read_text(), str(rel))
        except SyntaxError as e:
            out.append(f"{rel}: syntax error ({e.msg})")
            continue
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mods = [n.module]
            for m in mods:
                top = m.split(".")[0]
                if top in names:
                    out.append(f"{rel}:{n.lineno}: imports {m} (the robot code / platform)")
                elif top in SUSPECT_MODULES:
                    out.append(f"{rel}:{n.lineno}: imports {m}")
            if isinstance(n, ast.Attribute):
                if n.attr in SUSPECT_ATTRS:
                    out.append(f"{rel}:{n.lineno}: uses {SUSPECT_ATTRS[n.attr]}")
                elif n.attr == "modules" and isinstance(n.value, ast.Name) and n.value.id == "sys":
                    out.append(f"{rel}:{n.lineno}: uses sys.modules")
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "__import__":
                out.append(f"{rel}:{n.lineno}: calls __import__()")
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and any(t in n.value for t in SUSPECT_TEXT):
                out.append(f"{rel}:{n.lineno}: refers to the platform's files ({n.value[:60]!r})")
    return out
