"""Cross-service delegation closure and native handoff invariants."""
from contextlib import closing
from pathlib import Path
import unittest
from unittest.mock import patch

from ase.continuity import Continuity
from ase.delegations import DelegationRegistry
from ase.gates import GateService
from ase.discovery import discover_capabilities, record_discovery
from ase.delegation_artifacts import seal_result
from ase.workflow import WorkflowController
from ase.workflow import AC_SCHEMA
import json
import test_delegations as registry_fixture
import test_publishing_v2 as publishing_fixture


class DelegationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = registry_fixture.DelegationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.registry = DelegationRegistry(self.store)

    def test_handoff_carries_explicit_project_workspace_and_target_root(self):
        dispatched = self.fixture.start()
        self.assertEqual(dispatched['handoff']['project'], {'project_id': self.store.project_id,
            'workspace_id': self.store.workspace_id, 'root': str(self.store.repo)})

    def test_one_native_handle_cannot_serve_two_unsettled_assignments(self):
        first = self.fixture.start('first')
        native = {'backend': 'codex', 'agent_id': 'same-native-worker'}
        self.registry.attach('delegated', 'first', first['payload']['attempt']['id'], native,
            expected_revision=first['revision'])
        second = self.fixture.start('second')
        with self.assertRaises(ValueError):
            self.registry.attach('delegated', 'second', second['payload']['attempt']['id'], native,
                expected_revision=second['revision'])

    def test_result_cannot_auto_attach_another_unsettled_assignments_native_handle(self):
        first = self.fixture.start('first')
        native = {'backend': 'codex', 'agent_id': 'same-native-worker'}
        self.registry.attach('delegated', 'first', first['payload']['attempt']['id'], native,
            expected_revision=first['revision'])
        second = self.fixture.start('second')
        payload = {'attempt_id': second['payload']['attempt']['id'], 'agent_id': native['agent_id'],
            'status': 'COMPLETE', 'summary': 'Conflicting worker identity', 'artifacts': []}
        with self.assertRaises(ValueError):
            self.registry.result('delegated', 'second', payload['attempt_id'], payload, expected_revision=second['revision'])
        self.assertEqual(self.registry.get('delegated', 'second')['payload']['state'], 'DISPATCHING')

    def test_orphan_recovery_cannot_share_an_unsettled_native_handle(self):
        first = self.fixture.start('first')
        native = {'backend': 'codex', 'agent_id': 'same-native-worker'}
        self.registry.attach('delegated', 'first', first['payload']['attempt']['id'], native,
            expected_revision=first['revision'])
        second = self.fixture.start('second')
        payload = {'attempt_id': second['payload']['attempt']['id'], 'agent_id': native['agent_id'],
            'status': 'COMPLETE', 'summary': 'Conflicting worker identity', 'artifacts': []}
        seal_result(self.store, second['payload'], payload)
        self.registry.recover('delegated', 'second')
        self.assertEqual(self.registry.get('delegated', 'second')['payload']['state'], 'UNKNOWN')

    def test_restore_exposes_unknown_attempt_and_gate_reports_unsettled_work(self):
        self.fixture.start()
        self.registry.recover('delegated', 'research')
        restored = Continuity(self.store).restore('delegated')
        self.assertEqual(restored['delegations']['work'][0]['payload']['state'], 'UNKNOWN')
        gate = GateService(self.store).gate('delegated', stage_id='draft')
        self.assertTrue(any('Delegated work' in reason for reason in gate['blocked_reasons']))

    def test_verifier_packet_includes_registry_history_and_verified_result_manifest(self):
        accepted, output = self.fixture.accepted()
        request = WorkflowController(self.store)._request('delegated', 'ac', AC_SCHEMA, {'stage_id': 'draft'})
        files = {item['path']: item for item in request['source_packet']['files']}
        self.assertIn('_ase_context/delegations.json', files)
        evidence = json.loads(files['_ase_context/delegations.json']['content'])
        self.assertEqual(evidence['work'][0]['payload']['state'], 'ACCEPTED')
        self.assertEqual(evidence['results']['research']['attempt_id'], accepted['payload']['attempt']['id'])
        self.assertTrue(any(event['kind'] == 'delegation_accepted' for event in evidence['events']))
        self.assertIn('advisory', evidence['authority'].lower())

    def test_tampered_registry_result_is_reported_as_missing_packet_coverage(self):
        accepted, output = self.fixture.accepted()
        output.write_text('Changed after coordinator acceptance', encoding='utf-8')
        request = WorkflowController(self.store)._request('delegated', 'ac', AC_SCHEMA, {'stage_id': 'draft'})
        self.assertTrue(any('delegation' in value.lower() for value in request['source_packet']['coverage']['missing']))

    def test_exact_settled_reply_can_repeat_after_native_handle_is_reused(self):
        first = self.fixture.start('first')
        result, payload, _ = self.fixture.result(first, 'first', agent_id='shared-native')
        accepted = self.registry.accept('delegated', 'first', first['payload']['attempt']['id'],
            expected_revision=result['revision'], coordinator='lead', summary='Accepted original output')
        second = self.fixture.start('second')
        self.registry.attach('delegated', 'second', second['payload']['attempt']['id'],
            {'backend': 'codex', 'agent_id': 'shared-native'}, expected_revision=second['revision'])
        self.assertEqual(self.registry.attach('delegated', 'first', first['payload']['attempt']['id'],
            {'backend': 'codex', 'agent_id': 'shared-native'}, expected_revision=first['revision']), accepted)
        self.assertEqual(self.registry.result('delegated', 'first', first['payload']['attempt']['id'], payload,
            expected_revision=result['revision']), accepted)

    def test_child_closure_does_not_wait_for_parent_or_sibling_assignments(self):
        self.registry.register('delegated', registry_fixture.spec('parent-work'))
        tasks = self.fixture.tasks
        parent = tasks.snapshot('delegated')
        child = dict(parent['contract'], id='child', parent_task_id='delegated', parent_stage_id='draft')
        tasks.create(child)
        self.assertEqual(self.registry.closure_blockers('child'), [])
        self.assertTrue(self.registry.closure_blockers('delegated'))

    def test_child_recovery_does_not_change_its_parents_native_attempt(self):
        parent_work = self.fixture.start('parent-work')
        parent = self.fixture.tasks.snapshot('delegated')
        self.fixture.tasks.create(dict(parent['contract'], id='child', parent_task_id='delegated', parent_stage_id='draft'))
        recovered = self.registry.recover('child')
        self.assertEqual(recovered['work'], [])
        self.assertEqual(self.registry.get('delegated', 'parent-work'),
            {key: value for key, value in parent_work.items() if key != 'handoff'})

    def test_accepted_child_facts_remain_dependencies_after_child_completion(self):
        tasks = self.fixture.tasks
        parent = tasks.snapshot('delegated')
        child = tasks.create(dict(parent['contract'], id='child', parent_task_id='delegated', parent_stage_id='draft'))
        record_discovery(self.store, 'child', discover_capabilities(self.store.project,
            scope={'resources': ['inputs', 'guide.md']}, backend_config={}), expected_revision=0)
        value = self.registry.register('child', registry_fixture.spec('child-facts'))
        dispatched = self.registry.dispatch('child', 'child-facts', expected_revision=value['revision'], coordinator='lead', backend='codex')
        attempt = dispatched['payload']['attempt']['id']
        value = self.registry.result('child', 'child-facts', attempt,
            {'attempt_id': attempt, 'agent_id': 'child-native', 'status': 'COMPLETE', 'summary': 'Original child facts', 'artifacts': []},
            expected_revision=dispatched['revision'])
        self.registry.accept('child', 'child-facts', attempt, expected_revision=value['revision'], coordinator='lead', summary='Checked child facts')
        # A protocol boundary double isolates terminal-task dependency lookup;
        # it is not independent semantic approval or live evidence.
        child = tasks.snapshot('child')
        with patch.object(GateService, 'gate', return_value={'status': 'APPROVED', 'contract_hash': child['contract_hash'],
                'plan_hash': tasks.execution_plan('child')['plan_hash']}):
            tasks.transition('child', 'COMPLETED', expected_revision=child['revision'], reason='Protocol-only completed-child boundary')
        value = self.registry.register('delegated', registry_fixture.spec('consumer', depends_on=['child-facts']))
        dispatched = self.registry.dispatch('delegated', 'consumer', expected_revision=value['revision'], coordinator='lead', backend='codex')
        self.assertEqual(dispatched['payload']['state'], 'DISPATCHING')


class DelegationPublicationFenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = publishing_fixture.PublishingV2Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        record_discovery(self.fixture.store, 'publish', discover_capabilities(self.fixture.store.project,
            scope={'resources': ['app.py']}, backend_config={}), expected_revision=0)

    def test_late_registry_work_holds_original_publication_launch(self):
        fixture = self.fixture
        controller = WorkflowController(fixture.store)
        authority = fixture.publisher._publication_authority(controller, 'publish', 'origin')
        cancelled, guard = fixture.publisher._publication_control(controller, 'publish', 'origin', authority)
        self.assertFalse(cancelled())
        registry = DelegationRegistry(fixture.store)
        registry.register('publish', {'id': 'late-review', 'stage_id': 'change', 'goal': 'Check final proposal',
            'expected_result': 'Return an advisory review', 'resources': ['app.py']})
        self.assertTrue(cancelled())
        launched = False
        with self.assertRaises(ValueError):
            with guard():
                launched = True
        self.assertFalse(launched)

    def test_registry_change_then_cancellation_still_invalidates_waiting_publication(self):
        fixture = self.fixture
        controller = WorkflowController(fixture.store)
        authority = fixture.publisher._publication_authority(controller, 'publish', 'origin')
        registry = DelegationRegistry(fixture.store)
        value = registry.register('publish', {'id': 'late-review', 'stage_id': 'change', 'goal': 'Check final proposal',
            'expected_result': 'Return an advisory review', 'resources': ['app.py']})
        registry.cancel('publish', 'late-review', expected_revision=value['revision'], reason='No native launch occurred')
        cancelled, guard = fixture.publisher._publication_control(controller, 'publish', 'origin', authority)
        self.assertTrue(cancelled())
        with self.assertRaises(ValueError):
            with guard():
                self.fail('Changed publication authority reached process launch')
