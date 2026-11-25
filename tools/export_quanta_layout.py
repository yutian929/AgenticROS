"""Derive a fixed ALOHA layout from Quanta public kinematics/calibration.

Standard-library maintainer tool, no Isaac/ROS/native dependencies. Normal ALOHA
builds use the checked-in profile and do not read Quanta source or assets.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def profile():
    source = ROOT/'docker/robot_instances/robot_docs/quanta_x1'
    spec = importlib.util.spec_from_file_location('quanta_layout_math', ROOT/'docker/robot_instances/quanta_x1/cartesian.py')
    math_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(math_module)
    k = math_module
    joints = json.loads((source/'kinematics.json').read_text())['joints']
    by_child = {j['child'][0]: j for j in joints}
    values = dict(lift_joint=.379, head_yaw_joint=0., head_pitch_joint=-math.pi/12)
    cache = {'base_link': k.identity()}
    def anchor(j, prefix):
        w, x, y, z = j[prefix+'_rotation']
        return k.transform(j[prefix+'_anchor'], [x, y, z, w])
    def pose(link):
        if link not in cache:
            j = by_child[link]
            motion = k.identity()
            value = values.get(j['name'], 0.)
            if j['type'] == 'PhysicsPrismaticJoint':
                motion['XYZ'.index(j['axis'])][3] = value
            elif j['type'] == 'PhysicsRevoluteJoint':
                motion = k.rotation([value*float(a == j['axis']) for a in 'XYZ'])
            cache[link] = k.multiply(k.multiply(k.multiply(pose(j['parent'][0]), anchor(j, 'parent')),
                                               motion), k.inverse(anchor(j, 'child')))
        return cache[link]
    # Quanta base: +X forward / +Y left. ALOHA table: +X right / +Y forward.
    # The table center is Quanta base [0.9,0,0.8] in stationary tabletop mode.
    table_to_base = k.transform([.9, 0., .8], [0., 0., -math.sqrt(.5), math.sqrt(.5)])
    base_to_table = k.inverse(table_to_base)
    def record(transform):
        xyzw = k.quaternion(transform)
        return dict(position_m=[transform[i][3] for i in range(3)],
                    quaternion_wxyz=[xyzw[3], *xyzw[:3]])
    arms = {arm: record(k.multiply(base_to_table, pose(arm+'_arm_base_link'))) for arm in ('left', 'right')}
    optical = k.multiply(base_to_table, pose('camera_head_front_color_optical_frame'))
    camera = k.multiply(optical, k.transform([0., 0., 0.], [1., 0., 0., 0.]))
    calibration = json.loads((source/'cameras.json').read_text())['cameras']['head']
    gap = math.dist(arms['left']['position_m'], arms['right']['position_m'])
    home_tcp = k.Kinematics(source/'kinematics.json').describe('left', [0.]*7)
    return dict(layout='parallel_quanta_tabletop',
                source='Quanta EX001 public kinematics and head calibration; stationary tabletop',
                source_kinematics_sha256=hashlib.sha256((source/'kinematics.json').read_bytes()).hexdigest(),
                source_camera_sha256=hashlib.sha256((source/'cameras.json').read_bytes()).hexdigest(),
                reference_joints=values, base_to_table=base_to_table,
                table=dict(width_m=1.4, depth_m=1., top_z_m=0.),
                arms=arms, arm_spacing_m=gap,
                home_joint_positions=[-4.452380940566609e-6, -.18970944695618902, 1.2458661177668222,
                                      -5.114823037584833e-6, -1.056149084139735, 2.5177745112936335e-6, .0084],
                home_tcp_in_arm_base=home_tcp,
                home_notes='Native ALOHA IK solution for Quanta zero-arm TCP pose; retain ALOHA finger travel in metres. '
                           'Verified against the compiled native ALOHA model in layout physics tests.',
                main_camera=dict(name='head', model_camera='head_cam', **record(camera),
                                 optical_pose=record(optical), width=calibration['width'], height=calibration['height'],
                                 k=calibration['k'], d=[0.]*5, distortion_model='plumb_bob',
                                 source_distortion=calibration['d'],
                                 notes='Same pose and ideal pinhole intrinsics; Quanta lens distortion is not applied by MuJoCo.'),
                notes='Mounts share one forward direction. Native ALOHA links/joints/gains remain unchanged; '
                      'regenerate adapters because shared-base poses and camera geometry have changed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docker/robot_instances/aloha_tabletop/layout.json')
    args = parser.parse_args()
    args.output.write_text(json.dumps(profile(), indent=2)+'\n')
    print(args.output)
