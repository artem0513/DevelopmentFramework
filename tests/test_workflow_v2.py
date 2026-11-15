"""Budgets and filesystem effects are tested without semantic model stubs."""
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import WorkflowController, apply_edits
from ase.state import read_record, transaction, write_record
from ase.gates import GateService, source_snapshot
from ase.discovery import discover_capabilities, record_discovery
from ase.operations import OperationLedger
from ase.runner import Runner
from ase.journal import Journal
from test_runner import FIXTURE
from test_task_contract_v2 import contract


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ase-workflow-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        work = root / "work"
        work.mkdir()
        self.store = Store(work, root / "state")
        self.addCleanup(self.store.close)
        self.tasks = TaskService(self.store)
        legacy = contract()
        legacy["execution_policy"] = {"recovery_mode": "fixed"}
        task = self.tasks.create(legacy)
        task = self.tasks.propose(task["task_id"], {"summary": "Create guide; publication deferred", "stage_ids": ["draft"]}, expected_revision=task["revision"])
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="chat:approved", approved_stage_ids=["draft"])
        self.controller = WorkflowController(self.store, runner=None)

    def test_three_stage_cycles_survive_resume(self):
        for i in range(3):
            self.assertEqual(self.controller.reserve_cycle("approval-task", "draft")["stage_cycles"], i + 1)
        new_controller = WorkflowController(self.store, runner=None)
        with self.assertRaisesRegex(ValueError, "budget"):
            new_controller.reserve_cycle("approval-task", "draft")

    def test_second_same_cause_uses_one_diagnostic_pass(self):
        self.assertFalse(self.controller.record_failure("approval-task", "draft", ["same cause"])["diagnostic_required"])
        self.assertTrue(self.controller.record_failure("approval-task", "draft", ["same cause"])["diagnostic_required"])
        self.controller.reserve_diagnostic("approval-task", "draft")
        with self.assertRaisesRegex(ValueError, "diagnostic"):
            self.controller.reserve_diagnostic("approval-task", "draft")

    def test_apply_edits_checks_scope_and_preimage_before_any_write(self):
        path = self.store.repo / "guide.md"
        path.write_text("old", encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        scope = {"resources": ["guide.md"], "effects": ["file_write"]}
        with self.assertRaisesRegex(ValueError, "scope"):
            apply_edits(self.store.repo, scope, [{"path": "guide.md", "before_sha256": digest, "content": "new", "operation": "write"},
                                               {"path": "../escape", "before_sha256": None, "content": "bad", "operation": "write"}])
        self.assertEqual(path.read_text(), "old")
        with self.assertRaisesRegex(ValueError, "preimage"):
            apply_edits(self.store.repo, scope, [{"path": "guide.md", "before_sha256": "stale", "content": "new", "operation": "write"}])
        apply_edits(self.store.repo, scope, [{"path": "guide.md", "before_sha256": digest, "content": "new", "operation": "write"}])
        self.assertEqual(path.read_text(), "new")

    def test_stop_persists_and_never_marks_completed(self):
        self.controller.request_stop("approval-task", reason="finish today")
        self.assertEqual(self.tasks.snapshot("approval-task")["state"], "PAUSED")
        with self.assertRaisesRegex(ValueError, "stop|paused"):
            self.controller.reserve_cycle("approval-task", "draft")

    def test_packet_only_reads_approved_files_and_reasoned_dependencies(self):
        (self.store.repo / "guide.md").write_text("scoped content")
        (self.store.repo / "secret.bin").write_bytes(b"\xffPRIVATE")
        packet = self.controller._packet("approval-task")
        self.assertIn("guide.md", packet["coverage"]["included"])
        self.assertNotIn("secret.bin", packet["coverage"]["included"])
        self.assertEqual(packet["coverage"]["missing"], [])

    def test_directory_discovery_never_reads_a_directory_as_source_bytes(self):
        (self.store.repo / "guide.md").write_text("actual source")
        discovery = discover_capabilities(self.store.project, scope={"resources": ["."]}, backend_config={})
        record_discovery(self.store, "approval-task", discovery, expected_revision=0)
        packet = self.controller._packet("approval-task")
        self.assertEqual(packet["coverage"]["missing"], [])
        self.assertNotIn(".", packet["coverage"]["included"])
        self.assertIn("guide.md", packet["coverage"]["included"])

    def test_gate_never_completes_unexecuted_stages(self):
        verdict = GateService(self.store).gate("approval-task")
        self.assertIn("Stage incomplete: draft", verdict["reasons"])
        self.assertIn("Stage incomplete: publish", verdict["reasons"])

    def test_check_requires_approval_and_preserves_actual_artifact_path(self):
        c = contract("unapproved")
        c["evidence"][0]["path"] = "guide.md"
        self.tasks.create(c)
        with self.assertRaisesRegex(ValueError, "approval"):
            self.controller.check("unapproved")

    def test_active_time_budget_cannot_reset_on_new_controller(self):
        record = read_record(self.store.db, "task", "approval-task")
        record["payload"]["contract"]["execution_policy"]["active_time_budget_seconds"] = 1
        with transaction(self.store.db) as db:
            write_record(db, "task", "approval-task", record["payload"], expected_revision=record["revision"])
        self.controller.charge_time("approval-task", 1.1)
        with self.assertRaisesRegex(ValueError, "time budget"):
            WorkflowController(self.store).reserve_cycle("approval-task", "draft")

    def test_real_infrastructure_action_reconciles_without_duplicate_effect(self):
        c = contract("resource-task")
        c["unknowns"] = []
        c["profile"] = "infrastructure"
        c["scope"] = {"resources": ["resource"], "effects": ["command"]}
        c["stages"] = [{"id": "provision", "scope": c["scope"], "acceptance_ids": ["AC1"], "actions": [{
            "id": "provision-resource", "argv": [sys.executable, "-c", "from pathlib import Path; p=Path('resource'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"],
            "observation_argv": [sys.executable, "-c", "from pathlib import Path; import json; print(json.dumps({'exists':Path('resource').exists(),'resource_id':'controlled-test-resource'}))"]}]}]
        c["evidence"] = [{"id": "guide", "kind": "artifact", "path": "resource"}]
        task = self.tasks.create(c)
        task = self.tasks.propose(task["task_id"], {"summary": "Provision controlled local test resource", "stage_ids": ["provision"]}, expected_revision=task["revision"])
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="chat:approved", approved_stage_ids=["provision"])
        stage = self.tasks.snapshot(task["task_id"])["contract"]["stages"][0]
        self.controller.execute_actions(task["task_id"], stage)
        self.controller.execute_actions(task["task_id"], stage)
        self.assertEqual((self.store.repo / "resource").read_text(), "x")

    def test_stage_checks_do_not_require_deferred_artifacts(self):
        c = contract("partial-checks")
        c["scope"]["resources"].append("published.txt")
        c["evidence"].append({"id": "publication", "kind": "artifact", "path": "published.txt"})
        c["acceptance"].append({"id": "AC2", "expected": "Publication exists", "evidence_ids": ["publication"]})
        c["stages"][1]["acceptance_ids"] = ["AC2"]
        task = self.tasks.create(c)
        task = self.tasks.propose(task["task_id"], {"summary": "Draft only", "stage_ids": ["draft"]}, expected_revision=task["revision"])
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="chat:approve", approved_stage_ids=["draft"])
        (self.store.repo / "guide.md").write_text("actual draft")
        result = self.controller.check(task["task_id"], "draft")
        self.assertEqual([r["id"] for r in result["results"]], ["guide"])

    def test_discovered_dependency_changes_invalidate_non_git_evidence(self):
        (self.store.repo / "guide.md").write_text("guide")
        dependency = self.store.repo / "policy.md"
        dependency.write_text("first")
        discovery = discover_capabilities(self.store.project, scope={"resources": ["guide.md"], "dependencies": [{"path": "policy.md", "reason": "Governs guide policy"}]}, backend_config={})
        record_discovery(self.store, "approval-task", discovery, expected_revision=0)
        c = self.tasks.snapshot("approval-task")["contract"]
        before = source_snapshot(self.store, c, task_id="approval-task")
        dependency.write_text("changed")
        after = source_snapshot(self.store, c, task_id="approval-task")
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])

    def test_unknown_effect_blocks_gate_independently_of_local_source(self):
        ledger = OperationLedger(self.store)
        ledger.prepare("approval-task", "draft", "remote-effect", {"create": "resource"})
        ledger.started("remote-effect")
        verdict = GateService(self.store).gate("approval-task")
        self.assertTrue(any("Uncertain external operation" in r for r in verdict["reasons"]))

    def test_packet_log_preserves_captured_windows_newlines_and_hash(self):
        folder = self.store.root / "tasks" / "approval-task" / "checks"
        folder.mkdir(parents=True)
        log = folder / "actual.log"
        raw = b"Ran 1 test in 0.001s\r\n\r\nOK\r\n"
        log.write_bytes(raw)
        self.controller._save("checks", "approval-task", {"results": [{"id": "unit", "log": str(log), "log_sha256": hashlib.sha256(raw).hexdigest()}]})
        packet = self.controller._packet("approval-task")
        captured = next(f for f in packet["files"] if f["path"] == "_ase_context/logs/unit.txt")
        self.assertEqual(captured["sha256"], hashlib.sha256(raw).hexdigest())

    def test_full_child_gate_revalidates_changed_parent(self):
        c = contract("child")
        c.update(parent_task_id="approval-task", parent_stage_id="draft")
        c["stages"] = c["stages"][:1]
        c["unknowns"] = []
        self.tasks.create(c)
        parent = self.tasks.snapshot("approval-task")
        self.tasks.propose("approval-task", {"summary": "Changed parent proposal", "stage_ids": ["draft"]}, expected_revision=parent["revision"])
        verdict = GateService(self.store).gate("child")
        self.assertTrue(any("Inherited parent approval invalid" in r for r in verdict["reasons"]))

    def test_resume_final_cycle_verifies_without_another_implementation(self):
        """Real protocol children deliberately cannot provide semantic approval."""
        for _ in range(3):
            self.controller.reserve_cycle("approval-task", "draft")
        task = self.tasks.snapshot("approval-task")
        (self.store.repo / "guide.md").write_text("draft")
        discovery = discover_capabilities(self.store.project, scope={"resources": ["guide.md"]}, backend_config={})
        record_discovery(self.store, task["task_id"], discovery, expected_revision=0)
        self.controller._save("cycle", "approval-task:draft", {"phase": "VERIFYING", "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"]})
        base = self.store.state_home
        trusted = base / "trusted"
        trusted.mkdir()
        fixture = base / "fixture.py"
        fixture.write_text(FIXTURE)
        artifacts = self.store.root / "tasks" / task["task_id"] / "sessions"
        runner = Runner({"executable": sys.executable, "executable_args": [str(fixture), "success", "codex"], "trusted_cwd": str(trusted), "protocol_only": True}, artifacts)
        result = WorkflowController(self.store, runner=runner).run(task["task_id"], resume=True)
        self.assertEqual(result["status"], "BLOCKED")
        roles = [json.loads(p.read_text())["role"] for p in artifacts.glob("*/request.json")]
        self.assertEqual(sorted(roles), ["ac", "review"])
        self.assertEqual(read_record(self.store.db, "budget", task["task_id"])["payload"]["task_cycles"], 3)
        self.assertEqual(self.tasks.snapshot(task["task_id"])["state"], "BLOCKED")

    def test_crashed_workflow_session_is_closed_as_interrupted(self):
        journal = Journal(self.store)
        journal.start_session("approval-task", "crashed-session")
        self.controller._save("active_workflow", "approval-task", {"task_id": "approval-task", "session_id": "crashed-session"})
        # Missing discovery blocks before any model can be launched.
        result = self.controller.run("approval-task", resume=True)
        self.assertEqual(result["status"], "BLOCKED")
        recovered = read_record(self.store.db, "session_closure", "crashed-session:recovered")["payload"]
        self.assertEqual(recovered["result"]["finish_reason"], "interrupted")
        self.assertEqual(journal.session("approval-task", "crashed-session")["payload"]["status"], "INTERRUPTED")

    def test_review_packet_proves_changed_and_immutable_files_from_real_baseline(self):
        repo = self.store.repo
        (repo / "guide.md").write_text("old content")
        (repo / "test_immutable.py").write_text("immutable test source")
        for args in (("init", "-b", "feature/evidence"), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Evidence test"), ("add", "."), ("commit", "-m", "baseline")):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        c = contract("baseline-evidence")
        c["profile"] = "software"
        self.tasks.create(c)
        self.controller._save("baseline", "baseline-evidence", source_snapshot(self.store, c, task_id="baseline-evidence"))
        (repo / "guide.md").write_text("new content")
        packet = self.controller._packet("baseline-evidence")
        change_file = next(f for f in packet["files"] if f["path"] == "_ase_context/source_changes.json")
        changes = json.loads(change_file["content"])
        self.assertEqual([r["path"] for r in changes["changes"]], ["guide.md"])
        self.assertIn("test_immutable.py", changes["unchanged_paths"])
        self.assertEqual(changes["changes"][0]["before_sha256"], hashlib.sha256(b"old content").hexdigest())

    def test_verified_completion_resolves_owned_blocker_and_keeps_history(self):
        """Gate double isolates journal projection, never semantic approval."""
        c = contract("report-recovery")
        c["unknowns"], c["stages"] = [], c["stages"][:1]
        task = self.tasks.create(c)
        task = self.tasks.propose(task["task_id"], {"summary": "Create the guide", "stage_ids": ["draft"]}, expected_revision=task["revision"])
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="chat:approved", approved_stage_ids=["draft"])
        self.assertEqual(self.controller.run(task["task_id"])["status"], "BLOCKED")
        journal = Journal(self.store)
        before = journal.worklog("2026-01-01", "2030-01-01", timezone="UTC")
        self.assertTrue(any(b["task_id"] == task["task_id"] for b in before["current_blockers"]))
        plan = self.tasks.execution_plan(task["task_id"])
        gate = {"status": "APPROVED", "contract_hash": task["contract_hash"], "plan_hash": plan["plan_hash"]}
        with patch.object(self.controller, "_run", return_value=gate), patch("ase.gates.GateService.gate", return_value=gate):
            self.controller.run(task["task_id"], resume=True)
        report = journal.worklog("2026-01-01", "2030-01-01", timezone="UTC")
        self.assertFalse(any(b["task_id"] == task["task_id"] for b in report["current_blockers"]))
        self.assertEqual([e["kind"] for e in report["period_blockers"] if e["task_id"] == task["task_id"]], ["blocker_opened", "blocker_resolved"])

    def test_completed_resume_resolves_effectively_reopened_execution_blocker(self):
        """Gate double isolates corrected blocker projection, not semantic AC."""
        c = contract("corrected-blocker-recovery")
        c["unknowns"], c["stages"] = [], c["stages"][:1]
        task = self.tasks.create(c)
        task = self.tasks.propose(task["task_id"], {"summary": "Create guide", "stage_ids": ["draft"]}, expected_revision=task["revision"])
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="chat:approved", approved_stage_ids=["draft"])
        self.assertEqual(self.controller.run(task["task_id"])["status"], "BLOCKED")
        journal = Journal(self.store)
        journal.append({"kind": "blocker_resolved", "task_id": task["task_id"], "blocker_id": "execution-blocker", "summary": "Incorrect resolution"}, event_key="wrong-resolution")
        journal.append({"kind": "correction", "task_id": task["task_id"], "corrects": "wrong-resolution", "summary": "Resolution was premature"}, event_key="withdraw-resolution")
        self.assertTrue(any(b['task_id'] == task['task_id'] for b in journal.worklog('2026-01-01', '2030-01-01', timezone='UTC')['current_blockers']))
        plan = self.tasks.execution_plan(task["task_id"])
        gate = {"status": "APPROVED", "contract_hash": task["contract_hash"], "plan_hash": plan["plan_hash"]}
        with patch.object(self.controller, "_run", return_value=gate), patch("ase.gates.GateService.gate", return_value=gate):
            self.controller.run(task["task_id"], resume=True)
        report = journal.worklog('2026-01-01', '2030-01-01', timezone='UTC')
        self.assertFalse(any(b['task_id'] == task['task_id'] for b in report['current_blockers']))


if __name__ == "__main__":
    unittest.main()
