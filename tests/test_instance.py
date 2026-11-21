"""Lifecycle failure handling; no ROS, Docker bodies, or motion."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('instance_runtime', Path(__file__).resolve().parents[1]/'docker/agent_instance/agent/runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class Lifecycle(unittest.TestCase):
    def test_reload_cli_forwards_to_supervisor(self):
        with patch.object(runtime.sys,'argv',['runtime.py','clients-reload']), patch.object(runtime,'request',return_value={}) as request:
            self.assertEqual(runtime.main(),0)
            request.assert_called_once_with('clients-reload')

    def test_repeated_start_keeps_clients_process(self):
        process = Mock()
        process.poll.return_value = None
        manager = runtime.Runtime()
        with patch.object(runtime.subprocess, 'Popen', return_value=process) as spawn:
            manager.start('clients')
            manager.start('clients')
            self.assertEqual(spawn.call_count, 1)
        self.assertTrue(manager.status()['clients']['running'])

    def test_viewer_is_not_supervised_by_agent(self):
        with self.assertRaises(ValueError): runtime.Runtime().command('viewer-start')

    def test_stuck_client_is_reaped(self):
        process=Mock()
        process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('clients',8),0]
        manager=runtime.Runtime()
        manager.processes['clients']=process
        manager.stop('clients')
        process.kill.assert_called_once()

if __name__ == '__main__': unittest.main()
