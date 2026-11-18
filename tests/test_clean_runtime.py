"""Cleanup boundaries tested against disposable host directories and mocked Docker."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('clean_runtime', Path(__file__).resolve().parents[1]/'scripts/clean_runtime.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class Cleanup(unittest.TestCase):
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
