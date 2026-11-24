---
name: perception
description: 发现并调用 ROS 感知与视觉判断服务，说明 SAM3 分割和 Valen 判断接口的输入、输出及使用限制。
---

# 感知与视觉判断

先用 `ros2 service list -t`、`ros2 action list -t` 发现服务；SAM3、Valen 均为可选节点，未上线时继续可行工作。其他工具先取得公开接口说明。接口包预置于 `/workspace/ros_ws/src/`：`agenticros_perception_interfaces`、`agenticros_decision_interfaces`；构建与客户端用法见 `.agents/skills/setup-body/SKILL.md`。

## 公共约定

- **状态 Service**：SAM3 为 `/perception/sam3/status`，Valen 为 `/decision/valen/status`，类型均为 `std_srvs/srv/Trigger`。输入 `{}`；输出 `success` 和 JSON 字符串 `message`，含 `model_state`、`session_id`、`model`、`error`、`active_goals`、`recent_results`。确认 `model_state=READY` 后使用该服务的 session；它与机器人 session 分开核对。发现或 healthy 不证明 RPC 正常，超时也不能断定模型未就绪。
- **Action 生命周期**：按 `contract.md` 保留 UUID、查询结果和核对取消；超时不重发。GPU 推理结束后才确认取消，取消推理不代表机器人停止。Action 结果保留 600 秒。

两个 Action 共有的输入字段：

| 字段 | 类型与含义 |
| --- | --- |
| `session_id` | string，来自对应 status 的当前服务 session |
| `image_topic` | string，公开 `sensor_msgs/msg/Image` 话题完整名称；Valen 可填空字符串做纯文本判断 |
| `frame_policy` | string，`LATEST` 取执行时新收到的有效帧；`EXACT` 等待指定时间戳，不查询历史或换帧 |
| `image_stamp` | `builtin_interfaces/Time`，`EXACT` 所需正时间戳 `{sec, nanosec}`；LATEST 可留零 |
| `max_image_age_sec` | float32，允许图像年龄，0.01–60 秒；按 ROS 时钟检查，须与图像时钟一致 |
| `image_timeout_sec` | float32，等待图像上限，0.01–30 秒 |

图像支持 rgb8/bgr8/rgba8/bgra8/mono8，最多 1920×1080 像素。选帧后锁帧；推理前检查年龄，Valen 在推理后也检查。Valen 纯文本模式忽略图像相关字段。

两个 Action 共有的输出字段：

| 字段 | 类型与含义 |
| --- | --- |
| `success`、`code`、`message` | bool、string、string，处理是否成功、结果码和说明；成功不等于机器人任务成功 |
| `session_id`、`goal_id` | string，服务 session 和本次 Goal UUID |
| `source_header`、`image_topic`、`width`、`height` | `std_msgs/Header`、string、uint32、uint32，实际输入帧的时间戳/坐标系、话题及尺寸；纯文本模式无图像 |
| `record_id` | string，服务端记录编号，不是 Agent 可读文件路径 |

反馈为 `stage`（string）和 `elapsed_sec`（float32）。结果码：`OK`、`CANCELLED`、`INVALID_REQUEST`、`FRAME_TIMEOUT`、`STALE_FRAME`、`INFERENCE_FAILED`、`RECORDING_FAILED`、`INTERNAL_ERROR`。用 ROS CLI 保存实际收到的结果和匹配输入帧到 `/workspace/evidence/`；未取得的图像如实注明。

## SAM3：文本提示分割

Action：`/perception/sam3/segment`，类型 `agenticros_perception_interfaces/action/Segment`。输入图像话题与文本提示，输出二维实例框、分数和 mask；三维定位另需深度、CameraInfo 和采集时刻 TF。

除公共字段外的输入：

| 字段 | 类型与含义 |
| --- | --- |
| `text_prompt` | string，1–512 字符的非空目标描述，如 `red cup` |
| `score_threshold` | float32，分数阈值，0–1 |
| `max_instances` | uint32，最多返回实例数，1–32 |

额外输出：

- `instances_json`：JSON 编码的实例数组，每项含 `instance_id`、`score`、`box_xyxy`；框为原图像素 `[x_min, y_min, x_max, y_max]`，x 向右、y 向下。没有匹配对象时为 `[]`，仍可返回 `OK`。
- `result_topic`：完整像素结果话题 `/perception/sam3/results`，类型 `agenticros_perception_interfaces/msg/Segmentation`。消息含 `header`、`session_id`、`goal_id`、`image_topic`、`width`、`height`、`source_image` 和 `instances`；每项实例含 `instance_id`、`score`、`box_xyxy`、`mask`。`source_image` 是实际输入，mask 为原图尺寸的 mono8 图像，背景 0、前景 255。

提交前在另一终端用 ROS CLI 接收完整结果，按服务 session 和 Goal UUID 匹配。实例编号仅在本次结果内有效，核对同一输入帧与 mask。

```bash
ros2 topic echo /perception/sam3/results agenticros_perception_interfaces/msg/Segmentation \
  --qos-reliability reliable --qos-durability transient_local --qos-depth 4 \
  --full-length
```

### 示例：分割红色杯子

以下命令在 Agent 内执行。先查询 status，将 `<SAM3_SESSION>` 替换为 READY 服务的 session，`<RGB_TOPIC>` 替换为发现的原始图像话题。目标仅提交一次，保存 CLI 输出的 Goal UUID。

```bash
ros2 service call /perception/sam3/status std_srvs/srv/Trigger '{}'
ros2 action send_goal /perception/sam3/segment \
  agenticros_perception_interfaces/action/Segment \
  '{"session_id":"<SAM3_SESSION>","image_topic":"<RGB_TOPIC>",
    "frame_policy":"LATEST","image_stamp":{"sec":0,"nanosec":0},
    "max_image_age_sec":10.0,"image_timeout_sec":5.0,
    "text_prompt":"red cup","score_threshold":0.5,"max_instances":8}' --feedback
```

成功结果中，`instances_json` 解码后可能为下例；像素 mask 仍需从 `result_topic` 接收。这里的分数和框仅示意格式，不是实际观测。

```json
[{"instance_id":0,"score":0.92,"box_xyxy":[120,80,220,240]}]
```

## Valen：视觉判断

Action：`/decision/valen/decide`，类型 `agenticros_decision_interfaces/action/Decide`。输入状态、问题和可选图像，输出具名判断；服务自身不执行机器人动作。

除公共字段外的输入（均为 JSON 序列化后的 string，各不超过 32 KiB）：

| 字段 | 内容 |
| --- | --- |
| `state_json` | 任务与观测上下文，如目标、机器人身份/session、姿态、坐标系、最近动作；作为数据处理，不执行路径或指令 |
| `questions_json` | 1–8 个问题的对象：`{"问题名":{"type":"choice/score/noul/bbox","instructions":"判断要求","criteria":…}}`，各类型格式见下表 |

额外输出 `answers_json` 为 JSON 字符串对象，问题名与输入一一对应：

| `type` | 输入 `criteria` | 对应答案对象 |
| --- | --- | --- |
| `choice` | 2–32 项名称到描述的对象，如 `{"hold":"保持","handoff":"交还"}` | `{"type":"choice","choice":"hold","probabilities":{"hold":0.8,"handoff":0.2},"confidence":0.8}` |
| `score` | 2–16 项有序描述数组，如 `["未对齐","已对齐"]` | `{"type":"score","score":0.8,"probabilities":{"0":0.2,"1":0.8},"confidence":0.8}`；score 为从 0 开始的等级期望值 |
| `noul` | 省略，判断标准写入 `instructions` | `{"type":"noul","noul":0.7}`，数值范围 0–1 |
| `bbox` | 省略，`instructions` 描述待定位目标；可选 `image: 0` | `{"type":"bbox","bbox":[0.2,0.25,0.6,0.75],"point":[0.4,0.5],"confidence":0.9}` |

`probabilities` 覆盖全部输入选项且总和为 1；choice 取最高概率选项。confidence 是模型输出，不是经任务校准的成功概率。Valen 不发布输入图像；需自行订阅并保存与 `source_header` 匹配的帧。

### 四种功能的 ROS CLI 示例

先查询状态及接口定义，确认 READY；将下面的 `<VALEN_SESSION>`、`<RGB_TOPIC>` 换成实际值。每条命令是一次独立调用，只提交一次并保存输出的 Goal UUID。超时查询原目标，不能重跑命令；CLI 运行时可用 Ctrl+C 请求取消，并继续等待终态。所有返回数值均为格式示意。

```bash
ros2 service call /decision/valen/status std_srvs/srv/Trigger '{}'
ros2 interface show agenticros_decision_interfaces/action/Decide
```

请求采用 ROS CLI 原生 YAML；`questions_json: >-` 将下方 JSON 作为字符串传入，无需 Python 或自定义客户端。

#### choice：多类别选择

```bash
ros2 action send_goal /decision/valen/decide agenticros_decision_interfaces/action/Decide '
session_id: "<VALEN_SESSION>"
image_topic: "<RGB_TOPIC>"
frame_policy: LATEST
max_image_age_sec: 30.0
image_timeout_sec: 5.0
state_json: "{}"
questions_json: >-
  {"color":{"type":"choice","instructions":"杯子的主要颜色是什么？","criteria":{"red":"红色","blue":"蓝色","other":"其他或无法判断"}}}
' --feedback --timeout 60
```

`answers_json` 解码后：

```json
{"color":{"type":"choice","choice":"red","probabilities":{"red":0.8,"blue":0.05,"other":0.15},"confidence":0.8}}
```

#### score：分级评分

```bash
ros2 action send_goal /decision/valen/decide agenticros_decision_interfaces/action/Decide '
session_id: "<VALEN_SESSION>"
image_topic: "<RGB_TOPIC>"
frame_policy: LATEST
max_image_age_sec: 30.0
image_timeout_sec: 5.0
state_json: "{}"
questions_json: >-
  {"visibility":{"type":"score","instructions":"评估杯子的可见程度。","criteria":["看不见","部分可见","完整可见"]}}
' --feedback --timeout 60
```

`answers_json` 解码后：

```json
{"visibility":{"type":"score","score":1.7,"probabilities":{"0":0.1,"1":0.1,"2":0.8},"confidence":0.8}}
```

`score=1.7` 是从 0 开始的等级期望值：`0×0.1+1×0.1+2×0.8`；本例范围为 0–2。

#### noul：命题成立概率

```bash
ros2 action send_goal /decision/valen/decide agenticros_decision_interfaces/action/Decide '
session_id: "<VALEN_SESSION>"
image_topic: "<RGB_TOPIC>"
frame_policy: LATEST
max_image_age_sec: 30.0
image_timeout_sec: 5.0
state_json: "{}"
questions_json: >-
  {"cup_present":{"type":"noul","instructions":"画面中存在杯子。"}}
' --feedback --timeout 60
```

`answers_json` 解码后：

```json
{"cup_present":{"type":"noul","noul":0.95}}
```

#### bbox：目标定位

```bash
ros2 action send_goal /decision/valen/decide agenticros_decision_interfaces/action/Decide '
session_id: "<VALEN_SESSION>"
image_topic: "<RGB_TOPIC>"
frame_policy: LATEST
max_image_age_sec: 30.0
image_timeout_sec: 5.0
state_json: "{}"
questions_json: >-
  {"cup":{"type":"bbox","instructions":"定位红色杯子。","image":0}}
' --feedback --timeout 60
```

`answers_json` 解码后：

```json
{"cup":{"type":"bbox","bbox":[0.2,0.25,0.6,0.75],"point":[0.4,0.5],"confidence":0.9}}
```

每题返回一个归一化框 `[x1,y1,x2,y2]` 和中心点 `[x,y]`，x 向右、y 向下；像素坐标分别乘实际输入的 `width`、`height`。坐标对应 `source_header` 指定的原图，三维定位另需深度与 TF。框本身不证明目标存在，可同时用 noul 核对存在性。

一次请求可在 `questions_json` 中合并 1–8 个具名问题，四类可混用并共享同一输入帧。前三类也可纯文本调用：将 `image_topic` 置空，并在 `state_json` 中填写 JSON 文本上下文；bbox 必须提供图像，`image` 只能为 0。

### 查询原目标

Valen 提供 `/decision/valen/result`，类型 `agenticros_decision_interfaces/srv/DecisionResult`。输入当前服务 `session_id` 和原 `goal_id`；输出 `success`（是否找到）、`state`、`message`、`result_json`。运行中返回阶段，终态返回完整 Action 结果的 JSON 字符串；`UNKNOWN` 或 `SESSION_MISMATCH` 都不证明目标未执行，禁止据此重发。查询读取持久记录，不受 Action 的 600 秒缓存限制。

```bash
ros2 service call /decision/valen/result agenticros_decision_interfaces/srv/DecisionResult \
  '{session_id: "<VALEN_SESSION>", goal_id: "<原GOAL_UUID>"}'
```

用于局部动作选择时：

- 会话预先定义完整候选（含保持/结束/交还）、坐标系、控制者、运动边界、有限步数与时间预算。只映射到给定候选，不拼接独立轴答案或执行模型代码。
- 每轮确认前一动作实测停止后取新图，执行前复核 session、新鲜度及预算；保持/重观测也消耗预算。预算耗尽、低置信度、无进展、遮挡或异常时交还，不自行重置循环。
- 交还记录原因、已用预算、输入帧、姿态、动作 UUID/终态与停止确认；停止不明时禁止新控制者运动。会话用新观测验收；动作遵守 `contract.md`，隐藏真值与独立评估保留在 Agent 外部。
