"""Imported textured scanner body and spring-return mechanical trigger.

Only switch feedback is public; geometry and private poses stay robot-side.
"""
from pathlib import Path
import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics, PhysxSchema
from isaacsim.core.prims import SingleRigidPrim
from scanner_device import MechanicalSwitch

BODY = '/World/Scanner/body'
TRIGGER = '/World/Scanner/trigger'
TRIGGER_ANCHOR = np.array([.008, 0., .100])
# Robot faces world -X; its right side is world +Y. Local +Y is up,
# placing the scanner on its side with the grip nearer the robot.
HOME_POSITION = np.array([-.55, .27, .832])
HOME_QUAT = np.array([.5, .5, -.5, -.5])


def cube(stage, path, position, size, color, collision=True):
    shape = UsdGeom.Cube.Define(stage, path)
    shape.CreateSizeAttr(1.)
    shape.AddTranslateOp().Set(Gf.Vec3d(*map(float,position)))
    shape.AddScaleOp().Set(Gf.Vec3d(*map(float,size)))
    shape.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    if collision:
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    return shape.GetPrim()


def rigid(stage, path, position, mass, orientation):
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    xform = UsdGeom.Xformable(prim)
    xform.AddTranslateOp().Set(Gf.Vec3d(*map(float,position)))
    xform.AddOrientOp().Set(Gf.Quatf(*map(float,orientation)))
    UsdPhysics.RigidBodyAPI.Apply(prim)
    UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(mass)
    api = PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
    api.CreateEnableCCDAttr(True)
    api.CreateSolverPositionIterationCountAttr(32)
    api.CreateSolverVelocityIterationCountAttr(4)
    return prim


def rotation(q):
    w,x,y,z = q
    return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                     [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                     [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])


class ScannerScene:
    def __init__(self, world, position=HOME_POSITION, orientation=HOME_QUAT):
        stage = world.stage
        self.stage = stage
        UsdGeom.Xform.Define(stage, '/World/Scanner')
        # Preserve the supplied USDZ geometry, scale and texture at rest.
        asset = str(Path(__file__).with_name('scanner_asset')/'scanner.usdc')
        stage.GetPrimAtPath('/World/Scanner').GetReferences().AddReference(asset)
        rigid(stage, BODY, position, .168, orientation)
        trigger_position = position + rotation(orientation)@TRIGGER_ANCHOR
        rigid(stage, TRIGGER, trigger_position, .012, orientation)
        joint = UsdPhysics.PrismaticJoint.Define(stage, '/World/Scanner/trigger_joint')
        joint.CreateBody0Rel().SetTargets([BODY])
        joint.CreateBody1Rel().SetTargets([TRIGGER])
        joint.CreateAxisAttr('X')
        joint.CreateLocalPos0Attr(Gf.Vec3f(*map(float,TRIGGER_ANCHOR)))
        joint.CreateLocalPos1Attr(Gf.Vec3f(0.,0.,0.))
        joint.CreateLowerLimitAttr(-.006)
        joint.CreateUpperLimitAttr(0.)
        joint.CreateCollisionEnabledAttr(False)
        drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), 'linear')
        drive.CreateTypeAttr('force')
        drive.CreateTargetPositionAttr(0.)
        drive.CreateStiffnessAttr(400.)
        drive.CreateDampingAttr(3.)
        drive.CreateMaxForceAttr(10.)
        self.body = world.scene.add(SingleRigidPrim(BODY, name='scanner_body'))
        self.trigger = world.scene.add(SingleRigidPrim(TRIGGER, name='scanner_trigger'))
        self.switch = MechanicalSwitch()
        self.travel = 0.
        self.ready = False

    def minimum_height(self):
        """Private diagnostic from actual mesh vertices, never public device state."""
        if not hasattr(self, '_vertices'):
            self._vertices = {}
            cache = UsdGeom.XformCache()
            for path in (BODY, TRIGGER):
                vertices = []
                root = self.stage.GetPrimAtPath(path)
                root_inverse = cache.GetLocalToWorldTransform(root).GetInverse()
                for child in Usd.PrimRange(root):
                    if not child.IsA(UsdGeom.Mesh):
                        continue
                    mesh = UsdGeom.Mesh(child)
                    if mesh.GetPurposeAttr().Get() == 'guide':
                        continue
                    points = np.array(mesh.GetPointsAttr().Get())
                    indices = np.unique(mesh.GetFaceVertexIndicesAttr().Get())
                    matrix = cache.GetLocalToWorldTransform(child)*root_inverse
                    vertices.extend(tuple(matrix.Transform(Gf.Vec3d(*map(float,v)))) for v in points[indices])
                self._vertices[path] = np.asarray(vertices)
        values = []
        for path, body in ((BODY,self.body),(TRIGGER,self.trigger)):
            p,q = body.get_world_pose()
            values.append(float(np.min((self._vertices[path] @ rotation(q).T + p)[:,2])))
        return min(values)

    def initialize(self):
        self.body.initialize()
        self.trigger.initialize()

    def physics_step(self, sim_time):
        body_pos, body_quat = self.body.get_world_pose()
        trigger_pos, _ = self.trigger.get_world_pose()
        local = rotation(body_quat).T@(trigger_pos-body_pos)
        self.travel = float(np.clip(TRIGGER_ANCHOR[0]-local[0],0.,.0065))
        self.switch.observe_switch(self.travel, sim_time)
        self.ready = True

    def render_step(self):
        # The supplied texture is unchanged; switch feedback is on scanner/state.
        pass
