"""Owned crash staging does not consume the admitted source-inventory budget."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from ase.edit_application import plan_edits
from ase.gates import source_snapshot
from ase.local_edit_intent import _guard
from ase.workflow import _target


class LocalEditInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ase-edit-inventory-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for number in range(1000):
            (self.root / ('f%04d.txt' % number)).write_bytes(b'old')
        database = sqlite3.connect(':memory:')
        self.addCleanup(database.close)
        database.execute('CREATE TABLE task_discoveries(task_id TEXT, revision INTEGER, payload TEXT)')
        self.store = SimpleNamespace(db=database, repo=self.root, workspace_id='disposable')
        self.controller = SimpleNamespace(store=self.store)
        self.task = {'task_id': 'edge', 'contract': {'profile': 'document',
            'scope': {'resources': ['.'], 'effects': ['file_write']}}}
        edits = [{'path': 'f0000.txt', 'operation': 'write',
            'before_sha256': hashlib.sha256(b'old').hexdigest(), 'content': 'after'}]
        self.plan = plan_edits(self.root, self.task['contract']['scope'], edits, 'a' * 32, _target)

    def test_owned_partial_stage_does_not_block_recovery_at_source_limit(self):
        before = _guard(self.controller, self.task, self.plan)
        stage = self.root / self.plan['edits'][0]['temporary_path']
        stage.write_bytes(b'a')
        self.assertEqual(_guard(self.controller, self.task, self.plan), before)
        self.assertEqual(stage.read_bytes(), b'a')
        with self.assertRaisesRegex(ValueError, 'inventory exceeds limit'):
            source_snapshot(self.store, self.task['contract'], task_id='edge')

    def test_foreign_stage_name_still_consumes_source_inventory_budget(self):
        foreign = self.root / ('f0000.txt.ase-' + 'b' * 32 + '-0.tmp')
        foreign.write_bytes(b'a')
        with self.assertRaisesRegex(ValueError, 'inventory exceeds limit'):
            _guard(self.controller, self.task, self.plan)
        self.assertEqual(foreign.read_bytes(), b'a')
