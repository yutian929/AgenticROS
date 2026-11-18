"""Build a derived tabletop without changing upstream ALOHA assets."""
from pathlib import Path
import xml.etree.ElementTree as ET


def prepare(root):
    root = Path(root)
    robot = ET.parse(root / 'aloha.xml')
    for include in list(robot.getroot().findall('include')):
        if include.get('file') == 'keyframe_ctrl.xml':
            robot.getroot().remove(include)
    robot.write(root / 'agenticros_aloha.xml')
    scene = ET.parse(root / 'scene.xml')
    scene.getroot().find('include').set('file', 'agenticros_aloha.xml')
    scene.find('visual/quality').set('shadowsize', '1024')
    scene.find('visual/global').attrib.update(offwidth='640', offheight='480')
    body = scene.find('worldbody')
    # Use an explicit square-pixel pinhole camera for the 320x240 ROS stream.
    # Upstream sensor-size intrinsics target another resolution/aspect ratio.
    camera = body.find("camera[@name='overhead_cam']")
    for key in ('focal', 'resolution', 'sensorsize'):
        camera.attrib.pop(key, None)
    camera.set('fovy', '45')
    for color, x, rgba in [('red', -.18, '.9 .04 .03 1'), ('blue', .18, '.03 .12 .9 1')]:
        block = ET.SubElement(body, 'body', name=color+'_block', pos=f'{x} -.12 .025')
        ET.SubElement(block, 'freejoint')
        ET.SubElement(block, 'geom', name=color+'_block_geom', type='box', size='.02 .02 .02', mass='.045', rgba=rgba, friction='1.5 .01 .001')
        ET.SubElement(body, 'geom', name=color+'_plate', type='cylinder', pos=f'{x} .12 .006', size='.065 .006', rgba=rgba)
    scene.write(root / 'agenticros_scene.xml')


if __name__ == '__main__':
    prepare(Path(__file__).parent / 'model')
