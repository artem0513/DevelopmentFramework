"""Completed-state fixtures isolate fresh auth facts, never semantic approval."""
import unittest
import os
from pathlib import Path

import test_credential_integration as fixtures
from ase.gates import GateService
from ase.state import transaction
from ase.state import hash_value, read_record


@unittest.skipUnless(os.name == 'nt', 'requires actual Windows DPAPI')
class CompletedAccessRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CredentialIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def completed_fixture(self, task):
        service = self.fixture.tasks
        current = service.snapshot(task['task_id'])
        payload = {k: v for k, v in current.items() if k not in {'revision', 'legacy_v1'}}
        payload['state'] = 'COMPLETED'
        with transaction(self.fixture.store.db):
            service._save(payload, current['revision'])

    def test_completed_check_rotation_requires_fresh_check_without_reopening(self):
        f = self.fixture
        task = f.create(f.contract('completed-check'))
        f.controller.check(task['task_id'])
        self.completed_fixture(task)
        f.vault.update_secrets('lab', f.target, {'token':'new-completed-fixture-token'}, 1)
        self.assertIn('Credential evidence is missing or stale: observe', GateService(f.store).gate(task['task_id'])['reasons'])
        f.controller.check(task['task_id'])
        self.assertNotIn('Credential evidence is missing or stale: observe', GateService(f.store).gate(task['task_id'])['reasons'])
        self.assertEqual(f.tasks.snapshot(task['task_id'])['state'], 'COMPLETED')

    def test_completed_check_refreshes_action_observation_without_replaying_effect(self):
        f = self.fixture
        task = f.create(f.contract('completed-action', action=True))
        f.controller.execute_actions(task['task_id'], task['contract']['stages'][0])
        self.completed_fixture(task)
        f.vault.update_secrets('lab', f.target, {'token':'new-completed-fixture-token'}, 1)
        f.controller.check(task['task_id'])
        reasons = GateService(f.store).gate(task['task_id'])['reasons']
        self.assertFalse(any('Credential evidence' in reason and 'create-counter' in reason for reason in reasons), reasons)
        self.assertEqual((f.repo / 'resource.counter').read_text(), '1')
        self.assertEqual(f.tasks.snapshot(task['task_id'])['state'], 'COMPLETED')

    def test_declared_action_without_observation_cannot_be_approved(self):
        f = self.fixture
        task = f.create(f.contract('missing-action', action=True))
        reasons = GateService(f.store).gate(task['task_id'])['reasons']
        self.assertIn('Action evidence is missing or stale: create-counter', reasons)

    def test_completed_refresh_does_not_accept_a_missing_resource(self):
        f = self.fixture
        task = f.create(f.contract('missing-resource', action=True))
        f.controller.execute_actions(task['task_id'], task['contract']['stages'][0])
        self.completed_fixture(task)
        (f.repo / 'resource.counter').unlink()
        f.controller.check(task['task_id'])
        self.assertIn('Action postcondition is not established: create-counter', GateService(f.store).gate(task['task_id'])['reasons'])
        self.assertFalse((f.repo / 'resource.counter').exists())

    def test_authenticated_action_revalidates_actual_observation_log(self):
        f = self.fixture
        task = f.create(f.contract('tampered-observation', action=True))
        f.controller.execute_actions(task['task_id'], task['contract']['stages'][0])
        action_id = hash_value({'task':task['task_id'], 'stage':'work', 'action':'create-counter'})
        operation = read_record(f.store.db, 'operation', action_id)['payload']
        path = Path(operation['observation']['proof_ref'].rsplit('#sha256=', 1)[0])
        path.write_text('altered proof')
        reasons = GateService(f.store).gate(task['task_id'])['reasons']
        self.assertIn('Action observation proof is missing or invalid: create-counter', reasons)


if __name__ == '__main__':
    unittest.main()
