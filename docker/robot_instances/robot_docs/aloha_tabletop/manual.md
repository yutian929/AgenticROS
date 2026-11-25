# ALOHA tabletop public device documentation

Simulation only. Both arms share one MuJoCo world, physics clock and session.
All device endpoints are relative to `/robot/<robot_id>`. Session IDs belong in
payloads, never endpoint names. A new process creates a new session.

## Control actions

Each arm (`left` or `right`) provides:

| Endpoint suffix | ROS type | Operation |
| --- | --- | --- |
| `<arm>/driver/actuate` | `aloha_driver_interfaces/action/Actuate` | Seven absolute joint setpoints |
| `<arm>/driver/move_pose` | `aloha_driver_interfaces/action/MovePose` | Absolute or TCP-relative Cartesian pose |
| `<arm>/driver/move_delta` | `aloha_driver_interfaces/action/MoveDelta` | Cartesian increment; ALOHA extension |
| `<arm>/driver/recover` | `aloha_driver_interfaces/action/Recover` | Reconcile an unconfirmed stop |

Actuate, MovePose and Recover have the same field layout as the corresponding
Quanta actions. Use ALOHA's own package/type names; package names remain distinct.
Public IDL is in `interfaces/`; copy it into a writable workspace to build bindings.
There is no native standalone `solve_ik` service. The Cartesian actions contain
bounded numerical IK, not a grasp controller or collision-avoidance planner.

All motion goals require current `session_id` and `duration` in 0.1..30 simulation
seconds. Planning and settling take additional time. The trajectory follows the
physics clock, so wall time can be longer. Every action on one arm shares an
exclusive slot, including recovery; different arms can execute concurrently.
Concurrent arms have no mutual avoidance guarantee.

Actuate takes `positions[7]`: waist, shoulder, elbow, forearm_roll, wrist_angle,
wrist_rotate, left_finger. The first six values are radians; the last is finger
travel in metres, not total opening width. The opposite finger is mechanically
coupled. Quanta's gripper angle must not be copied into this linear field.
Limits are `[-3.14158,3.14158]`, `[-1.85005,1.25664]`, `[-1.76278,1.6057]`,
`[-3.14158,3.14158]`, `[-1.8675,2.23402]`, `[-3.14158,3.14158]`, `[0.002,0.037]`.
Actuate completes only when all six angular errors are below 0.03 rad, finger
error is below 0.5 mm, and a continuous physical window confirms stopping. A
blocked target can fail while its subsequent hold succeeds; inspect the receipt.

## Cartesian frames and poses

MovePose takes `session_id`, `reference_frame`, `target` (geometry_msgs/Pose)
and `duration`. MoveDelta takes the same session/frame/duration plus `translation`
(geometry_msgs/Vector3) and `rotation` (geometry_msgs/Quaternion). Positions are
metres; quaternions are unit ROS xyzw, never Euler angles or wxyz.

| Reference | MovePose | MoveDelta |
| --- | --- | --- |
| `arm_base` | Absolute TCP pose in the selected arm's mounting frame | Increment about the TCP origin, along arm-base axes |
| `tcp` | Relative transform from the starting TCP | Same relative transform |
| `base` | Legacy absolute TCP pose in the shared table base | Legacy increment along shared base axes |

Also accepted: the corresponding exact TF names
`robot/<robot_id>/<arm>/arm_base`, `robot/<robot_id>/<arm>/tcp`, and
`robot/<robot_id>/base`, with an optional leading slash. A frame from another
robot/arm is rejected. `arm_base` is a real mounting frame, not an alias for
`base`: the current parallel layout mounts the left arm at approximately
[-0.266,-0.70798,-0.061] m and the right at [0.266,-0.70798,-0.061] m.
Both mounting frames have +90-degree yaw, facing shared-base +Y. Center spacing
is 0.532 m, matching Quanta EX001; they no longer face each other. Shared-base
+X is right, +Y forward over the table, +Z up, with the origin at table center.
Read TF instead of assuming the arm frames and table base are identical.

The nominal initial TCP position in each arm_base is [0.3355,0,0.171] m with
identity orientation, matching Quanta's zero-arm TCP through native ALOHA IK.
The gripper keeps its ALOHA metre-based command. These are new mount/home poses;
old shared-base targets or opposing-arm adaptation results need to be revalidated.

TCP position is the midpoint of the two finger reference sites; its orientation
follows gripper_link. References are sampled once at execution start. For starting
pose (p0,R0), increment (t,D), `tcp` resolves to p=p0+R0*t, R=R0*D.
A base-axis increment uses p=p0+t, R=D*R0. Arm-base increments transform their
axes into the shared base first. Rotations are about the TCP origin, not an orbit
around the base origin.

Both Cartesian actions preserve the commanded gripper target under load. They
preflight the whole straight-translation/shortest-arc-rotation path before moving,
with a 10-second planning budget, joint limits and a 1.5 rad/s sampled speed bound.
Failure or cancellation during planning sends no trajectory. These checks do not
include collision avoidance. Arm gravity/bias feed-forward applies to Cartesian
motion and its subsequent hold, without compensating unknown contact loads.
Success requires measured position error <=3 mm, orientation error <=0.03 rad,
and continuous stop confirmation.

Feedback is `current_pose` in **this arm's arm_base**, `position_error`,
`orientation_error` and `phase`. This frame now matches Quanta's convention,
including when the request uses the legacy `base` reference. Old clients that
interpreted feedback as shared-base coordinates must use the new frame convention.
The `receipt_json` result includes session, goal UUID, operation, execution degree,
completion, reason, stop confirmation/evidence and `task_success=null`. Cartesian
receipts add `pose_frame`, `start_pose`, `target_pose`, `measured_pose`, errors and
`gripper_target`. These poses use arm_base. Legacy `start_tcp_xyz`, `target_tcp_xyz`,
quaternion aliases and `target_frame` remain in the shared base for compatibility.
A successful servo receipt never proves grasp or task success.

## State, stopping and recovery

`<arm>/driver/status` is `std_srvs/srv/Trigger`. It returns JSON, including:

- `session_id`, `arm`, `reported_at_unix`, `simulation_ready`, `feedback_fresh`, `fault`.
- `control_available`, `busy`, `active_goal_id`, `active_operation`, `active_phase`.
- `recovery_required`, `recovery_goal_id`, `stop_confirmed`, `stop_evidence`, `receipts`.
- `state`: joint names/positions/velocities/limits/units, both finger positions and
  velocities in metres, `sim_time`, `tcp_pose`, `pose_frame` and `tcp_frame`.

`<arm>/driver/state` publishes the same JSON schema at nominal 10 Hz. Freshness
means physical feedback less than five wall seconds old. Discovery or a successful
status RPC does not imply fresh feedback or available control. The legacy
`execution_status` and shared-base `state.tcp_xyz`, `tcp_quaternion_xyzw`, `frame`
remain available. The canonical `state.tcp_pose` is always in `state.pose_frame`,
which is `robot/<robot_id>/<arm>/arm_base`.

Stop evidence measures every physical step, including both fingers, over at least
0.25 simulation seconds and 12 samples. It bounds total absolute variation per
second and excursion, so oscillation cannot hide behind matching endpoints.
Angular limits are 0.02 rad/s and 0.002 rad; linear finger limits are 0.002 m/s and
0.0002 m. Evidence includes joint units, per-joint speeds/excursions/limits, window
duration, sample count and raw maximum solver speed. Maximum summary numbers mix
native units; use the per-joint arrays and units when interpreting them.

Cancel through the original action's standard ROS cancellation service and UUID.
Actuate attempts a measured-position hold; Cartesian actions retain the gripper
command while holding the arm. Only a new post-hold window can confirm stopping.
Failed stop confirmation latches the arm, records the failed `recovery_goal_id`,
and rejects further motion. Never replay a timed-out goal; reconcile its UUID in
`active_goal_id` and `receipts` first.

Recover takes current `session_id` and the failed `recovery_goal_id` as
`failed_goal_id`. It requires an idle exclusive slot, fresh feedback, a matching
failed UUID and no physics fault. It preserves the existing targets, resets only
the observation window and waits for new physical stop evidence. It never opens
the gripper, resets the scene, replays the failed action or actively removes a
contact. Success returns `reason=recovered`; persistent motion returns
`recovery_not_settled`. Cancellation or failed recovery preserves the latch and
original failure receipt. Stable physics alone never silently unlocks the arm.

## Sensors and TF

- `<arm>/driver/joint_states`: sensor_msgs/msg/JointState, native rad/m units.
- `<arm>/driver/ee_pose`: geometry_msgs/msg/PoseStamped, TCP in the arm's arm_base.
  Its timestamp corresponds to physical feedback, as does joint_states.
- TF: scoped `base -> <arm>/arm_base` is static; `<arm>/arm_base -> <arm>/tcp` is
  dynamic. Camera optical frames are also scoped to the robot namespace.

Four cameras are named `left`, `right`, `head`, `overhead`. Left/right are native
wrist cameras. `head` is the primary fixed front view, matching Quanta's nominal
head-camera pose relative to the tabletop, with 1280x720 resolution and ideal
intrinsics fx=720.3193, fy=719.8599, cx=640.3909, cy=351.073. Its position in
the shared table base is approximately [0,-0.757095,0.458837] m. It faces forward
and approximately 19.3 degrees downward. This ALOHA camera is fixed; it does not
add a movable robot head. Quanta's lens distortion is not applied: published D
is zero, describing the actual ideal pinhole render.

`overhead` remains the independent fixed top view for compatibility. Wrist and
overhead streams stay 320x240; wrist vertical FOV is 58 degrees and overhead is
45. All four provide RGB-D at nominal 2 Hz; performance can reduce wall frequency.
To use the primary view, select `/robot/<robot_id>/head/camera/color/image_raw`
in RViz. Each `<camera>/camera/` provides:

| Suffix | Message |
| --- | --- |
| `color/image_raw` | sensor_msgs/msg/Image, rgb8 |
| `color/image_raw/compressed` | sensor_msgs/msg/CompressedImage |
| `depth/image_raw` | sensor_msgs/msg/Image, 32FC1 optical-Z metres |
| `camera_info` | sensor_msgs/msg/CameraInfo |
| `depth/color/points` | sensor_msgs/msg/PointCloud2 |

Publishers offer Reliable, Volatile, Keep Last 5; Best Effort subscribers also
work. Use CameraInfo and TF calibration. RGB/depth/CameraInfo from one capture
share timestamp and frame; different cameras capture sequentially. Optical axes
are x right, y down, z forward. Reset caches on a new session. Agent saves its own
evidence; there is no snapshot Service, HTTP file download or public task truth.

## Scanner device

The initial scene includes two colored blocks, matching pads and a freely moving
textured barcode scanner with a physical spring-return trigger. The scanner
retains its original albedo/UVs, is approximately 9.27 x 5.90 x 15.85 cm, and has a
simulated mass of 180 g (including a 12 g trigger). Trigger travel is 6 mm, spring
stiffness 400 N/m, damping 3 N·s/m. Lighting/contact behavior depends on the engine.

`scanner/state` is std_msgs/msg/String JSON at nominal 10 Hz: `robot_id`,
`namespace`, `session_id`, `observed_at_unix`, `observed_at_sim`, `trigger_pressed`,
`press_count`, matching Quanta's switch schema. `observed_at_sim` is the physical
observation time; `observed_at_unix` is publication time. Check simulation-time
progress as well as wall freshness. At least 4 mm depression for 30 ms counts one
press; return to <=1.5 mm rearms it. Holding does not repeatedly increment the
counter. Counts reset on a new session. Press through physical contact; no
software press command, optical decoder, object pose or task evaluator is exposed.
