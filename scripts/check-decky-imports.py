#!/usr/bin/env python3
"""Check the backend only imports what Decky's frozen Python ships.

Decky runs plugin backends on its own PyInstaller-frozen Python, which only
bundles the stdlib modules Decky itself uses (no xml.etree, shlex, ...). If
main.py imports anything else, the backend never starts and the panel hangs.

Two checks, against every tests/decky_bundled_modules-*.txt list:

* static: every import statement in main.py and backend/*.py, including ones
  inside functions, plus importlib.import_module("...")/__import__("...")
  calls with literal names;
* runtime: import main.py and every backend module in a fresh interpreter
  that refuses any stdlib module the list does not contain. This only runs
  for the list whose Python version matches the interpreter (pass --python
  to use another interpreter, e.g. python3.11 for the v3.2.10 list).

Usage:
    python3 scripts/check-decky-imports.py                 # static, all lists; runtime where versions match
    python3 scripts/check-decky-imports.py --python python3.11 --python python3.14
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = {"backend"}
# Provided by Decky at runtime, not by the stdlib.
PROVIDED = {"decky"}
# Compiled into Decky's interpreter (not in the PyInstaller archive listing).
KNOWN_BUILTINS = {"pwd", "sys", "builtins", "posix", "_io", "_thread", "_signal", "time", "errno",
                  "itertools", "marshal", "gc", "_abc", "_codecs", "_collections", "_functools",
                  "_imp", "_operator", "_sre", "_stat", "_string", "_symtable", "_tokenize",
                  "_tracemalloc", "_warnings", "_weakref", "atexit", "faulthandler", "_locale"}
VERSION_LINE = re.compile(r"\(Python (\d+)\.(\d+)")


def sources() -> list[Path]:
    return [PROJECT / "main.py", *sorted((PROJECT / "backend").glob("*.py"))]


def module_lists() -> list[Path]:
    found = sorted((PROJECT / "tests").glob("decky_bundled_modules-*.txt"))
    if not found:
        raise SystemExit("No tests/decky_bundled_modules-*.txt lists found")
    return found


def load_list(path: Path) -> tuple[set[str], tuple[int, int] | None]:
    text = path.read_text()
    match = VERSION_LINE.search(text)
    version = (int(match.group(1)), int(match.group(2))) if match else None
    names = {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}
    return names, version


def imports_in(path: Path) -> list[tuple[int, str]]:
    """Every absolute module name a file can import, with its line number."""
    tree = ast.parse(path.read_text(), str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue  # relative imports stay inside backend/
            found.append((node.lineno, node.module))
            # "from xml import etree" imports the submodule xml.etree.
            found.extend((node.lineno, f"{node.module}.{alias.name}") for alias in node.names
                         if alias.name != "*" and _is_stdlib_submodule(f"{node.module}.{alias.name}"))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name in ("import_module", "__import__") and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.append((node.lineno, node.args[0].value))
    return found


def _is_stdlib_submodule(name: str) -> bool:
    """True when "from pkg import x" names a stdlib submodule pkg.x (e.g. xml.etree)."""
    if name.split(".")[0] not in sys.stdlib_module_names:
        return False
    for path in module_lists():
        if name in load_list(path)[0]:
            return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT))
    except ValueError:
        return str(path)


def static_problems(allowed: set[str]) -> list[str]:
    allowed = allowed | set(sys.builtin_module_names) | KNOWN_BUILTINS
    problems = []
    for path in sources():
        for line, name in imports_in(path):
            top = name.split(".")[0]
            if name == "__future__" or top in LOCAL_PACKAGES or top in PROVIDED:
                continue
            if top not in sys.stdlib_module_names:
                problems.append(f"{_display(path)}:{line} imports third-party {name} "
                                "(vendor pure-Python code in py_modules/ instead)")
                continue
            parts = name.split(".")
            for depth in range(1, len(parts) + 1):
                prefix = ".".join(parts[:depth])
                if prefix not in allowed:
                    problems.append(f"{_display(path)}:{line} imports {name} ({prefix} is not bundled)")
                    break
    return problems


# Runs in a fresh "-I -S" interpreter so only the bare startup modules are
# loaded. Uses nothing but builtins to install the blocker.
RUNTIME = r"""
import sys
allowed = set(sys.argv[2].split(","))
allowed |= set(sys.builtin_module_names)
blocked = []

class Block:
    @staticmethod
    def find_spec(name, path=None, target=None):
        top = name.split(".")[0]
        if top in sys.stdlib_module_names and name not in allowed:
            blocked.append(name)
            raise ModuleNotFoundError(f"No module named {name!r} (not bundled in Decky's Python)", name=name)
        return None

sys.meta_path.insert(0, Block)
import types
decky = types.ModuleType("decky")
noop = lambda *args, **kwargs: None
decky.logger = types.SimpleNamespace(info=noop, warning=noop, error=noop, exception=noop, debug=noop)
decky.DECKY_USER_HOME = "/tmp"
decky.DECKY_USER = "deck"
decky.DECKY_PLUGIN_LOG_DIR = "/tmp"
decky.DECKY_PLUGIN_SETTINGS_DIR = "/tmp"
decky.DECKY_PLUGIN_RUNTIME_DIR = "/tmp"
sys.modules["decky"] = decky
project = sys.argv[1]
sys.path.insert(0, project)
import importlib.util
spec = importlib.util.spec_from_file_location("deckclip_main", project + "/main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert hasattr(module, "Plugin"), "main.py has no Plugin class"
for name in sys.argv[3].split(","):
    importlib.import_module(name)
print("LOADED OK")
"""


def runtime_problem(python: str, allowed: set[str]) -> str | None:
    backend = ",".join(f"backend.{path.stem}" for path in sorted((PROJECT / "backend").glob("*.py"))
                       if path.stem != "__init__")
    result = subprocess.run([python, "-I", "-S", "-c", RUNTIME, str(PROJECT), ",".join(sorted(allowed)), backend],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or "LOADED OK" not in result.stdout:
        return (result.stderr.strip().splitlines() or ["no output"])[-1]
    return None


def interpreter_version(python: str) -> tuple[int, int] | None:
    path = shutil.which(python)
    if path is None:
        return None
    out = subprocess.run([path, "-I", "-S", "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return None
    major, minor = out.stdout.split()
    return int(major), int(minor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", action="append", default=[],
                        help="extra interpreter(s) for the runtime check (default: this one)")
    parser.add_argument("--require-runtime", action="store_true",
                        help="fail if no interpreter matches any list's Python version")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    interpreters = {}
    for python in [sys.executable, *args.python]:
        version = interpreter_version(python)
        if version is not None:
            interpreters.setdefault(version, shutil.which(python))
    report = {"static": {}, "runtime": {}}
    failed = False
    ran_runtime = False
    for path in module_lists():
        allowed, version = load_list(path)
        problems = static_problems(allowed)
        report["static"][path.name] = problems
        failed |= bool(problems)
        python = interpreters.get(version) if version else None
        if python:
            ran_runtime = True
            problem = runtime_problem(python, allowed | KNOWN_BUILTINS)
            report["runtime"][path.name] = problem or "ok"
            failed |= problem is not None
        else:
            report["runtime"][path.name] = f"skipped (no Python {version[0]}.{version[1]} given)" if version else "skipped"
    if args.require_runtime and not ran_runtime:
        failed = True
        report["runtime"]["error"] = "no interpreter matched any module list"
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for name, problems in report["static"].items():
            print(f"static  {name}: {'ok' if not problems else 'FAIL'}")
            for problem in problems:
                print(f"    {problem}")
        for name, status in report["runtime"].items():
            print(f"runtime {name}: {status}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
