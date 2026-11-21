# AgenticROS maintainer rules

The product is compose.agent.yaml, compose.robot.yaml, docker/, scripts/, tools/ and tests/. Host prerequisites are Docker
and Python 3; no native ROS or pixi. Historical prototypes live only in Git history.

- Simulation only; never operate physical robots or push without instruction.
- Public robot manuals and interface sources live in docker/robot_instances/robot_docs/<type>/, mounted read-only at /workspace/robot_docs. No robot implementation or evaluator may be placed there.
- Agent image contains generic ROS tools and adaptation rules, no device-specific docs or IDL,
  never simulator/evaluator source,
  hidden scene state, reference adapters or task programs. Codex generates its own code.
- Do not mount host home, source checkout, credentials or Docker socket into Agent.
  Only the dedicated ignored runs/<DOMAIN_ID>/agent_instance/agent/workspace (or a fresh trial workspace under runs/) may
  be bound to /workspace for generated code and evidence; the explicit robot_docs public directory is the only additional source-tree mount. Never bind the repository root.
- Preserve UUID/session checks, exclusive raw actions, cancellation and stop reconciliation.
  Never replay a timed-out goal or equate servo completion with grasp/task success.
- Keep independent evaluation outside Agent. Test image contents and actual mounts.
- Distinguish unit tests, Docker integration, autonomous adaptation and task evaluation.
- Preserve volumes, credentials, generated adapters and runs/. Do not restart a live body
  silently. Use tests/test.sh for isolated unit tests; motion tests require an idle body.

- Agent and Viewer have independent images/containers under docker/agent_instance/{agent,viewer};
  robot-only common code lives under docker/robot_instances/common; keep it small and optional.
- Discover robot types from their Dockerfiles; no hardcoded type list or ID-prefix inference. Each robot image owns its HEALTHCHECK.
- One Agent Server per domain and one runs/<DOMAIN_ID>/agent_instance/agent/workspace; execution subagents share persistent generic ROS clients.
- Stable /robot/<robot_id> namespaces; session belongs in payloads, never endpoint names.
- Use native DDS automatic discovery with one ROS_DOMAIN_ID for every node; no fixed discovery service.
- Robots join ROS directly; no HTTP registration or control leases. Report process, discovery, RPC, freshness and control separately.
- Changes must retain rule hot-sync and run isolated separate-container communication tests; do not claim same-host tests prove cross-host LAN behavior.
