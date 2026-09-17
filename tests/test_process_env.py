import os
import unittest
from unittest.mock import patch

from backend.process_env import system_tool_env


class ProcessEnvironmentTests(unittest.TestCase):
    def test_restores_original_without_mutating_parent(self):
        source = {"LD_LIBRARY_PATH": "/tmp/_MEIexample", "LD_LIBRARY_PATH_ORIG": "/original/lib", "PATH": "/usr/bin"}
        with patch.dict(os.environ, source, clear=True):
            child = system_tool_env()
            self.assertEqual(child["LD_LIBRARY_PATH"], "/original/lib")
            self.assertEqual(child["PATH"], "/usr/bin")
            self.assertEqual(dict(os.environ), source)

    def test_missing_original_uses_system_defaults(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEIexample"}, clear=True):
            self.assertNotIn("LD_LIBRARY_PATH", system_tool_env())
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/tmp/_MEIexample")

    def test_empty_original_uses_system_defaults(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEIexample", "LD_LIBRARY_PATH_ORIG": ""}, clear=True):
            self.assertNotIn("LD_LIBRARY_PATH", system_tool_env())

    def test_environment_without_overrides_is_preserved(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin", "LANG": "C.UTF-8"}, clear=True):
            self.assertEqual(system_tool_env(), dict(os.environ))
