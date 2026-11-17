"""Delete only the explicitly selected Agent instance; never stop containers or prune Docker."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, environment):
    return subprocess.run(['docker', *args], cwd=ROOT, env=environment,
                          check=True, capture_output=True, text=True).stdout


def delete_instance(instance, dry_run=False):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', instance):
        raise RuntimeError('无效 Agent instance 名称。')
    suffix = '' if instance == 'default' else '-instance-' + instance
    subdir = 'workspace' if instance == 'default' else f'instances/{instance}/workspace'
    workspace = ROOT / 'runs' / subdir
    target = workspace if instance == 'default' else workspace.parent
    lan = (ROOT / 'runs' if instance == 'default' else workspace.parent) / 'lan'
    if lan.exists():
        raise RuntimeError('拒绝删除：该 Agent instance 包含 LAN 配置。请先单独停止并归档 Agent instance 和 Robot instance；此命令不删除 LAN 数据。')
    for path in [workspace, *workspace.parents]:
        if path == ROOT:
            break
        if path.is_symlink():
            raise RuntimeError('拒绝删除：工作区路径包含符号链接：' + str(path))
    if target.exists() and not target.is_dir():
        raise RuntimeError('拒绝删除：工作区不是目录。')
    environment = dict(os.environ, AGENTICROS_INSTANCE_SUFFIX=suffix,
                       AGENTICROS_WORKSPACE_SUBDIR=subdir)
    config = json.loads(docker('compose', 'config', '--format', 'json', environment=environment))
    volume = config['volumes']['codex-home']['name']
    if volume != config['name'] + '_codex-home' + suffix:
        raise RuntimeError('拒绝删除：账号卷名称与 Agent instance 不匹配。')
    mounts = config['services']['agenticros']['volumes']
    actual_workspace = next(m['source'] for m in mounts if m['target'] == '/workspace')
    if Path(actual_workspace) != workspace:
        raise RuntimeError('拒绝删除：Compose 工作区与 Agent instance 不匹配。')
    # Include stopped containers: they can be restarted and still own these mounts.
    consumers = set()
    for source in (volume, str(workspace), str(target)):
        consumers.update(docker('ps', '-aq', '--filter', 'volume=' + source,
                                environment=environment).split())
    if consumers:
        raise RuntimeError('拒绝删除：以下容器仍引用该 Agent instance（包括已停止容器）。'
                           '请先退出并移除对应容器：' + ', '.join(sorted(consumers)))
    volumes = docker('volume', 'ls', '--format', '{{.Name}}', environment=environment).splitlines()
    print('账号卷：' + volume + ('（存在）' if volume in volumes else '（不存在）'))
    print('工作目录：' + str(target) + ('（存在）' if target.exists() else '（不存在）'))
    if dry_run:
        print('仅预览，未删除任何数据。')
        return
    # Docker rejects a volume that acquired a consumer after the preflight check.
    if volume in volumes:
        docker('volume', 'rm', volume, environment=environment)
    if target.exists():
        shutil.rmtree(target)
    print('已删除 Agent instance ' + instance + ' 的账号、配置、历史和工作目录。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        delete_instance(args.instance, args.dry_run)
    except (RuntimeError, OSError, ValueError, KeyError, StopIteration,
            subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        parser.exit(1, '删除未完成：' + detail + '\n')


if __name__ == '__main__':
    main()
