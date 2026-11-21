# my_robot 公开说明（接入模板）

## 型号与版本

- 型号：my_robot（必改：与型号目录一致）
- 驱动/接口版本：填写与部署镜像对应的版本
- 当前实现：只有 ROS 通信骨架，没有连接硬件，不支持运动

## 身份与状态

机器人实例前缀为 `/robot/<AGENTICROS_ROBOT_ID>`；启动器自动分配 ID。
同一实例重启保留 ID，驱动进程重启更新 session_id。
以下端点均相对此前缀：

| 端点 | 类型 | 说明 |
| --- | --- | --- |
| `driver/state` | `std_msgs/msg/String` | 2 Hz JSON，包含 robot_id、namespace、session_id、reported_at_unix、state、control_available |
| `driver/status` | `std_srvs/srv/Trigger` | message 返回相同 JSON；success 仅表示查询成功 |

模板始终报告 `hardware_connected=false`、`control_available=false`。
健康检查只验证节点回调循环，并不证明硬件连接或机器人能执行任务。

## 实际机器人接入时填写

- 硬件/仿真类型、启动前提、驱动配置项。
- 真实传感器 Topic：名称、消息类型、QoS、频率、时间戳语义。
- 坐标系、标定、单位、关节范围和速度/力限制。
- 控制 Service/Action：参数、动作 UUID、session 校验、并发约束、反馈及错误。
- 取消、停止确认、断连及重启后的状态核对方式；超时不自动重发。
- 自定义接口定义放在本目录的 `interfaces/`；仅使用标准接口时无需此目录。

公开资料供 Agent 只读访问，不放凭据、私有实现、隐藏场景或评测答案。
观测通过原生 ROS Topic 读取，需要保存时由 Agent 写入自己的工作区。
