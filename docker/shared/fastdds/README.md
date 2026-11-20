# Fast DDS TCP startup/reconnect fix

All three images build the same Fast DDS **2.6.12** source, verified by SHA-256,
and install only its runtime library plus patch/license metadata. The upstream
ROS Humble ABI is retained; no device interfaces are added to the Agent image.
This is a local patch, not an upstream release or a claimed upstream backport.

A TCP listener can accept before its DDS logical input opens. In unpatched
2.6.12, `RETCODE_INVALID_PORT` leaves that port pending, starts scanning other
ports, and later sends never renegotiate the pending original port. A live TCP
socket can therefore coexist with a permanently missing ROS discovery graph.

The LAN profile opts into exact-port negotiation with `max_logical_port=0`.
In this mode the patch preserves pending ports across reconnects, clears old
negotiation transactions when reconnecting, and retries rejected port negotiation
on subsequent sends, at most once per 100 ms per port with one outstanding
transaction. It does not resend application requests or change action semantics.
Other transport configurations keep the upstream port scanning behavior.

`reconnect.cpp` deliberately opens the TCP listener 1.5 seconds before its logical
port and checks five successive reconnects. Every image build runs this test.
Unpatched 2.6.12 failed the first recovery with a 3-second bound; the diagnostic
patched build recovered all five at the first 50 ms polling interval. Native ROS
separate-container tests and final release-image results are recorded in
[the validation report](../../../docs/direct-ros-validation.md).

Upstream code is Apache-2.0:
https://github.com/eProsima/Fast-DDS/tree/v2.6.12
The upstream change https://github.com/eProsima/Fast-DDS/pull/4324 explains why
pending ports must survive a failed negotiation followed by disconnection.
This patch retains that behavior while also recovering on the existing socket.

Building requires access to Ubuntu package archives and the pinned GitHub source
archive. The build stage is cached across images. Debug logging, source trees,
and compiler output from the middleware build are not copied into runtime images.
