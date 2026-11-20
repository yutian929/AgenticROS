# 使用说明

## 固定 Server 和机器人接入

在 Server 主机运行 `python3 scripts/lan.py server --host SERVER_IP`，然后 `bash scripts/agent.sh`。仅一个 Server、一个 `/workspace`，监督 Agent 可派生多个执行子 agent，不需要实例名称。

首次可配置 `--domain-id 42 --port 7447 --discovery-port 11811 --transport tcp`。配置固定保存到 `runs/server.json`；后续不重新分配。运行中传入不同配置会拒绝，先明确停止所有受影响任务与机器人，停止 Server，再编辑配置并重启。不要让任务 Agent 修改 DDS 设置。

在每个机器人主机运行：

```bash
bash scripts/robot.sh aloha_tabletop --server http://SERVER_IP:7447 --robot-id aloha_a
# 同一主机的第二台机器人需要不同图像 HTTP 端口
bash scripts/robot.sh cartesian_gripper --server http://SERVER_IP:7447 --robot-id gripper_b --observation-port 8801
```

重启沿用保存的 Server 地址、robot_id 和环境。ALOHA 是一个 robot_id、一个共享世界/session；两臂位于 `/robots/aloha_a/left/...` 与 `/robots/aloha_a/right/...`，保留 `move_pose`、`move_delta`、`actuate`、三相机和原生 RViz 面板。

唯一身份租约默认 6 秒。若旧进程已停止，等待其租约过期再启动同一 robot_id；新进程生成新 session。旧 session 命令被驱动拒绝，名称空间不会变化。网络故障/Server 重启导致租约过期后，客户端仍继续报告进程存活并提供只读诊断，控制保持锁定，明确重启机器人才能恢复。不会自动重放动作。

## 网络配置与定位

Linux 和 macOS Docker 默认使用 TCP Discovery Server，端口 11811；HTTP presence 7447；机器人图像 HTTP 默认 8800。TCP 模式统一启用 TCP + 同机 SHM（host 网络同时共享 IPC）；不同网络名称空间走 TCP。UDP 模式只使用 UDP。TCP DDS 数据使用动态监听端口，可信 LAN 防火墙必须允许双向节点数据连接；只放通发现端口不足以保证 Service/Action/图像成功。macOS 启动器会探测实际双向 Docker host 网络转发。

Linux 可在全网统一配置 UDP，但必须先用隔离环境验证。不要为删除组件切换为 multicast 自动发现。所有节点继承统一 profile、domain 和异步发布设置；不要临时导出另一套 DDS 配置。跨机器质量仍需在目标 LAN 实测。

定位顺序：

1. HTTP `/health` 读取固定域、发现地址和传输；`/robots` 的 presence 只证明进程续租。
2. `agenticros-ros graph` 检查 ROS 图发现，记录发现耗时。
3. 调用公开只读 Service，分别记录发现失败、请求超时、服务端拒绝；查询超时不能解释为动作未执行。
4. 用观测服务取带 session、时间和校准的快照，单独下载图像，校验会话与 SHA-256；DDS 成功不证明 HTTP 图像链路成功。

## 常驻通用客户端

在 Server Agent 终端内：

```bash
agenticros-ros graph
agenticros-ros status
agenticros-ros request '{"op":"service","endpoint":"/公开服务名称","type":"std_srvs/srv/Trigger","timeout":5}'
agenticros-ros request '{"op":"read","endpoint":"/公开主题名称","type":"std_msgs/msg/String"}'
```

CLI 仅通过本地 Unix socket 联系常驻进程，每次调用不启动 ROS participant。Service Client、ActionClient 和订阅按端点/类型复用，多个执行子 agent 共用同一进程。`control_available` 按实际收到的状态端点分别列出，不把一臂就绪当作整台机器人就绪；未出现的端点为未知。`service_callable` 只记录最近实际调用的证据，超时或证据过期为未知，明确记录最近错误；不会为了刷新状态遍历调用未知服务。Server 目录丢失时仍按 ROS 图列出机器人，标记 `membership_registered=false` 并禁止把发现当作控制许可。`read` 首次创建订阅时可能尚无样本，后续返回接收年龄；设备采集时间与 schema 应以公开说明核对。

自定义类型从公开参数/接口取得并构建到 `/workspace/ros_ws`。确认当前动作已核对后执行 `python3 /opt/instance/runtime.py clients-reload`（主机也可 `python3 scripts/lan.py reload-clients`），加载新类型。重载不重启机器人；旧 UUID 发送记录保留，旧句柄不可用时按显式公开状态接口核对。

动作 JSON 字段：`op=goal`、实际 `endpoint/type`、调用方分配的 `goal_id`、含 `session_id` 的 `request`、公开只读 `status_endpoint` 和可选 `status_type`。状态服务是环境公开契约，调用方负责选择正确且只读的端点。`result`/`cancel` 使用同一 `goal_id`。超时返回 `outcome=unknown`、`replay_allowed=false` 和状态核对结果；取消确认不是停止确认，必须检查终态回执、session、UUID 和实测停止。通信层不包含抓取或跨步骤任务程序。

## 更新和停止

日常提示词修改后重新进入 `agent.sh` 即可，规则在取得单 Agent 锁后安装。不会自动覆盖运行会话的规则、用户生成文件或本地编辑的规则冲突。

代码/IDL 改动需显式重建镜像。先停止对应仿真任务并核对停止，再停止机器人及 Server；不要替换正在运行的容器：

```bash
python3 scripts/lan.py robot --robot-id aloha_a --stop
python3 scripts/lan.py stop-server
AGENTICROS_DDS_HOST=SERVER_IP docker compose build server
docker build -f docker/robot_instances/aloha_tabletop/Dockerfile -t agenticros-aloha_tabletop:single .
python3 scripts/lan.py server --recreate
python3 scripts/lan.py robot --robot-id aloha_a --recreate
```

`--recreate` 仅替换已停止的对应容器；活动容器会被拒绝，卷和工作区保留。管理入口加载 `runs/server.json`；直接 Compose 启停命令需要提供相同完整配置。机器人启动时通过 `/health` 读取网络配置（不注册），把同一域和 DDS 配置传给容器所有节点；驱动首次 `/presence` 再校验配置一致性。

## 迁移

先退出旧 Agent 并停止相关旧 Server/机器人，保留账号卷、runs 和图像卷。选择**一个**旧工作区，不自动合并多个账号：

```bash
python3 scripts/migrate_workspace.py --from-directory runs/agent_instances/OLD/workspace
# 或旧版命名数据卷（先构建新 Server 镜像）
python3 scripts/migrate_workspace.py --from-volume OLD_workspace
```

脚本复制到 `runs/workspace` 并逐文件校验，赋予专用工作区所需的组读写权限，源目录/卷保留；目标已存在时拒绝覆盖。旧的 `runs/instances/OLD/workspace` 也可作为来源。迁移不复用旧 join 码或动态域配置，新建 `runs/server.json` 并让机器人以固定地址重新接入。

默认账号卷 `agenticros_codex-home` 继续使用。原命名实例账号可在首次 Server 启动前设置 `AGENTICROS_CODEX_VOLUME=agenticros_codex-home-instance-OLD`（或将同名配置键写入 `runs/server.json`）；Viewer 对应 `AGENTICROS_VIEWER_VOLUME`。直接复用专用卷，不读取或复制宿主凭据。所有旧生成代码和证据保留，但旧 session/旧名称空间的能力记录需要重新发现和验证，不能直接重放旧动作。远程 `Snapshot` 请求现在也要求 `session_id`：从机器人公开接口重新取得 IDL、构建绑定并重载常驻客户端；旧的空请求格式不会在联网模式被兼容接受。

不会自动调用清理脚本、删除 runs 或数据卷。`clean-runtime.sh` 是显式破坏性维护工具，与启动/迁移流程无关。
