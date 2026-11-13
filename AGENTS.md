# AgenticROS maintainer rules

The product is compose.yaml, docker/, scripts/ and tests/. Host prerequisites are Docker
and Python 3; no native ROS or pixi. Historical prototypes live only in Git history.

- Simulation only; never operate physical robots or push without instruction.
- Agent image contains public docs, raw IDL and tools, never simulator/evaluator source,
  hidden scene state, reference adapters or task programs. Codex generates its own code.
- Do not mount host home, source checkout, credentials or Docker socket into Agent.
- Preserve UUID/session checks, exclusive raw actions, cancellation and stop reconciliation.
  Never replay a timed-out goal or equate servo completion with grasp/task success.
- Keep independent evaluation outside Agent. Test image contents and actual mounts.
- Distinguish unit tests, Docker integration, autonomous adaptation and task evaluation.
- Preserve volumes, credentials, generated adapters and runs/. Do not restart a live body
  silently. Use scripts/test.sh for isolated unit tests; motion tests require an idle body.
