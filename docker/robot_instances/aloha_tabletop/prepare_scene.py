"""Build a derived tabletop without changing upstream ALOHA assets."""
from pathlib import Path
import json
import shutil
import xml.etree.ElementTree as ET


def prepare(root):
    root = Path(root)
    layout = json.loads((root.parent/'layout.json').read_text())
    numbers = lambda values: ' '.join(format(x, '.12g') for x in values)
    robot = ET.parse(root / 'aloha.xml')
    for arm, mount in layout['arms'].items():
        body = robot.find(f"worldbody/body[@name='{arm}/base_link']")
        body.set('pos', numbers(mount['position_m']))
        body.set('quat', numbers(mount['quaternion_wxyz']))
    for include in list(robot.getroot().findall('include')):
        if include.get('file') == 'keyframe_ctrl.xml':
            robot.getroot().remove(include)
    for camera in robot.getroot().iter('camera'):
        if camera.get('name', '').startswith('wrist_cam_'):
            for key in ('focal', 'resolution', 'sensorsize'):
                camera.attrib.pop(key, None)
            camera.set('fovy', '58')
    robot.write(root / 'agenticros_aloha.xml')
    scene = ET.parse(root / 'scene.xml')
    scene.getroot().find('include').set('file', 'agenticros_aloha.xml')
    scanner = root.parent / 'scanner_asset'
    shutil.copytree(scanner, root / 'assets/scanner', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('*.py', '__pycache__'))
    scanner_xml = ET.parse(scanner / 'scanner.xml')
    scanner_xml.find('worldbody/body').set('pos', '.32 .02 .033')
    # Keep generated scene composition separate from the source asset fragment.
    for child in scanner_xml.getroot():
        scene.getroot().append(child)
    scene.find('visual/quality').set('shadowsize', '1024')
    scene.find('visual/global').attrib.update(offwidth='1280', offheight='720', azimuth='-90', elevation='-25')
    body = scene.find('worldbody')
    # The upstream gantry holds opposing arms/cameras. Replace it with two fixed
    # rear mounts; do not leave misleading rails or camera housings in the view.
    for geom in list(body.findall("geom[@class='frame']")):
        body.remove(geom)
    table = body.find("geom[@name='table']")
    table.set('size', '.7 .5 .025')
    table.set('pos', '0 0 -.025')
    for mesh in scene.findall('asset/mesh'):
        if mesh.get('file') in ('tabletop.obj', 'tablelegs.obj'):
            mesh.set('scale', numbers([1./.762, 1.4/1.2192, 1.]))
    for arm, mount in layout['arms'].items():
        x, y, z = mount['position_m']
        bottom, top = -.75, z-.02
        ET.SubElement(body, 'geom', name=arm+'_mount_pedestal', type='box',
                      pos=numbers([x, y, (bottom+top)/2]), size=numbers([.075, .075, (top-bottom)/2]),
                      rgba='.22 .25 .29 1', friction='1 .005 .0005')
    main = layout['main_camera']
    k = main['k']
    # Convert OpenCV principal coordinates to MuJoCo frustum offsets; the
    # sign is checked against an independently rendered projection marker.
    ET.SubElement(body, 'camera', name=main['model_camera'], mode='fixed',
                  pos=numbers(main['position_m']), quat=numbers(main['quaternion_wxyz']),
                  resolution=numbers([main['width'], main['height']]),
                  sensorsize=numbers([main['width'], main['height']]), focal=numbers([k[0], k[4]]),
                  principal=numbers([main['width']/2-k[2], k[5]-main['height']/2]))
    # Use an explicit square-pixel pinhole camera for the 320x240 ROS stream.
    # Upstream sensor-size intrinsics target another resolution/aspect ratio.
    camera = body.find("camera[@name='overhead_cam']")
    for key in ('focal', 'resolution', 'sensorsize'):
        camera.attrib.pop(key, None)
    camera.set('fovy', '45')
    for color, x, rgba in [('red', -.18, '.9 .04 .03 1'), ('blue', .18, '.03 .12 .9 1')]:
        block = ET.SubElement(body, 'body', name=color+'_block', pos=f'{x} -.16 .025')
        ET.SubElement(block, 'freejoint')
        ET.SubElement(block, 'geom', name=color+'_block_geom', type='box', size='.02 .02 .02', mass='.045', rgba=rgba, friction='1.5 .01 .001')
        ET.SubElement(body, 'geom', name=color+'_plate', type='cylinder', pos=f'{x} .02 .006', size='.065 .006', rgba=rgba)
    scene.write(root / 'agenticros_scene.xml')


if __name__ == '__main__':
    prepare(Path(__file__).parent / 'model')
