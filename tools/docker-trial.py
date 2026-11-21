"""Run explicitly supplied simulation prompts in the single Server workspace.

Uses the existing Agent lock and synchronized rules. Never starts another Agent
Server, deletes generated code, restarts a robot, or supplies a task program.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import runtime
from check_capabilities import check_live,load_manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain-id', type=int, default=42)
    parser.add_argument('--robot-id',required=True,type=runtime.robot_name)
    parser.add_argument('--prompt',action='append',required=True)
    args=parser.parse_args()
    config=runtime.server_config(args.domain_id);runtime.health(config)
    robot=json.loads(runtime.client_file(args.robot_id,args.domain_id).read_text())
    trial='trial-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    evidence=runtime.ROOT/'runs/checks'/trial;evidence.mkdir(parents=True)
    cid=subprocess.check_output(runtime.compose(config)+['ps','-q','server'],cwd=runtime.ROOT,env=runtime.environment(config),text=True).strip()
    for index,prompt in enumerate(args.prompt):
        command=runtime.agent_command(config,['codex','exec'],['--skip-git-repo-check','--json',prompt],False)
        (evidence/f'{index}-prompt.txt').write_text(prompt)
        with (evidence/f'{index}.jsonl').open('w') as log:
            subprocess.run(command,cwd=runtime.ROOT,env=runtime.environment(config),stdout=log,stderr=subprocess.STDOUT,check=True)
    report=check_live(load_manifest(runtime.runtime_dir(args.domain_id)/'agent_instance/agent/workspace'),cid)
    (evidence/'capabilities.json').write_text(json.dumps(report,indent=2))
    evaluation=subprocess.check_output(runtime.compose(robot,True)+['exec','-T','client','/opt/body-venv/bin/python','/opt/body/evaluator.py'],cwd=runtime.ROOT,env=runtime.environment(robot),text=True)
    (evidence/'independent-evaluation.json').write_text(evaluation)
    print('Maintainer-only evidence: '+str(evidence))
    return 0 if json.loads(evaluation)['success'] else 1
if __name__=='__main__':sys.exit(main())
