# Agent 规则更新

修改 `docker/agent_instance/agent/` 下的 `AGENTS.md`、`contract.md`、`skills/` 或 `agents/` 后，
退出当前 Agent 会话，再用原启动命令启动或恢复会话：

```bash
bash scripts/agent.sh resume --last
```

启动器会将本机仓库里的规则制作成工作区内的版本快照，再在启动 Agent 前同步到实际加载位置。
不需要重新构建镜像或重启 Server、机器人，
修改不会自动注入正在执行的会话。远程部署时，先把代码更新到运行启动脚本的 Server 主机。

同步范围：

| 仓库源文件 | Agent 工作区位置 |
| --- | --- |
| `AGENTS.md` | `/workspace/AGENTS.md` |
| `contract.md` | `/workspace/contract.md` |
| `skills/` | `/workspace/.agents/skills/` |
| `agents/` | `/workspace/.codex/agents/` |

同名规则以仓库版本为准。此前同步且随后从仓库删除的文件，在本地未修改时会移除；
已在工作区修改的旧文件会保留。Agent 自行生成的其他 skill、节点、证据及账号数据保留。
快照及同步清单位于工作区 `.agenticros/`，不会挂载整个源码仓库。
先取得已有的单会话锁，再安装规则；第二个启动请求不会改写正在运行的会话规则。

镜像内规则仅作为首次初始化时的默认值。正常使用 `agent.sh`、`agent-login.sh` 或 `lan.py agent/login`
入口才能同步本机最新规则；直接执行旧镜像的原始 entrypoint 不具备这一功能。
Python/ROS 依赖、驱动、Viewer 等程序代码的更新仍按原流程重建对应镜像。
