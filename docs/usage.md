# 详细使用说明

首次使用见 [README](../README.md)。以下命令均在仓库根目录执行。
Server / Client 是主机角色；Agent instance / Robot instance 是运行实例。
命令中的 `--instance` 指 Agent instance，`--robot-id` 标识其下的 Robot instance。

## 多账号与会话

不同 Agent instance 分别保存账号、配置、历史和工作区：

```bash
bash scripts/agenticros.sh --instance account-a
bash scripts/agenticros.sh --instance account-b
bash scripts/agenticros.sh --instance account-a resume --last
bash scripts/docker-login.sh --instance account-a
```

名称使用小写字母、数字和连字符，首位不能为连字符，最长 48 字符；`--instance` 放在其他参数之前。
首次启动按菜单选择 ChatGPT 或 API；后续可复用账号，也可进入菜单切换配置。
模型默认配置见 [codex.toml](../docker/agent_instance/agent/codex.toml)。API 校验可能产生少量用量。
每个 Agent instance 同时只允许一个交互控制会话。

## 局域网与指定 IP

Linux 与 macOS 使用相同启动命令。macOS 需要 Docker Desktop 4.34+，开启
Settings → Resources → Network → Enable host networking，然后 Apply & restart；不能同时启用 Enhanced Container Isolation。
启动器会检测双向 host networking。多网卡或 VPN 环境可明确指定 Server 主机地址：

```bash
AGENTICROS_HOST=192.168.1.10 bash scripts/agenticros.sh --instance account-a
```

Robot instance 使用输出的完整连接码加入，不必另传 Server IP。同一 Agent instance 下，每个机器人使用不同 ID：

```bash
bash scripts/environment.sh simple_mujoco --join CONNECTION_CODE --robot-id robot_a
bash scripts/environment.sh simple_mujoco --join CONNECTION_CODE --robot-id robot_b
```

连接码包含地址、实例身份与加入令牌，只交给相关操作者。
`127.0.0.1` 仅适用于同机部署；跨机器必须使用可达的局域网 IP。
已有 Robot instance 的归属与连接码不符时，启动器会拒绝覆盖。

新 Agent instance 默认 Fast DDS TCP；已有 Linux UDP 配置保持原协议。
macOS 需要新建 TCP Agent instance，不能加入旧 UDP 实例。
Viewer 通过容器内回环地址访问注册服务；Robot instance 与注册服务使用同一主机 IP 时，启动器也让注册请求走回环地址。保存的连接码、对外地址及实例归属不变，远端 Robot instance 仍走局域网地址。
同机 ROS 数据可走共享内存，跨机走 TCP。注册端口从 TCP 7447、发现端口从 11811、快照端口从 TCP 8800 起选择，以实际输出为准。
TCP 数据还需要可信主机间双向动态 TCP 连接，仅开放注册与发现端口不够。
这些网络配置不提供 DDS 身份认证、加密或恶意节点隔离，不能用于公网或真实机器人。

## 查看状态与画面

```bash
python3 scripts/lan.py info --instance account-a
python3 scripts/lan.py status --instance account-a
python3 scripts/lan.py server --instance account-a
```

`info` 显示 join码、Viewer 地址与密码；`status` 列出注册状态。
`server` 只准备 Agent instance 的服务与可视化，不进入 Codex。
注册成功不等于 ROS 数据已互通，仍需核对实际观测。

Viewer 默认绑定 Server 主机的回环地址。需要其他电脑访问时，在首次创建时指定：

```bash
AGENTICROS_VIEWER_BIND=192.168.1.10 bash scripts/agenticros.sh --instance account-a
```

网页顶部选择机器人。点击 **ROS interfaces**，可在 Topics、Services、Actions 三个页签查看发现到的名称和类型，列表每两秒更新；这里只查看接口，不调用服务或发送动作。当前仿真以图像、点云和 TF 展示场景；不同机器人的坐标系独立。
可单独启停 Viewer，关闭 RViz 后也可点击网页中的 `Open / reset RViz`：

```bash
python3 scripts/lan.py viewer --instance account-a
python3 scripts/lan.py stop-viewer --instance account-a
```

## 停止、恢复与数据

先结束控制任务，再停止各 Robot instance，最后停止 Agent instance：

```bash
# 各 Client 主机
python3 scripts/lan.py client --instance account-a --robot-id robot_a --stop
# Server 主机
python3 scripts/lan.py stop-server --instance account-a
```

退出 Codex 不会停止 Agent instance 容器。恢复时先启动 Agent instance，再恢复机器人：

```bash
bash scripts/agenticros.sh --instance account-a resume --last
bash scripts/environment.sh simple_mujoco --instance account-a --robot-id robot_a
```

重启 Robot instance 会重置仿真场景和 session，必须重新发现、核对身份与观测。
租约失效时机器人中止动作并拒绝新目标；通信恢复不会重发动作或自动恢复控制，需明确停止并重启机器人。

命名实例的工作区在 `runs/instances/<名称>/workspace/`，连接配置在该实例的 `lan/` 目录。
账号、观测与可视化密码使用专用数据卷。停止或移除容器不等于删除这些数据；不要使用 `down -v`。
生成 ROS 包、能力清单和证据分别位于工作区中的 `ros_ws/src/`、`capabilities/manifest.json` 和 `evidence/`。

## 更新源码与镜像

已有镜像默认复用，不会因源码变化自动更新。按所在主机的角色构建所需镜像：

```bash
# Agent instance
docker build --target instance -f docker/agent_instance/Dockerfile -t agenticros-instance:lan .
# Robot instance
docker build -f docker/robot_instances/simple_mujoco/Dockerfile -t agenticros-simple_mujoco:lan .
```

构建后，先结束任务并停止对应实例，再重新启动。启动器会复用已有容器，不会自动替换在线或已创建的容器；
应用新镜像时需先移除对应的旧容器（保留数据卷），然后按原命令启动。
目录整理没有改变镜像标签、容器名、数据卷或已有配置路径。

### 从旧三容器部署迁移

先结束任务并停止各 Robot instance，再构建新镜像，替换旧 Agent instance 容器：

```bash
# 替换为自己的实例名称；旧 controller 使用 --rm，停止后自动移除
docker stop agenticros-controller-account-a agenticros-lan-account-a-viewer-1 agenticros-lan-account-a-server-1
docker rm agenticros-lan-account-a-viewer-1 agenticros-lan-account-a-server-1
bash scripts/agenticros.sh --instance account-a
```

随后按上文恢复 Robot instance。保留全部数据卷与 `runs/`，账号、加入令牌和 Viewer 密码沿用原数据。

## 旧版单机入口

不带 `--instance` 时保留旧版模式，启动顺序与命名实例模式不同：

```bash
bash scripts/environment.sh simple_mujoco
bash scripts/agenticros.sh
```

该模式由仿真容器提供 RViz2。恢复 Agent 会话使用 `bash scripts/agenticros.sh resume --last`；
结束任务后使用 `docker compose stop simple_mujoco` 停止仿真。
默认工作区为 `runs/workspace/`。新用户建议使用 README 中的命名实例流程。

旧单机实例可通过下列命令预览删除范围；去掉 `--dry-run` 会不可恢复地删除账号、配置、历史与工作区：

```bash
bash scripts/delete-instance.sh --instance account-a --dry-run
```

该脚本拒绝删除含 LAN 配置或仍被容器引用的实例，不用于删除当前命名 LAN 实例。

## 验证与开发

```bash
bash scripts/test.sh
python3 scripts/audit_agent_image.py --image agenticros-instance:lan
python3 scripts/check_lan.py --transport tcp
python3 scripts/check_lan.py --transport tcp --separate-ips --result-timeout 180
python3 scripts/check_viewer.py
```

集成验证使用新建的隔离实例，结束后移除测试容器，保留证据和数据卷。
独立 IP 测试使用私有 IPC，不能替代真实 Mac ↔ Linux 双机的路由、防火墙和链路验收。
`check_viewer.py` 不发出机器人运动指令；`check_lan.py` 会在新建测试仿真中验证动作、取消与断线停止。
这些结果不代表 Codex 自主适配或任务评估通过。目录结构、隔离约束与历史结果见 [开发记录](development-record.md)。
