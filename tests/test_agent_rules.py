"""Rule staging and installation, independent of ROS, credentials and live agents."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agent_rules', ROOT / 'docker/agent_instance/agent/rules.py')
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


class Rules(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.workspace = self.root / 'workspace'
        self.source.mkdir()
        self.workspace.mkdir()
        for name in ('AGENTS.md', 'contract.md', 'entrypoint.sh', 'rules.py'):
            (self.source / name).write_text('old ' + name)
        self.put('skills/setup-body/SKILL.md', 'old skill')
        self.put('agents/arm-executor.toml', 'old role')

    def put(self, name, content):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_stage_is_snapshot_and_never_changes_running_rules(self):
        (self.workspace / 'AGENTS.md').write_text('active session')
        first = rules.stage(self.source, self.workspace)
        self.assertEqual(first, rules.stage(self.source, self.workspace))
        self.put('AGENTS.md', 'new instructions')
        second = rules.stage(self.source, self.workspace)
        self.assertNotEqual(first, second)
        self.assertEqual((first / 'AGENTS.md').read_text(), 'old AGENTS.md')
        self.assertEqual((self.workspace / 'AGENTS.md').read_text(), 'active session')
        rules.install(second, self.workspace)
        self.assertEqual((self.workspace / 'AGENTS.md').read_text(), 'new instructions')

    def test_update_add_remove_and_preserve_generated_files(self):
        rules.install(self.source, self.workspace)
        custom = self.workspace / '.agents/skills/custom/SKILL.md'
        custom.parent.mkdir()
        custom.write_text('generated custom skill')
        adapter = self.workspace / 'adapter.py'
        adapter.write_text('generated adapter')
        (self.source / 'agents/arm-executor.toml').unlink()
        self.put('skills/setup-body/data/example.json', '{}')
        self.put('contract.md', 'new contract')
        rules.install(self.source, self.workspace)
        self.assertFalse((self.workspace / '.codex/agents/arm-executor.toml').exists())
        self.assertEqual((self.workspace / '.agents/skills/setup-body/data/example.json').read_text(), '{}')
        self.assertEqual((self.workspace / 'contract.md').read_text(), 'new contract')
        self.assertEqual(custom.read_text(), 'generated custom skill')
        self.assertEqual(adapter.read_text(), 'generated adapter')

    def test_removed_but_locally_edited_rule_is_preserved(self):
        rules.install(self.source, self.workspace)
        target = self.workspace / '.codex/agents/arm-executor.toml'
        target.write_text('local customization')
        (self.source / 'agents/arm-executor.toml').unlink()
        rules.install(self.source, self.workspace)
        self.assertEqual(target.read_text(), 'local customization')
        self.assertNotIn('.codex/agents/arm-executor.toml',
                         json.loads((self.workspace / '.agenticros/managed-rules.json').read_text()))

    def test_image_defaults_do_not_downgrade_rules_or_manifest(self):
        rules.install(self.source, self.workspace)
        manifest = (self.workspace / '.agenticros/managed-rules.json').read_bytes()
        self.put('AGENTS.md', 'stale image rules')
        (self.workspace / 'contract.md').unlink()
        rules.install(self.source, self.workspace, defaults=True)
        self.assertEqual((self.workspace / 'AGENTS.md').read_text(), 'old AGENTS.md')
        self.assertTrue((self.workspace / 'contract.md').is_file())
        self.assertEqual((self.workspace / '.agenticros/managed-rules.json').read_bytes(), manifest)

    def test_source_and_destination_symlinks_are_rejected_before_writes(self):
        outside = self.root / 'outside'
        outside.write_text('untouched')
        (self.workspace / 'contract.md').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            rules.install(self.source, self.workspace)
        self.assertFalse((self.workspace / 'AGENTS.md').exists())
        self.assertEqual(outside.read_text(), 'untouched')
        (self.source / 'skills/link').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            rules.stage(self.source, self.workspace)

    def test_manifest_cannot_delete_files_outside_owned_paths(self):
        state = self.workspace / '.agenticros'
        state.mkdir()
        (state / 'managed-rules.json').write_text(json.dumps({'../outside': 'hash'}))
        with self.assertRaisesRegex(ValueError, 'manifest'):
            rules.install(self.source, self.workspace)
        self.assertFalse((self.workspace / 'AGENTS.md').exists())

    def test_instance_workspaces_stay_independent(self):
        other = self.root / 'other'
        other.mkdir()
        rules.install(self.source, other)
        self.put('AGENTS.md', 'first instance update')
        rules.install(self.source, self.workspace)
        self.assertEqual((other / 'AGENTS.md').read_text(), 'old AGENTS.md')

    def test_staged_file_tampering_is_detected(self):
        staged = rules.stage(self.source, self.workspace)
        (staged / 'AGENTS.md').write_text('modified snapshot')
        with self.assertRaisesRegex(ValueError, 'modified'):
            rules.stage(self.source, self.workspace)

    def test_launcher_preserves_codex_arguments(self):
        scripts=self.root/'scripts';scripts.mkdir()
        shutil.copyfile(ROOT/'agent.sh',self.root/'agent.sh')
        bins=self.root/'bin';bins.mkdir();calls=self.root/'calls.json'
        fake=bins/'python3'
        fake.write_text('#!'+sys.executable+'\nimport json,os,sys\nopen(os.environ["CALLS"],"w").write(json.dumps(sys.argv[1:]))\n')
        fake.chmod(0o755)
        subprocess.run(['bash',str(self.root/'agent.sh'),'agent','resume','--last','argument with spaces'],
            env=dict(os.environ,PATH=str(bins)+':'+os.environ['PATH'],CALLS=str(calls)),check=True)
        self.assertEqual(json.loads(calls.read_text()),['scripts/runtime.py','agent','--','resume','--last','argument with spaces'])

if __name__=='__main__':unittest.main()
