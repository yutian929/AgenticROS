---
name: setup-body
description: Initialize and operate the ROSBody local MuJoCo simulation through its ROS 2 body interface. Use for ROSBody simulation tasks, observations and bounded robot actions.
---
Read the repository-root `setup_body.md` and follow its body contract. Start missing services,
check readiness, and use public ROS interfaces. Return images and measured action feedback to
the agent between visually dependent phases. Do not infer task success from motion completion.
Do not access evaluator-only truth during task trials. This skill authorizes simulation only.
