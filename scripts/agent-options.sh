# Shared by host entrypoints; source before invoking Docker or preparing files.
# Only a leading --agent_id is consumed; the remaining arguments belong to Codex.
instance=default
if [[ ${1:-} == --agent_id || ${1:-} == --instance ]]; then
  if [[ $# -lt 2 ]]; then
    echo '用法：--agent_id Agent实例名称（小写字母、数字和连字符，最长 48 字符）' >&2
    exit 2
  fi
  instance=$2
  shift 2
fi
if [[ ! $instance =~ ^[a-z0-9][a-z0-9-]{0,47}$ ]]; then
  echo '无效 Agent instance：使用小写字母、数字和连字符，首位不能是连字符，最长 48 字符。' >&2
  exit 2
fi
export AGENTICROS_INSTANCE="$instance"
export AGENTICROS_INSTANCE_SUFFIX=""
export AGENTICROS_WORKSPACE_SUBDIR="agent_instances/$instance/workspace"
if [[ $instance != default ]]; then
  export AGENTICROS_INSTANCE_SUFFIX="-instance-$instance"
  export AGENTICROS_WORKSPACE_SUBDIR="agent_instances/$instance/workspace"
fi

# Preserve existing workspaces until the user cleans or migrates them.
if [[ $instance == default && ( -d runs/workspace || -d runs/lan ) ]]; then
  export AGENTICROS_WORKSPACE_SUBDIR=workspace
elif [[ -d runs/instances/$instance/workspace || -d runs/instances/$instance/lan ]]; then
  export AGENTICROS_WORKSPACE_SUBDIR="instances/$instance/workspace"
fi
echo "Agent instance: $instance (runs/$AGENTICROS_WORKSPACE_SUBDIR)"
