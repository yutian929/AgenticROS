#!/usr/bin/env python3
"""Manage Agent, Viewer and robot containers using native ROS discovery."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import secrets
import shutil

ROOT = Path(__file__).resolve().parents[1]

def robot_name(value):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,95}', value):
        raise argparse.ArgumentTypeError('robot_id must use lowercase letters, digits and underscores')
    return value


def robot_types():
    root = ROOT/'docker/robot_instances'
    return sorted(p.name for p in root.glob('*') if p.name not in ('common','robot_docs')
                  and re.fullmatch(r'[a-z][a-z0-9_]{0,62}', p.name)
                  and not p.is_symlink() and (p/'Dockerfile').is_file())


def require_robot_type(value):
    if value not in robot_types():
        raise ValueError('unknown robot type: '+str(value)+'; available: '+', '.join(robot_types()))
    return value


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

def runtime_dir(domain=42):
    return ROOT/'runs'/str(domain_id(domain))


def server_file(domain=42, role='agent'):
    return runtime_dir(domain)/'agent_instance'/role/'config.json'


def client_file(robot, domain=42, robot_type=None):
    robot_name(robot)
    root = runtime_dir(domain)/'robot_instance'
    existing = list(root.glob('*/'+robot+'/client.json'))
    if len(existing) > 1:
        raise ValueError('duplicate robot ID in domain')
    if existing: return existing[0]
    if not robot_type:
        raise ValueError('no saved robot configuration; supply --environment for a new instance')
    require_robot_type(robot_type)
    return root/robot_type/robot/'client.json'


def environment(config):
    env = os.environ.copy()
    env.update({k: str(v) for k, v in config.items()})
    env['AGENTICROS_WORKSPACE_GID'] = str(os.getgid())
    env['AGENTICROS_HOST_UID'] = str(os.getuid())
    domain = domain_id(config.get('ROS_DOMAIN_ID'))
    env['ROS_DOMAIN_ID'] = str(domain)
    for key, value in dict(AGENTICROS_CODEX_VOLUME=f'agenticros-{domain}_codex-home',
                          AGENTICROS_VIEWER_VOLUME=f'agenticros-{domain}_viewer-auth',
                          AGENTICROS_VIEWER_PORT=8038+domain, AGENTICROS_VNC_PORT=5858+domain).items():
        env[key] = str(config.get(key, value))
    env['AGENTICROS_WORKSPACE'] = str(runtime_dir(config.get('ROS_DOMAIN_ID'))/'agent_instance/agent/workspace')
    return env


def compose(config, client=False):
    name = 'agenticros-'+str(domain_id(config['ROS_DOMAIN_ID']))
    if client: name += '-robot-'+config['AGENTICROS_ROBOT_ID'].replace('_','-')
    command = ['docker','compose','-p',name,'-f','compose.robot.yaml' if client else 'compose.agent.yaml']
    if client:
        kind = require_robot_type(config['AGENTICROS_ENVIRONMENT'])
        override = ROOT/'docker/robot_instances'/kind/'compose.yaml'
        if override.is_file():
            command += ['-f', str(override)]
    return command


def run(config, args, client=False):
    subprocess.run(compose(config, client)+args, env=environment(config), cwd=ROOT, check=True)


def domain_id(value=None):
    value=int(42 if value is None else value)
    if not 0 <= value <= 101: raise ValueError('ROS_DOMAIN_ID must be 0..101')
    return value


def server_config(domain=None, role='agent'):
    domain = domain_id(domain)
    path = server_file(domain, role)
    if path.exists():
        config = json.loads(path.read_text())
        if config['ROS_DOMAIN_ID'] != domain:
            raise ValueError('saved configuration domain differs from its directory')
    else:
        config = dict(ROS_DOMAIN_ID=domain, AGENTICROS_VIEWER_BIND='127.0.0.1',
                      AGENTICROS_VIEWER_PORT=8038+domain, AGENTICROS_VNC_PORT=5858+domain)
        write_new(path, config)
    for key in ('AGENTICROS_VIEWER_PORT','AGENTICROS_VNC_PORT'): port(config[key])
    return config


def health(config):
    ids=subprocess.check_output(compose(config)+['ps','-q','server'],cwd=ROOT,env=environment(config),text=True).split()
    if not ids: raise OSError('Server is not running')
    actual=json.loads(subprocess.check_output(['docker','inspect',ids[0]],text=True))[0]
    variables=dict(v.split('=',1) for v in actual['Config']['Env'] if '=' in v)
    if variables.get('FASTRTPS_DEFAULT_PROFILES_FILE') or variables.get('AGENTICROS_DDS_HOST'):
        raise ValueError('running Server uses legacy discovery; explicitly stop, rebuild and recreate it')
    for key in ('ROS_DOMAIN_ID',):
        if variables.get(key)!=str(config[key]):raise ValueError('running Server network configuration differs; refusing replacement')
    docs = next((m for m in actual.get('Mounts', []) if m['Destination']=='/workspace/robot_docs'), None)
    expected = str(ROOT/'docker/robot_instances/robot_docs')
    if not docs or docs.get('RW') or docs.get('Source','').removeprefix('/host_mnt') != expected:
        raise ValueError('running Agent lacks the read-only robot_docs mount; stop it, then run server --recreate with the same --domain-id')
    run(config,['exec','-T','server','python3','/opt/instance/runtime.py','health'])
    return ids[0]


def check_running_robot(config):
    ids=subprocess.check_output(compose(config,True)+['ps','-q','client'],cwd=ROOT,env=environment(config),text=True).split()
    for cid in ids:
        actual=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]
        variables=dict(v.split('=',1) for v in actual['Config']['Env'] if '=' in v)
        if (variables.get('FASTRTPS_DEFAULT_PROFILES_FILE') or variables.get('AGENTICROS_DDS_HOST')
                or variables.get('ROS_DOMAIN_ID') != str(config['ROS_DOMAIN_ID'])):
            raise ValueError('running robot uses different/legacy discovery; explicitly stop, rebuild and recreate it')


def ensure_image(config, service, client=False):
    image = 'agenticros-'+config['AGENTICROS_ENVIRONMENT']+':single' if client else 'agenticros-'+service+':single'
    if subprocess.run(['docker','image','inspect',image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        run(config,['build',service],client)
    if client:
        check = json.loads(subprocess.check_output(['docker','image','inspect',image,'--format','{{json .Config.Healthcheck}}'],text=True))
        if not check or not check.get('Test') or check['Test'][0]=='NONE':
            raise ValueError('robot image must define its own HEALTHCHECK: '+image)
    return image


def prepare_workspace(config):
    subprocess.run([sys.executable,'scripts/prepare_workspace.py'],cwd=ROOT,env=environment(config),check=True)


def prepare_sam3(config):
    """Stage only public IDL; model weights and implementation stay outside Agent."""
    root = runtime_dir(config['ROS_DOMAIN_ID'])/'agent_instance/sam3'
    for name in ('records',):
        path = root/name
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('SAM3 data directories must not be symlinks')
        path.mkdir(parents=True, exist_ok=True)
    prepare_workspace(config)
    source = ROOT/'docker/agent_instance/sam3/interfaces'
    target = runtime_dir(config['ROS_DOMAIN_ID'])/'agent_instance/agent/workspace/ros_ws/src/agenticros_perception_interfaces'
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError('Public interface destination must not be a symlink')
    if target.exists():
        expected = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        actual = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
        if any(p.is_symlink() for p in target.rglob('*')) or expected != actual:
            raise ValueError('Existing perception interfaces differ; preserve and reconcile them explicitly: '+str(target))
    else:
        for path in reversed((target.parent, target.parent.parent)):
            if not path.exists():
                path.mkdir()
                path.chmod(0o2770)
        shutil.copytree(source, target)
        for path in (target, *target.rglob('*')):
            path.chmod(0o2775 if path.is_dir() else 0o664)


def ensure_server(config):
    try:
        return health(config)
    except OSError:
        prepare_workspace(config)
        ensure_image(config,'server')
        run(config,['up','-d','--no-build','--pull','never','--no-recreate','--wait','server'])
        return health(config)


def stage_agent_rules(config):
    return subprocess.check_output([sys.executable,'docker/agent_instance/agent/rules.py','stage',
                                    str(runtime_dir(config['ROS_DOMAIN_ID'])/'agent_instance/agent/workspace')],cwd=ROOT,text=True).strip()


def agent_command(config, action, extra, interactive):
    entrypoint=stage_agent_rules(config)
    cmd=compose(config)+['exec']+([] if interactive else ['-T'])
    if os.getenv('AGENTICROS_MODEL_API_KEY'):
        cmd+=['-e','AGENTICROS_MODEL_API_KEY']
    return cmd+['server','python3','/opt/instance/runtime.py','attach','bash',entrypoint,*action,*extra]


def client_config(args):
    if not args.robot_id:
        if args.stop or getattr(args,'recreate',False):
            raise ValueError('--robot-id is required to manage an existing instance')
        kind = require_robot_type(args.environment)
        root = runtime_dir(args.domain_id)/'robot_instance'/kind
        root.mkdir(parents=True, exist_ok=True)
        for _ in range(16):
            identity = kind+'_'+secrets.token_hex(4)
            try:
                (root/identity).mkdir()  # Reserve atomically, including concurrent launches.
            except FileExistsError:
                continue
            args.robot_id = identity
            break
        else:
            raise ValueError('could not allocate a new robot ID; retry startup')
    path=client_file(args.robot_id, args.domain_id, args.environment)
    fields={'environment':'AGENTICROS_ENVIRONMENT','domain_id':'ROS_DOMAIN_ID'}
    if path.exists():
        config=json.loads(path.read_text())
        if (config['AGENTICROS_ROBOT_ID'] != args.robot_id or
                config['ROS_DOMAIN_ID'] != domain_id(args.domain_id) or
                config['AGENTICROS_ENVIRONMENT'] != path.parent.parent.name):
            raise ValueError('saved robot configuration differs from its directory')
        if args.stop:return config
        if 'AGENTICROS_SERVER' in config:
            raise ValueError('Legacy HTTP binding: back up and update client.json using docs/usage.md before starting')
        for option,key in fields.items():
            value=getattr(args,option,None)
            if value is not None and value!=config[key]:
                raise ValueError('saved robot binding differs; stop robot and edit its client.json explicitly')
    else:
        if args.stop or getattr(args,'recreate',False):
            raise ValueError('no saved robot configuration')
        config=dict(AGENTICROS_ROBOT_ID=args.robot_id,
                    AGENTICROS_ENVIRONMENT=require_robot_type(args.environment),
                    ROS_DOMAIN_ID=domain_id(args.domain_id))
        validate_network(config)
        write_new(path,config)
    validate_network(config)
    require_robot_type(config['AGENTICROS_ENVIRONMENT'])
    return config


def validate_network(config):
    domain_id(config['ROS_DOMAIN_ID'])


def recreate_stopped(config, client=False, service=None):
    service=service or ('client' if client else 'server')
    ids=subprocess.check_output(compose(config,client)+['ps','-aq',service],cwd=ROOT,env=environment(config),text=True).split()
    if ids:
        states=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
        if any(x['State']['Running'] for x in states):
            raise ValueError('refusing to replace a running container; stop and reconcile its tasks first')
        run(config,['rm','-f',service],client)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('robot-types',help='list robot directories containing a Dockerfile')
    server=sub.add_parser('server')
    server.add_argument('--recreate',action='store_true',help='replace only a stopped Server container, retaining data')
    server.add_argument('--domain-id',type=int)
    for name in ('agent','login','status','info','stop-server','viewer','stop-viewer','reload-clients','viewer-password','sam3','stop-sam3','sam3-status'):
        p=sub.add_parser(name)
        p.add_argument('--domain-id',type=int)
        if name in ('agent','login'): p.add_argument('codex_args',nargs=argparse.REMAINDER)
        if name == 'sam3': p.add_argument('--recreate',action='store_true',help='replace only a stopped SAM3 container, retaining records and weights')
    robot=sub.add_parser('robot')
    robot.add_argument('--robot-id',type=robot_name,help='existing instance ID for stop/recreate; generated on new starts')
    robot.add_argument('--domain-id',type=int)
    robot.add_argument('--environment',help='robot directory name; list with robot-types')
    robot.add_argument('--recreate',action='store_true',help='replace only a stopped robot container, retaining data')
    robot.add_argument('--stop',action='store_true')
    robot.add_argument('--shell',action='store_true',help='open Bash after startup when attached to a terminal')
    args=parser.parse_args()
    if args.command=='robot-types':
        print('\n'.join(robot_types()))
        return
    if args.command in ('agent','login') and any(a in ('--agent_id','--instance') for a in args.codex_args):
        parser.error('single Server: instance selection was removed')
    if args.command=='robot':
        if args.stop and args.recreate: parser.error('--stop and --recreate are mutually exclusive')
        config=client_config(args)
        if args.recreate: recreate_stopped(config,True)
        if not args.stop:
            check_running_robot(config)
            ensure_image(config,'client',True)
        run(config,['stop','client'] if args.stop else ['up','-d','--no-build','--pull','never','--no-recreate','--wait','client'],True)
        if not args.stop:
            print('Robot: '+config['AGENTICROS_ROBOT_ID'])
            print('Namespace: /robot/'+config['AGENTICROS_ROBOT_ID'])
            print('ROS_DOMAIN_ID: '+str(config['ROS_DOMAIN_ID']), flush=True)
            if args.shell and sys.stdin.isatty():
                print('Entering robot shell; Ctrl+D exits the shell and keeps the robot running.', flush=True)
                run(config,['exec','client','bash','-c',
                    'source /opt/ros/${ROS_DISTRO:-humble}/setup.bash && '
                    'if [ -f /opt/driver_ws/install/setup.bash ]; then source /opt/driver_ws/install/setup.bash; fi; '
                    'exec bash --norc -i'],True)
        return
    role = ('sam3' if args.command in ('sam3','stop-sam3','sam3-status') else
            'viewer' if args.command in ('viewer','stop-viewer','viewer-password','info') else 'agent')
    config=server_config(getattr(args,'domain_id',None), role)
    if args.command=='server' and args.recreate: recreate_stopped(config)
    if args.command=='stop-server': run(config,['stop','server'])
    elif args.command=='viewer':
        ensure_image(config,'viewer')
        run(config,['up','-d','--no-build','--pull','never','--no-recreate','--wait','viewer'])
        password = subprocess.check_output(compose(config)+['exec','-T','viewer','cat','/viewer-auth/password'],
                                           cwd=ROOT, env=environment(config), text=True).strip()
        print('Viewer: http://'+config['AGENTICROS_VIEWER_BIND']+':'+str(config['AGENTICROS_VIEWER_PORT'])+'/vnc.html')
        print('Password: '+password)
    elif args.command=='stop-viewer':
        run(config,['stop','viewer'])
    elif args.command=='sam3':
        prepare_sam3(config)
        if args.recreate: recreate_stopped(config, service='sam3')
        ensure_image(config,'sam3')
        run(config,['up','-d','--no-build','--pull','never','--no-recreate','sam3'])
        run(config,['exec','-T','sam3','/usr/local/bin/sam3-entrypoint','python3',
                    '/opt/sam3/healthcheck.py','--wait','--timeout','900'])
        print('SAM3: /perception/sam3/segment (ROS_DOMAIN_ID '+str(config['ROS_DOMAIN_ID'])+')')
        print('Records: '+str(runtime_dir(config['ROS_DOMAIN_ID'])/'agent_instance/sam3/records'))
    elif args.command=='stop-sam3':
        run(config,['stop','sam3'])
    elif args.command=='sam3-status':
        run(config,['exec','-T','sam3','/usr/local/bin/sam3-entrypoint','python3','/opt/sam3/healthcheck.py','--status'])
    elif args.command=='reload-clients':
        run(config,['exec','-T','server','python3','/opt/instance/runtime.py','clients-reload'])
    elif args.command=='viewer-password':
        run(config,['exec','-T','viewer','cat','/viewer-auth/password'])
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
        print('ROS_DOMAIN_ID: '+str(config['ROS_DOMAIN_ID'])+' (native discovery)')
        print('Viewer: http://'+config['AGENTICROS_VIEWER_BIND']+':'+str(config['AGENTICROS_VIEWER_PORT'])+'/vnc.html')
        print('Viewer password: python3 scripts/runtime.py viewer-password --domain-id '+str(config['ROS_DOMAIN_ID']))


if __name__=='__main__':
    try: main()
    except (ValueError,OSError,argparse.ArgumentTypeError,subprocess.CalledProcessError) as exc:
        print('Startup failed: '+str(exc),file=sys.stderr);sys.exit(1)
