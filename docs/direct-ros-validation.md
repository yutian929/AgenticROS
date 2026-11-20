# 直接加入 ROS 2 网络：验证记录

测试日期：2026-11-20。结果限定为一台 macOS 主机上的 Docker Desktop；没有真实硬件动作，没有跨物理机器或原生 Linux 主机验证。所有动作在新建隔离仿真中执行，账号、已有工作区、runs 和数据卷未清理。

## 实现与故障定位

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

这不是 HTTP 注册故障，也不是只能用常驻客户端规避的冷发现问题。新增一个同版本、局部、可审计的 Fast DDS 补丁：统一 profile 配置 `max_logical_port=0`，按公开的准确逻辑端口协商；被拒绝后，在后续发送中最多每 100 ms 重试一次协商，保持单个在途协商，重连清除旧协商事务。没有动作/RPC重放。其他配置保留上游扫描行为。所有镜像构建相同 SHA-256 固定源码与补丁，运行时不包含诊断日志或中间源码；首次构建多一个中间件编译阶段，后续共享缓存。详见[补丁和构建测试](../docker/shared/fastdds/README.md)。

底层确定性测试故意让逻辑端口比 TCP 监听晚 1.5 秒开放：未修补版本在 3 秒恢复窗口内失败；诊断修补版连续 5 次恢复，每次在首个 50 ms 轮询间隔内观察到成功。三容器诊断修补版通过 5 次发现服务重启和整个 Agent 重启；诊断耗时不用于宣称最终性能。正式 Release 镜像扩大验证结果单列。

参考：[TCP 发送与清理](https://github.com/eProsima/Fast-DDS/blob/v2.6.12/src/cpp/rtps/transport/TCPTransportInterface.cpp)、[pending port 状态](https://github.com/eProsima/Fast-DDS/blob/v2.6.12/src/cpp/rtps/transport/TCPChannelResource.cpp)、[上游保留 pending port 的背景](https://github.com/eProsima/Fast-DDS/pull/4324)。本补丁是仓库本地修复，不冒称上游已合并。

未采用的对照包括：只删除 loopback、纯 TCP 不带 SHM、显式 WAN locator、调整 logical port、25 秒断开等待、缩短 DDS lease、65000 高端口、原生 Discovery Server CLI。这些没有解决复现故障。固定 UDP 的独立容器小样本通过；但 macOS 实际主机 LAN 地址下的 UDP/UDP发现+TCP数据对照出现图可见而状态或服务不可用，MTU/缓冲区调整仍未同时通过。保留失败日志，部署仍统一 TCP 固定发现，没有切换自动发现；这些同机 UDP 失败也不能外推为真实跨机结果。

**HTTP 与数据分层。** 旧 HTTP 租约会在机器人立即重启时返回 409，造成与 DDS 不同的接入阻塞；该路径已删除。图像快照的签名 HTTP 文件下载仍保留，和 ROS 图像订阅分别验证。ROS 图已发现、服务存在、服务实际回应、状态新鲜和控制空闲分别报告。

**离线动作测试的时间预算修正。** 首次 TCP 集成测试在外层 40 秒等待上限结束，不能据此判断通信失败。保留夹具后的诊断中，发现服务离线期间已有 status RPC 正常回应；30 秒仿真动作在约 41.9 秒墙钟时间返回 `complete=true`、`stop_confirmed=true`。外层测试等待改为与既有 120 秒结果超时一致，并增加状态接收年龄和结果到达时间记录。动作请求仍只发送一次；未改变驱动执行、自动重试或生产超时策略。失败运行及诊断回执见[证据目录](evidence/direct-ros-network/lan-outage-time-diagnosis/)。最终完整重跑结果单列。

**同机地址归一化：旧节点的新端点无法传出。** 一次恢复后 status 请求在 45 秒内没有响应。扩大为每次 2 秒、总窗口 30 秒的只读恢复采样后，运行 `4889825c` 和诊断运行 `e8eded64` 再次失败：旧节点已有的 robot_a 客户端正常，新建 robot_b 客户端连续 15 次请求超时。诊断驱动在该窗口没有记录 status 回调；随后全新原生 `ros2 service call` 成功。进一步在 `284553cc` 的旧节点记录到 Fast DDS `transform_remote_locator` 将同机 Server 地址转换为 `127.0.0.1`；`CloseOutputChannel` 的保留列表仅包含 LAN 普通/WAN 地址，随后明确记录 `CLEAN ERASE TCPv4:[127.0.0.1]:11811-0`。因此已有机器人数据通道继续工作，新端点发现却无法发到 Server。现在仅在 advertised_host 等于 Server 地址时加入 loopback initial peer，保留该优化产生的发送资源；不向远端公布 loopback，也不切换 DDS 配置。两次完整 TCP 修复后运行（`2fb4b5d7`、`84fa4abd`）中，两台机器人的恢复后首次 status 均成功，无需诊断重试；第二次的全部样本见下表。所有重试仅为只读诊断，动作、取消和结果请求均不重放。[地址清理 trace](evidence/direct-ros-network/loopback-discovery-cleanup/trace-excerpt.txt)与[长中断失败样本](evidence/direct-ros-network/long-outage-failure-e8eded64/)。

**RViz 首条状态触发重复启动。** 最终 UI 检查捕获到图已完整、相机已收到数据，但 session 从未知变为首次已知时面板又重建一次。现在把首次获知身份、状态暂时变旧与真正 session 变化分开：前两者不重启 RViz，已知 session 变更仍清理旧视图。新增 UI 回归覆盖该顺序；修复后 Viewer 生命周期、三路实际图像与面板稳定性均通过，并人工检查了桌面截图。该 UI 修复发生在最终网络验收之后，仅重建 Server 的 Viewer 层；中间件及网络 profile 未再改变，各运行的精确镜像 ID 保留在 topology.json。保留[失败事件](evidence/direct-ros-network/viewer-session-learning-failure/events.jsonl)。

## 测量方法

`check_native_ros.py` 使用一个 Agent 容器和两个机器人容器，三个不同 IP、网络名称空间和私有 IPC；不以单容器代替通信验证。测试域独立，禁用对外路由。另用 `check_startup.py` 测实际 Compose host 网络和主机 LAN 地址回环；仍只有一台物理机器。

每轮记录：停 CLI daemon 后原生 `ros2 topic list`、新进程 `topic list --no-daemon --spin-time 3`、原生状态与压缩图像订阅、原生 status Service、两个机器人并发 Service。重启验证包含原有订阅跨机器人重启、新 session，以及发现服务进程和整个 Agent 容器重启后的新 CLI 调用。

Humble `ros2 topic list` 默认冷启动等待窗口为 0.5 秒，可能返回不完整图快照。保留其全部缺失样本；常驻/daemon 缓存不是验收替代品。必须通过 3 秒窗口的新 participant 检查、数据订阅和 RPC；`--strict-default` 可让默认窗口的任何缺失也导致测试失败。默认窗口和 3 秒窗口的结果分开报告。统一 DDS 同步周期从默认 450 ms 改为 100 ms，但不承诺 0.5 秒内获得完整动态图。

原生 CLI 的 `total_ms` 包含入口脚本、Python/ROS 初始化、发现和退出；Service 的 `discovery_ms` 是启动至 CLI 打印发送请求的时间上界，`response_ms` 是打印发送至打印响应。它们不是纯 DDS 发现耗时。`network_probe.py` 另测 `wait_for_service` 与 RPC；常驻客户端统计另列。原生 CLI 分位数采用 nearest-rank（另一套 RPC 汇总 P50 为中位数），失败耗时也保留，不能仅用成功样本掩盖超时。

`discovery_failure` 表示图缺项或服务未进入发送阶段；`request_timeout` 表示有界请求/样本等待到期（订阅未收到样本时不能断言已完成匹配）；`server_failure` 表示调用返回失败或执行异常。每组样本数、成功率、P50/P95/最大值见以下结果及 JSON。

<!-- RESULTS -->

## 原生 CLI 实测结果

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

## 冻结旧镜像的分段计时补采

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

## lan-tcp：常驻、独立 ROS 进程与图像下载

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

## lan-udp：常驻、独立 ROS 进程与图像下载

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

## 回归与覆盖

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

## 复现与限制

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
