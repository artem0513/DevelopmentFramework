"""Approval, partial scope and revision behavior against real SQLite state."""
import copy
from pathlib import Path
import tempfile
import unittest

from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import WorkflowController
from ase.state import read_record
import json


def contract(task_id="approval-task"):
    return {"id": task_id, "schema_version": 2, "profile": "document",
            "request": "Prepare a sourced operating guide and publish only after access is clarified.",
            "scope": {"resources": ["guide.md"], "effects": ["file_write"]},
            "acceptance": [{"id": "AC1", "expected": "The guide documents the confirmed workflow.",
                            "mandatory": True, "evidence_ids": ["guide"]}],
            "evidence": [{"id": "guide", "kind": "artifact", "path": "guide.md", "required": True}],
            "stages": [{"id": "draft", "scope": {"resources": ["guide.md"], "effects": ["file_write"]},
                        "acceptance_ids": ["AC1"], "depends_on": []},
                       {"id": "publish", "scope": {"resources": ["guide.md"], "effects": []},
                        "acceptance_ids": [], "depends_on": ["draft"], "unknown_ids": ["access"]}],
            "unknowns": [{"id": "access", "statement": "Publishing destination is not confirmed.",
                          "blocking_stage_ids": ["publish"], "status": "UNRESOLVED"}]}


class TaskContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ase-v2-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "work"
        self.repo.mkdir()
        self.store = Store(self.repo, self.root / "state")
        self.addCleanup(self.store.close)
        self.tasks = TaskService(self.store)

    def proposed(self):
        task = self.tasks.create(contract())
        return self.tasks.propose(task["task_id"], {"summary": "Draft the guide first; keep publishing deferred.",
                                                  "stage_ids": ["draft"]}, expected_revision=task["revision"])

    def test_execution_requires_exact_proposal(self):
        task = self.proposed()
        with self.assertRaisesRegex(ValueError, "approval"):
            self.tasks.require_approval(task["task_id"], "draft")
        with self.assertRaisesRegex(ValueError, "hash"):
            self.tasks.approve(task["task_id"], proposal_hash="0" * 64,
                               user_message_ref="chat:user:approval-1", approved_stage_ids=["draft"])
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"],
                           user_message_ref="chat:user:approval-1", approved_stage_ids=["draft"])
        self.assertEqual(self.tasks.require_approval(task["task_id"], "draft")["state"], "READY")
        with self.assertRaisesRegex(ValueError, "approval|unknown|scope"):
            self.tasks.require_approval(task["task_id"], "publish")

    def test_changed_material_proposal_invalidates_old_approval(self):
        task = self.proposed()
        task = self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"],
                                  user_message_ref="chat:user:approval-1", approved_stage_ids=["draft"])
        changed = self.tasks.propose(task["task_id"], {"summary": "Replace the guide with a migration policy.",
                                                       "stage_ids": ["draft"]}, expected_revision=task["revision"])
        self.assertNotEqual(changed["proposal_hash"], task["proposal_hash"])
        with self.assertRaisesRegex(ValueError, "approval"):
            self.tasks.require_approval(task["task_id"], "draft")

    def test_repeated_proposal_never_revives_stored_approval(self):
        task = self.proposed()
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="chat:approval", approved_stage_ids=["draft"])
        task = self.tasks.snapshot(task["task_id"])
        self.tasks.propose(task["task_id"], task["proposal"], expected_revision=task["revision"])
        with self.assertRaisesRegex(ValueError, "approval"):
            self.tasks.require_approval(task["task_id"], "draft")
        self.assertTrue(self.store.db.execute("SELECT 1 FROM runtime_records WHERE kind='approval_version' AND payload LIKE '%chat:approval%'").fetchone())

    def test_material_contract_revision_keeps_history_and_budget(self):
        task = self.proposed()
        old_hash = task["contract_hash"]
        changed = contract()
        changed["request"] = "Prepare a revised operating guide"
        result = self.tasks.revise(task["task_id"], changed, expected_revision=task["revision"])
        self.assertNotEqual(result["contract_hash"], old_hash)
        self.assertIsNone(result["proposal_hash"])
        self.assertEqual(self.tasks.store.db.execute("SELECT COUNT(*) FROM runtime_records WHERE kind='task_version'").fetchone()[0], 2)

    def test_extra_check_does_not_change_approved_goal(self):
        task = self.proposed()
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"],
                           user_message_ref="chat:user:approval-1", approved_stage_ids=["draft"])
        plan = self.tasks.execution_plan(task["task_id"])
        checks = copy.deepcopy(plan["checks"])
        checks.append({"id": "format", "kind": "artifact", "path": "guide.md", "required": True})
        result = self.tasks.update_execution_plan(task["task_id"], {"checks": checks},
                                                  expected_revision=plan["revision"])
        self.assertEqual(self.tasks.require_approval(task["task_id"], "draft")["proposal_hash"], task["proposal_hash"])
        self.assertNotEqual(result["plan_hash"], plan["plan_hash"])
        with self.assertRaisesRegex(ValueError, "required|baseline"):
            self.tasks.update_execution_plan(task["task_id"], {"checks": checks[1:]},
                                             expected_revision=result["revision"])

    def test_partial_scope_preserves_parent_obligations(self):
        task = self.proposed()
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"],
                           user_message_ref="chat:user:approval-1", approved_stage_ids=["draft"])
        self.assertEqual(self.tasks.snapshot(task["task_id"])["contract"]["unknowns"][0]["status"], "UNRESOLVED")
        with self.assertRaisesRegex(ValueError, "gate|APPROVED|obligations"):
            current = self.tasks.snapshot(task["task_id"])
            self.tasks.transition(task["task_id"], "COMPLETED", expected_revision=current["revision"], reason="looks done")
        self.assertNotEqual(self.tasks.snapshot(task["task_id"])["state"], "COMPLETED")

    def test_legacy_contract_has_no_new_authority(self):
        self.store.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)",
                              ("legacy", "Legacy task", "PLANNED", '{"request":"Legacy task","max_attempts":2}', "old", "old"))
        self.store.db.commit()
        self.assertTrue(self.tasks.snapshot("legacy")["legacy_v1"])
        with self.assertRaisesRegex(ValueError, "legacy|approval"):
            self.tasks.require_approval("legacy", "draft")

    def test_stale_updates_and_forged_approval_are_rejected(self):
        bad = contract()
        bad["approved"] = True
        with self.assertRaisesRegex(ValueError, "field|approval"):
            self.tasks.create(bad)
        task = self.proposed()
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.tasks.propose(task["task_id"], {"summary": "stale", "stage_ids": ["draft"]}, expected_revision=0)

    def test_stage_cannot_extend_parent_scope(self):
        bad = contract()
        bad["stages"][0]["scope"]["resources"].append("another-company")
        with self.assertRaisesRegex(ValueError, "scope"):
            self.tasks.create(bad)

    def test_dependency_cycle_is_rejected(self):
        bad = contract()
        bad["stages"][0]["depends_on"] = ["publish"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.tasks.create(bad)

    def test_explicit_child_approval_cannot_bypass_changed_parent_scope(self):
        parent = self.proposed()
        self.tasks.approve(parent["task_id"], proposal_hash=parent["proposal_hash"], user_message_ref="chat:parent", approved_stage_ids=["draft"])
        c = contract("child-explicit")
        c.update(parent_task_id=parent["task_id"], parent_stage_id="draft")
        c["stages"], c["unknowns"] = c["stages"][:1], []
        child = self.tasks.create(c)
        child = self.tasks.propose(child["task_id"], {"summary": "Child draft", "stage_ids": ["draft"]}, expected_revision=child["revision"])
        self.tasks.approve(child["task_id"], proposal_hash=child["proposal_hash"], user_message_ref="chat:child", approved_stage_ids=["draft"])
        changed = contract()
        changed["scope"]["effects"] = []
        for stage in changed["stages"]:
            stage["scope"]["effects"] = []
        parent = self.tasks.snapshot(parent["task_id"])
        parent = self.tasks.revise(parent["task_id"], changed, expected_revision=parent["revision"])
        parent = self.tasks.propose(parent["task_id"], {"summary": "Read only now", "stage_ids": ["draft"]}, expected_revision=parent["revision"])
        self.tasks.approve(parent["task_id"], proposal_hash=parent["proposal_hash"], user_message_ref="chat:parent-revised", approved_stage_ids=["draft"])
        with self.assertRaisesRegex(ValueError, "parent|scope"):
            self.tasks.require_approval(child["task_id"], "draft")

    def test_legacy_revision_preserves_attempt_ceiling_and_spent_cycles(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)", ("legacy-budget", "Existing task", "ESCALATED", '{"request":"Existing task","max_attempts":2}', "old", "old"))
        folder = self.store.root / "tasks" / "legacy-budget"
        folder.mkdir(parents=True)
        (folder / "progress.json").write_text(json.dumps({"attempts": 2, "implementation_completed": True}))
        task = self.tasks.revise("legacy-budget", contract("legacy-budget"), expected_revision=0)
        self.assertEqual(task["contract"]["execution_policy"]["max_task_cycles"], 2)
        task = self.tasks.propose(task["task_id"], {"summary": "Continue within the previous ceiling", "stage_ids": ["draft"]}, expected_revision=task["revision"])
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="chat:continue", approved_stage_ids=["draft"])
        with self.assertRaisesRegex(ValueError, "budget"):
            WorkflowController(self.store).reserve_cycle(task["task_id"], "draft")
        self.assertEqual(read_record(self.store.db, "budget", task["task_id"])["payload"]["task_cycles"], 2)
        self.assertTrue(read_record(self.store.db, "task_version", "legacy-budget:legacy")["payload"]["legacy_v1"])
        task = self.tasks.snapshot(task["task_id"])
        revised = self.tasks.revise(task["task_id"], contract("legacy-budget"), expected_revision=task["revision"])
        self.assertEqual(revised["contract"]["execution_policy"]["max_task_cycles"], 2)

    def test_malformed_legacy_progress_cannot_silently_reset_attempts(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)", ("legacy-bad", "Existing task", "ESCALATED", '{"request":"Existing task","max_attempts":2}', "old", "old"))
        folder = self.store.root / "tasks" / "legacy-bad"
        folder.mkdir(parents=True)
        (folder / "progress.json").write_text('{"attempts":true}')
        with self.assertRaisesRegex(ValueError, "legacy|Legacy"):
            self.tasks.revise("legacy-bad", contract("legacy-bad"), expected_revision=0)
        self.assertTrue(self.tasks.snapshot("legacy-bad")["legacy_v1"])


if __name__ == "__main__":
    unittest.main()
