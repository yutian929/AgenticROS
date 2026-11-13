# ROSBody

This repository is a simulation-only robot workspace. Read `setup_body.md` before operating it.

- Execute commands in the Pixi environment and source `scripts/ros_env.sh` for custom ROS interfaces.
- The body is a three-axis Cartesian gripper, not a six-axis industrial arm. It has no rotation command.
- Use the public ROS interfaces or `rosbody.client.Robot` for task execution.
- Inspect returned RGB images before image-dependent decisions. A printed image path is not visual inspection.
- Stop dependent actions on failed, unknown or unconfirmed-stop receipts. Never repeat a timed-out goal automatically.
- Simulator truth, `Simulation.evaluate()`, `runs/**/evaluator.json`, and scene object coordinates are evaluator-only during agent task trials. Do not use them to choose actions or declare success.
- This is an evaluation convention, not a security sandbox: a coding agent can technically read simulator files.
- Record generated task programs and observations under `runs/`. Do not put credentials in the repository.
- Prefer local Python helpers first; add a ROS service/node only for reusable capabilities.
- No hardware integration or hardware motion is authorized by this prototype.
