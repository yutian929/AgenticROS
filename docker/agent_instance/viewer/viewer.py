"""Server robot selector and RViz launcher using only public ROS discovery."""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.request

from PyQt5 import QtCore, QtWidgets
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.action import get_action_names_and_types
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
from std_msgs.msg import String
from sensor_msgs.msg import CompressedImage, PointCloud2
from tf2_msgs.msg import TFMessage
import yaml
from discovery import robots, scoped_topics, fixed_frame, configuration


def rviz_dock_layout(config):
    """Store a native RViz/Qt dock layout; image rendering remains in RViz."""
    names = [d['Name'] for d in config['Visualization Manager']['Displays']
             if d['Class'] == 'rviz_default_plugins/Image' and d['Enabled']]
    if len(names) < 2:
        return
    window = QtWidgets.QMainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    window.resize(1440, 740)
    window.setCentralWidget(QtWidgets.QWidget())
    panels = []
    for name in ('Displays', 'Views'):
        dock = QtWidgets.QDockWidget(name)
        dock.setObjectName(name)
        dock.setWidget(QtWidgets.QWidget())
        window.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
        panels.append(dock)
    cameras = []
    for name in names:
        dock = QtWidgets.QDockWidget(name)
        dock.setObjectName(name)
        dock.setWidget(QtWidgets.QWidget())
        window.addDockWidget(QtCore.Qt.TopDockWidgetArea, dock)
        if cameras:
            window.splitDockWidget(cameras[-1], dock, QtCore.Qt.Horizontal)
        cameras.append(dock)
    window.show()
    window.resizeDocks(panels, [280, 280], QtCore.Qt.Horizontal)
    window.resizeDocks(cameras, [220]*len(cameras), QtCore.Qt.Vertical)
    window.resizeDocks(cameras, [350]*len(cameras), QtCore.Qt.Horizontal)
    config['Window Geometry']['QMainWindow State'] = bytes(window.saveState().toHex()).decode()
    window.close()
    window.deleteLater()


class Discovery(QtCore.QThread):
    updated = QtCore.pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.stop = threading.Event()
        self.selected = ''

    def run(self):
        rclpy.init()
        node = rclpy.create_node('server_viewer')
        edges, frames = set(), set()
        directory = {'roster': {}, 'at': 0, 'sessions': {}}
        def roster_message(msg):
            roster=json.loads(msg.data)
            sessions={r['robot_id']:r['session_id'] for r in roster.get('robots',[])}
            if sessions != directory['sessions']:
                edges.clear(); frames.clear()
            directory.update(roster=roster, at=time.monotonic(), sessions=sessions)
        node.create_subscription(String, '/agenticros/robots', roster_message, 10)
        def tf(message):
            edges.update((t.header.frame_id, t.child_frame_id) for t in message.transforms)
        qos = QoSProfile(depth=100, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                         reliability=ReliabilityPolicy.RELIABLE)
        node.create_subscription(TFMessage, '/tf_static', tf, qos)
        node.create_subscription(TFMessage, '/tf', tf, 100)
        subscription, active, selected, next_poll = None, None, '', 0
        try:
            while not self.stop.is_set():
                rclpy.spin_once(node, timeout_sec=.1)
                if time.monotonic() < next_poll:
                    continue
                next_poll = time.monotonic()+2
                try:
                    roster = robots(directory['roster'])
                    age=time.monotonic()-directory['at']
                    error = 'Registry status stale; showing last known robots' if age > 3 else ''
                except Exception as exc:
                    roster, error = {}, 'Registry unavailable: '+type(exc).__name__
                topics = node.get_topic_names_and_types()
                services = node.get_service_names_and_types()
                actions = get_action_names_and_types(node)
                if selected != self.selected:
                    frames.clear()
                    selected = self.selected
                sensors = [(name, CompressedImage if 'sensor_msgs/msg/CompressedImage' in types else PointCloud2)
                           for name, types in scoped_topics(topics, self.selected) if self.selected
                           and ('sensor_msgs/msg/CompressedImage' in types or 'sensor_msgs/msg/PointCloud2' in types)]
                # Once a public TF or sensor header supplies the frame, stop the
                # discovery subscription. RViz alone consumes the live image stream.
                desired = sensors[0] if sensors and not fixed_frame(selected, edges, frames) else None
                if desired != active:
                    if subscription:
                        node.destroy_subscription(subscription)
                    subscription, active = None, desired
                    if desired:
                        subscription = node.create_subscription(desired[1], desired[0],
                            lambda msg: frames.add(msg.header.frame_id), qos_profile_sensor_data)
                self.updated.emit(dict(robots=roster, sessions=directory['sessions'], topics=topics, services=services, actions=actions, edges=list(edges), frames=list(frames), error=error))
        except ExternalShutdownException:
            pass
        finally:
            node.destroy_node()
            if rclpy.ok(): rclpy.shutdown()


class Viewer(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AgenticROS — '+'Server')
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        self.setGeometry(0, 0, 1440, 125)
        self.setStyleSheet('QWidget { background: #19222e; color: #edf3fa; font-size: 15px; }'
                          'QPushButton, QComboBox { padding: 7px; background: #30445a; }')
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('Agent Server: '+'Server'))
        self.selector = QtWidgets.QComboBox()
        self.selector.setMinimumWidth(300)
        self.selector.currentIndexChanged.connect(self.select)
        row.addWidget(self.selector)
        reopen = QtWidgets.QPushButton('Open / reset RViz')
        reopen.clicked.connect(lambda: self.select(force=True))
        row.addWidget(reopen)
        interfaces = QtWidgets.QPushButton('ROS interfaces')
        interfaces.clicked.connect(self.show_interfaces)
        row.addWidget(interfaces)
        layout.addLayout(row)
        self.status = QtWidgets.QLabel('Waiting for clients…')
        layout.addWidget(self.status)
        layout.addWidget(QtWidgets.QLabel('Each robot uses its own coordinate frame. This viewer does not send robot commands.'))
        self.process, self.signature, self.latest = None, None, {}
        self.interfaces_dialog = None
        self.discovery = Discovery()
        self.discovery.updated.connect(self.receive)
        self.discovery.start()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.heartbeat)
        self.timer.start(1000)

    def receive(self, snapshot):
        self.latest = snapshot
        self.refresh_interfaces()
        previous = self.selector.currentText()
        names = sorted(snapshot['robots'])
        if names != [self.selector.itemText(i) for i in range(self.selector.count())]:
            self.selector.blockSignals(True)
            self.selector.clear()
            self.selector.addItems(names)
            if previous in names:
                self.selector.setCurrentText(previous)
            self.selector.blockSignals(False)
        self.select()

    def stop_rviz(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None

    def select(self, index=None, force=False):
        name = self.selector.currentText()
        namespace = self.latest.get('robots', {}).get(name, '')
        self.discovery.selected = namespace
        topics = scoped_topics(self.latest.get('topics', []), namespace) if namespace else []
        frame = fixed_frame(namespace, self.latest.get('edges', []), self.latest.get('frames', [])) if namespace else ''
        config = configuration(topics, frame) if namespace and frame else {}
        # Service/action discovery and subscriber-only graph changes do not alter
        # the rendered view. Never restart RViz for those unrelated changes.
        signature = json.dumps([namespace, self.latest.get('sessions',{}).get(name), config], sort_keys=True)
        if namespace and frame:
            self.status.setText(name+'  |  '+namespace+'  |  Frame: '+frame+'  |  '+self.latest.get('error',''))
        if signature == self.signature and not force:
            return
        print(json.dumps(dict(event='rviz_reconfigure',namespace=namespace,frame=frame,session=self.latest.get('sessions',{}).get(name),displays=[d['Name'] for d in config.get('Visualization Manager',{}).get('Displays',[])])),flush=True)
        self.stop_rviz()
        self.signature = signature
        if not namespace or not frame:
            self.status.setText(self.latest.get('error') or ('Waiting for sensor frames…' if namespace else 'Waiting for clients…'))
            return
        path = Path('/tmp/current.rviz')
        rviz_dock_layout(config)
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        self.process = subprocess.Popen(['rviz2', '-d', str(path), '--ros-args', '-r', '__node:=server_rviz'])
        self.status.setText(name+'  |  '+namespace+'  |  Frame: '+frame)

    def show_interfaces(self):
        if self.interfaces_dialog is None:
            self.interfaces_dialog = QtWidgets.QDialog(self)
            self.interfaces_dialog.setWindowTitle('ROS interfaces — '+'Server')
            self.interfaces_dialog.resize(1100, 650)
            layout = QtWidgets.QVBoxLayout(self.interfaces_dialog)
            layout.addWidget(QtWidgets.QLabel('Discovered interfaces · updates every 2 seconds · read-only'))
            self.interface_tabs = QtWidgets.QTabWidget()
            self.interface_text = {}
            for key in ('topics', 'services', 'actions'):
                text = QtWidgets.QPlainTextEdit()
                text.setReadOnly(True)
                text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
                self.interface_text[key] = text
                self.interface_tabs.addTab(text, key.title())
            layout.addWidget(self.interface_tabs)
        self.refresh_interfaces()
        self.interfaces_dialog.show()
        self.interfaces_dialog.raise_()
        self.interfaces_dialog.activateWindow()

    def refresh_interfaces(self):
        if self.interfaces_dialog is None:
            return
        for index, key in enumerate(('topics', 'services', 'actions')):
            entries = sorted(self.latest.get(key, []))
            content = '\n\n'.join(name+'\n    '+', '.join(sorted(types)) for name, types in entries)
            content = content or 'No '+key+' discovered yet.'
            text = self.interface_text[key]
            if text.toPlainText() != content:
                scroll = text.verticalScrollBar().value()
                text.setPlainText(content)
                text.verticalScrollBar().setValue(scroll)
            self.interface_tabs.setTabText(index, key.title()+f' ({len(entries)})')

    def heartbeat(self):
        data = dict(server='Server', sessions=self.latest.get('sessions',{}), robots=self.latest.get('robots', {}),
                    selected=self.selector.currentText(), rviz_pid=self.process.pid if self.process and self.process.poll() is None else None,
                    error=self.latest.get('error',''),
                    interfaces={key: self.latest.get(key, []) for key in ('topics', 'services', 'actions')})
        path = Path('/tmp/viewer-status.tmp')
        path.write_text(json.dumps(data))
        path.replace('/tmp/viewer-status.json')

    def closeEvent(self, event):
        self.stop_rviz()
        self.discovery.stop.set()
        self.discovery.wait(5000)
        event.accept()


if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    viewer = Viewer()
    signal.signal(signal.SIGTERM, lambda *_: viewer.close())
    signal.signal(signal.SIGINT, lambda *_: viewer.close())
    viewer.show()
    app.exec_()
