#!/opt/agent-venv/bin/python
"""Container-local cloud login menu, credential storage and Responses smoke check."""
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ConnectionError(ValueError):
    pass


EFFORTS = ('none', 'low', 'medium', 'high', 'xhigh', 'max')


def effort(profile):
    return profile.get('reasoning_effort', 'high' if profile['kind'] == 'deepseek' else 'medium')


def profile_path():
    return Path(os.environ['CODEX_HOME']) / 'agenticros-connection.json'


def validate_fields(profile):
    if profile.get('kind') not in ('chatgpt', 'deepseek', 'api'):
        raise ConnectionError('未知登录类型。')
    model = profile.get('model', '')
    if not isinstance(model, str) or not model.strip() or any(ord(c) < 32 for c in model):
        raise ConnectionError('请输入有效的模型名称。')
    if effort(profile) not in EFFORTS:
        raise ConnectionError('无效推理强度，可选：' + ', '.join(EFFORTS))
    if profile['kind'] != 'chatgpt':
        base_url = profile.get('base_url', '')
        if not isinstance(base_url, str) or any(c.isspace() or ord(c) < 32 for c in base_url):
            raise ConnectionError('API 地址格式无效。')
        try:
            url = urllib.parse.urlsplit(base_url)
            url.port
        except ValueError:
            raise ConnectionError('API 地址或端口格式无效。') from None
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path.rstrip('/').endswith('/responses')):
            raise ConnectionError('请输入 HTTPS Base URL，不含密钥、查询参数或 /responses 后缀。')
        key = profile.get('api_key', '')
        if not isinstance(key, str) or not key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ConnectionError('API Key 不能为空，也不能包含空格或控制字符。')


def load_profile():
    try:
        profile = json.loads(profile_path().read_text())
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        raise ConnectionError('保存的连接配置无法读取；请重新配置，原文件未删除。') from None
    if not isinstance(profile, dict):
        raise ConnectionError('保存的连接配置格式无效。')
    validate_fields(profile)
    return profile


def save_profile(profile):
    validate_fields(profile)
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.connection-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(profile, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def codex_binary():
    from codex_cli_bin import bundled_codex_path
    return str(bundled_codex_path())


def command(profile, arguments):
    """Keys stay in the child environment, never argv or the public TOML."""
    environment = dict(os.environ)
    settings = {'model': profile['model'], 'model_reasoning_effort': effort(profile)}
    if profile['kind'] == 'chatgpt':
        settings.update(model_provider='openai', forced_login_method='chatgpt')
    else:
        settings.update({
            'model_provider': 'agenticros_cloud',
            'forced_login_method': 'api',
            'model_providers.agenticros_cloud.name': profile['kind'],
            'model_providers.agenticros_cloud.base_url': profile['base_url'],
            'model_providers.agenticros_cloud.wire_api': 'responses',
            'model_providers.agenticros_cloud.requires_openai_auth': False,
            'model_providers.agenticros_cloud.env_key': 'AGENTICROS_MODEL_API_KEY',
        })
        environment['AGENTICROS_MODEL_API_KEY'] = profile['api_key']
    argv = [codex_binary()]
    for key, value in settings.items():
        argv += ['-c', key + '=' + json.dumps(value, ensure_ascii=False)]
    return argv + arguments, environment


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectionError('API 地址发生重定向，请核对最终 Base URL 后重试。')


def response_stream(profile, payload):
    request = urllib.request.Request(profile['base_url'].rstrip('/') + '/responses',
        data=json.dumps(payload).encode(), method='POST', headers={
            'Authorization': 'Bearer ' + profile['api_key'],
            'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=35) as response:
            if response.headers.get_content_type() != 'text/event-stream':
                raise ConnectionError('接口未返回 SSE 流；请确认提供的是 Responses API。')
            total, data, completed = 0, [], None
            deadline = time.monotonic() + 45
            for line in response:
                total += len(line)
                if total > 2_000_000 or time.monotonic() > deadline:
                    raise ConnectionError('校验响应过大或超时。')
                text = line.decode('utf-8').strip()
                if text.startswith('data:'):
                    data.append(text[5:].lstrip())
                elif not text and data:
                    event = json.loads('\n'.join(data))
                    data = []
                    if event.get('type') in ('response.failed', 'response.incomplete', 'error'):
                        raise ConnectionError('模型未正常完成校验，请检查模型权限、额度和接口兼容性。')
                    if event.get('type') == 'response.completed':
                        completed = event.get('response')
                        break
            if not isinstance(completed, dict) or completed.get('status') != 'completed':
                raise ConnectionError('缺少完整的 response.completed 事件。')
            return completed
    except urllib.error.HTTPError as error:
        # Do not echo server bodies: gateways can include credentials in errors.
        reasons = {401: 'API Key 无效或已过期', 403: '无访问权限',
                   402: '余额不足或需要付费', 404: '地址、模型或 Responses 路由不存在',
                   429: '额度不足或触发限流'}
        raise ConnectionError('HTTP %s：%s。' % (error.code, reasons.get(error.code, '服务端拒绝请求'))) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ConnectionError('网络、TLS 或连接超时错误，请检查 API 地址与网络。') from None
    except (json.JSONDecodeError, UnicodeError, TypeError, AttributeError):
        raise ConnectionError('响应格式不兼容 Responses API。') from None


def validate_api(profile):
    """Two small, stateless tool-call requests; no ROS or executable tool is involved."""
    validate_fields(profile)
    nonce = uuid.uuid4().hex
    initial = [{'role': 'user', 'content':
        'Call agenticros_connection_probe with nonce ' + nonce +
        '. After receiving its result, reply with that nonce only.'}]
    tool = {'type': 'function', 'name': 'agenticros_connection_probe',
            'description': 'Connection test only; no external action.',
            'parameters': {'type': 'object', 'properties': {'nonce': {'type': 'string'}},
                           'required': ['nonce'], 'additionalProperties': False}}
    payload = dict(model=profile['model'], input=initial, tools=[tool], stream=True,
                   reasoning={'effort': effort(profile)},
                   tool_choice={'type': 'function', 'name': tool['name']}, max_output_tokens=4096)
    first = response_stream(profile, payload)
    output = first.get('output', [])
    if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
        raise ConnectionError('模型工具响应格式无效。')
    calls = [item for item in output if item.get('type') == 'function_call']
    if (len(calls) != 1 or calls[0].get('name') != tool['name'] or not calls[0].get('call_id')):
        raise ConnectionError('连接可达，但模型未返回预期工具调用。')
    try:
        correct = json.loads(calls[0]['arguments']) == {'nonce': nonce}
    except (KeyError, ValueError, TypeError):
        correct = False
    if not correct:
        raise ConnectionError('模型工具调用参数校验失败。')
    payload.update(input=initial + output + [{'type': 'function_call_output',
        'call_id': calls[0]['call_id'], 'output': json.dumps({'nonce': nonce})}], tool_choice='none')
    second = response_stream(profile, payload)
    try:
        text = ''.join(part.get('text', '') for item in second.get('output', [])
                       if item.get('type') == 'message' for part in item.get('content', [])
                       if part.get('type') == 'output_text')
    except (AttributeError, TypeError):
        raise ConnectionError('模型最终响应格式无效。') from None
    if text.strip() != nonce:
        raise ConnectionError('模型未正确处理工具结果，暂不保存该配置。')


def chatgpt_status(profile):
    argv, environment = command(profile, ['login', 'status'])
    result = subprocess.run(argv, env=environment, capture_output=True, text=True)
    return result.returncode == 0 and 'chatgpt' in (result.stdout + result.stderr).lower()


def switch_chatgpt_account(profile):
    # Authorize into a temporary file store. Cancelled/failed login must not
    # destroy the current account; only a verified replacement is committed.
    home = Path(os.environ['CODEX_HOME'])
    home.mkdir(parents=True, exist_ok=True)
    print('请在浏览器中选择要使用的 ChatGPT 账号，再完成设备码授权。')
    with tempfile.TemporaryDirectory(prefix='.account-login-', dir=home) as temporary:
        settings = ['-c', 'cli_auth_credentials_store="file"']
        argv, environment = command(profile, settings + ['login', '--device-auth'])
        environment['CODEX_HOME'] = temporary
        if subprocess.run(argv, env=environment).returncode:
            raise ConnectionError('ChatGPT 登录未完成，原账号和配置保留。')
        argv, _ = command(profile, settings + ['login', 'status'])
        result = subprocess.run(argv, env=environment, capture_output=True, text=True)
        auth = Path(temporary) / 'auth.json'
        if (result.returncode or 'chatgpt' not in (result.stdout + result.stderr).lower()
                or not auth.is_file()):
            raise ConnectionError('新账号认证校验失败，原账号和配置保留。')
        auth.chmod(0o600)
        os.replace(auth, home / 'auth.json')


def validate_connection(profile, *, switch_account=False):
    if profile['kind'] == 'chatgpt':
        if switch_account:
            switch_chatgpt_account(profile)
        elif not chatgpt_status(profile):
            print('请按 Codex 提示在浏览器完成设备码授权。')
            argv, environment = command(profile, ['login', '--device-auth'])
            if subprocess.run(argv, env=environment).returncode or not chatgpt_status(profile):
                raise ConnectionError('ChatGPT 登录未完成，原连接配置保留。')
        print('ChatGPT 认证有效；模型访问仍以实际请求结果为准。')
    else:
        print('正在校验鉴权、模型、SSE 流和工具调用往返（两次小请求，会产生 API 用量）……')
        validate_api(profile)
        print('API 校验通过；尚未验证图像能力或 ROS 任务。')


def prompt(label, default=''):
    value = input(label + (f' [{default}]' if default else '') + ': ').strip()
    return value or default


def default_model():
    # Only read the simple top-level default; Codex remains the TOML parser.
    try:
        text = (Path(os.environ['CODEX_HOME']) / 'config.toml').read_text().split('\n[', 1)[0]
        match = re.search(r'^model\s*=\s*("[^"\n]+")', text, re.MULTILINE)
        if match:
            return json.loads(match.group(1))
    except (OSError, ValueError):
        pass
    return 'gpt-6-astra'


def start(arguments):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ConnectionError('登录菜单需要交互终端；已配置的自动化请使用 codex exec。')
    try:
        saved = load_profile()
    except ConnectionError as error:
        print(str(error))
        saved = None
    if saved is None:
        # docker-login.sh can have saved a login without creating a menu profile.
        existing = {'kind': 'chatgpt', 'model': default_model()}
        if chatgpt_status(existing):
            saved = existing
    if saved:
        print('已保存：%s / %s / %s' % (saved['kind'], saved['model'], effort(saved)))
        while True:
            change = input('是否切换账号或修改连接配置？[y/N，q 退出]: ').strip().lower()
            if change in ('', 'n', 'no'):
                return launch(saved, arguments)
            if change in ('q', '0'):
                return 0
            if change in ('y', 'yes'):
                break
            print('请输入 y、n 或 q。')
    while True:
        print('\nAgenticROS — 选择云端模型登录方式')
        if saved:
            print('当前：%s / %s / %s' % (saved['kind'], saved['model'], effort(saved)))
            print('回车：直接使用当前配置')
            print('4. 仅修改当前配置的推理强度')
        print('1. ChatGPT 账号\n2. DeepSeek API\n3. 其他云端 API（Responses 协议）\n0. 退出')
        selection = input('请选择: ').strip()
        if selection == '0':
            return 0
        if not selection and saved:
            return launch(saved, arguments)
        elif selection == '4' and saved:
            candidate = dict(saved)
            candidate['reasoning_effort'] = prompt('推理强度（' + '/'.join(EFFORTS) + '）', effort(saved))
        elif selection in ('1', '2', '3'):
            kind = {'1': 'chatgpt', '2': 'deepseek', '3': 'api'}[selection]
            candidate = {'kind': kind}
            if kind == 'chatgpt':
                candidate['model'] = prompt('模型名称', default_model())
                candidate['reasoning_effort'] = prompt('推理强度（' + '/'.join(EFFORTS) + '）', 'medium')
            else:
                candidate['base_url'] = prompt('HTTPS Base URL',
                    'https://api.deepseek.com' if kind == 'deepseek' else '').rstrip('/')
                candidate['model'] = prompt('模型名称', 'deepseek-flash' if kind == 'deepseek' else '')
                candidate['reasoning_effort'] = prompt('推理强度（' + '/'.join(EFFORTS) + '）', effort(candidate))
                candidate['api_key'] = getpass.getpass('API Key（输入不回显）: ').strip()
        else:
            print('请选择已有选项。')
            continue
        try:
            validate_fields(candidate)
            if selection == '1':
                validate_connection(candidate, switch_account=True)
            else:
                validate_connection(candidate)
            save_profile(candidate)
        except ConnectionError as error:
            print('校验失败：' + str(error))
            continue
        print('配置已保存到容器专用卷，正在进入 Codex。')
        return launch(candidate, arguments)


def launch(profile, arguments):
    argv, environment = command(profile, arguments)
    os.execve(argv[0], argv, environment)


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    mode = arguments.pop(0) if arguments else 'start'
    try:
        if mode == 'start':
            return start(arguments)
        if mode == 'exec':
            # Explicit login/logout remain available independently of the selected API.
            profile = None if arguments and arguments[0] in ('login', 'logout') else load_profile()
            if profile:
                argv, environment = command(profile, arguments)
                os.execve(argv[0], argv, environment)
            os.execv(codex_binary(), [codex_binary(), *arguments])
        if mode == 'status':
            profile = load_profile()
            if profile:
                if profile['kind'] == 'chatgpt' and not chatgpt_status(profile):
                    return 1
                print('已配置：%s / %s（此检查不发起 API 请求）' % (profile['kind'], profile['model']))
                return 0
            return subprocess.run([codex_binary(), 'login', 'status']).returncode
        raise ConnectionError('用法：agenticros start|status|exec [Codex 参数]')
    except (ConnectionError, OSError) as error:
        # OSError paths may reveal filesystem details but never include server response bodies.
        print(str(error) if isinstance(error, ConnectionError) else '本地配置或程序启动失败。', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\n已退出，未提交的新配置不保存。')
        return 130


if __name__ == '__main__':
    sys.exit(main())
