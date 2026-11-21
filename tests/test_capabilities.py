"""能力验收的边界测试；不启动 ROS 或执行生成命令。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from check_capabilities import check_live, load_manifest


class CapabilityChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        (self.workspace / 'capabilities').mkdir()
        (self.workspace / 'evidence').mkdir()
        (self.workspace / 'evidence/status.txt').write_text('measured status from live service')
        self.manifest = dict(schema_version=1, environment={'identity': 'discovered test device'},
            generated_nodes=[], limitations=[], capabilities=[dict(name='status', kind='service',
            endpoint='/example/status', interface_type='std_srvs/srv/Trigger', origin='environment',
            status='verified', description='读取当前状态', invocation='说明模板，不应由验收执行',
            evidence=['evidence/status.txt'])])

    def load(self):
        (self.workspace / 'capabilities/manifest.json').write_text(json.dumps(self.manifest))
        return load_manifest(self.workspace)

    def test_existing_interface_needs_no_generated_code(self):
        manifest = self.load()
        with patch('check_capabilities.ros_list', return_value='/example/status [std_srvs/srv/Trigger]\n') as query:
            report = check_live(manifest, 'test-agent')
        self.assertEqual(report['verified_interfaces'], 1)
        self.assertEqual(report['generated_nodes'], 0)
        query.assert_called_once_with('test-agent', 'service')

    def test_missing_or_empty_evidence_rejected(self):
        for value in ([], ['evidence/missing.txt']):
            with self.subTest(value=value):
                self.manifest['capabilities'][0]['evidence'] = value
                with self.assertRaises(ValueError):
                    self.load()
        self.manifest['capabilities'][0]['evidence'] = ['evidence/status.txt']
        (self.workspace / 'evidence/status.txt').write_text('')
        with self.assertRaises(ValueError):
            self.load()

    def test_evidence_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as outside:
            file = Path(outside) / 'private.txt'
            file.write_text('not an evidence file')
            (self.workspace / 'evidence/link').symlink_to(file)
            for value in (str(file), '../private.txt', 'evidence/link'):
                with self.subTest(value=value):
                    self.manifest['capabilities'][0]['evidence'] = [value]
                    with self.assertRaises(ValueError):
                        self.load()

    def test_missing_or_wrong_live_interface_rejected(self):
        manifest = self.load()
        for graph in ('', '/example/status [std_srvs/srv/Empty]\n'):
            with self.subTest(graph=graph), patch('check_capabilities.ros_list', return_value=graph):
                with self.assertRaises(ValueError):
                    check_live(manifest, 'test-agent')

    def test_unverified_interface_not_required_online(self):
        item = copy.deepcopy(self.manifest['capabilities'][0])
        item.update(name='future', endpoint='/future', status='unverified', evidence=[])
        self.manifest['capabilities'].append(item)
        with patch('check_capabilities.ros_list', return_value='/example/status [std_srvs/srv/Trigger]\n'):
            self.assertEqual(check_live(self.load(), 'test-agent')['verified_interfaces'], 1)
        self.manifest['capabilities'][0]['status'] = 'unverified'
        with self.assertRaises(ValueError):
            self.load()

    def test_generated_node_must_be_present_once(self):
        self.manifest['capabilities'][0]['origin'] = 'generated'
        with self.assertRaises(ValueError):
            self.load()
        (self.workspace / 'ros_ws/src/example').mkdir(parents=True)
        (self.workspace / 'evidence/node.log').touch()
        self.manifest['generated_nodes'] = [dict(node_name='/example/node', package='example',
            source_path='ros_ws/src/example', start_command='do not execute',
            stop_command='do not execute', health_command='do not execute', log_path='evidence/node.log')]
        manifest = self.load()
        for node_list in ('', '/example/node\n/example/node\n', '/example/node\n'):
            with self.subTest(nodes=node_list), patch('check_capabilities.ros_list',
                    side_effect=['/example/status [std_srvs/srv/Trigger]\n', node_list]) as query:
                if node_list == '/example/node\n':
                    self.assertEqual(check_live(manifest, 'test-agent')['generated_nodes'], 1)
                else:
                    with self.assertRaises(ValueError):
                        check_live(manifest, 'test-agent')
                self.assertEqual([call.args[1] for call in query.call_args_list], ['service', 'node'])

    def test_all_interface_kinds_and_multiple_topic_types(self):
        for kind, interface in [('topic', 'sensor_msgs/msg/Image'), ('action', 'example/action/Move')]:
            item = copy.deepcopy(self.manifest['capabilities'][0])
            item.update(name=kind, kind=kind, endpoint='/' + kind, interface_type=interface)
            self.manifest['capabilities'].append(item)
        graphs = dict(service='/example/status [std_srvs/srv/Trigger]\n',
                      topic='/topic [sensor_msgs/msg/Image, std_msgs/msg/String]\n',
                      action='/action [example/action/Move]\n')
        with patch('check_capabilities.ros_list', side_effect=lambda container, kind: graphs[kind]):
            self.assertEqual(check_live(self.load(), 'test-agent')['verified_interfaces'], 3)
        self.manifest['capabilities'][-1]['interface_type'] = 'example/srv/Move'
        with self.assertRaises(ValueError):
            self.load()


if __name__ == '__main__':
    unittest.main()
