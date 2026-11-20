"""New user/system effects need a bound justification before approval and stage start (DH-1009 shape)."""
from contextlib import closing
import copy
from pathlib import Path
import sys
import tempfile
import unittest

from ase.store import Store
from ase.tasks import TaskService


def contract(unknown_stage='implement'):
    return {'id': 'dms-alerts', 'schema_version': 2, 'profile': 'software',
        'request': 'Email and show an alert when DMS token renewal fails',
        'scope': {'resources': ['app'], 'effects': ['file_write']},
        'sources': [{'id': 'JIRA-1009', 'locator': 'ticket text: email plus visible alert'}],
        'acceptance': [{'id': 'ALERT', 'expected': 'A confirmed renewal failure queues an email and shows an alert',
                        'evidence_ids': ['tests']}],
        'evidence': [{'id': 'tests', 'kind': 'test', 'parser': 'unittest',
                      'argv': [sys.executable, '-B', '-m', 'unittest', 'discover']}],
        'unknowns': [{'id': 'IDLE-DETECTION', 'statement': 'Must a failure be detected while nothing is syncing?',
                      'blocking_stage_ids': [unknown_stage]}],
        'stages': [{'id': 'implement', 'acceptance_ids': ['ALERT']},
                   {'id': 'other', 'acceptance_ids': ['ALERT']}]}


def timer(**override):
    effect = {'id': 'idle-timer', 'stage_ids': ['implement'],
              'change': 'Check authorization every five minutes even with no files to sync',
              'basis': 'ASSUMPTION', 'ref': 'IDLE-DETECTION',
              'uncovered_scenario': 'GetSession() only detects the failure when a connection is used'}
    effect.update(override)
    return effect


class TaskEffectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ase-effects-')
        root = Path(self.temp.name) / 'repo'
        root.mkdir()
        self.store = Store(root, Path(self.temp.name) / 'state')
        self.tasks = TaskService(self.store)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)

    def created(self, **kwargs):
        return self.tasks.create(contract(**kwargs))

    def propose(self, task, effects=None, **extra):
        proposal = {'summary': 'Queue the lapse email through the existing session path', 'stage_ids': ['implement']}
        if effects is not None:
            proposal['effects'] = effects
        proposal.update(extra)
        return self.tasks.propose(task['task_id'], proposal, expected_revision=task['revision'])

    def test_proposal_without_effects_keeps_existing_behavior(self):
        task = self.propose(self.created(unknown_stage='other'))
        task = self.tasks.approve(task['task_id'], proposal_hash=task['proposal_hash'],
                                  user_message_ref='test:owner', approved_stage_ids=['implement'])
        self.assertEqual(task['state'], 'READY')

    def test_existing_path_effect_tied_to_the_confirmed_requirement_passes(self):
        task = self.propose(self.created(unknown_stage='other'), [{
            'id': 'queue-email', 'stage_ids': ['implement'],
            'change': 'Queue the lapse email when GetSession() confirms a failed renewal',
            'basis': 'REQUIREMENT', 'ref': 'JIRA-1009',
            'uncovered_scenario': 'Not needed: the existing session path covers the confirmed requirement'}])
        task = self.tasks.approve(task['task_id'], proposal_hash=task['proposal_hash'],
                                  user_message_ref='test:owner', approved_stage_ids=['implement'])
        self.assertEqual(self.tasks.require_approval(task['task_id'], 'implement')['task_id'], task['task_id'])

    def test_new_timer_justified_only_by_prose_is_returned_before_any_approval(self):
        task = self.created()
        for effect in (timer(basis='REQUIREMENT', ref='the user must reconnect immediately'),
                       timer(basis='REQUIREMENT', ref='NO-SUCH-SOURCE'),
                       timer(basis='REQUIREMENT', ref='IDLE-DETECTION'),
                       timer(basis='ASSUMPTION', ref='NO-SUCH-UNKNOWN'),
                       timer(basis='PLAUSIBLE'), timer(change='   '), timer(uncovered_scenario='')):
            with self.subTest(effect=effect), self.assertRaises(ValueError):
                self.propose(task, [effect])
        self.assertEqual(self.tasks.snapshot(task['task_id'])['state'], task['state'])

    def test_unconfirmed_assumption_holds_the_stage_until_the_owner_decides(self):
        task = self.propose(self.created(), [timer()])
        with self.assertRaisesRegex(ValueError, 'Unresolved unknown blocks stage'):
            self.tasks.approve(task['task_id'], proposal_hash=task['proposal_hash'],
                               user_message_ref='test:owner', approved_stage_ids=['implement'])

    def test_owner_decision_recorded_as_resolved_unknown_supports_the_effect(self):
        decided = contract()
        decided['unknowns'][0].update(status='RESOLVED', resolution='Owner: detect failures while idle')
        task = self.tasks.create(decided)
        task = self.propose(task, [timer(basis='DECISION')])
        task = self.tasks.approve(task['task_id'], proposal_hash=task['proposal_hash'],
                                  user_message_ref='test:owner', approved_stage_ids=['implement'])
        self.assertEqual(task['state'], 'READY')
        with self.assertRaises(ValueError):
            undecided = contract(unknown_stage='other')
            undecided['id'] = 'dms-alerts-undecided'
            self.propose(self.tasks.create(undecided), [timer(basis='DECISION')])

    def test_acceptance_reference_must_belong_to_every_effect_stage(self):
        owned = contract(unknown_stage='other')
        owned['acceptance'].append({'id': 'REPORT', 'expected': 'A weekly report lists lapsed connections',
                                    'evidence_ids': ['tests']})
        owned['stages'][1]['acceptance_ids'] = ['REPORT']
        task = self.tasks.create(owned)
        for basis in ('REQUIREMENT', 'DECISION'):
            with self.subTest(basis=basis), self.assertRaisesRegex(ValueError, 'reference for its stages'):
                self.propose(task, [timer(basis=basis, ref='REPORT')])
        task = self.propose(task, [timer(basis='REQUIREMENT', ref='ALERT')])
        self.assertEqual(task['state'], 'AWAITING_APPROVAL')

    def test_decision_needs_a_recorded_answer_that_covers_the_effect_stage(self):
        unrelated = contract(unknown_stage='other')
        unrelated['unknowns'][0].update(status='RESOLVED', resolution='Owner: detect failures while idle')
        with self.assertRaisesRegex(ValueError, 'resolved unknown'):
            self.propose(self.tasks.create(unrelated), [timer(basis='DECISION')])
        unanswered = contract()
        unanswered['id'] = 'dms-alerts-unanswered'
        unanswered['unknowns'][0]['status'] = 'RESOLVED'
        with self.assertRaisesRegex(ValueError, 'resolved unknown'):
            self.propose(self.tasks.create(unanswered), [timer(basis='DECISION')])

    def test_placeholder_source_without_locator_cannot_justify_an_effect(self):
        placeholder = contract(unknown_stage='other')
        placeholder['sources'].append({'id': 'placeholder'})
        task = self.tasks.create(placeholder)
        with self.assertRaisesRegex(ValueError, 'reference for its stages'):
            self.propose(task, [timer(basis='REQUIREMENT', ref='placeholder')])

    def test_malformed_saved_entries_and_stage_ids_raise_value_errors_only(self):
        odd = contract(unknown_stage='other')
        odd['sources'].append({'id': [], 'locator': 'x'})
        task = self.tasks.create(odd)
        task = self.propose(task, [])
        self.assertEqual(task['state'], 'AWAITING_APPROVAL')
        task = self.propose(task, [timer(basis='REQUIREMENT', ref='ALERT')])
        with self.assertRaises(ValueError):
            self.tasks.propose(task['task_id'], {'summary': 'x', 'stage_ids': [{'id': 'implement'}]},
                               expected_revision=task['revision'])
        with self.assertRaises(ValueError):
            self.tasks.approve(task['task_id'], proposal_hash=task['proposal_hash'],
                               user_message_ref='test:owner', approved_stage_ids=[{'id': 'implement'}])

    def test_tampered_saved_effects_fail_closed(self):
        task = self.propose(self.created(unknown_stage='other'), [{
            'id': 'queue-email', 'stage_ids': ['implement'], 'change': 'Queue the lapse email',
            'basis': 'REQUIREMENT', 'ref': 'JIRA-1009', 'uncovered_scenario': 'Existing path is sufficient'}])
        stage = task['contract']['stages'][0]
        for effects in ({}, '', False, 0, None):
            stale = copy.deepcopy(task)
            stale['proposal']['effects'] = effects
            with self.subTest(effects=effects), self.assertRaises(ValueError):
                self.tasks._check_effects(stale, stage)
        stale = copy.deepcopy(task)
        stale['proposal']['effects'][0].update(basis='ASSUMPTION', ref='IDLE-DETECTION')
        stale['contract']['unknowns'][0].update(blocking_stage_ids=['implement'], status='PENDING')
        with self.assertRaises(ValueError):
            self.tasks._check_effects(stale, stage)
        stale = copy.deepcopy(task)
        stale['proposal']['stage_ids'].append('ghost')
        stale['proposal']['effects'][0]['stage_ids'] = ['ghost']
        with self.assertRaises(ValueError):
            self.tasks._check_effects(stale, stage)
        with self.assertRaises(ValueError):
            self.tasks.propose(task['task_id'], {'summary': 'x', 'stage_ids': ['implement'], 'effects': None},
                               expected_revision=task['revision'])

    def test_unknown_must_actually_block_the_effect_stage(self):
        task = self.created(unknown_stage='other')
        with self.assertRaisesRegex(ValueError, 'does not block'):
            self.propose(task, [timer()])

    def test_malformed_effect_lists_are_rejected(self):
        task = self.created()
        for effects in ('timer', [timer(), timer()], [{'id': 'x'}], [timer(stage_ids=[])],
                        [timer(stage_ids=['other'])], [dict(timer(), extra=True)],
                        [timer(stage_ids=[{'id': 'implement'}])], [timer(stage_ids=['implement', 'implement'])],
                        [timer(basis=['ASSUMPTION'])], [timer(ref=7)]):
            with self.subTest(effects=effects), self.assertRaises(ValueError):
                self.propose(task, effects)

    def test_stage_start_revalidates_the_stored_effects_against_the_current_contract(self):
        task = self.propose(self.created(unknown_stage='other'), [{
            'id': 'queue-email', 'stage_ids': ['implement'], 'change': 'Queue the lapse email',
            'basis': 'REQUIREMENT', 'ref': 'JIRA-1009', 'uncovered_scenario': 'Existing path is sufficient'}])
        task = self.tasks.approve(task['task_id'], proposal_hash=task['proposal_hash'],
                                  user_message_ref='test:owner', approved_stage_ids=['implement'])
        stale = copy.deepcopy(task)
        stale['contract']['sources'] = []
        with self.assertRaisesRegex(ValueError, 'acceptance or source reference'):
            self.tasks._check_effects(stale, stale['contract']['stages'][0])
        stale = copy.deepcopy(task)
        stale['proposal']['effects'][0].update(basis='ASSUMPTION', ref='IDLE-DETECTION')
        stale['contract']['unknowns'][0]['blocking_stage_ids'] = ['implement']
        with self.assertRaisesRegex(ValueError, 'Unconfirmed effect assumption'):
            self.tasks._check_effects(stale, stale['contract']['stages'][0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
