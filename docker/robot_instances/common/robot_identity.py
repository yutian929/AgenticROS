"""Robot-local identity; no server, registration or file transport."""
import os
import re
import secrets


class RobotIdentity:
    def __init__(self):
        self.robot_id = os.environ['AGENTICROS_ROBOT_ID']
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,95}', self.robot_id):
            raise ValueError('invalid robot_id')
        self.session = secrets.token_hex(6)
        self.namespace = '/robot/'+self.robot_id

    def identity(self):
        return dict(robot_id=self.robot_id, namespace=self.namespace)
