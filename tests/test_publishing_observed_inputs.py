"""Disposable source classification and real Git publication ownership checks.

Candidate credential-shaped strings are assembled only in temporary files.
History transport tests use an explicit protocol gate double, real Git commits,
hooks and local bare remotes; they do not claim independent semantic acceptance.
"""
import hashlib
import copy
from contextlib import closing, contextmanager
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ase.publishing import Publisher
from ase.gates import source_snapshot
from ase.discovery import discover_capabilities, record_discovery
from ase.state import read_record
from ase.core import SECRET
from ase.store import Store
from ase.tasks import TaskService
from ase.workflow import WorkflowController
import ase.publishing as publishing_module
import test_publishing_v2 as publishing_fixtures


class PythonPublicationScanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ase-publication-source-")
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.publisher = Publisher(SimpleNamespace(repo=self.repo))

    @staticmethod
    def assignment(value):
        return "to" + "ken = " + value + "\n"

    def inspect(self, content, *, name="candidate.py", before=None):
        path = self.repo / name
        path.write_text(content, encoding="utf-8")
        baseline = {"files": {} if before is None else {name: hashlib.sha256(before.encode()).hexdigest()}}
        current = {"files": {name: hashlib.sha256(path.read_bytes()).hexdigest()}}
        return self.publisher._secret_check(baseline, current)

    def test_runtime_lookup_and_encoding_expressions_do_not_embed_credentials(self):
        expressions = ["vault_value", "provider.current_credential", "vault['long_lookup_field_name']",
            "provider.fetch_credential()", "os.getenv('LONG_CREDENTIAL_FIELD_NAME')",
            "base64.b64encode((profile['secrets']['username'] + ':' + profile['secrets']['password']).encode()).decode()"]
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertIsNone(self.inspect(self.assignment(expression)))

    def test_condition_colon_is_not_a_literal_binding(self):
        code = "if values and sec" + "ret:\n    values.update(runtime_values)\n"
        self.assertIsNone(self.inspect(code))

    def test_condition_match_does_not_hide_a_following_literal_binding(self):
        code = "if sec" + "ret:\n    " + self.assignment(repr("disposable-opaque-credential-value"))
        self.assertIsNotNone(self.inspect(code))

    def test_lookup_key_metadata_does_not_exempt_literals_inside_unknown_calls(self):
        expression = "vault[transform(" + repr("disposable-opaque-credential-value") + ")]"
        self.assertIsNotNone(self.inspect(self.assignment(expression)))

    def test_unicode_columns_preserve_binding_classification(self):
        self.assertIsNone(self.inspect("métadonnée = 1; " + self.assignment("runtime_credential")))
        self.assertIsNotNone(self.inspect("métadonnée = 1; " + self.assignment(repr("disposable-opaque-credential-value"))))

    def test_current_detector_definitions_remain_publishable_python(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("ase/access.py", "ase/core.py", "ase/discovery.py"):
            with self.subTest(path=name):
                self.assertIsNone(self.inspect((root / name).read_text(encoding="utf-8")))

    def test_literal_values_and_literals_wrapped_in_unknown_calls_are_rejected(self):
        credential = "disposable-opaque-credential-value"
        expressions = [repr(credential), "str(" + repr(credential) + ")",
            "transform(runtime_value, " + repr(credential) + ")", "1234567890123",
            "re.compile(" + repr(credential) + ")", "re.compile(" + repr(credential + "[0-9]+") + ")"]
        for expression in expressions:
            with self.subTest(expression_type=expression.split("(", 1)[0]):
                self.assertIsNotNone(self.inspect(self.assignment(expression)))

    def test_constant_built_payloads_do_not_gain_dynamic_expression_exemption(self):
        expressions = ["''.join(" + repr(["gh" + "p_"] + ["xxxx"] * 5) + ")",
            "''.join(" + repr(["abcd", "efgh", "ijkl", "mnop"]) + ")",
            "bytes(" + repr(list(range(97, 109))) + ").decode()",
            "assemble(" + ", ".join(map(repr, ["abcd", "efgh", "ijkl", "mnop"])) + ")"]
        for index, expression in enumerate(expressions):
            content = self.assignment(expression)
            with self.subTest(example=index):
                self.assertIsNotNone(SECRET.search(content), "Regression must preserve a raw-positive candidate")
                self.assertIsNotNone(self.inspect(content))

    def test_literal_binding_is_not_exempt_when_baseline_line_was_identical(self):
        before = self.assignment(repr("disposable-opaque-credential-value"))
        self.assertIsNotNone(self.inspect(before + "unrelated = 1\n", before=before))
        self.assertIsNotNone(self.inspect(before + before, before=before))

    def test_comments_strings_and_known_prefixes_remain_rejected(self):
        prefix_value = "gh" + "p_" + "x" * 40
        assignments = self.assignment(repr("disposable-opaque-credential-value"))
        examples = ["# " + assignments, "text = " + repr(assignments) + "\n",
            "text = " + repr(prefix_value) + "\n", "# " + prefix_value + "\n",
            self.assignment("lookup(" + repr(prefix_value) + ")")]
        for index, content in enumerate(examples):
            with self.subTest(example=index):
                self.assertIsNotNone(self.inspect(content))

    def test_python_parse_failure_keeps_raw_detection(self):
        self.assertIsNotNone(self.inspect(self.assignment("runtime_credential(")))
        self.assertIsNotNone(self.inspect(self.assignment("'''unterminated")))

    def test_non_python_raw_detection_and_private_file_guards_remain(self):
        self.assertIsNotNone(self.inspect(self.assignment("runtime_credential()"), name="config.txt"))
        for name in (".env", "id_rsa", "id_ed25519", "client.pem", "client.key", "client.p12", "client.pfx"):
            with self.subTest(path=name):
                self.assertIn("Potential credential file", self.inspect("ordinary text\n", name=name))

    def test_hardlinked_source_is_still_rejected(self):
        original = self.repo / "original.py"
        original.write_text("value = 1\n")
        linked = self.repo / "linked.py"
        os.link(original, linked)
        self.assertIn("redirected or shared", self.publisher._secret_check({"files": {}}, {"files": {"linked.py": "changed"}}))

    def test_this_test_module_has_no_embedded_candidate_payload(self):
        self.assertIsNone(self.inspect(Path(__file__).read_text(encoding="utf-8")))


class ObservedInputPublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = publishing_fixtures.PublishingV2Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_ignored_observation_changes_do_not_become_publication_paths(self):
        fixture = self.fixture
        (fixture.repo / ".gitignore").write_text("observations/\n")
        fixture.git("add", ".gitignore")
        fixture.git("commit", "-m", "Ignore read-only fixture observations")
        fixture.refresh_baseline()
        task = fixture.tasks.snapshot("publish")
        baseline = read_record(fixture.store.db, "baseline", "publish")["payload"]
        observation = fixture.repo / "observations/current.txt"
        observation.parent.mkdir()
        observation.write_text("gh" + "p_" + "x" * 40)
        (fixture.repo / "app.py").write_text("value = 2\n")
        discovery = discover_capabilities(fixture.store.project, scope={"resources": ["app.py"],
            "dependencies": [{"path": "observations/current.txt", "reason": "Read-only fixture observation"}]}, backend_config={})
        record_discovery(fixture.store, "publish", discovery, expected_revision=0)
        current = source_snapshot(fixture.store, task["contract"], task_id="publish")
        self.assertIn("observations/current.txt", current["files"])
        paths = fixture.publisher._publication_paths(task, baseline, current)
        self.assertEqual(paths, ["app.py"])
        self.assertIsNone(fixture.publisher._secret_check(baseline, current))
        self.assertEqual(fixture.git("diff", "--cached", "--name-only"), "")

    def test_forced_index_entry_is_checked_even_when_git_ignored(self):
        fixture = self.fixture
        (fixture.repo / ".gitignore").write_text("observations/\n")
        fixture.git("add", ".gitignore")
        fixture.git("commit", "-m", "Ignore fixture observations")
        fixture.refresh_baseline()
        observation = fixture.repo / "observations/private.txt"
        observation.parent.mkdir()
        observation.write_text("Must not publish outside scope\n")
        fixture.git("add", "-f", "observations/private.txt")
        task = fixture.tasks.snapshot("publish")
        baseline = read_record(fixture.store.db, "baseline", "publish")["payload"]
        with self.assertRaisesRegex(ValueError, "outside approved publication scope"):
            fixture.publisher._publication_paths(task, baseline, source_snapshot(fixture.store, task["contract"]))

    def test_rename_checks_both_deleted_source_and_new_destination(self):
        fixture = self.fixture
        fixture.git("mv", "app.py", "outside.py")
        task = fixture.tasks.snapshot("publish")
        baseline = read_record(fixture.store.db, "baseline", "publish")["payload"]
        with self.assertRaisesRegex(ValueError, "outside approved publication scope"):
            fixture.publisher._publication_paths(task, baseline, source_snapshot(fixture.store, task["contract"]))


class PublishedHistorySecretTests(unittest.TestCase):
    def setUp(self):
        self.fixture = publishing_fixtures.PublishingV2Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.publisher.authorize()

    @staticmethod
    def canary():
        return PythonPublicationScanTests.assignment(repr("gh" + "p_" + "x" * 40))

    def commit(self, content, name="app.py"):
        fixture = self.fixture
        path = fixture.repo / name
        if content is None:
            path.unlink()
        else:
            path.write_text(content, encoding="utf-8")
        fixture.git("add", "--", name)
        fixture.git("commit", "-m", "Disposable history inspection fixture")
        return fixture.git("rev-parse", "HEAD")

    def publish(self, **kwargs):
        fixture = self.fixture
        # Real Git publication boundary only; no semantic approval is inferred.
        with patch("ase.publishing.GateService.gate", side_effect=fixture.protocol_gate):
            return fixture.publisher.publish("publish", execute_push=True, **kwargs)

    def assert_rejected_before_push(self, **kwargs):
        result = self.publish(**kwargs)
        self.assertEqual(result["status"], "REJECTED", result)
        self.assertFalse(result["published"])
        self.assertFalse(self.fixture.remote_commit())
        self.assertNotIn("gh" + "p_" + "x" * 40, str(result))
        self.assertIn("history", result["reasons"][0].lower())
        return result

    def test_literal_introduced_then_restored_to_baseline_is_rejected_before_push(self):
        self.commit(self.canary())
        self.commit("value = 1\n")
        self.assert_rejected_before_push()

    def test_literal_introduced_then_file_deleted_is_rejected_before_push(self):
        self.fixture.revise_scope(["app.py", "temporary.py"], ["file_write"])
        self.commit(self.canary(), "temporary.py")
        self.commit(None, "temporary.py")
        self.assert_rejected_before_push()

    def test_merged_side_branch_transient_literal_is_rejected_before_push(self):
        fixture = self.fixture
        fixture.git("checkout", "-b", "feature/disposable-side")
        self.commit(self.canary())
        self.commit("value = 1\n")
        fixture.git("checkout", "feature/publishing")
        self.commit("value = 2\n")
        fixture.git("merge", "--no-ff", "feature/disposable-side", "-m", "Merge clean final side tree")
        self.assert_rejected_before_push()

    def test_literal_only_in_merge_resolution_is_rejected_after_later_removal(self):
        fixture = self.fixture
        fixture.git("checkout", "-b", "feature/disposable-side")
        self.commit("value = 3\n")
        fixture.git("checkout", "feature/publishing")
        self.commit("value = 2\n")
        fixture.git("merge", "--no-ff", "feature/disposable-side", check=False)
        merge = self.commit(self.canary())
        self.assertEqual(len(fixture.git("rev-list", "--parents", "-n", "1", merge).split()), 3)
        self.commit("value = 4\n")
        self.assert_rejected_before_push()

    def test_clean_and_dynamic_python_metadata_history_is_publishable(self):
        self.commit(PythonPublicationScanTests.assignment("vault['long_lookup_field_name']"))
        self.commit(PythonPublicationScanTests.assignment("base64.b64encode((profile['secrets']['username'] + ':' + profile['secrets']['password']).encode()).decode()"))
        self.commit("value = 2\n")
        result = self.publish()
        self.assertTrue(result["published"], result)
        self.assertEqual(self.fixture.remote_commit(), self.fixture.git("rev-parse", "HEAD"))

    def test_legacy_credential_before_baseline_is_not_retroactively_scanned(self):
        self.commit(self.canary(), "legacy.py")
        self.fixture.refresh_baseline()
        self.commit("value = 2\n")
        result = self.publish()
        self.assertTrue(result["published"], result)

    def test_copy_of_baseline_blob_to_new_deleted_path_is_still_introduced(self):
        self.commit(self.canary(), "legacy.py")
        self.fixture.refresh_baseline()
        self.fixture.revise_scope(["app.py", "copy.py"], ["file_write"])
        self.commit(self.canary(), "copy.py")
        self.commit(None, "copy.py")
        self.assert_rejected_before_push()

    def test_transient_private_credential_path_is_rejected(self):
        self.fixture.revise_scope(["app.py", ".env"], ["file_write"])
        self.commit("ordinary disposable text\n", ".env")
        self.commit(None, ".env")
        result = self.assert_rejected_before_push()
        self.assertIn("credential file", result["reasons"][0])

    def test_large_transient_blob_is_rejected_without_silent_scan_omission(self):
        self.commit("x" * 2_000_001)
        self.commit("value = 1\n")
        result = self.assert_rejected_before_push()
        self.assertIn("bound", result["reasons"][0].lower())

    def test_binary_transient_blob_is_rejected_without_silent_scan_omission(self):
        self.commit("disposable binary\0payload")
        self.commit("value = 1\n")
        result = self.assert_rejected_before_push()
        self.assertIn("binary", result["reasons"][0].lower())

    def test_post_commit_hook_transient_history_is_checked_before_push(self):
        fixture = self.fixture
        quoted = self.canary().replace("'", "'\"'\"'")
        fixture.hook("post-commit", """test -f .git/history-hook-ran && exit 0
touch .git/history-hook-ran
cp app.py .git/reviewed-app
printf '%s' '""" + quoted + """' > app.py
git add app.py
git commit -m 'Transient local hook content'
cp .git/reviewed-app app.py
git add app.py
git commit -m 'Restore reviewed hook tree'""")
        (fixture.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.assert_rejected_before_push(commit_message="Reviewed disposable change")


class PublicationAuthorityTests(unittest.TestCase):
    """Actual Git/processes and ordinary concurrent API writes; gate seam only."""
    def setUp(self):
        self.fixture = publishing_fixtures.PublishingV2Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.publisher.authorize()
        self.before = self.fixture.git("rev-parse", "HEAD")
        self.task_id = "publish"
        (self.fixture.repo / "app.py").write_text("value = 2\n", encoding="utf-8")

    def protocol_gate(self, task_id, *_args, **_kwargs):
        task = self.fixture.tasks.snapshot(task_id)
        return {"status": "APPROVED", "reasons": [], "blocked_reasons": [], "rejected_reasons": [],
            "fingerprint": source_snapshot(self.fixture.store, task["contract"], task_id=task_id)["fingerprint"],
            "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"],
            "plan_hash": self.fixture.tasks.execution_plan(task_id)["plan_hash"]}

    def concurrent_change(self, kind, actor="publish"):
        errors = []
        def change():
            try:
                with closing(Store(self.fixture.repo, self.fixture.base / "state")) as store:
                    tasks = TaskService(store)
                    task = tasks.snapshot(actor)
                    if kind == "cancel":
                        tasks.transition(actor, "CANCELLED", expected_revision=task["revision"], reason="Disposable ordinary cancellation")
                    elif kind in {"stop", "stop_resume"}:
                        controller = WorkflowController(store)
                        controller.request_stop(actor, reason="Disposable ordinary stop")
                        if kind == "stop_resume":
                            # No discovery exists, so public resume clears the
                            # stop and blocks before any backend call.
                            self.assertEqual(controller.run(actor, resume=True)["status"], "BLOCKED")
                            self.assertIsNone(read_record(store.db, "stop", actor)["payload"])
                    elif kind == "reapprove":
                        task = tasks.propose(actor, copy.deepcopy(task["proposal"]), expected_revision=task["revision"])
                        tasks.approve(actor, proposal_hash=task["proposal_hash"], user_message_ref="test:replacement-grant",
                            approved_stage_ids=task["proposal"]["stage_ids"])
                    elif kind == "remote_regrant":
                        publisher = Publisher(store)
                        publisher.authorize(disable=True)
                        publisher.authorize()
                    else:
                        raise AssertionError("Unknown test change")
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=change)
        worker.start()
        worker.join(25)
        self.assertFalse(worker.is_alive(), "Ordinary authority write must not wait for a Git process")
        if errors:
            raise errors[0]

    def publish(self):
        with patch("ase.publishing.GateService.gate", side_effect=self.protocol_gate):
            return self.fixture.publisher.publish(self.task_id, execute_push=True, commit_message="Reviewed authority fixture")

    def assert_held(self, result, *, committed=False):
        self.assertFalse(result["published"], result)
        self.assertIn(result["status"], {"BLOCKED", "REJECTED"})
        self.assertFalse(self.fixture.remote_commit())
        self.assertEqual(self.fixture.git("rev-parse", "HEAD") == self.before, not committed)

    def at_command_launch(self, command, change, *, actor="publish"):
        actual = publishing_module.execute
        called = []
        def execute(argv, *args, **kwargs):
            if argv[5] == command and not called:
                called.append(command)
                original_guard = kwargs.get("launch_guard")
                @contextmanager
                def boundary():
                    self.concurrent_change(change, actor)
                    if original_guard is None:
                        yield
                    else:
                        with original_guard():
                            yield
                kwargs["launch_guard"] = boundary
            return actual(argv, *args, **kwargs)
        with patch("ase.publishing.execute", side_effect=execute):
            result = self.publish()
        self.assertEqual(called, [command], result)
        return result

    def at_lock_entry(self, change, *, actor="publish"):
        actual = publishing_module._exclusive
        @contextmanager
        def boundary(path):
            # _exclusive is nonblocking; this models the actual gap between
            # the entry snapshot and acquiring the real execution lock.
            self.concurrent_change(change, actor)
            with actual(path):
                yield
        with patch("ase.publishing._exclusive", side_effect=boundary):
            return self.publish()

    def test_cancel_between_entry_snapshot_and_lock_blocks_commit_and_push(self):
        self.assert_held(self.at_lock_entry("cancel"))

    def test_transient_stop_and_public_resume_cannot_rebind_waiting_publication(self):
        self.assert_held(self.at_lock_entry("stop_resume"))

    def test_stop_at_add_launch_blocks_index_mutation(self):
        self.assert_held(self.at_command_launch("add", "stop"))
        self.assertEqual(self.fixture.git("diff", "--cached", "--name-only"), "")

    def test_cancel_at_commit_launch_preserves_head_and_blocks_push(self):
        self.assert_held(self.at_command_launch("commit", "cancel"))

    def test_stop_at_push_launch_preserves_actual_local_commit_without_push(self):
        self.assert_held(self.at_command_launch("push", "stop"), committed=True)

    def test_same_hash_reapproval_does_not_rebind_original_publication(self):
        self.assert_held(self.at_command_launch("push", "reapprove"), committed=True)

    def test_exact_url_remote_authorization_regrant_does_not_rebind_old_batch(self):
        self.assert_held(self.at_command_launch("push", "remote_regrant"), committed=True)

    def test_ancestor_stop_at_child_push_launch_is_enforced(self):
        parent = self.fixture.tasks.snapshot("publish")
        child = copy.deepcopy(parent["contract"])
        child.update(id="delegate", parent_task_id="publish", parent_stage_id="change")
        self.fixture.tasks.create(child)
        WorkflowController(self.fixture.store)._save("baseline", "delegate", read_record(self.fixture.store.db, "baseline", "publish")["payload"])
        self.task_id = "delegate"
        self.assert_held(self.at_command_launch("push", "stop", actor="publish"), committed=True)

    def test_stop_after_actual_push_keeps_readonly_remote_confirmation_truthful(self):
        actual = publishing_module.execute
        def execute(argv, *args, **kwargs):
            receipt = actual(argv, *args, **kwargs)
            if argv[5] == "push":
                self.assertEqual(receipt["exit_code"], 0)
                self.concurrent_change("stop")
            return receipt
        with patch("ase.publishing.execute", side_effect=execute):
            result = self.publish()
        self.assertTrue(result["published"], result)
        self.assertEqual(result["remote_commit"], self.fixture.remote_commit())
        self.assertEqual(result["status"], "BLOCKED")

    def test_ordinary_stop_cancels_real_git_during_precommit_hook(self):
        fixture = self.fixture
        started, release = fixture.repo / ".git/hook-started", fixture.repo / ".git/hook-release"
        fixture.hook("pre-commit", "touch .git/hook-started\nwhile test ! -f .git/hook-release; do sleep .05; done")
        cancellation_seen = threading.Event()
        errors = []
        def stop_when_hook_runs():
            try:
                for _ in range(500):
                    if started.exists():
                        break
                    if cancellation_seen.wait(.05):
                        break
                self.assertTrue(started.exists(), "Actual pre-commit hook must have started")
                self.concurrent_change("stop")
            except BaseException as error:
                errors.append(error)
            finally:
                # Old code has no cancellation callback: release its real hook
                # after the control fails so RED never hangs for five minutes.
                if not cancellation_seen.wait(3):
                    release.touch()
        worker = threading.Thread(target=stop_when_hook_runs)
        worker.start()
        actual = publishing_module.execute
        def execute(argv, *args, **kwargs):
            cancel = kwargs.get("cancel_requested")
            def observed_cancel():
                value = cancel() if cancel else False
                if value:
                    cancellation_seen.set()
                return value
            kwargs["cancel_requested"] = observed_cancel
            return actual(argv, *args, **kwargs)
        with patch("ase.publishing.execute", side_effect=execute):
            result = self.publish()
        worker.join(30)
        self.assertFalse(worker.is_alive())
        if errors:
            raise errors[0]
        self.assertTrue(started.exists())
        self.assert_held(result)

if __name__ == "__main__":
    unittest.main()
