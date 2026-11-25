# MuJoCo ALOHA tabletop

Both arms, the colored blocks and the imported barcode scanner share one MuJoCo
3.3.7 world. Robot interfaces are documented in
`../robot_docs/aloha_tabletop/manual.md`. Simulator and evaluator source remain
inside the robot image; Agent sees only public documentation and ROS feedback.

## Parallel layout and main view

The derived scene now uses same-facing parallel mounts, not the upstream
opposing layout. Their center spacing is 0.532 m, matching Quanta EX001. The
shared table frame is +X right, +Y forward, +Z up, with its origin at the table
center. Both arms face +Y, at approximately [-0.266,-0.70798,-0.061] m and
[0.266,-0.70798,-0.061] m. The working table is 1.4 m wide by 1.0 m deep.
The old opposing-arm gantry is replaced in the derived scene with rear pedestals.
Upstream MJCF, native links, joint limits, masses and actuator gains are unchanged.

Native ALOHA home joints are solved to match Quanta's nominal zero-arm TCP:
[0.3355,0,0.171] m and identity orientation in each arm_base. Both arms have the
same local home pose; ALOHA finger travel remains in metres. Objects are placed
in the forward working area; the scanner's mesh, texture and spring are unchanged.

`head/camera/` is the new primary fixed front view: its pose relative to table
center and its 1280x720 ideal pinhole intrinsics match Quanta's nominal head
camera at lift=0.379 m / head_pitch=-15 degrees. Native wrist views and the
independent `overhead/camera/` remain available. Main-camera RGB, depth, points,
compressed RGB, CameraInfo and TF share the existing interface conventions.
The image renderer remains MuJoCo; Quanta's nonzero lens distortion is not
applied, so actual CameraInfo correctly reports zero distortion.

`layout.json` pins geometry/calibration provenance and the native home solution.
Regenerate it with `python3 tools/export_quanta_layout.py` from the repository
root; this standard-library tool reads public Quanta kinematics/calibration and
never launches Isaac or ROS. Normal ALOHA builds use the checked-in profile.
The profile changes installation frames and initial targets: old shared-base
programs and cached opposing-layout adaptation evidence must be revalidated.

## Quanta interface alignment

Both arms now expose the same `driver/actuate`, `driver/move_pose`,
`driver/recover`, `driver/status`, `driver/state`, `driver/joint_states` and
`driver/ee_pose` names as the current Quanta source. Recover validates the failed
UUID/session and observes the existing hold with a new stop window. Status and
state share the readiness/freshness/recovery/receipt schema. ee_pose and canonical
TCP pose objects use each arm's real `arm_base`; legacy shared-base requests and
pose aliases remain available. Cartesian feedback now uses arm_base as well.

Stopping observes both fingers at every physics step, with native rad/m limits.
Raw completion requires target tracking and stopping. Trajectories follow
simulation time; holds preserve the Cartesian gripper command. The public manual
documents limits and compatibility. Existing `move_delta`, RGB-D/point-cloud
streams and the physically named overhead camera remain ALOHA extensions. Native
IDL package names and physical gripper units remain model-specific.

For the full before/after comparison see `docs/robot-interface-alignment.md` at
the repository root. Quanta's older `:single` image is not rebuilt by this change.

## Scanner asset

`scanner_asset/` contains a native MJCF fragment, OBJ visuals/collision hulls,
the original `albedo.png`, an integrity manifest and a maintainer converter.
`prepare_scene.py` composes the asset into the derived scene, leaving the
upstream ALOHA files in `model/` unchanged. Existing block tasks remain available;
the existing private evaluator still scores the red-block task, not the scanner.

The conversion preserves all 50,000 source triangles, vertex positions, normals
and UV coordinates, and copies the texture byte for byte. The original split
body/trigger and 22 convex casing hulls are retained. The scanner is a free body;
the trigger slides along its local negative X axis from 0 to 6 mm. Casing/trigger
collision is explicitly excluded, matching the USD joint configuration. Both
parts still collide with the table and robot. Body/trigger masses are 168/12 g;
MuJoCo infers inertia from volume-weighted convex hull masses.

The trigger uses MuJoCo's [native joint spring and damping](https://mujoco.readthedocs.io/en/3.3.7/XMLreference.html#body-joint):
400 N/m stiffness, 3 N·s/m damping and zero rest displacement. The switch keeps
the Isaac implementation's 4 mm press, 1.5 mm release and 30 ms debounce rules,
sampled at every 2 ms physics step. It publishes mechanical feedback on
`/robot/<robot_id>/scanner/state`, without object poses or task truth.

The texture and nominal spring parameters are preserved, rather than promising
identical engine output. MuJoCo lighting differs from USD Preview Surface. The
native passive spring does not implement Isaac's 10 N drive-force clamp; spring
force within its nominal travel reaches only 2.4 N, but high-speed damping and
limit contacts can differ. Contact friction and inertia are simulation models.

Normal image builds use checked-in MJCF/OBJ/PNG and need no Isaac installation,
USD package or host Python dependencies beyond Python 3. To regenerate only the
asset, run `scanner_asset/prepare.py --source <quanta scanner_asset directory>
--output <aloha scanner_asset directory>` in a disposable Python container with
NumPy and `usd-core`. It does not import Isaac/Kit or require a GPU. The manifest
records the source USD and texture hashes and each exported mesh hash.

## Build and validation

From the repository root:

```bash
docker build -f docker/robot_instances/aloha_tabletop/Dockerfile \
  -t agenticros-aloha_tabletop:scanner-test .
bash tests/test.sh
python3 tests/check_aloha_scanner.py --image agenticros-aloha_tabletop:scanner-test
python3 tests/check_aloha.py --image agenticros-aloha_tabletop:scanner-test
python3 tests/check_aloha.py --image agenticros-aloha_tabletop:scanner-test --cartesian
```

The scanner check first runs network-isolated physics: a 2 N load, passive return,
mesh-contact pressing/release, tabletop stability and free-body translation. It
renders primary-head, overall-layout, overhead and close-up PNGs into `runs/checks/`. It then starts a disposable
robot and a public ROS observer in separate containers/network namespaces on an
isolated Docker network. The robot has no host mounts; the observer mounts only
its fresh test workspace and read-only public docs. It checks the scanner JSON,
shared session, both status RPCs, control availability and all four RGB cameras.
It also records the actual mounts and image IDs.

These are unit, scripted physics and Docker communication checks on one host.
They do not prove cross-host LAN behavior, autonomous adaptation, successful
scanner grasping, inversion or task completion. No live robot or user volume is
restarted or changed by these tests.

For the parallel layout, add `--layout --alignment` to the scanner check. This
also verifies the actual compiled mount transforms, inter-arm spacing, nominal
home TCPs, neutral arm/table collision clearance and main-camera raster projection
against CameraInfo K using an independently positioned marker. The ROS peer
receives the 1280x720 head view and validates its calibration in a separate
container. Raw/Cartesian/recovery regressions still use the real MuJoCo body.

After validation, build/tag the image as `agenticros-aloha_tabletop:single` for the
normal launcher. Starting a new instance with `bash robot.sh aloha_tabletop
--domain-id <domain>` preserves existing instances; the launcher reuses an
existing image tag rather than automatically rebuilding it.

Validated on 2026-11-24: 162 isolated unit tests passed; raw-action and Cartesian
regressions passed in `runs/checks/agenticros-aloha-check-2753f72e/` and
`runs/checks/agenticros-aloha-check-5d1e9eab/`. Scanner physics, preview images,
two-container ROS checks and actual mount inspection passed in
`runs/checks/agenticros-aloha-scanner-87ecb154/`. The 2 N spring test settled at
5 mm and returned to rest; physical mesh pressing registered two distinct presses
with release between them. Geometry/UV/normal comparison is recorded in
`runs/checks/aloha-scanner-geometry-audit.json`; texture bytes and triangle indices
match the split USD. The unchanged Agent image filesystem audit also passed.
The validated image was tagged `:single`; its previous image is retained as
`agenticros-aloha_tabletop:pre-scanner-fa0989028e5c`. Existing domain 42 containers
were not restarted.

Parallel-layout validation (2026-11-25): 168 isolated unit tests passed. Actual
MuJoCo raw/Cartesian/recovery regressions passed in
`runs/checks/agenticros-aloha-check-740c8c68/`,
`runs/checks/agenticros-aloha-check-4b96a309/` and
`runs/checks/agenticros-aloha-check-efa2d9ea/`. Scanner spring/return, compiled
parallel mount geometry, nominal TCP alignment, three-second neutral arm/table
and inter-arm clearance, and separate-container ROS checks passed in
`runs/checks/agenticros-aloha-scanner-0246c911/`. The independent main-camera
projection marker error was 0.701 pixels; nominal home TCP position error was
0.0182 mm. Camera fields use the native
[MuJoCo camera model](https://mujoco.readthedocs.io/en/3.3.7/XMLreference.html#body-camera).
The primary head and overall-layout previews are in that evidence directory.
The consolidated record is `runs/checks/aloha-parallel-layout-result.json`.
The validated image is now `:single`; the previous opposing layout is preserved
as `agenticros-aloha_tabletop:pre-parallel-a2e2ca147a7c`. No live container was
restarted. These are single-host physics/protocol regressions, not autonomous
adaptation or scanner grasp/task evaluation.
