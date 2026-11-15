#!/usr/bin/env python3
"""Linux LAN launch configuration. Requires only Python 3 and Docker on the host."""
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
import shlex
import socket
import struct
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def instance_name(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', value):
        raise argparse.ArgumentTypeError('invalid instance name')
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
        raise ValueError('instance path escapes runs')
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


def health(config):
    url = f"http://{config['AGENTICROS_DDS_HOST']}:{config['AGENTICROS_REGISTRY_PORT']}"
    with urllib.request.urlopen(url+'/health', timeout=3) as response:
        data = json.load(response)
    if data.get('instance_id') != config['AGENTICROS_INSTANCE_ID']:
        raise ValueError('server identity mismatch')
    return url


def server_url(value):
    url = urlsplit(value)
    if (url.scheme != 'http' or not url.hostname or url.username or url.password
            or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('server must be http://IPv4:port')
    ipv4(url.hostname)
    port(url.port or 80)
    return value.rstrip('/')


def lan_addresses():
    # Use Linux interfaces directly: no host ROS, iproute2 or third-party Python package.
    addresses = []
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
        if ipaddress.IPv4Address(address).is_loopback:
            raise ValueError('cannot advertise loopback to LAN peers; specify --host')
        return ipv4(address)
    addresses = lan_addresses()
    if len(addresses) == 1:
        return addresses[0][1]
    if not sys.stdin.isatty():
        raise ValueError('cannot select LAN IP automatically; set AGENTICROS_HOST or use server --host')
    print('请选择 client 能访问的本机局域网 IP：')
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
    return [json.loads(path.read_text()) for path in paths
            if path.name == 'server.json' or path.name.startswith('client-')]


def available_port(start, configs, udp=False, explicit=None):
    keys = ('AGENTICROS_DDS_PORT',) if udp else ('AGENTICROS_REGISTRY_PORT', 'AGENTICROS_OBSERVATION_PORT')
    reserved = {c[k] for c in configs for k in keys if k in c}
    for number in ([explicit] if explicit is not None else range(start, 65536)):
        if number in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('0.0.0.0', number))
            except OSError:
                continue
        return number
    raise ValueError('requested port is reserved/in use, or no free port is available')


def server_config(instance, host=None, registry_port=None, discovery_port=None, domain=None):
    with config_lock():
        path = server_file(instance)
        if path.exists():
            config = json.loads(path.read_text())
            requested = dict(AGENTICROS_DDS_HOST=host, AGENTICROS_REGISTRY_PORT=registry_port,
                             AGENTICROS_DDS_PORT=discovery_port, ROS_DOMAIN_ID=domain)
            if any(v is not None and v != config[k] for k, v in requested.items()):
                raise ValueError('saved server configuration differs; refusing to reconfigure a live instance')
            return config
        host = ipv4(host) if host else detect_host()
        saved = saved_configs()
        domains = {c['ROS_DOMAIN_ID'] for c in saved if 'ROS_DOMAIN_ID' in c}
        if domain is None:
            domain = next((d for d in range(43, 102) if d not in domains), None)
        if domain is None or not 0 <= domain <= 101 or domain in domains or domain == 42:
            raise ValueError('ROS domain is unavailable (42 is reserved for legacy single-host mode)')
        config = dict(AGENTICROS_INSTANCE=instance, AGENTICROS_INSTANCE_ID=secrets.token_hex(16),
                      AGENTICROS_DDS_HOST=host,
                      AGENTICROS_REGISTRY_PORT=available_port(7447, saved, explicit=registry_port),
                      AGENTICROS_DDS_PORT=available_port(11811, saved, udp=True, explicit=discovery_port),
                      ROS_DOMAIN_ID=domain,
                      AGENTICROS_DDS_PREFIX='.'.join(f'{x:02x}' for x in secrets.token_bytes(12)),
                      AGENTICROS_JOIN_TOKEN=secrets.token_hex(32))
        write_new(path, config)
        return config


def ensure_server(config):
    try:
        return health(config)
    except (OSError, urllib.error.URLError):
        # Never replace an existing registry: clients depend on its live epoch.
        run(config, ['up', '-d', '--build', '--no-recreate', '--wait', 'server'])
        return health(config)


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
        raise ValueError('invalid join code; copy the complete code printed by the server') from None


def print_join(config):
    print('\nInstance:', config['AGENTICROS_INSTANCE'], '  Server:',
          f"http://{config['AGENTICROS_DDS_HOST']}:{config['AGENTICROS_REGISTRY_PORT']}")
    print('在 client 的仓库目录执行（每台使用不同的 --robot-id）：')
    print('bash scripts/environment.sh simple_mujoco --join '+shlex.quote(join_code(config))+' --robot-id robot_a')
    print('连接码包含加入令牌，请只交给需要加入的 client。\n', flush=True)


def check_server_identity(url, instance, identity):
    with urllib.request.urlopen(url+'/health', timeout=3) as response:
        actual = json.load(response)
    if actual.get('instance_id') != identity or actual.get('instance') != instance:
        raise ValueError('server identity does not match join code')


def client_config(args):
    join = read_join_code(args.join) if args.join else None
    if join:
        if args.instance and args.instance != join['instance']:
            raise ValueError('--instance conflicts with join code')
        if args.server and server_url(args.server) != join['server']:
            raise ValueError('--server conflicts with join code')
        args.instance, args.server = join['instance'], join['server']
    if not args.instance:
        raise ValueError('provide --join CODE, or --instance for a saved client')
    with config_lock():
        path = folder(args.instance)/'lan'/('client-'+args.robot_id+'.json')
        if path.exists():
            config = json.loads(path.read_text())
            if ((args.server and server_url(args.server) != config['AGENTICROS_SERVER'])
                    or (args.host and args.host != config['AGENTICROS_CLIENT_HOST'])
                    or (args.observation_port and args.observation_port != config['AGENTICROS_OBSERVATION_PORT'])
                    or (join and (join['token'] != config['AGENTICROS_JOIN_TOKEN']
                                  or join['instance_id'] != config.get('AGENTICROS_INSTANCE_ID', join['instance_id'])))):
                raise ValueError('saved client binding differs; refusing to change ownership')
            identity = config.get('AGENTICROS_INSTANCE_ID') or (join and join['instance_id'])
            if identity and not args.stop:
                check_server_identity(config['AGENTICROS_SERVER'], args.instance, identity)
            return config
        if args.stop:
            raise ValueError('client configuration not found')
        token = join['token'] if join else os.getenv('AGENTICROS_JOIN_TOKEN', '')
        if not args.server or not token:
            raise ValueError('first start requires --join CODE, or --server and AGENTICROS_JOIN_TOKEN')
        url = server_url(args.server)
        if join:
            check_server_identity(url, args.instance, join['instance_id'])
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
    server = sub.add_parser('server', help='start instance registry and ROS discovery; never starts a body')
    server.add_argument('--instance', type=instance_name, default='default')
    server.add_argument('--host', type=ipv4, default=os.getenv('AGENTICROS_HOST'))
    server.add_argument('--port', type=port)
    server.add_argument('--discovery-port', type=port)
    server.add_argument('--domain-id', type=int)
    for command in ('status', 'token', 'info', 'agent', 'stop-server'):
        p = sub.add_parser(command)
        p.add_argument('--instance', type=instance_name, default='default')
        if command == 'agent':
            p.add_argument('codex_args', nargs=argparse.REMAINDER)
    client = sub.add_parser('client')
    client.add_argument('--instance', type=instance_name)
    client.add_argument('--join', help='connection code printed by agenticros.sh')
    client.add_argument('--robot-id', type=robot_name, required=True)
    client.add_argument('--server')
    client.add_argument('--host', type=ipv4)
    client.add_argument('--observation-port', type=port)
    client.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('LAN v1 requires Linux host networking')
    if args.command == 'server':
        config = server_config(args.instance, args.host, args.port, args.discovery_port, args.domain_id)
        ensure_server(config)
        print_join(config)
    elif args.command == 'client':
        config = client_config(args)
        run(config, ['stop', 'client'] if args.stop else ['up', '-d', '--build', '--no-recreate', '--wait', 'client'], client=True)
        print('Client:', args.robot_id, 'instance:', args.instance)
    else:
        config = (server_config(args.instance, os.getenv('AGENTICROS_HOST')) if args.command == 'agent'
                  else json.loads(server_file(args.instance).read_text()))
        if args.command == 'stop-server':
            run(config, ['stop', 'server'])
        elif args.command == 'info':
            print_join(config)
        elif args.command == 'token':
            print(config['AGENTICROS_JOIN_TOKEN'])
        elif args.command == 'status':
            with urllib.request.urlopen(health(config)+'/robots', timeout=3) as response:
                print(json.dumps(json.load(response), indent=2))
        else:
            ensure_server(config)
            subprocess.run([sys.executable, 'scripts/prepare_workspace.py'], cwd=ROOT,
                           env=environment(config), check=True)
            run(config, ['build', 'agenticros'])
            extra = args.codex_args
            if extra[:1] == ['--']:
                extra = extra[1:]
            cmd = compose(config)+['run', '--rm', '--no-deps', '--name', 'agenticros-controller-'+args.instance]
            if os.getenv('AGENTICROS_MODEL_API_KEY'):
                cmd += ['-e', 'AGENTICROS_MODEL_API_KEY']
            cmd += ['agenticros', 'agenticros', 'start', *extra]
            print_join(config)
            os.chdir(ROOT)
            os.execvpe(cmd[0], cmd, environment(config))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, argparse.ArgumentTypeError, subprocess.CalledProcessError) as exc:
        print(f'LAN startup failed: {exc}', file=sys.stderr)
        sys.exit(1)
