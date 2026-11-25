"""Native-unit stop evidence tests; standard library only, no robot process."""
import importlib.util
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aloha_control', ROOT/'docker/robot_instances/aloha_tabletop/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class StopEvidence(unittest.TestCase):
    def window(self):
        return control.MotionWindow(['arm'+str(i) for i in range(6)]+['left_finger', 'right_finger'])

    def test_requires_new_continuous_window(self):
        m = self.window()
        for i in range(125):
            m.observe(i*.002, [0.]*8)
        self.assertFalse(m.evidence()['settled'])
        m.observe(.25, [0.]*8)
        self.assertTrue(m.evidence()['settled'])
        for now in (.25, .2, .3):
            m.observe(now, [0.]*8)
            self.assertFalse(m.evidence()['settled'])
        m.reset()
        self.assertEqual(m.evidence()['samples'], 0)

    def test_oscillation_cannot_hide_behind_zero_endpoint_drift(self):
        m = self.window()
        for i in range(126):
            m.observe(i*.002, [0.]*7+[.0001*(i%2)])
        self.assertFalse(m.evidence()['settled'])
        self.assertLess(m.evidence()['max_excursion'], .0002)
        self.assertGreater(m.evidence()['mean_absolute_speeds'][-1], .002)

    def test_slider_uses_metres_per_second_and_angular_joint_uses_radians(self):
        angular, linear = self.window(), self.window()
        for i in range(126):
            t = i*.002
            angular.observe(t, [.006*t]+[0.]*7)
            linear.observe(t, [0.]*7+[.006*t])
        self.assertTrue(angular.evidence()['settled'])
        self.assertFalse(linear.evidence()['settled'])
        self.assertEqual(linear.evidence()['joint_units'], ['rad']*6+['m']*2)

    def test_nonfinite_or_missing_finger_never_confirms_stop(self):
        for now, q in [(math.nan, [0.]*8), (0., [0.]*7), (0., [0.]*7+[math.inf])]:
            m = self.window()
            with self.assertRaises(ValueError):
                m.observe(now, q)
            self.assertFalse(m.evidence()['settled'])

    def test_shared_action_field_layout_matches_quanta(self):
        docs = ROOT/'docker/robot_instances/robot_docs'
        def fields(path):
            return [l.split('#', 1)[0].strip() for l in path.read_text().splitlines()
                    if l.split('#', 1)[0].strip()]
        for action in ('Actuate', 'MovePose', 'Recover'):
            self.assertEqual(fields(docs/f'aloha_tabletop/interfaces/action/{action}.action'),
                             fields(docs/f'quanta_x1/interfaces/action/{action}.action'))


if __name__ == '__main__':
    unittest.main()
