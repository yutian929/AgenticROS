"""Privileged component fixture: collision-driven button press, hold and spring return.

Runs only in a disposable robot-test container, never in the Agent image/workspace.
The clamp/pusher are laboratory fixtures, not an autonomous two-arm solution.
"""
import json
from pathlib import Path
import numpy as np
from isaacsim import SimulationApp
app = SimulationApp(dict(headless=True, renderer='RaytracedLighting', anti_aliasing=0,multi_gpu=False))
from pxr import UsdPhysics,UsdGeom,UsdLux,Gf
from isaacsim.core.api import World
from isaacsim.core.api.objects import FixedCuboid
from isaacsim.core.prims import SingleRigidPrim
from isaacsim.sensors.camera import Camera
from PIL import Image
from scanner_scene import ScannerScene,BODY,TRIGGER,TRIGGER_ANCHOR,HOME_QUAT,HOME_POSITION,rotation,cube

world=World(physics_dt=1/240,rendering_dt=1/30,stage_units_in_meters=1.,backend='numpy',device='cpu')
stage=world.stage
world.scene.add(FixedCuboid('/World/Table',name='table',position=np.array([-.85,0.,.775]),scale=np.array([1.,1.2,.05]),color=np.array([.35,.38,.42])))
UsdLux.DomeLight.Define(stage,'/World/Light').CreateIntensityAttr(1000.)
# Keep the clamp fixture upright independently of the production table layout.
home=np.array([-.65,.16,.801]);fixture_quat=np.array([0.,0.,0.,1.])
scanner=ScannerScene(world, position=home, orientation=fixture_quat)
clamp=UsdPhysics.FixedJoint.Define(stage,'/World/FixtureClamp')
clamp.CreateBody1Rel().SetTargets([BODY]);clamp.CreateLocalPos0Attr(Gf.Vec3f(*map(float,home)));clamp.CreateLocalRot0Attr(Gf.Quatf(0.,0.,0.,1.))
pusher_pos=home+rotation(fixture_quat)@TRIGGER_ANCHOR+np.array([-.035,0.,0.])
released_x=float(pusher_pos[0]);pressed_x=float(home[0]-TRIGGER_ANCHOR[0]-.009-.006+.005)
prim=cube(stage,'/World/Pusher',pusher_pos,(.012,.025,.020),(.6,.3,.7))
UsdPhysics.RigidBodyAPI.Apply(prim).CreateKinematicEnabledAttr(True)
pusher=SingleRigidPrim('/World/Pusher',name='pusher')
view=Camera('/World/Overview',name='overview',resolution=(960,720),frequency=30)
view.set_clipping_range(.01,10.)
view.set_focal_length(2.5)
# USD camera looks down -Z, with +Y up.
from scipy.spatial.transform import Rotation
eye=np.array([-.95,-.22,1.10]);forward=home+np.array([0.,0.,.083])-eye
forward/=np.linalg.norm(forward)
right=np.cross(forward,[0.,0.,1.]);right/=np.linalg.norm(right)
up=np.cross(right,forward)
q=Rotation.from_matrix(np.column_stack((right,up,-forward))).as_quat()
view.set_world_pose(position=eye,orientation=q[[3,0,1,2]],camera_axes='usd')
world.reset();scanner.initialize();pusher.initialize();view.initialize()
travel=[];frame=0
out=Path('/evidence')

def advance(steps, end_x=None):
 global frame,pusher_pos
 start=float(pusher_pos[0])
 for i in range(steps):
  if end_x is not None:
   pusher_pos[0]=start+(end_x-start)*(i+1)/steps
   pusher.set_world_pose(position=pusher_pos)
  world.step(render=False)
  scanner.physics_step(float(world.current_time));travel.append(scanner.travel)
  if frame%8==0:
   world.render()
   scanner.render_step()
  frame+=1

def save(name,camera):
 rgba=camera.get_rgba(device='cpu')
 Image.fromarray(rgba[:,:,:3]).save(out/name)

try:
 advance(240)
 save('scene.png',view)
 assert scanner.switch.press_count==0 and not scanner.switch.pressed
 advance(60,pressed_x);advance(300)
 save('trigger_pressed.png',view)
 assert max(travel)>.004, max(travel)
 assert scanner.switch.press_count==1 and scanner.switch.pressed,scanner.switch.status()
 advance(60,released_x);advance(120)
 assert scanner.travel<.0015 and not scanner.switch.pressed,scanner.travel
 advance(60,pressed_x);advance(300)
 assert scanner.switch.press_count==2 and scanner.switch.pressed,scanner.switch.status()
 advance(60,released_x);advance(120)
 assert scanner.travel<.0015 and not scanner.switch.pressed,scanner.travel
 UsdGeom.Imageable(prim).MakeInvisible()
 advance(60)
 save('trigger_released.png',view)
 # Release the laboratory clamp and verify the actual right-side lying layout.
 stage.RemovePrim('/World/FixtureClamp')
 UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
 scanner.body.set_world_pose(position=HOME_POSITION,orientation=HOME_QUAT)
 scanner.trigger.set_world_pose(position=HOME_POSITION+rotation(HOME_QUAT)@TRIGGER_ANCHOR,orientation=HOME_QUAT)
 scanner.body.set_linear_velocity(np.zeros(3));scanner.body.set_angular_velocity(np.zeros(3))
 scanner.trigger.set_linear_velocity(np.zeros(3));scanner.trigger.set_angular_velocity(np.zeros(3))
 advance(720)
 body_position,body_quat=scanner.body.get_world_pose()
 assert np.isfinite(body_position).all() and np.linalg.norm(body_position-HOME_POSITION)<.025,body_position
 assert .797 < scanner.minimum_height() < .806,scanner.minimum_height()
 assert abs(rotation(body_quat)[2,1])>.95,body_quat
 assert scanner.switch.press_count==2 and not scanner.switch.pressed,scanner.switch.status()
 save('side_lying.png',view)
 (out/'result.json').write_text(json.dumps(dict(result='PASS',scope='mechanical scanner component fixture; not autonomous bimanual task evaluation',switch=scanner.switch.status(),max_trigger_travel_m=max(travel),released_travel_m=scanner.travel),indent=2))
 print('SCANNER_COMPONENT_PASS',flush=True)
except BaseException:
 import traceback
 failure=traceback.format_exc()
 (out/'failure.txt').write_text(failure)
 print(failure,flush=True)
 raise
finally:
 world.stop();app.close()
