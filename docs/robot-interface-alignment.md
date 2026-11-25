# ALOHA / Quanta interface alignment

The common baseline is the current public Quanta source contract, not an older
running image or an Agent-generated adapter. In particular, the Quanta `:single`
image inspected on 2026-11-24 exposes Actuate/Recover but predates the source's
MovePose and ee_pose additions. This change updates ALOHA; it does not rebuild or
restart Quanta.

All device endpoints use `/robot/<robot_id>` with `left`/`right` arm names and
sessions in payloads. Public manuals and native IDL remain under
`docker/robot_instances/robot_docs/<type>/`.

| Capability / suffix | Quanta source | ALOHA after alignment |
| --- | --- | --- |
| `<arm>/driver/actuate` | Actuate | Same name and field layout; native gripper units differ |
| `<arm>/driver/move_pose` | MovePose; arm_base/tcp | Same name/layout; arm_base/tcp and full scoped frames added; legacy base retained |
| `<arm>/driver/recover` | Recover with session/failed UUID | Added with same fields and exclusive recovery semantics |
| `<arm>/driver/move_delta` | Absent | Existing ALOHA extension retained |
| `<arm>/driver/status` | Trigger JSON | Aligned readiness/freshness/UUID/stop/recovery fields |
| `<arm>/driver/state` | String JSON | Same schema as status, including the common fields |
| `<arm>/driver/joint_states` | JointState | Same name/type; native units explicitly reported |
| `<arm>/driver/ee_pose` | PoseStamped in arm_base | Added with the same frame convention and physical timestamp |
| `<arm>/arm_base` TF | Actual arm mounting frame | Added as an actual transform, not a shared-base alias |
| `<camera>/camera/color/image_raw` | Image | Existing common name/type |
| `<camera>/camera/camera_info` | CameraInfo | Existing common name/type |
| `scanner/state` | Mechanical switch JSON | Same schema, thresholds, debounce and session-reset behavior |
| Standalone solve_ik Service | Not native | Not native; IK remains inside Cartesian actions |

Native IDL packages stay `quanta_driver_interfaces` and
`aloha_driver_interfaces`. Actuate, MovePose and Recover share field layouts, but
clients must select the model's actual ROS type; these are not aliases for a
single package. No device-specific IDL or adapter is added to the Agent image.

## Common control behavior

- Raw, Cartesian and recovery actions share an exclusive slot per arm. The other
  arm can run independently. Sessions and failed UUIDs are validated before use.
- Motion duration follows simulation time; feedback freshness uses wall time.
  Liveness, RPC success, fresh physical feedback and control availability are
  separate facts.
- Stop reconciliation uses a new continuous physical window, including both
  fingers, after a hold. Oscillation is measured through total variation, not
  merely matching window endpoints. ALOHA now uses this evidence on action
  completion and cancellation rather than a single speed sample.
- ALOHA raw completion now also requires target tracking, matching Quanta's
  distinction between stopped and reached. Native angular/finger tolerances are
  documented in its manual.
- An unconfirmed stop latches the arm and retains the failed UUID. Recovery only
  observes the existing hold; it never replays a goal or changes its targets.
  Successful new stop evidence is required to unlock. Cancellation, stale
  feedback, continuing motion or a fault cannot silently clear the latch.
- Receipts preserve the original failed result and expose `task_success=null`.
  Servo completion and recovery are not independent grasp/task evaluation.

## Physical differences retained

| Item | Quanta | ALOHA |
| --- | --- | --- |
| Gripper command | Rotary angle, radians, 0..1.89 | Linear finger travel, metres, .002...037 |
| Extra body state | Mobile-base pose, lift/head joints | Fixed tabletop mounts; no fabricated mobile-body state |
| Primary camera | head, physically installed on the head | head, fixed front view aligned to Quanta's nominal table-relative pose |
| Additional fixed view | None | Independent overhead view retained |
| Camera model | Fisheye wrists / head RGB | Pinhole RGB-D; head 1280x720, others 320x240 |
| Extra camera streams | RGB / CameraInfo | Also compressed RGB, depth and point clouds |
| Stop units | Angular joints/fingers | Six angular joints plus two linear fingers, with per-axis limits |
| arm_base definition | EX001 arm mounting frame | Native ALOHA arm mount; both face +Y in the new parallel layout |

The 2026-11-25 parallel layout adds a fixed head-like main view and preserves the
independent overhead camera. Arm spacing and table-relative main-camera pose /
ideal intrinsics match Quanta; native ALOHA home joints match its nominal zero-arm
TCPs. Lens distortion, arm geometry and gripper units remain model-specific.
Converting the gripper field to Quanta's angle would change native control meaning.
These differences are explicit in TF, CameraInfo and each public manual.

## Compatibility and validation

ALOHA retains existing base-reference requests, move_delta, camera endpoints and
shared-base `state.tcp_xyz` / `tcp_quaternion_xyzw` aliases. Canonical
`state.tcp_pose`, ee_pose, Cartesian feedback and receipt pose objects are in the
selected arm's arm_base, matching Quanta. Clients that previously interpreted
Cartesian feedback as shared-base coordinates must migrate that interpretation.

The validation separates standard-library unit tests, disposable actual MuJoCo
motion/recovery tests with explicit failure injection, and native DDS between a
robot container and an Agent-image public ROS peer. The peer builds only the
mounted public IDL in a fresh ignored workspace. Actual mounts and Agent image
contents are checked. The scanner physics regression remains separate from any
autonomous adaptation or grasp/task evaluation. Same-host containers do not prove
cross-host LAN behavior.

```bash
bash tests/test.sh
python3 tests/check_aloha.py --image agenticros-aloha_tabletop:aligned-test
python3 tests/check_aloha.py --image agenticros-aloha_tabletop:aligned-test --cartesian
python3 tests/check_aloha.py --image agenticros-aloha_tabletop:aligned-test --alignment
python3 tests/check_aloha_scanner.py --image agenticros-aloha_tabletop:aligned-test --alignment
```

Validated 2026-11-24: 167 isolated unit tests passed. Actual MuJoCo raw and
Cartesian regressions passed in `runs/checks/agenticros-aloha-check-a2d34079/`
and `runs/checks/agenticros-aloha-check-0ff34da5/`. Frame/recovery tests,
including a stationary unreachable raw target and injected failed stop, passed
in `runs/checks/agenticros-aloha-check-68de701b/`. Public IDL compilation,
PoseStamped/actions/status across two containers, sensor checks and mount
inspection passed in `runs/checks/agenticros-aloha-scanner-09ca5759/`.
The Agent image filesystem audit passed. The consolidated result is
`runs/checks/aloha-interface-alignment-result.json`.

The validated ALOHA image is tagged `:single`; its previous scanner image is
retained as `agenticros-aloha_tabletop:pre-alignment-a99b5a43d4d7`. No live
containers were restarted, and Quanta's image was not changed. These results
cover protocol/physics integration on one physical host, not autonomous
adaptation, scanner grasp/inversion or task evaluation.

On 2026-11-25 the parallel-layout image superseded this initial alignment image.
The main `head` camera is now table-relative to Quanta's nominal head view, and
the independent overhead view remains. Actual 0.532 m parallel mounts, native
home TCPs and camera projection are verified in
`runs/checks/agenticros-aloha-scanner-0246c911/`. See the ALOHA README and
`runs/checks/aloha-parallel-layout-result.json` for the current image, complete
validation scopes and the retained opposing-layout image tag.
