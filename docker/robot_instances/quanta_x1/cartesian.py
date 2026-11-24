"""Quanta arm kinematics and bounded Cartesian preflight; no scene/collision access.

Uses only the published joint anchors and measured joint positions. Pure Python
keeps host unit tests independent of ROS, Isaac, NumPy and native solver installs.
"""
import json
import math
from pathlib import Path
import time

POSITION_TOLERANCE = .003
ORIENTATION_TOLERANCE = .03
MAX_JOINT_SPEED = 1.5


def identity():
    return [[float(i == j) for j in range(4)] for i in range(4)]


def multiply(a, b):
    return [[sum(x*y for x, y in zip(row, column)) for column in zip(*b)] for row in a]


def inverse(t):
    result = identity()
    for i in range(3):
        for j in range(3):
            result[i][j] = t[j][i]
        result[i][3] = -sum(t[j][i]*t[j][3] for j in range(3))
    return result


def transform(position, quaternion):
    if len(position) != 3 or len(quaternion) != 4 or not all(math.isfinite(x) for x in [*position, *quaternion]):
        raise ValueError('pose requires finite metres and quaternion xyzw')
    norm = math.sqrt(sum(x*x for x in quaternion))
    if abs(norm-1.) > .01:
        raise ValueError('quaternion must have unit length, in xyzw order')
    x, y, z, w = [value/norm for value in quaternion]
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), float(position[0])],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), float(position[1])],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), float(position[2])],
            [0., 0., 0., 1.]]


def quaternion(t):
    # Largest diagonal branch is stable for rotations near pi as well as zero.
    trace = sum(t[i][i] for i in range(3))
    if trace > 0:
        scale = 2*math.sqrt(1+trace)
        q = [(t[2][1]-t[1][2])/scale, (t[0][2]-t[2][0])/scale,
             (t[1][0]-t[0][1])/scale, scale/4]
    else:
        i = max(range(3), key=lambda k: t[k][k])
        j, k = (i+1)%3, (i+2)%3
        scale = 2*math.sqrt(max(0., 1+t[i][i]-t[j][j]-t[k][k]))
        q = [0.]*4
        q[i], q[j], q[k] = scale/4, (t[j][i]+t[i][j])/scale, (t[k][i]+t[i][k])/scale
        q[3] = (t[k][j]-t[j][k])/scale
    norm = math.sqrt(sum(x*x for x in q))
    return [value/norm for value in q]


def rotation_vector(t):
    q = quaternion(t)
    if q[3] < 0:
        q = [-x for x in q]
    norm = math.sqrt(sum(x*x for x in q[:3]))
    factor = 2. if norm < 1e-10 else 2*math.atan2(norm, q[3])/norm
    return [factor*x for x in q[:3]]


def rotation(vector):
    angle = math.sqrt(sum(x*x for x in vector))
    factor = .5 if angle < 1e-10 else math.sin(angle/2)/angle
    return transform([0., 0., 0.], [factor*x for x in vector]+[math.cos(angle/2)])


def pose_dict(t):
    return dict(position=dict(zip(('x','y','z'), [t[i][3] for i in range(3)])),
                orientation=dict(zip(('x','y','z','w'), quaternion(t))))


def pose_errors(actual, target):
    distance = math.sqrt(sum((target[i][3]-actual[i][3])**2 for i in range(3)))
    angle = math.sqrt(sum(x*x for x in rotation_vector(multiply(target, inverse(actual)))))
    return distance, angle


def solve_linear(matrix, values):
    rows = [list(row)+[value] for row, value in zip(matrix, values)]
    for i in range(len(rows)):
        pivot = max(range(i, len(rows)), key=lambda j: abs(rows[j][i]))
        rows[i], rows[pivot] = rows[pivot], rows[i]
        scale = rows[i][i]
        if abs(scale) < 1e-15:
            raise ValueError('singular_ik_step')
        rows[i] = [x/scale for x in rows[i]]
        for j in range(len(rows)):
            if j != i:
                factor = rows[j][i]
                rows[j] = [a-factor*b for a, b in zip(rows[j], rows[i])]
    return [row[-1] for row in rows]


class Kinematics:
    def __init__(self, path=None):
        joints = json.loads(Path(path or Path(__file__).with_name('kinematics.json')).read_text())['joints']
        by_child = {joint['child'][0]: joint for joint in joints}
        self.chains = {}
        self.bounds = {}
        self.reach = {}
        for arm in ('left', 'right'):
            chain, link = [], arm+'_arm_gripper_base_link'
            while link != arm+'_arm_base_link':
                joint = by_child[link]
                chain.append(joint)
                link = joint['parent'][0]
            entries, limits = [], []
            for joint in reversed(chain):
                def anchor(prefix):
                    w, x, y, z = joint[prefix+'_rotation']
                    return transform(joint[prefix+'_anchor'], [x,y,z,w])
                axis = None
                if joint['type'] == 'PhysicsRevoluteJoint':
                    axis = [float(label == joint['axis']) for label in 'XYZ']
                    limits.append(tuple(math.radians(joint[key]) for key in ('lowerLimit','upperLimit')))
                elif joint['type'] != 'PhysicsFixedJoint':
                    raise ValueError('unsupported joint in arm chain')
                entries.append((anchor('parent'), inverse(anchor('child')), axis))
            if len(limits) != 6:
                raise ValueError('arm chain must contain six revolute joints')
            self.chains[arm], self.bounds[arm] = entries, limits
            self.reach[arm] = sum(math.sqrt(sum(t[i][3]**2 for i in range(3)))
                                  for parent, child, _ in entries for t in (parent,child))+.218
        self.tcp = transform([.217, 0., -.01], [0.,0.,0.,1.])

    def forward(self, arm, joints, jacobian=False):
        if len(joints) < 6 or not all(math.isfinite(q) for q in joints[:6]):
            raise ValueError('invalid joint feedback')
        t, axes, pivots, index = identity(), [], [], 0
        for parent, child_inverse, axis in self.chains[arm]:
            t = multiply(t, parent)
            if axis is not None:
                pivots.append([t[i][3] for i in range(3)])
                axes.append([sum(t[i][j]*axis[j] for j in range(3)) for i in range(3)])
                t = multiply(t, rotation([value*joints[index] for value in axis]))
                index += 1
            t = multiply(t, child_inverse)
        t = multiply(t, self.tcp)
        if not jacobian:
            return t
        columns = []
        for axis, pivot in zip(axes, pivots):
            x, y, z = [t[i][3]-pivot[i] for i in range(3)]
            a, b, c = axis
            columns.append([b*z-c*y, c*x-a*z, a*y-b*x, *[.15*v for v in axis]])
        return t, list(map(list, zip(*columns)))

    def target(self, arm, start, position, orientation, reference_frame):
        requested = transform(position, orientation)
        if reference_frame == 'arm_base':
            return requested
        if reference_frame == 'tcp':
            return multiply(self.forward(arm, start), requested)
        raise ValueError('reference_frame must be arm_base or tcp')

    def describe(self, arm, joints):
        return pose_dict(self.forward(arm, joints))

    def errors(self, arm, joints, target):
        return pose_errors(self.forward(arm, joints), target)

    def inverse(self, arm, target, seed, bounds, check):
        q = [max(lo, min(hi, value)) for value,(lo,hi) in zip(seed[:6], bounds)]
        for _ in range(120):
            check()
            actual, jac = self.forward(arm, q, jacobian=True)
            ep = [target[i][3]-actual[i][3] for i in range(3)]
            er = rotation_vector(multiply(target, inverse(actual)))
            if sum(x*x for x in ep) < .0002**2 and sum(x*x for x in er) < .002**2:
                return q
            error = ep+[.15*x for x in er]
            normal = [[sum(a*b for a,b in zip(row, other))+(1e-5 if i == j else 0.)
                       for j, other in enumerate(jac)] for i, row in enumerate(jac)]
            dual = solve_linear(normal, error)
            step = [sum(row[j]*value for row,value in zip(jac, dual)) for j in range(6)]
            scale = min(1., .12/max(1e-12, math.sqrt(sum(x*x for x in step))))
            q = [max(lo, min(hi, value+scale*delta)) for value,delta,(lo,hi) in zip(q,step,bounds)]
        raise ValueError('ik_failed: unreachable, singular or outside solver budget')

    def plan(self, arm, seed, target, duration, bounds, interrupted):
        deadline = time.monotonic()+10.
        def check():
            reason = interrupted()
            if reason:
                raise InterruptedError(reason)
            if time.monotonic() > deadline:
                raise ValueError('planning_timeout')
        start = self.forward(arm, seed)
        displacement, angle = pose_errors(start, target)
        count = max(1, math.ceil(duration/.05), math.ceil(displacement/.01), math.ceil(angle/.05))
        dt = duration/count
        vector = rotation_vector(multiply(target, inverse(start)))
        previous, path = list(seed[:6]), [list(seed)]
        for i in range(1, count+1):
            check()
            fraction = i/count
            fraction = fraction*fraction*(3-2*fraction)
            waypoint = multiply(rotation([fraction*x for x in vector]), start)
            for j in range(3):
                waypoint[j][3] = start[j][3]+fraction*(target[j][3]-start[j][3])
            q = self.inverse(arm, waypoint, previous, bounds, check)
            if max(abs(a-b) for a,b in zip(q,previous))/dt > MAX_JOINT_SPEED:
                raise ValueError('joint_speed_limit: increase duration or change target')
            path.append(q+[seed[6]])
            previous = q
        return path

    @staticmethod
    def public_pose(target):
        return pose_dict(target)
