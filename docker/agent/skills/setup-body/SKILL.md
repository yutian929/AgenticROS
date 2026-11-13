---
name: setup-body
description: Discover the raw ROS device, implement and verify the ROSBody standard adapter.
---
Read /workspace/contract.md and /workspace/body.md. This is an implementation task.
1. Discover live ROS nodes, services, actions and message types. Check the driver session.
2. Inspect documented joints, dimensions, frames and limits. Do not assume a robot model.
   Inventory existing controllers and adapters; reuse suitable verified capabilities. Do not
   rewrite existing servo/physics functionality merely to demonstrate autonomy.
3. Implement a reusable Python adapter in /workspace/adapter/body.py with the contract's API.
   Use rclpy directly. Build and launch additional ROS nodes if useful. There is no supplied
   Cartesian adapter, grasp program, perception implementation, or task-coordinate list.
4. Validate arguments before any goal submission. Preserve request UUIDs and evidence.
5. Verify observation and state first. Inspect an image, then test small safe movements and
   empty gripper open/close. Compare measured results, record failures and recovery.
6. Save /workspace/adapter/manifest.json with driver session, supported DOFs, tested limits,
   adapter path, launch instructions if needed, verification evidence and unresolved limitations.
7. Report ready only after tests pass. After driver restart, retain code but invalidate previous
   physical state and observations; check the new session and reobserve.
For subsequent tasks, use your adapter and write your own stage programs. Do not redo setup
unnecessarily. A manifest is a record, not proof: check live driver availability and identity.
