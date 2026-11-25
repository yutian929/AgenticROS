"""Export the existing split USD scanner to MJCF/OBJ without remeshing.

Maintainer asset tool only: requires numpy and usd-core, never Isaac/Kit/GPU.
Runtime and normal Docker builds use the checked-in output, not USD tooling.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import numpy as np
from pxr import Usd, UsdGeom


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_mesh(mesh, destination, visual=False):
    points = np.asarray(mesh.GetPointsAttr().Get(), dtype=float)
    faces = np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)
    assert all(n == 3 for n in mesh.GetFaceVertexCountsAttr().Get())
    assert mesh.GetOrientationAttr().Get() == 'rightHanded'
    assert np.allclose(np.asarray(UsdGeom.Xformable(mesh).GetLocalTransformation()), np.eye(4))
    normals = np.asarray(mesh.GetNormalsAttr().Get()) if visual else None
    uv = UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st') if visual else None
    if visual:
        assert uv.GetInterpolation() == mesh.GetNormalsInterpolation() == 'vertex'
        texcoords = np.asarray(uv.ComputeFlattened())
        assert len(points) == len(normals) == len(texcoords)
    with destination.open('w') as output:
        output.write('# Original scanner vertices, triangles and per-vertex UVs; metres.\n')
        for p in points:
            output.write('v ' + ' '.join(format(x, '.9g') for x in p) + '\n')
        if visual:
            for uv_value in texcoords:
                output.write('vt ' + ' '.join(format(x, '.9g') for x in uv_value) + '\n')
            for n in normals:
                output.write('vn ' + ' '.join(format(x, '.9g') for x in n) + '\n')
        for face in faces + 1:
            output.write('f ' + ' '.join(f'{i}/{i}/{i}' if visual else str(i) for i in face) + '\n')
    volume = abs(float(np.einsum('ij,ij->i', points[faces[:, 0]],
                               np.cross(points[faces[:, 1]], points[faces[:, 2]])).sum() / 6))
    return dict(vertices=len(points), faces=len(faces), volume_m3=volume,
                bounds_m=[points.min(0).tolist(), points.max(0).tolist()], sha256=digest(destination))


def prepare(source, output):
    source, output = Path(source), Path(output)
    original = json.loads((source/'manifest.json').read_text())
    stage = Usd.Stage.Open(str(source/'scanner.usdc'))
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source/'albedo.png', output/'albedo.png')
    meshes = {}
    for part in ('body', 'trigger'):
        meshes[part] = export_mesh(UsdGeom.Mesh(stage.GetPrimAtPath(f'/Scanner/{part}/Visual')),
                                   output/f'{part}.obj', visual=True)
    hulls = sorted(stage.GetPrimAtPath('/Scanner/body/Colliders').GetChildren(), key=lambda p: str(p.GetPath()))
    for i, prim in enumerate(hulls):
        name = f'hull_{i:02}'
        meshes[name] = export_mesh(UsdGeom.Mesh(prim), output/f'{name}.obj')
    meshes['trigger_collision'] = export_mesh(UsdGeom.Mesh(stage.GetPrimAtPath('/Scanner/trigger/Collider')),
                                             output/'trigger_collision.obj')
    assert meshes['body']['faces'] + meshes['trigger']['faces'] == original['source_faces']
    assert len(hulls) == original['body_collision_hulls']
    mjcf = ET.Element('mujoco', model='textured_spring_scanner')
    asset = ET.SubElement(mjcf, 'asset')
    ET.SubElement(asset, 'texture', name='scanner_albedo', type='2d', file='scanner/albedo.png')
    ET.SubElement(asset, 'material', name='scanner_plastic', texture='scanner_albedo',
                  texuniform='false', rgba='1 1 1 1', specular='0.25', shininess='0.25')
    for name in meshes:
        ET.SubElement(asset, 'mesh', name='scanner_'+name, file='scanner/'+name+'.obj')
    worldbody = ET.SubElement(mjcf, 'worldbody')
    # ALOHA faces +Y. Lie on the right side of the table, away from the blocks.
    body = ET.SubElement(worldbody, 'body', name='scanner', pos='.28 .03 .033',
                         quat='.7071067811865476 .7071067811865476 0 0')
    ET.SubElement(body, 'freejoint', name='scanner_free')
    def geom(parent, name, mass=None):
        attributes = dict(name='scanner_'+name+'_geom', type='mesh', mesh='scanner_'+name)
        if mass is None:
            attributes.update(contype='0', conaffinity='0', mass='0', group='2', material='scanner_plastic')
        else:
            attributes.update(mass=format(mass, '.12g'), group='3', rgba='0 0 0 0',
                              condim='4', friction='1 .005 .0005', solref='.01 1', solimp='.95 .99 .001')
        ET.SubElement(parent, 'geom', **attributes)
    geom(body, 'body')
    total_volume = sum(meshes[f'hull_{i:02}']['volume_m3'] for i in range(len(hulls)))
    for i in range(len(hulls)):
        name = f'hull_{i:02}'
        geom(body, name, .168 * meshes[name]['volume_m3'] / total_volume)
    trigger = ET.SubElement(body, 'body', name='scanner_trigger', pos='.008 0 .1')
    ET.SubElement(trigger, 'joint', name='scanner_trigger_slide', type='slide', axis='-1 0 0',
                  limited='true', range='0 .006', stiffness='400', springref='0', damping='3',
                  solreflimit='.004 1', solimplimit='.95 .99 .001')
    geom(trigger, 'trigger')
    geom(trigger, 'trigger_collision', .012)
    # Match the USD joint's disabled body0/body1 collision explicitly, including
    # when the casing is welded to a test bench (world-parent filtering differs).
    contact = ET.SubElement(mjcf, 'contact')
    ET.SubElement(contact, 'exclude', body1='scanner', body2='scanner_trigger')
    ET.indent(mjcf)
    ET.ElementTree(mjcf).write(output/'scanner.xml', encoding='unicode')
    manifest = dict(source=original['source'], source_sha256=original['source_sha256'],
                    split_usd_sha256=digest(source/'scanner.usdc'), albedo_sha256=digest(output/'albedo.png'),
                    dimensions_m=original['dimensions_m'], meshes=meshes, mass_kg=.18,
                    body_mass_kg=.168, trigger_mass_kg=.012, trigger_anchor_m=[.008, 0., .1],
                    spring=dict(travel_m=.006, stiffness_n_m=400., damping_ns_m=3.,
                                press_m=.004, release_m=.0015, debounce_s=.03),
                    notes='Unmodified albedo, vertex positions, normals, UVs and 50000 triangles. '
                          'Original 22 convex casing hulls and trigger hull retained. '
                          'Body inertia inferred from volume-weighted hull masses. '
                          'Native passive spring: maximum spring force 2.4 N in travel; '
                          'Isaac drive force clamp 10 N has no exact passive-spring equivalent. '
                          'MuJoCo lighting/contact solver differ from Isaac.')
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), indent=2))
