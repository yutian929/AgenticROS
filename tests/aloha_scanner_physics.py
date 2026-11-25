"""Private isolated MuJoCo asset, contact and spring validation; never in Agent."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
from PIL import Image

sys.path.insert(0, '/opt/body')
from scanner import Scanner

OUT = Path('/tmp/test-evidence')
OUT.mkdir(exist_ok=True)
ROOT = Path('/opt/body')


def bench():
    xml = ET.parse(ROOT/'scanner_asset/scanner.xml')
    root = xml.getroot()
    ET.SubElement(root, 'compiler', meshdir=str(ROOT/'model/assets'),
                  texturedir=str(ROOT/'model/assets'))
    ET.SubElement(root, 'option', timestep='.002', gravity='0 0 0', integrator='implicitfast')
    body = root.find('worldbody/body')
    body.remove(body.find('freejoint'))
    body.set('pos', '0 0 0')
    body.set('quat', '1 0 0 0')
    pusher = ET.SubElement(root.find('worldbody'), 'body', name='test_pusher', mocap='true', pos='.04 0 .1')
    ET.SubElement(pusher, 'geom', name='test_pusher_geom', type='sphere', size='.006')
    model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding='unicode'))
    return model, mujoco.MjData(model), Scanner(model)


def step(model, data, scanner, seconds):
    for _ in range(round(seconds/model.opt.timestep)):
        mujoco.mj_step(model, data)
        scanner.step(data)
        assert np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()


def main():
    model, data, scanner = bench()
    jid = model.joint('scanner_trigger_slide').id
    qid, vid = int(model.jnt_qposadr[jid]), int(model.jnt_dofadr[jid])
    # Apply a known joint load; displacement is solved by dynamics, not assigned.
    data.qfrc_applied[vid] = 2.
    step(model, data, scanner, .5)
    loaded = float(data.qpos[qid])
    assert .0048 < loaded < .0052, loaded  # F / k = 2 / 400 = 5 mm.
    assert scanner.status()['trigger_pressed'] and scanner.status()['press_count'] == 1
    data.qfrc_applied[:] = 0
    step(model, data, scanner, .5)
    released = float(data.qpos[qid])
    assert abs(released) < .00005 and not scanner.status()['trigger_pressed'], released
    assert scanner.status()['press_count'] == 1
    # A physical pusher must depress the mesh collider and cause a second edge.
    trigger_geom = model.geom('scanner_trigger_collision_geom').id
    pusher_geom = model.geom('test_pusher_geom').id
    contacts = 0
    for x in np.linspace(.04, .0105, 200):
        data.mocap_pos[0] = [x, 0., .1]
        step(model, data, scanner, .004)
        contacts += sum({c.geom1, c.geom2} == {trigger_geom, pusher_geom} for c in data.contact)
        if data.qpos[qid] >= .005:
            break
    step(model, data, scanner, .25)
    contact_travel = float(data.qpos[qid])
    assert contacts > 0, 'pusher never contacted trigger mesh'
    assert scanner.status()['trigger_pressed'] and scanner.status()['press_count'] == 2, (contact_travel, scanner.status())
    assert contact_travel <= .0063, contact_travel
    data.mocap_pos[0] = [.04, 0., .1]
    step(model, data, scanner, .5)
    assert abs(data.qpos[qid]) < .00005 and not scanner.status()['trigger_pressed']
    assert scanner.status()['press_count'] == 2
    bench_report = dict(load_n=2., equilibrium_m=loaded, returned_m=released,
                        pusher_contact_samples=int(contacts), contact_travel_m=contact_travel,
                        final_switch=scanner.status())
    # Settle the free asset on the real ALOHA table with both arms at neutral.
    model = mujoco.MjModel.from_xml_path(str(ROOT/'model/agenticros_scene.xml'))
    data = mujoco.MjData(model)
    layout = json.loads((ROOT/'layout.json').read_text())
    neutral = [*layout['home_joint_positions'], layout['home_joint_positions'][-1]]
    names = ['waist', 'shoulder', 'elbow', 'forearm_roll', 'wrist_angle', 'wrist_rotate', 'left_finger', 'right_finger']
    for arm in ('left', 'right'):
        for name, value in zip(names, neutral):
            data.qpos[model.joint(arm+'/'+name).qposadr[0]] = value
    data.ctrl[:] = [*neutral[:7], *neutral[:7]]
    scanner = Scanner(model)
    step(model, data, scanner, 5.)
    free = model.joint('scanner_free')
    dof = int(free.dofadr[0])
    settled_position = data.body('scanner').xpos.copy()
    assert np.max(np.abs(data.qvel[dof:dof+6])) < .005, data.qvel[dof:dof+6]
    assert not scanner.status()['trigger_pressed'] and scanner.status()['press_count'] == 0
    assert -.01 < settled_position[2] < .08, settled_position
    # Verify free-body translation under a load and rest after its removal.
    scanner_bid = model.body('scanner').id
    data.xfrc_applied[scanner_bid, 0] = 2.
    step(model, data, scanner, .4)
    data.xfrc_applied[:] = 0
    step(model, data, scanner, 3.)
    translated = float(np.linalg.norm(data.body('scanner').xpos[:2]-settled_position[:2]))
    assert translated > .01, translated
    assert np.max(np.abs(data.qvel[dof:dof+6])) < .01, data.qvel[dof:dof+6]
    table_report = dict(rest_position=settled_position.tolist(), loaded_translation_m=translated,
                        final_max_speed=float(np.max(np.abs(data.qvel[dof:dof+6]))),
                        body_mass_kg=float(model.body('scanner').mass[0]),
                        trigger_mass_kg=float(model.body('scanner_trigger').mass[0]))
    # Restore the initial table layout for review images, then settle naturally.
    data = mujoco.MjData(model)
    for arm in ('left', 'right'):
        for name, value in zip(names, neutral):
            data.qpos[model.joint(arm+'/'+name).qposadr[0]] = value
    data.ctrl[:] = [*neutral[:7], *neutral[:7]]
    step(model, data, Scanner(model), 3.)
    model.vis.global_.offwidth, model.vis.global_.offheight = 1280, 720
    with mujoco.Renderer(model, height=720, width=1280) as renderer:
        renderer.update_scene(data, camera='head_cam')
        Image.fromarray(renderer.render()).save(OUT/'aloha_parallel_head.png')
    with mujoco.Renderer(model, height=720, width=960) as renderer:
        renderer.update_scene(data, camera='overhead_cam')
        Image.fromarray(renderer.render()).save(OUT/'aloha_scanner_overhead.png')
        camera = mujoco.MjvCamera()
        camera.lookat[:] = data.body('scanner').xpos + [0., -.07, .0]
        camera.distance, camera.azimuth, camera.elevation = .38, 100., -45.
        renderer.update_scene(data, camera=camera)
        Image.fromarray(renderer.render()).save(OUT/'aloha_scanner_closeup.png')
        camera.lookat[:] = [0., -.25, .05]
        camera.distance, camera.azimuth, camera.elevation = 2., -90., -30.
        renderer.update_scene(data, camera=camera)
        Image.fromarray(renderer.render()).save(OUT/'aloha_parallel_overview.png')
    report = dict(result='PASS', scope='isolated asset/contact/spring physics; no autonomous grasp/task evaluation',
                  mujoco=mujoco.__version__, bench=bench_report, tabletop=table_report)
    (OUT/'physics.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
