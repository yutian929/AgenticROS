"""Verify bundled checkpoint offline in a disposable CPU-only container.

Does not run inference, use GPU, start a live service or mount any host models.
"""
import argparse
import json
from pathlib import Path
import subprocess
import uuid

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='agenticros-sam3:bundled-test')
    args=parser.parse_args()
    name='agenticros-sam3-bundle-'+uuid.uuid4().hex[:8]
    manifest=json.loads((ROOT/'docker/agent_instance/sam3/checkpoint.json').read_text())
    code='''import hashlib,json,pathlib
p=pathlib.Path('/models/sam3.pt')
m=json.loads(pathlib.Path('/models/checkpoint.json').read_text())
h=hashlib.sha256()
with p.open('rb') as f:
    for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
assert p.stat().st_size==m['size']
assert h.hexdigest()==m['sha256']
assert not list(pathlib.Path('/models').glob('*.part'))
print(json.dumps(m))
'''
    subprocess.run(['docker','create','--name',name,'--network','none','--read-only',
        '--user','1000:1000','--cap-drop','ALL','--security-opt','no-new-privileges:true',
        '--entrypoint','python3',args.image,'-c',code],check=True,capture_output=True)
    try:
        info=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
        assert not info['Mounts'],info['Mounts']
        assert not info['HostConfig']['DeviceRequests']
        result=subprocess.run(['docker','start','-a',name],check=True,text=True,capture_output=True)
        state=json.loads(subprocess.check_output(['docker','inspect',name]))[0]['State']
        assert state['ExitCode']==0,result.stderr
        assert json.loads(result.stdout)==manifest
        output=ROOT/'runs/checks'/name
        output.mkdir(parents=True)
        (output/'result.json').write_text(json.dumps(dict(result='PASS',image=args.image,
            network='none',mounts=info['Mounts'],checkpoint=manifest,real_inference=False),indent=2))
        print('PASS: '+str(output/'result.json'))
    finally:
        subprocess.run(['docker','rm','-f',name],check=False,capture_output=True)

if __name__=='__main__':main()
