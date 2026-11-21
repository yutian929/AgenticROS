"""Read-only cross-container check for Reliable and Best Effort sensor subscribers."""
import json
import sys
import time
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, CompressedImage, CameraInfo, PointCloud2

robots = json.loads(sys.argv[1])
rclpy.init()
node = rclpy.create_node('sensor_qos_check')
expected = {}
for robot in robots:
    cameras = ('left/', 'right/', 'overhead/') if robot['AGENTICROS_ENVIRONMENT']=='aloha_tabletop' else ('',)
    for camera in cameras:
        base = '/robot/'+robot['AGENTICROS_ROBOT_ID']+'/'+camera+'camera/'
        for suffix, message in (('color/image_raw', Image), ('color/image_raw/compressed', CompressedImage),
                                ('depth/image_raw', Image), ('camera_info', CameraInfo),
                                ('depth/color/points', PointCloud2)):
            expected[base+suffix] = message
received = set()
subscriptions = []
try:
    for reliability in (ReliabilityPolicy.RELIABLE, ReliabilityPolicy.BEST_EFFORT):
        subscriptions = []
        for topic, message in expected.items():
            key = (topic, reliability.name)
            subscriptions.append(node.create_subscription(message, topic,
                lambda msg, key=key: received.add(key), QoSProfile(depth=5, reliability=reliability)))
        required = {(topic, reliability.name) for topic in expected}
        end = time.monotonic()+45
        while not required <= received and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=.2)
        assert required <= received, sorted(required-received)
        for subscription in subscriptions:
            node.destroy_subscription(subscription)
    for topic in expected:
        publishers = node.get_publishers_info_by_topic(topic)
        assert publishers and all(p.qos_profile.reliability==ReliabilityPolicy.RELIABLE for p in publishers), topic
    print(json.dumps(dict(result='PASS',sensor_topics=len(expected),received_subscriptions=len(received))))
finally:
    node.destroy_node()
    rclpy.shutdown()
