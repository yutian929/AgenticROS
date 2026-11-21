"""维护者验收：检查能力产物及在线 ROS 图，不执行清单命令或机器人动作。"""
import argparse
import json
from pathlib import Path
import re
import subprocess


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text_field(item, key):
    value = item.get(key)
    require(isinstance(value, str) and bool(value.strip()), f'缺少非空字段：{key}')
    return value


def workspace_path(workspace, value):
    require(isinstance(value, str) and bool(value), '工作区路径必须为非空字符串')
    path = Path(value)
    require(not path.is_absolute() and '..' not in path.parts, f'需要工作区内相对路径：{value}')
    path = (workspace / path).resolve()
    require(path.is_relative_to(workspace.resolve()), f'路径越出工作区：{value}')
    return path


def load_manifest(workspace):
    workspace = Path(workspace)
    path = workspace_path(workspace, 'capabilities/manifest.json')
    manifest = json.loads(path.read_text())
    require(isinstance(manifest, dict), '能力清单必须为对象')
    require(type(manifest.get('schema_version')) is int and manifest['schema_version'] == 1,
            '不支持的 schema_version')
    require(isinstance(manifest.get('environment'), dict) and bool(manifest['environment']),
            '缺少环境身份及核对记录')
    limitations = manifest.get('limitations')
    require(isinstance(limitations, list) and all(isinstance(x, str) for x in limitations),
            'limitations 必须为字符串数组')
    capabilities = manifest.get('capabilities')
    require(isinstance(capabilities, list) and bool(capabilities), '缺少能力列表')
    names = set()
    verified = []
    for item in capabilities:
        require(isinstance(item, dict), '能力项必须为对象')
        name = text_field(item, 'name')
        require(name not in names, f'重复能力名：{name}')
        names.add(name)
        kind = text_field(item, 'kind')
        require(kind in ('topic', 'service', 'action'), f'未知接口种类：{kind}')
        require(text_field(item, 'endpoint').startswith('/'), 'endpoint 必须为完整 ROS 名称')
        interface = text_field(item, 'interface_type')
        category = {'topic': 'msg', 'service': 'srv', 'action': 'action'}[kind]
        require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*/' + category + r'/[A-Za-z][A-Za-z0-9_]*', interface),
                f'接口类型与 kind 不匹配：{interface}')
        require(item.get('origin') in ('environment', 'generated'), '未知接口来源')
        require(item.get('status') in ('verified', 'unverified'), '未知验证状态')
        text_field(item, 'description')
        text_field(item, 'invocation')
        evidence = item.get('evidence')
        require(isinstance(evidence, list), 'evidence 必须为路径数组')
        for value in evidence:
            file = workspace_path(workspace, value)
            require(file.is_file() and file.stat().st_size > 0, f'缺少非空证据文件：{value}')
        if item['status'] == 'verified':
            require(bool(evidence), f'已验证能力缺少证据：{name}')
            verified.append(item)
    require(bool(verified), '尚无已验证能力，不能开始任务阶段')
    nodes = manifest.get('generated_nodes')
    require(isinstance(nodes, list), 'generated_nodes 必须为数组')
    node_names = set()
    for node in nodes:
        require(isinstance(node, dict), '节点项必须为对象')
        name = text_field(node, 'node_name')
        require(name.startswith('/') and name not in node_names, f'节点名无效或重复：{name}')
        node_names.add(name)
        for key in ('package', 'start_command', 'stop_command', 'health_command'):
            text_field(node, key)
        require(workspace_path(workspace, node.get('source_path')).is_dir(), '生成节点源码目录不存在')
        require(workspace_path(workspace, node.get('log_path')).is_file(), '生成节点日志不存在')
    require(not any(c['origin'] == 'generated' for c in verified) or bool(nodes),
            '已验证的生成能力缺少节点记录')
    return manifest


def ros_list(container, kind):
    command = ['docker', 'exec', container, '/usr/local/bin/agent-entrypoint',
               'ros2', kind, 'list']
    if kind != 'node':
        command.append('-t')
    # 固定只读命令；不加载或执行清单里的 source/启动/健康检查命令。
    return subprocess.run(command, check=True, text=True, capture_output=True, timeout=30).stdout


def check_live(manifest, container):
    verified = [c for c in manifest['capabilities'] if c['status'] == 'verified']
    graphs = {}
    for kind in sorted({c['kind'] for c in verified}):
        graph = {}
        for line in ros_list(container, kind).splitlines():
            match = re.fullmatch(r'(/\S+)\s+\[([^\]]+)\]', line.strip())
            if match:
                graph[match[1]] = {t.strip() for t in match[2].split(',')}
        graphs[kind] = graph
    for item in verified:
        types = graphs[item['kind']].get(item['endpoint'], set())
        require(item['interface_type'] in types,
                f"已验证接口不在线或类型不同：{item['endpoint']} {item['interface_type']}")
    if manifest['generated_nodes']:
        nodes = ros_list(container, 'node').splitlines()
        for node in manifest['generated_nodes']:
            require(nodes.count(node['node_name']) == 1,
                    f"生成节点不在线或存在同名实例：{node['node_name']}")
    return {'manifest_and_graph': 'PASS', 'verified_interfaces': len(verified),
            'generated_nodes': len(manifest['generated_nodes']),
            'scope': '仅检查产物和图，不证明功能、停止状态、逐步决策行为或任务成功'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--container', required=True, help='正在运行的 Agent 容器名')
    args = parser.parse_args()
    try:
        result = check_live(load_manifest(args.workspace), args.container)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'能力检查失败：{error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
