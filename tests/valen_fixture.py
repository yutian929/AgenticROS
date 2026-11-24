"""Synthetic protocol backend. Never packaged in the model or Agent image."""
import threading
import time
import rclpy
from rclpy.executors import MultiThreadedExecutor
from node import Decision


class Fixture:
    metadata = {'backend': 'synthetic-transport-fixture', 'not_valen_inference': True}

    def __init__(self):
        self.lock = threading.Lock()

    def status(self):
        return dict(model_state='READY', model=self.metadata, error='')

    def infer(self, folder, request):
        mode = request['state'].get('mode', '')
        time.sleep(5. if mode == 'slow' else .5)
        if mode == 'fail':
            raise RuntimeError('injected inference failure')
        if request['has_image']:
            from PIL import Image
            image = Image.open(folder/'input.png')
            assert image.getpixel((0, 0)) == (30, 100, 40)
        result = {}
        for key, question in request['questions'].items():
            kind = question['type']
            if kind == 'bbox':
                result[key] = dict(type='bbox', bbox=[.25, .25, .75, .75], point=[.5, .5], confidence=.9)
                if mode == 'invalid_bbox':
                    result[key]['bbox'][0] = -1.
            elif kind == 'noul':
                result[key] = dict(type='noul', noul=.8)
            elif kind == 'score':
                result[key] = dict(type='score', score=0., confidence=1.,
                                   probabilities={str(i): float(i == 0) for i in range(len(question['criteria']))})
            else:
                options = list(question['criteria'])
                result[key] = dict(type='choice', choice=options[0], confidence=1.,
                                   probabilities={option: float(i == 0) for i, option in enumerate(options)})
            if mode == 'invalid' and kind == 'choice':
                result[key]['choice'] = 'unrequested_motion'
        return result

    def close(self):
        pass


rclpy.init()
node = Decision(backend=Fixture())
executor = MultiThreadedExecutor(num_threads=8)
executor.add_node(node)
try:
    executor.spin()
finally:
    executor.shutdown()
    node.destroy_node()
