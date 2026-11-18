"""Run the two exact user instructions in a fresh persistent agent workspace.
No capability implementation, coordinates or task program is supplied by this harness.
Retains the trial container/workspace on failure; never retries an action or deletes data.
"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from check_capabilities import check_live, load_manifest

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, text=True, **kwargs)


def main():
    os.environ['AGENTICROS_WORKSPACE_GID'] = str(os.getgid())
    trial = 'agenticros-trial-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    evidence = ROOT / 'runs' / 'checks' / trial
    workspace = evidence / 'workspace'
    workspace.mkdir(parents=True)
    workspace.chmod(0o2775)
    login = run(['docker', 'compose', 'run', '--rm', '--no-deps', '-T',
                 '-v', str(workspace) + ':/workspace',
                 'agenticros', 'agenticros', 'status'])
    if login.returncode:
        print('BLOCKED: configure the container model connection with bash scripts/agent.sh.', file=sys.stderr)
        return 2
    # The body must already be healthy; do not reset an existing user's physical state.
    body = run(['docker', 'compose', 'ps', '--format', 'json', 'cartesian_gripper'], capture_output=True, check=True)
    if not body.stdout.strip():
        raise RuntimeError('Start the environment first: bash scripts/robot.sh cartesian_gripper')
    name = trial
    run(['docker', 'compose', 'run', '-d', '--no-deps', '--name', name,
         '-v', str(workspace) + ':/workspace', 'agenticros', 'sleep', 'infinity'], check=True)
    (evidence / 'trial.json').write_text(json.dumps(dict(container=name, workspace_path=str(workspace),
        model_config=(ROOT / 'docker/agent_instance/agent/codex.toml').read_text()), indent=2))
    print(f'Trial container: {name}\nPersistent workspace: {workspace}\nHost evidence: {evidence}', flush=True)
    run(['docker', 'exec', name, '/usr/local/bin/agent-entrypoint', 'sh', '-c',
         'test -d /workspace/ros_ws/src && test -z "$(ls -A /workspace/ros_ws/src)" && '
         'test -d /workspace/capabilities && test -z "$(ls -A /workspace/capabilities)"'], check=True)
    for stage, prompt in [('adaptation', '适配一下你的身体'), ('task', '把红色方块夹取到红色盘子上')]:
        (evidence / (stage + '-prompt.txt')).write_text(prompt)
        command = ['docker', 'exec', name, '/usr/local/bin/agent-entrypoint', 'codex', 'exec',
                   '--skip-git-repo-check', '--json', '--output-last-message',
                   '/workspace/evidence/' + stage + '-final.txt', prompt]
        with (evidence / (stage + '.jsonl')).open('w') as log:
            process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            for line in process.stdout:
                log.write(line); log.flush()
                print(line, end='', flush=True)
            code = process.wait()
        if code:
            print(f'BLOCKED/FAILED: {stage} Codex exit={code}; retained container {name}. No automatic retry.')
            return code
        if stage == 'adaptation':
            try:
                report = check_live(load_manifest(workspace), name)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                print(f'FAILED: capability check: {error}; task was not started.')
                return 1
            (evidence / 'capability-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    result = run(['bash', 'scripts/docker-evaluate.sh'], capture_output=True, check=True)
    (evidence / 'independent-evaluation.json').write_text(result.stdout)
    # Never give this truth result to the task agent.
    print('Independent evaluation (maintainer only): ' + result.stdout)
    run(['docker', 'cp', name + ':/workspace/evidence', str(evidence / 'agent-evidence')], check=True)
    print('Review generated nodes, manifest, images and adaptation/task JSONL before accepting adaptation.\n'
          'Check that Codex decided each task step from feedback, rather than delegating the task to a script.\n'
          'Graph presence does not validate capability behavior or task success.\n'
          'Codex exit=0 alone is not validation. Container and host workspace remain for inspection.\n'
          f'Continue in this workspace: docker exec -it {name} /usr/local/bin/agent-entrypoint codex resume --last\n'
          f'Stop agent processes without deleting data: docker stop {name}')
    return 0 if json.loads(result.stdout)['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
