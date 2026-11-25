"""Continuous physical stop evidence with native angular/linear units."""
from collections import deque
import math


class MotionWindow:
    def __init__(self, names):
        self.names = list(names)
        self.units = ['rad']*6 + ['m']*2
        self.speed_limits = [.02]*6 + [.002]*2
        self.excursion_limits = [.002]*6 + [.0002]*2
        self.samples = deque()

    def reset(self):
        self.samples.clear()

    def observe(self, now, positions):
        q = tuple(map(float, positions))
        if len(q) != len(self.names) or not all(math.isfinite(x) for x in (now, *q)):
            self.reset()
            raise ValueError('nonfinite or incomplete physical feedback')
        if self.samples:
            previous = self.samples[-1][0]
            if now <= previous or now-previous > .05:
                self.reset()
        self.samples.append((float(now), q))
        while len(self.samples) > 1 and now-self.samples[1][0] >= .25:
            self.samples.popleft()

    def evidence(self):
        duration = self.samples[-1][0]-self.samples[0][0] if self.samples else 0.
        rates, spans = [], []
        if duration > 0:
            for axis in zip(*(q for _, q in self.samples)):
                rates.append(sum(abs(b-a) for a, b in zip(axis, axis[1:]))/duration)
                spans.append(max(axis)-min(axis))
        return dict(method='physics_step_position_variation', duration_s=duration,
                    samples=len(self.samples), joint_names=self.names, joint_units=self.units,
                    mean_absolute_speeds=rates, excursions=spans,
                    speed_limits=self.speed_limits, excursion_limits=self.excursion_limits,
                    max_mean_absolute_speed=max(rates, default=0.), max_excursion=max(spans, default=0.),
                    settled=duration >= .25-1e-9 and len(self.samples) >= 12 and bool(rates)
                            and all(x < limit for x, limit in zip(rates, self.speed_limits))
                            and all(x < limit for x, limit in zip(spans, self.excursion_limits)))
