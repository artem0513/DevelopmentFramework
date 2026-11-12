"""Directory source selection uses real Git/files; no semantic model doubles."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ase.discovery import discover_capabilities, record_discovery
from ase.gates import source_snapshot
from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import WorkflowController
from test_task_contract_v2 import contract


class DirectorySourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ase-directory-source-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.repo = self.base / "workspace"
        self.repo.mkdir()
        self.write("guide.md", "Confirmed operating guide")
        self.store = Store(self.repo, self.base / "state")
        self.addCleanup(self.store.close)

    def write(self, relative, content):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def task(self, *, software=False, resources=None, dependencies=None):
        if software:
            self.git("init", "-b", "feature/directory")
        c = contract("directory-task")
        c["profile"] = "software" if software else "document"
        task = TaskService(self.store).create(c)
        discovery = discover_capabilities(self.store.project, scope={"resources": resources or ["guide.md"],
            "dependencies": dependencies or []}, backend_config={})
        record_discovery(self.store, task["task_id"], discovery, expected_revision=0)
        return c, WorkflowController(self.store)

    def test_software_root_discovery_preserves_git_ignore_and_private_metadata(self):
        self.write(".gitignore", "cache/\n")
        ignored = self.write("cache/ignored.txt", "private ignored output")
        c, controller = self.task(software=True, resources=["."])
        original = Path.read_bytes
        def guarded(path):
            if path == ignored or ".git" in path.relative_to(self.repo).parts:
                raise AssertionError("Private or ignored file bytes were read")
            return original(path)
        with patch.object(Path, "read_bytes", guarded):
            snapshot = source_snapshot(self.store, c, task_id="directory-task")
            packet = controller._packet("directory-task")
        self.assertIn("guide.md", snapshot["files"])
        self.assertNotIn("cache/ignored.txt", snapshot["files"])
        self.assertIn("guide.md", packet["coverage"]["included"])
        self.assertFalse(any(".git/" in name for name in snapshot["files"]))
        self.assertEqual(packet["coverage"]["missing"], [])

    def test_material_dependency_directory_bodies_and_fingerprints_stay_aligned(self):
        policy = self.write("policies/nested/policy.md", "Version one policy")
        self.write("unrelated.txt", "Unrelated material")
        c, controller = self.task(dependencies=[{"path": "policies", "reason": "Governs the operating guide"}])
        before = source_snapshot(self.store, c, task_id="directory-task")
        packet = controller._packet("directory-task")
        item = next((item for item in packet["files"] if item["path"] == "policies/nested/policy.md"), None)
        self.assertIsNotNone(item, "Material dependency body was silently omitted")
        self.assertEqual(item["sha256"], before["files"][item["path"]])
        self.assertNotIn("unrelated.txt", packet["coverage"]["included"])
        policy.write_text("Version two policy", encoding="utf-8")
        after = source_snapshot(self.store, c, task_id="directory-task")
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])
        updated = next(item for item in controller._packet("directory-task")["files"] if item["path"] == "policies/nested/policy.md")
        self.assertEqual(updated["content"], "Version two policy")

    def test_binary_dependency_directory_reports_explicit_packet_gap(self):
        path = self.repo / "policies" / "binary.dat"
        path.parent.mkdir()
        path.write_bytes(b"\xff\x00policy")
        c, controller = self.task(dependencies=[{"path": "policies", "reason": "Contains source policy"}])
        self.assertIn("policies/binary.dat", source_snapshot(self.store, c, task_id="directory-task")["files"])
        self.assertTrue(any("policies/binary.dat" in gap for gap in controller._packet("directory-task")["coverage"]["missing"]))

    def test_directory_skips_nested_private_git_but_explicit_private_path_blocks(self):
        self.write("policies/.GiT/private.txt", "Private metadata")
        self.write("policies/policy.md", "Policy")
        c, controller = self.task(dependencies=[{"path": "policies", "reason": "Contains policy"}])
        snapshot = source_snapshot(self.store, c, task_id="directory-task")
        self.assertIn("policies/policy.md", snapshot["files"])
        self.assertFalse(any(".git" in path.lower() for path in snapshot["files"]))
        self.assertEqual(controller._packet("directory-task")["coverage"]["missing"], [])
        private_contract = {**c, "scope": {"resources": ["policies/.GiT/private.txt"], "effects": []}}
        with self.assertRaisesRegex(ValueError, "private|escapes|Git"):
            source_snapshot(self.store, private_contract)

    def test_explicit_ignored_dependency_file_is_still_captured(self):
        self.write(".gitignore", "policy.txt\n")
        policy = self.write("policy.txt", "Explicitly required policy")
        c, controller = self.task(software=True, dependencies=[{"path": "policy.txt", "reason": "Explicit governing policy"}])
        snapshot = source_snapshot(self.store, c, task_id="directory-task")
        self.assertEqual(snapshot["files"]["policy.txt"], hashlib.sha256(policy.read_bytes()).hexdigest())
        self.assertIn("policy.txt", controller._packet("directory-task")["coverage"]["included"])

    def test_redirected_dependency_directory_never_reads_external_file(self):
        outside = self.base / "external"
        outside.mkdir()
        canary = outside / "canary.txt"
        canary.write_text("Outside source")
        alias = self.repo / "policies" / "redirect"
        alias.parent.mkdir()
        if os.name == "nt":
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(outside)], capture_output=True)
            self.assertEqual(made.returncode, 0, made.stderr)
            self.addCleanup(alias.rmdir)
        else:
            alias.symlink_to(outside, target_is_directory=True)
            self.addCleanup(alias.unlink)
        c, _ = self.task(dependencies=[{"path": "policies", "reason": "Contains governing policy"}])
        original = Path.read_bytes
        def guarded(path):
            if path.resolve() == canary:
                raise AssertionError("External canary was read")
            return original(path)
        with patch.object(Path, "read_bytes", guarded), self.assertRaisesRegex(ValueError, "escapes|redirect|link"):
            source_snapshot(self.store, c, task_id="directory-task")


if __name__ == "__main__":
    unittest.main()
