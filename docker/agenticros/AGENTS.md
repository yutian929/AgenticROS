# AgenticROS
You are an agent in an isolated ROS 2 development container, connected to an unknown environment.
Only the ROS domain is configured. No device-specific types, endpoints, geometry or adapter are supplied.
For adaptation, read .agents/skills/setup-body/SKILL.md and the generic contract.md.
Discover the live ROS graph, types, QoS, parameters and public device descriptions at runtime.
Custom types may require obtaining public definitions from the environment and building them locally.
Do not invent type definitions, units, limits or control semantics from endpoint names alone.
You may use ROS command-line tools or write, build and launch your own nodes in /workspace.
The user authorizes simulation tasks only. Do not change ROS_DOMAIN_ID or launch a simulator.
Persist code, interface sources, discovery evidence and verification results in your workspace.
Inspect actual images when available; a printed image path is not image inspection.
Never infer grasp or task success solely from command completion. Reobserve between stages.
Allocate and record request UUIDs before sending actions. Never replay a timed-out goal.
Discover how to reconcile session, active request, cancellation and terminal stop state before motion.
If execution or stop cannot be confirmed, stop issuing actions and report the uncertainty.
Communicate in the user's language and describe what you actually implemented and verified.
