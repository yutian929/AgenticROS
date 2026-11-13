"""Run the two exact user instructions in a fresh persistent agent workspace.
No adapter, coordinates, perception or task program is supplied by this harness.
Retains the trial container/volume on failure; never retries an action or deletes data.
"""
import datetime
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, text=True, **kwargs)


def main():
    login = run(['docker', 'compose', 'run', '--rm', '--no-deps', '-T',
                 'agent', 'codex', 'login', 'status'])
    if login.returncode:
        print('BLOCKED: container Codex is not authenticated. Run bash scripts/docker-login.sh.', file=sys.stderr)
        return 2
    # The body must already be healthy; do not reset an existing user's physical state.
    body = run(['docker', 'compose', 'ps', '--format', 'json', 'body'], capture_output=True, check=True)
    if not body.stdout.strip():
        raise RuntimeError('Start the body first: docker compose up -d --wait body')
    trial = 'rosbody-trial-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    evidence = ROOT / 'runs' / trial
    evidence.mkdir(parents=True)
    name, volume = trial, trial + '-workspace'
    run(['docker', 'compose', 'run', '-d', '--no-deps', '--name', name,
         '-v', volume + ':/workspace', 'agent', 'sleep', 'infinity'], check=True)
    (evidence / 'trial.json').write_text(json.dumps(dict(container=name, workspace_volume=volume,
        model_config=(ROOT / 'docker/agent/codex.toml').read_text()), indent=2))
    print(f'Trial container: {name}\nPersistent workspace: {volume}\nHost evidence: {evidence}', flush=True)
    run(['docker', 'exec', name, '/usr/local/bin/agent-entrypoint', 'sh', '-c',
         'test -d /workspace/adapter && test -z "$(ls -A /workspace/adapter)"'], check=True)
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
            check = run(['docker', 'exec', name, 'sh', '-c',
                         'test -s /workspace/adapter/body.py && test -s /workspace/adapter/manifest.json'])
            if check.returncode:
                print('FAILED: adapter or manifest missing; task was not started.')
                return 1
    result = run(['bash', 'scripts/docker-evaluate.sh'], capture_output=True, check=True)
    (evidence / 'independent-evaluation.json').write_text(result.stdout)
    run(['docker', 'cp', name + ':/workspace/adapter', str(evidence / 'adapter')], check=True)
    # Never give this truth result to the task agent.
    print('Independent evaluation (maintainer only): ' + result.stdout)
    run(['docker', 'cp', name + ':/workspace/evidence', str(evidence / 'agent-evidence')], check=True)
    print('Review agent image inspections, generated code and manifest before accepting adaptation.\n'
          'Codex exit=0 alone is not validation. Container and volume remain for inspection.\n'
          f'Continue in this workspace: docker exec -it {name} /usr/local/bin/agent-entrypoint codex resume --last\n'
          f'Stop agent processes without deleting data: docker stop {name}')
    return 0 if json.loads(result.stdout)['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
