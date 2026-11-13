# Set up and use this body

## Body and scope

ROSBody v0.1 is a stepped MuJoCo simulation with a three-axis Cartesian gripper.
The world contains three coloured 4 cm cubes and three coloured flat circular plates.
The downward orientation is fixed. No RPY support, VLA, SAM3 or Jev is required.
Grasps use MuJoCo contact/friction; no weld, attachment or object teleportation is used during actions.

All xyz commands are grasp-center positions in `base`, in metres. The table surface is z=0.
The fingers extend 2 cm below the grasp center. Workspace: x ±0.38, y ±0.26, z 0.025..0.33.
Motion speed is 0.01..0.25 m/s in simulated time. Gripper width is the inner face separation,
0..0.08 m. Fixed downward fingers close along base x. Movement is straight-line interpolation;
workspace validation is NOT collision-free planning. Use clearance waypoints for transport.

## Start

From repository root:

```bash
pixi install
pixi run build
pixi run server
```

Optional visible viewer (on macOS use `mjpython`):

```bash
pixi run bash -c 'source scripts/ros_env.sh; mjpython -m rosbody.server --viewer'
```

The server remains running across client programs. It owns the authoritative simulation state.
It steps during actions and reset, and holds simulated time while the agent thinks. This is not
continuous real-time robot control. Cameras publish at 1 Hz wall time; joints at 10 Hz.
Use the same `ROS_DOMAIN_ID` in every terminal; no external robot should share that domain.

## Discover and check

```bash
pixi run ros2 node list
pixi run ros2 action list -t
pixi run bash scripts/client.sh status
pixi run bash scripts/client.sh snapshot
```

Snapshot returns absolute local RGB and depth file paths, immutable `snapshot_id`, camera
intrinsics, optical-to-base transform and robot state from the SAME simulation lock.
Open `rgb_path` with your image viewer tool. Depth is a float32 NumPy array in metres along
optical z. Use that snapshot's calibration; do not combine it with later transforms.
Topic streams use reliable QoS (depth 2) for cameras; sensor-data QoS for joints. Snapshot files assume agent and server share this filesystem.

## Public operations

- `/body/move_free`: `rosbody_interfaces/action/Move` (xyz, speed).
- `/body/gripper`: `rosbody_interfaces/action/Gripper` (width).
- `/body/capture_snapshot`: `rosbody_interfaces/srv/Snapshot`.
- `/body/status`: `std_srvs/srv/Trigger`, JSON state, session ID, busy flag and completed receipts.
- `/sim/reset`: `std_srvs/srv/Trigger`. Simulation-only; refused during active actions.
- Topics: `/camera/front/rgb`, `/camera/front/depth`, `/camera/front/camera_info`, `/joint_states`, `/tf`.

CLI examples (coordinates are syntax examples, not task plans):

```bash
pixi run bash scripts/client.sh move 0 -0.25 0.28 --speed 0.12
pixi run bash scripts/client.sh grip 0.08
```

Python programs use `from rosbody.client import Robot`, then `with Robot() as robot:` and
`robot.snapshot()`, `robot.move(xyz, speed)`, `robot.grip(width)`, `robot.status()`.
Run programs with `pixi run bash -c 'source scripts/ros_env.sh; python runs/my_stage.py'`.
Save your program first. Use structured result checks; return for image interpretation when needed.

## Initial perception capability

`rosbody.perception.locate_color(snapshot, color, kind)` uses RGB colour components and depth.
It expects exactly two separate visible regions of the specified colour: small cube and larger plate.
It returns a visible SURFACE point, not the object's center or a ready-made grasp pose.
This assumption fails under overlap, occlusion, changed sizes or lighting. Inspect the RGB and stop
when ambiguous. It does not read object positions or segmentation IDs from the simulator.

## Receipts and recovery

Actions are exclusive across both arm and gripper. Concurrent goals are rejected, not queued.
Each client-generated UUID identifies a goal. Results include `complete`, `executed`,
`stop_confirmed`, reason and measured state. Settled gripper closure does NOT prove object capture.
`task_success` is always null in agent-visible receipts.

On timeout, the client raises `ExecutionUnknown` including the goal ID. Query `/body/status`
and reconcile that ID; do not resubmit. If server restarted, old results may be unavailable:
state is UNKNOWN, not proof of no execution. ROS cancellation is a request; await the terminal
result. The server holds position, checks velocity, and reports stop confirmation. Unconfirmed
stops block new goals until simulation reset. Never reset to hide a failed task evaluation.

## Evidence and task completion

Server: `runs/server/events.jsonl`, immutable snapshots, and an evaluator-only verdict file.
Agent: save stage programs, receipts and snapshot references under a separate run directory.
Judge task completion from visible outcomes. Keep hidden evaluator results out of agent context.

`examples/pick_place.py` is a deterministic RGB-D integration test, not an autonomous agent.
For an autonomous trial, read this body description and receive only the task instruction;
do not copy the diagnostic task plan or inspect hidden simulator truth.
