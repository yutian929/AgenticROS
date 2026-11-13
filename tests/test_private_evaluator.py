"""Run inside an ephemeral body container; no ROS nodes or shared-state writes."""
import unittest

import mujoco

from evaluator import evaluate
from world import Simulation


class PrivateEvaluator(unittest.TestCase):
    def test_placement_requires_release_and_rest(self):
        sim = Simulation()
        self.assertFalse(evaluate(sim)['success'])
        body = sim.model.body('red_block')
        adr = sim.model.jnt_qposadr[body.jntadr[0]]
        plate = sim.model.geom('red_plate')
        sim.data.qpos[adr:adr+3] = [*plate.pos[:2], plate.pos[2] + plate.size[1] + .02]
        # Test the evaluator predicate with a controlled state, not platform-specific settling.
        dof = sim.model.jnt_dofadr[body.jntadr[0]]
        sim.data.qvel[dof:dof+6] = 0
        mujoco.mj_forward(sim.model, sim.data)
        self.assertTrue(evaluate(sim)['success'])
        sim.data.qvel[dof] = .1
        self.assertFalse(evaluate(sim)['success'])


if __name__ == '__main__':
    unittest.main()
