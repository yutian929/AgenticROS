# ALOHA tabletop public device documentation
Simulation only. Both arms share ONE MuJoCo world, physics clock and session.
Endpoints below are relative to the registered robot namespace.
Each arm exposes /left/driver/actuate or /right/driver/actuate (aloha_driver_interfaces/action/Actuate), /left/driver/status or /right/driver/status (std_srvs/srv/Trigger), and /left/driver/observe or /right/driver/observe (aloha_driver_interfaces/srv/Snapshot).
Goals contain current session_id, positions[7], duration (0.1..30 seconds).
Order: waist, shoulder, elbow, forearm_roll, wrist_angle, wrist_rotate (radians), left_finger (metres). The opposite finger is mechanically coupled. Last value is finger travel, NOT full opening width.
Limits: [-3.14158,3.14158], [-1.85005,1.25664], [-1.76278,1.6057], [-3.14158,3.14158], [-1.8675,2.23402], [-3.14158,3.14158], [0.002,0.037].
Each arm accepts only one active goal. Different arms can execute concurrently. Both use the same base frame. The raw actuate action interpolates in JOINT space. The Cartesian actions below supply local numerical IK and Cartesian interpolation. No obstacle-avoidance planner or grasp controller is supplied. Do not equate limits with collision safety.
Status and receipts contain measured state, session, goal UUID, execution degree, completion and stop confirmation. A completed servo operation is not proof of requested position or grasp success. Receipts survive only until process restart; never replay timed-out goals. Stop faults latch recovery_required. Cancel through ROS action cancellation and reconcile the receipt and live stopped state.
Observe returns synchronized RGB, optical-Z metric depth, intrinsics/extrinsics and measured arm state. In LAN mode immutable artifacts use authenticated download URLs. Validate session and hashes. Three distinct 320x240 RGB-D views: /left/camera and /right/camera are wrist-mounted cameras (58-degree vertical FOV); /overhead/camera is the fixed overhead camera (45-degree vertical FOV). Each also exposes /CAMERA/driver/observe. Use the provided calibration. Optical frames are left/camera_optical_frame, right/camera_optical_frame and overhead/camera_optical_frame under the robot namespace. Wrist transforms are dynamic on /tf, overhead is static on /tf_static. Sensor timestamps and extrinsics refer to the same scene capture; three views are captured sequentially, not simultaneously. Physics continues while agents think, paced near wall clock when CPU permits. Never infer wall time from sim_time.
Control ownership across agents, collision planning and task-specific capabilities are not supplied. Build and test the capabilities needed for each stage incrementally using public interfaces and feedback; independent per-arm actions do not require a pre-existing combined controller. These interfaces provide no collision-free guarantee. The initial scene has two colored blocks and matching pads. Evaluation truth is private and not available over ROS.


## Cartesian actions (both arms)

- /left/driver/move_pose and /right/driver/move_pose: aloha_driver_interfaces/action/MovePose.
- /left/driver/move_delta and /right/driver/move_delta: aloha_driver_interfaces/action/MoveDelta.
- Both require current session_id, reference_frame (exactly "base" or "tcp"), duration (0.1..30 seconds).
- Positions/translations are metres. Orientations/rotations are unit quaternions in ROS xyzw order; identity is x=0,y=0,z=0,w=1. A zero quaternion is invalid. There is no implicit Euler-angle order.
- TCP origin is the midpoint of the two finger reference sites, matching tcp_xyz. Its axes are the robot gripper_link axes, not camera optical axes. Read tcp_quaternion_xyzw and /tf (base -> left/tcp or right/tcp) rather than assuming an approach axis from the camera image.
- All reported TCP poses and feedback current_pose are in the robot base frame. The base is shared by both arms. Frames on /tf are scoped to the registered robot namespace.

Let (p0,R0) be the measured TCP pose sampled once when execution begins, t the requested translation/position, and D the quaternion rotation matrix:

| Action | reference_frame | Resolved base target |
| --- | --- | --- |
| move_pose | base | p=t, R=D (absolute pose) |
| move_pose | tcp | p=p0+R0*t, R=R0*D (pose relative to starting TCP) |
| move_delta | base | p=p0+t, R=D*R0 |
| move_delta | tcp | p=p0+R0*t, R=R0*D |

Delta rotations are about the TCP origin, never an orbit about the base origin. TCP references stay fixed at action start; these are finite moves, not continuous tool-frame velocity control.

MovePose goal fields: session_id, reference_frame, target (geometry_msgs/Pose), duration.
MoveDelta goal fields: session_id, reference_frame, translation (geometry_msgs/Vector3), rotation (geometry_msgs/Quaternion), duration.
For example, move_delta with reference_frame="base", translation={x:0,y:0,z:0.01}, rotation={x:0,y:0,z:0,w:1}, duration=2.0 requests a 1 cm lift along base Z. Change reference_frame to "tcp" to move along the starting TCP Z axis instead.

Execution preflights the complete sampled path in a separate kinematics state before issuing trajectory commands: straight TCP translation, shortest-arc orientation interpolation and smooth start/end timing. IK must converge within 0.15 mm / 0.0015 rad at each sample; commanded joint targets must obey limits and a 1.5 rad/s sampled joint-speed bound. Planning is bounded to 10 seconds and may fail near singularities or joint limits even if another IK branch exists. There is no global search. duration is nominal trajectory time; planning and up to 3 seconds of final settling are additional. Actual simulation throughput can be slower than wall time.

A Cartesian action keeps the gripper command unchanged. Position servos use model gravity/bias feed-forward on the six arm joints during Cartesian execution and subsequent holding. This does not compensate unknown contact loads. Raw actuate retains its previous position-control behavior.

All three actions share the same per-arm execution lock. Other-arm actions can run concurrently, but mutual avoidance is NOT guaranteed. Cartesian preflight does NOT check self/environment/inter-arm collision and does not automatically route around obstacles. No guarantee of acceleration limits or exact straight-line tracking under contact is provided. Start with bounded, observed motions in clear space.

Malformed frames, nonfinite values, non-unit quaternions, stale sessions and occupied/faulted arms are rejected. Accepted goals may abort with ik_failed, joint_speed_limit, planning_timeout, start_state_changed or target_tolerance_not_reached. An unreachable preflight does not execute the target trajectory. Do not retry a timed-out goal without reconciling its UUID receipt.

Cartesian success requires measured TCP error <= 3 mm and <= 0.03 rad, and measured joint speeds < 0.02 rad/s. Feedback includes current_pose, position_error, orientation_error and phase (planning/executing/settling). receipt_json includes sampled start, resolved base target, measured final state, errors, tolerances, session, UUID, executed, reason and stop_confirmed. It explicitly reports collision_checked=false. Completion is not grasp/task success.

Cancel works during planning and motion. Cancellation, lease loss, shutdown, tracking failure or physics failure abort motion and attempt a measured-position hold; reconcile stop_confirmed and live status before further motion. Failed stop confirmation latches recovery_required. Successful execution maintains the final joint target; cancel/failure holds the measured joint state. Existing receipts remain queryable via each arm's driver/status until restart.
