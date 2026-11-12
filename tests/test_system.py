"""Executable integration tests. Fake Codex tests validate the receipt protocol only.

Real model independence is exercised separately by the disposable live demonstration.
"""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ase-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.home = self.base / "state"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        self.git("init", "-b", "feature/test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "ASE Tests")
        (self.repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.repo / "test_app.py").write_text(
            "import unittest\nfrom app import add\nclass AddTests(unittest.TestCase):\n"
            "    def test_add(self): self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.cli("init")

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def cli(self, *args, ok=(0,)):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "legacy_cli_fixture.py"), "--repo", str(self.repo), "--home", str(self.home), *args],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertIn(result.returncode, ok, result.stdout + result.stderr)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail("CLI did not return JSON: " + result.stdout + result.stderr)

    def start(self, commands=None, **extra):
        contract = {
            "id": "task-one", "request": "Implement integer addition with executable evidence.",
            "risk": "MEDIUM", "acceptance": [{"id": "AC1", "expected": "2 + 3 returns 5",
                "mandatory": True, "commands": ["unit"]}],
            "commands": commands if commands is not None else [{"id": "unit",
                "argv": [sys.executable, "-m", "unittest", "discover", "-v"],
                "kind": "test", "required": True, "parser": "unittest"}],
            "protected_paths": [], "max_attempts": 2,
        }
        contract.update(extra)
        source = self.base / "task.json"
        source.write_text(json.dumps(contract), encoding="utf-8")
        result = self.cli("start", str(source))
        self.task = result["task_id"]
        self.state = Path(result["state_dir"])
        return result

    def fixture_codex(self, *, fail_ac=False, finding=False, severity="P1"):
        fixture = self.base / "codex_fixture.py"
        # This deterministic process is deliberately identified as a test fixture.
        fixture.write_text("""import json, pathlib, sys
args = sys.argv
if '--output-last-message' not in args:
    pathlib.Path('app.py').write_text('def add(a, b):\\n    return a + b\\n', encoding='utf-8')
    print(json.dumps({'type': 'agent.complete', 'fixture': True}))
    raise SystemExit(0)
out = pathlib.Path(args[args.index('--output-last-message') + 1])
schema = json.loads(pathlib.Path(args[args.index('--output-schema') + 1]).read_text(encoding='utf-8'))
if 'criteria' in schema.get('properties', {}):
    payload = {'criteria': [{'id': 'AC1', 'status': STATUS, 'expected': '2 + 3 returns 5',
        'evidence': ['test_app.py:test_add; recorded unit command exercised 2 + 3'],
        'method': 'deterministic receipt fixture; not a live independent model',
        'limitations': [], 'missing_coverage': []}]}
else:
    payload = {'findings': FINDINGS, 'summary': 'deterministic receipt protocol fixture'}
out.write_text(json.dumps(payload), encoding='utf-8')
print(json.dumps({'type': 'thread.started', 'thread_id': 'fixture-only'}))
""".replace("STATUS", repr("FAIL" if fail_ac else "PASS")).replace(
            "FINDINGS", repr([{"severity": severity, "path": "app.py", "line": 2,
                               "message": "Incorrect arithmetic"}] if finding else [])), encoding="utf-8")
        self.env["ASE_CODEX"] = json.dumps([sys.executable, str(fixture)])

    def checked_and_reviewed(self):
        self.start()
        self.cli("check", self.task)
        self.fixture_codex()
        self.cli("verify", self.task)

    def assert_gate(self, expected, *, contains=None):
        codes = {"APPROVED": (0,), "REJECTED": (2,), "BLOCKED": (3,)}
        result = self.cli("gate", self.task, ok=codes[expected])
        self.assertEqual(result["status"], expected)
        if contains:
            self.assertIn(contains.lower(), json.dumps(result).lower())
        return result

    def test_actual_unit_command_runs_and_missing_independent_review_blocks(self):
        self.start()
        receipt = self.cli("check", self.task)
        self.assertIn("results", receipt)
        self.assertIn("1", json.dumps(receipt))
        self.assert_gate("BLOCKED")

    def test_protocol_positive_gate(self):
        self.checked_and_reviewed()
        self.assert_gate("APPROVED")

    def test_stale_code_cannot_reuse_evidence(self):
        self.checked_and_reviewed()
        (self.repo / "app.py").write_text("def add(a, b):\n    return 99\n", encoding="utf-8")
        self.assert_gate("REJECTED")

    def test_untracked_code_invalidates_evidence(self):
        self.checked_and_reviewed()
        (self.repo / "new_module.py").write_text("raise RuntimeError('new code')\n", encoding="utf-8")
        self.assert_gate("REJECTED")

    def test_failed_build_is_rejected(self):
        self.start(commands=[{"id": "unit", "argv": [sys.executable, "-c", "raise SystemExit(7)"],
                             "kind": "build", "required": True}])
        self.cli("check", self.task, ok=(2, 3))
        self.fixture_codex()
        self.cli("verify", self.task)
        self.assert_gate("REJECTED")

    def test_missing_executable_is_blocked(self):
        self.start(commands=[{"id": "unit", "argv": ["ase-nonexistent-sdk-12345"],
                             "kind": "test", "parser": "unittest", "required": True}])
        self.cli("check", self.task, ok=(2, 3))
        self.assert_gate("BLOCKED")

    def test_zero_tests_does_not_pass(self):
        self.start(commands=[{"id": "unit", "argv": [sys.executable, "-c",
                              "print('Ran 0 tests in 0.0s\\nOK')"], "kind": "test",
                             "parser": "unittest", "required": True}])
        self.cli("check", self.task, ok=(2, 3))
        self.fixture_codex()
        self.cli("verify", self.task)
        result = self.cli("gate", self.task, ok=(2, 3))
        self.assertNotEqual(result["status"], "APPROVED")

    def test_independent_failed_ac_rejects(self):
        self.start()
        self.cli("check", self.task)
        self.fixture_codex(fail_ac=True)
        self.cli("verify", self.task, ok=(0, 2))
        self.assert_gate("REJECTED")

    def test_blocking_review_rejects(self):
        self.start()
        self.cli("check", self.task)
        self.fixture_codex(finding=True)
        self.cli("verify", self.task, ok=(0, 2))
        self.assert_gate("REJECTED")

    def test_p2_review_rejects_even_with_passing_tests_and_acceptance(self):
        self.start()
        checks = self.cli("check", self.task)
        self.assertEqual(checks["status"], "PASS")
        self.fixture_codex(finding=True, severity="P2")
        self.cli("verify", self.task)
        self.assertEqual(json.loads((self.state / "ac.json").read_text())["criteria"][0]["status"], "PASS")
        self.assert_gate("REJECTED", contains="Incorrect arithmetic")

    def test_p3_review_remains_advisory(self):
        self.start()
        self.cli("check", self.task)
        self.fixture_codex(finding=True, severity="P3")
        self.cli("verify", self.task)
        self.assert_gate("APPROVED")

    def test_protected_file_change_rejects(self):
        self.start(protected_paths=["app.py"])
        (self.repo / "app.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
        self.cli("check", self.task)
        self.fixture_codex()
        self.cli("verify", self.task)
        self.assert_gate("REJECTED")

    def test_task_and_context_persist_across_processes(self):
        self.start()
        first = self.cli("status", self.task)
        second = self.cli("status", self.task)
        self.assertEqual(first["task"], second["task"])
        self.assertIn(self.task, json.dumps(self.cli("context")))
        self.cli("backup")
        self.assertTrue(any(self.home.rglob("*.db")) or any(self.home.rglob("*.sqlite*")))

    def test_project_isolation(self):
        self.start()
        original = self.repo
        self.repo = self.base / "other-repo"
        self.repo.mkdir()
        self.git("init", "-b", "feature/other")
        self.cli("init")
        result = self.cli("status", self.task, ok=(2, 3))
        self.assertNotIn("Implement integer addition", json.dumps(result))
        self.repo = original
        self.assertIn(self.task, json.dumps(self.cli("status", self.task)))

    def test_forged_plain_pass_does_not_approve(self):
        self.start()
        self.cli("check", self.task)
        # Writing a superficial success file is not an independent execution receipt.
        candidate = self.state
        (candidate / "ac.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        result = self.cli("gate", self.task, ok=(2, 3))
        self.assertNotEqual(result["status"], "APPROVED")

    def test_verifier_unavailable_is_blocked(self):
        self.start()
        self.cli("check", self.task)
        self.env["ASE_CODEX"] = "ase-nonexistent-codex-12345"
        self.cli("verify", self.task, ok=(2, 3))
        self.assert_gate("BLOCKED")

    def test_skipped_suite_cannot_satisfy_required_tests(self):
        (self.repo / "test_app.py").write_text(
            "import unittest\n@unittest.skip('infrastructure unavailable')\n"
            "class AddTests(unittest.TestCase):\n    def test_add(self): pass\n", encoding="utf-8")
        self.start()
        self.cli("check", self.task, ok=(2, 3))
        self.fixture_codex()
        self.cli("verify", self.task)
        result = self.cli("gate", self.task, ok=(2, 3))
        self.assertNotEqual(result["status"], "APPROVED")

    def test_timeout_records_failure_and_cannot_approve(self):
        self.start(commands=[{"id": "unit", "argv": [sys.executable, "-c",
                              "import time; time.sleep(5)"], "kind": "test",
                             "parser": "unittest", "timeout": 0.1, "required": True}])
        self.cli("check", self.task, ok=(2, 3))
        result = self.cli("gate", self.task, ok=(2, 3))
        self.assertNotEqual(result["status"], "APPROVED")

    def test_tampered_verifier_raw_artifact_cannot_approve(self):
        self.checked_and_reviewed()
        candidates = list(self.home.rglob("ac.raw.json"))
        self.assertTrue(candidates, "Runner did not preserve the independent source artifact")
        candidates[0].write_text('{"criteria": [{"id": "AC1", "status": "PASS"}]}', encoding="utf-8")
        self.assert_gate("REJECTED")

    def test_local_remote_publish_requires_authorization_and_gate(self):
        self.checked_and_reviewed()
        remote = self.base / "remote.git"
        self.git("init", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        plan = self.cli("publish", self.task)
        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["published"])
        self.cli("publish", self.task, "--execute", ok=(3,))
        self.cli("authorize", "--remote", "origin")
        published = self.cli("publish", self.task, "--execute")
        self.assertTrue(published["published"])
        remote_sha = subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/feature/test"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(remote_sha, self.git("rev-parse", "HEAD"))
        (self.repo / "app.py").write_text("def add(a, b): return 0\n", encoding="utf-8")
        rejected = self.cli("publish", self.task, "--execute", ok=(2,))
        self.assertFalse(rejected["published"])
        unchanged = subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/feature/test"],
                                   capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(unchanged, remote_sha)

    def test_protected_branch_publishing_rejected(self):
        self.checked_and_reviewed()
        self.git("checkout", "-b", "main")
        rejected = self.cli("publish", self.task, ok=(2,))
        self.assertFalse(rejected["published"])

    def test_interrupted_task_lock_recovers_without_duplicate_implementation(self):
        self.checked_and_reviewed()
        lock = self.state / "operation.lock"
        lock.write_text(json.dumps({"pid": 2147483646, "created": "2000-01-01"}), encoding="utf-8")
        self.cli("resume", self.task, ok=(3,))
        result = self.cli("resume", self.task, "--recover")
        self.assertEqual(result["quality_gate"]["status"], "APPROVED")
        self.assertFalse(lock.exists())
        self.assertFalse(list(self.state.glob("implementation-*.json")),
                         "Approved recovery unexpectedly started another implementation")

    def test_live_lock_owner_prevents_recovery(self):
        self.start()
        lock = self.state / "operation.lock"
        lock.write_text(json.dumps({"pid": os.getpid(), "created": "2000-01-01"}), encoding="utf-8")
        result = self.cli("resume", self.task, "--recover", ok=(3,))
        self.assertIn("still running", json.dumps(result))
        self.assertTrue(lock.exists())

    def test_parallel_independent_receipt_sessions(self):
        self.start()
        self.cli("check", self.task)
        self.fixture_codex()
        self.cli("verify", self.task, "--parallel")
        self.assert_gate("APPROVED")
        bundle = json.loads((self.state / "verify.json").read_text(encoding="utf-8"))
        self.assertTrue(bundle["parallel"])
        self.assertEqual({r["role"] for r in bundle["receipts"]}, {"ac", "review"})

    def test_human_and_agent_time_remain_separate(self):
        self.start()
        for source, confirmed in (("agent", False), ("human", False), ("human", True)):
            args = ["time", self.task, "--activity", "review", "--start", "2026-11-01T10:00:00+02:00",
                    "--end", "2026-11-01T10:30:00+02:00", "--source", source]
            if confirmed:
                args.append("--confirmed")
            self.cli(*args)
        report = self.cli("daily", "--date", "2026-11-01")
        self.assertEqual(report["agent_seconds"], 1800)
        self.assertEqual(report["draft_human_seconds"], 1800)
        self.assertEqual(report["confirmed_human_seconds"], 1800)

    def test_high_risk_requires_approval_for_current_revision(self):
        self.start(risk="HIGH")
        self.cli("check", self.task)
        self.fixture_codex()
        self.cli("verify", self.task)
        self.assert_gate("BLOCKED")
        self.cli("approve-high-risk", self.task)
        self.assert_gate("APPROVED")
        (self.repo / "app.py").write_text("def add(a, b): return a + b + 0\n", encoding="utf-8")
        self.assert_gate("REJECTED")

    def test_complete_workflow_protocol_and_idempotent_resume(self):
        self.start()
        (self.repo / "app.py").write_text("def add(a, b): return 99\n", encoding="utf-8")
        self.fixture_codex()
        result = self.cli("run", self.task)
        self.assertEqual(result["quality_gate"]["status"], "APPROVED")
        self.assertEqual(len(list(self.state.glob("implementation-*.json"))), 1)
        self.cli("resume", self.task)
        self.assertEqual(len(list(self.state.glob("implementation-*.json"))), 1)

    def test_automatic_remediation_stops_at_configured_limit(self):
        self.start(max_attempts=2)
        self.fixture_codex(fail_ac=True)
        result = self.cli("run", self.task, ok=(2,))
        self.assertEqual(result["quality_gate"]["status"], "REJECTED")
        self.assertEqual(len(list(self.state.glob("implementation-*.json"))), 2)

    def cycle_fixture(self, persistent=False, unavailable_ac=False):
        """Separate deterministic processes test orchestration only, not judgment."""
        fixture = self.base / "cycle_fixture.py"
        events = self.base / "cycle-events.json"
        fixture.write_text("""import hashlib,json,pathlib,sys
args=sys.argv
events=pathlib.Path(EVENT_PATH)
records=json.loads(events.read_text()) if events.exists() else []
app=pathlib.Path('app.py')
if '--output-last-message' not in args:
    attempt=1+sum(r['role']=='implementation' for r in records)
    if attempt>1:
        prompt=args[-1]
        assert 'Prior review evidence:' in prompt and 'app.py' in prompt and 'Remove review marker' in prompt
        assert 'Prior AC evidence:' in prompt and 'Prior check evidence:' in prompt
    app.write_text('def add(a, b):\\n    return a + b\\n'+('# review marker\\n' if attempt==1 or PERSISTENT else ''))
    role='implementation'
else:
    out=pathlib.Path(args[args.index('--output-last-message')+1])
    schema=json.loads(pathlib.Path(args[args.index('--output-schema')+1]).read_text())
    if 'criteria' in schema.get('properties',{}):
        role='ac'
        if UNAVAILABLE_AC:
            records.append({'role':role,'source_sha256':hashlib.sha256(app.read_bytes()).hexdigest()})
            events.write_text(json.dumps(records))
            print('independent AC fixture unavailable')
            raise SystemExit(7)
        payload={'criteria':[{'id':'AC1','status':'PASS','expected':'2 + 3 returns 5',
            'evidence':['test_app.py:test_add; actual unit command passed'],
            'method':'deterministic protocol fixture, not independent semantic validation',
            'limitations':[],'missing_coverage':[]}]}
    else:
        role='review'
        finding={'severity':'P2','path':'app.py','line':3,'message':'Remove review marker'}
        payload={'findings':[finding] if 'review marker' in app.read_text() else [],
                 'summary':'deterministic cycle protocol fixture'}
    out.write_text(json.dumps(payload))
records.append({'role':role,'source_sha256':hashlib.sha256(app.read_bytes()).hexdigest()})
events.write_text(json.dumps(records))
print(json.dumps({'type':'agent.complete','fixture':True,'role':role}))
""".replace("EVENT_PATH", repr(str(events))).replace("PERSISTENT", repr(persistent)).replace(
            "UNAVAILABLE_AC", repr(unavailable_ac)), encoding="utf-8")
        self.env["ASE_CODEX"] = json.dumps([sys.executable, str(fixture)])
        return events

    def test_p2_automatic_cycle_retests_and_reviews_corrected_source(self):
        self.start()
        events = self.cycle_fixture()
        result = self.cli("run", self.task)
        self.assertEqual(result["quality_gate"]["status"], "APPROVED")
        records = json.loads(events.read_text())
        self.assertEqual([r["role"] for r in records], ["implementation", "ac", "review"] * 2)
        self.assertNotEqual(records[0]["source_sha256"], records[3]["source_sha256"])
        cycles = [json.loads(p.read_text()) for p in sorted((self.state / "cycles").glob("*/cycle.json"))]
        self.assertEqual([c["gate"]["status"] for c in cycles], ["REJECTED", "APPROVED"])
        self.assertEqual([c["attempt"] for c in cycles], [1, 2])
        for cycle in cycles:
            folder = Path(cycle["archive"])
            checks = json.loads((folder / "check.json").read_text())
            verify = json.loads((folder / "verify.json").read_text())
            self.assertEqual(checks["status"], "PASS")
            self.assertEqual(checks["fingerprint"], verify["fingerprint"])
            self.assertEqual(sorted(r["role"] for r in verify["receipts"]), ["ac", "review"])
            for receipt in verify["receipts"]:
                self.assertEqual(receipt["fingerprint"], checks["fingerprint"])
                for suffix, key in ((".raw.json", "raw_sha256"), (".session.log", "log_sha256")):
                    import hashlib
                    self.assertEqual(hashlib.sha256((folder / (receipt["role"] + suffix)).read_bytes()).hexdigest(), receipt[key])
        self.assertNotEqual(cycles[0]["gate"]["fingerprint"], cycles[1]["gate"]["fingerprint"])
        before = {str(p):p.read_bytes() for p in (self.state / "cycles").rglob("*") if p.is_file()}
        self.cli("resume", self.task)
        self.assertEqual(json.loads(events.read_text()), records)
        self.assertEqual({str(p):p.read_bytes() for p in (self.state / "cycles").rglob("*") if p.is_file()}, before)

    def test_repeated_p2_exhausts_budget_and_resume_does_not_reset_or_reverify(self):
        self.start(max_attempts=2)
        events = self.cycle_fixture(persistent=True)
        result = self.cli("run", self.task, ok=(2,))
        self.assertEqual(result["quality_gate"]["status"], "REJECTED")
        records = json.loads(events.read_text())
        self.assertEqual([r["role"] for r in records], ["implementation", "ac", "review"] * 2)
        self.assertEqual(json.loads((self.state / "progress.json").read_text())["attempts"], 2)
        self.cli("resume", self.task, ok=(2,))
        self.assertEqual(json.loads(events.read_text()), records)
        self.assertEqual(len(list((self.state / "cycles").glob("*/cycle.json"))), 2)

    def test_mixed_unavailable_ac_and_p2_stops_repairs_and_resume_probes_without_budget_use(self):
        self.start(max_attempts=2)
        events = self.cycle_fixture(unavailable_ac=True)
        result = self.cli("run", self.task, ok=(2, 3))
        self.assertEqual(len(list(self.state.glob("implementation-*.json"))), 1)
        verdict = result["quality_gate"]
        self.assertEqual(verdict["status"], "REJECTED")
        self.assertTrue(any("ac" in reason for reason in verdict["blocked_reasons"]))
        self.assertTrue(any("Remove review marker" in reason for reason in verdict["rejected_reasons"]))
        progress = json.loads((self.state / "progress.json").read_text())
        self.assertEqual(progress["attempts"], 1)
        before = json.loads(events.read_text())
        rerun = self.cli("run", self.task, ok=(2, 3))
        self.assertEqual(rerun["quality_gate"]["status"], "REJECTED")
        self.assertEqual(json.loads(events.read_text()), before)
        self.assertEqual(len(list(self.state.glob("implementation-*.json"))), 1)
        resumed = self.cli("resume", self.task, ok=(2, 3))
        self.assertEqual(resumed["quality_gate"]["status"], "REJECTED")
        after = json.loads(events.read_text())
        self.assertEqual(after[:len(before)], before)
        self.assertEqual([event["role"] for event in after[len(before):]], ["ac", "review"])
        self.assertEqual(len(list(self.state.glob("implementation-*.json"))), 1)
        self.assertEqual(json.loads((self.state / "progress.json").read_text())["attempts"], 1)
        self.cycle_fixture(unavailable_ac=False)
        recovered = self.cli("resume", self.task)
        self.assertEqual(recovered["quality_gate"]["status"], "APPROVED")
        final = json.loads(events.read_text())
        self.assertEqual([event["role"] for event in final[len(after):]],
                         ["ac", "review", "implementation", "ac", "review"])
        self.assertEqual(len(list(self.state.glob("implementation-*.json"))), 2)
        self.assertEqual(json.loads((self.state / "progress.json").read_text())["attempts"], 2)

    def test_pending_resume_with_blocked_check_does_not_start_independent_sessions(self):
        self.start(max_attempts=1, commands=[{
            "id": "unit", "kind": "test", "parser": "unittest", "required": True,
            "argv": ["ase-nonexistent-test-tool-12345"]}])
        events = self.cycle_fixture()
        (self.state / "progress.json").write_text(json.dumps({
            "attempts": 1, "implementation_completed": True, "cycle_completed": False}))
        result = self.cli("resume", self.task, ok=(2, 3))
        self.assertFalse(events.exists())
        self.assertTrue(result["quality_gate"]["blocked_reasons"])
        self.assertFalse(list((self.state / "cycles").glob("*/cycle.json")))

    def test_manual_blocked_verification_probe_keeps_zero_attempts_and_validates_progress_first(self):
        self.start()
        events = self.cycle_fixture(unavailable_ac=True)
        (self.repo / "app.py").write_text("def add(a, b):\n    return a + b\n# review marker\n")
        self.cli("check", self.task)
        self.cli("verify", self.task)
        self.cli("resume", self.task, ok=(2,))
        self.assertFalse((self.state / "progress.json").exists())
        self.assertFalse(list(self.state.glob("implementation-*.json")))
        cycle = json.loads(next((self.state / "cycles").glob("*/cycle.json")).read_text())
        self.assertEqual(cycle["attempt"], 0)
        before = events.read_bytes()
        (self.state / "progress.json").write_text(json.dumps({
            "attempts": -1, "implementation_completed": True, "cycle_completed": True}))
        result = self.cli("resume", self.task, ok=(3,))
        self.assertIn("progress", result["error"].lower())
        self.assertEqual(events.read_bytes(), before)

    def test_malformed_resume_progress_cannot_reset_attempt_budget(self):
        self.start()
        self.cycle_fixture()
        (self.state / "progress.json").write_text(json.dumps({"attempts": -1, "implementation_completed": True}))
        result = self.cli("resume", self.task, ok=(3,))
        self.assertIn("progress", result["error"].lower())
        self.assertFalse(list(self.state.glob("implementation-*.json")))

    def test_resume_finishes_pending_final_attempt_verification_without_new_implementation(self):
        self.start(max_attempts=1)
        self.fixture_codex()
        (self.state / "progress.json").write_text(json.dumps({
            "attempts": 1, "implementation_completed": True, "cycle_completed": False}))
        result = self.cli("resume", self.task)
        self.assertEqual(result["quality_gate"]["status"], "APPROVED")
        self.assertFalse(list(self.state.glob("implementation-*.json")))
        self.assertEqual(len(list((self.state / "cycles").glob("*/cycle.json"))), 1)
        progress = json.loads((self.state / "progress.json").read_text())
        self.assertEqual(progress["attempts"], 1)
        self.assertTrue(progress["cycle_completed"])

    def test_multiple_push_urls_cannot_bypass_remote_authorization(self):
        self.checked_and_reviewed()
        first, second = self.base / "first.git", self.base / "second.git"
        self.git("init", "--bare", str(first))
        self.git("init", "--bare", str(second))
        self.git("remote", "add", "origin", str(first))
        self.cli("authorize", "--remote", "origin")
        self.git("config", "--add", "remote.origin.pushurl", str(first))
        self.git("config", "--add", "remote.origin.pushurl", str(second))
        result = self.cli("publish", self.task, "--execute", ok=(2, 3))
        self.assertFalse(result.get("published", False))
        self.cli("authorize", "--remote", "origin", ok=(2, 3))
        for remote in (first, second):
            result = subprocess.run(["git", "--git-dir", str(remote), "show-ref"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, "An unauthorized destination received a branch")

    def test_failed_optional_command_linked_to_mandatory_ac_rejects(self):
        commands = [{"id": "unit", "argv": [sys.executable, "-m", "unittest", "discover", "-v"],
                     "kind": "test", "required": True, "parser": "unittest"},
                    {"id": "smoke", "argv": [sys.executable, "-c", "raise SystemExit(7)"],
                     "kind": "smoke", "required": False}]
        self.start(commands=commands, acceptance=[{"id": "AC1", "expected": "2 + 3 returns 5",
                                                  "mandatory": True, "commands": ["unit", "smoke"]}])
        self.cli("check", self.task)
        self.fixture_codex()
        self.cli("verify", self.task)
        self.assert_gate("REJECTED")

    def test_non_feature_branch_publishing_rejected(self):
        self.checked_and_reviewed()
        for branch in ("trunk", "staging"):
            self.git("checkout", "-b", branch)
            self.cli("publish", self.task, ok=(2,))

    def install_hook(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "install_hook.py"),
            "--repo", str(self.repo), "--task", self.task, "--home", str(self.home)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # These are frozen v1 receipt protocol regressions. The production
        # public CLI deliberately refuses new authority from a legacy task.
        hook = self.repo / ".git" / "hooks" / "pre-push"
        original = hook.read_text(encoding="utf-8")
        hook.write_text(original.replace(shlex.join([sys.executable, "-m", "ase"]),
                                        shlex.join([sys.executable, str(ROOT / "tests" / "legacy_cli_fixture.py")])), encoding="utf-8", newline="\n")

    def test_native_git_hook_blocks_main_divergent_head_and_failed_gate(self):
        self.start()
        remote = self.base / "hook-remote.git"
        self.git("init", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.install_hook()
        self.git("checkout", "-b", "main")
        push = subprocess.run(["git", "push", "origin", "main"], cwd=self.repo,
                              env=self.env, capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("feature branches only", push.stdout + push.stderr)
        self.git("checkout", "feature/test")
        self.git("branch", "feature/old")
        (self.repo / "new.txt").write_text("new revision", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "new head")
        push = subprocess.run(["git", "push", "origin", "feature/old:refs/heads/feature/test"], cwd=self.repo,
                              env=self.env, capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("only verified HEAD", push.stdout + push.stderr)
        push = subprocess.run(["git", "push", "origin", "feature/test"], cwd=self.repo,
                              env=self.env, capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("BLOCKED", push.stdout + push.stderr)
        self.assertFalse(self.git("--git-dir", str(remote), "for-each-ref").strip())

    def test_existing_git_hook_is_preserved(self):
        self.start()
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "install_hook.py"),
            "--repo", str(self.repo), "--task", self.task, "--home", str(self.home)],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\nexit 42\n")

    def test_verified_publisher_works_with_hook_and_wrong_target_is_denied(self):
        self.checked_and_reviewed()
        remote = self.base / "positive-hook.git"
        self.git("init", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.install_hook()
        wrong = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"],
                               cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("destination must match", wrong.stdout + wrong.stderr)
        self.cli("authorize", "--remote", "origin")
        self.assertTrue(self.cli("publish", self.task, "--execute")["published"])
        self.git("checkout", "-b", "bugfix/test")
        self.assertTrue(self.cli("publish", self.task, "--execute")["published"])
        self.assertEqual(self.git("--git-dir", str(remote), "rev-parse", "refs/heads/bugfix/test"),
                         self.git("rev-parse", "HEAD"))

    def test_weekly_preserves_human_agent_attribution_and_date_window(self):
        self.start()
        for day, source, confirmed in (("2026-10-25", "human", True), ("2026-10-26", "human", True),
                                       ("2026-11-01", "agent", False), ("2026-11-01", "human", False)):
            args = ["time", self.task, "--activity", "review", "--start", day + "T10:00:00+02:00",
                    "--end", day + "T10:30:00+02:00", "--source", source]
            if confirmed:
                args.append("--confirmed")
            self.cli(*args)
        report = self.cli("weekly", "--date", "2026-11-01")
        self.assertEqual(report["agent_seconds"], 1800)
        self.assertEqual(report["draft_human_seconds"], 1800)
        self.assertEqual(report["confirmed_human_seconds"], 1800)
        self.assertEqual(sum(len(day["entries"]) for day in report["days"]), 3)

    def test_time_correction_is_audited_and_confirmation_resets(self):
        self.start()
        entry = self.cli("time", self.task, "--activity", "review", "--start", "2026-11-01T10:00:00+02:00",
                         "--end", "2026-11-01T10:30:00+02:00", "--source", "human", "--confirmed")
        result = self.cli("correct-time", str(entry["entry_id"]), "--end", "2026-11-01T11:00:00+02:00",
                          "--reason", "User corrected measured end time")
        self.assertEqual(result["entry"]["seconds"], 3600)
        self.assertFalse(result["entry"]["confirmed"])
        report = self.cli("daily", "--date", "2026-11-01")
        self.assertEqual(report["confirmed_human_seconds"], 0)
        self.assertEqual(report["draft_human_seconds"], 3600)
        database = next(self.home.rglob("memory.sqlite3"))
        with closing(sqlite3.connect(database)) as db:
            row = db.execute("SELECT before_json,after_json,reason FROM time_entry_audit WHERE id=?", (result["audit_id"],)).fetchone()
        self.assertEqual(json.loads(row[0])["seconds"], 1800)
        self.assertEqual(json.loads(row[1])["seconds"], 3600)
        self.assertEqual(row[2], "User corrected measured end time")
        # Another project's numeric entry IDs must not access this project's record.
        original = self.repo
        self.repo = self.base / "isolated-corrections"
        self.repo.mkdir()
        self.git("init", "-b", "feature/other")
        self.cli("init")
        self.cli("correct-time", str(entry["entry_id"]), "--activity", "leak", ok=(3,))
        self.repo = original

    def test_session_summary_decisions_and_unresolved_questions_persist(self):
        self.start()
        self.cli("session-summary", self.task, "--summary", "Reviewed API boundaries",
                 "--decision", "Use integer arithmetic", "--unresolved", "Clarify overflow behavior")
        context = self.cli("context")
        self.assertIn("Reviewed API boundaries", json.dumps(context["sessions"]))
        self.assertIn("Use integer arithmetic", json.dumps(context["sessions"]))
        self.assertIn("Clarify overflow behavior", json.dumps(context["sessions"]))

    def test_v1_memory_migration_backs_up_and_preserves_tasks(self):
        self.start()
        database = next(self.home.rglob("memory.sqlite3"))
        # Reconstruct the actual v1 schema from a freshly populated database.
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DROP TABLE knowledge_claims")
            db.execute("DROP TABLE time_entry_audit")
            db.execute("DROP TABLE task_identities")
            db.execute("DROP TABLE checkpoints")
            db.execute("DROP TABLE source_history")
            db.execute("ALTER TABLE sessions DROP COLUMN important_decisions")
            db.execute("ALTER TABLE sessions DROP COLUMN unresolved_questions")
            db.execute("PRAGMA user_version=1")
        self.assertIn(self.task, json.dumps(self.cli("status", self.task)))
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT request FROM tasks WHERE id=?", (self.task,)).fetchone()[0],
                             "Implement integer addition with executable evidence.")
        backups = list(database.parent.glob("backups/*.sqlite3"))
        self.assertTrue(backups)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_old_raw_evidence_expires_but_structured_memory_survives(self):
        self.start()
        self.cli("session-summary", self.task, "--summary", "Retain the concise project decision")
        old_log, handoff = self.state / "ac.session.log", self.state / "handoff.json"
        unknown, current = self.state / "user-notes.log", self.state / "review.session.log"
        for path in (old_log, handoff, unknown, current):
            path.write_text("potential source excerpt", encoding="utf-8")
        expired = time.time() - 40 * 86400
        for path in (old_log, handoff, unknown):
            os.utime(path, (expired, expired))
        context = self.cli("context")
        self.assertFalse(old_log.exists())
        self.assertFalse(handoff.exists())
        self.assertTrue(current.exists())
        self.assertTrue(unknown.exists())
        self.assertIn("Retain the concise project decision", json.dumps(context))

    def test_active_operation_preserves_old_logs_during_retention(self):
        self.start()
        log = self.state / "ac.session.log"
        log.write_text("running evidence", encoding="utf-8")
        expired = time.time() - 40 * 86400
        os.utime(log, (expired, expired))
        (self.state / "operation.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        self.cli("context")
        self.assertTrue(log.exists())

    def test_cycle_verbose_retention_preserves_receipts_unknown_files_and_hardlinks(self):
        self.start()
        cycle = self.state / "cycles" / "0001-example"
        cycle.mkdir(parents=True)
        logs = [cycle / "ac.session.log", cycle / "review.session.log",
                cycle / "implementation-1.log", cycle / "handoff.json"]
        receipt, unknown = cycle / "verify.json", cycle / "unrelated.log"
        for path in [*logs, receipt, unknown]:
            path.write_text("retained or expired evidence", encoding="utf-8")
        outside = self.base / "outside.log"
        outside.write_text("never delete the hardlink alias", encoding="utf-8")
        linked = cycle / "implementation-2.log"
        os.link(outside, linked)
        expired = time.time() - 40 * 86400
        for path in [*logs, receipt, unknown, linked]:
            os.utime(path, (expired, expired))
        self.cli("context")
        self.assertTrue(all(not p.exists() for p in logs))
        self.assertTrue(receipt.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(linked.exists())
        self.assertEqual(outside.read_text(), "never delete the hardlink alias")

    def test_invalid_log_retention_is_blocked(self):
        self.env["ASE_LOG_RETENTION_DAYS"] = "0"
        self.cli("context", ok=(3,))

    def test_installed_launcher_canonicalizes_state_home_through_directory_alias(self):
        # Windows runners may supply TEMP through an 8.3 alias; a directory
        # reparse alias reproduces the same lexical-vs-canonical path boundary.
        alias = self.base / "launcher-base-alias"
        try:
            alias.symlink_to(self.base, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.base)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.base = alias
        self.test_installed_launcher_recovers_missing_environment_and_preserves_explicit_state_home()

    def test_installed_launcher_recovers_missing_environment_and_preserves_explicit_state_home(self):
        codex, skills = self.base / "codex", self.base / "skills"
        install_env = dict(self.env, LOCALAPPDATA=str(self.base / "local"))
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "install.py"),
            "--global", "--home", str(codex), "--skills", str(skills)], env=install_env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        launch_env = dict(install_env)
        for name in ("HOME", "USERPROFILE", "CODEX_HOME"):
            launch_env.pop(name, None)
        if os.name == "nt":
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            self.assertIsNotNone(powershell)
            command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(codex / "bin" / "ase.ps1")]
        else:
            command = [str(codex / "bin" / "ase")]
        def launcher(*args):
            result = subprocess.run([*command, "--repo", str(self.repo), *args], env=launch_env,
                                    cwd=self.base, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        def assert_state_home(state_dir, target):
            actual = Path(state_dir)
            # Engine returns canonical paths. Compare to the same filesystem
            # identity instead of a runner TEMP alias spelling (e.g. RUNNER~1).
            self.assertEqual(actual, actual.resolve())
            self.assertTrue(actual.is_relative_to(target.resolve()),
                            f"State path {actual} is outside canonical target {target.resolve()}")
        initialized = launcher("init")
        self.assertEqual(Path(initialized["root"]), self.repo.resolve())
        for args, target in ((("--home", str(self.base / "explicit")), self.base / "explicit"),
                             (("--home=" + str(self.base / "absolute-equals"),), self.base / "absolute-equals"),
                             (("--home=equals",), self.base / "equals")):
            initialized_at_target = launcher(*args, "init")
            self.assertEqual(Path(initialized_at_target["root"]), self.repo.resolve())
            self.assertTrue((target.resolve() / "registry.sqlite3").is_file())
        script = ("import os, unittest\nclass EnvironmentTests(unittest.TestCase):\n"
                  "    def test_codex_home(self): self.assertIsNone(os.environ.get('CODEX_HOME'))\n"
                  "    def test_home(self): self.assertIsNone(os.environ.get('HOME'))\n")
        if os.name == "nt":
            script += "    def test_userprofile(self): self.assertIsNone(os.environ.get('USERPROFILE'))\n"
        (self.repo / "test_launcher_env.py").write_text(script, encoding="utf-8")
        contract = {"id": "launcher", "request": "Verify environment in actual child process",
                    "acceptance": [{"id": "AC1", "expected": "Codex has an explicit home", "commands": ["unit"]}],
                    "commands": [{"id": "unit", "argv": [sys.executable, "-m", "unittest", "test_launcher_env", "-v"],
                                  "kind": "test", "parser": "unittest", "required": True}]}
        contract.update(schema_version=2, profile="software", scope={"resources": ["test_launcher_env.py"], "effects": []},
                        evidence=contract.pop("commands"), stages=[{"id": "verify", "acceptance_ids": ["AC1"]}])
        path = self.base / "launcher-task.json"
        path.write_text(json.dumps(contract), encoding="utf-8")
        launcher("start", str(path))
        proposal = self.base / "launcher-proposal.json"
        proposal.write_text(json.dumps({"summary": "Run the explicit child environment tests", "stage_ids": ["verify"]}))
        proposed = launcher("task", "propose", "launcher", "--file", str(proposal), "--expected-revision", "1")
        launcher("task", "approve", "launcher", "--proposal-hash", proposed["proposal_hash"], "--user-message-ref", "test:explicit-approved-environment-check", "--stage", "verify")
        receipt = launcher("check", "launcher")
        self.assertEqual(receipt["results"][0]["exit_code"], 0)


class InstallationTests(unittest.TestCase):
    def test_preserves_existing_configuration_and_backs_up_managed_changes(self):
        with tempfile.TemporaryDirectory(prefix="ase-install-") as temp:
            codex = Path(temp) / "codex"
            skills = Path(temp) / "skills"
            codex.mkdir()
            config = codex / "config.toml"
            config.write_text('[desktop]\nexisting = "retain me"\n', encoding="utf-8")
            agents = codex / "AGENTS.md"
            original = "# Personal rules\nPreserve user choices.\n"
            agents.write_text(original, encoding="utf-8")
            def install():
                result = subprocess.run([sys.executable, str(ROOT / "scripts" / "install.py"),
                    "--global", "--home", str(codex), "--skills", str(skills)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)
            install()
            self.assertTrue((codex / "autonomous-se" / "runtime" / "scripts" / "vendor_skills.py").is_file())
            self.assertTrue(agents.read_text(encoding="utf-8").startswith(original.rstrip()))
            self.assertEqual(config.read_text(encoding="utf-8"), '[desktop]\nexisting = "retain me"\n')
            self.assertTrue(any(p.read_text(encoding="utf-8") == original
                                for p in (codex / "backups").rglob("AGENTS.md")))
            profile = codex / "autonomous.config.toml"
            profile.write_text("# customized managed profile\n", encoding="utf-8")
            install()
            install()
            self.assertEqual(agents.read_text(encoding="utf-8").count("<!-- autonomous-se managed begin -->"), 1)
            self.assertEqual(profile.read_text(encoding="utf-8"), "# customized managed profile\n")
            self.assertFalse(json.loads((codex / "autonomous-se" / "installation.json").read_text())["remote_push_enabled"])


class ProcessExecutionTests(unittest.TestCase):
    def test_completed_parent_does_not_wait_for_child_stdout_to_close(self):
        from ase.core import execute
        with tempfile.TemporaryDirectory(prefix="ase-child-") as temp:
            script = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c',"
                      "'import time; time.sleep(5)']); print('parent done')")
            started = time.monotonic()
            result = execute([sys.executable, "-c", script], ROOT, Path(temp) / "execution.log", timeout=1)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2)
            self.assertEqual(result["exit_code"], 0)
            self.assertFalse(result["timed_out"])


if __name__ == "__main__":
    unittest.main()
