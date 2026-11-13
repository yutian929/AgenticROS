# Docker 最小系统验收记录 — 2026-11-13

## 环境与边界

- Apple Silicon Mac，Docker Desktop 4.99.0，Engine 29.9.3，Linux/ARM64。
- ROS 2 Jazzy / Ubuntu Noble；MuJoCo 3.3.7；Codex CLI 0.168.0。
- 模型配置和实际 Codex turn_context 均为 `gpt-6-astra`、`medium`。
- 保留原生分支 `codex/macos-minimal-sim`、`archive/macos-prototype-20261113`，
  从未提交的重构草稿创建 `codex/docker-agent-minimal`。未 push，未连接真实机器人。
- 本次运行的详细日志保存在宿主机 `runs/docker-validation-20261113/`，不进入镜像。

## 已完成验证

| 类别 | 实际结果 |
| --- | --- |
| 原生 macOS 回归 | `python -m pytest tests -q`：31 passed，2 个上游弃用警告；不等于 Docker 或自主任务通过 |
| 两个镜像构建 | 均完成，镜像架构均为 arm64，ROS IDL 在镜像内通过 colcon 编译 |
| 容器启动 | body 健康；agent CLI、Python、ROS、C++ 编译、用户 venv 可运行 |
| ROS 跨容器 | 发现 raw_body_driver、status、observe、actuate；读取同步 RGB/depth/标定 |
| 参数检查 | 越界、NaN、非法 duration、旧 session 均拒绝，物理时间未推进 |
| 原始控制 | 1 cm 级关节测试；并发拒绝；运行中 UUID 可查询；取消 ACK 与终态停止分别核对；正常伺服完成 |
| 图像与文件权限 | Agent 观测卷实际写入失败；返回图像和 depth 可读取；Mac Chrome 已显示实时画面 |
| 实际镜像内容 | 导出 Agent 镜像，扫描 40,162 个常规文件、比对 15 个私有源码哈希及禁用路径，通过 |
| 实际挂载 | 仅 workspace、codex-home、只读 observations、只读 codex.toml；非 root、ALL capabilities dropped |
| 评估隔离 | Agent 无 body 源码及评估 Unix socket；ROS 无评估服务；私有 HTTP 路径返回 404 |
| 重启语义 | session 从 `b98f1f341058` 变为 `c1a23cbd9535`，物理状态重置；新观测使用新 ID |
| 独立评估器 | 原生单测覆盖初始失败、放稳成功、运动中失败；Docker 初始场景评估为失败，符合预期 |

镜像标识：

- Agent：`sha256:8c191d72783c3f4bec2d130bce9c14f56020fb5bdf762fa8e8eeebe738b2bb03`
- Body：`sha256:5d88d7f4b6e34fc4638dd3558b50eec3743b0aa1b52edb6113b07d58698474c0`

## 自主试验

容器认证最初缺失，登录检查返回 Not logged in，模型请求返回 401；用户随后通过
容器独立登录，检查变为 Logged in using ChatGPT。没有复制宿主机凭据。

已运行干净工作区试验 `rosbody-trial-20261113-183345-fe97da`。容器启动时 adapter
目录为空；日志记录 Codex 自行发现 ROS 接口和读取观测。两条输入分别为：

1. `适配一下你的身体`
2. `把红色方块夹取到红色盘子上`

自主适配已通过：Codex 自行编写 `adapter/body.py`、验证程序、README 和 manifest；
三个平移轴各 1 cm、空夹爪开合、回位和 7 类非法输入测试通过。过程中自主修复了
独立 ROS context 的执行器初始化错误。会话记录中确认了 7 次图像查看工具调用。
产物副本：`runs/rosbody-trial-20261113-183345-fe97da/adaptation-output/`。

此通过范围是小幅空场适配；全工作空间极值、适配器断网/重启/取消故障注入尚未验证，
manifest 已如实列出。驱动取消测试通过不能等同于生成适配器的全部故障路径已验证。

自主任务已将红块搬到红盘，打开夹爪并退离。Agent 自行编写 RGB-D 定位、分阶段
任务程序，并在未抬起、搬运滑落后根据观测修改适配器、重新定位和分段重抓。没有
维护者提供的适配实现、抓取程序、坐标或评估反馈进入 Agent。

**严格的第 6 阶段未通过。** Agent 首次根据图像/深度判断放置成功，但独立评估返回：

```json
{"success": false, "footprint_inside": true, "on_plate_surface": true,
 "stationary": false, "finger_contact": false, "busy": false}
```

随后追加了一个仅要求依据公开观测推进时间并复核稳定性的文本指令，没有提供评估
结果、阈值或真值。Agent 比较间隔 8.4 秒的图像，自行发现缓慢旋转，尝试轻夹止转、
释放、退离并再次观察。最终相同评估器仍返回上述结果（sim_time=233.34 s）。没有
降低评估阈值或修改物理场景来让试验通过；不能把“放到盘上”写成“稳定性全部通过”。

用户表示当前效果可以后，停止了继续尝试。停止时没有运行中的动作，状态为
`busy=false, stop_confirmed=true, recovery_required=false`。仿真和 Agent 容器保留，
Agent 中只剩持久容器的 sleep 进程，没有后台任务继续移动身体。

最终生成的代码和全部工作区副本：
`runs/rosbody-trial-20261113-183345-fe97da/workspace-final/`。
原始试验日志、初次及最终独立评估分别保留在同一 trial 目录，未覆盖失败证据。

继续使用这次自主生成的适配器（不会把它预装进新 Agent 镜像）：

```bash
docker exec -it rosbody-trial-20261113-183345-fe97da /usr/local/bin/agent-entrypoint codex
```

停止而保留数据：

```bash
docker stop rosbody-trial-20261113-183345-fe97da
docker compose down
```

原始两条指令对应的 task 会话：`01a1d47b-b1ec-7c41-8933-9470fa25c884`。
默认 `docker-chat.sh` 使用另一个持久 workspace；不要把它与该 trial 的生成物混淆。

## 修复与限制

- 先登录可能先创建 observations 卷：两个镜像现在都预建 uid 1001 的目录。本次初始
  草稿创建的空观测卷仅修正了目录所有权，没有删除数据或卷。
- NumPy 固定为 Jazzy 兼容的 1.26.4，明确安装 ARM64 可用的依赖。
- Docker Desktop 曾出现普通启动请求挂起、官方 restart 超时；在仅有本次测试容器时
  使用官方 stop --force 后 start 恢复。没有 factory reset、prune 或删除卷。
- 默认嵌套 bwrap 沙箱创建 namespace 失败。Codex 在容器内使用 danger-full-access；
  实际会话记录确认此策略，显式相同策略的 shell/ROS 探针成功。Docker 的非 root、
  能力限制、文件挂载边界保持。不是对任意恶意代码的安全保证。
- 物理仿真在动作期间推进，思考期间暂停。评估为当前状态判据，不是实机验证或多次成功率。
- DDS 未认证，Agent 有出站网络，观测和 receipt 存储未设置配额；应只运行可信仿真任务。
- 旧原生实现只作回归参考；未进入 Agent 镜像。没有维护者预写的标准适配器或抓取程序。

聊天启动脚本现使用 `--no-recreate` 保留已经运行的 body；要应用新的身体镜像或 seed，须显式重建。
