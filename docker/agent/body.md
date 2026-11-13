# Device documentation: Cartesian gripper test rig
This is manufacturer-style kinematic documentation, not an adapter implementation.
The device is simulation only. It is already powered by the separate body container.
Do not launch another simulator. Discover its raw ROS endpoints in the configured domain.

## Geometry and units
Five prismatic joints, order [x, y, z, left, right], all in metres.
The carriage's fixed parent translation in base is [0, 0, 0.2]. The first three
joint axes are base +X,+Y,+Z. The grasp-center frame is the carriage frame.
Left/right finger joints travel outwards from a zero-gap position along -X/+X.
Their strokes are 0..0.04 m each. The fingers extend 0.02 m below grasp center.
Orientation is fixed downward; no rotational DOFs. Table plane is base z=0.
Permitted joint setpoints: x ±0.38, y ±0.26, z -0.175..0.13, fingers 0..0.04.
Use grasp-center speeds 0.01..0.25 m/s. There is no collision planner.

## Raw driver protocol
* `/driver/actuate`: rosbody_driver_interfaces/action/Actuate.
  Goal: session_id (must match the current driver), positions[5] in the joint order above, duration in seconds (0.1..30).
  Raw position servos interpolate all joints over duration, then settle. No TCP/width conversion.
  One goal at a time; invalid/concurrent goals are rejected. UUIDs are ROS action goal IDs.
  Feedback: measured joint positions. Result: receipt_json.
* `/driver/status`: std_srvs/srv/Trigger. JSON in message: session_id, busy,
  recovery_required, active_goal_id, execution_status, stop_confirmed, state, completed receipts keyed by goal UUID.
* `/driver/observe`: rosbody_driver_interfaces/srv/Snapshot. Empty request, snapshot_json response.
* `/driver/joint_states`: sensor_msgs/msg/JointState, positions and velocities at 10 Hz.

Raw receipt: complete (servo settled, not proof requested position reached), executed
("none"/"some"/"unknown"), stop_confirmed, reason, state, goal_id, session_id.
Use measured state to determine Cartesian completion and to report actual gripper opening.
A stop fault latches recovery_required; ask the operator to restart the simulation if needed.
No reset service is exposed. Completed receipts are in memory until driver restart.

RGB/depth are synchronized; depth is optical Z in metres. Calibration in each snapshot
contains K and optical_to_base_rotation/translation. Never mix calibrations or timestamps.
The simulation steps during commands and holds while the agent thinks. Captures happen only
on request. The user can watch the latest camera image in the separate browser viewer.

Every snapshot contains explicit session_id and captured_at_unix. Reject stale observations.
A session mismatch rejects a goal without movement. Status reports the active UUID; a
missing receipt is NOT evidence that a timed-out request never executed.
