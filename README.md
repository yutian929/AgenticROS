# AgenticROS

让 Codex 通过 ROS 2 发现机器人、读取观测并调用能力。提供 `cartesian_gripper` 单夹爪测试台和 `aloha_tabletop` 双臂桌面环境，可在浏览器中查看画面。

支持 **Linux 和 macOS**，宿主机只需 **Docker（含 Compose）和 Python 3**，无需安装 ROS。

## 主机与实例

| 层级 | Server 端 | Client 端 |
| --- | --- | --- |
| 主机（电脑） | **Server 主机**：运行 Agent instance | **Client 主机**：运行 Robot instance |
| 实例（当前为容器） | **Agent instance**：注册与发现服务 + Agent + Viewer | **Robot instance**：仿真或真实机器人接口 + 驱动 + 观测 |

当前实现的是仿真 Robot instance，真实机器人接口尚未支持。

```mermaid
flowchart TB
    subgraph SERVER["Server 主机"]
        IA["Agent instance: account-a<br/>注册与发现服务 + Agent + Viewer"]
        IB["Agent instance: account-b<br/>注册与发现服务 + Agent + Viewer"]
    end
    subgraph CLIENT1["Client 主机 1"]
        A1["Robot instance: robot_a<br/>仿真 + 驱动 + 观测"]
        A2["Robot instance: robot_b<br/>仿真 + 驱动 + 观测"]
    end
    subgraph CLIENT2["Client 主机 2"]
        B1["Robot instance: robot_c<br/>仿真 + 驱动 + 观测"]
        B2["Robot instance: robot_d<br/>仿真 + 驱动 + 观测"]
    end
    IA -->|管理| A1
    IA -->|管理| A2
    IB -->|管理| B1
    IB -->|管理| B2
```

一台主机可以运行多个实例，一个 Agent instance 可以管理多个 Robot instance。
**Server 和 Client 是主机角色，可以由同一台电脑承担**；不同电脑需在可互通的局域网内。图中只是部署示例，Robot instance 可加入任一 Agent instance。

首次使用只需一个 Agent instance 和一个 Robot instance。

## 快速开始

**先启动 Agent instance → 获取连接码 → 启动 Robot instance → 在 Codex 中下达指令。**

每台参与的电脑准备相同版本的本仓库，启动 Docker，以下命令均在仓库根目录执行。首次启动会构建缺少的镜像，需要联网并等待完成。

**macOS 额外设置一次：**使用 Docker Desktop 4.34+，在 **Settings → Resources → Network** 开启 **Enable host networking**，然后 **Apply & restart**。该功能不能与 Enhanced Container Isolation 同时使用。

### 1. 启动 Agent instance

在 Server 主机上执行：

```bash
bash scripts/agenticros.sh --instance account-a
```

按终端菜单登录 ChatGPT 账号或配置 API。脚本会打印 **join码、Viewer 地址和访问密码**。

### 2. 启动 Robot instance

在 Client 主机上（同机部署时另开终端），执行下面的命令，将 `CONNECTION_CODE` 替换为上一步输出的 join码：

```bash
bash scripts/environment.sh cartesian_gripper --join CONNECTION_CODE --robot-id robot_a
```

将 `CONNECTION_CODE` 替换为完整连接码。连接码已包含 Server 主机地址，**不用再传 IP**；只交给需要加入的 Client 主机操作者。

需要第二个 Robot instance 时，使用相同连接码，把 `robot_a` 改为 `robot_b` 即可。

### 3. 查看画面并开始使用

打开 Agent instance 输出的 Viewer 地址，输入密码，在顶部选择机器人查看 RViz2 画面。默认在 Server 主机浏览器打开。点击 **ROS interfaces** 可查看 Topics、Services 和 Actions。

在 Codex 会话中输入：

```text
发现并适配 robot_a，核对身份和 session，报告可用能力。
```

适配完成后，可以尝试仿真任务：

```text
把红色方块夹取到红色盘子上。
```

## 常用操作

命令中的 `--instance account-a` 指 **Agent instance**，`--robot-id robot_a` 标识该 Agent instance 下的 **Robot instance**。`lan.py client` 是管理 Robot instance 的现有子命令。

**查看连接状态、重新显示 join码和 Viewer 信息**（在 Server 主机上）：

```bash
python3 scripts/lan.py status --instance account-a
python3 scripts/lan.py info --instance account-a
```

**下次继续使用**：先在 Server 主机恢复会话，再在 Client 主机恢复 Robot instance，无需重新输入连接码。

```bash
# Server 主机
bash scripts/agenticros.sh --instance account-a resume --last
# Client 主机
bash scripts/environment.sh cartesian_gripper --instance account-a --robot-id robot_a
```

Robot instance 重启会重置仿真场景，继续任务前让 Codex 重新发现并核对 session。

**停止运行**：先结束 Codex 中的任务，再停止各 Robot instance，最后停止 Agent instance。

```bash
# 在各 Client 主机上执行
python3 scripts/lan.py client --instance account-a --robot-id robot_a --stop
# 在 Server 主机上执行
python3 scripts/lan.py stop-server --instance account-a
```

退出 Codex 不会停止容器；上述停止命令保留账号、配置和工作文件。

## 更多说明

- [详细使用说明](docs/usage.md)：多账号、指定 IP、镜像升级、远程查看画面和旧版迁移。**更新源码后，已有镜像需要手动重建。**
- [开发记录](docs/development-record.md)：Docker 目录结构、架构、测试与验证历史。

当前面向可信局域网中的仿真，不用于公网或物理机器人。已完成 macOS 同机和独立容器 IP 的集成验证，真实 Mac ↔ Linux 双机验收仍待完成。

## 双臂桌面环境

```bash
bash scripts/agenticros.sh --instance aloha-demo
bash scripts/environment.sh aloha_tabletop --join CONNECTION_CODE --robot-id aloha_a
```

一个 `aloha_tabletop` Robot instance 包含同一物理世界里的两臂，不能把左右臂作为两个独立实例启动。
左右臂接口位于该机器人 session 名称空间下的 `left/driver/`、`right/driver/`，共用坐标系与物理时钟。
Viewer 可发现两侧传感器；两侧图像目前来自同一俯视相机。
模型使用 MuJoCo Menagerie ALOHA 2，固定来源和许可见 `docker/robot_instances/aloha_tabletop/model/UPSTREAM`。
目前提供关节位置伺服、RGB-D、状态、动作取消和回执。IK、避障和双臂任务能力需由 Agent 发现环境后生成并验证。
这不表示已验证自主抓放或真实机器人；独立评估器目前仅判定红块放入红盘。

旧环境已经正式改名为 `cartesian_gripper`，旧 `simple_mujoco` 命令不再支持。
已有账号、工作区和数据卷保留；新环境使用新的 `--robot-id`，避免改变已有实例的环境绑定。

### 主 agent 与执行子 agent（实验配置）

主会话按 `AGENTS.md` 分配每个臂的子目标，并继续观测和协调；`arm-executor` 自定义子 agent 负责单臂闭环执行。
角色文件在启动时复制到 `/workspace/.codex/agents/arm-executor.toml`。启动或恢复节点不会自动恢复运动。
先进行只读发现，确认缺失能力，再授权分阶段的仿真动作。此配置不提供 IK 或完整任务脚本。
分配归属目前由协作协议约束，驱动只强制每臂一个在执行的 action；尚无跨 agent 的控制权限强制隔离。
双臂自主任务成功、监督响应延迟和异常恢复效果仍须单独验收，不能用 ROS 集成测试结果替代。
