"""Cartesian kinematics and bounded, preflighted paths. No collision planner."""
import math
import time
import mujoco
import numpy as np

POSITION_TOLERANCE = .003
ORIENTATION_TOLERANCE = .03
MAX_JOINT_SPEED = 1.5


def quat_matrix(q):
    q = np.asarray(q, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or abs(np.linalg.norm(q)-1) > .01:
        raise ValueError('quaternion must be finite, unit length, in xyzw order')
    q = q / np.linalg.norm(q)
    result = np.empty(9)
    mujoco.mju_quat2Mat(result, q[[3, 0, 1, 2]])
    return result.reshape(3, 3)


def matrix_quat(rotation):
    result = np.empty(4)
    mujoco.mju_mat2Quat(result, np.asarray(rotation).reshape(9))
    return result[[1, 2, 3, 0]]


def rotation_vector(rotation):
    q = matrix_quat(rotation)
    if q[3] < 0:
        q = -q
    norm = np.linalg.norm(q[:3])
    return 2*q[:3] if norm < 1e-9 else q[:3] * (2*math.atan2(norm, q[3])/norm)


def rotation_matrix(vector):
    angle = np.linalg.norm(vector)
    q = np.r_[np.asarray(vector)*(.5 if angle < 1e-9 else math.sin(angle/2)/angle), math.cos(angle/2)]
    return quat_matrix(q)


def tcp_pose(model, data, arm):
    # Preserve the existing grasp-center position; orientation follows gripper_link.
    position = (data.site(arm+'/left_finger').xpos + data.site(arm+'/right_finger').xpos)/2
    rotation = data.body(arm+'/gripper_link').xmat.reshape(3, 3)
    return position.copy(), rotation.copy()


def public_pose(pose):
    return dict(position=dict(zip(('x', 'y', 'z'), map(float, pose[0]))),
                orientation=dict(zip(('x', 'y', 'z', 'w'), map(float, matrix_quat(pose[1])))))


def arm_base_pose(model, data, arm):
    body = data.body(arm+'/base_link')
    return body.xpos.copy(), body.xmat.reshape(3, 3).copy()


def relative_pose(pose, reference):
    return reference[1].T@(pose[0]-reference[0]), reference[1].T@pose[1]


def resolve_target(start, translation, rotation, reference_frame, delta, arm_base=None):
    p, r = start
    t = np.asarray(translation, dtype=float)
    if t.shape != (3,) or not np.isfinite(t).all():
        raise ValueError('translation must be three finite metres')
    dr = quat_matrix(rotation)
    if reference_frame == 'tcp':
        return p+r@t, r@dr
    if reference_frame == 'base':
        return (p+t, dr@r) if delta else (t.copy(), dr)
    if reference_frame == 'arm_base' and arm_base is not None:
        origin, axes = arm_base
        return (p+axes@t, axes@dr@axes.T@r) if delta else (origin+axes@t, axes@dr)
    raise ValueError('reference_frame must be arm_base, base or tcp')


def pose_error(actual, target):
    return float(np.linalg.norm(target[0]-actual[0])), float(np.linalg.norm(rotation_vector(target[1]@actual[1].T)))


class CartesianPath:
    def __init__(self, sim):
        self.sim = sim
        self.model = sim.model
        # Solver owns independent data; never writes to live physics during preflight.
        self.data = mujoco.MjData(self.model)
        with sim.lock:
            self.data.qpos[:] = sim.data.qpos
            self.initial = sim.data.qpos[sim.qids].copy()
            self.gripper_command = float(sim.data.ctrl[sim.aids[-1]])
            self.sample_time = float(sim.data.time)
        mujoco.mj_forward(self.model, self.data)
        self.start = tcp_pose(self.model, self.data, sim.arm)
        self.arm_base = arm_base_pose(self.model, self.data, sim.arm)
        self.qids = sim.qids[:6]
        self.vids = sim.vids[:6]
        self.bounds = sim.limits[:6]
        self.sites = [self.model.site(sim.arm+'/'+finger).id for finger in ('left_finger','right_finger')]
        self.body = self.model.body(sim.arm+'/gripper_link').id

    def solve(self, target, check):
        jp, jr, jp2 = [np.zeros((3, self.model.nv)) for _ in range(3)]
        for _ in range(100):
            check()
            mujoco.mj_forward(self.model, self.data)
            p, r = tcp_pose(self.model, self.data, self.sim.arm)
            ep, er = target[0]-p, rotation_vector(target[1]@r.T)
            if np.linalg.norm(ep) < .00015 and np.linalg.norm(er) < .0015:
                return self.data.qpos[self.qids].copy()
            mujoco.mj_jacSite(self.model, self.data, jp, None, self.sites[0])
            mujoco.mj_jacSite(self.model, self.data, jp2, None, self.sites[1])
            mujoco.mj_jacBody(self.model, self.data, None, jr, self.body)
            jac = np.vstack(((jp[:, self.vids]+jp2[:, self.vids])/2, .2*jr[:, self.vids]))
            error = np.r_[ep, .2*er]
            step = jac.T @ np.linalg.solve(jac@jac.T + .00001*np.eye(6), error)
            step *= min(1., .12/max(np.linalg.norm(step), 1e-12))
            self.data.qpos[self.qids] = np.clip(self.data.qpos[self.qids]+step, self.bounds[:, 0], self.bounds[:, 1])
        raise ValueError('ik_failed: Cartesian path unreachable or singular within solver budget')

    def plan(self, target, duration, interrupted):
        deadline = time.monotonic()+10
        def check():
            reason = interrupted()
            if reason:
                raise InterruptedError(reason)
            if time.monotonic() > deadline:
                raise ValueError('planning_timeout')
        count = max(1, math.ceil(duration/.02))
        dt = duration/count
        rot = rotation_vector(target[1]@self.start[1].T)
        previous = self.initial[:6]
        path = []
        for i in range(1, count+1):
            s = i/count
            s = s*s*(3-2*s)
            pose = (self.start[0]+s*(target[0]-self.start[0]), rotation_matrix(s*rot)@self.start[1])
            q = self.solve(pose, check)
            if np.max(np.abs(q-previous))/dt > MAX_JOINT_SPEED:
                raise ValueError('joint_speed_limit: increase duration or change target')
            path.append(np.r_[q, self.gripper_command])
            previous = q
        return path, dt
