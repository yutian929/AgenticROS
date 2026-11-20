"""Migration keeps original data, rejects ambiguity and active consumers."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('migration',ROOT/'scripts/migrate_workspace.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
sys.path.pop(0)
class Migration(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);p=patch.object(m,'ROOT',self.root);p.start();self.addCleanup(p.stop)
    def test_verified_copy_preserves_source_and_rejects_existing_destination(self):
        source=self.root/'old';source.mkdir();(source/'adapter.py').write_text('generated code')
        (source/'link').symlink_to('adapter.py')
        with patch.object(m.subprocess,'check_output',return_value=''):
            m.migrate(str(source))
        target=self.root/'runs/workspace'
        self.assertEqual(m.manifest(source),m.manifest(target))
        self.assertEqual((source/'adapter.py').read_text(),'generated code')
        self.assertEqual((target/'adapter.py').stat().st_mode&0o660,0o660)
        with self.assertRaisesRegex(ValueError,'destination exists'):m.migrate(str(source))
    def test_dangling_destination_symlink_is_preserved(self):
        (self.root/'runs').mkdir()
        target=self.root/'runs/workspace';target.symlink_to('missing')
        with self.assertRaisesRegex(ValueError,'destination exists'):m.migrate(str(self.root))
        self.assertTrue(target.is_symlink())

    def test_active_source_is_not_copied(self):
        source=self.root/'old';source.mkdir()
        import json
        mounts=json.dumps([{'Mounts':[{'Type':'bind','Source':str(source)}]}])
        with patch.object(m.subprocess,'check_output',side_effect=['active',mounts]):
            with self.assertRaisesRegex(ValueError,'active container'):m.migrate(str(source))
        self.assertFalse((self.root/'runs/workspace').exists())
    def test_missing_volume_does_not_create_an_empty_migration(self):
        import subprocess
        with patch.object(m.subprocess,'check_output',return_value=''),patch.object(m.subprocess,'run',side_effect=subprocess.CalledProcessError(1,'inspect')):
            with self.assertRaises(subprocess.CalledProcessError):m.migrate(volume='missing')
        self.assertFalse((self.root/'runs/workspace').exists())
