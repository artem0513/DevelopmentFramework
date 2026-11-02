"""Protected-file exception receipt tests; deterministic verifiers are protocol-only."""
import json
from pathlib import Path
import unittest
from test_system import SystemTests

class ProtectedAuthorizationTests(unittest.TestCase):
    setUp=SystemTests.setUp
    git=SystemTests.git
    cli=SystemTests.cli
    start=SystemTests.start
    fixture_codex=SystemTests.fixture_codex
    assert_gate=SystemTests.assert_gate

    def change(self, path='AGENTS.md'):
        p=self.repo/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('User-authorized instruction\n')

    def authorize(self,*paths):
        return self.cli('authorize-protected',self.task,'--reason','User explicitly requested these files',*[v for p in paths for v in ('--path',p)])

    def test_default_reject_exact_authorization_accepts_protocol_gate(self):
        self.start();self.change();self.cli('check',self.task);self.fixture_codex();self.cli('verify',self.task)
        self.assert_gate('REJECTED',contains='Unauthorized protected')
        receipt=self.authorize('AGENTS.md');self.assertEqual(receipt['paths'],['AGENTS.md'])
        self.assert_gate('APPROVED')
        self.assertTrue((self.state/'protected-authorizations'/ (receipt['id']+'.json')).is_file())

    def test_authorization_does_not_approve_missing_verifiers(self):
        self.start();self.change();self.authorize('AGENTS.md');self.cli('check',self.task)
        self.assert_gate('BLOCKED',contains='Independent')

    def test_source_change_stales_authorization(self):
        self.start();self.change();self.authorize('AGENTS.md');(self.repo/'app.py').write_text('def add(a,b): return a+b\n# new source revision\n')
        self.cli('check',self.task);self.fixture_codex();self.cli('verify',self.task)
        self.assert_gate('REJECTED',contains='Unauthorized protected')

    def test_exact_scope_leaves_other_protections(self):
        self.start();self.change();self.change('.github/workflows/new.yml');self.authorize('AGENTS.md')
        gate=self.cli('gate',self.task,ok=(2,));self.assertTrue(any('.github/workflows/new.yml' in x for x in gate['rejected_reasons']))

    def test_bad_paths_rejected(self):
        self.start();self.change()
        for path in ['*','../AGENTS.md','./AGENTS.md','AGENTS.md/','C:/AGENTS.md','/AGENTS.md','.agents','app.py','AGENTS.md\\x','AGENTS.md:ads']:
            with self.subTest(path=path):
                r=self.cli('authorize-protected',self.task,'--path',path,'--reason','explicit user authorization',ok=(3,));self.assertEqual(r['status'],'BLOCKED')

    def test_reauthorization_preserves_audit_history(self):
        self.start();self.change();first=self.authorize('AGENTS.md');second=self.authorize('AGENTS.md')
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(len(list((self.state/'protected-authorizations').glob('*.json'))),2)

# Imported fixture class is not a second discovered test suite.
del SystemTests
