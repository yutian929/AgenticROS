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


client = load('lan_client', 'docker/shared/robot_network.py')
profile = load('lan_profile', 'docker/shared/lan_profile.py')
launcher = load('lan_launcher', 'scripts/lan.py')


class SensorArtifacts(unittest.TestCase):
    def test_robot_identity_starts_offline_with_new_session(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,dict(
                AGENTICROS_ROBOT_ID='arm_a',AGENTICROS_CLIENT_HOST='127.0.0.1',
                AGENTICROS_OBSERVATION_PORT='0',OBSERVATION_ROOT=temp)), \
                patch.object(urllib.request,'urlopen',side_effect=AssertionError('no registration allowed')):
            first=client.RobotNetwork()
            try:
                self.assertEqual(first.identity(),dict(robot_id='arm_a',namespace='/robots/arm_a'))
                old=first.session
            finally:first.close()
            restarted=client.RobotNetwork()
            try:self.assertNotEqual(old,restarted.session)
            finally:restarted.close()

    def test_snapshot_download_whitelist_and_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            c = client.RobotNetwork.__new__(client.RobotNetwork)
            c.root = Path(temp).resolve()
            c.session = 'a'*12
            c.secret = b'secret'
            c.host = '127.0.0.1'
            c.robot_id = 'arm_a'
            c.namespace = '/robots/arm_a'
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

    def test_tcp_uses_volatile_fixed_discovery_and_bounded_sync(self):
        ns={'f':'http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'}
        for server in (False,True):
            root=ET.fromstring(profile.profile('192.168.1.10',11811,launcher.PREFIX,server,'tcp'))
            self.assertEqual(root.find('.//f:discoveryProtocol',ns).text,'SERVER' if server else 'SUPER_CLIENT')
            self.assertEqual(root.find('.//f:clientAnnouncementPeriod/f:nanosec',ns).text,'100000000')
            self.assertEqual(root.find('.//f:max_logical_port',ns).text,'0')
            if not server:
                self.assertEqual(ET.tostring(root.find('.//f:initialPeersList/f:locator',ns)),
                                 ET.tostring(root.find('.//f:RemoteServer/f:metatrafficUnicastLocatorList/f:locator',ns)))
                peers=root.findall('.//f:initialPeersList/f:locator',ns)
                self.assertEqual(len(peers),3)
                self.assertEqual(peers[2].find("f:tcpv4/f:address",ns).text,"127.0.0.1")
                self.assertEqual(peers[1].find('f:tcpv4/f:wan_address',ns).text,'192.168.1.10')
            else:
                self.assertEqual(len(root.findall('.//f:builtin/f:metatrafficUnicastLocatorList/f:locator',ns)),1)

    def test_remote_robot_does_not_use_loopback_discovery_peer(self):
        ns={'f':'http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'}
        root=ET.fromstring(profile.profile('192.168.1.10',11811,launcher.PREFIX,False,'tcp','192.168.1.20'))
        self.assertEqual(len(root.findall('.//f:initialPeersList/f:locator',ns)),2)
        self.assertNotIn('127.0.0.1',ET.tostring(root).decode())

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
        self.assertNotIn('AGENTICROS_REGISTRY_PORT',a)
        self.assertNotIn('AGENTICROS_JOIN_TOKEN',a)
        with self.assertRaisesRegex(ValueError,'saved configuration'):launcher.server_config('192.168.1.1')
    def test_duplicate_ports_and_macos_udp_rejected(self):
        with self.assertRaises(ValueError):launcher.server_config('127.0.0.1',discovery_port=8080)
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
        a=argparse.Namespace(robot_id='arm_a',server='127.0.0.1',domain_id=None,discovery_port=None,transport=None,host='127.0.0.1',observation_port=None,environment='aloha_tabletop',stop=False)
        with patch.object(urllib.request,'urlopen') as http:
            c=launcher.client_config(a);self.assertEqual(launcher.client_config(a),c);http.assert_not_called()
        a.server='127.0.0.2'
        with self.assertRaisesRegex(ValueError,'binding'):launcher.client_config(a)
    def test_viewer_password_cli_uses_saved_server_without_runtime_dispatch(self):
        config=launcher.server_config('127.0.0.1')
        with patch.object(launcher.sys,'argv',['lan.py','viewer-password']),patch.object(launcher,'run') as run:
            launcher.main()
        run.assert_called_once_with(config,['exec','-T','server','cat','/viewer-auth/password'])

    def test_rules_staged_before_attach_with_lock(self):
        config=launcher.server_config('127.0.0.1')
        with patch.object(launcher,'stage_agent_rules',return_value='/workspace/staged/entrypoint.sh'):
            cmd=launcher.agent_command(config,['agenticros','start'],['resume','--last'],False)
        self.assertLess(cmd.index('attach'),cmd.index('/workspace/staged/entrypoint.sh'))
        self.assertEqual(cmd[-2:],['resume','--last'])

if __name__=='__main__':unittest.main()
