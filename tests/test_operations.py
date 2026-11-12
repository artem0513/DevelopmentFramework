import json
from pathlib import Path
import tempfile
import unittest

from ase.operations import OperationLedger
from ase.store import Store
from ase.tasks import TaskService
from test_task_contract_v2 import contract


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ase-operation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        work = self.root / "work"
        work.mkdir()
        self.store = Store(work, self.root / "state")
        self.addCleanup(self.store.close)
        TaskService(self.store).create(contract())
        self.ops = OperationLedger(self.store)

    def test_lost_create_ack_reconciles_without_duplicate(self):
        first = self.ops.prepare("approval-task", "draft", "create-resource", {"resource": "vm-1"})
        self.assertTrue(first["new"])
        resource = self.root / "resource.json"
        resource.write_text(json.dumps({"id": "vm-1", "running": True}))
        self.ops.started("create-resource")
        second = self.ops.prepare("approval-task", "draft", "create-resource", {"resource": "vm-1"})
        self.assertFalse(second["new"])
        self.assertEqual(second["state"], "UNKNOWN")
        result = self.ops.reconcile("create-resource", {"exists": resource.exists(), "resource_id": "vm-1", "proof_ref": str(resource)})
        self.assertEqual(result["state"], "APPLIED")
        self.assertFalse(self.ops.prepare("approval-task", "draft", "create-resource", {"resource": "vm-1"})["new"])

    def test_same_id_different_intent_conflicts(self):
        self.ops.prepare("approval-task", "draft", "create", {"resource": "vm-1"})
        with self.assertRaisesRegex(ValueError, "CONFLICT"):
            self.ops.prepare("approval-task", "draft", "create", {"resource": "vm-2"})

    def test_missing_observation_does_not_prove_absence(self):
        self.ops.prepare("approval-task", "draft", "create", {"resource": "vm-1"})
        with self.assertRaises(ValueError):
            self.ops.reconcile("create", {"exists": False})


if __name__ == "__main__":
    unittest.main()
