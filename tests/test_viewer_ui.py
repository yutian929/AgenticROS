"""Run with the Agent instance image's Python and Qt; never starts ROS or RViz."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ.setdefault('AGENTICROS_INSTANCE', 'ui-test')
import sys
sys.path.insert(0, '/opt/viewer')
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from viewer import Viewer, Discovery, rviz_dock_layout
from discovery import configuration


class InterfaceDialog(unittest.TestCase):
    def test_three_camera_docks_restore_in_native_top_area(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        topics = [('/robot/'+arm+'/camera/color/image_raw/compressed',
                   ['sensor_msgs/msg/CompressedImage']) for arm in ('left', 'overhead', 'right')]
        config = configuration(topics, 'robot/base')
        rviz_dock_layout(config)
        window = QtWidgets.QMainWindow()
        window.setCentralWidget(QtWidgets.QWidget())
        docks = []
        for name in ('Displays', 'Views', 'left/camera', 'overhead/camera', 'right/camera'):
            dock = QtWidgets.QDockWidget(name)
            dock.setObjectName(name)
            dock.setWidget(QtWidgets.QWidget())
            window.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
            docks.append(dock)
        state = QtCore.QByteArray.fromHex(config['Window Geometry']['QMainWindow State'].encode())
        self.assertTrue(window.restoreState(state))
        app.processEvents()
        for dock in docks[2:]:
            self.assertEqual(window.dockWidgetArea(dock), QtCore.Qt.TopDockWidgetArea)
        window.close()

    def test_tabs_refresh_names_types_and_empty_results(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with patch.object(Discovery, 'start'), patch.object(Viewer, 'select'):
            widget = Viewer()
            widget.timer.stop()
            snapshot = dict(robots={}, topics=[('/camera/image', ['sensor_msgs/msg/Image'])],
                            services=[('/robot/observe', ['example/srv/Snapshot'])],
                            actions=[('/robot/actuate', ['example/action/Actuate'])])
            widget.receive(snapshot)
            widget.show_interfaces()
            app.processEvents()
            self.assertEqual(widget.interface_tabs.count(), 3)
            for index, key in enumerate(('topics', 'services', 'actions')):
                text = widget.interface_text[key].toPlainText()
                self.assertIn(snapshot[key][0][0], text)
                self.assertIn(snapshot[key][0][1][0], text)
                self.assertEqual(widget.interface_tabs.tabText(index), key.title()+' (1)')
            widget.interface_tabs.setCurrentIndex(2)
            widget.receive(dict(robots={}, topics=[], services=[], actions=[]))
            self.assertEqual(widget.interface_tabs.currentIndex(), 2)
            self.assertEqual(widget.interface_text['actions'].toPlainText(), 'No actions discovered yet.')
            widget.interfaces_dialog.close()
            widget.close()


if __name__ == '__main__':
    unittest.main()
