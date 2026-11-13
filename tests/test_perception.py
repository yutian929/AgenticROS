import numpy as np
import pytest
from rosbody.sim import Simulation
from rosbody.perception import locate_color


@pytest.mark.parametrize("seed", [0, 3, 11])
def test_snapshot_localization_matches_visible_surface(tmp_path, seed):
    sim = Simulation(seed)
    snapshot = sim.snapshot(tmp_path)
    measured = locate_color(snapshot, "red", "block")
    truth = sim.data.body("red_block").xpos
    assert np.linalg.norm(np.array(measured["surface_xyz"][:2])-truth[:2]) < .004
    assert abs(measured["surface_xyz"][2] - truth[2] - .02) < .003
    assert measured["snapshot_id"] == snapshot["snapshot_id"]
    assert snapshot["state"]["sim_time"] == sim.data.time
    # Existing evidence remains immutable after motion.
    old = np.load(snapshot["depth_path"]).copy()
    sim.move([.2,0,.2])
    np.testing.assert_array_equal(old, np.load(snapshot["depth_path"]))
