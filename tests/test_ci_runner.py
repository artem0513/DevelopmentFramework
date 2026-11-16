"""Run the same tests through an interpreter's physical, guarded host path."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"


class CanonicalTestRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ase-ci-runner-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        (self.base / "tests").mkdir()

    def run_tests(self, *args, executable=None):
        return subprocess.run([str(executable or sys.executable), "-B", str(RUNNER), *args],
            cwd=self.base, capture_output=True, text=True, timeout=15)

    def test_default_discovery_relaunches_an_aliased_interpreter(self):
        executable = Path(sys.executable).resolve()
        if os.name == "nt":
            import _winapi
            from ase.native_paths import logical_path
            executable = logical_path(executable)
            alias = self.base / "python-alias"
            _winapi.CreateJunction(str(executable.parent), str(alias))
            aliased_executable = alias / executable.name
        else:
            aliased_executable = self.base / "python-alias"
            aliased_executable.symlink_to(executable)
        from ase.runner import checked_path
        with self.assertRaisesRegex(ValueError, "Reparse points"):
            checked_path(aliased_executable, directory=False)
        (self.base / "tests" / "test_physical_path.py").write_text(
            "import pathlib, sys, unittest\n"
            "class PhysicalPathTests(unittest.TestCase):\n"
            " def test_executable_has_no_alias(self):\n"
            "  self.assertEqual(pathlib.Path(sys.executable), pathlib.Path(sys.executable).resolve())\n",
            encoding="utf-8")
        result = self.run_tests(executable=aliased_executable)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_selected_tests_preserve_failure_status_and_output(self):
        (self.base / "test_selected.py").write_text(
            "import unittest\nclass SelectedTests(unittest.TestCase):\n"
            " def test_failure(self): self.fail('selected failure')\n", encoding="utf-8")
        result = self.run_tests("test_selected", "-v")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("selected failure", result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_invalid_unittest_arguments_preserve_usage_status(self):
        result = self.run_tests("--not-a-unittest-option")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unrecognized arguments: --not-a-unittest-option", result.stderr)
