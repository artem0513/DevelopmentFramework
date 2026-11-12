"""Real Git canaries retained from the retired coordinator, without its transport."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ase import inspection
from ase.core import Engine


class InspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ase-inspection-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / 'selected'
        self.repo.mkdir()
        self.git('init', '-b', 'feature/inspection')
        self.git('config', 'user.name', 'Inspection Tests')
        self.git('config', 'user.email', 'inspection@example.invalid')
        (self.repo / 'app.py').write_text('value = 1\n')
        self.git('add', '.')
        self.git('commit', '-m', 'initial')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout

    def program(self, name, tail):
        marker = self.base / (name + '-executed.txt')
        program = self.repo / '.git' / (name + '.py')
        program.write_text('from pathlib import Path\nimport sys\n'
                           + 'Path(' + repr(str(marker)) + ').write_text("canary executed")\n' + tail)
        command = '"' + Path(sys.executable).as_posix() + '" "' + program.as_posix() + '"'
        return marker, command

    def test_nested_directory_is_not_a_separate_git_root(self):
        nested = self.repo / 'nested'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, 'repository root'):
            Engine(nested, self.base / 'state', inspector=inspection)
        self.assertFalse((self.base / 'state').exists())

    def test_safe_git_reads_do_not_execute_repository_fsmonitor(self):
        marker, command = self.program('fsmonitor', "sys.stdout.buffer.write(b'token\\x00')\n")
        self.git('config', 'core.fsmonitor', command)
        self.git('status', '--porcelain')
        self.assertTrue(marker.exists(), 'Control did not exercise the real Git helper')
        marker.unlink()
        inspection.safe_snapshot(self.repo)
        inspection.safe_git(self.repo, 'status', '--porcelain')
        self.assertFalse(marker.exists())

    def test_safe_diff_does_not_execute_repository_textconv(self):
        marker, command = self.program('textconv', "print('converted source')\n")
        self.git('config', 'diff.canary.textconv', command)
        (self.repo / '.gitattributes').write_text('app.py diff=canary\n')
        (self.repo / 'app.py').write_text('value = 2\n')
        self.git('diff', '--textconv')
        self.assertTrue(marker.exists(), 'Control did not exercise the real Git helper')
        marker.unlink()
        inspection.safe_git(self.repo, 'diff', 'HEAD')
        inspection.safe_snapshot(self.repo)
        self.assertFalse(marker.exists())

    def test_safe_diff_does_not_execute_repository_clean_filter(self):
        marker, command = self.program('clean', 'sys.stdout.write(sys.stdin.read())\n')
        self.git('config', 'filter.canary.clean', command)
        (self.repo / '.gitattributes').write_text('app.py filter=canary\n')
        (self.repo / 'app.py').write_text('value = 2\n')
        self.git('diff')
        self.assertTrue(marker.exists(), 'Control did not exercise the real Git helper')
        marker.unlink()
        inspection.safe_git(self.repo, 'diff', 'HEAD')
        inspection.safe_snapshot(self.repo)
        self.assertFalse(marker.exists())

    def test_safe_reads_do_not_execute_repository_index_hook(self):
        marker, command = self.program('index-hook', '')
        hook = self.repo / '.git/hooks/post-index-change'
        hook.write_text('#!/bin/sh\n' + command + '\n', encoding='utf-8')
        hook.chmod(0o755)
        (self.repo / 'app.py').write_text('value = 2\n')
        self.git('add', 'app.py')
        self.assertTrue(marker.exists(), 'Control did not exercise the real Git hook')
        marker.unlink()
        inspection.safe_git(self.repo, 'status', '--porcelain')
        inspection.safe_git(self.repo, 'diff', '--cached')
        inspection.safe_snapshot(self.repo)
        self.assertFalse(marker.exists())

    def test_only_supported_read_operations_are_accepted(self):
        before = inspection.safe_snapshot(self.repo)
        for args in (('commit', '-am', 'unauthorized'), ('push',),
                     ('diff', '--ext-diff'), ('status', '--ignored'),
                     ('diff', '--', '../outside')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                inspection.safe_git(self.repo, *args)
        after = inspection.safe_snapshot(self.repo)
        self.assertEqual(before['fingerprint'], after['fingerprint'])
        self.assertEqual(before['commit'], after['commit'])


if __name__ == '__main__':
    unittest.main()
