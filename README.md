# AgenticROS

让 Codex 通过 ROS 2 发现设备能力、自行编写适配器，并执行文本任务。
当前提供 `simple_mujoco` 仿真环境，内置 ROS 驱动、RGB-D 观测和浏览器中的 RViz2。

环境为 **Ubuntu 22.04 + ROS 2 Humble**，已在 **Linux AMD64** 验证。
宿主机只需 Docker（含 Compose）和 Python 3，无需安装 ROS。
Agent 不预装仿真专用消息或身体说明，通过 ROS 2 运行时发现环境，获取公开定义、自行编译接口并编写控制代码。

具体环境位于 `docker/environments/<名称>/`，各自包含镜像、驱动、接口和启动脚本。
`environment.sh <名称>` 是统一入口，当前提供 `simple_mujoco`。

## 快速开始

```bash
bash scripts/environment.sh simple_mujoco # 启动仿真与 RViz2
bash scripts/docker-login.sh              # 首次登录容器内 Codex
bash scripts/docker-chat.sh               # 进入 Agent
```

依次下达指令：

```text
适配一下你的身体
把红色方块夹取到红色盘子上
```

启动脚本会显示 RViz2 的浏览器地址和连接密码，并尝试自动打开浏览器，输入显示的密码即可连接。
没有桌面环境时，手动打开脚本输出的地址；默认端口为 `8080`。

## 常用操作

```bash
bash scripts/docker-chat.sh resume --last # 继续会话
bash scripts/environment.sh simple_mujoco # 停机或断电后恢复环境
# 确认控制任务结束后停止环境：
docker compose stop simple_mujoco
```

模型配置见 [codex.toml](docker/agenticros/codex.toml)，默认 `gpt-6-astra / medium`。
工作区、认证、观测和 VNC 密码由数据卷保留；仿真重启会重置场景并更换 session，继续任务前须重新核对与观测。
一次只运行一个控制 Agent，不要使用 `down -v` 删除数据卷。

[开发记录](docs/development-record.md)包含架构、配置、迁移、测试命令和验证历史。
当前仅验证仿真环境，Humble 版本尚未完成自主抓取验收。
