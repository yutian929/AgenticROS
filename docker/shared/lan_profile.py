"""Generic Fast DDS LAN profiles. No device interfaces or adaptation code."""
import ipaddress
import re
from pathlib import Path


def profile(host, port, prefix, server=False):
    host = str(ipaddress.IPv4Address(host))
    port = int(port)
    if not 1024 <= port <= 65535 or not re.fullmatch(r'(?:[0-9a-f]{2}\.){11}[0-9a-f]{2}', prefix):
        raise ValueError('invalid DDS port or GUID prefix')
    locator = f'<locator><udpv4><address>{host}</address><port>{port}</port></udpv4></locator>'
    discovery = '<discoveryProtocol>SERVER</discoveryProtocol>' if server else (
        '<discoveryProtocol>SUPER_CLIENT</discoveryProtocol><discoveryServersList>'
        f'<RemoteServer prefix="{prefix}"><metatrafficUnicastLocatorList>{locator}'
        '</metatrafficUnicastLocatorList></RemoteServer></discoveryServersList>')
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">'
            '<transport_descriptors><transport_descriptor><transport_id>lan_udp</transport_id>'
            '<type>UDPv4</type></transport_descriptor></transport_descriptors>'
            '<participant profile_name="lan" is_default_profile="true"><rtps>'
            + (f'<prefix>{prefix}</prefix>' if server else '') + '<builtin>'
            + (f'<metatrafficUnicastLocatorList>{locator}</metatrafficUnicastLocatorList>' if server else '')
            + f'<discovery_config>{discovery}</discovery_config></builtin>'
            '<userTransports><transport_id>lan_udp</transport_id></userTransports>'
            '<useBuiltinTransports>false</useBuiltinTransports></rtps></participant></profiles>')


def configure(host, port, prefix, server=False):
    import os
    path = Path('/tmp/agenticros-server.xml' if server else '/tmp/agenticros-lan.xml')
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='lan-profile-', dir='/tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(profile(host, port, prefix, server))
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
