"""Real cancellation and durable stop records; no semantic fixture approvals."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from ase.core import execute
from ase.discovery import discover_capabilities, record_discovery
from ase.journal import Journal
from ase.runner import Runner
from ase.adapters.codex import CodexAdapter
from ase.state import read_record, transaction, write_record
from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import WorkflowController
from test_task_contract_v2 import contract
from test_runner import FIXTURE, TransportFixture

ROOT = Path(__file__).resolve().parents[1]


class ProcessCancellationTests(unittest.TestCase):
    def test_check_command_cancels_after_real_start_even_with_positive_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            marker = root / "ready"
            result = execute([sys.executable, "-c", "from pathlib import Path;import time;print('Ran 1 test in 0.01s\\nOK',flush=True);Path('ready').write_text('ready');time.sleep(30)"],
                             root, root / "check.log", 20, cancel_requested=marker.exists)
            self.assertTrue(marker.is_file())
            self.assertTrue(result["cancelled"])
            self.assertLess(result["duration_seconds"], 5)
            self.assertEqual(result["error"], "PROCESS_CANCELLED")

    def test_exit_zero_after_stop_is_still_cancelled(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            result = execute([sys.executable, "-c", "from pathlib import Path;Path('ready').write_text('ready')"], root, root / "check.log", 10,
                             cancel_requested=(root / "ready").exists)
            self.assertTrue(result["cancelled"])
            self.assertEqual(result["error"], "PROCESS_CANCELLED")


class RunnerCancellationTests(TransportFixture):
    def test_real_model_process_and_descendant_are_cancelled(self):
        outcome = Runner(self.config(case="sleep"), self.base / "artifacts").run(CodexAdapter(), self.request(), timeout=20,
                    cancel_requested=(self.trusted / "descendant.pid").exists)
        self.assertTrue((self.trusted / "descendant.pid").exists())
        self.assertTrue(outcome["receipt"]["cancelled"])
        self.assertEqual(outcome["result"]["blocker"], "PROCESS_CANCELLED")
        self.assertLess(outcome["receipt"]["duration_seconds"], 5)


class StopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ase-stop-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "workspace"
        self.repo.mkdir()
        self.home = self.base / "state"
        self.store = Store(self.repo, self.home)
        self.addCleanup(self.store.close)
        self.tasks = TaskService(self.store)
        c = contract("stop-task")
        c["unknowns"], c["stages"] = [], c["stages"][:1]
        task = self.tasks.create(c)
        task = self.tasks.propose(task["task_id"], {"summary": "Create guide", "stage_ids": ["draft"]}, expected_revision=task["revision"])
        self.tasks.approve(task["task_id"], proposal_hash=task["proposal_hash"], user_message_ref="test:authorized", approved_stage_ids=["draft"])
        record_discovery(self.store, task["task_id"], discover_capabilities(self.store.project, scope={"resources": ["."]}, backend_config={}), expected_revision=0)
        self.controller = WorkflowController(self.store)

    def test_idle_stop_is_atomic_dated_idempotent_closure(self):
        result = self.controller.request_stop("stop-task", reason="Finish today")
        self.assertEqual(result["state"], "PAUSED")
        checkpoint = self.store.db.execute("SELECT payload FROM checkpoints WHERE task_id='stop-task'").fetchone()
        self.assertIsNotNone(checkpoint)
        self.assertIn("resume", json.loads(checkpoint[0])["next_action"].lower())
        context = Journal(self.store).context()
        self.assertTrue(context["payload"]["next_action"])
        count = self.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.controller.request_stop("stop-task", reason="Finish today")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], count)
        self.assertEqual(Journal(self.store).context()["revision"], context["revision"])

    def test_failed_stop_closure_rolls_back_flag_and_task(self):
        with patch("ase.journal.Journal.finish_session", side_effect=ValueError("fixture closure fault")):
            with self.assertRaisesRegex(ValueError, "fixture"):
                self.controller.request_stop("stop-task", reason="Finish today")
        self.assertIsNone(read_record(self.store.db, "stop", "stop-task")["payload"])
        self.assertEqual(self.tasks.snapshot("stop-task")["state"], "READY")

    def test_stop_committed_before_final_closure_wins_over_approved_result(self):
        def finish_race(*args, **kwargs):
            self.controller.request_stop("stop-task", reason="Stop at the final barrier")
            return {"status": "APPROVED", "reasons": []}
        with patch.object(self.controller, "_run", side_effect=finish_race):
            result = self.controller.run("stop-task")
        self.assertEqual(result["closure"]["finish_reason"], "paused")
        self.assertEqual(self.tasks.snapshot("stop-task")["state"], "PAUSED")

    def test_public_stop_cancels_running_role_before_any_edit(self):
        trusted = self.home / "runner"
        trusted.mkdir()
        fixture = self.base / "fixture.py"
        fixture.write_text(FIXTURE)
        with transaction(self.store.db) as db:
            write_record(db, "backend_config", "codex", {"executable": sys.executable, "executable_args": [str(fixture), "sleep", "codex"],
                         "trusted_cwd": str(trusted), "protocol_only": True}, expected_revision=0)
        helper = self.base / "run.py"
        helper.write_text("from ase.store import Store\nfrom ase.workflow import WorkflowController\nimport json\n"
                          f"s=Store({str(self.repo)!r},{str(self.home)!r})\n"
                          "print(json.dumps(WorkflowController(s).run('stop-task')),flush=True)\ns.close()\n")
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        process = subprocess.Popen([sys.executable, str(helper)], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup_process():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.addCleanup(cleanup_process)
        marker = trusted / "descendant.pid"
        deadline = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "Actual model fixture never reached its ready barrier")
        start = time.monotonic()
        stopped = subprocess.run([sys.executable, "-m", "ase", "--repo", str(self.repo), "--home", str(self.home), "task", "stop", "stop-task", "--reason", "Stop active model"],
                                 cwd=ROOT, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        result = json.loads(stdout)
        self.assertEqual(result["closure"]["finish_reason"], "paused")
        self.assertLess(time.monotonic() - start, 5)
        self.assertFalse((self.repo / "guide.md").exists())
        receipts = list((self.store.root / "tasks/stop-task/sessions").glob("*/receipt.json"))
        self.assertEqual(len(receipts), 1)
        self.assertTrue(json.loads(receipts[0].read_text())["cancelled"])
