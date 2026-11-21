<!-- Historical commands below retain their paths at the time of validation.
Current entry points: scripts/lan.py -> scripts/runtime.py; scripts/test.sh -> tests/test.sh;
check scripts -> tests/ (check_capabilities.py -> tools/); audit/evaluation/migration/summary -> tools/.
agent-login.sh was replaced by scripts/runtime.py login -- --device-auth;
check_viewer.py was replaced by tests/check_lan.py --viewer-only --environment aloha_tabletop.
See README.md for current commands. -->

# 开发记录

本文汇总实现演进、验证数据和排障证据；当前操作命令统一见[使用说明](usage.md)。下文的通过数量、运行状态、镜像标签与部署状态均为对应开发阶段的记录，本次文档合并没有重新运行这些测试。

- [2026-11-21：实例拆分、域隔离与公开资料](#instance-validation)
- [2026-11-21：原生 ROS_DOMAIN_ID 网络迁移](#native-domain-network)
- [2026-11-20：固定发现服务下的直接 ROS 接入](#direct-ros-validation)
- [HTTP 注册与控制租约网络基线](#network-validation)
- [早期开发与验证历史](#early-history)

<a id="instance-validation"></a>

## 2026-11-21：实例拆分、域隔离与公开资料

Agent 与 Viewer 拆为独立镜像和容器，机器人使用自动 UUID 和稳定的 `/robot/<ID>` 名称空间，运行目录、账号卷及 Viewer 端口按 ROS_DOMAIN_ID 隔离。公开手册与接口统一放入 `robot_docs/` 并只读挂载给 Agent；Snapshot、HTTP 观测下载和 knowledge Service 已删除，观测改用原生 ROS 传感器话题。操作及迁移步骤见使用说明。

单机 Docker 容器验证不代表跨物理主机局域网或实机验证。使用独立测试标签、项目、域、工作区和卷，不替换现有运行实例。

本次验证通过：

- `AGENTICROS_TEST_IMAGE_TAG=split bash scripts/test.sh`：91 项测试通过。
- 自动 UUID、两个同类型机器人、Viewer 先于 Agent 启动、独立停止 Viewer、规则热同步：`runs/checks/agenticros-startup-check-548e149d/result.json`。
- 原生 ROS 发现与重启检查：`runs/checks/agenticros-native-ad8f22a9/`；历史动作、取消、session 与旧快照检查（快照接口现已删除）：`runs/checks/agenticros-check-6253de6f/`。
- 独立 Viewer 镜像显示 RViz 和三路相机；停止 Agent、重启 Viewer 后仍正常：`runs/checks/agenticros-check-697e74c6/viewer-evidence/`。
- Viewer 镜像没有 Codex、Agent 运行目录或工作区；Agent 镜像没有 Viewer、RViz 或 Viewer 口令目录。

本次域隔离验证：`scripts/check_domains.py --image-suffix split` 在域 90/91 同时启动 6 个容器，确认 ROS 图、工作区、Viewer 端口隔离，停止一个域后另一个域仍健康。证据：`runs/checks/domains-check-28f4362f/result.json`。新目录下的 UUID 启动、独立 Viewer、规则热同步检查通过，证据：`runs/checks/agenticros-startup-check-0459dbd5/result.json`。这些均为单机 Docker 验证。

robot_docs 验证：92 项回归测试通过；真实 Agent 容器验证只读挂载、宿主资料修改可见、两种公开接口包编译成功、knowledge Service 不存在，并继续通过 ROS 保存四路同步 RGB-D。证据：`runs/checks/agenticros-robot-docs-check-30b97a34/result.json`。Agent 镜像审计通过，测试仅使用单机隔离仿真容器。

<a id="native-domain-network"></a>

## 2026-11-21：原生 ROS_DOMAIN_ID 网络迁移

> 阶段记录：本节验证发生在移除 Snapshot/HTTP 观测之前；涉及快照地址、下载和当时部署状态的描述仅适用于该阶段。当前观测与实例布局见使用说明，后续验证见上节。

当前实现使用 ROS 2 Humble / Fast DDS 默认自动发现。删除固定 Discovery Server、动态 XML、TCP/WAN locator 配置及自编译 Fast DDS 补丁。Compose 保留 host 网络、host IPC 和异步发布，网络接入只配置 ROS_DOMAIN_ID；新配置默认 42，保存过的配置保留原域。

```bash
bash agent.sh agent --domain-id 42
bash robot.sh aloha_tabletop --domain-id 42
bash robot.sh cartesian_gripper --domain-id 42
```

机器人不依赖 Agent 先启动。保留 Agent、Viewer、常驻通用客户端、规则热同步及原有 session/UUID/取消/停止语义。快照 HTTP 服务独立于 ROS，默认自动选择地址与空闲端口；多网卡时可显式覆盖下载地址。

### 验证

在一台 macOS Docker Desktop 主机进行以下隔离检查，没有操作原有运行容器：

- Agent 测试镜像实际文件系统审计通过，未包含机器人私有源码/IDL；通信测试也核对了实际挂载。
- `AGENTICROS_TEST_IMAGE_TAG=native-domain bash scripts/test.sh`：86 项通过。
- Cartesian 原生 CLI：25/25 项通过，包含冷/热发现、状态、图像、并发服务、机器人重启及 Agent 重启；证据 `runs/checks/agenticros-native-273d5a42/`。
- ALOHA 原生 CLI：8/8 项通过；证据 `runs/checks/agenticros-native-103ef67f/`。
- 两组 CLI 检查的默认冷启动列表均无漏项；样本量有限，不代表发现时间保证。
- 独立 Agent + 两个 Cartesian 容器：快照文件下载（自动地址/端口）、会话检查、动作互斥、取消核对、机器人重启和常驻客户端重载通过；证据 `runs/checks/agenticros-check-cb24dfe7/`。
- Compose host 启动、机器人先于 Agent、重复启动保持原容器、规则热同步通过；证据 `runs/checks/agenticros-startup-check-eaed6f59/`。

原生 CLI/通信检查在独立 Docker bridge 网络的三个不同网络名称空间运行，Compose 启动检查使用 host 网络。它们不证明跨物理主机 LAN、真实硬件或自主任务成功。目标 Linux 局域网仍需验证组播、UDP、防火墙与快照地址可达性。

### 部署边界

本次只修改源码并构建 `:native-domain` 测试镜像，没有替换 `:single` 运行实例，没有提交或推送。旧实例仍使用旧网络，需停止任务后按 usage.md 显式重建和 recreate。旧固定发现报告属于历史证据，不能作为当前原生网络的验证结果。

<a id="direct-ros-validation"></a>

## 2026-11-20：固定发现服务下的直接 ROS 接入

> 历史方案：本节的固定 Discovery Server、TCP 补丁、Snapshot/HTTP 下载及复现参数已被后续实现替代；保留完整测量与失败记录，不作为当前部署说明。

测试日期：2026-11-20。结果限定为一台 macOS 主机上的 Docker Desktop；没有真实硬件动作，没有跨物理机器或原生 Linux 主机验证。所有动作在新建隔离仿真中执行，账号、已有工作区、runs 和数据卷未清理。

### 实现与故障定位

- 删除 HTTP 注册、心跳和控制租约。机器人只需要固定 Server IPv4、唯一 robot_id 和相同 ROS_DOMAIN_ID / DDS 配置；不需要 Server 在线才能启动本地驱动。
- 原生 ROS CLI、订阅、Service 和 Action 直接使用机器人公开接口；常驻通用客户端仍供子 agent 复用，但不参与原生 CLI 验收。
- 保留固定 Discovery Server。TCP 模式的冷发现与重连分别有两个问题，不能归为笼统的“网络不稳定”。

**冷启动：BACKUP 持久化开销。** 旧镜像在 ALOHA 双机器人、多相机图下出现高 CPU 和持续冷发现超时。只把 `BACKUP` 改为 `SERVER` 的对照由 8/40 成功提高至 40/40；该对照是同机独立容器，不能外推为 LAN 提速幅度。Fast DDS 2.6 的 BACKUP 路径同步维护发现数据库及持久化队列；当前临时 ROS 图可重建，因此不再持久化。数据卷和旧数据库均保留。tmpfs、单独命名卷、SQLite WAL 对照未同时满足稳定性与重启验收，未采用。

**发现服务重启：两层问题。** 仅使用 `RemoteServer` 时，参与者移除会清理 TCP 发送资源；因此固定地址同时加入 `initialPeersList`，并统一普通/WAN locator。小样本曾通过 43/43，但扩大到 10 轮、连续重启后再次失败。该失败也保留，不能用小样本宣称修复完成。

进一步使用同版本 Fast DDS 2.6.12 源码诊断构建，在两个容器同时记录 TCP/RTCP 日志，复现了如下时序：

1. 发现服务重启，TCP 监听已接受机器人连接；
2. 机器人请求打开逻辑端口 7410；Server 返回 `Not found: 7410`，此时 DDS input 尚未初始化；
3. 客户端保留 pending port，并扫描其他逻辑端口；后续 send 看到它已 pending，不再协商原来的端口；
4. TCP 连接保持建立，但新原生 CLI 无法发现机器人。已有数据连接仍能工作。

这不是 HTTP 注册故障，也不是只能用常驻客户端规避的冷发现问题。新增一个同版本、局部、可审计的 Fast DDS 补丁：统一 profile 配置 `max_logical_port=0`，按公开的准确逻辑端口协商；被拒绝后，在后续发送中最多每 100 ms 重试一次协商，保持单个在途协商，重连清除旧协商事务。没有动作/RPC重放。其他配置保留上游扫描行为。所有镜像构建相同 SHA-256 固定源码与补丁，运行时不包含诊断日志或中间源码；首次构建多一个中间件编译阶段，后续共享缓存。补丁和构建测试说明原位于 `docker/shared/fastdds/README.md`，该方案现已删除，可从 Git 历史追溯。

底层确定性测试故意让逻辑端口比 TCP 监听晚 1.5 秒开放：未修补版本在 3 秒恢复窗口内失败；诊断修补版连续 5 次恢复，每次在首个 50 ms 轮询间隔内观察到成功。三容器诊断修补版通过 5 次发现服务重启和整个 Agent 重启；诊断耗时不用于宣称最终性能。正式 Release 镜像扩大验证结果单列。

参考：[TCP 发送与清理](https://github.com/eProsima/Fast-DDS/blob/v2.6.12/src/cpp/rtps/transport/TCPTransportInterface.cpp)、[pending port 状态](https://github.com/eProsima/Fast-DDS/blob/v2.6.12/src/cpp/rtps/transport/TCPChannelResource.cpp)、[上游保留 pending port 的背景](https://github.com/eProsima/Fast-DDS/pull/4324)。本补丁是仓库本地修复，不冒称上游已合并。

未采用的对照包括：只删除 loopback、纯 TCP 不带 SHM、显式 WAN locator、调整 logical port、25 秒断开等待、缩短 DDS lease、65000 高端口、原生 Discovery Server CLI。这些没有解决复现故障。固定 UDP 的独立容器小样本通过；但 macOS 实际主机 LAN 地址下的 UDP/UDP发现+TCP数据对照出现图可见而状态或服务不可用，MTU/缓冲区调整仍未同时通过。保留失败日志，部署仍统一 TCP 固定发现，没有切换自动发现；这些同机 UDP 失败也不能外推为真实跨机结果。

**HTTP 与数据分层。** 旧 HTTP 租约会在机器人立即重启时返回 409，造成与 DDS 不同的接入阻塞；该路径已删除。图像快照的签名 HTTP 文件下载仍保留，和 ROS 图像订阅分别验证。ROS 图已发现、服务存在、服务实际回应、状态新鲜和控制空闲分别报告。

**离线动作测试的时间预算修正。** 首次 TCP 集成测试在外层 40 秒等待上限结束，不能据此判断通信失败。保留夹具后的诊断中，发现服务离线期间已有 status RPC 正常回应；30 秒仿真动作在约 41.9 秒墙钟时间返回 `complete=true`、`stop_confirmed=true`。外层测试等待改为与既有 120 秒结果超时一致，并增加状态接收年龄和结果到达时间记录。动作请求仍只发送一次；未改变驱动执行、自动重试或生产超时策略。失败运行及诊断回执见[证据目录](evidence/direct-ros-network/lan-outage-time-diagnosis/)。最终完整重跑结果单列。

**同机地址归一化：旧节点的新端点无法传出。** 一次恢复后 status 请求在 45 秒内没有响应。扩大为每次 2 秒、总窗口 30 秒的只读恢复采样后，运行 `4889825c` 和诊断运行 `e8eded64` 再次失败：旧节点已有的 robot_a 客户端正常，新建 robot_b 客户端连续 15 次请求超时。诊断驱动在该窗口没有记录 status 回调；随后全新原生 `ros2 service call` 成功。进一步在 `284553cc` 的旧节点记录到 Fast DDS `transform_remote_locator` 将同机 Server 地址转换为 `127.0.0.1`；`CloseOutputChannel` 的保留列表仅包含 LAN 普通/WAN 地址，随后明确记录 `CLEAN ERASE TCPv4:[127.0.0.1]:11811-0`。因此已有机器人数据通道继续工作，新端点发现却无法发到 Server。现在仅在 advertised_host 等于 Server 地址时加入 loopback initial peer，保留该优化产生的发送资源；不向远端公布 loopback，也不切换 DDS 配置。两次完整 TCP 修复后运行（`2fb4b5d7`、`84fa4abd`）中，两台机器人的恢复后首次 status 均成功，无需诊断重试；第二次的全部样本见下表。所有重试仅为只读诊断，动作、取消和结果请求均不重放。[地址清理 trace](evidence/direct-ros-network/loopback-discovery-cleanup/trace-excerpt.txt)与[长中断失败样本](evidence/direct-ros-network/long-outage-failure-e8eded64/)。

**RViz 首条状态触发重复启动。** 最终 UI 检查捕获到图已完整、相机已收到数据，但 session 从未知变为首次已知时面板又重建一次。现在把首次获知身份、状态暂时变旧与真正 session 变化分开：前两者不重启 RViz，已知 session 变更仍清理旧视图。新增 UI 回归覆盖该顺序；修复后 Viewer 生命周期、三路实际图像与面板稳定性均通过，并人工检查了桌面截图。该 UI 修复发生在最终网络验收之后，仅重建 Server 的 Viewer 层；中间件及网络 profile 未再改变，各运行的精确镜像 ID 保留在 topology.json。保留[失败事件](evidence/direct-ros-network/viewer-session-learning-failure/events.jsonl)。

### 测量方法

`check_native_ros.py` 使用一个 Agent 容器和两个机器人容器，三个不同 IP、网络名称空间和私有 IPC；不以单容器代替通信验证。测试域独立，禁用对外路由。另用 `check_startup.py` 测实际 Compose host 网络和主机 LAN 地址回环；仍只有一台物理机器。

每轮记录：停 CLI daemon 后原生 `ros2 topic list`、新进程 `topic list --no-daemon --spin-time 3`、原生状态与压缩图像订阅、原生 status Service、两个机器人并发 Service。重启验证包含原有订阅跨机器人重启、新 session，以及发现服务进程和整个 Agent 容器重启后的新 CLI 调用。

Humble `ros2 topic list` 默认冷启动等待窗口为 0.5 秒，可能返回不完整图快照。保留其全部缺失样本；常驻/daemon 缓存不是验收替代品。必须通过 3 秒窗口的新 participant 检查、数据订阅和 RPC；`--strict-default` 可让默认窗口的任何缺失也导致测试失败。默认窗口和 3 秒窗口的结果分开报告。统一 DDS 同步周期从默认 450 ms 改为 100 ms，但不承诺 0.5 秒内获得完整动态图。

原生 CLI 的 `total_ms` 包含入口脚本、Python/ROS 初始化、发现和退出；Service 的 `discovery_ms` 是启动至 CLI 打印发送请求的时间上界，`response_ms` 是打印发送至打印响应。它们不是纯 DDS 发现耗时。`network_probe.py` 另测 `wait_for_service` 与 RPC；常驻客户端统计另列。原生 CLI 分位数采用 nearest-rank（另一套 RPC 汇总 P50 为中位数），失败耗时也保留，不能仅用成功样本掩盖超时。

`discovery_failure` 表示图缺项或服务未进入发送阶段；`request_timeout` 表示有界请求/样本等待到期（订阅未收到样本时不能断言已完成匹配）；`server_failure` 表示调用返回失败或执行异常。每组样本数、成功率、P50/P95/最大值见以下结果及 JSON。

<!-- RESULTS -->

### 原生 CLI 实测结果

每个耗时单元格依次为 P50 / P95 / 最大值（ms）；原始样本与各阶段计时样本数保留在 evidence JSON。最初旧版基线仅采集总耗时，分段时间未采集；另用保留的冻结旧镜像补采相同分段计时作为对照。尚未发送的服务请求没有响应时间，不能把缺失值填成零。

| 场景 | 成功 / N（率） | 失败：发现 / 超时 / 服务端 | 总耗时 ms | 发现上界 ms | 响应 ms |
| --- | ---: | --- | --- | --- | --- |
| 旧版 ALOHA · cold/image | 3/5 (60%) | 0 / 2 / 0 | 9345.59 / 12587.02 / 12587.02 | — | — |
| 旧版 ALOHA · cold/service | 2/5 (40%) | 2 / 1 / 0 | 12508.22 / 12629.79 / 12629.79 | — | — |
| 旧版 ALOHA · cold/state | 3/5 (60%) | 0 / 2 / 0 | 11968.89 / 12592.95 / 12592.95 | — | — |
| 旧版 ALOHA · cold_daemon/list | 0/5 (0%) | 5 / 0 / 0 | 1638.30 / 1882.11 / 1882.11 | — | — |
| 旧版 ALOHA · cold_direct/list | 0/5 (0%) | 5 / 0 / 0 | 4042.25 / 4404.04 / 4404.04 | — | — |
| 旧版 ALOHA · concurrent/service | 0/10 (0%) | 10 / 0 / 0 | 12737.62 / 12890.64 / 12890.64 | — | — |
| 旧版 ALOHA · warm_daemon/list | 0/5 (0%) | 5 / 0 / 0 | 706.37 / 908.38 / 908.38 | — | — |
| 新版 ALOHA · agent_restart/list | 10/10 (100%) | 0 / 0 / 0 | 3665.62 / 3925.68 / 3925.68 | — | — |
| 新版 ALOHA · agent_restart/service | 10/10 (100%) | 0 / 0 / 0 | 1234.66 / 1983.01 / 1983.01 | 1054.77 / 1831.97 / 1831.97 | 2.90 / 133.44 / 133.44 |
| 新版 ALOHA · cold/image | 10/10 (100%) | 0 / 0 / 0 | 2156.31 / 3172.46 / 3172.46 | — | — |
| 新版 ALOHA · cold/service | 10/10 (100%) | 0 / 0 / 0 | 1293.71 / 2168.12 / 2168.12 | 1024.58 / 1659.06 / 1659.06 | 132.68 / 673.50 / 673.50 |
| 新版 ALOHA · cold/state | 10/10 (100%) | 0 / 0 / 0 | 1449.46 / 6583.12 / 6583.12 | — | — |
| 新版 ALOHA · cold_daemon/list | 8/10 (80%) | 2 / 0 / 0 | 1382.11 / 1759.33 / 1759.33 | — | — |
| 新版 ALOHA · cold_direct/list | 10/10 (100%) | 0 / 0 / 0 | 3827.34 / 4272.05 / 4272.05 | — | — |
| 新版 ALOHA · concurrent/service | 20/20 (100%) | 0 / 0 / 0 | 1258.51 / 1638.11 / 1774.41 | 1089.99 / 1481.60 / 1591.54 | 3.26 / 89.11 / 113.06 |
| 新版 ALOHA · discovery_restart/list | 50/50 (100%) | 0 / 0 / 0 | 3668.15 / 4216.41 / 4531.01 | — | — |
| 新版 ALOHA · discovery_restart/service | 50/50 (100%) | 0 / 0 / 0 | 1271.63 / 1717.53 / 1844.57 | 1066.83 / 1520.99 / 1659.38 | 3.07 / 223.55 / 306.27 |
| 新版 ALOHA · robot_restart/existing_subscription | 1/1 (100%) | 0 / 0 / 0 | 3503.24 / 3503.24 / 3503.24 | — | — |
| 新版 ALOHA · robot_restart/list | 10/10 (100%) | 0 / 0 / 0 | 3655.29 / 3997.73 / 3997.73 | — | — |
| 新版 ALOHA · robot_restart/service | 10/10 (100%) | 0 / 0 / 0 | 1289.89 / 3176.78 / 3176.78 | 1090.78 / 2542.41 / 2542.41 | 4.57 / 381.77 / 381.77 |
| 新版 ALOHA · warm_daemon/list | 10/10 (100%) | 0 / 0 / 0 | 432.25 / 473.56 / 473.56 | — | — |
| 新版 Cartesian · agent_restart/list | 5/5 (100%) | 0 / 0 / 0 | 3518.91 / 3859.10 / 3859.10 | — | — |
| 新版 Cartesian · agent_restart/service | 5/5 (100%) | 0 / 0 / 0 | 899.33 / 1344.36 / 1344.36 | 748.03 / 1205.76 / 1205.76 | 54.96 / 128.98 / 128.98 |
| 新版 Cartesian · cold/image | 5/5 (100%) | 0 / 0 / 0 | 1426.01 / 1611.10 / 1611.10 | — | — |
| 新版 Cartesian · cold/service | 5/5 (100%) | 0 / 0 / 0 | 1141.89 / 1561.90 / 1561.90 | 836.14 / 1184.34 / 1184.34 | 232.96 / 404.18 / 404.18 |
| 新版 Cartesian · cold/state | 5/5 (100%) | 0 / 0 / 0 | 1048.44 / 1343.41 / 1343.41 | — | — |
| 新版 Cartesian · cold_daemon/list | 3/5 (60%) | 2 / 0 / 0 | 959.58 / 1399.90 / 1399.90 | — | — |
| 新版 Cartesian · cold_direct/list | 5/5 (100%) | 0 / 0 / 0 | 3488.77 / 4019.23 / 4019.23 | — | — |
| 新版 Cartesian · concurrent/service | 10/10 (100%) | 0 / 0 / 0 | 999.63 / 1186.74 / 1186.74 | 833.85 / 994.32 / 994.32 | 30.41 / 231.88 / 231.88 |
| 新版 Cartesian · discovery_restart/list | 5/5 (100%) | 0 / 0 / 0 | 3492.73 / 3815.82 / 3815.82 | — | — |
| 新版 Cartesian · discovery_restart/service | 5/5 (100%) | 0 / 0 / 0 | 1008.06 / 1298.29 / 1298.29 | 827.64 / 1117.49 / 1117.49 | 73.15 / 132.33 / 132.33 |
| 新版 Cartesian · robot_restart/existing_subscription | 1/1 (100%) | 0 / 0 / 0 | 1750.79 / 1750.79 / 1750.79 | — | — |
| 新版 Cartesian · robot_restart/list | 5/5 (100%) | 0 / 0 / 0 | 3548.36 / 4183.19 / 4183.19 | — | — |
| 新版 Cartesian · robot_restart/service | 5/5 (100%) | 0 / 0 / 0 | 1252.69 / 1331.09 / 1331.09 | 1117.83 / 1189.60 / 1189.60 | 1.02 / 1.27 / 1.27 |
| 新版 Cartesian · warm_daemon/list | 5/5 (100%) | 0 / 0 / 0 | 222.43 / 232.42 / 232.42 | — | — |

### 冻结旧镜像的分段计时补采

原始修改前基线仍见上表。补采使用保留的 `:native-baseline` 镜像和当前计时脚本，40 项均失败；服务调用没有进入发送阶段，因此发现完成上界和响应耗时均不可测。`—` 不是零延迟。

| 阶段 / 操作 | 成功 / N | 发现失败 / 请求超时 / 服务端失败 | 总耗时 P50 / P95 / 最大值 ms |
| --- | ---: | --- | --- |
| cold/image | 0/5 | 0 / 5 / 0 | 12567.76 / 12613.11 / 12613.11 |
| cold/service | 0/5 | 5 / 0 / 0 | 12661.61 / 12824.66 / 12824.66 |
| cold/state | 0/5 | 0 / 5 / 0 | 12452.59 / 12666.08 / 12666.08 |
| cold_daemon/list | 0/5 | 5 / 0 / 0 | 1319.71 / 1648.33 / 1648.33 |
| cold_direct/list | 0/5 | 5 / 0 / 0 | 3752.46 / 3885.93 / 3885.93 |
| concurrent/service | 0/10 | 10 / 0 / 0 | 12573.92 / 12914.95 / 12914.95 |
| warm_daemon/list | 0/5 | 5 / 0 / 0 | 516.95 / 544.45 / 544.45 |

### lan-tcp：常驻、独立 ROS 进程与图像下载

这里的发现时间是 `wait_for_service`，不是原生 CLI 的启动上界；P50 为中位数，P95 为 nearest-rank。重启停机窗口中的失败计入，不当作正常运行失败或删除。

| 层 / 阶段 / 操作 | 成功 / N（率） | 错误 | 发现 ms | 响应 ms | 总耗时 ms |
| --- | ---: | --- | --- | --- | --- |
| image_download / concurrent /  | 5/5 (100%) | {} | — | 0.71 / 1.80 / 1.80 | — |
| image_download / warm /  | 5/5 (100%) | {} | — | 0.90 / 4.20 / 4.20 | — |
| resident_cli_cold / resident /  | 10/10 (100%) | {} | 0.03 / 0.04 / 0.04 | 0.91 / 1.38 / 1.38 | 20.64 / 25.14 / 25.14 |
| resident_cli_cold / restarted /  | 2/2 (100%) | {} | 0.05 / 0.05 / 0.05 | 0.94 / 0.97 / 0.97 | 24.97 / 27.88 / 27.88 |
| resident_rpc / resident /  | 61/61 (100%) | {} | 0.01 / 0.02 / 0.14 | 0.70 / 2.78 / 152.77 | 0.95 / 3.27 / 170.96 |
| resident_rpc / restarted /  | 10/10 (100%) | {} | 0.02 / 0.07 / 0.07 | 1.15 / 90.72 / 90.72 | 1.42 / 101.27 / 101.27 |
| ros_data / cold / status | 10/10 (100%) | {} | 506.37 / 756.57 / 756.57 | 50.15 / 504.36 / 504.36 | — |
| ros_data / concurrent / observe | 5/5 (100%) | {} | 0.03 / 0.04 / 0.04 | 94.97 / 160.88 / 160.88 | — |
| ros_data / concurrent / status | 30/30 (100%) | {} | 0.01 / 754.35 / 758.08 | 0.71 / 316.88 / 337.40 | — |
| ros_data / discovery_restart / status | 2/2 (100%) | {} | 0.16 / 0.28 / 0.28 | 291.94 / 581.36 / 581.36 | — |
| ros_data / restart_initial /  | 1/1 (100%) | {} | 0.02 / 0.02 / 0.02 | 183.46 / 183.46 / 183.46 | — |
| ros_data / robot_restart /  | 12/13 (92%) | {"request_timeout": 1} | 0.01 / 0.45 / 0.45 | 0.65 / 2000.32 / 2000.32 | — |
| ros_data / warm / observe | 5/5 (100%) | {} | 0.03 / 0.03 / 0.03 | 97.12 / 165.85 / 165.85 | — |
| ros_data / warm / status | 30/30 (100%) | {} | 0.01 / 0.02 / 752.45 | 0.55 / 0.67 / 1.48 | — |

### lan-udp：常驻、独立 ROS 进程与图像下载

这里的发现时间是 `wait_for_service`，不是原生 CLI 的启动上界；P50 为中位数，P95 为 nearest-rank。重启停机窗口中的失败计入，不当作正常运行失败或删除。

| 层 / 阶段 / 操作 | 成功 / N（率） | 错误 | 发现 ms | 响应 ms | 总耗时 ms |
| --- | ---: | --- | --- | --- | --- |
| image_download / concurrent /  | 5/5 (100%) | {} | — | 1.49 / 3.46 / 3.46 | — |
| image_download / warm /  | 5/5 (100%) | {} | — | 0.69 / 2.35 / 2.35 | — |
| resident_cli_cold / resident /  | 10/10 (100%) | {} | 0.03 / 0.04 / 0.04 | 0.91 / 3.07 / 3.07 | 21.40 / 27.61 / 27.61 |
| resident_cli_cold / restarted /  | 2/2 (100%) | {} | 0.05 / 0.06 / 0.06 | 26.06 / 51.17 / 51.17 | 49.73 / 77.38 / 77.38 |
| resident_rpc / resident /  | 41/41 (100%) | {} | 0.01 / 0.04 / 0.21 | 0.77 / 7.87 / 151.82 | 1.10 / 8.25 / 170.24 |
| resident_rpc / restarted /  | 10/10 (100%) | {} | 0.02 / 0.07 / 0.07 | 0.89 / 96.80 / 96.80 | 1.23 / 97.73 / 97.73 |
| ros_data / cold / status | 5/5 (100%) | {} | 251.75 / 255.39 / 255.39 | 1.85 / 2.62 / 2.62 | — |
| ros_data / concurrent / observe | 5/5 (100%) | {} | 0.03 / 0.05 / 0.05 | 97.47 / 158.12 / 158.12 | — |
| ros_data / concurrent / status | 20/20 (100%) | {} | 0.02 / 251.57 / 251.79 | 1.08 / 2.19 / 2.53 | — |
| ros_data / discovery_restart / status | 2/2 (100%) | {} | 0.33 / 0.64 / 0.64 | 141.47 / 228.85 / 228.85 | — |
| ros_data / restart_initial /  | 1/1 (100%) | {} | 0.01 / 0.01 / 0.01 | 56.90 / 56.90 / 56.90 | — |
| ros_data / robot_restart /  | 12/13 (92%) | {"request_timeout": 1} | 0.01 / 0.09 / 0.09 | 0.64 / 2000.44 / 2000.44 | — |
| ros_data / warm / observe | 5/5 (100%) | {} | 0.04 / 0.04 / 0.04 | 94.76 / 164.80 / 164.80 | — |
| ros_data / warm / status | 20/20 (100%) | {} | 0.01 / 0.01 / 250.78 | 0.54 / 0.64 / 1.19 | — |

### 回归与覆盖

| 检查 | 结果 |
| --- | --- |
| startup-lan | PASS |
| native-aloha-final | PASS |
| native-cartesian | PASS |
| lan-tcp | PASS |
| lan-udp | PASS |
| viewer | PASS |
| startup | PASS |
| aloha-cartesian | PASS |
| aloha-raw | PASS |
| image-audit | PASS |
| regression | PASS |

仓库回归共 88 项通过。

动作验证包括：双机器人并发、单臂互斥、UUID 防重放、取消后停止确认、结果超时后的状态核对、机器人重启拒绝旧 action/Snapshot session。发现服务离线时已接受的有限仿真动作继续由本地驱动执行；这与旧租约中断语义不同。

ALOHA `move_pose` / `move_delta` 与双臂共享世界使用单容器、无网络的隔离逻辑回归；它们不替代网络验收。ALOHA 原生图/状态/图像/Service 以及 RViz 三路相机在独立容器间检查；跨容器动作安全检查使用 Cartesian Gripper。未把这些结果冒称 ALOHA 笛卡尔动作的跨机验收。Compose 检查先启动机器人，再启动 Server；重复启动不替换容器，并验证规则热同步。镜像审计检查 Agent 无设备专用 IDL、任务程序或私有仿真源码。

### 复现与限制

```bash
bash scripts/test.sh
python3 scripts/check_native_ros.py --environment aloha_tabletop --samples 10 --restart --discovery-cycles 5
python3 scripts/check_native_ros.py --samples 5 --restart
python3 scripts/check_lan.py --samples 30 --cold-samples 10
python3 scripts/check_lan.py --transport udp --samples 20 --cold-samples 5
python3 scripts/check_viewer.py
python3 scripts/check_startup.py
python3 scripts/check_startup.py --host "$SERVER_IP"  # 同机实际 LAN 地址回环
python3 scripts/check_aloha.py --cartesian
python3 scripts/check_aloha.py
python3 scripts/audit_agent_image.py
```

[证据索引](evidence/direct-ros-network/trials.json)记录原始 runs 路径；[验收退出码](evidence/direct-ros-network/acceptance.json)与各组 JSON 可复核。旧镜像基线保留为本机 `:native-baseline`，可用 `check_native_ros.py --baseline --image-suffix native-baseline` 复现历史 HTTP 接入；该选项仅用于基线测试，不是产品兼容层。

未覆盖：跨物理机器 LAN、原生 Linux 宿主机、真实机器人、Wi-Fi/丢包/网络分区、长期运行、大规模机器人及硬件安全停机。三个网络名称空间证明跨容器通信；host/LAN 地址回环仍不证明跨机器。默认 0.5 秒冷 CLI 列表可能不完整，不能承诺任意时刻一次 list 都包含全部动态接口。没有用常驻进程通过代替独立冷启动通过，也没有预先承诺或外推加速幅度。

<a id="network-validation"></a>

## HTTP 注册与控制租约网络基线

> 历史方案：本节对应 HTTP presence/控制租约版本，包含已移除的接口、脚本和复现参数；仅作为基线证据。后续直接 ROS 接入见前两节。

本报告对应 `codex/single-agent-network`，基线为 `origin/main` 的 `b58a03a`。先完成基线采样，再修改架构。完整原始记录留在本工作树忽略的 `runs/`；可审查的指标、拓扑、源码校验和测试结果保存在 [evidence/single-agent-network](evidence/single-agent-network/manifest.json)。指标文件仅保留计时、结果与错误类别，不提交账号、租约密钥或签名下载 URL。

### 环境与可比范围

- 一台 macOS 主机上的 Docker Desktop Linux VM，10 个逻辑 CPU、约 7.75 GiB 内存。网络集成使用一个 Server 和两个独立机器人容器，三个不同 IP、三个独立网络名称空间、私有 IPC，实测跨容器 DDS 与 HTTP，不是单容器自测。
- 基线和修改后均使用 Cartesian Gripper、固定 TCP Discovery Server。同一机器、不同时间的顺序采样，不是统计显著性实验。ALOHA 动作另在 `--network none` 的隔离容器验证；ALOHA Viewer 使用三容器链路。
- 未操作真实硬件，没有使用用户账号运行自主任务，不声称完成自主抓取/交接或真实机器人验收。原工作树未提交修改保持原样。
- **没有跨物理主机验证，也没有原生 Linux 主机验证。** Docker Linux 容器通过不等于两台 Linux/macOS 主机的 LAN 通过。macOS host 网络另做双向 nonce 预检，不等于完整跨机器 DDS 验证。

### 测量定义

`discovery_ms` 是已有节点创建/取得客户端后，`wait_for_service` 的等待时间；不含 Python 导入与整个 participant 初始化。`response_ms` 是调用至收到响应的时间。`total_ms` 是常驻 socket 或冷 CLI 的端到端时间。相同指标才能比较。`warm` 包含第一个未预热调用；`cold` 每次重新启动 ROS 进程。`resident_cli_cold` 重启薄 CLI，但复用常驻 ROS worker，不能冒充冷 ROS participant。

P50 为中位数，P95 为 nearest-rank，最大值不剔除首样本或离群值；超时样本如有计时也纳入分布。N=10 的 P95 就是最大值。`registration` 行实际是 HTTP `/robots` 目录 GET，首次申请和续租单独测为 `http_enrollment`、`http_renewal`。

发现超时归 `discovery_failure`；发现成功但等待响应超时归 `request_timeout`；服务端否定/异常归 `server_failure`。图像 HTTP 下载单独记录，并核对 SHA-256；它与 DDS 相机订阅是两条链路。机器人停机期间的预期失败不从结果中删除。

### 实测结果

| 场景 | 成功 / N | 发现 ms：P50 / P95 / 最大 | 响应 ms：P50 / P95 / 最大 | 端到端 ms：P50 / P95 / 最大 |
| --- | ---: | --- | --- | --- |
| 基线 · 连续 | 50 / 50 | 0.009 / 0.039 / 1003.020 | 0.580 / 6.285 / 556.581 | — |
| 修改后 TCP · 连续 | 50 / 50 | 0.018 / 0.058 / 4517.240 | 1.015 / 2.916 / 3418.058 | — |
| 基线 · 双机器人并发 | 50 / 50 | 0.022 / 0.062 / 1006.425 | 177.101 / 264.516 / 371.524 | — |
| 修改后 TCP · 双机器人并发 | 50 / 50 | 0.019 / 0.043 / 1254.978 | 1.200 / 119.798 / 135.817 | — |
| 基线 · 新 ROS 进程 | 10 / 10 | 1002.283 / 1254.632 / 1254.632 | 526.958 / 1530.778 / 1530.778 | — |
| 修改后 TCP · 新 ROS 进程 | 10 / 10 | 1632.122 / 4685.915 / 4685.915 | 1820.575 / 4024.761 / 4024.761 | — |

上述正常状态调用的发现失败、请求超时、服务端失败均为 0。连续/并发的第一笔也计入；基线和修改后都达到这些样本上的 100%，不能据此声称成功率提升。**本轮独立 ROS 冷启动明显更慢，不支持总体加速结论。** 固定 worker 的价值是让日常查询避免反复建立 participant；同时保留超时及未知状态语义。

#### 常驻客户端（TCP）

| 场景 | 成功 / N | 发现 ms：P50 / P95 / 最大 | 响应 ms：P50 / P95 / 最大 | 端到端 ms：P50 / P95 / 最大 |
| --- | ---: | --- | --- | --- |
| 常驻 · socket RPC | 101 / 101 | 0.017 / 0.039 / 0.081 | 1.164 / 1.919 / 104.294 | 1.670 / 4.551 / 130.314 |
| 常驻 · 薄 CLI 冷启动 | 10 / 10 | 0.073 / 0.119 / 0.119 | 2.346 / 100.166 / 100.166 | 48.255 / 139.701 / 139.701 |
| worker 重载后 · socket RPC | 10 / 10 | 0.021 / 501.130 / 501.130 | 1.480 / 1072.581 / 1072.581 | 2.149 / 1618.412 / 1618.412 |
| worker 重载后 · 薄 CLI 冷启动 | 2 / 2 | 0.075 / 0.077 / 0.077 | 2.352 / 2.787 / 2.787 | 43.600 / 47.403 / 47.403 |

常驻阶段 101 笔 RPC 包含 50 笔连续、50 笔双机器人并发和 1 笔动作前状态查询；重载后 10 笔包含 5 连续、4 并发和 1 状态查询。所有行均无发现失败、请求超时或服务端失败；UUID 防重放和超时核对另有动作断言。

#### HTTP 注册、ROS 观测和图像下载分层

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

#### UDP 对照与重启故障注入

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

#### 验收记录

回归 **92 项通过**；TCP/UDP 三容器动作与重连、两套 ALOHA 集成、Viewer 两次生命周期、实际 host-network Compose 启动、规则热同步和镜像审计均通过。镜像审计扫描 61,951 个文件；[源码与镜像核对](evidence/single-agent-network/source-verification.json)记录了实际镜像 ID。

[原生 RViz 桌面截图](evidence/single-agent-network/viewer-desktop.png)已目视确认三路相机面板均有实际图像及点云。最后一次常驻三路首次取帧整体等待为 1,720.581 ms（N=1 个三路检查窗口，不作分位数推断）。



### 覆盖的行为

- 常驻 Service Client 连续调用、两个机器人并发查询；十次新的 ROS participant 冷启动；薄 CLI 冷启动复用 worker；worker 重载后复用已有工作区绑定。
- 机器人进程重启时名称空间保持不变，session 改变；旧 action 与旧 Snapshot session 被拒绝；同一个测试客户端恢复查询并执行新 session 的隔离仿真动作。
- 跨机器人 session 拒绝、动作 UUID、单臂互斥、取消回执、实测停止、动作结果超时后查询状态。通信 worker 重启后，持久发送日志继续拒绝同一 UUID，绝不自动重发。
- Registry 停止期间，运行中的仿真动作停止；Registry 恢复后旧控制许可不复活。进程存活与控制租约分开报告。
- ALOHA `actuate`、`move_pose`、`move_delta` 共用每臂执行门；双臂共享物理世界；取消、超限/错误 frame 拒绝、恢复及故障状态检查。
- Viewer 测试通过产品常驻客户端实际接收三路压缩相机图像，生成三个原生 RViz Image 面板及原生 Qt dock 布局；检查十二个公开 action 接口、VNC 鉴权、Viewer 停启保留机器人 session/口令、单 Agent 锁。图像到达后检查 RViz PID 稳定六秒。截图已目视确认本次三面板有实际图像；未做长期帧率或画质验收。
- 规则热同步、模型连接配置、工作区权限/迁移保留、UUID 竞争、超时分类、不可把发现当作控制许可等回归测试。镜像文件系统审计确认 Server 不含设备 IDL、私有仿真资产或任务适配程序。

### DDS 选择与排障记录

保留固定 Discovery Server；TCP 继续使用同一份 TCP+SHM profile，host 网络对应 host IPC，独立网络名称空间之间走 TCP。未改成 multicast 自动发现，也不允许任务 agent 临时改域/传输配置。UDP 仅作为显式统一配置的 Linux 候选，在同机隔离容器实测；macOS 启动入口仍拒绝 UDP。

开发复核中曾出现已有 Viewer 发现三相机 topic，但新启动独立 ROS 探针在 25 秒内未收到图像的失败。增加诊断后 `agenticros-check-c746bacc` 明确显示新探针的三路 publisher 数量均为 0：故障在冷 participant 的接口发现阶段，不能由另一节点的图判断该探针已发现。HTTP presence、状态 RPC 和 HTTP 快照下载通过，不是 HTTP 注册或图片 URL 失败。产品 Viewer/客户端使用常驻 participant；最终取帧验证改为经过这一实际路径，独立冷 participant 仍由 ROS RPC 冷启动测试单独计量。这个测试路径区别不代表冷图像发现缺陷已修复。另有 Viewer 受到无关 ROS 图变化和短暂 roster 缺失影响而反复配置的问题：现已按实际可视化配置及 session 决定重建，保留最近目录并显示过期提示，不把短暂缺样本当作机器人移除。

曾尝试去掉 SHM，仅使用 TCP：三容器 ALOHA 在 60 秒内无法发现全部接口，证据 `runs/checks/agenticros-check-9b16d9d3`。该实验被撤回。恢复统一配置后的 `agenticros-check-4a538e59` 首次及 Viewer 重启后的三路取帧均通过。失败记录还保留在 `agenticros-check-ea0b9978`、`agenticros-check-2942b63b`、`agenticros-check-23446768`。没有抓包证据可以把冷发现失败唯一归因于 SHM、丢包或某个 Fast DDS 内部缺陷；短期复核通过不能证明长期无间歇故障。另一次 TCP 重启探针在实际重启前，首次只读状态请求以 2 秒等待失败（`agenticros-check-70194b54`）；建立初始状态的等待现与普通 RPC 一致为 10 秒，停机期间的请求仍为 2 秒并计入失败。没有重发动作，也没有删掉该失败记录。

Fast DDS 的 SHM 主机判断与 Docker IPC 约束见[官方部署说明](https://fast-dds.docs.eprosima.com/en/v2.14.7/docker/shm_docker.html)。这只是配置依据；实际采用哪种传输以当前环境的结果为准，不把文档说明当作实测。

### 重现与后续 LAN 验收

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

<a id="early-history"></a>

## 早期开发与验证历史

> 本节保留早期架构说明和逐次开发记录。旧实例/join、HTTP 注册、控制租约、旧目录及构建命令均按当时版本理解，不代表当前配置。

容器使用 Ubuntu 22.04 / ROS 2 Humble；早期 Linux AMD64 验证与本次 macOS Apple Silicon 验证分别记录如下。
Agent instance 在一个容器内运行注册与发现服务、Agent 与 RViz2/noVNC；Robot instance 独立运行仿真与观测。
旧单机模式仍保留独立 Agent 容器及带 RViz2 的 `cartesian_gripper` 容器。

### 历史开发说明（归档）

Docker 源码按运行实例组织，主机角色不作为目录层级：

```text
docker/
├── agent_instance/
│   ├── Dockerfile
│   ├── runtime.py          # 容器内进程管理
│   ├── agent/              # Codex、账号配置、规则与技能
│   ├── registry/           # 注册与发现服务
│   └── viewer/             # RViz2 / noVNC
├── robot_instances/
│   └── cartesian_gripper/      # 仿真、驱动、观测与公开接口
│       └── registration.py # 注册、心跳与租约
└── shared/                 # 共用 DDS 网络配置
```

构建目标、镜像标签、容器名、数据卷和 `runs/` 路径保持兼容。
`agent_instance/agent/` 是 Agent 通用组件；完整 Agent instance 的构建入口是
`docker/agent_instance/Dockerfile`，构建目标为 `instance`。
Robot instance 的独立构建入口是 `docker/robot_instances/cartesian_gripper/Dockerfile`。


- `docker/robot_instances/cartesian_gripper/`：私有仿真、原始驱动、RGB-D 点云、评估器，以及公开 URDF、RViz 配置和容器图形进程管理。
- `docker/agent_instance/agent/`：通用 ROS 工具、适配要求、setup-body skill 和 Codex 配置，不含环境专用接口或身体资料。
- `docker/robot_instances/cartesian_gripper/interfaces/`：归仿真端所有的原始 ROS IDL，包名保留为 `agenticros_driver_interfaces`。
- `docker/robot_instances/cartesian_gripper/body.md`：由仿真端通过 ROS 参数公开的设备说明，不复制进 Agent 镜像。
- `scripts/`、`tests/`：启动和验收工具，无预写适配答案。

```bash
docker compose --profile simulation build
bash scripts/test.sh                      # 单测，不启动驱动
python3 scripts/audit_agent_image.py       # 镜像内容审计
python3 scripts/check_docker.py            # 已启动环境的 ROS、观测和隔离验证
python3 scripts/check_rviz.py              # RViz、标准观测话题、点云和 noVNC 验证（环境空闲时）
python3 scripts/check_docker.py --motion   # 小幅动作/取消测试，仅在没有任务时运行
python3 scripts/docker-trial.py            # 新工作区的自主适配与任务试验
bash scripts/docker-evaluate.sh           # 仿真容器内独立评估
```

自主试验保留容器、工作区和 `runs/` 证据；原有试验容器和日志不因重命名而改动。
单测、集成、自主适配和任务评估分别验收，结果与限制见下方开发与验证历史。
旧原生实现仅保留在 Git 历史和归档分支。

#### Codex 逐步调用与功能节点

Agent 的 `AGENTS.md`、`contract.md` 和 `setup-body/SKILL.md` 使用中文说明，命令、类型和字段名保留原样。
Codex 在会话中按“观测 → 选择下一步 → 调用 → 获取反馈 → 再决策”推进任务。
已有 ROS 接口满足需求就直接使用；缺少可复用能力或可靠的执行保障时，再开发、构建并启动功能节点。
节点内部可完成感知、规划、控制、反馈和取消，不负责替 Codex 编排整个用户任务。
禁止生成完整抓放等任务脚本、行为树或任务节点，也不能通过 Shell 串联预设动作绕过反馈判断。

容器初始化以下目录，不预装能力实现，也不自动启动生成节点或执行运动：

```text
/workspace/
├── AGENTS.md
├── contract.md
├── .agents/skills/setup-body/SKILL.md
├── ros_ws/src/                 # 自行开发的功能包和获取的公开 IDL
├── capabilities/manifest.json  # 自行生成；接口、调用模板、来源和验证证据
└── evidence/                   # 观测、动作回执、验证结果、节点进程日志
```

`ros_ws/build/`、`install/`、`log/` 在 Agent 构建时生成。功能节点在当前容器内持续运行，
由 Agent 记录启停、健康检查和进程归属；退出容器后进程结束，源码和记录保留。
新容器加载 overlay、恢复必要节点前须重新核对环境，不能恢复未确认的目标。
终端 Codex 不会天然持续接收 topic：节点订阅并维护观测，Codex 按需读取最新结果及实际图像。
CLI 等待超时不等于动作取消；UUID、session、执行程度和停止确认仍按契约处理。

`scripts/check_capabilities.py <宿主机工作区> --container <Agent容器名>` 检查能力清单结构、证据文件，
再用固定只读 ROS CLI 查询核对已验证接口类型和所声明生成节点是否在线。不执行清单中的命令，不发送运动。
`docker-trial.py` 在适配与任务两个阶段之间执行此检查，不再要求 `adapter/body.py`；
生成包、能力清单和证据直接保留在该试验的 `workspace/` 中。
图和文件检查不能证明功能语义、停止状态或 Codex 遵守逐步调用规则；需审阅两个阶段的 JSONL、生成源码及实际反馈。
任务是否成功仍由仿真容器内独立评估判定，评估真值不提供给 Agent。

本模式不兼容旧 `Body` 类作为必需产物的验收方式；旧工作区与备份不自动删除，也不作为新模式的验证证据。
新建日常容器仍复用 `runs/workspace/`，独立空工作区验收使用 `docker-trial.py`，仅在环境空闲且允许运动时运行。

#### 隔离边界

AgenticROS 不挂载整个项目、宿主机 home 或 Docker socket，只预装通用 ROS 工具和适配要求。
仓库内仅专用产物目录 `runs/workspace/` 绑定到 `/workspace`；不会暴露上级源码目录，观测仍为只读挂载。
启动时没有设备专用消息绑定、端点名称或身体说明。Agent 从 ROS 图发现节点、话题、服务和 action，
通过标准参数服务获取设备公开的 `device_documentation` 与 `interface_sources`，自行构建接口包并编写调用代码。
Humble 的图发现可以返回自定义类型名称，但 `ros2 interface show` 和客户端调用需要本地定义及类型支持；
标准 ROS 类型可直接使用，缺少的自定义定义不能仅靠类型名猜测。
`interface_sources` 只包含接口包的四个声明/构建文件，不包含驱动、仿真、评估或任务程序。
这些参数是本环境的公开发现约定，其他 ROS 设备不一定提供；缺少必要定义时应报告无法调用。
已有持久工作区可能保留之前的说明、代码和记忆，不会为制造“未知”而自动删除；首次发现验收使用独立空工作区。
评估器只监听仿真容器内部 Unix socket，不通过 ROS、HTTP 或观测卷提供真值。
两侧非 root、移除 capabilities；Codex 使用 `danger-full-access`，因为嵌套 bwrap 不可用。
DDS 未认证且 Agent 有出站网络；这是文件和进程隔离，不是防御所有恶意行为的沙箱。

#### 容器内 RViz2 与浏览器观察

`cartesian_gripper` 内由非 root 进程管理器启动驱动、`robot_state_publisher`、Xvfb、Openbox、RViz2、x11vnc 和 websockify/noVNC。
固定观测接口包括 `/driver/joint_states`、`/driver/state`、`/camera/color/image_raw`、
`/camera/depth/image_raw`、`/camera/camera_info`、`/camera/depth/color/points`、`/robot_description`、`/tf` 和 `/tf_static`。
点云与 RGB、深度、标定共用一次采集的时间戳，点云使用相机光学坐标系；不发布物体真值。
`observation.rviz` 预配置机器人、TF、网格、彩色点云和图像显示，可直接使用标准 RViz 功能调整视角或显示层。

启动脚本等待服务就绪后，显示实际映射的浏览器地址和连接密码，并在桌面环境中尝试自动打开浏览器。
默认地址为 `http://localhost:8080/vnc.html?autoconnect=1&resize=scale`，在页面输入终端显示的密码即可。
无桌面环境时手动打开该地址。密码不放在 URL 或 Agent 挂载中；启动终端输出包含密码。
原始 VNC 只监听容器内回环地址，宿主机仅映射 noVNC 到 `127.0.0.1`。网页只服务 noVNC 静态资源，不提供私有文件或评估 API。
默认 Xvfb 桌面为 1440×900，RViz 使用 Mesa/llvmpipe 软件渲染，无宿主机显示 socket、GPU 或 privileged 挂载。
Mac 使用 Docker Desktop，Linux 使用 Docker Engine 或 Docker Desktop；跨架构依赖由对应平台镜像安装，性能及 macOS 实测状态以验证记录为准。

关闭 RViz 窗口只会重新打开 RViz，不重启驱动。任何其他受管进程退出会停止整个容器，不自动重置仿真。
从旧观察服务升级时，先确认没有控制任务，再使用 `docker compose down --remove-orphans` 停止旧服务，
然后重新运行环境启动脚本；不要添加 `-v`，以保留工作区、认证、观测和浏览器密码。

#### 配置与旧版迁移

`AGENTICROS_VIEWER_PORT` 设置 RViz2/noVNC 浏览器端口（默认 8080），`ROS_DOMAIN_ID` 设置双方一致的 ROS 域（默认 42）。
仿真只在动作期间推进。确认没有控制任务后，可显式重建场景：

```bash
BODY_SEED=3 docker compose up -d --build --force-recreate --wait cartesian_gripper
```

重启生成新的 session ID，旧观测不能代表当前状态。
Compose 项目名、镜像与网络使用 `agenticros` 命名空间；当前持久卷为
`agenticros_codex-home`、`agenticros_observations` 和 `agenticros_viewer-auth`。
工作区现为 `runs/workspace/`，原 `agenticros_workspace` 卷只保留作迁移来源和备份，不自动删除。
原有数据不会自动出现在新项目中。升级已有安装时，先确认驱动空闲、停止所有控制程序，
再停止旧项目的容器，使用以下迁移入口（将 `OLD_PROJECT` 换成旧 Compose 项目名）：

```bash
docker compose --profile simulation build
python3 scripts/migrate_volumes.py --from-project OLD_PROJECT         # 查看复制计划
python3 scripts/migrate_volumes.py --from-project OLD_PROJECT --apply # 复制并校验
python3 scripts/prepare_workspace.py # 将已停止的旧工作区卷复制到 runs/workspace
bash scripts/environment.sh cartesian_gripper
```

迁移脚本拒绝运行中或非空目标卷，逐卷核对文件内容、权限和所有者，保留原卷作为备份；
不导入宿主机认证。若旧数据来自不同机器或自定义卷名，需要先显式导入对应应用卷。
ROS 接口包改名后，旧适配器中的导入名需更新为 `agenticros_driver_interfaces` 并重新验证；
已有代码和会话会保留，但重启后的身体 session、观测和适配验证状态必须重新核对。
历史验证文档、日志和禁用源码指纹中的原始名称保留用于追溯，不代表当前运行配置。

`scripts/environment.sh` 是统一选择入口，具体启动逻辑位于 `docker/robot_instances/<名称>/start.sh`。
当前仅提供 `cartesian_gripper`，未知或未实现的环境名称会报错，不启动容器。
设备登录不可用时，可在容器中通过 `codex login --with-api-key` 从 stdin 登录；不要把密钥写入仓库。

#### Agent 启动、登录与模型切换

`bash scripts/agenticros.sh` 构建 Agent 镜像，启动容器内的 `agenticros start` 终端菜单：

```text
1. ChatGPT 账号
2. DeepSeek API
3. 其他云端 API（Responses 协议）
0. 退出
```

ChatGPT 方式输入模型名，复用有效账号认证或启动 `codex login --device-auth`，由用户在浏览器确认。
账号分支检查认证状态，不把它当作模型访问或任务验收。
API 方式输入 HTTPS Base URL、模型名和不回显的 API Key；DeepSeek 预填官方地址和 `deepseek-flash`。
其他 API 可包括 OpenAI API；只支持云端 Responses 协议，本菜单不提供本地部署选项。
账号和 API 两种方式都可输入推理强度，并随连接配置保存；已有配置可选 4 单独修改强度，不重填 Key。
GPT-6.1 Sol 可选 `low/medium/high/xhigh/max`；其他模型支持范围由提供方决定，菜单中的 `none` 不适用于所有模型。
API 校验请求使用所选强度；账号分支只检查登录，强度支持情况以实际模型请求为准。
单次临时覆盖可用 `bash scripts/agenticros.sh -c 'model_reasoning_effort="high"'`；这不会修改已保存的强度，
菜单校验仍使用保存或新选择的值。

API 校验使用两个小型 SSE 请求，检查鉴权、模型可用性、完整结束事件、函数工具调用参数和工具结果往返。
校验会产生 API 用量，不执行工具程序、不访问 ROS、不执行机器人动作。
HTTP 错误按鉴权、权限、地址或模型、余额与限流分类；不回显服务端错误正文，拒绝携带 Key 跟随重定向。
这属于基础 API 兼容性检查，不代表完整 Codex 工具、图像、自主适配或抓取验收。

校验通过后，连接配置和 Key 原子写入专用 Codex 卷中的 `agenticros-connection.json`，文件权限 0600。
失败不覆盖旧配置，Ctrl+C 或 EOF 退出；后续启动可回车复用配置并重新校验。
Key 不写入源码、公开 TOML 或命令行参数，启动 Codex 时仅通过进程环境提供。
配置保存在卷中，不会被 entrypoint 每次复制基础 `codex.toml` 的操作覆盖。
`codex` 包装器让后续 `codex exec` 和试验脚本使用同一连接；显式 `codex login/logout` 仍可独立使用。

退出交互后仅删除本次运行容器，保留工作区和 Codex 卷；入口不启动或重启仿真。
`bash scripts/agenticros.sh resume --last` 在选择连接后恢复会话；`docker-chat.sh` 为兼容入口。
需要单独账号登录时仍可运行 `bash scripts/docker-login.sh`。
`agenticros status` 只检查本地配置或账号状态，不发起付费 API 校验。

当前基础默认配置仍在 `docker/agent_instance/agent/codex.toml`，菜单选择通过包装器的运行参数覆盖模型与提供方。
模型能力目录（尤其图像能力）可按提供方官方说明配置；菜单不会因文字/函数测试通过就宣称支持视觉。
参见 [OpenAI 登录说明](https://learn.chatgpt.com/docs/auth?surface=cli)、
[提供方配置](https://learn.chatgpt.com/docs/config-file/config-advanced)、
[协议兼容要求](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility) 和
[DeepSeek Codex 接入说明](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/)。

#### 断电或重启后恢复

Docker 启动后，在项目目录恢复已有仿真容器并等待健康检查：

```bash
docker compose up -d --no-build --no-recreate --wait cartesian_gripper
docker compose ps
```

工作区保存在仓库的 `runs/workspace/`，Codex 认证、观测文件和 VNC 密码保存在应用数据卷中，不因普通停止而删除；
仿真物理状态不持久化，启动后场景重置且 session 改变。恢复 Agent 会话前先核对 session、重新观测，
不重放断电前未确认完成的动作。不要用 `down -v` 删除数据卷。

#### 仓库中的生成产物

默认映射为 `runs/workspace/` → `/workspace`。生成的 `adapter/`、`interfaces/`、`evidence/`、
`ros_logs/` 会直接出现在宿主机，`runs/` 已被 Git 忽略，也不进入 Docker 构建上下文。
相机原始快照仍在 observations 卷；Codex 原始聊天会话、登录和模型 Key 仍在 codex-home 卷，
不会为导出代码而将凭据放入源码仓库。

启动入口先调用 `prepare_workspace.py`。仅当目标为空且没有运行中的旧卷使用者时，
才将旧工作区复制到临时目录，核对源/目标文件哈希和符号链接后替换空目标，保留原卷。
非空工作区直接复用，不自动覆盖或合并。旧 Agent 若仍运行，首次迁移会要求先退出；不停止仿真。
新增文件通过目录 setgid、宿主机附加组和 umask 0002 支持宿主机用户与非 root Agent 共同读写。
启动脚本自动设置宿主机组 ID；直接运行 Compose 时，自定义组用户需设置 `AGENTICROS_WORKSPACE_GID`。
`docker-login.sh` 也会准备工作区；全新检出时先构建 Agent 镜像，再迁移已有旧卷。

自主试验使用单独的 `runs/<trial>/workspace/`，保持空工作区验收，不与日常适配产物混用。

### 开发与验证历史

以下按开发阶段记录实现、验证范围和证据。旧容器名、日志路径、session 和当时的运行状态保留用于追溯，
不代表当前配置；此前的 Jazzy 和自写观察器记录也不代表当前 Humble/RViz2 版本已完成自主抓取验收。

#### Docker 初版：2026-11-13，44cce15

环境：Apple Silicon、Docker Desktop 4.99.0 / Engine 29.9.3、ROS 2 Jazzy、MuJoCo 3.3.7、Codex CLI 0.168.0。
实际模型配置为 `gpt-6-astra / medium`，用户在容器中独立登录。

| 阶段 | 结果 |
| --- | --- |
| 构建与启动 | 两个 ARM64 镜像成功，Body 健康，Agent CLI/ROS/Python/C++ 可运行 |
| ROS 与观测 | 跨容器发现、同步 RGB-D、标定读取通过；Chrome 显示实时画面 |
| 底层控制 | 非法参数和旧 session 拒绝、并发拒绝、UUID 查询、取消与停止确认通过 |
| 隔离 | 导出镜像扫描 40,162 个文件、比对 15 个私有源码哈希；实际挂载及只读观测验证通过 |
| 重启 | session 改变、物理状态重置，新观测使用新 ID |
| 自主适配 | 空工作区起步，Codex 自行编写适配器；三轴小幅运动、空夹爪开合及 7 类非法输入通过 |
| 自主任务 | 经未夹起、滑落和自主修正后，红块放到红盘上，夹爪释放并退离 |
| 严格独立评估 | **未通过静止条件**；盘内、接触盘面、脱离夹爪条件通过 |

追加公开观测的稳定性复核后，Agent 自行发现缓慢旋转并尝试修正，最终静止条件仍未通过。
未提供真值给 Agent，也未修改评估阈值。用户接受当前效果后停止尝试；驱动空闲、停止确认、无需恢复。
只验证了单个场景，适配器全工作空间和故障注入未验证；不能称为稳定抓取系统或实机验证。

证据保存在宿主机（不进镜像）：

- `runs/docker-validation-20261113/`：构建、隔离和原始驱动日志。
- `runs/rosbody-trial-20261113-183345-fe97da/`：自主试验、初次与最终评估、最终工作区副本 `workspace-final/`。
- 试验容器：`rosbody-trial-20261113-183345-fe97da`，数据卷和认证均保留。

#### Docker 分支精简

旧源码、pixi 和原生构建产物已移除，历史仍可通过 Git 取得。原有“31 项通过”包含旧实现测试，不代表现在的测试数量。
保留 4 项相关单测：3 项宿主机标准库布局检查，1 项临时 Body 容器中的评估器检查。
旧参考实现的禁用哈希保存在 `tests/forbidden_sources.json`，删除源码不会削弱对应的镜像扫描。

精简后验证：两个镜像构建成功，4 项单测、实际镜像审计和无运动 ROS 集成检查通过；仍扫描 40,162 个文件和 15 个禁用哈希。
评估器单测采用明确的静态状态验证判据，不再假设不同平台经过固定步数就必然落稳；运行时评估器未改。
正在运行的 Body 容器 ID、启动时间和 session 均未改变。没有重新做运动或自主任务试验。
本次日志：`runs/docker-cleanup-20261113/`。

#### AgenticROS 命名与独立启动（2026-11-13）

服务与目录改名为 `agenticros`、`simple_mujoco`。环境显式启动；聊天入口在环境未运行时退出，
不再自动启动仿真。`real` 入口仅解释外部 ROS 接入要求并退出；未验证实机连接。
保留 Compose 项目名 `rosbody` 以复用已有卷、认证和网络，原始 IDL 包名也保持兼容。

验证：两个镜像构建成功；4 项布局/启动边界单测与 1 项评估器单测通过；
在独立项目 `agenticros-rename-check`、ROS 域 43、端口 8081 启动新服务，ARM64 跨容器 ROS、
观测只读、非法动作拒绝与 HTTP 隔离检查通过；没有运行有效动作或重新进行自主任务。
实际 Agent 镜像内容审计通过。无环境时聊天入口退出，真实环境入口没有启动容器。
原有 `rosbody-body-1` 启动时间未变；旧会话、卷和试验代码保留。
临时测试仿真已停止，测试卷保留。证据：`runs/agenticros-rename-20261113/`。

#### Ubuntu 22.04 / Humble 迁移（2026-11-13，Linux AMD64）

两个镜像改用 `ros:humble-ros-base-jammy`，同步更新 ROS 包名、构建和启动时的环境路径。
Agent 的 SciPy 固定为支持 Python 3.10 的 1.15.3；MuJoCo 3.3.7 和 Codex CLI 0.168.0 保持原版本。
集成检查接受 AMD64 与 ARM64，不再限定 ARM64。本次只实测 AMD64，未重新验证 Humble ARM64。

本机 Docker Engine 29.9.3、Compose 5.7.1；容器实际为 Ubuntu 22.04.5、ROS 2 Humble、Python 3.10.12。
两个镜像构建成功，仿真健康、相机 HTTP 可访问；4 项布局单测与 1 项评估器单测通过。
跨容器 ROS、同步观测、只读观测挂载、非法参数和旧 session 拒绝、HTTP 隔离检查通过。
小幅运动、并发拒绝、UUID 回执、取消和停止确认通过；检查结束后驱动空闲。
实际 Agent 镜像审计通过，扫描 40,500 个文件和 15 个禁用源码哈希。

旧 Jazzy 仿真容器和两个项目镜像已按用户要求删除，卷及日志保留。
Humble 自主适配与抓取尚未验证，等待容器内 Codex 独立登录；以上检查不代表抓取任务成功。
本次证据：`runs/setup-20261113/`，包括构建、版本、单测、集成、运动和镜像审计日志。

#### 独立 ROS 观察界面（2026-11-13，Linux AMD64）

`simple_mujoco` 发布关节状态、RGB、米制深度、CameraInfo、相机静态 TF 和公开驱动状态，
不再运行 HTTP 服务。独立 `visualizer` 服务订阅 ROS，由 `robot_state_publisher` 发布公开 URDF 与机器人 TF，
通过本机 8080 端口显示模型、坐标轴、相机和 RGB-D 点云。Three.js 0.180.0 与许可证随代码提供，无运行时 CDN 依赖。
观察服务不挂载源码、观测卷、认证或 Docker socket，不提供执行动作或评估器接口。

新驱动和观察镜像构建成功，两个服务健康；9 项单测通过（4 项布局、1 项评估器、4 项传感器解码/投影）。
实际跨容器订阅、URDF/关节/TF 对应、同步 RGB-D 重建 6,052 个点、话题消息预览、
私有路径与挂载隔离检查通过；浏览界面没有推进物理仿真。原 Agent 隔离和无运动 ROS 检查通过。
浏览器实际显示三维模型、点云、相机、TF 与话题面板，并验证俯视按钮。
本阶段未重新运行有效运动或自主抓取，不将此前的运动检查等同于新驱动运动验收。
切换前确认旧驱动空闲且停止，重启后 session 为 `f79e2e054c7d`；卷和日志保留。
证据：`runs/setup-20261113/observer-*.log` 和 `build-observer.log`。

#### 统一 AgenticROS 命名与数据迁移（2026-11-14，Linux AMD64）

产品文字统一为 AgenticROS；Compose 项目、镜像、网络、数据卷、试验容器和审计容器使用 `agenticros` 前缀。
原始 ROS 接口包统一为 `agenticros_driver_interfaces`，同步修改 CMake、包清单、两侧构建路径、Python 导入和设备文档。
公开资料目录改为 `/opt/agenticros`，DDS 配置改为 `/etc/agenticros/fastdds.xml`，
Agent 环境脚本改为 `/etc/profile.d/agenticros.sh`，私有评估 socket 改为 `/tmp/agenticros-evaluator.sock`，
端口变量改为 `AGENTICROS_VIEWER_PORT`。固定 `/driver/*` 和相机话题名称、消息字段与控制语义保持原有定义。

移除旧服务名的启动兼容分支，增加显式应用卷迁移脚本。迁移前确认旧驱动空闲、停止已确认；
停止旧服务后复制 workspace、codex-home、observations 三个卷，逐项校验内容哈希、权限、所有者和符号链接。
三卷分别验证 15、13、18 个条目，原卷保留作备份。重复迁移被非空目标保护拒绝。
原始日志、禁用源码指纹及其说明标签未改写；镜像审计和隔离测试继续通过指纹表检查历史私有命名空间。

验证结果：三个新镜像构建成功，9 项单测通过；跨容器 ROS、新接口包加载、观测只读、
镜像与挂载隔离、三维观察、TF、同步 RGB-D 点云检查通过；评估入口可以访问新 socket。
Agent 镜像扫描 40,500 个文件、15 个私有源码指纹通过。
运行中的两个服务均健康，当前 session 为 `80742c9dfc30`，驱动空闲、无需恢复；实时 ROS 类型无旧包名。
旧容器、网络和镜像标签已清理，原应用数据卷及全部证据保留。
未执行有效运动或自主抓取；初始场景评估为未完成任务，不作为抓取验收结果。

本机证据：`runs/rename-agenticros-20261114/`，包含构建、卷迁移与保护检查、单测、ROS、观察器、
镜像审计、评估入口、旧资源清理和最终状态记录。

#### 容器内 RViz2 / noVNC（2026-11-14，Linux AMD64）

删除独立 `visualizer` 服务及 Three.js 网页，将公开 URDF、robot_state_publisher、RViz2、
Xvfb、Openbox、x11vnc 与 websockify/noVNC 集成到 `simple_mujoco`。
驱动继续提供固定 ROS 接口，新增 `/camera/depth/color/points` 标准 PointCloud2；
点云从同次 RGB-D 采集重建，与图像及 CameraInfo 时间戳一致，不读取物体真值。
RViz 配置默认显示模型、TF、网格、RGB-D 点云和 RGB 相机，可启用深度图。

非 root、只读根文件系统、cap_drop ALL 和 Agent 的挂载隔离保留。
无宿主机显示 socket、GPU 或 privileged 依赖，使用 Xvfb + Mesa/llvmpipe 软件渲染。
VNC 只监听容器回环地址，浏览器服务只映射宿主机 127.0.0.1:8080；
自动生成的八字符随机密码保存在专用 viewer-auth 卷，Agent 不挂载该卷。
网页根目录仅包含 noVNC 静态资源，私有源码、密码、观测和评估路径的 HTTP 检查均返回 404。

实际包版本：RViz2 11.2.29、noVNC 1.0.0、x11vnc 0.9.16、Xvfb 21.1.4。
仿真与 Agent 镜像构建成功；11 项单测通过（4 项布局、1 项评估器、5 项传感器与点云、1 项密码持久性和权限）。
集成检查通过：RViz 节点存在、机器人模型与 TF 对应、同时间戳 RGB/深度/标定/6,052 点彩色点云、
VNC 强制认证、进程健康、跨容器 ROS、Agent 只读观测和镜像隔离。
Agent 镜像审计扫描 40,500 个文件及 19 个私有源码指纹通过。
小幅运动、并发拒绝、取消、UUID 回执和停止确认通过；没有运行自主抓取任务。

浏览器通过 noVNC 实际认证并操作 RViz2，画面显示 Global Status: Ok，模型、相机和点云正常，拖动旋转视角通过。
终止 RViz 后仅 RViz 重开，其余受管进程 PID、驱动 session 和仿真时间未变；不自动重启驱动。
当前 session 为 `e22a3f7afdce`，检查后驱动空闲且停止已确认，工作区、认证和观测卷保留。
本次只实测 Linux AMD64；macOS/ARM64 未实际验证，不将通用依赖和无宿主机显示绑定等同于跨平台验收通过。

证据：`runs/rviz-novnc-20261114/`，含构建、单测、ROS/RViz/noVNC 集成、GUI 重开、运动回归、
镜像审计、包版本和浏览器实际截图 `rviz-desktop.png`。

#### 断电后恢复复查（2026-11-14，Linux AMD64）

断电后原仿真容器处于退出状态，源码、镜像、工作区、Codex 认证、观测和 viewer-auth 数据卷均保留。
使用 `docker compose up -d --no-build --no-recreate --wait simple_mujoco` 启动已有容器，健康检查通过。
重新运行 11 项单测、`check_rviz.py` 和 `check_docker.py`，均通过；恢复复查没有执行有效运动。
新 session 为 `4fa1b9d52698`，RViz、TF、同步 RGB-D 与 6,052 点点云、VNC 认证、Agent 挂载隔离正常。
README 明确以 Linux 实测结果为准，并补充断电恢复、数据持久化和无运动验证命令。
本次证据：`runs/power-recovery-20261114/`。本节不增加自主抓取或其他平台的验证结论。


#### 2026-11-14：改为运行时发现环境接口

将接口包和身体说明移至 `docker/simple_mujoco/`，从 Agent 镜像和启动复制流程中移除。
仿真通过只读 ROS 参数 `device_documentation`、`interface_sources` 公开说明和接口包声明；
Agent 只保留通用发现、编译工具和适配要求，不预置驱动名称或消息绑定。
已有数据卷不清理，旧代码、说明和会话可能仍在；未知环境验收使用独立空工作区。

12 项单测通过。独立临时 Compose 项目中，验证无自定义绑定时发现 action、通过标准参数服务
取得定义、编译接口包、调用状态和快照，并通过原始请求拒绝、挂载隔离和 RViz 检查。
镜像文件系统审计通过，确认无预装设备定义与身体说明。
测试未执行有效运动，不代表 Codex 自主适配或抓取任务验收。
测试项目及其临时卷已清理，原运行仿真、会话与应用数据卷保持不动。
当前运行容器仍是旧版本；新参数发布功能需在确认无任务后更新仿真容器才能生效。


#### 2026-11-14：容器内云端登录与连接校验

新增终端菜单，支持 ChatGPT 账号、DeepSeek 和其他云端 Responses API。
Key 隐藏输入，连接校验成功后保存到专用 Codex 卷；失败保留旧配置。
统一 Codex 包装器与试验入口的连接读取，保留独立登录命令兼容性。

通过 `scripts/test.sh` 的 22 项单测，以及 Agent 镜像构建与文件系统审计。
真实临时容器终端验证菜单显示、Key 不回显、无效地址拒绝和退出；未挂载现有认证卷。
API 流式与工具往返使用模拟响应验证，未使用真实云端 Key，未替用户完成账号授权。
当前运行的仿真容器未修改或重启，未执行机器人动作。


#### 2026-11-14：工作区绑定到仓库产物目录

将日常 `/workspace` 改为绑定宿主机 `runs/workspace/`，自主试验绑定独立的 `runs/<trial>/workspace/`。
不挂载仓库根目录，认证、模型 Key 和原始会话缓存仍在 Codex 专用卷。
旧 Agent 退出后，复制并校验原 `agenticros_workspace` 的 961 个条目，保留原卷作为备份。
迁移调整的是新副本的宿主机所有者与共享组权限，不修改原卷。

28 项单测通过。实际临时 Agent 容器验证了非 root、限定目录绑定、宿主机与容器双向读写、
只读观测挂载，以及既有适配器内容未改变；Agent 镜像审计通过。
未运行模型、未启动仿真驱动、未执行机器人动作，临时检查容器与测试文件已清理。

#### 2026-11-15：Codex 逐步调用 ROS 能力

将 Agent 规则、能力契约和适配技能改为中文，移除必须生成 `Body` 类的要求。
Codex 负责逐步决策与调用，按需生成可复用 ROS 功能节点；不以任务脚本代替会话中的判断。
工作区初始化改为 `ros_ws/src/`、`capabilities/` 和 `evidence/`，不自动启动生成节点或恢复动作。
试验入口在适配后检查能力清单、证据和在线 ROS 图，保留独立任务评估及逐步调用行为的人工审阅要求。

35 项单测通过；技能格式、Python/Shell 语法与差异检查通过。Agent 镜像构建成功，
镜像审计扫描 39,905 个文件、24 个禁用源码指纹通过。
无网络、无认证卷的临时 Agent 容器验证新目录及中文规则复制、后台测试节点持续运行、
实际话题消息读取、能力清单和在线节点检查。该节点仅为维护者测试夹具，未打入镜像，不是自主适配结果。
临时容器已清理，证据保存在 `runs/ros-capability-check-ndq4_ugz/`。

未调用模型、未执行机器人动作、未重启运行中的仿真；未验收 Codex 自主开发节点、逐步决策行为或抓放成功。
旧产物、认证和数据卷未删除，新建日常容器仍复用绑定工作区；空工作区自主验收另行运行。


#### 2026-11-16：macOS / Linux instance-client 适配

保留 `bash scripts/agenticros.sh --instance <名称>` → `bash scripts/environment.sh simple_mujoco --join <连接码> --robot-id <名称>` 的启动顺序。
Mac 通过系统 `ipconfig` 选择 IPv4，启动前实测 Docker Desktop 双向 host networking；临时探针可重试，服务启动和动作不重试。
新 instance 默认 TCP，已有 Linux UDP 配置、身份和数据保持不变。Mac 加入旧 UDP instance 会在启动前明确报错。

TCP 使用 Fast DDS `wan_addr` 映射宿主地址，并自动分配各 ROS participant 的监听端口；同机数据使用 SHM，Compose 设置 `ipc: host`。
这会共享 Docker 宿主 IPC，适用于当前可信仿真部署，不构成容器间的安全隔离。
发现端口限定为 1024..32767，避免测试中高端口重连失败的配置；发现记录使用 BACKUP 模式缓存在容器 `/tmp/agenticros-discovery/`。
恢复发现记录不会恢复注册 epoch、控制租约或动作。Humble 的服务响应端点匹配等待从默认 100 ms 调整为 TCP 模式下的 3 秒，避免新查询的响应过早被丢弃。
对应机制见 [Fast DDS TCP](https://fast-dds.docs.eprosima.com/en/2.6.x/fastdds/transport/tcp/tcp.html)、
[发现记录恢复](https://fast-dds.docs.eprosima.com/en/2.6.x/fastdds/use_cases/wifi/discovery_server_use_case.html) 和
[Humble 服务响应实现](https://github.com/ros2/rmw_fastrtps/blob/humble/rmw_fastrtps_shared_cpp/src/rmw_response.cpp)。

本次在一台 Apple Silicon Mac、Docker Desktop 4.99.0 上完成：

- `bash scripts/test.sh`：95 项通过；Python 语法、`git diff --check` 通过。
- Mac 局域网 IPv4 + host IPC：完整双 client 集成通过，证据 `runs/instances/lan-test-31573afd/`。
- TCP 三个独立 IP / 私有 IPC：完整集成通过，证据 `runs/instances/lan-test-92dd906d/`；每个测试容器有 512 MiB 私有共享内存，跨容器不能使用 SHM。执行命令为 `python3 scripts/check_lan.py --transport tcp --separate-ips --result-timeout 180`。
- 原有 UDP 独立 IP 路径通过：`runs/instances/lan-test-5a545a83/`。两种协议均检查身份、快照哈希、并行动作、拒绝串用 session、取消回执、租约失效停止和重启后不自动恢复控制。
- viewer / VNC 认证、桌面重启不重启 registry、会话锁、client 会话替换通过：`runs/instances/viewer-test-12351571/`。
- 两个实际 Bash 入口依次启动通过：`runs/instances/mac-cli-55af9326/`；只进入登录菜单并退出，没有调用模型。
- Agent 镜像审计扫描 61,946 个文件和 25 个私有源码指纹通过；六处运行时代码副本与源码哈希一致。

最终汇总：`runs/mac-adaptation/result.json`。其他目录保留了排障过程中的失败配置，不能作为最终通过证据。
本次测试容器均已停止并移除，运行容器数为 0；保留工作区、数据卷和证据，未提交或 push。
没有使用第二台物理机器，因此尚未验收 Mac↔Linux / Mac↔Mac 的真实路由、防火墙及链路故障；本次也没有在独立 Linux 宿主重新运行。
Docker 集成不代表 Codex 自主适配、任务成功或硬件验证。Mac 软件渲染可能慢于实时，测试等待上限不修改动作时长或租约，也不重发超时动作。

#### 2026-11-17：按 Agent / Robot instance 整理 Docker 目录

将 Agent、注册服务、Viewer 和进程管理统一归入 `docker/agent_instance/`；
机器人实现归入 `docker/robot_instances/`；`simple_mujoco/lan.py` 改名为 `registration.py` 并更新驱动导入。
同步修改 Dockerfile、Compose、启动脚本、镜像审计、测试与文档路径；保留构建目标、镜像标签、容器名、数据卷和配置格式。

验证：

- 两类 LAN 镜像和旧单机 Compose 镜像均在本机 macOS 成功构建。
- `bash scripts/test.sh`：95 项测试通过（88 项宿主机测试、7 项隔离容器测试）。
- 通用 Agent 与完整 Agent instance 镜像隔离审计通过；后者扫描 61,946 个文件，核对 25 个私有源码指纹。
- 新镜像双 Robot instance 的 Viewer 集成通过，包括注册发现、画面、可视化重启、会话锁与机器人 session 更新；无机器人运动指令。
  证据：`runs/instances/viewer-test-683907e0/viewer-check.json`。
- 测试容器均清理，最终运行容器数为 0；账号卷、观测卷与工作区保留。未做云端登录、Codex 自主任务或真实双机验收。

#### 2026-11-17：可视化接口浏览器

Viewer 的 `ROS interfaces` 窗口按 Topics、Services、Actions 分页显示公开 ROS 图中的名称与类型，每两秒刷新并保留当前页签。
服务通过 `get_service_names_and_types` 发现，动作通过 `rclpy.action.get_action_names_and_types` 发现，不安装设备专用接口包，也不调用服务或发送动作。
列表是发现结果，不表示接口可达性、类型绑定已安装或控制语义已验证。

验证：7 项 Viewer 单元测试、1 项容器 Qt 页签/刷新测试，以及新镜像双机器人集成通过。
集成验证包含两台机器人的 observe 服务和 actuate 动作发现；证据为
`runs/instances/viewer-test-0d7ac3cb/viewer-check.json`。已检查使用测试实例发现数据渲染的 Services、Actions 页签。

#### 2026-11-17：Cartesian 改名与 ALOHA 双臂环境

分支 `codex/supervisor-arm-agents`。原 `simple_mujoco` 正式改名为 `cartesian_gripper`，同步启动入口、Compose、镜像名、测试及当前文档；历史记录保留旧名称。
新增 `aloha_tabletop`，使用 MuJoCo Menagerie ALOHA 2 固定提交 `370c43965323c0944b433b202aac5058b5f907a9`，保留原模型与 BSD 许可，通过构建脚本生成任务场景。

一个 Robot instance 包含同一物理世界的两臂，共用 session、坐标系与物理时钟，各有七维位置动作、状态、取消和 UUID 回执。
物理线程持续运行；图像使用单独线程和缓存渲染器，避免软件渲染长时间占用物理锁。两侧当前共享俯视 RGB-D。
未提供 IK、避障或完整任务程序；私有评估器目前只评红块放入红盘。

Agent 增加主 agent 监督规则与 `arm-executor` 角色，由原生子 agent 工具执行分工；角色随入口复制到工作区。
分配归属是协作协议，驱动仅保证单臂动作互斥，不是跨 agent 权限隔离。
此次没有调用云端模型验证自主双臂任务、监督响应或异常恢复。

验证与证据：

- `bash scripts/test.sh`：97 项通过，日志 `runs/environment-tests-final.log`。
- `python3 scripts/check_aloha.py`：隔离容器中实际双臂并行动作、同臂互斥、错误 session 拒绝、UUID 回执、取消并停止与 RGB-D 通过；证据 `runs/agenticros-aloha-check-0312dfcb/integration.json`。此入口自动移除自身测试容器。
- `python3 scripts/check_viewer.py --environment aloha_tabletop`：两台 Robot instance 的注册、左右臂接口发现、Viewer 重启、会话锁及 session 替换通过；证据 `runs/instances/viewer-test-32cf962e/viewer-check.json`。
- 新通用 Agent 镜像启动时角色文件与规则复制、Codex 配置加载通过；证据 `runs/supervisor-role-smoke.log`。该检查不代表模型已使用角色。
- 两个环境镜像、通用 Agent 与完整 Agent instance 镜像构建成功；完整 instance 的角色复制和 Codex 0.168.0 启动检查通过。
- 新通用 Agent 与完整 instance 的文件系统隔离审计通过，分别扫描 39,915 / 61,947 个文件和 37 个私有源码指纹；日志 `runs/supervisor-base-audit.log`、`runs/supervisor-instance-audit.log`。最终无残留容器。
- Python 编译与 `git diff --check` 通过。

按用户授权清理旧仿真/Agent 容器及旧环境镜像标签，保留账号、工作区、观测卷和 runs 证据。
没有真实机器人操作、真实双机验收或 push。

#### 2026-11-18：独立腕部相机与三画面 Viewer

修复左右相机话题重复发布俯视画面的问题：`left/camera`、`right/camera` 分别绑定模型原有腕部相机，新增 `overhead/camera` 和 `/overhead/driver/observe`。
三路使用独立光学坐标系、RGB-D 和 CameraInfo，腕部动态 TF 与图像使用同一次采集的外参及时间戳；俯视使用静态 TF。
派生场景采用 320×240 方形像素相机，腕部垂直视场 58°，俯视 45°。三路顺序采集，不宣称跨相机同步。

通用 Viewer 从公开 CompressedImage 话题发现多路相机，按名称横排显示；本环境顺序是左腕、俯视、右腕。下方保留 RViz 点云。
它不依赖 ALOHA 模型或设备类型包，也不发送运动指令。

隔离双臂集成测试通过，并新增三路不同图像、独立 frame、CameraInfo 与快照内参一致、腕部外参随运动改变的检查。
证据：`runs/agenticros-aloha-check-fd235bbe/integration.json`。Viewer 单元测试 8 项通过。
两类镜像重新构建；按用户要求重启 `aloha_a`，更新运行中的 Viewer 文件并仅重启 Viewer 进程，保留当前 Codex 会话、账号和工作区。
已检查实际桌面三路画面，截图 `runs/three-cameras.png`。新 session 为 `9d6a7a4b372c`，旧 session 不再可用于控制。

#### 2026-11-18：实例产物目录与完整清理入口

Agent 启动及 LAN 管理统一使用 `--agent_id`，保留 `--instance` 兼容别名，Robot 仍用 `--robot-id`。
新建 Agent 的配置、工作区位于 `runs/agent_instances/<agent_id>/`；新建 Robot 的绑定配置、可直接查看的观测位于 `runs/robot_instances/<agent_id>/<robot_id>/`。
维护者测试报告移至 `runs/checks/<run_id>/`，测试实例本身仍使用相同的 Agent/Robot 目录结构。
旧实例继续使用既有目录、观测卷；不在线搬动当前 Codex 工作区。清理后新建实例全部使用新结构。

新增 `bash scripts/clean-runtime.sh`：默认只列清单，`--yes` 才移除本项目容器、数据卷（含登录状态）、整个 runs 和 Python 缓存；`--images` 可选移除项目镜像标签。
不执行全局 prune，不删除源码/Git，共享构建缓存保留。执行前拒绝其他容器共享选中卷，删除宿主目录前再检查容器引用，并拒绝 runs 符号链接。

验证：102 项回归测试通过；实际 Docker 临时容器/卷/目录清理通过；新 Robot 观测 bind mount 写入并由宿主读取通过。
双 Robot Viewer 集成通过，报告 `runs/checks/viewer-test-3bd82d8a/viewer-check.json`。
对现有用户数据只运行清理预览，没有执行完整删除或迁移；`aloha-demo` 与 `aloha_a` 保持运行。

#### 2026-11-18：统一面向用户的入口命名

启动入口改为 `scripts/agent.sh` 和 `scripts/robot.sh`；单独登录改为 `agent-login.sh`，旧单机 Agent 删除入口改为 `delete-agent.sh`。
内部 shell 参数文件改为 `agent-options.sh`，移除只转发启动命令的历史 `docker-chat.sh`。
LAN 管理公开命令改用 `robot`、`start-agent`、`stop-agent`，兼容原 `client`、`server`、`stop-server` 子命令。
同步更新当前文档、帮助、脚本提示与测试；历史开发记录保留当时的命令。Compose 服务、账号卷及现有实例标识保持兼容。

两类 shell 入口的参数转发检查、帮助检查和 102 项回归测试通过。此次没有重启运行环境，也没有执行清理。

#### 2026-11-18：撤销 RViz 外的三画面区域

按用户纠正移除 Viewer 自行订阅、解码和显示三路图像的区域。三路相机改由 RViz 内置 Image 面板显示，默认在 RViz 内从左到右排列左腕、俯视、右腕；保留原生可拖动 dock 和点云视图。
保留腕部相机绑定、标定和动态 TF 修正。运行中的 account-a 仅更新 Viewer 并重启可视化进程，机器人 session `f5ca9cd057ee` 保持不变。
8 项 Viewer 单元测试通过，并检查实际 RViz 三路画面，截图 `runs/checks/rviz-docks/viewer.png`。现有 Agent instance 镜像以只替换两份 Viewer 文件的增量构建更新，完整 Dockerfile 同样使用最新源码。

#### 2026-11-19：ALOHA move_pose / move_delta

两臂新增 `driver/move_pose` 与 `driver/move_delta` Action，接受当前 session、base/tcp 参考系、米制位置/增量、xyzw 四元数与时长。
move_pose(base) 为绝对目标；tcp 目标一次性相对于执行开始时的实测 TCP。base 增量的旋转绕当前 TCP 原点，而非基座原点。
TCP 位置保持原有指尖参考点中点定义，新增 gripper_link 朝向、完整位姿状态与独立 TCP TF。

使用 MuJoCo 模型的独立 MjData 做局部阻尼最小二乘 IK，对整条直线/姿态插值采样路径做预检，限制关节位置及采样目标速度。
发现原位置伺服在重力下的静态误差后，为笛卡尔动作及其后续保持加入模型偏置力前馈；原 actuate 行为不变。
实测 3 mm / 0.03 rad 到位容差与停止条件共同决定成功。所有动作共用单臂互斥，保留 UUID 回执、取消、租约失效、关机停止及恢复故障锁。
未加入碰撞检查、自动绕障、全局 IK 搜索、联合交接或任务级控制，回执明确 `collision_checked=false`。

验证：102 项回归测试通过；隔离 ROS 测试覆盖左右臂 × 两种 Action × 两种参考系的 8 组实际平移/旋转，及非法输入、过期 session、不可达、速度超限、规划取消、运动取消、双臂并行、跨 Action 互斥、关机和租约失效。
笛卡尔证据 `runs/checks/agenticros-aloha-check-d2d4423a/integration.json`；原双臂/相机回归证据 `runs/checks/agenticros-aloha-check-78f30e99/integration.json`。
汇总 `runs/checks/aloha-cartesian-validation/result.json`。新 Robot 镜像已构建，未重启用户运行中的 robot_b、未向其发送运动指令，未做自主抓取或交接验收。

#### 2026-11-19：ALOHA / Viewer 提交前复核

核对测试镜像中的 driver.py、world.py、cartesian.py、viewer.py、discovery.py 与工作区源码完全一致。
`scripts/test.sh` 的 112 项测试通过；隔离 Qt 测试 2 项通过，新增验证三相机原生 dock 状态可恢复到顶部区域。
原双臂/相机集成通过：`runs/checks/agenticros-aloha-check-2cc83bb5/integration.json`。
笛卡尔集成通过：`runs/checks/agenticros-aloha-check-ab6b3a39/integration.json`，覆盖两臂、两种动作、两种坐标系及异常/取消路径。
本轮使用独立、无网络仿真容器，没有执行自主抓取或交接；Qt 布局测试不替代实际 Viewer 画面的视觉验收。


## Runtime script cleanup (2026-11-21)

- Renamed scripts/lan.py to scripts/runtime.py and updated launchers, tests and current documentation.
- Kept four runtime/cleanup files in scripts/. Moved validation scripts to tests/ and maintenance tools (including migration) to tools/. Removed agent-login.sh and check_viewer.py wrappers; their underlying commands remain available.
- AGENTICROS_TEST_IMAGE_TAG=robot-types bash tests/test.sh: 95 tests passed. Initial default-tag run stopped because viewer:single was absent; validation used existing robot-types images.
- python3 tests/check_startup.py --image-suffix robot-types: PASS, four isolated containers, ROS queries, repeated startup, independent Viewer and rule hot-sync. Evidence: runs/checks/agenticros-startup-check-f0ab0b82/result.json. Single host only; test containers removed, evidence and test volumes retained.


## Compose filenames (2026-11-21)

Renamed compose.yaml to compose.agent.yaml and compose.client.yaml to compose.robot.yaml; removed the redundant .env. Updated launcher, fixtures, direct Compose tests and current documentation. Historical commands above retain their original names. Validation: 95 tests passed using robot-types images; isolated four-container startup/ROS/hot-sync test passed, evidence runs/checks/agenticros-startup-check-b18c68f5/result.json. Test containers removed; evidence and test volumes retained.


## Interactive robot shell (2026-11-21)

robot.sh now requests runtime.py robot --shell: start detached, wait for health, then docker compose exec Bash when stdin is a terminal. ROS and the optional driver workspace are sourced. EOF exits only the exec shell. Non-interactive invocations remain detached; --robot-id with --shell reuses an existing instance.

Validation: 95 tests passed; isolated four-container startup/ROS/hot-sync PASS at runs/checks/agenticros-startup-check-06d0892f/result.json. Real PTY launch, ROS topic listing, Ctrl+D, unchanged container start time, healthy driver and fresh ROS subscription after EOF passed at runs/checks/robot-shell-6a323755/result.json. An initial check incorrectly expected a robot ID in the default truncated ros2 echo output; corrected to verify message reception. All test containers removed, evidence retained.


## Plain RViz Viewer (2026-11-21)

Removed robot selector, sensor/TF/session subscriptions, automatic per-robot RViz configuration and discovery-triggered restarts. The status bar lists graph-discovered publishing robots only; RViz starts with a static grid and user-selected topics/frames. Open RViz is idempotent while the window is open and reuses the saved config after manual close. Removed obsolete configuration/dock tests, added discovery-churn preservation coverage.

91 tests passed. Separate-container robot join/leave preserved RViz PID and saved manual config: runs/checks/agenticros-viewer-stable-400b319a/result.json (desktop.png visually inspected). Two ALOHA robots, native camera access, noVNC authentication and independent Viewer lifecycle passed: runs/checks/agenticros-check-5c0376e8/result.json. Updated domain 42 Viewer only; previous config saved at runs/42/agent_instance/viewer/before-plain-rviz.rviz. Agent and robot containers were not restarted.


## Reliable sensor publishers (2026-11-21)

Both robot drivers now publish RGB, compressed RGB, depth, CameraInfo and PointCloud2 with Reliable/Volatile/Keep Last 5. Rebuilt both :single images; running user robots were not restarted. 92 unit tests passed. Initial simultaneous 40-reader test missed one Best Effort raw RGB stream; this did not affect any Reliable subscriber. Sequential Reliable and Best Effort groups received all 20 topics (40 subscriptions total), followed by synchronized observations and rule hot-sync: runs/checks/agenticros-robot-docs-check-a8443c4d/result.json. The initial timeout is retained in /tmp/reliable-sensors-integration.log; passing run in /tmp/reliable-sensors-integration-retry.log. This establishes bounded same-host compatibility, not loss-free Best Effort delivery under load or cross-host validation.
