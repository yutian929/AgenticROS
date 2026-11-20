"""Measure HTTP identity enrollment/renewal independently in two isolated containers.

No ROS or robot motion. Baseline image is optional and must be retained explicitly.
"""
import argparse
import json
from pathlib import Path
import secrets
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def docker(*args,**kwargs):
    return subprocess.run(['docker',*args],check=True,text=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='agenticros-server:single')
    parser.add_argument('--baseline',action='store_true')
    parser.add_argument('--samples',type=int,default=20)
    args=parser.parse_args()
    name='agenticros-presence-'+secrets.token_hex(4)
    root=ROOT/'runs/checks'/name;root.mkdir(parents=True)
    constructor=("Registry('baseline','baseline-id',74,'127.0.0.1',11811,'01.02.03.04.05.06.07.08.09.0a.0b.0c','fixture')" if args.baseline else
                 "Registry(74,'127.0.0.1',11811,'01.02.03.04.05.06.07.08.09.0a.0b.0c')")
    server_code="import sys;sys.path.insert(0,'/opt/agenticros-server');from registry import Registry,http_server;r="+constructor+";http_server(r,('0.0.0.0',7447)).serve_forever()"
    probe=r'''
import json,time,urllib.request,urllib.error
rows=[]
def post(path,data,token):
 req=urllib.request.Request('http://registry:7447/'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
 with urllib.request.urlopen(req,timeout=3) as r:return json.load(r)
for attempt in range(30):
 try:
  with urllib.request.urlopen('http://registry:7447/health',timeout=1) as r:json.load(r)
  break
 except OSError:time.sleep(.1)
for i in range(COUNT):
 body=dict(instance='baseline',robot_id='fixture_'+str(i),session_id=f'{i+1:012x}')
 start=time.monotonic()
 try:
  member=post('join' if BASELINE else 'presence',body,'fixture' if BASELINE else '')
  rows.append(dict(layer='http_enrollment',success=True,response_ms=(time.monotonic()-start)*1000))
 except urllib.error.HTTPError as e:
  rows.append(dict(layer='http_enrollment',success=False,error='server_failure',http_status=e.code,response_ms=(time.monotonic()-start)*1000));continue
 except OSError as e:
  rows.append(dict(layer='http_enrollment',success=False,error='request_timeout',response_ms=(time.monotonic()-start)*1000));continue
 start=time.monotonic()
 try:
  reply=post('heartbeat' if BASELINE else 'presence',member,member['lease'])
  rows.append(dict(layer='http_renewal',success=True,response_ms=(time.monotonic()-start)*1000))
 except OSError as e:
  rows.append(dict(layer='http_renewal',success=False,error=type(e).__name__,response_ms=(time.monotonic()-start)*1000))
print(json.dumps(rows))
'''.replace('COUNT',str(args.samples)).replace('BASELINE',str(args.baseline))
    docker('network','create','--internal',name,capture_output=True)
    try:
        docker('run','-d','--name',name+'-server','--network',name,'--network-alias','registry',
               '--cap-drop','ALL','--entrypoint','python3',args.image,'-c',server_code,capture_output=True)
        result=docker('run','--rm','--network',name,'--cap-drop','ALL','--entrypoint','python3',args.image,'-c',probe,capture_output=True)
        rows=json.loads(result.stdout)
        (root/'samples.json').write_text(json.dumps(rows,indent=2))
        (root/'topology.json').write_text(json.dumps(dict(physical_hosts=1,containers=2,baseline=args.baseline,
            image_id=docker('image','inspect',args.image,'--format','{{.Id}}',capture_output=True).stdout.strip()),indent=2))
        assert all(r['success'] for r in rows),rows
        print('PASS: '+str(root))
    finally:
        docker('rm','-f',name+'-server',capture_output=True)
        docker('network','rm',name,capture_output=True)

if __name__=='__main__':main()
