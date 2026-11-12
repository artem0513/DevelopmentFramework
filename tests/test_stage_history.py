"""Real protocol receipts prove history integrity, never semantic acceptance."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ase.adapters.codex import CodexAdapter
from ase.gates import source_snapshot
from ase.runner import Runner
from ase.state import read_record, write_record
from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import WorkflowController
import test_runner as transport
from test_journal import contract


class StageHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ase-stage-history-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "work"; self.root.mkdir()
        (self.root / "guide.txt").write_text("before")
        self.store = Store(self.root, self.base / "state")
        self.addCleanup(self.store.close)
        tasks = TaskService(self.store)
        task = tasks.create(contract("history"))
        task = tasks.propose("history", {"summary": "Write guide", "stage_ids": ["draft"]}, expected_revision=task["revision"])
        tasks.approve("history", proposal_hash=task["proposal_hash"], user_message_ref="test:approved", approved_stage_ids=["draft"])
        self.controller = WorkflowController(self.store)
        self.controller._save("baseline", "history", source_snapshot(self.store, tasks.snapshot("history")["contract"], task_id="history"))
        (self.root / "guide.txt").write_text("after")
        self.controller.check("history")

    def capture(self):
        self.assertTrue(hasattr(self.controller, "_capture_stage"), "stage proof capture is required")
        fixture = self.base / "fixture.py"; fixture.write_text(transport.FIXTURE)
        trusted = self.base / "trusted"; trusted.mkdir()
        runner = Runner({"executable": __import__("sys").executable, "executable_args": [str(fixture), "success", "codex"],
                         "trusted_cwd": str(trusted), "protocol_only": True}, self.store.root / "tasks" / "history" / "sessions")
        for role in ("implementation", "ac", "review"):
            request = self.controller._request("history", role, transport.SCHEMA, {"stage": {"id": "draft"}, "stage_id": "draft"})
            receipt = runner.run(CodexAdapter(), request, timeout=5)["receipt"]
            self.assertEqual(receipt["status"], "COMPLETE")
            self.controller._save("implementation" if role == "implementation" else "verification",
                                  "history" if role == "implementation" else "history:" + role, receipt)
        self.controller._capture_stage("history", "draft", {"status": "APPROVED", "fingerprint": "fixture-stage-result"})

    def history_packet(self):
        packet = self.controller._packet("history")
        item = next(item for item in packet["files"] if item["path"] == "_ase_context/stage_history.json")
        self.assertEqual(hashlib.sha256(item["content"].encode()).hexdigest(), item["sha256"])
        return json.loads(item["content"]), packet["coverage"]["missing"]

    def test_capture_retains_real_receipts_and_compact_verified_observations(self):
        self.capture()
        proof = read_record(self.store.db, "stage", "history:draft")["payload"]
        self.assertIn("checks", proof["proof"])
        history, missing = self.history_packet()
        self.assertEqual(missing, [])
        stage = history["stages"][0]
        self.assertEqual(stage["stage_id"], "draft")
        self.assertFalse(stage["semantic_approval"])
        self.assertEqual({role["evidence_kind"] for role in stage["roles"].values()}, {"protocol_only"})
        self.assertTrue(all(role["started_at_utc"] and role["ended_at_utc"] for role in stage["roles"].values()))
        self.assertIn("_ase_context/source_changes.json", {item["path"] for item in stage["observations"]})
        self.assertNotIn("guide.txt", {item["path"] for item in stage["observations"]})
        self.assertLess(len(json.dumps(history)), 262144)

    def test_tamper_and_stale_history_become_explicit_missing_coverage(self):
        self.capture()
        stage = read_record(self.store.db, "stage", "history:draft")["payload"]
        request = Path(stage["proof"]["roles"]["ac"]["artifacts"]["request"]["path"])
        request.write_text("tampered")
        history, missing = self.history_packet()
        self.assertEqual(history["stages"], [])
        self.assertTrue(any("Stage history unavailable: draft" in item for item in missing))
        record = read_record(self.store.db, "stage", "history:draft")
        with self.store.db:
            write_record(self.store.db, "stage", "history:draft", {**record["payload"], "proposal_hash": "stale"}, expected_revision=record["revision"])
        history, missing = self.history_packet()
        self.assertEqual(history["stages"], [])
        self.assertTrue(any("stale" in item.lower() for item in missing))

    def test_legacy_completed_stage_does_not_invent_historical_evidence(self):
        task = TaskService(self.store).snapshot("history")
        self.controller._save("stage", "history:draft", {"state": "COMPLETED", "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"]})
        history, missing = self.history_packet()
        self.assertEqual(history["stages"], [])
        self.assertTrue(any("Stage history unavailable: draft" in item for item in missing))


if __name__ == "__main__":
    unittest.main()
