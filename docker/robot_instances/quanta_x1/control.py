"""Thread-safe raw servo state machine. Only the simulation thread writes targets."""
from collections import deque
import math
import threading
import time

ARMS = ('left', 'right')
NAMES = {arm: [*(f'{arm}_arm_joint{i}' for i in range(1, 7)), f'{arm}_arm_gripper'] for arm in ARMS}


class MotionWindow:
    """Measure actual configuration motion at every physics step, including fingers.

    TGS contact correction can leave solver velocity inconsistent with the final
    integrated positions. Keep both diagnostics, but require a continuous window
    of low total variation (not merely matching window endpoints).
    """
    def __init__(self):
        self.samples = deque()

    def reset(self):
        self.samples.clear()

    def observe(self, now, positions):
        now = float(now)
        q = tuple(map(float, positions))
        if self.samples:
            previous, old = self.samples[-1]
            if now <= previous or now-previous > .05 or len(q) != len(old):
                self.reset()
        self.samples.append((now, q))
        while len(self.samples) > 1 and now-self.samples[1][0] >= .25:
            self.samples.popleft()

    def evidence(self):
        duration = self.samples[-1][0]-self.samples[0][0] if self.samples else 0.
        count = len(self.samples)
        rates = []
        spans = []
        if duration > 0:
            points = [q for _, q in self.samples]
            for axis in zip(*points):
                rates.append(sum(abs(b-a) for a,b in zip(axis,axis[1:])) / duration)
                spans.append(max(axis)-min(axis))
        return dict(method='physics_step_position_variation', duration_s=duration,
                    samples=count, max_mean_absolute_speed=max(rates,default=0.),
                    max_excursion=max(spans,default=0.),
                    settled=duration >= .25 and count >= 12 and bool(rates)
                            and max(rates) < .02 and max(spans) < .002)


class Control:
    def __init__(self, session):
        self.session = session
        self.lock = threading.RLock()
        self.positions = {arm: [0.] * 7 for arm in ARMS}
        self.velocities = {arm: [0.] * 7 for arm in ARMS}
        self.targets = {arm: [0.] * 7 for arm in ARMS}
        self.fingers = {arm: [] for arm in ARMS}
        self.finger_velocities = {arm: [] for arm in ARMS}
        self.limits = {}
        self.active = {arm: None for arm in ARMS}
        self.receipts = {arm: {} for arm in ARMS}
        self.recovery = {arm: False for arm in ARMS}
        self.motion = {a: MotionWindow() for a in ARMS}
        self.recovery_goal = {a: None for a in ARMS}
        self.updated = 0.
        self.sim_time = 0.
        self.fault = None
        self.ready = False
        self.body_state = {}

    def initialize(self, positions, limits):
        with self.lock:
            self.limits = limits
            self.positions = {a: list(positions[a]) for a in ARMS}
            self.targets = {a: list(positions[a]) for a in ARMS}
            self.ready = True
            self.updated = time.time()

    def available(self, arm):
        return bool(self.ready and not self.fault and time.time() - self.updated < 5
                    and not self.recovery[arm] and self.active[arm] is None)

    def reserve(self, arm, session, target, duration):
        with self.lock:
            if not self.available(arm) or session != self.session:
                return False
            if len(target) != 7 or not math.isfinite(duration) or not .1 <= duration <= 30:
                return False
            if not all(math.isfinite(x) and lo <= x <= hi for x, (lo, hi) in zip(target, self.limits[arm])):
                return False
            self.active[arm] = dict(gid=None, target=list(target), duration=duration,
                                    start=None, phase='reserved', cancel=False, operation='actuate')
            return True

    def reserve_recovery(self, arm, session, failed_goal_id):
        with self.lock:
            if (session != self.session or not self.ready or self.fault
                    or time.time()-self.updated >= 5 or self.active[arm] is not None
                    or not self.recovery[arm] or failed_goal_id != self.recovery_goal[arm]):
                return False
            self.active[arm] = dict(gid=None, target=list(self.targets[arm]), duration=2.,
                start=None, phase='reserved', cancel=False, operation='recover',
                failed_goal_id=failed_goal_id)
            return True

    def accepted(self, arm, gid):
        with self.lock:
            self.active[arm]['gid'] = gid
            self.active[arm]['phase'] = 'running'

    def cancel(self, arm, gid):
        with self.lock:
            goal = self.active[arm]
            if goal and goal['gid'] == gid:
                goal['cancel'] = True

    def _finish(self, arm, reason, stopped):
        goal = self.active[arm]
        self.receipts[arm][goal['gid']] = dict(
            goal_id=goal['gid'], session_id=self.session, arm=arm,
            complete=reason in ('reached','recovered'), reason=reason, stop_confirmed=stopped,
            operation=goal['operation'], failed_goal_id=goal.get('failed_goal_id'),
            stop_evidence=self.motion[arm].evidence(),
            executed='some' if goal['start'] is not None else 'none',
            joint_positions=list(self.positions[arm]), task_success=None)
        self.recovery[arm] = not stopped or (goal['operation']=='recover' and reason!='recovered')
        if not stopped and goal['operation']=='actuate':
            self.recovery_goal[arm] = goal['gid']
        if not self.recovery[arm]:
            self.recovery_goal[arm] = None
        self.active[arm] = None

    def fail(self, reason):
        with self.lock:
            self.fault = str(reason)
            for arm in ARMS:
                if self.active[arm] and self.active[arm]['gid']:
                    self._finish(arm, 'simulation_fault', False)
                self.recovery[arm] = True

    def tick(self, positions, velocities, sim_time):
        """Observe the completed physics step, then return the next step's targets."""
        with self.lock:
            if self.fault:
                return {a: list(v) for a, v in self.targets.items()}
            if not math.isfinite(sim_time) or not all(math.isfinite(x) for a in ARMS for x in [*positions[a], *velocities[a], *self.fingers[a], *self.finger_velocities[a]]):
                self.fail('nonfinite physics feedback')
                return {a: list(v) for a, v in self.targets.items()}
            self.positions = {a: list(v) for a, v in positions.items()}
            self.velocities = {a: list(v) for a, v in velocities.items()}
            self.updated, self.sim_time = time.time(), sim_time
            for arm in ARMS:
                self.motion[arm].observe(sim_time, [*positions[arm], *self.fingers[arm]])
                goal = self.active[arm]
                if not goal or goal['gid'] is None:
                    continue
                if goal['start'] is None:
                    goal.update(start=sim_time, origin=list(positions[arm]))
                    if goal['operation']=='recover':
                        self.motion[arm].reset()
                elapsed = sim_time - goal['start']
                stopped = self.motion[arm].evidence()['settled']
                if goal['operation']=='recover':
                    # Observe the existing hold; never reset pose or replay a goal.
                    if goal['cancel']:
                        self._finish(arm, 'cancelled', stopped)
                    elif stopped:
                        self._finish(arm, 'recovered', True)
                    elif elapsed > 2.:
                        self._finish(arm, 'recovery_not_settled', False)
                    continue
                if goal['phase'] == 'stopping':
                    # A hold command must have passed through at least one physics step.
                    if sim_time > goal['hold_at'] and stopped:
                        self._finish(arm, goal['reason'], True)
                    elif sim_time - goal['hold_at'] > 2.:
                        self._finish(arm, goal['reason'], False)
                    continue
                if goal['cancel'] or elapsed > goal['duration'] + 3.:
                    self.targets[arm] = list(positions[arm])
                    self.motion[arm].reset()
                    goal.update(phase='stopping', hold_at=sim_time,
                                reason='cancelled' if goal['cancel'] else 'target_tolerance_not_reached')
                    continue
                fraction = min(elapsed / goal['duration'], 1.)
                self.targets[arm] = [a + fraction * (b-a) for a, b in zip(goal['origin'], goal['target'])]
                if (elapsed > goal['duration'] and stopped
                        and max(abs(a-b) for a, b in zip(positions[arm], goal['target'])) < .03):
                    self._finish(arm, 'reached', True)
            return {a: list(v) for a, v in self.targets.items()}

    def status(self, arm):
        with self.lock:
            active = self.active[arm]
            fresh = time.time() - self.updated < 5
            return dict(session_id=self.session, arm=arm, reported_at_unix=self.updated,
                        simulation_ready=self.ready, feedback_fresh=fresh, fault=self.fault,
                        control_available=self.available(arm), busy=active is not None,
                        active_goal_id=active['gid'] if active else None,
                        recovery_required=self.recovery[arm], recovery_goal_id=self.recovery_goal[arm],
                        stop_evidence=dict(**self.motion[arm].evidence(),
                            raw_max_speed=max(map(abs, [*self.velocities[arm], *self.finger_velocities[arm]]))),
                        stop_confirmed=bool(fresh and self.ready and not self.fault and not active
                                            and not self.recovery[arm]
                                            and self.motion[arm].evidence()['settled']),
                        receipts=dict(self.receipts[arm]),
                        body=dict(self.body_state),
                        state=dict(joint_names=NAMES[arm], joint_positions=list(self.positions[arm]),
                                   joint_velocities=list(self.velocities[arm]), joint_limits=self.limits.get(arm),
                                   finger_joint_positions=list(self.fingers[arm]),
                                   finger_joint_velocities=list(self.finger_velocities[arm]),
                                   sim_time=self.sim_time))
