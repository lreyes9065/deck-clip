"""Decky's frozen Python only ships some stdlib modules; check imports and plugin.json.

The 0.15.1 Deck hang was a backend import of xml.etree, which Decky's
PluginLoader does not bundle. These tests run scripts/check-decky-imports.py
against the module lists of the oldest and newest Decky releases supported.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


checker = _load("check_decky_imports", "check-decky-imports.py")
plugin_lint = _load("lint_plugin_json", "lint-plugin-json.py")


def interpreter_for(version):
    """A python3.X on PATH (or this one) for the list's Python version."""
    if tuple(sys.version_info[:2]) == version:
        return sys.executable
    return shutil.which(f"python{version[0]}.{version[1]}")


class ModuleListTests(unittest.TestCase):
    def test_lists_for_oldest_and_newest_decky_are_committed(self):
        versions = {checker.load_list(path)[1] for path in checker.module_lists()}
        self.assertIn((3, 11), versions)  # Decky v3.0 - v3.2.10
        self.assertIn((3, 14), versions)  # Decky v3.3.0
        for path in checker.module_lists():
            names, _ = checker.load_list(path)
            # Sanity: lists come from a real PluginLoader, not a stub.
            self.assertTrue({"asyncio", "json", "subprocess", "xml.sax"} <= names, path.name)
            self.assertNotIn("xml.etree", names, path.name)


class StaticImportTests(unittest.TestCase):
    def test_backend_imports_only_bundled_modules(self):
        for path in checker.module_lists():
            allowed, _ = checker.load_list(path)
            self.assertEqual(checker.static_problems(allowed), [], path.name)

    def scan(self, source: str, list_name: str):
        with tempfile.TemporaryDirectory() as temp:
            extra = Path(temp) / "extra.py"
            extra.write_text(source)
            allowed, _ = checker.load_list(PROJECT / "tests" / list_name)
            with patch.object(checker, "sources", lambda: [extra]):
                return checker.static_problems(allowed)

    def test_catches_imports_inside_functions_and_dynamic_imports(self):
        cases = {
            "import xml.etree.ElementTree\n": "xml.etree",
            "def parse():\n    import xml.etree.ElementTree as ET\n    return ET\n": "xml.etree",
            "def parse():\n    from xml.etree import ElementTree\n": "xml.etree",
            "def parse():\n    from xml import etree\n": "xml.etree",
            "import importlib\nimportlib.import_module('tomllib')\n": "tomllib",
            "__import__('zoneinfo')\n": "zoneinfo",
            "import requests\n": "third-party",
        }
        for source, expected in cases.items():
            problems = self.scan(source, "decky_bundled_modules-v3.3.0.txt")
            self.assertTrue(problems and expected in problems[0], (source, problems))

    def test_module_dropped_in_newer_decky_is_caught_only_there(self):
        source = "def split(text):\n    import shlex\n    return shlex.split(text)\n"
        self.assertEqual(self.scan(source, "decky_bundled_modules-v3.2.10.txt"), [])
        self.assertTrue(self.scan(source, "decky_bundled_modules-v3.3.0.txt"))

    def test_bundled_and_local_imports_pass(self):
        source = "import asyncio, json\nfrom xml.sax import handler\nimport decky\nfrom backend import media\nfrom . import x\n"
        self.assertEqual(self.scan(source, "decky_bundled_modules-v3.3.0.txt"), [])


class RuntimeImportTests(unittest.TestCase):
    """Import main.py and every backend module with only Decky's modules allowed."""

    def check(self, list_name: str):
        allowed, version = checker.load_list(PROJECT / "tests" / list_name)
        python = interpreter_for(version)
        if python is None:
            self.skipTest(f"Python {version[0]}.{version[1]} is not installed")
        return python, allowed

    def test_main_and_backend_load_on_decky_v3_3_0_python(self):
        python, allowed = self.check("decky_bundled_modules-v3.3.0.txt")
        self.assertIsNone(checker.runtime_problem(python, allowed | checker.KNOWN_BUILTINS))

    def test_main_and_backend_load_on_decky_v3_2_10_python(self):
        python, allowed = self.check("decky_bundled_modules-v3.2.10.txt")
        self.assertIsNone(checker.runtime_problem(python, allowed | checker.KNOWN_BUILTINS))

    def test_runtime_check_refuses_a_missing_module(self):
        python, allowed = self.check("decky_bundled_modules-v3.3.0.txt")
        # backend/transfer.py imports secrets; pretend Decky stopped shipping it.
        problem = checker.runtime_problem(python, (allowed | checker.KNOWN_BUILTINS) - {"secrets"})
        self.assertIsNotNone(problem)
        self.assertIn("secrets", problem)


class PluginJsonLintTests(unittest.TestCase):
    def test_repository_plugin_json_is_release_ready(self):
        plugin = json.loads((PROJECT / "plugin.json").read_text())
        package = json.loads((PROJECT / "package.json").read_text())
        self.assertEqual(plugin_lint.lint(plugin, package), [])

    def test_debug_root_and_typo_flags_fail(self):
        package = json.loads((PROJECT / "package.json").read_text())
        base = json.loads((PROJECT / "plugin.json").read_text())
        for flags, expected in ((["debug"], "debug"), (["root"], "root"), (["_root"], "_root"), (["fast"], "unknown")):
            problems = plugin_lint.lint(dict(base, flags=flags), package)
            self.assertTrue(any(expected in problem for problem in problems), (flags, problems))
        self.assertEqual(plugin_lint.lint(dict(base, flags=["root"]), package, allow_root=True), [])


if __name__ == "__main__":
    unittest.main()
