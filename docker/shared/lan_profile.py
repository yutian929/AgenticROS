"""Generic Fast DDS LAN profiles. No device interfaces or adaptation code."""
import ipaddress
import re
from pathlib import Path


def profile(host, port, prefix, server=False, transport='udp', advertised_host=None):
    host = str(ipaddress.IPv4Address(host))
    port = int(port)
    if not 1024 <= port <= 65535 or not re.fullmatch(r'(?:[0-9a-f]{2}\.){11}[0-9a-f]{2}', prefix):
        raise ValueError('invalid DDS port or GUID prefix')
    if transport not in ('udp', 'tcp'):
        raise ValueError('DDS transport must be udp or tcp')
    if transport == 'tcp' and port > 32767:
        raise ValueError('TCP discovery port must be 1024..32767 for reliable Fast DDS 2.6 reconnection')
    advertised_host = str(ipaddress.IPv4Address(advertised_host or host))
    transport_id = 'lan_' + transport
    descriptor = '<type>UDPv4</type>'
    locator = f'<locator><udpv4><address>{host}</address><port>{port}</port></udpv4></locator>'
    listen_locator = locator
    extra_descriptor = extra_transport = ''
    if transport == 'tcp':
        # Dynamic listeners let multiple ROS processes reuse this profile. Fast
        # DDS stamps wan_addr onto its generated discovery/data locators, so
        # remote peers use the host address rather than Docker's private VM IP.
        # The pinned middleware patch retries exact logical ports after a
        # listener/endpoint startup race; do not scan unrelated logical ports.
        descriptor = ('<type>TCPv4</type><listening_ports>'
                      f'<port>{port if server else 0}</port></listening_ports>'
                      f'<wan_addr>{advertised_host}</wan_addr>'
                      '<enable_tcp_nodelay>true</enable_tcp_nodelay><max_logical_port>0</max_logical_port>')
        locator = (f'<locator><tcpv4><address>{host}</address>'
                   f'<physical_port>{port}</physical_port><port>7410</port></tcpv4></locator>')
        listen_locator = locator
        # Same-host DDS data uses SHM (Compose shares the host IPC namespace).
        # Do not publish a loopback user-data locator to remote machines.
        extra_descriptor = ('<transport_descriptor><transport_id>local_shm</transport_id>'
                            '<type>SHM</type><segment_size>33554432</segment_size></transport_descriptor>')
        extra_transport = '<transport_id>local_shm</transport_id>'
    # Fast DDS stamps wan_addr on advertised locators. Retain both the plain
    # fixed endpoint and its WAN alias as initial peers: CloseOutputChannel
    # otherwise removes a send resource when the fixed server disappears.
    # A loopback metatraffic locator would add a third, host-dependent alias.
    peers = locator + locator.replace('</address>', '</address><wan_address>'+host+'</wan_address>') if transport == 'tcp' else ''
    # Fast DDS normalizes a peer on this host to loopback. Protect that send
    # resource too, otherwise participant removal invalidates the old node's
    # discovery sender while established robot data channels keep working.
    # This is an initial peer only, never an advertised remote locator.
    if transport == 'tcp' and not server and advertised_host == host:
        peers += locator.replace(f'<address>{host}</address>', '<address>127.0.0.1</address>')
    # Humble waits only 100 ms for a new service client's response reader by
    # default. TCP discovery can take longer; wait for the match rather than
    # dropping an already-executed request's reply. This never resends a goal.
    service_qos = (
        '<publisher profile_name="service"><qos><reliability>'
        '<kind>RELIABLE</kind>'
        '<max_blocking_time><sec>3</sec><nanosec>0</nanosec></max_blocking_time>'
        '</reliability></qos></publisher>' if transport == 'tcp' else '')
    # Discovery is reconstructible ROS graph state. BACKUP's synchronous
    # persistence causes cold-discovery stalls in our multi-robot benchmark.
    # Keep a fixed discovery service without persisting reconstructible state.
    server_protocol = 'SERVER'
    discovery = f'<discoveryProtocol>{server_protocol}</discoveryProtocol>' if server else (
        '<discoveryProtocol>SUPER_CLIENT</discoveryProtocol><discoveryServersList>'
        f'<RemoteServer prefix="{prefix}"><metatrafficUnicastLocatorList>{locator}'
        '</metatrafficUnicastLocatorList></RemoteServer></discoveryServersList>')
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">'
            f'<transport_descriptors><transport_descriptor><transport_id>{transport_id}</transport_id>'
            + descriptor + '</transport_descriptor>' + extra_descriptor + '</transport_descriptors>'
            '<participant profile_name="lan" is_default_profile="true"><rtps>'
            + (f'<prefix>{prefix}</prefix>' if server else '') + '<builtin>'
            + (f'<metatrafficUnicastLocatorList>{listen_locator}</metatrafficUnicastLocatorList>' if server else '')
            + (f'<initialPeersList>{peers}</initialPeersList>' if not server and transport == 'tcp' else '')
            + f'<discovery_config>{discovery}'
            # Default 450 ms discovery sync nearly exhausts ros2cli's 500 ms
            # cold graph window. Use one 100 ms period on server and clients.
            '<clientAnnouncementPeriod><sec>0</sec><nanosec>100000000</nanosec>'
            '</clientAnnouncementPeriod></discovery_config></builtin>'
            f'<userTransports><transport_id>{transport_id}</transport_id>' + extra_transport + '</userTransports>'
            '<useBuiltinTransports>false</useBuiltinTransports></rtps></participant>' + service_qos + '</profiles>')


def configure(host, port, prefix, server=False, transport=None, advertised_host=None):
    import os
    path = Path('/tmp/agenticros-server.xml' if server else '/tmp/agenticros-lan.xml')
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='lan-profile-', dir='/tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(profile(host, port, prefix, server,
                                 transport or os.getenv('AGENTICROS_DDS_TRANSPORT', 'udp'),
                                 advertised_host or os.getenv('AGENTICROS_ADVERTISE_HOST') or host))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    os.environ['FASTRTPS_DEFAULT_PROFILES_FILE'] = str(path)
    os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'
    os.environ.pop('ROS_DISCOVERY_SERVER', None)


if __name__ == '__main__':
    import os
    configure(os.environ['AGENTICROS_DDS_HOST'], os.environ['AGENTICROS_DDS_PORT'],
              os.environ['AGENTICROS_DDS_PREFIX'])
