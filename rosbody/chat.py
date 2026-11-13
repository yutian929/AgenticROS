"""An independent Codex process and a managed, simulation-only ROS body."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

from .agent_workspace import ROOT, prepare_workspace, save_metadata


def add_chat_commands(sub):
    chat = sub.add_parser('chat', help='Start an independent Codex robot agent')
    chat.add_argument('--session', help='Resume a session directory (simulation restarts)')
    chat.add_argument('--model', help='Codex model; defaults to your Codex configuration')
    chat.add_argument('--viewer', action='store_true', help='Show the live MuJoCo window')
    chat.add_argument('--verbose', action='store_true', help='Print complete command output')
    chat.add_argument('--seed', type=int, default=0)
    chat.add_argument('--prompt', action='append', help='Run text commands in order, then exit')
    for name in ('doctor', 'login'):
        p = sub.add_parser(name)
        if name == 'login':
            p.add_argument('--api-key', action='store_true', help='Read OPENAI_API_KEY from environment')


def runtime():
    from codex_cli_bin import bundled_codex_path
    return str(bundled_codex_path())


def authenticated():
    check = subprocess.run([runtime(), 'login', 'status'], capture_output=True, text=True)
    return check.returncode == 0


@contextmanager
def session_lock(path):
    with (path / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('This session is already open in another process.') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def domain_lease():
    """Avoid two managed chats sharing DDS discovery, including startup races."""
    directory = ROOT / 'runs' / 'ros-domains'
    directory.mkdir(parents=True, exist_ok=True)
    first = secrets.randbelow(120)
    for offset in range(120):
        domain = 80 + (first + offset) % 120
        lock = (directory / str(domain)).open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            continue
        try:
            yield domain
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        return
    raise RuntimeError('No simulation domain available.')


@contextmanager
def simulation(session, env, seed, viewer=False):
    # Refuse to attach to an existing body, even if a random domain collides.
    try:
        check = subprocess.run([sys.executable, '-m', 'rosbody.cli', 'status'],
                               env=env, capture_output=True, timeout=2)
        if check.returncode == 0:
            raise RuntimeError('Selected ROS domain already contains a body; start again.')
    except subprocess.TimeoutExpired:
        pass
    out = session / ('body-' + time.strftime('%Y%m%d-%H%M%S'))
    with (session / 'server.log').open('a') as log:
        # Invoke the trampoline through Python: macOS /bin/sh strips DYLD_*
        # from the environment, which would break generated ROS type support.
        interpreter = [sys.executable]
        if viewer and sys.platform == 'darwin':
            interpreter.append(str(Path(sys.executable).with_name('mjpython')))
        command = interpreter + ['-m', 'rosbody.server', '--out', str(out), '--seed', str(seed)]
        if viewer:
            command.append('--viewer')
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'Simulator exited; see {session / "server.log"}')
                try:
                    check = subprocess.run([sys.executable, '-m', 'rosbody.cli', 'status'],
                                           env=env, capture_output=True, text=True, timeout=3)
                except subprocess.TimeoutExpired:
                    continue
                if check.returncode == 0:
                    status = json.loads(check.stdout)
                    break
            else:
                raise RuntimeError('Simulator did not become ready; inspect server.log.')
            yield status
            return
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def run_turn(thread, prompt, session, verbose=False):
    turn = thread.turn(prompt)
    try:
        with (session / 'events.jsonl').open('a') as log:
            for event in turn.stream():
                payload = event.payload.model_dump(mode='json', by_alias=True)
                log.write(json.dumps({'method': event.method, 'payload': payload}, ensure_ascii=False) + '\n')
                log.flush()
                if event.method == 'item/agentMessage/delta':
                    print(payload.get('delta', ''), end='', flush=True)
                elif event.method == 'item/started':
                    item = payload.get('item', {})
                    if item.get('type') in ('commandExecution', 'imageView', 'fileChange'):
                        print(f"\n[{item['type']}]", flush=True)
                        if item['type'] == 'commandExecution':
                            print(item.get('command', '(command pending)'), flush=True)
                        elif item['type'] == 'imageView':
                            print(item.get('path', ''), flush=True)
                        else:
                            for change in item.get('changes', []):
                                print(change.get('path', ''), flush=True)
                elif event.method == 'item/completed':
                    item = payload.get('item', {})
                    if item.get('type') == 'commandExecution':
                        output = item.get('aggregatedOutput') or ''
                        if not verbose and len(output) > 3000:
                            print('[输出较长，仅显示末尾 3000 字符；完整内容见 events.jsonl 或使用 --verbose]')
                            output = output[-3000:]
                        if output:
                            print(output, flush=True)
                        print(f"[exit={item.get('exitCode')} · {item.get('status', '')}]", flush=True)
                elif event.method == 'turn/completed':
                    result = payload['turn']
                    if result.get('status') != 'completed':
                        raise RuntimeError(f"Agent turn {result.get('status')}: {result.get('error')}")
        print()
    except KeyboardInterrupt:
        turn.interrupt()
        raise


def chat(args):
    from openai_codex import Codex, CodexConfig, Sandbox, ApprovalMode
    if not authenticated():
        raise RuntimeError('Codex is not logged in. Run `pixi run rosbody login` first.')
    session = (Path(args.session).expanduser().resolve() if args.session else
               ROOT / 'runs' / 'chat' / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3)))
    if args.session and not (session / 'session.json').is_file():
        raise RuntimeError('Resume directory has no session.json.')
    session.mkdir(parents=True, exist_ok=True)
    with session_lock(session), domain_lease() as domain:
        meta_file = session / 'session.json'
        meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
        # A fresh domain prevents a restarted conversation from finding an old body.
        workspace = session / 'workspace'
        env = prepare_workspace(workspace, domain)
        for key in ('CODEX_THREAD_ID', 'CODEX_TURN_ID'):
            env.pop(key, None)
        config = CodexConfig(cwd=str(workspace), env=env)
        print(f'ROSBody session: {session}\nStarting a fresh simulation (seed={args.seed}).', flush=True)
        with simulation(session, env, args.seed, args.viewer), Codex(config) as codex:
            options = dict(cwd=str(workspace), sandbox=Sandbox.workspace_write,
                           approval_mode=ApprovalMode.deny_all,
                           config={'sandbox_workspace_write': {'network_access': True}},
                           developer_instructions='You are ROSBody’s independent simulation agent. Follow workspace AGENTS.md. Execute user tasks through the ROS body; never access real hardware.')
            if args.model:
                options['model'] = args.model
            resumed = bool(meta.get('thread_id'))
            thread = (codex.thread_resume(meta['thread_id'], **options) if resumed else codex.thread_start(**options))
            meta.update(thread_id=thread.id, domain=domain, workspace=str(workspace), seed=args.seed)
            save_metadata(meta_file, meta)
            print(f'Codex thread: {thread.id}\n输入任务；/exit 退出。Ctrl+C 中断并关闭仿真。', flush=True)
            prompts = iter(args.prompt) if args.prompt else None
            first = True
            while True:
                try:
                    prompt = next(prompts) if prompts else input('\nROSBody > ')
                except (StopIteration, EOFError):
                    break
                if prompt.strip() in ('/exit', '/quit'):
                    break
                if not prompt.strip():
                    continue
                if first:
                    prompt = ('[Host notice: the simulator has just been created anew. Previous physical state is NOT restored. Read setup_body.md and obtain fresh observations before acting. Never replay previous goals.]\n' + prompt)
                    first = False
                run_turn(thread, prompt, session, args.verbose)
    return 0


def dispatch(args):
    try:
        if args.command == 'chat':
            return chat(args)
        if args.command == 'doctor':
            subprocess.run([runtime(), '--version'], check=True)
            subprocess.run([runtime(), 'login', 'status'], check=False)
            from .agent_workspace import body_environment
            body_environment(0)
            print('ROS 2 Jazzy interfaces ready. Authentication is checked without calling a model.')
            return 0 if authenticated() else 1
        if args.api_key:
            key = os.environ.get('OPENAI_API_KEY')
            if not key:
                raise RuntimeError('Set OPENAI_API_KEY in your shell; do not put keys in chat or files.')
            return subprocess.run([runtime(), 'login', '--with-api-key'], input=key, text=True).returncode
        return subprocess.run([runtime(), 'login']).returncode
    except KeyboardInterrupt:
        print('\nInterrupted; owned simulator closed. No automatic task replay.', file=sys.stderr)
        return 130
    except (RuntimeError, OSError) as exc:
        print(f'ROSBody: {exc}', file=sys.stderr)
        return 1
