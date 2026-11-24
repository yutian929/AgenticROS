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
spec = importlib.util.spec_from_file_location('quanta_cartesian', ROOT/'docker/robot_instances/quanta_x1/cartesian.py')
cartesian = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cartesian)
KINEMATICS = ROOT/'docker/robot_instances/robot_docs/quanta_x1/kinematics.json'


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


class CartesianGeometry(unittest.TestCase):
    def setUp(self):
        self.k = cartesian.Kinematics(KINEMATICS)
        self.q = [.2,.9,-1.1,.2,.2,.1,.6]

    def test_zero_configuration_and_quaternion_round_trips(self):
        import math
        for arm in control.ARMS:
            pose = self.k.describe(arm, [0.]*7)
            for key, expected in zip('xyz', [.3355000148,0.,.1710000055]):
                self.assertAlmostEqual(pose['position'][key], expected, places=8)
        for vector in ([0.,0.,0.], [math.pi,0.,0.], [0.,math.pi,0.], [0.,0.,math.pi]):
            matrix = cartesian.rotation(vector)
            reconstructed = cartesian.transform([0.,0.,0.], cartesian.quaternion(matrix))
            self.assertLess(cartesian.pose_errors(matrix,reconstructed)[1], 1e-8)

    def test_geometric_jacobian_matches_finite_differences(self):
        actual, jac = self.k.forward('left', self.q, jacobian=True)
        step = 1e-6
        for j in range(6):
            q = list(self.q);q[j] += step
            moved = self.k.forward('left',q)
            angular = cartesian.rotation_vector(cartesian.multiply(moved,cartesian.inverse(actual)))
            measured = [(moved[i][3]-actual[i][3])/step for i in range(3)]+[.15*x/step for x in angular]
            for i in range(6):
                self.assertAlmostEqual(jac[i][j], measured[i], places=5)

    def test_tool_translation_uses_rotated_axes_and_rotation_keeps_tcp_origin(self):
        import math
        start = self.k.forward('left',self.q)
        target = self.k.target('left',self.q,[.01,0.,0.],[0.,0.,0.,1.],'tcp')
        for i in range(3):
            self.assertAlmostEqual(target[i][3], start[i][3]+.01*start[i][0])
        rotated = self.k.target('left',self.q,[0.,0.,0.],[0.,0.,math.sin(.1),math.cos(.1)],'tcp')
        self.assertLess(cartesian.pose_errors(start,rotated)[0], 1e-12)
        self.assertAlmostEqual(cartesian.pose_errors(start,rotated)[1], .2)
        absolute = self.k.target('left',self.q,[.3,.1,.2],[0.,0.,0.,1.],'arm_base')
        self.assertEqual([absolute[i][3] for i in range(3)],[.3,.1,.2])

    def test_preflight_reaches_target_with_limits_speed_and_grip_preserved(self):
        for arm in control.ARMS:
            target = self.k.target(arm,self.q,[.005,0.,0.],[0.,0.,0.,1.],'tcp')
            path = self.k.plan(arm,self.q,target,1.,self.k.bounds[arm],lambda:None)
            distance, angle = self.k.errors(arm,path[-1],target)
            self.assertLess(distance,.0002);self.assertLess(angle,.002)
            for old, new in zip(path,path[1:]):
                self.assertLessEqual(max(abs(a-b) for a,b in zip(old[:6],new[:6]))*(len(path)-1),1.5)
                self.assertEqual(new[6],self.q[6])
                self.assertTrue(all(lo <= q <= hi for q,(lo,hi) in zip(new[:6],self.k.bounds[arm])))
        unreachable = cartesian.transform([10.,0.,0.],[0.,0.,0.,1.])
        with self.assertRaisesRegex(ValueError,'ik_failed'):
            self.k.inverse('left',unreachable,self.q,self.k.bounds['left'],lambda:None)


class CartesianActions(unittest.TestCase):
    def setUp(self):
        self.k = cartesian.Kinematics(KINEMATICS)
        self.c = control.Control('session', self.k)
        self.c.namespace = '/robot/unit'
        self.q = {a:[.2,.9,-1.1,.2,.2,.1,.6] for a in control.ARMS}
        self.v = {a:[0.]*7 for a in control.ARMS}
        self.c.initialize(self.q,{a:[*self.k.bounds[a],(0.,1.89)] for a in control.ARMS})
        self.t = 0.
        self.advance(.3)

    def advance(self, duration, follow=False, moving=False):
        for _ in range(round(duration*240)):
            self.t += 1/240
            if moving:self.q['left'][0] += .001
            targets = self.c.tick(self.q,self.v,self.t)
            if follow:
                for arm in control.ARMS:self.q[arm][:6] = targets[arm][:6]

    def start(self, duration=1., plan=True):
        self.assertTrue(self.c.reserve_pose('left','session','tcp',[.005,0.,0.],[0.,0.,0.,1.],duration))
        self.c.accepted('left','pose1')
        if plan:self.assertTrue(self.c.plan_pose('left','pose1'))

    def test_input_identity_frames_and_exclusive_slot(self):
        for session, frame, position, rotation, duration in [
            ('old','tcp',[0.]*3,[0.,0.,0.,1.],1.),
            ('session','robot/unit/right/arm_base',[0.]*3,[0.,0.,0.,1.],1.),
            ('session','world',[0.]*3,[0.,0.,0.,1.],1.),
            ('session','tcp',[0.]*3,[0.]*4,1.),
            ('session','tcp',[float('nan'),0.,0.],[0.,0.,0.,1.],1.),
            ('session','arm_base',[10.,0.,0.],[0.,0.,0.,1.],1.),
            ('session','tcp',[0.]*3,[0.,0.,0.,1.],0.)]:
            self.assertFalse(self.c.reserve_pose('left',session,frame,position,rotation,duration))
        self.start()
        self.assertFalse(self.c.reserve('left','session',self.q['left'],1.))
        self.assertFalse(self.c.reserve_recovery('left','session','pose1'))
        self.assertFalse(self.c.reserve_pose('left','session','tcp',[0.]*3,[0.,0.,0.,1.],1.))
        self.assertTrue(self.c.reserve_pose('right','session','robot/unit/right/tcp',[0.]*3,[0.,0.,0.,1.],1.))

    def test_planning_failure_sends_no_motion_and_waits_for_post_hold_stop(self):
        self.start(plan=False)
        original = {a:list(q) for a,q in self.c.targets.items()}
        with patch.object(self.k,'plan',side_effect=ValueError('ik_failed')):
            self.assertFalse(self.c.plan_pose('left','pose1'))
        self.assertEqual(self.c.targets,original)
        self.advance(.2)
        self.assertNotIn('pose1',self.c.receipts['left'])
        self.advance(.1)
        receipt = self.c.receipts['left']['pose1']
        self.assertEqual(receipt['reason'],'planning_failed')
        self.assertEqual(receipt['executed'],'none')
        self.assertTrue(receipt['stop_confirmed'])

    def test_cancel_during_planning_never_installs_or_replays_path(self):
        self.start(plan=False)
        self.assertFalse(self.c.plan_pose('left','pose1',lambda:'cancelled'))
        self.advance(.3)
        receipt = self.c.receipts['left']['pose1']
        self.assertEqual(receipt['reason'],'cancelled')
        self.assertEqual(receipt['executed'],'none')
        self.assertTrue(receipt['stop_confirmed'])

    def test_pose_completion_preserves_loaded_gripper_command(self):
        self.q['left'][6] = .4
        self.advance(.3)
        self.start()
        self.advance(1.5,follow=True)
        receipt = self.c.receipts['left']['pose1']
        self.assertTrue(receipt['complete'] and receipt['stop_confirmed'])
        self.assertLess(receipt['position_error'],.003)
        self.assertLess(receipt['orientation_error'],.03)
        self.assertEqual(self.c.targets['left'][6],.6)
        self.assertEqual(receipt['gripper_target'],.6)
        self.assertIsNone(receipt['task_success'])

    def test_joint_tolerance_alone_does_not_complete_cartesian_goal(self):
        target_q = list(self.q['left']);target_q[0] += .02
        pose = self.k.describe('left',target_q)
        self.assertTrue(self.c.reserve_pose('left','session','arm_base',list(pose['position'].values()),
            list(pose['orientation'].values()),1.))
        self.c.accepted('left','pose1')
        self.assertTrue(self.c.plan_pose('left','pose1'))
        self.assertLess(max(abs(a-b) for a,b in zip(self.c.active['left']['target'],self.q['left'])),.03)
        self.advance(4.4)
        receipt = self.c.receipts['left']['pose1']
        self.assertFalse(receipt['complete'])
        self.assertGreater(receipt['position_error'],.003)
        self.assertEqual(receipt['reason'],'pose_tolerance_not_reached')
        self.assertTrue(receipt['stop_confirmed'])

    def test_cancelled_pose_with_unconfirmed_stop_requires_explicit_recovery(self):
        self.start(duration=3.)
        self.advance(.1,follow=True)
        self.c.cancel('left','wrong')
        self.assertFalse(self.c.active['left']['cancel'])
        self.c.cancel('left','pose1')
        self.advance(2.2,moving=True)
        receipt = dict(self.c.receipts['left']['pose1'])
        self.assertFalse(receipt['stop_confirmed'])
        self.assertEqual(self.c.recovery_goal['left'],'pose1')
        self.advance(.3)
        self.assertFalse(self.c.reserve_pose('left','session','tcp',[0.]*3,[0.,0.,0.,1.],1.))
        self.assertTrue(self.c.reserve_recovery('left','session','pose1'))
        self.c.accepted('left','recovery1');self.advance(.3)
        self.assertTrue(self.c.status('left')['control_available'])
        self.assertEqual(self.c.receipts['left']['pose1'],receipt)

    def test_clock_rollback_faults_without_replaying_or_extrapolating_path(self):
        self.start(duration=3.)
        self.advance(.1,follow=True)
        targets = {arm:list(q) for arm,q in self.c.targets.items()}
        self.assertEqual(self.c.tick(self.q,self.v,self.t-.2),targets)
        self.assertIn('backwards',self.c.fault)
        self.assertFalse(self.c.receipts['left']['pose1']['stop_confirmed'])
        self.assertEqual(self.c.recovery_goal['left'],'pose1')
        self.assertFalse(self.c.reserve_pose('left','session','tcp',[0.]*3,[0.,0.,0.,1.],1.))


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
