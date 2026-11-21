"""Add a disposable camera-free ROS type without editing the launcher; no motion."""
import importlib.util
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    name='agenticros-type-check-'+secrets.token_hex(4)
    kind='test_mobile_base_'+secrets.token_hex(4)
    root=ROOT/'runs/checks'/name;root.mkdir(parents=True)
    for file in ('robot.sh','compose.agent.yaml','compose.robot.yaml'):
        shutil.copyfile(ROOT/file,root/file)
    (root/'scripts').mkdir()
    for file in ('runtime.py','prepare_workspace.py'):
        shutil.copyfile(ROOT/'scripts'/file,root/'scripts'/file)
    shutil.copytree(ROOT/'docker/agent_instance/agent',root/'docker/agent_instance/agent',ignore=shutil.ignore_patterns('__pycache__'))
    docs=root/'docker/robot_instances/robot_docs'/kind;docs.mkdir(parents=True)
    (docs/'manual.md').write_text('Disposable ROS publisher with its own heartbeat healthcheck; no camera or actuator.')
    source=root/'docker/robot_instances'/kind;source.mkdir()
    (source/'Dockerfile').write_text(f'''FROM ros:humble-ros-base-jammy
COPY docker/robot_instances/{kind}/node.py /opt/node.py
RUN useradd -u 1001 -m fixture
USER fixture
ENV HOME=/tmp ROS_LOG_DIR=/tmp/ros_logs
HEALTHCHECK --interval=1s --timeout=2s --retries=2 CMD python3 -c "import pathlib,time; assert time.time()-pathlib.Path('/tmp/type-ready').stat().st_mtime<3"
CMD ["python3", "/opt/node.py"]
''')
    (source/'node.py').write_text('''import json,os,uuid
from pathlib import Path
import rclpy
from std_msgs.msg import String
rclpy.init();robot=os.environ['AGENTICROS_ROBOT_ID'];session=uuid.uuid4().hex
node=rclpy.create_node('fixture',namespace='/robot/'+robot)
publisher=node.create_publisher(String,'driver/state',10)
def publish():
    if not Path('/tmp/pause-health').exists():Path('/tmp/type-ready').touch()
    publisher.publish(String(data=json.dumps(dict(robot_id=robot,session_id=session))))
node.create_timer(.2,publish);rclpy.spin(node)
''')
    spec=importlib.util.spec_from_file_location('type_fixture',root/'scripts/runtime.py')
    lan=importlib.util.module_from_spec(spec);spec.loader.exec_module(lan)
    original=lan.compose
    def compose(config,client=False):
        command=original(config,client)
        if not client:command[3]=name+'-agent'
        return command
    lan.compose=compose
    config=lan.server_config(87);config['AGENTICROS_CODEX_VOLUME']=name+'-account'
    robots=[]
    def cid(robot):
        return subprocess.check_output(compose(robot,True)+['ps','-q','client'],cwd=root,env=lan.environment(robot),text=True).strip()
    def health(container,expected):
        deadline=time.monotonic()+20
        while subprocess.check_output(['docker','inspect',container,'--format','{{.State.Health.Status}}'],text=True).strip()!=expected:
            assert time.monotonic()<deadline,expected
            time.sleep(.5)
    try:
        assert subprocess.check_output(['bash','robot.sh','--list'],cwd=root,text=True).strip()==kind
        for _ in range(2):
            subprocess.run(['bash','robot.sh',kind,'--domain-id','87'],cwd=root,check=True)
        robots=[json.loads(p.read_text()) for p in (root/'runs/87/robot_instance'/kind).glob('*/client.json')]
        assert len(robots)==2 and robots[0]['AGENTICROS_ROBOT_ID']!=robots[1]['AGENTICROS_ROBOT_ID']
        for robot in robots:
            assert robot['AGENTICROS_ENVIRONMENT']==kind
            assert subprocess.run(['docker','exec',cid(robot),'test','-e','/tmp/body-camera-ready']).returncode != 0
        first=cid(robots[0])
        subprocess.run(['python3','scripts/runtime.py','robot','--robot-id',robots[0]['AGENTICROS_ROBOT_ID'],'--domain-id','87'],cwd=root,check=True)
        assert cid(robots[0])==first
        subprocess.run(['docker','exec',first,'touch','/tmp/pause-health'],check=True)
        health(first,'unhealthy')
        subprocess.run(['docker','exec',first,'rm','/tmp/pause-health'],check=True)
        health(first,'healthy')
        agent=lan.ensure_server(config)
        for robot in robots:
            topic='/robot/'+robot['AGENTICROS_ROBOT_ID']+'/driver/state'
            output=subprocess.check_output(['docker','exec',agent,'/usr/local/bin/agent-entrypoint','timeout','20','ros2','topic','echo','--once',topic,'std_msgs/msg/String'],text=True)
            assert robot['AGENTICROS_ROBOT_ID'] in output
        result=dict(result='PASS',new_type=kind,containers=3,automatic_discovery=True,
                    camera_free_healthcheck=True,unhealthy_and_recovery=True,repeat_start_keeps_id=True,native_ros_received=True)
        (root/'result.json').write_text(json.dumps(result,indent=2))
        print('PASS: '+str(root),flush=True)
    finally:
        # Discover even partially started instances if a launch failed.
        for p in (root/'runs/87/robot_instance'/kind).glob('*/client.json'):
            saved=json.loads(p.read_text())
            container=cid(saved)
            if container:
                log=subprocess.run(['docker','logs',container],capture_output=True,text=True)
                (root/(saved['AGENTICROS_ROBOT_ID']+'.log')).write_text(log.stdout+log.stderr)
            lan.run(saved,['down','--volumes'],True)
        lan.run(config,['down','--volumes'])
        subprocess.run(['docker','image','rm','agenticros-'+kind+':single'],capture_output=True)


if __name__=='__main__':main()
