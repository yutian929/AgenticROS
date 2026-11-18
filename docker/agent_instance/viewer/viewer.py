"""Instance robot selector and RViz launcher using only public ROS discovery."""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.request

from PyQt5 import QtCore, QtGui, QtWidgets
import rclpy
from rclpy.action import get_action_names_and_types
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, PointCloud2
from tf2_msgs.msg import TFMessage
import yaml
from discovery import robots, scoped_topics, fixed_frame, configuration


class Discovery(QtCore.QThread):
    updated = QtCore.pyqtSignal(object)
    image_received = QtCore.pyqtSignal(str, bytes)

    def __init__(self):
        super().__init__()
        self.stop = threading.Event()
        self.selected = ''

    def run(self):
        rclpy.init()
        node = rclpy.create_node('instance_viewer')
        edges, frames = set(), set()
        def tf(message):
            edges.update((t.header.frame_id, t.child_frame_id) for t in message.transforms)
        qos = QoSProfile(depth=100, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                         reliability=ReliabilityPolicy.RELIABLE)
        node.create_subscription(TFMessage, '/tf_static', tf, qos)
        node.create_subscription(TFMessage, '/tf', tf, 100)
        image_subscriptions = {}
        subscription, active, selected, next_poll = None, None, '', 0
        try:
            while not self.stop.is_set():
                rclpy.spin_once(node, timeout_sec=.1)
                if time.monotonic() < next_poll:
                    continue
                next_poll = time.monotonic()+2
                try:
                    with urllib.request.urlopen(os.environ['AGENTICROS_REGISTRY_URL']+'/robots', timeout=3) as response:
                        roster = robots(json.load(response), os.environ['AGENTICROS_INSTANCE_ID'])
                    error = ''
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
                image_topics = [name for name, kind in sensors if kind is CompressedImage]
                desired_images = set(image_topics) if len(image_topics) > 1 else set()
                for topic in list(image_subscriptions):
                    if topic not in desired_images:
                        node.destroy_subscription(image_subscriptions.pop(topic))
                for topic in sorted(desired_images - image_subscriptions.keys()):
                    image_subscriptions[topic] = node.create_subscription(CompressedImage, topic,
                        lambda msg, topic=topic: self.image_received.emit(topic, bytes(msg.data)),
                        qos_profile_sensor_data)
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
                self.updated.emit(dict(robots=roster, topics=topics, services=services, actions=actions, edges=list(edges), frames=list(frames), error=error))
        finally:
            node.destroy_node()
            rclpy.shutdown()


class Viewer(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AgenticROS — '+os.environ['AGENTICROS_INSTANCE'])
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        self.setGeometry(0, 0, 1440, 125)
        self.setStyleSheet('QWidget { background: #19222e; color: #edf3fa; font-size: 15px; }'
                          'QPushButton, QComboBox { padding: 7px; background: #30445a; }')
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('Instance: '+os.environ['AGENTICROS_INSTANCE']))
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
        self.camera_row = QtWidgets.QWidget()
        self.camera_layout = QtWidgets.QHBoxLayout(self.camera_row)
        self.camera_layout.setContentsMargins(0, 0, 0, 0)
        self.camera_labels, self.camera_topics = {}, []
        self.camera_row.hide()
        layout.addWidget(self.camera_row)
        self.process, self.signature, self.latest = None, None, {}
        self.interfaces_dialog = None
        self.discovery = Discovery()
        self.discovery.updated.connect(self.receive)
        self.discovery.image_received.connect(self.receive_image)
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
        image_topics = [topic for topic, types in topics if 'sensor_msgs/msg/CompressedImage' in types]
        self.configure_cameras(image_topics if len(image_topics) > 1 else [], namespace)
        signature = json.dumps([namespace, topics, frame])
        if signature == self.signature and not force:
            return
        self.stop_rviz()
        self.signature = signature
        if not namespace or not frame:
            self.status.setText(self.latest.get('error') or ('Waiting for sensor frames…' if namespace else 'Waiting for clients…'))
            return
        path = Path('/tmp/current.rviz')
        path.write_text(yaml.safe_dump(configuration(topics, frame, image_strip=bool(self.camera_topics)), sort_keys=False))
        self.process = subprocess.Popen(['rviz2', '-d', str(path), '--ros-args', '-r', '__node:=instance_rviz'])
        self.status.setText(name+'  |  '+namespace+'  |  Frame: '+frame)

    def configure_cameras(self, topics, namespace):
        if topics == self.camera_topics:
            return
        self.camera_topics = topics
        self.camera_labels = {}
        while self.camera_layout.count():
            item = self.camera_layout.takeAt(0)
            item.widget().deleteLater()
        for topic in topics:
            panel = QtWidgets.QWidget()
            column = QtWidgets.QVBoxLayout(panel)
            column.setContentsMargins(0, 0, 0, 0)
            title = topic.removeprefix(namespace+'/').removesuffix('/color/image_raw/compressed')
            heading = QtWidgets.QLabel(title)
            heading.setAlignment(QtCore.Qt.AlignCenter)
            column.addWidget(heading)
            image = QtWidgets.QLabel('Waiting for image...')
            image.setAlignment(QtCore.Qt.AlignCenter)
            image.setMinimumSize(1, 240)
            image.setStyleSheet('background: #101720;')
            column.addWidget(image)
            self.camera_layout.addWidget(panel, 1)
            self.camera_labels[topic] = image
        self.camera_row.setVisible(bool(topics))
        self.resize(1440, 410 if topics else 125)

    def receive_image(self, topic, data):
        label = self.camera_labels.get(topic)
        if label is None:
            return
        image = QtGui.QPixmap()
        if image.loadFromData(data):
            label.setPixmap(image.scaled(label.size(), QtCore.Qt.KeepAspectRatio,
                                         QtCore.Qt.SmoothTransformation))

    def show_interfaces(self):
        if self.interfaces_dialog is None:
            self.interfaces_dialog = QtWidgets.QDialog(self)
            self.interfaces_dialog.setWindowTitle('ROS interfaces — '+os.environ['AGENTICROS_INSTANCE'])
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
        data = dict(instance=os.environ['AGENTICROS_INSTANCE'], robots=self.latest.get('robots', {}),
                    selected=self.selector.currentText(), rviz_pid=self.process.pid if self.process and self.process.poll() is None else None,
                    error=self.latest.get('error',''), camera_topics=self.camera_topics,
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
