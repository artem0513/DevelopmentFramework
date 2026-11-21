"""Original-lead policy with real local checks/protocol captures, not model AC."""
from contextlib import closing
import copy
from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ase.adapters.codex import CodexAdapter
from ase.runner import Runner
from ase.state import read_record, transaction, write_record
from ase.protocol import canonical
from ase.store import Store
from ase.tasks import TaskService, validate_contract_v2
from ase.coordinator import CoordinatorEvents
from ase.workflow import WorkflowController
import test_runner as transport

POLICY = {'version': 1, 'required': True, 'additional_passes': 3}
OWNER = 'original-main'


class LeadReviewTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ase.lead_review'),
                             'Missing durable original-lead additional-pass service')
        self.module = importlib.import_module('ase.lead_review')
        temp = tempfile.TemporaryDirectory(prefix='ase-lead-review-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'work'; self.root.mkdir()
        (self.root / 'guide.txt').write_text('The approved guide', encoding='utf-8')
        self.home = self.base / 'state'
        self.store = Store(self.root, self.home)
        self.addCleanup(lambda: self.store.close())
        self.tasks = TaskService(self.store)
        self.contract = {'id': 'lead', 'schema_version': 2, 'profile': 'document',
            'request': 'Write the approved guide without dropping the business rule',
            'scope': {'resources': ['guide.txt'], 'effects': ['file_write']}, 'risk': 'MEDIUM',
            'lead_review': POLICY,
            'acceptance': [{'id': 'RULE', 'expected': 'The guide retains the approved business rule', 'evidence_ids': ['ready']}],
            'evidence': [{'id': 'ready', 'kind': 'probe',
                'argv': [sys.executable, '-B', '-c', "import json,pathlib; assert pathlib.Path('guide.txt').read_text(); print(json.dumps({'ready':True}))"],
                'predicate': {'path': 'ready', 'op': 'eq', 'value': True}}],
            'stages': [{'id': 'finish', 'acceptance_ids': ['RULE']}], 'unknowns': []}
        self.tasks.create(self.contract)
        self.approve('Initial approved business wording')
        CoordinatorEvents(self.store).bind('lead', {'backend': 'codex', 'host_id': 'local', 'thread_id': OWNER}, user_message_ref='test:original-user', expected_revision=0)
        self.controller = WorkflowController(self.store)
        self.controller.check('lead')
        self.service = self.module.LeadReview(self.store)
        self.turn = 0

    def approve(self, summary):
        task = self.tasks.snapshot('lead')
        task = self.tasks.propose('lead', {'summary': summary, 'business': summary, 'stage_ids': ['finish']}, expected_revision=task['revision'])
        return self.tasks.approve('lead', proposal_hash=task['proposal_hash'], user_message_ref='test:user-approved:' + summary, approved_stage_ids=['finish'])

    def implementation(self, alter=None, *, refresh=False):
        if read_record(self.store.db, 'implementation', 'lead')['payload'] and not refresh:
            return
        fixture = self.base / 'fixture.py'; fixture.write_text(transport.FIXTURE)
        trusted = self.base / 'trusted'; trusted.mkdir(exist_ok=True)
        runner = Runner({'executable': sys.executable,
            'executable_args': [str(fixture), 'success', 'codex'],
            'trusted_cwd': str(trusted), 'protocol_only': True}, self.store.root / 'tasks/lead/sessions')
        request = self.controller._request('lead', 'implementation', transport.SCHEMA, {'stage': {'id': 'finish'}})
        if alter:
            alter(request)
        receipt = runner.run(CodexAdapter(), request, timeout=10)['receipt']
        self.controller._save('implementation', 'lead', receipt)

    def prepare(self):
        self.implementation()
        old = self.service.snapshot('lead')
        return self.service.prepare('lead', thread_id=OWNER, expected_revision=old['revision'])

    def payload(self, ticket, *, findings=None, resolutions=None, status='PASS', turn=None):
        self.turn += 1
        proof = {'check_id': 'ready', 'source': 'guide.txt', 'assertion': 'Actual probe reads the guide and asserts a nonempty result', 'kind': 'check'}
        rows = ticket['handoff']['requirements']['requirements']
        assessment = {'requirements': [{'id': row['id'], 'expected_sha256': row['expected_sha256'], 'status': status,
            'evidence': [proof], 'reason': 'Checked this exact approved requirement against supplied source and executed probe'} for row in rows],
            'risks': [{'area': area, 'status': 'CHECKED' if area in {'business', 'tests'} else 'NOT_APPLICABLE',
                'reason': 'Inspected the document requirement; no runtime/security/performance behavior changes are in this bounded task',
                'evidence': [proof] if area in {'business', 'tests'} else []} for area in ('business', 'failure', 'performance', 'security', 'tests')],
            'findings': findings or [], 'resolutions': resolutions or [], 'remaining_gaps': [],
            'summary': 'Original and approved requirements reconsidered as a complete task; protocol fixture is not independent semantic approval'}
        return {'origin': {'backend': 'codex', 'host_id': 'local', 'thread_id': OWNER,
            'turn_ref': turn or 'native-turn-' + str(self.turn)}, 'assessment': assessment}

    def record(self, ticket, payload=None):
        return self.service.record('lead', ticket['handoff']['attempt_id'], payload or self.payload(ticket),
            thread_id=OWNER, expected_revision=ticket['revision'])

    def finish_three(self):
        for _ in range(3):
            self.record(self.prepare())

    def test_missing_and_fewer_than_three_passes_hold_completion(self):
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')
        for count in (1, 2):
            self.record(self.prepare())
            result = self.service.gate('lead')
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['completed_passes'], count)
        self.record(self.prepare())
        self.assertEqual(self.service.gate('lead')['status'], 'READY')

    def test_prepare_requires_initial_checks_and_implementation_capture(self):
        with self.assertRaisesRegex(ValueError, 'implementation'):
            self.service.prepare('lead', thread_id=OWNER, expected_revision=0)

    def test_pre_edit_receipt_can_start_only_from_exact_current_completed_stage_proof(self):
        from unittest.mock import patch
        from ase.state import hash_value
        self.implementation()
        receipt = read_record(self.store.db, 'implementation', 'lead')['payload']
        (self.root / 'guide.txt').write_text('Authorized implemented guide with the business rule')
        checks = self.controller.check('lead')
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.prepare()
        task = self.tasks.snapshot('lead')
        history = {'stages': [{'stage_id': 'finish', 'semantic_approval': True, 'policy_current': True,
            'current_identity': True, 'contract_hash': task['contract_hash'], 'proposal_hash': task['proposal_hash'],
            'checks': checks, 'roles': {'implementation': {'receipt_sha256': hash_value(receipt)}}}]}
        # This seam models independently authenticated stage proof only for the
        # protocol unit. An actual Codex run separately exercises the real path.
        with patch.object(self.controller.__class__, '_stage_history', return_value=(history, [])):
            ticket = self.prepare()
        self.assertEqual(ticket['handoff']['number'], 1)
        self.assertEqual(ticket['handoff']['base']['fingerprint'], checks['fingerprint'])

    def test_historical_policy_implementation_needs_current_policy_stage_proof(self):
        from unittest.mock import patch
        from ase.state import hash_value
        self.implementation()
        receipt = read_record(self.store.db, 'implementation', 'lead')['payload']
        with patch('ase.runtime_policy._identity', return_value='e' * 64):
            checks = self.controller.check('lead')
            with self.assertRaisesRegex(ValueError, 'stale'):
                self.prepare()
            task = self.tasks.snapshot('lead')
            entry = {'stage_id': 'finish', 'semantic_approval': True, 'policy_current': True, 'current_identity': True,
                'contract_hash': task['contract_hash'], 'proposal_hash': task['proposal_hash'], 'checks': checks,
                'roles': {'implementation': {'receipt_sha256': hash_value(receipt)}}}
            with patch.object(self.controller.__class__, '_stage_history', return_value=({'stages': [dict(entry, policy_current=False)]}, [])):
                with self.assertRaisesRegex(ValueError, 'stale'):
                    self.prepare()
            with patch.object(self.controller.__class__, '_stage_history', return_value=({'stages': [entry]}, [])):
                ticket = self.prepare()
        self.assertEqual(ticket['handoff']['base']['fingerprint'], checks['fingerprint'])

    def test_rechecked_receipt_cannot_borrow_another_stage_implementation(self):
        from unittest.mock import patch
        self.implementation(); checks = self.controller.check('lead'); task = self.tasks.snapshot('lead')
        entry = {'stage_id': 'finish', 'semantic_approval': True, 'policy_current': True, 'current_identity': True,
            'contract_hash': task['contract_hash'], 'proposal_hash': task['proposal_hash'], 'checks': checks,
            'roles': {'implementation': {'receipt_sha256': '0' * 64}}}
        with patch.object(self.controller.__class__, '_stage_history', return_value=({'stages': [entry]}, [])):
            with self.assertRaisesRegex(ValueError, 'stale'):
                self.prepare()

    def test_worker_cannot_replace_original_lead(self):
        self.implementation()
        with self.assertRaisesRegex(ValueError, 'original|owner|lead'):
            self.service.prepare('lead', thread_id='worker', expected_revision=0)
        ticket = self.prepare(); data = self.payload(ticket)
        data['origin']['thread_id'] = 'worker'
        with self.assertRaises(ValueError):
            self.record(ticket, data)

    def test_unsettled_ticket_never_launches_another_pass(self):
        ticket = self.prepare()
        with self.assertRaisesRegex(ValueError, 'reconcile|in.flight|unsettled|existing'):
            self.service.prepare('lead', thread_id=OWNER, expected_revision=ticket['revision'])
        self.assertEqual(self.service.snapshot('lead')['payload']['inflight']['id'], ticket['handoff']['attempt_id'])

    def test_duplicate_capture_is_idempotent_and_reused_turn_is_rejected(self):
        first = self.prepare(); data = self.payload(first, turn='same-native-turn')
        self.record(first, data)
        self.record(first, data)
        self.assertEqual(self.service.gate('lead')['completed_passes'], 1)
        second = self.prepare()
        with self.assertRaisesRegex(ValueError, 'turn|reus'):
            self.record(second, self.payload(second, turn='same-native-turn'))

    def test_partial_or_wrong_requirement_rows_cannot_count(self):
        ticket = self.prepare(); data = self.payload(ticket)
        data['assessment']['requirements'].pop()
        with self.assertRaisesRegex(ValueError, 'requirement'):
            self.record(ticket, data)
        data = self.payload(ticket)
        data['assessment']['requirements'][0]['expected_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'requirement'):
            self.record(ticket, data)

    def test_unknown_check_and_unsupplied_source_are_rejected(self):
        ticket = self.prepare()
        for field, value in [('check_id', 'imaginary'), ('source', 'missing.txt')]:
            data = self.payload(ticket)
            data['assessment']['requirements'][0]['evidence'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.record(ticket, data)

    def test_performance_claim_requires_measurement_evidence(self):
        ticket = self.prepare(); data = self.payload(ticket)
        row = next(r for r in data['assessment']['risks'] if r['area'] == 'performance')
        row.update(status='CHECKED', evidence=[data['assessment']['requirements'][0]['evidence'][0]])
        with self.assertRaisesRegex(ValueError, 'measure|performance'):
            self.record(ticket, data)

    def test_changed_source_before_record_is_sealed_as_stale_not_counted(self):
        ticket = self.prepare(); data = self.payload(ticket)
        (self.root / 'guide.txt').write_text('Changed while native critique was running')
        self.record(ticket, data)
        result = self.service.gate('lead')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['completed_passes'], 0)
        self.assertIsNone(self.service.snapshot('lead')['payload']['inflight'])

    def test_completed_depth_survives_fix_but_final_must_be_current(self):
        self.finish_three()
        (self.root / 'guide.txt').write_text('Fixed guide preserving the requirement')
        self.controller.check('lead')
        result = self.service.gate('lead')
        self.assertEqual(result['completed_passes'], 3)
        self.assertEqual(result['status'], 'BLOCKED')
        self.record(self.prepare())
        self.assertEqual(self.service.gate('lead')['status'], 'READY')
        self.assertEqual(self.service.gate('lead')['completed_passes'], 4)

    def test_open_findings_cannot_disappear_from_later_passes(self):
        ticket = self.prepare(); proof = self.payload(ticket)['assessment']['requirements'][0]['evidence'][0]
        finding = {'id': 'missing-rule', 'requirement_id': 'AC:RULE', 'priority': 'P2',
            'description': 'A concrete rule counterexample needs correction', 'evidence': [proof]}
        self.record(ticket, self.payload(ticket, findings=[finding]))
        self.record(self.prepare()); self.record(self.prepare())
        self.assertNotEqual(self.service.gate('lead')['status'], 'READY')
        (self.root / 'guide.txt').write_text('The corrected guide now retains the missing business rule')
        self.controller.check('lead')
        last = self.prepare()
        resolution = {'id': 'missing-rule', 'origin_anchor_sha256': '', 'outcome': 'fixed', 'reason': 'Current guide/probe now exercise the corrected rule', 'evidence': [proof]}
        self.record(last, self.payload(last, resolutions=[resolution]))
        self.assertEqual(self.service.gate('lead')['status'], 'READY')

    def test_unknown_resolution_and_bare_pass_are_not_evidence(self):
        ticket = self.prepare(); data = self.payload(ticket)
        data['assessment']['requirements'][0]['evidence'] = []
        with self.assertRaises(ValueError):
            self.record(ticket, data)
        data = self.payload(ticket)
        proof = data['assessment']['requirements'][0]['evidence'][0]
        data['assessment']['resolutions'] = [{'id': 'never-found', 'origin_anchor_sha256': '', 'outcome': 'fixed', 'reason': 'Claim only', 'evidence': [proof]}]
        with self.assertRaisesRegex(ValueError, 'earlier|Unknown'):
            self.record(ticket, data)

    def test_new_finding_cannot_be_closed_inside_the_same_capture(self):
        ticket = self.prepare(); data = self.payload(ticket)
        proof = data['assessment']['requirements'][0]['evidence'][0]
        finding = {'id': 'same-pass', 'requirement_id': 'AC:RULE', 'priority': 'P2',
            'description': 'A concrete candidate issue must be retained for subsequent assessment', 'evidence': [proof]}
        data['assessment']['findings'] = [finding]
        data['assessment']['resolutions'] = [{'id': 'same-pass', 'origin_anchor_sha256': '', 'outcome': 'not_a_bug',
            'reason': 'Trying to erase the just-opened finding in this same capture', 'evidence': [proof]}]
        with self.assertRaisesRegex(ValueError, 'earlier|previous|Unknown'):
            self.record(ticket, data)
        data['assessment']['resolutions'] = []; self.record(ticket, data)
        later = self.prepare(); data = self.payload(later)
        data['assessment']['resolutions'] = [{'id': 'same-pass', 'origin_anchor_sha256': '', 'outcome': 'not_a_bug',
            'reason': 'Subsequent source/evidence inspection established that the approved rule is already present', 'evidence': [proof]}]
        self.record(later, data)
        self.assertEqual(self.service.assessment('lead')['finding_ledger']['same-pass']['state'], 'RESOLVED')

    def test_original_and_approved_proposal_clarifications_survive_reproposal(self):
        original = self.service.requirements('lead')
        self.approve('Approved clarification: preserve the exact business ordering')
        clarified = self.service.requirements('lead')
        self.assertNotEqual(original['anchor_sha256'], clarified['anchor_sha256'])
        serialized = json.dumps(clarified)
        self.assertIn(self.contract['request'], serialized)
        self.assertIn('Approved clarification: preserve the exact business ordering', serialized)
        task = self.tasks.snapshot('lead')
        self.tasks.propose('lead', {'summary': 'UNAPPROVED replacement contradicting business ordering', 'stage_ids': ['finish']}, expected_revision=task['revision'])
        self.assertNotIn('UNAPPROVED replacement', json.dumps(self.service.requirements('lead')))
        with self.assertRaises(ValueError):
            self.prepare()

    def test_new_approved_requirements_start_a_fresh_series(self):
        self.finish_three()
        task = self.tasks.snapshot('lead'); changed = copy.deepcopy(task['contract'])
        changed['acceptance'][0]['expected'] += '; preserve ordering'
        self.tasks.revise('lead', changed, expected_revision=task['revision'])
        self.approve('Owner approved the clarified ordering')
        self.controller.check('lead')
        result = self.service.gate('lead')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['completed_passes'], 0)

    def open_rule(self, name='retained-rule-gap'):
        ticket = self.prepare(); data = self.payload(ticket)
        proof = data['assessment']['requirements'][0]['evidence'][0]
        data['assessment']['findings'] = [{'id': name, 'requirement_id': 'AC:RULE', 'priority': 'P2',
            'description': 'An original business rule still has a concrete counterexample', 'evidence': [proof]}]
        self.record(ticket, data)
        return ticket['handoff']['requirements']['anchor_sha256'], proof

    def reapprove(self, summary='Clarify the title; retain the original business rule'):
        self.approve(summary); self.controller.check('lead'); self.implementation(refresh=True)

    def test_open_material_finding_survives_title_only_reapproval_and_new_depth(self):
        origin, _ = self.open_rule(); self.reapprove(); self.finish_three()
        gate = self.service.gate('lead')
        self.assertEqual(gate['completed_passes'], 3)
        self.assertEqual(gate['status'], 'BLOCKED')
        rows = self.service.assessment('lead')['finding_ledger'].values()
        self.assertTrue(any(row['id'] == 'retained-rule-gap' and row['state'] == 'OPEN'
            and row['origin_anchor_sha256'] == origin for row in rows))

    def test_empty_intermediate_approval_does_not_drop_an_inherited_finding(self):
        self.open_rule(); self.reapprove('Empty intermediate approved title')
        self.service.classify('lead', {'kind': 'nontrivial', 'reason': 'Business rule remains relevant',
            'evidence_ids': ['ready'], 'source_refs': ['guide.txt']}, thread_id=OWNER,
            expected_revision=self.service.snapshot('lead')['revision'])
        self.reapprove('Third approved title; preserve all rules'); self.finish_three()
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')

    def test_inherited_material_finding_blocks_new_trivial_exemption(self):
        self.open_rule()
        task = self.tasks.snapshot('lead'); contract = copy.deepcopy(task['contract']); contract['risk'] = 'LOW'
        self.tasks.revise('lead', contract, expected_revision=task['revision']); self.reapprove()
        self.service.classify('lead', {'kind': 'trivial', 'reason': 'Current title edit is mechanical',
            'evidence_ids': ['ready'], 'source_refs': ['guide.txt']}, thread_id=OWNER,
            expected_revision=self.service.snapshot('lead')['revision'])
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')

    def test_explicit_origin_resolution_carries_original_proof_and_keeps_current_depth(self):
        origin, proof = self.open_rule()
        (self.root / 'guide.txt').write_text('The corrected guide now contains the missing rule')
        self.reapprove(); ticket = self.prepare()
        self.assertTrue(any(row['origin_anchor_sha256'] == origin for row in ticket['handoff']['finding_ledger'].values()))
        resolution = {'id': 'retained-rule-gap', 'origin_anchor_sha256': origin, 'outcome': 'fixed',
            'reason': 'Current changed source and executed probe establish the original rule', 'evidence': [proof]}
        self.record(ticket, self.payload(ticket, resolutions=[resolution]))
        self.record(self.prepare()); self.record(self.prepare())
        self.assertEqual(self.service.gate('lead')['status'], 'READY')
        assessment = self.service.assessment('lead')
        self.assertEqual(len(assessment['passes']), 3)
        self.assertEqual(len(assessment['inherited_passes']), 1)
        self.assertFalse(assessment['input_coverage_missing'])
        self.assertTrue(assessment['inherited_passes'][0]['inputs']['sources'])

    def test_deleted_credited_history_blocks_instead_of_forgiving_findings(self):
        self.open_rule(); self.reapprove(); self.finish_three()
        self.store.db.execute("DELETE FROM runtime_records WHERE kind='lead_review_history'")
        self.store.db.commit()
        result = self.service.gate('lead')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(any('credited' in reason.lower() for reason in result['blocked_reasons']))

    def legacy_prepare(self):
        """Build the exact supported pre-v2 ticket shape, including its input basis."""
        base = self.service._base('lead'); base.pop('prior_lineage_sha256')
        with patch.object(self.service, '_base', return_value=base):
            ticket = self.prepare()
        row = self.service.snapshot('lead'); value = copy.deepcopy(row['payload'])
        value['inflight'].pop('assessment_version')
        with transaction(self.store.db):
            saved = write_record(self.store.db, 'lead_review', 'lead', value, expected_revision=row['revision'])
        ticket['revision'] = saved['revision']
        return ticket

    def test_closed_finding_id_can_be_reused_in_a_new_approved_series(self):
        _, proof = self.open_rule('reused-id')
        ticket = self.prepare()
        self.record(ticket, self.payload(ticket, resolutions=[{'id': 'reused-id', 'origin_anchor_sha256': '',
            'outcome': 'not_a_bug', 'reason': 'The original source already establishes the rule', 'evidence': [proof]}]))
        self.reapprove(); current, _ = self.open_rule('reused-id')
        rows = list(self.service.assessment('lead')['finding_ledger'].values())
        self.assertEqual(sum(row['id'] == 'reused-id' for row in rows), 2)
        self.assertTrue(any(row['origin_anchor_sha256'] == current and row['state'] == 'OPEN' for row in rows))

    def test_legacy_local_resolution_cannot_close_same_id_in_an_ancestor(self):
        origin, proof = self.open_rule('same-id'); self.reapprove(); current, _ = self.open_rule('same-id')
        ticket = self.legacy_prepare()
        self.record(ticket, self.payload(ticket, resolutions=[{'id': 'same-id', 'outcome': 'not_a_bug',
            'reason': 'Only the current-series counterexample was disproved', 'evidence': [proof]}]))
        rows = list(self.service.assessment('lead')['finding_ledger'].values())
        self.assertTrue(any(row['origin_anchor_sha256'] == origin and row['state'] == 'OPEN' for row in rows))
        self.assertTrue(any(row['origin_anchor_sha256'] == current and row['state'] == 'RESOLVED' for row in rows))
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')

    def test_cross_anchor_fixed_requires_actual_changed_evidence(self):
        origin, proof = self.open_rule(); self.reapprove(); ticket = self.prepare()
        with self.assertRaisesRegex(ValueError, 'changed source/check/action'):
            self.record(ticket, self.payload(ticket, resolutions=[{'id': 'retained-rule-gap',
                'origin_anchor_sha256': origin, 'outcome': 'fixed', 'reason': 'New title only, unchanged implementation', 'evidence': [proof]}]))

    def test_unknown_origin_cannot_resolve_a_real_local_finding(self):
        _, proof = self.open_rule(); ticket = self.prepare()
        with self.assertRaisesRegex(ValueError, 'earlier open finding'):
            self.record(ticket, self.payload(ticket, resolutions=[{'id': 'retained-rule-gap',
                'origin_anchor_sha256': 'f' * 64, 'outcome': 'not_a_bug', 'reason': 'Unknown origin is not authority', 'evidence': [proof]}]))

    def test_removed_criterion_does_not_automatically_supersede_a_finding(self):
        self.open_rule()
        task = self.tasks.snapshot('lead'); contract = copy.deepcopy(task['contract'])
        contract['acceptance'][0].update(id='NEW', expected='A different current title rule')
        contract['stages'][0]['acceptance_ids'] = ['NEW']
        self.tasks.revise('lead', contract, expected_revision=task['revision']); self.reapprove()
        self.finish_three()
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')

    def test_legacy_inflight_response_recovers_once_with_original_schema_and_bytes(self):
        self.implementation(); ticket = self.legacy_prepare(); data = self.payload(ticket)
        with patch.object(self.service, '_commit_capture', side_effect=RuntimeError('old process ended after seal')):
            with self.assertRaises(RuntimeError): self.record(ticket, data)
        path = self.service._path('lead', ticket['handoff']['attempt_id']); sealed = path.read_bytes()
        self.store.close(); self.store = Store(self.root, self.home); self.service = self.module.LeadReview(self.store)
        self.service.recover('lead'); row = self.service.snapshot('lead')
        self.assertEqual(len(row['payload']['passes']), 1)
        self.assertEqual(self.service.recover('lead'), row)
        self.assertEqual(path.read_bytes(), sealed)

    def test_prior_lineage_is_bound_without_self_invalidating_current_passes(self):
        self.open_rule(); self.reapprove(); first = self.prepare(); self.record(first)
        before = self.service._base('lead')['prior_lineage_sha256']; second = self.prepare(); self.record(second)
        self.assertEqual(self.service._base('lead')['prior_lineage_sha256'], before)
        bound = self.service.binding('lead')
        rows = list(self.store.db.execute("SELECT key,payload,revision FROM runtime_records WHERE kind='lead_review_history'"))
        value = json.loads(rows[0]['payload']); value['last_stale'] = {'retained_note': 'Recovered prior context changed'}
        write_record(self.store.db, 'lead_review_history', rows[0]['key'], value, expected_revision=rows[0]['revision'])
        self.assertNotEqual(self.service.binding('lead'), bound)
        self.assertNotEqual(self.service._base('lead')['prior_lineage_sha256'], before)

    def approval_drift_resolution(self, *, legacy=False):
        origin, proof = self.open_rule()
        ticket = self.legacy_prepare() if legacy else self.prepare()
        resolution = {'id': 'retained-rule-gap', 'outcome': 'not_a_bug',
            'reason': 'Original prepared source was independently re-examined', 'evidence': [proof]}
        if not legacy: resolution['origin_anchor_sha256'] = ''
        data = self.payload(ticket, resolutions=[resolution])
        prepared = copy.deepcopy(self.service.snapshot('lead')['payload']['inflight'])
        self.approve('A later approved title arrived while the original critique was in flight')
        self.controller.check('lead')
        self.record(ticket, data)
        row = self.service.snapshot('lead')
        self.assertIsNone(row['payload']['inflight'])
        self.assertEqual(len(row['payload']['passes']), 1)
        path = self.service._path('lead', ticket['handoff']['attempt_id'])
        raw = path.read_bytes()
        self.assertEqual(json.loads(raw), {'ticket': prepared, 'result': data})
        self.assertIsNotNone(row['payload']['last_stale'])
        self.assertEqual(self.service.recover('lead'), row)
        self.assertEqual(path.read_bytes(), raw)
        self.implementation(refresh=True); self.finish_three()
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')
        self.assertTrue(any(value['origin_anchor_sha256'] == origin and value['state'] == 'OPEN'
            for value in self.service.assessment('lead')['finding_ledger'].values()))

    def test_approval_drift_seals_local_resolution_stale_without_losing_response(self):
        self.approval_drift_resolution()

    def test_approval_drift_preserves_legacy_local_response_and_recovery(self):
        self.approval_drift_resolution(legacy=True)

    def test_intermediate_stage_does_not_wait_for_final_root_passes(self):
        result = self.service.gate('lead', stage_id='finish')
        self.assertEqual(result['status'], 'NOT_REQUIRED')

    def test_legacy_saved_contract_is_not_silently_configured(self):
        legacy = copy.deepcopy(self.contract); legacy.pop('lead_review'); legacy['id'] = 'old'
        self.tasks.create(legacy)
        self.assertEqual(self.service.gate('old')['status'], 'NOT_CONFIGURED')
        self.assertNotIn('lead_review', validate_contract_v2(legacy))

    def test_policy_rejects_boolean_version_shortcuts_and_fewer_passes(self):
        for value in ({'version': True, 'required': True, 'additional_passes': 3},
                {'version': 1, 'required': False, 'additional_passes': 3},
                {'version': 1, 'required': True, 'additional_passes': 2}):
            c = copy.deepcopy(self.contract); c['lead_review'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_contract_v2(c)

    def test_low_risk_mechanical_exemption_is_current_and_does_not_erase_work(self):
        task = self.tasks.snapshot('lead'); c = copy.deepcopy(task['contract']); c['risk'] = 'LOW'
        self.tasks.revise('lead', c, expected_revision=task['revision']); self.approve('Only a mechanical spelling correction')
        self.controller.check('lead')
        self.service.classify('lead', {'kind': 'trivial', 'reason': 'Only corrected spelling in an existing nonbehavioral guide',
            'evidence_ids': ['ready'], 'source_refs': ['guide.txt']}, thread_id=OWNER, expected_revision=0)
        self.assertEqual(self.service.gate('lead')['status'], 'READY')
        (self.root / 'guide.txt').write_text('A later changed guide needs fresh classification or full critiques')
        self.controller.check('lead')
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')

    def test_mechanical_exemption_cannot_bypass_a_started_material_critique(self):
        task = self.tasks.snapshot('lead'); contract = copy.deepcopy(task['contract']); contract['risk'] = 'LOW'
        self.tasks.revise('lead', contract, expected_revision=task['revision']); self.approve('Mechanical initial scope')
        self.controller.check('lead')
        self.service.classify('lead', {'kind': 'trivial', 'reason': 'Initially mechanical one-guide change',
            'evidence_ids': ['ready'], 'source_refs': ['guide.txt']}, thread_id=OWNER, expected_revision=0)
        first = self.prepare(); data = self.payload(first, status='FAIL')
        proof = data['assessment']['requirements'][0]['evidence'][0]
        data['assessment']['findings'] = [{'id': 'material-rule', 'requirement_id': 'AC:RULE', 'priority': 'P2',
            'description': 'A concrete business rule is missing despite initial mechanical classification', 'evidence': [proof]}]
        self.record(first, data)
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')
        assessment = self.service.assessment('lead')
        self.assertEqual(assessment['classification']['kind'], 'nontrivial')
        self.assertEqual(assessment['prior_classification']['kind'], 'trivial')
        (self.root / 'guide.txt').write_text('The corrected guide includes the missing business rule')
        self.controller.check('lead')
        second = self.prepare(); data = self.payload(second)
        data['assessment']['resolutions'] = [{'id': 'material-rule', 'origin_anchor_sha256': '', 'outcome': 'fixed',
            'reason': 'Current corrected guide and re-executed probe inspected', 'evidence': [proof]}]
        self.record(second, data)
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')
        self.record(self.prepare())
        final = self.service.gate('lead')
        self.assertEqual(final['status'], 'READY')
        self.assertEqual(final['classification']['kind'], 'nontrivial')

    def test_medium_risk_and_bare_exemptions_are_rejected(self):
        value = {'kind': 'trivial', 'reason': 'Claimed mechanical edit', 'evidence_ids': ['ready'], 'source_refs': ['guide.txt']}
        with self.assertRaisesRegex(ValueError, 'LOW|Trivial'):
            self.service.classify('lead', value, thread_id=OWNER, expected_revision=0)
        value.update(kind='nontrivial', source_refs=[])
        with self.assertRaises(ValueError):
            self.service.classify('lead', value, thread_id=OWNER, expected_revision=0)

    def test_child_completion_does_not_consume_or_require_root_passes(self):
        child = copy.deepcopy(self.contract)
        child.update(id='child', parent_task_id='lead', parent_stage_id='finish')
        self.tasks.create(child)
        self.assertEqual(self.service.gate('child')['status'], 'NOT_REQUIRED')
        with self.assertRaises(ValueError):
            self.service.prepare('child', thread_id=OWNER, expected_revision=0)
        self.assertEqual(self.service.gate('lead')['completed_passes'], 0)

    def test_a_native_turn_cannot_be_reused_after_approved_requirement_change(self):
        first = self.prepare(); self.record(first, self.payload(first, turn='original-native-turn'))
        self.approve('Approved clarification after the first critical pass')
        self.controller.check('lead')
        # New series requires a genuinely current implementation reconciliation.
        self.controller._save('implementation', 'lead', None)
        ticket = self.prepare()
        with self.assertRaisesRegex(ValueError, 'turn|reus'):
            self.record(ticket, self.payload(ticket, turn='original-native-turn'))

    def test_stale_capture_acknowledgement_is_idempotent(self):
        ticket = self.prepare(); data = self.payload(ticket)
        (self.root / 'guide.txt').write_text('Concurrent source change')
        first = self.record(ticket, data)
        second = self.record(ticket, data)
        self.assertEqual(first, second)
        self.assertEqual(self.service.gate('lead')['completed_passes'], 0)

    def test_initial_capture_from_another_project_cannot_start_the_series(self):
        self.implementation(lambda request: request['project'].update(project_id='foreign-project'))
        with self.assertRaisesRegex(ValueError, 'project|implementation|root'):
            self.prepare()

    def test_escaped_assessment_file_is_not_completion_evidence(self):
        from ase.state import write_record
        self.finish_three()
        old = self.service.snapshot('lead'); value = copy.deepcopy(old['payload'])
        original = Path(value['passes'][0]['path'])
        outside = self.base / 'misassociated-capture.json'
        outside.write_bytes(original.read_bytes())
        value['passes'][0]['path'] = str(outside)
        with self.store.db:
            write_record(self.store.db, 'lead_review', 'lead', value, expected_revision=old['revision'])
        self.assertEqual(self.service.gate('lead')['status'], 'BLOCKED')

    def test_closed_assessment_schema_rejects_extra_fields_and_invalid_risk_status(self):
        ticket = self.prepare()
        data = self.payload(ticket); data['assessment']['extra_approval'] = True
        with self.assertRaises(ValueError):
            self.record(ticket, data)
        data = self.payload(ticket); data['assessment']['risks'][0]['status'] = 'PROBABLY_FINE'
        with self.assertRaises(ValueError):
            self.record(ticket, data)

    def test_fixed_resolution_needs_changed_evidence_not_another_claim(self):
        first = self.prepare(); data = self.payload(first)
        proof = data['assessment']['requirements'][0]['evidence'][0]
        data['assessment']['findings'] = [{'id': 'real-gap', 'requirement_id': 'AC:RULE', 'priority': 'P2',
            'description': 'A concrete rule gap needs a current correction', 'evidence': [proof]}]
        self.record(first, data)
        next_pass = self.prepare(); data = self.payload(next_pass)
        data['assessment']['resolutions'] = [{'id': 'real-gap', 'origin_anchor_sha256': '', 'outcome': 'fixed', 'reason': 'Same old evidence, more confident text', 'evidence': [proof]}]
        with self.assertRaisesRegex(ValueError, 'changed|evidence'):
            self.record(next_pass, data)

    def test_rerunning_identical_checks_does_not_prove_a_fix(self):
        first = self.prepare(); data = self.payload(first)
        proof = data['assessment']['requirements'][0]['evidence'][0]
        data['assessment']['findings'] = [{'id': 'same-source-gap', 'requirement_id': 'AC:RULE', 'priority': 'P2',
            'description': 'An actual missing rule requires a correction', 'evidence': [proof]}]
        self.record(first, data)
        self.controller.check('lead')
        second = self.prepare(); data = self.payload(second)
        self.assertNotEqual(first['handoff']['base']['check_hash'], second['handoff']['base']['check_hash'])
        data['assessment']['resolutions'] = [{'id': 'same-source-gap', 'origin_anchor_sha256': '', 'outcome': 'fixed',
            'reason': 'Only reran the same probe without correcting the rule', 'evidence': [proof]}]
        with self.assertRaisesRegex(ValueError, 'changed|evidence'):
            self.record(second, data)

    def test_nested_proposal_credentials_are_rejected_before_storage(self):
        task = self.tasks.snapshot('lead')
        # Deliberately synthetic runtime input; keep credential-shaped bytes out
        # of the published fixture while still exercising the real text guard.
        proposal = {'summary': 'Safe summary', 'technical': {'connection': 'password=' + 'syntheticOnly-0123456789'}, 'stage_ids': ['finish']}
        with self.assertRaises(ValueError):
            self.tasks.propose('lead', proposal, expected_revision=task['revision'])
        self.assertEqual(self.tasks.snapshot('lead'), task)

    def test_structured_secret_keys_cannot_enter_approved_requirement_history(self):
        task = self.tasks.snapshot('lead')
        for key in ('password', 'api_key', 'access_token', 'client-secret'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.tasks.propose('lead', {'summary': 'Safe summary', 'technical': {'connection': [{key: 'SyntheticOnly0123456789'}]},
                    'stage_ids': ['finish']}, expected_revision=task['revision'])
        self.assertEqual(self.tasks.snapshot('lead'), task)
        safe = self.tasks.propose('lead', {'summary': 'Use vault reference', 'technical': {'credential_ref': 'vault:project/server'},
            'stage_ids': ['finish']}, expected_revision=task['revision'])
        self.assertEqual(safe['proposal']['technical']['credential_ref'], 'vault:project/server')

    def test_remote_only_task_can_reference_its_exact_executed_check_capture(self):
        task = self.tasks.snapshot('lead'); contract = copy.deepcopy(task['contract'])
        contract['profile'] = 'infrastructure'
        contract['scope'] = {'resources': ['ssh://server.example.test'], 'effects': ['read_only']}
        contract['stages'][0]['scope'] = copy.deepcopy(contract['scope'])
        contract['evidence'][0]['argv'] = [sys.executable, '-B', '-c', "import json;print(json.dumps({'ready':True}))"]
        self.tasks.revise('lead', contract, expected_revision=task['revision']); self.approve('Remote probe observation')
        self.controller.check('lead')
        for _ in range(3):
            ticket = self.prepare(); data = self.payload(ticket)
            source = ticket['handoff']['base'].get('check_sources', {}).get('ready')
            self.assertIsNotNone(source, 'Remote proof needs an authenticated current executed check source')
            for row in data['assessment']['requirements'] + data['assessment']['risks']:
                for proof in row['evidence']: proof['source'] = source
            self.record(ticket, data)
        self.assertEqual(self.service.gate('lead')['status'], 'READY')

    def test_competing_prepare_has_one_ticket_across_store_connections(self):
        self.implementation(); barrier = threading.Barrier(3)
        def prepare():
            with closing(Store(self.root, self.home)) as store:
                self.assertEqual(store.workspace_id, self.store.workspace_id)
                service = self.module.LeadReview(store); barrier.wait(10)
                try: return ('prepared', service.prepare('lead', thread_id=OWNER, expected_revision=0))
                except ValueError as error: return ('rejected', str(error))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(prepare) for _ in range(2)]
            barrier.wait(10); results = [f.result(timeout=20) for f in futures]
        winners = [value for status, value in results if status == 'prepared']
        self.assertEqual(len(winners), 1, results)
        row = self.service.snapshot('lead')
        self.assertEqual(row['revision'], 1)
        self.assertEqual(row['payload']['inflight']['id'], winners[0]['handoff']['attempt_id'])
        self.assertEqual(len(list((self.store.root/'tasks/lead/lead-review').rglob('basis.json'))), 1)

    def _competing_records(self, conflicting, *, deferred=False):
        ticket = self.prepare(); payload = self.payload(ticket)
        expected_ticket = copy.deepcopy(self.service.snapshot('lead')['payload']['inflight'])
        opened = threading.Event(); prefix_seen = threading.Event()
        first_flushed = threading.Event(); second_flushed = threading.Event()
        ready = threading.Barrier(3); capture_fds = set()
        original_open, original_sync, original_read = os.open, os.fsync, self.module._read
        def pending(path): return Path(str(path)).name == 'capture.json.pending'
        def scheduled_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if pending(path):
                capture_fds.add(fd)
                if threading.current_thread().name == 'lead-first':
                    opened.set(); prefix_seen.wait(1)
            return fd
        def scheduled_sync(fd):
            original_sync(fd)
            if fd in capture_fds:
                (first_flushed if threading.current_thread().name == 'lead-first' else second_flushed).set()
        def scheduled_read(path, limit):
            if pending(path) and threading.current_thread().name == 'lead-second' and not prefix_seen.is_set():
                raw = original_read(path, limit); prefix_seen.set()
                self.assertTrue(first_flushed.wait(5)); return raw
            if pending(path) and threading.current_thread().name == 'lead-first' and prefix_seen.is_set():
                self.assertTrue(second_flushed.wait(5))
            return original_read(path, limit)
        def record(first):
            threading.current_thread().name = 'lead-first' if first else 'lead-second'
            with closing(Store(self.root, self.home)) as store:
                self.assertEqual(store.workspace_id, self.store.workspace_id)
                service = self.module.LeadReview(store); data = copy.deepcopy(payload)
                if conflicting and not first: data['assessment']['summary'] += ' conflicting host data'
                if deferred: store.db.execute('BEGIN')
                ready.wait(10)
                if not first: self.assertTrue(opened.wait(10))
                try:
                    result = service.record('lead', ticket['handoff']['attempt_id'], data,
                        thread_id=OWNER, expected_revision=ticket['revision'])
                    if deferred: store.db.commit()
                    return ('recorded', result['revision'])
                except (ValueError, OSError) as error:
                    if deferred: store.db.rollback()
                    return ('rejected', str(error))
        with patch.object(os, 'open', scheduled_open), patch.object(os, 'fsync', scheduled_sync), \
                patch.object(self.module, '_read', scheduled_read), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(record, first) for first in (True, False)]
            ready.wait(10); results = [future.result(timeout=20) for future in futures]
        row = self.service.snapshot('lead'); capture = self.service._path('lead', ticket['handoff']['attempt_id'])
        self.assertEqual(len(row['payload']['passes']), 1, results)
        self.assertIsNone(row['payload']['inflight'])
        self.assertEqual(capture.read_bytes(), canonical({'ticket': expected_ticket, 'result': payload}))
        self.assertFalse(capture.with_suffix('.json.pending').exists())
        self.assertEqual(sum(status == 'recorded' for status, value in results), 1 if conflicting else 2, results)
        if not conflicting: self.assertEqual({value for status, value in results}, {row['revision']})
        before = self.service.snapshot('lead'); self.assertEqual(self.service.recover('lead'), before)

    def test_competing_identical_record_is_one_immutable_transition(self):
        self._competing_records(False)

    def test_competing_conflicting_record_cannot_replace_first_response(self):
        self._competing_records(True)

    def test_competing_deferred_identical_record_reserves_writer_before_staging(self):
        self._competing_records(False, deferred=True)

    def test_competing_deferred_conflicting_record_keeps_first_response(self):
        self._competing_records(True, deferred=True)

    def test_capture_preserves_caller_rollback_and_recovers_saved_reply(self):
        ticket = self.prepare(); payload = self.payload(ticket)
        self.store.db.execute('BEGIN')
        write_record(self.store.db, 'unit_marker', 'caller', {'uncommitted': True}, expected_revision=0)
        self.record(ticket, payload)
        self.assertTrue(self.store.db.in_transaction, 'record must not commit its caller')
        self.assertEqual(len(self.service.snapshot('lead')['payload']['passes']), 1)
        self.store.db.rollback()
        self.assertIsNone(read_record(self.store.db, 'unit_marker', 'caller')['payload'])
        self.assertEqual(len(self.service.snapshot('lead')['payload']['passes']), 0)
        self.assertTrue(self.service._path('lead', ticket['handoff']['attempt_id']).exists())
        self.service.recover('lead')
        self.assertEqual(self.service.gate('lead')['completed_passes'], 1)
        self.assertIsNone(read_record(self.store.db, 'unit_marker', 'caller')['payload'])

    def test_plan_drift_during_capture_and_after_final_credit(self):
        for after_credit in (False, True):
            with self.subTest(after_credit=after_credit):
                case = LeadReviewTests(); case.setUp(); self.addCleanup(case.doCleanups)
                if after_credit: case.finish_three()
                else: ticket = case.prepare(); payload = case.payload(ticket)
                plan = case.tasks.execution_plan('lead')
                extra = {'id': 'extra', 'kind': 'probe', 'argv': [sys.executable, '-B', '-c',
                    "import json;print(json.dumps({'ready':True}))"],
                    'predicate': {'path': 'ready', 'op': 'eq', 'value': True}}
                case.tasks.update_execution_plan('lead', {'checks': [*plan['checks'], extra]}, expected_revision=plan['revision'])
                if not after_credit:
                    case.record(ticket, payload)
                    state = case.service.snapshot('lead')['payload']
                    self.assertEqual(len(state['passes']), 0); self.assertIsNotNone(state['last_stale'])
                    self.assertIsNone(state['inflight'])
                else:
                    self.assertEqual(case.service.gate('lead')['status'], 'BLOCKED')
                    case.controller.check('lead')
                    held = case.service.gate('lead')
                    self.assertEqual(held['completed_passes'], 3); self.assertEqual(held['status'], 'BLOCKED')
                    case.implementation(refresh=True); case.record(case.prepare())
                    self.assertEqual(case.service.gate('lead')['status'], 'READY')
                    self.assertEqual(case.service.gate('lead')['completed_passes'], 4)

    def test_action_observation_drift_during_capture_and_after_final_credit(self):
        for after_credit in (False, True):
            with self.subTest(after_credit=after_credit):
                case = LeadReviewTests(); case.setUp(); self.addCleanup(case.doCleanups)
                task = case.tasks.snapshot('lead'); contract = copy.deepcopy(task['contract'])
                contract['scope']['effects'] = ['file_write', 'command']
                contract['stages'][0]['scope'] = copy.deepcopy(contract['scope'])
                contract['stages'][0]['actions'] = [{'id': 'existing-resource',
                    'argv': [sys.executable, '-B', '-c', "raise AssertionError('Effect must not launch')"],
                    'observation_argv': [sys.executable, '-B', '-c',
                        "import json,os;print(json.dumps({'exists':True,'resource_id':os.environ['ASE_TEST_RESOURCE_ID']}))"]}]
                case.tasks.revise('lead', contract, expected_revision=task['revision'])
                case.approve('Observe the disposable preexisting resource without submitting an effect')
                case.controller.check('lead'); stage = case.tasks.snapshot('lead')['contract']['stages'][0]
                with patch.dict(os.environ, {'ASE_TEST_RESOURCE_ID': 'fixture-version-1'}):
                    case.controller.execute_actions('lead', stage)
                if after_credit: case.finish_three()
                else: ticket = case.prepare(); payload = case.payload(ticket)
                before = case.service._base('lead'); checks = copy.deepcopy(read_record(case.store.db, 'checks', 'lead'))
                with patch.dict(os.environ, {'ASE_TEST_RESOURCE_ID': 'fixture-version-2'}):
                    case.controller.execute_actions('lead', stage)
                changed = case.service._base('lead')
                self.assertEqual(before['fingerprint'], changed['fingerprint'])
                self.assertEqual(before['check_hash'], changed['check_hash'])
                self.assertEqual(checks, read_record(case.store.db, 'checks', 'lead'))
                self.assertNotEqual(before['action_context_sha256'], changed['action_context_sha256'])
                for operation in case.store.db.execute("SELECT payload FROM runtime_records WHERE kind='operation'"):
                    observed = json.loads(operation['payload'])
                    # APPLIED is conservatively treated as historically
                    # attempted, even for a preexisting observed resource.
                    # Actual host launch instead records attempt/execution IDs.
                    self.assertNotIn('attempt_number', observed)
                    self.assertNotIn('execution_receipt', observed)
                    self.assertEqual(observed['observation']['resource_id'], 'fixture-version-2')
                if not after_credit:
                    case.record(ticket, payload); value = case.service.snapshot('lead')['payload']
                    self.assertEqual(len(value['passes']), 0); self.assertIsNotNone(value['last_stale'])
                    self.assertIsNone(value['inflight'])
                else:
                    held = case.service.gate('lead')
                    self.assertEqual(held['completed_passes'], 3); self.assertEqual(held['status'], 'BLOCKED')
                    case.implementation(refresh=True); case.record(case.prepare())
                    self.assertEqual(case.service.gate('lead')['status'], 'READY')
                    self.assertEqual(case.service.gate('lead')['completed_passes'], 4)


if __name__ == '__main__':
    unittest.main()
