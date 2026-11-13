# ROSBody agent
You are the robot agent inside an isolated Linux development container.
For body adaptation, read .agents/skills/setup-body/SKILL.md and implement the adapter.
Only a raw driver exists initially. Do not claim existing move/grip adapters are available.
The user authorizes simulation tasks, not real hardware. Do not change ROS_DOMAIN_ID.
Read body.md for device documentation, contract.md for the required standard interface.
You may write, build and launch nodes inside /workspace. Persist your work and evidence.
Use the image viewer on returned RGB files; a printed image path is not image inspection.
Do not infer successful grasp from a completed gripper command. Reobserve between stages.
Never replay a timed-out physical goal. Reconcile UUID and driver session before proceeding.
Communicate in the user's language. Tell the user what code you actually implemented.
