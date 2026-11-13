"""Sensor decoding and projection only; no simulator dependency or scene truth."""
import numpy as np


def decode_image(msg):
    channels, dtype = {
        'rgb8': (3, np.dtype('u1')), 'bgr8': (3, np.dtype('u1')),
        'mono8': (1, np.dtype('u1')), '32FC1': (1, np.dtype('>f4' if msg.is_bigendian else '<f4')),
        '16UC1': (1, np.dtype('>u2' if msg.is_bigendian else '<u2')),
    }[msg.encoding]
    row_bytes = msg.width * channels * dtype.itemsize
    if msg.height <= 0 or msg.width <= 0 or msg.step < row_bytes or len(msg.data) != msg.height * msg.step:
        raise ValueError('Invalid image dimensions or stride')
    rows = np.frombuffer(bytes(msg.data), dtype=np.uint8).reshape(msg.height, msg.step)
    pixels = rows[:, :row_bytes].copy().view(dtype).reshape(msg.height, msg.width, channels)
    if channels == 1:
        return pixels[:, :, 0]
    return pixels[:, :, ::-1].copy() if msg.encoding == 'bgr8' else pixels


def project_depth(depth, rgb, k, stride=5):
    if depth.shape != rgb.shape[:2] or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('RGB and registered depth dimensions differ')
    k = np.asarray(k).reshape(3, 3)
    if not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0:
        raise ValueError('Camera is not calibrated')
    v, u = np.mgrid[0:depth.shape[0]:stride, 0:depth.shape[1]:stride]
    z = depth[::stride, ::stride]
    mask = np.isfinite(z) & (z > .01) & (z < 3.)
    points = np.stack(((u-k[0, 2])*z/k[0, 0], (v-k[1, 2])*z/k[1, 1], z), axis=-1)
    return points[mask].astype(np.float32), rgb[::stride, ::stride][mask]
