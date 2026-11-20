"""Workspace preparation guards; never changes real user volumes."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('prepare_workspace',Path(__file__).resolve().parents[1]/'scripts/prepare_workspace.py')
workspace=importlib.util.module_from_spec(spec);spec.loader.exec_module(workspace)
class WorkspacePreparation(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);p=patch.object(workspace,'ROOT',self.root);p.start();self.addCleanup(p.stop)
    def test_existing_files_retained(self):
        target=self.root/'runs/workspace';target.mkdir(parents=True);(target/'adapter.py').write_text('keep')
        workspace.prepare();self.assertEqual((target/'adapter.py').read_text(),'keep')
    def test_new_workspace_permissions(self):
        workspace.prepare();self.assertEqual((self.root/'runs/workspace').stat().st_mode&0o2770,0o2770)
    def test_symlink_rejected(self):
        (self.root/'runs').symlink_to(self.root)
        with self.assertRaisesRegex(RuntimeError,'symlinks'):workspace.prepare()
    def test_legacy_workspaces_require_explicit_selection(self):
        old=self.root/'runs/agent_instances/old/workspace';old.mkdir(parents=True);(old/'data').write_text('keep')
        with self.assertRaisesRegex(RuntimeError,'migrate_workspace'):workspace.prepare()
        self.assertEqual((old/'data').read_text(),'keep');self.assertFalse((self.root/'runs/workspace').exists())
