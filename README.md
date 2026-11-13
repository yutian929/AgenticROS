# AgenticROS

让 Codex 通过 ROS 2 发现身体能力、自行实现适配，并执行用户的文本任务。
Agent 容器包含 Codex、ROS 2 和开发工具，不预装适配器或任务程序。

环境独立于 AgenticROS：当前提供 **`simple_mujoco`** 仿真容器，包含 MuJoCo、原始 ROS 驱动和 RGB-D 观测。
未来接实机时，由外部硬件驱动提供 ROS 接口；当前尚未验证实机接入。

容器环境统一使用 **Ubuntu 22.04 Jammy + ROS 2 Humble**，支持 AMD64 和 ARM64。
仿真与 Agent 均在容器中运行；宿主机只需要 Docker Engine（含 Compose）或 Docker Desktop，以及 Python 3。

## 快速开始

安装并启动 Docker Engine（含 Compose）或 Docker Desktop，在项目目录依次运行，无需本机 ROS：

```bash
bash scripts/environment.sh simple_mujoco # 先启动仿真环境
bash scripts/docker-login.sh              # 首次登录容器内 Codex
bash scripts/docker-chat.sh               # 再进入 AgenticROS
```

依次给 Codex 下达指令：

```text
适配一下你的身体
把红色方块夹取到红色盘子上
```

在 [localhost:8080](http://localhost:8080) 观察仿真；终端显示命令、输出和错误。
启动 AgenticROS 不会自动启动或重置环境。

观察界面由独立的 `visualizer` 服务提供，通过 ROS 订阅关节状态、TF、机器人模型和 RGB-D 话题，
显示可旋转的三维模型、彩色点云、相机画面及话题消息。点云从同步相机观测重建，不读取物体真值。
默认连接 `simple_mujoco` 的固定接口；观察服务不提供运动控制，也不挂载仿真源码或观测文件卷。

## 配置与日常使用

模型配置：[docker/agenticros/codex.toml](docker/agenticros/codex.toml)，默认 `gpt-6-astra`、`medium`。
代码、适配结果、会话和认证持久保存在 Docker 卷中，不复制宿主机凭据。

```bash
bash scripts/docker-chat.sh resume --last # 继续会话
docker compose stop simple_mujoco visualizer # 停止仿真与观察界面，保留数据
```

一次只运行一个控制 Agent。退出 Codex 不会取消已接受的动作，交接前应确认停止。
不要使用 `down -v` 删除数据；仿真重启后需重新核对 session 和观测。

[开发、隔离边界与旧版迁移](docs/development.md) · [实际验证结果与限制](docs/docker-validation.md)
