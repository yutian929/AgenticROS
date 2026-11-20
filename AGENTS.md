# AgenticROS maintainer rules

The product is compose.yaml, docker/, scripts/ and tests/. Host prerequisites are Docker
and Python 3; no native ROS or pixi. Historical prototypes live only in Git history.

- Simulation only; never operate physical robots or push without instruction.
- Agent image contains generic ROS tools and adaptation rules, no device-specific docs or IDL,
  never simulator/evaluator source,
  hidden scene state, reference adapters or task programs. Codex generates its own code.
- Do not mount host home, source checkout, credentials or Docker socket into Agent.
  Only the dedicated ignored runs/workspace (or a fresh trial workspace under runs/) may
  be bound to /workspace for generated code and evidence; never bind the repository root.
- Preserve UUID/session checks, exclusive raw actions, cancellation and stop reconciliation.
  Never replay a timed-out goal or equate servo completion with grasp/task success.
- Keep independent evaluation outside Agent. Test image contents and actual mounts.
- Distinguish unit tests, Docker integration, autonomous adaptation and task evaluation.
- Preserve volumes, credentials, generated adapters and runs/. Do not restart a live body
  silently. Use scripts/test.sh for isolated unit tests; motion tests require an idle body.

- One Agent Server and one runs/workspace; execution subagents share persistent generic ROS clients.
- Stable /robots/<robot_id> namespaces; session belongs in payloads, never endpoint names.
- One configured DDS domain/profile for every node. Keep fixed discovery unless measured tests justify a network change.
- HTTP presence owns identity leases only. Report process, discovery, RPC, freshness and control separately.
- Changes must retain rule hot-sync and run isolated separate-container communication tests; do not claim same-host tests prove cross-host LAN behavior.
