"""Minimal RGB-D perception; no simulator object IDs or ground-truth positions."""
import numpy as np
from PIL import Image


def locate_color(snapshot, color="red", kind="block"):
    from scipy.ndimage import label
    rgb = np.asarray(Image.open(snapshot["rgb_path"])).astype(float) / 255
    idx = {"red": 0, "green": 1, "blue": 2}[color]
    others = [i for i in range(3) if i != idx]
    mask = (rgb[:,:,idx] > 0.35) & (rgb[:,:,idx] > rgb[:,:,others].max(axis=2) * 1.6)
    components, n = label(mask)
    candidates = []
    depth = np.load(snapshot["depth_path"])
    cal = snapshot["calibration"]
    k = np.array(cal["K"])
    rot = np.array(cal["optical_to_base_rotation"])
    trans = np.array(cal["optical_to_base_translation"])
    for i in range(1, n+1):
        rows, cols = np.where(components == i)
        if len(rows) < 40:
            continue
        # Median visible top surface; depth and RGB belong to the same snapshot.
        u, v = float(np.median(cols)), float(np.median(rows))
        z = float(np.median(depth[rows, cols]))
        p = rot @ np.array([(u-k[0,2])*z/k[0,0], (v-k[1,2])*z/k[1,1], z]) + trans
        candidates.append({"pixel_count": len(rows), "pixel_uv": [u,v], "surface_xyz": p.tolist()})
    candidates.sort(key=lambda x: x["pixel_count"])
    if len(candidates) != 2:
        raise ValueError(f"Expected two separate visible {color} regions (block/plate), found {len(candidates)}; inspect a fresh image")
    result = dict(candidates[0 if kind == "block" else 1])
    result.update(snapshot_id=snapshot["snapshot_id"], method="RGB color components + metric depth; small=block, large=plate")
    return result
