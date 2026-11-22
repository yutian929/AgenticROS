"""Original movable scanner body and spring-return mechanical trigger.

Only switch feedback is public; geometry and private poses stay robot-side.
"""
import numpy as np
from pxr import Gf, UsdGeom, UsdPhysics, PhysxSchema
from isaacsim.core.prims import SingleRigidPrim
from scanner_device import MechanicalSwitch

BODY = '/World/Scanner/body'
TRIGGER = '/World/Scanner/trigger'
TRIGGER_ANCHOR = np.array([.012, 0., .102])
# Robot faces world -X; its right side is world +Y. Local +Y is up,
# placing the scanner on its side with the grip nearer the robot.
HOME_POSITION = np.array([-.55, .27, .837])
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


def loft(stage, path, sections, color, axis='z', power=.65, collision=True):
    """Smooth closed rounded cross sections, with convex hull collision."""
    count = 48
    points = []
    for coordinate, center, radius_a, radius_b in sections:
        for angle in np.linspace(0., 2*np.pi, count, endpoint=False):
            c, t = np.cos(angle), np.sin(angle)
            a = radius_a*np.sign(c)*abs(c)**power
            b = radius_b*np.sign(t)*abs(t)**power
            points.append((center+a,b,coordinate) if axis=='z' else (coordinate,a,center+b))
    faces = []
    for row in range(len(sections)-1):
        for i in range(count):
            j=(i+1)%count
            faces.append([row*count+i,row*count+j,(row+1)*count+j,(row+1)*count+i])
    faces += [list(reversed(range(count))), list(range((len(sections)-1)*count,len(points)))]
    # x cross sections use y,z; both bases are right handed.
    vertices=np.array(points)
    normals=np.zeros_like(vertices)
    for face in faces:
        normal=np.cross(vertices[face[1]]-vertices[face[0]],vertices[face[2]]-vertices[face[0]])
        normal/=max(np.linalg.norm(normal),1.e-12)
        for vertex in face:normals[vertex]+=normal
    normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1.e-12)
    mesh=UsdGeom.Mesh.Define(stage,path)
    mesh.CreatePointsAttr([Gf.Vec3f(*map(float,v)) for v in points])
    mesh.CreateFaceVertexCountsAttr([len(face) for face in faces])
    mesh.CreateFaceVertexIndicesAttr([v for face in faces for v in face])
    mesh.CreateNormalsAttr([Gf.Vec3f(*map(float,v)) for v in normals])
    mesh.SetNormalsInterpolation('vertex')
    mesh.CreateSubdivisionSchemeAttr('none')
    mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    if collision:
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr('convexHull')
    return mesh.GetPrim()


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
        rigid(stage, BODY, position, .24, orientation)
        # Photo-inspired proportions: 96 mm depth, 68 mm width, 165 mm height.
        # Dimensions are supplied by the reference; curvature is an approximation.
        loft(stage, BODY+'/head', [
            (-.048,.139,.010,.009),(-.045,.139,.023,.018),
            (-.038,.140,.031,.023),(-.027,.140,.034,.025),
            (.018,.140,.033,.024),(.037,.137,.031,.022),
            (.045,.136,.027,.018),(.0464,.136,.025,.016)],
            (.022,.025,.029),axis='x')
        loft(stage, BODY+'/handle', [
            (0.,-.018,.014,.016),(.005,-.018,.019,.020),
            (.016,-.020,.020,.020),(.035,-.025,.018,.018),
            (.065,-.028,.017,.017),(.090,-.025,.018,.017),
            (.110,-.020,.020,.019),(.125,-.017,.024,.022)],
            (.018,.020,.024))
        # Dark rubber lower boot follows the curved grip; no oversized stand.
        loft(stage, BODY+'/boot', [
            (.0002,-.018,.0145,.0165),(.005,-.018,.0195,.0205),
            (.015,-.020,.0205,.0205),(.021,-.021,.0195,.020)],
            (.009,.011,.013),collision=False)
        # Recessed dark red optical window is visual only, not a reader.
        loft(stage, BODY+'/bezel', [(.0463,.136,.0255,.0165),
            (.0474,.136,.025,.016),(.0477,.136,.0235,.0145)],
            (.006,.007,.009),axis='x',collision=False)
        loft(stage, BODY+'/window', [(.0478,.136,.022,.0125),(.048,.136,.022,.0125)],
            (.16,.009,.012),axis='x',collision=False)
        cube(stage, BODY+'/indicator', (-.006,0.,.1644), (.013,.006,.0008),(.04,.22,.20),False)
        trigger_position = position + rotation(orientation)@TRIGGER_ANCHOR
        rigid(stage, TRIGGER, trigger_position, .012, orientation)
        loft(stage, TRIGGER+'/button', [
            (-.008,0.,.011,.012),(-.004,0.,.014,.017),
            (.004,0.,.014,.017),(.009,0.,.010,.014)],
            (1.,.20,.008),axis='x',power=.72)
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
            for path in (BODY, TRIGGER):
                vertices = []
                for child in self.stage.GetPrimAtPath(path).GetChildren():
                    if child.IsA(UsdGeom.Mesh):
                        points = UsdGeom.Mesh(child).GetPointsAttr().Get()
                    elif child.IsA(UsdGeom.Cube):
                        half = UsdGeom.Cube(child).GetSizeAttr().Get()/2
                        points = [(x,y,z) for x in (-half,half) for y in (-half,half) for z in (-half,half)]
                    else:
                        continue
                    matrix = UsdGeom.Xformable(child).GetLocalTransformation()
                    vertices.extend(tuple(matrix.Transform(Gf.Vec3d(*map(float,v)))) for v in points)
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
        color = (.10,.85,.15) if self.switch.pressed else (.12,.25,.10)
        UsdGeom.Gprim(self.stage.GetPrimAtPath(BODY+'/indicator')).GetDisplayColorAttr().Set([Gf.Vec3f(*color)])
