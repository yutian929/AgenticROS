# AgenticROS

通过 ROS 2 连接 Agent、Viewer 和多台机器人。各自运行在独立 Docker 容器中，使用原生 DDS 自动发现，统一 `ROS_DOMAIN_ID` 即可选择通信域，无需配置 Server IP。

当前提供 `aloha_tabletop` 和 `cartesian_gripper` 仿真环境，尚未完成真实硬件和跨物理机器的部署验证。

另有需要 NVIDIA GPU 的 `quanta_x1`：独立 Isaac Sim 加载 Quanta X1 **整机与 G 夹爪**原生模型和简单桌面，不安装 X2Real 场景库。运行时与约 379 MB 整机资产全部包含在机器人镜像中，无需 prepare 脚本或宿主机资产目录。直接执行 `bash robot.sh quanta_x1`；已有本地镜像直接启动，缺少时从 Dockerfile 构建。首次构建较大，也可使用预先拉取并标记为 `agenticros-quanta_x1:single` 的完整镜像。安装与验证范围见[型号说明](docker/robot_instances/quanta_x1/README.md)。

## 启动

需要 Docker、Docker Compose 和 Python 3。跨机器部署使用 Linux host 网络，局域网须允许组播及 DDS 双向 UDP；仅设置相同域不能绕过网络隔离。

在仓库根目录执行，交互式命令分别使用不同终端：

```bash
# Agent：启动后进入 Codex
bash agent.sh agent

# Viewer：独立启动
bash agent.sh viewer

# Robot：启动后进入容器 Bash，已加载 ROS 环境
bash robot.sh aloha_tabletop
# 或 bash robot.sh cartesian_gripper
```

所有启动命令默认使用域 `42`，可追加 `--domain-id 43`。无需 `.env`，首次缺少镜像时自动构建。

### 再次执行会怎样？

| 命令 | 首次执行 | 再次执行（同一个域） |
| --- | --- | --- |
| `bash agent.sh agent` | 创建 Agent 容器并进入 Codex | 复用已有容器，再进入 Codex |
| `bash agent.sh viewer` | 创建 Viewer，打印地址和密码 | 复用已有容器，再打印地址和密码 |
| `bash robot.sh <型号>` | 创建机器人并进入 Bash | **创建另一台机器人**，生成新的短 ID |

Agent、Viewer 已停止时，再次启动会恢复已有容器；工作区和数据卷保留。已有镜像不会自动重建。换一个域则使用该域自己的实例和数据。

机器人在终端输出完整 ID：`<型号>_<8位随机后缀>`，例如 `aloha_tabletop_a1b2c3d4`，话题位于 `/robot/<ID>/...`。同型号可以同时启动多个实例。

在机器人 Shell 中按 **Ctrl+D** 或输入 `exit` 只退出 Shell，容器和机器人继续运行。退出 Agent 终端也不会停止容器。非交互调用 `robot.sh` 时只启动容器，不打开 Shell。

Viewer 是通过 noVNC 访问的普通 RViz。顶部只显示发现的机器人；机器人上线或离线不会切换、重配或重启 RViz。请在 RViz 的 Add 和 Fixed Frame 中自行选择话题与坐标系。机器人图像和点云使用 Reliable QoS，兼容 RViz 默认订阅；彩色图请选择 `camera/color/image_raw`。

域 42 的 Viewer 地址为 [http://127.0.0.1:8080/vnc.html](http://127.0.0.1:8080/vnc.html)，其他域的网页端口为 `8038 + 域编号`。启动成功后，终端会直接显示访问地址和密码；再次执行 `bash agent.sh viewer` 也可查看。

## 可选 SAM3 感知基础设施

SAM3 与 Agent、Viewer 并列，代码位于 `docker/agent_instance/sam3/`，独立启动，不依赖 Agent 或机器人容器的生命周期。默认启动 Agent 不会启动 SAM3。

使用 SAM3 需要额外配置支持 GPU 的 Docker NVIDIA Container Toolkit，模型默认从 [ModelScope facebook/sam3](https://modelscope.cn/models/facebook/sam3) 下载原始 `sam3.pt`。普通 Agent、Viewer 和 CPU 仿真机器人不需要这些 GPU 依赖；Quanta X1 同样需要。模型推理使用独立 Python 3.12 环境，ROS 节点仍使用 Humble；不在宿主机安装 ROS 或模型依赖。

直接启动，无需单独 prepare/setup：

```bash
bash agent.sh sam3 --domain-id 42
python3 scripts/runtime.py sam3-status --domain-id 42
python3 scripts/runtime.py stop-sam3 --domain-id 42
```

首次启动在本地没有镜像时自动构建。Docker 构建阶段从 ModelScope 下载固定版本的 `sam3.pt`（约 3.45 GB），校验固定的大小和 SHA256，再将权重复制进最终镜像 `/models/sam3.pt`。下载缓存留在 BuildKit 中，断点续传和重建可复用；运行时不下载模型。容器启动时加载权重并完成图像、文本推理预热，健康检查确认 `READY` 后返回。

宿主机仍需提前安装 NVIDIA 驱动和 NVIDIA Container Toolkit；启动器不会安装系统软件或重启 Docker。权重版本、大小及校验值见 `docker/agent_instance/sam3/checkpoint.json`；下载和使用遵循模型提供方条款。

SAM3 仅挂载独立的 `records` 目录保存推理记录，不再挂载宿主 `models` 目录。旧 `runs/<DOMAIN_ID>/agent_instance/sam3/models/` 会原样保留，但新版不再使用。`clean-runtime --yes` 会删除记录、工作区等运行数据，保留镜像中的权重；加 `--images` 后才需要重新构建，已有构建缓存可复用。

已有旧镜像不会自动升级。迁移时先确认没有正在执行的推理任务，然后显式停止、重建并替换 SAM3；Agent 和机器人无需重启：

```bash
python3 scripts/runtime.py stop-sam3 --domain-id 42
docker compose -f compose.agent.yaml build sam3
bash agent.sh sam3 --domain-id 42 --recreate
```

再次启动复用已有容器，不会自动替换正在运行的服务。

SAM3 使用 UDP 传输和原生 DDS 自动发现，以兼容宿主网络下的独立 IPC；不依赖跨容器共享内存。容器 `healthy` 仅证明内部模型就绪，Agent 到 SAM3 的通信还需从 Agent 侧验证。

ROS 接口为 `/perception/sam3/segment` Action、`/perception/sam3/results` Topic 和 `/perception/sam3/status` Service，使用同一个 `ROS_DOMAIN_ID` 和原生 DDS 发现。Agent 的 [perception skill](docker/agent_instance/agent/skills/perception/SKILL.md) 提供完整调用示例；下一次进入 Agent 时沿用现有规则同步机制。启动器仅将公开接口包复制到专用 Agent 工作区，Agent 按 skill 构建绑定，不会装入 SAM3 实现或模型。接口包已有本地改动时拒绝覆盖。

每次处理保存到 `runs/<DOMAIN_ID>/agent_instance/sam3/records/<service_session>/<goal_uuid>/`：请求、实际输入 PNG、图像时间戳与坐标系、模型代码版本和权重 SHA256、mask、叠加图、结果及事件日志。失败或取消也保存已有证据；被拒绝的请求使用 `rejected-<uuid>` 记录编号，因为 ROS 的请求验证回调不提供 Goal UUID。记录不自动删除。突然断电或强制杀进程可能只留下未完成日志，不代表成功，也不会自动重放请求。

第一版为单帧文本分割，最多四个待处理请求，GPU 推理串行；支持最新收到帧和精确时间戳等待，不提供历史帧检索或跨帧追踪。取消执行中的推理需等待 GPU 工作结束；等待超时后按原 Goal UUID 核对，不自动重发。SAM3 不负责三维抓取定位或任务评估。

独立容器协议测试使用合成后端，不需要 GPU 或权重，也不接触在线机器人：

```bash
docker build --target transport -f docker/agent_instance/sam3/Dockerfile -t agenticros-sam3:transport-test .
python3 tests/check_sam3.py
# 复现生产拓扑：宿主网络、SAM3 private IPC、Agent host IPC；使用空闲测试域
python3 tests/check_sam3.py --host-network --agent-image agenticros-server:single --domain-id 73
```

该测试覆盖通用 ROS 客户端、720p 图像与 mask 传输、取消、超时核对和记录落盘；它不证明真实 SAM3 的检测质量、自主适配、任务成功或跨物理主机 LAN 行为。

## 可选 Valen 4B 视觉决策

Valen 与 Agent、Viewer、SAM3 独立，默认不启动。使用公开 Apache-2.0 权重
[`Valen-Team/Valen-Decision-1.5-4B`](https://huggingface.co/Valen-Team/Valen-Decision-1.5-4B)，
固定 revision `9b0cacc976d7fb72f252ee1adb1564f5f15e462c`。模型、推理依赖与实现不装入 Agent，
Agent 只取得公开接口和规则。需要已配置的 NVIDIA Docker runtime，不在宿主机安装模型依赖。

```bash
# 首次缺少镜像时自动构建；启动独立服务，已运行则复用
bash agent.sh valen --domain-id 42
python3 scripts/runtime.py valen-status --domain-id 42
python3 scripts/runtime.py stop-valen --domain-id 42
```

与 SAM3 相同，Docker 构建时下载约 9.1 GB 的固定版本模型，验证权重 SHA256 和小文件 Git blob 校验值，
再打包到最终镜像 `/models/Valen-Decision-1.5-4B`。完整文件清单和校验值固定在
`docker/agent_instance/valen/model.json`；构建不查询浮动版本或动态生成校验基准。
BuildKit 按版本保留下载缓存与断点，重建可复用；已有文件不同则保留并报错。
容器运行时再次校验镜像固定清单，禁止在线下载，完成图像推理预热后才就绪。
BF16 不可用的 GPU 使用 FP16 骨干，训练好的决策头保持 FP32。显存、驱动和延迟仍需在部署机器实测，
不要在未核对资源的在线仿真旁运行 GPU 压力测试。

ROS 接口为 `/decision/valen/decide` Action 和 `/decision/valen/status` Service，
使用同域 DDS 自动发现。启动器只预置 `agenticros_decision_interfaces` 公开接口包，
构建与调用见 Agent 的 [perception skill](docker/agent_instance/agent/skills/perception/SKILL.md)。
服务接受 JSON 状态、Choice/Noul/Score 问题和一个公开 RGB 图像话题；返回原候选内的答案与概率，
不执行运动。选帧、推理前后检查时间，过期结果失败；客户端运动前仍须复核新鲜度与机器人 session。

Agent 可按子任务生成有限步数的局部执行节点：定义工具坐标、候选动作、预算和结束条件，
每轮执行一个小动作、确认停止、重新观察；预算耗尽、模型建议结束或出现异常后交还 Agent。
规则已说明该流程，但模型服务本身不包含机器人适配器、现成任务程序或自动运动循环。

每次请求的证据保留在 `runs/<域>/agent_instance/valen/records/<服务session>/<Goal UUID>/`。
模型属于镜像，只挂载可写记录目录；不挂载源仓库、宿主 home、凭据或 Docker socket。
启动和构建不会改动旧 `runs/<域>/agent_instance/valen/models/`，新版不再依赖它；清理运行数据仍会删除该旧目录。
普通 `clean-runtime.sh --yes` 删除运行数据但保留镜像中的模型；只有加 `--images` 才会移除项目镜像。
BuildKit 缓存不受该脚本清理；若镜像和构建缓存都被额外删除，则需要重新下载模型。
更新服务先显式停止，再重建镜像，最后 `python3 scripts/runtime.py valen --domain-id 42 --recreate`；
拒绝重建运行中的容器，模型与记录保留。

隔离通信测试不使用真实模型、GPU或在线机器人：

```bash
docker build --target transport -f docker/agent_instance/valen/Dockerfile -t agenticros-valen:transport-test .
python3 tests/check_valen.py --agent-image agenticros-server:single
```

它验证独立容器 DDS、720p 图像、取消、超时核对、过期结果和记录落盘，不证明真实模型质量、
局部控制收敛、抓取成功或跨物理主机 LAN 行为。

打包校验可在无 GPU、无网络、无宿主模型挂载的临时容器中运行：

```bash
# 微型合成权重：验证真实 weights 构建流程和离线缓存复用，不下载真实模型
python3 tests/check_valen_bundle.py --fixture
# 完整镜像构建后：核对真实模型全部文件、版本和校验值，不执行推理
python3 tests/check_valen_bundle.py --image agenticros-valen:single
```

## 重新进入机器人容器

使用启动时输出的完整 ID。忘记 ID 时，查看对应域、型号的目录，子目录名就是 ID：

```bash
ls runs/42/robot_instance/aloha_tabletop/
```

将下面的 `<ID>` 替换为实际 ID：

```bash
python3 scripts/runtime.py robot --robot-id <ID> --domain-id 42 --shell
```

这个命令复用已有机器人；若容器已停止，会先启动再进入。不会生成新 ID，Shell 已加载 ROS 环境。进入后可执行：

```bash
ros2 topic list
ros2 node list
exit
```

**不要用 `bash robot.sh <型号>` 重新进入旧机器人，它会创建新实例。**

## 状态、停止与清理

管理命令默认使用域 42；其他域追加 `--domain-id <域编号>`。

```bash
# 查看当前域的机器人状态
python3 scripts/runtime.py status

# 停止指定机器人、Viewer 或 Agent，保留数据
python3 scripts/runtime.py robot --robot-id <ID> --stop
python3 scripts/runtime.py stop-viewer
python3 scripts/runtime.py stop-server
```

清理先预览，再确认执行；会删除本项目的容器、数据卷（含登录数据）和 `runs/`，请先保存需要的数据：

```bash
bash scripts/clean-runtime.sh
bash scripts/clean-runtime.sh --yes
```

## 目录与数据

```text
agent.sh / robot.sh       # 日常启动入口
compose.agent.yaml       # Agent、Viewer 容器配置
compose.robot.yaml       # 通用机器人容器配置
docker/
  agent_instance/
    agent/
    viewer/
  robot_instances/
    aloha_tabletop/
    cartesian_gripper/
    common/              # 最小公共身份逻辑
    robot_docs/          # 按型号分类的公开手册、接口定义
    _template/           # 新机器人模板
scripts/                 # 运行管理与清理
tests/                   # 单元测试、隔离容器验证
tools/                   # 审计、评估、迁移等维护工具
runs/<DOMAIN_ID>/
  agent_instance/
    agent/workspace/     # Agent 生成的代码和资料
    viewer/
  robot_instance/<型号>/<ID>/
```

运行目录、容器项目和数据卷按域隔离。`robot_docs/` 只读挂载到 Agent 的 `/workspace/robot_docs/`；机器人实现与评测代码不挂载给 Agent。

## 更多

- 查看可用型号：`bash robot.sh --list`。
- 接入新型号：复制[最小机器人模板](docker/robot_instances/_template/README.md)，启动器自动发现。
- 运行测试：`bash tests/test.sh`；隔离启动验证：`python3 tests/check_startup.py`。
- 详细配置、镜像更新与迁移见[使用说明](docs/usage.md)，历史验证见[开发记录](docs/development-record.md)。
