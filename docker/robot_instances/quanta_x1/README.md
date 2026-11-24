# Quanta X1 whole body, G grippers — stationary tabletop mode

This standalone Isaac Sim body uses X2Real's **Quanta X1 / EX001** whole-body
`v00.05.05/ex001.usd`, including chassis, lift, head and both G grippers. It replaces
the earlier Arm-6A draft. The user's target is X1Pro; the pinned public X2Real
source calls this model Quanta X1, and does not establish a separate X1Pro asset
or hardware revision match. This is simulation only.

```bash
bash robot.sh quanta_x1 --domain-id 42
```

The robot image is self-contained: Isaac Sim 6.0.1.0, ROS Jazzy, driver/IDL and
pinned whole-body USD assets. No host asset directory, asset mount, preparation
script, Python download step or host Isaac/ROS installation is needed to run it.
The launcher reuses `agenticros-quanta_x1:single`, building it only if absent.

Host prerequisites are Docker with NVIDIA Container Toolkit and a compatible RTX
GPU/driver; the repository launcher also needs Python 3 and Compose. In the
Dockerfile's separate `assets` stage, a 478 MB pinned distribution shard is
downloaded and SHA256-verified; only the 378.8 MB model, 6 KB sensor definition
and provenance are copied into `/assets` in the final image. The archive stays
in a BuildKit cache, not the final image. No X2Real scenes, policies, task programs
or evaluators are installed. The Isaac runtime remains about 39 GB; adding the
model does not duplicate it. Assets are Apache-2.0; Isaac uses NVIDIA's Omniverse
license. Existing files under `runs/assets/` and `runs/downloads/` are preserved
but are no longer used by this robot.

### Distributing an image

No registry image has been published by this change. Building a self-contained
image locally does not by itself establish permission to publish that image.
NVIDIA's [Isaac Sim license FAQ](https://docs.isaacsim.omniverse.nvidia.com/latest/common/license-faq.html)
separates Apache-licensed source from Omniverse Kit and other proprietary
components, and states that redistribution of Isaac Sim with Kit to third
parties requires an NVIDIA AI Enterprise license. Confirm the applicable rights
before publishing this full image. Until then, distribute source/Dockerfiles
and let users obtain the dependencies from their official sources under the
applicable terms and build locally with `bash robot.sh quanta_x1`.

GitHub Container Registry supports public images, but its
[documented limits](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#troubleshooting)
include 10 GB per layer and a 10-minute upload timeout. The current large
Isaac installation layer has not been validated for GHCR publication.

Only after the redistribution rights and registry limits are resolved and a
maintainer publishes an image can users pull and run it without a checkout:

```bash
# Replace the placeholder with the actual published image name and version.
docker pull <registry>/agenticros-quanta_x1:<version>
docker run -d --name quanta-x1 --gpus all --network host --ipc host \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --init \
  -e ROS_DOMAIN_ID=42 -e AGENTICROS_ROBOT_ID=quanta_demo \
  <registry>/agenticros-quanta_x1:<version>
```

Choose a unique robot ID within the ROS domain. To use the repository launcher
with a downloaded image, tag it as `agenticros-quanta_x1:single` first. Building
an updated image does not update a running container; stop/reconcile and recreate
explicitly. The image's HEALTHCHECK waits for physics and all three cameras.
Agent/Viewer are separate deployments; Agent still receives only public docs/IDL.

## Alignment and intentional scope

Pinned upstream code and asset revisions are recorded in UPSTREAM.json.
`manaenv/assets/robots/ex001.py` supplies gravity enabled, self-collisions off,
16/0 solver iterations, arm/gripper/head/lift gains, initial yaw 180 degrees,
lift 0.379 m, head pitch -15 degrees, and lift feed-forward 145 N (0.4 s ramp). Physics runs at 240 Hz and rendering
at 30 Hz, matching the upstream Quanta X1 mobile scene timing (240/8).
For contact manipulation this integration uses 16 position / 4 velocity solver
iterations instead of upstream 16/0; all actuator limits remain unchanged.

Use the three native camera prims and their authored transforms:

- Left/right wrist: 640×480, OpenCV fisheye, calibration retained from this USD.
- Head: 1280×720, OpenCV pinhole, calibration overridden exactly as upstream's
  `ex001_head_front_camera.py` does (not the older calibration in the USD).
- Nominal updates are 30 Hz in simulation time (upstream 0.0333 s). Actual wall
  rate depends on rendering throughput. CameraInfo scales native wrist intrinsics
  from 1600×1200 to output resolution and reports the correct distortion model.

There is **no synthetic overhead camera** in the robot observation interface.
The stationary mode anchors the chassis at its initial pose and holds lift/head
at the upstream home. It exposes arm/gripper actions, not driving/lift/head actions.
Each arm also exposes `driver/move_pose` with an absolute pose in that arm's
`arm_base` frame or a relative transform in the TCP frame sampled at acceptance.
The driver preflights IK, limits and speed for the complete Cartesian path;
it does not perform collision planning. Raw, pose and recovery actions share
the same exclusive slot, UUID receipts and measured-stop reconciliation.
`driver/ee_pose` publishes measured-joint FK in the arm base frame; `/tf`
also carries the actual arm-base and TCP frames. Public inputs, outputs and
ROS CLI examples are in `robot_docs/quanta_x1/manual.md`.
The locally generated ground, simple 0.8 m-high tabletop, interactive scanner,
and light are our minimal test scene, not an
X2Real task or calibrated real desk. We do not claim identical task evaluation,
whole-body mobile control, render appearance or X1Pro hardware calibration.

Kit writes cache/data to `/tmp/kit` under the read-only container root. The robot
healthcheck requires fresh physics and all three cameras. The optional type-local
Compose file supplies GPU access; model assets are baked into the image. Agent receives only public
manual, kinematics, camera calibration and IDL; never simulator/model/scene source.

## Validation

```bash
bash tests/test.sh
docker build --target transport -f docker/robot_instances/quanta_x1/Dockerfile -t agenticros-quanta_x1:transport-test .
python3 tests/check_quanta.py --host-network
python3 tests/check_quanta.py --real
# Optional: also exercise production host networking
python3 tests/check_quanta.py --real --host-network
```

Checks create disposable separate robot and Agent containers. The default test
network is an internal Docker bridge without external routing; the real robot
has no host bind mounts or asset volumes. The real check
requires native image dimensions and CameraInfo, whole-body home pose, concurrent
arm actions, both G mimic linkages, old-session rejection, exclusive ownership,
UUID receipts, cancellation and measured stop, plus Agent mount/image isolation.
The native CLI pose probe exercises both arms' base-frame targets, tool-axis
translation and rotation, validates FK against USD-backed public TF in the real
test, and checks raw/pose exclusion, timeout reconciliation and standard cancel.
Synthetic tests establish protocol behavior only. Neither check establishes
autonomous adaptation, grasp/task success, sustained streaming or cross-host LAN.

On 2026-11-24, the unit suite and separate-container native CLI checks passed
for arm-base and tool-frame pose control. The six pose moves in the Isaac GPU
check had a maximum position error of 0.75 mm and orientation error below
0.0015 rad; public FK telemetry matched USD-backed TF. GPU evidence is retained
under `runs/checks/agenticros-quanta-check-f0783a4d/workspace/pose-evidence/`,
and synthetic evidence under `runs/checks/agenticros-quanta-check-fb9f5760/`.
The GPU CLI logs still contain 33 instances of the previously recorded Fast DDS
`sequence size exceeds remaining buffer` diagnostic; explicit motion, identity,
timeout reconciliation and measured-stop checks passed.

On 2026-11-22, the unit suite, separate-container synthetic transport check and
actual GPU check passed locally on RTX 2080 Ti / driver 580.173.02. The bundled
image passed on an internal Docker network with zero robot mounts. Image contents
were separately checksum-verified with networking disabled. Real evidence
is preserved under `runs/checks/agenticros-quanta-check-f7b0e32a/` (result,
CameraInfo, head/wrist PPMs and Agent isolation report). A Fast DDS
`sequence size exceeds remaining buffer` diagnostic occurred in the client log
despite all explicit checks passing. This is recorded, not a claim of error-free
or sustained-stream operation. Temporary test containers were removed.

## Mechanical scanner

The scene uses the user-supplied `barcode_scanner.usdz` at its original scale:
92.697 × 59.012 × 158.502 mm (depth × width × height). The source, checksum,
rebuild script and generated asset are in `scanner_asset/`. Its 50,000 textured
triangles are partitioned into the housing and original orange trigger without
changing their rest geometry or UVs. The supplied convex collision hulls are
trimmed around the moving trigger, which has its own convex collider.

The trigger is a spring-return prismatic joint into the grip: 6 mm travel,
400 N/m stiffness, 3 N·s/m damping; 4 mm held for 30 ms registers a press,
release below 1.5 mm rearms it. These are simulation choices, not measured
mechanism specifications. The source's 180 g mass is split into a 168 g body
and 12 g trigger. It starts lying on its side on the robot's right-hand portion
of the table, with the grip closer to the robot. No extra indicator or cosmetic
geometry is added. There is no barcode plaque, reader camera or decoding.
Physical switch state and press count are exposed through `scanner/state`;
scene poses and simulator implementation remain outside the Agent.

To rebuild the asset using the existing robot image (CPU only):

```bash
docker run --rm --user "$(id -u):$(id -g)" --entrypoint /opt/sim/bin/python \
  -v "$PWD/docker/robot_instances/quanta_x1/scanner_asset:/asset" \
  agenticros-quanta_x1:single /asset/prepare.py
docker run --rm --network none --entrypoint /opt/sim/bin/python \
  -v "$PWD/docker/robot_instances/quanta_x1/scanner_asset:/asset:ro" \
  -v "$PWD/tests/verify_scanner_asset.py:/test.py:ro" \
  agenticros-quanta_x1:single /test.py /asset
```

`python3 tests/check_scanner.py --image agenticros-quanta_x1:scanner-test`
uses a private clamp and collision-driven pusher to check pressing, holding,
release, repeat actuation and stable side-lying placement. This is not autonomous robot adaptation or task
evaluation. Evidence is saved under `runs/checks/` separately from Agent workspaces.
GPU checks refuse to start beside an existing GPU container, require 18 GiB
available RAM and cap their own container at 16 GiB with no swap. Run only one
GPU check at a time; do not stop a user's running body without authorization.

On 2026-11-23, the imported asset passed CPU checks preserving all 50,000
textured triangles and original dimensions, the isolated unit suite, and
separate-container synthetic ROS checks
(`runs/checks/agenticros-quanta-check-784bb93e/`). The GPU component fixture
passed two collision-driven presses, hold debounce, release and stable side-lying
placement: maximum travel 4.828 mm, final released travel 0.0039 mm.
Images and results: `runs/checks/agenticros-scanner-component-d9528caa/evidence/`.
This is component validation, not a successful autonomous bimanual grasp/press task.

Historical validation of the previous procedural geometry: on 2026-11-22, the mechanical fixture passed with two physical presses, no repeat
while held, maximum travel 4.9995 mm and released travel below 0.002 mm. Evidence:
`runs/checks/agenticros-scanner-component-64676c88/evidence/`.
The scanner scene also passed separate-container GPU/ROS checks, including the
three robot cameras, G mimic joints, concurrent actions, session/UUID handling,
cancellation/stop confirmation, scanner session/state and Agent isolation:
`runs/checks/agenticros-quanta-check-7a48a86f/`. The client again logged the Fast
DDS diagnostic noted above despite all assertions passing. Synthetic protocol
evidence is in `runs/checks/agenticros-quanta-check-aeb410d1/`; the unit suite passed.
These checks do not establish autonomous bimanual grasping or task completion.

## Contact stop and recovery

Stop reconciliation observes every physics step over at least 0.25 simulated
seconds, including both passive fingers. It bounds total position variation per
time and position excursion, rather than relying on a single contact-solver
velocity sample. Raw velocities remain public; missing/nonfinite feedback never
confirms a stop. A blocked target can fail while its subsequent hold is confirmed;
that failure does not claim grasp success and need not permanently lock the arm.

The session-bound `Recover` action checks the failed goal ID and shares the arm's
exclusive action slot. It observes the existing hold without resetting pose,
opening the gripper or replaying a goal. Recovery requires new post-request
feedback; cancellation or unsuccessful recovery keeps the latch. Historical
failure receipts are immutable. The public manual documents the protocol.

`tests/check_quanta.py --real --contact --image <candidate>` additionally runs a
scripted contact regression in a separate test client, never in Agent. Joint
inputs use a centered reference grasp and recorded left-arm contact poses on a
fresh isolated scene with new UUIDs. The original end-biased grasp remained
controllable after the fix but slipped during lifting; this is not grasp success.
The host independently checks private object telemetry for table clearance after
lifting; that telemetry is enabled only with `QUANTA_PHYSICS_DIAGNOSTICS=1` and
is never published through ROS or mounted in Agent. This is a regression test,
not autonomous adaptation or completion of the two-hand task.

Recovery fix validation (2026-11-22): unit suite passed; session/failed-UUID
recovery protocol passed between separate containers in
`runs/checks/agenticros-quanta-check-9994fdb9/`. Real physics protocol and all
right-gripper / left-contact / withdrawal / follow-up arm-motion assertions
passed in `runs/checks/agenticros-quanta-check-6398ab82/`. The independent lift
evaluation **failed**: the scanner remained on the table. That run therefore
exited nonzero under its combined check; it is not a successful grasp/task run.
The updated harness records control regression and lift evaluation separately;
add `--require-lift` to gate on the unchanged lift-clearance criterion as well.
Do not treat control availability or a successful servo receipt as grasp success.
