# 开发记录

当前实现使用 Ubuntu 22.04 / ROS 2 Humble，已在 Linux AMD64 验证。
仿真、ROS 观测发布及 RViz2/noVNC 均位于 `simple_mujoco` 容器，Agent 使用独立的 `agenticros` 容器。

## 当前开发说明

- `docker/simple_mujoco/`：私有仿真、原始驱动、RGB-D 点云、评估器，以及公开 URDF、RViz 配置和容器图形进程管理。
- `docker/agenticros/`：通用 ROS 工具、适配要求、setup-body skill 和 Codex 配置，不含环境专用接口或身体资料。
- `docker/simple_mujoco/interfaces/`：归仿真端所有的原始 ROS IDL，包名保留为 `agenticros_driver_interfaces`。
- `docker/simple_mujoco/body.md`：由仿真端通过 ROS 参数公开的设备说明，不复制进 Agent 镜像。
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

### 隔离边界

AgenticROS 不挂载项目、宿主机 home 或 Docker socket，只预装通用 ROS 工具和适配要求，挂载只读观测与自己的工作区。
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
Compose 项目名、镜像与网络使用 `agenticros` 命名空间；持久卷为
`agenticros_workspace`、`agenticros_codex-home`、`agenticros_observations` 和 `agenticros_viewer-auth`。
原有数据不会自动出现在新项目中。升级已有安装时，先确认驱动空闲、停止所有控制程序，
再停止旧项目的容器，使用以下迁移入口（将 `OLD_PROJECT` 换成旧 Compose 项目名）：

```bash
docker compose --profile simulation build
python3 scripts/migrate_volumes.py --from-project OLD_PROJECT         # 查看复制计划
python3 scripts/migrate_volumes.py --from-project OLD_PROJECT --apply # 复制并校验
bash scripts/environment.sh simple_mujoco
```

迁移脚本拒绝运行中或非空目标卷，逐卷核对文件内容、权限和所有者，保留原卷作为备份；
不导入宿主机认证。若旧数据来自不同机器或自定义卷名，需要先显式导入对应应用卷。
ROS 接口包改名后，旧适配器中的导入名需更新为 `agenticros_driver_interfaces` 并重新验证；
已有代码和会话会保留，但重启后的身体 session、观测和适配验证状态必须重新核对。
历史验证文档、日志和禁用源码指纹中的原始名称保留用于追溯，不代表当前运行配置。

`environment.sh real` 仅说明外部 ROS 接入要求并退出，不启动容器或操作硬件。
设备登录不可用时，可在容器中通过 `codex login --with-api-key` 从 stdin 登录；不要把密钥写入仓库。

### 断电或重启后恢复

Docker 启动后，在项目目录恢复已有仿真容器并等待健康检查：

```bash
docker compose up -d --no-build --no-recreate --wait simple_mujoco
docker compose ps
```

工作区、Codex 认证、观测文件和 VNC 密码保存在应用数据卷中，不因普通停止而删除；
仿真物理状态不持久化，启动后场景重置且 session 改变。恢复 Agent 会话前先核对 session、重新观测，
不重放断电前未确认完成的动作。不要用 `down -v` 删除数据卷。

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
