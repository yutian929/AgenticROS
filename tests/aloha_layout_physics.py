"""Private layout, neutral collision and main-camera projection checks."""
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, '/opt/body')
import mujoco
import numpy as np
from PIL import Image
from cartesian import tcp_pose, arm_base_pose, relative_pose, matrix_quat, pose_error

ROOT = Path('/opt/body')
OUT = Path('/tmp/test-evidence')
OUT.mkdir(exist_ok=True)


def rotation(wxyz):
    result = np.empty(9)
    mujoco.mju_quat2Mat(result, np.array(wxyz, dtype=float))
    return result.reshape(3, 3)


def initialize(model, layout):
    data = mujoco.MjData(model)
    names = ('waist', 'shoulder', 'elbow', 'forearm_roll', 'wrist_angle', 'wrist_rotate', 'left_finger', 'right_finger')
    home = [*layout['home_joint_positions'], layout['home_joint_positions'][-1]]
    for arm in ('left', 'right'):
        for n, value in zip(names, home):
            data.qpos[model.joint(arm+'/'+n).qposadr[0]] = value
    data.ctrl[:] = home[:7]*2
    mujoco.mj_forward(model, data)
    return data


def main():
    layout = json.loads((ROOT/'layout.json').read_text())
    model = mujoco.MjModel.from_xml_path(str(ROOT/'model/agenticros_scene.xml'))
    data = initialize(model, layout)
    mounts = {a: arm_base_pose(model, data, a) for a in ('left', 'right')}
    distance = float(np.linalg.norm(mounts['left'][0]-mounts['right'][0]))
    assert abs(distance-layout['arm_spacing_m']) < 1e-9
    assert np.allclose(mounts['left'][1], mounts['right'][1], atol=1e-10)
    assert np.allclose(mounts['left'][1][:, 0], [0, 1, 0], atol=1e-10)
    expected = layout['home_tcp_in_arm_base']
    target = (np.array(list(expected['position'].values())),
              rotation([expected['orientation'][k] for k in ('w', 'x', 'y', 'z')]))
    home_errors = {}
    for arm in ('left', 'right'):
        assert np.allclose(mounts[arm][0], layout['arms'][arm]['position_m'], atol=1e-10)
        local = relative_pose(tcp_pose(model, data, arm), mounts[arm])
        ep, er = pose_error(local, target)
        assert ep < .00015 and er < .0015, (arm, ep, er)
        home_errors[arm] = dict(position_m=ep, orientation_rad=er)
    # Check all physical contacts during a three-second uncommanded neutral hold.
    def arm_for_geom(gid):
        name = model.body(int(model.geom_bodyid[gid])).name
        return name.split('/')[0] if name.startswith(('left/', 'right/')) else None
    invalid = []
    table = model.geom('table').id
    for step in range(1500):
        mujoco.mj_step(model, data)
        if step % 5:
            continue
        for c in data.contact:
            a, b = arm_for_geom(c.geom1), arm_for_geom(c.geom2)
            if (a and b and a != b) or (a and c.geom2 == table) or (b and c.geom1 == table):
                invalid.append([int(c.geom1), int(c.geom2), float(c.dist)])
            names = [model.geom(c.geom1).name or '', model.geom(c.geom2).name or '']
            if any(n.startswith('scanner_') for n in names) and any(n in ('red_plate', 'blue_plate', 'red_block_geom', 'blue_block_geom') for n in names):
                invalid.append(['scanner_prop_overlap', *names])
    assert not invalid, invalid
    assert np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()
    head = layout['main_camera'];cid = model.camera('head_cam').id
    optical = data.cam_xmat[cid].reshape(3, 3)@np.diag([1., -1., -1.])
    assert np.allclose(data.cam_xpos[cid], head['optical_pose']['position_m'], atol=1e-10)
    assert np.allclose(optical, rotation(head['optical_pose']['quaternion_wxyz']), atol=1e-10)
    assert tuple(model.cam_resolution[cid]) == (1280, 720)
    # An independent colored target validates raster projection against published K.
    path = Path('/tmp/layout-projection-model')
    shutil.copytree(ROOT/'model', path)
    tree = ET.parse(path/'agenticros_scene.xml')
    point_optical = np.array([.1, .05, 1.])
    point = data.cam_xpos[cid]+optical@point_optical
    asset = tree.find('asset')
    ET.SubElement(asset, 'material', name='projection_green', rgba='0 1 0 1', emission='1')
    ET.SubElement(tree.find('worldbody'), 'geom', name='projection_marker', type='sphere',
                  pos=' '.join(map(str, point)), size='.006', material='projection_green',
                  contype='0', conaffinity='0')
    tree.write(path/'projection.xml')
    probe_model = mujoco.MjModel.from_xml_path(str(path/'projection.xml'))
    probe_data = initialize(probe_model, layout)
    with mujoco.Renderer(probe_model, width=1280, height=720) as renderer:
        renderer.update_scene(probe_data, camera='head_cam')
        pixels = renderer.render()
        Image.fromarray(pixels).save(OUT/'main_camera_projection_check.png')
    mask = (pixels[:, :, 1] > 180)&(pixels[:, :, 0] < 70)&(pixels[:, :, 2] < 70)
    yy, xx = np.where(mask)
    assert len(xx) > 10, 'projection marker was not visible'
    actual = np.array([xx.mean(), yy.mean()])
    k = np.array(head['k']).reshape(3, 3)
    expected_pixel = (k@point_optical)[:2]
    error = float(np.linalg.norm(actual-expected_pixel))
    assert error < 2., (actual, expected_pixel, error)
    report = dict(result='PASS', scope='isolated native MuJoCo layout/neutral/projection checks',
                  arm_spacing_m=distance, parallel_forward_axes=True, home_pose_errors=home_errors,
                  neutral_arm_table_or_interarm_contacts=invalid,
                  head_pose_matches_quanta=True, head_resolution=[1280, 720],
                  projection=dict(actual_pixel=actual.tolist(), expected_pixel=expected_pixel.tolist(), error_px=error))
    (OUT/'layout.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':main()
