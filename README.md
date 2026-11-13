# AgenticROS

AgenticROS 是运行 Codex、ROS 2 Jazzy、Python 和编译工具的 Agent 容器。
环境独立启动，通过 ROS 2 提供身体控制和传感器接口。Mac 无需安装 ROS 或 pixi。

- **仿真环境 `simple_mujoco`**：独立 Docker 容器，运行 MuJoCo、原始关节驱动、RGB-D 观测和私有评估器。
- **真实环境**：Linux 主机上的硬件驱动与传感器节点，不需要本项目的仿真容器。目前只验证了 `simple_mujoco`；实机网络、身体资料和观测传输尚需适配。
- **AgenticROS**（Compose 服务 `agenticros`）：初始没有适配器或任务程序，由 Codex 根据公开资料自行实现。

## 启动

安装并启动 Docker Desktop，在项目目录依次运行：

```bash
bash scripts/environment.sh simple_mujoco # 1. 先启动环境
bash scripts/docker-login.sh              # 首次独立登录容器 Codex
bash scripts/docker-chat.sh               # 2. 再启动 AgenticROS
```

聊天脚本不会启动或重启环境。`environment.sh real` 只说明外部环境接入要求并退出，不启动容器或操作硬件。
在 Codex 中输入“适配一下你的身体”，完成后输入“把红色方块夹取到红色盘子上”。
[localhost:8080](http://localhost:8080) 显示相机画面，终端显示命令、输出和错误。
认证存入专用卷，不复制宿主机凭据；设备登录不可用时，可在自己的终端通过 `codex login --with-api-key` 的 stdin 登录。

## 配置与停止

编辑 [docker/agenticros/codex.toml](docker/agenticros/codex.toml)，默认 `gpt-6-astra`、`medium`。
配置启动时同步。命名卷持久保存工作区、适配器、会话、认证和观测。
Compose 项目名暂保留 `rosbody`，确保复用现有卷与网络；产品名和服务名已更新。

```bash
bash scripts/docker-chat.sh resume --last
docker compose stop simple_mujoco        # 停止仿真，保留数据
# 确认没有控制任务后，显式重建环境（会生成新 session ID）
BODY_SEED=3 docker compose up -d --build --force-recreate --wait simple_mujoco
```

退出 Codex 不会取消已接受的动作；交接前核对 UUID 和停止回执，一次只运行一个控制 Agent。
旧版 `body` 仿真若仍运行，启动脚本会保留并复用；切换服务名需要先停止控制，再手动 `docker stop rosbody-body-1`，然后重新启动环境。
不要使用 `down -v` 或删除卷。重启后的旧观测不能代表当前状态。
`ROSBODY_VIEWER_PORT` 设置端口，`ROS_DOMAIN_ID` 设置双方一致的 ROS 域（默认 42）。
仿真只在动作期间推进。

## 开发与验证

- `docker/simple_mujoco/`：私有仿真、原始驱动、评估器。
- `docker/agenticros/`：公开身体资料、接口契约、setup-body skill 和 Codex 配置。
- `docker/interfaces/`：原始 ROS IDL；保留现有 `rosbody_driver_interfaces` 包名以兼容生成的适配器。
- `scripts/`、`tests/`：启动和验收工具，无预写适配答案。

```bash
docker compose --profile simulation build
bash scripts/test.sh                      # 单测，不启动驱动
python3 scripts/audit_agent_image.py       # 镜像内容审计
python3 scripts/check_docker.py            # 已启动环境的 ROS、观测和隔离验证
python3 scripts/check_docker.py --motion   # 小幅动作/取消测试，仅在没有任务时运行
python3 scripts/docker-trial.py            # 新工作区的自主适配与任务试验
bash scripts/docker-evaluate.sh           # 仿真容器内独立评估
```

自主试验保留容器、工作区和 `runs/` 证据；原有试验容器和日志不因重命名而改动。
单测、集成、自主适配和任务评估分别验收，已有结果与限制见[验证记录](docs/docker-validation.md)。
旧原生实现仅保留在 Git 历史和归档分支。

## 隔离边界

AgenticROS 不挂载项目、宿主机 home 或 Docker socket，只获得公开资料、原始接口、只读观测与自己的工作区。
评估器只监听仿真容器内部 Unix socket，不通过 ROS、HTTP 或观测卷提供真值。
两侧非 root、移除 capabilities；Codex 使用 `danger-full-access`，因为嵌套 bwrap 不可用。
DDS 未认证且 Agent 有出站网络；这是文件和进程隔离，不是防御所有恶意行为的沙箱。
