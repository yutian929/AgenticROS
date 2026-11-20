# 使用说明

## 固定 Server 和机器人接入

在 Server 主机运行 `python3 scripts/lan.py server --host SERVER_IP`，然后 `bash scripts/agent.sh`。仅一个 Server、一个 `/workspace`，监督 Agent 可派生多个执行子 agent，不需要实例名称。

首次可配置 `--domain-id 42 --discovery-port 11811 --transport tcp`。配置固定保存到 `runs/server.json`；后续不重新分配。运行中传入不同配置会拒绝，先明确停止所有受影响任务与机器人，停止 Server，再编辑配置并重启。不要让任务 Agent 修改 DDS 设置。

先按 README 确认 Server 的实际局域网 IP，并在两端设置 `SERVER_IP`。在每个机器人主机运行：

```bash
bash scripts/robot.sh aloha_tabletop --server "$SERVER_IP" --robot-id aloha_a
# 同一主机的第二台机器人需要不同图像 HTTP 端口
bash scripts/robot.sh cartesian_gripper --server "$SERVER_IP" --robot-id gripper_b --observation-port 8801
```

重启沿用保存的 Server 地址、robot_id 和环境。ALOHA 是一个 robot_id、一个共享世界/session；两臂位于 `/robots/aloha_a/left/...` 与 `/robots/aloha_a/right/...`，保留 `move_pose`、`move_delta`、`actuate`、三相机和原生 RViz 面板。

不需要 HTTP 注册、join 码或 HTTP 心跳/控制租约；ROS 2 / DDS 自身的发现、可靠传输和存活检测仍保留。机器人可以先于 Server 启动，发现服务可用后建立 ROS 连接。两端的 `--domain-id`、`--discovery-port`、`--transport` 必须一致（默认 `42 / 11811 / tcp`），不会再通过 HTTP 获取配置。`robot_id` 必须全网唯一。重启前停止旧进程，新进程生成新 session，稳定名称空间不变。旧 session 请求被拒绝，不会重放动作。

已接受的有限动作独立于 Server 存活，由机器人本地监督；取消/故障触发停止核对。网络断开或 Agent 退出不等于动作停止，超时后必须查询 UUID/session 对应状态。

## 网络配置与定位

Linux 和 macOS Docker 默认使用 TCP Discovery Server，端口 11811；机器人图像 HTTP 默认 8800。发现服务使用普通 `SERVER` 模式，不将临时 ROS 图写入 BACKUP 数据库。TCP 发现服务只公布固定地址，不额外公布 loopback；客户端将固定发现地址写入 `RemoteServer`，并将普通/WAN 两种 locator 都写入 `initialPeersList`，保留发现服务重启后的重连资源；Server 本机的客户端还保留 Fast DDS 自动归一化得到的 loopback peer（不向远端公布 loopback）；同步周期统一为 100 ms。所有镜像包含相同的 Fast DDS 2.6.12 局部补丁，修复 TCP 先监听、DDS 逻辑端口后就绪时的重连卡死；`max_logical_port=0` 启用准确端口协商重试，不重发业务请求。首次构建会编译中间件，后续使用 Docker 缓存；详见[补丁说明](../docker/shared/fastdds/README.md)。TCP 模式统一启用 TCP + 同机 SHM（host 网络同时共享 IPC）；不同网络名称空间走 TCP。UDP 模式只使用 UDP。TCP DDS 数据使用动态监听端口，可信 LAN 防火墙必须允许双向节点数据连接；只放通发现端口不足以保证 Service/Action/图像成功。macOS 启动器会探测实际双向 Docker host 网络转发。

自建机器人容器也需采用同一 profile 和已验证的 DDS 运行库；只设置 `ROS_DOMAIN_ID` 不足以保证 TCP 发现及重连。仓库提供的三种镜像统一安装上述补丁，未修补的外部客户端没有通过本次重连验证。

Linux 可在全网统一配置 UDP，但必须先用隔离环境验证。不要为删除组件切换为 multicast 自动发现。所有节点继承统一 profile、domain 和异步发布设置；不要临时导出另一套 DDS 配置。跨机器质量仍需在目标 LAN 实测。

定位顺序：

1. 对比两端保存配置与容器环境的域、发现地址、传输和公布地址；检查发现服务进程及双向 TCP 可达性。
2. 在 Agent 容器内运行原生 `ros2 topic list`，再用 `ros2 topic list --no-daemon --spin-time 3` 区分 daemon 缓存和新 participant 发现，记录完整耗时。不能把增大等待时间当作持续发现失败的修复。
3. 调用公开只读 Service，分别记录发现失败、请求超时、服务端拒绝；查询超时不能解释为动作未执行。
4. 用观测服务取带 session、时间和校准的快照，单独下载图像，校验会话与 SHA-256；DDS 成功不证明 HTTP 图像链路成功。

## 原生 ROS CLI

进入容器 shell 后即可使用标准 ROS 工具。主机执行时加载保存配置：

```bash
# 获取现有 Server 容器；命令本身不启动或修改它
export AGENTICROS_DDS_HOST="$SERVER_IP"
SERVER_CONTAINER=$(docker compose ps -q server)
docker exec -it "$SERVER_CONTAINER" /usr/local/bin/agent-entrypoint bash
# 以下在容器内执行；名称以实际 robot_id/公开接口为准
ros2 topic list
ros2 topic list --no-daemon --spin-time 3
ros2 topic echo /robots/aloha_a/left/driver/state std_msgs/msg/String --once --no-daemon
ros2 service call /robots/aloha_a/left/driver/status std_srvs/srv/Trigger '{}'
```

如果主机 Compose 插值需要 Server 地址，先 `export AGENTICROS_DDS_HOST="$SERVER_IP"`。首次 CLI 查询包含进程启动与 DDS 发现耗时。Humble 原生 `topic list` 的冷启动默认只等 0.5 秒，可能返回不完整快照；需要完整检查时使用上面的 `--no-daemon --spin-time 3`，它每次新建 participant，不借用常驻客户端。测试同时保留默认窗口的缺失记录，不把它记成成功。排查时先确认环境一致，停止该域的旧 CLI daemon（`ros2 daemon stop`）再测试；不修改域或传输来规避故障。

## 常驻通用客户端

在 Server Agent 终端内：

```bash
agenticros-ros graph
agenticros-ros status
agenticros-ros request '{"op":"service","endpoint":"/公开服务名称","type":"std_srvs/srv/Trigger","timeout":5}'
agenticros-ros request '{"op":"read","endpoint":"/公开主题名称","type":"std_msgs/msg/String"}'
```

CLI 仅通过本地 Unix socket 联系常驻进程，每次调用不启动 ROS participant。Service Client、ActionClient 和订阅按端点/类型复用，多个执行子 agent 共用同一进程。`control_available` 按实际收到的状态端点分别列出，不把一臂就绪当作整台机器人就绪；未出现的端点为未知。`service_callable` 只记录最近实际调用的证据，超时或证据过期为未知，明确记录最近错误；不会为了刷新状态遍历调用未知服务。机器人目录直接来自 ROS 图，session 和存活证据来自 ROS 状态样本；旧图端点不证明进程仍在运行；自身保留的订阅不会单独被算作机器人可发现。`read` 首次创建订阅时可能尚无样本，后续返回接收年龄；设备采集时间与 schema 应以公开说明核对。

自定义类型从公开参数/接口取得并构建到 `/workspace/ros_ws`。确认当前动作已核对后执行 `python3 /opt/instance/runtime.py clients-reload`（主机也可 `python3 scripts/lan.py reload-clients`），加载新类型。重载不重启机器人；旧 UUID 发送记录保留，旧句柄不可用时按显式公开状态接口核对。

动作 JSON 字段：`op=goal`、实际 `endpoint/type`、调用方分配的 `goal_id`、含 `session_id` 的 `request`、公开只读 `status_endpoint` 和可选 `status_type`。状态服务是环境公开契约，调用方负责选择正确且只读的端点。`result`/`cancel` 使用同一 `goal_id`。超时返回 `outcome=unknown`、`replay_allowed=false` 和状态核对结果；取消确认不是停止确认，必须检查终态回执、session、UUID 和实测停止。通信层不包含抓取或跨步骤任务程序。

## 更新和停止

日常提示词修改后重新进入 `agent.sh` 即可，规则在取得单 Agent 锁后安装。不会自动覆盖运行会话的规则、用户生成文件或本地编辑的规则冲突。

代码/IDL 改动需显式重建镜像。先停止对应仿真任务并核对停止，再停止机器人及 Server；不要替换正在运行的容器：

```bash
python3 scripts/lan.py robot --robot-id aloha_a --stop
python3 scripts/lan.py stop-server
AGENTICROS_DDS_HOST="$SERVER_IP" docker compose build server
docker build -f docker/robot_instances/aloha_tabletop/Dockerfile -t agenticros-aloha_tabletop:single .
python3 scripts/lan.py server --recreate
python3 scripts/lan.py robot --robot-id aloha_a --recreate
```

`--recreate` 仅替换已停止的对应容器；活动容器会被拒绝，卷和工作区保留。管理入口加载 `runs/server.json`；直接 Compose 启停命令需要提供相同完整配置。机器人只加载本地保存的固定配置，所有节点继承同一 DDS profile，无 HTTP 接入步骤。

## 迁移

从本分支旧 HTTP 接入版本升级：先停止相关任务、机器人和 Server。旧机器人配置可能没有保存 DDS 参数（以前由 HTTP 获取），因此在**每个机器人主机**明确补齐与 Server 相同的配置。下面默认值仅适用于 Server 的 `42 / 11811 / tcp`；如使用自定义值，先调整后再运行：

```bash
export SERVER_IP=192.168.1.10  # 替换实际地址
python3 - <<'PYTHON'
import json, os, shutil
from pathlib import Path
path=Path('runs/robots/aloha_a/client.json')  # 替换 robot_id
backup=path.with_suffix('.json.before-direct-ros')
if backup.exists(): raise SystemExit(f'备份已存在，先核对：{backup}')
data=json.loads(path.read_text())
shutil.copy2(path,backup)
data.pop('AGENTICROS_SERVER',None)
data.update(AGENTICROS_DDS_HOST=os.environ['SERVER_IP'], ROS_DOMAIN_ID=42,
            AGENTICROS_DDS_PORT=11811, AGENTICROS_DDS_TRANSPORT='tcp',
            AGENTICROS_DDS_PREFIX='44.53.00.5f.45.50.52.4f.53.49.4d.41')
path.write_text(json.dumps(data,indent=2)+'\n')
PYTHON
```

Server 主机备份 `runs/server.json` 后，删除废弃的 `AGENTICROS_REGISTRY_PORT` 键；其余地址、域、端口、账号卷配置保留。

重新构建镜像并按上节 `--recreate` 替换已停止的容器。无工作区、凭据、生成代码或观测数据迁移；旧发现数据库不再使用，原文件不自动删除。新 session 下重新核对状态。

先退出旧 Agent 并停止相关旧 Server/机器人，保留账号卷、runs 和图像卷。选择**一个**旧工作区，不自动合并多个账号：

```bash
python3 scripts/migrate_workspace.py --from-directory runs/agent_instances/OLD/workspace
# 或旧版命名数据卷（先构建新 Server 镜像）
python3 scripts/migrate_workspace.py --from-volume OLD_workspace
```

脚本复制到 `runs/workspace` 并逐文件校验，赋予专用工作区所需的组读写权限，源目录/卷保留；目标已存在时拒绝覆盖。旧的 `runs/instances/OLD/workspace` 也可作为来源。迁移不复用旧 join 码或动态域配置，新建 `runs/server.json` 并让机器人以固定地址重新接入。

默认账号卷 `agenticros_codex-home` 继续使用。原命名实例账号可在首次 Server 启动前设置 `AGENTICROS_CODEX_VOLUME=agenticros_codex-home-instance-OLD`（或将同名配置键写入 `runs/server.json`）；Viewer 对应 `AGENTICROS_VIEWER_VOLUME`。直接复用专用卷，不读取或复制宿主凭据。所有旧生成代码和证据保留，但旧 session/旧名称空间的能力记录需要重新发现和验证，不能直接重放旧动作。远程 `Snapshot` 请求现在也要求 `session_id`：从机器人公开接口重新取得 IDL、构建绑定并重载常驻客户端；旧的空请求格式不会在联网模式被兼容接受。

不会自动调用清理脚本、删除 runs 或数据卷。`clean-runtime.sh` 是显式破坏性维护工具，与启动/迁移流程无关。
