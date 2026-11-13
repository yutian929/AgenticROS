"""Deterministic RGB-D integration check; not an autonomous model evaluation.

Run from repository root, in the built ROS environment:
  python examples/pick_place.py
"""
import json
from pathlib import Path
from rosbody.client import Robot
from rosbody.perception import locate_color


def main():
    with Robot() as robot:
        robot.reset()
        before = robot.snapshot()
        block = locate_color(before, "red", "block")["surface_xyz"]
        plate = locate_color(before, "red", "plate")["surface_xyz"]
        steps = []
        def record(receipt):
            steps.append(receipt)
            print(json.dumps(receipt), flush=True)
        record(robot.grip(.08))
        record(robot.move([*block[:2], .14]))
        record(robot.move([*block[:2], max(.025, block[2]-.015)]))
        record(robot.grip(0))
        record(robot.move([*block[:2], .16]))
        lifted = robot.snapshot()
        # This diagnostic example checks RGB-D elevation, not simulator truth.
        lift_surface = locate_color(lifted, "red", "block")["surface_xyz"]
        if lift_surface[2] < .10:
            raise RuntimeError("RGB-D does not confirm lift; stop for a new observation/plan")
        record(robot.move([*plate[:2], .16]))
        record(robot.move([*plate[:2], plate[2]+.03]))
        record(robot.grip(.08))
        record(robot.move([*plate[:2], .22]))
        record(robot.move([0, -.25, .28]))
        after = robot.snapshot()
        output = Path("runs/client-demo.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"before": before, "lifted": lifted, "after": after,
                                      "actions": steps, "task_success": None}, indent=2))
        print(f"Inspect final RGB: {after['rgb_path']}")
        print(f"Client record: {output.resolve()}; task success is evaluated separately")


if __name__ == "__main__":
    main()
