# 开发与运行说明

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
单测、集成、自主适配和任务评估分别验收，已有结果与限制见[验证记录](docker-validation.md)。
旧原生实现仅保留在 Git 历史和归档分支。

## 隔离边界

AgenticROS 不挂载项目、宿主机 home 或 Docker socket，只获得公开资料、原始接口、只读观测与自己的工作区。
评估器只监听仿真容器内部 Unix socket，不通过 ROS、HTTP 或观测卷提供真值。
两侧非 root、移除 capabilities；Codex 使用 `danger-full-access`，因为嵌套 bwrap 不可用。
DDS 未认证且 Agent 有出站网络；这是文件和进程隔离，不是防御所有恶意行为的沙箱。

## 配置与旧版迁移

`ROSBODY_VIEWER_PORT` 设置相机端口（默认 8080），`ROS_DOMAIN_ID` 设置双方一致的 ROS 域（默认 42）。
仿真只在动作期间推进。确认没有控制任务后，可显式重建场景：

```bash
BODY_SEED=3 docker compose up -d --build --force-recreate --wait simple_mujoco
```

重启生成新的 session ID，旧观测不能代表当前状态。
Compose 项目名保留 `rosbody`，复用原有卷、认证和网络。旧版 `body` 仿真若仍运行，启动脚本会保留并复用；
切换服务名需先停止控制，再手动 `docker stop rosbody-body-1`，然后重新运行环境启动脚本。

`environment.sh real` 仅说明外部 ROS 接入要求并退出，不启动容器或操作硬件。
设备登录不可用时，可在容器中通过 `codex login --with-api-key` 从 stdin 登录；不要把密钥写入仓库。
