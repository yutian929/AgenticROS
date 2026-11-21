"""Run with the Viewer image's Python and Qt; never starts ROS or RViz."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
sys.path.insert(0, '/opt/viewer')
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from viewer import Viewer, Discovery


class InterfaceDialog(unittest.TestCase):
    def test_discovery_never_reconfigures_or_restarts_rviz(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with patch.object(Discovery, 'start'), patch('viewer.subprocess.Popen') as launch, \
                patch('viewer.Path.exists', return_value=False), \
                patch('viewer.Path.read_text', return_value='default'), patch('viewer.Path.write_text') as write:
            launch.return_value.poll.return_value = None
            widget = Viewer(); widget.timer.stop()
            self.assertFalse(widget.findChildren(QtWidgets.QComboBox))
            empty = dict(robots={}, topics=[], services=[], actions=[])
            for robots in ({}, {'a':'/robot/a'}, {'a':'/robot/a', 'b':'/robot/b'}, {'b':'/robot/b'}, {}):
                widget.receive(dict(empty, robots=robots))
                for name in robots: self.assertIn(name, widget.status.text())
            widget.open_rviz()
            self.assertEqual(launch.call_count, 1)
            write.assert_called_once_with('default')
            launch.return_value.terminate.assert_not_called()
            # Reopening after manual close uses the saved configuration.
            launch.return_value.poll.return_value = 0
            with patch('viewer.Path.exists', return_value=True): widget.open_rviz()
            self.assertEqual(launch.call_count, 2)
            self.assertEqual(write.call_count, 1)
            widget.close()

    def test_tabs_refresh_names_types_and_empty_results(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with patch.object(Discovery, 'start'), patch.object(Viewer, 'open_rviz'):
            widget = Viewer()
            widget.timer.stop()
            snapshot = dict(robots={}, topics=[('/camera/image', ['sensor_msgs/msg/Image'])],
                            services=[('/robot/driver/status', ['std_srvs/srv/Trigger'])],
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
