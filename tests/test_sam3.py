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
setup = load('sam3_setup', 'docker/agent_instance/sam3/download_checkpoint.py')


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
                return Response(data)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            # A previous source failed before writing any bytes: metadata alone
            # must not prevent starting a fresh ModelScope download.
            (folder/'download.json').write_text(json.dumps({'source':'huggingface','revision':'old'}))
            manifest=dict(source='modelscope',repo='facebook/sam3',revision=commit,filename='sam3.pt',sha256=hashlib.sha256(data).hexdigest(),size=len(data))
            setup.download(folder,Opener(),manifest)
            self.assertEqual((folder/'sam3.pt').read_bytes(),data)
            self.assertIn('Revision='+commit+'&FilePath=sam3.pt',calls[0].full_url)
            self.assertTrue(all(call.get_header('Authorization') is None for call in calls))
            self.assertTrue(all(call.full_url.startswith(setup.MODEL_API) for call in calls))
            self.assertEqual(json.loads((folder/'download.json').read_text())['source'],'modelscope')
            setup.download(folder,Opener(),manifest)
            self.assertEqual(len(calls),1)
            (folder/'sam3.pt').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
                setup.download(folder,Opener(),manifest)

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
            setup.download(folder,opener,state)
            self.assertEqual(opener.range,'bytes=5-')
            self.assertEqual((folder/'sam3.pt').read_bytes(),data)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'download.json').write_text(json.dumps(state))
            (folder/'sam3.pt.part').write_bytes(b'x'*len(data))
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
                setup.download(folder,Opener(),state)
            self.assertFalse((folder/'sam3.pt').exists())

    def test_parallel_ranges_are_ordered_and_checked(self):
        import io
        data=b'0123456789abcdef'
        class Response(io.BytesIO):
            status=206
        def fetch(url,headers):
            start,end=map(int,headers['Range'].split('=')[1].split('-'))
            response=Response(data[start:end+1])
            response.headers={'Content-Range':f'bytes {start}-{end}/{len(data)}'}
            return response
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'partial'
            path.write_bytes(data[:3])
            setup.download_ranges(fetch,'https://example.test',path,3,len(data),chunk_size=4,workers=2)
            self.assertEqual(path.read_bytes(),data)
            def invalid(url,headers):
                response=fetch(url,headers);response.status=200;return response
            with self.assertRaisesRegex(ValueError,'exact checkpoint byte range'):
                setup.download_ranges(invalid,'https://example.test',path,0,len(data),chunk_size=4,workers=2)

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
                self.assertFalse((root/'agent_instance/sam3/models').exists())
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
        self.assertEqual(set(mounts), {'/records'})
        self.assertTrue(all('/runs/43/agent_instance/sam3/' in item['source'] for item in mounts.values()))
        self.assertNotIn('depends_on',service)
        self.assertFalse(any('sam3' in item['source'] for item in config['services']['server']['volumes']))

if __name__ == '__main__':
    unittest.main()
