"""VNC credentials must survive restarts and remain private; no GUI is started."""
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import desktop


class ViewerCredentials(unittest.TestCase):
    def test_generated_password_is_private_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / 'auth'
            with patch.object(desktop, 'AUTH', auth):
                desktop.prepare_password()
                password = (auth / 'password').read_text()
                stored = (auth / 'passwd').read_bytes()
                self.assertEqual(len(password.strip()), 8)
                self.assertEqual(stat.S_IMODE((auth / 'password').stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE((auth / 'passwd').stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o700)
                desktop.prepare_password()
                self.assertEqual((auth / 'password').read_text(), password)
                self.assertEqual((auth / 'passwd').read_bytes(), stored)


if __name__ == '__main__':
    unittest.main()
