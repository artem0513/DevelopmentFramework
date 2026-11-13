"""Offline integrity and executable-test sensitivity, never model behavior scores."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ase.knowledge import Knowledge
from ase.store import Store
from ase.workflow import WorkflowController, EDIT_SCHEMA
from scripts import evaluate_production_reasoning as evaluation


class ProductionReasoningScenariosTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ase-production-scenario-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = evaluation.load_cases(evaluation.DEFAULT_CASES)
        self.cases = {case['id']: case for case in self.fixture['cases']}

    def run_artifact_tests(self, target):
        return subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v', 'test_artifact'],
                              cwd=target, capture_output=True, timeout=30)

    def test_capture_never_creates_target_database_or_calls_backend(self):
        args = argparse.Namespace(output=self.root / 'capture', cases=evaluation.DEFAULT_CASES,
                                  repetitions=1, case=[])
        with patch.object(evaluation, 'WorkflowController') as controller, patch.object(evaluation, 'Store') as store:
            selected = evaluation.capture(args)
        controller.assert_not_called()
        store.assert_not_called()
        self.assertEqual(len(selected), 4)
        self.assertFalse(list(args.output.rglob('*.sqlite3')))
        self.assertFalse((args.output / 'launch.json').exists())
        before = {path.name: path.read_bytes() for path in args.output.iterdir()}
        with self.assertRaisesRegex(ValueError, 'fresh output'):
            evaluation.capture(args)
        self.assertEqual(before, {path.name: path.read_bytes() for path in args.output.iterdir()})

    def test_sqlite_tests_detect_actual_callee_defect_and_gratuitous_false_fix(self):
        case = self.cases['actual-callee-pair']
        target = evaluation.initialize(self.root / 'sql', case)
        original = self.run_artifact_tests(target)
        self.assertNotEqual(original.returncode, 0)
        self.assertIn(b'module=\'true_store\'', original.stderr)
        path = target / 'true_store.py'
        path.write_text(path.read_text().replace('        yield connection\n',
                        '        yield connection\n        connection.commit()\n'), encoding='utf-8')
        fixed = self.run_artifact_tests(target)
        self.assertEqual(fixed.returncode, 0, fixed.stderr.decode())
        self.assertIn(b'Ran 3 tests', fixed.stderr)
        path = target / 'false_store.py'
        path.write_text(path.read_text() + '\n# An unnecessary reviewer-requested repair\n', encoding='utf-8')
        gratuitous = self.run_artifact_tests(target)
        self.assertNotEqual(gratuitous.returncode, 0)
        self.assertIn(b'test_false_allegation', gratuitous.stderr)

    def test_rewrite_tests_require_real_refactor_and_detect_semantic_drift(self):
        target = evaluation.initialize(self.root / 'rewrite', self.cases['equivalent-and-control'])
        self.assertNotEqual(self.run_artifact_tests(target).returncode, 0)
        (target / 'equivalent.py').write_text((target / 'equivalent-candidate.txt').read_text(), encoding='utf-8')
        (target / 'decision-note.json').write_text(json.dumps({
            'equivalent': {'action': 'APPLIED', 'owner_message': '', 'reason': 'Equivalent under existing authorization'},
            'control': {'action': 'HELD', 'owner_message': 'Authorize a changed None result?', 'reason': 'None result changes'},
        }), encoding='utf-8')
        equivalent = self.run_artifact_tests(target)
        self.assertEqual(equivalent.returncode, 0, equivalent.stderr.decode())
        self.assertIn(b'Ran 5 tests', equivalent.stderr)
        (target / 'controlled.py').write_text((target / 'controlled-candidate.txt').read_text(), encoding='utf-8')
        self.assertNotEqual(self.run_artifact_tests(target).returncode, 0)

    def test_real_source_and_owner_drift_invalidates_cached_claims_and_checks(self):
        case = self.cases['draft-resume-drift']
        folder = self.root / 'drift'
        target = evaluation.initialize(folder, case)
        with closing(Store(target, folder / 'private-state')) as store:
            task_id = evaluation.authorize(store, evaluation.contract_for(case), dependencies=case['files'])
            controller = WorkflowController(store)
            current = evaluation.cache_sources(store, task_id, case)
            self.assertEqual({claim['validity'] for claim in current['claims']}, {'CURRENT'})
            guide = 'Internal draft\n' + ''.join((target / name).read_text() for name in ('brief.md', 'owner-decision.md'))
            (target / 'guide.md').write_text(guide, encoding='utf-8')
            before = controller.check(task_id, 'draft')
            self.assertEqual(self.run_artifact_tests(target).returncode, 0)
            for relative, content in case['drift']['files'].items():
                (target / relative).write_text(content, encoding='utf-8')
            stale = Knowledge(store).retrieve(scope={}, refresh=True)
            self.assertEqual({claim['validity'] for claim in stale['claims']}, {'STALE'})
            self.assertNotEqual(self.run_artifact_tests(target).returncode, 0)
            after = controller.check(task_id, 'draft')
            self.assertNotEqual(before['fingerprint'], after['fingerprint'])

    def test_normal_request_does_not_receive_withheld_rubric_or_manual_guides(self):
        case = self.cases['draft-resume-drift']
        folder = self.root / 'packet'
        target = evaluation.initialize(folder, case)
        with closing(Store(target, folder / 'private-state')) as store:
            task_id = evaluation.authorize(store, evaluation.contract_for(case))
            request = WorkflowController(store)._request(task_id, 'implementation', EDIT_SCHEMA, {'stage_id': 'draft'})
        encoded = json.dumps(request)
        self.assertNotIn('rubric', encoded.lower())
        self.assertNotIn(self.fixture['rubric']['draft-resume-drift']['criteria'][0], encoded)
        contract = json.loads(next(row['content'] for row in request['source_packet']['files']
                                   if row['path'] == '_ase_context/contract.json'))
        self.assertEqual([item['id'] for item in contract['acceptance']], ['DRAFT', 'PUSH'])
        self.assertEqual(contract['stages'][1]['depends_on'], ['draft'])

    def test_owner_answer_enters_normal_packet_with_exact_revised_authority(self):
        case = self.cases['draft-resume-drift']
        folder = self.root / 'owner-answer'
        target = evaluation.initialize(folder, case)
        with closing(Store(target, folder / 'private-state')) as store:
            task_id = evaluation.authorize(store, evaluation.contract_for(case), dependencies=case['files'])
            controller = WorkflowController(store)
            original = controller.tasks.snapshot(task_id)
            controller.reserve_cycle(task_id, 'draft')
            for relative, content in case['drift']['files'].items():
                (target / relative).write_text(content, encoding='utf-8')
            evaluation.authorize(store, evaluation.contract_for(case, drift=True), revise=True,
                                 dependencies=case['files'], user_message_ref=case['drift']['owner_message_ref'])
            current = controller.tasks.snapshot(task_id)
            request = controller._request(task_id, 'implementation', EDIT_SCHEMA, {'stage_id': 'draft'})
            supplied = json.loads(next(row['content'] for row in request['source_packet']['files']
                                      if row['path'] == '_ase_context/contract.json'))
            self.assertIn(case['drift']['owner_instruction'], supplied['request'])
            self.assertIn(case['drift']['owner_message_ref'], supplied['request'])
            self.assertNotEqual(original['contract_hash'], current['contract_hash'])
            self.assertNotEqual(original['proposal_hash'], current['proposal_hash'])
            self.assertEqual(original['contract']['scope'], current['contract']['scope'])
            self.assertEqual(request['bindings']['contract_sha256'], current['contract_hash'])
            self.assertEqual(request['bindings']['proposal_sha256'], current['proposal_hash'])
            grant = evaluation.read_record(store.db, 'approval', task_id)['payload']
            self.assertEqual(grant['user_message_ref'], case['drift']['owner_message_ref'])
            # Exact reauthorization does not reset the earlier cycle or its cap.
            self.assertEqual(controller.reserve_cycle(task_id, 'draft')['stage_cycles'], 2)
            with self.assertRaisesRegex(ValueError, 'budget'):
                controller.reserve_cycle(task_id, 'draft')


if __name__ == '__main__':
    unittest.main()
