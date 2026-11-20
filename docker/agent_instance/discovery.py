"""Fixed Fast DDS discovery participant. No HTTP registry or control leases."""
import os
from pathlib import Path
import rclpy
from rclpy.executors import ExternalShutdownException
from lan_profile import configure


def main():
    configure(os.environ['AGENTICROS_DDS_HOST'],os.environ['AGENTICROS_DDS_PORT'],
              os.environ['AGENTICROS_DDS_PREFIX'],server=True)
    rclpy.init()
    node=rclpy.create_node('discovery_server',namespace='/agenticros')
    ready=Path('/tmp/discovery-ready');ready.write_text(str(os.getpid()))
    try:rclpy.spin(node)
    except (KeyboardInterrupt,ExternalShutdownException):pass
    finally:
        ready.unlink(missing_ok=True)
        node.destroy_node()
        if rclpy.ok():rclpy.shutdown()

if __name__=='__main__':main()
