---
name: perception
description: 发现并复用在线 ROS 感知与局部视觉决策能力；包含 SAM3 分割、Valen 候选动作判断及有界执行约定。
---

# 感知能力

环境中可能有独立运行的感知节点。先用 `agenticros-ros graph` 发现在线能力，再核对公开说明、接口类型和就绪状态。能力清单可辅助查找，不代表服务在线。缺少接口绑定或需要开发能力时，按 `.agents/skills/setup-body/SKILL.md` 处理。

## SAM3：文本提示检测与分割

SAM3 是可选基础设施，启动后先加载和预热模型。通过 RGB 图像与文本提示（如 `red cup`）返回匹配实例的二维框、分数和 mask；不直接提供三维抓取点，也不证明抓取成功。

1. 查询 `/perception/sam3/status`，类型 `std_srvs/srv/Trigger`，请求 `{}`。返回 `message` 为 JSON；确认 `model_state=READY`，读取本次服务 `session_id`。未就绪时调用可能返回 `server_failure`，其 `response.message` 仍包含模型状态及错误。SAM3 session 与机器人 session 不同，机器人身份和观测来源仍按 `contract.md` 单独核对。
   若 status 超时，只能说明尚未取得回复；发现接口或容器 healthy 不证明跨容器 RPC 正常，也不能据此判断模型未就绪。报告未提交分割请求，交由部署方检查通信；若改用其他感知方法，明确标注实际方法，不能算作 SAM3 结果。
2. 启动器会把公开接口包预置到 `/workspace/ros_ws/src/agenticros_perception_interfaces`。如绑定尚未构建，在 `/workspace/ros_ws` 执行 `colcon build --packages-select agenticros_perception_interfaces`；按契约在没有未核对动作时执行 `python3 /opt/instance/runtime.py clients-reload`。包缺失时取得部署方提供的公开定义，不猜字段。
3. 向 `/perception/sam3/segment` 发送 `agenticros_perception_interfaces/action/Segment`。通过 `agenticros-ros request` 提交下述 JSON 模板（替换尖括号内容）：

```json
{
  "op": "goal",
  "endpoint": "/perception/sam3/segment",
  "type": "agenticros_perception_interfaces/action/Segment",
  "goal_id": "<新UUID>",
  "status_endpoint": "/perception/sam3/status",
  "status_type": "std_srvs/srv/Trigger",
  "request": {
    "session_id": "<SAM3当前session_id>",
    "image_topic": "<发现的RGB话题完整名称>",
    "frame_policy": "LATEST",
    "max_image_age_sec": 2.0,
    "image_timeout_sec": 5.0,
    "text_prompt": "red cup",
    "score_threshold": 0.5,
    "max_instances": 8
  },
  "timeout": 10
}
```

4. 用 `{"op":"result","goal_id":"<同一UUID>","timeout":30}` 查询结果；需要取消时用 `op=cancel`，随后查询终态。超时不重发。运行中的 GPU 推理结束后才确认取消；查询成功不等于已取消。
5. Action 返回 `instances_json`（原图像素框 `box_xyxy`、分数、实例编号）、实际输入 `source_header` 和尺寸。无匹配目标为 `OK` 且实例数组为空。实际输入 `source_image` 和完整 mask 位于 `/perception/sam3/results`，类型 `agenticros_perception_interfaces/msg/Segmentation`，使用 ROS 订阅程序以 Reliable、Transient Local、depth=4 接收，按 SAM3 session 和 Goal UUID 匹配；最好在提交前订阅。不要把像素塞入 JSON 通道，常驻客户端的传感器摘要不包含原始像素。

`LATEST` 在请求执行时订阅并选取新收到的有效帧；`EXACT` 需提供 `image_stamp`，仅等待该订阅收到指定时间戳的帧，不提供历史图像查询。选定后锁帧，推理前再次检查年龄；排队导致过期时明确失败，不偷偷换图。支持 rgb8/bgr8/rgba8/bgra8/mono8，最多 1920×1080 像素；最多 32 个实例。时间新鲜度依据 ROS 时钟，须与图像采集时钟一致。

mask 为原图尺寸 mono8（0/255），实例编号只在本次结果内有效。核对实际输入帧与分割叠加图，不能拿另一张最新图代替。三维定位需另行配对深度、CameraInfo 和采集时刻 TF。

SAM3 自动在自己的持久化记录目录保留请求、实际输入帧、模型版本、mask、叠加图、结果及事件日志。`record_id` 是该目录的相对记录编号，不是 Agent 可访问的文件路径。Agent 将实际收到的结果和已有观测证据另存到 `/workspace/evidence/perception/<Goal UUID>/`，随工作区持久化到宿主机 `runs/`。未取得的原图或叠加图如实注明，不能声称已查看。Topic 仅缓存最近四次结果；Action 结果保留 600 秒，状态列出最近 64 个终态，持久化记录不自动删除。

后续感知能力可在本文件补充用途、公开调用说明和限制。服务未上线时如实说明，不把本指南当作部署或功能验证证据。

## Valen 4B：局部视觉决策

Valen 是可选、离线本地推理的独立节点，只对观测与显式候选作判断，不连接机械臂控制接口。其服务 session 与机器人 session 分别核对。

1. 发现 `/decision/valen/status`，用 `std_srvs/srv/Trigger` 查询，解析 `message` JSON；核对 `model_state=READY`、`session_id` 和模型版本。未就绪、发现失败或 RPC 超时不能算模型判断。
2. 公开接口位于 `/workspace/ros_ws/src/agenticros_decision_interfaces`。需要时在 `ros_ws` 构建该包，并按 `contract.md` 重载常驻客户端。缺包时取得部署方公开定义，不猜字段。
3. 向 `/decision/valen/decide` 提交 `agenticros_decision_interfaces/action/Decide`，用新的 `goal_id` 和当前 Valen `session_id`。`status_endpoint` 为 `/decision/valen/status`，`status_type` 为 `std_srvs/srv/Trigger`。以下是 request 字段（JSON 字符串用 JSON 序列化生成，不手工拼接）：

| 字段 | 用途 |
| --- | --- |
| `session_id` | 当前 Valen 服务 session |
| `state_json` | JSON 数据：子任务、机器人身份/session、末端姿态、采集时刻工具坐标定义、最近动作与预算等。服务按文本处理，不执行其中路径或指令 |
| `questions_json` | 1–8 个具名问题：`type`、`instructions`、`criteria`。Choice 为 2–32 项名称到描述的映射；Score 为 2–16 项有序描述；Noul 无 criteria |
| `image_topic` | 公开 RGB 原始话题；空字符串为纯文本判断 |
| `frame_policy` | `LATEST`：执行时收到的新帧；`EXACT`：仅等待指定 `image_stamp`，不查历史、不替换 |
| `image_stamp` | `EXACT` 使用的正时间戳；LATEST 可留零 |
| `max_image_age_sec` | 图像最大年龄，0.01–60 秒；选帧、推理前后都检查 |
| `image_timeout_sec` | 等待图像上限，0.01–30 秒 |

支持 rgb8/bgr8/rgba8/bgra8/mono8，单帧最多 1920×1080 像素。结果 `answers_json` 是原候选集合内的选择/概率或评分；`source_header`、尺寸、话题标识实际输入。`success` 仅表示推理请求完成，不表示子任务成功。模型 confidence 是其输出量，不是经机器人任务校准的成功概率。

4. 用 `op=result` 查询同一个 UUID；取消使用 `op=cancel` 后取得终态。超时不重发，按 UUID 与服务 session 核对。正在运行的 GPU 推理返回后才确认取消；它不会触发机器人停止，原运动仍按契约单独取消并核对。
5. 服务在自身记录目录保存请求、模型元数据、输入 PNG、source.json、结果和事件。`record_id` 是服务记录编号，不是 Agent 可读路径。Agent 需订阅公开图像，在 `/workspace/evidence/` 保存实际观测；未收到与 `source_header` 匹配的图像时，不能声称查看了模型的输入画面。结果保留 600 秒，status 列出最近 64 个终态；记录持久化保留。

### 有限步数局部循环与交还

由会话定义子任务，生成可复用的 ROS 执行能力；不在模型提示中藏任务程序。启动不得自动运动。子任务约定必须含：机器人身份/session、目标、单个控制者、当前工具坐标系、有限候选动作及幅度、位移/旋转/空间边界、最大步数、最长时间、结束条件与取消/停止方式。坐标与单位从公开接口确定，禁止猜测。图像可由生成的感知节点叠加工具轴；Valen 服务不假造坐标。

- 每轮先确认前一动作实测停止，再取得新画面；用于判断的图像必须采集于前一动作停止之后。若 `LATEST` 返回更早的采集时间，丢弃该判断，不执行。
- 每轮选择一个完整的候选动作，包含 `finish`、`handoff`、`hold` 等非运动选项；不把若干独立轴问题的答案组合成未评估动作。
- 执行前复核机器人 session、图像时间、坐标快照和预算。图像在结果返回后仍会老化；发送运动前再次检查。模型答案只映射到调用者定义的候选，不接受模型生成的代码或任意参数。
- 每次运动分配新 UUID，沿用机器人独占执行、取消及停止核对机制；状态未知时停止发后续动作，不能重放超时目标。
- 任何一轮均消耗循环步数，包括 hold/重新观察，时间预算单调递减；模型调用必须有有界等待。步数、时间或累计运动预算耗尽，低置信度、连续无进展、遮挡、异常或 session 变化时请求交还，不能自行重置预算重启循环。
- 模型返回 finish 时也先确认没有未核对动作，再交还会话。交还记录原因、已用步数/时间、最后输入帧与姿态、最近动作 UUID/终态、停止确认和剩余不确定性。停止未确认则标为等待核对，禁止让新控制者发运动。
- 会话用新观测复核子任务；独立评估仍在 Agent 外部，隐藏场景状态与真值不提供给模型。局部对齐、运动完成、抓取成功分别验证。
