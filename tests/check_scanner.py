"""Disposable GPU component fixture. No commands go to an existing robot or Agent."""
import argparse,json,subprocess,time,uuid
from pathlib import Path
from gpu_test_guard import GPU_MEMORY_ARGS, require_idle_gpu
ROOT=Path(__file__).resolve().parents[1]

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--image',default='agenticros-quanta_x1:scanner-test')
 args=p.parse_args()
 require_idle_gpu()
 name='agenticros-scanner-component-'+uuid.uuid4().hex[:8]
 output=ROOT/'runs/checks'/name;output.mkdir(parents=True)
 evidence=output/'evidence';evidence.mkdir();evidence.chmod(0o777)
 command=['docker','create','--name',name,'--network','none','--gpus','all',
          *GPU_MEMORY_ARGS,'--no-healthcheck',
          '--read-only','--tmpfs','/tmp','--cap-drop','ALL','--security-opt','no-new-privileges:true',
          '-e','PYTHONPATH=/opt/body','--mount',f'type=bind,source={ROOT}/tests/scanner_physics.py,target=/test.py,readonly',
          '--mount',f'type=bind,source={evidence},target=/evidence',
          '--entrypoint','/opt/sim/bin/python',args.image,'/test.py','--portable-root','/tmp/kit']
 subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
 try:
  subprocess.run(['docker','start',name],check=True,stdout=subprocess.DEVNULL)
  print('Component fixture: '+str(output),flush=True)
  deadline=time.monotonic()+600
  while time.monotonic()<deadline:
   state=json.loads(subprocess.check_output(['docker','inspect',name]))[0]['State']
   if not state['Running']:
    break
   time.sleep(2)
  else:raise RuntimeError('Scanner fixture timeout')
  assert state['ExitCode']==0 and (output/'evidence/result.json').is_file(),state
  print('PASS: '+str(output/'evidence/result.json'))
 finally:
  with (output/'robot.log').open('w') as f:
   subprocess.run(['docker','logs',name],stdout=f,stderr=subprocess.STDOUT)
  subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL)
if __name__=='__main__':main()
