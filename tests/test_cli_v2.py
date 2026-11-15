"""Public CLI routes generic projects through v2 services and fails closed."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from ase.projects import ProjectRegistry
from ase.gates import source_snapshot
from ase.state import read_record
from ase.store import Store, now
from ase.tasks import TaskService
from test_journal import contract


ROOT = Path(__file__).resolve().parents[1]


class UniversalCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ase-cli-v2-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "documents"
        self.root.mkdir()
        (self.root / "guide.txt").write_text("Operating guide", encoding="utf-8")
        self.home = self.base / "state"
        self.environment = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        self.sequence = 0

    def payload(self, value):
        self.sequence += 1
        path = self.base / ("input-" + str(self.sequence) + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def cli(self, *arguments, ok=0, selector=None):
        select = ["--project", selector] if selector else ["--repo", str(self.root)]
        run = subprocess.run([sys.executable, "-m", "ase", "--home", str(self.home), *select, *arguments],
                             cwd=ROOT, env=self.environment, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, ok, run.stdout + run.stderr)
        try:
            result = json.loads(run.stdout)
        except ValueError:
            self.fail("CLI did not return JSON: " + run.stdout + run.stderr)
        self.assertEqual(run.stderr, "")
        return result

    def create(self):
        return self.cli("task", "create", "--file", self.payload(contract("guide")))

    def test_non_git_registration_listing_and_stable_selector(self):
        project = self.cli("project", "register", "--kind", "document", "--timezone", "Europe/Bucharest")
        self.assertEqual(project["root"], str(self.root))
        projects = self.cli("project", "list")["projects"]
        self.assertEqual(projects[0]["project_id"], project["project_id"])
        second = self.base / "second-root"
        second.mkdir()
        attached = self.cli("project", "attach", project["project_id"], "--root", str(second), "--expected-revision", "1")
        self.assertEqual(attached["project_id"], project["project_id"])
        self.assertNotEqual(attached["workspace_id"], project["workspace_id"])
        status = self.cli("status", selector=project["project_id"])
        self.assertEqual(status["project_id"], project["project_id"])

    def test_v2_task_proposal_approval_and_revision(self):
        task = self.create()
        self.assertEqual(task["state"], "DRAFT")
        proposal = self.cli("task", "propose", "guide", "--expected-revision", str(task["revision"]), "--file", self.payload({"summary": "Draft the guide", "stage_ids": ["draft"]}))
        self.cli("task", "approve", "guide", "--file", self.payload({"proposal_hash": "wrong", "user_message_ref": "chat:approval", "approved_stage_ids": ["draft"]}), ok=3)
        approved = self.cli("task", "approve", "guide", "--file", self.payload({"proposal_hash": proposal["proposal_hash"], "user_message_ref": "chat:approval", "approved_stage_ids": ["draft"]}))
        self.assertEqual(approved["state"], "READY")
        changed = contract("guide")
        changed["request"] = "Revise the operating guide for new requirements"
        revised = self.cli("task", "revise", "guide", "--expected-revision", str(approved["revision"]), "--file", self.payload(changed))
        self.assertEqual(revised["approved_stage_ids"], [])
        self.assertIsNone(revised["proposal_hash"])
        status = self.cli("task", "status", "guide")
        self.assertEqual(status["readiness"]["clarity"], "UNASSESSED")
        self.cli("task", "verify", "guide", ok=3)

    def test_discovery_checks_gate_stop_and_unapproved_run(self):
        self.create()
        discovery = self.cli("task", "discover", "guide", "--expected-revision", "0")
        self.assertFalse(discovery["payload"]["authorization_granted"])
        status = self.cli("task", "status", "guide")
        self.assertTrue(status["readiness"]["discovery_present"])
        self.cli("task", "check", "guide", ok=3)
        self.cli("task", "run", "guide", ok=3)
        proposal = self.cli("task", "propose", "guide", "--expected-revision", "1", "--file", self.payload({"summary": "Draft the guide", "stage_ids": ["draft"]}))
        self.cli("task", "approve", "guide", "--file", self.payload({"proposal_hash": proposal["proposal_hash"], "user_message_ref": "chat:approval", "approved_stage_ids": ["draft"]}))
        checks = self.cli("task", "check", "guide")
        self.assertEqual(checks["results"][0]["sha256"], hashlib.sha256((self.root / "guide.txt").read_bytes()).hexdigest())
        self.assertEqual(self.cli("task", "gate", "guide", ok=3)["status"], "BLOCKED")
        self.cli("task", "stop", "guide", "--reason", "Wait for reviewed approval")

    def test_session_closure_reports_and_knowledge(self):
        self.create()
        self.cli("session", "start", "guide", "editing")
        finish = {"finish_reason": "paused", "summary": "Saved guide", "next_action": "Review guide", "context": {"summary": "Guide awaits review"}, "blockers": []}
        closure = self.cli("session", "finish", "guide", "editing", "--closure-key", "close-1", "--expected-revision", "0", "--expected-context-revision", "0", "--file", self.payload(finish))
        self.assertEqual(closure["state"], "PAUSED")
        worklog = self.cli("worklog", "--start", "2026-01-01", "--until", "2027-01-01")
        self.assertTrue(worklog["events"])
        self.cli("meeting", "--start", "2026-01-01", "--until", "2027-01-01")
        knowledge = self.cli("knowledge", "retrieve", "--file", self.payload({"scope": {}}))
        self.assertEqual(len(knowledge["claims"]), 1)
        self.assertEqual(knowledge["claims"][0]["topic"], "task-summary:guide")
        self.assertEqual(knowledge["claims"][0]["validity"], "UNKNOWN")
        self.assertEqual(knowledge["claims"][0]["source_validity"], "UNKNOWN")
        self.cli("knowledge", "observations", "--expected-revision", "0", "--file", self.payload({"observations": [], "dependencies": []}), ok=3)
        observations = self.cli("knowledge", "observations", "--expected-revision", str(knowledge["revision"]), "--file", self.payload({"observations": [], "dependencies": []}))
        self.assertEqual(observations["revision"], knowledge["revision"] + 1)

    def test_public_legacy_execution_is_blocked_before_invocation(self):
        with closing(Store(self.root, self.home)) as store, store.db:
            store.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)", ("old", "Legacy task", "READY", "{}", now(), now()))
        for command in ("run", "resume", "verify", "check", "gate", "publish"):
            with self.subTest(command=command):
                result = self.cli(command, "old", ok=3)
                self.assertIn("v2", json.dumps(result))

    def test_cli_transfer_roundtrip_and_json_errors(self):
        self.create()
        archive = self.base / "project.zip"
        exported = self.cli("project", "export", str(archive))
        self.assertEqual(exported["format"], "autonomous-se-project")
        target = self.base / "imported"
        target.mkdir()
        imported = self.cli("project", "import", str(archive), "--root", str(target))
        self.assertNotEqual(imported["project_id"], exported["project"]["project_id"])
        status = self.cli("task", "status", "guide", selector=imported["project_id"])
        self.assertEqual(status["state"], "AWAITING_APPROVAL")
        invalid = self.cli("task", "approve", ok=3)
        self.assertEqual(invalid["status"], "BLOCKED")
        self.cli("task", "create", "--file", self.payload({"schema_version": 2}), ok=3)

    def test_packaging_contains_adapter_and_timezone_assets(self):
        settings = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        packaging = settings["tool"]["setuptools"]
        import fnmatch
        configured = packaging.get("packages", [])
        patterns = configured.get("find", {}).get("include", []) if isinstance(configured, dict) else configured
        packages = {".".join(path.parent.relative_to(ROOT).parts) for path in (ROOT / "ase").rglob("__init__.py")}
        self.assertIn("ase.adapters", packages)
        for package in packages:
            self.assertTrue(any(fnmatch.fnmatchcase(package, pattern) for pattern in patterns), package)
        self.assertIn("data/timezones/*", packaging.get("package-data", {}).get("ase", []))

    def test_non_git_init_and_legacy_task_revision(self):
        initial = self.cli("init")
        self.assertTrue(initial["project_id"])
        with closing(Store(self.root, self.home)) as store, store.db:
            store.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)", ("old", "Legacy task", "READY", "{}", now(), now()))
        revised = self.cli("task", "revise", "old", "--expected-revision", "0", "--file", self.payload(contract("old")))
        self.assertFalse(revised["legacy_v1"])
        self.assertEqual(revised["approved_stage_ids"], [])

    def test_current_explicit_protected_and_high_risk_authority(self):
        task = self.create()
        discovery = self.cli("task", "discover", "guide", "--expected-revision", "0")["payload"]
        (self.root / "rules.txt").write_text("Shared operating rules", encoding="utf-8")
        discovery["resources"].append({"path": "rules.txt", "status": "INSPECTED", "type": "file"})
        discovery["dependencies"].append({"path": "rules.txt", "from": "guide.txt", "reason": "Shared guide requirements"})
        self.cli("task", "discover", "guide", "--expected-revision", "1", "--file", self.payload(discovery))
        proposal = self.cli("task", "propose", "guide", "--expected-revision", str(task["revision"]), "--file", self.payload({"summary": "Draft the guide", "stage_ids": ["draft"]}))
        self.cli("task", "approve", "guide", "--file", self.payload({"proposal_hash": proposal["proposal_hash"], "user_message_ref": "chat:approval", "approved_stage_ids": ["draft"]}))
        authorization = self.cli("task", "authorize-protected", "guide", "--path", "guide.txt", "--reason", "Approved guide edit", "--user-message-ref", "chat:protected")
        self.assertEqual(authorization["paths"], ["guide.txt"])
        high = self.cli("task", "approve-high-risk", "guide", "--reason", "Reviewed resource changes", "--user-message-ref", "chat:high-risk")
        self.assertEqual(high["user_message_ref"], "chat:high-risk")
        with closing(Store(self.root, self.home)) as store:
            self.assertEqual(read_record(store.db, "protected_approval", "guide")["payload"]["fingerprint"], authorization["fingerprint"])
            self.assertEqual(read_record(store.db, "high_risk_approval", "guide")["payload"]["contract_hash"], task["contract_hash"])
            self.assertEqual(high["fingerprint"], source_snapshot(store, TaskService(store).snapshot("guide")["contract"], task_id="guide")["fingerprint"])
        with closing(Store(self.root, self.home)) as store, store.db:
            store.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)", ("old", "Legacy task", "READY", "{}", now(), now()))
        for arguments in (("publish", "old"), ("approve-high-risk", "old"), ("authorize-protected", "old", "--path", "guide.txt", "--reason", "Old permissions")):
            with self.subTest(command=arguments[0]):
                blocked = self.cli(*arguments, ok=3)
                self.assertIn("v2", json.dumps(blocked))

    def test_documented_approval_session_and_report_forms(self):
        capabilities = self.cli("capabilities")
        self.assertTrue(capabilities["tools"])
        self.assertFalse(capabilities["authorization_granted"])
        task = self.create()
        task = self.cli("task", "propose", "guide", "--file", self.payload({"summary": "Draft guide", "stage_ids": ["draft"]}), "--expected-revision", str(task["revision"]))
        approved = self.cli("task", "approve", "guide", "--proposal-hash", task["proposal_hash"], "--user-message-ref", "chat:explicit-approval", "--stage", "draft")
        self.assertEqual(approved["state"], "READY")
        closed = self.cli("session", "finish", "guide", "--session-id", "documented-session", "--file", self.payload({"finish_reason": "paused", "summary": "Save work", "next_action": "Continue later", "context": {"summary": "Guide task"}}), "--closure-key", "documented-close", "--expected-revision", "0", "--expected-context-revision", "0")
        self.assertEqual(closed["finish_reason"], "paused")
        self.cli("worklog", "--from", "2026-11-01", "--until", "2026-12-01", "--timezone", "Europe/Bucharest")
        self.cli("meeting-report", "--from", "2026-11-01", "--until", "2026-12-01", "--as-of-end")

    def test_document_project_cli_does_not_require_git_on_path(self):
        no_tools = self.base / "no-tools"
        no_tools.mkdir()
        result = subprocess.run([sys.executable, "-m", "ase", "--repo", str(self.root), "--home", str(self.home), "project", "register", "--kind", "document", "--timezone", "Europe/Bucharest"],
                                cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), "PATH": str(no_tools)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["kind"], "document")


if __name__ == "__main__":
    unittest.main()
