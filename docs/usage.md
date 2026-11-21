# 使用说明

本文集中说明当前启动、配置、规则更新和迁移方式。实现演进、测试结果与限制见[开发记录](development-record.md)。

<a id="instance-layout"></a>

## 实例布局与域隔离

```text
docker/
  agent_instance/
    agent/                 # Codex、规则、常驻 ROS 客户端、Agent 运行管理
      Dockerfile
    viewer/                # ROS 图发现、RViz、noVNC、Viewer 运行管理
      Dockerfile
  robot_instances/
    cartesian_gripper/
    aloha_tabletop/
    common/robot_identity.py
    robot_docs/<TYPE>/     # 公开手册与接口定义
```

Agent 与 Viewer 独立构建和运行；`common/robot_identity.py` 只为机器人提供身份与 session。没有全局网络注册或 DDS 配置，顶层 `docker/shared` 已删除。Agent 不安装 RViz/桌面，Viewer 不包含 Codex、工作区、账号卷或常驻客户端。

```text
runs/<DOMAIN_ID>/
  agent_instance/
    agent/config.json
    agent/workspace/
    viewer/config.json
  robot_instance/
    <ROBOT_TYPE>/<ROBOT_ID>/client.json
```

省略域参数固定使用 42。Compose 项目为 `agenticros-<DOMAIN_ID>`，机器人项目追加 `-robot-<ID>`；账号和 Viewer 口令卷分别为 `agenticros-<DOMAIN_ID>_codex-home`、`agenticros-<DOMAIN_ID>_viewer-auth`。镜像共用，工作区和登录状态不跨域共享。

Viewer 网页端口为 `8038 + DOMAIN_ID`，VNC 端口为 `5858 + DOMAIN_ID`，因此域 42 使用 8080/5900，域 43 使用 8081/5901。外部程序占用端口时，可编辑该域 `agent_instance/viewer/config.json` 中的端口后重启 Viewer。所有管理命令（含 info、viewer-password、stop-server、stop-viewer）支持 `--domain-id`。

`clean-runtime.sh --yes` 仍清理本项目所有域。旧版未按域划分的目录和容器不会在启动时自动迁移或删除。


## 原生 ROS 网络

每台 Linux 主机接入同一允许组播和 DDS UDP 的子网，容器使用 host 网络和 host IPC。只需统一域编号：

```bash
# Agent 主机
bash agent.sh agent --domain-id 42
# Viewer 独立容器（可在 Agent 启动前运行）
bash agent.sh viewer --domain-id 42
# 机器人主机
bash robot.sh aloha_tabletop --domain-id 42
bash robot.sh cartesian_gripper --domain-id 42
```

没有固定发现进程、Server IP、DDS 端口、GUID 前缀或 TCP 配置。原生 ROS 节点只要域一致即可参与发现；机器人可以先启动。
配置首次保存到 runs/<DOMAIN_ID>/agent_instance/agent/config.json 和 runs/<DOMAIN_ID>/robot_instance/<ROBOT_TYPE>/<robot_id>/client.json，各域独立保存。省略 `--domain-id` 固定使用 42，不读取宿主 ROS_DOMAIN_ID。

Agent 直接订阅机器人的 RGB、深度、CameraInfo、TF 和状态话题，所需观测保存到自己的 `/workspace/evidence/`。机器人没有快照 Service、HTTP 文件下载服务或观测目录挂载；启动不再接受 `--host`、`--observation-port`。
RGB/深度/CameraInfo 按 header 时间戳和光学坐标系配对，动态 TF 使用对应采集时间，状态话题单独检查新鲜度和 session。

保留稳定 robot_id/session、UUID、动作互斥、取消及停止核对。Agent 退出或网络断开不代表已接受的有限动作停止；禁止重发结果未知的动作。

排查先核对域编号、网络组播及防火墙，再检查话题数据与只读 Service。macOS 同机验证不代表跨物理主机 LAN 验证。

## 机器人实例

`bash robot.sh aloha_tabletop --domain-id 42` 每次新建一个实例，自动生成 `aloha_tabletop_<8位随机后缀>`（例如 `aloha_tabletop_a1b2c3d4`）；本机同域发生重名时重新生成，已有长 ID 仍可使用。同类型可以并存，不需要 `--robot-id`。脚本输出完整 ID 和 `/robot/<ID>` 名称空间；话题、服务、动作、Viewer 和 Agent 状态发现均使用此前缀。

再次执行启动命令会生成新 ID。管理既有实例时使用输出的 ID，例如 `python3 scripts/runtime.py robot --robot-id ID --stop`；需要恢复原实例时使用同一 `--robot-id ID`。没有中心名称注册服务。

## 原生 ROS CLI

进入容器 shell 后即可使用标准 ROS 工具。主机执行时加载保存配置：

```bash
# 获取现有 Server 容器；命令本身不启动或修改它
SERVER_CONTAINER=$(docker compose -f compose.agent.yaml ps -q server)
docker exec -it "$SERVER_CONTAINER" /usr/local/bin/agent-entrypoint bash
# 以下在容器内执行；名称以实际 robot_id/公开接口为准
ros2 topic list
ros2 topic list --no-daemon --spin-time 3
ros2 topic echo /robot/aloha_a/left/driver/state std_msgs/msg/String --once --no-daemon
ros2 service call /robot/aloha_a/left/driver/status std_srvs/srv/Trigger '{}'
```

首次 CLI 查询包含进程启动与 DDS 发现耗时。Humble 原生 `topic list` 的冷启动默认只等 0.5 秒，可能返回不完整快照；需要完整检查时使用上面的 `--no-daemon --spin-time 3`，它每次新建 participant，不借用常驻客户端。测试同时保留默认窗口的缺失记录，不把它记成成功。排查时先确认环境一致，停止该域的旧 CLI daemon（`ros2 daemon stop`）再测试；不修改域或传输来规避故障。

## 独立 Viewer

`docker/agent_instance/agent/` 与 `docker/agent_instance/viewer/` 各自构建镜像。`agent.sh agent` 只启动 Agent，`agent.sh viewer [--domain-id 42]` 只启动 Viewer。两者以同一 ROS 域独立发现机器人，没有共享进程或 Unix socket。

Viewer 在域 42 默认访问 `http://127.0.0.1:8080/vnc.html`，启动命令 `bash agent.sh viewer` 会直接打印地址和口令，`python3 scripts/runtime.py stop-viewer` 只停止 Viewer。域 43 对应网页端口 8081、VNC 端口 5901；端口按域递增。管理命令用 `--domain-id` 选择域。Viewer 配置保存在 `runs/<DOMAIN_ID>/agent_instance/viewer/config.json`。Agent 容器不挂载口令卷；Viewer 不挂载工作区或 Codex 账号卷。

从旧布局升级时，新入口创建带域编号的独立容器，不复用旧容器。旧 Viewer 若占用相同端口，需先停止旧 Viewer 或修改新域 Viewer 配置中的端口。旧卷和工作区不会自动删除。

## 常驻通用客户端

在 Server Agent 终端内：

```bash
agenticros-ros graph
agenticros-ros status
agenticros-ros request '{"op":"service","endpoint":"/公开服务名称","type":"std_srvs/srv/Trigger","timeout":5}'
agenticros-ros request '{"op":"read","endpoint":"/公开主题名称","type":"std_msgs/msg/String"}'
```

CLI 仅通过本地 Unix socket 联系常驻进程，每次调用不启动 ROS participant。Service Client、ActionClient 和订阅按端点/类型复用，多个执行子 agent 共用同一进程。`control_available` 按实际收到的状态端点分别列出，不把一臂就绪当作整台机器人就绪；未出现的端点为未知。`service_callable` 只记录最近实际调用的证据，超时或证据过期为未知，明确记录最近错误；不会为了刷新状态遍历调用未知服务。机器人目录直接来自 ROS 图，session 和存活证据来自 ROS 状态样本；旧图端点不证明进程仍在运行；自身保留的订阅不会单独被算作机器人可发现。`read` 首次创建订阅时可能尚无样本，后续返回接收年龄；设备采集时间与 schema 应以公开说明核对。

自定义类型从只读挂载的 robot_docs/<型号>/interfaces 复制到工作区，并构建到 `/workspace/ros_ws`。确认当前动作已核对后执行 `python3 /opt/instance/runtime.py clients-reload`（主机也可 `python3 scripts/runtime.py reload-clients`），加载新类型。重载不重启机器人；旧 UUID 发送记录保留，旧句柄不可用时按显式公开状态接口核对。

动作 JSON 字段：`op=goal`、实际 `endpoint/type`、调用方分配的 `goal_id`、含 `session_id` 的 `request`、公开只读 `status_endpoint` 和可选 `status_type`。状态服务是环境公开契约，调用方负责选择正确且只读的端点。`result`/`cancel` 使用同一 `goal_id`。超时返回 `outcome=unknown`、`replay_allowed=false` 和状态核对结果；取消确认不是停止确认，必须检查终态回执、session、UUID 和实测停止。通信层不包含抓取或跨步骤任务程序。

## Agent 规则更新

修改 `docker/agent_instance/agent/` 下的 `AGENTS.md`、`contract.md`、`skills/` 或 `agents/` 后，
退出当前 Agent 会话，再用原启动命令启动或恢复会话：

```bash
bash agent.sh agent --domain-id 42 resume --last
```

启动器会将本机仓库里的规则制作成工作区内的版本快照，再在启动 Agent 前同步到实际加载位置。
不需要重新构建镜像或重启 Server、机器人，
修改不会自动注入正在执行的会话。远程部署时，先把代码更新到运行启动脚本的 Server 主机。

同步范围：

| 仓库源文件 | Agent 工作区位置 |
| --- | --- |
| `AGENTS.md` | `/workspace/AGENTS.md` |
| `contract.md` | `/workspace/contract.md` |
| `skills/` | `/workspace/.agents/skills/` |
| `agents/` | `/workspace/.codex/agents/` |

同名规则以仓库版本为准。此前同步且随后从仓库删除的文件，在本地未修改时会移除；
已在工作区修改的旧文件会保留。Agent 自行生成的其他 skill、节点、证据及账号数据保留。
快照及同步清单位于工作区 `.agenticros/`，不会挂载整个源码仓库。
先取得已有的单会话锁，再安装规则；第二个启动请求不会改写正在运行的会话规则。

镜像内规则仅作为首次初始化时的默认值。正常使用 `agent.sh`、`scripts/runtime.py login`
入口才能同步本机最新规则；直接执行旧镜像的原始 entrypoint 不具备这一功能。
Python/ROS 依赖、驱动、Viewer 等程序代码的更新仍按原流程重建对应镜像。

## 更新和停止

代码/IDL 改动需显式重建镜像。先停止对应仿真任务并核对停止，再停止机器人及 Server；不要替换正在运行的容器：

```bash
python3 scripts/runtime.py robot --robot-id aloha_a --stop
python3 scripts/runtime.py stop-server
docker compose -f compose.agent.yaml build server
docker build -f docker/robot_instances/aloha_tabletop/Dockerfile -t agenticros-aloha_tabletop:single .
python3 scripts/runtime.py server --recreate
python3 scripts/runtime.py robot --robot-id aloha_a --recreate
```

`--recreate` 仅替换已停止的对应容器；活动容器会被拒绝，卷和工作区保留。管理入口加载 `runs/<DOMAIN_ID>/agent_instance/agent/config.json`；直接 Compose 启停命令需要提供相同完整配置。机器人只加载本地保存的固定配置，所有节点使用同一域的原生 DDS 自动发现，无 HTTP 接入步骤。

## 迁移

从旧固定 Discovery Server 版本升级：先核对任务停止，再显式停止并重建 Server 和机器人镜像，用 `--recreate` 替换已停止容器。运行中的旧 Server 会被启动器识别并拒绝混用；没有自动部署或重启。
旧 JSON 中 AGENTICROS_DDS_* 字段已不被 Compose 使用，可在备份后删除；保留 ROS_DOMAIN_ID、机器人身份、工作区和账号卷。旧的 --server、--transport、--discovery-port 参数已删除。

先退出旧 Agent 并停止相关旧 Server/机器人，保留账号卷、runs 和图像卷。选择**一个**旧工作区，不自动合并多个账号：

```bash
python3 tools/migrate_workspace.py --from-directory runs/agent_instances/OLD/workspace
# 或旧版命名数据卷（先构建新 Server 镜像）
python3 tools/migrate_workspace.py --from-volume OLD_workspace
```

脚本复制到 `runs/<DOMAIN_ID>/agent_instance/agent/workspace` 并逐文件校验，赋予专用工作区所需的组读写权限，源目录/卷保留；目标已存在时拒绝覆盖。旧的 `runs/instances/OLD/workspace` 也可作为来源。迁移不复用旧 join 码或动态域配置，新建 `runs/<DOMAIN_ID>/agent_instance/agent/config.json` 并让机器人以相同域编号重新接入。

新域使用独立账号卷 `agenticros-<DOMAIN_ID>_codex-home` 和口令卷 `agenticros-<DOMAIN_ID>_viewer-auth`，需要分别登录。旧容器、旧卷和旧目录保留，不自动迁移或删除。工作区迁移可通过上面的命令加 `--domain-id` 指定目标域。所有旧生成代码和证据保留，但旧 session/旧名称空间的能力记录需要重新发现和验证，不能直接重放旧动作。旧的 Snapshot 接口已删除，旧适配代码应改为订阅原生传感器话题。

不会自动调用清理脚本、删除 runs 或数据卷。`clean-runtime.sh` 是显式破坏性维护工具，与启动/迁移流程无关。

## 机器人公开资料

```text
docker/robot_instances/robot_docs/
  aloha_tabletop/
    manual.md
    interfaces/
  cartesian_gripper/
    manual.md
    interfaces/
```

Agent 启动时把此目录只读挂载到 `/workspace/robot_docs/`。资料按型号共享，Agent 的工作区、容器和账号仍按 ROS_DOMAIN_ID 隔离。只放公开手册、配置说明和 ROS 接口定义，不放仿真实现、隐藏场景、评测代码或凭据。

Agent 直接读取文件，需要接口绑定时把对应 `interfaces/` 复制到 `/workspace/ros_ws/src/<包名>/` 再执行 colcon build。资料目录是只读的，不能在里面写缓存或构建；生成文件和观测保存在 Agent 工作区。修改已有文档会直接反映到挂载中，无需重建 Agent 镜像。

手册更新通过只读挂载直接生效。修改自定义接口定义后，需要重建机器人镜像，并在 Agent 工作区重新编译对应接口包。

资料目录存在不代表机器人在线：仍需通过 ROS 核对机器人类型、UUID、session、状态及文档版本。跨宿主机使用时，需要事先把对应资料放到 Agent 宿主机；ROS domain 不同步文件。

旧 Agent 容器没有这个挂载，需要正常退出会话、停止后重建（域编号按实际填写）：

```bash
python3 scripts/runtime.py stop-server --domain-id 42
python3 scripts/runtime.py server --recreate --domain-id 42
bash agent.sh agent --domain-id 42
```

工作区、账号卷和旧证据保留。清理运行数据不会删除源代码中的 robot_docs。

## 接入新机器人

可直接复制的文件和逐项修改说明见 [最小模板](../docker/robot_instances/_template/README.md)。模板自身不作为可启动型号，仅提供只读状态，不连接硬件。

1. 创建 `docker/robot_instances/<TYPE>/Dockerfile`。型号目录名使用小写字母开头、小写字母/数字/下划线，总长最多 63；`common`、`robot_docs` 为保留目录。启动器扫描直属目录的 Dockerfile，不维护固定型号列表。
2. 在镜像中定义启动命令和 `HEALTHCHECK`。内部实现可用 Python、C++、原生 ROS 包或厂商 SDK，不要求继承公共基类。当前通用容器为 host 网络、host IPC、只读根文件系统和可写 `/tmp`；同机使用 Fast DDS 共享内存时，建议与 Agent 一样使用 UID 1001，避免跨用户共享内存权限问题；需要 GPU、设备映射或额外写入目录时，需明确增加对应容器配置，不能仅靠 Dockerfile 获得这些资源。
3. 启动程序继承 `ROS_DOMAIN_ID`，用 `AGENTICROS_ROBOT_ID` 构造 `/robot/<ID>` 命名空间。型号和机器人 ID 由启动器保存到 `runs/<DOMAIN_ID>/robot_instance/<TYPE>/<ID>/client.json`；恢复实例读取配置，不从 ID 推断型号。每次驱动进程启动更新 session，控制接口保留身份、动作 UUID、互斥与取消/停止约定。
4. 将公开手册和必要的接口定义放到 `docker/robot_instances/robot_docs/<TYPE>/`，Agent 自动通过现有只读挂载读取。资料应说明单位、坐标系、限制、状态/控制接口和版本。私有实现、仿真内部状态与评测代码放在型号目录内。

```bash
bash robot.sh --list
bash robot.sh my_robot --domain-id 42
```

启动器只在镜像不存在时构建；修改 Dockerfile/实现后需显式重建镜像。机器人镜像必须提供有效 HEALTHCHECK，启动时由 Compose 等待其就绪。健康标准由型号决定，不要求相机：例如底盘检查驱动连接，相机检查帧更新。现有两个仿真类型保留原来的相机新鲜度检查，Cartesian 的独立桌面模式还检查 GUI。`common/robot_identity.py` 继续作为可选的小型身份工具，不承载注册、发现或型号逻辑。

管理既有实例可以只提供 ID 和域；新实例必须指定型号。相同型号可启动多份，未知型号或不存在的既有 ID 会明确报错，不自动回退到其他型号。

型号扩展验证：95 项回归测试通过；隔离测试动态添加一种无相机 ROS 类型，经原始 robot.sh 自动构建、启动两个 UUID 实例，验证自定义健康检查的失败/恢复、既有实例恢复和 Agent 原生 ROS 接收。证据：`runs/checks/agenticros-type-check-29c87f0f/result.json`。现有两种机器人自身健康检查、只读 robot_docs 和公开接口编译也通过：`runs/checks/agenticros-robot-docs-check-ce04a879/result.json`。均为单机隔离容器验证，未操作真实硬件。

### Robot shell

`bash robot.sh <TYPE> [--domain-id 42]` opens Bash after the robot is healthy when invoked from a terminal. Standard ROS and `/opt/driver_ws/install/setup.bash` (if present) are loaded. Ctrl+D/`exit` disconnects this shell without stopping the driver. Non-interactive scripts only start the robot. Re-enter with `python3 scripts/runtime.py robot --robot-id <ID> --domain-id 42 --shell`; this reuses the existing instance.

### Viewer 显示

Viewer 启动一个普通 RViz，顶部只报告 ROS 图中发现的机器人名称。发现记录可能随 DDS 图更新延迟变化，不代表设备健康。没有机器人选择器，也不会根据机器人、话题或 session 变化重配 RViz。通过 RViz 的 Add、Fixed Frame 和 File 菜单自行选择显示内容与保存配置；Open RViz 仅在窗口关闭后重新打开当前配置。容器重建不保留 /tmp 中的配置。
