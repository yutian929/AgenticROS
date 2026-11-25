"""Pinned public-reference geometry checks; no native packages or robot process."""
import importlib.util
import json
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('layout_export', ROOT/'tools/export_quanta_layout.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class ParallelLayout(unittest.TestCase):
    def test_profile_matches_public_quanta_kinematics_and_calibration(self):
        actual = json.loads((ROOT/'docker/robot_instances/aloha_tabletop/layout.json').read_text())
        self.assertEqual(actual, exporter.profile())
        self.assertAlmostEqual(actual['arm_spacing_m'], .532, places=6)
        mounts = actual['arms']
        self.assertEqual(mounts['left']['quaternion_wxyz'], mounts['right']['quaternion_wxyz'])
        left, right = mounts['left']['position_m'], mounts['right']['position_m']
        self.assertLess(left[0], right[0])
        self.assertAlmostEqual(left[1], right[1])
        self.assertAlmostEqual(left[2], right[2])
        self.assertAlmostEqual(math.dist(left, right), actual['arm_spacing_m'])
        self.assertEqual((actual['main_camera']['width'], actual['main_camera']['height']), (1280, 720))
        self.assertEqual(actual['main_camera']['k'][0], 720.3193)
        self.assertEqual(actual['main_camera']['d'], [0.]*5)


if __name__ == '__main__':
    unittest.main()
