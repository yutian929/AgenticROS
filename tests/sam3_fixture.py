"""Deterministic transport-only backend. Never packaged in production images."""
import threading
import time
from PIL import Image
import rclpy
from rclpy.executors import MultiThreadedExecutor
from node import Perception

class Fixture:
    metadata = {'backend': 'synthetic-transport-fixture', 'not_sam3_inference': True}
    def __init__(self):
        self.lock = threading.Lock()
    def status(self):
        return dict(model_state='READY', model=self.metadata, error='')
    def infer(self, folder, request):
        time.sleep(1.5)
        if request['text_prompt'] == 'fail':
            raise RuntimeError('injected inference failure')
        image = Image.open(folder/'input.png')
        image.save(folder/'overlay.png')
        (folder/'masks').mkdir()
        if request['text_prompt'] == 'empty':
            return []
        Image.new('L', image.size, 255).save(folder/'masks/0.png')
        return [dict(instance_id=0, score=.9, box_xyxy=[0.,0.,float(image.width),float(image.height)])]
    def close(self):
        pass

rclpy.init()
node = Perception(backend=Fixture())
executor = MultiThreadedExecutor(num_threads=8)
executor.add_node(node)
try:
    executor.spin()
finally:
    executor.shutdown()
    node.destroy_node()
