# AgenticROS

让 Codex 成为 ROS 2 空间中的调用中枢，逐步观测、调用接口、读取反馈并决定下一步。
当前提供 `simple_mujoco` 仿真环境，内置 ROS 驱动、RGB-D 观测和浏览器中的 RViz2。

环境为 **Ubuntu 22.04 + ROS 2 Humble**，已在 **Linux AMD64** 验证。
宿主机只需 Docker（含 Compose）和 Python 3，无需安装 ROS。
Agent 不预装仿真专用消息或身体说明，通过 ROS 2 运行时发现环境、获取公开定义并自行编译接口。
已有能力直接调用；缺少能力时才开发、启动 ROS 功能节点。任务决策留在 Codex 会话中，不生成脚本包办整个任务。

客户端实现位于 `docker/clients/<名称>/`，各自包含镜像、驱动、接口和启动脚本；
`docker/server/` 提供实例注册和 ROS 发现服务，`docker/agenticros/` 提供大脑运行环境。
`environment.sh <名称>` 是统一入口，当前提供 `simple_mujoco`。

## 快速开始：一个 server，多个 client

每台机器准备相同版本的本仓库、Docker（含 Compose）和 Python 3，并确保处于可互通的局域网。
以下命令均在各自的仓库根目录执行，镜像会自动构建。

**1. 在 server 上启动 instance：**

```bash
bash scripts/agenticros.sh --instance account-a
```

脚本自动准备注册和 ROS 发现服务，打印 client 加入命令，然后进入 Codex 登录菜单。
首次启动自动选择局域网 IP、可用端口和 ROS domain；多网卡无法确定时会提示选择。

**2. 在每台 client 上复制执行 server 打印的命令：**

```bash
bash scripts/environment.sh simple_mujoco --join CONNECTION_CODE --robot-id robot_a
```

`CONNECTION_CODE` 替换为 server 输出的完整连接码；第二台使用相同连接码，将名称改为 `robot_b`。
client 自动检测自己的 IP、启动仿真并加入这个 instance。server 也可以同时运行一个 client。
连接码包含加入令牌，只交给需要加入的 client。

在 server 的另一个终端检查连接：

```bash
python3 scripts/lan.py status --instance account-a
# 重新显示 client 加入命令：
python3 scripts/lan.py info --instance account-a
```

看到两个机器人均为 `connected: true` 后，在 Codex 中输入：

```text
分别发现并适配 robot_a 和 robot_b，核对各自身份和 session，报告可用能力。
```

Codex 是大脑，负责决策和发布 ROS 调令；已有能力直接调用，缺少能力时先补成可复用 ROS 节点，
再回到调用职责。不要用临时脚本包办整个任务。更多配置见下方的局域网说明。

### 单机模式（带 RViz2）

```bash
bash scripts/environment.sh simple_mujoco # 启动仿真与 RViz2
bash scripts/agenticros.sh                # 启动 Agent，选择账号或 API 并校验
```

Agent 启动后显示终端菜单：ChatGPT 账号、DeepSeek API、其他云端 Responses API。
账号方式按设备码完成浏览器授权；API 方式输入地址、模型和隐藏的 Key，校验通过后进入 Codex。
后续启动先询问是否切换账号或修改连接配置，回车或输入 `n` 直接使用已保存的配置进入 Codex，
不重复发送 API 校验请求；输入 `y` 进入配置菜单，输入 `q` 退出。
在菜单中选择 ChatGPT 会重新发起设备码授权，请在浏览器中选择目标账号；取消或认证失败保留原账号。
旧的 `docker-chat.sh` 保留为兼容入口。
模型名后可选择推理强度；已有配置可选“仅修改推理强度”，不必重填 Key。

依次下达指令：

```text
适配一下你的身体
把红色方块夹取到红色盘子上
```

单机仿真启动脚本会显示 RViz2 的浏览器地址和连接密码，并尝试自动打开浏览器，输入显示的密码即可连接。
没有桌面环境时，手动打开脚本输出的地址；默认端口为 `8080`。

## 常用操作

### 按账号隔离 Agent

使用不同的 instance 名称，分别保存账号、连接配置、Codex 历史和生成的工作文件：

```bash
bash scripts/agenticros.sh --instance account-a
bash scripts/agenticros.sh --instance account-b
bash scripts/agenticros.sh --instance account-a resume --last
# 只进行设备码登录（浏览器中选择对应账号）：
bash scripts/docker-login.sh --instance account-a
```

新 instance 首次选择 ChatGPT 时需要独立登录；再次进入同名 instance 会复用其数据。
名称使用小写字母、数字和连字符，首位不能是连字符，最长 48 字符；`--instance` 放在其他参数之前。
`default` 是原有环境，不传参数等同于 `--instance default`，原账号和文件不迁移、不删除。
命名环境的工作目录为 `runs/instances/<名称>/workspace/`，认证卷默认为
`agenticros_codex-home-instance-<名称>`。删除容器不会清除这些数据。
命名 instance 启动时自动准备独立 LAN server，并打印 client 加入命令；同一 instance 只运行一个控制 Agent。
不带 `--instance` 的默认入口继续使用原有单机环境。旧命名 instance 的账号和工作区保留，
但不会自动接管原有共享仿真，需要用新的加入命令启动对应 client。

删除一个旧单机 instance（不可恢复，会删除账号、连接配置、Codex 历史和工作文件；
含 LAN 配置的 instance 会被此脚本拒绝）：

```bash
bash scripts/delete-instance.sh --instance account-a --dry-run # 预览
bash scripts/delete-instance.sh --instance account-a           # 执行删除
```

必须明确提供名称；删除原有默认环境需显式指定 `--instance default`。
若仍有容器引用该环境（包括已停止的容器），脚本拒绝删除并列出容器 ID，须先退出并移除这些容器。
脚本不停止仿真，不删除共享观测、其他 instance、镜像或历史迁移保留的旧工作区卷。
重复删除不存在的 instance 不会创建卷或工作目录。

### 继续会话与停止仿真

LAN 模式按 server → client → 任务的顺序恢复：

```bash
# Server：复用 instance、账号和加入令牌
bash scripts/agenticros.sh --instance account-a resume --last
# 各 client：复用已保存的连接配置，无需重新输入连接码
bash scripts/environment.sh simple_mujoco --instance account-a --robot-id robot_a
# 停止时先结束 Codex 中的任务，再在每台 client 上执行（替换自己的 robot-id）
python3 scripts/lan.py client --instance account-a --robot-id robot_a --stop
# 最后在 server 上停止通信服务
python3 scripts/lan.py stop-server --instance account-a
```

退出 Codex 不会自动停止 server 或 client；上述停止命令保留数据。
client 重启会重置仿真场景，继续会话后必须重新发现并核对机器人 session。

旧单机模式：

```bash
bash scripts/agenticros.sh resume --last  # 继续会话
bash scripts/environment.sh simple_mujoco # 停机或断电后恢复环境
# 确认控制任务结束后停止环境：
docker compose stop simple_mujoco
```

模型配置见 [codex.toml](docker/agenticros/codex.toml)，默认 `gpt-6-astra / medium`。
菜单选择、模型和 Key 保存在容器专用 Codex 卷，校验失败不覆盖原配置；API 校验会产生少量用量。
命名 instance 的生成代码、编译产物和动作证据保存在 `runs/instances/<名称>/workspace/`；
默认单机环境使用 `runs/workspace/`，两者都对应容器内 `/workspace`，Git 默认忽略。
首次切换时，先退出旧 Agent；启动脚本会复制并校验旧工作区卷，保留原卷。不会自动合并非空目录。
认证、相机观测和 VNC 密码仍由独立数据卷保留；仿真重启会重置场景并更换 session，继续任务前须重新核对与观测。
一次只运行一个控制 Agent，不要使用 `down -v` 删除数据卷。

生成的 ROS 包位于 `runs/workspace/ros_ws/src/`，能力及调用方法记录在 `capabilities/manifest.json`，
观测、回执和进程日志位于 `evidence/`（后两者同属该工作区）。新容器会复用原工作区；
需要空工作区的自主验收时使用 `python3 scripts/docker-trial.py`，它会运行适配及抓放任务，须先确认环境空闲。

[开发记录](docs/development-record.md)包含架构、配置、迁移、测试命令和验证历史。
当前仅验证仿真环境；新的逐步调用规则与工具检查不代表 Codex 已完成自主适配或抓取验收。

## 局域网：一个大脑、两台仿真客户端

LAN v1 使用 Linux Docker host 网络，支持同一可信局域网中的一个 instance 管理多台
`simple_mujoco` 客户端。客户端无桌面，图像通过 ROS 和独立快照下载提供。
不带 instance 的默认入口保留原有单机模式；命名 instance 自动启用 LAN。
两端需要相同版本的仓库、Docker（含 Compose）和 Python 3，无需宿主机 ROS。

**Server 只执行一条命令：**

```bash
bash scripts/agenticros.sh --instance account-a
```

首次自动选择本机局域网 IP，分配可用的注册端口、DDS 发现端口和 ROS domain，启动 server，
再进入 Codex 登录菜单。已存在的 instance 复用原配置，不重启在线 server，也不更换加入令牌。
端口和 domain 会避开本仓库其他 instance 的已保存配置；domain `42` 留给原有单机环境。
多网卡无法唯一确定 IP 时会提示选择；也可以显式指定：

```bash
AGENTICROS_HOST=192.168.1.10 bash scripts/agenticros.sh --instance account-a
```

**Client 复制 server 打印的完整命令，修改自己的机器人名称：**

```bash
bash scripts/environment.sh simple_mujoco --join <连接码> --robot-id robot_a
# 另一台 client 使用同一个连接码，把 robot_a 改成 robot_b
```

`<连接码>` 是占位符，实际粘贴时不带尖括号。连接码包含 server 地址、instance 身份和加入令牌，
只交给需要加入的 client。client 根据到 server 的路由自动选择自己的 IP，自动构建并启动仿真。
同机多 client 自动分配不同的快照端口；仍可用 `--host`、`--observation-port` 显式指定。
连接码与已有 client 归属不一致时会拒绝覆盖。

需要重新显示加入命令、查看在线机器人或单独准备 server 时：

```bash
python3 scripts/lan.py info --instance account-a
python3 scripts/lan.py status --instance account-a
python3 scripts/lan.py server --instance account-a  # 只启动通信服务，不进入 Codex
```

每台机器的配置保存在忽略目录 `runs/instances/account-a/lan/`，令牌文件权限为 `600`。
再次启动 client 不必重新输入连接码：

```bash
bash scripts/environment.sh simple_mujoco --instance account-a --robot-id robot_a
```

旧的 `--instance ... --server ... --host ...` 与 `AGENTICROS_JOIN_TOKEN` 方式仍可使用。
脚本不会替换正在运行的容器；更新镜像后应先明确停止对应客户端，再自行重建容器，保留观测卷。
现有 `delete-instance.sh` 会拒绝删除含 LAN 配置的 instance，以保留连接配置和客户端数据。

```bash
# Server 查看在线客户端，也可读取 ROS topic /agenticros/robots
python3 scripts/lan.py status --instance account-a
# 给 Codex 的指令示例：分别发现并适配 robot_a、robot_b，核对身份后报告可用能力

# Client 明确停止自己的仿真，保留配置和观测
python3 scripts/lan.py client --instance account-a --robot-id robot_a --stop
# Server 明确停止实例通信服务；客户端租约到期后停止动作并拒绝新目标
python3 scripts/lan.py stop-server --instance account-a
```

ROS 地址由实例目录返回，格式为 `/robots/<robot_id>/s_<session_id>/...`。
机器人重启后会话和地址改变，旧回执与新动作不会混用。不同机器人使用独立局部坐标系，
不能直接把 A 的坐标作为 B 的目标。Codex 仍通过 ROS 接口适配和调用能力；不预装设备适配器。

运行约束：

- 同一机器人 ID 的有效注册只能有一个；加入令牌和 instance 必须匹配。启动入口用固定容器名限制同一 instance 的控制 Agent 数量。
- 心跳租约为 6 秒。租约失效后，客户端中止动作、记录停止回执并拒绝新目标；通信恢复不会自动恢复控制或重发动作。需操作者明确停止并重新启动 client，再核对新会话。
- 快照返回同步 RGB、深度、标定和身份，以及只允许读取该快照文件的 URL 和 SHA256；Agent 下载到专用工作区，无需共享客户端卷。下载不是新的感知能力。
- 注册服务从 TCP `7447`、发现从 UDP `11811`、客户端快照从 TCP `8800` 起选择可用端口，以实际输出为准。DDS 数据仍在主机间直接传输；需允许可信主机间双向 DDS UDP 流量，仅开放发现端口不够。
- 本版是可信局域网仿真功能，加入令牌用于注册，ROS domain/namespace 用于路由与避免冲突，**不提供 DDS 身份认证、加密或对恶意同网段节点的隔离**。不面向公网或物理机器人。
- Agent 仅绑定专用工作区和只读配置，不挂载宿主机 home、源码、宿主机凭据或 Docker socket；LAN host 网络会扩大网络可达范围。独立评估器仍只在客户端私有 Unix socket 上，未开放到网络。
- 当前小脑代码可在 Agent 中生成并经 ROS 调用远端驱动；自动部署生成节点到远端 client 不在本版范围内。

验证（不会操作已有仿真，另建两台隔离测试客户端，保留生成文件与数据卷）：

```bash
bash scripts/test.sh
# 集成测试前构建专用镜像
docker build -f docker/server/Dockerfile -t agenticros-server:lan .
docker build -f docker/agenticros/Dockerfile -t agenticros-agenticros:lan .
docker build -f docker/clients/simple_mujoco/Dockerfile -t agenticros-simple_mujoco:lan .
python3 scripts/audit_agent_image.py --image agenticros-agenticros:lan
python3 scripts/check_lan.py                  # 同机 host 网络
python3 scripts/check_lan.py --separate-ips   # 独立 IP 与网络命名空间，模拟多机
```

`check_lan.py` 验证实际挂载、运行时获取接口、两机器人独立动作与观测、会话拒绝、互斥、取消、
断线停止和重连不自动恢复。`--separate-ips` 在隔离 Docker 网络中为 server、Agent、两台 client
分配四个独立 IP，检查实际网络命名空间；客户端使用相同端口，也不能通过彼此的 localhost 或共享观测卷通信。
这是单宿主机的跨 IP 集成测试，不覆盖真实局域网的路由、防火墙和链路故障，也不代表 Codex 自主适配或任务评估。
