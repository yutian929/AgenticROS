#!/usr/bin/env python3
"""Launch Agent instances and Robot instances on Linux/macOS. Requires Python 3 and Docker."""
import argparse
import base64
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def instance_name(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', value):
        raise argparse.ArgumentTypeError('invalid Agent instance name')
    return value


def robot_name(value):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', value):
        raise argparse.ArgumentTypeError('robot_id must use lowercase letters, digits and underscores')
    return value


def ipv4(value):
    host = ipaddress.IPv4Address(value)
    if host.is_unspecified or host.is_multicast:
        raise argparse.ArgumentTypeError('supply a reachable host IPv4 address')
    return str(host)


def port(value):
    number = int(value)
    if not 1024 <= number <= 65535:
        raise argparse.ArgumentTypeError('port must be 1024..65535')
    return number


def folder(instance):
    path = ROOT/'runs' if instance == 'default' else ROOT/'runs/instances'/instance
    if (ROOT/'runs').resolve() not in (path.resolve(), *path.resolve().parents):
        raise ValueError('Agent instance path escapes runs')
    return path


def server_file(instance):
    return folder(instance)/'lan/server.json'


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, indent=2)


def environment(config):
    env = os.environ.copy()
    env.update({k: str(v) for k, v in config.items()})
    # Host-network containers on the same machine can use loopback for HTTP.
    # Keep the advertised address and persisted ownership unchanged for LAN peers.
    server = config.get('AGENTICROS_SERVER')
    if server:
        address = urlsplit(server)
        if address.hostname == config.get('AGENTICROS_CLIENT_HOST'):
            env['AGENTICROS_SERVER'] = f'http://127.0.0.1:{address.port}'
    name = config['AGENTICROS_INSTANCE']
    env['AGENTICROS_WORKSPACE_SUBDIR'] = 'workspace' if name == 'default' else f'instances/{name}/workspace'
    env['AGENTICROS_INSTANCE_SUFFIX'] = '' if name == 'default' else '-instance-'+name
    env['AGENTICROS_WORKSPACE_GID'] = str(os.getgid())
    return env


def compose(config, client=False):
    name = 'agenticros-lan-'+config['AGENTICROS_INSTANCE']
    if client:
        name += '-'+config['AGENTICROS_ROBOT_ID'].replace('_', '-')
    return ['docker', 'compose', '-p', name, '-f', 'compose.client.yaml' if client else 'compose.lan.yaml']


def run(config, args, client=False):
    subprocess.run(compose(config, client)+args, env=environment(config), cwd=ROOT, check=True)


def ensure_image(config, service, client=False):
    images = {'client': 'agenticros-simple_mujoco:lan', 'instance': 'agenticros-instance:lan'}
    result = subprocess.run(['docker', 'image', 'inspect', images[service]],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        run(config, ['build', service], client=client)


def health(config):
    url = f"http://{config['AGENTICROS_DDS_HOST']}:{config['AGENTICROS_REGISTRY_PORT']}"
    with urllib.request.urlopen(url+'/health', timeout=3) as response:
        data = json.load(response)
    if data.get('instance_id') != config['AGENTICROS_INSTANCE_ID']:
        raise ValueError('Agent instance registry identity mismatch')
    if data.get('discovery_transport', 'udp') != config.get('AGENTICROS_DDS_TRANSPORT', 'udp'):
        raise ValueError('Agent instance transport does not match saved configuration; rebuild the Agent instance image '
                         'and explicitly restart the idle instance (no automatic replacement)')
    return url


def server_url(value):
    url = urlsplit(value)
    if (url.scheme != 'http' or not url.hostname or url.username or url.password
            or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('--server must be the Agent instance registry URL: http://IPv4:port')
    ipv4(url.hostname)
    port(url.port or 80)
    return value.rstrip('/')


def lan_addresses():
    # Use each OS's interface API; never infer the advertised IP from Docker's VM.
    addresses = []
    if sys.platform == 'darwin':
        for _, name in socket.if_nameindex():
            if not name.startswith('en'):
                continue
            result = subprocess.run(['/usr/sbin/ipconfig', 'getifaddr', name],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                address = ipv4(result.stdout.strip())
                if not ipaddress.IPv4Address(address).is_loopback:
                    addresses.append((name, address))
        return addresses
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _, name in socket.if_nameindex():
            if not (Path('/sys/class/net')/name/'device').exists():
                continue
            try:
                data = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack('256s', name.encode()[:15]))
                address = socket.inet_ntoa(data[20:24])
                if not ipaddress.IPv4Address(address).is_loopback:
                    addresses.append((name, address))
            except OSError:
                continue
    return addresses


def detect_host(server=None):
    if server:
        # UDP connect selects a route without sending traffic to the destination.
        url = urlsplit(server)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((url.hostname, url.port))
            address = sock.getsockname()[0]
        if ipaddress.IPv4Address(address).is_loopback and not ipaddress.IPv4Address(url.hostname).is_loopback:
            raise ValueError('cannot advertise loopback to LAN peers; specify --host')
        return ipv4(address)
    addresses = lan_addresses()
    if len(addresses) == 1:
        return addresses[0][1]
    if not sys.stdin.isatty():
        raise ValueError('cannot select LAN IP automatically; set AGENTICROS_HOST or use server --host')
    print('请选择 Client 主机能访问的 Server 主机局域网 IP：')
    for index, (name, address) in enumerate(addresses, 1):
        print(f'  {index}. {name}: {address}')
    answer = input('输入序号或本机 IPv4 地址: ').strip()
    if answer.isdigit() and 1 <= int(answer) <= len(addresses):
        return addresses[int(answer)-1][1]
    return ipv4(answer)


@contextlib.contextmanager
def config_lock():
    (ROOT/'runs').mkdir(exist_ok=True)
    with (ROOT/'runs/.lan-config.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def saved_configs():
    paths = list((ROOT/'runs/lan').glob('*.json'))
    paths += list((ROOT/'runs/instances').glob('*/lan/server.json'))
    paths += list((ROOT/'runs/instances').glob('*/lan/client-*.json'))
    paths += list((ROOT/'runs/instances').glob('*/lan/viewer.json'))
    return [json.loads(path.read_text()) for path in paths
            if path.name in ('server.json', 'viewer.json') or path.name.startswith('client-')]


def available_port(start, configs, udp=False, explicit=None, maximum=65535):
    keys = ('AGENTICROS_DDS_PORT',) if udp else ('AGENTICROS_DDS_PORT', 'AGENTICROS_REGISTRY_PORT', 'AGENTICROS_OBSERVATION_PORT',
                                               'AGENTICROS_VIEWER_PORT', 'AGENTICROS_VNC_PORT')
    if explicit is not None and not 1024 <= explicit <= maximum:
        raise ValueError(f'requested port must be 1024..{maximum}')
    reserved = {c[k] for c in configs for k in keys if k in c}
    for number in ([explicit] if explicit is not None else range(start, maximum + 1)):
        if number in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('0.0.0.0', number))
            except OSError:
                continue
        return number
    raise ValueError('requested port is reserved/in use, or no free port is available')


def server_config(instance, host=None, registry_port=None, discovery_port=None, domain=None, transport=None):
    if transport not in (None, 'udp', 'tcp'):
        raise ValueError('DDS transport must be udp or tcp')
    with config_lock():
        path = server_file(instance)
        if path.exists():
            config = json.loads(path.read_text())
            requested = dict(AGENTICROS_DDS_HOST=host, AGENTICROS_REGISTRY_PORT=registry_port,
                             AGENTICROS_DDS_PORT=discovery_port, ROS_DOMAIN_ID=domain)
            if any(v is not None and v != config[k] for k, v in requested.items()):
                raise ValueError('saved Agent instance configuration differs; refusing to reconfigure a live Agent instance')
            if transport is not None and transport != config.get('AGENTICROS_DDS_TRANSPORT', 'udp'):
                raise ValueError('saved transport differs; use a new Agent instance instead of changing a live network')
            validate_platform_transport(config.get('AGENTICROS_DDS_TRANSPORT', 'udp'))
            if config.get('AGENTICROS_DDS_TRANSPORT') == 'tcp' and config['AGENTICROS_DDS_PORT'] > 32767:
                raise ValueError('saved TCP discovery port exceeds 32767; use a new Agent instance for reliable reconnection')
            return config
        transport = transport or 'tcp'
        validate_platform_transport(transport)
        host = ipv4(host) if host else detect_host()
        saved = saved_configs()
        domains = {c['ROS_DOMAIN_ID'] for c in saved if 'ROS_DOMAIN_ID' in c}
        if domain is None:
            domain = next((d for d in range(43, 102) if d not in domains), None)
        if domain is None or not 0 <= domain <= 101 or domain in domains or domain == 42:
            raise ValueError('ROS domain is unavailable (42 is reserved for legacy single-host mode)')
        registry_port = available_port(7447, saved, explicit=registry_port)
        discovery_port = available_port(11811, saved + [{'AGENTICROS_REGISTRY_PORT': registry_port}],
                                        udp=transport == 'udp', explicit=discovery_port,
                                        maximum=32767 if transport == 'tcp' else 65535)
        config = dict(AGENTICROS_INSTANCE=instance, AGENTICROS_INSTANCE_ID=secrets.token_hex(16),
                      AGENTICROS_DDS_HOST=host,
                      AGENTICROS_REGISTRY_PORT=registry_port,
                      AGENTICROS_DDS_PORT=discovery_port,
                      AGENTICROS_DDS_TRANSPORT=transport,
                      ROS_DOMAIN_ID=domain,
                      AGENTICROS_DDS_PREFIX='.'.join(f'{x:02x}' for x in secrets.token_bytes(12)),
                      AGENTICROS_JOIN_TOKEN=secrets.token_hex(32))
        write_new(path, config)
        return config


def prepare_workspace(config):
    subprocess.run([sys.executable, 'scripts/prepare_workspace.py'], cwd=ROOT,
                   env=environment(config), check=True)


def validate_platform_transport(transport):
    if transport not in ('udp', 'tcp'):
        raise ValueError('unsupported Agent instance DDS transport')
    if sys.platform == 'darwin' and transport != 'tcp':
        raise ValueError('macOS requires a TCP Agent instance; create a new Agent instance with '
                         'AGENTICROS_DDS_TRANSPORT=tcp (saved UDP instances remain unchanged)')


def check_host_network(image):
    # Desktop forwarding can lag a new listener, and the host/VM ephemeral
    # port spaces can overlap. Retry only this disposable nonce probe, never
    # a service startup or robot operation.
    for attempt in range(3):
        try:
            return _check_host_network_once(image)
        except ValueError:
            if attempt == 2:
                raise
            time.sleep(.1)


def _check_host_network_once(image):
    """Probe Desktop's actual host forwarding, without touching existing containers."""
    if sys.platform != 'darwin':
        return
    name = 'agenticros-network-check-' + secrets.token_hex(4)
    nonce = secrets.token_hex(16).encode()
    # Test container -> host first, then host -> container; timeout also bounds
    # an engine with host networking disabled. No mounts or credentials.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(8)
        host_port = listener.getsockname()[1]
        code = ('import socket; '
                f'c=socket.create_connection(("127.0.0.1",{host_port}),timeout=5); '
                's=socket.socket(); s.bind(("0.0.0.0",0)); s.listen(1); s.settimeout(8); '
                'c.sendall(str(s.getsockname()[1]).encode()+b"\\n"); c.close(); '
                f'a,_=s.accept(); a.sendall({nonce!r}); a.close(); s.close()')
        try:
            subprocess.run(['docker', 'run', '-d', '--rm', '--name', name, '--network', 'host',
                            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                            '--entrypoint', 'python3', image, '-c', code],
                           check=True, stdout=subprocess.DEVNULL, timeout=30)
            with listener.accept()[0] as peer:
                peer.settimeout(3)
                number = int(peer.makefile('rb').readline(16))
            with socket.create_connection(('127.0.0.1', number), timeout=5) as peer:
                received = peer.makefile('rb').read(len(nonce))
                if received != nonce:
                    raise ValueError('unexpected network probe response')
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ValueError('Docker Desktop host networking is unavailable. Enable Settings → '
                             'Resources → Network → Enable host networking, then Apply & restart '
                             '(Docker Desktop 4.34+; Enhanced Container Isolation must be off).') from exc
        finally:
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15)


def ensure_server(config):
    try:
        return health(config)
    except (OSError, urllib.error.URLError):
        # Never replace a running instance: clients depend on its live epoch.
        config = viewer_config(config)
        prepare_workspace(config)
        ensure_image(config, 'instance')
        check_host_network('agenticros-instance:lan')
        run(config, ['up', '-d', '--no-build', '--pull', 'never', '--no-recreate', '--wait', 'instance'])
        return health(config)


def control(config, command):
    run(viewer_config(config), ['exec', '-T', 'instance', 'python3', '/opt/instance/runtime.py', command])


def viewer_config(config):
    with config_lock():
        path = folder(config['AGENTICROS_INSTANCE'])/'lan/viewer.json'
        if path.exists():
            settings = json.loads(path.read_text())
        else:
            saved = saved_configs()
            settings = dict(AGENTICROS_INSTANCE=config['AGENTICROS_INSTANCE'],
                            AGENTICROS_VIEWER_BIND=ipv4(os.getenv('AGENTICROS_VIEWER_BIND', '127.0.0.1')),
                            AGENTICROS_VIEWER_PORT=available_port(8080, saved),
                            AGENTICROS_VNC_PORT=available_port(5900, saved))
            write_new(path, settings)
        return dict(config, **settings)


def ensure_viewer(config):
    config = viewer_config(config)
    ensure_server(config)
    control(config, 'viewer-start')
    return config


def print_viewer(config):
    path = folder(config['AGENTICROS_INSTANCE'])/'lan/viewer.json'
    if not path.exists():
        return
    config = dict(config, **json.loads(path.read_text()))
    print('Viewer: http://'+config['AGENTICROS_VIEWER_BIND']+':'+str(config['AGENTICROS_VIEWER_PORT'])+'/vnc.html?autoconnect=1&resize=scale')
    result = subprocess.run(compose(config)+['exec', '-T', 'instance', 'cat', '/viewer-auth/password'],
                            cwd=ROOT, env=environment(config), text=True, capture_output=True)
    if result.returncode == 0:
        print('Viewer password:', result.stdout.strip(), flush=True)
    else:
        print('Viewer is stopped; start with: python3 scripts/lan.py viewer --instance '+config['AGENTICROS_INSTANCE'], flush=True)


def join_code(config):
    data = dict(v=1, instance=config['AGENTICROS_INSTANCE'], instance_id=config['AGENTICROS_INSTANCE_ID'],
                server=f"http://{config['AGENTICROS_DDS_HOST']}:{config['AGENTICROS_REGISTRY_PORT']}",
                token=config['AGENTICROS_JOIN_TOKEN'])
    return base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).decode().rstrip('=')


def read_join_code(code):
    try:
        if len(code) > 4096:
            raise ValueError()
        data = json.loads(base64.b64decode(code+'='*(-len(code) % 4), altchars=b'-_', validate=True))
        if data['v'] != 1 or not re.fullmatch('[0-9a-f]{32}', data['instance_id']):
            raise ValueError()
        instance_name(data['instance'])
        data['server'] = server_url(data['server'])
        if not re.fullmatch('[0-9a-f]{64}', data['token']):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, argparse.ArgumentTypeError):
        raise ValueError('invalid join code; copy the complete code printed by the Agent instance launcher') from None


def print_join(config):
    print('join码：' + join_code(config), flush=True)


def check_server_identity(url, instance, identity):
    with urllib.request.urlopen(url+'/health', timeout=3) as response:
        actual = json.load(response)
    if (identity is not None and actual.get('instance_id') != identity) or actual.get('instance') != instance:
        raise ValueError('Agent instance registry identity does not match join code')
    validate_platform_transport(actual.get('discovery_transport', 'udp'))
    return actual


def client_config(args):
    join = read_join_code(args.join) if args.join else None
    if join:
        if args.instance and args.instance != join['instance']:
            raise ValueError('Agent instance name (--instance) conflicts with join code')
        if args.server and server_url(args.server) != join['server']:
            raise ValueError('--server conflicts with join code')
        args.instance, args.server = join['instance'], join['server']
    if not args.instance:
        raise ValueError('provide --join CODE, or --instance naming the Agent instance for a saved Robot instance')
    with config_lock():
        path = folder(args.instance)/'lan'/('client-'+args.robot_id+'.json')
        if path.exists():
            config = json.loads(path.read_text())
            if ((args.server and server_url(args.server) != config['AGENTICROS_SERVER'])
                    or (args.host and args.host != config['AGENTICROS_CLIENT_HOST'])
                    or (args.observation_port and args.observation_port != config['AGENTICROS_OBSERVATION_PORT'])
                    or (join and (join['token'] != config['AGENTICROS_JOIN_TOKEN']
                                  or join['instance_id'] != config.get('AGENTICROS_INSTANCE_ID', join['instance_id'])))):
                raise ValueError('saved Robot instance binding differs; refusing to change ownership')
            identity = config.get('AGENTICROS_INSTANCE_ID') or (join and join['instance_id'])
            if identity and not args.stop:
                check_server_identity(config['AGENTICROS_SERVER'], args.instance, identity)
            elif not args.stop:
                check_server_identity(config['AGENTICROS_SERVER'], args.instance, None)
            return config
        if args.stop:
            raise ValueError('Robot instance configuration not found')
        token = join['token'] if join else os.getenv('AGENTICROS_JOIN_TOKEN', '')
        if not args.server or not token:
            raise ValueError('first start requires --join CODE, or --server and AGENTICROS_JOIN_TOKEN')
        url = server_url(args.server)
        if join:
            check_server_identity(url, args.instance, join['instance_id'])
        else:
            check_server_identity(url, args.instance, None)
        config = dict(AGENTICROS_INSTANCE=args.instance, AGENTICROS_ROBOT_ID=args.robot_id,
                      AGENTICROS_SERVER=url, AGENTICROS_CLIENT_HOST=args.host or detect_host(url),
                      AGENTICROS_OBSERVATION_PORT=available_port(8800, saved_configs(), explicit=args.observation_port),
                      AGENTICROS_JOIN_TOKEN=token)
        if join:
            config['AGENTICROS_INSTANCE_ID'] = join['instance_id']
        write_new(path, config)
        return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    server = sub.add_parser('server', help='start the Agent instance (registry, discovery and Viewer), without entering Codex')
    server.add_argument('--instance', type=instance_name, default='default', help='Agent instance name (default: default)')
    server.add_argument('--host', type=ipv4, default=os.getenv('AGENTICROS_HOST'), help='Server host IPv4 reachable by Client hosts')
    server.add_argument('--port', type=port)
    server.add_argument('--discovery-port', type=port)
    server.add_argument('--domain-id', type=int)
    server.add_argument('--transport', choices=('udp', 'tcp'), default=os.getenv('AGENTICROS_DDS_TRANSPORT'))
    for command in ('status', 'token', 'info', 'agent', 'login', 'viewer', 'stop-viewer', 'stop-server'):
        descriptions = {
            'status': 'list Robot instances registered with an Agent instance',
            'token': 'show the Agent instance join token',
            'info': 'show Agent instance connection and Viewer information',
            'agent': 'enter Codex in an Agent instance',
            'login': 'log in to Codex in an Agent instance',
            'viewer': 'start the Agent instance Viewer',
            'stop-viewer': 'stop the Agent instance Viewer',
            'stop-server': 'stop the Agent instance container, preserving its data',
        }
        p = sub.add_parser(command, help=descriptions[command])
        p.add_argument('--instance', type=instance_name, default='default', help='Agent instance name (default: default)')
        if command in ('agent', 'login'):
            p.add_argument('codex_args', nargs=argparse.REMAINDER)
    client = sub.add_parser('client', help='start or stop a Robot instance on this Client host')
    client.add_argument('--instance', type=instance_name, help='Agent instance to join; inferred from --join on first start')
    client.add_argument('--join', help='Agent instance connection code printed by agenticros.sh')
    client.add_argument('--robot-id', type=robot_name, required=True, help='Robot instance ID, unique within its Agent instance')
    client.add_argument('--server', help='Agent instance registry URL (included in --join)')
    client.add_argument('--host', type=ipv4, help='Client host IPv4 reachable by the Server host')
    client.add_argument('--observation-port', type=port)
    client.add_argument('--stop', action='store_true', help='stop this Robot instance, preserving its data')
    args = parser.parse_args()
    if sys.platform not in ('linux', 'darwin'):
        parser.error('Agent instance / Robot instance mode requires Linux or macOS with Docker Desktop')
    if args.command == 'server':
        config = server_config(args.instance, args.host, args.port, args.discovery_port, args.domain_id, args.transport)
        ensure_server(config)
        print_join(config)
    elif args.command == 'client':
        config = client_config(args)
        if not args.stop:
            ensure_image(config, 'client', client=True)
            check_host_network('agenticros-simple_mujoco:lan')
        run(config, ['stop', 'client'] if args.stop else
            ['up', '-d', '--no-build', '--pull', 'never', '--no-recreate', '--wait', 'client'], client=True)
        print('Robot instance:', args.robot_id, '  Agent instance:', args.instance)
    else:
        config = (server_config(args.instance, os.getenv('AGENTICROS_HOST'),
                                transport=os.getenv('AGENTICROS_DDS_TRANSPORT')) if args.command in ('agent', 'login')
                  else json.loads(server_file(args.instance).read_text()))
        if args.command == 'stop-server':
            run(viewer_config(config), ['stop', 'instance'])
        elif args.command == 'stop-viewer':
            control(config, 'viewer-stop')
        elif args.command == 'viewer':
            ensure_server(config)
            print_viewer(ensure_viewer(config))
        elif args.command == 'info':
            print_join(config)
            print_viewer(config)
        elif args.command == 'token':
            print(config['AGENTICROS_JOIN_TOKEN'])
        elif args.command == 'status':
            with urllib.request.urlopen(health(config)+'/robots', timeout=3) as response:
                print(json.dumps(json.load(response), indent=2))
        else:
            ensure_server(config)
            config = ensure_viewer(config)
            extra = args.codex_args
            if extra[:1] == ['--']:
                extra = extra[1:]
            cmd = compose(config)+['exec']
            if not sys.stdin.isatty():
                cmd += ['-T']
            if os.getenv('AGENTICROS_MODEL_API_KEY'):
                cmd += ['-e', 'AGENTICROS_MODEL_API_KEY']
            action = ['codex', 'login'] if args.command == 'login' else ['agenticros', 'start']
            cmd += ['instance', '/usr/local/bin/agent-entrypoint', 'python3',
                    '/opt/instance/runtime.py', 'attach', *action, *extra]
            print_join(config)
            print_viewer(config)
            os.chdir(ROOT)
            os.execvpe(cmd[0], cmd, environment(config))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, argparse.ArgumentTypeError, subprocess.CalledProcessError) as exc:
        print(f'LAN startup failed: {exc}', file=sys.stderr)
        sys.exit(1)
