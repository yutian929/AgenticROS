"""Cleanup boundaries tested against disposable host directories and mocked Docker."""
import importlib.util
import errno
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('clean_runtime', Path(__file__).resolve().parents[1]/'scripts/clean_runtime.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class Cleanup(unittest.TestCase):
    def test_default_cleanup_keeps_model_images_and_build_cache(self):
        def docker(*args):
            if args == ('image', 'ls', '--format', '{{.Repository}}:{{.Tag}}'):
                return 'agenticros-valen:single\nagenticros-sam3:single\nunrelated:latest'
            return ''
        with patch.object(cleanup, 'docker', side_effect=docker) as calls:
            ordinary = cleanup.inventory()
            self.assertEqual(ordinary['images'], [])
            cleanup.clean(ordinary)
            self.assertFalse(any(call.args[:2] == ('image', 'rm') for call in calls.call_args_list))
            self.assertFalse(any('prune' in call.args for call in calls.call_args_list))
            explicit = cleanup.inventory(images=True)
            self.assertEqual(explicit['images'], ['agenticros-sam3:single', 'agenticros-valen:single'])

    def test_disappeared_network_is_success_but_other_failures_propagate(self):
        missing = subprocess.CalledProcessError(1, ['docker'], stderr='Error response from daemon: network old not found')
        with patch.object(cleanup, 'docker', side_effect=missing):
            cleanup.remove_resource('network', 'old')
        for message in ('network old has active endpoints', 'permission denied', 'Cannot connect to Docker daemon'):
            error = subprocess.CalledProcessError(1, ['docker'], stderr=message)
            with patch.object(cleanup, 'docker', side_effect=error):
                with self.assertRaises(subprocess.CalledProcessError):
                    cleanup.remove_resource('network', 'old')

    def test_directory_race_retries_without_touching_source(self):
        original = cleanup.shutil.rmtree
        calls = []
        def remove(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError(errno.ENOTEMPTY, 'Directory not empty')
            original(path)
        with patch.object(cleanup, 'docker', return_value=''), patch.object(cleanup.shutil, 'rmtree', side_effect=remove), patch.object(cleanup.time, 'sleep'):
            cleanup.remove_directory(self.root/'runs')
        self.assertEqual(len(calls), 2)
        self.assertFalse((self.root/'runs').exists())
        self.assertTrue((self.root/'source.py').exists())

    def test_persistent_writer_stops_after_bounded_retries(self):
        with patch.object(cleanup, 'docker', return_value=''), patch.object(cleanup.shutil, 'rmtree', side_effect=OSError(errno.ENOTEMPTY, 'busy')) as remove, patch.object(cleanup.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'keeps changing'):
                cleanup.remove_directory(self.root/'runs')
            self.assertEqual(remove.call_count, 3)

    def test_new_consumer_blocks_directory_retry(self):
        with patch.object(cleanup, 'check_consumers', side_effect=[None, RuntimeError('consumer')]), patch.object(cleanup.shutil, 'rmtree', side_effect=OSError(errno.ENOTEMPTY, 'busy')) as remove, patch.object(cleanup.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'consumer'):
                cleanup.remove_directory(self.root/'runs')
            self.assertEqual(remove.call_count, 1)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(cleanup, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.root/'runs').mkdir()
        (self.root/'runs/evidence').write_text('runtime')
        (self.root/'source.py').write_text('source')

    def test_preview_never_mutates_and_excludes_other_projects(self):
        ours = dict(Id='ours', Name='/agenticros-instance-test', Config={'Labels':{}}, Mounts=[])
        other = dict(Id='other', Name='/database', Config={'Labels':{}}, Mounts=[])
        def docker(*args):
            if args == ('ps', '-aq'): return 'ours other'
            if args[:2] == ('container','inspect'): return json.dumps([ours, other])
            if args[:3] == ('volume','ls','-q'): return 'agenticros_data unrelated'
            if args[:2] == ('volume','inspect'): return json.dumps([{'Name':'agenticros_data'}, {'Name':'unrelated'}])
            if args[:3] == ('network','ls','-q'): return ''
            self.fail('Unexpected mutating command: '+str(args))
        with patch.object(cleanup, 'docker', side_effect=docker):
            plan = cleanup.inventory()
        self.assertEqual(plan['volumes'], ['agenticros_data'])
        self.assertEqual([c['id'] for c in plan['containers']], ['ours'])
        self.assertTrue((self.root/'runs/evidence').exists())

    def test_cleanup_removes_runs_not_source_and_never_prunes(self):
        plan = dict(containers=[{'id':'test'}], volumes=['agenticros_test'],
                    networks=['agenticros_test'], images=[], directories=[str(self.root/'runs')])
        with patch.object(cleanup, 'docker', return_value='') as docker:
            cleanup.clean(plan)
        self.assertFalse((self.root/'runs').exists())
        self.assertTrue((self.root/'source.py').exists())
        self.assertFalse(any('prune' in call.args for call in docker.call_args_list))

    def test_runs_symlink_refused_before_docker(self):
        import shutil
        shutil.rmtree(self.root/'runs')
        (self.root/'runs').symlink_to(self.root)
        with patch.object(cleanup, 'docker') as docker:
            with self.assertRaisesRegex(RuntimeError, 'symlink'):
                cleanup.inventory()
            docker.assert_not_called()

    def test_new_container_reference_blocks_host_deletion(self):
        container = dict(Mounts=[dict(Type='bind', Source=str(self.root/'runs'))])
        plan = dict(containers=[], volumes=[], networks=[], images=[], directories=[str(self.root/'runs')])
        with patch.object(cleanup, 'docker', side_effect=['new', json.dumps([container])]):
            with self.assertRaisesRegex(RuntimeError, 'consumer'):
                cleanup.clean(plan)
        self.assertTrue((self.root/'runs/evidence').exists())


if __name__ == '__main__':
    unittest.main()
