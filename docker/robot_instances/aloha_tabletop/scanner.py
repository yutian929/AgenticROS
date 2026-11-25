"""Body-local passive trigger feedback. Never exports object pose or task truth."""
import math


class MechanicalSwitch:
    PRESS_M = .004
    RELEASE_M = .0015
    DEBOUNCE_S = .03

    def __init__(self):
        self.pressed = False
        self.press_since = None
        self.press_count = 0
        self.observed_at_sim = 0.

    def observe_switch(self, travel, now):
        if not math.isfinite(travel) or not math.isfinite(now):
            raise ValueError('Nonfinite switch feedback')
        self.observed_at_sim = now
        if travel <= self.RELEASE_M:
            self.pressed = False
            self.press_since = None
        elif not self.pressed:
            if travel < self.PRESS_M:
                self.press_since = None
            elif self.press_since is None:
                self.press_since = now
            elif now-self.press_since >= self.DEBOUNCE_S:
                self.pressed = True
                self.press_count += 1

    def status(self):
        return dict(trigger_pressed=self.pressed, press_count=self.press_count,
                    observed_at_sim=self.observed_at_sim)


class Scanner:
    def __init__(self, model):
        self.qid = int(model.joint('scanner_trigger_slide').qposadr[0])
        self.switch = MechanicalSwitch()

    def step(self, data):
        self.switch.observe_switch(float(data.qpos[self.qid]), float(data.time))

    def status(self):
        return self.switch.status()
