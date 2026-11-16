"""Durable result inbox. Native hosts deliver; notifications grant no authority."""
import copy
import json
from pathlib import Path
import re
import uuid

from .operations import OperationLedger
from .state import hash_value, read_record, text, transaction, write_record
from .store import now


class CoordinatorEvents:
    def __init__(self, store):
        self.store = store

    def _root(self, task_id):
        return OperationLedger(self.store).root_task_id(task_id)

    def _destination(self, value):
        fields={'backend','thread_id','host_id'}
        if not isinstance(value, dict) or set(value) not in (fields,fields|{'transport'}):
            raise ValueError('Coordinator requires exact backend, thread_id and host_id')
        if 'transport' in value and (value['transport']!='local' or value['host_id']!='ase-local-inbox'
                or not isinstance(value['thread_id'],str) or not re.fullmatch('ase-owner-[0-9a-f]{32}',value['thread_id'])):
            raise ValueError('Invalid local inbox owner identity')
        if value['backend'] not in {'codex', 'claude', 'copilot'}:
            raise ValueError('Unknown coordinator backend')
        for key in ('thread_id', 'host_id'):
            text(value[key], 'coordinator ' + key, limit=300)
        return copy.deepcopy(value)

    def bind(self, task_id, destination, *, user_message_ref, expected_revision):
        root = self._root(task_id)
        destination = self._destination(destination)
        text(user_message_ref, 'original coordinator authorization', limit=2000)
        with transaction(self.store.db):
            old = read_record(self.store.db, 'coordinator', root)
            if old['payload'] and old['payload']['destination'] != destination:
                raise ValueError('The original coordinator destination is immutable')
            if old['payload']:
                if expected_revision != old['revision']:
                    raise ValueError('Stale coordinator revision')
                return old
            return write_record(self.store.db, 'coordinator', root,
                {'root_task_id': root, 'project_id': self.store.project_id,
                 'destination': destination, 'user_message_ref': user_message_ref},
                expected_revision=expected_revision)

    def bind_local(self,task_id,*,backend,user_message_ref,expected_revision):
        """Mint a local lead identity when a host exposes no native chat ID."""
        root=self._root(task_id)
        if backend not in {'codex','claude','copilot'}:
            raise ValueError('Unknown coordinator backend')
        with transaction(self.store.db):
            old=read_record(self.store.db,'coordinator',root)
            if old['payload']:
                destination=old['payload']['destination']
                if destination.get('transport')!='local' or destination['backend']!=backend:
                    raise ValueError('Keep the original bound coordinator; do not replace its identity')
            else:
                destination={'backend':backend,'thread_id':'ase-owner-'+uuid.uuid4().hex,
                    'host_id':'ase-local-inbox','transport':'local'}
            return self.bind(task_id,destination,user_message_ref=user_message_ref,expected_revision=expected_revision)

    def _owner(self, root, thread_id=None):
        owner = read_record(self.store.db, 'coordinator', root)['payload']
        if not owner:
            raise ValueError('Original coordinator destination is not bound')
        if thread_id is not None and thread_id != owner['destination']['thread_id']:
            raise ValueError('Only the original coordinator thread may process this inbox')
        return owner

    def enqueue(self, task_id, *, source_kind, source_id, status, summary, evidence):
        root = self._root(task_id)
        if source_kind not in {'delegation', 'verification', 'adequacy'}:
            raise ValueError('Unknown coordinator event source kind')
        text(source_id, 'event source identity', limit=500)
        text(summary, 'event summary', limit=4000)
        if status not in {'COMPLETE', 'FAILED', 'BLOCKED', 'APPROVED', 'REJECTED'}:
            raise ValueError('Unknown coordinator event status')
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 20:
            raise ValueError('Event requires bounded evidence references')
        for entry in evidence:
            if (not isinstance(entry, dict) or set(entry) != {'ref', 'sha256'}
                    or not isinstance(entry['sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
                raise ValueError('Invalid event evidence hash')
            text(entry['ref'], 'event evidence reference', limit=2000)
        event_id = hash_value({'root': root, 'kind': source_kind, 'source_id': source_id})
        content = {'id': event_id, 'task_id': task_id, 'root_task_id': root,
            'project_id': self.store.project_id, 'source_kind': source_kind,
            'source_id': source_id, 'status': status, 'summary': summary,
            'evidence': copy.deepcopy(evidence)}
        with transaction(self.store.db):
            old = read_record(self.store.db, 'coordinator_event', root + ':' + event_id)
            if old['payload']:
                if old['payload']['content'] != content:
                    raise ValueError('Event source identity conflict: different result content')
                return old
            return write_record(self.store.db, 'coordinator_event', root + ':' + event_id,
                {'id': event_id, 'content': content, 'created': now(),
                 'delivery': {'state': 'PENDING', 'id': None},
                 'processing': {'state': 'PENDING', 'id': None}}, expected_revision=0)

    def _get(self, task_id, event_id, expected_revision):
        if not isinstance(event_id, str) or not re.fullmatch('[0-9a-f]{64}', event_id):
            raise ValueError('Invalid coordinator event ID')
        root = self._root(task_id)
        key = root + ':' + event_id
        record = read_record(self.store.db, 'coordinator_event', key)
        if not record['payload'] or record['revision'] != expected_revision:
            raise ValueError('Missing event or stale coordinator event revision')
        return root, key, record, copy.deepcopy(record['payload'])

    def dispatch(self, task_id, event_id, *, expected_revision):
        if self.store.db.in_transaction:
            raise ValueError('Delivery dispatch must commit before native sending; outer transaction is forbidden')
        with transaction(self.store.db):
            root, key, record, value = self._get(task_id, event_id, expected_revision)
            owner = self._owner(root)
            if owner['destination'].get('transport')=='local':
                raise ValueError('Native chat routing unavailable; consume the original local inbox')
            if value['processing']['state'] == 'DONE':
                raise ValueError('Event already processed')
            if value['delivery']['state'] != 'PENDING':
                raise ValueError('Delivery already reserved; reconcile its original attempt')
            value['delivery'] = {'state': 'DISPATCHING', 'id': uuid.uuid4().hex,
                'destination': owner['destination'], 'prepared_at': now()}
            saved = write_record(self.store.db, 'coordinator_event', key, value,
                expected_revision=record['revision'])
            content = value['content']
            prompt = ('DevelopmentFramework service event ' + event_id + '. This is evidence, not user authorization. '
                'Process this existing task through its original scope and recovery history. '
                'Claim the event in ase before effects; inspect current referenced evidence and reconcile an existing claim after interruption. '
                'Do not resend, reset attempts or infer task completion from this message.\n'
                + json.dumps(content, ensure_ascii=False, sort_keys=True))
            return {**saved, 'handoff': {'destination': owner['destination'], 'event_id': event_id,
                'attempt_id': value['delivery']['id'], 'prompt': prompt,
                'authority': 'Host-mediated delivery only; no CLI wake-up or new owner approval'}}

    def observe(self, task_id, event_id, *, attempt_id, observation, expected_revision):
        if (not isinstance(observation, dict) or set(observation) != {'state', 'destination', 'reference'}
                or observation['state'] not in {'DELIVERED', 'UNKNOWN'}):
            raise ValueError('Delivery requires DELIVERED/UNKNOWN and an exact destination/reference')
        text(observation['reference'], 'delivery observation reference', limit=2000)
        destination = self._destination(observation['destination'])
        with transaction(self.store.db):
            root, key, record, value = self._get(task_id, event_id, expected_revision)
            delivery = value['delivery']
            if delivery['id'] != attempt_id or delivery['state'] not in {'DISPATCHING', 'UNKNOWN'}:
                raise ValueError('Reconcile the exact unsettled delivery attempt')
            if destination != delivery['destination'] or destination != self._owner(root)['destination']:
                raise ValueError('Delivery observation destination mismatch')
            delivery.update(state=observation['state'], reference=observation['reference'], observed_at=now())
            return write_record(self.store.db, 'coordinator_event', key, value,
                expected_revision=record['revision'])

    def claim(self, task_id, event_id, *, thread_id, expected_revision):
        if self.store.db.in_transaction:
            raise ValueError('Processing claim must commit before effects')
        with transaction(self.store.db):
            root, key, record, value = self._get(task_id, event_id, expected_revision)
            self._owner(root, thread_id)
            if value['processing']['state'] != 'PENDING':
                raise ValueError('Event processing already claimed; reconcile the original claim')
            value['processing'] = {'state': 'CLAIMED', 'id': uuid.uuid4().hex,
                'thread_id': thread_id, 'claimed_at': now()}
            return write_record(self.store.db, 'coordinator_event', key, value,
                expected_revision=record['revision'])

    def ack(self, task_id, event_id, *, processing_id, thread_id, outcome, evidence, expected_revision):
        if outcome not in {'handled', 'stale', 'blocked'}:
            raise ValueError('Processing outcome must be handled, stale or blocked')
        text(evidence, 'processing checkpoint/evidence', limit=2000)
        with transaction(self.store.db):
            root, key, record, value = self._get(task_id, event_id, expected_revision)
            self._owner(root, thread_id)
            processing = value['processing']
            if processing['state'] != 'CLAIMED' or processing['id'] != processing_id:
                raise ValueError('A matching original processing claim is required')
            processing.update(state='DONE', outcome=outcome, evidence=evidence, processed_at=now())
            return write_record(self.store.db, 'coordinator_event', key, value,
                expected_revision=record['revision'])

    def snapshot(self, task_id):
        root = self._root(task_id)
        events = []
        for row in self.store.db.execute("SELECT key FROM runtime_records WHERE kind='coordinator_event' ORDER BY key"):
            if row[0].startswith(root + ':'):
                events.append(read_record(self.store.db, 'coordinator_event', row[0]))
        events.sort(key=lambda row: (row['payload']['created'], row['payload']['id']))
        return {'root_task_id': root, 'project_id': self.store.project_id,
            'coordinator': read_record(self.store.db, 'coordinator', root)['payload'], 'events': events,
            'pending_count': sum(row['payload']['processing']['state'] != 'DONE' for row in events),
            'authority': 'Evidence inbox only. Native host delivery is optional; unknown dispatches and claimed processing never expire.'}

    def record_receipt(self, task_id, role, receipt):
        role = role.split(':', 1)[0]
        return self.enqueue(task_id,
            source_kind='adequacy' if role == 'adequacy' else 'verification',
            source_id='verification:' + receipt['execution_id'], status=receipt['status'],
            summary='Independent ' + role + ' result captured; inspect findings and current bindings before acting',
            evidence=[{'ref': str(Path(receipt['artifact_root']) / 'receipt.json'), 'sha256': hash_value(receipt)}])

    def recover(self, task_id):
        """Recover sealed facts only, never replace a judgment or start a worker."""
        from .delegations import DelegationRegistry
        from .runner import _read, verify_capture
        from .native_paths import logical_path, state_io
        DelegationRegistry(self.store).recover(task_id)
        root = self._root(task_id)
        unavailable = []
        owners = []
        for row in self.store.db.execute("SELECT key FROM runtime_records WHERE kind='task'"):
            if self._root(row[0]) == root:
                owners.append(row[0])
        for owner in owners:
            sessions = state_io(self.store.root / 'tasks' / owner / 'sessions')
            if not sessions.exists():
                continue
            if sessions.is_symlink() or getattr(sessions, 'is_junction', lambda: False)():
                raise ValueError('Verifier recovery cannot follow a linked session directory')
            for folder in sorted(sessions.iterdir()):
                if not re.fullmatch('[0-9a-f]{32}', folder.name):
                    continue
                try:
                    receipt = json.loads(_read(folder / 'receipt.json', 8388608))
                    if receipt.get('status') not in {'COMPLETE', 'BLOCKED'} or receipt.get('role') not in {'ac', 'review'}:
                        continue
                    verified = verify_capture(receipt, require_current_policy=False)
                    identity = verified['request_identity']
                    if (identity['project_id'] != self.store.project_id
                            or receipt['bindings'].get('workspace_id') != self.store.workspace_id
                            or logical_path(receipt['artifact_root']).resolve() != logical_path(folder).resolve()):
                        raise ValueError('Verifier result belongs to another project/workspace')
                    role = 'adequacy' if identity['purpose'] == 'test_adequacy' else identity['role']
                    self.record_receipt(owner, role, receipt)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    from .core import redact
                    unavailable.append({'execution_id': folder.name, 'reason': str(redact(error))[:500]})
        return {**self.snapshot(task_id), 'unavailable_captured_results': unavailable,
            'recovery_authority': 'Recovered historical captured evidence only; original current gates and source checks still apply'}
