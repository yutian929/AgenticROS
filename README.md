# ROSBody

Mac 上的两个 Docker 容器，通过 ROS 2 Jazzy 通信，无需本机 ROS 或 pixi。

- **Body**：MuJoCo 场景、原始关节驱动、RGB-D 观测和私有评估器。
- **Agent**：Codex、ROS 2、Python、编译工具和身体资料。初始不包含适配器或任务程序，由 Codex 自行实现。

## 启动

安装并启动 Docker Desktop，然后在项目目录运行：

```bash
docker compose build
bash scripts/docker-login.sh        # 首次在容器中独立登录
bash scripts/docker-chat.sh         # 进入 Codex
```

依次输入：

```text
适配一下你的身体
把红色方块夹取到红色盘子上
```

在 [localhost:8080](http://localhost:8080) 观察执行，终端显示 Codex 的命令、输出和错误。
认证保存在专用卷，不复制宿主机凭据；设备登录不可用时可在自己的终端使用 `codex login --with-api-key` 从 stdin 登录。

## 配置与数据

编辑 [docker/agent/codex.toml](docker/agent/codex.toml)：默认 `gpt-6-astra`、`medium`。
配置只读挂载，启动时同步到容器。命名卷保存工作区、适配器、会话、认证和观测。

```bash
bash scripts/docker-chat.sh resume --last  # 继续默认工作区的会话
docker compose down                       # 停止，保留卷；不要加 -v
```

退出 Codex 不会取消已接受的动作，交接控制前必须核对 UUID 和停止回执。一次只运行一个控制 Agent。
聊天启动脚本保留正在运行的 Body；要应用新身体代码或场景种子，先停止控制，再显式运行：

```bash
BODY_SEED=3 docker compose up -d --build --force-recreate --wait body
```

重启会重置物理状态并生成新 session ID；旧观测不能代表当前状态，动作必须携带当前 ID。
`ROSBODY_VIEWER_PORT=8081` 可调整浏览器端口。仿真只在动作期间推进，等待墙上时间不会让物体继续运动。

## 开发与验证

| 路径 | 用途 |
| --- | --- |
| `docker/body/` | 私有仿真、驱动和评估 |
| `docker/interfaces/` | 原始 ROS 接口定义 |
| `docker/agent/body.md`、`contract.md` | 身体资料和标准接口契约 |
| `docker/agent/skills/setup-body/` | 自主适配流程 |
| `scripts/`、`tests/` | 启动和验收工具，无预写适配答案 |

维护者只需 Docker 和 Python 3 标准库：

```bash
docker compose build
bash scripts/test.sh                       # 布局和私有评估器单测，不影响活体仿真
python3 scripts/audit_agent_image.py        # 实际镜像内容审计
docker compose up -d --no-recreate --wait body
python3 scripts/check_docker.py            # 跨容器通信、观测和隔离
python3 scripts/check_docker.py --motion   # 另做小幅动作/取消测试，仅在没有任务时运行
python3 scripts/docker-trial.py            # 独立新工作区，交给 Codex 两条原始指令
bash scripts/docker-evaluate.sh            # 维护者侧独立评估，不向 Agent 提供结果
```

自主试验保留容器、专用工作区及 `runs/` 中的日志与证据。它与默认聊天工作区分开，继续或停止时使用脚本打印的名称：

```bash
docker exec -it TRIAL_CONTAINER /usr/local/bin/agent-entrypoint codex
docker stop TRIAL_CONTAINER
```

单测、原始控制、自主适配和任务评估分别验收，不能凭 Codex 退出码判定成功。
已完成试验和限制见 [验证记录](docs/docker-validation.md)。旧原生实现留在 Git 历史和归档分支。

## 隔离边界

Agent 不挂载项目、宿主机 home 或 Docker socket，只得到公开资料、原始接口、只读观测和自己的工作区。
评估器只监听 Body 内部 Unix socket，不通过 ROS、HTTP 或观测卷提供真值。

两个容器均非 root、移除 capabilities。Codex 在容器内使用 `danger-full-access`，因为嵌套 bwrap namespace 不可用。
DDS 未认证，Agent 有出站网络；这是文件和进程隔离，不是防御所有恶意行为的沙箱。仅用于仿真。
