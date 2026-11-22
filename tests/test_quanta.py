"""Raw action state-machine tests; no ROS/GPU/robot process."""
import importlib.util
from pathlib import Path
import time
import unittest
import tempfile
import json
from unittest.mock import patch
import gpu_test_guard

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('quanta_control', ROOT/'docker/robot_instances/quanta_x1/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class Actions(unittest.TestCase):
    def setUp(self):
        self.c = control.Control('session')
        self.q = {a: [0.]*7 for a in control.ARMS}
        self.v = {a: [0.]*7 for a in control.ARMS}
        self.t = 0.
        self.c.initialize(self.q, {a: [(-2.,2.)]*6+[(0.,1.89)] for a in control.ARMS})
        self.c.fingers = {a: [0.,0.] for a in control.ARMS}
        self.c.finger_velocities = {a: [0.,0.] for a in control.ARMS}

    def advance(self, seconds, move=None):
        for _ in range(round(seconds*240)):
            self.t += 1/240
            if move:move()
            self.c.tick(self.q,self.v,self.t)

    def start(self, arm='left', gid='abc'):
        self.assertTrue(self.c.reserve(arm,'session',[.1]*7,1.))
        self.c.accepted(arm,gid)
        self.advance(.01)

    def lock_arm(self):
        self.start()
        self.c.cancel('left','abc')
        def moving():self.q['left'][0]+=.001
        self.advance(2.2,moving)
        self.assertTrue(self.c.recovery['left'])

    def test_session_limits_and_exclusive_owner(self):
        self.assertFalse(self.c.reserve('left','old-session',[0.]*7,1.))
        self.assertFalse(self.c.reserve('left','session',[0.]*6+[5.7],1.))
        self.start()
        self.assertFalse(self.c.reserve('left','session',[0.]*7,1.))
        self.assertTrue(self.c.reserve('right','session',[0.]*7,1.))

    def test_blocked_target_returns_failure_but_settled_hold_allows_next_goal(self):
        self.start()
        self.advance(4.2)
        self.assertTrue(self.c.status('left')['busy'])
        self.advance(.2)
        receipt=self.c.receipts['left']['abc']
        self.assertFalse(receipt['complete'])
        self.assertTrue(receipt['stop_confirmed'])
        self.assertIsNone(receipt['task_success'])
        self.assertTrue(self.c.reserve('left','session',[.2]*7,1.))

    def test_cancel_requires_uuid_and_full_post_hold_window(self):
        self.start()
        self.c.cancel('left','wrong')
        self.advance(.2)
        self.assertFalse(self.c.active['left']['cancel'])
        self.c.cancel('left','abc')
        self.advance(.2)
        self.assertNotIn('abc',self.c.receipts['left'])
        self.assertEqual(self.c.targets['left'],self.q['left'])
        self.advance(.1)
        self.assertEqual(self.c.receipts['left']['abc']['reason'],'cancelled')
        self.assertTrue(self.c.receipts['left']['abc']['stop_confirmed'])

    def test_raw_solver_velocity_disagreement_requires_continuous_positions(self):
        self.start()
        self.q['left']=[.1]*7
        self.v['left']=[.2]*7
        self.c.finger_velocities['left']=[.2,-.2]
        self.advance(1.3)
        self.assertTrue(self.c.receipts['left']['abc']['complete'])
        self.assertGreater(self.c.status('left')['stop_evidence']['raw_max_speed'],.02)
        self.assertIsNone(self.c.receipts['left']['abc']['task_success'])

    def test_passive_finger_oscillation_does_not_hide_behind_zero_endpoint_drift(self):
        self.start()
        self.q['left']=[.1]*7
        def oscillate():self.c.fingers['left'][0]=.01 if round(self.t*240)%2 else -.01
        self.advance(1.3,oscillate)
        self.assertTrue(self.c.status('left')['busy'])
        self.assertFalse(self.c.status('left')['stop_evidence']['settled'])
        self.advance(.3)
        self.assertTrue(self.c.receipts['left']['abc']['stop_confirmed'])

    def test_recovery_checks_identity_and_new_feedback_preserves_failed_receipt(self):
        self.lock_arm()
        original=dict(self.c.receipts['left']['abc'])
        targets=list(self.c.targets['left'])
        self.assertFalse(self.c.reserve_recovery('left','old','abc'))
        self.assertFalse(self.c.reserve_recovery('left','session','wrong'))
        self.assertFalse(self.c.reserve('left','session',[0.]*7,1.))
        self.advance(.3)
        self.assertTrue(self.c.recovery['left']) # Never silently unlock.
        self.assertTrue(self.c.reserve_recovery('left','session','abc'))
        self.c.accepted('left','recover1')
        self.advance(.2)
        self.assertTrue(self.c.recovery['left'])
        self.assertFalse(self.c.reserve('left','session',[0.]*7,1.))
        self.advance(.1)
        self.assertTrue(self.c.status('left')['control_available'])
        self.assertEqual(self.c.targets['left'],targets)
        self.assertEqual(self.c.receipts['left']['abc'],original)
        self.assertEqual(self.c.receipts['left']['recover1']['reason'],'recovered')

    def test_recovery_cancellation_or_continued_motion_keeps_latch(self):
        self.lock_arm()
        self.assertTrue(self.c.reserve_recovery('left','session','abc'))
        self.c.accepted('left','recover1')
        self.c.cancel('left','recover1')
        self.advance(.1)
        self.assertTrue(self.c.recovery['left'])
        self.assertTrue(self.c.reserve_recovery('left','session','abc'))
        self.c.accepted('left','recover2')
        self.advance(2.2,lambda:self.q['left'].__setitem__(0,self.q['left'][0]+.001))
        self.assertFalse(self.c.receipts['left']['recover2']['stop_confirmed'])
        self.assertTrue(self.c.recovery['left'])

    def test_nonfinite_simulation_time_faults_instead_of_stalling_goal(self):
        self.start()
        self.c.tick(self.q,self.v,float('nan'))
        self.assertFalse(self.c.receipts['left']['abc']['stop_confirmed'])
        self.assertFalse(self.c.reserve_recovery('left','session','abc'))

    def test_feedback_gap_and_nonfinite_never_confirm_stop(self):
        self.advance(.3)
        self.assertTrue(self.c.status('left')['stop_confirmed'])
        self.t+=.1;self.c.tick(self.q,self.v,self.t)
        self.assertFalse(self.c.status('left')['stop_confirmed'])
        self.c.updated=time.time()-10
        self.assertFalse(self.c.status('left')['control_available'])
        self.assertFalse(self.c.reserve_recovery('left','session','abc'))
        self.c.updated=time.time();self.start()
        self.q['left'][0]=float('nan')
        self.advance(.01)
        self.assertFalse(self.c.receipts['left']['abc']['stop_confirmed'])
        self.assertFalse(self.c.status('left')['control_available'])


class RuntimeOverrides(unittest.TestCase):
    def test_optional_type_local_compose_not_id_prefix(self):
        spec = importlib.util.spec_from_file_location('quanta_runtime_test', ROOT/'scripts/runtime.py')
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, 'ROOT', Path(directory)):
            kind = Path(directory)/'docker/robot_instances/custom_type'
            kind.mkdir(parents=True)
            (kind/'Dockerfile').write_text('FROM scratch\n')
            config = dict(AGENTICROS_ENVIRONMENT='custom_type', AGENTICROS_ROBOT_ID='unrelated_id', ROS_DOMAIN_ID=91)
            self.assertEqual(runtime.compose(config, True)[-1], 'compose.robot.yaml')
            (kind/'compose.yaml').write_text('services: {}\n')
            self.assertEqual(runtime.compose(config, True)[-2:], ['-f', str(kind/'compose.yaml')])
            self.assertNotIn(str(kind/'compose.yaml'), runtime.compose(config, False))


class ScannerSwitch(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('scanner_device', ROOT/'docker/robot_instances/quanta_x1/scanner_device.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.c = module.MechanicalSwitch()

    def press(self, at=0.):
        self.c.observe_switch(.005, at)
        self.c.observe_switch(.005, at+.04)

    def test_press_hold_release_and_repress(self):
        self.assertEqual(self.c.status()['press_count'], 0)
        self.press()
        self.assertTrue(self.c.pressed)
        self.c.observe_switch(.005,1.)
        self.assertEqual(self.c.press_count,1)
        self.c.observe_switch(0.,1.1)
        self.assertFalse(self.c.pressed)
        self.press(1.2)
        self.assertEqual(self.c.press_count,2)
        self.assertNotIn('task_success',self.c.status())

    def test_switch_bounce_does_not_trigger(self):
        self.c.observe_switch(.005,0.)
        self.c.observe_switch(.003,.02)
        self.c.observe_switch(.005,.03)
        self.assertFalse(self.c.pressed)
        self.assertEqual(self.c.press_count,0)
        with self.assertRaises(ValueError): self.c.observe_switch(float('nan'),.04)


class GpuTestGuard(unittest.TestCase):
    def test_existing_gpu_container_is_not_stopped(self):
        container = dict(Name='/existing-body', HostConfig=dict(
            DeviceRequests=[dict(Capabilities=[['gpu']])]), Config={})
        with patch.object(gpu_test_guard.subprocess, 'check_output', side_effect=['id', json.dumps([container])]) as command:
            with self.assertRaisesRegex(RuntimeError, 'existing-body'):
                gpu_test_guard.require_idle_gpu()
        self.assertEqual([call.args[0][:2] for call in command.call_args_list],
                         [['docker', 'ps'], ['docker', 'inspect']])

    def test_insufficient_memory_is_rejected(self):
        with patch.object(gpu_test_guard.subprocess, 'check_output', return_value=''), patch.object(Path, 'read_text', return_value='MemAvailable: 1048576 kB\n'):
            with self.assertRaisesRegex(RuntimeError, 'available RAM'):
                gpu_test_guard.require_idle_gpu()

    def test_idle_with_sufficient_memory(self):
        with patch.object(gpu_test_guard.subprocess, 'check_output', return_value=''), patch.object(Path, 'read_text', return_value='MemAvailable: 20971520 kB\n'):
            gpu_test_guard.require_idle_gpu()


if __name__ == '__main__':
    unittest.main()
