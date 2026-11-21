# 新机器人最小模板

此目录以下划线开头，不会被 `robot.sh --list` 当成机器人型号。
模板可直接运行，提供 ROS 状态 Topic 和查询 Service；没有硬件连接或运动功能。

## 复制与启动

在 AgenticROS 项目根目录执行，选择尚未使用的型号名（小写字母开头，仅含小写字母、数字、下划线，最多 63 个字符）：

```bash
robot_type=my_robot
test ! -e "docker/robot_instances/$robot_type" && \
test ! -e "docker/robot_instances/robot_docs/$robot_type" && \
mkdir -p "docker/robot_instances/$robot_type" "docker/robot_instances/robot_docs/$robot_type" && \
cp docker/robot_instances/_template/{Dockerfile,entrypoint.sh,driver.py,healthcheck.py} "docker/robot_instances/$robot_type/" && \
cp docker/robot_instances/_template/robot_docs/manual.md "docker/robot_instances/robot_docs/$robot_type/"
```

修改复制出的 Dockerfile 中 `ARG ROBOT_TYPE=my_robot` 和 manual.md 的型号。
使用默认示例名 my_robot 时，可先不改代码直接验证通信：

```bash
bash robot.sh --list
bash robot.sh my_robot --domain-id 42
bash agent.sh agent --domain-id 42
```

Agent 内的资料位于 `/workspace/robot_docs/my_robot/`，为只读挂载。
Agent 可用 `agenticros-ros status` 发现实例；原生 `ros2 topic list --no-daemon --spin-time 3` 也可使用。
把启动输出中的实际 ID 替换到下面命令，不要保留 `<ID>`：

```bash
ros2 topic echo --once /robot/<ID>/driver/state std_msgs/msg/String
ros2 service call /robot/<ID>/driver/status std_srvs/srv/Trigger '{}'
```

## 哪些地方要改

| 文件 | 必改/按需修改 |
| --- | --- |
| `Dockerfile` | 型号名必须匹配目录；实际接入时安装驱动依赖、复制文件并构建 ROS 包 |
| `entrypoint.sh` | 实际驱动的启动命令及 workspace setup；原样保留可运行模板节点 |
| `driver.py` | 用真实数据替换状态并接入实际接口；已有厂商节点可替换此文件，不要求 Python 或继承此类 |
| `healthcheck.py` | 检查本型号真实就绪条件；模板只检查节点心跳 |
| `robot_docs/<型号>/manual.md` | 写明型号、版本、接口、单位、坐标系、限制和停止方式 |

启动器传入 ROS_DOMAIN_ID 和 AGENTICROS_ROBOT_ID；不要固定实例 ID。
使用 `/robot/<ID>/` 命名空间，驱动进程重启时更新 session。
`common/robot_identity.py` 是可选工具；若改用原生/C++驱动，可移除其 COPY，并在自己的驱动或启动适配层实现身份约定。

## 容器约束与更新

- 默认域为 42；同型号重复启动会生成新的 8 位随机后缀，本机重名时重试。
- 当前容器共享 host 网络/IPC，使用 UID 1001；根文件系统只读，临时文件写到 `/tmp`。
- GPU、USB/串口映射、额外写入目录需要单独配置，Dockerfile 不会自动授予这些资源。
- 只使用标准消息时不需要 `interfaces/` 或额外 ROS 包；自定义接口的公开源码放在型号资料目录。
- 修改驱动/Dockerfile 后显式重建；启动器只在镜像不存在时构建：

```bash
docker build -f docker/robot_instances/my_robot/Dockerfile -t agenticros-my_robot:single .
```

新启动的实例使用新镜像。已有实例先核对任务停止，再用其 ID 管理，保留原 ID 和配置：

```bash
python3 scripts/runtime.py robot --robot-id <ID> --domain-id 42 --stop
python3 scripts/runtime.py robot --robot-id <ID> --domain-id 42 --recreate
```

不需要修改 robot.sh、scripts/runtime.py 或 Agent 代码。

## 已验证

按本模板复制并启动了两个独立实例与一个 Agent，验证 UUID 不重复、ROS 状态查询、Agent 自动发现、重启 session 更新及公开手册可见。测试容器和临时镜像已清理。证据：`runs/checks/agenticros-template-check-b34a0a80/result.json`；这是单机通信骨架验证，不是硬件驱动验证。
