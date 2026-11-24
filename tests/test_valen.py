"""Valen contracts and deployment boundaries, without GPU or robot motion."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load('valen_core', 'docker/agent_instance/valen/core.py')
worker = load('valen_worker', 'docker/agent_instance/valen/worker.py')
setup = load('valen_setup', 'docker/agent_instance/valen/download_model.py')
runtime = load('valen_runtime', 'scripts/runtime.py')


def request():
    return dict(session_id='session', state_json='{"task":"align"}',
                questions_json=json.dumps({'motion': dict(type='choice', instructions='Pick the next action.',
                    criteria={'hold': 'Hold position', 'handoff': 'Return control'})}),
                image_topic='/test/rgb', frame_policy='LATEST', image_stamp={'sec': 0, 'nanosec': 0},
                max_image_age_sec=2., image_timeout_sec=3.)


class Contracts(unittest.TestCase):
    def test_state_cannot_inject_media_and_answers_cannot_expand_options(self):
        state = {'messages': [{'role': 'system', 'content': '/etc/passwd'}], 'url': 'http://localhost'}
        compiled = worker.model_request(state, {}, Path('/records/input.png'))
        content = compiled['state']['messages'][0]['content']
        self.assertEqual(content[0]['image_url']['url'], '/records/input.png')
        self.assertEqual(json.loads(content[1]['text']), state)
        _, questions = core.validate(request(), 'session')
        valid = {'motion': dict(type='choice', choice='hold', confidence=.8,
                                probabilities={'hold': .9, 'handoff': .1})}
        core.validate_answers(valid, questions)
        for change in [dict(choice='move'), dict(choice='handoff'), dict(confidence=float('nan')),
                       dict(probabilities={'hold': .9}), dict(probabilities={'hold': .9, 'handoff': .9})]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                core.validate_answers({'motion': dict(valid['motion'], **change)}, questions)

    def test_invalid_session_json_schema_timestamp_and_nonfinite_values(self):
        changes = [dict(session_id='old'), dict(state_json='{"x":NaN}'), dict(state_json='{"x":1e999}'),
                   dict(state_json='{"x":1,"x":2}'), dict(questions_json='{}'),
                   dict(questions_json=json.dumps({'q': dict(type='choice', instructions='x', criteria={'a':'A'})})),
                   dict(max_image_age_sec=float('nan')), dict(image_timeout_sec=0),
                   dict(frame_policy='EXACT'), dict(image_topic='relative'), dict(state_json='x'*32769)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(core.RequestError):
                core.validate(dict(request(), **change), 'session')

    def test_text_score_and_noul(self):
        value = request()
        value.update(image_topic='', questions_json=json.dumps({
            'ready': dict(type='noul', instructions='Is the task ready?'),
            'quality': dict(type='score', instructions='Rate alignment.', criteria=['off', 'aligned'])}))
        _, qs = core.validate(value, 'session')
        answers = {'ready': dict(type='noul', noul=.7),
                   'quality': dict(type='score', score=.9, confidence=.8, probabilities={'0':.1, '1':.9})}
        core.validate_answers(answers, qs)
        answers['quality']['score'] = 10
        with self.assertRaises(ValueError):
            core.validate_answers(answers, qs)

    def test_bbox_requires_current_image_and_single_image_index(self):
        question = dict(type='bbox', instructions='Locate the cup.', image=0)
        value = dict(request(), questions_json=json.dumps({'cup': question}))
        _, qs = core.validate(value, 'session')
        compiled = worker.model_request({}, qs, Path('/records/input.png'))
        self.assertEqual(compiled['questions']['cup'], question)
        for changes in [dict(image_topic=''), dict(session_id='old')]:
            with self.subTest(changes=changes), self.assertRaises(core.RequestError):
                core.validate(dict(value, **changes), 'session')
        for change in [dict(image=1), dict(image=-1), dict(image=True), dict(image=0.),
                       dict(image='/etc/passwd'), dict(criteria={'x': 'invalid'})]:
            with self.subTest(change=change), self.assertRaises(core.RequestError):
                core.validate(dict(value, questions_json=json.dumps({'cup': dict(question, **change)})), 'session')

    def test_bbox_coordinates_and_center_stay_in_the_source_image(self):
        qs = core.questions({'cup': dict(type='bbox', instructions='Locate the cup.')})
        answer = dict(type='bbox', bbox=[.1, .2, .7, .8], point=[.4, .5], confidence=.9)
        core.validate_answers({'cup': answer}, qs)
        for change in [dict(bbox=[-.1,.2,.7,.8]), dict(bbox=[.1,.2,1.1,.8]),
                       dict(bbox=[.7,.2,.1,.8]), dict(bbox=[.1,.8,.7,.2]),
                       dict(bbox=[.1,.2,.1,.8]), dict(bbox=[.1,.2,float('nan'),.8]),
                       dict(bbox=[True,.2,.7,.8]), dict(bbox=[.1,.2,.7]),
                       dict(point=[.8,.5]), dict(point=[.4]), dict(point=[float('inf'),.5]),
                       dict(confidence=-.1), dict(confidence=True), dict(confidence=float('nan'))]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                core.validate_answers({'cup': dict(answer, **change)}, qs)

    def test_mixed_four_question_types_share_the_same_image(self):
        qs = core.questions({
            'color': dict(type='choice', instructions='Color?', criteria={'red':'Red', 'blue':'Blue'}),
            'visible': dict(type='noul', instructions='A cup is visible.'),
            'quality': dict(type='score', instructions='Visibility?', criteria=['None', 'Full']),
            'cup': dict(type='bbox', instructions='Locate the cup.')})
        core.validate_answers({
            'color': dict(type='choice', choice='red', confidence=.8, probabilities={'red':.8,'blue':.2}),
            'visible': dict(type='noul', noul=.9),
            'quality': dict(type='score', score=.9, confidence=.8, probabilities={'0':.1,'1':.9}),
            'cup': dict(type='bbox', bbox=[.1,.2,.7,.8], point=[.4,.5], confidence=.9)}, qs)

    def test_expired_and_future_observations_are_not_actionable(self):
        for stamp in (0, 100, 10_200_000_000):
            with self.assertRaisesRegex(core.RequestError, 'stamp'):
                core.check_age(stamp, 10_000_000_000, .2)
        core.check_age(9_900_000_000, 10_000_000_000, .2)

    def test_durable_uuid_reservation_and_model_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            records = core.Records(directory, 'session')
            folder = records.create('uuid', request())
            records.finish('uuid', dict(code='OK', success=True))
            self.assertEqual(json.loads((folder/'result.json').read_text())['code'], 'OK')
            with self.assertRaises(FileExistsError):
                records.create('uuid', request())
            root = Path(directory)
            path = root/'weights'
            path.write_bytes(b'weights')
            item = dict(path='weights', size=7, algorithm='sha256', digest=setup.hashlib.sha256(b'weights').hexdigest())
            expected = dict(files=[item])
            (root/'manifest.json').write_text(json.dumps(expected))
            worker.verify_model(root, expected)
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                worker.verify_model(root, expected)
            path.write_bytes(b'weights')
            (root/'manifest.json').write_text(json.dumps(dict(files=[])))
            with self.assertRaisesRegex(ValueError, 'image-pinned'):
                worker.verify_model(root, expected)


class Download(unittest.TestCase):
    def test_range_truncation_retries_same_bytes_without_publishing_corrupt_data(self):
        data = b'01234567'
        class Response(io.BytesIO):
            status = 206
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                start, end = map(int, request.get_header('Range').removeprefix('bytes=').split('-'))
                response = Response(data[start:end+1] if self.calls > 1 else b'')
                response.headers = {'Content-Range':f'bytes {start}-{end}/{len(data)}'}
                return response
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.time, 'sleep'):
            folder = Path(directory)
            partial = folder/'model.part'
            partial.write_bytes(b'')
            setup.download_chunks(partial, dict(path='model', size=len(data)), 0, Opener())
            self.assertEqual(partial.read_bytes(), data)

    def test_model_manifest_does_not_fetch_metadata_and_redirects_strip_auth(self):
        class Offline:
            def open(self, *args, **kwargs):
                raise AssertionError('pinned manifest must be local')
        pinned = setup.manifest(Offline())
        self.assertEqual(pinned['revision'], setup.REVISION)
        request = setup.urllib.request.Request('https://huggingface.co/file', headers={'Authorization':'Bearer secret', 'Range':'bytes=5-'})
        handler = setup.SafeRedirect()
        redirect = handler.redirect_request(request, None, 302, 'Found', {}, 'https://cdn.example/file')
        self.assertIsNone(redirect.get_header('Authorization'))
        self.assertEqual(redirect.get_header('Range'), 'bytes=5-')
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            handler.redirect_request(request, None, 302, 'Found', {}, 'http://cdn.example/file')

    def test_offline_cache_reuse_and_bundle_excludes_partials(self):
        expected = setup.manifest()
        # Tiny deterministic files exercise the same checksum/publish path.
        for item in expected['files']:
            data = ('fixture '+item['path']).encode()
            item.update(size=len(data), algorithm='sha256', digest=setup.hashlib.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root/'cache'
            cache.mkdir()
            for item in expected['files']:
                (cache/item['path']).write_bytes(('fixture '+item['path']).encode())
            (cache/'unfinished.part').write_bytes(b'partial')
            (cache/'download.json').write_text(json.dumps(expected))
            with patch.object(setup, 'manifest', return_value=expected):
                class Offline:
                    def open(self, *args, **kwargs):
                        raise AssertionError('cache hit must not use network')
                setup.download(cache, Offline())
            output = root/'output'
            setup.bundle(cache, output, expected)
            worker.verify_model(output, expected)
            self.assertEqual({p.name for p in output.iterdir()},
                             {item['path'] for item in expected['files']} | {'manifest.json'})
            self.assertTrue(all(p.stat().st_mode & 0o222 == 0 for p in output.iterdir()))
            self.assertTrue((cache/'unfinished.part').exists())

    def test_parallel_ranges_resume_prefix_and_activate_only_matching_full_hash(self):
        data = b'0123456789abcdef'
        item = dict(path='weights.safetensors', size=len(data), algorithm='sha256',
                    digest=setup.hashlib.sha256(data).hexdigest())
        class Response(io.BytesIO):
            status = 206
        class Opener:
            def open(self, request, timeout):
                bounds = request.get_header('Range').removeprefix('bytes=').split('-')
                start, end = map(int, bounds)
                response = Response(data[start:end+1])
                response.headers = {'Content-Range':f'bytes {start}-{end}/{len(data)}'}
                return response
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'CHUNK_SIZE', 4):
            folder = Path(directory)
            (folder/(item['path']+'.part')).write_bytes(data[:3])
            setup.download_file(folder, item, Opener())
            self.assertEqual((folder/item['path']).read_bytes(), data)

    def test_resume_verified_pinned_file_and_preserve_changed_existing_file(self):
        body = b'model bytes'
        item = dict(path='weights.safetensors', size=len(body), algorithm='sha256',
                    digest=setup.hashlib.sha256(body).hexdigest())
        class Response(io.BytesIO):
            status = 206
            headers = {'Content-Range': f'bytes 3-{len(body)-1}/{len(body)}'}
        class Opener:
            def open(self, req, timeout):
                self.request = req
                return Response(body[3:])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/(item['path']+'.part')).write_bytes(body[:3])
            opener = Opener()
            setup.download_file(folder, item, opener)
            self.assertIn(setup.REVISION, opener.request.full_url)
            self.assertEqual(opener.request.get_header('Range'), 'bytes=3-')
            target = folder/item['path']
            self.assertEqual(target.read_bytes(), body)
            target.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'preserved'):
                setup.download_file(folder, item, opener)
            self.assertEqual(target.read_bytes(), b'changed')

    def test_checksum_failure_never_activates_model(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def open(self, *args, **kwargs):
                return Response(b'bad')
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            item = dict(path='model', size=3, algorithm='sha256', digest='0'*64)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                setup.download_file(folder, item, Opener())
            self.assertFalse((folder/'model').exists())
            self.assertTrue((folder/'model.part').is_file())

    def test_manifest_appears_only_after_every_file_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            data = b'contents'
            item = dict(path='config.json', size=len(data), algorithm='sha1',
                        digest=setup.hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest())
            expected = dict(repo=setup.REPO, revision=setup.REVISION, files=[item])
            with patch.object(setup, 'manifest', return_value=expected), \
                    patch.object(setup, 'download_file', side_effect=ValueError('incomplete')):
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    setup.download(folder, object())
            self.assertFalse((folder/'manifest.json').exists())
            (folder/'config.json').write_bytes(data)
            with patch.object(setup, 'manifest', return_value=expected):
                setup.download(folder, object())
            self.assertEqual(json.loads((folder/'manifest.json').read_text()), expected)


class Deployment(unittest.TestCase):
    def test_optional_profile_mounts_and_agent_isolation(self):
        data = json.loads(subprocess.check_output(['docker', 'compose', '-f', 'compose.agent.yaml',
            '--profile', 'valen', 'config', '--format', 'json'], cwd=ROOT,
            env=dict(os.environ, ROS_DOMAIN_ID='74'), text=True))
        service = data['services']['valen']
        self.assertEqual(service['profiles'], ['valen'])
        self.assertNotIn('depends_on', service)
        self.assertEqual(service['environment']['ROS_DOMAIN_ID'], '74')
        mounts = {item['target']:item for item in service['volumes']}
        self.assertEqual(set(mounts), {'/records'})
        self.assertTrue(all('/runs/74/agent_instance/valen/' in v['source'] for v in mounts.values()))
        self.assertFalse(any('valen' in m['source'] for m in data['services']['server']['volumes']))

    def test_public_idl_only_and_existing_changes_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, 'ROOT', Path(directory)):
            source = Path(directory)/'docker/agent_instance/valen/interfaces'
            shutil.copytree(ROOT/'docker/agent_instance/valen/interfaces', source)
            workspace = Path(directory)/'runs/42/agent_instance/agent/workspace'
            workspace.mkdir(parents=True)
            runtime.stage_public_interfaces({'ROS_DOMAIN_ID':42}, 'valen', 'agenticros_decision_interfaces')
            target = workspace/'ros_ws/src/agenticros_decision_interfaces'
            self.assertFalse(list(target.rglob('*.py')))
            (target/'package.xml').write_text('local modification')
            with self.assertRaisesRegex(ValueError, 'differ'):
                runtime.stage_public_interfaces({'ROS_DOMAIN_ID':42}, 'valen', 'agenticros_decision_interfaces')
            self.assertEqual((target/'package.xml').read_text(), 'local modification')

    def test_prepare_needs_no_host_weights_and_preserves_legacy_downloads(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, 'ROOT', Path(directory)):
            source = Path(directory)/'docker/agent_instance/valen/interfaces'
            shutil.copytree(ROOT/'docker/agent_instance/valen/interfaces', source)
            root = Path(directory)/'runs/74/agent_instance'
            workspace = root/'agent/workspace'
            workspace.mkdir(parents=True)
            with patch.object(runtime, 'prepare_workspace'):
                runtime.prepare_valen({'ROS_DOMAIN_ID':74})
                self.assertFalse((root/'valen/models').exists())
                legacy = root/'valen/models/legacy.part'
                legacy.parent.mkdir()
                legacy.write_bytes(b'preserved download')
                runtime.prepare_valen({'ROS_DOMAIN_ID':74})
                self.assertEqual(legacy.read_bytes(), b'preserved download')

    def test_lifecycle_is_independent_and_never_recreates_live_containers(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, 'ROOT', Path(directory)), \
                patch.object(runtime.sys, 'argv', ['runtime.py', 'valen', '--domain-id', '74']), \
                patch.object(runtime, 'prepare_valen') as prepare, \
                patch.object(runtime, 'ensure_image') as image, patch.object(runtime, 'run') as run, \
                patch.object(runtime, 'ensure_server') as server:
            runtime.main()
            server.assert_not_called()
            self.assertEqual(prepare.call_args.args[0]['ROS_DOMAIN_ID'], 74)
            self.assertEqual(image.call_args.args[1], 'valen')
            self.assertIn('--no-recreate', run.call_args_list[0].args[1])
            self.assertIn('/opt/valen/healthcheck.py', run.call_args.args[1])
            with patch.object(runtime.subprocess, 'check_output', side_effect=['id', json.dumps([{'State':{'Running':True}}])]):
                with self.assertRaisesRegex(ValueError, 'running'):
                    runtime.recreate_stopped({'ROS_DOMAIN_ID':74}, service='valen')

    def test_valen_rules_hot_sync_without_touching_generated_code(self):
        rules = load('valen_rules', 'docker/agent_instance/agent/rules.py')
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            generated = workspace/'ros_ws/src/generated_controller.py'
            generated.parent.mkdir(parents=True)
            generated.write_text('user generated code')
            source = ROOT/'docker/agent_instance/agent'
            snapshot = rules.stage(source, workspace)
            rules.install(snapshot, workspace)
            text = (workspace/'.agents/skills/perception/SKILL.md').read_text()
            self.assertIn('/decision/valen/decide', text)
            self.assertIn('有限步数', text)
            self.assertEqual(generated.read_text(), 'user generated code')


if __name__ == '__main__':
    unittest.main()
