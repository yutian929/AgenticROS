# 单 Agent、多机器人网络验证

> 历史报告：对应 HTTP presence/控制租约版本，保留作为基线证据。当前直接 ROS 接入的架构与测试见 [原生 ROS 验证报告](direct-ros-validation.md)。

本报告对应 `codex/single-agent-network`，基线为 `origin/main` 的 `b58a03a`。先完成基线采样，再修改架构。完整原始记录留在本工作树忽略的 `runs/`；可审查的指标、拓扑、源码校验和测试结果保存在 [evidence/single-agent-network](evidence/single-agent-network/manifest.json)。指标文件仅保留计时、结果与错误类别，不提交账号、租约密钥或签名下载 URL。

## 环境与可比范围

- 一台 macOS 主机上的 Docker Desktop Linux VM，10 个逻辑 CPU、约 7.75 GiB 内存。网络集成使用一个 Server 和两个独立机器人容器，三个不同 IP、三个独立网络名称空间、私有 IPC，实测跨容器 DDS 与 HTTP，不是单容器自测。
- 基线和修改后均使用 Cartesian Gripper、固定 TCP Discovery Server。同一机器、不同时间的顺序采样，不是统计显著性实验。ALOHA 动作另在 `--network none` 的隔离容器验证；ALOHA Viewer 使用三容器链路。
- 未操作真实硬件，没有使用用户账号运行自主任务，不声称完成自主抓取/交接或真实机器人验收。原工作树未提交修改保持原样。
- **没有跨物理主机验证，也没有原生 Linux 主机验证。** Docker Linux 容器通过不等于两台 Linux/macOS 主机的 LAN 通过。macOS host 网络另做双向 nonce 预检，不等于完整跨机器 DDS 验证。

## 测量定义

`discovery_ms` 是已有节点创建/取得客户端后，`wait_for_service` 的等待时间；不含 Python 导入与整个 participant 初始化。`response_ms` 是调用至收到响应的时间。`total_ms` 是常驻 socket 或冷 CLI 的端到端时间。相同指标才能比较。`warm` 包含第一个未预热调用；`cold` 每次重新启动 ROS 进程。`resident_cli_cold` 重启薄 CLI，但复用常驻 ROS worker，不能冒充冷 ROS participant。

P50 为中位数，P95 为 nearest-rank，最大值不剔除首样本或离群值；超时样本如有计时也纳入分布。N=10 的 P95 就是最大值。`registration` 行实际是 HTTP `/robots` 目录 GET，首次申请和续租单独测为 `http_enrollment`、`http_renewal`。

发现超时归 `discovery_failure`；发现成功但等待响应超时归 `request_timeout`；服务端否定/异常归 `server_failure`。图像 HTTP 下载单独记录，并核对 SHA-256；它与 DDS 相机订阅是两条链路。机器人停机期间的预期失败不从结果中删除。

## 实测结果

| 场景 | 成功 / N | 发现 ms：P50 / P95 / 最大 | 响应 ms：P50 / P95 / 最大 | 端到端 ms：P50 / P95 / 最大 |
| --- | ---: | --- | --- | --- |
| 基线 · 连续 | 50 / 50 | 0.009 / 0.039 / 1003.020 | 0.580 / 6.285 / 556.581 | — |
| 修改后 TCP · 连续 | 50 / 50 | 0.018 / 0.058 / 4517.240 | 1.015 / 2.916 / 3418.058 | — |
| 基线 · 双机器人并发 | 50 / 50 | 0.022 / 0.062 / 1006.425 | 177.101 / 264.516 / 371.524 | — |
| 修改后 TCP · 双机器人并发 | 50 / 50 | 0.019 / 0.043 / 1254.978 | 1.200 / 119.798 / 135.817 | — |
| 基线 · 新 ROS 进程 | 10 / 10 | 1002.283 / 1254.632 / 1254.632 | 526.958 / 1530.778 / 1530.778 | — |
| 修改后 TCP · 新 ROS 进程 | 10 / 10 | 1632.122 / 4685.915 / 4685.915 | 1820.575 / 4024.761 / 4024.761 | — |

上述正常状态调用的发现失败、请求超时、服务端失败均为 0。连续/并发的第一笔也计入；基线和修改后都达到这些样本上的 100%，不能据此声称成功率提升。**本轮独立 ROS 冷启动明显更慢，不支持总体加速结论。** 固定 worker 的价值是让日常查询避免反复建立 participant；同时保留超时及未知状态语义。

### 常驻客户端（TCP）

| 场景 | 成功 / N | 发现 ms：P50 / P95 / 最大 | 响应 ms：P50 / P95 / 最大 | 端到端 ms：P50 / P95 / 最大 |
| --- | ---: | --- | --- | --- |
| 常驻 · socket RPC | 101 / 101 | 0.017 / 0.039 / 0.081 | 1.164 / 1.919 / 104.294 | 1.670 / 4.551 / 130.314 |
| 常驻 · 薄 CLI 冷启动 | 10 / 10 | 0.073 / 0.119 / 0.119 | 2.346 / 100.166 / 100.166 | 48.255 / 139.701 / 139.701 |
| worker 重载后 · socket RPC | 10 / 10 | 0.021 / 501.130 / 501.130 | 1.480 / 1072.581 / 1072.581 | 2.149 / 1618.412 / 1618.412 |
| worker 重载后 · 薄 CLI 冷启动 | 2 / 2 | 0.075 / 0.077 / 0.077 | 2.352 / 2.787 / 2.787 | 43.600 / 47.403 / 47.403 |

常驻阶段 101 笔 RPC 包含 50 笔连续、50 笔双机器人并发和 1 笔动作前状态查询；重载后 10 笔包含 5 连续、4 并发和 1 状态查询。所有行均无发现失败、请求超时或服务端失败；UUID 防重放和超时核对另有动作断言。

### HTTP 注册、ROS 观测和图像下载分层

| 场景 | 成功 / N | 发现 ms：P50 / P95 / 最大 | 响应 ms：P50 / P95 / 最大 | 端到端 ms：P50 / P95 / 最大 |
| --- | ---: | --- | --- | --- |
| 基线 · HTTP 目录 GET | 12 / 12 | — | 1.402 / 2.355 / 2.355 | — |
| 基线 · HTTP 图像下载 | 10 / 10 | — | 1.195 / 5.284 / 5.284 | — |
| 基线 · ROS Snapshot | 10 / 10 | 0.032 / 0.055 / 0.055 | 783.106 / 809.811 / 809.811 | — |
| 修改后 TCP · HTTP 目录 GET | 12 / 12 | — | 3.360 / 32.242 / 32.242 | — |
| 修改后 TCP · HTTP 图像下载 | 10 / 10 | — | 3.563 / 25.046 / 25.046 | — |
| 修改后 TCP · ROS Snapshot | 10 / 10 | 0.042 / 0.076 / 0.076 | 200.988 / 302.413 / 302.413 | — |
| 基线 · 首次身份申请 | 20 / 20 | — | 0.330 / 0.526 / 0.785 | — |
| 基线 · presence 续租 | 20 / 20 | — | 0.339 / 0.598 / 0.658 | — |
| 修改后 · 首次身份申请 | 20 / 20 | — | 0.552 / 0.696 / 0.864 | — |
| 修改后 · presence 续租 | 20 / 20 | — | 0.531 / 0.702 / 0.722 | — |

以上各层正常采样失败均为 0；HTTP 图像内容均通过 SHA-256 校验。Snapshot 含生成图像/落盘时间，不能当作纯传输延迟。独立 HTTP presence 基准运行在两个容器，不含 ROS。

### UDP 对照与重启故障注入

| 场景 | 成功 / N | 发现 ms：P50 / P95 / 最大 | 响应 ms：P50 / P95 / 最大 | 端到端 ms：P50 / P95 / 最大 |
| --- | ---: | --- | --- | --- |
| UDP · 连续 | 50 / 50 | 0.013 / 0.033 / 506.624 | 0.707 / 0.992 / 515.828 | — |
| UDP · 并发 | 50 / 50 | 0.016 / 0.063 / 505.798 | 0.986 / 4.439 / 454.553 | — |
| UDP · 新 ROS 进程 | 10 / 10 | 504.582 / 509.271 / 509.271 | 451.471 / 524.984 / 524.984 | — |
| UDP · 常驻 socket | 101 / 101 | 0.012 / 0.027 / 0.053 | 0.832 / 1.347 / 101.393 | 1.148 / 1.810 / 102.006 |
| UDP · 薄 CLI 冷启动 | 10 / 10 | 0.042 / 0.073 / 0.073 | 1.501 / 90.853 / 90.853 | 39.961 / 138.339 / 138.339 |

UDP 正常状态/RPC 样本也无发现失败、请求超时或服务端失败，仅证明此 Docker 网络内可用。

| 场景 | 成功 / N | 发现 ms：P50 / P95 / 最大 | 响应 ms：P50 / P95 / 最大 | 端到端 ms：P50 / P95 / 最大 |
| --- | ---: | --- | --- | --- |
| TCP · restart_initial | 1 / 1 | 0.030 / 0.030 / 0.030 | 4930.390 / 4930.390 / 4930.390 | — |
| TCP · robot_restart | 12 / 23 | 0.645 / 2012.192 / 2013.050 | 0.985 / 2001.388 / 2001.388 | — |
| UDP · robot_restart | 13 / 16 | 0.026 / 2027.432 / 2027.432 | 1.296 / 2000.163 / 2000.163 | — |

TCP 停机/恢复循环：12/23 成功（52.2%）；失败分类 `{"request_timeout": 1, "discovery_failure": 10}`，服务端失败 0。这些失败属于主动停机和重新发现窗口，仍完整保留；最后十笔恢复后查询均成功。

UDP 停机/恢复循环：13/16 成功（81.2%）；失败分类 `{"request_timeout": 1, "discovery_failure": 2}`，服务端失败 0。这些失败属于主动停机和重新发现窗口，仍完整保留；最后十笔恢复后查询均成功。

### 验收记录

回归 **92 项通过**；TCP/UDP 三容器动作与重连、两套 ALOHA 集成、Viewer 两次生命周期、实际 host-network Compose 启动、规则热同步和镜像审计均通过。镜像审计扫描 61,951 个文件；[源码与镜像核对](evidence/single-agent-network/source-verification.json)记录了实际镜像 ID。

[原生 RViz 桌面截图](evidence/single-agent-network/viewer-desktop.png)已目视确认三路相机面板均有实际图像及点云。最后一次常驻三路首次取帧整体等待为 1,720.581 ms（N=1 个三路检查窗口，不作分位数推断）。



## 覆盖的行为

- 常驻 Service Client 连续调用、两个机器人并发查询；十次新的 ROS participant 冷启动；薄 CLI 冷启动复用 worker；worker 重载后复用已有工作区绑定。
- 机器人进程重启时名称空间保持不变，session 改变；旧 action 与旧 Snapshot session 被拒绝；同一个测试客户端恢复查询并执行新 session 的隔离仿真动作。
- 跨机器人 session 拒绝、动作 UUID、单臂互斥、取消回执、实测停止、动作结果超时后查询状态。通信 worker 重启后，持久发送日志继续拒绝同一 UUID，绝不自动重发。
- Registry 停止期间，运行中的仿真动作停止；Registry 恢复后旧控制许可不复活。进程存活与控制租约分开报告。
- ALOHA `actuate`、`move_pose`、`move_delta` 共用每臂执行门；双臂共享物理世界；取消、超限/错误 frame 拒绝、恢复及故障状态检查。
- Viewer 测试通过产品常驻客户端实际接收三路压缩相机图像，生成三个原生 RViz Image 面板及原生 Qt dock 布局；检查十二个公开 action 接口、VNC 鉴权、Viewer 停启保留机器人 session/口令、单 Agent 锁。图像到达后检查 RViz PID 稳定六秒。截图已目视确认本次三面板有实际图像；未做长期帧率或画质验收。
- 规则热同步、模型连接配置、工作区权限/迁移保留、UUID 竞争、超时分类、不可把发现当作控制许可等回归测试。镜像文件系统审计确认 Server 不含设备 IDL、私有仿真资产或任务适配程序。

## DDS 选择与排障记录

保留固定 Discovery Server；TCP 继续使用同一份 TCP+SHM profile，host 网络对应 host IPC，独立网络名称空间之间走 TCP。未改成 multicast 自动发现，也不允许任务 agent 临时改域/传输配置。UDP 仅作为显式统一配置的 Linux 候选，在同机隔离容器实测；macOS 启动入口仍拒绝 UDP。

开发复核中曾出现已有 Viewer 发现三相机 topic，但新启动独立 ROS 探针在 25 秒内未收到图像的失败。增加诊断后 `agenticros-check-c746bacc` 明确显示新探针的三路 publisher 数量均为 0：故障在冷 participant 的接口发现阶段，不能由另一节点的图判断该探针已发现。HTTP presence、状态 RPC 和 HTTP 快照下载通过，不是 HTTP 注册或图片 URL 失败。产品 Viewer/客户端使用常驻 participant；最终取帧验证改为经过这一实际路径，独立冷 participant 仍由 ROS RPC 冷启动测试单独计量。这个测试路径区别不代表冷图像发现缺陷已修复。另有 Viewer 受到无关 ROS 图变化和短暂 roster 缺失影响而反复配置的问题：现已按实际可视化配置及 session 决定重建，保留最近目录并显示过期提示，不把短暂缺样本当作机器人移除。

曾尝试去掉 SHM，仅使用 TCP：三容器 ALOHA 在 60 秒内无法发现全部接口，证据 `runs/checks/agenticros-check-9b16d9d3`。该实验被撤回。恢复统一配置后的 `agenticros-check-4a538e59` 首次及 Viewer 重启后的三路取帧均通过。失败记录还保留在 `agenticros-check-ea0b9978`、`agenticros-check-2942b63b`、`agenticros-check-23446768`。没有抓包证据可以把冷发现失败唯一归因于 SHM、丢包或某个 Fast DDS 内部缺陷；短期复核通过不能证明长期无间歇故障。另一次 TCP 重启探针在实际重启前，首次只读状态请求以 2 秒等待失败（`agenticros-check-70194b54`）；建立初始状态的等待现与普通 RPC 一致为 10 秒，停机期间的请求仍为 2 秒并计入失败。没有重发动作，也没有删掉该失败记录。

Fast DDS 的 SHM 主机判断与 Docker IPC 约束见[官方部署说明](https://fast-dds.docs.eprosima.com/en/v2.14.7/docker/shm_docker.html)。这只是配置依据；实际采用哪种传输以当前环境的结果为准，不把文档说明当作实测。

## 重现与后续 LAN 验收

```bash
bash scripts/test.sh
python3 scripts/check_lan.py --transport tcp
python3 scripts/check_lan.py --transport udp
python3 scripts/benchmark_presence.py
python3 scripts/check_viewer.py
python3 scripts/check_startup.py
python3 scripts/check_aloha.py --cartesian
python3 scripts/check_aloha.py
python3 scripts/audit_agent_image.py
python3 scripts/summarize_network.py runs/checks/具体测试/workspace/evidence
```

先按 README 构建三个镜像；测试只创建自身隔离容器，结束时移除这些测试容器/网络，保留证据与数据卷。`check_lan.py` 的 robot restart 和 Registry loss 是主动故障注入，不可指向正在执行用户任务的部署。

正式 LAN 上仍须固定 Server IPv4/域/传输，确认双向动态 TCP 数据端口及图像 HTTP 端口，分别测目录、发现、RPC、DDS 图像与 HTTP 下载；重复冷启动、机器人重启和长时间稳定运行。当前脚本自动拓扑是同机隔离测试，不能把它的成功直接作为该跨机器验收结果。对于大规模机器人、Wi-Fi、网络分区、系统时钟差异和真实控制器响应，本次均无数据。
