"""Private evaluator regression, never included in the agent image."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'docker/body'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evaluator_requires_placement_release_and_rest():
    sim = load('world').Simulation()
    evaluate = load('evaluator').evaluate
    assert not evaluate(sim)['success']
    body = sim.model.body('red_block')
    adr = sim.model.jnt_qposadr[body.jntadr[0]]
    # This is evaluator unit-test truth, not an agent task program.
    plate = sim.model.geom('red_plate')
    sim.data.qpos[adr:adr+3] = [*plate.pos[:2], plate.pos[2] + plate.size[1] + .02]
    sim.step(500)
    assert evaluate(sim)['success']
    dof = sim.model.jnt_dofadr[body.jntadr[0]]
    sim.data.qvel[dof] = .1
    assert not evaluate(sim)['success']
