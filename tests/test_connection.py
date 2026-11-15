"""Cloud onboarding tests: no credentials, external model calls or ROS nodes."""
import contextlib
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1] / 'docker/agenticros/connection.py'
spec = importlib.util.spec_from_file_location('connection', SOURCE)
connection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connection)


def api_profile():
    return dict(kind='api', model='test-model', base_url='https://example.invalid/v1', api_key='test-key')


def stream(response):
    result = io.BytesIO(('data: ' + json.dumps({'type': 'response.completed',
                         'response': response}) + '\n\n').encode())
    result.headers = Message()
    result.headers['Content-Type'] = 'text/event-stream'
    return result


class CloudConnection(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, CODEX_HOME=self.directory.name)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_profile_is_atomic_private_and_persistent(self):
        profile = api_profile()
        connection.save_profile(profile)
        self.assertEqual(connection.load_profile(), profile)
        self.assertEqual(stat.S_IMODE(connection.profile_path().stat().st_mode), 0o600)
        profile['model'] = 'second-model'
        connection.save_profile(profile)
        self.assertEqual(connection.load_profile()['model'], 'second-model')
        self.assertEqual(list(Path(self.directory.name).glob('.connection-*')), [])

    def test_invalid_candidate_does_not_replace_saved_profile(self):
        connection.save_profile(api_profile())
        invalid = dict(api_profile(), base_url='https://example.invalid/?key=secret')
        with self.assertRaises(connection.ConnectionError):
            connection.save_profile(invalid)
        self.assertEqual(connection.load_profile(), api_profile())
        for url in ('http://example.invalid', 'https://user:key@example.invalid',
                    'https://example.invalid/responses', ''):
            with self.assertRaises(connection.ConnectionError):
                connection.validate_fields(dict(api_profile(), base_url=url))

    def test_keys_are_passed_only_in_environment(self):
        with patch.object(connection, 'codex_binary', return_value='/mock/codex'):
            argv, env = connection.command(api_profile(), ['exec', 'hello'])
        self.assertNotIn('test-key', str(argv))
        self.assertEqual(env['AGENTICROS_MODEL_API_KEY'], 'test-key')
        self.assertIn('model_provider="agenticros_cloud"', argv)
        self.assertEqual(argv[-2:], ['exec', 'hello'])

    def test_effort_is_validated_and_applied_for_both_auth_modes(self):
        with patch.object(connection, 'codex_binary', return_value='/mock/codex'):
            for kind in ('chatgpt', 'api'):
                for level in ('low', 'medium', 'high', 'xhigh', 'max'):
                    profile = dict(api_profile(), kind=kind, reasoning_effort=level)
                    connection.validate_fields(profile)
                    argv, _ = connection.command(profile, [])
                    self.assertIn('model_reasoning_effort="' + level + '"', argv)
        with self.assertRaises(connection.ConnectionError):
            connection.validate_fields(dict(api_profile(), reasoning_effort='invalid'))

    def test_streamed_tool_call_and_result_round_trip(self):
        requests = []
        def receive(request, timeout):
            payload = json.loads(request.data)
            requests.append(payload)
            self.assertEqual(request.full_url, 'https://example.invalid/v1/responses')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
            self.assertTrue(payload['stream'])
            self.assertEqual(payload['reasoning'], {'effort': 'medium'})
            if len(requests) == 1:
                output = [{'type': 'function_call', 'name': 'agenticros_connection_probe',
                           'call_id': 'call-1', 'arguments': '{"nonce": "nonce-1"}'}]
            else:
                self.assertEqual(payload['input'][-1]['call_id'], 'call-1')
                self.assertEqual(json.loads(payload['input'][-1]['output']), {'nonce': 'nonce-1'})
                output = [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'nonce-1'}]}]
            return stream({'status': 'completed', 'output': output})
        with patch.object(connection.urllib.request, 'build_opener') as opener, \
                patch.object(connection.uuid, 'uuid4') as identity:
            identity.return_value.hex = 'nonce-1'
            opener.return_value.open.side_effect = receive
            connection.validate_api(api_profile())
        self.assertEqual(len(requests), 2)

    def test_incomplete_stream_is_not_success(self):
        with patch.object(connection.urllib.request, 'build_opener') as opener:
            response = io.BytesIO(b'data: {"type":"response.created"}\n\n')
            response.headers = Message()
            response.headers['Content-Type'] = 'text/event-stream'
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(connection.ConnectionError, 'response.completed'):
                connection.response_stream(api_profile(), {})

    def test_http_errors_never_echo_server_body_or_key(self):
        error = urllib.error.HTTPError('https://example.invalid', 401, 'test-key', {}, io.BytesIO(b'test-key'))
        with patch.object(connection.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(connection.ConnectionError) as caught:
                connection.response_stream(api_profile(), {})
        self.assertIn('401', str(caught.exception))
        self.assertNotIn('test-key', str(caught.exception))

    def test_redirect_is_refused_before_forwarding_key(self):
        with self.assertRaises(connection.ConnectionError):
            connection.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid')

    def test_unexpected_tool_or_nonce_is_rejected(self):
        for item in ({'type': 'message'}, {'type': 'function_call', 'name': 'wrong'},
                     {'type': 'function_call', 'name': 'agenticros_connection_probe',
                      'call_id': 'id', 'arguments': '{}'}):
            with patch.object(connection, 'response_stream', return_value={'output': [item]}):
                with self.assertRaises(connection.ConnectionError):
                    connection.validate_api(api_profile())

    def test_failed_menu_validation_keeps_previous_credentials(self):
        connection.save_profile(api_profile())
        with patch.object(sys.stdin, 'isatty', return_value=True), \
                patch.object(sys.stdout, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['y', '2', '', '', '', '0']), \
                patch.object(connection.getpass, 'getpass', return_value='new-test-key'), \
                patch.object(connection, 'validate_connection', side_effect=connection.ConnectionError('failed')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            # redirect_stdout replaces the patched stdout, so provide a TTY-like capture.
            output.isatty = lambda: True
            self.assertEqual(connection.start([]), 0)
        self.assertEqual(connection.load_profile(), api_profile())
        self.assertNotIn('test-key', output.getvalue())

    def test_effort_only_edit_retains_model_and_key(self):
        connection.save_profile(api_profile())
        class Started(Exception):
            pass
        with patch.object(sys.stdin, 'isatty', return_value=True), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                patch('builtins.input', side_effect=['y', '4', 'high']), \
                patch.object(connection, 'validate_connection') as validate, \
                patch.object(connection, 'codex_binary', return_value='/mock/codex'), \
                patch.object(connection.os, 'execve', side_effect=Started):
            output.isatty = lambda: True
            with self.assertRaises(Started):
                connection.start(['resume', '--last'])
        expected = dict(api_profile(), reasoning_effort='high')
        validate.assert_called_once_with(expected)
        self.assertEqual(connection.load_profile(), expected)

    def test_chatgpt_uses_device_login_only_when_needed(self):
        profile = dict(kind='chatgpt', model='test-model')
        with patch.object(connection, 'chatgpt_status', side_effect=[False, True]), \
                patch.object(connection, 'codex_binary', return_value='/mock/codex'), \
                patch.object(connection.subprocess, 'run') as run, \
                contextlib.redirect_stdout(io.StringIO()):
            run.return_value.returncode = 0
            connection.validate_connection(profile)
        self.assertEqual(run.call_args.args[0][-2:], ['login', '--device-auth'])

    def test_returning_user_enters_saved_config_without_probe_or_rewrite(self):
        for profile in (api_profile(), dict(kind='chatgpt', model='saved-model')):
            connection.save_profile(profile)
            with patch.object(sys.stdin, 'isatty', return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()) as output, \
                    patch('builtins.input', return_value=''), \
                    patch.object(connection, 'launch') as launch, \
                    patch.object(connection, 'validate_connection') as validate, \
                    patch.object(connection, 'save_profile') as save:
                output.isatty = lambda: True
                connection.start(['resume', '--last'])
            launch.assert_called_once_with(profile, ['resume', '--last'])
            validate.assert_not_called()
            save.assert_not_called()

    def test_selecting_chatgpt_forces_new_login(self):
        connection.save_profile(dict(kind='chatgpt', model='old-model'))
        with patch.object(sys.stdin, 'isatty', return_value=True), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                patch('builtins.input', side_effect=['y', '1', 'new-model', 'medium']), \
                patch.object(connection, 'validate_connection') as validate, \
                patch.object(connection, 'launch') as launch:
            output.isatty = lambda: True
            connection.start([])
        expected = dict(kind='chatgpt', model='new-model', reasoning_effort='medium')
        validate.assert_called_once_with(expected, switch_account=True)
        launch.assert_called_once_with(expected, [])

    def test_device_login_without_menu_profile_offers_reuse(self):
        with patch.object(sys.stdin, 'isatty', return_value=True), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                patch('builtins.input', return_value='n') as prompt, \
                patch.object(connection, 'chatgpt_status', return_value=True), \
                patch.object(connection, 'launch') as launch:
            output.isatty = lambda: True
            connection.start([])
        self.assertIn('是否切换', prompt.call_args.args[0])
        self.assertEqual(launch.call_args.args[0]['kind'], 'chatgpt')

    def test_cancelled_switch_keeps_original_auth(self):
        auth = Path(self.directory.name) / 'auth.json'
        auth.write_text('original credentials')
        with patch.object(connection, 'codex_binary', return_value='/mock/codex'), \
                patch.object(connection.subprocess, 'run') as run, \
                contextlib.redirect_stdout(io.StringIO()):
            run.return_value.returncode = 1
            with self.assertRaises(connection.ConnectionError):
                connection.switch_chatgpt_account(dict(kind='chatgpt', model='test-model'))
        self.assertEqual(auth.read_text(), 'original credentials')
        self.assertEqual(list(Path(self.directory.name).glob('.account-login-*')), [])

    def test_switch_commits_only_verified_auth(self):
        auth = Path(self.directory.name) / 'auth.json'
        auth.write_text('original credentials')
        def run(argv, env, **kwargs):
            self.assertEqual(auth.read_text(), 'original credentials')
            self.assertNotEqual(env['CODEX_HOME'], self.directory.name)
            if argv[-2:] == ['login', '--device-auth']:
                (Path(env['CODEX_HOME']) / 'auth.json').write_text('new credentials')
                return connection.subprocess.CompletedProcess(argv, 0)
            return connection.subprocess.CompletedProcess(argv, 0, 'Logged in using ChatGPT', '')
        with patch.object(connection, 'codex_binary', return_value='/mock/codex'), \
                patch.object(connection.subprocess, 'run', side_effect=run), \
                contextlib.redirect_stdout(io.StringIO()):
            connection.switch_chatgpt_account(dict(kind='chatgpt', model='test-model'))
        self.assertEqual(auth.read_text(), 'new credentials')
        self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
