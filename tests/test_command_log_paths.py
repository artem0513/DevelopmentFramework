"""Actual command-log I/O and public resume; protocol fixtures are not AC proof."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

from ase.core import execute
from ase.discovery import discover_capabilities, record_discovery
from ase.gates import GateService
from ase.profiles import evaluate_profile
from ase.projects import _safe_state
from ase.state import read_record, transaction, write_record
from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import WorkflowController
import test_runner as transport


ROOT = Path(__file__).resolve().parents[1]


def physical(path):
    """Test setup/cleanup only: create long fixtures before runtime support."""
    return Path("\\\\?\\" + str(path)) if os.name == "nt" else path


class CommandLogPathTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="ase-command-logs-")).resolve()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        # Verify the recursive target before Windows cleanup; never follow a
        # computed target outside this explicitly created temporary directory.
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("ase-command-logs-"):
            raise AssertionError("Unexpected temporary cleanup boundary")
        shutil.rmtree(physical(self.base))

    def long_log(self, length=263, root=None):
        root = root or self.base
        parent = root
        target = length - 37  # slash + full uuid4 hexadecimal + .log
        while len(str(parent)) + 61 < target:
            parent /= "p" * 60
        if len(str(parent)) < target:
            parent /= "p" * (target - len(str(parent)) - 1)
        physical(parent).mkdir(parents=True, exist_ok=True)
        log = parent / (uuid.uuid4().hex + ".log")
        self.assertEqual(len(str(log)), length)
        return log

    def test_execute_reads_writes_and_hashes_263_and_longer_log_paths(self):
        for length in (263, 360):
            with self.subTest(length=length):
                log = self.long_log(length)
                proof = execute([sys.executable, "-c", "print('native command output')"], self.base, log, timeout=5)
                self.assertEqual(proof["exit_code"], 0, proof)
                self.assertIsNone(proof["error"])
                self.assertEqual(proof["log"], str(log))
                self.assertNotIn("\\\\?\\", proof["log"])
                data = physical(log).read_bytes()
                self.assertEqual(proof["log_sha256"], hashlib.sha256(data).hexdigest())
                self.assertIn(b"native command output", data)
                second = execute([sys.executable, "-c", "print('replacement output')"], self.base, log, timeout=5)
                self.assertEqual(second["exit_code"], 0)
                self.assertEqual(physical(log).read_text(encoding="utf-8").strip(), "replacement output")

    def test_unwritable_log_target_is_rejected_before_child_effect(self):
        log = self.long_log()
        physical(log).mkdir()
        marker = self.base / "unexpected-child"
        with self.assertRaises((OSError, ValueError)):
            execute([sys.executable, "-c", "from pathlib import Path;Path(" + repr(str(marker)) + ").write_text('effect')"], self.base, log, timeout=5)
        self.assertFalse(marker.exists(), "Log preflight must happen before spawning the command")

    def test_native_view_preserves_logical_drive_unc_and_rejects_device_namespaces(self):
        from ase.native_paths import logical_path, native_path
        ordinary = self.long_log()
        self.assertEqual(logical_path(native_path(ordinary)), ordinary)
        if os.name == "nt":
            self.assertEqual(str(native_path(r"\\server\share\state\log.txt")), r"\\?\UNC\server\share\state\log.txt")
            self.assertEqual(str(logical_path(r"\\?\UNC\server\share\state\log.txt")), r"\\server\share\state\log.txt")
        else:
            self.assertEqual(native_path(ordinary), ordinary)
        invalid = (r"\\.\pipe\unexpected", r"\\?\GLOBALROOT\Device\HarddiskVolume1\file", r"\??\C:\state\file", r"\\?\Volume{123}\file",
                   r"\\?\UNC\.\pipe\unexpected", r"\\?\UNC\?\C:\unexpected", r"\\?\UNC\??\share\unexpected")
        if os.name == "nt":
            invalid += (r"C:\state\NUL", r"\\?\C:\state\NUL", r"C:\state\NUL.log", r"C:\state\trailing.", "C:\\state\\trailing ", r"\\?\C:\state\trailing.")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                native_path(value)

    def test_long_hardlinked_log_cannot_bypass_state_safety(self):
        log = self.long_log()
        target = self.base / "shared-canary"
        target.write_text("Unchanged", encoding="utf-8")
        os.link(target, physical(log))
        with self.assertRaises(ValueError):
            _safe_state(log)
        self.assertEqual(target.read_text(encoding="utf-8"), "Unchanged")

    def test_junction_ancestor_remains_rejected_for_long_log(self):
        target = self.base / "outside"
        target.mkdir()
        alias = self.base / "redirect"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.addCleanup(alias.rmdir)
        else:
            alias.symlink_to(target, target_is_directory=True)
            self.addCleanup(alias.unlink)
        log = self.long_log(360, alias)
        with self.assertRaises(ValueError):
            _safe_state(log)

    def test_recovery_reads_actual_long_log_for_original_partial_test_progress(self):
        import test_partial_progress as partial
        fixture = partial.PartialTestProgress()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        def relocate(result):
            checks = fixture.store.root / "tasks" / "partial" / "checks"
            log = self.long_log(360, checks)
            physical(log).write_bytes(Path(result["log"]).read_bytes())
            branch = checks / log.relative_to(checks).parts[0]
            self.assertTrue(branch.is_relative_to(checks))
            fixture.addCleanup(lambda: shutil.rmtree(physical(branch)))
            result["log"] = str(log)
        observed = fixture.observe(1, mutate_result=relocate)
        self.assertTrue(observed["newly_verified"], "One actually passing unchanged original case must remain visible through a long evidence log")

    def resume_fixture(self):
        repo = self.base / "work"
        repo.mkdir()
        store = Store(repo, self.base / "state")
        self.addCleanup(store.close)
        template = store.root / "tasks" / "t" / "actions" / ("a" * 64) / ("b" * 32 + ".log")
        task_length = 263 - (len(str(template)) - 1)
        self.assertTrue(4 <= task_length <= 80)
        task_id = "long" + "r" * (task_length - 4)
        observation = [sys.executable, "-c", "from pathlib import Path;import json;p=Path('resource.count');n=int(p.read_text()) if p.exists() else 0;print(json.dumps({'exists':bool(n),'resource_id':'counter-one' if n else None,'count':n}))"]
        effect = [sys.executable, "-c", "from pathlib import Path;import json;p=Path('resource.count');p.write_text(str((int(p.read_text()) if p.exists() else 0)+1));print(json.dumps({'provider_attempt_id':'native-log-attempt'}))"]
        contract = {"id": task_id, "schema_version": 2, "profile": "infrastructure", "request": "Create one local counter with honest observation evidence",
                    "scope": {"resources": ["resource.count"], "effects": ["command"]},
                    "acceptance": [{"id": "AC1", "expected": "The actual counter equals one", "evidence_ids": ["state"]}],
                    "evidence": [{"id": "state", "kind": "probe", "argv": observation, "predicate": {"path": "count", "op": "eq", "value": 1}}],
                    "stages": [{"id": "deliver", "acceptance_ids": ["AC1"], "depends_on": [], "actions": [{"id": "counter", "argv": effect, "observation_argv": observation, "timeout": 5}]}]}
        tasks = TaskService(store)
        task = tasks.create(contract)
        task = tasks.propose(task_id, {"summary": "Create the disposable counter once", "stage_ids": ["deliver"]}, expected_revision=task["revision"])
        tasks.approve(task_id, proposal_hash=task["proposal_hash"], user_message_ref="fixture:explicit-command-scope", approved_stage_ids=["deliver"])
        record_discovery(store, task_id, discover_capabilities(store.project, scope={"resources": ["resource.count"]}, backend_config={}), expected_revision=0)
        controller = WorkflowController(store)
        controller.reserve_cycle(task_id, "deliver")
        approved = tasks.snapshot(task_id)
        controller._save("cycle", task_id + ":deliver", {"phase": "CHECKING", "contract_hash": approved["contract_hash"], "proposal_hash": approved["proposal_hash"]})
        fixture = self.base / "provider.py"
        payload = """payload = {'verdict':'PASS'}
if 'REQUEST_JSON:\\n' in input_text:
 request=json.loads(input_text.split('REQUEST_JSON:\\n',1)[1])
 if request['role']=='implementation': raise AssertionError('No new implementation is authorized by this fixture')
 if request['role']=='ac': payload={'criteria':[{'id':'AC1','expected':'The actual counter equals one','status':'UNVERIFIED','evidence':['Protocol transport only'],'method':'Controlled local fixture','limitations':['No live independent semantic acceptance'],'missing_coverage':['Semantic judgment unavailable']}]}
 if request['role']=='review': payload={'findings':[],'summary':'Protocol transport only; no independent semantic review'}
"""
        fixture.write_text(transport.FIXTURE.replace("payload = {'verdict':'PASS'}", payload), encoding="utf-8")
        trusted = self.base / "trusted"
        trusted.mkdir()
        with transaction(store.db) as db:
            write_record(db, "backend_config", "codex", {"executable": sys.executable, "executable_args": [str(fixture), "success", "codex"],
                         "trusted_cwd": str(trusted), "protocol_only": True}, expected_revision=0)
        return store, controller, task_id

    def test_public_resume_consumes_original_length_action_logs_without_replaying_effect(self):
        store, controller, task_id = self.resume_fixture()
        for attempt in range(2):
            result = subprocess.run([sys.executable, "-B", "-m", "ase", "--repo", str(store.repo), "--home", str(store.state_home), "resume", task_id],
                                    cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=30)
            payload = json.loads(result.stdout)
            self.assertTrue((store.repo / "resource.count").is_file(), json.dumps({"attempt": attempt, "public_resume": payload}))
            self.assertEqual((store.repo / "resource.count").read_text(), "1", payload)
            self.assertNotIn("No such file or directory", json.dumps(payload))
            self.assertEqual(payload["status"], "BLOCKED", "Protocol fixture must never establish semantic approval")
        operations = [json.loads(row[0]) for row in store.db.execute("SELECT payload FROM runtime_records WHERE kind='operation'")]
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertEqual(operation["state"], "APPLIED")
        self.assertEqual(operation["submission_binding"]["provider_attempt_id"], "native-log-attempt")
        folder = store.root / "tasks" / task_id / "actions" / operation["action_id"]
        self.assertEqual(len(folder.name), 64)
        logs = list(physical(folder).glob("*.log"))
        self.assertGreaterEqual(len(logs), 3)
        self.assertTrue(all(len(path.name) == 36 and len(str(folder / path.name)) == 263 for path in logs))
        self.assertNotIn("\\\\?\\", operation["observation"]["proof_ref"])

    def test_profile_gate_and_packet_consume_long_command_log_with_logical_metadata(self):
        store, controller, task_id = self.resume_fixture()
        task = controller.tasks.snapshot(task_id)
        spec = controller.tasks.execution_plan(task_id)["checks"][0]
        log = self.long_log(360, store.root / "tasks" / task_id / "checks")
        # Execute the actual declared probe at the long path for both failed
        # and satisfied postconditions; no independent judgment is fabricated.
        proof = execute(spec["argv"], store.repo, log, timeout=5)
        proof.update(id=spec["id"], kind="probe", configured_argv=spec["argv"])
        self.assertEqual(evaluate_profile("infrastructure", [spec], [proof])["status"], "REJECTED")
        (store.repo / "resource.count").write_text("1")
        proof = execute(spec["argv"], store.repo, log, timeout=5)
        proof.update(id=spec["id"], kind="probe", configured_argv=spec["argv"])
        self.assertEqual(evaluate_profile("infrastructure", [spec], [proof])["status"], "PASS")
        from ase.gates import source_snapshot
        from ase.state import hash_value
        controller._save("checks", task_id, {"kind": "checks", "task_id": task_id, "results": [proof], "fingerprint": source_snapshot(store, task["contract"], task_id)["fingerprint"],
                         "workspace_id": store.workspace_id, "contract_hash": task["contract_hash"], "plan_hash": controller.tasks.execution_plan(task_id)["plan_hash"]})
        packet = controller._packet(task_id, stage_id="deliver")
        self.assertEqual(packet["coverage"]["missing"], [])
        self.assertIn("_ase_context/logs/state.txt", packet["coverage"]["included"])
        gate = GateService(store).gate(task_id, stage_id="deliver")
        self.assertEqual(gate["profile_evidence"]["status"], "PASS")
        self.assertFalse(any("outside selected" in reason or "Source log missing" in reason for reason in gate["reasons"]))


if __name__ == "__main__":
    unittest.main()
