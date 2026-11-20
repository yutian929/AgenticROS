#!/usr/bin/env python3
"""One Agent Server and multiple LAN robots. Requires Docker and Python 3."""
import argparse
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

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '44.53.00.5f.45.50.52.4f.53.49.4d.41'

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

def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, indent=2)

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
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((server, 11811))
            address = sock.getsockname()[0]
        if ipaddress.IPv4Address(address).is_loopback and not ipaddress.IPv4Address(server).is_loopback:
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

def server_file():
    return ROOT/'runs/server.json'


def client_file(robot):
    robot_name(robot)
    return ROOT/'runs/robots'/robot/'client.json'


def environment(config):
    env = os.environ.copy()
    env.update({k: str(v) for k, v in config.items()})
    env['AGENTICROS_WORKSPACE_GID'] = str(os.getgid())
    for key,value in dict(ROS_DOMAIN_ID=42,AGENTICROS_DDS_HOST='127.0.0.1',AGENTICROS_DDS_PORT=11811,AGENTICROS_DDS_PREFIX=PREFIX,AGENTICROS_DDS_TRANSPORT='tcp').items():
        env.setdefault(key,str(value))
    if 'AGENTICROS_ROBOT_ID' in config:
        env['AGENTICROS_OBSERVATION_SOURCE'] = str(client_file(config['AGENTICROS_ROBOT_ID']).parent/'observations')
    return env


def compose(config, client=False):
    name = 'agenticros-robot-'+config['AGENTICROS_ROBOT_ID'].replace('_','-') if client else 'agenticros'
    return ['docker','compose','-p',name,'-f','compose.client.yaml' if client else 'compose.yaml']


def run(config, args, client=False):
    subprocess.run(compose(config, client)+args, env=environment(config), cwd=ROOT, check=True)


def validate_platform_transport(transport):
    if transport not in ('tcp','udp'):raise ValueError('DDS transport must be tcp or udp')
    if sys.platform == 'darwin' and transport != 'tcp':
        raise ValueError('macOS Docker requires TCP; configure the entire LAN consistently')


def server_config(host=None, discovery_port=None, domain=None, transport=None):
    requested = dict(AGENTICROS_DDS_HOST=host,
                     AGENTICROS_DDS_PORT=discovery_port, ROS_DOMAIN_ID=domain,
                     AGENTICROS_DDS_TRANSPORT=transport)
    if server_file().exists():
        config = json.loads(server_file().read_text())
        if any(v is not None and v != config[k] for k,v in requested.items()):
            raise ValueError('saved configuration differs; stop the Server and edit runs/server.json explicitly')
    else:
        config = dict(AGENTICROS_DDS_HOST=host or detect_host(),
                      AGENTICROS_DDS_PORT=discovery_port or 11811,
                      ROS_DOMAIN_ID=42 if domain is None else domain,
                      AGENTICROS_DDS_TRANSPORT=transport or 'tcp', AGENTICROS_DDS_PREFIX=PREFIX,
                      AGENTICROS_VIEWER_BIND='127.0.0.1', AGENTICROS_VIEWER_PORT=8080,
                      AGENTICROS_VNC_PORT=5900)
    ipv4(config['AGENTICROS_DDS_HOST'])
    for key in ('AGENTICROS_DDS_PORT','AGENTICROS_VIEWER_PORT','AGENTICROS_VNC_PORT'): port(config[key])
    validate_platform_transport(config['AGENTICROS_DDS_TRANSPORT'])
    if not 0 <= config['ROS_DOMAIN_ID'] <= 101:
        raise ValueError('ROS_DOMAIN_ID must be 0..101')
    if config['AGENTICROS_DDS_TRANSPORT']=='tcp' and config['AGENTICROS_DDS_PORT'] > 32767:
        raise ValueError('TCP discovery port must be <=32767')
    if len({config[k] for k in ('AGENTICROS_DDS_PORT','AGENTICROS_VIEWER_PORT','AGENTICROS_VNC_PORT')}) != 3:
        raise ValueError('Server ports must be distinct')
    if not server_file().exists():
        for key in ('AGENTICROS_CODEX_VOLUME','AGENTICROS_VIEWER_VOLUME'):
            if os.getenv(key): config[key]=os.environ[key]
        write_new(server_file(), config)
    return config


def health(config):
    ids=subprocess.check_output(compose(config)+['ps','-q','server'],cwd=ROOT,env=environment(config),text=True).split()
    if not ids: raise OSError('Server is not running')
    actual=json.loads(subprocess.check_output(['docker','inspect',ids[0]],text=True))[0]
    variables=dict(v.split('=',1) for v in actual['Config']['Env'] if '=' in v)
    for key in ('ROS_DOMAIN_ID','AGENTICROS_DDS_HOST','AGENTICROS_DDS_PORT','AGENTICROS_DDS_PREFIX','AGENTICROS_DDS_TRANSPORT'):
        if variables.get(key)!=str(config[key]):raise ValueError('running Server network configuration differs; refusing replacement')
    run(config,['exec','-T','server','python3','/opt/instance/runtime.py','health'])
    return ids[0]


def ensure_image(config, service, client=False):
    image = 'agenticros-'+config['AGENTICROS_ENVIRONMENT']+':single' if client else 'agenticros-server:single'
    if subprocess.run(['docker','image','inspect',image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        run(config,['build',service],client)
    return image


def prepare_workspace(config):
    subprocess.run([sys.executable,'scripts/prepare_workspace.py'],cwd=ROOT,env=environment(config),check=True)


def ensure_server(config):
    try:
        return health(config)
    except OSError:
        prepare_workspace(config)
        image=ensure_image(config,'server')
        check_host_network(image)
        run(config,['up','-d','--no-build','--pull','never','--no-recreate','--wait','server'])
        return health(config)


def stage_agent_rules(config):
    return subprocess.check_output([sys.executable,'docker/agent_instance/agent/rules.py','stage',
                                    str(ROOT/'runs/workspace')],cwd=ROOT,text=True).strip()


def agent_command(config, action, extra, interactive):
    entrypoint=stage_agent_rules(config)
    cmd=compose(config)+['exec']+([] if interactive else ['-T'])
    if os.getenv('AGENTICROS_MODEL_API_KEY'):
        cmd+=['-e','AGENTICROS_MODEL_API_KEY']
    return cmd+['server','python3','/opt/instance/runtime.py','attach','bash',entrypoint,*action,*extra]


def client_config(args):
    path=client_file(args.robot_id)
    fields={'server':'AGENTICROS_DDS_HOST','host':'AGENTICROS_CLIENT_HOST','environment':'AGENTICROS_ENVIRONMENT',
            'observation_port':'AGENTICROS_OBSERVATION_PORT','domain_id':'ROS_DOMAIN_ID',
            'discovery_port':'AGENTICROS_DDS_PORT','transport':'AGENTICROS_DDS_TRANSPORT'}
    if path.exists():
        config=json.loads(path.read_text())
        if args.stop:return config
        if 'AGENTICROS_SERVER' in config:
            raise ValueError('Legacy HTTP binding: back up and update client.json using docs/usage.md before starting')
        for option,key in fields.items():
            value=getattr(args,option,None)
            if value is not None and value!=config[key]:
                raise ValueError('saved robot binding differs; stop robot and edit its client.json explicitly')
    else:
        if args.stop or not args.server:
            raise ValueError('first start requires --server SERVER_IP and --robot-id ID')
        config=dict(AGENTICROS_ROBOT_ID=args.robot_id,AGENTICROS_DDS_HOST=args.server,
                    AGENTICROS_CLIENT_HOST=args.host or detect_host(args.server),
                    AGENTICROS_OBSERVATION_PORT=args.observation_port or 8800,
                    AGENTICROS_ENVIRONMENT=args.environment or 'cartesian_gripper',
                    ROS_DOMAIN_ID=args.domain_id if args.domain_id is not None else 42,
                    AGENTICROS_DDS_PORT=args.discovery_port or 11811,
                    AGENTICROS_DDS_TRANSPORT=args.transport or 'tcp',AGENTICROS_DDS_PREFIX=PREFIX)
        validate_network(config)
        write_new(path,config)
    validate_network(config)
    return config


def validate_network(config):
    ipv4(config['AGENTICROS_DDS_HOST']);port(config['AGENTICROS_DDS_PORT'])
    validate_platform_transport(config['AGENTICROS_DDS_TRANSPORT'])
    if not 0<=config['ROS_DOMAIN_ID']<=101:raise ValueError('ROS_DOMAIN_ID must be 0..101')
    if config['AGENTICROS_DDS_TRANSPORT']=='tcp' and config['AGENTICROS_DDS_PORT']>32767:
        raise ValueError('TCP discovery port must be <=32767')


def prepare_observations(config):
    path=client_file(config['AGENTICROS_ROBOT_ID']).parent/'observations'
    for parent in [path,*path.parents]:
        if parent==ROOT: break
        if parent.is_symlink(): raise ValueError('Observation path contains a symlink')
    path.mkdir(parents=True,exist_ok=True)
    path.chmod(path.stat().st_mode | 0o2770)


def recreate_stopped(config, client=False):
    service='client' if client else 'server'
    ids=subprocess.check_output(compose(config,client)+['ps','-aq',service],cwd=ROOT,env=environment(config),text=True).split()
    if ids:
        states=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
        if any(x['State']['Running'] for x in states):
            raise ValueError('refusing to replace a running container; stop and reconcile its tasks first')
        run(config,['rm','-f',service],client)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    server=sub.add_parser('server')
    server.add_argument('--recreate',action='store_true',help='replace only a stopped Server container, retaining data')
    server.add_argument('--host',type=ipv4,default=os.getenv('AGENTICROS_HOST'))
    server.add_argument('--discovery-port',type=port)
    server.add_argument('--domain-id',type=int)
    server.add_argument('--transport',choices=('tcp','udp'),default=os.getenv('AGENTICROS_DDS_TRANSPORT'))
    for name in ('agent','login','status','info','stop-server','viewer','stop-viewer','reload-clients','viewer-password'):
        p=sub.add_parser(name)
        if name in ('agent','login'): p.add_argument('codex_args',nargs=argparse.REMAINDER)
    robot=sub.add_parser('robot')
    robot.add_argument('--robot-id',type=robot_name,required=True)
    robot.add_argument('--server',type=ipv4,help='fixed DDS Server IPv4 address; no HTTP registration')
    robot.add_argument('--domain-id',type=int)
    robot.add_argument('--discovery-port',type=port)
    robot.add_argument('--transport',choices=('tcp','udp'))
    robot.add_argument('--host',type=ipv4)
    robot.add_argument('--observation-port',type=port)
    robot.add_argument('--environment',choices=('cartesian_gripper','aloha_tabletop'))
    robot.add_argument('--recreate',action='store_true',help='replace only a stopped robot container, retaining data')
    robot.add_argument('--stop',action='store_true')
    args=parser.parse_args()
    if args.command in ('agent','login') and any(a in ('--agent_id','--instance') for a in args.codex_args):
        parser.error('single Server: instance selection was removed')
    if args.command=='robot':
        if args.stop and args.recreate: parser.error('--stop and --recreate are mutually exclusive')
        config=client_config(args)
        if args.recreate: recreate_stopped(config,True)
        if not args.stop:
            prepare_observations(config)
            check_host_network(ensure_image(config,'client',True))
        run(config,['stop','client'] if args.stop else ['up','-d','--no-build','--pull','never','--no-recreate','--wait','client'],True)
        return
    config=server_config(args.host,args.discovery_port,args.domain_id,args.transport) if args.command=='server' else server_config(os.getenv('AGENTICROS_HOST'))
    if args.command=='server' and args.recreate: recreate_stopped(config)
    if args.command=='stop-server': run(config,['stop','server'])
    elif args.command in ('viewer','stop-viewer','reload-clients'):
        command={'viewer':'viewer-start','stop-viewer':'viewer-stop','reload-clients':'clients-reload'}[args.command]
        run(config,['exec','-T','server','python3','/opt/instance/runtime.py',command])
    elif args.command=='viewer-password':
        run(config,['exec','-T','server','cat','/viewer-auth/password'])
    elif args.command=='status':
        run(config,['exec','-T','server','agenticros-ros','status'])
    elif args.command in ('agent','login'):
        ensure_server(config)
        extra=args.codex_args
        if extra[:1]==['--']: extra=extra[1:]
        action=['codex','login'] if args.command=='login' else ['agenticros','start']
        cmd=agent_command(config,action,extra,sys.stdin.isatty())
        os.chdir(ROOT); os.execvpe(cmd[0],cmd,environment(config))
    else:
        if args.command=='server': ensure_server(config)
        print('DDS Server: '+config['AGENTICROS_DDS_HOST']+':'+str(config['AGENTICROS_DDS_PORT']))
        print('Viewer: http://'+config['AGENTICROS_VIEWER_BIND']+':'+str(config['AGENTICROS_VIEWER_PORT'])+'/vnc.html')
        print('Viewer password: python3 scripts/lan.py viewer-password')


if __name__=='__main__':
    try: main()
    except (ValueError,OSError,argparse.ArgumentTypeError,subprocess.CalledProcessError) as exc:
        print('Startup failed: '+str(exc),file=sys.stderr);sys.exit(1)
