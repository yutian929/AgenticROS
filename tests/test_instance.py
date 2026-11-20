"""Lifecycle failure handling; no ROS, Docker bodies, or motion."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('instance_runtime', Path(__file__).resolve().parents[1]/'docker/agent_instance/runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class Lifecycle(unittest.TestCase):
    def test_reload_cli_forwards_to_supervisor(self):
        with patch.object(runtime.sys,'argv',['runtime.py','clients-reload']), patch.object(runtime,'request',return_value={}) as request:
            self.assertEqual(runtime.main(),0)
            request.assert_called_once_with('clients-reload')

    def test_repeated_start_keeps_discovery_process(self):
        process = Mock()
        process.poll.return_value = None
        manager = runtime.Runtime()
        with patch.object(runtime.subprocess, 'Popen', return_value=process) as spawn:
            manager.start('server')
            manager.start('server')
            self.assertEqual(spawn.call_count, 1)
        self.assertTrue(manager.status()['server']['running'])

    def test_desktop_stop_does_not_signal_discovery(self):
        server, viewer = Mock(), Mock()
        server.poll.return_value = viewer.poll.return_value = None
        manager = runtime.Runtime()
        manager.processes = dict(server=server, viewer=viewer)
        manager.enabled = {'server', 'viewer'}
        manager.command('viewer-stop')
        viewer.terminate.assert_called_once()
        server.terminate.assert_not_called()
        self.assertEqual(manager.enabled, {'server'})

    def test_failed_desktop_shutdown_stops_container_instead_of_leaving_orphans(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('desktop', 45), 0]
        manager = runtime.Runtime()
        manager.processes['viewer'] = process
        with self.assertRaisesRegex(RuntimeError, 'stopping Agent instance'):
            manager.stop('viewer')
        self.assertTrue(manager.stopping)
        process.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
