"""Two-domain integration on one host, with disposable containers and volumes."""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-suffix', default='single')
    args = parser.parse_args()
    suffix = '-check-'+secrets.token_hex(4)
    root = ROOT/'runs/checks'/('domains'+suffix)
    root.mkdir(parents=True)
    for file in ('compose.agent.yaml', 'compose.robot.yaml'):
        (root/file).write_text((ROOT/file).read_text().replace(':single', ':'+args.image_suffix))
    shutil.copytree(ROOT/'scripts', root/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'docker/agent_instance/agent', root/'docker/agent_instance/agent', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'docker/robot_instances/robot_docs',root/'docker/robot_instances/robot_docs')
    for dockerfile in (ROOT/'docker/robot_instances').glob('*/Dockerfile'):
        target=root/'docker/robot_instances'/dockerfile.parent.name/'Dockerfile'
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(dockerfile,target)
    spec = importlib.util.spec_from_file_location('domain_fixture', root/'scripts/runtime.py')
    lan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lan)
    original = lan.compose
    def compose(config, client=False):
        command = original(config, client)
        command[3] += suffix
        return command
    lan.compose = compose
    lan.ensure_image = lambda *a: None  # fixture uses already built test images
    def cid(config, service, client=False):
        return subprocess.check_output(compose(config, client)+['ps','-q',service], cwd=root,
                                       env=lan.environment(config), text=True).strip()
    configs, robots, started = [], [], []
    try:
        for domain in (90, 91):
            config = lan.server_config(domain)
            # Keep production names/domain suffix, adding an isolated test suffix only.
            for key, ending in [('AGENTICROS_CODEX_VOLUME','codex-home'),('AGENTICROS_VIEWER_VOLUME','viewer-auth')]:
                config[key] = f'agenticros-{domain}_{ending}'+suffix
            configs.append(config)
            robot = lan.client_config(argparse.Namespace(robot_id=None, environment='cartesian_gripper',
                        domain_id=domain, stop=False))
            robots.append(robot)
            started.append((robot, True))
            lan.run(robot,['up','-d','--no-build','--wait','client'],True)
            started.append((config, False))
            lan.ensure_server(config)
            lan.run(config,['up','-d','--no-build','--wait','viewer'])
            with urllib.request.urlopen(f"http://127.0.0.1:{config['AGENTICROS_VIEWER_PORT']}/vnc.html",timeout=5) as response:
                assert response.status == 200
        for config, robot in zip(configs, robots):
            agent = cid(config,'server')
            deadline = time.monotonic()+40
            while True:
                graph = json.loads(subprocess.check_output(['docker','exec',agent,'agenticros-ros','request',
                                    json.dumps(dict(op='status'))],text=True))
                found = {r['robot_id'] for r in graph['robots']}
                assert not (found & {r['AGENTICROS_ROBOT_ID'] for r in robots if r != robot}), graph
                if robot['AGENTICROS_ROBOT_ID'] in found: break
                assert time.monotonic()<deadline, graph
                time.sleep(.5)
            mounts = json.loads(subprocess.check_output(['docker','inspect',agent],text=True))[0]['Mounts']
            workspace = next(m['Source'] for m in mounts if m['Destination']=='/workspace')
            assert workspace.removeprefix('/host_mnt') == str(lan.runtime_dir(config['ROS_DOMAIN_ID'])/'agent_instance/agent/workspace')
            subprocess.run(['docker','exec',agent,'bash','-c',f"echo {config['ROS_DOMAIN_ID']} > /workspace/domain-check"],check=True)
        for config in configs:
            assert (lan.runtime_dir(config['ROS_DOMAIN_ID'])/'agent_instance/agent/workspace/domain-check').read_text().strip()==str(config['ROS_DOMAIN_ID'])
        # Stopping one domain leaves the other healthy.
        lan.run(configs[0],['stop','server','viewer'])
        lan.health(configs[1])
        result = dict(result='PASS', domains=[90,91], containers=6, isolated_graphs=True,
                      isolated_workspaces=True, viewer_ports=[c['AGENTICROS_VIEWER_PORT'] for c in configs], physical_hosts=1)
        (root/'result.json').write_text(json.dumps(result,indent=2))
        print('PASS: '+str(root),flush=True)
    finally:
        for config, client in reversed(started):
            lan.run(config,(['--profile','viewer'] if not client else [])+['down','--volumes'],client)


if __name__ == '__main__':
    main()
