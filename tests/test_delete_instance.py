"""Destructive-operation guards tested with temporary files and mocked Docker."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('delete_instance', ROOT / 'scripts/delete_instance.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class DeleteInstance(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / 'runs/instances/123'
        (self.target / 'workspace').mkdir(parents=True)
        (self.target / 'workspace/adapter.py').write_text('generated')
        self.other = self.root / 'runs/instances/456/workspace'
        self.other.mkdir(parents=True)
        (self.other / 'keep').write_text('keep')
        self.volumes = {'agenticros_codex-home-instance-123', 'agenticros_codex-home-instance-456'}
        self.calls = []
        self.consumer = ''
        self.fail_remove = False
        patcher = patch.object(cleanup, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(cleanup, 'docker', side_effect=self.docker)
        patcher.start()
        self.addCleanup(patcher.stop)

    def docker(self, *args, environment):
        self.calls.append(args)
        if args[:2] == ('compose', 'config'):
            return json.dumps({'name': 'agenticros', 'volumes': {'codex-home': {
                'name': 'agenticros_codex-home' + environment['AGENTICROS_INSTANCE_SUFFIX']}},
                'services': {'agenticros': {'volumes': [{'target': '/workspace',
                    'source': str(self.root / 'runs' / environment['AGENTICROS_WORKSPACE_SUBDIR'])}]}}})
        if args[0] == 'ps':
            return self.consumer
        if args[:2] == ('volume', 'ls'):
            return '\n'.join(self.volumes)
        if args[:2] == ('volume', 'rm'):
            if self.fail_remove:
                raise subprocess.CalledProcessError(1, args, stderr='volume in use')
            self.volumes.remove(args[2])
            return ''
        raise AssertionError(args)

    def delete(self, name='123', dry_run=False):
        with contextlib.redirect_stdout(io.StringIO()):
            cleanup.delete_instance(name, dry_run)

    def test_only_selected_instance_deleted_and_repeat_is_safe(self):
        self.delete()
        self.delete()
        self.assertFalse(self.target.exists())
        self.assertEqual((self.other / 'keep').read_text(), 'keep')
        self.assertEqual(self.volumes, {'agenticros_codex-home-instance-456'})

    def test_dry_run_preserves_everything(self):
        self.delete(dry_run=True)
        self.assertTrue(self.target.exists())
        self.assertEqual(len(self.volumes), 2)
        self.assertFalse(any(c[:2] == ('volume', 'rm') for c in self.calls))

    def test_container_reference_blocks_all_deletion(self):
        self.consumer = 'container-id'
        with self.assertRaisesRegex(RuntimeError, 'container-id'):
            self.delete()
        self.assertTrue(self.target.exists())
        self.assertEqual(len(self.volumes), 2)

    def test_volume_removal_failure_preserves_workspace(self):
        self.fail_remove = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.delete()
        self.assertTrue(self.target.exists())

    def test_symlink_parent_rejected_before_docker(self):
        (self.root / 'runs/instances/link').symlink_to(self.other.parent)
        with self.assertRaisesRegex(RuntimeError, '符号链接'):
            self.delete('link')
        self.assertEqual(self.calls, [])
        self.assertTrue((self.other / 'keep').exists())

    def test_nested_symlink_does_not_delete_its_target(self):
        (self.target / 'workspace/link').symlink_to(self.other)
        self.delete()
        self.assertTrue((self.other / 'keep').exists())

    def test_invalid_name_rejected_before_docker(self):
        for name in ('', '..', '../456', '/tmp', 'a' * 49):
            with self.assertRaises(RuntimeError):
                self.delete(name)
        self.assertEqual(self.calls, [])

    def test_default_is_explicit_and_preserves_other_instances(self):
        default = self.root / 'runs/workspace'
        default.mkdir()
        self.volumes.add('agenticros_codex-home')
        self.delete('default')
        self.assertFalse(default.exists())
        self.assertTrue(self.target.exists())
        self.assertNotIn('agenticros_codex-home', self.volumes)

    def test_shell_requires_explicit_name(self):
        result = subprocess.run(['bash', str(ROOT / 'scripts/delete-instance.sh')],
                                capture_output=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
