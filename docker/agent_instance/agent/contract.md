# AgenticROS ROS 能力契约

本文约定 ROS 能力的调用、执行和记录格式；任务原则见 `AGENTS.md`，适配流程见 `.agents/skills/setup-body/SKILL.md`。不预设设备接口或要求特定适配类。

## 能力接口

- 优先复用已有接口，无需额外包装。新能力通过 ROS 提供：Topic 承载连续状态，Service 用于快速请求，Action 用于需要反馈和取消的耗时操作。
- 能力负责可复用的感知、计算、规划和控制，参数化表达目标；整个用户任务及跨步骤决策由会话负责。开发时可用临时脚本探索，持续依赖的能力再沉淀为节点。
- CLI 或节点均可调用，只要能保留请求标识、反馈、取消和状态核对。观测应注明采集时间、坐标系及相关 session，供调用者判断新鲜度和归属。
- `unverified` 能力可以进入开发试验。根据公开接口和观测，通过计算、标定或分阶段仿真验证适用范围，再用于后续阶段；缺少完整模型不阻止可独立验证的工作。

## 常驻通信客户端

优先用 `agenticros-ros graph/status/request` 访问共享的常驻 ROS 进程，复用订阅和 Service/Action Client，避免每次轮询启动 ROS CLI。`request` 接收 JSON，支持 `service/read/goal/result/cancel`；端点与类型从公开接口发现。发送动作需 `goal_id`、`request.session_id`、`status_endpoint`；超时的 `outcome=unknown` 仍须按下文核对，查询成功不代表停止成功。

机器人端点固定为 `/robot/<robot_id>/...`，session 放在状态、请求和回执中，不进入端点名称。按机器人隔离能力、证据和坐标系；重启或 session 改变后清除旧观测缓存并读取当前状态。分别判断进程存活、ROS 发现、服务响应、观测新鲜度和控制可用性；机器人直接加入 ROS 网络，无 HTTP 注册或控制租约；发现服务离线不代表已接受动作停止，`status` 的样本新鲜度不代表图像采集时间。

机器人观测使用原生 ROS 传感器话题；Agent 按需订阅并在自身工作区保存。RGB、深度和 CameraInfo 配对时间戳与坐标系，动态 TF 对应采集时刻；状态独立检查新鲜度与 session。不存在机器人快照文件 Service 或 HTTP 观测下载。

## 动作执行

- 发送前核对机器人身份、session、实测及忙碌/恢复状态，确认单位、坐标系、自由度、限制与取消方式。拒绝非有限值、不支持的自由度、超限或已知不可达输入；未知可达性通过计算或分段试验确认。运动需考虑障碍和净空。
- 为目标分配并记录 UUID，携带当前 session，只发送一次。接受或结果超时后按 UUID 查询实际状态，不自动重发；session 改变后查不到旧回执，也不证明旧动作未执行。
- 取消后取得终态回执并核对实测停止。客户端退出、超时或节点中断均不代表动作停止。身份、动作归属或停止状态不明时暂停该臂后续动作，继续可行的观测、诊断和能力开发。
- 结果记录完成情况、执行程度（`none`、`some`、`unknown`）、停止确认、实测状态、session、UUID 和原因，按需附误差。根据反馈决定下一步；关键阶段重新观测，伺服完成不等于抓取或任务成功。

## 工作区与进程

- `/workspace/robot_docs/`：按型号分类的只读公开资料，不代表在线机器人；核对适用型号和版本，不在此写入或构建。
- `/workspace/ros_ws/src/`：生成的 ROS 包和取得的公开接口源码；在 `ros_ws` 内构建。
- `/workspace/capabilities/manifest.json`：能力清单，格式见下文。
- `/workspace/evidence/`：观测、动作回执、验证记录和进程日志。

所有节点继承同一 ROS_DOMAIN_ID，使用原生 DDS 自动发现，不按任务或子 agent 改变网络配置。新接口绑定构建后，在无未核对动作时用 `python3 /opt/instance/runtime.py clients-reload` 仅重载通信客户端。

节点应有可重复的构建、启停和健康检查方法。启动前检查已有实例；用受管理进程或后台 launch 保持运行，记录 PID/进程组和日志，各终端按需加载 overlay。只停止自己管理的进程；退出控制节点前核对动作与停止状态。重启或换会话后重新核对环境，启动节点不得自动运动或重放目标。

## 能力清单

保存 JSON 对象，以下字段均需提供；按实际发现和验证填写。

| 顶层字段 | 内容 |
| --- | --- |
| `schema_version` | 整数 `1` |
| `environment` | 非空对象，记录身份、会话及核对方式，未知项如实说明 |
| `capabilities` | 能力对象数组，字段见下文 |
| `generated_nodes` | 自行启动的节点数组；仅复用环境接口时可为空 |
| `limitations` | 限制和未验证内容的字符串数组，可为空 |

每项 capability：

- `name`：唯一标识；`kind`：`topic`、`service` 或 `action`。
- `endpoint`：完整 ROS 名称；`interface_type`：与 kind 对应的 `包/msg/类型`、`包/srv/类型` 或 `包/action/类型`。
- `origin`：`environment` 或 `generated`；`status`：`verified` 或 `unverified`。
- `description`：用途、单位、坐标系、限制及反馈含义。
- `invocation`：含必要 overlay 和超时说明的调用模板。
- `evidence`：相对 `/workspace` 的非空证据文件路径数组；未验证时可为空，`verified` 至少有一项。

每项 generated node：`node_name`（唯一完整 ROS 名称）、`package`、`source_path`（现有源码目录）、`start_command`、`stop_command`、`health_command`、`log_path`（实际日志文件）。路径相对 `/workspace`，不得越出工作区；文本字段非空。已验证的生成能力须有节点记录。

验收要求至少一项已验证能力，核对清单、证据文件、已验证接口的在线类型及生成节点唯一在线状态；不会执行清单里的命令。验收通过不证明功能、停止状态或任务成功。尚未通过验收不妨碍继续开发验证，清单也不能代替实时观测。
