"""接入骨架：只发布节点状态，不连接硬件，不提供运动指令。"""
import json
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from std_msgs.msg import String
from std_srvs.srv import Trigger
from robot_identity import RobotIdentity


class Driver(Node):
    def __init__(self):
        self.identity = RobotIdentity()  # ID 来自启动器；每次进程启动生成新 session。
        super().__init__('driver', namespace=self.identity.namespace)
        self.publisher = self.create_publisher(String, 'driver/state', 10)
        self.create_service(Trigger, 'driver/status', self.status)
        self.create_timer(.5, self.publish)
        # 按需修改：初始化实际驱动、订阅传感器；启动时不要自动发送运动命令。

    def state(self):
        return dict(**self.identity.identity(), session_id=self.identity.session,
                    reported_at_unix=time.time(), control_available=False,
                    state=dict(mode='template', hardware_connected=False))
        # 必改（实际接入时）：用真实反馈替换 state，不能把节点存活当作硬件可用。
        # 原生传感器/控制接口可直接使用；在公开手册中写明类型、单位及坐标系。

    def status(self, request, response):
        response.success = True  # 仅表示状态查询成功，不表示硬件已连接。
        response.message = json.dumps(self.state())
        return response

    def publish(self):
        self.publisher.publish(String(data=json.dumps(self.state())))
        Path('/tmp/robot-ready').touch()


def main():
    rclpy.init()
    driver = Driver()
    try:
        rclpy.spin(driver)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        driver.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
