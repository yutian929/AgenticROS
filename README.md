# AgenticROS

通过 ROS 2 连接 Agent、Viewer 和多台机器人。各自运行在独立 Docker 容器中，使用原生 DDS 自动发现，统一 `ROS_DOMAIN_ID` 即可选择通信域，无需配置 Server IP。

当前提供 `aloha_tabletop` 和 `cartesian_gripper` 仿真环境，尚未完成真实硬件和跨物理机器的部署验证。

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

使用 SAM3 需要额外配置支持 GPU 的 Docker NVIDIA Container Toolkit，模型默认从 [ModelScope facebook/sam3](https://modelscope.cn/models/facebook/sam3) 下载原始 `sam3.pt`。普通 Agent、Viewer 和机器人启动不需要这些依赖。模型推理使用独立 Python 3.12 环境，ROS 节点仍使用 Humble；不在宿主机安装 ROS 或模型依赖。

首次配置使用专用脚本；默认下载模型、构建镜像并检查环境，不启动服务：

```bash
bash scripts/prepare_sam3.sh --check-only
# 默认从 ModelScope 下载，不需要 Hugging Face Token。
bash scripts/prepare_sam3.sh --domain-id 42
```

脚本下载到专用 models 目录，支持同一模型版本的断点续传，并按 ModelScope 提供的固定文件版本、文件大小和 SHA256 校验后才启用权重。无需安装 ModelScope SDK 或输入 Hugging Face Token。已有权重不会覆盖。构建后会在临时容器中检查 CUDA。`--check-only` 仅初始化目录并报告缺失项。Python 下载实现为内部辅助脚本，不需要另一个日常入口。

脚本自动检查 NVIDIA Docker runtime。若缺少或未配置 NVIDIA Container Toolkit，会先说明宿主安装与 Docker 重启的影响，再询问是否安装配置（`[y/N]`）；输入 `y` 或 `yes` 才继续，拒绝、直接回车或非交互运行均退出。无需额外安装参数，已有可用 runtime 时直接继续模型准备。

自动安装仅支持本地 Debian/Ubuntu 的 rootful Docker，遵循 [NVIDIA 官方说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)，可能需要 sudo。安装前和 Docker 重启前都会检查运行中的容器；发现容器就退出，要求先明确停止并核对其任务。它不会安装完整 CUDA Toolkit 或替换 GPU 驱动。下载和使用仍遵循模型仓库的许可要求。

也可以将已有的授权 `sam3.pt` 手动放到 `runs/42/agent_instance/sam3/models/sam3.pt`。准备完成后启动：

```bash
bash agent.sh sam3 --domain-id 42
python3 scripts/runtime.py sam3-status --domain-id 42
python3 scripts/runtime.py stop-sam3 --domain-id 42
```

首次启动构建镜像，随后在容器启动时加载本地权重并完成图像、文本推理预热，健康检查确认 `READY` 才返回。模型缺失、GPU 不可用或加载失败会明确报错，不在第一次请求时加载。模型目录只读挂载，不挂载宿主凭据，不自动下载受限权重。加载状态可通过 `/perception/sam3/status` 查询，图像是否可用在具体请求中独立检查。

再次启动复用已有容器，不自动替换。更新实现后先停止 SAM3、重建镜像，再显式重建已停止的容器；其他容器和记录保持不变：

```bash
python3 scripts/runtime.py stop-sam3 --domain-id 42
docker compose -f compose.agent.yaml build sam3
python3 scripts/runtime.py sam3 --domain-id 42 --recreate
```

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
