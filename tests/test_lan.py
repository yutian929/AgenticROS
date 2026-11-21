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


client = load('lan_client', 'docker/robot_instances/common/robot_identity.py')
launcher = load('lan_launcher', 'scripts/runtime.py')


class Identity(unittest.TestCase):
    def test_robot_identity_starts_offline_with_new_session(self):
        with patch.dict(os.environ, AGENTICROS_ROBOT_ID='arm_a'), patch.object(socket, 'socket', side_effect=AssertionError('no network required')):
            first=client.RobotIdentity()
            self.assertEqual(first.identity(),dict(robot_id='arm_a',namespace='/robot/arm_a'))
            self.assertNotEqual(first.session,client.RobotIdentity().session)


class Configuration(unittest.TestCase):
    def test_invalid_names_and_addresses_rejected(self):
        for name in ('../bad', 'A', 'robot-a', '1arm'):
            with self.assertRaises(argparse.ArgumentTypeError):
                launcher.robot_name(name)



class Startup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(launcher,'ROOT',Path(self.temp.name));p.start();self.addCleanup(p.stop)
        for name in ('cartesian_gripper','aloha_tabletop'):
            path=launcher.ROOT/'docker/robot_instances'/name/'Dockerfile'
            path.parent.mkdir(parents=True);path.write_text('FROM scratch\n')
    def test_one_fixed_domain_and_configuration(self):
        a=launcher.server_config();self.assertEqual(a['ROS_DOMAIN_ID'],42)
        self.assertEqual(launcher.server_config(),a)
        self.assertNotIn('AGENTICROS_REGISTRY_PORT',a)
        self.assertNotIn('AGENTICROS_JOIN_TOKEN',a)
        self.assertEqual(launcher.server_config(43)['ROS_DOMAIN_ID'],43)
        with patch.dict(os.environ, ROS_DOMAIN_ID='99'):
            self.assertEqual(launcher.server_config()['ROS_DOMAIN_ID'],42)
    def test_domains_isolate_compose_resources_and_viewer_config(self):
        rendered=[]
        for domain in (42,43):
            config=launcher.server_config(domain)
            viewer=launcher.server_config(domain,'viewer')
            viewer['AGENTICROS_VIEWER_PORT']=18000+domain
            launcher.server_file(domain,'viewer').write_text(json.dumps(viewer))
            self.assertNotEqual(launcher.server_config(domain)['AGENTICROS_VIEWER_PORT'],
                                launcher.server_config(domain,'viewer')['AGENTICROS_VIEWER_PORT'])
            with patch.dict(os.environ, ROS_DOMAIN_ID='99', AGENTICROS_CODEX_VOLUME='wrong'):
                env=launcher.environment(config)
            data=json.loads(subprocess.check_output(launcher.compose(config)+['--profile','viewer','config','--format','json'],cwd=ROOT,env=env,text=True))
            rendered.append(data)
            self.assertEqual(data['services']['server']['environment']['ROS_DOMAIN_ID'],str(domain))
            mount=next(m for m in data['services']['server']['volumes'] if m['target']=='/workspace')
            self.assertIn('/runs/'+str(domain)+'/agent_instance/agent/workspace',mount['source'])
        self.assertNotEqual(rendered[0]['name'],rendered[1]['name'])
        for volume in ('codex-home','viewer-auth'):
            self.assertNotEqual(rendered[0]['volumes'][volume]['name'],rendered[1]['volumes'][volume]['name'])
        for port in ('AGENTICROS_VIEWER_PORT','AGENTICROS_VNC_PORT'):
            self.assertNotEqual(rendered[0]['services']['viewer']['environment'][port],rendered[1]['services']['viewer']['environment'][port])

    def test_new_type_is_discovered_without_launcher_changes(self):
        root=launcher.ROOT/'docker/robot_instances'
        for name in ('new_robot_with_underscores','common','robot_docs','Bad-Type'):
            path=root/name;path.mkdir(exist_ok=True);(path/'Dockerfile').write_text('FROM scratch')
        (root/'empty_type').mkdir()
        self.assertEqual(launcher.robot_types(),['aloha_tabletop','cartesian_gripper','new_robot_with_underscores'])
        args=argparse.Namespace(robot_id=None,domain_id=43,environment='new_robot_with_underscores',stop=False)
        config=launcher.client_config(args)
        identity=args.robot_id
        self.assertRegex(identity,r'^new_robot_with_underscores_[0-9a-f]{8}$')
        args.environment=None
        self.assertEqual(launcher.client_config(args),config)
        self.assertEqual(launcher.client_file(identity,43).parent.parent.name,'new_robot_with_underscores')
        # Stop still works from saved metadata if the source directory is removed.
        (root/'new_robot_with_underscores/Dockerfile').unlink()
        args.stop=True
        self.assertEqual(launcher.client_config(args),config)

    def test_short_robot_id_collision_retries_without_reusing_instance(self):
        args = argparse.Namespace(robot_id=None, domain_id=42, environment='aloha_tabletop', stop=False)
        with patch.object(launcher.secrets, 'token_hex', return_value='1234abcd'):
            first = launcher.client_config(args)
        args.robot_id = None
        with patch.object(launcher.secrets, 'token_hex', side_effect=['1234abcd', 'abcd5678']):
            second = launcher.client_config(args)
        self.assertEqual(second['AGENTICROS_ROBOT_ID'], 'aloha_tabletop_abcd5678')
        self.assertEqual(json.loads(launcher.client_file(first['AGENTICROS_ROBOT_ID'],42).read_text()), first)

    def test_unknown_type_and_unknown_id_do_not_create_configuration(self):
        for kind in (None,'missing','../escape'):
            args=argparse.Namespace(robot_id=None,domain_id=42,environment=kind,stop=False)
            with self.assertRaises(ValueError):launcher.client_config(args)
        with self.assertRaises(ValueError):launcher.client_file('aloha_tabletop_unknown')
        self.assertFalse((launcher.ROOT/'runs').exists())

    def test_robot_image_requires_its_own_healthcheck(self):
        config=dict(AGENTICROS_ENVIRONMENT='anything')
        with patch.object(launcher.subprocess,'run',return_value=argparse.Namespace(returncode=0)):
            for health in (None,{},dict(Test=['NONE'])):
                with patch.object(launcher.subprocess,'check_output',return_value=json.dumps(health)):
                    with self.assertRaisesRegex(ValueError,'HEALTHCHECK'):launcher.ensure_image(config,'client',True)
            with patch.object(launcher.subprocess,'check_output',return_value=json.dumps(dict(Test=['CMD','/custom-ready']))):
                self.assertEqual(launcher.ensure_image(config,'client',True),'agenticros-anything:single')

    def test_invalid_domain_rejected(self):
        with self.assertRaises(ValueError): launcher.server_config(102)
    def test_robot_type_generates_distinct_stable_ids(self):
        ids=[]
        for _ in range(2):
            args=argparse.Namespace(robot_id=None,environment='cartesian_gripper',domain_id=55,stop=False)
            config=launcher.client_config(args)
            ids.append(config['AGENTICROS_ROBOT_ID'])
            self.assertRegex(ids[-1],r'^cartesian_gripper_[0-9a-f]{8}$')
            self.assertEqual(launcher.client_config(args),config)
        self.assertNotEqual(*ids)

    def test_robot_needs_only_domain(self):
        a=argparse.Namespace(robot_id='arm_a',domain_id=55,environment='cartesian_gripper',stop=False)
        c=launcher.client_config(a)
        self.assertEqual(c['ROS_DOMAIN_ID'],55)
        self.assertFalse(any(k.startswith('AGENTICROS_DDS_') for k in c))
    def test_live_server_not_replaced(self):
        config=launcher.server_config()
        with patch.object(launcher,'health',return_value='ok'),patch.object(launcher,'run') as run:
            self.assertEqual(launcher.ensure_server(config),'ok');run.assert_not_called()
        with patch.object(launcher,'health',side_effect=ValueError('mismatch')),patch.object(launcher,'run') as run:
            with self.assertRaises(ValueError):launcher.ensure_server(config)
            run.assert_not_called()
    def test_robot_config_no_join_or_registration_and_retained_binding(self):
        a=argparse.Namespace(robot_id='arm_a',server='127.0.0.1',domain_id=None,discovery_port=None,transport=None,environment='aloha_tabletop',stop=False)
        with patch.object(urllib.request,'urlopen') as http:
            c=launcher.client_config(a);self.assertEqual(launcher.client_config(a),c);http.assert_not_called()
        a.domain_id=43
        other=launcher.client_config(a)
        self.assertEqual(other['ROS_DOMAIN_ID'],43)
        self.assertNotEqual(launcher.client_file('arm_a',42),launcher.client_file('arm_a',43))
    def test_viewer_password_cli_uses_saved_server_without_runtime_dispatch(self):
        config=launcher.server_config()
        with patch.object(launcher.sys,'argv',['runtime.py','viewer-password']),patch.object(launcher,'run') as run:
            launcher.main()
        run.assert_called_once_with(config,['exec','-T','viewer','cat','/viewer-auth/password'])

    def test_agent_domain_starts_server_and_preserves_codex_arguments(self):
        with patch.object(launcher.sys,'argv',['runtime.py','agent','--domain-id','55','--','resume','--last']), \
                patch.object(launcher,'ensure_server') as ensure, \
                patch.object(launcher,'agent_command',return_value=['docker','exec','fixture']) as command, \
                patch.object(launcher.os,'chdir'), patch.object(launcher.os,'execvpe') as execute:
            launcher.main()
        self.assertEqual(ensure.call_args.args[0]['ROS_DOMAIN_ID'],55)
        self.assertEqual(command.call_args.args[2],['resume','--last'])
        self.assertEqual(execute.call_args.args[2]['ROS_DOMAIN_ID'],'55')

    def test_agent_shell_forwards_domain_separately(self):
        import sys
        stub=Path(self.temp.name)/'python3'
        stub.write_text('#!'+sys.executable+'\nimport json,sys; print(json.dumps(sys.argv[1:]))\n')
        stub.chmod(0o755)
        env=dict(os.environ,PATH=self.temp.name+os.pathsep+os.environ['PATH'])
        for arguments, expected in [(['agent'],['--']),(['agent','--domain-id','55','resume','--last'],['--domain-id','55','--','resume','--last'])]:
            output=subprocess.check_output(['bash',str(ROOT/'agent.sh'),*arguments],env=env,text=True)
            self.assertEqual(json.loads(output),['scripts/runtime.py','agent',*expected])
        self.assertNotEqual(subprocess.run(['bash',str(ROOT/'agent.sh'),'agent','--domain-id'],env=env,capture_output=True).returncode,0)
        output=subprocess.check_output(['bash',str(ROOT/'agent.sh'),'viewer','--domain-id','55'],env=env,text=True)
        self.assertEqual(json.loads(output),['scripts/runtime.py','viewer','--domain-id','55'])

    def test_viewer_starts_without_agent(self):
        with patch.object(launcher.sys,'argv',['runtime.py','viewer','--domain-id','55']), \
                patch.object(launcher,'ensure_image') as image,patch.object(launcher,'run') as run, \
                patch.object(launcher,'ensure_server') as agent, \
                patch.object(launcher.subprocess,'check_output',return_value='test-pass\n') as password, \
                patch('builtins.print') as output:
            launcher.main()
        output.assert_any_call('Viewer: http://127.0.0.1:8093/vnc.html')
        output.assert_any_call('Password: test-pass')
        self.assertEqual(password.call_args.args[0][-4:], ['-T','viewer','cat','/viewer-auth/password'])
        agent.assert_not_called()
        self.assertEqual(image.call_args.args[1],'viewer')
        self.assertEqual(run.call_args.args[0]['ROS_DOMAIN_ID'],55)
        self.assertEqual(run.call_args.args[1][-1],'viewer')

    def test_rules_staged_before_attach_with_lock(self):
        config=launcher.server_config()
        with patch.object(launcher,'stage_agent_rules',return_value='/workspace/staged/entrypoint.sh'):
            cmd=launcher.agent_command(config,['agenticros','start'],['resume','--last'],False)
        self.assertLess(cmd.index('attach'),cmd.index('/workspace/staged/entrypoint.sh'))
        self.assertEqual(cmd[-2:],['resume','--last'])

if __name__=='__main__':unittest.main()
