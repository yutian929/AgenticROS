# ROSBody Docker rewrite — maintainer instructions

The active product is compose.yaml plus docker/ and scripts/docker-chat.sh.
The old native prototype is preserved on codex/macos-minimal-sim and retained here for
regression reference. It must never be copied into the agent image or mounted into it.

- Simulation only; no physical robot integration or motion is authorized.
- Agent image: Codex, ROS, raw IDL, documentation, build tools; NO prebuilt body adapter.
- Body image: raw actuator driver, physics and sensors; no standard move/grip implementation.
- Agent must generate its adapter, perception and task programs during autonomous trials.
- Keep private scene/model/evaluator data out of the agent image and mounts.
- Do not mount host home, source checkout, credentials, Docker socket or privileged devices.
- Maintain bounded exclusive raw actions, UUID receipts, cancellation and stop reconciliation.
- Never automatically replay timed-out goals or infer task completion from servo completion.
- Inspect observations for image-dependent actions. Do not use hidden truth for task planning.
- Test container startup, ROS connectivity and mount isolation. Clearly distinguish native
  tests, Docker integration checks and autonomous agent trials in reports.
- Do not erase volumes, restart a user's live body, or change their generated adapter silently.
