"""Check the Agent public-document mount and separate robot containers; never send motion commands."""
import argparse
import importlib.util
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-suffix',default='single')
    parser.add_argument('--observations',action='store_true',help='also record synchronized camera topics in Agent')
    args=parser.parse_args()
    name='agenticros-robot-docs-check-'+secrets.token_hex(4)
    root=ROOT/'runs/checks'/name;root.mkdir(parents=True)
    for file in ('compose.agent.yaml','compose.robot.yaml'):
        (root/file).write_text((ROOT/file).read_text().replace(':single',':'+args.image_suffix))
    shutil.copytree(ROOT/'scripts',root/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'docker/agent_instance/agent',root/'docker/agent_instance/agent',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'docker/robot_instances/robot_docs',root/'docker/robot_instances/robot_docs')
    for dockerfile in (ROOT/'docker/robot_instances').glob('*/Dockerfile'):
        target=root/'docker/robot_instances'/dockerfile.parent.name/'Dockerfile'
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(dockerfile,target)
    spec=importlib.util.spec_from_file_location('robot_docs_fixture',root/'scripts/runtime.py')
    lan=importlib.util.module_from_spec(spec);spec.loader.exec_module(lan)
    original=lan.compose
    def compose(config,client=False):
        command=original(config,client)
        command[3]=name+('-'+config['AGENTICROS_ROBOT_ID'] if client else '-agent')
        return command
    lan.compose=compose
    lan.ensure_image=lambda *args: None
    config=lan.server_config(89)
    config['AGENTICROS_CODEX_VOLUME']=name+'-account'
    robots=[]
    try:
        for kind in ('cartesian_gripper','aloha_tabletop'):
            robot=lan.client_config(argparse.Namespace(robot_id=None,environment=kind,domain_id=89,
                        stop=False))
            robots.append(robot)
            lan.run(robot,['up','-d','--no-build','--wait','client'],True)
        agent=lan.ensure_server(config)
        docs_root=root/'docker/robot_instances/robot_docs'
        expected={str(p.relative_to(docs_root)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in docs_root.rglob('*') if p.is_file()}
        script="import pathlib,json,hashlib; root=pathlib.Path('/workspace/robot_docs'); print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}))"
        actual=json.loads(subprocess.check_output(['docker','exec',agent,'python3','-c',script],text=True))
        assert actual==expected
        mounts=json.loads(subprocess.check_output(['docker','inspect',agent],text=True))[0]['Mounts']
        mount=next(m for m in mounts if m['Destination']=='/workspace/robot_docs')
        assert mount['Type']=='bind' and not mount['RW']
        write=subprocess.run(['docker','exec',agent,'touch','/workspace/robot_docs/forbidden'],capture_output=True)
        assert write.returncode!=0 and b'Read-only file system' in write.stderr
        # Host edits to the public catalog are visible without rebuilding Agent.
        marker=docs_root/'mount-check.txt';marker.write_text('live public document')
        assert subprocess.check_output(['docker','exec',agent,'cat','/workspace/robot_docs/mount-check.txt'],text=True)=='live public document'
        marker.unlink()
        # Compile public IDL in the writable workspace, never in the docs mount.
        build="""import pathlib,shutil,xml.etree.ElementTree as ET,subprocess
root=pathlib.Path('/workspace/ros_ws');(root/'src').mkdir(parents=True,exist_ok=True)
for source in pathlib.Path('/workspace/robot_docs').glob('*/interfaces'):
    name=ET.parse(source/'package.xml').getroot().findtext('name')
    shutil.copytree(source,root/'src'/name)
subprocess.run(['colcon','build','--executor','sequential'],cwd=root,check=True)
"""
        subprocess.run(['docker','exec','-i',agent,'bash','-c','source /etc/profile.d/agenticros.sh; python3 -'],input=build,text=True,check=True)
        graph=json.loads(subprocess.check_output(['docker','exec',agent,'agenticros-ros','graph'],text=True))
        assert not any('/knowledge/' in name for name,_ in graph['services'])
        evidence=dict(documents=expected,read_only=True,live_edits_visible=True,public_idl_build=True,knowledge_services_absent=True)
        if args.observations:
            subprocess.run(['docker','exec','-i',agent,'bash','-c','source /etc/profile.d/agenticros.sh; python3 - '+
                            __import__('shlex').quote(json.dumps(robots))],input=(ROOT/'tests/sensor_qos_probe.py').read_text(),text=True,check=True)
            subprocess.run(['docker','exec','-i',agent,'bash','-c','source /etc/profile.d/agenticros.sh; python3 - '+
                            __import__('shlex').quote(json.dumps(robots))],input=(ROOT/'tests/observation_probe.py').read_text(),text=True,check=True)
        subprocess.run(lan.agent_command(config,['true'],[],False),cwd=root,env=lan.environment(config),check=True)
        (root/'result.json').write_text(json.dumps(dict(result='PASS',containers=3,physical_hosts=1,ros_observations=args.observations,robot_docs=evidence),indent=2))
        print('PASS: '+str(root),flush=True)
    finally:
        lan.run(config,['down','--volumes'])
        for robot in robots:lan.run(robot,['down','--volumes'],True)


if __name__=='__main__':main()
