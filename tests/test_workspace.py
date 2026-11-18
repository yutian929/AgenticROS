"""Workspace preparation guards; no live containers or user volumes are changed."""
import importlib.util
import io
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('prepare_workspace',
    Path(__file__).resolve().parents[1] / 'scripts/prepare_workspace.py')
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class WorkspacePreparation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root_patch = patch.object(workspace, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_active_legacy_consumer_blocks_import(self):
        with patch.object(workspace.subprocess, 'run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, json.dumps({'name': 'test'})),
                               subprocess.CompletedProcess([], 0),
                               subprocess.CompletedProcess([], 0, 'active-container')]
            with self.assertRaisesRegex(RuntimeError, '退出旧 Agent'):
                workspace.prepare()
        self.assertFalse((self.root / 'runs/workspace').exists())

    def test_existing_host_files_are_never_merged_or_overwritten(self):
        target = self.root / 'runs/workspace'
        target.mkdir(parents=True)
        (target / 'adapter.py').write_text('keep this code')
        with patch.object(workspace.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            run.side_effect = [subprocess.CompletedProcess([], 0, json.dumps({'name': 'test'})),
                               subprocess.CompletedProcess([], 0)]
            workspace.prepare()
        self.assertEqual((target / 'adapter.py').read_text(), 'keep this code')
        self.assertEqual(run.call_count, 2)

    def test_new_workspace_has_shared_group_permissions(self):
        with patch.object(workspace.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            run.side_effect = [subprocess.CompletedProcess([], 0, json.dumps({'name': 'test'})),
                               subprocess.CompletedProcess([], 1)]
            workspace.prepare()
        mode = (self.root / 'runs/agent_instances/default/workspace').stat().st_mode
        self.assertEqual(mode & 0o2770, 0o2770)

    def test_symlink_cannot_expose_another_host_directory(self):
        (self.root / 'runs').symlink_to(self.root)
        with self.assertRaisesRegex(RuntimeError, 'symlinks'):
            workspace.prepare()

    def test_instances_keep_separate_files_without_legacy_import(self):
        with patch.object(workspace.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            for name in ('account-a', 'account-b'):
                with patch.dict(os.environ, AGENTICROS_INSTANCE=name):
                    workspace.prepare()
            target = self.root / 'runs/agent_instances/account-a/workspace'
            (target / 'adapter.py').write_text('retained')
            with patch.dict(os.environ, AGENTICROS_INSTANCE='account-a'):
                workspace.prepare()
            run.assert_not_called()
        self.assertEqual((target / 'adapter.py').read_text(), 'retained')
        self.assertEqual(list((self.root / 'runs/agent_instances/account-b/workspace').iterdir()), [])
        self.assertFalse((self.root / 'runs/workspace').exists())

    def test_instance_parent_symlink_is_rejected(self):
        (self.root / 'runs').mkdir()
        (self.root / 'runs/agent_instances').symlink_to(self.root)
        with patch.dict(os.environ, AGENTICROS_INSTANCE='account-a'):
            with self.assertRaisesRegex(RuntimeError, 'symlinks'):
                workspace.prepare()

    def test_instance_path_traversal_is_rejected(self):
        with patch.dict(os.environ, AGENTICROS_INSTANCE='../../outside'):
            with self.assertRaisesRegex(RuntimeError, 'Invalid Agent instance'):
                workspace.prepare()


if __name__ == '__main__':
    unittest.main()
