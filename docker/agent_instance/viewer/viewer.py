"""Plain RViz desktop with a read-only ROS discovery status bar."""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading

from PyQt5 import QtCore, QtWidgets
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.action import get_action_names_and_types

class Discovery(QtCore.QThread):
    updated = QtCore.pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.stop = threading.Event()

    def run(self):
        rclpy.init()
        # Graph-only observer: no executor services parameter requests.
        # Do not advertise parameter RPCs that would never receive a reply.
        node = rclpy.create_node('server_viewer', start_parameter_services=False)
        try:
            while not self.stop.is_set():
                topics = node.get_topic_names_and_types()
                services = node.get_service_names_and_types()
                actions = get_action_names_and_types(node)
                robots = {}
                for name, _ in topics:
                    parts = name.split('/')
                    if len(parts) >= 4 and parts[1] == 'robot' and node.count_publishers(name):
                        robots[parts[2]] = '/robot/'+parts[2]
                self.updated.emit(dict(robots=robots, topics=topics, services=services, actions=actions))
                self.stop.wait(2)
        except ExternalShutdownException:
            pass
        finally:
            node.destroy_node()
            if rclpy.ok(): rclpy.shutdown()


class Viewer(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AgenticROS Viewer')
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        self.setGeometry(0, 0, 1440, 125)
        self.setStyleSheet('QWidget { background: #19222e; color: #edf3fa; font-size: 15px; }'
                          'QPushButton { padding: 7px; background: #30445a; }')
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('Viewer · ROS domain '+os.getenv('ROS_DOMAIN_ID','42')))
        reopen = QtWidgets.QPushButton('Open RViz')
        reopen.clicked.connect(self.open_rviz)
        row.addWidget(reopen)
        interfaces = QtWidgets.QPushButton('ROS interfaces')
        interfaces.clicked.connect(self.show_interfaces)
        row.addWidget(interfaces)
        layout.addLayout(row)
        self.status = QtWidgets.QLabel('No robots discovered.')
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(0)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(self.status)
        layout.addWidget(QtWidgets.QLabel('Configure topics and Fixed Frame directly in RViz. Discovery never changes the view.'))
        self.process, self.latest = None, {}
        self.interfaces_dialog = None
        self.discovery = Discovery()
        self.discovery.updated.connect(self.receive)
        self.discovery.start()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.heartbeat)
        self.timer.start(1000)
        self.open_rviz()

    def receive(self, snapshot):
        self.latest = snapshot
        self.refresh_interfaces()
        names = sorted(snapshot['robots'])
        self.status.setText('Detected robots: '+', '.join(names) if names else 'No robots discovered.')
        self.status.setToolTip('\n'.join(names))

    def stop_rviz(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None

    def open_rviz(self):
        if self.process and self.process.poll() is None:
            return
        path = Path('/tmp/current.rviz')
        if not path.exists():
            path.write_text(Path('/opt/viewer/default.rviz').read_text())
        self.process = subprocess.Popen(['rviz2', '-d', str(path), '--ros-args', '-r', '__node:=server_rviz'])

    def show_interfaces(self):
        if self.interfaces_dialog is None:
            self.interfaces_dialog = QtWidgets.QDialog(self)
            self.interfaces_dialog.setWindowTitle('ROS interfaces — Viewer')
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
        data = dict(domain_id=int(os.getenv('ROS_DOMAIN_ID','42')), robots=self.latest.get('robots', {}),
                    rviz_pid=self.process.pid if self.process and self.process.poll() is None else None,
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
