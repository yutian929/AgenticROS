# AgenticROS

让 Codex 成为 ROS 2 空间中的调用中枢，逐步观测、调用接口、读取反馈并决定下一步。
当前提供 `simple_mujoco` 仿真环境，内置 ROS 驱动、RGB-D 观测和浏览器中的 RViz2。

环境为 **Ubuntu 22.04 + ROS 2 Humble**，已在 **Linux AMD64** 验证。
宿主机只需 Docker（含 Compose）和 Python 3，无需安装 ROS。
Agent 不预装仿真专用消息或身体说明，通过 ROS 2 运行时发现环境、获取公开定义并自行编译接口。
已有能力直接调用；缺少能力时才开发、启动 ROS 功能节点。任务决策留在 Codex 会话中，不生成脚本包办整个任务。

具体环境位于 `docker/environments/<名称>/`，各自包含镜像、驱动、接口和启动脚本。
`environment.sh <名称>` 是统一入口，当前提供 `simple_mujoco`。

## 快速开始

```bash
bash scripts/environment.sh simple_mujoco # 启动仿真与 RViz2
bash scripts/agenticros.sh                # 启动 Agent，选择账号或 API 并校验
```

Agent 启动后显示终端菜单：ChatGPT 账号、DeepSeek API、其他云端 Responses API。
账号方式按设备码完成浏览器授权；API 方式输入地址、模型和隐藏的 Key，校验通过后进入 Codex。
后续启动先询问是否切换账号或修改连接配置，回车或输入 `n` 直接使用已保存的配置进入 Codex，
不重复发送 API 校验请求；输入 `y` 进入配置菜单，输入 `q` 退出。
在菜单中选择 ChatGPT 会重新发起设备码授权，请在浏览器中选择目标账号；取消或认证失败保留原账号。
旧的 `docker-chat.sh` 保留为兼容入口。
模型名后可选择推理强度；已有配置可选“仅修改推理强度”，不必重填 Key。

依次下达指令：

```text
适配一下你的身体
把红色方块夹取到红色盘子上
```

启动脚本会显示 RViz2 的浏览器地址和连接密码，并尝试自动打开浏览器，输入显示的密码即可连接。
没有桌面环境时，手动打开脚本输出的地址；默认端口为 `8080`。

## 常用操作

### 按账号隔离 Agent

使用不同的 instance 名称，分别保存账号、连接配置、Codex 历史和生成的工作文件：

```bash
bash scripts/agenticros.sh --instance account-a
bash scripts/agenticros.sh --instance account-b
bash scripts/agenticros.sh --instance account-a resume --last
# 只进行设备码登录（浏览器中选择对应账号）：
bash scripts/docker-login.sh --instance account-a
```

新 instance 首次选择 ChatGPT 时需要独立登录；再次进入同名 instance 会复用其数据。
名称使用小写字母、数字和连字符，首位不能是连字符，最长 48 字符；`--instance` 放在其他参数之前。
`default` 是原有环境，不传参数等同于 `--instance default`，原账号和文件不迁移、不删除。
命名环境的工作目录为 `runs/instances/<名称>/workspace/`，认证卷默认为
`agenticros_codex-home-instance-<名称>`。删除容器不会清除这些数据。
账号环境仍共享仿真、ROS 网络和公开观测；同一时间只运行一个控制 Agent。

删除一个 instance（不可恢复，会删除账号、连接配置、Codex 历史和工作文件）：

```bash
bash scripts/delete-instance.sh --instance account-a --dry-run # 预览
bash scripts/delete-instance.sh --instance account-a           # 执行删除
```

必须明确提供名称；删除原有默认环境需显式指定 `--instance default`。
若仍有容器引用该环境（包括已停止的容器），脚本拒绝删除并列出容器 ID，须先退出并移除这些容器。
脚本不停止仿真，不删除共享观测、其他 instance、镜像或历史迁移保留的旧工作区卷。
重复删除不存在的 instance 不会创建卷或工作目录。

### 继续会话与停止仿真

```bash
bash scripts/agenticros.sh resume --last  # 继续会话
bash scripts/environment.sh simple_mujoco # 停机或断电后恢复环境
# 确认控制任务结束后停止环境：
docker compose stop simple_mujoco
```

模型配置见 [codex.toml](docker/agenticros/codex.toml)，默认 `gpt-6-astra / medium`。
菜单选择、模型和 Key 保存在容器专用 Codex 卷，校验失败不覆盖原配置；API 校验会产生少量用量。
生成代码、编译产物和动作证据直接保存在仓库的 `runs/workspace/`，对应容器内 `/workspace`，Git 默认忽略。
首次切换时，先退出旧 Agent；启动脚本会复制并校验旧工作区卷，保留原卷。不会自动合并非空目录。
认证、相机观测和 VNC 密码仍由独立数据卷保留；仿真重启会重置场景并更换 session，继续任务前须重新核对与观测。
一次只运行一个控制 Agent，不要使用 `down -v` 删除数据卷。

生成的 ROS 包位于 `runs/workspace/ros_ws/src/`，能力及调用方法记录在 `capabilities/manifest.json`，
观测、回执和进程日志位于 `evidence/`（后两者同属该工作区）。新容器会复用原工作区；
需要空工作区的自主验收时使用 `python3 scripts/docker-trial.py`，它会运行适配及抓放任务，须先确认环境空闲。

[开发记录](docs/development-record.md)包含架构、配置、迁移、测试命令和验证历史。
当前仅验证仿真环境；新的逐步调用规则与工具检查不代表 Codex 已完成自主适配或抓取验收。
