"""Standalone Isaac Sim body: pinned G model, simple table, native ROS interface."""
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time

ASSET = Path('/assets/ex001.usd')
ASSET_SHA256 = '8affd2eebf7dd4c7dad984add4d0289a006ef844f34b3694370c38589be38efa'
if not ASSET.is_file() or hashlib.sha256(ASSET.read_bytes()).hexdigest() != ASSET_SHA256:
    raise RuntimeError('Bundled G model missing or checksum mismatch; rebuild or pull a complete Quanta X1 image')

from isaacsim import SimulationApp
app = SimulationApp(dict(headless=True, width=640, height=480, renderer='RaytracedLighting',
                         anti_aliasing=0, multi_gpu=False))

import numpy as np
from pxr import UsdGeom, UsdPhysics, PhysxSchema, UsdLux, Gf
from cameras import configure_cameras
from scanner_scene import ScannerScene
from isaacsim.core.api import World
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api.objects import FixedCuboid
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.types import ArticulationAction
from isaacsim.sensors.camera import Camera
import rclpy
from rclpy.executors import MultiThreadedExecutor
from robot_identity import RobotIdentity
from control import Control, ARMS, NAMES
from driver import Driver


def main():
    identity = RobotIdentity()
    control = Control(identity.session)
    shutdown = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: shutdown.set())
    world = World(physics_dt=1/240, rendering_dt=1/30, stage_units_in_meters=1., backend='numpy', device='cpu')
    stage = world.stage
    add_reference_to_stage(str(ASSET), '/World/Robot')
    UsdGeom.Xformable(stage.GetPrimAtPath('/World/Robot')).MakeMatrixXform().Set(
        Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0.,0.,1.),180.)))
    robot_root = stage.GetPrimAtPath('/World/Robot/base_link')
    root_api = PhysxSchema.PhysxArticulationAPI.Apply(robot_root)
    root_api.CreateEnabledSelfCollisionsAttr(False)
    root_api.CreateSolverPositionIterationCountAttr(16)
    root_api.CreateSolverVelocityIterationCountAttr(4)
    anchor = UsdPhysics.FixedJoint.Define(stage, '/World/RobotAnchor')
    anchor.CreateBody1Rel().SetTargets(['/World/Robot/base_link'])
    anchor.CreateLocalRot0Attr(Gf.Quatf(0.,0.,0.,1.))
    # Stationary tabletop mode anchors the base; upstream mobile mode leaves it free.
    # Keep whole-body gravity and actuator gains from EX001Embodiment.
    for prim in stage.Traverse():
        if prim.GetPath().HasPrefix('/World/Robot') and prim.HasAPI(UsdPhysics.RigidBodyAPI):
            PhysxSchema.PhysxRigidBodyAPI.Apply(prim).CreateDisableGravityAttr(False)
    limits = {}
    for arm in ARMS:
        limits[arm] = []
        for name in NAMES[arm]:
            prim = stage.GetPrimAtPath('/World/Robot/joints/'+name)
            joint = UsdPhysics.RevoluteJoint(prim)
            limits[arm].append(np.deg2rad([joint.GetLowerLimitAttr().Get(), joint.GetUpperLimitAttr().Get()]).tolist())
            drive = UsdPhysics.DriveAPI.Get(prim, 'angular')
            grip = name.endswith('gripper')
            drive.GetStiffnessAttr().Set(200. if grip else 1500.)
            drive.GetDampingAttr().Set(30. if grip else 150.)
            drive.GetMaxForceAttr().Set(200.)
        # Public G amplitude from the upstream embodiment, not the H URDF.
        limits[arm][-1] = [0., 1.89]
    auxiliary = {'lift_joint': (.379, 10000., 2000., 2000.),
                 'head_yaw_joint': (0., 1500., 150., 200.),
                 'head_pitch_joint': (-np.pi/12, 1500., 150., 200.)}
    for name, (_, kp, kd, force) in auxiliary.items():
        drive = UsdPhysics.DriveAPI.Get(stage.GetPrimAtPath('/World/Robot/joints/'+name),
                                       'linear' if name == 'lift_joint' else 'angular')
        drive.GetStiffnessAttr().Set(kp)
        drive.GetDampingAttr().Set(kd)
        drive.GetMaxForceAttr().Set(force)
    for name in ('left_wheel_joint','right_wheel_joint'):
        drive = UsdPhysics.DriveAPI.Get(stage.GetPrimAtPath('/World/Robot/joints/'+name), 'angular')
        drive.GetStiffnessAttr().Set(0.)
        drive.GetDampingAttr().Set(5000.)
        drive.GetMaxForceAttr().Set(5000.)
        drive.GetTargetVelocityAttr().Set(0.)
    robot = world.scene.add(SingleArticulation('/World/Robot/base_link', name='quanta'))
    # Avoid Isaac's default ground helper: it resolves a remote NVIDIA asset.
    world.scene.add(FixedCuboid('/World/Ground', name='ground',
                               position=np.array([0.,0.,-.05]), scale=np.array([20.,20.,.1]),
                               color=np.array([.2,.24,.28])))
    # Simple task surface in front of the robot's upstream 180-degree yaw pose.
    world.scene.add(FixedCuboid('/World/Table', name='table', position=np.array([-.9, 0., .775]),
                               scale=np.array([1., 1.4, .05]), color=np.array([.35,.38,.42])))
    scanner = ScannerScene(world)
    light = UsdLux.DomeLight.Define(stage, '/World/Light')
    light.CreateIntensityAttr(1000.)
    cameras, calibration = configure_cameras(stage, Camera)
    world.reset()
    robot.initialize()
    scanner.initialize()
    indices = {a: np.array([robot.get_dof_index(n) for n in NAMES[a]], dtype=int) for a in ARMS}
    finger_indices = {a: np.array([robot.get_dof_index(a+'_arm_gripper_'+side+'_joint')
                                  for side in ('left', 'right')], dtype=int) for a in ARMS}
    # Initialize the mimic followers consistently with the G model's native relation.
    robot.set_world_pose(position=np.zeros(3), orientation=np.array([0.,0.,0.,1.]))
    q = np.zeros_like(robot.get_joint_positions())
    auxiliary_indices = np.array([robot.get_dof_index(n) for n in auxiliary], dtype=int)
    auxiliary_targets = np.array([v[0] for v in auxiliary.values()])
    q[auxiliary_indices] = auxiliary_targets
    lift_index = robot.get_dof_index('lift_joint')
    for arm in ARMS:
        q[indices[arm]] = 0.
        q[robot.get_dof_index(arm+'_arm_gripper_right_joint')] = np.deg2rad(4.1827)
        q[robot.get_dof_index(arm+'_arm_gripper_left_joint')] = -np.deg2rad(4.1827)
    robot.set_joint_positions(q)
    robot.set_joint_velocities(np.zeros_like(q))
    robot.apply_action(ArticulationAction(joint_positions=q))
    for camera in cameras.values():
        camera.initialize()
        camera.set_clipping_range(.01, 1.e5)
    for warm_step in range(240):
        robot.apply_action(ArticulationAction(joint_positions=auxiliary_targets, joint_indices=auxiliary_indices))
        robot.set_joint_efforts(np.array([145. * min(world.current_time/.4, 1.)]), joint_indices=np.array([lift_index]))
        world.step(render=False)
        if warm_step % 8 == 0:
            world.render()
    control.initialize({a: robot.get_joint_positions(indices[a]).tolist() for a in ARMS}, limits)
    rclpy.init()
    driver = Driver(identity, control)
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(driver)
    worker = threading.Thread(target=executor.spin, daemon=True)
    worker.start()
    frame = 0
    camera_times = {n: 0. for n in cameras}
    camera_frames = {n: None for n in cameras}
    try:
        while app.is_running() and not shutdown.is_set():
            started = time.monotonic()
            if not worker.is_alive():
                raise RuntimeError('ROS executor stopped')
            if control.fault:
                raise RuntimeError(control.fault)
            world.step(render=False)
            if frame % 8 == 0:
                world.render()
            scanner.physics_step(float(world.current_time))
            pos = robot.get_joint_positions()
            vel = robot.get_joint_velocities()
            with control.lock:
                control.fingers = {a: pos[finger_indices[a]].tolist() for a in ARMS}
                control.finger_velocities = {a: vel[finger_indices[a]].tolist() for a in ARMS}
            with control.lock:
                control.body_state = dict(joint_names=list(auxiliary),
                    joint_positions=pos[auxiliary_indices].tolist(), joint_velocities=vel[auxiliary_indices].tolist(),
                    base_position=robot.get_world_pose()[0].tolist(), base_orientation_wxyz=robot.get_world_pose()[1].tolist(),
                    mode='stationary_tabletop')
            robot.apply_action(ArticulationAction(joint_positions=auxiliary_targets, joint_indices=auxiliary_indices))
            robot.set_joint_efforts(np.array([145.]), joint_indices=np.array([lift_index]))
            targets = control.tick({a: pos[indices[a]].tolist() for a in ARMS},
                                   {a: vel[indices[a]].tolist() for a in ARMS}, float(world.current_time))
            for arm in ARMS:
                robot.apply_action(ArticulationAction(joint_positions=np.array(targets[arm]), joint_indices=indices[arm]))
            if frame % 8 == 0:
                scanner.render_step()
                driver.scanner_observation(scanner.switch.status())
                cache = UsdGeom.XformCache()
                for name, camera in cameras.items():
                    metadata = camera.get_current_frame()
                    frame_key = (metadata.get('rendering_frame'), metadata.get('rendering_time'))
                    rgb = camera.get_rgba(device='cpu')
                    if (frame_key != (None, None) and frame_key != camera_frames[name]
                            and rgb is not None and rgb.size):
                        position, quaternion = camera.get_world_pose(camera_axes='ros')
                        driver.camera(name, rgb, calibration[name], position, quaternion[[1,2,3,0]])
                        camera_times[name] = time.time()
                        camera_frames[name] = frame_key
                base_pos, base_quat = robot.get_world_pose()
                driver.transform('base', base_pos, base_quat[[1,2,3,0]])
                for arm in ARMS:
                    arm_base = cache.GetLocalToWorldTransform(stage.GetPrimAtPath('/World/Robot/'+arm+'_arm_base_link'))
                    arm_quat = arm_base.ExtractRotationQuat()
                    driver.transform(arm+'/arm_base', arm_base.Transform(Gf.Vec3d(0.,0.,0.)),
                                     [*arm_quat.GetImaginary(), arm_quat.GetReal()])
                    transform = cache.GetLocalToWorldTransform(stage.GetPrimAtPath('/World/Robot/'+arm+'_arm_gripper_base_link'))
                    position = transform.Transform(Gf.Vec3d(.217, 0., -.01))
                    quat = transform.ExtractRotationQuat()
                    driver.transform(arm+'/tcp', position, [*quat.GetImaginary(), quat.GetReal()])
                health = dict(camera_calibration=calibration, body=control.body_state, scanner_ready=scanner.ready, simulation_ready=scanner.ready and all(time.time()-t < 15 for t in camera_times.values()),
                              reported_at_unix=control.updated, fault=control.fault,
                              session_id=identity.session, robot_id=identity.robot_id)
                if os.getenv('QUANTA_PHYSICS_DIAGNOSTICS') == '1':
                    # Private test telemetry, never a ROS topic or Agent mount.
                    p, q = scanner.body.get_world_pose()
                    from scanner_scene import rotation
                    corners = np.array([[x,y,z] for x in (-.048,.048)
                                        for y in (-.034,.034) for z in (0.,.165)])
                    lower = float(np.min((corners @ rotation(q).T + p)[:,2]))
                    diagnostic = dict(sim_time=float(world.current_time),
                        scanner_position=p.tolist(), scanner_orientation=q.tolist(),
                        scanner_conservative_min_z=lower, scanner_min_z=scanner.minimum_height())
                    Path('/tmp/quanta-physics.tmp').write_text(json.dumps(diagnostic))
                    Path('/tmp/quanta-physics.tmp').replace('/tmp/quanta-physics.json')
                tmp = Path('/tmp/quanta-health.tmp')
                tmp.write_text(json.dumps(health))
                tmp.replace('/tmp/quanta-health.json')
            frame += 1
            shutdown.wait(max(0., 1/240 - (time.monotonic()-started)))
    finally:
        control.fail('simulation shutting down; stop not confirmed')
        executor.shutdown(timeout_sec=5.)
        driver.destroy_node()
        rclpy.shutdown()
        world.stop()


try:
    main()
finally:
    app.close()
