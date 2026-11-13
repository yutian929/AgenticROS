"""Small command interface for a coding agent, with structured stdout."""
import argparse
import json
from .client import Robot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["snapshot", "status", "reset"]:
        sub.add_parser(name)
    move = sub.add_parser("move")
    move.add_argument("xyz", nargs=3, type=float)
    move.add_argument("--speed", type=float, default=.12)
    grip = sub.add_parser("grip")
    grip.add_argument("width", type=float)
    locate = sub.add_parser("locate")
    locate.add_argument("snapshot_json")
    locate.add_argument("--color", choices=["red", "blue", "green"], default="red")
    locate.add_argument("--kind", choices=["block", "plate"], default="block")
    args = parser.parse_args()
    if args.command == "locate":
        from .perception import locate_color
        with open(args.snapshot_json) as f:
            result = locate_color(json.load(f), args.color, args.kind)
    else:
        with Robot() as robot:
            if args.command == "move":
                result = robot.move(args.xyz, args.speed)
            elif args.command == "grip":
                result = robot.grip(args.width)
            else:
                result = getattr(robot, args.command)()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
