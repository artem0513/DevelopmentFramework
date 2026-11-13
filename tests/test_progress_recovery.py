"""Durable recovery behavior against disposable tasks and actual evidence."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from ase.state import read_record, hash_value, transaction, write_record
from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import WorkflowController, DIAGNOSTIC_SCHEMA
from ase.adapters import get_adapter
from ase.runner import Runner
from ase.discovery import discover_capabilities, record_discovery
from unittest.mock import patch
from test_runner import FIXTURE
from test_task_contract_v2 import contract


class ProgressRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ase-progress-")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        workspace = base / "work"
        workspace.mkdir()
        self.store = Store(workspace, base / "state")
        self.addCleanup(self.store.close)
        self.tasks = TaskService(self.store)
        original = contract("recovery")
        original["stages"], original["unknowns"] = original["stages"][:1], []
        original["execution_policy"] = {"max_stage_cycles": 3, "max_task_cycles": 12}
        self.approve(original)
        self.controller = WorkflowController(self.store)

    def progress_task(self, *, checks=1, **policy):
        spec = contract("progress")
        spec["unknowns"], spec["stages"] = [], spec["stages"][:1]
        resources = ["part" + str(i) + ".md" for i in range(checks)]
        spec["scope"]["resources"] = resources
        spec["stages"][0]["scope"] = copy.deepcopy(spec["scope"])
        spec["evidence"] = [{"id": "part" + str(i), "kind": "artifact", "path": resource} for i, resource in enumerate(resources)]
        spec["acceptance"][0]["evidence_ids"] = [item["id"] for item in spec["evidence"]]
        spec["execution_policy"] = {"recovery_mode": "progress", **policy}
        return self.approve(spec)

    def approve(self, spec):
        spec = copy.deepcopy(spec)
        spec.setdefault("id", "recovery")
        old = read_record(self.store.db, "task", spec["id"])["payload"]
        task = (self.tasks.revise(spec["id"], spec, expected_revision=self.tasks.snapshot(spec["id"])["revision"])
                if old else self.tasks.create(spec))
        stages = [s["id"] for s in task["contract"]["stages"]]
        task = self.tasks.propose(task["task_id"], {"summary": "Perform the confirmed scope", "stage_ids": stages}, expected_revision=task["revision"])
        return self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="test:scope-authority", approved_stage_ids=stages)

    def captured_diagnostic(self, controller, task_id, stage_id, *, schema=None):
        """Real captured protocol receipt; this grants no semantic acceptance."""
        folder = self.store.state_home / "diagnostic-fixture"
        folder.mkdir(exist_ok=True)
        fixture = folder / "provider.py"
        fixture.write_text(FIXTURE.replace("payload = {'verdict':'PASS'}",
            "payload = {'cause':'Actual captured diagnostic cause','recommendation':'Run one corrective probe','requires_new_approval':False}"))
        runner = Runner({"executable": sys.executable, "executable_args": [str(fixture), "success", "codex"],
            "trusted_cwd": str(folder), "protocol_only": True}, self.store.root / "tasks" / task_id / "sessions")
        return runner.run(get_adapter("codex"), controller._request(task_id, "diagnostic", schema or DIAGNOSTIC_SCHEMA, {"stage_id": stage_id}), timeout=3)

    def authorize_diagnostic(self, controller, task_id, stage_id):
        diagnosis = self.captured_diagnostic(controller, task_id, stage_id)
        self.assertTrue(controller._complete_diagnostic(task_id, stage_id, diagnosis))
        return diagnosis

    def test_reworded_review_does_not_hide_repeated_failing_criterion(self):
        for index in range(3):
            failure = self.controller.record_failure("recovery", "draft", ["Mandatory AC failed: AC1", "P2: Different review wording " + str(index)])
        self.assertEqual(failure["repetitions"], 3)

    def test_renamed_stage_retains_explicit_cycle_allowance(self):
        for _ in range(3):
            self.controller.reserve_cycle("recovery", "draft")
        changed = copy.deepcopy(self.tasks.snapshot("recovery")["contract"])
        changed["stages"][0]["id"] = "renamed"
        self.approve(changed)
        with self.assertRaisesRegex(ValueError, "budget"):
            WorkflowController(self.store).reserve_cycle("recovery", "renamed")

    def test_relabelled_criterion_and_decomposed_stage_retain_allowance(self):
        for _ in range(3):
            self.controller.reserve_cycle("recovery", "draft")
        changed = copy.deepcopy(self.tasks.snapshot("recovery")["contract"])
        changed["acceptance"][0].update(id="RENAMED", expected="Reworded confirmed workflow description")
        changed["stages"][0].update(id="part-one", acceptance_ids=["RENAMED"])
        changed["stages"].append({**copy.deepcopy(changed["stages"][0]), "id": "part-two", "depends_on": ["part-one"]})
        self.approve(changed)
        with self.assertRaisesRegex(ValueError, "budget"):
            self.controller.reserve_cycle("recovery", "part-one")

    def test_verified_original_checks_allow_more_than_three_total_candidates(self):
        # The root is replaced before recovery starts; this is its original goal.
        spec = contract("long-progress")
        spec["unknowns"], spec["stages"] = [], spec["stages"][:1]
        spec["scope"]["resources"] = ["part" + str(i) + ".md" for i in range(6)]
        spec["stages"][0]["scope"] = copy.deepcopy(spec["scope"])
        spec["evidence"] = [{"id": "part" + str(i), "kind": "artifact", "path": path} for i, path in enumerate(spec["scope"]["resources"])]
        spec["acceptance"][0]["evidence_ids"] = [item["id"] for item in spec["evidence"]]
        spec["execution_policy"] = {"recovery_mode": "progress"}
        self.approve(spec)
        for index in range(6):
            reservation = self.controller.reserve_cycle("long-progress", "draft")
            (self.store.repo / ("part" + str(index) + ".md")).write_text("Verified artifact\n")
            self.controller.check("long-progress", "draft")
            failure = self.controller.record_failure("long-progress", "draft", ["Mandatory AC failed: AC1"])
            self.assertFalse(failure["diagnostic_required"])
        self.assertEqual(reservation["stage_cycles"], 6)

    def test_three_stalled_cycles_diagnose_once_and_unsuccessful_probe_stops_resume(self):
        self.progress_task()
        for index in range(3):
            self.controller.reserve_cycle("progress", "draft")
            (self.store.repo / "part0.md").write_text("Edited bytes " + str(index))
            # No current check: edits and prose cannot establish progress.
            failure = self.controller.record_failure("progress", "draft", ["Mandatory AC failed: AC1", "P2: " + str(index)])
            self.assertEqual(failure["diagnostic_required"], index == 2)
        self.controller.reserve_diagnostic("progress", "draft")
        self.authorize_diagnostic(self.controller, "progress", "draft")
        self.controller.reserve_cycle("progress", "draft")
        failure = self.controller.record_failure("progress", "draft", ["Mandatory AC failed: AC1"])
        self.assertTrue(failure["stopped"])
        resumed = WorkflowController(self.store)
        with self.assertRaisesRegex(ValueError, "progress|probe"):
            resumed.reserve_cycle("progress", "draft")
        with self.assertRaisesRegex(ValueError, "diagnostic|progress"):
            resumed.reserve_diagnostic("progress", "draft")

    def test_oscillating_verified_check_does_not_create_new_progress(self):
        original = contract("oscillation")
        original["unknowns"], original["stages"] = [], original["stages"][:1]
        original["execution_policy"] = {"recovery_mode": "progress"}
        self.approve(original)
        path = self.store.repo / "guide.md"
        path.write_text("Pass once")
        self.controller.check("oscillation", "draft")
        self.assertFalse(self.controller.record_failure("oscillation", "draft", ["Mandatory AC failed: AC1"])["diagnostic_required"])
        for index in range(3):
            if index % 2 == 0:
                path.unlink(missing_ok=True)
            else:
                path.write_text("Pass again with changed source")
            self.controller.check("oscillation", "draft")
            failure = self.controller.record_failure("oscillation", "draft", ["Mandatory AC failed: AC1"])
        self.assertTrue(failure["diagnostic_required"])
        self.assertEqual(failure["stagnation"], 3)

    def test_delegation_shares_root_hard_cap_after_rename(self):
        for _ in range(2):
            self.controller.reserve_cycle("recovery", "draft")
        child = contract("delegate")
        child["unknowns"], child["stages"] = [], child["stages"][:1]
        child.update(parent_task_id="recovery", parent_stage_id="draft")
        child["stages"][0]["id"] = "subtask"
        self.tasks.create(child)
        self.controller.reserve_cycle("delegate", "subtask")
        with self.assertRaisesRegex(ValueError, "budget"):
            self.controller.reserve_cycle("recovery", "draft")

    def test_delayed_visibility_blocks_duplicate_external_submission(self):
        spec = contract("delayed-resource")
        spec["profile"] = "infrastructure"
        spec["unknowns"] = []
        spec["scope"] = {"resources": ["submissions"], "effects": ["command"]}
        spec["evidence"] = [{"id": "guide", "kind": "artifact", "path": "submissions"}]
        spec["stages"] = [{"id": "provision", "scope": spec["scope"], "acceptance_ids": ["AC1"], "depends_on": [], "actions": [{
            "id": "submit", "argv": [sys.executable, "-c", "from pathlib import Path;p=Path('submissions');p.write_text(p.read_text()+'x' if p.exists() else 'x')"],
            "observation_argv": [sys.executable, "-c", "print('{\"exists\":false}')"]}]}]
        task = self.approve(spec)
        stage = task["contract"]["stages"][0]
        with self.assertRaises(ValueError):
            self.controller.execute_actions("delayed-resource", stage)
        with self.assertRaisesRegex(ValueError, "UNKNOWN|uncertain|Unresolved"):
            self.controller.execute_actions("delayed-resource", stage)
        self.assertEqual((self.store.repo / "submissions").read_text(), "x")

    def test_actual_observation_preserves_authoritative_absence(self):
        folder = self.store.root / "observation-test"
        folder.mkdir()
        observation = self.controller._observe_action("recovery", {"id": "draft"},
            {"observation_argv": [sys.executable, "-c", "print('{\"exists\":false,\"absence_confirmed\":true}')"]}, folder)
        self.assertIs(observation.get("absence_confirmed"), True)

    def loop_fixture(self, *, supported=True, progress_on_probe=False, hard_cap=None):
        """Protocol children test orchestration; a gate double never approves."""
        spec = contract("loop")
        spec["unknowns"], spec["stages"] = [], spec["stages"][:1]
        spec["execution_policy"] = {"recovery_mode": "progress", "max_stage_cycles": hard_cap}
        task = self.approve(spec)
        record_discovery(self.store, "loop", discover_capabilities(self.store.project, scope={"resources": ["guide.md"]}, backend_config={}), expected_revision=0)
        folder = self.store.state_home / "loop-fixture"
        folder.mkdir()
        body = """
schema = json.loads(pathlib.Path(args[9]).read_text())
if 'edits' in schema['properties']:
 counter = pathlib.Path('candidates')
 count = int(counter.read_text()) + 1 if counter.exists() else 1
 counter.write_text(str(count))
 edits = [{'path':'guide.md','operation':'write','before_sha256':'','content':'Verified original artifact'}] if count == 4 and PROGRESS else []
 payload = {'edits':edits,'summary':'Controlled candidate'}
elif 'cause' in schema['properties']:
 payload = {'cause':'Actual evidence supports this cause' if SUPPORTED else '', 'recommendation':'Run a corrective probe' if SUPPORTED else '', 'requires_new_approval':False}
""".replace("PROGRESS", repr(progress_on_probe)).replace("SUPPORTED", repr(supported))
        fixture = folder / "provider.py"
        fixture.write_text(FIXTURE.replace("payload = {'verdict':'PASS'}", "payload = {'verdict':'PASS'}\n" + body))
        runner = Runner({"executable": sys.executable, "executable_args": [str(fixture), "success", "codex"],
                         "trusted_cwd": str(folder), "protocol_only": True}, self.store.root / "tasks/loop/sessions")
        controller = WorkflowController(self.store, runner=runner)
        verdict = {"status": "REJECTED", "blocked_reasons": [], "rejected_reasons": ["Mandatory AC failed: AC1"]}
        return controller, verdict

    def test_workflow_stops_after_one_unsuccessful_corrective_probe_and_resume(self):
        controller, rejected = self.loop_fixture()
        with patch.object(controller, "verify", return_value=rejected):
            result = controller.run("loop")
            resumed = controller.run("loop", resume=True)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(resumed["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 4)
        roles = [json.loads(path.read_text())["role"] for path in (self.store.root / "tasks/loop/sessions").glob("*/request.json")]
        self.assertEqual(roles.count("diagnostic"), 1)

    def test_verified_corrective_probe_continues_until_explicit_hard_cap(self):
        controller, rejected = self.loop_fixture(progress_on_probe=True, hard_cap=5)
        with patch.object(controller, "verify", return_value=rejected):
            result = controller.run("loop")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 5)
        self.assertEqual(read_record(self.store.db, "recovery", "loop")["payload"]["groups"]["draft"]["stagnation"], 1)

    def test_unsupported_diagnostic_does_not_authorize_corrective_probe(self):
        controller, rejected = self.loop_fixture(supported=False)
        with patch.object(controller, "verify", return_value=rejected):
            result = controller.run("loop")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 3)

    def test_approved_stage_commits_original_check_high_water(self):
        controller, _ = self.loop_fixture(progress_on_probe=True)
        (self.store.repo / "guide.md").write_text("Already satisfies the check")
        def approved(*args):
            from ase.gates import source_snapshot
            return {"status": "APPROVED", "fingerprint": source_snapshot(self.store, self.tasks.snapshot("loop")["contract"])["fingerprint"]}
        with patch.object(controller, "verify", side_effect=approved):
            controller._run("loop")
        history = read_record(self.store.db, "recovery", "loop")["payload"]
        self.assertEqual(len(history["high_water"]), 1)

    def test_revision_cannot_expand_an_existing_explicit_hard_cap(self):
        for _ in range(3):
            self.controller.reserve_cycle("recovery", "draft")
        changed = copy.deepcopy(self.tasks.snapshot("recovery")["contract"])
        changed["execution_policy"].update(recovery_mode="progress", max_stage_cycles=None, max_task_cycles=None)
        self.approve(changed)
        with self.assertRaisesRegex(ValueError, "budget"):
            self.controller.reserve_cycle("recovery", "draft")

    def test_unrelated_stage_progress_cannot_repeat_an_unchanged_diagnostic(self):
        spec = contract("separate")
        spec["unknowns"] = []
        spec["scope"]["resources"].append("second.md")
        spec["evidence"].append({"id": "second", "kind": "artifact", "path": "second.md"})
        spec["acceptance"].append({"id": "AC2", "expected": "Second result exists", "evidence_ids": ["second"]})
        spec["stages"][1].update(id="second", depends_on=[], acceptance_ids=["AC2"], unknown_ids=[],
                                   scope={"resources": ["second.md"], "effects": ["file_write"]})
        spec["execution_policy"] = {"recovery_mode": "progress"}
        self.approve(spec)
        for _ in range(3):
            self.controller.record_failure("separate", "draft", ["Mandatory AC failed: AC1"])
        self.controller.reserve_diagnostic("separate", "draft")
        (self.store.repo / "second.md").write_text("Verified second obligation")
        self.controller.check("separate", "second")
        self.controller.record_failure("separate", "second", ["Mandatory AC failed: AC2"])
        with self.assertRaisesRegex(ValueError, "diagnostic|progress"):
            self.controller.reserve_diagnostic("separate", "draft")

    def test_stagnation_threshold_requires_diagnosis_before_further_mutation(self):
        self.progress_task()
        for _ in range(3):
            self.controller.record_failure("progress", "draft", ["Mandatory AC failed: AC1"])
        with self.assertRaisesRegex(ValueError, "diagnos|reassess"):
            self.controller.reserve_cycle("progress", "draft")

    def test_saved_policy_without_mode_retains_fixed_caps_and_prior_failures(self):
        spec = contract("saved-fixed")
        spec["stages"], spec["unknowns"] = spec["stages"][:1], []
        spec["execution_policy"] = {"recovery_mode": "fixed"}
        task = self.tasks.create(spec)
        # Construct the serialized pre-upgrade v2 contract before its approval.
        old = read_record(self.store.db, "task", "saved-fixed")
        payload = old["payload"]
        payload["contract"]["execution_policy"].pop("recovery_mode")
        payload["contract"]["execution_policy"].pop("stagnation_limit")
        payload["contract_hash"] = hash_value(payload["contract"])
        with transaction(self.store.db) as db:
            write_record(db, "task", "saved-fixed", payload, expected_revision=old["revision"])
            version = read_record(db, "task_version", "saved-fixed:1")
            write_record(db, "task_version", "saved-fixed:1", payload, expected_revision=version["revision"])
            write_record(db, "budget", "saved-fixed", {"task_cycles": 1, "stages": {"saved-fixed:draft": 1}, "diagnostics": {},
                "failures": {"saved-fixed:draft:" + hash_value(["Mandatory AC failed: AC1"]): 1}}, expected_revision=0)
        task = self.tasks.propose("saved-fixed", {"summary": "Continue existing approved goal", "stage_ids": ["draft"]}, expected_revision=self.tasks.snapshot("saved-fixed")["revision"])
        self.tasks.approve("saved-fixed", proposal_hash=task["proposal_hash"], user_message_ref="test:historic-authority", approved_stage_ids=["draft"])
        failure = self.controller.record_failure("saved-fixed", "draft", ["Mandatory AC failed: AC1"])
        self.assertTrue(failure["diagnostic_required"])
        self.assertEqual(failure["repetitions"], 2)
        for _ in range(2):
            self.controller.reserve_cycle("saved-fixed", "draft")
        with self.assertRaisesRegex(ValueError, "budget"):
            self.controller.reserve_cycle("saved-fixed", "draft")
        self.controller.reserve_diagnostic("saved-fixed", "draft")
        with self.assertRaisesRegex(ValueError, "diagnostic"):
            self.controller.reserve_diagnostic("saved-fixed", "draft")

    def test_stale_checks_do_not_reset_stagnation(self):
        original = contract("stale-proof")
        original["unknowns"], original["stages"] = [], original["stages"][:1]
        original["execution_policy"] = {"recovery_mode": "progress"}
        self.approve(original)
        (self.store.repo / "guide.md").write_text("Checked before change")
        self.controller.check("stale-proof", "draft")
        (self.store.repo / "guide.md").write_text("Different unchecked bytes")
        for _ in range(3):
            failure = self.controller.record_failure("stale-proof", "draft", ["Mandatory AC failed: AC1"])
        self.assertEqual(failure["newly_verified"], [])
        self.assertTrue(failure["diagnostic_required"])

    def test_swapping_existing_stage_labels_preserves_exhausted_original_obligation(self):
        spec = contract("swapped")
        spec["unknowns"] = []
        spec["scope"]["resources"] = ["left.md", "right.md"]
        spec["evidence"] = [{"id": side, "kind": "artifact", "path": side + ".md"} for side in ("left", "right")]
        spec["acceptance"] = [{"id": side.upper(), "expected": side + " original obligation", "evidence_ids": [side]} for side in ("left", "right")]
        spec["stages"] = [{"id": side, "scope": {"resources": [side + ".md"], "effects": ["file_write"]},
            "acceptance_ids": [side.upper()], "depends_on": []} for side in ("left", "right")]
        spec["execution_policy"] = {"recovery_mode": "fixed", "max_stage_cycles": 1}
        self.approve(spec)
        self.controller.reserve_cycle("swapped", "left")
        # A truly distinct unchanged obligation retains its own allowance.
        self.controller.reserve_cycle("swapped", "right")
        changed = copy.deepcopy(self.tasks.snapshot("swapped")["contract"])
        for stage in changed["stages"]:
            stage["id"] = "right" if stage["id"] == "left" else "left"
        self.approve({**changed, "id": "swapped"})
        with self.assertRaisesRegex(ValueError, "budget"):
            self.controller.reserve_cycle("swapped", "right")

    def test_swapping_into_unused_existing_stage_label_cannot_reset_allowance(self):
        spec = contract("swap-unused")
        spec["unknowns"] = []
        spec["scope"]["resources"] = ["guide.md", "right.md"]
        spec["evidence"].append({"id": "right", "kind": "artifact", "path": "right.md"})
        spec["acceptance"].append({"id": "AC2", "expected": "Distinct right obligation", "evidence_ids": ["right"]})
        spec["stages"][1].update(id="right", depends_on=[], unknown_ids=[], acceptance_ids=["AC2"],
            scope={"resources": ["right.md"], "effects": ["file_write"]})
        spec["execution_policy"] = {"recovery_mode": "fixed", "max_stage_cycles": 1}
        self.approve(spec)
        self.controller.reserve_cycle("swap-unused", "draft")
        changed = copy.deepcopy(self.tasks.snapshot("swap-unused")["contract"])
        changed["stages"][0]["id"], changed["stages"][1]["id"] = "right", "draft"
        self.approve({**changed, "id": "swap-unused"})
        with self.assertRaisesRegex(ValueError, "budget"):
            self.controller.reserve_cycle("swap-unused", "right")

    def test_reserved_corrective_probe_cannot_be_reserved_again_after_restart(self):
        self.progress_task()
        for _ in range(3):
            self.controller.reserve_cycle("progress", "draft")
            self.controller.record_failure("progress", "draft", ["Mandatory AC failed: AC1"])
        self.controller.reserve_diagnostic("progress", "draft")
        self.authorize_diagnostic(self.controller, "progress", "draft")
        self.controller.reserve_cycle("progress", "draft")
        with self.assertRaisesRegex(ValueError, "probe|reconcile|inflight"):
            WorkflowController(self.store).reserve_cycle("progress", "draft")

    def test_crash_after_corrective_edits_reconciles_without_fifth_candidate(self):
        controller, rejected = self.loop_fixture()
        save = controller._save
        crashed = False
        def crash_at_checkpoint(kind, key, value):
            nonlocal crashed
            budget = read_record(self.store.db, "budget", "loop")["payload"] or {}
            if kind == "cycle" and value.get("phase") == "CHECKING" and budget.get("task_cycles") == 4 and not crashed:
                crashed = True
                raise KeyboardInterrupt()
            return save(kind, key, value)
        with patch.object(controller, "verify", return_value=rejected), patch.object(controller, "_save", side_effect=crash_at_checkpoint):
            self.assertEqual(controller.run("loop")["status"], "BLOCKED")
            self.assertEqual(controller.run("loop", resume=True)["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 4)

    def test_explicit_resume_observes_new_original_progress_before_mutation(self):
        controller, rejected = self.loop_fixture(hard_cap=5)
        with patch.object(controller, "verify", return_value=rejected):
            controller.run("loop")
            (self.store.repo / "guide.md").write_text("Externally repaired original artifact")
            resumed = controller.run("loop", resume=True)
        self.assertEqual(resumed["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 5)
        history = read_record(self.store.db, "recovery", "loop")["payload"]
        self.assertTrue(history["high_water"])

    def test_crashed_probe_resume_runs_no_external_action(self):
        controller, rejected = self.loop_fixture()
        for _ in range(3):
            controller.reserve_cycle("loop", "draft")
            controller.record_failure("loop", "draft", ["Mandatory AC failed: AC1"])
        controller.reserve_diagnostic("loop", "draft")
        self.authorize_diagnostic(controller, "loop", "draft")
        controller.reserve_cycle("loop", "draft")
        with patch.object(controller, "verify", return_value=rejected), patch.object(controller, "execute_actions", side_effect=AssertionError("Resume must observe without actions")):
            self.assertEqual(controller.run("loop", resume=True)["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 4)

    def test_stopped_resume_preserves_unknown_external_operation(self):
        from ase.gates import GateService
        from ase.operations import OperationLedger
        controller, rejected = self.loop_fixture()
        with patch.object(controller, "verify", return_value=rejected):
            controller.run("loop")
        ledger = OperationLedger(self.store)
        ledger.prepare("loop", "draft", "unknown-effect", {"request": "Already submitted external effect"})
        ledger.started("unknown-effect")
        (self.store.repo / "guide.md").write_text("Externally repaired original artifact")
        with patch.object(controller, "verify", side_effect=lambda task_id, stage_id: GateService(self.store).gate(task_id, stage_id)), patch.object(controller, "execute_actions", side_effect=AssertionError("Unknown effects cannot be replayed")):
            resumed = controller.run("loop", resume=True)
        self.assertTrue(any("Uncertain external operation" in reason for reason in resumed["blocked_reasons"]))
        self.assertEqual(read_record(self.store.db, "operation", "unknown-effect")["payload"]["state"], "UNKNOWN")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 4)

    def test_actual_observation_preserves_provider_attempt_identity(self):
        folder = self.store.root / "provider-observation-test"
        folder.mkdir()
        observation = self.controller._observe_action("recovery", {"id": "draft"},
            {"observation_argv": [sys.executable, "-c", "print('{\"exists\":false,\"absence_confirmed\":true,\"provider_attempt_id\":\"request-1\"}')"]}, folder)
        self.assertEqual(observation.get("provider_attempt_id"), "request-1")

    def test_provider_terminal_absence_is_bound_to_captured_submission(self):
        spec = contract("bound-submission")
        spec["profile"], spec["unknowns"] = "infrastructure", []
        spec["scope"] = {"resources": ["submissions"], "effects": ["command"]}
        spec["evidence"] = [{"id": "guide", "kind": "artifact", "path": "submissions"}]
        spec["stages"] = [{"id": "provision", "scope": spec["scope"], "acceptance_ids": ["AC1"], "depends_on": [], "actions": [{
            "id": "submit", "argv": [sys.executable, "-c", "from pathlib import Path;import json;p=Path('submissions');n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n));print(json.dumps({'provider_attempt_id':'request-'+str(n)}))"],
            "observation_argv": [sys.executable, "-c", "from pathlib import Path;import json;print(json.dumps({'exists':False,'absence_confirmed':True,'provider_attempt_id':'request-1'} if Path('submissions').exists() else {'exists':False}))"]}]}]
        task = self.approve(spec)
        stage = task["contract"]["stages"][0]
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.controller.execute_actions("bound-submission", stage)
        with self.assertRaisesRegex(ValueError, "UNKNOWN|uncertain|Unresolved"):
            self.controller.execute_actions("bound-submission", stage)
        self.assertEqual((self.store.repo / "submissions").read_text(), "2")

    def test_stopped_resume_can_verify_new_semantic_ac_with_checks_already_at_high_water(self):
        """A semantic-proof boundary double isolates read-only recovery control flow."""
        from ase.gates import source_snapshot
        controller, rejected = self.loop_fixture(hard_cap=5)
        (self.store.repo / "guide.md").write_text("Artifact exists but original semantic AC still fails")
        with patch.object(controller, "verify", return_value=rejected):
            controller.run("loop")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 5)
        self.assertTrue(read_record(self.store.db, "recovery", "loop")["payload"]["groups"]["draft"]["stopped"])
        (self.store.repo / "guide.md").write_text("Externally corrected original semantic requirement")
        observed = []
        def fresh_verification(task_id, stage_id):
            observed.append((task_id, stage_id))
            task = self.tasks.snapshot(task_id)
            plan = self.tasks.execution_plan(task_id)
            checks = read_record(self.store.db, "checks", task_id)["payload"]
            bindings = {"fingerprint": source_snapshot(self.store, task["contract"], task_id=task_id)["fingerprint"],
                "contract_sha256": task["contract_hash"], "proposal_sha256": task["proposal_hash"], "plan_sha256": plan["plan_hash"],
                "check_sha256": hash_value(checks), "workspace_id": self.store.workspace_id}
            controller._save("verification", task_id + ":ac", {"role": "ac", "artifact_root": str(self.store.root / "semantic-proof-double"),
                "execution_id": "fresh-ac-execution", "bindings": bindings})
            return {"status": "REJECTED", "blocked_reasons": [], "rejected_reasons": ["P2: A separate review obligation remains"]}
        def semantic_proof(receipt, *, expected_bindings):
            self.assertEqual(receipt["bindings"], expected_bindings)
            expected = self.tasks.snapshot("loop")["contract"]["acceptance"][0]["expected"]
            return {"evidence_kind": "independent_cli", "execution_id": receipt["execution_id"],
                "result": {"status": "COMPLETE", "session_id": "fresh-ac-session", "payload": {"criteria": [{"id": "AC1", "status": "PASS",
                    "expected": expected, "evidence": ["Fresh corrected artifact"], "method": "Controlled proof boundary", "missing_coverage": []}]}}}
        with patch.object(controller, "verify", side_effect=fresh_verification), patch("ase.workflow.verify_receipt", side_effect=semantic_proof), patch.object(controller, "execute_actions", side_effect=AssertionError("Read-only resume must not execute actions")):
            resumed = controller.run("loop", resume=True)
        self.assertEqual(observed, [("loop", "draft")])
        self.assertEqual(resumed["status"], "BLOCKED")
        history = read_record(self.store.db, "recovery", "loop")["payload"]
        self.assertIn("ac:AC1", history["high_water"])
        self.assertFalse(history["groups"]["draft"]["stopped"])
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 5)

    def test_stopped_resume_verifies_then_remains_stopped_without_new_original_progress(self):
        controller, rejected = self.loop_fixture(hard_cap=5)
        (self.store.repo / "guide.md").write_text("Executable check already passes")
        with patch.object(controller, "verify", return_value=rejected):
            controller.run("loop")
        (self.store.repo / "guide.md").write_text("Changed prose without verified semantic progress")
        with patch.object(controller, "verify", return_value=rejected) as verifier, patch.object(controller, "execute_actions", side_effect=AssertionError("Read-only resume must not execute actions")):
            controller.run("loop", resume=True)
        self.assertEqual(verifier.call_count, 1)
        self.assertTrue(read_record(self.store.db, "recovery", "loop")["payload"]["groups"]["draft"]["stopped"])
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 5)

    def unresolved_effect(self, task_id):
        spec = contract(task_id)
        spec["profile"], spec["unknowns"] = "infrastructure", []
        spec["scope"] = {"resources": ["submissions"], "effects": ["command"]}
        spec["evidence"] = [{"id": "guide", "kind": "artifact", "path": "submissions"}]
        spec["stages"] = [{"id": "provision", "scope": spec["scope"], "acceptance_ids": ["AC1"], "depends_on": [], "actions": [{
            "id": "submit", "argv": [sys.executable, "-c", "from pathlib import Path;p=Path('submissions');p.write_text(p.read_text()+'x' if p.exists() else 'x')"],
            "observation_argv": [sys.executable, "-c", "print('{\"exists\":false}')"]}]}]
        task = self.approve(spec)
        with self.assertRaises(ValueError):
            self.controller.execute_actions(task_id, task["contract"]["stages"][0])
        return task

    def reopen_store(self):
        workspace, state_home = self.store.repo, self.store.state_home
        self.store.close()
        self.store = Store(workspace, state_home)
        self.addCleanup(self.store.close)
        self.tasks, self.controller = TaskService(self.store), WorkflowController(self.store)

    def test_stage_rename_and_store_reopen_cannot_repeat_unknown_external_effect(self):
        task = self.unresolved_effect("renamed-effect")
        spec = copy.deepcopy(task["contract"])
        spec["id"] = "renamed-effect"
        spec["stages"][0]["id"] = "renamed-provision"
        task = self.approve(spec)
        self.reopen_store()
        with self.assertRaises(ValueError):
            self.controller.execute_actions("renamed-effect", task["contract"]["stages"][0])
        self.assertEqual((self.store.repo / "submissions").read_text(), "x")

    def test_action_relabel_and_store_reopen_cannot_repeat_unknown_external_effect(self):
        task = self.unresolved_effect("relabelled-effect")
        spec = copy.deepcopy(task["contract"])
        spec["id"] = "relabelled-effect"
        spec["stages"][0]["id"] = "renamed-provision"
        spec["stages"][0]["actions"][0]["id"] = "renamed-submit"
        task = self.approve(spec)
        self.reopen_store()
        with self.assertRaises(ValueError):
            self.controller.execute_actions("relabelled-effect", task["contract"]["stages"][0])
        self.assertEqual((self.store.repo / "submissions").read_text(), "x")

    def test_delegation_and_store_reopen_cannot_repeat_unknown_external_effect(self):
        task = self.unresolved_effect("effect-parent")
        child = copy.deepcopy(task["contract"])
        child.update(id="effect-delegate", parent_task_id="effect-parent", parent_stage_id="provision")
        child["stages"][0]["id"] = "delegate-provision"
        child["stages"][0]["actions"][0]["id"] = "delegate-submit"
        delegated = self.tasks.create(child)
        self.reopen_store()
        with self.assertRaises(ValueError):
            self.controller.execute_actions("effect-delegate", delegated["contract"]["stages"][0])
        self.assertEqual((self.store.repo / "submissions").read_text(), "x")

    def test_unrelated_root_does_not_inherit_another_tasks_operation_identity(self):
        self.unresolved_effect("separate-effect-one")
        self.reopen_store()
        self.unresolved_effect("separate-effect-two")
        self.assertEqual((self.store.repo / "submissions").read_text(), "xx")

    def test_observation_binding_is_stamped_from_actual_caller_and_full_action(self):
        folder = self.store.root / "bound-observation-test"
        folder.mkdir()
        action = {"id": "current-action", "argv": ["configured-effect"], "timeout": 30,
            "observation_argv": [sys.executable, "-c", "print('{\"exists\":false,\"observing_task_id\":\"forged\",\"action_hash\":\"forged\"}')"]}
        observation = self.controller._observe_action("recovery", {"id": "draft"}, action, folder)
        self.assertEqual(observation.get("action_hash"), hash_value(action))
        self.assertEqual(observation.get("observing_task_id"), "recovery")
        self.assertEqual(observation.get("observing_stage_id"), "draft")
        self.assertEqual(observation.get("observing_workspace_id"), self.store.workspace_id)

    @unittest.skipUnless(os.name == "nt", "requires disposable Windows DPAPI fixture")
    def test_completed_delegated_action_refreshes_canonical_observation_without_replay(self):
        """A completed-state fixture isolates credential facts, never semantic approval."""
        from test_credential_integration import CredentialIntegrationTests
        from ase.operations import OperationLedger
        fixture = CredentialIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        parent = fixture.create(fixture.contract("ca-p", action=True))
        fixture.controller.execute_actions(parent["task_id"], parent["contract"]["stages"][0])
        spec = fixture.contract("ca-c", action=True)
        spec.update(parent_task_id=parent["task_id"], parent_stage_id="work")
        spec["stages"][0]["id"] = "delegated-work"
        child = fixture.tasks.create(spec)
        stage = child["contract"]["stages"][0]
        fixture.controller.execute_actions(child["task_id"], stage)
        action = stage["actions"][0]
        intent = {"project_id": fixture.store.project_id, "workspace_id": fixture.store.workspace_id, "action": action}
        ledger = OperationLedger(fixture.store)
        canonical = ledger.lookup(child["task_id"], stage["id"], hash_value({"task": child["task_id"], "stage": stage["id"], "action": action["id"]}), intent)
        before = canonical["observation"]["access"]
        current = fixture.tasks.snapshot(child["task_id"])
        payload = {key: value for key, value in current.items() if key not in {"revision", "legacy_v1"}}
        payload["state"] = "COMPLETED"
        with transaction(fixture.store.db):
            fixture.tasks._save(payload, current["revision"])
        fixture.vault.update_secrets("lab", fixture.target, {"token": "rotated-canonical-fixture-token"}, 1)
        fixture.controller.check(child["task_id"])
        refreshed = read_record(fixture.store.db, "operation", canonical["action_id"])["payload"]
        self.assertNotEqual(refreshed["observation"]["access"], before)
        self.assertEqual((fixture.repo / "resource.counter").read_text(), "1")
        self.assertEqual(fixture.tasks.snapshot(child["task_id"])["state"], "COMPLETED")

    def failed_diagnostic_reproposal(self, failure_kind):
        controller, rejected = self.loop_fixture(supported=failure_kind != "unsupported")
        run = controller.runner.run
        def diagnostic_failure(adapter, request, **kwargs):
            if request["role"] == "diagnostic" and failure_kind == "interrupted":
                raise KeyboardInterrupt()
            if request["role"] == "diagnostic" and failure_kind == "failed":
                previous = controller.runner.config["executable_args"][1]
                controller.runner.config["executable_args"][1] = "exit"
                try:
                    return run(adapter, request, **kwargs)
                finally:
                    controller.runner.config["executable_args"][1] = previous
            return run(adapter, request, **kwargs)
        with patch.object(controller, "verify", return_value=rejected), patch.object(controller.runner, "run", side_effect=diagnostic_failure):
            self.assertEqual(controller.run("loop")["status"], "BLOCKED")
        current = self.tasks.snapshot("loop")
        proposed = self.tasks.propose("loop", {"summary": "Repropose after " + failure_kind + " diagnostic", "stage_ids": ["draft"]}, expected_revision=current["revision"])
        self.tasks.approve("loop", proposal_hash=proposed["proposal_hash"], user_message_ref="test:reproposal", approved_stage_ids=["draft"])
        with self.assertRaisesRegex(ValueError, "diagnos|reassess|progress"):
            controller.reserve_cycle("loop", "draft")
        with patch.object(controller, "verify", return_value=rejected):
            self.assertEqual(controller.run("loop", resume=True)["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 3)

    def test_unsupported_diagnostic_reproposal_cannot_arm_corrective_probe(self):
        self.failed_diagnostic_reproposal("unsupported")

    def test_failed_diagnostic_reproposal_cannot_arm_corrective_probe(self):
        self.failed_diagnostic_reproposal("failed")

    def test_interrupted_diagnostic_reproposal_cannot_arm_corrective_probe(self):
        self.failed_diagnostic_reproposal("interrupted")

    def test_reserving_diagnostic_does_not_itself_authorize_mutation(self):
        self.progress_task()
        for _ in range(3):
            self.controller.reserve_cycle("progress", "draft")
            self.controller.record_failure("progress", "draft", ["Mandatory AC failed: AC1"])
        self.controller.reserve_diagnostic("progress", "draft")
        with self.assertRaisesRegex(ValueError, "diagnos|reassess|progress"):
            self.controller.reserve_cycle("progress", "draft")

    def test_saved_precompletion_probe_flag_without_validated_authorization_is_not_permission(self):
        self.progress_task()
        for _ in range(3):
            self.controller.reserve_cycle("progress", "draft")
            self.controller.record_failure("progress", "draft", ["Mandatory AC failed: AC1"])
        history = read_record(self.store.db, "recovery", "progress")["payload"]
        history["groups"]["draft"].update(probe_pending=True, diagnosed_generation=0)
        self.controller._save("recovery", "progress", history)
        with self.assertRaisesRegex(ValueError, "diagnos|reassess|progress"):
            WorkflowController(self.store).reserve_cycle("progress", "draft")

    def reserved_diagnostic(self):
        self.progress_task()
        for _ in range(3):
            self.controller.reserve_cycle("progress", "draft")
            self.controller.record_failure("progress", "draft", ["Mandatory AC failed: AC1"])
        self.controller.reserve_diagnostic("progress", "draft")

    def test_tampered_diagnostic_receipt_cannot_mint_probe_authorization(self):
        self.reserved_diagnostic()
        diagnosis = self.captured_diagnostic(self.controller, "progress", "draft")
        Path(diagnosis["receipt"]["artifacts"]["result"]["path"]).write_text("{}")
        self.assertFalse(self.controller._complete_diagnostic("progress", "draft", diagnosis))
        with self.assertRaisesRegex(ValueError, "diagnos|progress"):
            self.controller.reserve_cycle("progress", "draft")

    def test_probe_authorization_revalidates_captured_diagnostic_before_consumption(self):
        self.reserved_diagnostic()
        diagnosis = self.authorize_diagnostic(self.controller, "progress", "draft")
        Path(diagnosis["receipt"]["artifacts"]["result"]["path"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "diagnos|progress"):
            self.controller.reserve_cycle("progress", "draft")
        self.assertEqual(read_record(self.store.db, "budget", "progress")["payload"]["task_cycles"], 3)

    def test_diagnostic_prose_without_completed_receipt_cannot_mint_permission(self):
        self.reserved_diagnostic()
        self.assertFalse(self.controller._complete_diagnostic("progress", "draft", {"receipt": {}, "result": {"status": "COMPLETE",
            "payload": {"cause": "Claimed cause", "recommendation": "Claimed probe", "requires_new_approval": False}}}))
        with self.assertRaisesRegex(ValueError, "diagnos|progress"):
            self.controller.reserve_cycle("progress", "draft")

    def test_unfinished_diagnostic_survives_stage_rename_and_delegation(self):
        self.reserved_diagnostic()
        changed = copy.deepcopy(self.tasks.snapshot("progress")["contract"])
        changed["id"] = "progress"
        changed["stages"][0]["id"] = "renamed"
        self.approve(changed)
        with self.assertRaisesRegex(ValueError, "diagnos|progress"):
            self.controller.reserve_cycle("progress", "renamed")
        child = copy.deepcopy(changed)
        child.update(id="diagnostic-child", parent_task_id="progress", parent_stage_id="renamed")
        child["stages"][0]["id"] = "delegate"
        self.tasks.create(child)
        with self.assertRaisesRegex(ValueError, "diagnos|progress"):
            self.controller.reserve_cycle("diagnostic-child", "delegate")

    def test_failed_diagnostic_can_resume_only_after_new_original_evidence(self):
        controller, rejected = self.loop_fixture(supported=False, hard_cap=4)
        with patch.object(controller, "verify", return_value=rejected):
            controller.run("loop")
            (self.store.repo / "guide.md").write_text("Externally established original proof")
            controller.run("loop", resume=True)
        group = read_record(self.store.db, "recovery", "loop")["payload"]["groups"]["draft"]
        self.assertIsNone(group["diagnostic_inflight"])
        self.assertFalse(group["stopped"])
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 4)

    def test_diagnostic_receipt_cannot_authorize_another_stage_reservation_with_identical_bindings(self):
        spec = contract("two-diags")
        spec["unknowns"] = []
        spec["scope"]["resources"].append("right.md")
        spec["evidence"].append({"id": "right", "kind": "artifact", "path": "right.md"})
        spec["acceptance"].append({"id": "AC2", "expected": "Distinct right obligation", "evidence_ids": ["right"]})
        spec["stages"][1].update(id="right", depends_on=[], unknown_ids=[], acceptance_ids=["AC2"],
            scope={"resources": ["right.md"], "effects": ["file_write"]})
        self.approve(spec)
        self.controller.reserve_diagnostic("two-diags", "draft")
        original = self.captured_diagnostic(self.controller, "two-diags", "draft")
        self.controller.reserve_diagnostic("two-diags", "right")
        self.assertEqual(original["receipt"]["bindings"], self.controller._diagnostic_bindings("two-diags"))
        self.assertFalse(self.controller._complete_diagnostic("two-diags", "right", original))

    def test_diagnostic_receipt_with_unapproved_schema_cannot_mint_permission(self):
        self.reserved_diagnostic()
        schema = copy.deepcopy(DIAGNOSTIC_SCHEMA)
        schema["required"] = ["cause"]
        diagnosis = self.captured_diagnostic(self.controller, "progress", "draft", schema=schema)
        self.assertFalse(self.controller._complete_diagnostic("progress", "draft", diagnosis))

    def test_completed_diagnostic_authorization_survives_interruption_before_phase_save(self):
        controller, rejected = self.loop_fixture()
        save = controller._save
        interrupted = False
        def interrupt_after_permission(kind, key, value):
            nonlocal interrupted
            history = read_record(self.store.db, "recovery", "loop")["payload"] or {}
            pending = history.get("groups", {}).get("draft", {}).get("probe_pending")
            if kind == "cycle" and value.get("phase") == "REJECTED" and pending and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return save(kind, key, value)
        with patch.object(controller, "verify", return_value=rejected), patch.object(controller, "_save", side_effect=interrupt_after_permission):
            self.assertEqual(controller.run("loop")["status"], "BLOCKED")
            self.assertTrue(interrupted)
            self.assertEqual(controller.run("loop", resume=True)["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "loop")["payload"]["task_cycles"], 4)
        self.assertTrue(read_record(self.store.db, "recovery", "loop")["payload"]["groups"]["draft"]["stopped"])
        roles = [json.loads(path.read_text())["role"] for path in (self.store.root / "tasks/loop/sessions").glob("*/request.json")]
        self.assertEqual(roles.count("diagnostic"), 1)

    def scheduling_fixture(self, *, missing_child=False, uncertain_action=False, task_cap=None):
        """Real provider requests and checks; gate doubles isolate scheduling only."""
        from ase.gates import source_snapshot
        spec = contract("schedule")
        spec["unknowns"] = []
        names = ["blocked", "independent", "dependent"]
        spec["scope"] = {"resources": [name + ".md" for name in names], "effects": ["file_write", "command"]}
        spec["evidence"] = [{"id": name, "kind": "artifact", "path": name + ".md"} for name in names]
        spec["acceptance"] = [{"id": "AC" + str(index), "expected": name + " original result", "evidence_ids": [name]} for index, name in enumerate(names)]
        spec["stages"] = [{"id": name, "scope": {"resources": [name + ".md"], "effects": ["file_write"]},
            "acceptance_ids": ["AC" + str(index)], "depends_on": ["blocked"] if name == "dependent" else []} for index, name in enumerate(names)]
        spec["execution_policy"] = {"recovery_mode": "progress", "max_stage_cycles": 1, "max_task_cycles": task_cap}
        if missing_child:
            spec["stages"][0]["required_child_task_id"] = "missing-child"
        if uncertain_action:
            spec["scope"]["resources"].append("submit.count")
            spec["stages"][0]["scope"] = {"resources": ["blocked.md", "submit.count"], "effects": ["file_write", "command"]}
            spec["stages"][0]["actions"] = [{"id": "uncertain", "argv": [sys.executable, "-c", "from pathlib import Path;Path('submit.count').write_text('one')"],
                "observation_argv": [sys.executable, "-c", "print('{\"exists\":false}')"]}]
        self.approve(spec)
        (self.store.repo / "independent.md").write_text("Already satisfies the independent executable check")
        record_discovery(self.store, "schedule", discover_capabilities(self.store.project, scope={"resources": spec["scope"]["resources"]}, backend_config={}), expected_revision=0)
        folder = self.store.state_home / "schedule-fixture"
        folder.mkdir()
        fixture = folder / "provider.py"
        fixture.write_text(FIXTURE.replace("payload = {'verdict':'PASS'}", "payload = {'edits':[],'summary':'No source edits required'}"))
        runner = Runner({"executable": sys.executable, "executable_args": [str(fixture), "success", "codex"],
            "trusted_cwd": str(folder), "protocol_only": True}, self.store.root / "tasks/schedule/sessions")
        controller = WorkflowController(self.store, runner=runner)
        def verification(task_id, stage_id):
            if stage_id == "blocked":
                return {"status": "BLOCKED", "blocked_reasons": ["Stage decision unavailable: blocked"], "rejected_reasons": []}
            return {"status": "APPROVED", "blocked_reasons": [], "rejected_reasons": [],
                "fingerprint": source_snapshot(self.store, self.tasks.snapshot(task_id)["contract"], task_id=task_id)["fingerprint"]}
        return controller, verification

    def implementation_stages(self):
        return [request["handoff"]["stage"]["id"] for path in (self.store.root / "tasks/schedule/sessions").glob("*/request.json")
                if (request := json.loads(path.read_text()))["role"] == "implementation"]

    def test_blocked_required_child_holds_dependents_but_runs_later_independent_stage(self):
        controller, verification = self.scheduling_fixture(missing_child=True)
        with patch.object(controller, "verify", side_effect=verification):
            result = controller.run("schedule")
        self.assertEqual(self.implementation_stages(), ["independent"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("Required child missing: missing-child" in reason for reason in result["blocked_reasons"]))
        self.assertEqual(read_record(self.store.db, "stage", "schedule:independent")["payload"]["state"], "COMPLETED")
        self.assertIsNone(read_record(self.store.db, "stage", "schedule:dependent")["payload"])

    def test_blocked_stage_verdict_does_not_prevent_later_independent_stage(self):
        controller, verification = self.scheduling_fixture()
        with patch.object(controller, "verify", side_effect=verification):
            result = controller.run("schedule")
        self.assertEqual(set(self.implementation_stages()), {"blocked", "independent"})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("Stage decision unavailable: blocked" in reason for reason in result["blocked_reasons"]))
        self.assertIsNone(read_record(self.store.db, "stage", "schedule:dependent")["payload"])

    def test_unknown_action_failure_holds_dependents_but_runs_independent_stage(self):
        controller, verification = self.scheduling_fixture(uncertain_action=True)
        with patch.object(controller, "verify", side_effect=verification):
            result = controller.run("schedule")
        self.assertEqual(set(self.implementation_stages()), {"blocked", "independent"})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("postcondition" in reason or "UNKNOWN" in reason for reason in result["blocked_reasons"]))
        self.assertEqual((self.store.repo / "submit.count").read_text(), "one")

    def test_root_stop_prevents_later_independent_stage(self):
        controller, verification = self.scheduling_fixture()
        def stop_after_first(task_id, stage_id):
            controller.request_stop(task_id, reason="Stop the whole managed task")
            return verification(task_id, stage_id)
        with patch.object(controller, "verify", side_effect=stop_after_first):
            result = controller.run("schedule")
        self.assertEqual(self.implementation_stages(), ["blocked"])
        self.assertEqual(result["closure"]["finish_reason"], "paused")

    def test_global_cycle_budget_prevents_later_independent_mutation(self):
        controller, verification = self.scheduling_fixture(task_cap=1)
        with patch.object(controller, "verify", side_effect=verification):
            result = controller.run("schedule")
        self.assertEqual(self.implementation_stages(), ["blocked"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(read_record(self.store.db, "budget", "schedule")["payload"]["task_cycles"], 1)

    def test_multi_stage_report_retains_each_original_verdict_and_profile_evidence(self):
        from ase.gates import GateService
        controller, verification = self.scheduling_fixture()
        def actual_blocked_profile(task_id, stage_id):
            if stage_id == "blocked":
                return GateService(self.store).gate(task_id, stage_id=stage_id)
            return verification(task_id, stage_id)
        with patch.object(controller, "verify", side_effect=actual_blocked_profile):
            result = controller.run("schedule")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result.get("stage_results", {}).get("blocked", {}).get("profile_evidence", {}).get("by_id", {}).get("blocked", {}).get("status"), "FAIL")
        self.assertEqual(result["stage_results"]["independent"]["status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
