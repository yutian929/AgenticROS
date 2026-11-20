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


registry = load('registry', 'docker/agent_instance/registry/registry.py')
client = load('lan_client', 'docker/shared/registration.py')
profile = load('lan_profile', 'docker/shared/lan_profile.py')
launcher = load('lan_launcher', 'scripts/lan.py')


class Membership(unittest.TestCase):
    def setUp(self):
        self.r=registry.Registry(42,'127.0.0.1',11811,launcher.PREFIX)
        self.body=dict(robot_id='arm_a',session_id='a'*12)
    def test_presence_idempotent_and_duplicate_robot_rejected(self):
        a=self.r.presence(self.body)
        self.assertEqual(self.r.presence(self.body)['lease'],a['lease'])
        with self.assertRaises(FileExistsError):self.r.presence(dict(self.body,session_id='b'*12))
        self.assertEqual(a['namespace'],'/robots/arm_a')
        self.assertNotIn('lease"',json.dumps(self.r.roster()))
        self.assertNotIn('connected',json.dumps(self.r.roster()))
    def test_old_session_and_epoch_cannot_revive_or_displace(self):
        with patch.object(registry.time,'monotonic',return_value=10):a=self.r.presence(self.body)
        with patch.object(registry.time,'monotonic',return_value=17):
            with self.assertRaises(PermissionError):self.r.presence(a,a['lease'])
            with self.assertRaises(PermissionError):self.r.presence(self.body)
            b=self.r.presence(dict(self.body,session_id='b'*12))
            self.assertEqual(a['namespace'],b['namespace'])
            with self.assertRaises(PermissionError):self.r.presence(a,a['lease'])
            with self.assertRaises(PermissionError):self.r.presence(dict(b,epoch='old'),b['lease'])
            self.r.presence(b,b['lease'])
    def test_lost_control_does_not_hide_live_process_or_reenable_it(self):
        with patch.object(registry.time,'monotonic',return_value=10):a=self.r.presence(self.body)
        with patch.object(registry.time,'monotonic',return_value=17):
            reply=self.r.presence(dict(a,control_lease_lost=True),a['lease'])
            self.assertEqual(reply['lease_seconds'],0)
            robot=self.r.roster()['robots'][0]
            self.assertTrue(robot['process_alive']);self.assertFalse(robot['control_lease_valid'])
            with self.assertRaises(PermissionError):self.r.presence(a,a['lease'])

    def test_http_presence(self):
        http=registry.http_server(self.r,('127.0.0.1',0));worker=threading.Thread(target=http.serve_forever);worker.start()
        try:
            url='http://127.0.0.1:'+str(http.server_port)
            a=client.post(url+'/presence',self.body,'');self.assertEqual(a['robot_id'],'arm_a')
            client.post(url+'/presence',a,a['lease'])
            with self.assertRaises(urllib.error.HTTPError):client.post(url+'/join',self.body,'')
        finally:http.shutdown();http.server_close();worker.join()


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
            c.root = Path(temp).resolve()
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

    def test_invalid_names_and_addresses_rejected(self):
        for name in ('../bad', 'A', 'robot-a', '1arm'):
            with self.assertRaises(argparse.ArgumentTypeError):
                launcher.robot_name(name)
        for address in ('0.0.0.0', '224.0.0.1'):
            with self.assertRaises(argparse.ArgumentTypeError):
                launcher.ipv4(address)


class Startup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(launcher,'ROOT',Path(self.temp.name));p.start();self.addCleanup(p.stop)
    def test_one_fixed_domain_and_configuration(self):
        a=launcher.server_config('127.0.0.1');self.assertEqual(a['ROS_DOMAIN_ID'],42)
        self.assertEqual(launcher.server_config(),a)
        self.assertEqual(a['AGENTICROS_REGISTRY_PORT'],7447)
        self.assertNotIn('AGENTICROS_JOIN_TOKEN',a)
        with self.assertRaisesRegex(ValueError,'saved configuration'):launcher.server_config('192.168.1.1')
    def test_duplicate_ports_and_macos_udp_rejected(self):
        with self.assertRaises(ValueError):launcher.server_config('127.0.0.1',registry_port=11811)
        with patch.object(launcher.sys,'platform','darwin'):
            with self.assertRaises(ValueError):launcher.server_config('127.0.0.1',transport='udp')
    def test_live_server_not_replaced(self):
        config=launcher.server_config('127.0.0.1')
        with patch.object(launcher,'health',return_value='ok'),patch.object(launcher,'run') as run:
            self.assertEqual(launcher.ensure_server(config),'ok');run.assert_not_called()
        with patch.object(launcher,'health',side_effect=ValueError('mismatch')),patch.object(launcher,'run') as run:
            with self.assertRaises(ValueError):launcher.ensure_server(config)
            run.assert_not_called()
    def test_robot_config_no_join_or_registration_and_retained_binding(self):
        a=argparse.Namespace(robot_id='arm_a',server='http://127.0.0.1:7447',host='127.0.0.1',observation_port=None,environment='aloha_tabletop',stop=False)
        with patch.object(launcher.urllib.request,'urlopen') as http:
            c=launcher.client_config(a);self.assertEqual(launcher.client_config(a),c);http.assert_not_called()
        a.server='http://127.0.0.2:7447'
        with self.assertRaisesRegex(ValueError,'binding'):launcher.client_config(a)
    def test_viewer_password_cli_uses_saved_server_without_runtime_dispatch(self):
        config=launcher.server_config('127.0.0.1')
        with patch.object(launcher.sys,'argv',['lan.py','viewer-password']),patch.object(launcher,'run') as run:
            launcher.main()
        run.assert_called_once_with(config,['exec','-T','server','cat','/viewer-auth/password'])

    def test_server_url_canonicalizes_default_port(self):
        self.assertEqual(launcher.server_url('http://127.0.0.1/'),'http://127.0.0.1:7447')

    def test_rules_staged_before_attach_with_lock(self):
        config=launcher.server_config('127.0.0.1')
        with patch.object(launcher,'stage_agent_rules',return_value='/workspace/staged/entrypoint.sh'):
            cmd=launcher.agent_command(config,['agenticros','start'],['resume','--last'],False)
        self.assertLess(cmd.index('attach'),cmd.index('/workspace/staged/entrypoint.sh'))
        self.assertEqual(cmd[-2:],['resume','--last'])

if __name__=='__main__':unittest.main()
