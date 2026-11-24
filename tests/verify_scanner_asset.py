"""CPU USD geometry/texture regression; run with robot image /opt/sim/bin/python.

Pass the scanner_asset directory, including the source USDZ, as argv[1].
This is asset validation, not physical or autonomous task evaluation.
"""
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
from pxr import Usd, UsdGeom, UsdShade

root=Path(sys.argv[1]);manifest=json.loads((root/'manifest.json').read_text())
assert hashlib.sha256((root/'source.usdz').read_bytes()).hexdigest()==manifest['source_sha256']
source=Usd.Stage.Open(str(root/'source.usdz'));stage=Usd.Stage.Open(str(root/'scanner.usdc'))
original=UsdGeom.Mesh(source.GetPrimAtPath('/BarcodeScanner/Visual'))
points=np.array(original.GetPointsAttr().Get());faces=np.array(original.GetFaceVertexIndicesAttr().Get()).reshape(-1,3)
uv=np.array(UsdGeom.PrimvarsAPI(original).GetPrimvar('st').ComputeFlattened())
centers=points[faces].mean(1)
selection=(centers[:,0]>0)&(centers[:,2]>.084)&(centers[:,2]<.116)&(abs(centers[:,1])<.009)
all_points=[]
for part,mask,offset in [('body',~selection,np.zeros(3)),('trigger',selection,np.array(manifest['trigger_anchor_m']))]:
    mesh=UsdGeom.Mesh(stage.GetPrimAtPath(f'/Scanner/{part}/Visual'))
    p=np.array(mesh.GetPointsAttr().Get());f=np.array(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1,3)
    np.testing.assert_allclose((p+offset)[f],points[faces[mask]],atol=1e-8,rtol=0)
    tex=np.array(UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st').ComputeFlattened())
    np.testing.assert_array_equal(tex[f],uv[faces[mask]])
    material,_=UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
    assert material and material.GetPath().pathString=='/Scanner/body/Looks/Plastic'
    all_points.append(p+offset)
all_points=np.concatenate(all_points)
np.testing.assert_allclose(np.ptp(all_points,axis=0),manifest['dimensions_m'],atol=1e-8,rtol=0)
assert UsdGeom.GetStageMetersPerUnit(stage)==1 and UsdGeom.GetStageUpAxis(stage)=='Z'
texture=stage.GetPrimAtPath('/Scanner/body/Looks/Plastic/Albedo').GetAttribute('inputs:file').Get()
assert Path(texture.resolvedPath).is_file()
collider=UsdGeom.Mesh(stage.GetPrimAtPath('/Scanner/trigger/Collider'))
assert np.max(np.ptp(np.array(collider.GetPointsAttr().Get()),axis=0))<.04
print('PASS: original dimensions, all 50000 textured faces, material resolution and trigger collider bounds')
