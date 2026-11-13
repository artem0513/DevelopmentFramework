"""Authorized supersession preserves verified history without approving new work.

Subprocess sessions below are protocol fixtures, never independent semantic AC.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from ase.adapters.codex import CodexAdapter
from ase.gates import GateService, source_snapshot
from ase.runner import Runner
from ase.state import read_record, write_record
from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import EDIT_SCHEMA, WorkflowController
import test_runner as transport
from test_journal import contract


class RevisionHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ase-revision-history-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.repo = self.base / "work"
        self.repo.mkdir()
        (self.repo / "guide.txt").write_text("original draft")
        self.store = Store(self.repo, self.base / "state")
        self.addCleanup(lambda: self.store.close())
        self.tasks = TaskService(self.store)
        spec = contract("revision")
        spec["scope"]["resources"].append("delivery.txt")
        spec["acceptance"].append({"id": "DELIVERY", "expected": "Guide delivered", "evidence_ids": ["delivery"]})
        spec["evidence"].append({"id": "delivery", "kind": "artifact", "path": "delivery.txt", "required": True})
        spec["stages"][0]["scope"] = {"resources": ["guide.txt"], "effects": ["file_write"]}
        spec["stages"].append({"id": "deliver", "scope": {"resources": ["delivery.txt"], "effects": ["file_write"]},
            "acceptance_ids": ["DELIVERY"], "depends_on": ["draft"]})
        task = self.tasks.create(spec)
        self.approve(task, "Original draft")
        self.controller = WorkflowController(self.store)
        self.controller._save("baseline", "revision", source_snapshot(self.store, self.task["contract"], task_id="revision"))
        self.controller.check("revision", "draft")
        self.trusted = self.base / "trusted"
        self.trusted.mkdir()
        self.fixture = self.base / "fixture.py"
        self.runner = Runner({"executable": sys.executable,
            "executable_args": [str(self.fixture), "success", "codex"],
            "trusted_cwd": str(self.trusted), "protocol_only": True}, self.store.root / "tasks/revision/sessions")
        self.original_implementation = self.capture_implementation()
        for role in ("ac", "review"):
            self.fixture.write_text(transport.FIXTURE)
            request = self.controller._request("revision", role, transport.SCHEMA, {"stage_id": "draft"})
            result = self.runner.run(CodexAdapter(), request, timeout=5)
            self.assertEqual(result["result"]["status"], "COMPLETE")
            self.controller._save("verification", "revision:" + role, result["receipt"])
        self.controller._capture_stage("revision", "draft", {"status": "APPROVED", "fingerprint": "protocol-scheduler-fixture"})
        self.original = copy.deepcopy(self.task)

    def approve(self, task, summary):
        task = self.tasks.propose("revision", {"summary": summary, "stage_ids": ["draft"]}, expected_revision=task["revision"])
        self.task = self.tasks.approve("revision", proposal_hash=task["proposal_hash"],
            user_message_ref="test:owner-update", approved_stage_ids=["draft"])

    def capture_implementation(self):
        self.fixture.write_text(transport.FIXTURE.replace("payload = {'verdict':'PASS'}",
            "payload = {'edits': [], 'summary': 'Protocol proposal only'}"))
        stage = self.task["contract"]["stages"][0]
        request = self.controller._request("revision", "implementation", EDIT_SCHEMA, {"stage": stage})
        result = self.runner.run(CodexAdapter(), request, timeout=5)
        self.assertEqual(result["result"]["status"], "COMPLETE")
        self.controller._save("stage_implementation", "revision:draft", result["receipt"])
        return result["receipt"]

    def revise(self, *, approve=True):
        spec = copy.deepcopy(self.tasks.snapshot("revision")["contract"])
        spec["request"] += "; use the new owner answer"
        spec["acceptance"][0]["expected"] = "Guide reflects the new answer"
        spec["acceptance"][0]["evidence_ids"].append("new-answer")
        spec["evidence"].append({"id": "new-answer", "kind": "artifact", "path": "guide.txt", "required": True})
        task = self.tasks.revise("revision", spec, expected_revision=self.tasks.snapshot("revision")["revision"])
        (self.repo / "guide.txt").write_text("new owner answer")
        if approve:
            self.approve(task, "Refresh draft against the new owner answer")

    def packet(self):
        packet = self.controller._packet("revision", stage_id="draft")
        supplied = {item["path"]: item["content"] for item in packet["files"]}
        history = json.loads(supplied["_ase_context/stage_history.json"])
        manifest = json.loads(supplied["_ase_context/action_evidence.json"])
        return packet, supplied, history, manifest["stages"][0]["implementation_proposal"]

    def test_approved_revision_transports_old_proof_as_history_without_current_approval(self):
        self.revise()
        self.store.close()
        self.store = Store(self.repo, self.base / "state")
        self.tasks = TaskService(self.store)
        self.controller = WorkflowController(self.store)
        packet, supplied, history, proposal = self.packet()
        self.assertEqual(packet["coverage"]["missing"], [])
        self.assertEqual(supplied["guide.txt"], "new owner answer")
        stage = history["stages"][0]
        self.assertFalse(stage["current_identity"])
        self.assertFalse(stage["semantic_approval"])
        self.assertEqual(stage["contract_hash"], self.original["contract_hash"])
        self.assertEqual(proposal["status"], "HISTORICAL_VERIFIED_CAPTURE")
        self.assertFalse(proposal["current_identity"])
        self.assertEqual(proposal["execution_id"], self.original_implementation["execution_id"])
        gate = GateService(self.store).gate("revision", "draft")
        self.assertNotEqual(gate["status"], "APPROVED")
        self.assertIn("Stale check evidence: contract_hash", gate["reasons"])
        self.assertIn("Independent ac receipt invalid: Receipt inputs are stale", gate["reasons"])
        checks = self.controller.check("revision", "draft")
        self.assertEqual({item["id"] for item in checks["results"]}, {"guide", "new-answer"})
        self.assertEqual(checks["contract_hash"], self.task["contract_hash"])
        self.assertIn("Independent ac receipt invalid: Receipt inputs are stale",
            GateService(self.store).gate("revision", "draft")["reasons"])
        self.assertIn("Stage incomplete: draft", GateService(self.store).gate("revision")["reasons"])
        self.assertFalse((self.repo / "delivery.txt").exists())

    def test_new_current_candidate_replaces_historical_candidate_in_verifier_packet(self):
        self.revise()
        latest = self.capture_implementation()
        packet, _, history, proposal = self.packet()
        self.assertEqual(packet["coverage"]["missing"], [])
        self.assertEqual(proposal["status"], "VERIFIED_CAPTURE")
        self.assertEqual(proposal["execution_id"], latest["execution_id"])
        self.assertNotEqual(proposal["execution_id"], self.original_implementation["execution_id"])
        self.assertFalse(history["stages"][0]["current_identity"])

    def test_new_approved_proposal_on_same_contract_has_verified_historical_origin(self):
        self.approve(self.tasks.snapshot("revision"), "Owner reapproved the updated execution approach")
        packet, _, history, proposal = self.packet()
        self.assertEqual(packet["coverage"]["missing"], [])
        self.assertFalse(history["stages"][0]["current_identity"])
        self.assertEqual(proposal["status"], "HISTORICAL_VERIFIED_CAPTURE")

    def test_revision_without_current_approval_does_not_authorize_old_capture(self):
        self.revise(approve=False)
        packet, _, history, proposal = self.packet()
        self.assertEqual(history["stages"], [])
        self.assertEqual(proposal["status"], "INVALID")
        self.assertTrue(packet["coverage"]["missing"])

    def test_unknown_historical_identity_still_fails_closed_after_legitimate_revision(self):
        self.revise()
        record = read_record(self.store.db, "stage", "revision:draft")
        with self.store.db:
            write_record(self.store.db, "stage", "revision:draft", {**record["payload"], "proposal_hash": "unknown"},
                expected_revision=record["revision"])
        packet, _, history, proposal = self.packet()
        self.assertEqual(history["stages"], [])
        self.assertEqual(proposal["status"], "INVALID")
        self.assertTrue(any("Stage history unavailable" in item for item in packet["coverage"]["missing"]))

    def test_archived_authority_and_receipt_tampering_still_block_coverage(self):
        self.revise()
        records = (("runtime_records", "kind='task_version' AND key='revision:1'"),
            ("runtime_records", "kind='approval_version' AND key='revision:2'"),
            ("runtime_events", "event_key='revision:proposal:2'"),
            ("runtime_events", "event_key='revision:approval:4'"))
        for table, selector in records:
            with self.subTest(table=table, selector=selector):
                row = self.store.db.execute("SELECT * FROM " + table + " WHERE " + selector).fetchone()
                self.assertIsNotNone(row)
                with self.store.db:
                    self.store.db.execute("DELETE FROM " + table + " WHERE " + selector)
                packet, _, history, proposal = self.packet()
                self.assertEqual(history["stages"], [])
                self.assertEqual(proposal["status"], "INVALID")
                self.assertTrue(packet["coverage"]["missing"])
                with self.store.db:
                    placeholders = ",".join("?" for _ in row)
                    self.store.db.execute("INSERT INTO " + table + " VALUES(" + placeholders + ")", tuple(row))
        request = Path(self.original_implementation["artifacts"]["request"]["path"])
        request.write_text("tampered historical request")
        packet, _, history, proposal = self.packet()
        self.assertEqual(history["stages"], [])
        self.assertEqual(proposal["status"], "INVALID")
        self.assertTrue(packet["coverage"]["missing"])


if __name__ == "__main__":
    unittest.main()
