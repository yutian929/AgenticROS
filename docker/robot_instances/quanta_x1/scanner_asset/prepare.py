"""Rebuild the user-supplied scanner into two textured rigid-body visual parts.

Run with the robot image's /opt/sim/bin/python. No Kit or GPU is needed.
Original vertex positions, UVs, material and total face set are preserved at rest.
The trigger mask is specific to the pinned input, not a generic mesh segmenter.
"""
from pathlib import Path
import hashlib
import json
import zipfile
import numpy as np
import trimesh
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

ROOT = Path(__file__).resolve().parent
ANCHOR = np.array([.008, 0., .100])


def write_mesh(stage, path, vertices, faces, collision=False):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([Gf.Vec3f(*map(float, p)) for p in vertices])
    mesh.CreateFaceVertexCountsAttr([3]*len(faces))
    mesh.CreateFaceVertexIndicesAttr(np.asarray(faces).reshape(-1).tolist())
    mesh.CreateSubdivisionSchemeAttr('none')
    if collision:
        mesh.CreatePurposeAttr('guide')
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr('convexHull')
    return mesh


def clip(mesh, normal, origin):
    vertices = np.asarray(mesh.vertices)
    distance = (vertices-np.asarray(origin)) @ np.asarray(normal)
    points = list(vertices[distance >= 0])
    for a,b in mesh.edges_unique:
        if distance[a]*distance[b] < 0:
            points.append(vertices[a]+(vertices[b]-vertices[a])*distance[a]/(distance[a]-distance[b]))
    points = np.asarray(points)
    if len(points) < 4 or np.linalg.matrix_rank(points-points[0]) < 3:
        return None
    return trimesh.convex.convex_hull(points)


def main():
    source = ROOT/'source.usdz'
    expected = 'ce1f31421d39ad8225648e51a6f6f739102558148054943cefb9d37b29599bd4'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, 'Source changed; review segmentation before rebuilding'
    with zipfile.ZipFile(source) as archive:
        (ROOT/'albedo.png').write_bytes(archive.read('0/sam3d_albedo.png'))
    src = Usd.Stage.Open(str(source))
    stage = Usd.Stage.CreateNew(str(ROOT/'scanner.usdc'))
    UsdGeom.SetStageUpAxis(stage, 'Z')
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, '/Scanner').GetPrim())
    Sdf.CopySpec(src.GetRootLayer(), '/BarcodeScanner', stage.GetRootLayer(), '/Scanner/body')
    body = stage.GetPrimAtPath('/Scanner/body')
    body.RemoveAPI(UsdPhysics.RigidBodyAPI)
    body.RemoveAPI(UsdPhysics.MassAPI)
    for attr in list(body.GetAttributes()):
        if attr.GetName().startswith('physics:'):body.RemoveProperty(attr.GetName())
    stage.GetPrimAtPath('/Scanner/body/Looks/Plastic/Albedo').GetAttribute('inputs:file').Set(Sdf.AssetPath('albedo.png'))
    visual = UsdGeom.Mesh(stage.GetPrimAtPath('/Scanner/body/Visual'))
    vertices = np.array(visual.GetPointsAttr().Get())
    faces = np.array(visual.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)
    centers = vertices[faces].mean(1)
    selected = (centers[:,0]>0)&(centers[:,2]>.084)&(centers[:,2]<.116)&(abs(centers[:,1])<.009)
    assert selected.sum() == 2643, 'Unexpected source mesh; review trigger segmentation'
    UsdGeom.Xform.Define(stage, '/Scanner/trigger')
    Sdf.CopySpec(stage.GetRootLayer(), '/Scanner/body/Visual', stage.GetRootLayer(), '/Scanner/trigger/Visual')
    for path, selection, offset in [('/Scanner/body/Visual',~selected,np.zeros(3)),
                                    ('/Scanner/trigger/Visual',selected,ANCHOR)]:
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath(path))
        # Compact unused vertices without changing any rendered triangle or UV.
        chosen = faces[selection]
        used, inverse = np.unique(chosen, return_inverse=True)
        points = vertices[used]-offset
        mesh.GetPointsAttr().Set([Gf.Vec3f(*map(float,v)) for v in points])
        mesh.GetFaceVertexCountsAttr().Set([3]*int(selection.sum()))
        mesh.GetFaceVertexIndicesAttr().Set(inverse.reshape(-1).tolist())
        for primvar in UsdGeom.PrimvarsAPI(mesh).GetPrimvars():
            if primvar.GetInterpolation() == 'vertex':
                values = primvar.ComputeFlattened()
                primvar.Set([values[int(i)] for i in used])
                primvar.BlockIndices()
        normals = mesh.GetNormalsAttr().Get()
        if normals and mesh.GetNormalsInterpolation() == 'vertex':
            mesh.GetNormalsAttr().Set([normals[int(i)] for i in used])
        mesh.GetExtentAttr().Set([Gf.Vec3f(*map(float,points.min(0))),Gf.Vec3f(*map(float,points.max(0)))])
        mesh.CreateDoubleSidedAttr(True)
    # CopySpec keeps the material binding in the body namespace.
    stage.GetPrimAtPath('/Scanner/trigger/Visual').GetRelationship('material:binding').SetTargets(['/Scanner/body/Looks/Plastic'])
    # Retain supplied convex hulls, except cut away the moving trigger region.
    originals=[]
    for prim in stage.GetPrimAtPath('/Scanner/body/Colliders').GetChildren():
        mesh=UsdGeom.Mesh(prim)
        originals.append(trimesh.Trimesh(vertices=np.array(mesh.GetPointsAttr().Get()),
            faces=np.array(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1,3),process=False))
    stage.RemovePrim('/Scanner/body/Colliders')
    UsdGeom.Xform.Define(stage,'/Scanner/body/Colliders')
    pieces=[]
    for mesh in originals:
        # Widen the opening by 2 mm beyond the visual trigger's ends.
        for normal, origin in [([0,0,-1],[0,0,.081]),([0,0,1],[0,0,.119])]:
            part=clip(mesh,normal,origin)
            if part is not None:pieces.append(part)
        mid=clip(mesh,[0,0,1],[0,0,.081])
        if mid is not None:mid=clip(mid,[0,0,-1],[0,0,.119])
        if mid is not None:mid=clip(mid,[-1,0,0],[-.001,0,0])
        if mid is not None:pieces.append(mid)
    for i,mesh in enumerate(pieces):
        write_mesh(stage,f'/Scanner/body/Colliders/Hull_{i:02}',mesh.vertices,mesh.faces,True)
    trigger=trimesh.convex.convex_hull(vertices[np.unique(faces[selected])]-ANCHOR)
    write_mesh(stage,'/Scanner/trigger/Collider',trigger.vertices,trigger.faces,True)
    stage.GetRootLayer().Save()
    manifest=dict(source='User-supplied barcode_scanner.usdz',source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        units='meters',dimensions_m=(vertices.max(0)-vertices.min(0)).tolist(),
        trigger_anchor_m=ANCHOR.tolist(),source_faces=len(faces),body_faces=int((~selected).sum()),trigger_faces=int(selected.sum()),
        body_collision_hulls=len(pieces),mass_kg=.18,
        notes='Original visual geometry and texture retained at rest; segmented trigger, collision clearance and spring parameters are simulation adaptations.')
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
