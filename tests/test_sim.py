import numpy as np
import pytest
from rosbody.sim import Simulation


def test_contact_grasp_transport_release():
    sim = Simulation()
    original = sim.data.body("red_block").xpos.copy()
    assert sim.move([-.2, -.12, .10])["complete"]
    assert sim.move([-.2, -.12, .025])["complete"]
    assert sim.grip(0)["complete"]
    assert sim.move([-.2, -.12, .16])["complete"]
    assert sim.data.body("red_block").xpos[2] > original[2] + .10
    assert .03 < sim.state()["gripper_width"] < .05
    assert sim.move([.2, .15, .16])["complete"]
    assert sim.move([.2, .15, .04])["complete"]
    assert sim.grip(.08)["complete"]
    assert sim.move([0, -.25, .28])["complete"]
    assert sim.evaluate()["success"]
    assert "red_block_xyz" not in sim.state()


@pytest.mark.parametrize("xyz,speed", [([2,0,.1],.1), ([0,0,float('nan')],.1), ([0,0,.1],0), ([0,0,.001],.1)])
def test_rejected_motion_does_not_step(xyz, speed):
    sim = Simulation()
    t = sim.data.time
    q = sim.data.qpos.copy()
    with pytest.raises(ValueError):
        sim.move(xyz, speed)
    assert sim.data.time == t
    np.testing.assert_array_equal(sim.data.qpos, q)


def test_cancel_stops_before_destination():
    sim = Simulation()
    count = 0
    def cancel():
        nonlocal count
        count += 1
        return count > 8
    result = sim.move([.35,.2,.1], cancel=cancel)
    assert not result["complete"]
    assert result["reason"] == "cancelled"
    assert result["executed"] == "some"
    assert result["stop_confirmed"]
    assert np.linalg.norm(np.array(result["state"]["tcp_xyz"])-[.35,.2,.1]) > .1


def test_empty_gripper_is_not_task_success():
    sim = Simulation()
    result = sim.grip(0)
    assert result["complete"]
    assert result["task_success"] is None
    assert not sim.evaluate()["success"]
