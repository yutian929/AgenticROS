"""LAN protocol tests: no ROS and no movement."""
import argparse
import importlib.util
import json
import io
from pathlib import Path
import tempfile
import subprocess
import os
import socket
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = load('registry', 'docker/server/registry.py')
client = load('lan_client', 'docker/clients/simple_mujoco/lan.py')
profile = load('lan_profile', 'docker/shared/lan_profile.py')
launcher = load('lan_launcher', 'scripts/lan.py')


class Membership(unittest.TestCase):
    def setUp(self):
        self.r = registry.Registry('fleet', 'instance-1', 73, '127.0.0.1', 12811,
                                   '01.02.03.04.05.06.07.08.09.0a.0b.0c', 'secret', ttl=6)
        self.body = dict(instance='fleet', robot_id='arm_a', session_id='a'*12)

    def test_two_clients_unique_namespaces_and_no_secret_in_roster(self):
        a = self.r.join(self.body, 'secret')
        b = self.r.join(dict(self.body, robot_id='arm_b', session_id='b'*12), 'secret')
        self.assertNotEqual(a['namespace'], b['namespace'])
        self.assertEqual(a['instance_id'], b['instance_id'])
        self.assertNotIn('lease', json.dumps(self.r.roster()))
        self.assertNotIn('secret', json.dumps(self.r.roster()))

    def test_wrong_instance_token_and_duplicate_rejected(self):
        for body, token in [(dict(self.body, instance='other'), 'secret'), (self.body, 'bad')]:
            with self.assertRaises(PermissionError):
                self.r.join(body, token)
        self.r.join(self.body, 'secret')
        with self.assertRaises(FileExistsError):
            self.r.join(self.body, 'secret')

    def test_expired_and_old_epoch_cannot_renew_or_displace_new_session(self):
        with patch.object(registry.time, 'monotonic', return_value=10):
            a = self.r.join(self.body, 'secret')
        with patch.object(registry.time, 'monotonic', return_value=17):
            with self.assertRaises(PermissionError):
                self.r.heartbeat(a, a['lease'])
            b = self.r.join(dict(self.body, session_id='b'*12), 'secret')
            self.assertNotEqual(a['namespace'], b['namespace'])
            with self.assertRaises(PermissionError):
                self.r.heartbeat(a, a['lease'])
            with self.assertRaises(PermissionError):
                self.r.heartbeat(dict(b, epoch='previous-server'), b['lease'])
            self.r.heartbeat(b, b['lease'])

    def test_http_enrollment_and_denial(self):
        http = registry.http_server(self.r, ('127.0.0.1', 0))
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        url = 'http://127.0.0.1:'+str(http.server_port)
        try:
            a = client.post(url+'/join', self.body, 'secret')
            self.assertEqual(a['robot_id'], 'arm_a')
            with self.assertRaises(urllib.error.HTTPError) as error:
                client.post(url+'/join', self.body, 'secret')
            self.assertEqual(error.exception.code, 409)
        finally:
            http.shutdown()
            http.server_close()
            worker.join()


class ClientSafety(unittest.TestCase):
    def test_deadline_latches_and_late_response_cannot_revive(self):
        with patch.object(client.time, 'monotonic', return_value=10):
            lease = client.Lease(6)
        with patch.object(client.time, 'monotonic', return_value=17):
            lease.renew(16, 6)
            self.assertFalse(lease.valid())
        with patch.object(client.time, 'monotonic', return_value=18):
            lease.renew(18, 6)
            self.assertFalse(lease.valid())

    def test_renewal_uses_send_time_and_preserves_validity(self):
        with patch.object(client.time, 'monotonic', return_value=10):
            lease = client.Lease(6)
        with patch.object(client.time, 'monotonic', return_value=12):
            lease.renew(11, 6)
            self.assertEqual(lease.deadline, 17)
            self.assertTrue(lease.valid())

    def test_snapshot_download_whitelist_and_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            c = client.Client.__new__(client.Client)
            c.root = Path(temp)
            c.session = 'a'*12
            c.secret = b'secret'
            c.host = '127.0.0.1'
            c.membership = dict(instance='fleet', instance_id='i', robot_id='arm_a', namespace='/robots/arm_a')
            # Port zero is only used by this isolated test.
            c.port = 0
            c.http = c.artifact_server()
            c.port = c.http.server_port
            worker = threading.Thread(target=c.http.serve_forever, daemon=True)
            worker.start()
            sid = c.session+'-'+'b'*12
            folder = c.root/'snapshots'/sid
            folder.mkdir(parents=True)
            (folder/'rgb.png').write_bytes(b'rgb fixture')
            (folder/'depth.npy').write_bytes(b'depth fixture')
            snap = dict(snapshot_id=sid, session_id=c.session, state={'frame':'base'}, calibration={},
                        rgb_path='local', depth_path='local', snapshot_json_path='local')
            try:
                result = c.export(snap)
                self.assertNotIn('rgb_path', result)
                self.assertEqual(result['state']['frame'], 'robots/arm_a/base')
                with urllib.request.urlopen(result['rgb_url']) as response:
                    data = response.read()
                self.assertEqual(client.hashlib.sha256(data).hexdigest(), result['artifacts']['rgb.png']['sha256'])
                for path in ['/../../opt/body/world.py', '/snapshots/'+sid+'/other.txt', '/snapshots/'+sid+'/rgb.png']:
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(f'http://127.0.0.1:{c.port}'+path)
            finally:
                c.http.shutdown()
                c.http.server_close()
                worker.join()


class Configuration(unittest.TestCase):
    def test_discovery_profiles_have_no_shared_memory_and_use_super_client(self):
        ns = {'f':'http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'}
        for server in (False, True):
            root = ET.fromstring(profile.profile('127.0.0.1', 12811, '01.02.03.04.05.06.07.08.09.0a.0b.0c', server))
            self.assertEqual(root.find('.//f:discoveryProtocol', ns).text, 'SERVER' if server else 'SUPER_CLIENT')
            self.assertEqual(root.find('.//f:useBuiltinTransports', ns).text, 'false')

    def test_lan_compose_keeps_credentials_and_sources_out_of_agent(self):
        config = dict(AGENTICROS_INSTANCE='test-fleet', AGENTICROS_INSTANCE_ID='instance-1',
                      AGENTICROS_DDS_HOST='127.0.0.1', AGENTICROS_REGISTRY_PORT=17447,
                      AGENTICROS_DDS_PORT=12811, ROS_DOMAIN_ID=73,
                      AGENTICROS_DDS_PREFIX='01.02.03.04.05.06.07.08.09.0a.0b.0c',
                      AGENTICROS_JOIN_TOKEN='secret')
        data = json.loads(subprocess.check_output(launcher.compose(config)+['config','--format','json'],
                          cwd=ROOT, env=launcher.environment(config), text=True))
        self.assertEqual(set(data['services']), {'instance'})
        agent = data['services']['instance']
        self.assertEqual(agent['network_mode'], 'host')
        self.assertEqual(agent['container_name'], 'agenticros-instance-test-fleet')
        mounts = {m['target']:m for m in agent['volumes']}
        self.assertEqual(set(mounts), {'/workspace','/home/agent/.codex','/config/codex.toml','/viewer-auth'})
        self.assertEqual(Path(mounts['/workspace']['source']), ROOT/'runs/instances/test-fleet/workspace')
        self.assertTrue(mounts['/config/codex.toml']['read_only'])
        self.assertEqual(mounts['/home/agent/.codex']['type'], 'volume')

    def test_deletion_preserves_lan_configuration_without_calling_docker(self):
        deletion = load('lan_delete_test', 'scripts/delete_instance.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'runs/instances/fleet/lan').mkdir(parents=True)
            with patch.object(deletion, 'ROOT', root), patch.object(deletion, 'docker') as docker:
                with self.assertRaisesRegex(RuntimeError, 'LAN'):
                    deletion.delete_instance('fleet')
                docker.assert_not_called()

    def test_invalid_names_and_addresses_rejected(self):
        for name in ('../bad', 'A', 'robot-a', '1arm'):
            with self.assertRaises(argparse.ArgumentTypeError):
                launcher.robot_name(name)
        for address in ('0.0.0.0', '224.0.0.1'):
            with self.assertRaises(argparse.ArgumentTypeError):
                launcher.ipv4(address)


class Startup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = patch.object(launcher, 'ROOT', Path(self.temp.name))
        self.root.start()
        self.addCleanup(self.root.stop)

    def server(self, name='fleet'):
        return launcher.server_config(name, '127.0.0.1')

    def args(self, **changes):
        values = dict(instance=None, join=None, robot_id='robot_a', server=None,
                      host=None, observation_port=None, stop=False)
        return argparse.Namespace(**dict(values, **changes))

    def test_new_instances_allocate_separate_resources_and_keep_saved_identity(self):
        a, b = self.server('account-a'), self.server('account-b')
        for key in ('AGENTICROS_INSTANCE_ID', 'AGENTICROS_JOIN_TOKEN', 'ROS_DOMAIN_ID',
                    'AGENTICROS_REGISTRY_PORT', 'AGENTICROS_DDS_PORT'):
            self.assertNotEqual(a[key], b[key])
        self.assertEqual(launcher.server_config('account-a'), a)
        self.assertEqual(launcher.server_file('account-a').stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, 'refusing'):
            launcher.server_config('account-a', '192.168.1.2')

    def test_busy_ports_and_reserved_domains_are_not_reused(self):
        with socket.socket() as sock:
            sock.bind(('0.0.0.0', 0))
            with self.assertRaises(ValueError):
                launcher.available_port(7447, [], explicit=sock.getsockname()[1])
        a = self.server()
        with self.assertRaises(ValueError):
            launcher.server_config('other', '127.0.0.1', domain=a['ROS_DOMAIN_ID'])
        with self.assertRaises(ValueError):
            launcher.server_config('legacy-conflict', '127.0.0.1', domain=42)

    def test_live_server_is_not_restarted_and_wrong_identity_is_not_replaced(self):
        config = self.server()
        with patch.object(launcher, 'health', return_value='ok'), patch.object(launcher, 'run') as run:
            self.assertEqual(launcher.ensure_server(config), 'ok')
            run.assert_not_called()
        with patch.object(launcher, 'health', side_effect=ValueError('identity mismatch')), patch.object(launcher, 'run') as run:
            with self.assertRaises(ValueError):
                launcher.ensure_server(config)
            run.assert_not_called()
        with patch.object(launcher, 'health', side_effect=[OSError(), 'ok']), patch.object(launcher, 'run') as run, patch.object(launcher, 'ensure_image'), patch.object(launcher, 'prepare_workspace'):
            launcher.ensure_server(config)
            self.assertIn('--no-recreate', run.call_args.args[1])
            self.assertIn('--no-build', run.call_args.args[1])
            self.assertIn('never', run.call_args.args[1])

    def test_existing_image_starts_offline_and_only_missing_image_is_built(self):
        config = self.server()
        for service, client in [('instance', False), ('client', True)]:
            with patch.object(launcher.subprocess, 'run', return_value=argparse.Namespace(returncode=0)), patch.object(launcher, 'run') as run:
                launcher.ensure_image(config, service, client=client)
                run.assert_not_called()
            with patch.object(launcher.subprocess, 'run', return_value=argparse.Namespace(returncode=1)), patch.object(launcher, 'run') as run:
                launcher.ensure_image(config, service, client=client)
                run.assert_called_once_with(config, ['build', service], client=client)

    def test_join_roundtrip_and_invalid_payloads(self):
        config = self.server()
        data = launcher.read_join_code(launcher.join_code(config))
        self.assertEqual(data['instance'], 'fleet')
        for raw in ('bad!', '', 'e30', 'W10', 'A'*4097):
            with self.assertRaises(ValueError):
                launcher.read_join_code(raw)
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            launcher.print_join(config)
        command = next(s for s in output.getvalue().splitlines() if s.startswith('bash '))
        self.assertIn('--join '+launcher.join_code(config)+' --robot-id robot_a', command)

    def test_join_creates_clients_reuses_config_and_refuses_rebinding(self):
        config = self.server()
        code = launcher.join_code(config)
        with patch.object(launcher, 'check_server_identity'), patch.object(launcher, 'detect_host', return_value='192.168.1.12'):
            a = launcher.client_config(self.args(join=code))
            b = launcher.client_config(self.args(join=code, robot_id='robot_b'))
            self.assertNotEqual(a['AGENTICROS_OBSERVATION_PORT'], b['AGENTICROS_OBSERVATION_PORT'])
            self.assertEqual(a['AGENTICROS_INSTANCE_ID'], config['AGENTICROS_INSTANCE_ID'])
            self.assertEqual(a['AGENTICROS_CLIENT_HOST'], '192.168.1.12')
            self.assertEqual(launcher.client_config(self.args(instance='fleet')), a)
            with self.assertRaises(ValueError):
                launcher.client_config(self.args(instance='other', join=code))
            changed = dict(config, AGENTICROS_JOIN_TOKEN='a'*64)
            with self.assertRaises(ValueError):
                launcher.client_config(self.args(join=launcher.join_code(changed)))
        # Stopping must work even when the server is unavailable.
        with patch.object(launcher, 'check_server_identity', side_effect=OSError('offline')):
            self.assertEqual(launcher.client_config(self.args(instance='fleet', stop=True)), a)
            with self.assertRaises(OSError):
                launcher.client_config(self.args(instance='fleet'))

    def test_wrong_registry_identity_does_not_save_client(self):
        config = self.server()
        response = io.StringIO(json.dumps(dict(instance='fleet', instance_id='another')))
        with patch.object(launcher.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'identity'):
                launcher.client_config(self.args(join=launcher.join_code(config)))
        self.assertFalse((launcher.folder('fleet')/'lan/client-robot_a.json').exists())

    def test_host_detection_requires_selection_for_multiple_interfaces(self):
        with patch.object(launcher, 'lan_addresses', return_value=[('eth0','192.168.1.2')]):
            self.assertEqual(launcher.detect_host(), '192.168.1.2')
        with patch.object(launcher, 'lan_addresses', return_value=[('eth0','192.168.1.2'), ('eth1','10.0.0.2')]), patch.object(launcher.sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'AGENTICROS_HOST'):
                launcher.detect_host()

    def test_named_shell_entry_automatically_routes_to_lan_and_preserves_arguments(self):
        fake = Path(self.temp.name)/'python3'
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        fake.chmod(0o700)
        result = subprocess.check_output(['bash', 'scripts/agenticros.sh', '--instance', 'new-account',
                                          'resume', '--last'], cwd=ROOT,
                                         env=dict(os.environ, PATH=str(fake.parent)+':'+os.environ['PATH']), text=True)
        self.assertEqual(result.splitlines()[1:], ['scripts/lan.py','agent','--instance','new-account','--','resume','--last'])


if __name__ == '__main__':
    unittest.main()
