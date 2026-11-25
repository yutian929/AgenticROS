"""One shared physics clock for both arms. No task-level controller."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time
import uuid
from pathlib import Path
import mujoco
import numpy as np
from cartesian import tcp_pose, matrix_quat, arm_base_pose, relative_pose, public_pose
from scanner import Scanner
from control import MotionWindow

JOINTS = ('waist', 'shoulder', 'elbow', 'forearm_roll', 'wrist_angle', 'wrist_rotate', 'left_finger')


class World:
    def __init__(self, seed=0):
        self.lock = threading.RLock()
        self.session = uuid.uuid4().hex[:12]
        self.layout = json.loads((Path(__file__).parent/'layout.json').read_text())
        self.model = mujoco.MjModel.from_xml_path(str(Path(__file__).parent/'model/agenticros_scene.xml'))
        self.data = mujoco.MjData(self.model)
        self.model.opt.timestep = .002
        neutral = [*self.layout['home_joint_positions'], self.layout['home_joint_positions'][-1]]
        for arm in ('left', 'right'):
            for name, value in zip((*JOINTS, 'right_finger'), neutral):
                self.data.qpos[self.model.joint(arm+'/'+name).qposadr[0]] = value
        self.data.ctrl[:] = [*neutral[:7], *neutral[:7]]
        rng = np.random.default_rng(seed)
        for color in ('red', 'blue'):
            body = self.model.body(color+'_block')
            addr = self.model.jnt_qposadr[body.jntadr[0]]
            self.data.qpos[addr:addr+2] += rng.uniform(-.02, .02, 2)
        mujoco.mj_forward(self.model, self.data)
        self.scanner = Scanner(self.model)
        self.scanner.step(self.data)
        self.updated = 0.
        self.motion = {}
        self.motion_qids, self.motion_vids = {}, {}
        for arm in ('left', 'right'):
            names = [arm+'/'+n for n in (*JOINTS, 'right_finger')]
            self.motion[arm] = MotionWindow(names)
            self.motion_qids[arm] = [self.model.joint(n).qposadr[0] for n in names]
            self.motion_vids[arm] = [self.model.joint(n).dofadr[0] for n in names]
        self.compensated_targets = {}
        self.compensation = {}
        for arm, offset in (('left', 0), ('right', 7)):
            aids = list(range(offset, offset+6))
            vids = [self.model.joint(arm+'/'+name).dofadr[0] for name in JOINTS[:6]]
            self.compensation[arm] = (aids, vids, self.model.actuator_gainprm[aids, 0].copy())
        self.stop = threading.Event()
        self.failure = None
        self.render = ThreadPoolExecutor(max_workers=1)
        self.renderers = {}
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def run(self):
        try:
            while not self.stop.is_set():
                started = time.monotonic()
                with self.lock:
                    for arm, target in self.compensated_targets.items():
                        aids, vids, gains = self.compensation[arm]
                        # Native position servos need gravity/bias feed-forward for
                        # accurate Cartesian setpoints. Do not compensate contacts.
                        command = target[:6]+self.data.qfrc_bias[vids]/gains
                        self.data.ctrl[aids] = np.clip(command, self.model.actuator_ctrlrange[aids, 0],
                                                      self.model.actuator_ctrlrange[aids, 1])
                    for _ in range(5):
                        previous = float(self.data.time)
                        mujoco.mj_step(self.model, self.data)
                        if self.data.time <= previous:
                            raise RuntimeError('simulation time did not advance')
                        self.scanner.step(self.data)
                        for arm in ('left', 'right'):
                            self.motion[arm].observe(float(self.data.time), self.data.qpos[self.motion_qids[arm]])
                        self.updated = time.time()
                    if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
                        raise RuntimeError('nonfinite physics state')
                self.stop.wait(max(0., .01-(time.monotonic()-started)))
        except Exception as exc:
            self.failure = str(exc)
            self.stop.set()

    def close(self):
        self.stop.set()
        self.worker.join(timeout=5)
        def close_renderers():
            # Earlier queued captures may still create a different-sized
            # renderer. Drain them on the same worker before closing all.
            for renderer in self.renderers.values():
                renderer.close()
            self.renderers.clear()
        self.render.submit(close_renderers).result()
        self.render.shutdown()


class Simulation:
    def __init__(self, world, arm):
        self.world, self.arm = world, arm
        self.model, self.data, self.lock = world.model, world.data, world.lock
        self.session = world.session
        self.camera_name = arm+'_cam' if arm in ('head', 'overhead') else 'wrist_cam_'+arm
        if arm in ('head', 'overhead'):
            return
        self.names = [arm+'/'+n for n in JOINTS]
        self.qids = [self.model.joint(n).qposadr[0] for n in self.names]
        self.vids = [self.model.joint(n).dofadr[0] for n in self.names]
        self.aids = list(range(0, 7) if arm == 'left' else range(7, 14))
        self.limits = self.model.actuator_ctrlrange[self.aids].copy()

    def set_target(self, target, compensate=False):
        with self.lock:
            if compensate:
                self.world.compensated_targets[self.arm] = np.asarray(target).copy()
            else:
                self.world.compensated_targets.pop(self.arm, None)
            self.data.ctrl[self.aids] = target

    def state(self):
        with self.lock:
            if self.arm in ('head', 'overhead'):
                return dict(sim_time=float(self.data.time), frame='base',
                            physics_fault=self.world.failure)
            left = self.data.site(self.arm+'/left_finger').xpos
            right = self.data.site(self.arm+'/right_finger').xpos
            position, rotation = tcp_pose(self.model, self.data, self.arm)
            local = relative_pose((position, rotation), arm_base_pose(self.model, self.data, self.arm))
            finger_names = [self.arm+'/'+n for n in ('left_finger', 'right_finger')]
            finger_qids = [self.model.joint(n).qposadr[0] for n in finger_names]
            finger_vids = [self.model.joint(n).dofadr[0] for n in finger_names]
            return dict(sim_time=float(self.data.time), frame='base', arm=self.arm,
                        tcp_pose=public_pose(local), pose_frame=self.arm+'/arm_base',
                        tcp_quaternion_xyzw=matrix_quat(rotation).tolist(),
                        tcp_frame=self.arm+'/tcp',
                        tcp_xyz=position.tolist(), gripper_width=float(np.linalg.norm(left-right)),
                        joint_names=self.names, joint_positions=self.data.qpos[self.qids].tolist(),
                        joint_limits=self.limits.tolist(), joint_units=['rad']*6+['m'],
                        finger_joint_positions=self.data.qpos[finger_qids].tolist(),
                        finger_joint_velocities=self.data.qvel[finger_vids].tolist(), finger_joint_units=['m', 'm'],
                        joint_velocities=self.data.qvel[self.vids].tolist(), physics_fault=self.world.failure,
                        orientation='quaternion xyzw; axes of gripper_link; origin at finger-site midpoint')

    def stop_evidence(self):
        with self.lock:
            evidence = self.world.motion[self.arm].evidence()
            evidence['raw_max_speed'] = float(np.max(np.abs(self.data.qvel[self.world.motion_vids[self.arm]])))
            return evidence

    def observe(self):
        # Called only on the shared rendering worker. Copy the scene under the
        # physics lock; expensive software rendering must not stop simulation.
        width, height = (1280, 720) if self.arm == 'head' else (320, 240)
        if (width, height) not in self.world.renderers:
            self.world.renderers[width, height] = mujoco.Renderer(self.model, height=height, width=width)
        renderer = self.world.renderers[width, height]
        with self.lock:
            renderer.update_scene(self.data, camera=self.camera_name)
            cam = self.model.camera(self.camera_name)
            focal = height/2/math.tan(float(cam.fovy[0])*math.pi/360)
            k = (np.asarray(self.world.layout['main_camera']['k']).reshape(3, 3).tolist()
                 if self.arm == 'head' else [[focal,0,width/2],[0,focal,height/2],[0,0,1]])
            calibration = dict(camera=self.arm, width=width, height=height, K=k,
                               depth_units='metres along optical z',
                               optical_to_base_rotation=(self.data.cam_xmat[cam.id].reshape(3,3)@np.diag([1.,-1.,-1.])).tolist(),
                               optical_to_base_translation=self.data.cam_xpos[cam.id].tolist(),
                               captured_at_unix=time.time())
            state = self.state()
        renderer.disable_depth_rendering()
        rgb = renderer.render().copy()
        renderer.enable_depth_rendering()
        depth = renderer.render().copy()
        return rgb, depth, calibration, state
