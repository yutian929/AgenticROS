"""One shared physics clock for both arms. No task-level controller."""
from concurrent.futures import ThreadPoolExecutor
import math
import threading
import time
import uuid
from pathlib import Path
import mujoco
import numpy as np
from cartesian import tcp_pose, matrix_quat

JOINTS = ('waist', 'shoulder', 'elbow', 'forearm_roll', 'wrist_angle', 'wrist_rotate', 'left_finger')


class World:
    def __init__(self, seed=0):
        self.lock = threading.RLock()
        self.session = uuid.uuid4().hex[:12]
        self.model = mujoco.MjModel.from_xml_path(str(Path(__file__).parent/'model/agenticros_scene.xml'))
        self.data = mujoco.MjData(self.model)
        self.model.opt.timestep = .002
        neutral = [0, -.96, 1.16, 0, -.3, 0, .0084, .0084]
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
        self.compensated_targets = {}
        self.compensation = {}
        for arm, offset in (('left', 0), ('right', 7)):
            aids = list(range(offset, offset+6))
            vids = [self.model.joint(arm+'/'+name).dofadr[0] for name in JOINTS[:6]]
            self.compensation[arm] = (aids, vids, self.model.actuator_gainprm[aids, 0].copy())
        self.stop = threading.Event()
        self.failure = None
        self.render = ThreadPoolExecutor(max_workers=1)
        self.renderer = None
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
                    mujoco.mj_step(self.model, self.data, nstep=5)
                    if not np.isfinite(self.data.qpos).all():
                        raise RuntimeError('nonfinite physics state')
                self.stop.wait(max(0., .01-(time.monotonic()-started)))
        except Exception as exc:
            self.failure = str(exc)
            self.stop.set()

    def close(self):
        self.stop.set()
        self.worker.join(timeout=5)
        if self.renderer is not None:
            self.render.submit(self.renderer.close).result()
        self.render.shutdown()


class Simulation:
    def __init__(self, world, arm):
        self.world, self.arm = world, arm
        self.model, self.data, self.lock = world.model, world.data, world.lock
        self.session = world.session
        self.camera_name = 'overhead_cam' if arm == 'overhead' else 'wrist_cam_'+arm
        if arm == 'overhead':
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
            if self.arm == 'overhead':
                return dict(sim_time=float(self.data.time), frame='base',
                            physics_fault=self.world.failure)
            left = self.data.site(self.arm+'/left_finger').xpos
            right = self.data.site(self.arm+'/right_finger').xpos
            position, rotation = tcp_pose(self.model, self.data, self.arm)
            return dict(sim_time=float(self.data.time), frame='base', arm=self.arm,
                        tcp_quaternion_xyzw=matrix_quat(rotation).tolist(),
                        tcp_frame=self.arm+'/tcp',
                        tcp_xyz=position.tolist(), gripper_width=float(np.linalg.norm(left-right)),
                        joint_names=self.names, joint_positions=self.data.qpos[self.qids].tolist(),
                        joint_velocities=self.data.qvel[self.vids].tolist(), physics_fault=self.world.failure,
                        orientation='quaternion xyzw; axes of gripper_link; origin at finger-site midpoint')

    def observe(self):
        # Called only on the shared rendering worker. Copy the scene under the
        # physics lock; expensive software rendering must not stop simulation.
        if self.world.renderer is None:
            self.world.renderer = mujoco.Renderer(self.model, height=240, width=320)
        renderer = self.world.renderer
        with self.lock:
            renderer.update_scene(self.data, camera=self.camera_name)
            cam = self.model.camera(self.camera_name)
            focal = 120/math.tan(float(cam.fovy[0])*math.pi/360)
            calibration = dict(camera=self.arm, width=320, height=240, K=[[focal,0,160],[0,focal,120],[0,0,1]],
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
