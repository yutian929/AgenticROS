# 开发记录

容器使用 Ubuntu 22.04 / ROS 2 Humble；早期 Linux AMD64 验证与本次 macOS Apple Silicon 验证分别记录如下。
Agent instance 在一个容器内运行注册与发现服务、Agent 与 RViz2/noVNC；Robot instance 独立运行仿真与观测。
旧单机模式仍保留独立 Agent 容器及带 RViz2 的 `simple_mujoco` 容器。

## 当前开发说明

Docker 源码按运行实例组织，主机角色不作为目录层级：

```text
docker/
├── agent_instance/
│   ├── Dockerfile
│   ├── runtime.py          # 容器内进程管理
│   ├── agent/              # Codex、账号配置、规则与技能
│   ├── registry/           # 注册与发现服务
│   └── viewer/             # RViz2 / noVNC
├── robot_instances/
│   └── simple_mujoco/      # 仿真、驱动、观测与公开接口
│       └── registration.py # 注册、心跳与租约
└── shared/                 # 共用 DDS 网络配置
```

构建目标、镜像标签、容器名、数据卷和 `runs/` 路径保持兼容。
`agent_instance/agent/` 是 Agent 通用组件；完整 Agent instance 的构建入口是
`docker/agent_instance/Dockerfile`，构建目标为 `instance`。
Robot instance 的独立构建入口是 `docker/robot_instances/simple_mujoco/Dockerfile`。


- `docker/robot_instances/simple_mujoco/`：私有仿真、原始驱动、RGB-D 点云、评估器，以及公开 URDF、RViz 配置和容器图形进程管理。
- `docker/agent_instance/agent/`：通用 ROS 工具、适配要求、setup-body skill 和 Codex 配置，不含环境专用接口或身体资料。
- `docker/robot_instances/simple_mujoco/interfaces/`：归仿真端所有的原始 ROS IDL，包名保留为 `agenticros_driver_interfaces`。
- `docker/robot_instances/simple_mujoco/body.md`：由仿真端通过 ROS 参数公开的设备说明，不复制进 Agent 镜像。
- `scripts/`、`tests/`：启动和验收工具，无预写适配答案。

```bash
docker compose --profile simulation build
bash scripts/test.sh                      # 单测，不启动驱动
python3 scripts/audit_agent_image.py       # 镜像内容审计
python3 scripts/check_docker.py            # 已启动环境的 ROS、观测和隔离验证
python3 scripts/check_rviz.py              # RViz、标准观测话题、点云和 noVNC 验证（环境空闲时）
python3 scripts/check_docker.py --motion   # 小幅动作/取消测试，仅在没有任务时运行
python3 scripts/docker-trial.py            # 新工作区的自主适配与任务试验
bash scripts/docker-evaluate.sh           # 仿真容器内独立评估
```

自主试验保留容器、工作区和 `runs/` 证据；原有试验容器和日志不因重命名而改动。
单测、集成、自主适配和任务评估分别验收，结果与限制见下方开发与验证历史。
旧原生实现仅保留在 Git 历史和归档分支。

### Codex 逐步调用与功能节点

Agent 的 `AGENTS.md`、`contract.md` 和 `setup-body/SKILL.md` 使用中文说明，命令、类型和字段名保留原样。
Codex 在会话中按“观测 → 选择下一步 → 调用 → 获取反馈 → 再决策”推进任务。
已有 ROS 接口满足需求就直接使用；缺少可复用能力或可靠的执行保障时，再开发、构建并启动功能节点。
节点内部可完成感知、规划、控制、反馈和取消，不负责替 Codex 编排整个用户任务。
禁止生成完整抓放等任务脚本、行为树或任务节点，也不能通过 Shell 串联预设动作绕过反馈判断。

容器初始化以下目录，不预装能力实现，也不自动启动生成节点或执行运动：

```text
/workspace/
├── AGENTS.md
├── contract.md
├── .agents/skills/setup-body/SKILL.md
├── ros_ws/src/                 # 自行开发的功能包和获取的公开 IDL
├── capabilities/manifest.json  # 自行生成；接口、调用模板、来源和验证证据
└── evidence/                   # 观测、动作回执、验证结果、节点进程日志
```

`ros_ws/build/`、`install/`、`log/` 在 Agent 构建时生成。功能节点在当前容器内持续运行，
由 Agent 记录启停、健康检查和进程归属；退出容器后进程结束，源码和记录保留。
新容器加载 overlay、恢复必要节点前须重新核对环境，不能恢复未确认的目标。
终端 Codex 不会天然持续接收 topic：节点订阅并维护观测，Codex 按需读取最新结果及实际图像。
CLI 等待超时不等于动作取消；UUID、session、执行程度和停止确认仍按契约处理。

`scripts/check_capabilities.py <宿主机工作区> --container <Agent容器名>` 检查能力清单结构、证据文件，
再用固定只读 ROS CLI 查询核对已验证接口类型和所声明生成节点是否在线。不执行清单中的命令，不发送运动。
`docker-trial.py` 在适配与任务两个阶段之间执行此检查，不再要求 `adapter/body.py`；
生成包、能力清单和证据直接保留在该试验的 `workspace/` 中。
图和文件检查不能证明功能语义、停止状态或 Codex 遵守逐步调用规则；需审阅两个阶段的 JSONL、生成源码及实际反馈。
任务是否成功仍由仿真容器内独立评估判定，评估真值不提供给 Agent。

本模式不兼容旧 `Body` 类作为必需产物的验收方式；旧工作区与备份不自动删除，也不作为新模式的验证证据。
新建日常容器仍复用 `runs/workspace/`，独立空工作区验收使用 `docker-trial.py`，仅在环境空闲且允许运动时运行。

### 隔离边界

AgenticROS 不挂载整个项目、宿主机 home 或 Docker socket，只预装通用 ROS 工具和适配要求。
仓库内仅专用产物目录 `runs/workspace/` 绑定到 `/workspace`；不会暴露上级源码目录，观测仍为只读挂载。
启动时没有设备专用消息绑定、端点名称或身体说明。Agent 从 ROS 图发现节点、话题、服务和 action，
通过标准参数服务获取设备公开的 `device_documentation` 与 `interface_sources`，自行构建接口包并编写调用代码。
Humble 的图发现可以返回自定义类型名称，但 `ros2 interface show` 和客户端调用需要本地定义及类型支持；
标准 ROS 类型可直接使用，缺少的自定义定义不能仅靠类型名猜测。
`interface_sources` 只包含接口包的四个声明/构建文件，不包含驱动、仿真、评估或任务程序。
这些参数是本环境的公开发现约定，其他 ROS 设备不一定提供；缺少必要定义时应报告无法调用。
已有持久工作区可能保留之前的说明、代码和记忆，不会为制造“未知”而自动删除；首次发现验收使用独立空工作区。
评估器只监听仿真容器内部 Unix socket，不通过 ROS、HTTP 或观测卷提供真值。
两侧非 root、移除 capabilities；Codex 使用 `danger-full-access`，因为嵌套 bwrap 不可用。
DDS 未认证且 Agent 有出站网络；这是文件和进程隔离，不是防御所有恶意行为的沙箱。

### 容器内 RViz2 与浏览器观察

`simple_mujoco` 内由非 root 进程管理器启动驱动、`robot_state_publisher`、Xvfb、Openbox、RViz2、x11vnc 和 websockify/noVNC。
固定观测接口包括 `/driver/joint_states`、`/driver/state`、`/camera/color/image_raw`、
`/camera/depth/image_raw`、`/camera/camera_info`、`/camera/depth/color/points`、`/robot_description`、`/tf` 和 `/tf_static`。
点云与 RGB、深度、标定共用一次采集的时间戳，点云使用相机光学坐标系；不发布物体真值。
`observation.rviz` 预配置机器人、TF、网格、彩色点云和图像显示，可直接使用标准 RViz 功能调整视角或显示层。

启动脚本等待服务就绪后，显示实际映射的浏览器地址和连接密码，并在桌面环境中尝试自动打开浏览器。
默认地址为 `http://localhost:8080/vnc.html?autoconnect=1&resize=scale`，在页面输入终端显示的密码即可。
无桌面环境时手动打开该地址。密码不放在 URL 或 Agent 挂载中；启动终端输出包含密码。
原始 VNC 只监听容器内回环地址，宿主机仅映射 noVNC 到 `127.0.0.1`。网页只服务 noVNC 静态资源，不提供私有文件或评估 API。
默认 Xvfb 桌面为 1440×900，RViz 使用 Mesa/llvmpipe 软件渲染，无宿主机显示 socket、GPU 或 privileged 挂载。
Mac 使用 Docker Desktop，Linux 使用 Docker Engine 或 Docker Desktop；跨架构依赖由对应平台镜像安装，性能及 macOS 实测状态以验证记录为准。

关闭 RViz 窗口只会重新打开 RViz，不重启驱动。任何其他受管进程退出会停止整个容器，不自动重置仿真。
从旧观察服务升级时，先确认没有控制任务，再使用 `docker compose down --remove-orphans` 停止旧服务，
然后重新运行环境启动脚本；不要添加 `-v`，以保留工作区、认证、观测和浏览器密码。

### 配置与旧版迁移

`AGENTICROS_VIEWER_PORT` 设置 RViz2/noVNC 浏览器端口（默认 8080），`ROS_DOMAIN_ID` 设置双方一致的 ROS 域（默认 42）。
仿真只在动作期间推进。确认没有控制任务后，可显式重建场景：

```bash
BODY_SEED=3 docker compose up -d --build --force-recreate --wait simple_mujoco
```

重启生成新的 session ID，旧观测不能代表当前状态。
Compose 项目名、镜像与网络使用 `agenticros` 命名空间；当前持久卷为
`agenticros_codex-home`、`agenticros_observations` 和 `agenticros_viewer-auth`。
工作区现为 `runs/workspace/`，原 `agenticros_workspace` 卷只保留作迁移来源和备份，不自动删除。
原有数据不会自动出现在新项目中。升级已有安装时，先确认驱动空闲、停止所有控制程序，
再停止旧项目的容器，使用以下迁移入口（将 `OLD_PROJECT` 换成旧 Compose 项目名）：

```bash
docker compose --profile simulation build
python3 scripts/migrate_volumes.py --from-project OLD_PROJECT         # 查看复制计划
python3 scripts/migrate_volumes.py --from-project OLD_PROJECT --apply # 复制并校验
python3 scripts/prepare_workspace.py # 将已停止的旧工作区卷复制到 runs/workspace
bash scripts/environment.sh simple_mujoco
```

迁移脚本拒绝运行中或非空目标卷，逐卷核对文件内容、权限和所有者，保留原卷作为备份；
不导入宿主机认证。若旧数据来自不同机器或自定义卷名，需要先显式导入对应应用卷。
ROS 接口包改名后，旧适配器中的导入名需更新为 `agenticros_driver_interfaces` 并重新验证；
已有代码和会话会保留，但重启后的身体 session、观测和适配验证状态必须重新核对。
历史验证文档、日志和禁用源码指纹中的原始名称保留用于追溯，不代表当前运行配置。

`scripts/environment.sh` 是统一选择入口，具体启动逻辑位于 `docker/robot_instances/<名称>/start.sh`。
当前仅提供 `simple_mujoco`，未知或未实现的环境名称会报错，不启动容器。
设备登录不可用时，可在容器中通过 `codex login --with-api-key` 从 stdin 登录；不要把密钥写入仓库。

### Agent 启动、登录与模型切换

`bash scripts/agenticros.sh` 构建 Agent 镜像，启动容器内的 `agenticros start` 终端菜单：

```text
1. ChatGPT 账号
2. DeepSeek API
3. 其他云端 API（Responses 协议）
0. 退出
```

ChatGPT 方式输入模型名，复用有效账号认证或启动 `codex login --device-auth`，由用户在浏览器确认。
账号分支检查认证状态，不把它当作模型访问或任务验收。
API 方式输入 HTTPS Base URL、模型名和不回显的 API Key；DeepSeek 预填官方地址和 `deepseek-flash`。
其他 API 可包括 OpenAI API；只支持云端 Responses 协议，本菜单不提供本地部署选项。
账号和 API 两种方式都可输入推理强度，并随连接配置保存；已有配置可选 4 单独修改强度，不重填 Key。
GPT-6.1 Sol 可选 `low/medium/high/xhigh/max`；其他模型支持范围由提供方决定，菜单中的 `none` 不适用于所有模型。
API 校验请求使用所选强度；账号分支只检查登录，强度支持情况以实际模型请求为准。
单次临时覆盖可用 `bash scripts/agenticros.sh -c 'model_reasoning_effort="high"'`；这不会修改已保存的强度，
菜单校验仍使用保存或新选择的值。

API 校验使用两个小型 SSE 请求，检查鉴权、模型可用性、完整结束事件、函数工具调用参数和工具结果往返。
校验会产生 API 用量，不执行工具程序、不访问 ROS、不执行机器人动作。
HTTP 错误按鉴权、权限、地址或模型、余额与限流分类；不回显服务端错误正文，拒绝携带 Key 跟随重定向。
这属于基础 API 兼容性检查，不代表完整 Codex 工具、图像、自主适配或抓取验收。

校验通过后，连接配置和 Key 原子写入专用 Codex 卷中的 `agenticros-connection.json`，文件权限 0600。
失败不覆盖旧配置，Ctrl+C 或 EOF 退出；后续启动可回车复用配置并重新校验。
Key 不写入源码、公开 TOML 或命令行参数，启动 Codex 时仅通过进程环境提供。
配置保存在卷中，不会被 entrypoint 每次复制基础 `codex.toml` 的操作覆盖。
`codex` 包装器让后续 `codex exec` 和试验脚本使用同一连接；显式 `codex login/logout` 仍可独立使用。

退出交互后仅删除本次运行容器，保留工作区和 Codex 卷；入口不启动或重启仿真。
`bash scripts/agenticros.sh resume --last` 在选择连接后恢复会话；`docker-chat.sh` 为兼容入口。
需要单独账号登录时仍可运行 `bash scripts/docker-login.sh`。
`agenticros status` 只检查本地配置或账号状态，不发起付费 API 校验。

当前基础默认配置仍在 `docker/agent_instance/agent/codex.toml`，菜单选择通过包装器的运行参数覆盖模型与提供方。
模型能力目录（尤其图像能力）可按提供方官方说明配置；菜单不会因文字/函数测试通过就宣称支持视觉。
参见 [OpenAI 登录说明](https://learn.chatgpt.com/docs/auth?surface=cli)、
[提供方配置](https://learn.chatgpt.com/docs/config-file/config-advanced)、
[协议兼容要求](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility) 和
[DeepSeek Codex 接入说明](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/)。

### 断电或重启后恢复

Docker 启动后，在项目目录恢复已有仿真容器并等待健康检查：

```bash
docker compose up -d --no-build --no-recreate --wait simple_mujoco
docker compose ps
```

工作区保存在仓库的 `runs/workspace/`，Codex 认证、观测文件和 VNC 密码保存在应用数据卷中，不因普通停止而删除；
仿真物理状态不持久化，启动后场景重置且 session 改变。恢复 Agent 会话前先核对 session、重新观测，
不重放断电前未确认完成的动作。不要用 `down -v` 删除数据卷。

### 仓库中的生成产物

默认映射为 `runs/workspace/` → `/workspace`。生成的 `adapter/`、`interfaces/`、`evidence/`、
`ros_logs/` 会直接出现在宿主机，`runs/` 已被 Git 忽略，也不进入 Docker 构建上下文。
相机原始快照仍在 observations 卷；Codex 原始聊天会话、登录和模型 Key 仍在 codex-home 卷，
不会为导出代码而将凭据放入源码仓库。

启动入口先调用 `prepare_workspace.py`。仅当目标为空且没有运行中的旧卷使用者时，
才将旧工作区复制到临时目录，核对源/目标文件哈希和符号链接后替换空目标，保留原卷。
非空工作区直接复用，不自动覆盖或合并。旧 Agent 若仍运行，首次迁移会要求先退出；不停止仿真。
新增文件通过目录 setgid、宿主机附加组和 umask 0002 支持宿主机用户与非 root Agent 共同读写。
启动脚本自动设置宿主机组 ID；直接运行 Compose 时，自定义组用户需设置 `AGENTICROS_WORKSPACE_GID`。
`docker-login.sh` 也会准备工作区；全新检出时先构建 Agent 镜像，再迁移已有旧卷。

自主试验使用单独的 `runs/<trial>/workspace/`，保持空工作区验收，不与日常适配产物混用。

## 开发与验证历史

以下按开发阶段记录实现、验证范围和证据。旧容器名、日志路径、session 和当时的运行状态保留用于追溯，
不代表当前配置；此前的 Jazzy 和自写观察器记录也不代表当前 Humble/RViz2 版本已完成自主抓取验收。

### Docker 初版：2026-11-13，44cce15

环境：Apple Silicon、Docker Desktop 4.99.0 / Engine 29.9.3、ROS 2 Jazzy、MuJoCo 3.3.7、Codex CLI 0.168.0。
实际模型配置为 `gpt-6-astra / medium`，用户在容器中独立登录。

| 阶段 | 结果 |
| --- | --- |
| 构建与启动 | 两个 ARM64 镜像成功，Body 健康，Agent CLI/ROS/Python/C++ 可运行 |
| ROS 与观测 | 跨容器发现、同步 RGB-D、标定读取通过；Chrome 显示实时画面 |
| 底层控制 | 非法参数和旧 session 拒绝、并发拒绝、UUID 查询、取消与停止确认通过 |
| 隔离 | 导出镜像扫描 40,162 个文件、比对 15 个私有源码哈希；实际挂载及只读观测验证通过 |
| 重启 | session 改变、物理状态重置，新观测使用新 ID |
| 自主适配 | 空工作区起步，Codex 自行编写适配器；三轴小幅运动、空夹爪开合及 7 类非法输入通过 |
| 自主任务 | 经未夹起、滑落和自主修正后，红块放到红盘上，夹爪释放并退离 |
| 严格独立评估 | **未通过静止条件**；盘内、接触盘面、脱离夹爪条件通过 |

追加公开观测的稳定性复核后，Agent 自行发现缓慢旋转并尝试修正，最终静止条件仍未通过。
未提供真值给 Agent，也未修改评估阈值。用户接受当前效果后停止尝试；驱动空闲、停止确认、无需恢复。
只验证了单个场景，适配器全工作空间和故障注入未验证；不能称为稳定抓取系统或实机验证。

证据保存在宿主机（不进镜像）：

- `runs/docker-validation-20261113/`：构建、隔离和原始驱动日志。
- `runs/rosbody-trial-20261113-183345-fe97da/`：自主试验、初次与最终评估、最终工作区副本 `workspace-final/`。
- 试验容器：`rosbody-trial-20261113-183345-fe97da`，数据卷和认证均保留。

### Docker 分支精简

旧源码、pixi 和原生构建产物已移除，历史仍可通过 Git 取得。原有“31 项通过”包含旧实现测试，不代表现在的测试数量。
保留 4 项相关单测：3 项宿主机标准库布局检查，1 项临时 Body 容器中的评估器检查。
旧参考实现的禁用哈希保存在 `tests/forbidden_sources.json`，删除源码不会削弱对应的镜像扫描。

精简后验证：两个镜像构建成功，4 项单测、实际镜像审计和无运动 ROS 集成检查通过；仍扫描 40,162 个文件和 15 个禁用哈希。
评估器单测采用明确的静态状态验证判据，不再假设不同平台经过固定步数就必然落稳；运行时评估器未改。
正在运行的 Body 容器 ID、启动时间和 session 均未改变。没有重新做运动或自主任务试验。
本次日志：`runs/docker-cleanup-20261113/`。

### AgenticROS 命名与独立启动（2026-11-13）

服务与目录改名为 `agenticros`、`simple_mujoco`。环境显式启动；聊天入口在环境未运行时退出，
不再自动启动仿真。`real` 入口仅解释外部 ROS 接入要求并退出；未验证实机连接。
保留 Compose 项目名 `rosbody` 以复用已有卷、认证和网络，原始 IDL 包名也保持兼容。

验证：两个镜像构建成功；4 项布局/启动边界单测与 1 项评估器单测通过；
在独立项目 `agenticros-rename-check`、ROS 域 43、端口 8081 启动新服务，ARM64 跨容器 ROS、
观测只读、非法动作拒绝与 HTTP 隔离检查通过；没有运行有效动作或重新进行自主任务。
实际 Agent 镜像内容审计通过。无环境时聊天入口退出，真实环境入口没有启动容器。
原有 `rosbody-body-1` 启动时间未变；旧会话、卷和试验代码保留。
临时测试仿真已停止，测试卷保留。证据：`runs/agenticros-rename-20261113/`。

### Ubuntu 22.04 / Humble 迁移（2026-11-13，Linux AMD64）

两个镜像改用 `ros:humble-ros-base-jammy`，同步更新 ROS 包名、构建和启动时的环境路径。
Agent 的 SciPy 固定为支持 Python 3.10 的 1.15.3；MuJoCo 3.3.7 和 Codex CLI 0.168.0 保持原版本。
集成检查接受 AMD64 与 ARM64，不再限定 ARM64。本次只实测 AMD64，未重新验证 Humble ARM64。

本机 Docker Engine 29.9.3、Compose 5.7.1；容器实际为 Ubuntu 22.04.5、ROS 2 Humble、Python 3.10.12。
两个镜像构建成功，仿真健康、相机 HTTP 可访问；4 项布局单测与 1 项评估器单测通过。
跨容器 ROS、同步观测、只读观测挂载、非法参数和旧 session 拒绝、HTTP 隔离检查通过。
小幅运动、并发拒绝、UUID 回执、取消和停止确认通过；检查结束后驱动空闲。
实际 Agent 镜像审计通过，扫描 40,500 个文件和 15 个禁用源码哈希。

旧 Jazzy 仿真容器和两个项目镜像已按用户要求删除，卷及日志保留。
Humble 自主适配与抓取尚未验证，等待容器内 Codex 独立登录；以上检查不代表抓取任务成功。
本次证据：`runs/setup-20261113/`，包括构建、版本、单测、集成、运动和镜像审计日志。

### 独立 ROS 观察界面（2026-11-13，Linux AMD64）

`simple_mujoco` 发布关节状态、RGB、米制深度、CameraInfo、相机静态 TF 和公开驱动状态，
不再运行 HTTP 服务。独立 `visualizer` 服务订阅 ROS，由 `robot_state_publisher` 发布公开 URDF 与机器人 TF，
通过本机 8080 端口显示模型、坐标轴、相机和 RGB-D 点云。Three.js 0.180.0 与许可证随代码提供，无运行时 CDN 依赖。
观察服务不挂载源码、观测卷、认证或 Docker socket，不提供执行动作或评估器接口。

新驱动和观察镜像构建成功，两个服务健康；9 项单测通过（4 项布局、1 项评估器、4 项传感器解码/投影）。
实际跨容器订阅、URDF/关节/TF 对应、同步 RGB-D 重建 6,052 个点、话题消息预览、
私有路径与挂载隔离检查通过；浏览界面没有推进物理仿真。原 Agent 隔离和无运动 ROS 检查通过。
浏览器实际显示三维模型、点云、相机、TF 与话题面板，并验证俯视按钮。
本阶段未重新运行有效运动或自主抓取，不将此前的运动检查等同于新驱动运动验收。
切换前确认旧驱动空闲且停止，重启后 session 为 `f79e2e054c7d`；卷和日志保留。
证据：`runs/setup-20261113/observer-*.log` 和 `build-observer.log`。

### 统一 AgenticROS 命名与数据迁移（2026-11-14，Linux AMD64）

产品文字统一为 AgenticROS；Compose 项目、镜像、网络、数据卷、试验容器和审计容器使用 `agenticros` 前缀。
原始 ROS 接口包统一为 `agenticros_driver_interfaces`，同步修改 CMake、包清单、两侧构建路径、Python 导入和设备文档。
公开资料目录改为 `/opt/agenticros`，DDS 配置改为 `/etc/agenticros/fastdds.xml`，
Agent 环境脚本改为 `/etc/profile.d/agenticros.sh`，私有评估 socket 改为 `/tmp/agenticros-evaluator.sock`，
端口变量改为 `AGENTICROS_VIEWER_PORT`。固定 `/driver/*` 和相机话题名称、消息字段与控制语义保持原有定义。

移除旧服务名的启动兼容分支，增加显式应用卷迁移脚本。迁移前确认旧驱动空闲、停止已确认；
停止旧服务后复制 workspace、codex-home、observations 三个卷，逐项校验内容哈希、权限、所有者和符号链接。
三卷分别验证 15、13、18 个条目，原卷保留作备份。重复迁移被非空目标保护拒绝。
原始日志、禁用源码指纹及其说明标签未改写；镜像审计和隔离测试继续通过指纹表检查历史私有命名空间。

验证结果：三个新镜像构建成功，9 项单测通过；跨容器 ROS、新接口包加载、观测只读、
镜像与挂载隔离、三维观察、TF、同步 RGB-D 点云检查通过；评估入口可以访问新 socket。
Agent 镜像扫描 40,500 个文件、15 个私有源码指纹通过。
运行中的两个服务均健康，当前 session 为 `80742c9dfc30`，驱动空闲、无需恢复；实时 ROS 类型无旧包名。
旧容器、网络和镜像标签已清理，原应用数据卷及全部证据保留。
未执行有效运动或自主抓取；初始场景评估为未完成任务，不作为抓取验收结果。

本机证据：`runs/rename-agenticros-20261114/`，包含构建、卷迁移与保护检查、单测、ROS、观察器、
镜像审计、评估入口、旧资源清理和最终状态记录。

### 容器内 RViz2 / noVNC（2026-11-14，Linux AMD64）

删除独立 `visualizer` 服务及 Three.js 网页，将公开 URDF、robot_state_publisher、RViz2、
Xvfb、Openbox、x11vnc 与 websockify/noVNC 集成到 `simple_mujoco`。
驱动继续提供固定 ROS 接口，新增 `/camera/depth/color/points` 标准 PointCloud2；
点云从同次 RGB-D 采集重建，与图像及 CameraInfo 时间戳一致，不读取物体真值。
RViz 配置默认显示模型、TF、网格、RGB-D 点云和 RGB 相机，可启用深度图。

非 root、只读根文件系统、cap_drop ALL 和 Agent 的挂载隔离保留。
无宿主机显示 socket、GPU 或 privileged 依赖，使用 Xvfb + Mesa/llvmpipe 软件渲染。
VNC 只监听容器回环地址，浏览器服务只映射宿主机 127.0.0.1:8080；
自动生成的八字符随机密码保存在专用 viewer-auth 卷，Agent 不挂载该卷。
网页根目录仅包含 noVNC 静态资源，私有源码、密码、观测和评估路径的 HTTP 检查均返回 404。

实际包版本：RViz2 11.2.29、noVNC 1.0.0、x11vnc 0.9.16、Xvfb 21.1.4。
仿真与 Agent 镜像构建成功；11 项单测通过（4 项布局、1 项评估器、5 项传感器与点云、1 项密码持久性和权限）。
集成检查通过：RViz 节点存在、机器人模型与 TF 对应、同时间戳 RGB/深度/标定/6,052 点彩色点云、
VNC 强制认证、进程健康、跨容器 ROS、Agent 只读观测和镜像隔离。
Agent 镜像审计扫描 40,500 个文件及 19 个私有源码指纹通过。
小幅运动、并发拒绝、取消、UUID 回执和停止确认通过；没有运行自主抓取任务。

浏览器通过 noVNC 实际认证并操作 RViz2，画面显示 Global Status: Ok，模型、相机和点云正常，拖动旋转视角通过。
终止 RViz 后仅 RViz 重开，其余受管进程 PID、驱动 session 和仿真时间未变；不自动重启驱动。
当前 session 为 `e22a3f7afdce`，检查后驱动空闲且停止已确认，工作区、认证和观测卷保留。
本次只实测 Linux AMD64；macOS/ARM64 未实际验证，不将通用依赖和无宿主机显示绑定等同于跨平台验收通过。

证据：`runs/rviz-novnc-20261114/`，含构建、单测、ROS/RViz/noVNC 集成、GUI 重开、运动回归、
镜像审计、包版本和浏览器实际截图 `rviz-desktop.png`。

### 断电后恢复复查（2026-11-14，Linux AMD64）

断电后原仿真容器处于退出状态，源码、镜像、工作区、Codex 认证、观测和 viewer-auth 数据卷均保留。
使用 `docker compose up -d --no-build --no-recreate --wait simple_mujoco` 启动已有容器，健康检查通过。
重新运行 11 项单测、`check_rviz.py` 和 `check_docker.py`，均通过；恢复复查没有执行有效运动。
新 session 为 `4fa1b9d52698`，RViz、TF、同步 RGB-D 与 6,052 点点云、VNC 认证、Agent 挂载隔离正常。
README 明确以 Linux 实测结果为准，并补充断电恢复、数据持久化和无运动验证命令。
本次证据：`runs/power-recovery-20261114/`。本节不增加自主抓取或其他平台的验证结论。


### 2026-11-14：改为运行时发现环境接口

将接口包和身体说明移至 `docker/simple_mujoco/`，从 Agent 镜像和启动复制流程中移除。
仿真通过只读 ROS 参数 `device_documentation`、`interface_sources` 公开说明和接口包声明；
Agent 只保留通用发现、编译工具和适配要求，不预置驱动名称或消息绑定。
已有数据卷不清理，旧代码、说明和会话可能仍在；未知环境验收使用独立空工作区。

12 项单测通过。独立临时 Compose 项目中，验证无自定义绑定时发现 action、通过标准参数服务
取得定义、编译接口包、调用状态和快照，并通过原始请求拒绝、挂载隔离和 RViz 检查。
镜像文件系统审计通过，确认无预装设备定义与身体说明。
测试未执行有效运动，不代表 Codex 自主适配或抓取任务验收。
测试项目及其临时卷已清理，原运行仿真、会话与应用数据卷保持不动。
当前运行容器仍是旧版本；新参数发布功能需在确认无任务后更新仿真容器才能生效。


### 2026-11-14：容器内云端登录与连接校验

新增终端菜单，支持 ChatGPT 账号、DeepSeek 和其他云端 Responses API。
Key 隐藏输入，连接校验成功后保存到专用 Codex 卷；失败保留旧配置。
统一 Codex 包装器与试验入口的连接读取，保留独立登录命令兼容性。

通过 `scripts/test.sh` 的 22 项单测，以及 Agent 镜像构建与文件系统审计。
真实临时容器终端验证菜单显示、Key 不回显、无效地址拒绝和退出；未挂载现有认证卷。
API 流式与工具往返使用模拟响应验证，未使用真实云端 Key，未替用户完成账号授权。
当前运行的仿真容器未修改或重启，未执行机器人动作。


### 2026-11-14：工作区绑定到仓库产物目录

将日常 `/workspace` 改为绑定宿主机 `runs/workspace/`，自主试验绑定独立的 `runs/<trial>/workspace/`。
不挂载仓库根目录，认证、模型 Key 和原始会话缓存仍在 Codex 专用卷。
旧 Agent 退出后，复制并校验原 `agenticros_workspace` 的 961 个条目，保留原卷作为备份。
迁移调整的是新副本的宿主机所有者与共享组权限，不修改原卷。

28 项单测通过。实际临时 Agent 容器验证了非 root、限定目录绑定、宿主机与容器双向读写、
只读观测挂载，以及既有适配器内容未改变；Agent 镜像审计通过。
未运行模型、未启动仿真驱动、未执行机器人动作，临时检查容器与测试文件已清理。

### 2026-11-15：Codex 逐步调用 ROS 能力

将 Agent 规则、能力契约和适配技能改为中文，移除必须生成 `Body` 类的要求。
Codex 负责逐步决策与调用，按需生成可复用 ROS 功能节点；不以任务脚本代替会话中的判断。
工作区初始化改为 `ros_ws/src/`、`capabilities/` 和 `evidence/`，不自动启动生成节点或恢复动作。
试验入口在适配后检查能力清单、证据和在线 ROS 图，保留独立任务评估及逐步调用行为的人工审阅要求。

35 项单测通过；技能格式、Python/Shell 语法与差异检查通过。Agent 镜像构建成功，
镜像审计扫描 39,905 个文件、24 个禁用源码指纹通过。
无网络、无认证卷的临时 Agent 容器验证新目录及中文规则复制、后台测试节点持续运行、
实际话题消息读取、能力清单和在线节点检查。该节点仅为维护者测试夹具，未打入镜像，不是自主适配结果。
临时容器已清理，证据保存在 `runs/ros-capability-check-ndq4_ugz/`。

未调用模型、未执行机器人动作、未重启运行中的仿真；未验收 Codex 自主开发节点、逐步决策行为或抓放成功。
旧产物、认证和数据卷未删除，新建日常容器仍复用绑定工作区；空工作区自主验收另行运行。


### 2026-11-16：macOS / Linux instance-client 适配

保留 `bash scripts/agenticros.sh --instance <名称>` → `bash scripts/environment.sh simple_mujoco --join <连接码> --robot-id <名称>` 的启动顺序。
Mac 通过系统 `ipconfig` 选择 IPv4，启动前实测 Docker Desktop 双向 host networking；临时探针可重试，服务启动和动作不重试。
新 instance 默认 TCP，已有 Linux UDP 配置、身份和数据保持不变。Mac 加入旧 UDP instance 会在启动前明确报错。

TCP 使用 Fast DDS `wan_addr` 映射宿主地址，并自动分配各 ROS participant 的监听端口；同机数据使用 SHM，Compose 设置 `ipc: host`。
这会共享 Docker 宿主 IPC，适用于当前可信仿真部署，不构成容器间的安全隔离。
发现端口限定为 1024..32767，避免测试中高端口重连失败的配置；发现记录使用 BACKUP 模式缓存在容器 `/tmp/agenticros-discovery/`。
恢复发现记录不会恢复注册 epoch、控制租约或动作。Humble 的服务响应端点匹配等待从默认 100 ms 调整为 TCP 模式下的 3 秒，避免新查询的响应过早被丢弃。
对应机制见 [Fast DDS TCP](https://fast-dds.docs.eprosima.com/en/2.6.x/fastdds/transport/tcp/tcp.html)、
[发现记录恢复](https://fast-dds.docs.eprosima.com/en/2.6.x/fastdds/use_cases/wifi/discovery_server_use_case.html) 和
[Humble 服务响应实现](https://github.com/ros2/rmw_fastrtps/blob/humble/rmw_fastrtps_shared_cpp/src/rmw_response.cpp)。

本次在一台 Apple Silicon Mac、Docker Desktop 4.99.0 上完成：

- `bash scripts/test.sh`：95 项通过；Python 语法、`git diff --check` 通过。
- Mac 局域网 IPv4 + host IPC：完整双 client 集成通过，证据 `runs/instances/lan-test-31573afd/`。
- TCP 三个独立 IP / 私有 IPC：完整集成通过，证据 `runs/instances/lan-test-92dd906d/`；每个测试容器有 512 MiB 私有共享内存，跨容器不能使用 SHM。执行命令为 `python3 scripts/check_lan.py --transport tcp --separate-ips --result-timeout 180`。
- 原有 UDP 独立 IP 路径通过：`runs/instances/lan-test-5a545a83/`。两种协议均检查身份、快照哈希、并行动作、拒绝串用 session、取消回执、租约失效停止和重启后不自动恢复控制。
- viewer / VNC 认证、桌面重启不重启 registry、会话锁、client 会话替换通过：`runs/instances/viewer-test-12351571/`。
- 两个实际 Bash 入口依次启动通过：`runs/instances/mac-cli-55af9326/`；只进入登录菜单并退出，没有调用模型。
- Agent 镜像审计扫描 61,946 个文件和 25 个私有源码指纹通过；六处运行时代码副本与源码哈希一致。

最终汇总：`runs/mac-adaptation/result.json`。其他目录保留了排障过程中的失败配置，不能作为最终通过证据。
本次测试容器均已停止并移除，运行容器数为 0；保留工作区、数据卷和证据，未提交或 push。
没有使用第二台物理机器，因此尚未验收 Mac↔Linux / Mac↔Mac 的真实路由、防火墙及链路故障；本次也没有在独立 Linux 宿主重新运行。
Docker 集成不代表 Codex 自主适配、任务成功或硬件验证。Mac 软件渲染可能慢于实时，测试等待上限不修改动作时长或租约，也不重发超时动作。

### 2026-11-17：按 Agent / Robot instance 整理 Docker 目录

将 Agent、注册服务、Viewer 和进程管理统一归入 `docker/agent_instance/`；
机器人实现归入 `docker/robot_instances/`；`simple_mujoco/lan.py` 改名为 `registration.py` 并更新驱动导入。
同步修改 Dockerfile、Compose、启动脚本、镜像审计、测试与文档路径；保留构建目标、镜像标签、容器名、数据卷和配置格式。

验证：

- 两类 LAN 镜像和旧单机 Compose 镜像均在本机 macOS 成功构建。
- `bash scripts/test.sh`：95 项测试通过（88 项宿主机测试、7 项隔离容器测试）。
- 通用 Agent 与完整 Agent instance 镜像隔离审计通过；后者扫描 61,946 个文件，核对 25 个私有源码指纹。
- 新镜像双 Robot instance 的 Viewer 集成通过，包括注册发现、画面、可视化重启、会话锁与机器人 session 更新；无机器人运动指令。
  证据：`runs/instances/viewer-test-683907e0/viewer-check.json`。
- 测试容器均清理，最终运行容器数为 0；账号卷、观测卷与工作区保留。未做云端登录、Codex 自主任务或真实双机验收。
