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
