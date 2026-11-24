# Quanta X1 整机 — G 夹爪，桌面任务模式

仅仿真。使用 X2Real `v00.05.05/ex001.usd`，包含底盘、升降、头部和双臂；每臂六个旋转关节和 G 型联动夹爪。公开上游称其为 Quanta X1 / EX001，尚不能证明它与特定 X1Pro 硬件修订及标定完全一致。

## 初始状态与范围

整机初始世界位置为零，绕 z 轴转 180°；升降 0.379 m、头部 yaw 0、pitch −15°。桌面模式固定底盘并保持升降及头部初始目标。当前接口只控制双臂和夹爪，不提供底盘、升降或头部运动动作。

## 发现和身份

命名空间 `/robot/<robot_id>`，每次进程启动更新 `session_id`。左右臂分别提供 `.../<left|right>/driver/state` (`std_msgs/msg/String`) 和 `.../<left|right>/driver/status` (`std_srvs/srv/Trigger`)。

状态包含关节位置、速度、限位、两指被动关节角及速度（left finger、right finger）、仿真时间、反馈时间、当前动作 UUID、历史回执、`feedback_fresh`、`recovery_required` 和 `control_available`。`body` 给出升降/头部实测状态及底盘位姿。发现节点或 RPC 成功均不代表控制可用。

## 原始关节控制

IDL 在 `interfaces/`，包名 `quanta_driver_interfaces`；复制到可写工作区构建并加载。

`/robot/<robot_id>/<left|right>/driver/actuate` 为 `Actuate` Action。请求包含当前 `session_id`、7 个绝对关节目标 `positions` 和 0.1–30 秒的 `duration`。目标顺序为该臂 joint1..joint6、gripper，全部为弧度。左右臂可同时运动；同臂的 Actuate、MovePose、Recover 共用一个独占槽位。目标按仿真时间插值，受阻可能无法到达。

G 夹爪驱动角 0 为闭合，1.89 为全开，不是米制或归一化接口。保留原生 PhysX mimic 关系，电机到位不表示物体已抓牢。

公开 `kinematics.json` 可用于校核运动学：锚点用米、文件内四元数 wxyz、旋转限位为度，移动关节限位为米。双侧锚点满足父链接×父锚点×关节运动 = 子链接×子锚点。ROS 旋转关节目标用弧度，Pose 的四元数用 xyzw。

## TCP 位姿控制

`/robot/<robot_id>/<arm>/driver/move_pose`，类型 `quanta_driver_interfaces/action/MovePose`：

| 输入 | 含义 |
| --- | --- |
| `session_id` | 当前机器人 session |
| `reference_frame` | `arm_base` 或 `tcp`，含义见下表；也接受对应完整 TF frame ID |
| `target` | `geometry_msgs/Pose`，位置为米、单位四元数 xyzw |
| `duration` | 0.1–30 秒仿真轨迹时间，不含预计算耗时 |

| 参考系 | 目标含义 |
| --- | --- |
| `arm_base` / `robot/<robot_id>/<arm>/arm_base` | 该臂安装基座 `<arm>_arm_base_link` 下的绝对 TCP 位姿，与整机 `base`、`world` 不同 |
| `tcp` / `robot/<robot_id>/<arm>/tcp` | 相对接受目标时实测 TCP 的位姿增量，`T目标 = T起始TCP × T增量`；工具轴和原点仅采样一次 |

TCP 沿用夹爪基座中 `[0.217,0,-0.01]` m 的参考点。工具系平移沿起始工具轴；只旋转时将平移置零，旋转围绕 TCP 原点。夹爪沿用接受时的驱动目标，包括受阻夹持时的原指令。

执行前对整条位置直线与姿态插补路径进行 IK、关节限位和 1.5 rad/s 速度上限检查，规划预算为 10 秒墙钟；通过后才由物理线程插值执行关节轨迹。这不是避障规划，仍需根据观测检查障碍净空。规划失败不发送运动；取消或失败时转为关节保持并保留夹爪指令，随后核对新的停止证据。

Feedback 为 `current_pose`（实测 TCP，在该臂 arm_base 下）、`position_error`（米）、`orientation_error`（弧度）、`phase`。Result 的 `receipt_json` 沿用 UUID、session、执行程度和停止回执，另含 `start_pose`、解析后的 `target_pose`、`measured_pose`、`pose_frame`、误差、夹爪指令和规划错误。只有位置误差小于 0.003 m、姿态误差小于 0.03 rad 且确认停止，才返回 `complete=true`；不表示抓取或任务成功。

### ROS CLI 示例

先替换机器人 ID，读取状态和当前 TCP；确认 session、`control_available`、`stop_confirmed` 及周围观测。

```bash
quanta_ns='/robot/<ROBOT_ID>'
ros2 service call "$quanta_ns/left/driver/status" std_srvs/srv/Trigger '{}'
ros2 topic echo "$quanta_ns/left/driver/ee_pose" geometry_msgs/msg/PoseStamped --once
```

机械臂基座系下的绝对目标：将 session 及全部位置、四元数占位符换成已核对的目标。不要把整机或世界坐标直接填入。

```bash
quanta_session='<当前SESSION>'
ros2 action send_goal "$quanta_ns/left/driver/move_pose" quanta_driver_interfaces/action/MovePose \
  "{session_id: '$quanta_session', reference_frame: arm_base,
    target: {position: {x: <X>, y: <Y>, z: <Z>},
             orientation: {x: <QX>, y: <QY>, z: <QZ>, w: <QW>}}, duration: 2.0}" \
  --feedback --timeout 60
```

工具系沿当前 TCP 的 x 轴移动 5 mm，保持姿态及夹爪指令：

```bash
ros2 action send_goal "$quanta_ns/left/driver/move_pose" quanta_driver_interfaces/action/MovePose \
  "{session_id: '$quanta_session', reference_frame: tcp,
    target: {position: {x: 0.005, y: 0.0, z: 0.0},
             orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}, duration: 1.5}" \
  --feedback --timeout 60
```

每个目标仅提交一次并记录 CLI 输出的 UUID。运行中的 CLI 可用 Ctrl+C 请求取消并等待终态；超时后通过 status 的 `active_goal_id` 和 `receipts` 核对原 UUID，不能重跑命令。

## 取消与结果

按原动作 UUID 调用标准 CancelGoal。Actuate 保持当前关节位置；MovePose 保持臂关节位置并沿用夹爪指令。只使用保持指令之后的连续物理反馈确认停止。无法确认则锁定该臂，`recovery_required=true`。

CLI 已退出时，也可调用原 Action 的标准取消 Service；将 `<UUID字节列表>` 换成原 UUID 的 16 个字节，YAML 支持 `0xNN` 格式。`actuate`、`recover` 使用各自 Action 名称。取消请求接受后仍须从 status 按 UUID 核对终态回执及实测停止。

```bash
ros2 service call "$quanta_ns/left/driver/move_pose/_action/cancel_goal" action_msgs/srv/CancelGoal \
  '{goal_info: {goal_id: {uuid: [<UUID字节列表>]}}}'
```

超时按原 UUID 核对 status 的 `active_goal_id` 和 `receipts`，禁止重发动作。旧 session 被拒绝；客户端断开不会自动撤销已接受动作。

Actuate 的 `complete=true` 只表示驱动关节误差小于 0.03 rad，且连续反馈确认停止，不表示抓取或任务成功；`task_success` 始终为 null。目标受物体阻挡时可返回 `complete=false`、`reason=target_tolerance_not_reached`；若 `stop_confirmed=true` 且状态显示 `control_available=true`，可根据观测决定下一动作，不能重放超时目标。

停止确认使用每个物理步的驱动关节和被动夹指位置：连续至少 0.25 秒、至少 12 个样本，各关节累计绝对位移除以时间小于 0.02 rad/s，且区间内位置变化幅度小于 0.002 rad。重复/倒退仿真时间或超过 0.05 秒的反馈间隔会重新开始观测。使用累计变化而非首尾差，避免来回振动被误判为静止。原始求解器速度仍完整发布；`stop_evidence` 同时提供观测方法、窗口长度、样本数、位置变化与 `raw_max_speed`，接触约束下两类读数可能不一致。

### 恢复已锁定的臂

`/robot/<robot_id>/<arm>/driver/recover` 为公开 `Recover` Action，IDL 与 Actuate 一起提供。请求包含当前 `session_id` 和状态中的 `recovery_goal_id`，后者填入 `failed_goal_id`。仅接受当前 session、正确的失败动作、没有活动动作、反馈新鲜且无仿真故障的请求；恢复与 Actuate 共用同一臂的独占槽位。

恢复保持现有驱动目标，不重置姿态、不张开夹爪、不重发原动作。只有请求后的新连续反馈确认停止，才返回 `reason=recovered` 并解除锁定。持续运动、反馈异常或超时均不会解除锁定；取消恢复也保留锁定。按恢复动作自身 UUID 核对回执，原失败回执保持原样。恢复只表示运动控制可继续，抓握是否稳定、物体是否离桌仍需独立观测。

## 观测和坐标

- `<arm>/driver/joint_states`：`sensor_msgs/msg/JointState`，弧度及弧度/秒。
- `<arm>/driver/ee_pose`：`geometry_msgs/msg/PoseStamped`，由实测关节与公开运动学计算的 TCP 位姿，`header.frame_id=robot/<robot_id>/<arm>/arm_base`；时间戳对应物理反馈。status 的 `state.tcp_pose` 同时提供该位姿。
- `left`、`right`、`head` 各有 `camera/color/image_raw` 和 `camera/camera_info`，Reliable QoS。
- 左右腕为 640×480 鱼眼 RGB；头部为 1280×720 RGB。名义频率为仿真时间 30 Hz；实际墙钟频率取决于性能。使用发布的 CameraInfo，不能把鱼眼当普通针孔。
- 腕部 `distortion_model=equidistant`，D 为 k1..k4；头部 `plumb_bob`，D 为 k1,k2,p1,p2,k3。公开静态标定见 `cameras.json`。
- 无 overhead topic；第三路为整机自带头部相机，保持 USD 安装位姿。当前仅 RGB，不发布深度。
- TF 父帧 `robot/<robot_id>/world`，子帧 `base`、`<arm>/arm_base`、`<arm>/tcp`、`<camera>/camera_optical_frame`。base 是整机根帧，包含初始 180° yaw；arm_base 是对应机械臂安装基座。相机光学轴 x 右、y 下、z 前。
- `tcp` 是夹爪基座坐标中 `[0.217,0,-0.01]` m 的固定参考点，方向跟随夹爪基座；此工具参考点不同于上游使用零偏移的 EE。ROS TF 四元数 xyzw。

反馈新鲜度使用墙钟；轨迹持续时间使用仿真时间，低帧率时动作墙钟耗时更长。

## 通用机械扫码枪

场景提供独立枪式机身和弹簧回位开关。外观、贴图和尺寸采用用户提供的扫码枪资产，整体深×宽×高约 92.7×59.0×158.5 mm，保持资产原始比例。橙色扳机从原网格拆分为独立可动部件。沿用资产的 180 g 总质量设定（机身 168 g、扳机 12 g），并非实物称重结果。橙色机械开关的仿真行程设为 6 mm；压下至少 4 mm 并保持约 30 ms 仿真时间，确认一次按压。持续按住不会重复计数，松回 1.5 mm 以下后才允许下一次按压。可由一只夹爪稳定握持、另一只夹爪接触按压；没有软件按压 RPC。

`/robot/<robot_id>/scanner/state` 为 `std_msgs/msg/String` JSON，包含机器人身份、`observed_at_unix`、`observed_at_sim`、`trigger_pressed` 和 `press_count`。计数在当前 session 内累计；进程重启后重置。贴图保持资产原貌；按压状态以该话题反馈为准。

当前仅模拟机械交互，没有条码牌、光学读取或解码结果。开关状态和按压计数不证明双手协作、握持稳定或任务成功。
