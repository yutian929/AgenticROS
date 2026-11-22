"""SAM3 unit/lifecycle checks; no ROS, weights or GPU required."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

core = load('sam3_core', 'docker/agent_instance/sam3/core.py')
launcher = load('sam3_launcher', 'scripts/runtime.py')
setup = load('sam3_setup', 'scripts/setup_sam3.py')


def request():
    return dict(session_id='session', image_topic='/image', frame_policy='LATEST',
                text_prompt='cup', max_image_age_sec=2., image_timeout_sec=3.,
                score_threshold=.5, max_instances=8)


class Validation(unittest.TestCase):
    def test_rejects_stale_session_and_invalid_limits(self):
        core.validate(request(), 'session')
        for key, value in [('session_id','old'), ('score_threshold',math.nan),
                           ('image_timeout_sec',math.inf), ('max_image_age_sec',0.),
                           ('max_instances',33), ('text_prompt',' '), ('frame_policy','guess')]:
            with self.subTest(key=key), self.assertRaises(core.RequestError):
                core.validate(dict(request(), **{key:value}), 'session')

    def test_capture_time_not_receive_time_controls_freshness(self):
        core.check_age(9_000_000_000, 10_000_000_000, 2.)
        for stamp in (0, 1_000_000_000, 11_000_000_000):
            with self.assertRaises(core.RequestError):
                core.check_age(stamp, 10_000_000_000, 2.)

    def test_records_persist_and_uuid_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            records = core.Records(directory, 'session')
            folder = records.create('uuid', request())
            records.finish('uuid', dict(code='CANCELLED', success=False))
            with self.assertRaises(FileExistsError):
                records.create('uuid', {'replace':True})
            self.assertEqual(json.loads((folder/'request.json').read_text()), request())
            self.assertEqual(json.loads((folder/'result.json').read_text())['code'], 'CANCELLED')
            self.assertEqual(len((folder/'events.jsonl').read_text().splitlines()), 2)

    def test_model_worker_is_eager_and_reused_then_reports_exit(self):
        import subprocess
        import sys
        original = subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory)/'worker.py'
            script.write_text('''import json,sys,time
print(json.dumps({'state':'WARMING_UP'}),flush=True)
time.sleep(.2)
print(json.dumps({'state':'READY','fixture':True}),flush=True)
for line in sys.stdin:
    print(json.dumps({'instances':[]}),flush=True)
''')
            def start(*args, **kwargs):
                return original([sys.executable,str(script)], **kwargs)
            with patch.object(core.subprocess,'Popen',side_effect=start) as spawn:
                backend = core.Backend()
                try:
                    states = set()
                    deadline = time.monotonic()+5
                    while backend.status()['model_state'] != 'READY':
                        states.add(backend.state)
                        self.assertLess(time.monotonic(),deadline)
                        time.sleep(.01)
                    self.assertIn('WARMING_UP',states)
                    self.assertEqual(spawn.call_count,1)
                    with backend.lock:
                        self.assertEqual(backend.infer(Path(directory),{}),[])
                        self.assertEqual(backend.infer(Path(directory),{}),[])
                    self.assertEqual(spawn.call_count,1)
                    backend.process.terminate()
                    backend.process.wait(timeout=5)
                    self.assertEqual(backend.status()['model_state'],'FAILED')
                finally:
                    backend.close()


class Setup(unittest.TestCase):
    def test_missing_toolkit_requires_explicit_interactive_yes(self):
        import pty
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, body in {
                'docker': '#!/bin/bash\nif [[ "$*" == "info --format {{json .Runtimes}}" ]]; then echo "{}"; fi\n',
                'nvidia-smi': '#!/bin/bash\nexit 0\n',
                'apt-get': '#!/bin/bash\necho INSTALL_REACHED\nexit 77\n',
                'sudo': '#!/bin/bash\nexec "$@"\n',
            }.items():
                path = folder/name
                path.write_text(body)
                path.chmod(0o755)
            env = dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH'],DOCKER_HOST='unix:///var/run/docker.sock')
            command = ['bash',str(ROOT/'scripts/prepare_sam3.sh')]
            result = subprocess.run(command,input='yes\n',env=env,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('interactive terminal',result.stderr)
            self.assertNotIn('INSTALL_REACHED',result.stdout)
            for answer, accepted in [('\n',False),('n\n',False),('yes\n',True)]:
                with self.subTest(answer=answer):
                    master, slave = pty.openpty()
                    try:
                        process = subprocess.Popen(command,stdin=slave,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,text=True)
                        os.write(master,answer.encode())
                        stdout, stderr = process.communicate(timeout=10)
                        self.assertEqual('INSTALL_REACHED' in stdout,accepted,(stdout,stderr))
                        self.assertEqual(process.returncode,77 if accepted else 1)
                    finally:
                        os.close(master)
                        os.close(slave)

    def test_prepare_shell_defaults_and_check_only(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            python = folder/'python3'
            python.write_text('#!'+sys.executable+'''\nimport json,sys
if sys.argv[1:2] == ['-c']:
    sys.exit(0 if 'nvidia' in json.load(sys.stdin) else 1)
print(json.dumps(sys.argv[1:]))
''')
            python.chmod(0o755)
            docker = folder/'docker'
            docker.write_text('#!/bin/bash\nif [[ "$*" == "info --format {{json .Runtimes}}" ]]; then echo \'{"nvidia":{}}\'; fi\n')
            docker.chmod(0o755)
            env = dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH'])
            for args, tail in [(['--domain-id','55'],['--domain-id','55','--download','--build']),
                               (['--check-only'],[])]:
                output = subprocess.check_output(['bash',str(ROOT/'scripts/prepare_sam3.sh'),*args],env=env,text=True)
                self.assertEqual(json.loads(output),['scripts/setup_sam3.py',*tail])

    def test_prepare_refuses_toolkit_restart_with_live_containers(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, body in {
                'docker': '''#!/bin/bash
case "$*" in
  'info --format {{json .Runtimes}}') echo '{}' ;;
  'ps -q') echo live-body ;;
esac
''',
                'nvidia-smi': '#!/bin/bash\nexit 0\n',
                'apt-get': '#!/bin/bash\nexit 99\n',
                'sudo': '#!/bin/bash\necho "UNEXPECTED MUTATION"; exit 99\n',
            }.items():
                path = folder/name
                path.write_text(body)
                path.chmod(0o755)
            result = subprocess.run(['bash',str(ROOT/'scripts/prepare_sam3.sh')],
                env=dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH'],DOCKER_HOST='unix:///var/run/docker.sock'),
                text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('containers are running',result.stderr)
            self.assertNotIn('UNEXPECTED MUTATION',result.stdout)

    def test_modelscope_download_is_pinned_verified_and_anonymous(self):
        import hashlib
        import io
        data = b'checkpoint fixture'
        commit = 'a'*40
        metadata = dict(Code=200, Success=True, Data=dict(Files=[dict(Path='sam3.pt', Type='blob', Revision=commit,
            Sha256=hashlib.sha256(data).hexdigest(),Size=len(data))]))
        calls = []
        class Response(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return Response(json.dumps(metadata).encode() if len(calls)==1 else data)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            # A previous source failed before writing any bytes: metadata alone
            # must not prevent starting a fresh ModelScope download.
            (folder/'download.json').write_text(json.dumps({'source':'huggingface','revision':'old'}))
            setup.download(folder,Opener())
            self.assertEqual((folder/'sam3.pt').read_bytes(),data)
            self.assertIn('Revision='+commit+'&FilePath=sam3.pt',calls[1].full_url)
            self.assertTrue(all(call.get_header('Authorization') is None for call in calls))
            self.assertTrue(all(call.full_url.startswith(setup.MODEL_API) for call in calls))
            self.assertEqual(json.loads((folder/'download.json').read_text())['source'],'modelscope')
            setup.download(folder,Opener())
            self.assertEqual(len(calls),2)

    def test_download_resumes_and_rejects_bad_digest(self):
        import hashlib
        import io
        data = b'checkpoint fixture'
        commit = 'b'*40
        digest = hashlib.sha256(data).hexdigest()
        metadata = dict(Code=200, Success=True, Data=dict(Files=[dict(Path='sam3.pt', Type='blob', Revision=commit, Sha256=digest,Size=len(data))]))
        class Response(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def open(self, request, timeout):
                if '/repo/files?' in request.full_url:
                    return Response(json.dumps(metadata).encode())
                self.range = request.get_header('Range')
                result = Response(data[5:])
                result.status = 206
                result.headers = {'Content-Range':f'bytes 5-{len(data)-1}/{len(data)}'}
                return result
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            state = dict(source='modelscope',repo='facebook/sam3',revision=commit,filename='sam3.pt',sha256=digest,size=len(data))
            (folder/'download.json').write_text(json.dumps(state))
            (folder/'sam3.pt.part').write_bytes(data[:5])
            opener = Opener()
            setup.download(folder,opener)
            self.assertEqual(opener.range,'bytes=5-')
            self.assertEqual((folder/'sam3.pt').read_bytes(),data)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'download.json').write_text(json.dumps(state))
            (folder/'sam3.pt.part').write_bytes(b'x'*len(data))
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
                setup.download(folder,Opener())
            self.assertFalse((folder/'sam3.pt').exists())

    def test_cross_host_redirect_strips_auth_and_rejects_http(self):
        import urllib.request
        request = urllib.request.Request('https://modelscope.cn/file',headers={'Authorization':'Bearer secret','Range':'bytes=5-'})
        handler = setup.SafeRedirect()
        result = handler.redirect_request(request,None,302,'Found',{},'https://cdn.example/file')
        self.assertIsNone(result.get_header('Authorization'))
        self.assertEqual(result.get_header('Range'),'bytes=5-')
        with self.assertRaisesRegex(ValueError,'HTTPS'):
            handler.redirect_request(request,None,302,'Found',{},'http://cdn.example/file')


class Lifecycle(unittest.TestCase):
    def test_readiness_wait_fails_immediately_or_waits_for_ready(self):
        import sys
        import types
        ros = MagicMock()
        client = ros.create_node.return_value.create_client.return_value
        client.wait_for_service.return_value = True
        future = client.call_async.return_value
        future.done.return_value = True
        srv = types.ModuleType('std_srvs.srv')
        srv.Trigger = MagicMock()
        with patch.dict(sys.modules, {'rclpy':ros,'std_srvs':types.ModuleType('std_srvs'),'std_srvs.srv':srv}):
            health = load('sam3_health', 'docker/agent_instance/sam3/healthcheck.py')
        with patch.object(sys,'argv',['healthcheck.py','--wait']), patch.object(health.time,'sleep') as sleep:
            future.result.return_value.message = json.dumps({'model_state':'FAILED','error':'dtype mismatch'})
            self.assertEqual(health.main(),2)
            sleep.assert_not_called()
            future.result.side_effect = [types.SimpleNamespace(message=json.dumps({'model_state':state}))
                                         for state in ('LOADING','WARMING_UP','READY')]
            self.assertEqual(health.main(),0)
            self.assertEqual(sleep.call_count,2)

    def test_shell_sam3_entrypoint_preserves_domain(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory)/'python3'
            stub.write_text('#!'+sys.executable+'\nimport json,sys; print(json.dumps(sys.argv[1:]))\n')
            stub.chmod(0o755)
            output = subprocess.check_output(['bash',str(ROOT/'agent.sh'),'sam3','--domain-id','55'],
                env=dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH']),text=True)
            self.assertEqual(json.loads(output), ['scripts/runtime.py','sam3','--domain-id','55'])

    def test_sam3_starts_independently_and_never_recreates_live_service(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher,'ROOT',Path(directory)), \
                patch.object(launcher.sys,'argv',['runtime.py','sam3','--domain-id','55']), \
                patch.object(launcher,'prepare_sam3') as prepare, \
                patch.object(launcher,'ensure_image') as image, patch.object(launcher,'run') as run, \
                patch.object(launcher,'ensure_server') as server:
            launcher.main()
            server.assert_not_called()
            self.assertEqual(prepare.call_args.args[0]['ROS_DOMAIN_ID'],55)
            self.assertEqual(image.call_args.args[1],'sam3')
            self.assertIn('--no-recreate',run.call_args_list[0].args[1])
            self.assertEqual(run.call_args_list[0].args[1][-1],'sam3')
            self.assertNotIn('--wait',run.call_args_list[0].args[1])
            self.assertIn('/opt/sam3/healthcheck.py',run.call_args.args[1])
            self.assertIn('--wait',run.call_args.args[1])
            self.assertTrue((Path(directory)/'runs/55/agent_instance/sam3/config.json').is_file())

    def test_stop_does_not_require_weights_or_start_agent(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher,'ROOT',Path(directory)), \
                patch.object(launcher.sys,'argv',['runtime.py','stop-sam3']), \
                patch.object(launcher,'prepare_sam3') as prepare, patch.object(launcher,'run') as run:
            launcher.main()
            prepare.assert_not_called()
            self.assertEqual(run.call_args.args[1],['stop','sam3'])

    def test_staging_preserves_existing_interfaces_and_excludes_implementation(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher,'ROOT',Path(directory)):
            source = Path(directory)/'docker/agent_instance/sam3/interfaces'
            shutil.copytree(ROOT/'docker/agent_instance/sam3/interfaces', source)
            root = Path(directory)/'runs/42'
            workspace = root/'agent_instance/agent/workspace'
            workspace.mkdir(parents=True)
            with patch.object(launcher,'prepare_workspace'):
                with self.assertRaisesRegex(ValueError,'authorized SAM3 weights'):
                    launcher.prepare_sam3({'ROS_DOMAIN_ID':42})
                (root/'agent_instance/sam3/models/sam3.pt').write_bytes(b'test')
                launcher.prepare_sam3({'ROS_DOMAIN_ID':42})
                launcher.prepare_sam3({'ROS_DOMAIN_ID':42})
                target = workspace/'ros_ws/src/agenticros_perception_interfaces'
                self.assertFalse(list(target.rglob('*.py')))
                (target/'package.xml').write_text('local changes')
                with self.assertRaisesRegex(ValueError,'differ'):
                    launcher.prepare_sam3({'ROS_DOMAIN_ID':42})
                self.assertEqual((target/'package.xml').read_text(),'local changes')

    def test_recreate_refuses_running_sam3(self):
        with patch.object(launcher.subprocess,'check_output', side_effect=['id',json.dumps([{'State':{'Running':True}}])]), \
                patch.object(launcher,'run') as run:
            with self.assertRaisesRegex(ValueError,'running'):
                launcher.recreate_stopped({'ROS_DOMAIN_ID':42},service='sam3')
            run.assert_not_called()

    def test_optional_profile_and_mount_boundary(self):
        import subprocess
        config = json.loads(subprocess.check_output(['docker','compose','-f','compose.agent.yaml',
            '--profile','sam3','config','--format','json'],cwd=ROOT,
            env=dict(os.environ, ROS_DOMAIN_ID='43'),text=True))
        service = config['services']['sam3']
        self.assertEqual(service['profiles'], ['sam3'])
        self.assertEqual(service['environment']['ROS_DOMAIN_ID'],'43')
        mounts = {item['target']:item for item in service['volumes']}
        self.assertEqual(set(mounts), {'/records','/models'})
        self.assertTrue(mounts['/models']['read_only'])
        self.assertTrue(all('/runs/43/agent_instance/sam3/' in item['source'] for item in mounts.values()))
        self.assertNotIn('depends_on',service)
        self.assertFalse(any('sam3' in item['source'] for item in config['services']['server']['volumes']))

if __name__ == '__main__':
    unittest.main()
