---
name: perception
description: 需要检测、分割或定位目标时，发现并复用在线 ROS 感知能力；包含 SAM3 的用途和使用流程。
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
