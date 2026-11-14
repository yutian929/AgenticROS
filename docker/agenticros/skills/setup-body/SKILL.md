---
name: setup-body
description: Discover an unknown ROS 2 environment and implement and verify your own adapter.
---
Read /workspace/contract.md. No device-specific interface package or manual is preinstalled.
1. Inventory live nodes, topics, services, actions, types and QoS using ROS 2 tools.
2. Inspect parameters and publicly exposed documentation to establish units, limits, geometry,
   control semantics and safety requirements. Do not assume endpoint names or robot structure.
3. Standard types can be used directly. For unknown custom types, locate public definitions
   exposed by the environment, inspect them and build the required interface package yourself.
   ROS graph discovery reports type names; Humble does not automatically install their bindings.
   If definitions or necessary semantics are unavailable, report the specific blocker; do not guess.
4. Implement reusable code in /workspace/adapter/body.py. You may use CLI tools or your own nodes.
   Save any interface source, build and launch instructions. No adapter or task program is supplied.
5. Establish live identity, measured state and observations before motion. Validate inputs and
   request IDs; implement timeout, cancellation and stop reconciliation before testing motion.
   Inspect actual images where available, then verify small safe movements supported by the device.
6. Save /workspace/adapter/manifest.json with discovered capabilities, identity, interface origin,
   code paths, invocation instructions, test evidence and unresolved limitations.
7. Report ready only for verified capabilities. Recheck identity and reobserve after a restart.
Reuse verified generated code for subsequent tasks. Do not treat a manifest as proof of live state.
