"""Sensor invariants: byte order, padded rows, metric projection, invalid depth."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'docker/visualizer') if '__file__' in globals() and __file__ != '<stdin>' else '/opt/visualizer')
from sensors import decode_image, project_depth


class SensorProjection(unittest.TestCase):
    def test_padded_color_rows(self):
        msg = SimpleNamespace(width=1, height=2, encoding='bgr8', is_bigendian=0,
                              step=4, data=bytes([10, 20, 30, 99, 40, 50, 60, 99]))
        np.testing.assert_array_equal(decode_image(msg), [[[30, 20, 10]], [[60, 50, 40]]])

    def test_big_endian_depth(self):
        msg = SimpleNamespace(width=2, height=1, encoding='32FC1', is_bigendian=1,
                              step=8, data=np.array([.5, 1.25], dtype='>f4').tobytes())
        np.testing.assert_allclose(decode_image(msg), [[.5, 1.25]])
        msg.step = 7
        with self.assertRaises(ValueError):
            decode_image(msg)

    def test_projection_uses_intrinsics_and_filters_invalid_depth(self):
        depth = np.array([[1., np.nan], [0., 2.]], dtype=np.float32)
        rgb = np.array([[[255, 0, 0], [0, 0, 0]], [[0, 0, 0], [0, 255, 0]]], dtype=np.uint8)
        points, colors = project_depth(depth, rgb, [2., 0., 0., 0., 4., 0., 0., 0., 1.], stride=1)
        np.testing.assert_allclose(points, [[0., 0., 1.], [1., .5, 2.]])
        np.testing.assert_array_equal(colors, [[255, 0, 0], [0, 255, 0]])

    def test_mismatched_streams_and_uncalibrated_camera_rejected(self):
        with self.assertRaises(ValueError):
            project_depth(np.ones((2, 2)), np.zeros((1, 2, 3)), np.eye(3))
        with self.assertRaises(ValueError):
            project_depth(np.ones((2, 2)), np.zeros((2, 2, 3)), np.zeros((3, 3)))


if __name__ == '__main__':
    unittest.main()
