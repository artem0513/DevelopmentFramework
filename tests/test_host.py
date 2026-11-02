"""Host boundary tests; substituted model work is not live acceptance evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ase.core import Engine

ROOT = Path(__file__).resolve().parents[1]


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ase-host-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo, self.home = self.base / "allowed", self.base / "state"
        self.repo.mkdir()
        for args in (("init", "-b", "feature/host"), ("config", "user.name", "Host Tests"),
                     ("config", "user.email", "host@example.invalid")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-m", "initial"], check=True, capture_output=True)
        self.contract = {"id": "host-task", "request": "Verify existing code only", "risk": "MEDIUM",
                         "acceptance": [{"id": "AC1", "expected": "value is 1", "commands": ["unit"]}],
                         "commands": [{"id": "unit", "kind": "test", "parser": "unittest", "required": True,
                                       "argv": [sys.executable, "-m", "unittest", "discover", "-v"]}]}
        engine = Engine(self.repo, self.home)
        try:
            engine.start(self.contract)
        finally:
            engine.store.close()

    def server(self, messages, env=None):
        result = subprocess.run([sys.executable, "-m", "ase.host", "--root", str(self.repo),
            "--home", str(self.home), "--codex", sys.executable], cwd=ROOT,
            input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True,
            text=True, timeout=15, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    def coordinator(self):
        from ase.host import Coordinator
        coordinator = Coordinator([self.repo], self.home, Path(sys.executable))
        self.addCleanup(coordinator.close)
        return coordinator

    @staticmethod
    def is_rejected(response):
        return bool(response.get("error") or response.get("result", {}).get("isError"))

    def test_stdio_initializes_and_exposes_only_read_only_verification_tools(self):
        responses = self.server([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "boundary-test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(len(responses), 2)
        self.assertIn("protocolVersion", responses[0]["result"])
        self.assertEqual({tool["name"] for tool in responses[1]["result"]["tools"]},
                         {"verification_start", "verification_status"})

    def test_protocol_rejects_unknown_method_tool_and_privileged_arguments(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "publish"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "publish", "arguments": {}}}]
        for index, extra in enumerate(({"command": ["git", "push"]}, {"home": str(self.base / "other")},
                                       {"codex": sys.executable}, {"env": {"ASE_CODEX": "evil"}},
                                       {"parallel": "not-a-boolean"}), 3):
            messages.append({"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {
                "name": "verification_start", "arguments": {"repo": str(self.repo), "task_id": "host-task", **extra}}})
        responses = self.server(messages)
        self.assertEqual(len(responses), len(messages))
        for response in responses:
            self.assertTrue(self.is_rejected(response), response)
        self.assertFalse(list(self.home.rglob("ac.session.log")), "Rejected inputs started host execution")

    def test_protocol_rejects_repo_escape_and_unknown_job(self):
        responses = self.server([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "verification_start", "arguments": {"repo": str(self.base), "task_id": "host-task"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "verification_status", "arguments": {"job_id": "nonexistent"}}},
        ])
        for response in responses:
            self.assertTrue(self.is_rejected(response), response)

    def test_relative_or_missing_codex_startup_is_rejected(self):
        from ase.host import Coordinator
        for executable in ("python", str(self.base / "missing.exe")):
            with self.subTest(executable=executable), self.assertRaises((ValueError, OSError)):
                Coordinator([self.repo], self.home, executable)

    def test_repository_path_prefix_and_traversal_do_not_escape_allowlist(self):
        coordinator = self.coordinator()
        outside = self.base / "allowed-other"
        outside.mkdir()
        for candidate in (outside, self.repo / ".." / "allowed-other"):
            with self.subTest(candidate=candidate), self.assertRaises((ValueError, OSError)):
                coordinator.start(candidate, "host-task")

    def test_host_inspection_rejects_nested_directory_as_a_separate_git_root(self):
        from ase import inspection
        nested = self.repo / "nested"
        nested.mkdir()
        engine = None
        try:
            with self.assertRaises(ValueError):
                engine = Engine(nested, self.home, inspector=inspection)
        finally:
            if engine is not None:
                engine.store.close()

    def test_directory_symlink_cannot_escape_allowlist(self):
        coordinator = self.coordinator()
        outside = self.base / "outside"
        outside.mkdir()
        link = self.repo / "escape"
        self.directory_link(link, outside)
        with self.assertRaises((ValueError, OSError)):
            coordinator.start(link, "host-task")

    def directory_link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_state_junction_cannot_redirect_host_artifact_writes_outside_fixed_home(self):
        project_state = next((self.home / "projects").iterdir())
        outside = self.base / "outside-state"
        project_state.rename(outside)
        self.directory_link(project_state, outside)
        coordinator = self.coordinator()
        with patch.object(Engine, "verify", lambda engine, task_id, parallel=False: engine.gate(task_id)):
            result = self.wait_terminal(coordinator, coordinator.start(self.repo, "host-task")["job_id"])
        self.assertFalse((outside / "tasks" / "host-task" / "report.json").exists(),
                         "Fixed state home followed an attacker-controlled junction for host writes")
        self.assertEqual(result["status"], "FAILED", result)

    def test_task_directory_junction_cannot_redirect_host_report_writes(self):
        task_state = next((self.home / "projects").iterdir()) / "tasks" / "host-task"
        outside = self.base / "outside-task-state"
        task_state.rename(outside)
        self.directory_link(task_state, outside)
        coordinator = self.coordinator()
        with patch.object(Engine, "verify", lambda engine, task_id, parallel=False: engine.gate(task_id)):
            result = self.wait_terminal(coordinator, coordinator.start(self.repo, "host-task")["job_id"])
        self.assertFalse((outside / "report.json").exists(), "Host followed a linked task output directory")
        self.assertEqual(result["status"], "FAILED", result)

    def test_artifact_hardlink_cannot_overwrite_file_outside_fixed_home(self):
        self.prepare_process_adapter()
        task_state = next((self.home / "projects").iterdir()) / "tasks" / "host-task"
        outside = self.base / "outside-log-file.txt"
        outside.write_text("outside sentinel", encoding="utf-8")
        os.link(outside, task_state / "ac.session.log")
        coordinator = self.coordinator()
        result = self.run_adapter_job(coordinator)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside sentinel",
                         "Host log write followed a hardlink outside fixed state home")
        self.assertEqual(result["status"], "FAILED", result)

    def wait_terminal(self, coordinator, job_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = coordinator.status(job_id)
            if result["status"] in {"COMPLETED", "FAILED"}:
                return result
            time.sleep(0.01)
        self.fail("Host job did not reach a terminal state")

    def test_concurrency_is_bounded_and_host_never_runs_check_or_publish(self):
        coordinator = self.coordinator()
        entered, release = threading.Event(), threading.Event()
        def waiting_verifier(engine, task_id, parallel=False):
            entered.set()
            release.wait(5)
            return engine.gate(task_id)
        with patch.object(Engine, "verify", waiting_verifier), \
             patch.object(Engine, "check", side_effect=AssertionError("host cannot execute test contracts")), \
             patch.object(Engine, "run", side_effect=AssertionError("host cannot implement")), \
             patch.object(Engine, "publish", side_effect=AssertionError("host cannot publish")):
            first = coordinator.start(self.repo, "host-task")
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises((ValueError, RuntimeError)):
                    coordinator.start(self.repo, "host-task")
                other = dict(self.contract, id="other-task")
                engine = Engine(self.repo, self.home)
                try:
                    engine.start(other)
                finally:
                    engine.store.close()
                with self.assertRaises((ValueError, RuntimeError)):
                    coordinator.start(self.repo, "other-task")
            finally:
                release.set()
            result = self.wait_terminal(coordinator, first["job_id"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIn("BLOCKED", json.dumps(result), "Missing evidence became a host approval")
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.repo), "status", "--porcelain"], text=True), "")

    def test_host_error_is_terminal_failed_and_never_approved(self):
        coordinator = self.coordinator()
        with patch.object(Engine, "verify", side_effect=RuntimeError("fixture model transport unavailable")):
            started = coordinator.start(self.repo, "host-task")
            result = self.wait_terminal(coordinator, started["job_id"])
        self.assertEqual(result["status"], "FAILED")
        self.assertNotIn('"status": "APPROVED"', json.dumps(result))

    def test_startup_executable_pin_ignores_ambient_model_command_override(self):
        def check_pinned_command(engine, task_id, parallel=False):
            command = engine._codex()
            if command[0] != str(Path(sys.executable).resolve()) or "attacker-command" in command:
                raise AssertionError("Ambient ASE_CODEX overrode the trusted startup executable")
            return engine.gate(task_id)
        with patch.dict(os.environ, {"ASE_CODEX": '["attacker-command", "--execute"]'}):
            coordinator = self.coordinator()
            with patch.object(Engine, "verify", check_pinned_command):
                result = self.wait_terminal(coordinator, coordinator.start(self.repo, "host-task")["job_id"])
        self.assertEqual(result["status"], "COMPLETED", result)

    def prepare_process_adapter(self):
        # Python is an operator-configured deterministic adapter, never a live
        # Codex judgment or sandbox test. The real Engine still owns every receipt.
        (self.repo / "test_value.py").write_text(
            "import unittest\nfrom app import value\nclass ValueTests(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(value, 1)\n", encoding="utf-8")
        (self.repo / "exec").write_text("""import json, pathlib, sys
args = sys.argv
schema = json.loads(pathlib.Path(args[args.index('--output-schema') + 1]).read_text())
if 'criteria' in schema['properties']:
    payload = {'criteria': [{'id': 'AC1', 'expected': 'value is 1', 'status': 'PASS',
        'evidence': ['test_value.py:test_value and recorded unit.log'],
        'method': 'deterministic process protocol adapter, not live judgment',
        'limitations': [], 'missing_coverage': []}]}
else:
    payload = {'findings': [], 'summary': 'deterministic protocol adapter'}
pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(payload))
print(json.dumps({'type': 'thread.started', 'fixture': True}))
""", encoding="utf-8")
        engine = Engine(self.repo, self.home)
        try:
            checks = engine.check("host-task")
            self.assertEqual(checks["status"], "PASS")
            self.assertEqual(checks["results"][0]["counts"]["total"], 1)
        finally:
            engine.store.close()

    def test_host_preserves_real_command_and_independent_process_receipt_protocol(self):
        self.prepare_process_adapter()
        coordinator = self.coordinator()
        result = self.run_adapter_job(coordinator)
        self.assertEqual(result["status"], "COMPLETED", result)
        self.assertEqual(result["result"]["report"]["quality_gate"]["status"], "APPROVED")
        verification = next(self.home.rglob("verify.json"))
        receipts = json.loads(verification.read_text(encoding="utf-8"))["receipts"]
        self.assertEqual({receipt["role"] for receipt in receipts}, {"ac", "review"})
        for receipt in receipts:
            self.assertEqual(receipt["exit_code"], 0)
            self.assertTrue(Path(receipt["raw"]).is_file())
            self.assertTrue(Path(receipt["log"]).is_file())

    def run_adapter_job(self, coordinator):
        # Substitute only the external model command. The absolute adapter path
        # works even when reviewer startup CWD is a separate trusted directory.
        with patch.object(Engine, "_codex", lambda engine: [sys.executable, str(self.repo / "exec")]):
            return self.wait_terminal(coordinator, coordinator.start(self.repo, "host-task")["job_id"])

    def test_host_git_reads_do_not_execute_repository_fsmonitor_program(self):
        marker = self.base / "outside-host-write.txt"
        monitor = self.repo / ".git" / "fsmonitor-canary.py"
        monitor.write_text("from pathlib import Path\nimport sys\n"
                           f"Path({str(marker)!r}).write_text('executed outside child sandbox')\n"
                           "sys.stdout.buffer.write(b'canary-token\\x00')\n", encoding="utf-8")
        command = f'"{Path(sys.executable).as_posix()}" "{monitor.as_posix()}"'
        subprocess.run(["git", "-C", str(self.repo), "config", "core.fsmonitor", command],
                       check=True, capture_output=True)
        coordinator = self.coordinator()
        # Substitute only slow model work. All host Git reads/reporting remain real.
        with patch.object(Engine, "verify", lambda engine, task_id, parallel=False: engine.gate(task_id)):
            self.wait_terminal(coordinator, coordinator.start(self.repo, "host-task")["job_id"])
        self.assertFalse(marker.exists(), "Untrusted repository Git config executed in the host process")

    def test_host_diff_does_not_execute_repository_textconv_program(self):
        self.prepare_process_adapter()
        marker = self.base / "outside-textconv-write.txt"
        program = self.repo / ".git" / "textconv-canary.py"
        program.write_text("from pathlib import Path\n"
                           f"Path({str(marker)!r}).write_text('host textconv executed')\n"
                           "print('converted source')\n", encoding="utf-8")
        command = f'"{Path(sys.executable).as_posix()}" "{program.as_posix()}"'
        subprocess.run(["git", "-C", str(self.repo), "config", "diff.canary.textconv", command],
                       check=True, capture_output=True)
        (self.repo / ".gitattributes").write_text("app.py diff=canary\n", encoding="utf-8")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        coordinator = self.coordinator()
        self.run_adapter_job(coordinator)
        self.assertFalse(marker.exists(), "Repository textconv executed outside the child sandbox")

    def test_host_diff_does_not_execute_repository_clean_filter(self):
        self.prepare_process_adapter()
        marker = self.base / "outside-filter-write.txt"
        program = self.repo / ".git" / "filter-canary.py"
        program.write_text("from pathlib import Path\nimport sys\n"
                           f"Path({str(marker)!r}).write_text('host clean filter executed')\n"
                           "sys.stdout.write(sys.stdin.read())\n", encoding="utf-8")
        command = f'"{Path(sys.executable).as_posix()}" "{program.as_posix()}"'
        subprocess.run(["git", "-C", str(self.repo), "config", "filter.canary.clean", command],
                       check=True, capture_output=True)
        (self.repo / ".gitattributes").write_text("app.py filter=canary\n", encoding="utf-8")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        coordinator = self.coordinator()
        self.run_adapter_job(coordinator)
        self.assertFalse(marker.exists(), "Repository clean filter executed outside the child sandbox")


class HostStdinTests(unittest.TestCase):
    def test_execution_child_cannot_wait_on_or_consume_live_mcp_stdin(self):
        with tempfile.TemporaryDirectory(prefix="ase-stdin-") as temp:
            base = Path(temp)
            helper = base / "helper.py"
            helper.write_text(
                "from ase.core import execute\nfrom pathlib import Path\nimport json,sys\n"
                f"result=execute([sys.executable,'-c','import sys; print(repr(sys.stdin.read()))'], {str(base)!r}, Path({str(base / 'child.log')!r}), timeout=1)\n"
                "print(json.dumps(result),flush=True)\n", encoding="utf-8")
            process = subprocess.Popen([sys.executable, str(helper)], cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT)}, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                # The parent intentionally leaves the input channel open, just
                # like a live NDJSON MCP client waiting for a tool response.
                process.wait(timeout=5)
                output, errors = process.stdout.read(), process.stderr.read()
                self.assertEqual(process.returncode, 0, output + errors)
                receipt = json.loads(output)
                self.assertFalse(receipt["timed_out"], "Model command inherited the still-open MCP input channel")
                self.assertEqual(receipt["exit_code"], 0)
                self.assertEqual((base / "child.log").read_text(encoding="utf-8").strip(), "''")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    pipe.close()


if __name__ == "__main__":
    unittest.main()
