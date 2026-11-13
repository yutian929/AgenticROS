# ROSBody standard body contract v0.2
Implement `/workspace/adapter/body.py` exporting context-manager class `Body`.
This file defines the API; it does not supply its implementation.

* `status()` -> measured state, driver session ID, available capabilities, busy/recovery status.
* `observe()` -> immutable RGB/depth paths, camera calibration and synchronized measured state.
* `move(xyz, speed=0.12)` -> move grasp center in base coordinates, metres, metres/second.
* `grip(width)` -> set inner finger separation in metres.
* `close()` and context-manager cleanup -> release your client resources without resetting body.

Capability metadata must explicitly say whether orientation is supported. This first task
requires position only. Refuse unsupported DOFs and invalid/unreachable/nonfinite inputs
before sending a goal. A workspace bound is not collision avoidance: transport needs clearance.

Action returns must distinguish complete, executed ("none", "some", "unknown"),
stop_confirmed, measured state, driver session, request UUID, reason and position error where
applicable. Gripper contact can prevent requested closure without implying a failed servo;
report actual opening. Never claim grasp/task success from a movement receipt alone.

Allocate UUID BEFORE submission, record it in evidence, then send exactly once with the current session_id. An acceptance
or result timeout leaves execution UNKNOWN. Query /driver/status to reconcile the UUID; do
not automatically resend it. Cancellation must be confirmed by a terminal receipt. Stop
subsequent actions when stop is unconfirmed. Rejected requests mean no execution. A server
restart invalidates receipt lookup from the old session, not proof of non-execution.

Persist programs, observations and receipts under /workspace/evidence. Snapshot paths are
readable under /observations. Implement perception from RGB-D if a task requires it; there
are no hidden scene coordinates or object IDs available to the agent.
