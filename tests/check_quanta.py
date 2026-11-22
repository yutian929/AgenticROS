"""Disposable separate-container checks: synthetic protocol or actual Isaac physics/rendering."""
import json
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import time
from gpu_test_guard import GPU_MEMORY_ARGS, require_idle_gpu

ROOT = Path(__file__).resolve().parents[1]

def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', help='use a fresh isolated GPU body and require real camera frames')
    parser.add_argument('--host-network', action='store_true', help='exercise production host network/IPC on a separate test identity')
    parser.add_argument('--image', help='candidate robot image; does not replace a live body')
    parser.add_argument('--contact', action='store_true', help='private scripted grasp/lift regression after real protocol checks')
    parser.add_argument('--require-lift', action='store_true', help='also require the independent grasp/lift task evaluation to pass')
    args = parser.parse_args()
    if args.require_lift and not args.contact: parser.error('--require-lift requires --contact')
    if args.contact and not args.real: parser.error('--contact requires --real')
    if args.real:
        require_idle_gpu()
    name = 'agenticros-quanta-check-'+uuid.uuid4().hex[:8]
    robot, agent = name+'-robot', name+'-agent'
    robot_id = 'quanta_test_'+uuid.uuid4().hex[:8]
    network = 'host' if args.host_network else name
    root = ROOT/'runs/checks'/name
    workspace = root/'workspace'
    workspace.mkdir(parents=True)
    workspace.chmod(0o777)
    shutil.copy(ROOT/'tests/quanta_probe.py', workspace/'probe.py')
    docs = ROOT/'docker/robot_instances/robot_docs/quanta_x1'
    if not args.host_network:
        run('docker','network','create','--internal',name, stdout=subprocess.DEVNULL)
    try:
        command = ['docker','create','--name',robot,'--network',network,'--read-only','--tmpfs','/tmp',
                   '--cap-drop','ALL','--security-opt','no-new-privileges:true','--init',
                   '-e','RMW_FASTRTPS_PUBLICATION_MODE=ASYNCHRONOUS',
                   '-e','ROS_DOMAIN_ID=91','-e','AGENTICROS_ROBOT_ID='+robot_id]
        if args.host_network:
            command += ['--ipc','host']
        if args.contact:
            command += ['-e','QUANTA_PHYSICS_DIAGNOSTICS=1']
        if args.real:
            command += ['--gpus','all',*GPU_MEMORY_ARGS,args.image or 'agenticros-quanta_x1:single']
        else:
            command += ['--mount',f'type=bind,source={ROOT}/tests/quanta_fixture.py,target=/opt/body/fixture.py,readonly',
                        '--entrypoint','bash',args.image or 'agenticros-quanta_x1:transport-test','-c',
                        'source /opt/ros/jazzy/setup.bash; source /opt/driver_ws/install/setup.bash; python3 /opt/body/fixture.py']
        run(*command, stdout=subprocess.DEVNULL)
        robot_mounts=json.loads(subprocess.check_output(['docker','inspect',robot]))[0]['Mounts']
        if args.real:
            assert not any(m['Type'] in ('bind','volume') for m in robot_mounts), robot_mounts
        run('docker','start',robot,stdout=subprocess.DEVNULL)
        if args.real:
            deadline = time.monotonic()+600
            while time.monotonic() < deadline:
                state=json.loads(subprocess.check_output(['docker','inspect',robot]))[0]['State']
                if not state['Running']:
                    raise RuntimeError('Isaac body exited; inspect robot.log')
                if state.get('Health',{}).get('Status') == 'healthy':
                    break
                time.sleep(2)
            else:
                raise RuntimeError('Isaac camera/physics readiness timed out')
        run('docker','run','-d','--name',agent,'--network',network,
            *(['--ipc','host'] if args.host_network else []),
            '-e','ROS_DOMAIN_ID=91','-e','TEST_ROBOT_ID='+robot_id,
            '-e','TEST_SCOPE='+('Isaac GPU simulation and ROS transport' if args.real else 'synthetic ROS transport'),
            '-e','TEST_REQUIRE_IMAGES='+('1' if args.real else '0'),
            '--mount',f'type=bind,source={workspace},target=/workspace',
            '--mount',f'type=bind,source={docs},target=/workspace/robot_docs/quanta_x1,readonly',
            '--entrypoint','sleep','agenticros-server:single','infinity',stdout=subprocess.DEVNULL)
        mounts=json.loads(subprocess.check_output(['docker','inspect',agent]))[0]['Mounts']
        assert {m['Destination'] for m in mounts} == {'/workspace','/workspace/robot_docs/quanta_x1'}
        assert all(not m['RW'] for m in mounts if m['Destination'].endswith('quanta_x1'))
        run('docker','exec',agent,'python3','-c',
            "import importlib.util,pathlib; assert importlib.util.find_spec('isaacsim') is None; "
            "assert not pathlib.Path('/assets').exists(); assert not pathlib.Path('/opt/body').exists(); "
            "assert not list(pathlib.Path('/workspace').rglob('ex001.usd')); "
            "assert not list(pathlib.Path('/workspace').rglob('scanner_scene.py'))")
        (root/'isolation.json').write_text(json.dumps(dict(agent_mounts=mounts,robot_mounts=robot_mounts,
            simulator_package_absent=True,robot_model_absent=True,internal_test_network=not args.host_network),indent=2))
        with (root/'probe.log').open('w') as log:
            run('docker','exec',agent,'bash','-c',
                'source /opt/ros/humble/setup.bash && mkdir -p /workspace/ros_ws/src && '
                'cp -r /workspace/robot_docs/quanta_x1/interfaces /workspace/ros_ws/src/quanta_driver_interfaces && '
                'cd /workspace/ros_ws && colcon build --merge-install && source install/setup.bash && python3 /workspace/probe.py',
                stdout=log,stderr=subprocess.STDOUT,timeout=180)
        assert (workspace/'result.json').is_file()
        if args.contact:
            evidence=root/'contact';evidence.mkdir();evidence.chmod(0o777)
            baseline=json.loads(subprocess.check_output(['docker','exec',robot,'cat','/tmp/quanta-physics.json']))
            with (root/'contact.log').open('w') as log:
                run('docker','run','--rm','--name',name+'-contact','--network',network,
                    '-e','ROS_DOMAIN_ID=91','-e','TEST_ROBOT_ID='+robot_id,
                    '--mount',f'type=bind,source={ROOT}/tests/quanta_contact_probe.py,target=/opt/test/contact.py,readonly',
                    '--mount',f'type=bind,source={docs}/kinematics.json,target=/opt/test/kinematics.json,readonly',
                    '--mount',f'type=bind,source={evidence},target=/evidence',
                    '--entrypoint','bash',args.image or 'agenticros-quanta_x1:single','-c',
                    'source /opt/ros/jazzy/setup.bash && source /opt/driver_ws/install/setup.bash && /opt/sim/bin/python /opt/test/contact.py',
                    stdout=log,stderr=subprocess.STDOUT,timeout=180)
            observed=json.loads(subprocess.check_output(['docker','exec',robot,'cat','/tmp/quanta-physics.json']))
            (evidence/'independent_clearance.json').write_text(json.dumps(dict(before=baseline,after=observed),indent=2))
            lifted = (observed['scanner_min_z'] > .815 and
                      observed['scanner_position'][2]-baseline['scanner_position'][2] > .04)
            (evidence/'lift_evaluation.json').write_text(json.dumps(dict(
                result='PASS' if lifted else 'FAIL', scope='independent scripted grasp/lift evaluation',
                task_success=lifted, minimum_clearance_m=observed['scanner_min_z']-.8,
                required_clearance_m=.015, before=baseline, after=observed),indent=2))
            print('Independent lift evaluation: '+('PASS' if lifted else 'FAIL'),flush=True)
            if args.require_lift:
                assert lifted, observed
        print('PASS: '+str(workspace/'result.json'))
    finally:
        with (root/'robot.log').open('w') as log:
            subprocess.run(['docker','logs',robot],stdout=log,stderr=subprocess.STDOUT)
        for container in (name+'-contact',agent,robot):
            subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if not args.host_network:
            subprocess.run(['docker','network','rm',name],stdout=subprocess.DEVNULL)

if __name__ == '__main__':main()
