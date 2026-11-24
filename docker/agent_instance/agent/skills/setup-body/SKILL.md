---
name: setup-body
description: 适配未知 ROS 2 环境或补充缺失能力，发现接口、开发验证功能节点并记录可复用能力。
---

先读取 `/workspace/contract.md`；身份、观测、动作及节点管理遵守其中约定。

## 适配流程

1. **发现接口。** 用 `ros2 node list`、`ros2 topic list -t`、`ros2 service list -t`、`ros2 action list -t` 查询在线接口，核对类型、QoS 和控制语义。发现异常时用 `ros2 topic list --no-daemon --spin-time 3` 检查新 participant，常驻客户端成功不能替代冷发现验证。
2. **取得公开定义。** 按实际型号和版本查阅只读 `/workspace/robot_docs/<型号>/manual.md` 与 `interfaces/`；目录存在不代表机器人在线，也没有 knowledge ROS Service。需要自定义绑定时，将公开接口源码复制到 `/workspace/ros_ws/src/<包名>/` 构建；发现类型名不代表已有绑定。
3. **补齐并验证。** 优先复用接口；缺能力时开发最小 ROS 节点，缺模型时利用观测、标定和局部辨识分阶段试验。保存源码、启停方法和证据，按下文登记能力，随后继续用户任务。

观测直接订阅原生 ROS 传感器话题，按契约配对时间戳、坐标系并检查新鲜度。传感器订阅需匹配 QoS；常驻客户端摘要不含原始像素。需要文件时自行保存到 `evidence/`，没有快照 Service 或 HTTP 下载接口。

## 常驻客户端

需要持续订阅或跟踪动作时，主 agent 与子 agent 共用 `agenticros-ros graph/status/request`，不另起 Server。`request` 接收 JSON，支持 `service/read/goal/result/cancel`；动作需 `goal_id`、`request.session_id`、`status_endpoint`，端点与类型从公开接口发现。

构建新绑定后，确认没有未核对动作，再执行 `python3 /opt/instance/runtime.py clients-reload`。CLI 和自行开发的节点按需加载 workspace overlay；重载或切换客户端不得重发未核对动作。

## 能力清单格式

保存为 `/workspace/capabilities/manifest.json`，以下字段必填，按实际发现和验证记录：

| 字段 | 内容 |
| --- | --- |
| `schema_version` | 整数 `1` |
| `environment` | 非空对象，记录身份、session 及核对方式；未知项如实说明 |
| `capabilities` | 能力对象数组，格式见下文 |
| `generated_nodes` | 自行启动的节点数组；仅复用环境接口时可为空 |
| `limitations` | 限制与未验证内容的字符串数组，可为空 |

每项 capability：

- `name`：唯一名称；`kind`：`topic/service/action`。
- `endpoint`：完整 ROS 名称；`interface_type`：对应的 `包/msg/类型`、`包/srv/类型` 或 `包/action/类型`。
- `origin`：`environment/generated`；`status`：`verified/unverified`。
- `description`：用途、单位、坐标系、限制和反馈含义；`invocation`：含 overlay 与超时说明的调用模板。
- `evidence`：证据文件路径数组；`verified` 至少一项，未验证时可为空。

每项 generated node 必填：`node_name`（唯一完整 ROS 名称）、`package`、`source_path`（现有源码目录）、`start_command`、`stop_command`、`health_command`、`log_path`（实际日志文件）。文本字段非空；清单内文件路径均相对 `/workspace`，不得越界。已验证的生成能力须有节点记录。

清单验收要求至少一项已验证能力，检查证据非空、已验证接口的在线类型及生成节点唯一在线状态，不执行清单命令。验收范围与限制见 `contract.md`；尚未通过验收可继续开发验证，清单不代替实时观测。

## 示例：发现并登记已有话题

在 Agent 内执行 `ros2 topic list -t`。假设发现 `/example/state [std_msgs/msg/String]`，可只读接收一条消息并保存证据；实际使用时替换为发现的话题和类型。

```bash
mkdir -p /workspace/evidence
set -o pipefail
timeout 10s ros2 topic echo /example/state std_msgs/msg/String \
  --once --qos-reliability best_effort | tee /workspace/evidence/example-state.txt
```

成功收到消息、核对来源身份与当前 session 后，可在 `capabilities/manifest.json` 中登记如下。身份占位符须换成实际记录；本例只验证接收，无需启动节点。未完成验证时填 `unverified`，证据数组可为空；不要把示例当成验证结果。

```json
{
  "schema_version": 1,
  "environment": {
    "identity": "<实际身份>",
    "session_id": "<当前SESSION>",
    "verification": "<实际核对方法>"
  },
  "capabilities": [{
    "name": "state_stream",
    "kind": "topic",
    "endpoint": "/example/state",
    "interface_type": "std_msgs/msg/String",
    "origin": "environment",
    "status": "verified",
    "description": "读取状态文本；仅验证消息接收，字段语义尚未验证",
    "invocation": "timeout 10s ros2 topic echo /example/state std_msgs/msg/String --once --qos-reliability best_effort",
    "evidence": ["evidence/example-state.txt"]
  }],
  "generated_nodes": [],
  "limitations": ["未验证状态字段语义、运动或任务能力"]
}
```
