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
import lan
from check_capabilities import check_live,load_manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--robot-id',required=True,type=lan.robot_name)
    parser.add_argument('--prompt',action='append',required=True)
    args=parser.parse_args()
    config=lan.server_config();lan.health(config)
    robot=json.loads(lan.client_file(args.robot_id).read_text())
    trial='trial-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    evidence=lan.ROOT/'runs/checks'/trial;evidence.mkdir(parents=True)
    cid=subprocess.check_output(lan.compose(config)+['ps','-q','server'],cwd=lan.ROOT,env=lan.environment(config),text=True).strip()
    for index,prompt in enumerate(args.prompt):
        command=lan.agent_command(config,['codex','exec'],['--skip-git-repo-check','--json',prompt],False)
        (evidence/f'{index}-prompt.txt').write_text(prompt)
        with (evidence/f'{index}.jsonl').open('w') as log:
            subprocess.run(command,cwd=lan.ROOT,env=lan.environment(config),stdout=log,stderr=subprocess.STDOUT,check=True)
    report=check_live(load_manifest(lan.ROOT/'runs/workspace'),cid)
    (evidence/'capabilities.json').write_text(json.dumps(report,indent=2))
    evaluation=subprocess.check_output(lan.compose(robot,True)+['exec','-T','client','/opt/body-venv/bin/python','/opt/body/evaluator.py'],cwd=lan.ROOT,env=lan.environment(robot),text=True)
    (evidence/'independent-evaluation.json').write_text(evaluation)
    print('Maintainer-only evidence: '+str(evidence))
    return 0 if json.loads(evaluation)['success'] else 1
if __name__=='__main__':sys.exit(main())
